"""Globe hologramme (Monde 3D) — le ciel : avions près d'un point, trafic mondial, satellites, images de la NASA.

Porté d'Athéna (server/services/cielService.ts), voir reviews/globe-contrat.md. Ce qui se mesure ne se génère pas :
chaque avion vient d'adsb.lol ou d'OpenSky Network, chaque satellite d'une orbite publiée (CelesTrak, TLE API en
secours) dont la position est CALCULÉE avec SGP4, chaque image de NASA GIBS. Sans réseau, rien n'est inventé.

- ``sky.planes``     avions en vol autour d'un point (adsb.lol ; OpenSky en secours s'il est allumé), 40 au plus,
                     cache 60 s ;
- ``sky.traffic``    échantillon régulier du trafic mondial (OpenSky, éteint par défaut : voir OPENSKY_SETTING),
                     1 500 au plus, cache 5 min, sinon l'ancien ;
- ``sky.satellites`` 8 satellites, positions recalculées à chaque appel, orbites gardées 6 h et copiées sur disque ;
- ``detail_image`` et ``daily_image`` : images JPEG de /api/globe/detail et /api/globe/nasa (fonctions, pas des outils).

Les données (``data``) ont la forme des réponses d'Athéna, sans le champ ``disponible`` ; les dates sont en
millisecondes depuis 1970 (``mesureA``, ``mesure``, ``orbiteDu``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from . import weather
from .base import (NoParams, Params, Source, TTLCache, ToolError, ToolResult, aurora_home, fetch, fetch_json,
                   fr_duration, fr_list, fr_num, get_setting, now_iso, tool)

try:   # requirements.txt : sgp4 (sans lui, seuls les satellites manquent)
    from sgp4.api import Satrec, jday
    from sgp4.propagation import gstime
except ImportError:  # pragma: no cover
    Satrec = jday = gstime = None

log = logging.getLogger('aurora.tools.sky')

# adsb.lol : rayon en milles nautiques, 250 au plus ; 240 nm ≈ 445 km, le cercle inscrit dans le carré d'OpenSky.
# Athéna devait envoyer un nom d'application (« 403 User-Agent too generic » avec l'agent de Node). Essai du
# 8/11/2026 : base.user_agent() (« AURORA/0.2 (assistant local) ») est accepté, 200 et 396 avions près de 44,4 / 4,8.
ADSB_URL = 'https://api.adsb.lol/v2/point/{lat:.3f}/{lon:.3f}/240'
OPENSKY_URL = 'https://opensky-network.org/api/states/all'
CELESTRAK_URL = 'https://celestrak.org/NORAD/elements/gp.php'
TLE_API_URL = 'https://tle.ivanstanojevic.me/api/tle/{norad}'
GIBS_WMS_URL = 'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi'

HALF_SIDE_DEG = 4                 # demi-côté du carré, en degrés de latitude : 4° ≈ 445 km
MAX_PLANES = 40                   # les plus proches ; le total mesuré reste donné
PLANES_CACHE_S = 60               # une lecture par minute au plus
MAX_WORLD = 1500
WORLD_CACHE_S = 5 * 60            # OpenSky sans compte : 400 crédits par jour, 4 par lecture du monde
WORLD_KEEP_S = 24 * 3600          # repli : la dernière vraie mesure, avec son heure
ORBITS_CACHE_S = 6 * 3600
ORBITS_RETRY_S = 15 * 60          # après un échec du réseau, nouvel essai 15 min plus tard
CELESTRAK_MIN_INTERVAL_S = 2 * 3600   # CelesTrak demande de ne pas relire une orbite plus d'une fois toutes les 2 h
MAX_ORBIT_AGE_S = 14 * 86400      # au-delà, SGP4 dérive de plusieurs dizaines de km : pas affiché
TRACE_MINUTES = 45
DETAIL_CACHE_S = 24 * 3600
DETAIL_MAX_REGIONS = 60
DETAIL_MAX_LAT_SPAN = 40
DETAIL_LAYER = 'BlueMarble_NextGeneration'
DAILY_LAYER = 'VIIRS_SNPP_CorrectedReflectance_TrueColor'
DAILY_CACHE_S = 6 * 3600
DAILY_KEEP_S = 7 * 86400
DAILY_SIZE = (2048, 1024)
# A day without data comes back as a black JPEG with status 200. Measured on 8/11/2026: Suomi NPP had no image on
# GIBS since 4/11 (12 572 bytes for 2048 × 1024, 0,006 byte per pixel); a real world image weighs far more (476 bytes
# against 11 527 for NOAA-20 the same day at 256 × 128). Below this density the day counts as missing.
DAILY_EMPTY_BYTES_PER_PIXEL = 0.02
IMAGE_MAX_BYTES = 12_000_000

WGS84_A = 6378.137                 # km
WGS84_E2 = (2 - 1 / 298.257223563) / 298.257223563

# Satellites suivis : le numéro NORAD est la vraie clé, le nom vient de la source des orbites.
# Suomi NPP est celui qui a pris l'image NASA posée sur le globe.
SATELLITES: tuple[tuple[int, str], ...] = (
    (25544, 'Station spatiale internationale'),
    (48274, 'Station spatiale chinoise Tiangong'),
    (20580, 'Télescope spatial Hubble'),
    (37849, "Suomi NPP — l'image NASA du globe"),
    (43013, 'NOAA-20 — météo'),
    (25994, 'Terra — observation de la Terre'),
    (27424, 'Aqua — cycle de l’eau'),
    (49260, 'Landsat 9 — images de la Terre'),
)

ADSB_TITLE = 'adsb.lol — positions ADS-B en direct (données sous licence ODbL)'
OPENSKY_TITLE = 'OpenSky Network — positions des avions (usage non commercial)'
# 24/11/2026 : OpenSky Network est éteint par défaut. Ses conditions (https://opensky-network.org/about/terms-of-use,
# article 3 vi, lues ce jour-là) réservent son API à la recherche et à l'enseignement sans but lucratif, et demandent
# une licence écrite pour un produit qui l'interroge en direct. AURORA ne l'interroge donc que si la personne l'a
# allumé elle-même dans Paramètres (réglage « opensky_enabled », config.SETTING_KEYS), après avoir lu ces conditions.
# Même choix que l'édition payante d'Athéna pour les avions (adsb.lol seul), avec l'interrupteur en plus.
OPENSKY_SETTING = 'sky.opensky_enabled'
WORLD_OFF = ("Trafic mondial éteint : sa seule source, OpenSky Network, est réservée à la recherche et à "
             "l'enseignement sans but lucratif. Vous pouvez l'allumer vous-même dans Paramètres, après avoir lu ses "
             "conditions. Les avions autour d'un point restent mesurés par adsb.lol.")
CELESTRAK_TITLE = 'CelesTrak — orbites publiées des satellites (TLE)'
TLE_API_TITLE = 'TLE API — orbites NORAD republiées (secours de CelesTrak)'
GEOCODE_TITLE = 'Open-Meteo — géocodage (CC BY 4.0)'

_planes_cache = TTLCache(PLANES_CACHE_S, maxsize=32)
_world_fresh = TTLCache(WORLD_CACHE_S, maxsize=1)
_world_last = TTLCache(WORLD_KEEP_S, maxsize=1)
_orbits_fresh = TTLCache(ORBITS_CACHE_S, maxsize=1)
_orbits_relay = TTLCache(ORBITS_RETRY_S, maxsize=1)
_orbits_last = TTLCache(MAX_ORBIT_AGE_S, maxsize=1)
_celestrak_asked = TTLCache(CELESTRAK_MIN_INTERVAL_S, maxsize=64)
_detail_cache = TTLCache(DETAIL_CACHE_S, maxsize=DETAIL_MAX_REGIONS)
_daily_fresh = TTLCache(DAILY_CACHE_S, maxsize=1)
_daily_last = TTLCache(DAILY_KEEP_S, maxsize=1)

_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


# --------------------------------------------------------------------------- small helpers

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _num(value: Any) -> float | None:
    """A finite number, else None (a bool is not a number, as in JavaScript)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _jround(value: float) -> int:
    """Math.round of JavaScript (half up), so the numbers match Athéna's."""
    return int(math.floor(value + 0.5))


