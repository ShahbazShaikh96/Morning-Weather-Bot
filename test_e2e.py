"""Real HTTP boundary through command logic; NWS/Telegram replaced locally."""
import json
import threading
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.request import Request, urlopen
from werkzeug.serving import make_server
from commands import reply, TZ, CACHE
from test_web_app import CONFIG, HEADERS, MemoryLedger, update
from web_app import create_app


class EndToEndTest(unittest.TestCase):
    def test_http_forecast_duplicate_and_restart(self):
        CACHE.clear()
        self.addCleanup(CACHE.clear)
        sent, ledger = [], MemoryLedger()
        def fixture(url):
            if '/points/' in url: return {'properties': {'forecastHourly': 'hourly'}}
            return {'properties': {'periods': [{'startTime': '2026-10-10T15:00:00-04:00',
                'endTime': '2026-10-10T16:00:00-04:00', 'temperature': 60, 'temperatureUnit': 'F',
                'shortForecast': 'Clear', 'windSpeed': '5 mph', 'probabilityOfPrecipitation': {'value': 0}}]}}
        for _ in range(2):
            app = create_app(CONFIG, ledger, lambda text: reply(text, datetime(2026, 10, 9, 8, tzinfo=TZ)),
                             lambda token, chat, text: sent.append((chat, text)))
            server = make_server('127.0.0.1', 0, app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                with urlopen(base + '/healthz') as response:
                    self.assertEqual(json.load(response)['status'], 'ok')
                with patch('commands.weather.request_json', side_effect=fixture):
                    body = json.dumps(update(text='/forecast tomorrow 15:00')).encode()
                    with urlopen(Request(base + '/telegram/webhook', data=body,
                                 headers={**HEADERS, 'Content-Type': 'application/json'})) as response:
                        self.assertEqual(response.status, 200)
            finally:
                server.shutdown(); thread.join(); server.server_close()
        self.assertEqual(len(sent), 1)
        self.assertIn('60°F / 16°C', sent[0][1])
