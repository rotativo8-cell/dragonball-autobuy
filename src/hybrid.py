import logging
from datetime import datetime, timezone
from .stock_alerts import ninnin_stock_message
from .shops.base import CriticalError, check_product, check_cart_preview


def now():
    return datetime.now(timezone.utc).isoformat()


async def hybrid_cycle(config, state, telegram, shop, get_page, name):
    from .shops.ninnin import URL, PlaywrightBlocked
    product = await shop.inspect_http()
    previous = state.get(name) or {}
    entry = dict(previous)
    # Migración conservadora de los eventos guardados antes de la arquitectura híbrida.
    attempted = entry.get('purchase_attempted', entry.get('status') in ('claimed', 'completed'))
    completed = entry.get('purchase_completed', entry.get('status') == 'completed')
    if not isinstance(attempted, bool) or not isinstance(completed, bool):
        raise CriticalError('Nin-Nin: estado de intentos inválido; revisar manualmente')
    entry.update(purchase_attempted=attempted or completed, purchase_completed=completed)
    stock = 'IN_STOCK' if product.available else 'OUT_OF_STOCK'
    entry.update(stock=stock, name=product.name, price=str(product.price), currency=product.currency,
                 shop=name, url=URL, checked_at=now())
    if stock != previous.get('stock'):
        entry['stock_changed_at'] = now()
        if product.available and previous.get('stock') == 'OUT_OF_STOCK':
            entry['stock_event'] = {'at':entry['stock_changed_at'], 'shop':name, 'url':URL,
                                    'price':str(product.price), 'currency':product.currency, 'name':product.name, 'previous_stock':'OUT_OF_STOCK'}
            entry['notification_pending'] = True
    state.set(name, entry)
    if not product.available:
        logging.info('Nin-Nin HTTP: OUT_OF_STOCK | %s %s', product.price, product.currency)
        return
    logging.info('Nin-Nin HTTP: IN_STOCK | %s %s', product.price, product.currency)
    if entry.get('notification_pending'):
        event = entry['stock_event']
        if event.get('previous_stock') == 'OUT_OF_STOCK':
            await telegram.send(ninnin_stock_message(event, config.product_limit))
        entry['notification_pending'] = False
        state.set(name, entry)
    if entry['purchase_attempted'] or completed:
        logging.info('Nin-Nin: intento ya registrado/completado; no repetir aunque vuelva el stock')
        return
    try:
        check_product(product, config)
        if product.price > config.total_limit:
            raise CriticalError('El producto supera MAX_TOTAL_PRICE')
        if product.currency != shop.currency:
            raise CriticalError('La moneda no coincide con NINNIN_CURRENCY')
    except CriticalError as error:
        logging.warning('Nin-Nin: PRICE_LIMIT_OR_CURRENCY; no abrir navegador: %s', error)
        entry['status'] = 'price_blocked'
        state.set(name, entry)
        return
    # Guardar ANTES de abrir navegador o tocar el carrito. Nunca reiniciar automáticamente.
    entry.update(purchase_attempted=True, attempted_at=now(), status='claimed')
    state.set(name, entry)
    page = await get_page(name)
    try:
        try:
            browser_product = await shop.inspect(page)
        finally:
            await shop.capture(page, 'stock-event')
        if not browser_product.available:
            entry['status'] = 'browser_out_of_stock'
            state.set(name, entry)
            await telegram.send('Nin-Nin: el navegador ya no confirma stock; intento bloqueado para evitar duplicados.')
            return
        cart = await shop.prepare_checkout(page, config)
        check_cart_preview(cart, config)
        entry.update(purchase_completed=True, completed_at=now(), status='completed',
                     outcome='DRY_RUN_CART_VERIFIED', subtotal=str(cart.subtotal) if cart.subtotal is not None else None)
        state.set(name, entry)
        logging.info('Nin-Nin: DRY_RUN_CART_VERIFIED, 1 unidad; detenido antes de checkout; compra real NO realizada')
    except PlaywrightBlocked:
        entry['status'] = 'PLAYWRIGHT_BLOCKED'
        entry['blocked_at'] = now()
        state.set(name, entry)
        logging.error('Nin-Nin: PLAYWRIGHT_BLOCKED; no evadir ni volver a intentar carrito')
        await telegram.send(f'Nin-Nin: PLAYWRIGHT_BLOCKED. Acceso denegado/CAPTCHA/Cloudflare; sin compra ni bypass.\n{URL}')
    finally:
        await page.close()
