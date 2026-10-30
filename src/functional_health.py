"""Process heartbeat and per-shop freshness are separate signals."""
from datetime import datetime
import json
import math
import time


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def inspect_shops(values, names, *, now=None, started_at=0, stale_after=900):
    now = time.time() if now is None else now
    results = {}
    for name in names:
        entry = values.get(name) or {}
        if not isinstance(entry, dict):
            results[name] = 'invalid_state'
            continue
        if entry.get('network_suspended') or entry.get('http_503_suspended'):
            results[name] = 'suspended'
            continue
        if any(entry.get(k) in ('failed', 'uncertain', 'exhausted', 'attempting')
               for k in ('notification_status', 'email_status')):
            results[name] = 'notification_failure'
            continue
        try:
            stamp = datetime.fromisoformat(entry['checked_at'])
            if stamp.tzinfo is None:
                raise ValueError('timezone required')
            last = stamp.timestamp()
            if last > now + 60:
                raise ValueError('future timestamp')
        except (KeyError, ValueError, TypeError, OverflowError):
            results[name] = 'starting' if 0 <= now - started_at < stale_after else 'never_checked'
            continue
        deadlines = [entry.get(k, 0) for k in ('next_check_at', 'network_next_at', 'http_503_next_at')]
        deadline = max([last + stale_after] + [d + 90 for d in deadlines if number(d)])
        # Respect the pause without presenting hours of unverified stock as healthy.
        if now > min(deadline, last + max(stale_after, 3600)):
            results[name] = 'stale'
        elif any(number(d) and d > now for d in deadlines):
            results[name] = 'waiting'
        else:
            results[name] = 'ok'
    return results


def read_health(directory, names, *, now=None, stale_after=900):
    now = time.time() if now is None else now
    try:
        heartbeat = float((directory / 'heartbeat').read_text())
        if not math.isfinite(heartbeat) or not -60 <= now - heartbeat < 270:
            return {'healthy': False, 'process': 'stale', 'shops': {}}
        if (directory / 'critical.json').exists():
            return {'healthy': False, 'process': 'critical', 'shops': {}}
        values = json.loads((directory / 'state.json').read_text())
        if not isinstance(values, dict):
            raise ValueError('invalid state')
        try:
            started = float((directory / 'started_at').read_text())
        except (OSError, ValueError):
            started = 0
        real_names = [n for n in names if n != 'demo']
        shops = inspect_shops(values, real_names, now=now, started_at=started, stale_after=stale_after)
        healthy = all(s in ('ok', 'waiting', 'starting') for s in shops.values())
        return {'healthy': healthy, 'process': 'ok', 'shops': shops}
    except (OSError, ValueError, TypeError, OverflowError):
        return {'healthy': False, 'process': 'invalid_or_missing_state', 'shops': {}}
