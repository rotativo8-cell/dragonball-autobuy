import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import requests
from src.polling import NetworkUnavailable, record_network_error, recovered_network, service_paused
from src.shops.base import CriticalError
from src.shops.ninnin_http import download_html
from src.state import State


class NetworkErrors(unittest.TestCase):
    def test_certificate_errors_remain_critical(self):
        with patch('src.shops.ninnin_http.requests.get', side_effect=requests.exceptions.SSLError('certificate verify failed')):
            with self.assertRaises(CriticalError):download_html()

    def test_transport_errors_are_deferred_not_tls(self):
        for error in (requests.ReadTimeout('timeout'), requests.ConnectionError('reset')):
            with self.subTest(error=type(error).__name__), patch('src.shops.ninnin_http.requests.get',side_effect=error):
                with self.assertRaises(NetworkUnavailable):download_html()

    def test_pauses_preserve_stock_and_attempt_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory))
            state.set('ninnin',{'stock':'OUT_OF_STOCK','purchase_attempted':True})
            with patch('src.polling.time.time',return_value=1000):
                for count,delay in [(1,300),(2,600),(3,1200),(4,1200)]:
                    record_network_error(state,'ninnin',NetworkUnavailable('ReadTimeout'))
                    self.assertEqual(state.get('ninnin')['network_next_at'],1000+delay)
                    self.assertEqual(state.get('ninnin')['network_suspended'],count>3)
                self.assertTrue(service_paused(state,'ninnin'))
                self.assertFalse(service_paused(state,'jumpichiban'))
            self.assertTrue(state.get('ninnin')['purchase_attempted'])
            self.assertEqual(state.get('ninnin')['stock'],'OUT_OF_STOCK')
            self.assertNotIn('last_http_status',state.get('ninnin'))

    def test_hybrid_returns_before_browser_or_notifications(self):
        from src.hybrid import hybrid_cycle
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory));shop=AsyncMock()
            shop.inspect_http.side_effect=NetworkUnavailable('ConnectionError')
            browser=AsyncMock();telegram=AsyncMock()
            asyncio.run(hybrid_cycle(None,state,telegram,shop,browser,'ninnin'))
            browser.assert_not_awaited();telegram.send.assert_not_awaited()
            self.assertEqual(state.get('ninnin')['network_error_count'],1)

    def test_recovery_clears_only_network_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory));state.set('ninnin',{'network_error_count':2,'network_suspended':True,'purchase_attempted':True})
            recovered_network(state,'ninnin')
            self.assertEqual(state.get('ninnin')['network_error_count'],0)
            self.assertTrue(state.get('ninnin')['purchase_attempted'])
