"""Synchronous webhook: Telegram owns retries until a durable send claim exists."""
import hmac
import json
import logging
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from flask import Flask, request
from commands import reply
from storage import Ledger


class RateLimited(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, min(int(seconds), 3600))


def send(token, chat_id, text):
    payload = json.dumps({'chat_id': chat_id, 'text': text, 'link_preview_options': {'is_disabled': True}}).encode()
    try:
        with urlopen(Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload,
                             headers={'Content-Type': 'application/json'}), timeout=15) as response:
            data = json.load(response)
    except HTTPError as error:
        if error.code == 429:
            data = json.load(error)
            raise RateLimited(data.get('parameters', {}).get('retry_after', 30)) from None
        raise RuntimeError('Telegram delivery failed') from None
    if not data.get('ok'):
        if data.get('error_code') == 429:
            raise RateLimited(data.get('parameters', {}).get('retry_after', 30))
        raise RuntimeError('Telegram delivery failed')


def create_app(config=None, ledger=None, responder=reply, sender=send):
    settings = dict(os.environ) if config is None else config
    # Trim accidental paste whitespace; never include values in exceptions.
    token = settings.get('TELEGRAM_BOT_TOKEN', '').strip()
    secret = settings.get('TELEGRAM_WEBHOOK_SECRET', '').strip()
    owner_text = settings.get('TELEGRAM_ALLOWED_USER_ID', '').strip()
    if not token:
        raise ValueError('TELEGRAM_BOT_TOKEN is missing or empty')
    if not re.fullmatch(r'[0-9]+', owner_text) or int(owner_text) <= 0:
        raise ValueError('TELEGRAM_ALLOWED_USER_ID must be a positive numeric personal user ID; channel IDs and usernames are invalid')
    owner = int(owner_text)
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret):
        raise ValueError('TELEGRAM_WEBHOOK_SECRET must contain 32–256 characters using only ASCII letters, numbers, underscore or hyphen; paste the secret contents without quotes')
    database_url = settings.get('DATABASE_URL', '').strip()
    if ledger is None and not database_url:
        raise ValueError('DATABASE_URL is missing or empty')
    ledger = ledger or Ledger(database_url)
    ledger.initialize()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 65536

    @app.get('/healthz')
    def health():
        try:
            ledger.healthy()
            return {'status': 'ok'}
        except Exception:
            return {'status': 'unavailable'}, 503

    @app.post('/telegram/webhook')
    def webhook():
        if not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token', '').encode(), secret.encode()):
            return '', 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('update_id')) is not int:
            return '', 400
        message = data.get('message')
        if not isinstance(message, dict):
            return '', 200
        chat, user = message.get('chat') or {}, message.get('from') or {}
        if chat.get('type') != 'private' or user.get('id') != owner or chat.get('id') != owner:
            return '', 200
        text = message.get('text')
        if not isinstance(text, str):
            return '', 200
        update_id = data['update_id']
        try:
            if ledger.contains(update_id):
                return '', 200
            # Fail before claim on NWS outages; Telegram will retry.
            answer = responder(text)
            if not ledger.claim(update_id):
                return '', 200
        except Exception:
            logging.warning('Webhook dependency unavailable; retry requested')
            return '', 503
        try:
            sender(token, owner, answer)
        except RateLimited as error:
            try:
                ledger.release(update_id)
            except Exception:
                logging.error('Rate limit claim could not be released')
                return '', 503
            return '', 503, {'Retry-After': str(error.seconds)}
        except Exception:
            # Delivery may have succeeded. Retrying sendMessage could duplicate it.
            try:
                ledger.finish(update_id, 'uncertain')
            except Exception:
                pass
            logging.error('Telegram delivery uncertain; send a new command if no reply arrives')
            return '', 200
        try:
            ledger.finish(update_id, 'sent')
        except Exception:
            logging.error('Delivery succeeded but ledger finalization failed; claim retained')
        return '', 200

    return app
