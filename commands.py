"""Deterministic private-chat commands; shared NWS and clothing rules."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import weather_bot as weather
from forecast_cache import ReadCache

CACHE = ReadCache()

TZ = ZoneInfo('America/New_York')
BLOCKS = {'morning': (8, 12), 'afternoon': (12, 18), 'evening': (18, 22), 'night': (22, 24)}
WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
HELP = """Kent Weather
Kent, Ohio 44240 · America/New_York

FORECAST
/forecast tomorrow 15:00
/forecast Monday morning
/forecast 11th October
/forecast October 11 at 3pm
/forecast 2026-10-12 09:00

WHAT TO WEAR
/clothes tomorrow evening
/clothes Monday morning

Morning 08:00–12:00 · Afternoon 12:00–18:00
Evening 18:00–22:00 · Night 22:00–00:00
Dates without a time use 12:00 noon.
Weekdays mean the next occurrence, including today when the time is still ahead.
Month/day without a year means the next occurrence.

/help — show these examples
Forecast availability depends on NWS coverage.
The first reply after inactivity may take a minute or longer."""


def resolve_day(value, now):
    if value in ('today', 'tomorrow'):
        return now.date() + timedelta(days=value == 'tomorrow')
    if value in WEEKDAYS:
        return now.date() + timedelta(days=(WEEKDAYS.index(value) - now.weekday()) % 7)
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        pass
    normalized = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', value)
    for fmt in ('%d %B %Y', '%B %d %Y', '%d %b %Y', '%b %d %Y'):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            pass
    for fmt in ('%d %B', '%B %d', '%d %b', '%b %d'):
        for year in range(now.year, now.year + 9):
            try:
                date = datetime.strptime(f'{normalized} {year}', fmt + ' %Y').date()
            except ValueError:
                continue
            if date >= now.date():
                return date
    raise ValueError('Date not recognized. Try Monday, tomorrow, 11th October, or YYYY-MM-DD.')


def parse_command(text, now):
    match = re.fullmatch(r'/(forecast|clothes)\s+(.+)', text.strip().lower())
    if not match:
        raise ValueError('Command not recognized.')
    command, expression = match.groups()
    expression = re.sub(r'\s+', ' ', expression)
    timing = re.search(r' (morning|afternoon|evening|night|\d{1,2}:\d{2}|\d{1,2}(?::\d{2})? ?[ap]m)$', expression)
    clock = timing.group(1) if timing else '12:00'
    day = expression[:timing.start()].removesuffix(' at') if timing else expression
    date = resolve_day(day, now)
    block = BLOCKS.get(clock)
    if block:
        clock = f'{block[0]:02}:00'
    elif re.search('[ap]m$', clock):
        compact = clock.replace(' ', '').upper()
        clock = datetime.strptime(compact, '%I:%M%p' if ':' in compact else '%I%p').strftime('%H:%M')
    if day in WEEKDAYS and date == now.date() and datetime.strptime(clock, '%H:%M').time() <= now.time().replace(tzinfo=None):
        date += timedelta(days=7)
    start = target(date.isoformat(), clock, now)
    end = start + timedelta(hours=block[1] - block[0]) if block else start
    return command, start, end


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
    try:
        command, start, end = parse_command(text, now)
    except ValueError as error:
        return '' + str(error) + '\n\n' + HELP
    is_block = end > start
    point_url = f'https://api.weather.gov/points/{weather.LAT},{weather.LON}'
    point = CACHE.get(point_url, 86400, lambda: weather.request_json(point_url)['properties'])
    hourly_url = point['forecastHourly']
    periods = CACHE.get(hourly_url, 300, lambda: weather.request_json(hourly_url)['properties']['periods'])
    chosen = []
    intervals = []
    for p in periods:
        a, b = (datetime.fromisoformat(p[k]).astimezone(TZ) for k in ('startTime', 'endTime'))
        if (a < end and b > start) if is_block else (a <= start < b):
            chosen.append(p)
            intervals.append((a, b))
    heading = f'{weather.LOCATION}\n{start:%a, %d %b %Y}\n{start:%H:%M}'
    heading += f'–{end:%H:%M} {start:%Z}' if is_block else f' {start:%Z}'
    if is_block:
        cursor = start
        for a, b in sorted(intervals):
            if a > cursor:
                break
            cursor = max(cursor, b)
        if cursor < end:
            chosen = []
    if not chosen:
        return heading + '\n\nHourly forecast unavailable for the requested time. Try a nearer time later.'
    values = [weather.celsius(p) for p in chosen]
    if any(v is None for v in values):
        return heading + '\n\nTemperature unavailable; clothing recommendation unavailable.'
    probs = [weather.probability(p) for p in chosen]
    pop = max(probs) if all(p is not None for p in probs) else None
    wind = [float(n) for p in chosen for n in re.findall(r'\d+(?:\.\d+)?', p.get('windSpeed') or '')]
    winds = list(dict.fromkeys(' '.join(filter(None, [p.get('windDirection'), p.get('windSpeed')])) or 'unavailable' for p in chosen))
    # Summarize a whole block instead of printing every hourly wind variation.
    wind_text = ', '.join(winds) if len(winds) <= 2 else f'{min(wind):g}–{max(wind):g} mph across the period' if wind else 'unavailable'
    if any(not p.get('windSpeed') for p in chosen):
        wind_text += ' · some hours unavailable'
    lines = [heading, '', ' / '.join(dict.fromkeys(p.get('shortForecast') or 'Conditions unavailable' for p in chosen)), '',
             'Temperature: ' + weather.temp(min(values)) + (' to ' + weather.temp(max(values)) if len(values) > 1 else ''),
             'Precipitation: ' + (f'up to {pop:.0f}%' if pop is not None else 'unavailable or incomplete'),
             'Wind: ' + wind_text]
    if command == 'clothes':
        lines += ['', 'WHAT TO WEAR', weather.clothes(min(values), pop, bool(wind and max(wind) >= weather.RULES['windbreaker_wind_mph']))]
    lines += ['', 'NWS · hourly data cached up to 5 minutes', 'Gusts & feels-like: not included in this forecast feed.']
    return '\n'.join(lines)