def _at(row: list, index: int) -> Any:
    return row[index] if len(row) > index else None


def _coord(lat: float, lon: float) -> str:
    return (f"{fr_num(abs(lat), 1)}° {'N' if lat >= 0 else 'S'}, "
            f"{fr_num(abs(lon), 1)}° {'E' if lon >= 0 else 'O'}")


def _lock(name: str) -> asyncio.Lock:
    """One read at a time, per event loop (the tests run several loops, the service only one)."""
    loop = asyncio.get_running_loop()
    held = _locks.get(name)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        _locks[name] = held
    return held[1]


def opensky_enabled() -> bool:
    """La personne a-t-elle allumé OpenSky Network dans Paramètres ? Éteint tant qu'elle ne l'a pas fait."""
    return get_setting(OPENSKY_SETTING, False) is True


def _why(exc: ToolError) -> str:
    if exc.status == 429:
        return exc.message_fr + " Le quota gratuit d'OpenSky (400 crédits par jour sans compte) est sans doute épuisé."
    return exc.message_fr


def _src(title: str, url: str, retrieved_at: str | None = None) -> Source:
    return Source(title=title, url=url, kind='api', retrieved_at=retrieved_at or now_iso())


# --------------------------------------------------------------------------- planes near a point

def category_adsb(code: Any) -> str:
    """ADS-B emitter category: A6 high performance; A1 light, A2 small, A7 helicopter, B* glider, ULM, balloon…"""
    if code == 'A6':
        return 'rapide'
    if code in ('A1', 'A2', 'A7') or (isinstance(code, str) and code.startswith('B')):
        return 'leger'
    return 'ligne'


