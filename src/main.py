import asyncio
import fcntl
import importlib
import json
import logging
import re
import time
from contextlib import suppress
from pathlib import Path

from playwright.async_api import async_playwright
from .config import Config
from .state import State
from .telegram import Telegram
from .shops.base import CriticalError, Shop, CartPreview, check_product, check_checkout, check_cart_preview


async def critical(config, telegram, page, message):
    config.data.mkdir(parents=True, exist_ok=True)
    marker = config.data / 'critical.json'
    temp = marker.with_suffix('.tmp')
    temp.write_text(json.dumps({'message': message, 'time': time.time()}))
    temp.replace(marker)
    logging.error('PARADA CRÍTICA: %s. Revisar data/critical.json antes de reanudar.', message)
    if page is not None and not page.is_closed():
        with suppress(Exception):
            await asyncio.wait_for(page.screenshot(path=str(config.screenshots / f'error-{time.time_ns()}.png'), full_page=True), 10)
    try:
        await telegram.send(f'Dragonball: PARADA CRÍTICA. {message}')
    except Exception:
        logging.error('Aviso Telegram pendiente; ver critical.json y los logs')


async def cycle(config, state, telegram, shop, page, name):
    product = await shop.inspect(page)
    if not isinstance(product.available, bool):
        raise CriticalError('Disponibilidad desconocida')
    previous = state.get(name) or {}
    current_stock = 'IN_STOCK' if product.available else 'OUT_OF_STOCK'
    if hasattr(shop, 'capture') and previous.get('stock') != current_stock:
        await shop.capture(page, current_stock.lower())
    logging.info('%s: %s', name, current_stock)
    if not product.available:
        # Una nueva transición sin stock -> con stock autoriza otro evento.
        state.set(name, {'status': 'out_of_stock', 'stock': current_stock})
        logging.info('%s: sin stock; precio=%s', name, product.price)
        return
    logging.info('%s: disponible; precio=%s', name, product.price)
    previous = state.get(name) or {}
    if previous.get('status') in ('claimed', 'completed'):
        logging.info('%s: evento ya registrado; no repetir carrito ni aviso', name)
        return
    check_product(product, config)
    # Reservar antes de cualquier efecto externo, también ante una caída.
    state.set(name, {'status': 'claimed', 'stock': current_stock, 'price': str(product.price), 'currency': product.currency})
    await telegram.send(f'{name}: stock disponible; precio {product.price}. Simulación de 1 unidad.')
    checkout = await shop.prepare_checkout(page, config)
    if isinstance(checkout, CartPreview):
        check_cart_preview(checkout, config)
        state.set(name, {'status': 'completed', 'stock': current_stock, 'price': str(checkout.product), 'subtotal': str(checkout.subtotal) if checkout.subtotal is not None else None, 'currency': checkout.currency})
        logging.info('%s: DRY_RUN; 1 unidad; subtotal=%s %s; detenido ANTES de checkout', name, checkout.subtotal, checkout.currency)
        return
    check_checkout(checkout, config)
    state.set(name, {'status': 'completed', 'stock': current_stock, 'price': str(checkout.product), 'total': str(checkout.total)})
    logging.info('%s: DRY_RUN completado; 1 unidad; envío=%s; total=%s; SIN confirmar compra',
                 name, checkout.shipping, checkout.total)


class BrowserPool:
    """Sin proceso Chromium hasta que una tienda necesite una página."""
    def __init__(self, config):
        self.config = config
        self.playwright = None
        self.context = None

    async def __aenter__(self):
        return self

    async def get_page(self, name):
        if self.context is None:
            self.playwright = await async_playwright().start()
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(self.config.data / 'browser'), headless=True)
        page = await self.context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        return page

    async def release(self):
        if self.context is not None:
            await self.context.close()
            self.context = None
        if self.playwright is not None:
            await self.playwright.stop()
            self.playwright = None

    async def __aexit__(self, *args):
        await self.release()


async def run(config, once=False):
    config.data.mkdir(parents=True, exist_ok=True)
    config.screenshots.mkdir(parents=True, exist_ok=True)
    with (config.data / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.error('Ya hay un monitor usando este directorio de datos')
            return
        telegram = Telegram(config)
        if (config.data / 'critical.json').exists():
            logging.error('Parada crítica guardada: resolver la causa y retirar critical.json para reanudar')
            with suppress(Exception):
                await telegram.send('Dragonball sigue detenido por una parada crítica guardada. Revisa los logs y data/critical.json.')
            return
        page = None
        try:
            state = State(config.data)
            shops = []
            for name in config.shops:
                if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
                    raise CriticalError('Nombre de módulo inválido')
                shop = importlib.import_module(f'src.shops.{name}').create_shop()
                if not isinstance(shop, Shop):
                    raise CriticalError('El módulo no cumple el contrato de tienda')
                shops.append((name, shop))
            from .hybrid import hybrid_cycle
            async with BrowserPool(config) as browsers:
                logging.info('Monitor iniciado: DRY_RUN=true; módulos=%s; intervalo=%ss', config.shops, config.interval)
                try:
                    while True:
                        started = time.monotonic()
                        for name, shop in shops:
                            if getattr(shop, 'stock_only', False):
                                from .stock_only import stock_only_cycle
                                await asyncio.wait_for(stock_only_cycle(config, state, telegram, shop, name), timeout=120)
                            elif getattr(shop, 'http_monitor', False):
                                await asyncio.wait_for(hybrid_cycle(config, state, telegram, shop, browsers.get_page, name), timeout=120)
                                await browsers.release()
                            else:
                                page = await browsers.get_page(name)
                                await asyncio.wait_for(cycle(config, state, telegram, shop, page, name), timeout=60)
                                await page.close()
                                page = None
                        (config.data / 'heartbeat').write_text(str(time.time()))
                        if once:
                            break
                        # Al menos interval segundos entre inicios de revisiones.
                        remaining = max(0, config.interval - (time.monotonic() - started))
                        while remaining > 0:
                            delay = min(remaining, 15)
                            await asyncio.sleep(delay)
                            remaining -= delay
                            (config.data / 'heartbeat').write_text(str(time.time()))
                except Exception as error:
                    message = str(error) if isinstance(error, CriticalError) else f'Operación fallida ({type(error).__name__}); revisar módulo o conectividad'
                    await critical(config, telegram, page, message)

        except Exception as error:
            message = str(error) if isinstance(error, CriticalError) else f'Inicio fallido ({type(error).__name__}); revisar configuración y dependencias'
            await critical(config, telegram, page, message)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Ejecutar un ciclo para diagnóstico')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        config = Config.load()
        asyncio.run(run(config, args.once))
    except (ValueError, RuntimeError) as error:
        logging.error('Configuración inválida: %s', error)
        import os
        directory = Path(os.getenv('DATA_DIR', 'data'))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'critical.json').write_text(json.dumps({'message': 'Configuración inválida; revisar logs', 'time': time.time()}))
        # Salida 0: no reiniciar una configuración peligrosa o incompleta.
    except KeyboardInterrupt:
        logging.info('Monitor detenido')


if __name__ == '__main__':
    main()
