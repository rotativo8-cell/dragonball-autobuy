"""Pausas conservadoras ante límites HTTP, sin reintentos inmediatos."""
import math
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from .shops.base import CriticalError


class RateLimited(CriticalError):
    def __init__(self, retry_after=0):
        super().__init__('Jump Ichiban HTTP: HTTP 429; pausa sin bypass')
        self.retry_after = retry_after


def retry_after_seconds(value):
    """Aceptar segundos o fecha HTTP; una cabecera inválida usa el backoff."""
    if not value:
        return 0
    try:
        if value.strip().isdigit():
            return int(value.strip())
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, math.ceil(date.timestamp() - time.time()))
    except (ValueError, TypeError, OverflowError):
        return 0


class ServiceUnavailable(Exception):
    """503 temporal; nunca autoriza navegador ni bypass."""
    def __init__(self, retry_after=0):
        super().__init__('HTTP 503')
        self.retry_after = retry_after


class NetworkUnavailable(Exception):
    """Corte/timeout de transporte; no incluye errores de validación TLS."""
    def __init__(self, kind):
        super().__init__(f'Conectividad temporal ({kind})')
        self.kind = kind


def record_network_error(state, name, error):
    import logging
    entry = dict(state.get(name) or {})
    count = entry.get('network_error_count', 0) + 1
    delay = 300 * 2 ** min(count - 1, 2)
    entry.update(network_error_count=count, network_next_at=time.time() + delay,
                 network_suspended=count > 3, network_error_kind=error.kind)
    state.set(name, entry)
    if count > 3:
        logging.error('%s: conectividad sin recuperar; tienda suspendida; otras tiendas continúan; revisión manual necesaria', name)
    else:
        logging.warning('%s: %s; pausa de %ss; intento diferido %s/3; otras tiendas continúan', name, error, delay, count)


def recovered_network(state, name):
    import logging
    entry = dict(state.get(name) or {})
    if entry.get('network_error_count'):
        logging.info('%s: conectividad recuperada tras %s fallos', name, entry['network_error_count'])
        entry.update(network_error_count=0, network_next_at=0, network_suspended=False, network_error_kind=None)
        state.set(name, entry)


def service_paused(state, name):
    entry = state.get(name) or {}
    return (entry.get('http_503_suspended', False) or time.time() < entry.get('http_503_next_at', 0)
            or entry.get('network_suspended', False) or time.time() < entry.get('network_next_at', 0))


def record_503(state, name, error):
    import logging
    entry = dict(state.get(name) or {})
    count = entry.get('http_503_count', 0) + 1
    delay = max(300 * 2 ** min(count - 1, 2), error.retry_after)
    entry.update(http_503_count=count, http_503_next_at=time.time() + delay,
                 http_503_suspended=count > 3, last_http_status=503)
    state.set(name, entry)
    if count > 3:
        logging.error('%s HTTP 503: reintentos agotados; tienda suspendida; otras tiendas continúan; revisión manual necesaria', name)
    else:
        logging.warning('%s HTTP 503: reintento %s/3 en al menos %ss; otras tiendas continúan', name, count, delay)


def recovered_503(state, name):
    import logging
    entry = dict(state.get(name) or {})
    if entry.get('http_503_count'):
        logging.info('%s HTTP 503: recuperación confirmada tras %s fallos', name, entry['http_503_count'])
        entry.update(http_503_count=0, http_503_next_at=0, http_503_suspended=False, last_http_status=200)
        state.set(name, entry)