def category_opensky(code: Any) -> str:
    """OpenSky index 17: 7 high performance; 2 light, 3 small, 8 helicopter, 9 glider, 10 balloon, 12 ULM."""
    value = _num(code)
    if value == 7:
        return 'rapide'
    if value in (2, 3, 8, 9, 10, 12):
        return 'leger'
    return 'ligne'


def square(lat: float, lon: float) -> dict:
    """The ±4° square around the point (wider in longitude away from the equator, 30° at most)."""
    dlon = min(HALF_SIDE_DEG / max(math.cos(math.radians(lat)), .2), 30)
    return {'lamin': max(-90.0, lat - HALF_SIDE_DEG), 'lamax': min(90.0, lat + HALF_SIDE_DEG),
            'lomin': max(-180.0, lon - dlon), 'lomax': min(180.0, lon + dlon)}


def parse_adsb(payload: Any) -> tuple[int, list[dict]]:
    """(mesureA in ms, planes in flight) from adsb.lol /v2/point. Feet → m, knots → km/h."""
    if not isinstance(payload, dict) or not isinstance(payload.get('ac'), list):
        raise ToolError("Réponse inattendue d'adsb.lol (liste d'avions absente).")
    now_ms = _num(payload.get('now')) or time.time() * 1000
    planes = []
    for a in payload['ac']:
        if not isinstance(a, dict):
            continue
        lat, lon = _num(a.get('lat')), _num(a.get('lon'))
        if lat is None or lon is None or a.get('alt_baro') == 'ground':
            continue
        feet = _num(a.get('alt_geom'))
        if feet is None:
            feet = _num(a.get('alt_baro'))
        speed, track = _num(a.get('gs')), _num(a.get('track'))
        planes.append({
            'id': str(a.get('hex') or ''), 'indicatif': str(a.get('flight') or '').strip(),
            'pays': str(a.get('r') or ''), 'type': str(a.get('t') or ''), 'categorie': category_adsb(a.get('category')),
            'lat': lat, 'lon': lon,
            'altitude': None if feet is None else _jround(feet * 0.3048),
            'vitesse': None if speed is None else _jround(speed * 1.852),
            'cap': None if track is None else _jround(track),
            'mesure': _jround(now_ms - (_num(a.get('seen_pos')) or 0) * 1000),
        })
    return _jround(now_ms), planes


def _opensky_states(payload: Any) -> list:
    if not isinstance(payload, dict) or not isinstance(payload.get('states') or [], list):
        raise ToolError("Réponse inattendue d'OpenSky Network (liste d'états absente).")
    return [s for s in payload.get('states') or [] if isinstance(s, list) and len(s) >= 11]


def parse_opensky(payload: Any) -> tuple[int, list[dict]]:
    """(mesureA in ms, planes in flight) from OpenSky states/all?extended=1.

    [0] icao24 [1] call sign [2] country [3] time of the position [5] lon [6] lat [7] baro altitude [8] on ground
    [9] speed m/s [10] track [13] geometric altitude [17] category (extended=1)."""
    states = _opensky_states(payload)
    seconds = _num(payload.get('time'))
    planes = []
    for s in states:
        lat, lon = _num(s[6]), _num(s[5])
        if s[8] or lat is None or lon is None:
            continue
        alt = _num(_at(s, 13))
        if alt is None:
            alt = _num(s[7])
        speed, track = _num(s[9]), _num(s[10])
        seen = _num(s[3])
        if seen is None:
            seen = seconds if seconds is not None else time.time()
        planes.append({
            'id': str(s[0] or ''), 'indicatif': str(s[1] or '').strip(), 'pays': str(s[2] or ''), 'type': '',
            'categorie': category_opensky(_at(s, 17)), 'lat': lat, 'lon': lon,
            'altitude': None if alt is None else _jround(alt),
            'vitesse': None if speed is None else _jround(speed * 3.6),
            'cap': None if track is None else _jround(track),
            'mesure': _jround(seen * 1000),
        })
    return _jround(seconds * 1000) if seconds else _jround(time.time() * 1000), planes


def nearest_first(planes: list[dict], lat: float, lon: float) -> list[dict]:
    k = math.cos(math.radians(lat))

    def distance(a: dict) -> float:
        dlon = (a['lon'] - lon + 180) % 360 - 180
        return (a['lat'] - lat) ** 2 + (dlon * k) ** 2

    return sorted(planes, key=distance)


