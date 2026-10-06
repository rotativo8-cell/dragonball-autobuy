"""Formato compartido por el monitor y la prueba manual de Telegram."""
from decimal import Decimal


def ninnin_stock_message(event, max_product_price):
    price = Decimal(event['price'])
    heading = ('⚠️ STOCK DETECTADO PERO PRECIO SUPERIOR AL LÍMITE'
               if price > max_product_price else '🚨 STOCK NIN-NIN')
    details = f"Nin-Nin Game\n\n{event['name']}\n💰 {price:.2f} {event['currency']}"
    if price > max_product_price:
        return f"{heading}\n\n{details}\nLímite: {max_product_price:.2f} {event['currency']}\n\n🔗 PRODUCTO:\n{event['url']}"
    return f"{heading}\n\n{details}\n\n🛒 COMPRAR AHORA:\n{event['url']}"
