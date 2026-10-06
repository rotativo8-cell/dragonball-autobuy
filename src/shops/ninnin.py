"""Nin-Nin: un producto fijo, solo lectura y carrito; nunca checkout/pago."""
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from .base import Shop, Product, CartPreview, CriticalError, check_product, check_cart_preview

URL = 'https://www.nin-nin-game.com/en/dragon-ball/240042-dragon-ball-visual-adventure-premium-set-vol2-limited-edition-bandai-.html'
NAME = 'Dragon Ball: Visual Adventure Premium Set Vol.2 (Limited Edition) [Bandai]'
PRODUCT_ID = '240042'
CART_URL = 'https://www.nin-nin-game.com/en/cart'
ADD_NAME = re.compile(r'^(add to cart|add to basket|añadir al carrito|agregar al carrito|ajouter au panier)$', re.I)


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def amount(text):
    text = text.strip().replace('\u00a0', '').replace(' ', '')
    text = re.sub(r'[^0-9.,-]', '', text)
    if ',' in text and '.' in text:
        text = text.replace(',', '') if text.rfind('.') > text.rfind(',') else text.replace('.', '').replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    try:
        value = Decimal(text)
        if not value.is_finite() or value < 0:
            raise InvalidOperation()
        return value
    except InvalidOperation:
        raise CriticalError('Nin-Nin: precio desconocido o inválido') from None


def schema_products(value):
    if isinstance(value, list):
        for node in value:
            yield from schema_products(node)
    elif isinstance(value, dict):
        kind = value.get('@type', [])
        if kind == 'Product' or isinstance(kind, list) and 'Product' in kind:
            yield value
        if '@graph' in value:
            yield from schema_products(value['@graph'])


class PlaywrightBlocked(CriticalError):
    pass


