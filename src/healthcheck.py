import os
import sys
import time
from pathlib import Path


def healthy():
    directory = Path(os.getenv('DATA_DIR', 'data'))
    heartbeat = directory / 'heartbeat'
    try:
        return (not (directory / 'critical.json').exists()
                and time.time() - float(heartbeat.read_text()) < 270)
    except (OSError, ValueError):
        return False


if __name__ == '__main__':
    sys.exit(0 if healthy() else 1)
