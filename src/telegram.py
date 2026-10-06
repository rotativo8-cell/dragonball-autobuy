import asyncio
import json
import logging
from urllib.request import Request, urlopen


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
            return
        def deliver():
            request = Request(f'https://api.telegram.org/bot{self.token}/sendMessage',
                              data=json.dumps({'chat_id': self.chat, 'text': message}).encode(),
                              headers={'Content-Type': 'application/json'})
            # No incluir excepciones HTTP con el token en los logs.
            try:
                with urlopen(request, timeout=15) as response:
                    if not json.load(response).get('ok'):
                        raise RuntimeError()
            except Exception:
                raise RuntimeError('No se pudo enviar la notificación Telegram') from None
        await asyncio.to_thread(deliver)
