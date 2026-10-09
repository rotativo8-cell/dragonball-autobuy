import json
import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch
from playwright.async_api import async_playwright
from src.config import Config
from src.main import cycle
from src.state import State
from src.shops.base import CriticalError, CartPreview
from src.shops.ninnin import NinNinShop, NAME, URL, CART_URL, amount

FIXTURE = Path(__file__).parent / 'fixtures/ninnin_out_of_stock.html'


def available_html():
    original = FIXTURE.read_text()
    original = original.replace('https://schema.org/OutOfStock', 'https://schema.org/InStock')
    original = original.replace('<span class="exclusive soon"> Soon available </span>', '<button type="button">Add to cart</button>')
    original = original.replace('id="quantity_wanted_p" style="display: none;"', 'id="quantity_wanted_p"')
    return original


class NinNinTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        test_environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'PLAYWRIGHT_BROWSERS_PATH') if key in os.environ}
        test_environment.update(DRY_RUN='true', NINNIN_CURRENCY='EUR', SCREENSHOT_DIR=str(self.directory))
        self.environment = patch.dict(os.environ, test_environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.telegram = AsyncMock()
        self.config = replace(Config.load(), product_limit=Decimal('100'), total_limit=Decimal('110'), allow_no_telegram=True)
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page()
        self.shop = NinNinShop()
        self.html = FIXTURE.read_text()
        self.status = 200
        self.cart_html = ''
        self.requests = []
        async def route(request):
            self.requests.append(request.request.url)
            body = self.cart_html if request.request.url == CART_URL else self.html
            await request.fulfill(status=self.status, content_type='text/html; charset=utf-8', body=body)
        await self.page.route('**/*', route)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.temp.cleanup()

    async def test_notification_credentials_are_isolated(self):
        self.assertEqual(self.config.token, "")
        self.assertEqual(self.config.chat, "")
        self.assertNotIn("EMAIL_ENABLED", os.environ)
        self.assertNotIn("SMTP_PASSWORD", os.environ)
        self.assertIsInstance(self.telegram, AsyncMock)

    async def test_real_html_out_of_stock(self):
        product = await self.shop.inspect(self.page)
        self.assertFalse(product.available)
        self.assertEqual(product.name, NAME)
        self.assertEqual(product.price, Decimal('94.39'))
        self.assertEqual(product.currency, 'EUR')
        self.assertFalse(self.shop.button_detected)

    async def test_out_of_stock_no_cart_and_transition_screenshot(self):
        state = State(self.directory)
        await cycle(self.config, state, self.telegram, self.shop, self.page, 'ninnin')
        self.assertEqual(state.get('ninnin')['stock'], 'OUT_OF_STOCK')
        self.assertEqual(self.requests, [URL])
        self.assertEqual(len(list(self.directory.glob('*out_of_stock*.png'))), 1)
        await cycle(self.config, state, self.telegram, self.shop, self.page, 'ninnin')
        self.assertEqual(len(list(self.directory.glob('*out_of_stock*.png'))), 1)

    async def test_in_stock_button_and_structured_price(self):
        self.html = available_html()
        product = await self.shop.inspect(self.page)
        self.assertTrue(product.available)
        self.assertTrue(self.shop.button_detected)

    async def test_other_product_button_cannot_mark_in_stock(self):
        self.html += '<button>Add to cart</button>'
        product = await self.shop.inspect(self.page)
        self.assertFalse(product.available)
        self.assertFalse(self.shop.button_detected)

    async def test_conflicting_stock_fails(self):
        self.html = available_html().replace('https://schema.org/InStock', 'https://schema.org/OutOfStock')
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)
        self.assertTrue(list(self.directory.glob('*inspection-error*.png')))

    async def test_price_mismatch_fails(self):
        self.html = self.html.replace('94,39 €', '99,99 €')
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)

    async def test_wrong_identity_fails(self):
        self.html = self.html.replace('value="240042"', 'value="240043"')
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)

    async def test_missing_selector_screenshot(self):
        self.html = self.html.replace('id="our_price_display"', 'id="renamed"')
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)
        self.assertTrue(list(self.directory.glob('*.png')))

    async def test_cloudflare_stops(self):
        self.html = '<title>Just a moment...</title><h1>Verify you are human</h1>'
        self.status = 403
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)
        self.assertTrue(self.shop.captcha_cloudflare)
        self.assertFalse(self.shop.add_attempted)
        self.assertTrue(list(self.directory.glob('*protection*.png')))

    async def test_administrative_denial_stops_without_claiming_cloudflare(self):
        self.html = '<h1>403 Forbidden</h1>Request forbidden by administrative rules.'
        self.status = 403
        with self.assertRaises(CriticalError):
            await self.shop.inspect(self.page)
        self.assertTrue(self.shop.blocked)
        self.assertFalse(self.shop.captcha_cloudflare)

    async def test_limit_prevents_click(self):
        self.html = available_html()
        await self.shop.inspect(self.page)
        with self.assertRaises(CriticalError):
            await self.shop.prepare_checkout(self.page, replace(self.config, product_limit=Decimal('50')))
        self.assertFalse(self.shop.add_attempted)

    async def test_wrong_currency_prevents_click(self):
        self.html = available_html()
        await self.shop.inspect(self.page)
        self.shop.currency = 'JPY'
        with self.assertRaises(CriticalError):
            await self.shop.prepare_checkout(self.page, self.config)
        self.assertFalse(self.shop.add_attempted)

    async def test_nonempty_cart_prevents_click(self):
        self.html = available_html().replace('id="mobile_cart_qty">0', 'id="mobile_cart_qty">1')
        await self.shop.inspect(self.page)
        with self.assertRaises(CriticalError):
            await self.shop.prepare_checkout(self.page, self.config)
        self.assertFalse(self.shop.add_attempted)

    async def test_one_unit_then_stop_before_checkout(self):
        # Contrato de carrito sintético; no se afirma que el checkout real haya sido validado.
        self.html = available_html() + '''<script>Array.from(document.querySelectorAll('form button')).find(b=>b.textContent==='Add to cart').onclick=()=>{
            window.added=Number(document.querySelector('[name=qty]').value);
            document.querySelector('#mobile_cart_qty').textContent='1';
            document.querySelector('.desktop-cart-quantity').textContent='1';
        };</script>'''
        self.cart_html = f'''<div id="mobile_cart_qty">1</div><span class="desktop-cart-quantity">1</span>
        <table><tr><td><a href="{URL}">{NAME}</a></td><td><input name="quantity" value="1"></td></tr></table>
        <div><span>Subtotal</span><span>94,39 €</span></div><button>Confirm order</button>'''
        await self.shop.inspect(self.page)
        cart = await self.shop.prepare_checkout(self.page, self.config)
        self.assertIsInstance(cart, CartPreview)
        self.assertEqual(cart.quantity, 1)
        self.assertEqual(cart.subtotal, Decimal('94.39'))
        self.assertEqual(self.requests, [URL, CART_URL])
        self.assertTrue(self.shop.add_attempted)

    async def test_wrong_cart_quantity_fails(self):
        self.cart_html = f'<div id="mobile_cart_qty">2</div><table><tr><td><a href="{URL}">{NAME}</a></td><td><input name="quantity" value="2"></td></tr></table>'
        await self.page.goto(CART_URL)
        with self.assertRaises(CriticalError):
            await self.shop.read_cart(self.page)

    def test_money_formats(self):
        for text in ('94,39 €', '94.39 EUR'):
            self.assertEqual(amount(text), Decimal('94.39'))
        self.assertEqual(amount('1.234,56 €'), Decimal('1234.56'))
        with self.assertRaises(CriticalError):
            amount('unknown')

    async def test_total_limit_prevents_click(self):
        self.html = available_html()
        await self.shop.inspect(self.page)
        with self.assertRaises(CriticalError):
            await self.shop.prepare_checkout(self.page, replace(self.config, total_limit=Decimal('90')))
        self.assertFalse(self.shop.add_attempted)

    async def test_unexpected_checkout_stops(self):
        self.html = '<h1>Checkout</h1><button>Pay now</button>'
        await self.page.goto('https://www.nin-nin-game.com/en/order')
        with self.assertRaises(CriticalError):
            await self.shop.guard(self.page)

    async def test_cart_preview_persisted_and_not_repeated(self):
        class PreviewShop:
            calls = 0
            async def inspect(self, page):
                from src.shops.base import Product
                return Product(True, Decimal('94.39'), NAME, 'EUR')
            async def prepare_checkout(self, page, config):
                self.calls += 1
                return CartPreview(1, Decimal('94.39'), None, 'EUR')
        shop = PreviewShop()
        state = State(self.directory)
        await cycle(self.config, state, self.telegram, shop, None, 'ninnin')
        await cycle(self.config, State(self.directory), self.telegram, shop, None, 'ninnin')
        self.assertEqual(shop.calls, 1)
        self.telegram.send.assert_awaited_once()
        self.assertEqual(state.get('ninnin')['status'], 'completed')
        self.assertIsNone(state.get('ninnin')['subtotal'])
        self.assertNotIn('total', state.get('ninnin'))
