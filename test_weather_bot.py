import unittest
from datetime import datetime
from unittest.mock import patch
import weather_bot as bot


def period(hour, daytime=True, pop=None):
    return {"startTime": f"2026-10-09T{hour:02}:00:00-04:00", "isDaytime": daytime,
            "temperature": 60 if daytime else 40, "temperatureUnit": "F",
            "shortForecast": "Clear", "windDirection": "W", "windSpeed": "5 to 10 mph",
            "probabilityOfPrecipitation": {"value": pop}}


def fixture(url, data=None):
    if "/points/" in url:
        return {"properties": {"forecast": "daily", "forecastHourly": "hourly"}}
    if url == "daily":
        return {"properties": {"periods": [period(8), period(18, False)]}}
    if url == "hourly":
        return {"properties": {"periods": [period(h) for h in range(8, 22)]}}
    raise RuntimeError("Simulated alert outage")


class WeatherTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(bot.temp(0), "32°F / 0°C")
        self.assertEqual(bot.celsius({"temperature": 50, "temperatureUnit": "F"}), 10)

    def test_cold_wet_windy_clothing(self):
        result = bot.clothes(5, 80, True)
        for expected in ("Jacket", "umbrella", "water-resistant", "windbreaker"):
            self.assertIn(expected, result)

    @patch.object(bot, "request_json", side_effect=fixture)
    def test_missing_data_is_not_all_clear(self, _):
        text = bot.build_briefing(datetime(2026, 10, 9, 8, tzinfo=bot.TZ))
        self.assertIn("Alerts: unavailable", text)
        self.assertNotIn("No active NWS alerts", text)
        self.assertIn("Unavailable or incomplete precipitation data", text)
        self.assertIn("Tonight's low: 40°F / 4°C", text)

    def test_evening_preview(self):
        def evening(url, data=None):
            result = fixture(url, data)
            if url == "daily":
                result["properties"]["periods"] = [period(18, False)]
            return result
        with patch.object(bot, "request_json", side_effect=evening):
            text = bot.build_briefing(datetime(2026, 10, 9, 18, tzinfo=bot.TZ))
        self.assertIn("daytime period ended", text)

    @patch.dict(bot.os.environ, {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "fake"})
    @patch.object(bot, "request_json", return_value={"ok": True})
    def test_long_message(self, send):
        bot.send_telegram("A" * 8500)
        self.assertEqual(send.call_count, 3)
        self.assertTrue(all(len(call.args[1]["text"]) <= 4000 for call in send.call_args_list))


if __name__ == "__main__":
    unittest.main()
