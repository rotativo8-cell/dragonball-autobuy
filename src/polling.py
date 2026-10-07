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
