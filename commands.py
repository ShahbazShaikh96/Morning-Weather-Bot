"""Deterministic private-chat commands; shared NWS and clothing rules."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import weather_bot as weather

TZ = ZoneInfo('America/New_York')
HELP = '''Kent weather — America/New_York
/forecast tomorrow 15:00
/forecast YYYY-MM-DD HH:MM
/clothes tomorrow evening
/help
Evening means 18:00–22:00. Hourly coverage depends on NWS availability.
After inactivity, the first reply can take a minute or longer. Please wait.
Feels-like and gusts are unavailable in this feed.'''


def target(day, clock, now):
    date = now.date() + timedelta(days=1) if day == 'tomorrow' else datetime.strptime(day, '%Y-%m-%d').date()
    naive = datetime.combine(date, datetime.strptime(clock, '%H:%M').time())
    dt = naive.replace(tzinfo=TZ)
    if dt.astimezone(timezone.utc).astimezone(TZ).replace(tzinfo=None) != naive:
        raise ValueError('That local time does not exist because of daylight saving time.')
    if dt.utcoffset() != dt.replace(fold=1).utcoffset():
        raise ValueError('That local time occurs twice because of daylight saving time; choose another hour.')
    if dt <= now:
        raise ValueError('Choose a future time in America/New_York.')
    return dt


def reply(text, now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    if text.strip() in ('/help', '/start'):
        return HELP
    match = re.fullmatch(r'/(forecast|clothes) (tomorrow|\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}|evening)', text.strip())
    if not match:
        return 'Unrecognized command.\n' + HELP
    command, day, clock = match.groups()
    if clock == 'evening' and command != 'clothes':
        return 'Use HH:MM for /forecast.\n' + HELP
    try:
        start = target(day, '18:00' if clock == 'evening' else clock, now)
    except ValueError as error:
        return str(error) + '\n' + HELP
    end = start + timedelta(hours=4) if clock == 'evening' else start
    point = weather.request_json(f'https://api.weather.gov/points/{weather.LAT},{weather.LON}')['properties']
    periods = weather.request_json(point['forecastHourly'])['properties']['periods']
    chosen = []
    for p in periods:
        a, b = (datetime.fromisoformat(p[k]).astimezone(TZ) for k in ('startTime', 'endTime'))
        if (a < end and b > start) if clock == 'evening' else (a <= start < b):
            chosen.append(p)
    heading = f'{weather.LOCATION} — {start:%Y-%m-%d %H:%M %Z}'
    if clock == 'evening':
        heading += ' to 22:00'
        # Require all four hourly periods; do not advise from a partial evening.
        covered = {datetime.fromisoformat(p['startTime']).astimezone(TZ).hour for p in chosen}
        if covered != {18, 19, 20, 21}:
            chosen = []
    if not chosen:
        return heading + '\nHourly forecast unavailable for the requested time. Try a nearer time later.'
    values = [weather.celsius(p) for p in chosen]
    if any(v is None for v in values):
        return heading + '\nTemperature unavailable; clothing recommendation unavailable.'
    probs = [weather.probability(p) for p in chosen]
    pop = max(probs) if all(p is not None for p in probs) else None
    wind = [float(n) for p in chosen for n in re.findall(r'\d+(?:\.\d+)?', p.get('windSpeed', ''))]
    lines = [heading, 'Temperature: ' + weather.temp(min(values)) + (' to ' + weather.temp(max(values)) if len(values) > 1 else ''),
             ' / '.join(dict.fromkeys(p.get('shortForecast', 'Conditions unavailable') for p in chosen)),
             'Precipitation: ' + (f'up to {pop:.0f}%' if pop is not None else 'unavailable or incomplete'),
             'Wind: ' + ', '.join(dict.fromkeys(p.get('windSpeed', 'unavailable') for p in chosen))]
    if command == 'clothes':
        lines.append(weather.clothes(min(values), pop, bool(wind and max(wind) >= weather.RULES['windbreaker_wind_mph'])))
    lines.append('Source: National Weather Service. Feels-like and gusts unavailable.')
    return '\n'.join(lines)
