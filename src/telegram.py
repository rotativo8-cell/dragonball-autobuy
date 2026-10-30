import asyncio
import json
import logging
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from .notification_delivery import RetryableNotification


class Telegram:
    def __init__(self, config):
        self.token, self.chat = config.token, config.chat
        if bool(self.token) != bool(self.chat):
            raise ValueError('Telegram necesita token y chat ID juntos')
        if not self.token and not config.allow_no_telegram:
            raise ValueError('Configura Telegram o ALLOW_NO_TELEGRAM=true para una prueba local')

    async def send(self, message):
        if not self.token:
            logging.warning('Telegram desactivado: %s', message)
            return 'disabled'
        def deliver():
            request = Request(f'https://api.telegram.org/bot{self.token}/sendMessage',
                              data=json.dumps({'chat_id': self.chat, 'text': message}).encode(),
                              headers={'Content-Type': 'application/json'})
            # No incluir excepciones HTTP con el token en los logs.
            try:
                with urlopen(request, timeout=15) as response:
                    payload = json.load(response)
                    if not payload.get('ok'):
                        if payload.get('error_code') == 429:
                            raise RetryableNotification('Telegram rechazó temporalmente el envío', (payload.get('parameters') or {}).get('retry_after', 0))
                        raise RuntimeError()
            except RetryableNotification:
                raise
            except HTTPError as error:
                if error.code == 429:
                    retry_after = error.headers.get('Retry-After', 0) if error.headers else 0
                    try:
                        payload = json.load(error)
                        retry_after = (payload.get('parameters') or {}).get('retry_after', retry_after)
                    except (ValueError, TypeError, AttributeError):
                        pass
                    raise RetryableNotification('Telegram rechazó temporalmente el envío', retry_after) from None
                raise RuntimeError('No se pudo enviar la notificación Telegram') from None
            except Exception:
                raise RuntimeError('No se pudo enviar la notificación Telegram') from None
        await asyncio.to_thread(deliver)
