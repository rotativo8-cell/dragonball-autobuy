import os
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from src.config import Config
from src.hybrid import hybrid_cycle
from src.shops.base import Product
from src.shops.ninnin import NAME, URL
from src.state import State
from src.stock_alerts import ninnin_stock_message
from src.test_telegram_stock import send_test


def event(price='94.39'):
    return dict(name=NAME,price=price,currency='EUR',url=URL,previous_stock='OUT_OF_STOCK')


class StockMessageTests(unittest.TestCase):
    def test_in_stock_format_and_full_url(self):
        message = ninnin_stock_message(event(),Decimal('100'))
        self.assertTrue(message.startswith('🚨 STOCK NIN-NIN'))
        self.assertIn('Nin-Nin Game',message)
        self.assertIn(NAME,message)
        self.assertIn('💰 94.39 EUR',message)
        self.assertIn('🛒 COMPRAR AHORA:\n'+URL,message)

    def test_price_above_limit(self):
        message = ninnin_stock_message(event(),Decimal('50'))
        self.assertTrue(message.startswith('⚠️ STOCK DETECTADO PERO PRECIO SUPERIOR AL LÍMITE'))
        self.assertIn('94.39 EUR',message)
        self.assertIn('Límite: 50.00 EUR',message)
        self.assertIn(URL,message)
        self.assertNotIn('COMPRAR AHORA',message)

    def test_equal_limit_is_normal_alert(self):
        self.assertTrue(ninnin_stock_message(event(),Decimal('94.39')).startswith('🚨'))


class StockTransitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        email_patch = patch('src.hybrid.send_stock_email', new=AsyncMock(return_value='disabled'))
        email_patch.start()
        self.addCleanup(email_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ,{},clear=True):
            self.config = replace(Config.load(),data=Path(self.temp.name),product_limit=Decimal('100'))
        self.state = State(self.config.data)
        self.telegram = MagicMock(send=AsyncMock())
        self.shop = MagicMock(inspect_http=AsyncMock(return_value=Product(True,Decimal('94.39'),NAME,'EUR')))
        self.get_page = AsyncMock()

    async def poll(self):
        await hybrid_cycle(self.config,self.state,self.telegram,self.shop,self.get_page,'ninnin')

    async def test_no_notice_for_first_observation_in_stock(self):
        self.state.set('ninnin',{'purchase_completed':True})
        await self.poll()
        self.telegram.send.assert_not_called()

    async def test_transition_and_no_duplicate_after_restart(self):
        self.state.set('ninnin',{'stock':'OUT_OF_STOCK','purchase_completed':True})
        await self.poll()
        self.state = State(self.config.data)
        await self.poll()
        self.telegram.send.assert_awaited_once()
        self.assertIn('🚨 STOCK NIN-NIN',self.telegram.send.call_args.args[0])
        self.assertIn(URL,self.telegram.send.call_args.args[0])
        self.get_page.assert_not_called()

    async def test_high_price_alert_once_no_cart(self):
        self.config = replace(self.config,product_limit=Decimal('50'))
        self.state.set('ninnin',{'stock':'OUT_OF_STOCK'})
        await self.poll()
        await self.poll()
        self.telegram.send.assert_awaited_once()
        self.assertIn('⚠️ STOCK DETECTADO PERO PRECIO SUPERIOR AL LÍMITE',self.telegram.send.call_args.args[0])
        self.get_page.assert_not_called()
        self.assertFalse(self.state.get('ninnin')['purchase_attempted'])

    async def test_pending_alert_uses_original_event_not_new_poll_price(self):
        self.state.set('ninnin',{'stock':'IN_STOCK','purchase_completed':True,
                              'notification_pending':True,'stock_event':event('110.00')})
        await self.poll()
        message=self.telegram.send.call_args.args[0]
        self.assertIn('110.00 EUR',message)
        self.assertIn('SUPERIOR AL LÍMITE',message)

    async def test_old_pending_without_transition_not_sent(self):
        self.state.set('ninnin',{'stock':'IN_STOCK','purchase_completed':True,
                              'notification_pending':True,'stock_event':{'price':'94.39'}})
        await self.poll()
        self.telegram.send.assert_not_called()


class ManualTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_dotenv_sends_fiction_without_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'.env').write_text('TELEGRAM_BOT_TOKEN=fake-test-token\nTELEGRAM_CHAT_ID=fake-test-chat\nDRY_RUN=true\n')
            (path/'state.json').write_text('{"unchanged":true}')
            before={file.name:file.read_bytes() for file in path.iterdir()}
            with patch.dict(os.environ,{},clear=True),patch('src.test_telegram_stock.Path.cwd',return_value=path),patch('src.test_telegram_stock.Telegram') as telegram:
                telegram.return_value.send=AsyncMock()
                await send_test()
                configuration=telegram.call_args.args[0]
                self.assertEqual(configuration.token,'fake-test-token')
                self.assertEqual(configuration.chat,'fake-test-chat')
                self.assertFalse(configuration.allow_no_telegram)
                message=telegram.return_value.send.call_args.args[0]
                self.assertIn('PRUEBA FICTICIA',message)
                self.assertIn('🚨 STOCK NIN-NIN',message)
                self.assertIn(URL,message)
            self.assertEqual(before,{file.name:file.read_bytes() for file in path.iterdir()})

    async def test_missing_credentials_rejected_even_when_no_telegram_allowed(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'ALLOW_NO_TELEGRAM':'true'},clear=True),patch('src.test_telegram_stock.Path.cwd',return_value=Path(directory)):
            with self.assertRaises(ValueError):
                await send_test()

    async def test_live_mode_rejected_no_send(self):
        with patch.dict(os.environ,{'DRY_RUN':'false'}),patch('src.test_telegram_stock.load_dotenv'),patch('src.test_telegram_stock.Telegram') as telegram:
            with self.assertRaises(ValueError):
                await send_test()
            telegram.assert_not_called()
