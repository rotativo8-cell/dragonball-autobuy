"""Bounded retries only after an explicitly rejected notification.

An interrupted/uncertain delivery is never replayed automatically.
"""
import logging
import time


class RetryableNotification(RuntimeError):
    """The provider explicitly rejected this attempt temporarily."""
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        try:
            self.retry_after = max(0, int(retry_after))
        except (TypeError, ValueError, OverflowError):
            self.retry_after = 0


async def deliver_stock(state, name, telegram, render, email_sender, limit):
    entry = dict(state.get(name) or {})
    event = entry.get('stock_event') or {}
    for channel, status_key, pending_key in (
        ('telegram', 'notification_status', 'notification_pending'),
        ('email', 'email_status', 'email_notification_pending'),
    ):
        now = time.time()
        prefix = 'delivery_' + channel
        pending = entry.get(pending_key, False)
        status = entry.get(status_key)
        if pending:
            entry[pending_key] = False
            entry[prefix + '_attempts'] = 0
            entry[prefix + '_created_at'] = now
        elif status == 'attempting':
            entry[status_key] = 'uncertain'
            state.set(name, entry)
            continue
        elif status != 'retryable' or now < entry.get(prefix + '_next_at', 0):
            continue
        if event.get('previous_stock') != 'OUT_OF_STOCK':
            entry[status_key] = 'skipped'
            state.set(name, entry)
            continue
        attempts = entry.get(prefix + '_attempts', 0)
        if attempts >= 3 or now - entry.get(prefix + '_created_at', 0) >= 900:
            entry[status_key] = 'exhausted'
            state.set(name, entry)
            continue
        entry[prefix + '_attempts'] = attempts + 1
        entry[status_key] = 'attempting'
        state.set(name, entry)  # Reserve before external delivery.
        retry_after = 0
        try:
            if channel == 'telegram':
                result = await telegram.send(render(event, limit))
                result = 'disabled' if result == 'disabled' else 'sent'
            else:
                result = await email_sender(event, limit)
        except RetryableNotification as error:
            result = 'retryable'
            retry_after = error.retry_after
        except Exception:
            result = 'failed'
            logging.error('%s: fallo de entrega %s; sin reintento incierto', name, channel)
        entry[status_key] = result if result in ('sent', 'disabled', 'retryable', 'failed', 'uncertain') else 'uncertain'
        if entry[status_key] == 'retryable':
            if attempts + 1 >= 3:
                entry[status_key] = 'exhausted'
            else:
                entry[prefix + '_next_at'] = now + max(300 * (attempts + 1), retry_after)
        state.set(name, entry)
