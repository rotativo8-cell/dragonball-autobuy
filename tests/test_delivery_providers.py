import json
import os
import smtplib
import unittest
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from src.email_notifier import send_stock_email
from src.notification_delivery import RetryableNotification
from src.telegram import Telegram


class TelegramDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_returns_explicit_status(self):
        sender = Telegram(SimpleNamespace(token='', chat='', allow_no_telegram=True))
        with patch('src.telegram.urlopen') as request:
            self.assertEqual(await sender.send('test'), 'disabled')
            request.assert_not_called()

    async def test_429_body_retry_after_is_preserved_without_token_in_exception(self):
        sender = Telegram(SimpleNamespace(token='fake-private-token', chat='test', allow_no_telegram=False))
        body = BytesIO(json.dumps({'parameters': {'retry_after': 1200}}).encode())
        error = HTTPError('https://api.telegram.org/hidden', 429, 'limited', {}, body)
        with patch('src.telegram.urlopen', side_effect=error):
            with self.assertRaises(RetryableNotification) as caught:
                await sender.send('test')
        self.assertEqual(caught.exception.retry_after, 1200)
        self.assertNotIn('fake-private-token', str(caught.exception))

    async def test_uncertain_error_is_not_classified_as_retryable(self):
        sender = Telegram(SimpleNamespace(token='test', chat='test', allow_no_telegram=False))
        with patch('src.telegram.urlopen', side_effect=TimeoutError('uncertain')):
            with self.assertRaises(RuntimeError) as caught:
                await sender.send('test')
        self.assertNotIsInstance(caught.exception, RetryableNotification)


class EmailDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_temporary_data_rejection_can_be_retried(self):
        env = {'EMAIL_ENABLED': 'true', 'SMTP_HOST': 'smtp.example.test', 'SMTP_PORT': '587',
               'SMTP_USER': 'test@example.test', 'SMTP_PASSWORD': 'test', 'EMAIL_TO': 'test@example.test'}
        event = {'shop': 'ninnin', 'price': '50', 'name': 'test', 'currency': 'EUR', 'url': 'https://example.test'}
        with patch.dict(os.environ, env, clear=True), patch('src.email_notifier.smtplib.SMTP') as smtp, patch('src.email_notifier.ssl.create_default_context', return_value=MagicMock()):
            smtp.return_value.__enter__.return_value.send_message.side_effect = smtplib.SMTPDataError(451, b'temporary')
            self.assertEqual(await send_stock_email(event, Decimal('60')), 'retryable')
