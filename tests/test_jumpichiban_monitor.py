import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from src.stock_only import stock_only_cycle
from src.shops.jumpichiban import JumpIchibanShop, URL, inspect_html, download_html, MAX_HTML_BYTES
from src.shops.base import Product, CriticalError
from src.state import State
from src.email_notifier import stock_email

class JumpMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state = State(self.directory)
        self.shop = JumpIchibanShop()
        self.shop.inspect_http = AsyncMock()
        self.telegram = SimpleNamespace(send=AsyncMock())
        self.email = patch('src.stock_only.send_stock_email',new_callable=AsyncMock)
        self.sender = self.email.start()
        self.sender.return_value = 'sent'

    async def asyncTearDown(self):
        self.email.stop()
        self.temp.cleanup()

    async def cycle(self, available):
        self.shop.inspect_http.return_value = Product(available,Decimal('104'),'Vol.2','USD')
        await stock_only_cycle(None,self.state,self.telegram,self.shop,'jumpichiban')

    async def test_transition_restart_and_restock(self):
        await self.cycle(False)
        await self.cycle(True)
        self.state = State(self.directory)
        await self.cycle(True)
        self.assertEqual(self.telegram.send.await_count,1)
        self.assertEqual(self.sender.await_count,1)
        self.assertIn(URL,self.telegram.send.call_args.args[0])
        await self.cycle(False)
        await self.cycle(True)
        self.assertEqual(self.sender.await_count,2)

    async def test_initial_stock_no_alert(self):
        await self.cycle(True)
        self.telegram.send.assert_not_awaited()
        self.sender.assert_not_awaited()

    async def test_notification_failure_no_duplicate(self):
        self.telegram.send.side_effect = RuntimeError('test')
        await self.cycle(False)
        await self.cycle(True)
        await self.cycle(True)
        self.assertEqual(self.telegram.send.await_count,1)
        self.sender.assert_awaited_once()
        self.assertEqual(self.state.get('jumpichiban')['notification_status'],'failed')

    async def test_browser_and_cart_disabled(self):
        with self.assertRaises(CriticalError): await self.shop.inspect(None)
        with self.assertRaises(CriticalError): await self.shop.prepare_checkout(None,None)

    async def test_access_restricted(self):
        with self.assertRaises(CriticalError): inspect_html('<h2>Access Restricted</h2>')

    async def test_email_names_ichiban_and_usd(self):
        subject,body = stock_email(dict(shop='jumpichiban',price='104',currency='USD',name='Vol.2',url=URL),Decimal('50'))
        self.assertIn('JUMP ICHIBAN',subject)
        self.assertIn('104.00 USD',body)
        self.assertNotIn('NIN-NIN',subject)


class JumpDownloadTests(unittest.TestCase):
    def response(self, chunks):
        response = MagicMock()
        response.status_code = 200
        response.headers = {'Content-Type':'text/html'}
        response.encoding = 'utf-8'
        response.iter_content.return_value = iter(chunks)
        response.__enter__.return_value = response
        return response

    def test_accepts_large_shopify_page(self):
        with patch('src.shops.jumpichiban.requests.get', return_value=self.response([b'x'*6_400_000])):
            self.assertEqual(len(download_html()),6_400_000)

    def test_still_limits_response(self):
        with patch('src.shops.jumpichiban.requests.get', return_value=self.response([b'x'*MAX_HTML_BYTES,b'x'])):
            with self.assertRaises(CriticalError): download_html()
