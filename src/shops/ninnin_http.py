"""Lecture HTTP ordinaire : aucune session de navigateur ni contournement."""
import asyncio
import json
import os
import re
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from .base import CriticalError, Product

USER_AGENT = 'dragonball-autobuy/1.0 (stock monitor; no automated purchases)'
MAX_HTML_BYTES = 2_000_000


def parse_html(html):
    from .ninnin import NAME, URL, PRODUCT_ID, normalized, amount, schema_products
    soup = BeautifulSoup(html, 'html.parser')
    # Ignorar scripts normales (incluidos los recursos normales de Cloudflare).
    text = ' '.join(soup.stripped_strings)
    if re.search(r'just a moment|verify (you are|that you are) human|checking your browser|request forbidden by administrative rules|403 forbidden|captcha', text, re.I):
        raise CriticalError('Nin-Nin HTTP: protección o acceso denegado; no evadir')
    headings = soup.find_all('h1')
    if len(headings) != 1 or normalized(headings[0].get_text()) != normalized(NAME):
        raise CriticalError('Nin-Nin HTTP: producto desconocido o HTML inesperado')
    name = headings[0].get_text(' ', strip=True)
    forms = [form for form in soup.find_all('form') if form.find('input', attrs={'name':'id_product', 'value':PRODUCT_ID})]
    if len(forms) != 1:
        raise CriticalError('Nin-Nin HTTP: identidad de formulario desconocida')
    form = forms[0]
    action = urlparse(urljoin(URL, form.get('action', '')))
    if action.scheme != 'https' or action.hostname != 'www.nin-nin-game.com' or action.path != '/en/cart':
        raise CriticalError('Nin-Nin HTTP: acción del formulario inesperada')
    products = []
    for script in soup.find_all('script', attrs={'type':'application/ld+json'}):
        raw = re.sub(r'/\*.*?\*/', '', script.get_text(), flags=re.S).strip()
        try:
            products.extend(schema_products(json.loads(raw)))
        except (ValueError, TypeError):
            continue
    matches = [p for p in products if normalized(p.get('name', '')) == normalized(name)]
    if len(matches) != 1 or not isinstance(matches[0].get('offers'), dict):
        raise CriticalError('Nin-Nin HTTP: oferta estructurada ausente o ambigua')
    offer = matches[0]['offers']
    target = urlparse(offer.get('url', ''))
    if target.scheme != 'https' or target.hostname != 'www.nin-nin-game.com' or not re.search(r'/240042-', target.path):
        raise CriticalError('Nin-Nin HTTP: identificador de oferta incorrecto')
    currency = offer.get('priceCurrency', '')
    price = amount(str(offer.get('price', '')))
    nodes = form.select('#our_price_display, [itemprop="price"]')
    if len(nodes) != 1 or amount(nodes[0].get_text()) != price:
        raise CriticalError('Nin-Nin HTTP: precios ausentes o contradictorios')
    price_text = nodes[0].get_text()
    if not re.fullmatch(r'[A-Z]{3}', currency) or not (currency in price_text or {'EUR':'€','JPY':'¥','GBP':'£','USD':'$'}.get(currency, '\0') in price_text):
        raise CriticalError('Nin-Nin HTTP: moneda desconocida o contradictoria')
    # Los scripts de variantes incluyen textos genéricos: excluirlos de las señales visibles.
    for script in form.find_all('script'):
        script.decompose()
    unavailable = bool(re.search(r'soon available|out of stock|sold out|no longer in stock|sin stock|agotado', form.get_text(' ', strip=True), re.I))
    availability = offer.get('availability', '').rsplit('/', 1)[-1]
    if availability == 'OutOfStock' and unavailable:
        stock = False
    elif availability == 'InStock' and not unavailable:
        stock = True
    else:
        raise CriticalError('Nin-Nin HTTP: stock desconocido o contradictorio')
    return Product(stock, price, name, currency)


def download_html():
    from ..polling import ServiceUnavailable, retry_after_seconds
    from .ninnin import URL
    # Usar las CAs provistas por el entorno cuando existen. Nunca verify=False.
    verify = os.getenv('REQUESTS_CA_BUNDLE') or os.getenv('SSL_CERT_FILE') or True
    try:
        with requests.get(URL, headers={'User-Agent':USER_AGENT, 'Accept':'text/html'},
                          timeout=(10, 20), allow_redirects=False, stream=True, verify=verify) as response:
            if response.status_code == 503:
                raise ServiceUnavailable(retry_after_seconds(response.headers.get('Retry-After')))
            if response.status_code != 200:
                raise CriticalError(f'Nin-Nin HTTP: HTTP {response.status_code}; detener sin reintentos ni bypass')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise CriticalError('Nin-Nin HTTP: respuesta no HTML')
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > MAX_HTML_BYTES:
                    raise CriticalError('Nin-Nin HTTP: respuesta demasiado grande')
                chunks.append(chunk)
            return b''.join(chunks).decode(response.encoding or 'utf-8', errors='strict')
    except requests.RequestException:
        raise CriticalError('Nin-Nin HTTP: error de conexión/TLS; no se cambia la validación ni se reintenta') from None
    except UnicodeError:
        raise CriticalError('Nin-Nin HTTP: codificación de HTML inválida') from None


async def inspect_http():
    return parse_html(await asyncio.to_thread(download_html))
