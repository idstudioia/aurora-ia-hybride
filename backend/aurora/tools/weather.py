"""Current weather from Open-Meteo (free API, no key): geocoding + current conditions.

``weather_cities()`` fetches ~14 world cities in a single multi-coordinate request for the
globe page. Results are cached 10 minutes; failures are never replaced by made-up values.

Globe hologramme (Monde 3D, 8/11/2026, porté d'Athéna : cielService.meteoPoint, evenementsService.vent/air) :
``weather.point`` (un point cliqué), ``weather.grid`` (vent, pluie, température, humidité sur 612 points) et
``weather.air`` (qualité de l'air de Copernicus sur la même grille). Voir reviews/globe-contrat.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import Field

from .base import Params, Source, TTLCache, ToolError, ToolResult, fetch_json, fr_list, fr_num, get_setting, now_iso, tool

GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
SOURCE_NAME = 'Open-Meteo'
CURRENT_FIELDS = ('temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,'
                  'wind_speed_10m,wind_direction_10m,is_day')
DAILY_FIELDS = 'temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max'
FORECAST_DAYS = 7          # « demain », « ce week-end », « cette semaine » are answered from the daily forecast
CACHE_S = 600
WEEKDAYS_FR = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
MONTHS_FR = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
             'novembre', 'décembre')

_weather_cache = TTLCache(CACHE_S, maxsize=64)
_geo_cache = TTLCache(24 * 3600, maxsize=256)
_cities_cache = TTLCache(CACHE_S, maxsize=2)

# WMO weather interpretation codes -> (French label, icon family)
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ('ciel dégagé', 'clear'),
    1: ('ciel plutôt dégagé', 'mostly-clear'),
    2: ('partiellement nuageux', 'partly-cloudy'),
    3: ('couvert', 'cloudy'),
    45: ('brouillard', 'fog'),
    48: ('brouillard givrant', 'fog'),
    51: ('bruine légère', 'drizzle'),
    53: ('bruine modérée', 'drizzle'),
    55: ('bruine dense', 'drizzle'),
    56: ('bruine verglaçante légère', 'sleet'),
    57: ('bruine verglaçante dense', 'sleet'),
    61: ('pluie faible', 'rain'),
    63: ('pluie modérée', 'rain'),
    65: ('pluie forte', 'rain'),
    66: ('pluie verglaçante faible', 'sleet'),
    67: ('pluie verglaçante forte', 'sleet'),
    71: ('neige faible', 'snow'),
    73: ('neige modérée', 'snow'),
    75: ('neige forte', 'snow'),
    77: ('grains de neige', 'snow'),
    80: ('averses de pluie faibles', 'showers'),
    81: ('averses de pluie modérées', 'showers'),
    82: ('averses de pluie violentes', 'showers'),
    85: ('averses de neige faibles', 'snow'),
    86: ('averses de neige fortes', 'snow'),
    95: ('orage', 'thunderstorm'),
    96: ('orage avec grêle faible', 'thunderstorm'),
    99: ('orage avec forte grêle', 'thunderstorm'),
}

# Fixed city coordinates for the globe (positions, not measurements).
WORLD_CITIES = [
    ('Paris', 'France', 48.8566, 2.3522),
    ('Montréal', 'Canada', 45.5019, -73.5674),
    ('Dakar', 'Sénégal', 14.7167, -17.4677),
    ('São Paulo', 'Brésil', -23.5505, -46.6333),
    ('Singapour', 'Singapour', 1.3521, 103.8198),
    ('Tokyo', 'Japon', 35.6762, 139.6503),
    ('Sydney', 'Australie', -33.8688, 151.2093),
    ('New York', 'États-Unis', 40.7128, -74.0060),
    ('Le Caire', 'Égypte', 30.0444, 31.2357),
    ('Moscou', 'Russie', 55.7558, 37.6173),
    ('Mumbai', 'Inde', 19.0760, 72.8777),
    ('Le Cap', 'Afrique du Sud', -33.9249, 18.4241),
    ('Reykjavik', 'Islande', 64.1466, -21.9426),
    ('Lima', 'Pérou', -12.0464, -77.0428),
]

_DIRECTIONS = ['nord', 'nord-est', 'est', 'sud-est', 'sud', 'sud-ouest', 'ouest', 'nord-ouest']
_DIRECTIONS_SHORT = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO']


def condition(code: int | None, is_day: bool | None = True) -> tuple[str, str]:
    """(French label, icon key). Icon keys: clear-day/night, partly-cloudy-day/night, cloudy,
    fog, drizzle, sleet, rain, showers, snow, thunderstorm, unknown."""
    if code is None or int(code) not in WMO_CODES:
        return ('conditions inconnues', 'unknown')
    label, family = WMO_CODES[int(code)]
    suffix = 'day' if is_day in (None, True, 1) else 'night'
    if family == 'clear':
        return label, f'clear-{suffix}'
    if family in ('mostly-clear', 'partly-cloudy'):
        return label, f'partly-cloudy-{suffix}'
    return label, family


def wind_direction(deg: float | None) -> tuple[str | None, str | None]:
    if deg is None:
        return None, None
    idx = int(((float(deg) % 360) + 22.5) // 45) % 8
    return _DIRECTIONS[idx], _DIRECTIONS_SHORT[idx]


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


async def geocode(city: str) -> dict:
    key = city.strip().lower()
    hit = _geo_cache.get(key)
    if hit is not None:
        return hit
    payload = await fetch_json(GEOCODE_URL, params={'name': city, 'count': 1, 'language': 'fr', 'format': 'json'},
                               source="Le géocodage Open-Meteo", timeout=8)
    results = payload.get('results') if isinstance(payload, dict) else None
    if not results:
        raise ToolError(f'Ville introuvable : « {city} ».', data={'city': city})
    r = results[0]
    place = {'name': r.get('name') or city, 'country': r.get('country') or '', 'admin1': r.get('admin1') or '',
             'latitude': r.get('latitude'), 'longitude': r.get('longitude'), 'timezone': r.get('timezone') or ''}
    if place['latitude'] is None or place['longitude'] is None:
        raise ToolError(f'Coordonnées absentes pour « {city} ».')
    _geo_cache.set(key, place)
    return place


def _parse_current(payload: dict) -> dict:
    cur = payload.get('current') or {}
    if not isinstance(cur, dict) or cur.get('temperature_2m') is None:
        raise ToolError('Open-Meteo n\'a pas renvoyé de conditions actuelles.')
    is_day = cur.get('is_day')
    is_day_bool = None if is_day is None else bool(is_day)
    code = cur.get('weather_code')
    label, icon = condition(code, is_day_bool)
    direction, direction_short = wind_direction(_num(cur.get('wind_direction_10m')))
    return {
        'observed_at': cur.get('time'),
        'utc_offset_seconds': payload.get('utc_offset_seconds'),
        'temperature_c': _num(cur.get('temperature_2m')),
        'apparent_temperature_c': _num(cur.get('apparent_temperature')),
        'humidity_pct': _num(cur.get('relative_humidity_2m')),
        'wind_kmh': _num(cur.get('wind_speed_10m')),
        'wind_direction_deg': _num(cur.get('wind_direction_10m')),
        'wind_direction': direction,
        'wind_direction_short': direction_short,
        'weather_code': code,
        'condition': label,
        'icon': icon,
        'is_day': is_day_bool,
    }


def _daily_minmax(payload: dict) -> tuple[float | None, float | None]:
    daily = payload.get('daily') or {}
    mins, maxs = daily.get('temperature_2m_min') or [], daily.get('temperature_2m_max') or []
    return (_num(mins[0]) if mins else None, _num(maxs[0]) if maxs else None)


def _daily(payload: dict) -> list[dict]:
    """Daily forecast lines [{date, min_c, max_c, weather_code, condition, precip_prob_pct}] (index = day offset)."""
    daily = payload.get('daily') or {}
    dates = daily.get('time') or []
    out = []
    for i, day in enumerate(dates):
        def at(key: str):
            values = daily.get(key) or []
            return values[i] if i < len(values) else None
        code = at('weather_code')
        out.append({'date': day, 'min_c': _num(at('temperature_2m_min')), 'max_c': _num(at('temperature_2m_max')),
                    'weather_code': code, 'condition': condition(code)[0] if code is not None else None,
                    'precip_prob_pct': _num(at('precipitation_probability_max'))})
    return out


def _source(retrieved_at: str | None = None, title: str = 'Open-Meteo — prévisions et observations') -> list[Source]:
    """retrieved_at: time of the real request (a cached answer keeps it, it is not « now »)."""
    return [Source(title=title, url='https://open-meteo.com/', kind='api', retrieved_at=retrieved_at or now_iso())]


def _day_label(offset: int, iso_day: str | None) -> str:
    from datetime import date
    when = {0: "aujourd'hui", 1: 'demain', 2: 'après-demain'}.get(offset, '')
    try:
        d = date.fromisoformat(str(iso_day))
        text = f'{WEEKDAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]}'
        return f'{when} ({text})' if when else text
    except ValueError:
        return when or f'dans {offset} jours'


def summarize_forecast(data: dict, days: list[int]) -> str:
    """Requested days from the daily forecast; a day outside it is said unavailable, never replaced."""
    where = data['city'] + (f" ({data['country']})" if data.get('country') else '')
    daily = data.get('daily') or []
    lines = []
    for offset in days:
        item = daily[offset] if 0 <= offset < len(daily) else None
        if item is None or item.get('min_c') is None or item.get('max_c') is None:
            label = {1: 'demain', 2: 'après-demain'}.get(offset, f'dans {offset} jours')
            lines.append(f'Prévision pour {label} à {where} : indisponible (Open-Meteo ne l’a pas fournie).')
            continue
        text = (f"Prévision pour {_day_label(offset, item.get('date'))} à {where} : "
                f"{item.get('condition') or 'conditions inconnues'}, min {fr_num(item['min_c'], 1)} °C, "
                f"max {fr_num(item['max_c'], 1)} °C")
        if item.get('precip_prob_pct') is not None:
            text += f", risque de pluie {fr_num(item['precip_prob_pct'], 0)} %"
        lines.append(text + '.')
    return '\n'.join(lines)


def summarize(data: dict) -> str:
    where = data['city'] + (f" ({data['country']})" if data.get('country') else '')
    parts = [f"À {where} : {data['condition']}, {fr_num(data['temperature_c'], 1)} °C"]
    if data.get('apparent_temperature_c') is not None:
        parts[0] += f" (ressenti {fr_num(data['apparent_temperature_c'], 1)} °C)"
    if data.get('humidity_pct') is not None:
        parts.append(f"humidité {fr_num(data['humidity_pct'], 0)} %")
    if data.get('wind_kmh') is not None:
        wind = f"vent {fr_num(data['wind_kmh'], 0)} km/h"
        if data.get('wind_direction'):
            wind += f" de secteur {data['wind_direction']}"
        parts.append(wind)
    text = ', '.join(parts) + '.'
    if data.get('today_min_c') is not None and data.get('today_max_c') is not None:
        text += f" Aujourd'hui : min {fr_num(data['today_min_c'], 1)} °C, max {fr_num(data['today_max_c'], 1)} °C."
    return text


async def current_weather(city: str) -> dict:
    key = city.strip().lower()
    hit = _weather_cache.get(key)
    if hit is not None:
        return {**hit, 'cached': True}
    place = await geocode(city)
    payload = await fetch_json(FORECAST_URL, params={
        'latitude': place['latitude'], 'longitude': place['longitude'], 'current': CURRENT_FIELDS,
        'daily': DAILY_FIELDS, 'timezone': 'auto', 'forecast_days': FORECAST_DAYS,
    }, source=SOURCE_NAME, timeout=10)
    if not isinstance(payload, dict):
        raise ToolError('Réponse inattendue d\'Open-Meteo.')
    data = {'city': place['name'], 'country': place['country'], 'admin1': place['admin1'],
            'latitude': place['latitude'], 'longitude': place['longitude'],
            'timezone': payload.get('timezone') or place['timezone'], 'query': city}
    data.update(_parse_current(payload))
    data['today_min_c'], data['today_max_c'] = _daily_minmax(payload)
    data['daily'] = _daily(payload)
    data['units'] = {'temperature': '°C', 'wind': 'km/h', 'humidity': '%'}
    data['retrieved_at'] = now_iso()
    _weather_cache.set(key, data)
    return {**data, 'cached': False}


async def weather_cities() -> dict:
    hit = _cities_cache.get('all')
    if hit is not None:
        return {**hit, 'cached': True}
    payload = await fetch_json(FORECAST_URL, params={
        'latitude': ','.join(f'{c[2]:.4f}' for c in WORLD_CITIES),
        'longitude': ','.join(f'{c[3]:.4f}' for c in WORLD_CITIES),
        'current': CURRENT_FIELDS, 'timezone': 'auto',
    }, source=SOURCE_NAME, timeout=12)
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or len(payload) != len(WORLD_CITIES):
        raise ToolError('Réponse multi-villes inattendue d\'Open-Meteo.')
    cities, missing = [], []
    for (name, country, lat, lon), item in zip(WORLD_CITIES, payload):
        try:
            cur = _parse_current(item if isinstance(item, dict) else {})
        except ToolError:
            missing.append(name)
            continue
        cities.append({'name': name, 'country': country, 'lat': lat, 'lon': lon,
                       'timezone': item.get('timezone'), **cur})
    if not cities:
        raise ToolError('Aucune ville n\'a de conditions actuelles dans la réponse d\'Open-Meteo.')
    data = {'cities': cities, 'count': len(cities), 'missing': missing,
            'units': {'temperature': '°C', 'wind': 'km/h', 'humidity': '%'}, 'retrieved_at': now_iso()}
    _cities_cache.set('all', data)
    return {**data, 'cached': False}


class WeatherParams(Params):
    city: str | None = Field(default=None, max_length=100)
    days: list[int] | None = Field(default=None, max_length=7)   # forecast day offsets (0 = today)


@tool('weather.current', description='Météo actuelle d\'une ville et prévision des 7 prochains jours (Open-Meteo).',
      action='web.fetch', params=WeatherParams, network=True, provenance='open-meteo', timeout_s=25)
async def weather_tool(p: WeatherParams) -> ToolResult:
    city = (p.city or '').strip() or str(get_setting('default_city', '') or '').strip() or 'Paris'
    data = await current_weather(city)
    summary = summarize(data)
    if p.days:
        days = [d for d in p.days if isinstance(d, int) and d >= 0]
        data = {**data, 'requested_days': days}
        summary = summarize_forecast(data, days) + f'\nConditions actuelles : {summary}'
    return ToolResult(ok=True, data=data, summary=summary, provenance='open-meteo',
                      sources=_source(data.get('retrieved_at')))


@tool('weather.cities', description='Météo actuelle de 14 grandes villes du monde (une seule requête Open-Meteo).',
      action='web.fetch', network=True, provenance='open-meteo', timeout_s=25)
async def cities_tool(_p: Params) -> ToolResult:
    data = await weather_cities()
    temps = [(c['temperature_c'], c['name']) for c in data['cities'] if c.get('temperature_c') is not None]
    summary = f"Météo actuelle de {data['count']} villes (Open-Meteo)"
    if temps:
        lo, hi = min(temps), max(temps)
        summary += f" : de {fr_num(lo[0], 0)} °C à {lo[1]} à {fr_num(hi[0], 0)} °C à {hi[1]}"
    summary += '.'
    if data['missing']:
        summary += f" Indisponible : {fr_list(data['missing'])}."
    return ToolResult(ok=True, data=data, summary=summary, provenance='open-meteo',
                      sources=_source(data.get('retrieved_at')))


# =========================================================================== globe hologramme (Monde 3D)
# Porté d'Athéna (cielService.meteoPoint, evenementsService.vent et air, 04/10/2026) ; reviews/globe-contrat.md.
# `data` a la forme de la réponse d'Athéna (sans « disponible ») ; une valeur absente à la source reste null.

AIR_URL = 'https://air-quality-api.open-meteo.com/v1/air-quality'
POINT_FIELDS = 'temperature_2m,weather_code,wind_speed_10m'
GRID_FIELDS = 'wind_speed_10m,wind_direction_10m,precipitation,temperature_2m,relative_humidity_2m'
AIR_FIELDS = 'european_aqi,pm2_5'
GRID = {'pas': 10, 'lat0': -80, 'lon0': -180, 'nLat': 17, 'nLon': 36}   # 17 × 36 = 612 points, k = i*36 + j
GRID_SIZE = GRID['nLat'] * GRID['nLon']
GRID_REQUESTS = 1          # les 612 points en une requête (essai réel du 8/11/2026) ; à monter si Open-Meteo refusait
FALLBACK_S = 24 * 3600     # repli : le dernier résultat réussi, rendu (et annoncé) quand la source ne répond plus
LICENCE_TITLE = 'Open-Meteo — données météo (CC BY 4.0)'
COPERNICUS = 'Generated using Copernicus Atmosphere Monitoring Service information'

_point_cache = TTLCache(60, maxsize=256)       # 60 s par point arrondi : un clic répété ne repart pas sur Internet
_grid_cache = TTLCache(30 * 60, maxsize=1)
_grid_fallback = TTLCache(FALLBACK_S, maxsize=1)
_air_cache = TTLCache(60 * 60, maxsize=1)      # Copernicus met à jour toutes les heures
_air_fallback = TTLCache(FALLBACK_S, maxsize=1)


async def cached_or_fallback(cache: TTLCache, fallback: TTLCache, key: Any,
                             load: Callable[[], Awaitable[Any]]) -> tuple[Any, str, str | None]:
    """(value, retrieved_at, why_old). The fresh cache first, then the source. If the source fails: the last value
    that succeeded (kept ``fallback.ttl_s``) with the reason, so that the answer says it is an old one; without one,
    the error. Offline mode never falls back. Shared by the globe tools (weather, events, news)."""
    hit = cache.get(key)
    if hit is not None:
        return hit[0], hit[1], None
    try:
        value = await load()
    except ToolError as exc:
        old = None if exc.data.get('offline') else fallback.get(key)
        if old is None:
            raise
        return old[0], old[1], exc.message_fr
    entry = (value, now_iso())
    cache.set(key, entry)
    fallback.set(key, entry)
    return value, entry[1], None


def when_fr(moment: datetime | str | int | float) -> str:
    """'8/11 à 14:15 UTC' from a datetime, an ISO text or milliseconds since 1970."""
    try:
        if isinstance(moment, (int, float)):
            moment = datetime.fromtimestamp(moment / 1000, timezone.utc)
        elif isinstance(moment, str):
            moment = datetime.fromisoformat(moment.replace('Z', '+00:00'))
        moment = moment.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return str(moment)
    return f'{moment.day}/{moment.month} à {moment:%H:%M} UTC'


def fallback_note(why_old: str | None, retrieved_at: str) -> str:
    """Said at the end of a summary when the answer is the previous result (the source did not answer)."""
    if not why_old:
        return ''
    return f" Attention : {why_old.rstrip('.')} ; voici le résultat précédent, lu le {when_fr(retrieved_at)}."


def js_round(value: float | None) -> int | None:
    """Math.round of JavaScript (a half goes up), as in Athéna; None stays None."""
    return None if value is None else int(math.floor(value + 0.5))


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def coords_fr(lat: float, lon: float, digits: int = 2) -> str:
    """'48,86° N, 2,35° E'."""
    return (f"{fr_num(abs(lat), digits)}° {'N' if lat >= 0 else 'S'}, "
            f"{fr_num(abs(lon), digits)}° {'E' if lon >= 0 else 'O'}")


# --------------------------------------------------------------------------- a point of the globe

class PointParams(Params):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


async def weather_point(lat: float, lon: float) -> tuple[dict, str]:
    """({temperature, code, ciel, vent}, retrieved_at) of a point rounded to 0,01° (about 1 km)."""
    lat, lon = round(lat, 2), round(lon, 2)
    hit = _point_cache.get((lat, lon))
    if hit is not None:
        return hit
    payload = await fetch_json(FORECAST_URL, params={'latitude': f'{lat:.2f}', 'longitude': f'{lon:.2f}',
                                                     'current': POINT_FIELDS}, source=SOURCE_NAME, timeout=8)
    cur = payload.get('current') if isinstance(payload, dict) else None
    temperature = _finite(cur.get('temperature_2m')) if isinstance(cur, dict) else None
    if temperature is None:
        raise ToolError("Open-Meteo n'a pas renvoyé de conditions actuelles pour ce point.")
    code = _finite(cur.get('weather_code'))
    code = int(code) if code is not None else None
    data = {'temperature': js_round(temperature), 'code': code, 'ciel': condition(code)[0],
            'vent': js_round(_finite(cur.get('wind_speed_10m')))}
    entry = (data, now_iso())
    _point_cache.set((lat, lon), entry)
    return entry


@tool('weather.point', description="Météo actuelle d'un point du globe (Open-Meteo) : température, ciel, vent.",
      action='web.fetch', params=PointParams, network=True, provenance='open-meteo', timeout_s=15)
async def point_tool(p: PointParams) -> ToolResult:
    data, retrieved = await weather_point(p.lat, p.lon)
    summary = (f"Au point {coords_fr(round(p.lat, 2), round(p.lon, 2))} : {data['ciel']}, "
               f"{fr_num(data['temperature'], 0)} °C")
    if data['vent'] is not None:
        summary += f", vent {fr_num(data['vent'], 0)} km/h"
    return ToolResult(ok=True, data=data, summary=summary + ' (Open-Meteo).', provenance='open-meteo',
                      sources=_source(retrieved, LICENCE_TITLE))


# --------------------------------------------------------------------------- the 612-point grid (wind, air)

def grid_points() -> tuple[list[int], list[int]]:
    """Latitudes and longitudes of the 612 points, in Athéna's order (k = i*nLon + j)."""
    lats, lons = [], []
    for i in range(GRID['nLat']):
        for j in range(GRID['nLon']):
            lats.append(GRID['lat0'] + i * GRID['pas'])
            lons.append(GRID['lon0'] + j * GRID['pas'])
    return lats, lons


