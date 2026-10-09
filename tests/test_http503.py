import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from src.state import State
from src.polling import ServiceUnavailable, service_paused
from src.stock_only import stock_only_cycle
from src.hybrid import hybrid_cycle
from src.shops.base import Product, CriticalError


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_shops_pause_recover_without_duplicates_or_cart(self):
        for name in ('ninnin', 'jumpichiban'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                state = State(Path(directory))
                state.set(name, dict(stock='OUT_OF_STOCK', purchase_attempted=True))
                shop = SimpleNamespace(inspect_http=AsyncMock(), url='https://example.test')
                telegram = SimpleNamespace(send=AsyncMock())
                browser = AsyncMock(side_effect=AssertionError('browser forbidden'))
                config = SimpleNamespace(product_limit=Decimal('120'))
                async def tick():
                    if name == 'ninnin':
                        await hybrid_cycle(config, state, telegram, shop, browser, name)
                    else:
                        await stock_only_cycle(config, state, telegram, shop, name)
                with patch('src.polling.time.time', return_value=1000), patch('src.hybrid.send_stock_email', new_callable=AsyncMock) as email1, patch('src.stock_only.send_stock_email', new_callable=AsyncMock) as email2:
                    email1.return_value = email2.return_value = 'sent'
                    shop.inspect_http.side_effect = ServiceUnavailable(900)
                    await tick()
                    await tick()
                    shop.inspect_http.assert_awaited_once()
                    self.assertEqual(state.get(name)['stock'], 'OUT_OF_STOCK')
                    self.assertEqual(state.get(name)['http_503_next_at'], 1900)
                    state = State(Path(directory))
                    self.assertTrue(service_paused(state, name))
                    telegram.send.assert_not_awaited()
                    with patch('src.polling.time.time', return_value=2000):
                        shop.inspect_http.side_effect = None
                        shop.inspect_http.return_value = Product(True, Decimal('95'), 'Vol.2', 'EUR')
                        telegram.send.side_effect = RuntimeError('delivery uncertain')
                        await tick()
                        await tick()
                    telegram.send.assert_awaited_once()
                    self.assertEqual(email1.await_count + email2.await_count, 1)
                    self.assertEqual(state.get(name)['http_503_count'], 0)
                    browser.assert_not_awaited()

    async def test_retry_budget_persists_and_other_shop_continues(self):
        from src.main import run
        from src.shops.jumpichiban import JumpIchibanShop
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = SimpleNamespace(data=data, screenshots=data, shops=('ninnin','jumpichiban'), interval=30)
            state = State(data)
            shop = JumpIchibanShop()
            shop.inspect_http = AsyncMock(side_effect=ServiceUnavailable())
            telegram = SimpleNamespace(send=AsyncMock())
            for index in range(4):
                with patch('src.polling.time.time', return_value=1000 + index * 3000):
                    await stock_only_cycle(config, state, telegram, shop, 'ninnin')
            state = State(data)
            self.assertTrue(state.get('ninnin')['http_503_suspended'])
            other = JumpIchibanShop()
            other.inspect_http = AsyncMock(return_value=Product(False,Decimal('103'),'Vol.2','USD'))
            modules = [SimpleNamespace(create_shop=lambda: shop), SimpleNamespace(create_shop=lambda: other)]
            with patch('src.main.Telegram', return_value=telegram), patch('src.main.async_playwright') as browser, patch('src.main.importlib.import_module', side_effect=modules):
                await run(config, once=True)
            other.inspect_http.assert_awaited_once()
            self.assertTrue((data/'heartbeat').exists())
            self.assertFalse((data/'critical.json').exists())
            browser.assert_not_called()
            telegram.send.assert_not_awaited()

    async def test_read_only_never_opens_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            shop = SimpleNamespace(inspect_http=AsyncMock(return_value=Product(True, Decimal('95'), 'Vol.2', 'EUR')))
            browser = AsyncMock(side_effect=AssertionError('forbidden'))
            with patch.dict('os.environ', {'MONITOR_ONLY':'true'}):
                await hybrid_cycle(SimpleNamespace(), State(Path(directory)), SimpleNamespace(send=AsyncMock()), shop, browser, 'ninnin')
            browser.assert_not_awaited()


class DownloadTests(unittest.TestCase):
    def test_statuses(self):
        for module in ('ninnin_http', 'jumpichiban'):
            for status in (503, 403):
                with self.subTest(module=module, status=status):
                    response = MagicMock(status_code=status, headers={'Retry-After':'600'})
                    response.__enter__.return_value = response
                    with patch(f'src.shops.{module}.requests.get', return_value=response) as get:
                        from importlib import import_module
                        with self.assertRaises(ServiceUnavailable if status == 503 else CriticalError) as caught:
                            import_module(f'src.shops.{module}').download_html()
                    get.assert_called_once()
                    response.iter_content.assert_not_called()
                    if status == 503:
                        self.assertEqual(caught.exception.retry_after,600)
