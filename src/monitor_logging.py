"""Archivo diario UTC: día actual y tres días completos, sin límite por tamaño."""
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


class DailyMonitorLog(logging.Handler):
    def __init__(self, directory):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.day = None
        self.stream = None

    def emit(self, record):
        try:
            today = datetime.now(timezone.utc).date()
            if self.day != today:
                if self.stream:
                    self.stream.close()
                path = self.directory / f'monitor-{today.isoformat()}.log'
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    # Permitir que el propietario del montaje lea sus logs del contenedor.
                    owner = self.directory.stat()
                    if os.geteuid() == 0:
                        os.fchown(fd, owner.st_uid, owner.st_gid)
                    self.stream = os.fdopen(fd, 'a', encoding='utf-8')
                except BaseException:
                    os.close(fd)
                    raise
                self.day = today
                self.prune(today)
            self.stream.write(self.format(record) + '\n')
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def prune(self, today):
        cutoff = today - timedelta(days=3)
        for path in self.directory.iterdir():
            match = re.fullmatch(r'(?:monitor|history)-(\d{4}-\d{2}-\d{2})\.log', path.name)
            if match and not path.is_symlink():
                try:
                    day = datetime.strptime(match[1], '%Y-%m-%d').date()
                except ValueError:
                    continue
                if day < cutoff:
                    path.unlink()

    def close(self):
        if self.stream:
            self.stream.close()
        super().close()


def configure_logging():
    formatter = logging.Formatter('%(asctime)s UTC %(levelname)s %(message)s')
    formatter.converter = time.gmtime
    handlers = [logging.StreamHandler()]
    directory = os.getenv('MONITOR_LOG_DIR')
    if directory:
        handlers.append(DailyMonitorLog(directory))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
