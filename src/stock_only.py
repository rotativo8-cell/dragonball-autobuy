"""Avisos persistentes HTTP, sin acciones de carrito."""
import logging
import random
import time
from .polling import RateLimited
from .hybrid import now
from .email_notifier import send_stock_email


async def stock_only_cycle(config, state, telegram, shop, name):
    previous = state.get(name) or {}
    interval_range = getattr(shop, 'poll_interval_range', None)
    if interval_range is not None:
        if time.time() < previous.get('next_check_at', 0):
            return
        # Reservar una pausa antes de la petición: una caída no permite insistir.
        reserved = dict(previous)
        reserved['next_check_at'] = time.time() + random.randint(*interval_range)
        state.set(name, reserved)
    try:
        product = await shop.inspect_http()
    except RateLimited as error:
        if interval_range is None:
            raise
        entry = dict(state.get(name) or {})
        count = min(previous.get('rate_limit_count', 0) + 1, 3)
        delay = max(900 * 2 ** (count - 1), error.retry_after)
        entry.update(rate_limit_count=count, next_check_at=time.time() + delay,
                     last_http_status=429)
        state.set(name, entry)
        logging.warning('%s HTTP 429: pausa de %ss; sin bypass; otras tiendas continúan',
                        name, delay)
        return
    stock = 'IN_STOCK' if product.available else 'OUT_OF_STOCK'
    entry = dict(previous)
    if interval_range is not None:
        entry.update(next_check_at=time.time() + random.randint(*interval_range),
                     rate_limit_count=0, last_http_status=200)
    entry.update(stock=stock,name=product.name,price=str(product.price),currency=product.currency,
                 shop=name,url=shop.url,checked_at=now())
    if stock != previous.get('stock'):
        entry['stock_changed_at'] = now()
    # Primera lectura fija el estado; alertar solo por reposición confirmada.
    event = product.available and previous.get('stock') == 'OUT_OF_STOCK'
    if event:
        entry['stock_event'] = {key:entry[key] for key in ('name','price','currency','shop','url','stock_changed_at')}
        entry['stock_event']['previous_stock'] = 'OUT_OF_STOCK'
        entry['notification_status'] = 'attempting'
        entry['email_status'] = 'attempting'
    state.set(name,entry)
    logging.info('Jump Ichiban HTTP: %s | %s %s',stock,product.price,product.currency)
    if not event:
        return
    message = f'🚨 STOCK JUMP ICHIBAN\n\n{product.name}\n💰 {product.price:.2f} {product.currency}\n\n🛒 COMPRAR AHORA:\n{shop.url}'
    try:
        await telegram.send(message)
        entry['notification_status'] = 'sent'
    except Exception:
        entry['notification_status'] = 'failed'
        logging.error('Jump Ichiban: fallo de aviso Telegram; el monitor continúa')
    # Sin comparar USD con los límites EUR de Nin-Nin.
    entry['email_status'] = await send_stock_email(entry['stock_event'], product.price)
    state.set(name,entry)
