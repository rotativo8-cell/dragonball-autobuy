"""SMTP independiente, con STARTTLS obligatorio y errores sin credenciales."""
import asyncio
import logging
import os
import smtplib
import ssl
from decimal import Decimal
from email.message import EmailMessage
from .config import boolean


def stock_email(event, max_product_price):
    price = Decimal(event['price'])
    if event.get('shop') == 'jumpichiban':
        return ('🚨 STOCK JUMP ICHIBAN - Dragon Ball Visual Adventure Vol.2',
                f"🚨 STOCK JUMP ICHIBAN\n\n{event['name']}\nPrecio: {price:.2f} {event['currency']}\n\nCOMPRAR AHORA:\n{event['url']}\n")
    expensive = price > max_product_price
    subject = ('⚠️ STOCK NIN-NIN - PRECIO SUPERIOR AL LÍMITE' if expensive
               else '🚨 STOCK NIN-NIN - Dragon Ball Visual Adventure Vol.2')
    heading = '⚠️ STOCK DETECTADO PERO PRECIO SUPERIOR AL LÍMITE' if expensive else '🚨 STOCK NIN-NIN'
    body = f"{heading}\n\nNin-Nin Game\n{event['name']}\nPrecio: {price:.2f} {event['currency']}\n"
    if expensive:
        body += f'Límite: {max_product_price:.2f} {event["currency"]}\n'
    label = 'PRODUCTO' if expensive else 'COMPRAR AHORA'
    return subject, body + f'\n{label}:\n{event["url"]}\n'


class EmailNotifier:
    def __init__(self):
        self.enabled = boolean('EMAIL_ENABLED', 'false')
        if not self.enabled:
            return
        self.host = os.getenv('SMTP_HOST', 'smtp.gmail.com').strip()
        self.port = int(os.getenv('SMTP_PORT', '587'))
        self.user = os.getenv('SMTP_USER', '').strip()
        self.password = os.getenv('SMTP_PASSWORD', '')
        self.recipient = os.getenv('EMAIL_TO', '').strip()
        if not self.host or not 1 <= self.port <= 65535 or not self.user or not self.password or not self.recipient:
            raise ValueError('Configuración SMTP incompleta o inválida')
        if any('\r' in value or '\n' in value for value in (self.user, self.recipient, self.host)):
            raise ValueError('Configuración SMTP inválida')

    async def send(self, subject, body):
        if not self.enabled:
            return 'disabled'
        def deliver():
            message = EmailMessage()
            message['Subject'] = subject
            message['From'] = self.user
            message['To'] = self.recipient
            message.set_content(body)
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self.user, self.password)
                refused = smtp.send_message(message)
                if refused:
                    raise RuntimeError('Destinatario rechazado')
        await asyncio.wait_for(asyncio.to_thread(deliver), timeout=30)
        return 'sent'


async def send_stock_email(event, max_product_price):
    """Un fallo SMTP o de configuración nunca se propaga al monitor."""
    try:
        subject, body = stock_email(event, max_product_price)
        result = await EmailNotifier().send(subject, body)
        if result == 'sent':
            logging.info('Email SMTP: aviso de stock enviado')
        return result
    except Exception as error:
        # No imprimir respuesta SMTP, direcciones, contraseña ni configuración.
        logging.error('Email SMTP: envío fallido (%s); el monitor continúa, sin reintento del mismo evento', type(error).__name__)
        return 'failed'
