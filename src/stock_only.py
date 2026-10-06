"""Avisos persistentes HTTP, sin acciones de carrito."""
import logging
from .hybrid import now
from .email_notifier import send_stock_email


async def stock_only_cycle(config, state, telegram, shop, name):
    product = await shop.inspect_http()
    previous = state.get(name) or {}
    stock = 'IN_STOCK' if product.available else 'OUT_OF_STOCK'
    entry = dict(previous)
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
