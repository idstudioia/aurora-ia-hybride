"""Globe hologramme — la Terre (8/11/2026) : météo d'un point, grilles du vent et de l'air, événements naturels,
actualités du monde placées par pays, guide Wikipédia/Wikivoyage, routes /api/globe/* et mode Privé.

Aucun appel réseau : httpx.MockTransport, comme tests/test_tools.py. Les cas d'actualité reprennent ceux d'Athéna
(tests/actualites.test.ts), observés dans les vrais flux le 12/10/2026.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx

from aurora.tools import base, events, news, quakes, weather
from aurora.tools.base import ToolError, run_tool
from tests.test_api import ApiTestCase
from tests.test_tools import MockHttp

_SANDBOX: tempfile.TemporaryDirectory | None = None
_ENV = None


def setUpModule():
    """Settings reads (db.get_setting, user agent) never touch the user's real ~/.aurora."""
    global _SANDBOX, _ENV
    _SANDBOX = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    sandbox = Path(_SANDBOX.name)
    _ENV = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(sandbox / 'globe.db'), 'AURORA_HOME': str(sandbox / 'home'),
                                        'ASTRA_DB_PATH': str(sandbox / 'no_astra.db'), 'AURORA_OFFLINE': '0'})
    _ENV.start()


def tearDownModule():
    _ENV.stop()
    _SANDBOX.cleanup()


def run(coro):
    return asyncio.run(coro)


def answer(value) -> httpx.Response:
    """bytes/str: body; dict/list: JSON; int: that HTTP error; exception: raised (network failure)."""
    if isinstance(value, BaseException):
        raise value
    if isinstance(value, int):
        return httpx.Response(value, text='erreur')
    if isinstance(value, (dict, list)):
        return httpx.Response(200, json=value)
    return httpx.Response(200, content=value if isinstance(value, bytes) else value.encode('utf-8'))


def by_prefix(routes: dict):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, value in routes.items():
            if url.startswith(prefix):
                return answer(value)
        raise AssertionError(f'URL inattendue : {url}')
    return handle


def iso(delta: timedelta, naive: bool = False) -> str:
    moment = datetime.now(timezone.utc) + delta
    return moment.strftime('%Y-%m-%dT%H:%M:%S') + ('' if naive else 'Z')


DOCTYPE = b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x "y">]><feed><entry><title>&x;</title></entry></feed>'
DOWN = httpx.ConnectError('réseau coupé')


# =========================================================================== weather.point

class WeatherPointTests(unittest.TestCase):
    CURRENT = {'current': {'time': '2026-11-08T14:15', 'temperature_2m': 12.5, 'weather_code': 3,
                           'wind_speed_10m': 14.4}}

    def test_point_shape_rounding_and_short_cache(self):
        with MockHttp(lambda req: httpx.Response(200, json=self.CURRENT)) as http:
            r = run(run_tool('weather.point', {'lat': 48.8566, 'lon': 2.3522}))
            again = run(run_tool('weather.point', {'lat': 48.8571, 'lon': 2.3549}))   # same point at 0,01°
        self.assertTrue(r.ok, r.summary)
        # Math.round of JavaScript, as Athéna: 12,5 → 13 (Python's round would give 12)
        self.assertEqual(r.data, {'temperature': 13, 'code': 3, 'ciel': 'couvert', 'vent': 14})
        self.assertEqual(again.data, r.data)
        self.assertEqual(len(http.calls), 1)
        query = parse_qs(urlsplit(str(http.calls[0].url)).query)
        self.assertEqual((query['latitude'], query['longitude']), (['48.86'], ['2.35']))
        self.assertEqual(query['current'], ['temperature_2m,weather_code,wind_speed_10m'])
        self.assertIn('48,86° N, 2,35° E', r.summary)
        self.assertIn('CC BY 4.0', r.sources[0].title)

    def test_unknown_code_and_missing_values(self):
        payload = {'current': {'temperature_2m': -3.5, 'weather_code': 42}}
        with MockHttp(lambda req: httpx.Response(200, json=payload)):
            r = run(run_tool('weather.point', {'lat': -33.9, 'lon': 18.4}))
        self.assertEqual(r.data, {'temperature': -3, 'code': 42, 'ciel': 'conditions inconnues', 'vent': None})
        self.assertIn('33,90° S, 18,40° E', r.summary)

    def test_no_measure_is_an_error_and_bounds_are_checked(self):
        with MockHttp(lambda req: httpx.Response(200, json={'current': {}})):
            r = run(run_tool('weather.point', {'lat': 0, 'lon': 0}))
        self.assertFalse(r.ok)
        self.assertIn('Open-Meteo', r.error)
        with MockHttp(lambda req: (_ for _ in ()).throw(AssertionError('aucune requête'))):
            for bad in ({'lat': 91, 'lon': 0}, {'lat': 0, 'lon': -181}, {'lat': 'abc', 'lon': 0}, {'lon': 2}):
                self.assertIn('Paramètres invalides', run(run_tool('weather.point', bad)).error)


# =========================================================================== weather.grid / weather.air

def grid_payload(n: int = 612, overrides: dict[int, dict] | None = None) -> list[dict]:
    points = []
    for k in range(n):
        cur = {'time': '2026-11-08T14:15', 'wind_speed_10m': 5.0, 'wind_direction_10m': 180.0, 'precipitation': 0.0,
               'temperature_2m': 10.0 + k / 100, 'relative_humidity_2m': 50, 'european_aqi': 20 + k % 50,
               'pm2_5': 3.5}
        cur.update((overrides or {}).get(k, {}))
        points.append({'latitude': 0, 'longitude': 0, 'current': cur})
    return points


