"""Correo manual ficticio, sin consultar tienda ni modificar estado."""
import asyncio
import os
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
from .config import Config
from .email_notifier import EmailNotifier, stock_email
from .shops.ninnin import NAME, URL


async def send_test():
    load_dotenv(Path.cwd() / '.env', override=False)
    config = Config.load()  # DRY_RUN=false sigue bloqueado.
    notifier = EmailNotifier()
    if not notifier.enabled:
        raise ValueError('Activa EMAIL_ENABLED=true en .env para enviar la prueba')
    event = dict(name=NAME, price=str(min(Decimal('19.99'), config.product_limit)),
                 currency=os.getenv('NINNIN_CURRENCY', 'EUR').upper(), url=URL)
    subject, body = stock_email(event, config.product_limit)
    await notifier.send('[PRUEBA FICTICIA] ' + subject, 'PRUEBA FICTICIA — NO ES STOCK REAL\n\n' + body)


def main():
    try:
        asyncio.run(send_test())
    except Exception:
        print('Prueba email: ERROR. Revisa EMAIL_ENABLED, configuración SMTP y acceso STARTTLS; no se muestran credenciales.')
        return 1
    print('Prueba email: OK — correo ficticio enviado; estado del producto intacto.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
