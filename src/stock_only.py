"""Avisos persistentes HTTP, sin acciones de carrito."""
from decimal import Decimal
import logging
import random
import time
from .polling import RateLimited
from .hybrid import now
from .email_notifier import send_stock_email
from .notification_delivery import deliver_stock


def jumpichiban_stock_message(event):
    return (f"🚨 STOCK JUMP ICHIBAN\n\n{event['name']}\n"
            f"💰 {Decimal(event['price']):.2f} {event['currency']}\n\n"
            f"🛒 COMPRAR AHORA:\n{event['url']}")


async def stock_only_cycle(config, state, telegram, shop, name):
    from .polling import ServiceUnavailable, NetworkUnavailable, service_paused, record_503, recovered_503, record_network_error, recovered_network
    def render(event, limit):
        return jumpichiban_stock_message(event)
    await deliver_stock(state, name, telegram, render, send_stock_email, Decimal('0'))
    if service_paused(state, name):
        return
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
    except ServiceUnavailable as error:
        record_503(state, name, error)
        return
    except NetworkUnavailable as error:
        record_network_error(state, name, error)
        return
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
    recovered_503(state, name)
    recovered_network(state, name)
    previous = state.get(name) or {}
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
        entry['notification_pending'] = True
        entry['email_notification_pending'] = True
    state.set(name,entry)
    logging.info('Jump Ichiban HTTP: %s | %s %s',stock,product.price,product.currency)
    if not event:
        return
    await deliver_stock(state, name, telegram, render, send_stock_email, product.price)
    entry = state.get(name)
    logging.info('%s aviso evento=%s Telegram=%s email=%s', name, entry['stock_changed_at'], entry.get('notification_status'), entry.get('email_status'))