def grid_position(k: int) -> tuple[int, int]:
    """(lat, lon) of the point k."""
    return GRID['lat0'] + (k // GRID['nLon']) * GRID['pas'], GRID['lon0'] + (k % GRID['nLon']) * GRID['pas']


async def _grid_request(url: str, query: str, source: str) -> list:
    """The 612 points' answers in GRID_REQUESTS request(s), in the grid's order. The commas stay as they are in
    the URL: encoded (%2C) they would triple every separator and the request line would pass 7 kB."""
    lats, lons = grid_points()
    step = -(-GRID_SIZE // GRID_REQUESTS)
    points: list = []
    for start in range(0, GRID_SIZE, step):
        part = slice(start, start + step)
        coords = f"latitude={','.join(map(str, lats[part]))}&longitude={','.join(map(str, lons[part]))}"
        payload = await fetch_json(f'{url}?{coords}&{query}', source=source, timeout=20, max_bytes=10_000_000)
        payload = [payload] if isinstance(payload, dict) else payload
        if not isinstance(payload, list) or len(payload) != len(lats[part]):
            raise ToolError(f'Réponse inattendue de {source} : la grille de {GRID_SIZE} points est incomplète.')
        points.extend(payload)
    return points


def _values(points: list, key: str) -> list[float | None]:
    return [_finite(((p.get('current') if isinstance(p, dict) else None) or {}).get(key)) for p in points]


def _measured_at(points: list) -> int:
    """mesureA (ms) from the first point, as in Athéna; Open-Meteo gives its time in GMT, without a zone."""
    first = points[0].get('current') if points and isinstance(points[0], dict) else None
    try:
        when = datetime.fromisoformat(str((first or {}).get('time'))).replace(tzinfo=timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)
    return int(when.timestamp() * 1000)


def parse_grid(points: list) -> dict:
    """Open-Meteo's answers → {mesureA, pas, lat0, lon0, nLat, nLon, u, v, pluie, temperature, humidite}.
    u, v in m/s (towards the east, towards the north); pluie in mm (last 15 min)."""
    u: list[float | None] = []
    v: list[float | None] = []
    for speed, direction in zip(_values(points, 'wind_speed_10m'), _values(points, 'wind_direction_10m')):
        if speed is None or direction is None:
            u.append(None)
            v.append(None)
            continue
        # The weather direction is where the wind COMES from: it blows towards direction + 180°.
        rad = math.radians(direction)
        u.append(round(-speed * math.sin(rad), 2) + 0.0)   # + 0.0: no « -0.0 »
        v.append(round(-speed * math.cos(rad), 2) + 0.0)
    return {'mesureA': _measured_at(points), **GRID, 'u': u, 'v': v, 'pluie': _values(points, 'precipitation'),
            'temperature': _values(points, 'temperature_2m'), 'humidite': _values(points, 'relative_humidity_2m')}


def summarize_grid(d: dict) -> str:
    parts = [f"Vent, pluie, température et humidité sur {len(d['u'])} points du globe (grille de {d['pas']}°, "
             f"Open-Meteo, relevé du {when_fr(d['mesureA'])})."]
    winds = [(math.hypot(a, b), k) for k, (a, b) in enumerate(zip(d['u'], d['v'])) if a is not None and b is not None]
    if winds:
        top, k = max(winds)
        parts.append(f"Vent le plus fort : {fr_num(top, 1)} m/s ({fr_num(top * 3.6, 0)} km/h) près de "
                     f"{coords_fr(*grid_position(k), digits=0)}.")
    parts.append(f"Pluie sur {sum(1 for x in d['pluie'] if x)} point(s).")
    temps = [x for x in d['temperature'] if x is not None]
    if temps:
        parts.append(f'Température de {fr_num(min(temps), 1)} °C à {fr_num(max(temps), 1)} °C.')
    wet = [x for x in d['humidite'] if x is not None]
    if wet:
        parts.append(f'Humidité de {fr_num(min(wet), 0)} à {fr_num(max(wet), 0)} %.')
    missing = len(d['u']) - len(winds)
    if missing:
        parts.append(f'{missing} point(s) sans mesure de vent.')
    return ' '.join(parts)


async def wind_grid() -> tuple[dict, str, str | None]:
    async def load() -> dict:
        return parse_grid(await _grid_request(FORECAST_URL, f'current={GRID_FIELDS}&wind_speed_unit=ms', SOURCE_NAME))
    return await cached_or_fallback(_grid_cache, _grid_fallback, 'grid', load)


@tool('weather.grid', description='Vent, pluie, température et humidité sur 612 points du globe (grille de 10°, '
                                  'une requête Open-Meteo).', action='web.fetch', network=True,
      provenance='open-meteo', timeout_s=45)
async def grid_tool(_p: Params) -> ToolResult:
    data, retrieved, why_old = await wind_grid()
    return ToolResult(ok=True, data=data, summary=summarize_grid(data) + fallback_note(why_old, retrieved),
                      provenance='open-meteo', sources=_source(retrieved, LICENCE_TITLE))


def parse_air(points: list) -> dict:
    """{mesureA, pas, lat0, lon0, nLat, nLon, indice (indice européen), pm25 (µg/m³)}."""
    return {'mesureA': _measured_at(points), **GRID, 'indice': _values(points, 'european_aqi'),
            'pm25': _values(points, 'pm2_5')}


def summarize_air(d: dict) -> str:
    text = (f"Qualité de l'air sur {len(d['indice'])} points du globe (indice européen et PM2,5, Copernicus CAMS par "
            f"Open-Meteo, relevé du {when_fr(d['mesureA'])})")
    index = [(x, k) for k, x in enumerate(d['indice']) if x is not None]
    if not index:
        return text + ' : aucune valeur reçue.'
    worst, k = max(index)
    text += (f" : indice de {fr_num(min(index)[0], 0)} à {fr_num(worst, 0)} (le plus élevé près de "
             f"{coords_fr(*grid_position(k), digits=0)})")
    pm25 = [x for x in d['pm25'] if x is not None]
    if pm25:
        text += f" ; PM2,5 jusqu'à {fr_num(max(pm25), 0)} µg/m³"
    missing = len(d['indice']) - len(index)
    return text + '.' + (f' {missing} point(s) sans mesure.' if missing else '')


def _air_sources(retrieved: str) -> list[Source]:
    return [Source(title="Open-Meteo — API qualité de l'air (CC BY 4.0)",
                   url='https://open-meteo.com/en/docs/air-quality-api', kind='api', retrieved_at=retrieved),
            Source(title=COPERNICUS, url='https://atmosphere.copernicus.eu/', kind='api', retrieved_at=retrieved)]


async def air_grid() -> tuple[dict, str, str | None]:
    async def load() -> dict:
        return parse_air(await _grid_request(AIR_URL, f'current={AIR_FIELDS}', "L'API qualité de l'air d'Open-Meteo"))
    return await cached_or_fallback(_air_cache, _air_fallback, 'air', load)


@tool('weather.air', description="Qualité de l'air (indice européen, PM2,5) sur 612 points du globe (Copernicus CAMS "
                                 "par Open-Meteo).", action='web.fetch', network=True,
      provenance='open-meteo:copernicus-cams', timeout_s=45)
async def air_tool(_p: Params) -> ToolResult:
    data, retrieved, why_old = await air_grid()
    return ToolResult(ok=True, data=data, summary=summarize_air(data) + fallback_note(why_old, retrieved),
                      provenance='open-meteo:copernicus-cams', sources=_air_sources(retrieved))
