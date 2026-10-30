import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.functional_health import inspect_shops, read_health


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


class FunctionalHealthTests(unittest.TestCase):
    def test_suspended_shop_fails_despite_recent_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'heartbeat').write_text('10000')
            (root / 'state.json').write_text(json.dumps({'ninnin': {
                'network_suspended': True, 'checked_at': stamp(9999)}}))
            result = read_health(root, ['ninnin'], now=10001)
            self.assertFalse(result['healthy'])
            self.assertEqual(result['shops']['ninnin'], 'suspended')

    def test_temporary_backoff_respected_and_stale_shop_detected(self):
        values = {'ninnin': {'checked_at': stamp(1000), 'http_503_next_at': 5000}}
        self.assertEqual(inspect_shops(values, ['ninnin'], now=3000)['ninnin'], 'waiting')
        self.assertEqual(inspect_shops(values, ['ninnin'], now=5100)['ninnin'], 'stale')

    def test_first_read_grace_is_bounded_and_future_dates_rejected(self):
        self.assertEqual(inspect_shops({}, ['ninnin'], now=100, started_at=50)['ninnin'], 'starting')
        self.assertEqual(inspect_shops({}, ['ninnin'], now=1000, started_at=50)['ninnin'], 'never_checked')
        self.assertEqual(inspect_shops({'ninnin': {'checked_at': stamp(99999)}}, ['ninnin'], now=1000)['ninnin'], 'never_checked')

    def test_failed_delivery_is_not_healthy(self):
        for status in ('failed', 'uncertain', 'exhausted', 'attempting'):
            with self.subTest(status=status):
                result = inspect_shops({'ninnin': {'checked_at': stamp(1000), 'email_status': status}}, ['ninnin'], now=1001)
                self.assertEqual(result['ninnin'], 'notification_failure')

    def test_corrupt_state_and_nonfinite_heartbeat_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for heartbeat, state in [('nan', '{}'), ('1000', '[]'), ('1000', 'invalid')]:
                (root / 'heartbeat').write_text(heartbeat)
                (root / 'state.json').write_text(state)
                self.assertFalse(read_health(root, ['ninnin'], now=1001)['healthy'])
