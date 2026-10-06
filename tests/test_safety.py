import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from src.config import Config
from src.shops.base import Checkout, CriticalError, Product, check_checkout, check_product
from src.state import State
from src.main import cycle
from src.telegram import Telegram


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(os.environ, {}, clear=True):
            self.config = replace(Config.load(), allow_no_telegram=True)

    def test_live_mode_blocked(self):
        with patch.dict(os.environ, {'DRY_RUN': 'false'}):
            with self.assertRaises(ValueError):
                Config.load()

    def test_price_guards(self):
        for price in (None, Decimal('NaN'), Decimal('-1'), Decimal('51')):
            with self.assertRaises(CriticalError):
                check_product(Product(True, price), self.config)
        for checkout in (
            Checkout(2, Decimal('20'), Decimal('5'), Decimal('25'), True),
            Checkout(1, Decimal('20'), Decimal('11'), Decimal('31'), True),
            Checkout(1, Decimal('20'), Decimal('5'), Decimal('61'), True),
            Checkout(1, Decimal('20'), Decimal('5'), Decimal('25'), False),
            Checkout(1, Decimal('20'), Decimal('5'), Decimal('10'), True),
        ):
            with self.assertRaises(CriticalError):
                check_checkout(checkout, self.config)

    async def test_persistence_and_duplicates(self):
        class FakeShop:
            calls = 0
            async def inspect(self, page):
                return Product(True, Decimal('20'))
            async def prepare_checkout(self, page, config):
                self.calls += 1
                return Checkout(1, Decimal('20'), Decimal('5'), Decimal('25'), True)
        shop = FakeShop()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            await cycle(self.config, State(path), Telegram(self.config), shop, None, 'demo')
            await cycle(self.config, State(path), Telegram(self.config), shop, None, 'demo')
            self.assertEqual(shop.calls, 1)
            self.assertEqual(State(path).get('demo')['status'], 'completed')

    async def test_reserved_event_survives_failure(self):
        class BrokenShop:
            async def inspect(self, page):
                return Product(True, Decimal('20'))
            async def prepare_checkout(self, page, config):
                raise CriticalError('CAPTCHA')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(CriticalError):
                await cycle(self.config, State(path), Telegram(self.config), BrokenShop(), None, 'demo')
            self.assertEqual(State(path).get('demo')['status'], 'claimed')

    async def test_critical_marker_and_screenshot(self):
        from src.main import critical
        class Page:
            def is_closed(self):
                return False
            async def screenshot(self, path, full_page):
                Path(path).write_bytes(b'test screenshot')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config = replace(self.config, data=path, screenshots=path)
            await critical(config, Telegram(config), Page(), 'CAPTCHA')
            self.assertTrue((path / 'critical.json').exists())
            self.assertEqual(len(list(path.glob('error-*.png'))), 1)

    async def test_critical_prevents_shop_start(self):
        from src.main import run
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'critical.json').write_text('{}')
            config = replace(self.config, data=path, screenshots=path)
            with patch('src.main.async_playwright') as browser:
                await run(config, once=True)
                browser.assert_not_called()
