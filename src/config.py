import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


def boolean(name, default):
    value = os.getenv(name, default).lower()
    if value not in ('true', 'false'):
        raise ValueError(f'{name} debe ser true o false')
    return value == 'true'


def money(name, default):
    try:
        value = Decimal(os.getenv(name, default))
        if not value.is_finite() or value < 0:
            raise ValueError()
        return value
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name} debe ser un importe positivo o cero') from None


@dataclass(frozen=True)
class Config:
    product_limit: Decimal
    total_limit: Decimal
    shipping_limit: Decimal
    interval: int
    shops: tuple[str, ...]
    data: Path
    screenshots: Path
    token: str
    chat: str
    allow_no_telegram: bool

    @classmethod
    def load(cls):
        if not boolean('DRY_RUN', 'true'):
            raise ValueError('Esta versión bloquea DRY_RUN=false: las compras reales están deshabilitadas')
        interval = int(os.getenv('CHECK_INTERVAL_SECONDS', '45'))
        if interval < 30:
            raise ValueError('CHECK_INTERVAL_SECONDS debe ser al menos 30 segundos')
        if os.getenv('MAX_QUANTITY', '1') != '1':
            raise ValueError('MAX_QUANTITY está bloqueado a 1')
        shops = tuple(s.strip() for s in os.getenv('SHOP_MODULES', 'demo').split(',') if s.strip())
        if not 1 <= len(shops) <= 2 or len(set(shops)) != len(shops):
            raise ValueError('Configura uno o dos módulos distintos')
        return cls(money('MAX_PRODUCT_PRICE', '50'), money('MAX_TOTAL_PRICE', '60'),
                   money('MAX_SHIPPING_PRICE', '10'), interval, shops,
                   Path(os.getenv('DATA_DIR', 'data')), Path(os.getenv('SCREENSHOT_DIR', 'screenshots')),
                   os.getenv('TELEGRAM_BOT_TOKEN', ''), os.getenv('TELEGRAM_CHAT_ID', ''),
                   boolean('ALLOW_NO_TELEGRAM', 'false'))
