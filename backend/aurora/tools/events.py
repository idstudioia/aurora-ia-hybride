"""Globe hologramme (Monde 3D) — les événements naturels en cours : séismes forts, tsunamis, volcans, cyclones, tornades.

Porté d'Athéna (server/services/evenementsService.ts, 04/10/2026), voir reviews/globe-contrat.md :
- séismes : USGS, magnitude 4,5 ou plus sur 7 jours (lu par quakes.parse_geojson). Le champ « tsunami » de l'USGS
  ne dit PAS qu'un tsunami a eu lieu (il marque les grands séismes en mer) : il ne sert pas pour les tsunamis ;
- tsunamis : les bulletins Atom des deux centres d'alerte de la NOAA (PTWC, NTWC) des 7 derniers jours. Un bulletin
  « Information » n'est PAS une alerte : sa catégorie est donnée telle quelle. Lus avec les protections de news.py ;
- volcans et cyclones : GDACS (ONU, Commission européenne), alertes orange ou rouges en cours ou finies depuis
  moins de 2 jours ;
- tornades : les alertes « Tornado Warning » du service météo américain (NWS), États-Unis seulement. Ailleurs,
  aucune source libre : on ne dit jamais « aucune tornade dans le monde ».
Une source en panne n'empêche pas les autres et l'interface sait lesquelles ont répondu. Les valeurs de ``infos``
sont celles de la source (nombre, date ISO, ou code comme Red ou Warning que l'interface traduit) : rien n'est déduit.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from . import news, quakes
from .base import Params, Source, TTLCache, ToolError, ToolResult, fetch, fetch_json, fr_list, fr_num, tool
from .weather import cached_or_fallback, fallback_note, js_round

log = logging.getLogger(__name__)

USGS_URL = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson'
TSUNAMI_URL = 'https://www.tsunami.gov/events/xml/{code}Atom.xml'
TSUNAMI_CENTRES = (('PHEB', 'NOAA PTWC'), ('PAAQ', 'NOAA NTWC'))
GDACS_URL = 'https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH'
NWS_URL = 'https://api.weather.gov/alerts/active?event=Tornado%20Warning'
WEEK_MS = 7 * 86_400_000
GDACS_RECENT_MS = 2 * 86_400_000
TIMEOUT_S = 15

_cache = TTLCache(10 * 60, maxsize=1)
_fallback = TTLCache(24 * 3600, maxsize=1)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _js_text(value) -> str:
    """String(value) of JavaScript, as in Athéna (5.0 → '5', True → 'true')."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def infos(pairs: list[tuple[str, object]]) -> list[dict]:
    """[{cle, valeur}] with Athéna's keys; a field empty at the source does not appear."""
    out = []
    for key, value in pairs:
        if value is None or value == '' or (isinstance(value, float) and not math.isfinite(value)):
            continue
        out.append({'cle': key, 'valeur': _js_text(value)})
    return out


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _ms(text) -> int | None:
    return news._epoch_ms(news.parse_date(str(text or '')))


# --------------------------------------------------------------------------- the five sources

def quake_events(payload: dict) -> list[dict]:
    """USGS GeoJSON (M4,5+, 7 days) → events. Athéna's details: magnitude (with its type), depth, « felt » reports,
    intensity, PAGER alert, review status."""
    events = []
    for q in quakes.parse_geojson(payload, details=True)['quakes']:
        lat, lon = _number(q['lat']), _number(q['lon'])
        if lat is None or lon is None:
            continue
        depth, mmi = _number(q['depth_km']), _number(q['mmi'])
        events.append({
            'type': 'seisme', 'titre': q['place'], 'detail': '', 'lat': lat, 'lon': lon, 'date': q['time_ms'],
            'source': 'USGS',
            'infos': infos([('magnitude', f"{q['mag']:.1f} ({q['mag_type'] or ''})"),
                            ('profondeur', f'{js_round(depth)} km' if depth is not None else None),
                            ('ressenti', q['felt']), ('intensite', f'{mmi:.1f}' if mmi is not None else None),
                            ('alerte', q['alert']), ('statut', q['status'])]),
        })
    return events


