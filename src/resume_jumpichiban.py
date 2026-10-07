"""Reanudar exclusivamente la parada antigua HTTP 429 de Jump Ichiban."""
import fcntl
import json
import os
import time
from pathlib import Path
from .state import State

OLD_MESSAGE = 'Jump Ichiban HTTP: HTTP 429; detener sin bypass'


def resume(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Detén el monitor antes de reanudar') from None
        marker = directory / 'critical.json'
        if not marker.exists():
            return 'No hay parada crítica; no se modifica el estado.'
        if json.loads(marker.read_text()).get('message') != OLD_MESSAGE:
            raise RuntimeError('La parada no es el HTTP 429 antiguo de Jump Ichiban; no se modifica.')
        state = State(directory)
        entry = dict(state.get('jumpichiban') or {})
        entry['next_check_at'] = max(entry.get('next_check_at', 0), time.time() + 900)
        entry['rate_limit_count'] = max(entry.get('rate_limit_count', 0), 1)
        entry['last_http_status'] = 429
        state.set('jumpichiban', entry)
        backup = directory / f'critical-jumpichiban-429-{time.time_ns()}.json'
        marker.replace(backup)
        return 'Parada 429 archivada. Jump Ichiban esperará al menos 15 minutos; estado conservado.'


def main():
    try:
        print(resume(Path(os.getenv('DATA_DIR', 'data'))))
    except (RuntimeError, ValueError, OSError) as error:
        print(f'No se reanuda: {error}')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