async def planes_near(place: dict) -> tuple[dict, str, str | None]:
    """(data, retrieved_at, fallback note). adsb.lol first, OpenSky Network if it fails; cached 60 s."""
    key = (round(place['lat'], 3), round(place['lon'], 3), place['nom'])
    hit = _planes_cache.get(key)
    if hit is not None:
        return hit
    async with _lock('avions'):
        hit = _planes_cache.get(key)
        if hit is not None:
            return hit
        note = None
        try:
            payload = await fetch_json(ADSB_URL.format(lat=place['lat'], lon=place['lon']), source='adsb.lol',
                                       timeout=10, max_bytes=10_000_000)
            measured_at, planes = parse_adsb(payload)
            source = 'adsb.lol (ODbL)'
        except ToolError as exc:
            note = exc.message_fr
            if not opensky_enabled():   # no silent fallback on a source the person has not turned on
                raise ToolError(f"Avions indisponibles : {note} Le secours par OpenSky Network est éteint "
                                "(Paramètres). Aucun avion n'est affiché.") from None
            log.info('adsb.lol indisponible (%s), essai OpenSky', note)
            box = square(place['lat'], place['lon'])
            try:
                payload = await fetch_json(OPENSKY_URL, params={**{k: f'{v:.3f}' for k, v in box.items()},
                                                                'extended': 1},
                                           source='OpenSky Network', timeout=10, max_bytes=10_000_000)
                measured_at, planes = parse_opensky(payload)
            except ToolError as second:
                raise ToolError(f'Avions indisponibles : {note} Secours : {_why(second)} '
                                "Aucun avion n'est affiché.") from None
            source = 'OpenSky Network'
        planes = nearest_first(planes, place['lat'], place['lon'])
        data = {'lieu': {'nom': place['nom'], 'lat': place['lat'], 'lon': place['lon']}, 'source': source,
                'mesureA': measured_at, 'demiCote': HALF_SIDE_DEG, 'total': len(planes),
                'liste': planes[:MAX_PLANES]}
        value = (data, now_iso(), note)
        _planes_cache.set(key, value)
        return value


def summarize_planes(data: dict, note: str | None) -> str:
    place = data['lieu']
    where = f"autour de {place['nom']}" if place['nom'] else f"autour du point {_coord(place['lat'], place['lon'])}"
    who = 'adsb.lol' if data['source'].startswith('adsb') else 'OpenSky Network'
    total = data['total']
    if total == 0:
        text = f'{who} : aucun avion en vol mesuré {where} (carré de ±{HALF_SIDE_DEG}°, environ 900 km de côté).'
    else:
        planes = 'avion en vol mesuré' if total == 1 else 'avions en vol mesurés'
        shown = f'{MAX_PLANES} au plus affichés' if total <= MAX_PLANES else f'les {MAX_PLANES} plus proches affichés'
        text = f'{who} : {fr_num(total, 0)} {planes} {where} ; {shown}.'
        first = data['liste'][0]
        bits = [first['indicatif'] or first['id']]
        if first['type']:
            bits[0] += f" ({first['type']})"
        if first['altitude'] is not None:
            bits.append(f"{fr_num(first['altitude'], 0)} m d'altitude")
        if first['vitesse'] is not None:
            bits.append(f"{fr_num(first['vitesse'], 0)} km/h")
        text += ' Le plus proche : ' + ', '.join(bits) + '.'
    if note:
        text = f'adsb.lol ne répond pas ({note}) : OpenSky Network sert de secours. ' + text
    return text