class GridTests(unittest.TestCase):
    def test_grid_order_is_athenas(self):
        lats, lons = weather.grid_points()
        self.assertEqual((len(lats), len(lons)), (612, 612))
        self.assertEqual((lats[0], lons[0]), (-80, -180))
        self.assertEqual((lats[1], lons[1]), (-80, -170))
        self.assertEqual((lats[36], lons[36]), (-70, -180))      # k = i*36 + j
        self.assertEqual((lats[611], lons[611]), (80, 170))
        self.assertTrue(all(weather.grid_position(k) == (lats[k], lons[k]) for k in range(612)))

    def test_one_request_wind_components_and_shape(self):
        overrides = {0: {'wind_speed_10m': 10.0, 'wind_direction_10m': 0.0},     # from the north: blows south
                     1: {'wind_speed_10m': 10.0, 'wind_direction_10m': 90.0},    # from the east: blows west
                     2: {'wind_speed_10m': None},                                # missing: null, never invented
                     3: {'precipitation': 1.2, 'relative_humidity_2m': 100}}
        with MockHttp(lambda req: httpx.Response(200, json=grid_payload(overrides=overrides))) as http:
            r = run(run_tool('weather.grid', {}))
            again = run(run_tool('weather.grid', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(len(http.calls), 1)                 # one request, then 30 min of cache
        self.assertEqual(again.data, r.data)
        url = str(http.calls[0].url)
        self.assertNotIn('%2C', url)                         # raw commas: the request line stays short
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(len(query['latitude'][0].split(',')), 612)
        self.assertEqual(query['wind_speed_unit'], ['ms'])
        self.assertIn('wind_direction_10m', query['current'][0])
        d = r.data
        self.assertEqual(set(d), {'mesureA', 'pas', 'lat0', 'lon0', 'nLat', 'nLon', 'u', 'v', 'pluie', 'temperature',
                                  'humidite'})
        self.assertEqual((d['pas'], d['lat0'], d['lon0'], d['nLat'], d['nLon']), (10, -80, -180, 17, 36))
        self.assertEqual(d['mesureA'], int(datetime(2026, 11, 8, 14, 15, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertEqual((d['u'][0], d['v'][0]), (0.0, -10.0))
        self.assertEqual((d['u'][1], d['v'][1]), (-10.0, 0.0))
        self.assertEqual((d['u'][2], d['v'][2]), (None, None))
        self.assertEqual((d['u'][5], d['v'][5]), (0.0, 5.0))    # from the south: blows north
        self.assertEqual(d['pluie'][3], 1.2)
        self.assertEqual(len(d['temperature']), 612)
        self.assertIn('612 points', r.summary)
        self.assertIn('Pluie sur 1 point(s)', r.summary)
        self.assertIn('1 point(s) sans mesure de vent', r.summary)
        self.assertIn('CC BY 4.0', r.sources[0].title)

    def test_incomplete_grid_is_refused(self):
        with MockHttp(lambda req: httpx.Response(200, json=grid_payload(611))):
            r = run(run_tool('weather.grid', {}))
        self.assertFalse(r.ok)
        self.assertIn('incomplète', r.error)

    def test_previous_grid_when_open_meteo_fails(self):
        state = {'down': False}

        def handle(req):
            return answer(DOWN if state['down'] else grid_payload())
        with MockHttp(handle) as http:
            first = run(run_tool('weather.grid', {}))
            weather._grid_cache.clear()          # 30 min later
            state['down'] = True
            old = run(run_tool('weather.grid', {}))
            self.assertEqual(len(http.calls), 2)
        self.assertTrue(old.ok)
        self.assertEqual(old.data, first.data)
        self.assertIn('résultat précédent', old.summary)
        with MockHttp(lambda req: answer(DOWN)):   # nothing before: an honest failure
            r = run(run_tool('weather.grid', {}))
        self.assertFalse(r.ok)
        self.assertIn('injoignable', r.error)

    def test_air_quality_and_copernicus_attribution(self):
        with MockHttp(lambda req: httpx.Response(200, json=grid_payload())) as http:
            r = run(run_tool('weather.air', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertTrue(str(http.calls[0].url).startswith(weather.AIR_URL + '?'))
        self.assertIn('current=european_aqi,pm2_5', str(http.calls[0].url))
        self.assertEqual(set(r.data), {'mesureA', 'pas', 'lat0', 'lon0', 'nLat', 'nLon', 'indice', 'pm25'})
        self.assertEqual((r.data['indice'][0], r.data['indice'][49], r.data['pm25'][0]), (20.0, 69.0, 3.5))
        self.assertIn('indice de 20 à 69', r.summary)
        titles = [s.title for s in r.sources]
        self.assertIn('Generated using Copernicus Atmosphere Monitoring Service information', titles)
        self.assertTrue(any('CC BY 4.0' in t for t in titles))


# =========================================================================== events.active

def usgs_week() -> dict:
    now = int(time.time() * 1000)
    return {'type': 'FeatureCollection', 'metadata': {'title': 'USGS Magnitude 4.5+ Earthquakes, Past Week'},
            'features': [
                {'id': 'us1', 'properties': {'mag': 6.84, 'magType': 'mww', 'place': '120 km SE of Hualien City, Taiwan',
                                             'time': now - 3_600_000, 'felt': 23, 'mmi': 5.123, 'alert': 'yellow',
                                             'status': 'reviewed', 'tsunami': 1, 'url': 'https://earthquake.usgs.gov/1'},
                 'geometry': {'type': 'Point', 'coordinates': [121.9, 23.5, 15.6]}},
                {'id': 'us2', 'properties': {'mag': 4.5, 'magType': 'mb', 'place': 'south of the Fiji Islands',
                                             'time': now - 7_200_000, 'felt': None, 'mmi': None, 'alert': None,
                                             'status': 'reviewed'},
                 'geometry': {'type': 'Point', 'coordinates': [178.1, -25.2, 550.4]}},
            ]}


def tsunami_atom() -> bytes:
    """Same structure as the real PTWC feed (read on 8/11/2026), without its contact block."""
    recent, old = iso(timedelta(hours=-1)), iso(timedelta(days=-8))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
<title>  TSUNAMI INFORMATION STATEMENT NUMBER 1  </title><updated>{recent}</updated>
<entry><title>  ABOUT 72 MILES NORTHWEST OF   PUERTO RICO  </title><updated>{recent}</updated>
<geo:lat>19.344</geo:lat><geo:long>-67.794</geo:long>
<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
<strong>Category:</strong> Information<br/><strong>Preliminary Magnitude: </strong>4.6(Ml)<br/>
<strong>Lat/Lon: </strong>19.344 / -67.794<br/></div></summary></entry>
<entry><title>OLD BULLETIN</title><updated>{old}</updated><geo:lat>10</geo:lat><geo:long>10</geo:long>
<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><strong>Category:</strong> Warning</div></summary></entry>
<entry><title>NO POSITION</title><updated>{recent}</updated></entry>
</feed>""".encode('utf-8')


def gdacs(kind: str) -> dict:
    def feat(name, level, current, todate, lon, lat, severity=''):
        return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {'name': name, 'alertlevel': level, 'country': 'Pays test', 'iscurrent': current,
                               'fromdate': iso(timedelta(days=-20), naive=True), 'todate': todate,
                               'severitydata': {'severitytext': severity}}}
    if kind == 'VO':
        return {'features': [
            feat('Eruption  Kanlaon', 'Orange', 'true', iso(timedelta(days=-10), naive=True), 123.132, 10.412),
            feat('Etna', 'Red', 'false', iso(timedelta(days=-1), naive=True), 15.0, 37.75),
            feat('Fini depuis longtemps', 'Red', 'false', iso(timedelta(days=-5), naive=True), 0, 0),
            feat('Alerte verte', 'Green', 'true', iso(timedelta(hours=-1), naive=True), 0, 0),
        ]}
    return {'features': [feat('Tropical Cyclone MURASU-26', 'Red', 'true', iso(timedelta(hours=-2), naive=True), 80.2,
                              15.8, 'Hurricane/Typhoon > 74 mph (maximum wind speed of 157 km/h)')]}


def nws() -> dict:
    ring = [[-97.0, 35.0], [-96.0, 35.0], [-96.0, 36.0], [-97.0, 36.0], [-97.0, 35.0]]
    return {'features': [
        {'geometry': {'type': 'Polygon', 'coordinates': [ring]},
         'properties': {'areaDesc': 'Cleveland, OK', 'headline': 'Tornado Warning issued', 'sent': iso(timedelta(minutes=-5)),
                        'expires': '2026-11-08T15:00:00-06:00'}},
        {'geometry': None, 'properties': {'areaDesc': 'Zone sans polygone'}},
    ]}


def event_routes(**changes) -> dict:
    routes = {
        events.USGS_URL: usgs_week(),
        events.TSUNAMI_URL.format(code='PHEB'): tsunami_atom(),
        events.TSUNAMI_URL.format(code='PAAQ'): tsunami_atom(),
        events.GDACS_URL + '?eventlist=VO': gdacs('VO'),
        events.GDACS_URL + '?eventlist=TC': gdacs('TC'),
        'https://api.weather.gov/alerts/active': nws(),
    }
    routes.update({key: value for key, value in changes.items()})
    return routes


class EventsTests(unittest.TestCase):
    def test_every_source_and_athenas_shape(self):
        with MockHttp(by_prefix(event_routes())) as http:
            r = run(run_tool('events.active', {}))
            run(run_tool('events.active', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(len(http.calls), 6)            # 10 min of cache
        self.assertEqual(set(r.data), {'evenements', 'sources'})
        self.assertEqual(r.data['sources'], [
            {'nom': 'USGS (séismes)', 'ok': True, 'nombre': 2}, {'nom': 'NOAA (tsunamis)', 'ok': True, 'nombre': 2},
            {'nom': 'GDACS (volcans)', 'ok': True, 'nombre': 2}, {'nom': 'NWS (tornades, États-Unis)', 'ok': True, 'nombre': 1},
            {'nom': 'GDACS (cyclones)', 'ok': True, 'nombre': 1}])
        for event in r.data['evenements']:
            self.assertEqual(set(event), {'type', 'titre', 'detail', 'lat', 'lon', 'date', 'source', 'infos'})
            self.assertIsInstance(event['date'], int)
        quake = next(e for e in r.data['evenements'] if e['titre'].startswith('120 km'))
        self.assertEqual((quake['type'], quake['lat'], quake['lon'], quake['source']), ('seisme', 23.5, 121.9, 'USGS'))
        self.assertEqual(quake['infos'], [{'cle': 'magnitude', 'valeur': '6.8 (mww)'}, {'cle': 'profondeur', 'valeur': '16 km'},
                                          {'cle': 'ressenti', 'valeur': '23'}, {'cle': 'intensite', 'valeur': '5.1'},
                                          {'cle': 'alerte', 'valeur': 'yellow'}, {'cle': 'statut', 'valeur': 'reviewed'}])
        small = next(e for e in r.data['evenements'] if e['titre'].startswith('south'))
        self.assertEqual([i['cle'] for i in small['infos']], ['magnitude', 'profondeur', 'statut'])   # empty: absent
        tsunami = next(e for e in r.data['evenements'] if e['type'] == 'tsunami')
        self.assertEqual(tsunami['titre'], 'ABOUT 72 MILES NORTHWEST OF PUERTO RICO')
        self.assertEqual(tsunami['infos'], [{'cle': 'categorie', 'valeur': 'Information'},
                                            {'cle': 'magnitude', 'valeur': '4.6(Ml)'}])
        self.assertEqual({e['source'] for e in r.data['evenements'] if e['type'] == 'tsunami'}, {'NOAA PTWC', 'NOAA NTWC'})
        volcanoes = [e for e in r.data['evenements'] if e['type'] == 'volcan']
        self.assertEqual([v['titre'] for v in volcanoes], ['Eruption Kanlaon', 'Etna'])
        self.assertEqual([i['cle'] for i in volcanoes[0]['infos']], ['alerte', 'pays', 'debut', 'fin'])
        self.assertTrue(volcanoes[0]['infos'][3]['valeur'].endswith('Z'))
        cyclone = next(e for e in r.data['evenements'] if e['type'] == 'cyclone')
        self.assertIn({'cle': 'force', 'valeur': 'Hurricane/Typhoon > 74 mph (maximum wind speed of 157 km/h)'},
                      cyclone['infos'])
        tornado = next(e for e in r.data['evenements'] if e['type'] == 'tornade')
        self.assertAlmostEqual(tornado['lat'], 35.4)
        self.assertAlmostEqual(tornado['lon'], -96.6)
        self.assertEqual((tornado['titre'], tornado['detail'], tornado['source']),
                         ('Cleveland, OK', 'Tornado Warning issued', 'NWS (États-Unis)'))
        self.assertIn('2 séismes de magnitude 4,5 ou plus', r.summary)
        self.assertIn('M 6,8', r.summary)
        self.assertEqual(len(r.sources), 4)              # USGS, NOAA, GDACS (once), NWS
        self.assertTrue(all(s.url.startswith('https://') for s in r.sources))

    def test_a_source_down_does_not_stop_the_others(self):
        routes = event_routes(**{'https://api.weather.gov/alerts/active': 503,
                                 events.TSUNAMI_URL.format(code='PAAQ'): DOCTYPE})
        with MockHttp(by_prefix(routes)):
            r = run(run_tool('events.active', {}))
        self.assertTrue(r.ok, r.summary)
        by_name = {s['nom']: s for s in r.data['sources']}
        self.assertEqual(by_name['NWS (tornades, États-Unis)'], {'nom': 'NWS (tornades, États-Unis)', 'ok': False, 'nombre': 0})
        self.assertEqual(by_name['NOAA (tsunamis)']['nombre'], 1)     # PTWC kept, NTWC's dangerous XML refused
        self.assertIn('DOCTYPE', r.summary)
        self.assertIn('503', r.summary)
        self.assertFalse(any(e['type'] == 'tornade' for e in r.data['evenements']))

    def test_all_down_fails_honestly_then_falls_back(self):
        down = {prefix: DOWN for prefix in event_routes()}
        with MockHttp(by_prefix(down)):
            r = run(run_tool('events.active', {}))
        self.assertFalse(r.ok)
        self.assertIn("Aucune source d'événements naturels", r.error)
        state = {'routes': event_routes()}
        with MockHttp(lambda req: by_prefix(state['routes'])(req)):
            first = run(run_tool('events.active', {}))
            events._cache.clear()                       # 10 min later, every source silent
            state['routes'] = down
            old = run(run_tool('events.active', {}))
        self.assertTrue(old.ok)
        self.assertEqual(old.data, first.data)
        self.assertIn('résultat précédent', old.summary)

    def test_dangerous_or_huge_xml_is_refused(self):
        with self.assertRaises(ToolError) as ctx:
            events.tsunami_events(DOCTYPE, 'NOAA PTWC')
        self.assertIn('DOCTYPE', ctx.exception.message_fr)
        with self.assertRaises(ToolError):
            events.tsunami_events(b'<feed>' + b' ' * (news.MAX_FEED_BYTES + 1) + b'</feed>', 'NOAA PTWC')
        with self.assertRaises(ToolError):
            events.tsunami_events(b'<feed><entry>', 'NOAA PTWC')

    def test_parse_geojson_keeps_its_output_without_details(self):
        payload = usgs_week()
        plain = quakes.parse_geojson(payload)['quakes'][0]
        self.assertEqual(set(plain), {'id', 'mag', 'place', 'time', 'lat', 'lon', 'depth_km', 'url', 'tsunami', 'alert'})
        detailed = quakes.parse_geojson(payload, details=True)['quakes'][0]
        self.assertEqual(set(detailed) - set(plain), {'time_ms', 'mag_type', 'felt', 'mmi', 'status'})


# =========================================================================== news.world (Athéna's cases)

PAYS = [
    {'n': 'Grèce', 'cap': 'Athènes', 'la': 38, 'lo': 23.7}, {'n': 'États-Unis', 'cap': 'Washington', 'la': 38.9, 'lo': -77},
    {'n': 'Russie', 'cap': 'Moscou', 'la': 55.7, 'lo': 37.6}, {'n': 'France', 'cap': 'Paris', 'la': 48.9, 'lo': 2.35},
    {'n': 'Guinée', 'cap': 'Conakry', 'la': 9.5, 'lo': -13.7}, {'n': 'Guinée-Bissau', 'cap': 'Bissau', 'la': 11.9, 'lo': -15.6},
    {'n': 'Maroc', 'cap': 'Rabat', 'la': 34, 'lo': -6.8}, {'n': 'Espagne', 'cap': 'Madrid', 'la': 40.4, 'lo': -3.7},
]
NS = 'xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/"'
FLUX = f"""<rss {NS}><channel>
<item><title>À Ceuta, la visite du roi d'Espagne</title><link>https://www.france24.com/fr/afrique/a</link><description> Le roi se rend à Ceuta. </description>
<media:thumbnail url="https://s.france24.com/a.jpg"/><source url="x">© Ludovic Marin, AFP</source><pubDate>Mon, 12 Oct 2026 15:50:25 GMT</pubDate></item>
<item><title><![CDATA[Côte d&#039;Ivoire : le foncier]]></title><link>https://fr.euronews.com/video/2026/10/12/b</link><description>Résumé</description>
<media:content url="https://images.euronews.com/b.jpg" type="image/jpeg" medium="image"/><pubDate>Mon, 12 Oct 2026 19:47:24 +0200</pubDate></item>
<item><title>À Ceuta, la visite du roi d'Espagne</title><link>https://www.rfi.fr/fr/autre-lien</link><description>Doublon</description><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>""".encode('utf-8')


class CountryTests(unittest.TestCase):
    def test_the_country_named_first_wins(self):
        self.assertEqual(news.country_of("En Grèce, l'ambassadrice américaine Kimberly Guilfoyle embarrasse jusqu'à "
                                         "Washington", PAYS), 'Grèce')

    def test_media_names_are_not_countries(self):
        self.assertEqual(news.country_of("L'OTAN montre les muscles face à la Russie — France 24", PAYS), 'Russie')
        self.assertIsNone(news.country_of('Le journal de France 24', PAYS))
        self.assertIsNone(news.country_of('Le journal d’Africanews', PAYS))
        self.assertIsNone(news.country_of('Selon Jeune Afrique et Le Monde Afrique, puis BBC Afrique', PAYS))

    def test_longest_name_at_the_same_place_and_capitals(self):
        self.assertEqual(news.country_of('Guinée-Bissau : vers un report de la présidentielle ?', PAYS), 'Guinée-Bissau')
        self.assertEqual(news.country_of('Moscou annonce une trêve', PAYS), 'Russie')

    def test_rdc_and_typographic_apostrophe(self):
        liste = PAYS + [{'n': 'République démocratique du Congo', 'cap': 'Kinshasa', 'la': -4.3, 'lo': 15.3},
                        {'n': "Côte d'Ivoire", 'cap': 'Yamoussoukro', 'la': 6.8, 'lo': -5.3},
                        {'n': 'Sénégal', 'cap': 'Dakar', 'la': 14.7, 'lo': -17.4}]
        self.assertEqual(news.country_of('RDC : Joseph Kabila prend officiellement la tête de la rébellion', liste),
                         'République démocratique du Congo')
        self.assertEqual(news.country_of('RD Congo : la rébellion avance', liste), 'République démocratique du Congo')
        self.assertEqual(news.country_of('Côte d’Ivoire, Sénégal : le canadien Fortuna Mining peut-il rééditer', liste),
                         "Côte d'Ivoire")
        self.assertIsNone(news.country_of('Le groupe Bordcomm annonce', liste))   # « RDC » inside a word: no

    def test_the_real_country_list(self):
        index = news.country_index()
        self.assertEqual(len({country for _w, country, _p in index.words}), 197)
        self.assertEqual(len(index.positions), 196)      # one country of the guide has no coordinates
        self.assertEqual(news.country_of('RDC : Joseph Kabila prend la tête de la rébellion'),
                         'République démocratique du Congo')
        self.assertEqual(news.country_of('Kinshasa sous la pluie'), 'République démocratique du Congo')
        self.assertEqual(news.country_of('Une visite à Washington'), 'États-Unis')


class WorldFeedTests(unittest.TestCase):
    def test_read_a_feed_image_credit_video_entities(self):
        a = news.parse_world_feed(FLUX, 'France 24')
        self.assertEqual(len(a), 3)
        self.assertEqual(a[0]['image'], 'https://s.france24.com/a.jpg')
        self.assertIn('AFP', a[0]['credit'])
        self.assertFalse(a[0]['video'])
        self.assertEqual(a[0]['resume'], 'Le roi se rend à Ceuta.')
        self.assertEqual(a[0]['date'], int(datetime(2026, 10, 12, 15, 50, 25, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertEqual((a[0]['source'], a[0]['theme']), ('France 24', 'actualite'))
        self.assertEqual(a[1]['titre'], "Côte d'Ivoire : le foncier")
        self.assertTrue(a[1]['video'])
        self.assertEqual(a[1]['image'], 'https://images.euronews.com/b.jpg')
        self.assertNotIn('image', a[2])
        self.assertNotIn('credit', a[1])

    def test_same_title_from_two_media_counts_once(self):
        articles, points = news.place_articles(news.parse_world_feed(FLUX, 'France 24'), PAYS)
        self.assertEqual(len([a for a in articles if a['titre'].startswith('À Ceuta')]), 1)
        self.assertEqual(next(p for p in points if p['pays'] == 'Espagne')['n'], 1)
        ceuta = next(a for a in articles if a['titre'].startswith('À Ceuta'))
        self.assertEqual((ceuta['pays'], ceuta['lat'], ceuta['lon']), ('Espagne', 40.4, -3.7))
        self.assertEqual(articles[0]['titre'], "Côte d'Ivoire : le foncier")     # newest first

    def test_the_precise_theme_wins(self):
        general = news.parse_world_feed(FLUX, 'France 24', 'actualite')
        sport = news.parse_world_feed(FLUX, 'RFI', 'sport')
        articles, _ = news.place_articles(general + sport, PAYS)
        self.assertEqual(next(a for a in articles if a['titre'].startswith('À Ceuta'))['theme'], 'sport')

    def test_http_links_become_https(self):
        xml = ('<item><title>Éthiopie : Adwa reprise</title><link>http://fr.africanews.com/2026/10/12/adwa/</link>'
               '<description>x</description><enclosure url="http://images.euronews.com/a.jpg" type="image/jpeg"/>'
               '<pubDate>Mon, 12 Oct 2026 17:03:20 +0000</pubDate></item>').encode('utf-8')
        [a] = news.parse_world_feed(xml, 'Africanews')
        self.assertEqual(a['lien'], 'https://fr.africanews.com/2026/10/12/adwa/')
        self.assertEqual(a['image'], 'https://images.euronews.com/a.jpg')

    def test_the_video_version_wins(self):
        texte = ('<item><title>Espagne : la fête nationale</title><link>https://fr.africanews.com/a/</link><description>x'
                 '</description><pubDate>Mon, 12 Oct 2026 19:47:24 +0200</pubDate></item>').encode('utf-8')
        video = ('<item><title>Espagne : la fête nationale</title><link>https://fr.euronews.com/video/2026/10/12/a</link>'
                 '<description>x</description><pubDate>Mon, 12 Oct 2026 19:47:24 +0200</pubDate></item>').encode('utf-8')
        articles, _ = news.place_articles(news.parse_world_feed(texte, 'Africanews')
                                          + news.parse_world_feed(video, 'Euronews'), PAYS)
        self.assertEqual(len(articles), 1)
        self.assertTrue(articles[0]['video'])
        self.assertEqual((articles[0]['source'], articles[0]['lien']), ('Euronews', 'https://fr.euronews.com/video/2026/10/12/a'))

    def test_jeune_afrique_cdata_with_spaces(self):
        xml = ('<item> <title> <![CDATA[RDC : Joseph Kabila prend la tête de la rébellion]]> </title> <link> '
               '<![CDATA[https://www.jeuneafrique.com/1851360/politique/rdc/]]> </link> <pubDate> '
               '<![CDATA[2026-10-12T20:57:38+00:00]]> </pubDate> <description> <![CDATA[L’ancien président congolais a '
               'annoncé un mouvement.]]> </description> </item>').encode('utf-8')
        [a] = news.parse_world_feed(xml, 'Jeune Afrique')
        self.assertEqual(a['lien'], 'https://www.jeuneafrique.com/1851360/politique/rdc/')
        self.assertEqual(a['date'], int(datetime(2026, 10, 12, 20, 57, 38, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertNotIn('image', a)

    def test_image_in_the_body(self):
        xml = (f'<rss {NS}><channel><item><title>Vous pouvez préparer votre précommande</title>'
               '<link>https://www.numerama.com/tech/1/</link><description>x</description><content:encoded><![CDATA['
               '<img width="1200" height="675" src="https://img.numerama.com/a.png?resize=1200,675" class="attachment-full" />'
               ']]></content:encoded><pubDate>Mon, 12 Oct 2026 18:00:00 +0000</pubDate></item></channel></rss>').encode()
        self.assertEqual(news.parse_world_feed(xml, 'Numerama')[0]['image'], 'https://img.numerama.com/a.png?resize=1200,675')

    def test_unsafe_feeds_and_links_are_refused(self):
        with self.assertRaises(ToolError) as ctx:
            news.parse_world_feed(DOCTYPE, 'Piège')
        self.assertIn('DOCTYPE', ctx.exception.message_fr)
        xml = b'<item><title>Lien dangereux</title><link>javascript:alert(1)</link></item>'
        self.assertEqual(news.parse_world_feed(xml, 'Piège'), [])


def world_feed_xml(index: int, feed: news.WorldFeed) -> bytes:
    """Two articles per feed: one about France, one about Ukraine every other feed; distinct links and titles."""
    hour = timedelta(hours=-(index + 1))
    items = [f'<item><title>France : nouvelle n° {index}</title><link>https://exemple.org/{index}/a</link>'
             f'<description>Texte {index}</description><pubDate>{iso(hour)}</pubDate></item>']
    if index % 2 == 0:
        items.append(f'<item><title>Kiev : annonce n° {index}</title><link>https://exemple.org/{index}/b</link>'
                     f'<description>Ukraine</description><pubDate>{iso(hour - timedelta(minutes=1))}</pubDate></item>')
    return f'<rss><channel>{"".join(items)}</channel></rss>'.encode('utf-8')


def world_routes(failing: dict[str, object] | None = None) -> dict[str, object]:
    failing = failing or {}
    return {str(httpx.URL(f.url)): failing.get(f.name, world_feed_xml(i, f)) for i, f in enumerate(news.WORLD_FEEDS)}


def exact(routes: dict[str, object]):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in routes:
            raise AssertionError(f'URL inattendue : {url}')
        return answer(routes[url])
    return handle


class WorldNewsTests(unittest.TestCase):
    def test_world_feeds_are_athenas_27(self):
        self.assertEqual(len(news.WORLD_FEEDS), 27)
        self.assertEqual(len({f.url for f in news.WORLD_FEEDS}), 27)
        self.assertTrue(all(f.url.startswith('https://') for f in news.WORLD_FEEDS))
        self.assertEqual({f.theme for f in news.WORLD_FEEDS},
                         {'actualite', 'economie', 'sport', 'culture', 'science', 'technologie', 'voyage'})
        self.assertTrue(all(f.source in news.MEDIA_HOMES for f in news.WORLD_FEEDS))
        science_urls = {f.url for f in news.FEEDS}
        self.assertFalse(science_urls & {f.url for f in news.WORLD_FEEDS})   # the Accueil's 5 feeds stay apart

    def test_world_news_shape_sources_points_and_summary(self):
        routes = world_routes({'France 24 — Europe': 503, 'Jeune Afrique': DOCTYPE})
        with MockHttp(exact(routes)) as http:
            r = run(run_tool('news.world', {}))
            run(run_tool('news.world', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(len(http.calls), 27)            # in parallel, then 15 min of cache
        d = r.data
        self.assertEqual(set(d), {'mesureA', 'articles', 'points', 'sources'})
        self.assertEqual(d['sources'][:3], [{'source': 'France 24', 'ok': True}, {'source': 'RFI', 'ok': True},
                                            {'source': 'Euronews', 'ok': True}])
        self.assertIn({'source': 'Jeune Afrique', 'ok': False}, d['sources'])
        self.assertEqual(len(d['sources']), len(news.MEDIA_HOMES))
        self.assertEqual(len(d['articles']), 25 + 13)    # 25 feeds answered, 13 of them even-numbered (Ukraine)
        dates = [a['date'] for a in d['articles']]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(d['points'][0]['pays'], 'France')
        self.assertEqual(d['points'][0]['n'], 25)
        self.assertEqual(d['points'][1]['pays'], 'Ukraine')            # placed by its capital, Kiev
        france = next(a for a in d['articles'] if a.get('pays') == 'France')
        self.assertEqual(set(france), {'titre', 'lien', 'resume', 'date', 'source', 'theme', 'video', 'pays', 'lat', 'lon'})
        self.assertIn('25 flux sur 27', r.summary)
        self.assertIn('France (25)', r.summary)
        self.assertIn('France 24 — Europe', r.summary)
        self.assertIn('Jeune Afrique', r.summary)
        self.assertNotIn('Jeune Afrique (flux RSS)', [s.title for s in r.sources])
        self.assertIn('France 24 (flux RSS)', [s.title for s in r.sources])

    def test_at_most_1200_articles_newest_first(self):
        with mock.patch.object(news, 'WORLD_MAX_ARTICLES', 3), MockHttp(exact(world_routes())):
            r = run(run_tool('news.world', {}))
        self.assertEqual(len(r.data['articles']), 3)
        self.assertEqual(r.data['points'][0]['n'], 27)   # points count every article, as Athéna
        self.assertEqual(news.WORLD_MAX_ARTICLES, 1200)

    def test_a_request_from_the_chat_gets_its_country_only(self):
        # 8/11/2026: « les actualités en Ukraine » (planner -> news.world {pays, limit}); the globe route sends none.
        with MockHttp(exact(world_routes())):
            r = run(run_tool('news.world', {'pays': 'Ukraine', 'limit': 5}))
            unknown = run(run_tool('news.world', {'pays': 'Atlantide', 'theme': 'astrologie', 'limit': 3}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(r.data['demande'], {'pays': 'Ukraine'})
        self.assertEqual(len(r.data['articles']), 5)
        self.assertTrue(all(a['pays'] == 'Ukraine' for a in r.data['articles']))
        self.assertEqual([p['pays'] for p in r.data['points']], ['Ukraine'])
        self.assertIn('sur « Ukraine » : 14 article(s)', r.summary)   # 27 feeds, 14 even-numbered
        self.assertEqual(unknown.data['demande'], {})             # unknown country or theme: not used as a filter
        self.assertEqual(len(unknown.data['articles']), 3)

    def test_all_feeds_down_then_previous_result(self):
        down = {str(httpx.URL(f.url)): DOWN for f in news.WORLD_FEEDS}
        with MockHttp(exact(down)):
            r = run(run_tool('news.world', {}))
        self.assertFalse(r.ok)
        self.assertIn("Aucun des 27 flux", r.error)
        state = {'routes': world_routes()}
        with MockHttp(lambda req: exact(state['routes'])(req)):
            first = run(run_tool('news.world', {}))
            news._world_cache.clear()
            state['routes'] = down
            old = run(run_tool('news.world', {}))
        self.assertTrue(old.ok)
        self.assertEqual(old.data, first.data)
        self.assertIn('résultat précédent', old.summary)


# =========================================================================== news.article (« Lis-le-moi », « Explique-le »)

ARTICLE_LINK = 'https://journal.exemple/monde/une-enquete'
ARTICLE = {'titre': 'Une enquête met au jour un réseau de faux sites', 'lien': ARTICLE_LINK, 'source': 'Global Voices',
           'resume': 'Résumé du journal.', 'date': 1, 'theme': 'actualite', 'video': False}
ARTICLE_PARAGRAPHS = [
    'En septembre 2026, une enquête a mis en lumière un réseau de faux sites d’information ciblant la République '
    'démocratique du Congo, selon plusieurs organisations de vérification des faits.',
    'Les pages imitaient la mise en forme de médias connus et reprenaient leurs logos, avant de publier des textes '
    'inventés sur la situation dans l’est du pays.',
]
ARTICLE_PAGE = ('<nav><a href="/">Accueil</a></nav>' + ''.join(f'<p>{t}</p>' for t in ARTICLE_PARAGRAPHS)
                + '<p class="wp-caption-text">Image de la une des médias de la RDC, réalisée avec des captures.</p>')


class ArticleTests(unittest.TestCase):
    """11/11/2026 (ID Studio IA : « regarde Athéna la version payante, on a un système de lecture des articles plus abouti »):
    the text of an article of the globe read on the newspaper's page, or its explanation by the LOCAL model (the
    double of the other tests, doubles_core.FakeOllama)."""

    def setUp(self):
        from aurora.tools import webpage
        self.webpage = webpage
        news._explanations.clear()
        self.read = mock.AsyncMock(return_value=webpage.Page(ARTICLE_LINK, ARTICLE_PAGE, 'text/html'))
        world = mock.AsyncMock(return_value=({'articles': [ARTICLE, {**ARTICLE, 'lien': ARTICLE_LINK + '-video',
                                                                 'video': True}]}, [], 'x', None))
        for patch in (mock.patch.object(news, 'world_news', world), mock.patch.object(webpage, 'read_public', self.read)):
            patch.start()
            self.addCleanup(patch.stop)

    def article(self, mode: str = 'lire', link: str = ARTICLE_LINK, model=None):
        from tests.doubles_core import FakeOllama
        with mock.patch('aurora.providers.ollama.ollama', model or FakeOllama()):
            return run(run_tool('news.article', {'lien': link, 'mode': mode}))

    def test_lis_le_moi_gives_the_text_of_the_newspapers_page(self):
        r = self.article()
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(r.data, {'titre': ARTICLE['titre'], 'source': 'Global Voices', 'lien': ARTICLE_LINK,
                                  'mode': 'lire', 'texte': '\n'.join(ARTICLE_PARAGRAPHS), 'partiel': False})
        self.read.assert_awaited_once_with(ARTICLE_LINK)
        self.assertEqual([(s.url, s.kind) for s in r.sources], [(ARTICLE_LINK, 'web')])
        self.assertIn('lu sur la page de Global Voices', r.summary)
        # « réservé aux abonnés » : the beginning only, and it is said.
        self.read.return_value = self.webpage.Page(
            ARTICLE_LINK, ARTICLE_PAGE + '<p class="fig-premium-paywall__infos">Cet article est réservé aux abonnés.</p>',
            'text/html')
        cut = self.article()
        self.assertTrue(cut.data['partiel'])
        self.assertIn('seul le début', cut.summary)

    def test_a_link_from_elsewhere_and_a_video_are_never_fetched(self):
        for link in ('https://ailleurs.exemple/une-page', ARTICLE_LINK + '-video'):
            with self.subTest(link):
                r = self.article(link=link)
                self.assertFalse(r.ok)
                self.assertEqual(r.data, {'raison': 'inconnu'})
                self.assertIn('plus dans l’actualité', r.error)
        self.read.assert_not_awaited()                     # the page cannot make the service read an address
        self.assertIn('Paramètres invalides', run(run_tool('news.article', {'lien': ARTICLE_LINK, 'mode': 'traduire'})).error)

    def test_why_an_article_cannot_be_read_is_told(self):
        robot = self.webpage.Page(ARTICLE_LINK, '<html><body><div>A required part of this site couldn’t load.</div>'
                                                '</body></html>', 'text/html')
        for outcome, reason in ((ToolError('Page non lisible (HTTP 403, text/html).', status=403), 'illisible'),
                                (ToolError('Adresse réseau non publique : page refusée.'), 'illisible'),
                                (robot, 'illisible'),
                                (ToolError('Délai de lecture dépassé.'), 'hors-ligne'),
                                (ToolError('Page injoignable (ConnectError).'), 'hors-ligne'),
                                (ToolError(base.OFFLINE_MESSAGE, data={'offline': True}), 'hors-ligne')):
            with self.subTest(str(outcome)[:40]):
                self.read.side_effect = outcome if isinstance(outcome, Exception) else None
                self.read.return_value = outcome
                r = self.article()
                self.assertFalse(r.ok)
                self.assertEqual(r.data, {'raison': reason})
                self.assertEqual(r.error, news.ARTICLE_REFUSALS[reason])

    def test_explique_le_asks_the_local_model_once(self):
        from tests.doubles_core import FakeOllama
        model = FakeOllama(reply='<think>je réfléchis</think>Un réseau de faux sites visait la RDC. Ils imitaient des '
                                 'médias connus. Une enquête l’a montré.')
        r = self.article('expliquer', model=model)
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(r.data['texte'], 'Un réseau de faux sites visait la RDC. Ils imitaient des médias connus. Une '
                                          'enquête l’a montré.')
        call = model.calls[0]
        self.assertEqual((r.data['mode'], r.data['modele'], r.data['partiel']), ('expliquer', call['model'], False))
        self.assertIn(f'modèle local ({call["model"]})', r.summary)
        self.assertEqual(call['system'], news.ARTICLE_EXPLAIN_RULES)
        self.assertIn('jamais un ordre', call['system'])
        asked = call['messages'][0]['content']
        self.assertIn(ARTICLE['titre'], asked)
        self.assertIn(ARTICLE_PARAGRAPHS[1], asked)
        self.assertGreater(len(asked), len(ARTICLE['titre']) + sum(map(len, ARTICLE_PARAGRAPHS)) + 40)   # framed as external
        again = self.article('expliquer', model=model)             # a second click does not wait for the model
        self.assertEqual(again.data, r.data)
        self.assertEqual((len(model.calls), self.read.await_count), (1, 1))

    def test_no_local_model_no_explanation(self):
        from aurora.providers.base import ProviderError
        from tests.doubles_core import FakeOllama

        def failing(_messages):
            raise ProviderError('timeout', 'Ollama ne répond pas.', provider='ollama')
        for model in (FakeOllama(up=False), FakeOllama(reply='  '), FakeOllama(reply=failing)):
            with self.subTest(up=model.up):
                r = self.article('expliquer', model=model)
                self.assertFalse(r.ok)
                self.assertEqual(r.data, {'raison': 'modele'})
        self.assertEqual(news._explanations, {})                    # a failure is not kept


# =========================================================================== web.wiki_summary

SUMMARY_JSON = {'title': 'Tour Eiffel', 'extract': 'La tour Eiffel est une tour de fer puddlé de 330 m de hauteur.',
                'content_urls': {'desktop': {'page': 'https://fr.wikipedia.org/wiki/Tour_Eiffel'}},
                'thumbnail': {'source': 'https://upload.wikimedia.org/tour.jpg'}}


class WikiSummaryTests(unittest.TestCase):
    def test_summary_shape_and_day_cache(self):
        with MockHttp(lambda req: httpx.Response(200, json=SUMMARY_JSON)) as http:
            r = run(run_tool('web.wiki_summary', {'titre': 'Tour Eiffel'}))
            run(run_tool('web.wiki_summary', {'titre': 'Tour Eiffel'}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(r.data, {'titre': 'Tour Eiffel', 'texte': SUMMARY_JSON['extract'],
                                  'lien': 'https://fr.wikipedia.org/wiki/Tour_Eiffel',
                                  'image': 'https://upload.wikimedia.org/tour.jpg'})
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(str(http.calls[0].url), 'https://fr.wikipedia.org/api/rest_v1/page/summary/Tour%20Eiffel')
        self.assertIn('Tour Eiffel', r.summary)
        self.assertEqual(r.sources[0].url, 'https://fr.wikipedia.org/wiki/Tour_Eiffel')

    def test_wikivoyage_and_encoded_title(self):
        with MockHttp(lambda req: httpx.Response(200, json={'title': 'AC/DC', 'extract': 'Groupe.'})) as http:
            r = run(run_tool('web.wiki_summary', {'projet': 'wikivoyage', 'titre': 'AC/DC'}))
        self.assertEqual(str(http.calls[0].url), 'https://fr.wikivoyage.org/api/rest_v1/page/summary/AC%2FDC')
        self.assertEqual(r.data['lien'], 'https://fr.wikivoyage.org/wiki/AC%2FDC')
        self.assertNotIn('image', r.data)

    def test_not_found_refused_and_offline_are_told_apart(self):
        for value, reason in ((404, 'introuvable'), (403, 'refus'), (DOWN, 'hors-ligne'), (500, 'hors-ligne'),
                              ({'title': 'Vide'}, 'introuvable')):
            with self.subTest(reason=reason, value=value), MockHttp(lambda req, v=value: answer(v)):
                r = run(run_tool('web.wiki_summary', {'titre': 'Lieu'}))
                self.assertFalse(r.ok)
                self.assertEqual(r.data.get('raison'), reason)
                self.assertTrue(r.error)
        with MockHttp(lambda req: httpx.Response(404)) as http:   # a failure is not kept
            run(run_tool('web.wiki_summary', {'titre': 'Lieu'}))
            run(run_tool('web.wiki_summary', {'titre': 'Lieu'}))
        self.assertEqual(len(http.calls), 2)

    def test_parameters_are_checked(self):
        with MockHttp(lambda req: (_ for _ in ()).throw(AssertionError('aucune requête'))):
            for bad in ({'titre': 'x' * 201}, {'titre': ''}, {'titre': 'Paris', 'projet': 'wiktionary'}):
                self.assertIn('Paramètres invalides', run(run_tool('web.wiki_summary', bad)).error)


# =========================================================================== routes, private mode

GLOBE_HOSTS = {'api.open-meteo.com', 'air-quality-api.open-meteo.com', 'earthquake.usgs.gov', 'www.tsunami.gov',
               'www.gdacs.org', 'api.weather.gov', 'fr.wikipedia.org', 'fr.wikivoyage.org'} | {
    httpx.URL(f.url).host for f in news.WORLD_FEEDS}
TERRE_ROUTES = ('/api/globe/meteo?lat=48.85&lon=2.35', '/api/globe/evenements', '/api/globe/vent', '/api/globe/air',
                '/api/globe/actualites', '/api/globe/guide?projet=wikivoyage&titre=France')


class GlobeRouteTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        base.clear_caches()
        self.calls: list[httpx.Request] = []

    def tearDown(self):
        base.set_http_transport(None)
        base.clear_caches()
        super().tearDown()

    def record(self, handler):
        def handle(request):
            self.calls.append(request)
            return handler(request)
        base.set_http_transport(httpx.MockTransport(handle))

    def test_private_mode_blocks_every_terre_route_without_any_request(self):
        self.client.patch('/api/settings', json={'mode': 'prive'})
        self.record(lambda req: httpx.Response(404))
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}):
            for path in TERRE_ROUTES:
                with self.subTest(path=path):
                    res = self.client.get(path)
                    self.assertEqual(res.status_code, 200)
                    body = res.json()
                    self.assertFalse(body['ok'])
                    self.assertTrue(body['data']['private_mode'])
                    self.assertIn('Mode privé', body['summary'])
        self.assertEqual([c for c in self.calls if c.url.host in GLOBE_HOSTS], [])
        # 11/11/2026: « Lis-le-moi » reads a newspaper's page: never in Private mode either.
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}):
            body = self.client.post('/api/globe/article', json={'lien': ARTICLE_LINK, 'mode': 'lire'}).json()
        self.assertTrue(body['data']['private_mode'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.client.post('/api/globe/article', json={'lien': ARTICLE_LINK, 'mode': 'traduire'}).status_code, 422)
        self.assertEqual(self.client.post('/api/globe/article', json={'lien': ARTICLE_LINK},
                                          headers={'X-Aurora-Token': ''}).status_code, 401)

    def test_parameters_are_validated_and_the_token_required(self):
        for path in ('/api/globe/meteo?lat=95&lon=0', '/api/globe/meteo?lat=0&lon=181', '/api/globe/meteo?lat=abc&lon=0',
                     '/api/globe/meteo?lat=nan&lon=0', '/api/globe/meteo', '/api/globe/guide',
                     '/api/globe/guide?titre=Paris&projet=wiktionary', '/api/globe/guide?titre=' + 'x' * 201):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422)
        for path in TERRE_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers={'X-Aurora-Token': ''}).status_code, 401)

    def test_meteo_route_end_to_end(self):
        self.record(lambda req: httpx.Response(200, json=WeatherPointTests.CURRENT))
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}):
            body = self.client.get('/api/globe/meteo', params={'lat': 48.8566, 'lon': 2.3522}).json()
        self.assertTrue(body['ok'], body)
        self.assertEqual(body['data'], {'temperature': 13, 'code': 3, 'ciel': 'couvert', 'vent': 14})
        self.assertEqual(body['provenance'], 'open-meteo')
        self.assertTrue(body['sources'])
        self.assertEqual([c.url.host for c in self.calls if c.url.host in GLOBE_HOSTS], ['api.open-meteo.com'])

    def test_offline_is_said(self):
        body = self.client.get('/api/globe/evenements').json()     # TempEnv: AURORA_OFFLINE=1
        self.assertFalse(body['ok'])
        self.assertTrue(body['data'].get('offline'))


# =========================================================================== non-regression of the Accueil

SCIENCE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Test</title>
<item><title>Une comete observee</title><link>https://exemple.test/comete</link>
<pubDate>Tue, 27 Oct 2026 08:00:00 +0100</pubDate><description>Un telescope a vu la comete.</description>
<media:content url="https://exemple.test/img.jpg" medium="image"/></item></channel></rss>"""


class AccueilNonRegressionTests(unittest.TestCase):
    def test_news_latest_reads_only_its_five_feeds_with_the_same_items(self):
        with MockHttp(lambda req: httpx.Response(200, content=SCIENCE_RSS)) as http:
            r = run(run_tool('news.latest', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual({str(c.url) for c in http.calls}, {f.url for f in news.FEEDS})
        self.assertEqual(len(r.data['feeds']), 5)
        self.assertEqual(set(r.data), {'topic', 'items', 'matched', 'total', 'feeds', 'external'})
        self.assertEqual(set(r.data['items'][0]), {'id', 'title', 'link', 'published', 'summary', 'image', 'feed',
                                                   'feed_title', 'lang'})

    def test_quakes_recent_unchanged(self):
        with MockHttp(lambda req: httpx.Response(200, json=usgs_week())) as http:
            r = run(run_tool('quakes.recent', {}))
        self.assertTrue(r.ok, r.summary)
        self.assertEqual(str(http.calls[0].url), quakes.FEED_URL)
        self.assertEqual(set(r.data['quakes'][0]), {'id', 'mag', 'place', 'time', 'lat', 'lon', 'depth_km', 'url',
                                                    'tsunami', 'alert'})
        self.assertEqual((r.data['window_hours'], r.data['min_magnitude']), (24, 2.5))

    def test_weather_current_source_unchanged(self):
        self.assertEqual(weather._source('2026-11-08T00:00:00Z')[0].title, 'Open-Meteo — prévisions et observations')

    def test_the_new_tools_are_registered_as_network_reads(self):
        for name in ('weather.point', 'weather.grid', 'weather.air', 'events.active', 'news.world', 'web.wiki_summary'):
            spec = base.REGISTRY[name]
            self.assertEqual((spec.action, spec.network), ('web.fetch', True), name)
        self.assertEqual(base.LOAD_ERRORS, {})


if __name__ == '__main__':
    unittest.main()
