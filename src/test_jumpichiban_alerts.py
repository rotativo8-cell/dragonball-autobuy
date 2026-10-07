"""Prueba real de canales de Ichiban, con datos ficticios y sin tocar tiendas."""
import argparse
import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
from .config import Config
from .email_notifier import EmailNotifier, stock_email
from .telegram import Telegram
from .shops.jumpichiban import URL
from .stock_only import jumpichiban_stock_message


async def send_test(channel='both'):
    load_dotenv(Path.cwd() / '.env', override=False)
    config = Config.load()
    event = dict(shop='jumpichiban', name='Dragon Ball Visual Adventure Vol.2 — PRUEBA',
                 price='19.99', currency='USD', url=URL)
    failed = False
    if channel in ('both', 'telegram'):
        try:
            telegram = Telegram(replace(config, allow_no_telegram=False))
            await telegram.send('[PRUEBA FICTICIA — NO ES STOCK REAL]\n\n' +
                                jumpichiban_stock_message(event))
            print('Ichiban Telegram: OK — prueba ficticia enviada.')
        except Exception:
            print('Ichiban Telegram: ERROR — revisar configuración y conectividad.')
            failed = True
    if channel in ('both', 'email'):
        try:
            notifier = EmailNotifier()
            if not notifier.enabled:
                raise ValueError('Email desactivado')
            subject, body = stock_email(event, Decimal('19.99'))
            await notifier.send('[PRUEBA FICTICIA] ' + subject,
                                'PRUEBA FICTICIA — NO ES STOCK REAL\n\n' + body)
            print('Ichiban email: OK — prueba ficticia enviada.')
        except Exception:
            print('Ichiban email: ERROR — revisar SMTP, EMAIL_ENABLED y STARTTLS.')
            failed = True
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', choices=('both', 'telegram', 'email'), default='both')
    args = parser.parse_args()
    try:
        return asyncio.run(send_test(args.channel))
    except Exception:
        print('Ichiban prueba: ERROR — revisar configuración; no se muestran secretos.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
