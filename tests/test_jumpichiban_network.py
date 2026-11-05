"""Jump Ichiban: errores de transporte diferidos, TLS/codificación críticos."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import requests

from src.polling import NetworkUnavailable
from src.shops.base import CriticalError, Product
from src.shops.jumpichiban import JumpIchibanShop, download_html
from src.state import State
from src.stock_only import stock_only_cycle


class JumpTransportDownloadTests(unittest.TestCase):
    def test_connection_and_timeout_are_deferred_without_retry(self):
        for error in (requests.ReadTimeout('private detail'),
                      requests.ConnectionError('private detail')):
            with self.subTest(kind=type(error).__name__):
                with patch('src.shops.jumpichiban.requests.get', side_effect=error) as get:
                    with self.assertRaises(NetworkUnavailable) as caught:
                        download_html()
                self.assertEqual(caught.exception.kind, type(error).__name__)
                self.assertNotIn('private detail', str(caught.exception))
                get.assert_called_once()

    def test_tls_stays_critical_and_is_identifiable(self):
        with patch('src.shops.jumpichiban.requests.get',
                   side_effect=requests.exceptions.SSLError('private detail')):
            with self.assertRaises(CriticalError) as caught:
                download_html()
        self.assertIn('SSLError', str(caught.exception))
        self.assertNotIn('private detail', str(caught.exception))

    def test_invalid_encoding_stays_critical_and_is_identifiable(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {'Content-Type': 'text/html'}
        response.encoding = 'utf-8'
        response.iter_content.return_value = [b'\xff']
        with patch('src.shops.jumpichiban.requests.get', return_value=response):
            with self.assertRaises(CriticalError) as caught:
                download_html()
        self.assertIn('codificación', str(caught.exception))


class JumpTransportCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_preserves_state_and_pause_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            state = State(path)
            state.set('jumpichiban', {'stock': 'OUT_OF_STOCK',
                                     'notification_status': 'sent',
                                     'purchase_attempted': True})
            shop = JumpIchibanShop()
            shop.inspect_http = AsyncMock(side_effect=NetworkUnavailable('ReadTimeout'))
            telegram = SimpleNamespace(send=AsyncMock())
            with patch('src.polling.time.time', return_value=1000):
                await stock_only_cycle(None, state, telegram, shop, 'jumpichiban')
            entry = state.get('jumpichiban')
            self.assertEqual(entry['network_next_at'], 1300)
            self.assertEqual(entry['stock'], 'OUT_OF_STOCK')
            self.assertEqual(entry['notification_status'], 'sent')
            self.assertTrue(entry['purchase_attempted'])
            state = State(path)
            with patch('src.polling.time.time', return_value=1299):
                await stock_only_cycle(None, state, telegram, shop, 'jumpichiban')
            shop.inspect_http.assert_awaited_once()
            shop.inspect_http.side_effect = None
            shop.inspect_http.return_value = Product(False, Decimal('104'), 'Vol.2', 'USD')
            with patch('src.polling.time.time', return_value=1300):
                await stock_only_cycle(None, state, telegram, shop, 'jumpichiban')
            self.assertEqual(state.get('jumpichiban')['network_error_count'], 0)
            telegram.send.assert_not_awaited()

    async def test_other_shop_and_heartbeat_continue_after_timeout(self):
        from src.main import run
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shop = JumpIchibanShop()
            shop.inspect_http = AsyncMock(side_effect=NetworkUnavailable('ConnectionError'))
            other = JumpIchibanShop()
            other.stock_only = False
            config = SimpleNamespace(data=path, screenshots=path,
                                     shops=('jumpichiban', 'ninnin'), interval=45)
            modules = [SimpleNamespace(create_shop=lambda: shop),
                       SimpleNamespace(create_shop=lambda: other)]
            with patch('src.main.Telegram', return_value=SimpleNamespace(send=AsyncMock())), \
                 patch('src.hybrid.hybrid_cycle', new_callable=AsyncMock) as ninnin, \
                 patch('src.main.async_playwright') as browser, \
                 patch('src.main.importlib.import_module', side_effect=modules):
                await run(config, once=True)
            ninnin.assert_awaited_once()
            browser.assert_not_called()
            self.assertTrue((path / 'heartbeat').exists())
            self.assertFalse((path / 'critical.json').exists())