class NinNinShop(Shop):
    http_monitor = True

    async def inspect_http(self):
        from .ninnin_http import inspect_http
        return await inspect_http()

    def __init__(self):
        if os.getenv('MAX_QUANTITY', '1') != '1':
            raise CriticalError('Nin-Nin: MAX_QUANTITY está bloqueado a 1')
        self.currency = os.getenv('NINNIN_CURRENCY', 'EUR').upper()
        if not re.fullmatch(r'[A-Z]{3}', self.currency):
            raise CriticalError('NINNIN_CURRENCY debe ser un código ISO de tres letras')
        self.screenshots = Path(os.getenv('SCREENSHOT_DIR', 'screenshots'))
        self.last_product = None
        self.button_detected = False
        self.blocked = False
        self.captcha_cloudflare = False
        self.add_attempted = False

    async def capture(self, page, reason):
        self.screenshots.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(self.screenshots / f'ninnin-{reason}-{time.time_ns()}.png'), full_page=True, timeout=10000)
        except Exception:
            logging.error('Nin-Nin: no se pudo guardar la captura (%s)', reason)

    async def guard(self, page, status=None):
        body = await page.locator('body').inner_text(timeout=10000)
        title = await page.title()
        # No confundir scripts normales de Cloudflare o un captcha oculto con un desafío.
        challenge = bool(re.search(r'just a moment|attention required|checking your browser|verify (you are|that you are) human|captcha|security verification', title + '\n' + body, re.I))
        for frame in await page.locator('iframe').all():
            source = await frame.get_attribute('src') or ''
            if await frame.is_visible() and re.search(r'captcha|challenges.cloudflare.com', source, re.I):
                challenge = True
        denied = status in (401, 403, 429, 503) or bool(re.search(r'request forbidden by administrative rules|access denied|403 forbidden|too many requests', body, re.I))
        if challenge or denied:
            self.blocked = True
            self.captcha_cloudflare = challenge
            await self.capture(page, 'protection')
            raise PlaywrightBlocked('Nin-Nin: CAPTCHA/Cloudflare o acceso denegado; no se intentará evadir la protección')
        if re.search(r'3d secure|3-d secure|authenticate your payment', title + '\n' + body, re.I):
            raise CriticalError('Nin-Nin: autenticación de pago inesperada')
        parsed = urlparse(page.url)
        if re.search(r'/(?:order|checkout|payment|authentication|login)(?:/|$)', parsed.path, re.I):
            raise CriticalError('Nin-Nin: checkout, pago o login inesperado; detener')
        if parsed.hostname != 'www.nin-nin-game.com' or parsed.scheme != 'https':
            raise CriticalError('Nin-Nin: navegación a un dominio inesperado')

    async def form(self, page):
        # Identidad del formulario por atributo de negocio, no por posición en el DOM.
        forms = page.locator('form').filter(has=page.locator(f'input[name="id_product"][value="{PRODUCT_ID}"]'))
        if await forms.count() != 1:
            raise CriticalError('Nin-Nin: formulario del producto ausente o ambiguo')
        form = forms.first
        action = await form.get_attribute('action') or ''
        from urllib.parse import urljoin
        target = urlparse(urljoin(URL, action))
        if target.scheme != 'https' or target.hostname != 'www.nin-nin-game.com' or target.path != '/en/cart':
            raise CriticalError('Nin-Nin: acción de formulario inesperada')
        return form

    async def add_button(self, form):
        candidates = form.get_by_role('button', name=ADD_NAME)
        visible = [item for item in await candidates.all() if await item.is_visible()]
        if not visible:
            # Respaldo limitado al formulario correcto y al nombre de la acción.
            candidates = form.locator('input[type="submit"][name="Submit"], button[name="Submit"]')
            for item in await candidates.all():
                text = (await item.get_attribute('value') or await item.inner_text()).strip()
                if await item.is_visible() and ADD_NAME.fullmatch(text):
                    visible.append(item)
        if len(visible) > 1:
            raise CriticalError('Nin-Nin: botón de carrito ambiguo')
        return visible[0] if visible else None

    async def parse_product(self, page):
        heading = page.get_by_role('heading', level=1)
        if await heading.count() != 1:
            raise CriticalError('Nin-Nin: nombre de producto ausente o ambiguo')
        name = (await heading.inner_text()).strip()
        if normalized(name) != normalized(NAME):
            raise CriticalError('Nin-Nin: el producto no coincide con el solicitado')
        form = await self.form(page)
        products = []
        for script in await page.locator('script[type="application/ld+json"]').all():
            raw = await script.text_content() or ''
            raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.S).strip()
            try:
                products.extend(schema_products(json.loads(raw)))
            except (ValueError, TypeError):
                continue
        matches = [p for p in products if normalized(p.get('name', '')) == normalized(name)]
        if len(matches) != 1 or not isinstance(matches[0].get('offers'), dict):
            raise CriticalError('Nin-Nin: datos estructurados del producto ausentes o ambiguos')
        offer = matches[0]['offers']
        offer_url = urlparse(offer.get('url', ''))
        if offer_url.hostname != 'www.nin-nin-game.com' or not re.search(r'/240042-', offer_url.path):
            raise CriticalError('Nin-Nin: identificador de oferta incorrecto')
        currency = offer.get('priceCurrency', '')
        price = amount(str(offer.get('price', '')))
        # El meta OG actual contiene 9439: NO se usa como precio ni se adivina su escala.
        price_nodes = form.locator('[itemprop="price"], #our_price_display')
        visible_prices = [node for node in await price_nodes.all() if await node.is_visible()]
        if len(visible_prices) != 1:
            raise CriticalError('Nin-Nin: precio visible ausente o ambiguo')
        text = await visible_prices[0].inner_text()
        if amount(text) != price:
            raise CriticalError('Nin-Nin: precio visible y estructurado no coinciden')
        symbols = {'EUR': '€', 'JPY': '¥', 'GBP': '£', 'USD': '$'}
        if not re.fullmatch(r'[A-Z]{3}', currency) or not (currency in text or symbols.get(currency, '\0') in text):
            raise CriticalError('Nin-Nin: moneda visible y estructurada no coinciden')
        button = await self.add_button(form)
        self.button_detected = button is not None
        availability = offer.get('availability', '').rsplit('/', 1)[-1]
        form_text = await form.inner_text()
        unavailable = bool(re.search(r'soon available|out of stock|sold out|no longer in stock|sin stock|agotado', form_text, re.I))
        enabled = button is not None and await button.is_enabled()
        if availability == 'OutOfStock' and unavailable and not enabled:
            stock = False
        elif availability == 'InStock' and enabled and not unavailable:
            stock = True
        else:
            raise CriticalError('Nin-Nin: stock desconocido o señales contradictorias')
        product = Product(stock, price, name, currency)
        self.last_product = product
        logging.info('Nin-Nin: %s | %s | %s %s', 'IN_STOCK' if stock else 'OUT_OF_STOCK', name, price, currency)
        return product

    async def inspect(self, page):
        try:
            response = await page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            await self.guard(page, response.status if response else None)
            return await self.parse_product(page)
        except Exception:
            await self.capture(page, 'inspection-error')
            raise

    async def empty_cart(self, page):
        # Un carrito desconocido NO es un carrito vacío.
        quantities = page.locator('#mobile_cart_qty, .desktop-cart-quantity')
        values = [(await node.text_content() or '').strip() for node in await quantities.all()]
        if not values or any(value != '0' for value in values):
            raise CriticalError('Nin-Nin: carrito previo no vacío o cantidad desconocida')

    async def read_cart(self, page):
        await self.guard(page)
        if urlparse(page.url).path != '/en/cart':
            raise CriticalError('Nin-Nin: no se está en la página de carrito')
        # Localizar por identidad del producto; no por la primera fila o primer botón.
        links = page.get_by_role('link').filter(has_text=NAME)
        visible = [link for link in await links.all() if await link.is_visible() and re.search(r'/240042-', await link.get_attribute('href') or '')]
        if len(visible) != 1:
            raise CriticalError('Nin-Nin: el producto correcto no aparece inequívocamente en el carrito')
        rows = visible[0].locator('xpath=ancestor::tr[1]')
        if not await rows.count():
            rows = visible[0].locator('xpath=ancestor::*[@role="listitem"][1]')
        if await rows.count() != 1:
            raise CriticalError('Nin-Nin: estructura de carrito desconocida; detener sin checkout')
        quantity = rows.locator('input').filter(visible=True)
        quantities = []
        for item in await quantity.all():
            field_name = await item.get_attribute('name') or ''
            field_type = await item.get_attribute('type') or ''
            if re.search(r'qty|quantity', field_name, re.I) or field_type == 'number':
                quantities.append(await item.input_value())
        if quantities != ['1']:
            raise CriticalError('Nin-Nin: la cantidad del producto en carrito no es exactamente uno')
        counts = page.locator('#mobile_cart_qty, .desktop-cart-quantity')
        values = [(await node.text_content() or '').strip() for node in await counts.all()]
        if not values or any(value != '1' for value in values):
            raise CriticalError('Nin-Nin: el carrito contiene otras unidades o no se puede verificar')
        # Subtotal opcional, localizado por etiqueta; nunca usarlo como total con envío.
        subtotal = None
        labels = page.get_by_text(re.compile(r'^(subtotal|total products|total productos)\s*:?', re.I))
        for label in await labels.all():
            if await label.is_visible():
                text = await label.locator('..').inner_text()
                if self.currency in text or {'EUR':'€','JPY':'¥','GBP':'£','USD':'$'}.get(self.currency, '\0') in text:
                    candidate = amount(text)
                    if subtotal is not None and subtotal != candidate:
                        raise CriticalError('Nin-Nin: subtotal ambiguo')
                    subtotal = candidate
        return CartPreview(1, self.last_product.price, subtotal, self.currency)

    async def prepare_checkout(self, page, config):
        # Este método implementa SOLO añadir al carrito; ni siquiera abre checkout.
        try:
            if os.getenv('DRY_RUN', 'true').lower() != 'true':
                raise CriticalError('Nin-Nin: DRY_RUN=true obligatorio')
            await self.guard(page)
            product = await self.parse_product(page)
            if not product.available:
                raise CriticalError('Nin-Nin: producto sin stock; no añadir')
            check_product(product, config)
            if product.price > config.total_limit:
                raise CriticalError('Nin-Nin: el producto ya supera MAX_TOTAL_PRICE; no añadir')
            if product.currency != self.currency:
                raise CriticalError('Nin-Nin: moneda distinta de NINNIN_CURRENCY; no hay conversión')
            await self.empty_cart(page)
            form = await self.form(page)
            quantity = form.locator('input[name="qty"]')
            if await quantity.count() != 1 or not await quantity.is_visible():
                raise CriticalError('Nin-Nin: selector de cantidad ausente o no editable')
            await quantity.fill('1')
            if await quantity.input_value() != '1':
                raise CriticalError('Nin-Nin: no se pudo fijar una unidad')
            if self.add_attempted:
                raise CriticalError('Nin-Nin: ya se intentó añadir; no repetir')
            button = await self.add_button(form)
            if button is None or not await button.is_enabled():
                raise CriticalError('Nin-Nin: botón añadir no disponible')
            # No aceptar cookies, no forzar clicks y nunca pulsar botones de pago.
            await button.click(trial=True, timeout=10000)
            self.add_attempted = True
            await button.click(timeout=10000, no_wait_after=True)
            try:
                await page.wait_for_function('Array.from(document.querySelectorAll("#mobile_cart_qty, .desktop-cart-quantity")).some(e => e.textContent.trim() === "1")', timeout=10000)
            except Exception:
                await self.guard(page)
                raise CriticalError('Nin-Nin: resultado de añadir incierto; no reintentar') from None
            await self.guard(page)
            await page.goto(CART_URL, wait_until='domcontentloaded', timeout=20000)
            cart = await self.read_cart(page)
            check_cart_preview(cart, config)
            logging.info('Nin-Nin: 1 unidad verificada, subtotal=%s %s; DRY_RUN detenido ANTES de checkout', cart.subtotal, cart.currency)
            return cart
        except Exception:
            await self.capture(page, 'cart-error')
            raise


def create_shop():
    return NinNinShop()
