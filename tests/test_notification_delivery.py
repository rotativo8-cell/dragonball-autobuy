import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from src.notification_delivery import RetryableNotification, deliver_stock
from src.state import State


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = State(self.directory)
        self.state.set('ninnin', {'stock_event': {'previous_stock': 'OUT_OF_STOCK', 'price': '50'},
                                 'notification_pending': True, 'email_notification_pending': True})
        self.telegram = SimpleNamespace(send=AsyncMock())
        self.email = AsyncMock(return_value='sent')

    async def tick(self, now):
        with patch('src.notification_delivery.time.time', return_value=now):
            await deliver_stock(self.state, 'ninnin', self.telegram, lambda event, limit: event['price'], self.email, 50)

    async def test_rejected_delivery_retries_after_restart_without_resending_email(self):
        self.telegram.send.side_effect = [RetryableNotification('429'), None]
        await self.tick(1000)
        self.state = State(self.directory)
        await self.tick(1299)
        self.assertEqual(self.telegram.send.await_count, 1)
        await self.tick(1300)
        await self.tick(1400)
        self.assertEqual(self.telegram.send.await_count, 2)
        self.email.assert_awaited_once()
        self.assertEqual(self.state.get('ninnin')['notification_status'], 'sent')

    async def test_interrupted_delivery_is_uncertain_not_replayed(self):
        self.state.set('ninnin', {'stock_event': {'previous_stock': 'OUT_OF_STOCK'},
                                 'notification_status': 'attempting'})
        await self.tick(1000)
        self.telegram.send.assert_not_awaited()
        self.assertEqual(self.state.get('ninnin')['notification_status'], 'uncertain')

    async def test_disabled_channel_and_generic_failure_are_not_sent_or_retried(self):
        self.telegram.send.return_value = 'disabled'
        self.email.return_value = 'failed'
        await self.tick(1000)
        await self.tick(1400)
        self.assertEqual(self.state.get('ninnin')['notification_status'], 'disabled')
        self.assertEqual(self.state.get('ninnin')['email_status'], 'failed')
        self.email.assert_awaited_once()

    async def test_retry_limit_and_provider_delay(self):
        self.telegram.send.side_effect = RetryableNotification('429', retry_after=600)
        await self.tick(1000)
        await self.tick(1300)
        self.assertEqual(self.telegram.send.await_count, 1)
        await self.tick(1600)
        await self.tick(2200)
        self.assertEqual(self.telegram.send.await_count, 2)
        self.assertEqual(self.state.get('ninnin')['notification_status'], 'exhausted')

    async def test_retryable_email_does_not_resend_successful_telegram(self):
        self.email.side_effect = ['retryable', 'sent']
        await self.tick(1000)
        await self.tick(1300)
        self.telegram.send.assert_awaited_once()
        self.assertEqual(self.email.await_count, 2)
