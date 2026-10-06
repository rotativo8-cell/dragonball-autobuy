from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


class CriticalError(RuntimeError):
    """CAPTCHA, autenticación de pago, precio desconocido o flujo inesperado."""


@dataclass(frozen=True)
class Product:
    available: bool
    price: Decimal | None
    name: str = ""
    currency: str = ""


@dataclass(frozen=True)
class Checkout:
    quantity: int
    product: Decimal
    shipping: Decimal
    total: Decimal
    before_confirmation: bool


@dataclass(frozen=True)
class CartPreview:
    quantity: int
    product: Decimal
    subtotal: Decimal | None
    currency: str


def check_cart_preview(cart, config):
    check_money(cart.product)
    if cart.quantity != 1 or cart.product > config.product_limit:
        raise CriticalError('Cantidad o precio de carrito fuera de límite')
    if cart.subtotal is not None:
        check_money(cart.subtotal)
        if cart.subtotal < cart.product or cart.subtotal > config.total_limit:
            raise CriticalError('Subtotal de carrito inconsistente o superior al límite')
    # Envío y total aún desconocidos: nunca avanzar a checkout.


def check_money(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise CriticalError('Importe desconocido o inválido')


def check_product(product, config):
    check_money(product.price)
    if product.price > config.product_limit:
        raise CriticalError('Precio del producto superior al límite')


def check_checkout(checkout, config):
    for value in (checkout.product, checkout.shipping, checkout.total):
        check_money(value)
    if checkout.quantity != 1 or not checkout.before_confirmation:
        raise CriticalError('Cantidad o paso de checkout inesperado')
    if checkout.total < checkout.product + checkout.shipping:
        raise CriticalError('Desglose de precios inconsistente')
    if (checkout.product > config.product_limit or checkout.shipping > config.shipping_limit
            or checkout.total > config.total_limit):
        raise CriticalError('Precio de producto, envío o total superior al límite')


class Shop(ABC):
    """Contrato para módulos auditados. No implementar nunca confirmar/pagar.

    Cada acción requiere una comprobación previa del paso, CAPTCHA/3DS,
    cantidad=1 y precios. Si falta un dato, detenerse antes de avanzar.
    El carrito debe estar vacío antes de añadir; nunca reutilizarlo a ciegas.
    No usar clicks genéricos, auto-submit ni selectores de pago ambiguos.
    """
    @abstractmethod
    async def inspect(self, page) -> Product: ...

    @abstractmethod
    async def prepare_checkout(self, page, config) -> Checkout | CartPreview:
        """Añadir exactamente una unidad y parar ANTES de confirmar.

        Validar límites antes de cada transición. Si el envío/total todavía
        no se conoce, solo avanzar por pasos sin compromiso de pago.
        CAPTCHA, 3DS y cualquier paso inesperado => CriticalError.
        """
        ...
