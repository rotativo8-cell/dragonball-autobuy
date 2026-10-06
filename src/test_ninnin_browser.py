"""Diagnóstico manual de solo lectura: nunca toca carrito, estado ni Telegram."""
import asyncio
import re
from pathlib import Path
from contextlib import suppress
import os

from playwright.async_api import async_playwright
from .config import boolean
from .shops.base import CriticalError
from .shops.ninnin import NinNinShop, URL


def empty_result(path):
    return dict(access='ERROR', http='Sin navegación', title='No disponible', product='No detectado',
                price='No detectado', stock='ERROR', button=False, challenge=False,
                reason='', screenshot=str(path), screenshot_saved=False)


def error_description(error):
    if isinstance(error, (CriticalError, ValueError)):
        return str(error)
    code = re.search(r'net::(ERR_[A-Z_]+)', str(error))
    return code.group(1) if code else f'Fallo de diagnóstico ({type(error).__name__})'


async def check_page(page, path):
    result = empty_result(path)
    try:
        if not boolean('DRY_RUN', 'true'):
            raise ValueError('DRY_RUN=true obligatorio; no se ha iniciado la navegación')
        shop = NinNinShop()
        response = await page.goto(URL, wait_until='domcontentloaded', timeout=30000)
        result['http'] = str(response.status) if response else 'Sin respuesta HTTP'
        result['title'] = await page.title()
        try:
            await shop.guard(page, response.status if response else None)
        finally:
            result['challenge'] = shop.captcha_cloudflare
        if response is None or response.status != 200:
            raise CriticalError(f'Nin-Nin: respuesta HTTP inesperada ({result["http"]})')
        product = await shop.parse_product(page)
        result.update(access='OK', product=product.name, price=f'{product.price} {product.currency}',
                      stock='IN_STOCK' if product.available else 'OUT_OF_STOCK', button=shop.button_detected)
    except Exception as error:
        result['reason'] = error_description(error)
        with suppress(Exception):
            result['title'] = await page.title()
    finally:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=True, timeout=10000)
            result['screenshot_saved'] = True
        except Exception:
            result['access'] = 'ERROR'
            result['reason'] += '; no se pudo guardar screenshot'
    return result


async def diagnose():
    path = Path(os.getenv('SCREENSHOT_DIR', 'screenshots')) / 'ninnin-browser-test.png'
    result = empty_result(path)
    browser = None
    try:
        if not boolean('DRY_RUN', 'true'):
            raise ValueError('DRY_RUN=true obligatorio; Chromium no se ha iniciado')
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()  # Sesión aislada: no usa cookies ni estado del monitor.
                result = await check_page(page, path)
            finally:
                if browser is not None:
                    await browser.close()
    except Exception as error:
        result['access'] = 'ERROR'
        result['reason'] = error_description(error)
    return result


def print_result(result):
    # Una línea por campo, incluso si la página devuelve saltos de línea en el título.
    clean = lambda text: ' '.join(str(text).split())
    for name, value in (
        ('Playwright acceso',result['access']), ('HTTP',result['http']), ('Título',result['title']),
        ('Producto',result['product']), ('Precio',result['price']), ('Stock',result['stock']),
        ('Botón carrito','SÍ' if result['button'] else 'NO'),
        ('CAPTCHA/Cloudflare','SÍ' if result['challenge'] else 'NO'),
        ('Screenshot',result['screenshot'] + ('' if result['screenshot_saved'] else ' (NO GUARDADO)')),
    ):
        print(f'{name}: {clean(value)}')
    if result['reason']:
        print(f'Motivo: {clean(result["reason"])}')


def main():
    result = asyncio.run(diagnose())
    print_result(result)
    return 0 if result['access'] == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
