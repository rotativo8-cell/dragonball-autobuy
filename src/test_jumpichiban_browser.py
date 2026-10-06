"""Prueba manual de Jump Ichiban: solo lectura, sin carrito ni checkout."""
import asyncio
import json
import os
import re
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from .config import boolean
from .shops.base import CriticalError
from .shops.ninnin import normalized, schema_products
from .test_ninnin_browser import empty_result, error_description, print_result

URL = 'https://jumpichiban.com/products/dragon-ball-visual-adventure-premium-set-vol-2'
NAME = 'Dragon Ball Visual Adventure Premium Set Vol.2'


def parse_product(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    headings = [h for h in soup.find_all('h1')
                if normalized(h.get_text(' ', strip=True)).startswith(normalized(NAME))]
    if len(headings) != 1:
        raise CriticalError('Jump Ichiban: identidad del producto desconocida; H1=' + repr([h.get_text(' ', strip=True)[:180] for h in soup.find_all('h1')]))
    products = []
    for script in soup.select('script[type="application/ld+json"]'):
        with suppress(ValueError, TypeError):
            products.extend(schema_products(json.loads(script.get_text())))
    matches = [p for p in products if normalized(p.get('name', '')).startswith(normalized(NAME))]
    if len(matches) != 1:
        raise CriticalError('Jump Ichiban: producto estructurado ausente o ambiguo')
    offers = matches[0].get('offers')
    offers = offers if isinstance(offers, list) else [offers]
    if len(offers) != 1 or not isinstance(offers[0], dict):
        raise CriticalError('Jump Ichiban: oferta ausente o múltiples variantes; revisar manualmente')
    offer = offers[0]
    target = urlparse(offer.get('url', ''))
    if target.scheme != 'https' or target.hostname != 'jumpichiban.com' or target.path != urlparse(URL).path:
        raise CriticalError('Jump Ichiban: URL de oferta incorrecta')
    currency = offer.get('priceCurrency', '')
    try:
        price = Decimal(str(offer.get('price', '')))
        if not price.is_finite() or price < 0 or not re.fullmatch('[A-Z]{3}', currency):
            raise InvalidOperation
    except InvalidOperation:
        raise CriticalError('Jump Ichiban: precio o moneda desconocidos') from None
    availability = offer.get('availability', '').rsplit('/', 1)[-1]
    if availability not in ('InStock', 'OutOfStock', 'PreOrder'):
        raise CriticalError('Jump Ichiban: stock desconocido')
    return headings[0].get_text(' ', strip=True), f'{price} {currency}', availability


async def check_page(page, path):
    result = empty_result(path)
    try:
        if not boolean('DRY_RUN', 'true') or os.getenv('MAX_QUANTITY', '1') != '1':
            raise ValueError('DRY_RUN=true y MAX_QUANTITY=1 obligatorios')
        response = await page.goto(URL, wait_until='domcontentloaded', timeout=30000)
        result['http'] = str(response.status) if response else 'Sin respuesta HTTP'
        result['title'] = await page.title()
        visible = result['title'] + '\n' + await page.locator('body').inner_text(timeout=10000)
        result['challenge'] = bool(re.search(r'just a moment|verify (you are|that you are) human|captcha|checking your browser', visible, re.I))
        for frame in await page.locator('iframe').all():
            if await frame.is_visible() and re.search(r'captcha|challenges.cloudflare.com', await frame.get_attribute('src') or '', re.I):
                result['challenge'] = True
        if result['challenge'] or response is None or response.status != 200:
            raise CriticalError('Jump Ichiban: acceso bloqueado/HTTP inesperado; detener sin bypass')
        if urlparse(page.url).hostname != 'jumpichiban.com' or urlparse(page.url).path != urlparse(URL).path:
            raise CriticalError('Jump Ichiban: redirección inesperada')
        # El tema anima el título con split-words después de DOMContentLoaded.
        heading = page.locator('h1').filter(has_text=re.compile(r'Dragon\s+Ball.*Visual\s+Adventure', re.I))
        await heading.first.wait_for(state='visible', timeout=10000)
        # inner_text conserva espacios de los componentes animados del tema.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(await page.content(), 'html.parser')
        product_headings = await heading.all()
        for node in soup.find_all('h1'):
            node.decompose()
        for product_heading in product_headings:
            node = soup.new_tag('h1')
            node.string = await product_heading.inner_text()
            soup.append(node)
        name, price, stock = parse_product(str(soup))
        buttons = page.locator('form[action*="/cart/add"] button[name="add"]')
        enabled = []
        for button in await buttons.all():
            if await button.is_visible() and await button.is_enabled():
                enabled.append(button)
        result['button'] = len(enabled) == 1
        if (stock in ('InStock', 'PreOrder')) != result['button']:
            raise CriticalError('Jump Ichiban: stock y botón contradictorios; revisar manualmente')
        result.update(access='OK', product=name, price=price,
                      stock={'InStock':'IN_STOCK','PreOrder':'PREORDER','OutOfStock':'OUT_OF_STOCK'}[stock])
    except Exception as error:
        result['reason'] = error_description(error)
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
    path = Path(os.getenv('SCREENSHOT_DIR', 'screenshots')) / 'jumpichiban-browser-test.png'
    result = empty_result(path)
    try:
        if not boolean('DRY_RUN', 'true') or os.getenv('MAX_QUANTITY', '1') != '1':
            raise ValueError('DRY_RUN=true y MAX_QUANTITY=1 obligatorios; navegador no iniciado')
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                result = await check_page(page, path)
            finally:
                await browser.close()
    except Exception as error:
        result['reason'] = error_description(error)
    return result


def main():
    load_dotenv()
    result = asyncio.run(diagnose())
    print_result(result)
    return 0 if result['access'] == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
