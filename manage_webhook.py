"""Interactive local setup. Secrets use hidden prompts and are never persisted."""
import argparse
from getpass import getpass
import json
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def call(token, method, data=None):
    body = json.dumps(data or {}).encode()
    with urlopen(Request(f'https://api.telegram.org/bot{token}/{method}', data=body,
                         headers={'Content-Type': 'application/json'}), timeout=30) as response:
        result = json.load(response)
    if not result.get('ok'): raise RuntimeError('Telegram rejected the request')
    return result['result']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['identity', 'register', 'status'])
    args = parser.parse_args()
    token = getpass('Bot token (hidden, never stored): ').strip()
    try:
        if args.action == 'identity':
            if call(token, 'getWebhookInfo').get('url'):
                raise RuntimeError('Identity discovery requires no existing webhook')
            updates = call(token, 'getUpdates', {'allowed_updates': ['message']})
            ids = {u['message']['from']['id'] for u in updates if u.get('message', {}).get('chat', {}).get('type') == 'private'}
            print('Recent private-chat user IDs:', ', '.join(map(str, sorted(ids))) or 'none; send /help to your bot first')
        elif args.action == 'register':
            base = input('Render service URL (https://...onrender.com): ').strip().rstrip('/')
            parsed = urlparse(base)
            if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.onrender.com') or parsed.path or parsed.query or parsed.username:
                raise ValueError('Use the Render HTTPS origin')
            secret = getpass('Webhook secret (same as Render, hidden): ').strip()
            import re
            if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret): raise ValueError('Invalid secret')
            with urlopen(base + '/healthz', timeout=120) as response:
                if json.load(response).get('status') != 'ok': raise RuntimeError('Service unhealthy')
            call(token, 'setWebhook', {'url': base + '/telegram/webhook', 'secret_token': secret,
                                      'allowed_updates': ['message'], 'max_connections': 1, 'drop_pending_updates': False})
            print('Webhook registered. Pending updates preserved.')
        else:
            result = call(token, 'getWebhookInfo')
            # Avoid printing remote descriptions or token-bearing URLs.
            print('Webhook configured:', bool(result.get('url')))
            print('Pending updates:', result.get('pending_update_count'))
            print('Last delivery error timestamp:', result.get('last_error_date', 'none'))
    except Exception:
        print('Setup failed. Check configuration, service health and connectivity. No secrets logged.')
        raise SystemExit(1)


if __name__ == '__main__': main()
