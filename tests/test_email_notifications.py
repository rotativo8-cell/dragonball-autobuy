import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from src.config import Config
from src.email_notifier import EmailNotifier, send_stock_email, stock_email
from src.hybrid import hybrid_cycle
from src.shops.base import Product
from src.shops.ninnin import NAME, URL
from src.state import State
from src.test_email import send_test

ENV = dict(EMAIL_ENABLED='true',SMTP_HOST='smtp.example.test',SMTP_PORT='587',SMTP_USER='test@example.test',SMTP_PASSWORD='fake-test-password',EMAIL_TO='recipient@example.test')
EVENT = dict(name=NAME,price='94.39',currency='EUR',url=URL)


class EmailTests(unittest.IsolatedAsyncioTestCase):
    def test_subject_body_and_high_price(self):
        subject,body=stock_email(EVENT,Decimal('100'))
        self.assertEqual(subject,'🚨 STOCK NIN-NIN - Dragon Ball Visual Adventure Vol.2')
        self.assertIn(NAME,body)
        self.assertIn('94.39 EUR',body)
        self.assertIn('COMPRAR AHORA:\n'+URL,body)
        subject,body=stock_email(EVENT,Decimal('50'))
        self.assertEqual(subject,'⚠️ STOCK NIN-NIN - PRECIO SUPERIOR AL LÍMITE')
        self.assertIn('94.39 EUR',body)
        self.assertIn(URL,body)

    async def test_starttls_before_login_and_send(self):
        with patch.dict(os.environ,ENV,clear=True),patch('src.email_notifier.smtplib.SMTP') as smtp:
            connection=smtp.return_value.__enter__.return_value
            connection.send_message.return_value={}
            result=await EmailNotifier().send('Subject','Body')
            self.assertEqual(result,'sent')
            smtp.assert_called_once_with('smtp.example.test',587,timeout=10)
            self.assertEqual([call[0] for call in connection.method_calls],['ehlo','starttls','ehlo','login','send_message'])
            context=connection.starttls.call_args.kwargs['context']
            self.assertTrue(context.check_hostname)
            import ssl
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            message=connection.send_message.call_args.args[0]
            self.assertEqual(message['To'],'recipient@example.test')
            self.assertEqual(message['From'],'test@example.test')

    async def test_disabled_never_connects(self):
        with patch.dict(os.environ,{},clear=True),patch('src.email_notifier.smtplib.SMTP') as smtp:
            self.assertEqual(await send_stock_email(EVENT,Decimal('100')),'disabled')
            smtp.assert_not_called()

    async def test_configuration_error_does_not_propagate_or_log_secrets(self):
        with patch.dict(os.environ,{'EMAIL_ENABLED':'true','SMTP_PASSWORD':'fake-test-password'},clear=True),self.assertLogs(level='ERROR') as logs:
            self.assertEqual(await send_stock_email(EVENT,Decimal('100')),'failed')
        self.assertNotIn('fake-test-password',' '.join(logs.output))

    async def test_smtp_error_does_not_propagate_or_downgrade_tls(self):
        with patch.dict(os.environ,ENV,clear=True),patch('src.email_notifier.smtplib.SMTP') as smtp,self.assertLogs(level='ERROR') as logs:
            connection=smtp.return_value.__enter__.return_value
            connection.starttls.side_effect=RuntimeError('fake-test-password must not appear')
            self.assertEqual(await send_stock_email(EVENT,Decimal('100')),'failed')
            connection.login.assert_not_called()
            connection.send_message.assert_not_called()
        self.assertNotIn('fake-test-password',' '.join(logs.output))

    async def test_manual_reads_dotenv_without_state_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'.env').write_text('\n'.join(key+'='+value for key,value in ENV.items())+'\nDRY_RUN=true\n')
            (path/'state.json').write_text('{"untouched":true}')
            before={p.name:p.read_bytes() for p in path.iterdir()}
            with patch.dict(os.environ,{},clear=True),patch('src.test_email.Path.cwd',return_value=path),patch('src.email_notifier.smtplib.SMTP') as smtp:
                smtp.return_value.__enter__.return_value.send_message.return_value={}
                await send_test()
                message=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                self.assertIn('PRUEBA FICTICIA',message['Subject'])
                self.assertIn('NO ES STOCK REAL',message.get_content())
            self.assertEqual(before,{p.name:p.read_bytes() for p in path.iterdir()})

    async def test_manual_disabled_rejected(self):
        with patch.dict(os.environ,{},clear=True),patch('src.test_email.load_dotenv'),patch('src.email_notifier.smtplib.SMTP') as smtp:
            with self.assertRaises(ValueError):
                await send_test()
            smtp.assert_not_called()

    async def test_manual_false_dry_run_rejected(self):
        with patch.dict(os.environ,dict(ENV,DRY_RUN='false'),clear=True),patch('src.test_email.load_dotenv'),patch('src.email_notifier.smtplib.SMTP') as smtp:
            with self.assertRaises(ValueError):
                await send_test()
            smtp.assert_not_called()


class EmailTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ,{},clear=True):
            self.config=replace(Config.load(),data=Path(self.temp.name),product_limit=Decimal('100'))
        self.state=State(self.config.data)
        self.state.set('ninnin',{'stock':'OUT_OF_STOCK','purchase_completed':True})
        self.telegram=MagicMock(send=AsyncMock())
        self.shop=MagicMock(inspect_http=AsyncMock(return_value=Product(True,Decimal('94.39'),NAME,'EUR')))
        self.browser=AsyncMock()

    async def poll(self):
        await hybrid_cycle(self.config,self.state,self.telegram,self.shop,self.browser,'ninnin')

    async def test_both_once_even_after_restart(self):
        with patch('src.hybrid.send_stock_email',new=AsyncMock(return_value='sent')) as email:
            await self.poll()
            self.state=State(self.config.data)
            await self.poll()
            email.assert_awaited_once()
        self.telegram.send.assert_awaited_once()
        self.assertEqual(self.state.get('ninnin')['email_status'],'sent')

    async def test_email_failure_keeps_telegram_and_monitor(self):
        with patch.dict(os.environ,dict(ENV,SMTP_PORT='invalid'),clear=True):
            with self.assertLogs(level='ERROR'):
                await self.poll()
            await self.poll()
        self.telegram.send.assert_awaited_once()
        self.assertEqual(self.state.get('ninnin')['email_status'],'failed')
        self.assertFalse(self.state.get('ninnin')['email_notification_pending'])
        self.browser.assert_not_called()

    async def test_initial_available_no_email(self):
        self.state.set('ninnin',{'purchase_completed':True})
        with patch('src.hybrid.send_stock_email',new=AsyncMock()) as email:
            await self.poll()
            email.assert_not_called()
        self.telegram.send.assert_not_called()

    async def test_new_transition_resets_only_notification_flags(self):
        with patch('src.hybrid.send_stock_email',new=AsyncMock(return_value='sent')) as email:
            await self.poll()
            self.shop.inspect_http.return_value=Product(False,Decimal('94.39'),NAME,'EUR')
            await self.poll()
            self.shop.inspect_http.return_value=Product(True,Decimal('94.39'),NAME,'EUR')
            await self.poll()
            self.assertEqual(email.await_count,2)
        self.assertEqual(self.telegram.send.await_count,2)
        self.assertTrue(self.state.get('ninnin')['purchase_completed'])
        self.browser.assert_not_called()
