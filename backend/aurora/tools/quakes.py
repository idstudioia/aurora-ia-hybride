"""Recent earthquakes (magnitude ≥ 2.5, last 24 h) from the USGS GeoJSON summary feed."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from .base import Params, Source, TTLCache, ToolError, ToolResult, fetch_json, fr_duration, fr_num, now_iso, tool

FEED_URL = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson'
CACHE_S = 300
_cache = TTLCache(CACHE_S, maxsize=2)


def _iso_ms(ms) -> str | None:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def parse_geojson(payload: dict, details: bool = False) -> dict:
    """``details``: also the fields the globe shows (events.py, 8/11/2026): time_ms, mag_type, felt, mmi, status.
    Without it, the output is the one quakes.recent has always had."""
    if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
        raise ToolError("Réponse inattendue de l'USGS (GeoJSON invalide).")
    quakes = []
    for feat in payload['features']:
        props = feat.get('properties') or {}
        coords = (feat.get('geometry') or {}).get('coordinates') or []
        mag = props.get('mag')
        if not isinstance(mag, (int, float)) or len(coords) < 2:
            continue
        extra = {}
        if details:
            when = props.get('time')
            extra = {'time_ms': int(when) if isinstance(when, (int, float)) else None,
                     'mag_type': props.get('magType'), 'felt': props.get('felt'), 'mmi': props.get('mmi'),
                     'status': props.get('status')}
        quakes.append({**extra,
            'id': feat.get('id'),
            'mag': round(float(mag), 1),
            'place': props.get('place') or '',
            'time': _iso_ms(props.get('time')),
            'lat': coords[1],
            'lon': coords[0],
            'depth_km': coords[2] if len(coords) > 2 else None,
            'url': props.get('url') or '',
            'tsunami': bool(props.get('tsunami')),
            'alert': props.get('alert'),
        })
    quakes.sort(key=lambda q: q['time'] or '', reverse=True)
    meta = payload.get('metadata') or {}
    return {'quakes': quakes, 'count': len(quakes), 'generated_at': _iso_ms(meta.get('generated')),
            'title': meta.get('title') or '', 'window_hours': 24, 'min_magnitude': 2.5}


async def recent() -> dict:
    hit = _cache.get('day')
    if hit is not None:
        return {**hit, 'cached': True}
    payload = await fetch_json(FEED_URL, source="L'USGS", timeout=10, max_bytes=10_000_000)
    data = parse_geojson(payload)
    data['retrieved_at'] = now_iso()   # time of the real request, kept by cached reads
    _cache.set('day', data)
    return {**data, 'cached': False}


class QuakesParams(Params):
    min_mag: float = Field(default=2.5, ge=2.5, le=10)
    limit: int = Field(default=200, ge=1, le=1000)


@tool('quakes.recent', description='Séismes de magnitude ≥ 2,5 des dernières 24 h (USGS).', action='web.fetch',
      params=QuakesParams, network=True, provenance='usgs', timeout_s=20)
async def quakes_tool(p: QuakesParams) -> ToolResult:
    data = await recent()
    selected = [q for q in data['quakes'] if q['mag'] >= p.min_mag]
    data = {**data, 'quakes': selected[:p.limit], 'count': len(selected), 'min_magnitude': p.min_mag}
    src = [Source(title='USGS — Earthquake Hazards Program (flux 24 h)', url=FEED_URL, kind='api',
                  retrieved_at=data.get('retrieved_at') or now_iso())]
    if not selected:
        summary = f"USGS : aucun séisme de magnitude ≥ {fr_num(p.min_mag, 1)} enregistré ces dernières 24 h."
        return ToolResult(ok=True, data=data, summary=summary, provenance='usgs', sources=src)
    strongest = max(selected, key=lambda q: q['mag'])
    data['strongest'] = strongest
    ago = ''
    if strongest['time']:
        delta = (datetime.now(timezone.utc) - datetime.fromisoformat(strongest['time'].replace('Z', '+00:00'))).total_seconds()
        ago = f", il y a {fr_duration(max(delta, 0))}"
    summary = (f"USGS : {len(selected)} séisme(s) de magnitude ≥ {fr_num(p.min_mag, 1)} en 24 h ; le plus fort : "
               f"M {fr_num(strongest['mag'], 1)} — {strongest['place']}{ago}"
               f" (profondeur {fr_num(strongest['depth_km'], 0)} km).")
    return ToolResult(ok=True, data=data, summary=summary, provenance='usgs', sources=src)
