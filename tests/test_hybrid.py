import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import requests
from src.config import Config
from src.state import State
from src.main import run
from src.hybrid import hybrid_cycle
from src.shops.base import Product, CartPreview, CriticalError
from src.shops.ninnin import NAME, PlaywrightBlocked
from src.shops.ninnin_http import parse_html, download_html

HTML = (Path(__file__).parent / 'fixtures/ninnin_out_of_stock.html').read_text()
AVAILABLE = HTML.replace('https://schema.org/OutOfStock','https://schema.org/InStock').replace('<span class="exclusive soon"> Soon available </span>', '<button>Add to cart</button>')


class HttpParserTests(unittest.TestCase):
    def test_out_of_stock(self):
        product = parse_html(HTML)
        self.assertFalse(product.available)
        self.assertEqual(product.price, Decimal('94.39'))
        self.assertEqual(product.name, NAME)
        self.assertEqual(product.currency, 'EUR')

    def test_available(self):
        self.assertTrue(parse_html(AVAILABLE).available)

    def test_unexpected_html(self):
        for html in ('<h1>Other product</h1>', HTML.replace('value="240042"','value="999"'), HTML.replace('94,39 €','99,99 €')):
            with self.assertRaises(CriticalError):
                parse_html(html)

    def test_conflicting_stock(self):
        with self.assertRaises(CriticalError):
            parse_html(HTML.replace('https://schema.org/OutOfStock','https://schema.org/InStock'))

    def test_protection_no_bypass(self):
        with self.assertRaises(CriticalError):
            parse_html('<title>Just a moment</title><h1>Verify you are human</h1>')

    def test_http_errors_no_retry(self):
        for status in (403, 429, 500, 302):
            response = MagicMock(status_code=status)
            with patch('src.shops.ninnin_http.requests.get') as get:
                get.return_value.__enter__.return_value = response
                with self.assertRaises(CriticalError):
                    download_html()
                get.assert_called_once()
                self.assertIsNot(get.call_args.kwargs['verify'], False)
                self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_http_success(self):
        response = MagicMock(status_code=200, encoding='utf-8', headers={'Content-Type':'text/html; charset=utf-8'})
        response.iter_content.return_value = [HTML.encode()]
        with patch('src.shops.ninnin_http.requests.get') as get:
            get.return_value.__enter__.return_value = response
            self.assertEqual(parse_html(download_html()).price, Decimal('94.39'))

    def test_network_error(self):
        with patch('src.shops.ninnin_http.requests.get', side_effect=requests.ConnectionError):
            with self.assertRaises(CriticalError):
                download_html()

    def test_minimum_interval_and_quantity(self):
        with patch.dict(os.environ, {'CHECK_INTERVAL_SECONDS':'29'}):
            with self.assertRaises(ValueError):
                Config.load()
        with patch.dict(os.environ, {'CHECK_INTERVAL_SECONDS':'30','MAX_QUANTITY':'1'}):
            self.assertEqual(Config.load().interval, 30)
        for quantity in ('0','2','01'):
            with patch.dict(os.environ, {'MAX_QUANTITY':quantity}):
                with self.assertRaises(ValueError):
                    Config.load()


class HybridTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        email_patch = patch('src.hybrid.send_stock_email', new=AsyncMock(return_value='disabled'))
        email_patch.start()
        self.addCleanup(email_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {}, clear=True):
            self.config = replace(Config.load(), data=Path(self.temp.name), screenshots=Path(self.temp.name),
                                  shops=('ninnin',), product_limit=Decimal('100'), total_limit=Decimal('110'), allow_no_telegram=True)
        self.state = State(self.config.data)
        self.telegram = MagicMock(send=AsyncMock())
        self.out = parse_html(HTML)
        self.stock = parse_html(AVAILABLE)
        self.page = MagicMock(close=AsyncMock())
        self.get_page = AsyncMock(return_value=self.page)
        self.shop = MagicMock(currency='EUR', inspect_http=AsyncMock(return_value=self.stock),
                              inspect=AsyncMock(return_value=self.stock), capture=AsyncMock(),
                              prepare_checkout=AsyncMock(return_value=CartPreview(1,Decimal('94.39'),Decimal('94.39'),'EUR')))

    async def poll(self):
        await hybrid_cycle(self.config,self.state,self.telegram,self.shop,self.get_page,'ninnin')

    async def test_repeated_out_of_stock_no_browser_or_notifications(self):
        self.shop.inspect_http.return_value = self.out
        await self.poll()
        await self.poll()
        self.get_page.assert_not_called()
        self.telegram.send.assert_not_called()
        self.assertEqual(self.state.get('ninnin')['stock'],'OUT_OF_STOCK')

    async def test_transition_notifies_before_browser_and_records_event(self):
        self.shop.inspect_http.return_value = self.out
        await self.poll()
        self.shop.inspect_http.return_value = self.stock
        async def open_browser(name):
            self.telegram.send.assert_awaited_once()
            self.assertTrue(State(self.config.data).get(name)['purchase_attempted'])
            return self.page
        self.get_page.side_effect = open_browser
        await self.poll()
        self.shop.capture.assert_awaited_once_with(self.page,'stock-event')
        self.assertEqual(self.state.get('ninnin')['stock_event']['price'],'94.39')
        self.assertIn('at', self.state.get('ninnin')['stock_event'])
        self.assertTrue(self.state.get('ninnin')['purchase_completed'])
        self.assertEqual(self.state.get('ninnin')['outcome'],'DRY_RUN_CART_VERIFIED')

    async def test_completed_survives_stock_flips_and_restart(self):
        await self.poll()
        self.shop.inspect_http.return_value = self.out
        await self.poll()
        self.state = State(self.config.data)
        self.shop.inspect_http.return_value = self.stock
        await self.poll()
        self.get_page.assert_awaited_once()
        self.shop.prepare_checkout.assert_awaited_once()
        self.assertTrue(self.state.get('ninnin')['purchase_completed'])

    async def test_blocked_browser_alert_and_no_retry(self):
        self.shop.inspect.side_effect = PlaywrightBlocked('HTTP 403')
        await self.poll()
        await self.poll()
        self.assertEqual(self.state.get('ninnin')['status'],'PLAYWRIGHT_BLOCKED')
        self.get_page.assert_awaited_once()
        self.assertEqual(self.telegram.send.await_count,1)
        self.shop.prepare_checkout.assert_not_called()
        self.assertTrue(self.state.get('ninnin')['purchase_attempted'])
        self.assertFalse(self.state.get('ninnin')['purchase_completed'])

    async def test_high_price_no_browser(self):
        self.state.set('ninnin', {'stock':'OUT_OF_STOCK'})
        self.config = replace(self.config, product_limit=Decimal('50'))
        await self.poll()
        self.get_page.assert_not_called()
        self.assertFalse(self.state.get('ninnin')['purchase_attempted'])
        self.telegram.send.assert_awaited_once()

    async def test_unknown_outcome_no_retry(self):
        self.shop.prepare_checkout.side_effect = CriticalError('Carrito incierto')
        with self.assertRaises(CriticalError):
            await self.poll()
        self.state = State(self.config.data)
        await self.poll()
        self.shop.prepare_checkout.assert_awaited_once()
        self.assertTrue(self.state.get('ninnin')['purchase_attempted'])

    async def test_legacy_completed_never_resets(self):
        self.state.set('ninnin',{'status':'completed','stock':'IN_STOCK'})
        self.shop.inspect_http.return_value = self.out
        await self.poll()
        self.shop.inspect_http.return_value = self.stock
        await self.poll()
        self.get_page.assert_not_called()
        self.assertTrue(self.state.get('ninnin')['purchase_completed'])

    async def test_main_run_out_of_stock_does_not_start_playwright(self):
        with patch('src.shops.ninnin.NinNinShop.inspect_http',new=AsyncMock(return_value=self.out)), patch('src.main.async_playwright') as browser:
            await run(self.config,once=True)
            browser.assert_not_called()
        self.assertFalse((self.config.data/'critical.json').exists())
        self.assertTrue((self.config.data/'heartbeat').exists())