class PlanesParams(Params):
    lat: float | None = Field(default=None, ge=-85, le=85)
    lon: float | None = Field(default=None, ge=-180, le=180)
    city: str | None = Field(default=None, max_length=100)

    @model_validator(mode='after')
    def _both_or_none(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError('donnez la latitude et la longitude ensemble')
        return self


@tool('sky.planes', description="Avions en vol autour d'un point ou d'une ville (adsb.lol ; OpenSky Network en "
      "secours seulement s'il est allumé dans Paramètres) : les 40 plus proches et le total mesuré.",
      action='web.fetch', params=PlanesParams,
      network=True, provenance='adsb.lol', timeout_s=35)
async def planes_tool(p: PlanesParams) -> ToolResult:
    sources = []
    if p.lat is not None and p.lon is not None:
        place = {'nom': '', 'lat': p.lat, 'lon': p.lon}
    else:   # the default city of the settings, geocoded like the weather
        city = (p.city or '').strip() or str(get_setting('default_city', '') or '').strip() or 'Paris'
        found = await weather.geocode(city)
        place = {'nom': found['name'], 'lat': float(found['latitude']), 'lon': float(found['longitude'])}
        sources.append(_src(GEOCODE_TITLE, weather.GEOCODE_URL))
    data, retrieved_at, note = await planes_near(place)
    if data['source'].startswith('adsb'):
        sources.insert(0, _src(ADSB_TITLE, 'https://adsb.lol/', retrieved_at))
        provenance = 'adsb.lol'
    else:
        sources.insert(0, _src(OPENSKY_TITLE, 'https://opensky-network.org/', retrieved_at))
        provenance = 'opensky'
    return ToolResult(ok=True, data=data, summary=summarize_planes(data, note), provenance=provenance,
                      sources=sources)


# --------------------------------------------------------------------------- world traffic

def parse_world(payload: Any) -> dict:
    """[lat, lon, track °, speed km/h, altitude m] per plane in flight, one in N at a regular step (up to 1 500):
    the sample keeps the real distribution (dense over Europe and the United States, empty over the oceans)."""
    flying = []
    for x in _opensky_states(payload):
        lat, lon, speed, track = _num(x[6]), _num(x[5]), _num(x[9]), _num(x[10])
        if x[8] or lat is None or lon is None or speed is None or track is None:
            continue
        alt = _num(_at(x, 13))
        if alt is None:
            alt = _num(x[7]) or 0.0
        flying.append([round(lat, 3), round(lon, 3), _jround(track), _jround(speed * 3.6), _jround(alt)])
    step = max(1.0, len(flying) / MAX_WORLD)
    sample, i = [], 0.0
    while i < len(flying) and len(sample) < MAX_WORLD:
        sample.append(flying[int(i)])
        i += step
    seconds = _num(payload.get('time'))
    return {'source': 'OpenSky Network', 'mesureA': _jround((seconds or time.time()) * 1000),
            'total': len(flying), 'avions': sample}


async def world_traffic() -> tuple[dict, str, str | None]:
    """(data, retrieved_at, failure note). Cached 5 min; if OpenSky fails, the last real measure (24 h at most).
    Off, nothing is read and nothing kept is shown: the person is told how to turn it on."""
    if not opensky_enabled():
        raise ToolError(WORLD_OFF)
    hit = _world_fresh.get('monde')
    if hit is not None:
        return hit
    async with _lock('monde'):
        hit = _world_fresh.get('monde')
        if hit is not None:
            return hit
        try:
            payload = await fetch_json(OPENSKY_URL, source='OpenSky Network', timeout=20, max_bytes=40_000_000)
            value = (parse_world(payload), now_iso(), None)
            _world_last.set('monde', value)
        except ToolError as exc:
            last = _world_last.get('monde')
            if last is None:
                raise ToolError(f"Trafic mondial indisponible : {_why(exc)} Aucun avion n'est affiché.") from None
            value = (last[0], last[1], _why(exc))
        _world_fresh.set('monde', value)   # also after a failure: the next try is 5 min later (quota)
        return value


def summarize_world(data: dict, note: str | None) -> str:
    text = (f"OpenSky Network : {fr_num(data['total'], 0)} avions en vol mesurés dans le monde ; "
            f"{fr_num(len(data['avions']), 0)} affichés, pris à pas régulier pour garder la vraie répartition.")
    if note:
        age = fr_duration(max(0.0, time.time() - data['mesureA'] / 1000))
        text = f'{note} Dernière vraie mesure gardée, prise il y a {age}. ' + text
    return text


@tool('sky.traffic', description='Échantillon du trafic aérien mondial mesuré (OpenSky Network, éteint tant que '
      "la personne ne l'a pas allumé dans Paramètres) : 1 500 avions au plus, pris à pas régulier.",
      action='web.fetch', params=NoParams, network=True, provenance='opensky',
      timeout_s=30)
async def traffic_tool(_p: NoParams) -> ToolResult:
    data, retrieved_at, note = await world_traffic()
    return ToolResult(ok=True, data=data, summary=summarize_world(data, note), provenance='opensky',
                      sources=[_src(OPENSKY_TITLE, 'https://opensky-network.org/', retrieved_at)])


# --------------------------------------------------------------------------- satellites

@dataclass
class Orbit:
    norad: int
    nom: str
    role: str
    l1: str
    l2: str
    source: str        # 'CelesTrak' or 'TLE API'
    satrec: Any
    epoch_ms: int      # date of the published orbit


def to_orbit(norad: int, nom: str, role: str, l1: str, l2: str, source: str) -> Orbit | None:
    if Satrec is None:
        raise ToolError("La bibliothèque sgp4 n'est pas installée : les positions des satellites ne peuvent pas "
                        'être calculées.')
    if not (l1.startswith('1 ') and l2.startswith('2 ')):
        return None
    sat = Satrec.twoline2rv(l1, l2)
    if getattr(sat, 'error', 0) != 0:
        return None
    epoch_ms = _jround((sat.jdsatepoch + sat.jdsatepochF - 2440587.5) * 86_400_000)
    return Orbit(norad, nom, role, l1, l2, source, sat, epoch_ms)


def _norad_of(line1: str) -> int | None:
    try:
        return int(line1[2:7])
    except ValueError:
        return None


def parse_tle_text(text: str, norad: int) -> tuple[str, str, str] | None:
    """(name, line 1, line 2) of the requested satellite in a TLE text, or None."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i in range(len(lines) - 1):
        if lines[i].startswith('1 ') and lines[i + 1].startswith('2 '):
            if _norad_of(lines[i]) != norad:
                return None
            return (lines[i - 1] if i > 0 else str(norad)), lines[i], lines[i + 1]
    return None


async def read_tle(norad: int) -> tuple[str, str, str, str] | None:
    """(name, line 1, line 2, source). CelesTrak at most once per satellite every 2 h, then TLE API."""
    if _celestrak_asked.get(norad) is None:
        _celestrak_asked.set(norad, True)
        try:
            got = await fetch(CELESTRAK_URL, params={'CATNR': norad, 'FORMAT': 'tle'}, source='CelesTrak',
                              timeout=10, max_bytes=20_000, accept='text/plain')
            found = parse_tle_text(got.content.decode('utf-8', 'replace'), norad)
            if found:
                return (*found, 'CelesTrak')
        except ToolError as exc:
            log.info('CelesTrak, satellite %s : %s', norad, exc.message_fr)
    # 06/10/2026 (Athéna) : CelesTrak refusait toute connexion ; TLE API republie les mêmes orbites NORAD.
    try:
        payload = await fetch_json(TLE_API_URL.format(norad=norad), source='TLE API', timeout=10, max_bytes=50_000)
    except ToolError as exc:
        log.info('TLE API, satellite %s : %s', norad, exc.message_fr)
        return None
    if not isinstance(payload, dict):
        return None
    l1, l2 = str(payload.get('line1') or '').strip(), str(payload.get('line2') or '').strip()
    if not (l1.startswith('1 ') and l2.startswith('2 ')) or _norad_of(l1) != norad:
        return None
    return str(payload.get('name') or norad), l1, l2, 'TLE API'


def orbits_file() -> Path:
    """Next to the base of this AURORA (a test instance has its own copy, never the user's)."""
    return Path(aurora_home()) / 'cache' / 'orbites-satellites.json'


def save_orbits(orbits: list[Orbit]) -> None:
    path = orbits_file()
    kept = [{'norad': o.norad, 'nom': o.nom, 'role': o.role, 'l1': o.l1, 'l2': o.l2, 'source': o.source}
            for o in orbits]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps({'lu': now_iso(), 'orbites': kept}, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, path)
    except OSError as exc:
        log.warning('Orbites non gardées sur le disque : %s', exc)


def read_saved_orbits() -> tuple[list[Orbit], str | None]:
    """(orbits, ISO date of their reading) from the disk copy; measured orbits, never made up."""
    try:
        payload = json.loads(orbits_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [], None
    if not isinstance(payload, dict):
        return [], None
    roles = dict(SATELLITES)
    orbits = []
    for item in payload.get('orbites') or []:
        if not isinstance(item, dict) or not isinstance(item.get('norad'), int) or item['norad'] not in roles:
            continue
        orbit = to_orbit(item['norad'], str(item.get('nom') or item['norad']), roles[item['norad']],
                         str(item.get('l1') or ''), str(item.get('l2') or ''), str(item.get('source') or 'CelesTrak'))
        if orbit:
            orbits.append(orbit)
    return orbits, (str(payload.get('lu') or '') or None)


async def load_orbits() -> tuple[list[Orbit], dict]:
    """(orbits, info). Network every 6 h; if it fails: the orbits already read, else the disk copy, and a new try
    15 min later. info: retrieved_at, kept_at (disk copy), network_failed."""
    for cache in (_orbits_fresh, _orbits_relay):
        hit = cache.get('orbites')
        if hit is not None:
            return hit
    async with _lock('orbites'):
        for cache in (_orbits_fresh, _orbits_relay):
            hit = cache.get('orbites')
            if hit is not None:
                return hit
        read = await asyncio.gather(*(read_tle(norad) for norad, _role in SATELLITES))
        orbits = []
        for (norad, role), tle in zip(SATELLITES, read):
            orbit = to_orbit(norad, tle[0], role, tle[1], tle[2], tle[3]) if tle else None
            if orbit:
                orbits.append(orbit)
        if orbits:
            value = (orbits, {'retrieved_at': now_iso(), 'kept_at': None, 'network_failed': False})
            _orbits_fresh.set('orbites', value)
            _orbits_last.set('orbites', value)
            save_orbits(orbits)
            log.info('Orbites lues : %d/%d (%s)', len(orbits), len(SATELLITES),
                     ', '.join(sorted({o.source for o in orbits})))
            return value
        last = _orbits_last.get('orbites')
        if last is not None:
            value = (last[0], {**last[1], 'network_failed': True})
        else:
            kept, kept_at = read_saved_orbits()
            if not kept:
                raise ToolError("CelesTrak et TLE API sont injoignables et aucune orbite n'est gardée sur le "
                                "disque : aucun satellite n'est affiché.")
            log.warning('Sources des orbites injoignables : %d orbites relues sur le disque', len(kept))
            value = (kept, {'retrieved_at': kept_at, 'kept_at': kept_at, 'network_failed': True})
        _orbits_relay.set('orbites', value)
        return value


def geodetic(x: float, y: float, z: float, gmst: float) -> tuple[float, float, float]:
    """TEME position (km) → (latitude °, longitude °, altitude km) on the WGS84 ellipsoid.

    Rotation of -GMST around the polar axis (polar motion ignored, like satellite.js), then the iterative
    latitude of satellite.js eciToGeodetic."""
    c, s = math.cos(gmst), math.sin(gmst)
    xe, ye = c * x + s * y, -s * x + c * y
    r = math.hypot(xe, ye)
    lat = math.atan2(z, r)
    for _ in range(20):
        sin_lat = math.sin(lat)
        k = 1 / math.sqrt(1 - WGS84_E2 * sin_lat * sin_lat)
        lat = math.atan2(z + WGS84_A * k * WGS84_E2 * sin_lat, r)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    k = 1 / math.sqrt(1 - WGS84_E2 * sin_lat * sin_lat)
    if abs(cos_lat) > 1e-9:
        alt = r / cos_lat - WGS84_A * k
    else:   # above a pole
        alt = abs(z) - WGS84_A * k * (1 - WGS84_E2)
    return math.degrees(lat), math.degrees(math.atan2(ye, xe)), alt


def position(orbit: Orbit, jd: float, fr: float) -> tuple[float, float, float] | None:
    err, r, _v = orbit.satrec.sgp4(jd, fr)
    if err != 0 or not all(math.isfinite(c) for c in r):
        return None
    return geodetic(r[0], r[1], r[2], gstime(jd + fr))


def satellite_positions(orbits: list[Orbit], when: datetime) -> tuple[list[dict], list[Orbit]]:
    """(satellites shown, orbits too old). Positions computed at ``when``, track 0 to 45 min, one per minute."""
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    now_ms = when.timestamp() * 1000
    shown, too_old = [], []
    for orbit in orbits:
        if now_ms - orbit.epoch_ms > MAX_ORBIT_AGE_S * 1000:
            too_old.append(orbit)
            continue
        here = position(orbit, jd, fr)
        if here is None:
            continue
        trace = []
        for minute in range(TRACE_MINUTES + 1):
            point = position(orbit, jd, fr + minute / 1440)
            if point is not None:
                trace.append([round(point[0], 2), round(point[1], 2)])
        shown.append({'norad': orbit.norad, 'nom': orbit.nom, 'role': orbit.role, 'lat': round(here[0], 4),
                      'lon': round(here[1], 4), 'altitude': _jround(here[2]), 'orbiteDu': orbit.epoch_ms,
                      'source': orbit.source, 'trace': trace})
    return shown, too_old


def _day_fr(ms: int | float) -> str:
    d = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return f'{d.day} {weather.MONTHS_FR[d.month - 1]} {d.year}'


def summarize_satellites(shown: list[dict], orbits: list[Orbit], too_old: list[Orbit], info: dict) -> str:
    origin = fr_list(sorted({o.source for o in orbits}))
    if shown:
        lines = [f"{s['nom']} ({s['role']}) : {_coord(s['lat'], s['lon'])}, {fr_num(s['altitude'], 0)} km "
                 f"d'altitude, orbite du {_day_fr(s['orbiteDu'])}" for s in shown]
        text = (f'{len(shown)} satellite(s) : positions calculées avec le modèle SGP4 à partir des orbites publiées '
                f'({origin}), pas observées. ' + ' ; '.join(lines) + '.')
    else:
        text = 'Aucun satellite affiché.'
    if too_old:
        text += f" Orbite de plus de 14 jours, non affiché : {fr_list([o.nom for o in too_old])}."
    read = {o.norad for o in orbits}
    missing = [role for norad, role in SATELLITES if norad not in read]
    if missing:
        text += f" Orbite introuvable : {fr_list(missing)}."
    if info.get('network_failed'):
        if info.get('kept_at'):
            text += (f" CelesTrak et TLE API ne répondent pas : orbites relues sur le disque "
                     f"(lues le {str(info['kept_at'])[:16].replace('T', ' à ')} UTC) ; nouvel essai dans 15 min.")
        else:
            text += ' CelesTrak et TLE API ne répondent pas : orbites déjà lues gardées ; nouvel essai dans 15 min.'
    return text


@tool('sky.satellites', description="Position calculée (SGP4) de 8 satellites — ISS, Tiangong, Hubble, Suomi NPP, "
      'NOAA-20, Terra, Aqua, Landsat 9 — et leur trace des 45 prochaines minutes (orbites CelesTrak).',
      action='web.fetch', params=NoParams, network=True, provenance='celestrak', timeout_s=40)
async def satellites_tool(_p: NoParams) -> ToolResult:
    orbits, info = await load_orbits()
    shown, too_old = satellite_positions(orbits, _utcnow())
    sources = []
    for name, title, url in (('CelesTrak', CELESTRAK_TITLE, 'https://celestrak.org/'),
                             ('TLE API', TLE_API_TITLE, 'https://tle.ivanstanojevic.me/')):
        if any(o.source == name for o in orbits):
            sources.append(_src(title, url, info.get('retrieved_at')))
    return ToolResult(ok=True, data={'liste': shown}, summary=summarize_satellites(shown, orbits, too_old, info),
                      provenance='celestrak', sources=sources)


# --------------------------------------------------------------------------- NASA GIBS images (not tools)

async def _gibs(layer: str, bbox: str, width: int, height: int, day: str | None = None) -> bytes:
    """A NASA GIBS WMS image (EPSG:4326, BBOX lat,lon,lat,lon) in JPEG; ToolError otherwise."""
    params = {'SERVICE': 'WMS', 'REQUEST': 'GetMap', 'VERSION': '1.3.0', 'LAYERS': layer, 'CRS': 'EPSG:4326',
              'BBOX': bbox, 'WIDTH': width, 'HEIGHT': height, 'FORMAT': 'image/jpeg'}
    if day:
        params['TIME'] = day
    got = await fetch(GIBS_WMS_URL, params=params, source='NASA GIBS', timeout=20, max_bytes=IMAGE_MAX_BYTES,
                      accept='image/jpeg')
    if 'image/jpeg' not in got.content_type.lower() or not got.content.startswith(b'\xff\xd8'):
        raise ToolError("NASA GIBS n'a pas renvoyé d'image JPEG.")
    return got.content


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """« la1,lo1,la2,lo2 » → 4 finite numbers; ValueError (French) otherwise."""
    parts = str(text or '').split(',')
    try:
        values = tuple(float(p) for p in parts)
    except ValueError:
        values = ()
    if len(values) != 4 or not all(math.isfinite(v) and abs(v) <= 1000 for v in values):
        raise ValueError('Région invalide : quatre nombres attendus, « lat1,lon1,lat2,lon2 ».')
    return values


def snap_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Edges rounded on a grid (a quarter of the region, to the quarter degree) so that a region already seen comes
    back from the cache. ValueError if the region is empty or spans more than 40° of latitude."""
    la1, lo1, la2, lo2 = bbox
    step = max(.25, _jround(max(la2 - la1, (lo2 - lo1) / 2) / 4 / .25) * .25)
    snapped = (max(-90.0, math.floor(la1 / step) * step), max(-180.0, math.floor(lo1 / step) * step),
               min(90.0, math.ceil(la2 / step) * step), min(180.0, math.ceil(lo2 / step) * step))
    if not (snapped[2] > snapped[0] and snapped[3] > snapped[1]) or snapped[2] - snapped[0] > DETAIL_MAX_LAT_SPAN:
        raise ValueError(f'Région invalide : elle doit être non vide et couvrir {DETAIL_MAX_LAT_SPAN}° de latitude '
                         'au plus.')
    return tuple(v + 0.0 for v in snapped)   # + 0.0: no « -0 »


def bbox_text(bbox: tuple[float, ...]) -> str:
    """« 44,4.25,46,6 » like JavaScript's join (integers without « .0 »)."""
    return ','.join(str(int(v)) if float(v).is_integer() else repr(float(v)) for v in bbox)


async def detail_image(bbox: tuple[float, float, float, float]) -> tuple[bytes, tuple[float, float, float, float]]:
    """(JPEG, region served) of NASA Blue Marble Next Generation (no clouds, about 500 m per pixel, public domain).
    60 regions kept 24 h. ValueError for an invalid region (no network call), ToolError if GIBS fails."""
    served = snap_bbox(bbox)
    key = bbox_text(served)
    hit = _detail_cache.get(key)
    if hit is not None:
        return hit, served
    height = 1024
    ratio = (served[3] - served[1]) / (served[2] - served[0]) * math.cos(math.radians((served[0] + served[2]) / 2))
    width = max(256, min(2048, _jround(height * ratio)))
    image = await _gibs(DETAIL_LAYER, key, width, height)
    _detail_cache.set(key, image)
    return image, served


async def daily_image() -> tuple[bytes, str]:
    """(JPEG 2048×1024, day AAAA-MM-JJ) of VIIRS (Suomi NPP) for the whole world: yesterday in UTC, else the day
    before; a day without data (black image) counts as missing. Kept 6 h; if GIBS fails, the last image (7 days at
    most) with its own date. No other satellite in its place: the globe says « Suomi NPP » under the image."""
    hit = _daily_fresh.get('jour')
    if hit is not None:
        return hit
    async with _lock('nasa'):
        hit = _daily_fresh.get('jour')
        if hit is not None:
            return hit
        today, reasons = _utcnow(), []
        width, height = DAILY_SIZE
        for back in (1, 2):
            day = (today - timedelta(days=back)).date().isoformat()
            try:
                image = await _gibs(DAILY_LAYER, '-90,-180,90,180', width, height, day)
            except ToolError as exc:
                reasons.append(f"{day} : {exc.message_fr.rstrip('.')}")
                continue
            if len(image) < width * height * DAILY_EMPTY_BYTES_PER_PIXEL:
                reasons.append(f'{day} : image vide, Suomi NPP sans donnée ce jour-là sur NASA GIBS')
                continue
            value = (image, day)
            _daily_fresh.set('jour', value)
            _daily_last.set('jour', value)
            return value
        last = _daily_last.get('jour')
        if last is not None:
            return last
        log.info('Image NASA du jour indisponible : %s', ' ; '.join(reasons))
        raise ToolError('Image NASA du jour indisponible (' + ' ; '.join(reasons) + '). Le globe garde sa carte locale.')