async def _quakes() -> list[dict]:
    return quake_events(await fetch_json(USGS_URL, source="L'USGS", timeout=TIMEOUT_S, max_bytes=10_000_000))


def _labelled(summary, label: str) -> str:
    """Text after a bold label of the bulletin (« <strong>Category:</strong> Information »), read with ElementTree."""
    if summary is None:
        return ''
    for el in summary.iter():
        if news._local(el.tag) in ('strong', 'b') and news._text(el).rstrip(': ').endswith(label):
            return (el.tail or '').strip()
    return ''


def tsunami_events(content: bytes, centre: str, now_ms: int | None = None) -> list[dict]:
    """A NOAA Atom feed → the bulletins of the last 7 days (category and magnitude as the bulletin gives them)."""
    now_ms = now_ms or _now_ms()
    events = []
    for entry in news.safe_xml(content, f'du {centre}').iter():
        if news._local(entry.tag) != 'entry':
            continue
        fields = {news._local(child.tag): child for child in entry}
        lat, lon = _float(news._text(fields.get('lat'))), _float(news._text(fields.get('long')))
        when = _ms(news._text(fields.get('updated')))
        if lat is None or lon is None or when is None or not now_ms - when < WEEK_MS:
            continue
        summary = fields.get('summary')
        events.append({'type': 'tsunami', 'titre': ' '.join(news._text(fields.get('title')).split()), 'detail': '',
                       'lat': lat, 'lon': lon, 'date': when, 'source': centre,
                       'infos': infos([('categorie', _labelled(summary, 'Category')),
                                       ('magnitude', _labelled(summary, 'Magnitude'))])})
    return events


