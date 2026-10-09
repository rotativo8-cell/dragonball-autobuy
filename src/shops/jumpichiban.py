"""Monitor HTTP de solo lectura; Chromium/carrito deshabilitados."""
import asyncio
import os
import requests
from decimal import Decimal
from bs4 import BeautifulSoup
from .base import Shop, Product, CriticalError
from .ninnin_http import USER_AGENT

# El HTML Shopify observado ocupa ~6.3 MB; límite exclusivo de esta tienda.
MAX_HTML_BYTES = 10_000_000
from ..test_jumpichiban_browser import URL, parse_product
from ..polling import RateLimited, retry_after_seconds


def download_html():
    from ..polling import ServiceUnavailable, retry_after_seconds
    try:
        with requests.get(URL, headers={'User-Agent':USER_AGENT, 'Accept':'text/html'},
                          timeout=(10,20), allow_redirects=False, stream=True,
                          verify=os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE') or True) as response:
            if response.status_code == 429:
                raise RateLimited(retry_after_seconds(response.headers.get('Retry-After')))
            if response.status_code == 503:
                raise ServiceUnavailable(retry_after_seconds(response.headers.get('Retry-After')))
            if response.status_code != 200:
                raise CriticalError(f'Jump Ichiban HTTP: HTTP {response.status_code}; detener sin bypass')
            if 'text/html' not in response.headers.get('Content-Type','').lower():
                raise CriticalError('Jump Ichiban HTTP: respuesta no HTML')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise CriticalError('Jump Ichiban HTTP: respuesta demasiado grande')
                chunks.append(chunk)
            return b''.join(chunks).decode(response.encoding or 'utf-8')
    except (requests.RequestException, UnicodeError):
        raise CriticalError('Jump Ichiban HTTP: fallo de conexión/TLS/codificación; no reintentar ni cambiar validación') from None


def inspect_html(html):
    soup = BeautifulSoup(html,'html.parser')
    for script in soup.find_all('script'):
        script.decompose()
    import re
    if re.search(r'access restricted|just a moment|verify (you are|that you are) human|captcha|access denied', soup.get_text(' ',strip=True), re.I):
        raise CriticalError('Jump Ichiban HTTP: acceso bloqueado; detener sin bypass')
    name, price, stock = parse_product(html)
    value, currency = price.split()
    return Product(stock in ('InStock','PreOrder'), Decimal(value), name, currency)


class JumpIchibanShop(Shop):
    http_monitor = True
    stock_only = True
    poll_interval_range = (180, 300)
    url = URL

    async def inspect_http(self):
        return inspect_html(await asyncio.to_thread(download_html))

    async def inspect(self, page):
        raise CriticalError('Jump Ichiban: navegador deshabilitado')

    async def prepare_checkout(self, page, config):
        raise CriticalError('Jump Ichiban: carrito/checkout deshabilitados')


def create_shop():
    return JumpIchibanShop()
