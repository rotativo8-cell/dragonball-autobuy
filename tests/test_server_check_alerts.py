import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from ops import server_check as server
from src import test_jumpichiban_alerts as alerts
from src.stock_only import jumpichiban_stock_message


class IchibanAlertsTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_both_fictional_messages_without_shop_or_state(self):
        config = SimpleNamespace()
        with patch.object(alerts, 'load_dotenv'), \
             patch.object(alerts.Config, 'load', return_value=config), \
             patch.object(alerts, 'replace', return_value=config), \
             patch.object(alerts, 'Telegram') as telegram, \
             patch.object(alerts, 'EmailNotifier') as email, \
             patch('src.shops.jumpichiban.requests.get') as request, \
             patch('src.state.State.set') as write:
            telegram.return_value.send = AsyncMock()
            email.return_value.enabled = True
            email.return_value.send = AsyncMock()
            self.assertEqual(await alerts.send_test(), 0)
            text = telegram.return_value.send.await_args.args[0]
            self.assertIn('PRUEBA FICTICIA', text)
            self.assertIn('JUMP ICHIBAN', text)
            self.assertIn('USD', text)
            self.assertIn(alerts.URL, text)
            subject, body = email.return_value.send.await_args.args
            self.assertIn('PRUEBA FICTICIA', subject)
            self.assertIn('JUMP ICHIBAN', subject)
            self.assertIn('NO ES STOCK REAL', body)
            request.assert_not_called()
            write.assert_not_called()

    async def test_one_channel_failure_does_not_prevent_other(self):
        with patch.object(alerts, 'load_dotenv'), \
             patch.object(alerts.Config, 'load'), \
             patch.object(alerts, 'replace'), \
             patch.object(alerts, 'Telegram') as telegram, \
             patch.object(alerts, 'EmailNotifier') as email:
            telegram.return_value.send = AsyncMock(side_effect=RuntimeError())
            email.return_value.enabled = True
            email.return_value.send = AsyncMock()
            self.assertEqual(await alerts.send_test(), 1)
            email.return_value.send.assert_awaited_once()

    async def test_live_mode_rejected_before_sending(self):
        with patch.object(alerts, 'load_dotenv'), \
             patch.dict(os.environ, {'DRY_RUN': 'false'}), \
             patch.object(alerts, 'Telegram') as telegram, \
             patch.object(alerts, 'EmailNotifier') as email:
            with self.assertRaises(ValueError):
                await alerts.send_test()
            telegram.assert_not_called()
            email.assert_not_called()


class ServerCheckTests(unittest.TestCase):
    def test_retention_only_own_logs_older_than_five_days(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            now = time.time()
            old = path / 'revision-old.log'
            fresh = path / 'revision-new.log'
            other = path / 'other.log'
            for file in (old, fresh, other):
                file.write_text('test')
            for file in (old, other):
                os.utime(file, (now - 5 * 86400, now - 5 * 86400))
            server.prune_logs(path, now)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(other.exists())

    def test_boot_mail_once_per_boot_including_uncertain_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            sender = Mock(side_effect=RuntimeError('secret must not appear'))
            path = Path(directory)
            text = server.notify_boot('summary', 0, path, 'boot1', sender)
            self.assertIn('FALLO', text)
            self.assertNotIn('secret', text)
            server.notify_boot('summary', 0, path, 'boot1', sender)
            sender.assert_called_once()
            sender.side_effect = None
            server.notify_boot('summary', 0, path, 'boot2', sender)
            self.assertEqual(sender.call_count, 2)

    def test_dynamic_containers_health_and_temporary_exclusion(self):
        def container(name, state, health=None, oneoff=False):
            result = dict(Name='/' + name, State={'Status': state},
                          Config={'Labels': {'com.docker.compose.oneoff': str(oneoff)}})
            if health:
                result['State']['Health'] = {'Status': health}
            return result
        containers = [container('new-service', 'running'),
                      container('bad-service', 'running', 'unhealthy'),
                      container('stopped-service', 'exited'),
                      container('test-command', 'exited', oneoff=True)]
        def fake(args, timeout=20):
            if args[0] == 'findmnt':
                return server.MOUNTS[args[3]]
            if args[:3] == ['docker', 'ps', '-aq']:
                return '1 2 3 4'
            if args[:2] == ['docker', 'inspect']:
                return json.dumps(containers)
            return ''
        with patch.object(server, 'command', side_effect=fake):
            results = server.checks()
        text, failures = server.report(results)
        self.assertIn('new-service', text)
        self.assertNotIn('test-command', text)
        self.assertEqual(failures, 2)
        self.assertIn('sin comprobación de salud', text)

    def test_wrong_mount_not_reported_accessible(self):
        with patch.object(server, 'command', return_value=''):
            results = server.checks()
        self.assertFalse(results[0][0])
        self.assertFalse(results[1][0])

    def test_boot_email_recipient_and_starttls(self):
        env = dict(SMTP_HOST='smtp.example.com', SMTP_PORT='587',
                   SMTP_USER='sender@example.com', SMTP_PASSWORD='fake-secret')
        with patch.object(server, 'command', return_value=json.dumps(
                {'services': {'monitor': {'environment': env}}})), \
             patch.object(server.smtplib, 'SMTP') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value = {}
            server.send_mail('summary', 0)
        session = smtp.return_value.__enter__.return_value
        session.starttls.assert_called_once()
        message = session.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'rotativo8@gmail.com')
