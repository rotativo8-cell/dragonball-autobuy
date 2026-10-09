import logging
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from src.monitor_logging import DailyMonitorLog


class MonitorLoggingTests(unittest.TestCase):
    def test_retention_permissions_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'logs'
            directory.mkdir()
            for name in ('monitor-2026-10-05.log', 'history-2026-10-05.log',
                         'monitor-2026-10-06.log', 'history-2026-10-06.log', 'unrelated.log'):
                (directory / name).write_text('prior\n')
            clock = datetime(2026, 10, 9, 23, 59, tzinfo=timezone.utc)
            with patch('src.monitor_logging.datetime') as date:
                date.now.return_value = clock
                date.strptime.side_effect = datetime.strptime
                handler = DailyMonitorLog(directory)
                handler.emit(logging.makeLogRecord({'msg':'first'}))
                handler.close()
                self.assertFalse((directory/'monitor-2026-10-05.log').exists())
                self.assertFalse((directory/'history-2026-10-05.log').exists())
                self.assertTrue((directory/'monitor-2026-10-06.log').exists())
                self.assertTrue((directory/'history-2026-10-06.log').exists())
                self.assertTrue((directory/'unrelated.log').exists())
                handler = DailyMonitorLog(directory)
                handler.emit(logging.makeLogRecord({'msg':'after restart'}))
                date.now.return_value = datetime(2026, 10, 10, tzinfo=timezone.utc)
                handler.emit(logging.makeLogRecord({'msg':'new day'}))
                handler.close()
            self.assertEqual((directory/'monitor-2026-10-09.log').read_text(), 'first\nafter restart\n')
            self.assertEqual((directory/'monitor-2026-10-10.log').read_text(), 'new day\n')
            self.assertFalse((directory/'monitor-2026-10-06.log').exists())
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory/'monitor-2026-10-09.log').stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory/'monitor-2026-10-10.log').stat().st_mode & 0o777, 0o600)

    def test_log_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory/'unrelated'
            target.write_text('private')
            today = datetime.now(timezone.utc).date()
            (directory/f'monitor-{today}.log').symlink_to(target)
            handler = DailyMonitorLog(directory)
            with patch.object(handler, 'handleError') as error:
                handler.emit(logging.makeLogRecord({'msg':'do not overwrite'}))
            error.assert_called_once()
            self.assertEqual(target.read_text(),'private')
            handler.close()