def _float(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


async def _tsunamis() -> tuple[list[dict], list[str]]:
    """Both centres; one silent is said, the other's bulletins are kept (Athéna dropped both)."""
    async def read(code: str, centre: str) -> list[dict]:
        got = await fetch(TSUNAMI_URL.format(code=code), source=f'Le centre {centre}', timeout=TIMEOUT_S,
                          max_bytes=news.MAX_FEED_BYTES, accept='application/atom+xml, application/xml, text/xml')
        return tsunami_events(got.content, centre)

    results = await asyncio.gather(*(read(code, centre) for code, centre in TSUNAMI_CENTRES), return_exceptions=True)
    events, notes = [], []
    for (_code, centre), res in zip(TSUNAMI_CENTRES, results):
        if isinstance(res, BaseException):
            if isinstance(res, ToolError) and res.data.get('offline'):
                raise res
            notes.append(f"{centre} : {res.message_fr if isinstance(res, ToolError) else 'erreur de lecture'}")
        else:
            events.extend(res)
    if len(notes) == len(TSUNAMI_CENTRES):
        raise ToolError(' ; '.join(notes))
    return events, notes


def gdacs_events(payload: dict, kind: str, now_ms: int | None = None) -> list[dict]:
    """GDACS search → the orange or red alerts in progress, or finished less than 2 days ago (its search also
    returns the finished ones)."""
    if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
        raise ToolError('Réponse inattendue de GDACS.')
    now_ms = now_ms or _now_ms()
    events = []
    for feat in payload['features']:
        props = (feat.get('properties') if isinstance(feat, dict) else None) or {}
        coords = ((feat.get('geometry') or {}).get('coordinates') or []) if isinstance(feat, dict) else []
        until = _ms(props.get('todate'))
        current = str(props.get('iscurrent')).lower() == 'true'
        if not (current or (until is not None and now_ms - until < GDACS_RECENT_MS)):
            continue
        if props.get('alertlevel') not in ('Orange', 'Red') or len(coords) < 2:
            continue
        lat, lon = _number(coords[1]), _number(coords[0])
        if lat is None or lon is None:
            continue
        severity = props.get('severitydata') if isinstance(props.get('severitydata'), dict) else {}
        events.append({
            'type': kind, 'titre': ' '.join(str(props.get('name') or '').split()), 'detail': '',
            'infos': infos([('alerte', props.get('alertlevel')), ('pays', props.get('country')),
                            ('force', severity.get('severitytext')),
                            ('debut', f"{props['fromdate']}Z" if props.get('fromdate') else None),
                            ('fin', f"{props['todate']}Z" if props.get('todate') else None)]),
            'lat': lat, 'lon': lon, 'date': until or now_ms, 'source': 'GDACS',
        })
    return events


async def _gdacs(code: str, kind: str) -> list[dict]:
    payload = await fetch_json(GDACS_URL, params={'eventlist': code}, source='GDACS', timeout=TIMEOUT_S,
                               max_bytes=10_000_000)
    return gdacs_events(payload, kind)


def tornado_events(payload: dict, now_ms: int | None = None) -> list[dict]:
    """NWS « Tornado Warning » alerts → the centre of each alert polygon (enough to place the point)."""
    if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
        raise ToolError('Réponse inattendue du NWS.')
    now_ms = now_ms or _now_ms()
    events = []
    for feat in payload['features']:
        if not isinstance(feat, dict):
            continue
        rings = (feat.get('geometry') or {}).get('coordinates') or []
        ring = rings[0] if rings and isinstance(rings[0], list) else []
        points = [(_number(p[0]), _number(p[1])) for p in ring if isinstance(p, list) and len(p) >= 2]
        if not points or any(x is None or y is None for x, y in points):
            continue
        props = feat.get('properties') or {}
        events.append({'type': 'tornade', 'titre': str(props.get('areaDesc') or ''),
                       'detail': str(props.get('headline') or ''),
                       'lat': sum(y for _, y in points) / len(points), 'lon': sum(x for x, _ in points) / len(points),
                       'date': _ms(props.get('sent')) or now_ms, 'source': 'NWS (États-Unis)',
                       'infos': infos([('zone', props.get('areaDesc')), ('expire', props.get('expires'))])})
    return events


async def _tornadoes() -> list[dict]:
    payload = await fetch_json(NWS_URL, source='Le NWS', timeout=TIMEOUT_S, max_bytes=10_000_000,
                               headers={'Accept': 'application/geo+json'})
    return tornado_events(payload)


# Athéna's names and order (the interface shows them).
SOURCES = (
    ('USGS (séismes)', _quakes),
    ('NOAA (tsunamis)', _tsunamis),
    ('GDACS (volcans)', lambda: _gdacs('VO', 'volcan')),
    ('NWS (tornades, États-Unis)', _tornadoes),
    ('GDACS (cyclones)', lambda: _gdacs('TC', 'cyclone')),
)
ATTRIBUTIONS = {
    'USGS (séismes)': ('USGS — Earthquake Hazards Program (séismes M4,5+ sur 7 jours)', USGS_URL, 'api'),
    'NOAA (tsunamis)': ('NOAA — centres d’alerte aux tsunamis (PTWC, NTWC)', 'https://www.tsunami.gov/', 'rss'),
    'GDACS (volcans)': ('GDACS — Global Disaster Alert and Coordination System (ONU, Commission européenne)',
                        'https://www.gdacs.org/', 'api'),
    'NWS (tornades, États-Unis)': ('NWS — National Weather Service, alertes tornade (États-Unis)',
                                   'https://www.weather.gov/', 'api'),
}
ATTRIBUTIONS['GDACS (cyclones)'] = ATTRIBUTIONS['GDACS (volcans)']


async def _load() -> dict:
    """The five sources at once; one that fails is marked ok:false and the others go on."""
    results = await asyncio.gather(*(load() for _name, load in SOURCES), return_exceptions=True)
    events: list[dict] = []
    sources: list[dict] = []
    notes: list[str] = []
    for (name, _load_one), res in zip(SOURCES, results):
        if isinstance(res, BaseException):
            if isinstance(res, ToolError) and res.data.get('offline'):
                raise res
            if not isinstance(res, ToolError):
                log.warning('%s : erreur inattendue (%s)', name, type(res).__name__)
            sources.append({'nom': name, 'ok': False, 'nombre': 0})
            notes.append(f"{name} : {res.message_fr if isinstance(res, ToolError) else 'erreur de lecture'}")
            continue
        found, more = res if isinstance(res, tuple) else (res, [])
        events.extend(found)
        notes.extend(more)
        sources.append({'nom': name, 'ok': True, 'nombre': len(found)})
    if not any(s['ok'] for s in sources):
        raise ToolError("Aucune source d'événements naturels n'a répondu : " + ' ; '.join(notes) + '.')
    return {'data': {'evenements': events, 'sources': sources}, 'notes': notes}


async def active_events() -> tuple[dict, list[str], str, str | None]:
    """(data, notes on the sources that failed, retrieved_at, why_old). Kept 10 min; the previous result if every
    source fails."""
    value, retrieved, why_old = await cached_or_fallback(_cache, _fallback, 'events', _load)
    return value['data'], value['notes'], retrieved, why_old


def summarize(data: dict, notes: list[str]) -> str:
    count = {s['nom']: s for s in data['sources']}

    def said(name: str, one: str, many: str) -> str | None:
        source = count.get(name)
        if not source or not source['ok']:
            return None
        return f"{source['nombre']} {one if source['nombre'] <= 1 else many}"

    parts = [p for p in (
        said('USGS (séismes)', 'séisme de magnitude 4,5 ou plus en 7 jours', 'séismes de magnitude 4,5 ou plus en 7 jours'),
        said('NOAA (tsunamis)', 'bulletin de tsunami en 7 jours', 'bulletins de tsunami en 7 jours'),
        said('GDACS (volcans)', 'volcan en alerte orange ou rouge', 'volcans en alerte orange ou rouge'),
        said('GDACS (cyclones)', 'cyclone en alerte orange ou rouge', 'cyclones en alerte orange ou rouge'),
        said('NWS (tornades, États-Unis)', 'alerte tornade aux États-Unis', 'alertes tornade aux États-Unis'),
    ) if p]
    text = 'Événements naturels : ' + fr_list(parts) + '.'
    quakes_found = [e for e in data['evenements'] if e['type'] == 'seisme']
    magnitudes = [(float(i['valeur'].split(' ')[0]), e) for e in quakes_found for i in e['infos'] if i['cle'] == 'magnitude']
    if magnitudes:
        mag, strongest = max(magnitudes, key=lambda m: m[0])
        text += f" Le plus fort séisme : M {fr_num(mag, 1)} — {strongest['titre'] or 'lieu non précisé'}."
    if notes:
        text += f" Sans réponse : {' ; '.join(n.rstrip('.') for n in notes)}."
    return text


@tool('events.active', description='Événements naturels en cours pour le globe : séismes M4,5+ (USGS), tsunamis (NOAA), '
                                   'volcans et cyclones (GDACS), tornades aux États-Unis (NWS).',
      action='web.fetch', network=True, provenance='usgs+noaa+gdacs+nws', timeout_s=45)
async def events_tool(_p: Params) -> ToolResult:
    data, notes, retrieved, why_old = await active_events()
    sources, seen = [], set()
    for s in data['sources']:
        title, url, kind = ATTRIBUTIONS[s['nom']]
        if s['ok'] and title not in seen:
            seen.add(title)
            sources.append(Source(title=title, url=url, kind=kind, retrieved_at=retrieved))
    return ToolResult(ok=True, data=data, summary=summarize(data, notes) + fallback_note(why_old, retrieved),
                      provenance='usgs+noaa+gdacs+nws', sources=sources)
