import fcntl
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.resume_jumpichiban import resume, OLD_MESSAGE
from src.state import State


class ResumeJumpIchibanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.marker = self.directory / 'critical.json'

    def test_only_old_429_archived_stock_and_other_shop_preserved(self):
        self.marker.write_text(json.dumps({'message': OLD_MESSAGE}))
        state = State(self.directory)
        state.set('jumpichiban', {'stock': 'OUT_OF_STOCK', 'notification_status': 'sent'})
        state.set('ninnin', {'purchase_attempted': True})
        with patch('src.resume_jumpichiban.time.time', return_value=1000):
            resume(self.directory)
        state = State(self.directory)
        self.assertEqual(state.get('jumpichiban')['stock'], 'OUT_OF_STOCK')
        self.assertEqual(state.get('jumpichiban')['notification_status'], 'sent')
        self.assertEqual(state.get('jumpichiban')['next_check_at'], 1900)
        self.assertTrue(state.get('ninnin')['purchase_attempted'])
        self.assertFalse(self.marker.exists())
        self.assertEqual(len(list(self.directory.glob('critical-jumpichiban-429-*.json'))), 1)

    def test_longer_cooldown_preserved(self):
        self.marker.write_text(json.dumps({'message': OLD_MESSAGE}))
        State(self.directory).set('jumpichiban', {'next_check_at': 10000, 'rate_limit_count': 3})
        with patch('src.resume_jumpichiban.time.time', return_value=1000):
            resume(self.directory)
        self.assertEqual(State(self.directory).get('jumpichiban')['next_check_at'], 10000)
        self.assertEqual(State(self.directory).get('jumpichiban')['rate_limit_count'], 3)

    def test_other_critical_error_untouched(self):
        self.marker.write_text(json.dumps({'message': 'HTTP 403'}))
        before = self.marker.read_text()
        with self.assertRaises(RuntimeError):
            resume(self.directory)
        self.assertEqual(self.marker.read_text(), before)
        self.assertFalse((self.directory / 'state.json').exists())

    def test_active_monitor_refused(self):
        self.marker.write_text(json.dumps({'message': OLD_MESSAGE}))
        with (self.directory / 'monitor.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):
                resume(self.directory)
        self.assertTrue(self.marker.exists())

    def test_missing_marker_does_not_change_state(self):
        resume(self.directory)
        self.assertFalse((self.directory / 'state.json').exists())
