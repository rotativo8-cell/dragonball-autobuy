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
from src.polling import RateLimited, retry_after_seconds

class JumpMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state = State(self.directory)
        self.shop = JumpIchibanShop()
        self.shop.inspect_http = AsyncMock()
        self.telegram = SimpleNamespace(send=AsyncMock())
        self.clock = 1000
        self.email = patch('src.stock_only.send_stock_email',new_callable=AsyncMock)
        self.sender = self.email.start()
        self.sender.return_value = 'sent'

    async def asyncTearDown(self):
        self.email.stop()
        self.temp.cleanup()

    async def cycle(self, available):
        self.shop.inspect_http.return_value = Product(available,Decimal('104'),'Vol.2','USD')
        self.clock += 301
        with patch('src.stock_only.time.time', return_value=self.clock):
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


class JumpPollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.state = State(self.directory)
        self.shop = JumpIchibanShop()
        self.shop.inspect_http = AsyncMock(return_value=Product(False, Decimal('104'), 'Vol.2', 'USD'))
        self.telegram = SimpleNamespace(send=AsyncMock())
        self.clock = 1000

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def tick(self):
        with patch('src.stock_only.time.time', return_value=self.clock):
            await stock_only_cycle(None, self.state, self.telegram, self.shop, 'jumpichiban')

    async def test_normal_interval_and_restart(self):
        await self.tick()
        deadline = self.state.get('jumpichiban')['next_check_at']
        self.assertGreaterEqual(deadline, 1180)
        self.assertLessEqual(deadline, 1300)
        self.state = State(self.directory)
        self.clock = deadline - 1
        await self.tick()
        self.shop.inspect_http.assert_awaited_once()
        self.clock = deadline
        await self.tick()
        self.assertEqual(self.shop.inspect_http.await_count, 2)

    async def test_backoff_stock_preserved_no_alert_and_recovery(self):
        self.state.set('jumpichiban', {'stock': 'OUT_OF_STOCK', 'notification_status': 'sent'})
        self.shop.inspect_http.side_effect = RateLimited()
        for delay in (900, 1800, 3600, 3600):
            await self.tick()
            entry = self.state.get('jumpichiban')
            self.assertEqual(entry['next_check_at'], self.clock + delay)
            self.assertEqual(entry['stock'], 'OUT_OF_STOCK')
            self.assertEqual(entry['notification_status'], 'sent')
            calls = self.shop.inspect_http.await_count
            self.state = State(self.directory)
            self.clock += delay - 1
            await self.tick()
            self.assertEqual(self.shop.inspect_http.await_count, calls)
            self.clock += 1
        self.telegram.send.assert_not_awaited()
        self.shop.inspect_http.side_effect = None
        await self.tick()
        entry = self.state.get('jumpichiban')
        self.assertEqual(entry['rate_limit_count'], 0)
        self.assertEqual(entry['last_http_status'], 200)
        self.assertGreaterEqual(entry['next_check_at'] - self.clock, 180)
        self.assertLessEqual(entry['next_check_at'] - self.clock, 300)

    async def test_retry_after_can_exceed_one_hour(self):
        self.shop.inspect_http.side_effect = RateLimited(7200)
        await self.tick()
        self.assertEqual(self.state.get('jumpichiban')['next_check_at'], 8200)

    async def test_other_error_still_critical(self):
        self.shop.inspect_http.side_effect = CriticalError('HTTP 403')
        with self.assertRaises(CriticalError):
            await self.tick()

    async def test_other_shop_and_heartbeat_continue_after_429(self):
        from src.main import run
        self.shop.inspect_http.side_effect = RateLimited()
        other = JumpIchibanShop()
        other.stock_only = False
        config = SimpleNamespace(data=self.directory, screenshots=self.directory,
                                 shops=('jumpichiban', 'ninnin'), interval=45)
        modules = [SimpleNamespace(create_shop=lambda: self.shop),
                   SimpleNamespace(create_shop=lambda: other)]
        with patch('src.main.Telegram', return_value=self.telegram), \
             patch('src.hybrid.hybrid_cycle', new_callable=AsyncMock) as ninnin, \
             patch('src.main.async_playwright') as browser, \
             patch('src.main.importlib.import_module', side_effect=modules):
            await run(config, once=True)
        ninnin.assert_awaited_once()
        browser.assert_not_called()
        self.assertTrue((self.directory / 'heartbeat').exists())
        self.assertFalse((self.directory / 'critical.json').exists())


class JumpRateLimitDownloadTests(unittest.TestCase):
    def test_429_does_not_read_body_or_retry(self):
        response = MagicMock()
        response.status_code = 429
        response.headers = {'Retry-After': '7200'}
        response.__enter__.return_value = response
        with patch('src.shops.jumpichiban.requests.get', return_value=response) as get:
            with self.assertRaises(RateLimited) as caught:
                download_html()
        self.assertEqual(caught.exception.retry_after, 7200)
        get.assert_called_once()
        response.iter_content.assert_not_called()
        response.__exit__.assert_called_once()

    def test_403_remains_critical(self):
        response = MagicMock()
        response.status_code = 403
        response.__enter__.return_value = response
        with patch('src.shops.jumpichiban.requests.get', return_value=response):
            with self.assertRaises(CriticalError) as caught:
                download_html()
        self.assertNotIsInstance(caught.exception, RateLimited)

    def test_retry_after_headers(self):
        with patch('src.polling.time.time', return_value=0):
            self.assertEqual(retry_after_seconds('7200'), 7200)
            self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 02:00:00 GMT'), 7200)
            for invalid in (None, '', 'garbage', '-30', 'NaN'):
                self.assertEqual(retry_after_seconds(invalid), 0)
