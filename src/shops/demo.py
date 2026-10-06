from decimal import Decimal
from .base import Shop, Product, Checkout, check_checkout


class DemoShop(Shop):
    async def inspect(self, page):
        await page.set_content('<h1>Dragon Ball — demostración local</h1><p id="stock">Disponible</p><p id="price">20.00</p><div id="cart"></div>')
        return Product(await page.locator('#stock').inner_text() == 'Disponible',
                       Decimal(await page.locator('#price').inner_text()))

    async def prepare_checkout(self, page, config):
        checkout = Checkout(1, Decimal('20'), Decimal('5'), Decimal('25'), True)
        check_checkout(checkout, config)
        await page.locator('#cart').evaluate('(el) => el.textContent = "1 unidad; envío 5.00; total 25.00; pendiente de confirmar"')
        if '1 unidad' not in await page.locator('#cart').inner_text():
            raise RuntimeError('Falló el carrito de demostración')
        return checkout


def create_shop():
    return DemoShop()
