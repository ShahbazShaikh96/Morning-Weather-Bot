import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from commands import reply, target, TZ, CACHE
from storage import Ledger
from web_app import create_app, RateLimited

CONFIG = {'TELEGRAM_BOT_TOKEN': 'test', 'TELEGRAM_WEBHOOK_SECRET': 's' * 32, 'TELEGRAM_ALLOWED_USER_ID': '123'}
HEADERS = {'X-Telegram-Bot-Api-Secret-Token': 's' * 32}


class MemoryLedger:
    def __init__(self): self.rows = {}
    def initialize(self): pass
    def healthy(self): pass
    def contains(self, uid): return uid in self.rows
    def claim(self, uid):
        if uid in self.rows: return False
        self.rows[uid] = 'sending'
        return True
    def finish(self, uid, state): self.rows[uid] = state
    def release(self, uid): del self.rows[uid]


def update(uid=1, owner=123, kind='private', text='/help'):
    return {'update_id': uid, 'message': {'from': {'id': owner}, 'chat': {'id': owner, 'type': kind}, 'text': text}}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.store, self.sender = MemoryLedger(), Mock()
        self.respond = Mock(return_value='Forecast')
        self.client = create_app(CONFIG, self.store, self.respond, self.sender).test_client()

    def test_configuration_errors_name_key_without_value(self):
        for key, invalid in [('TELEGRAM_BOT_TOKEN', ''),
                             ('TELEGRAM_ALLOWED_USER_ID', '-100123'),
                             ('TELEGRAM_ALLOWED_USER_ID', '@private_user'),
                             ('TELEGRAM_WEBHOOK_SECRET', 'private invalid secret!')]:
            config = {**CONFIG, key: invalid}
            with self.assertRaises(ValueError) as error:
                create_app(config, MemoryLedger())
            self.assertIn(key, str(error.exception))
            if invalid:
                self.assertNotIn(invalid, str(error.exception))

    def test_configuration_trims_paste_whitespace(self):
        config = {key: ' ' + value + '\n' for key, value in CONFIG.items()}
        client = create_app(config, MemoryLedger(), self.respond, self.sender).test_client()
        self.assertEqual(client.post('/telegram/webhook', json=update(), headers=HEADERS).status_code, 200)
        self.sender.assert_called_once_with('test', 123, 'Forecast')

    def post(self, data=None, headers=HEADERS):
        return self.client.post('/telegram/webhook', json=data or update(), headers=headers)

    def test_secret_owner_and_private_chat(self):
        self.assertEqual(self.post(headers={}).status_code, 403)
        self.assertEqual(self.post(update(owner=456)).status_code, 200)
        self.assertEqual(self.post(update(kind='channel')).status_code, 200)
        self.sender.assert_not_called()

    def test_duplicates_and_app_restart(self):
        self.post()
        client = create_app(CONFIG, self.store, self.respond, self.sender).test_client()
        client.post('/telegram/webhook', json=update(), headers=HEADERS)
        self.sender.assert_called_once()

    def test_dependency_retry_then_success(self):
        self.respond.side_effect = [RuntimeError('outage'), 'Forecast']
        self.assertEqual(self.post().status_code, 503)
        self.assertFalse(self.store.rows)
        self.assertEqual(self.post().status_code, 200)
        self.sender.assert_called_once()

    def test_ambiguous_send_not_repeated(self):
        self.sender.side_effect = TimeoutError()
        self.post(); self.post()
        self.sender.assert_called_once()
        self.assertEqual(self.store.rows[1], 'uncertain')

    def test_explicit_rate_limit_retries(self):
        self.sender.side_effect = [RateLimited(30), None]
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Retry-After'], '30')
        self.post()
        self.assertEqual(self.store.rows[1], 'sent')

    def test_health_failure_and_malformed_json(self):
        self.store.healthy = Mock(side_effect=RuntimeError())
        self.assertEqual(self.client.get('/healthz').status_code, 503)
        self.assertEqual(self.post({'update_id': 'bad'}).status_code, 400)

    def test_claim_collision(self):
        self.store.claim = Mock(return_value=False)
        self.post()
        self.sender.assert_not_called()


class CommandsTests(unittest.TestCase):
    def setUp(self):
        CACHE.clear()

    def tearDown(self):
        CACHE.clear()
    now = datetime(2026, 10, 9, 8, tzinfo=TZ)

    def test_dst_and_past(self):
        for day, clock in [('2026-11-01', '01:30'), ('2027-03-14', '02:30'), ('2026-10-08', '15:00')]:
            with self.assertRaises(ValueError): target(day, clock, self.now)
        self.assertEqual(target('tomorrow', '15:00', self.now).day, 10)

    def test_help_and_bad_input(self):
        for text in ['/help', '/forecast garbage', '/forecast tomorrow 25:00']:
            self.assertIn('/clothes tomorrow evening', reply(text, self.now))

    @patch('commands.weather.request_json')
    def test_hourly_and_evening_coverage(self, fetch):
        periods = [{'startTime': f'2026-10-10T{h}:00:00-04:00', 'endTime': f'2026-10-10T{h+1}:00:00-04:00',
                    'temperature': 50, 'temperatureUnit': 'F', 'shortForecast': 'Rain', 'windSpeed': '25 mph',
                    'probabilityOfPrecipitation': {'value': 80}} for h in [15, 18, 19, 20, 21]]
        def fixture(url):
            return {'properties': {'forecastHourly': 'hourly'}} if '/points/' in url else {'properties': {'periods': periods}}
        fetch.side_effect = fixture
        self.assertIn('50°F / 10°C', reply('/forecast tomorrow 15:00', self.now))
        self.assertIn('umbrella', reply('/clothes tomorrow evening', self.now))
        self.assertEqual(fetch.call_count, 2)  # point + hourly fetched once for both commands
        periods.pop()
        CACHE.clear()
        self.assertIn('unavailable', reply('/clothes tomorrow evening', self.now))
        self.assertIn('unavailable', reply('/forecast 2026-12-30 15:00', self.now))


class ConnectionTests(unittest.TestCase):
    @patch('storage.psycopg.connect')
    def test_pooler_compatible_timeout(self, connect):
        ledger = Ledger('test-url')
        connection = ledger.connect()
        connect.assert_called_once_with('test-url', connect_timeout=10)
        connection.execute.assert_called_once_with("SET LOCAL statement_timeout = '10s'")

    @patch('storage.psycopg.connect')
    def test_failed_timeout_setup_closes_connection(self, connect):
        connect.return_value.execute.side_effect = RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            Ledger('test-url').connect()
        connect.return_value.close.assert_called_once()


@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Set TEST_DATABASE_URL for real Postgres integration')
class PostgresTests(unittest.TestCase):
    def test_restart_and_concurrent_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        import secrets
        url = os.environ['TEST_DATABASE_URL']
        a, b = Ledger(url), Ledger(url)
        a.initialize()
        uid = secrets.randbelow(2**60)
        try:
            with ThreadPoolExecutor(2) as pool:
                self.assertEqual(sorted(pool.map(lambda ledger: ledger.claim(uid), [a, b])), [False, True])
            self.assertTrue(Ledger(url).contains(uid))
            with a.connect() as conn:
                self.assertEqual(conn.execute('SHOW statement_timeout').fetchone()[0], '10s')
            b.finish(uid, 'sent')
            self.assertFalse(a.claim(uid))
        finally:
            a.release(uid)


if __name__ == '__main__': unittest.main()
