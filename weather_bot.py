"""NWS weather briefing to Telegram. Run --dry-run to preview without sending."""
import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

# EDIT THESE RULES. Temperatures are Celsius, wind is mph, probability is percent.
RULES = {
    "jacket_below_c": 10,
    "light_layer_below_c": 18,
    "umbrella_at_percent": 40,
    "windbreaker_wind_mph": 20,
}
LAT = os.getenv("WEATHER_LAT", "41.154")
LON = os.getenv("WEATHER_LON", "-81.358")
LOCATION = os.getenv("WEATHER_LOCATION", "Kent, Ohio 44240")
TZ = ZoneInfo(os.getenv("WEATHER_TIMEZONE", "America/New_York"))


def request_json(url, data=None):
    headers = {"User-Agent": os.getenv("NWS_USER_AGENT", "MorningWeatherBot/1.0"),
               "Accept": "application/geo+json, application/json"}
    payload = None
    if data is not None:
        payload = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    # Retry read-only requests. Do not retry an ambiguous Telegram send.
    attempts = 3 if data is None else 1
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, data=payload, headers=headers), timeout=25) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError):
            if attempt + 1 == attempts:
                raise RuntimeError("Remote request failed; check service availability and configuration.") from None
            time.sleep(2 ** attempt)


def celsius(period):
    value = period.get("temperature")
    if isinstance(value, dict):
        unit = value.get("unitCode", "")
        number = value.get("value")
    else:
        unit, number = period.get("temperatureUnit", "F"), value
    if number is None:
        return None
    return (number - 32) * 5 / 9 if unit in ("F", "wmoUnit:degF") else number


def temp(c):
    return "unavailable" if c is None else f"{c * 9 / 5 + 32:.0f}°F / {c:.0f}°C"


def probability(p):
    return (p.get("probabilityOfPrecipitation") or {}).get("value")


def clothes(c, pop, windy=False):
    if c is None:
        return "Temperature unavailable; check the forecast before choosing layers."
    if c < RULES["jacket_below_c"]:
        advice = "Jacket over a warm layer, long pants, closed shoes."
    elif c < RULES["light_layer_below_c"]:
        advice = "Light jacket or hoodie, long pants, sneakers."
    else:
        advice = "T-shirt or light long-sleeve, comfortable pants, sneakers."
    if pop is not None and pop >= RULES["umbrella_at_percent"]:
        advice += " Carry an umbrella; choose water-resistant shoes."
    if windy:
        advice += " Use a windbreaker."
    return advice


def build_briefing(now=None):
    now = now or datetime.now(TZ)
    point = request_json(f"https://api.weather.gov/points/{LAT},{LON}")["properties"]
    daily = request_json(point["forecast"])["properties"]["periods"]
    hours = request_json(point["forecastHourly"])["properties"]["periods"]
    today = [p for p in daily if datetime.fromisoformat(p["startTime"]).astimezone(TZ).date() == now.date()]
    day = next((p for p in today if p["isDaytime"]), None)
    night = next((p for p in today if not p["isDaytime"]), None)
    if not day or not night:
        raise RuntimeError("Today's daytime or overnight forecast is missing; no briefing sent.")
    lines = [f"{LOCATION} — {now:%A, %B %d, %Y}", "Using configured location (not live GPS).",
             day["shortForecast"], f"High: {temp(celsius(day))} | Tonight's low: {temp(celsius(night))}", ""]
    wet_hours = []
    for label, start, end in [("Morning", 8, 12), ("Afternoon", 12, 18), ("Evening", 18, 22)]:
        block = []
        for p in hours:
            dt = datetime.fromisoformat(p["startTime"]).astimezone(TZ)
            if dt.date() == now.date() and start <= dt.hour < end:
                block.append(p)
                if probability(p) is not None and probability(p) >= RULES["umbrella_at_percent"]:
                    wet_hours.append(dt.strftime("%-I %p"))
        values = [celsius(p) for p in block if celsius(p) is not None]
        probs = [probability(p) for p in block if probability(p) is not None]
        if not values:
            lines.append(f"{label}: hourly forecast unavailable.")
            continue
        # Use the coolest forecast hour to choose a removable layer.
        low, high = min(values), max(values)
        pop = max(probs) if probs else None
        winds = list(dict.fromkeys(f"{p.get('windDirection', '')} {p.get('windSpeed', 'unavailable')}" for p in block))
        mph = [float(n) for p in block for n in re.findall(r"\d+(?:\.\d+)?", p.get("windSpeed", ""))]
        # NWS text windSpeed uses mph; gusts are not guaranteed in this endpoint.
        windy = bool(mph and max(mph) >= RULES["windbreaker_wind_mph"])
        rain = f"{pop:.0f}%" if pop is not None else "unavailable"
        if len(probs) < len(block):
            rain += " (some hours missing)"
        lines += [f"{label}: {temp(low)} to {temp(high)}. Precipitation: up to {rain}.",
                  clothes(low, pop, windy), f"Wind: {', '.join(winds)}.", ""]
    lines.append("Rain/snow timing: " + (", ".join(wet_hours) + " have elevated precipitation chances." if wet_hours else "No elevated chances in available daytime/evening hours."))
    tonight_pop = probability(night)
    lines.append("Tonight: " + night["shortForecast"] + "; precipitation " + (f"{tonight_pop:.0f}%" if tonight_pop is not None else "unavailable") + ".")
    lines.append("Feels-like, gusts and separate snow probability: unavailable in this forecast feed.")
    try:
        alerts = request_json("https://api.weather.gov/alerts/active?" + urlencode({"point": f"{LAT},{LON}"}))["features"]
        lines.append("Alerts: " + (" | ".join(a["properties"].get("headline") or a["properties"]["event"] for a in alerts) if alerts else "No active NWS alerts."))
    except (RuntimeError, KeyError):
        lines.append("Alerts: unavailable; check weather.gov for current warnings.")
    lines.append(f"Source: https://forecast.weather.gov/MapClick.php?lat={LAT}&lon={LON}")
    return "\n".join(lines)


def send_telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in repository secrets.")
    # Telegram's text limit is 4096 characters. Preserve the complete alert text.
    chunks = []
    while text:
        cut = len(text) if len(text) <= 4000 else text.rfind("\n", 0, 4000)
        if cut <= 0:
            cut = 4000
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    for chunk in chunks:
        result = request_json(f"https://api.telegram.org/bot{token}/sendMessage",
                              {"chat_id": chat, "text": chunk, "link_preview_options": {"is_disabled": True}})
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected the message. Check the bot's channel permissions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        briefing = build_briefing()
        if args.dry_run:
            print(briefing)
        else:
            send_telegram(briefing)
            print("Weather briefing delivered.")
    except Exception:
        # Never log request URLs: Telegram URLs contain the secret token.
        logging.error("Weather bot failed. Check weather service availability, secrets and channel permissions. No credentials logged.")
        raise SystemExit(1)
