"""Tests du ciel du globe hologramme : tools/sky.py et les routes /api/globe/avions, monde, satellites, detail, nasa.

Aucun appel réseau réel : httpx.MockTransport, comme tests/test_tools.py. Base et dossier AURORA temporaires.
Lancer : cd backend && <python du venv> -m unittest tests.test_sky -v
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx

from aurora.tools import base, sky
from aurora.tools.base import run_tool
from tests.test_api import NO_TOKEN, ApiTestCase
from tests.test_tools import MockHttp, no_network

# ISS orbit published on 9 December 2019 (example of the python-sgp4 documentation), and a fixed date 1 h 20 later.
ISS = ('ISS (ZARYA)',
       '1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991',
       '2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482')
ISS_EPOCH_MS = round((2458826.5 + 0.69339541 - 2440587.5) * 86_400_000)
WHEN = datetime(2019, 12, 9, 18, 0, tzinfo=timezone.utc)
JPEG = b'\xff\xd8\xff\xe0' + bytes(range(256)) * 200 + b'\xff\xd9'   # 51 206 bytes: dense like a real image
EMPTY_JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 12_000 + b'\xff\xd9'    # a day without data: black, ~12 KB
PLANE_KEYS = {'id', 'indicatif', 'pays', 'type', 'categorie', 'lat', 'lon', 'altitude', 'vitesse', 'cap', 'mesure'}

_SANDBOX: tempfile.TemporaryDirectory | None = None
_ENV = None


def setUpModule():
    """Settings and the orbit copy go to a temporary AURORA_HOME, never to ~/.aurora."""
    global _SANDBOX, _ENV
    _SANDBOX = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    sandbox = Path(_SANDBOX.name)
    _ENV = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(sandbox / 'sky_test.db'),
                                        'AURORA_HOME': str(sandbox / 'home'),
                                        'ASTRA_DB_PATH': str(sandbox / 'no_astra.db'), 'AURORA_OFFLINE': '0'})
    _ENV.start()
    from aurora import db
    db.initialize()


def tearDownModule():
    _ENV.stop()
    _SANDBOX.cleanup()


def run(coro):
    return asyncio.run(coro)


def adsb_payload(n: int) -> dict:
    """n planes north of 44.4 / 4.8, the farthest first (the tool must sort them)."""
    ac = [{'hex': f'a{i:05x}', 'flight': f'AFR{i:03d} ', 'r': f'F-G{i:03d}', 't': 'A320', 'category': 'A3',
           'lat': round(44.4 + 0.05 * (n - i), 3), 'lon': 4.8, 'alt_baro': 35000, 'alt_geom': 35500, 'gs': 450.0,
           'track': 90.0, 'seen_pos': 1.5} for i in range(n)]
    return {'now': 1_794_132_856_001, 'ac': ac, 'total': n}


def opensky_state(icao: str, lat: float, lon: float, *, ground=False, speed=230.0, track=45.0, geo=10100.0,
                  category=None) -> list:
    row = [icao, 'DLH9U  ', 'Germany', 1_700_000_000, 1_700_000_001, lon, lat, 10000.0, ground, speed, track, 0,
           None, geo, None, False, 0]
    if category is not None:
        row.append(category)
    return row


def opensky_on():
    """The person has turned OpenSky Network on in Paramètres. It is off by default since 24/11/2026: its terms
    keep its API for non-profit research and teaching, so each person turns it on herself."""
    return mock.patch.object(sky, 'opensky_enabled', return_value=True)


def tle_handler(request: httpx.Request) -> httpx.Response:
    """CelesTrak knows only the ISS; TLE API knows nothing."""
    if request.url.host == 'celestrak.org':
        if request.url.params.get('CATNR') == '25544':
            return httpx.Response(200, text='\r\n'.join(ISS) + '\r\n', headers={'content-type': 'text/plain'})
        return httpx.Response(200, text='No GP data found', headers={'content-type': 'text/plain'})
    if request.url.host == 'tle.ivanstanojevic.me':
        return httpx.Response(404, json={'message': 'introuvable'})
    raise AssertionError(f'requête inattendue : {request.url}')


def gibs_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.host == 'gibs.earthdata.nasa.gov', request.url
    return httpx.Response(200, content=JPEG, headers={'content-type': 'image/jpeg'})


def great_circle_deg(a: list, b: list) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(h)))


# =========================================================================== reading the sources

class ReadingTests(unittest.TestCase):
    def test_adsb_conversions_ground_and_categories(self):
        payload = {'now': 1_000_000, 'ac': [
            {'hex': 'abc', 'flight': 'AFR123  ', 'r': 'F-HPNS', 't': 'BCS3', 'category': 'A3', 'lat': 44.0,
             'lon': 5.0, 'alt_baro': 35000, 'alt_geom': 36000, 'gs': 450, 'track': 271.6, 'seen_pos': 2.0},
            {'hex': 'sol', 'lat': 44.8, 'lon': -0.7, 'alt_baro': 'ground', 'gs': 0.0},
            {'hex': 'sans-position', 'alt_baro': 1000},
            {'hex': 'baro', 'lat': 45, 'lon': 5, 'alt_baro': 1000, 'alt_geom': None, 'category': 'A1'},
            {'hex': 'helico', 'lat': 45, 'lon': 5, 'category': 'A7', 'gs': True},   # a bool is not a speed
            'pas un avion',
        ]}
        measured, planes = sky.parse_adsb(payload)
        self.assertEqual(measured, 1_000_000)
        self.assertEqual([p['id'] for p in planes], ['abc', 'baro', 'helico'])
        self.assertEqual(planes[0], {'id': 'abc', 'indicatif': 'AFR123', 'pays': 'F-HPNS', 'type': 'BCS3',
                                     'categorie': 'ligne', 'lat': 44.0, 'lon': 5.0,
                                     'altitude': 10973,     # 36 000 ft (geometric first) × 0,3048
                                     'vitesse': 833,        # 450 kn × 1,852
                                     'cap': 272, 'mesure': 998_000})
        self.assertEqual((planes[1]['altitude'], planes[1]['categorie'], planes[1]['vitesse']), (305, 'leger', None))
        self.assertEqual(planes[1]['mesure'], 1_000_000)
        self.assertEqual((planes[2]['altitude'], planes[2]['vitesse'], planes[2]['categorie']), (None, None, 'leger'))
        for bad in ({'now': 1}, [], 'texte', {'ac': 'x'}):
            with self.assertRaises(base.ToolError):
                sky.parse_adsb(bad)

    def test_opensky_states(self):
        payload = {'time': 1_700_000_005, 'states': [
            opensky_state('3c6444', 50.0, 8.5, category=7),
            opensky_state('sol', 48.0, 2.0, ground=True),
            ['sans-position', 'Y', 'France', None, None, None, None, None, False, None, None],
            ['court'],
            ['planeur', '', 'France', None, 1, 5.0, 45.0, 1500.0, False, None, None, 0, None, None, None, False,
             0, 9],
            ['simple', 'AFR1', 'France', 1_699_999_990, 1, 3.0, 46.0, 9000.0, False, 200.0, 10.0, 0, None, None,
             None, False, 0],   # no index 17 (no extended=1): « ligne »
        ]}
        measured, planes = sky.parse_opensky(payload)
        self.assertEqual(measured, 1_700_000_005_000)
        self.assertEqual([p['id'] for p in planes], ['3c6444', 'planeur', 'simple'])
        self.assertEqual(planes[0], {'id': '3c6444', 'indicatif': 'DLH9U', 'pays': 'Germany', 'type': '',
                                     'categorie': 'rapide', 'lat': 50.0, 'lon': 8.5, 'altitude': 10100,
                                     'vitesse': 828, 'cap': 45, 'mesure': 1_700_000_000_000})
        self.assertEqual((planes[1]['categorie'], planes[1]['altitude'], planes[1]['vitesse'], planes[1]['mesure']),
                         ('leger', 1500, None, 1_700_000_005_000))
        self.assertEqual((planes[2]['categorie'], planes[2]['altitude'], planes[2]['mesure']),
                         ('ligne', 9000, 1_699_999_990_000))
        self.assertEqual(sky.parse_opensky({'time': 1, 'states': None}), (1000, []))
        with self.assertRaises(base.ToolError):
            sky.parse_opensky('x')

    def test_categories(self):
        self.assertEqual(sky.category_adsb('A6'), 'rapide')
        for code in ('A1', 'A2', 'A7', 'B1', 'B6'):
            self.assertEqual(sky.category_adsb(code), 'leger', code)
        for code in ('A3', 'A5', 'C1', None, '', 7):
            self.assertEqual(sky.category_adsb(code), 'ligne', code)
        self.assertEqual(sky.category_opensky(7), 'rapide')
        for code in (2, 3, 8, 9, 10, 12):
            self.assertEqual(sky.category_opensky(code), 'leger', code)
        for code in (0, 1, 4, 6, None, 'A6', True):
            self.assertEqual(sky.category_opensky(code), 'ligne', code)

    def test_square(self):
        self.assertEqual(sky.square(0, 10), {'lamin': -4, 'lamax': 4, 'lomin': 6, 'lomax': 14})
        self.assertAlmostEqual(sky.square(60, 0)['lomax'], 8)
        self.assertAlmostEqual(sky.square(84, 0)['lomax'], 20)   # cos < 0,2 → 4 / 0,2
        high = sky.square(88, 175)
        self.assertEqual((high['lamax'], high['lomax']), (90, 180))


# =========================================================================== sky.planes

class PlanesToolTests(unittest.TestCase):
    def test_adsb_first_sorted_limited_cached(self):
        def handler(request):
            self.assertEqual(request.url.host, 'api.adsb.lol')
            return httpx.Response(200, json=adsb_payload(50))

        with MockHttp(handler) as http:
            r = run(run_tool('sky.planes', {'lat': 44.4, 'lon': 4.8}))
            self.assertTrue(r.ok, r.error)
            self.assertEqual(http.calls[0].url.path, '/v2/point/44.400/4.800/240')
            self.assertEqual(http.calls[0].headers['user-agent'], base.user_agent())
            self.assertEqual(set(r.data), {'lieu', 'source', 'mesureA', 'demiCote', 'total', 'liste'})
            self.assertEqual(r.data['lieu'], {'nom': '', 'lat': 44.4, 'lon': 4.8})
            self.assertEqual((r.data['source'], r.data['demiCote'], r.data['total']), ('adsb.lol (ODbL)', 4, 50))
            self.assertEqual(r.data['mesureA'], 1_794_132_856_001)
            self.assertEqual(len(r.data['liste']), 40)
            self.assertEqual(set(r.data['liste'][0]), PLANE_KEYS)
            self.assertEqual(r.data['liste'][0]['id'], 'a00031')   # the nearest (i = 49)
            distances = [abs(p['lat'] - 44.4) for p in r.data['liste']]
            self.assertEqual(distances, sorted(distances))
            self.assertIn('adsb.lol : 50 avions en vol mesurés autour du point 44,4° N, 4,8° E ; '
                          'les 40 plus proches affichés.', r.summary)
            self.assertEqual(r.provenance, 'adsb.lol')
            self.assertIn('ODbL', r.sources[0].title)
            again = run(run_tool('sky.planes', {'lat': 44.4, 'lon': 4.8}))
            self.assertEqual(again.data, r.data)
            self.assertEqual(len(http.calls), 1)   # cache 60 s

    def test_opensky_fallback(self):
        def handler(request):
            if request.url.host == 'api.adsb.lol':
                return httpx.Response(403, text='User-Agent too generic')
            self.assertEqual(request.url.host, 'opensky-network.org')
            return httpx.Response(200, json={'time': 1_700_000_005, 'states': [
                opensky_state('loin', 47.0, 4.8), opensky_state('pres', 44.5, 4.8, category=2),
                opensky_state('sol', 44.41, 4.8, ground=True)]})

        with opensky_on(), MockHttp(handler) as http:
            r = run(run_tool('sky.planes', {'lat': 44.4, 'lon': 4.8}))
        self.assertTrue(r.ok, r.error)
        params = http.calls[1].url.params
        self.assertEqual((params['lamin'], params['lamax'], params['extended']), ('40.400', '48.400', '1'))
        self.assertAlmostEqual(float(params['lomin']), 4.8 - 4 / math.cos(math.radians(44.4)), places=3)
        self.assertEqual((r.data['source'], r.data['total']), ('OpenSky Network', 2))
        self.assertEqual([p['id'] for p in r.data['liste']], ['pres', 'loin'])
        self.assertEqual(r.data['liste'][0]['categorie'], 'leger')
        self.assertEqual(r.provenance, 'opensky')
        self.assertIn('non commercial', r.sources[0].title)
        self.assertTrue(r.summary.startswith('adsb.lol ne répond pas'), r.summary)
        self.assertIn('2 avions en vol mesurés', r.summary)

    def test_both_sources_fail(self):
        def handler(request):
            return httpx.Response(500 if request.url.host == 'api.adsb.lol' else 429)

        with opensky_on(), MockHttp(handler):
            r = run(run_tool('sky.planes', {'lat': 10, 'lon': 10}))
        self.assertFalse(r.ok)
        self.assertIn('adsb.lol', r.error)
        self.assertIn('OpenSky', r.error)
        self.assertIn('quota', r.error)
        self.assertIn("Aucun avion n'est affiché", r.error)

    def test_opensky_off_there_is_no_fallback_and_it_is_said(self):
        """24/11/2026: off by default. adsb.lol down, OpenSky is NOT asked, and the person is told why."""
        def handler(request):
            self.assertEqual(request.url.host, 'api.adsb.lol')   # never opensky-network.org
            return httpx.Response(503)

        self.assertFalse(sky.opensky_enabled())
        with MockHttp(handler) as http:
            r = run(run_tool('sky.planes', {'lat': 44.4, 'lon': 4.8}))
        self.assertFalse(r.ok)
        self.assertEqual(len(http.calls), 1)
        self.assertIn('OpenSky Network est éteint', r.error)
        self.assertIn('Paramètres', r.error)
        self.assertIn("Aucun avion n'est affiché", r.error)

    def test_default_city_geocoded(self):
        from aurora import db
        db.set_setting('default_city', 'Lyon')
        self.addCleanup(db.set_setting, 'default_city', 'Paris')

        def handler(request):
            if request.url.host == 'geocoding-api.open-meteo.com':
                self.assertEqual(request.url.params['name'], 'Lyon')
                return httpx.Response(200, json={'results': [{'name': 'Lyon', 'country': 'France', 'latitude': 45.75,
                                                              'longitude': 4.85, 'timezone': 'Europe/Paris'}]})
            self.assertEqual(request.url.path, '/v2/point/45.750/4.850/240')
            return httpx.Response(200, json=adsb_payload(3))

        with MockHttp(handler):
            r = run(run_tool('sky.planes', {}))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.data['lieu'], {'nom': 'Lyon', 'lat': 45.75, 'lon': 4.85})
        self.assertIn('3 avions en vol mesurés autour de Lyon ; 40 au plus affichés.', r.summary)
        self.assertTrue(any('géocodage' in s.title for s in r.sources))

    def test_latitude_alone_is_refused_without_network(self):
        with MockHttp(no_network) as http:
            r = run(run_tool('sky.planes', {'lat': 44.4}))
            out = run(run_tool('sky.planes', {'lat': 95, 'lon': 0}))
        self.assertFalse(r.ok)
        self.assertIn('latitude et la longitude ensemble', r.error)
        self.assertFalse(out.ok)
        self.assertEqual(http.calls, [])


# =========================================================================== sky.traffic

def world_payload(flying: int, *, extra: int = 0) -> dict:
    """``flying`` planes in flight whose latitude encodes their index (i / 1000), plus ``extra`` to be left out."""
    states = []
    for i in range(flying):
        states.append(opensky_state(f'{i:06x}', i / 1000, (i % 300) - 150.5, speed=250.0, track=90.0,
                                    geo=10200.0 if i % 2 else None))
    for i in range(extra):
        states.append(opensky_state(f'sol{i}', 1.0, 1.0, ground=True))
        states.append(opensky_state(f'cap{i}', 1.0, 1.0, track=None))
    return {'time': 1_700_000_000, 'states': states}


class TrafficTests(unittest.TestCase):
    def setUp(self):
        on = opensky_on()   # the person has turned OpenSky on; the test of the default is the last of this class
        on.start()
        self.addCleanup(on.stop)

    def test_sampling_at_a_regular_step(self):
        with MockHttp(lambda request: httpx.Response(200, json=world_payload(4000, extra=7))) as http:
            r = run(run_tool('sky.traffic'))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(http.calls[0].url, httpx.URL(sky.OPENSKY_URL))
        self.assertEqual(set(r.data), {'source', 'mesureA', 'total', 'avions'})
        self.assertEqual((r.data['source'], r.data['mesureA'], r.data['total']),
                         ('OpenSky Network', 1_700_000_000_000, 4000))
        avions = r.data['avions']
        self.assertEqual(len(avions), 1500)
        indexes = [round(a[0] * 1000) for a in avions]
        self.assertEqual(indexes[0], 0)
        self.assertTrue(all(b - a in (2, 3) for a, b in zip(indexes, indexes[1:])))   # step 4000 / 1500 ≈ 2,67
        self.assertGreaterEqual(indexes[-1], 3990)
        self.assertEqual(avions[0], [0.0, -150.5, 90, 900, 10000])   # [lat, lon, cap, km/h, m] ; [13] absent → [7]
        self.assertTrue(all(a[4] == (10200 if i % 2 else 10000) for i, a in zip(indexes, avions)))
        self.assertIn('4 000 avions en vol mesurés dans le monde ; 1 500 affichés', r.summary.replace(base.NBSP, ' '))

    def test_small_world_is_kept_whole(self):
        with MockHttp(lambda request: httpx.Response(200, json=world_payload(3))):
            r = run(run_tool('sky.traffic'))
        self.assertEqual([round(a[0] * 1000) for a in r.data['avions']], [0, 1, 2])

    def test_cache_then_last_measure_when_opensky_fails(self):
        answers = [httpx.Response(200, json=world_payload(3)), httpx.Response(429, text='Too many requests')]
        with MockHttp(lambda request: answers.pop(0)) as http:
            first = run(run_tool('sky.traffic'))
            run(run_tool('sky.traffic'))
            self.assertEqual(len(http.calls), 1)   # cache 5 min
            sky._world_fresh.clear()               # 5 min later
            stale = run(run_tool('sky.traffic'))
            self.assertTrue(stale.ok, stale.error)
            self.assertEqual(stale.data, first.data)
            self.assertIn('Dernière vraie mesure gardée', stale.summary)
            self.assertIn('quota', stale.summary)
            run(run_tool('sky.traffic'))
            self.assertEqual(len(http.calls), 2)   # after a failure too, the next try waits 5 min

    def test_no_measure_at_all_is_said(self):
        with MockHttp(lambda request: httpx.Response(503)):
            r = run(run_tool('sky.traffic'))
        self.assertFalse(r.ok)
        self.assertIn('Trafic mondial indisponible', r.error)

    def test_one_read_at_a_time(self):
        async def together():
            return await asyncio.gather(run_tool('sky.traffic'), run_tool('sky.traffic'), run_tool('sky.traffic'))

        with MockHttp(lambda request: httpx.Response(200, json=world_payload(5))) as http:
            results = run(together())
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(http.calls), 1)

    def test_off_nothing_is_read_and_a_kept_measure_is_not_shown(self):
        """24/11/2026: OpenSky is off until the person turns it on in Paramètres. Off, no request leaves, a measure
        kept from before is not shown either, and the answer says where to turn it on."""
        with MockHttp(lambda request: httpx.Response(200, json=world_payload(3))) as http:
            self.assertTrue(run(run_tool('sky.traffic')).ok)          # on: one real read, kept 5 min
            with mock.patch.object(sky, 'opensky_enabled', return_value=False):
                off = run(run_tool('sky.traffic'))
            self.assertEqual(len(http.calls), 1)
        self.assertFalse(off.ok)
        self.assertIn('Trafic mondial éteint', off.error)
        self.assertIn('Paramètres', off.error)
        self.assertIn('adsb.lol', off.error)


# =========================================================================== sky.satellites

class SatelliteTests(unittest.TestCase):
    def setUp(self):
        sky.orbits_file().unlink(missing_ok=True)

    def test_geodetic_reference_points(self):
        a = sky.WGS84_A
        b = a * math.sqrt(1 - sky.WGS84_E2)
        for (x, y, z, gmst), expected in (((a + 400, 0, 0, 0), (0, 0, 400)),
                                          ((0, a + 400, 0, 0), (0, 90, 400)),
                                          ((0, a + 400, 0, math.pi / 2), (0, 0, 400)),   # Earth turned a quarter
                                          ((0, 0, b + 400, 0.3), (90, None, 400)),
                                          ((0, 0, -(b + 400), 0), (-90, None, 400))):
            lat, lon, alt = sky.geodetic(x, y, z, gmst)
            self.assertAlmostEqual(lat, expected[0], places=6)
            if expected[1] is not None:
                self.assertAlmostEqual(lon, expected[1], places=6)
            self.assertAlmostEqual(alt, expected[2], places=4)

    def test_iss_from_a_fixed_orbit_at_a_fixed_date(self):
        with MockHttp(tle_handler) as http, mock.patch.object(sky, '_utcnow', return_value=WHEN):
            r = run(run_tool('sky.satellites'))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(len(r.data['liste']), 1)
        iss = r.data['liste'][0]
        self.assertEqual(set(iss), {'norad', 'nom', 'role', 'lat', 'lon', 'altitude', 'orbiteDu', 'source', 'trace'})
        self.assertEqual((iss['norad'], iss['nom'], iss['role'], iss['source']),
                         (25544, 'ISS (ZARYA)', 'Station spatiale internationale', 'CelesTrak'))
        self.assertTrue(380 <= iss['altitude'] <= 450, iss['altitude'])
        self.assertLessEqual(abs(iss['lat']), 52)
        self.assertEqual(iss['orbiteDu'], ISS_EPOCH_MS)
        # independent check: geocentric latitude and longitude straight from the TEME vector and the sidereal time
        from sgp4.api import jday
        from sgp4.propagation import gstime
        jd, fr = jday(2019, 12, 9, 18, 0, 0)
        orbit = sky.to_orbit(25544, ISS[0], 'Station spatiale internationale', ISS[1], ISS[2], 'CelesTrak')
        _err, (x, y, z), _v = orbit.satrec.sgp4(jd, fr)
        self.assertAlmostEqual(iss['lat'], math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z))), delta=0.3)
        lon = math.degrees(math.atan2(y, x) - gstime(jd + fr))
        self.assertAlmostEqual((iss['lon'] - lon + 540) % 360 - 180, 0, places=3)
        # track: 0 to 45 min, one point per minute, 2 decimals, ~3,9° of ground per minute
        trace = iss['trace']
        self.assertEqual(len(trace), 46)
        self.assertEqual(trace[0], [round(iss['lat'], 2), round(iss['lon'], 2)])
        self.assertTrue(all(round(v, 2) == v for point in trace for v in point))
        steps = [great_circle_deg(p, q) for p, q in zip(trace, trace[1:])]
        self.assertTrue(all(3.3 < s < 4.4 for s in steps), steps)
        # sources and honesty: computed, not observed; 7 orbits not found
        hosts = [c.url.host for c in http.calls]
        self.assertEqual(hosts.count('celestrak.org'), 8)
        self.assertEqual(hosts.count('tle.ivanstanojevic.me'), 7)
        self.assertIn('SGP4', r.summary)
        self.assertIn('pas observées', r.summary)
        self.assertIn('Orbite introuvable', r.summary)
        self.assertEqual([s.title for s in r.sources], [sky.CELESTRAK_TITLE])
        kept = json.loads(sky.orbits_file().read_text(encoding='utf-8'))
        self.assertEqual([o['norad'] for o in kept['orbites']], [25544])
        self.assertEqual(Path(sky.orbits_file()).parent, Path(os.environ['AURORA_HOME']) / 'cache')

    def test_orbit_older_than_14_days_is_not_shown(self):
        later = datetime.fromtimestamp(ISS_EPOCH_MS / 1000, timezone.utc) + timedelta(days=14, hours=1)
        with MockHttp(tle_handler), mock.patch.object(sky, '_utcnow', return_value=later):
            r = run(run_tool('sky.satellites'))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.data, {'liste': []})
        self.assertIn('Orbite de plus de 14 jours, non affiché : ISS (ZARYA)', r.summary)

    def test_tle_api_rescues_celestrak(self):
        def handler(request):
            if request.url.host == 'celestrak.org':
                return httpx.Response(503)
            if request.url.path == '/api/tle/25544':
                return httpx.Response(200, json={'name': 'ISS (ZARYA)', 'line1': ISS[1], 'line2': ISS[2]})
            return httpx.Response(404)

        with MockHttp(handler), mock.patch.object(sky, '_utcnow', return_value=WHEN):
            r = run(run_tool('sky.satellites'))
        self.assertTrue(r.ok, r.error)
        self.assertEqual(r.data['liste'][0]['source'], 'TLE API')
        self.assertEqual([s.title for s in r.sources], [sky.TLE_API_TITLE])

    def test_celestrak_every_2_hours_and_disk_copy_offline(self):
        with MockHttp(tle_handler) as http, mock.patch.object(sky, '_utcnow', return_value=WHEN):
            run(run_tool('sky.satellites'))                     # network read, copy on disk
            sky._orbits_fresh.clear()
            sky._orbits_last.clear()                             # as after a restart of the service
            before = len(http.calls)
            with self.assertLogs('aurora.tools.sky', level='WARNING') as logged:
                r = run(run_tool('sky.satellites'))
            self.assertIn('relues sur le disque', logged.output[0])
            later = [c.url.host for c in http.calls[before:]]
            self.assertNotIn('celestrak.org', later)            # asked less than 2 h ago
            self.assertEqual(later.count('tle.ivanstanojevic.me'), 8)
            self.assertTrue(r.ok, r.error)                      # TLE API knows nothing: the disk copy
            self.assertEqual(r.data['liste'][0]['norad'], 25544)
            self.assertIn('relues sur le disque', r.summary)
            before = len(http.calls)
            run(run_tool('sky.satellites'))
            self.assertEqual(len(http.calls), before)           # next network try 15 min later
        with MockHttp(lambda request: httpx.Response(503)), mock.patch.object(sky, '_utcnow', return_value=WHEN), \
                self.assertLogs('aurora.tools.sky', level='WARNING'):
            offline = run(run_tool('sky.satellites'))          # every cache emptied, no network at all
        self.assertTrue(offline.ok, offline.error)
        self.assertEqual(offline.data['liste'][0]['nom'], 'ISS (ZARYA)')
        self.assertIn('relues sur le disque', offline.summary)

    def test_no_network_and_no_copy_is_said(self):
        with MockHttp(lambda request: httpx.Response(503)):
            r = run(run_tool('sky.satellites'))
        self.assertFalse(r.ok)
        self.assertIn("aucun satellite n'est affiché", r.error)

    def test_disk_copy_ignores_unknown_or_broken_orbits(self):
        path = sky.orbits_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'lu': '2019-12-09T17:00:00Z', 'orbites': [
            {'norad': 25544, 'nom': 'ISS (ZARYA)', 'role': 'autre', 'l1': ISS[1], 'l2': ISS[2], 'source': 'CelesTrak'},
            {'norad': 99999, 'nom': 'inconnu', 'l1': ISS[1], 'l2': ISS[2]},
            {'norad': 20580, 'nom': 'HST', 'l1': 'abimé', 'l2': '2 xx'},
            'pas une orbite']}), encoding='utf-8')
        orbits, kept_at = sky.read_saved_orbits()
        self.assertEqual([(o.norad, o.role) for o in orbits], [(25544, 'Station spatiale internationale')])
        self.assertEqual(kept_at, '2019-12-09T17:00:00Z')

    def test_tle_text_reading(self):
        self.assertEqual(sky.parse_tle_text('\n'.join(ISS), 25544), ISS)
        self.assertEqual(sky.parse_tle_text('\n'.join(ISS[1:]), 25544), ('25544', ISS[1], ISS[2]))
        self.assertIsNone(sky.parse_tle_text('\n'.join(ISS), 20580))   # another satellite
        self.assertIsNone(sky.parse_tle_text('No GP data found', 25544))


# =========================================================================== NASA GIBS images

class ImageTests(unittest.TestCase):
    def test_bbox_reading_and_grid(self):
        self.assertEqual(sky.parse_bbox('44,4,46,6'), (44.0, 4.0, 46.0, 6.0))
        for bad in ('', '1,2,3', 'a,b,c,d', '1,2,3,nan', '1,2,3,inf', '1,2,3,4,5', '1,2,3,5000'):
            with self.assertRaises(ValueError, msg=bad):
                sky.parse_bbox(bad)
        self.assertEqual(sky.snap_bbox((44.1, 4.1, 45.9, 5.9)), (44.0, 4.0, 46.0, 6.0))       # step 0,5°
        self.assertEqual(sky.snap_bbox((10.3, 20.7, 30.1, 60.2)), (10.0, 20.0, 35.0, 65.0))    # step 5°
        self.assertEqual(sky.snap_bbox((85.2, 175.3, 89.9, 179.9)), (85.0, 175.0, 90.0, 180.0))  # clamped
        self.assertEqual(sky.snap_bbox((-0.1, -0.1, 0.1, 0.1)), (-0.25, -0.25, 0.25, 0.25))
        for bad in ((46, 4, 44, 6), (44, 6, 46, 4), (0, 0, 45, 10)):   # inverted, more than 40° of latitude
            with self.assertRaises(ValueError, msg=str(bad)):
                sky.snap_bbox(bad)
        self.assertEqual(sky.bbox_text((44.0, 4.25, 46.0, 6.0)), '44,4.25,46,6')

    def test_detail_image_request_and_cache(self):
        with MockHttp(gibs_ok) as http:
            image, served = run(sky.detail_image((44.1, 4.1, 45.9, 5.9)))
            self.assertEqual((image, served), (JPEG, (44.0, 4.0, 46.0, 6.0)))
            params = http.calls[0].url.params
            self.assertEqual((params['LAYERS'], params['BBOX'], params['CRS'], params['VERSION'], params['FORMAT']),
                             ('BlueMarble_NextGeneration', '44,4,46,6', 'EPSG:4326', '1.3.0', 'image/jpeg'))
            self.assertEqual((params['WIDTH'], params['HEIGHT']), ('724', '1024'))   # 1024 × cos 45°
            self.assertNotIn('TIME', params)
            self.assertEqual(run(sky.detail_image((44.2, 4.3, 45.8, 5.7)))[1], served)   # same grid cell
            self.assertEqual(len(http.calls), 1)
            with self.assertRaises(ValueError):
                run(sky.detail_image((0, 0, 45, 10)))
            self.assertEqual(len(http.calls), 1)

    def test_detail_image_refuses_what_is_not_a_jpeg(self):
        answers = [httpx.Response(200, text='<ServiceExceptionReport/>',
                                  headers={'content-type': 'application/vnd.ogc.se_xml'}),
                   httpx.Response(200, content=b'<html>', headers={'content-type': 'image/jpeg'})]
        with MockHttp(lambda request: answers.pop(0)):
            for _ in range(2):
                with self.assertRaises(base.ToolError):
                    run(sky.detail_image((44, 4, 46, 6)))
        with MockHttp(gibs_ok), mock.patch.object(sky, 'IMAGE_MAX_BYTES', 10):
            with self.assertRaises(base.ToolError) as caught:
                run(sky.detail_image((44, 4, 46, 6)))
        self.assertIn('trop volumineuse', caught.exception.message_fr)

    def test_daily_image_yesterday_then_the_day_before_then_the_last_one(self):
        state = {'up': True}

        def handler(request):
            if not state['up']:
                return httpx.Response(503)
            params = request.url.params
            self.assertEqual((params['WIDTH'], params['HEIGHT'], params['BBOX']), ('2048', '1024', '-90,-180,90,180'))
            # 7/11: no satellite has data (status 200, a JPEG, but black); 6/11: Suomi NPP has none, NOAA-20 has its image
            if params['TIME'] == '2026-11-07' or params['LAYERS'] == sky.DAILY_LAYER:
                return httpx.Response(200, content=EMPTY_JPEG, headers={'content-type': 'image/jpeg'})
            return httpx.Response(200, content=JPEG, headers={'content-type': 'image/jpeg'})

        snpp, noaa20, noaa21 = (layer for layer, _ in sky.DAILY_LAYERS)
        now = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
        with MockHttp(handler) as http, mock.patch.object(sky, '_utcnow', return_value=now):
            self.assertEqual(run(sky.daily_image()), (JPEG, '2026-11-06', 'NOAA-20'))
            self.assertEqual([(c.url.params['TIME'], c.url.params['LAYERS']) for c in http.calls],
                             [('2026-11-07', snpp), ('2026-11-07', noaa20), ('2026-11-07', noaa21),
                              ('2026-11-06', snpp), ('2026-11-06', noaa20)])
            run(sky.daily_image())
            self.assertEqual(len(http.calls), 5)                 # cache 6 h
            sky._daily_fresh.clear()
            state['up'] = False
            self.assertEqual(run(sky.daily_image()), (JPEG, '2026-11-06', 'NOAA-20'))   # the last real image, its date
        with MockHttp(lambda request: httpx.Response(503)) as http, mock.patch.object(sky, '_utcnow', return_value=now):
            with self.assertRaises(base.ToolError) as caught:
                run(sky.daily_image())
            self.assertEqual(len(http.calls), 2)   # GIBS fails: one try per day, not one per satellite
        self.assertIn('Image NASA du jour indisponible', caught.exception.message_fr)
        self.assertIn('2026-11-06 : NASA GIBS a répondu par une erreur HTTP 503', caught.exception.message_fr)
        with MockHttp(lambda request: httpx.Response(200, content=EMPTY_JPEG, headers={'content-type': 'image/jpeg'})), \
                mock.patch.object(sky, '_utcnow', return_value=now):
            with self.assertRaises(base.ToolError) as caught:
                run(sky.daily_image())   # never a black image passed off as the Earth
        self.assertIn('2026-11-07 : image vide, Suomi NPP, NOAA-20, NOAA-21 sans donnée', caught.exception.message_fr)
        self.assertIn('2026-11-06 : image vide', caught.exception.message_fr)


# =========================================================================== routes

class GlobeSkyRouteTests(ApiTestCase):
    """The token and the client of tests/test_api.py (TempEnv: temporary base, AURORA_OFFLINE=1)."""

    def test_private_mode_answers_without_any_network_call(self):
        self.client.patch('/api/settings', json={'mode': 'prive'})
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}), MockHttp(no_network) as http:
            for path in ('/api/globe/avions?lat=44.4&lon=4.8', '/api/globe/avions', '/api/globe/monde',
                         '/api/globe/satellites'):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 200, path)
                body = res.json()
                self.assertFalse(body['ok'], path)
                self.assertTrue(body['data']['private_mode'], path)
                self.assertIn('Mode privé', body['summary'])
            for path in ('/api/globe/detail?bbox=44,4,46,6', '/api/globe/nasa', '/api/globe/detail?bbox=x'):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 403, path)
                self.assertTrue(res.json()['data']['private_mode'], path)
                self.assertIn('Mode privé', res.json()['detail'])
            self.assertEqual(http.calls, [])

    def test_routes_need_the_token(self):
        for path in ('/api/globe/avions', '/api/globe/monde', '/api/globe/satellites', '/api/globe/detail',
                     '/api/globe/nasa'):
            self.assertEqual(self.client.get(path, headers=NO_TOKEN).status_code, 401, path)

    def test_json_routes_give_the_contract_shapes(self):
        def handler(request):
            if request.url.host == 'api.adsb.lol':
                return httpx.Response(200, json=adsb_payload(2))
            if request.url.host == 'opensky-network.org':
                return httpx.Response(200, json=world_payload(4))
            return tle_handler(request)

        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}), MockHttp(handler), \
                mock.patch.object(sky, '_utcnow', return_value=WHEN):
            planes = self.client.get('/api/globe/avions', params={'lat': 44.4, 'lon': 4.8}).json()
            # 24/11/2026: the world traffic comes from OpenSky, off until the person turns it on in Paramètres.
            self.assertFalse(self.client.get('/api/settings').json()['opensky_enabled'])
            off = self.client.get('/api/globe/monde').json()
            self.assertIn(self.client.patch('/api/settings', json={'opensky_enabled': 'oui'}).status_code, (400, 422))
            self.assertTrue(self.client.patch('/api/settings', json={'opensky_enabled': True}).json()['opensky_enabled'])
            world = self.client.get('/api/globe/monde').json()
            sats = self.client.get('/api/globe/satellites').json()
            invalid = self.client.get('/api/globe/avions', params={'lat': 44.4}).json()
        self.assertFalse(off['ok'], off)
        self.assertIn('Trafic mondial éteint', off['error'])
        self.assertTrue(world['ok'], world)
        self.assertTrue(planes['ok'], planes)
        self.assertEqual(set(planes['data']), {'lieu', 'source', 'mesureA', 'demiCote', 'total', 'liste'})
        self.assertIn('ODbL', planes['sources'][0]['title'])
        self.assertEqual(set(world['data']), {'source', 'mesureA', 'total', 'avions'})
        self.assertEqual(len(world['data']['avions'][0]), 5)
        self.assertEqual(set(sats['data']), {'liste'})
        self.assertEqual(len(sats['data']['liste'][0]['trace']), 46)
        self.assertFalse(invalid['ok'])

    def test_image_routes_headers_and_errors(self):
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}), MockHttp(gibs_ok), \
                mock.patch.object(sky, '_utcnow', return_value=datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)):
            res = self.client.get('/api/globe/detail', params={'bbox': '44.1,4.1,45.9,5.9'})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers['content-type'], 'image/jpeg')
            self.assertEqual(res.headers['x-bbox'], '44,4,46,6')
            self.assertEqual(res.headers['cache-control'], 'private, max-age=86400')
            self.assertEqual(res.content, JPEG)
            for bad in ({'bbox': 'n,i,m,p'}, {}, {'bbox': '0,0,45,10'}, {'bbox': '46,4,44,6'}):
                res = self.client.get('/api/globe/detail', params=bad)
                self.assertEqual(res.status_code, 400, bad)
                self.assertIn('Région invalide', res.json()['detail'])
            nasa = self.client.get('/api/globe/nasa')
            self.assertEqual(nasa.status_code, 200)
            self.assertEqual((nasa.headers['x-date-image'], nasa.headers['x-satellite-image'], nasa.headers['cache-control']),
                             ('2026-11-07', 'Suomi NPP', 'no-store'))
            self.assertEqual(nasa.content, JPEG)
        with mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'}), MockHttp(lambda request: httpx.Response(503)):
            res = self.client.get('/api/globe/detail', params={'bbox': '10,10,12,12'})
            self.assertEqual(res.status_code, 503)
            self.assertIn('NASA GIBS', res.json()['detail'])
            res = self.client.get('/api/globe/nasa')
            self.assertEqual(res.status_code, 503)
            self.assertFalse(res.json()['ok'])
        offline = self.client.get('/api/globe/nasa')   # AURORA_OFFLINE=1: refused before any request
        self.assertEqual(offline.status_code, 503)
        self.assertIn('hors ligne', offline.json()['detail'])


if __name__ == '__main__':
    unittest.main()
