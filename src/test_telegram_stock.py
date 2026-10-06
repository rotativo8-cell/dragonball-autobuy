"""Enviar un aviso ficticio con el .env real; sin estado, navegador ni carrito."""
import asyncio
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
from .config import Config
from .stock_alerts import ninnin_stock_message
from .telegram import Telegram
from .shops.ninnin import NAME, URL


async def send_test():
    # Native: .env en la carpeta actual. Docker: las variables ya vienen de Compose.
    # No sobreescribir credenciales inyectadas ni guardar o imprimir sus valores.
    load_dotenv(Path.cwd() / '.env', override=False)
    config = replace(Config.load(), allow_no_telegram=False)
    telegram = Telegram(config)  # Rechazar configuración incompleta, incluso con ALLOW_NO_TELEGRAM=true.
    event = dict(name=NAME, price=str(min(Decimal('19.99'), config.product_limit)),
                 currency=os.getenv('NINNIN_CURRENCY', 'EUR').upper(), url=URL)
    message = '[PRUEBA FICTICIA — NO ES STOCK REAL]\n\n' + ninnin_stock_message(event, config.product_limit)
    await telegram.send(message)


def main():
    try:
        asyncio.run(send_test())
    except (ValueError, RuntimeError) as error:
        print(f'Prueba Telegram: ERROR — {error}')
        return 1
    print('Prueba Telegram: OK — mensaje ficticio enviado; estado del monitor intacto.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
