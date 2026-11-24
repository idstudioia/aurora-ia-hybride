"""HTTP API + SSE (fastapi.testclient). No network: AURORA_OFFLINE=1, orchestrator wired to doubles."""
import logging
import time
import unittest

from fastapi.testclient import TestClient

from aurora import main
from aurora.core import orchestrator as orch_module
from aurora.tasks import events
from tests.doubles_core import FakeClaude, FakeDecision, FakeTools, TempEnv, build, snapshot_response

ORIGIN = 'http://127.0.0.1:5173'
BASE = 'http://127.0.0.1:8765'
NO_TOKEN = {'X-Aurora-Token': ''}   # the client sends the token on every request, like the interface


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def route(text: str) -> FakeDecision:
    lowered = text.lower()
    if 'ram' in lowered or 'diagnostic' in lowered:
        return FakeDecision('system.diagnose') if 'diagnostic' in lowered else \
            FakeDecision('system.status', slots={'metric': 'ram'})
    return FakeDecision('chat.general')


class ApiTestCase(unittest.TestCase):
    claude_configured = False

    def setUp(self):
        self.env = TempEnv().__enter__()
        tools = FakeTools({'system.snapshot': snapshot_response,
                           'system.processes': {'ok': True, 'data': {'processes': []}, 'summary': 'Aucun.',
                                                'provenance': 'psutil'},
                           'system.diagnose': {'ok': True, 'data': {'causes': []},
                                               'summary': 'Diagnostic : aucun problème majeur détecté.',
                                               'provenance': 'psutil'}})
        self.orch, self.tools, self.ollama, self.claude = build(
            route, tools=tools, claude=FakeClaude(configured=self.claude_configured))
        orch_module.set_orchestrator(self.orch)
        main.app.state.sse_ping_s = 0.2
        self.client = TestClient(main.app, base_url=BASE, raise_server_exceptions=False)
        self.client.__enter__()
        self.token = self.client.get('/api/bootstrap', headers={'Origin': ORIGIN}).json()['token']
        self.auth = {'X-Aurora-Token': self.token}
        self.client.headers.update(self.auth)   # CLAUDE.md: the token is required on every request

    def tearDown(self):
        self.client.__exit__(None, None, None)
        orch_module.set_orchestrator(None)
        self.env.__exit__(None, None, None)


class SessionTests(ApiTestCase):
    def test_bootstrap_requires_allowed_origin(self):
        ok = self.client.get('/api/bootstrap', headers={'Origin': ORIGIN})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(set(ok.json()), {'token', 'version', 'mode'})
        self.assertEqual(ok.json()['mode'], 'equilibre')
        self.assertEqual(self.client.get('/api/bootstrap', headers={'Origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/bootstrap').status_code, 403)
        # Proof that a foreign web site never gets the token, whatever the way it tries:
        from aurora.security import auth
        foreign = ('https://evil.example', 'http://evil.example', 'http://127.0.0.1.evil.example',
                   'http://127.0.0.1:5173.evil.example', 'http://localhost:5174', 'http://127.0.0.1:8765',
                   'https://127.0.0.1:5173', 'null', 'file://', 'chrome-extension://abcdef')
        for origin in foreign:                       # fetch() / XHR from another origin
            res = self.client.get('/api/bootstrap', headers={'Origin': origin, **NO_TOKEN})
            self.assertEqual(res.status_code, 403, origin)
            self.assertNotIn(auth.SESSION_TOKEN, res.text, origin)
            self.assertIsNone(res.headers.get('access-control-allow-origin'), origin)   # and unreadable anyway
        for headers in ({}, {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors'}):
            res = self.client.get('/api/bootstrap', headers={**headers, **NO_TOKEN})   # <script src>, <img>, link
            self.assertEqual(res.status_code, 403)
            self.assertNotIn(auth.SESSION_TOKEN, res.text)
        preflight = self.client.options('/api/bootstrap', headers={            # incl. Private Network Access
            'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Private-Network': 'true', **NO_TOKEN})
        self.assertNotEqual(preflight.status_code, 200)
        self.assertIsNone(preflight.headers.get('access-control-allow-origin'))
        self.assertIsNone(preflight.headers.get('access-control-allow-private-network'))
        with TestClient(main.app, base_url='http://evil.example') as rebinding:   # DNS rebinding to 127.0.0.1
            res = rebinding.get('/api/bootstrap', headers={'Origin': 'http://evil.example'})
            self.assertEqual(res.status_code, 403)
            self.assertNotIn(auth.SESSION_TOKEN, res.text)
        # The AURORA interface gets it: exact CORS answer (never '*'), no credentials, never cached,
        # and the answer cannot be framed by another page.
        self.assertEqual(ok.headers.get('access-control-allow-origin'), ORIGIN)
        self.assertIn('origin', ok.headers.get('vary', '').lower())
        self.assertIsNone(ok.headers.get('access-control-allow-credentials'))
        self.assertEqual(ok.headers.get('cache-control'), 'no-store')
        self.assertEqual(ok.headers.get('x-frame-options'), 'DENY')
        self.assertIn("frame-ancestors 'none'", ok.headers.get('content-security-policy', ''))
        # With a launcher handing the token at start-up (planned Tauri app), the route can be closed.
        from unittest import mock
        import os
        with mock.patch.dict(os.environ, {'AURORA_BOOTSTRAP': '0'}):
            closed = self.client.get('/api/bootstrap', headers={'Origin': ORIGIN, **NO_TOKEN})
            self.assertEqual(closed.status_code, 404)
            self.assertNotIn(auth.SESSION_TOKEN, closed.text)
            self.assertEqual(self.client.get('/api/health').status_code, 200)   # the token still works

    def test_cors_headers_for_frontend(self):
        res = self.client.options('/api/ask', headers={'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST',
                                                       'Access-Control-Request-Headers': 'x-aurora-token,content-type'})
        self.assertEqual(res.headers.get('access-control-allow-origin'), ORIGIN)
        # PUT is needed by Paramètres › Clé Claude (PUT /api/secrets/anthropic, cross-origin 5173 → 8765).
        res = self.client.options('/api/secrets/anthropic', headers={
            'Origin': ORIGIN, 'Access-Control-Request-Method': 'PUT',
            'Access-Control-Request-Headers': 'x-aurora-token,content-type'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('PUT', res.headers.get('access-control-allow-methods', ''))

    def test_non_local_host_rejected(self):
        with TestClient(main.app, base_url='http://aurora.attacker.example') as other:
            self.assertEqual(other.get('/api/health').status_code, 403)

    def test_locked_routes_require_token(self):
        self.assertEqual(self.client.post('/api/ask', json={'text': 'RAM ?'}, headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.post('/api/ask', json={'text': 'RAM ?'},
                                          headers={'X-Aurora-Token': 'faux'}).status_code, 401)
        self.assertEqual(self.client.patch('/api/settings', json={'mode': 'prive'}, headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.delete('/api/events', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.get('/api/stream', headers=NO_TOKEN).status_code, 401)
        # CLAUDE.md: the token is required on every request, reads included; only /api/bootstrap gives it.
        for path in ('/api/health', '/api/capabilities', '/api/system', '/api/system/processes', '/api/memories',
                     '/api/memories/graph', '/api/tasks', '/api/runs', '/api/events', '/api/permissions',
                     '/api/conversations', '/api/settings', '/api/usage', '/api/quality/latest', '/api/weather'):
            self.assertEqual(self.client.get(path, headers=NO_TOKEN).status_code, 401, path)
        self.assertEqual(self.client.post('/api/sim/orbit', json={'body': 'terre', 'altitude_km': 400},
                                          headers=NO_TOKEN).status_code, 401)
        from aurora import main
        public = [r.path for r in main.app.routes if r.path.startswith('/api/')
                  and not any(getattr(d.dependency, '__name__', '') == 'require_token'
                              for d in getattr(r, 'dependencies', []))]
        self.assertEqual(public, ['/api/bootstrap'])
        for path in ('/docs', '/redoc', '/openapi.json'):          # no public API description either
            self.assertEqual(self.client.get(path, headers=NO_TOKEN).status_code, 404, path)

    def test_health_and_capabilities_are_real(self):
        health = self.client.get('/api/health').json()
        self.assertEqual(health['status'], 'ok')
        self.assertEqual(health['version'], '0.2.0')
        caps = health['capabilities']
        self.assertFalse(caps['internet'])                 # offline in tests: measured, not assumed
        self.assertFalse(caps['cloud']['configured'])      # no ANTHROPIC_API_KEY
        self.assertEqual(caps['mode'], 'equilibre')
        self.assertEqual(set(self.client.get('/api/capabilities').json()),
                         {'internet', 'ollama', 'cloud', 'mode', 'shell', 'pc_control', 'voice_hint', 'checked_at'})
        self.assertFalse(caps['shell'])                    # the terminal with permission is off by default
        self.assertFalse(caps['pc_control'])               # so are the mouse and keyboard (9/11/2026)

    def test_errors_hide_internals(self):
        async def boom(*args, **kwargs):
            raise RuntimeError('sk-ant-secret-123 Traceback')
        self.orch.handle_request = boom
        res = self.client.post('/api/ask', json={'text': 'RAM ?'}, headers=self.auth)
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.json(), {'detail': 'Erreur interne du service AURORA.'})
        bad = self.client.post('/api/ask', json={'text': 'x', 'source': 'hack'}, headers=self.auth)
        self.assertEqual(bad.status_code, 422)
        self.assertIn('Requête invalide', bad.json()['detail'])


class AskTests(ApiTestCase):
    def test_ask_measure(self):
        res = self.client.post('/api/ask', json={'text': 'Quelle quantité de RAM est utilisée ?'}, headers=self.auth)
        self.assertEqual(res.status_code, 200, res.text)
        run = res.json()
        for key in ('task_id', 'conversation_id', 'status', 'answer', 'decision', 'engine', 'sources', 'steps',
                    'permission', 'verification', 'memories_used', 'ui', 'duration_ms', 'cost_usd'):
            self.assertIn(key, run)
        self.assertEqual(run['status'], 'done')
        self.assertIn('7,8 Go', run['answer'])
        detail = self.client.get(f"/api/runs/{run['task_id']}").json()
        self.assertEqual(set(detail), {'task', 'steps', 'events', 'permissions'})
        self.assertEqual(detail['events'][0]['kind'], 'run.created')
        runs_list = self.client.get('/api/runs').json()
        self.assertEqual(runs_list[0]['id'], run['task_id'])
        events_desc = self.client.get('/api/events', params={'run_id': run['task_id']}).json()
        self.assertEqual(events_desc[0]['kind'], 'run.done')
        self.assertIsInstance(events_desc[0]['payload'], dict)
        convs = self.client.get('/api/conversations').json()
        msgs = self.client.get(f"/api/conversations/{convs[0]['id']}/messages").json()
        self.assertEqual([m['role'] for m in msgs], ['user', 'assistant'])
        self.assertEqual(self.client.get('/api/runs/99999').status_code, 404)

    def test_unknown_intent_rejected(self):
        res = self.client.post('/api/ask', json={'text': 'x', 'options': {'intent': 'shell.exec'}}, headers=self.auth)
        self.assertEqual(res.status_code, 422)

    def test_starter_chat_compat(self):
        res = self.client.post('/api/chat', json={'prompt': 'Bonjour', 'mode': 'local'}, headers=self.auth)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(set(body), {'answer', 'mode', 'run'})
        self.assertEqual(body['mode'], 'local')
        self.assertEqual(body['answer'], 'Réponse locale de test.')

    def test_starter_diagnostic_compat(self):
        res = self.client.post('/api/tools/diagnostic', headers=self.auth)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(set(body), {'task', 'metrics', 'run'})
        self.assertEqual(body['run']['decision']['intent'], 'system.diagnose')
        self.assertEqual(body['run']['decision']['method'], 'explicit')
        self.assertEqual(body['metrics']['ram_total_gb'], 16.0)
        self.assertEqual(body['task']['kind'], 'diagnostic')

    def test_run_cancel_and_resume_routes(self):
        run = self.client.post('/api/ask', json={'text': 'RAM ?'}, headers=self.auth).json()
        self.assertEqual(self.client.post(f"/api/runs/{run['task_id']}/resume", headers=self.auth).json()['status'],
                         'done')
        self.assertEqual(self.client.post(f"/api/runs/{run['task_id']}/cancel", headers=self.auth).json()['status'],
                         'done')
        self.assertEqual(self.client.post('/api/runs/4242/cancel', headers=self.auth).status_code, 404)
        # A request run is not a manual task: the starter PATCH cannot rewrite its status.
        self.assertEqual(self.client.patch(f"/api/tasks/{run['task_id']}", json={'status': 'pending'},
                                           headers=self.auth).status_code, 409)


class LabApiTests(ApiTestCase):
    """08/11/2026: the 3D lab of the home page (reviews/accueil-holographique.md § 3), real tools/lab.py."""

    def test_catalogue_and_scene_routes(self):
        catalogue = self.client.get('/api/lab/scenes')
        self.assertEqual(catalogue.status_code, 200, catalogue.text)
        self.assertTrue(catalogue.json()['ok'])
        self.assertEqual([s['id'] for s in catalogue.json()['data']['scenes']], ['bras_robotique', 'fusee', 'x_swing'])
        scene = self.client.get('/api/lab/scenes/bras_robotique').json()
        self.assertTrue(scene['ok'])
        self.assertEqual(scene['data']['format'], 'aurora.scene/1')
        self.assertEqual(scene['summary'], 'Scène « Bras robotique à 4 axes » : 13 pièces, illustration pédagogique.')
        unknown = self.client.get('/api/lab/scenes/moteur').json()
        self.assertFalse(unknown['ok'])
        self.assertIn('inconnue', unknown['error'])
        self.assertEqual(self.client.get('/api/lab/scenes/Bras-Robot').status_code, 422)
        for path in ('/api/lab/scenes', '/api/lab/scenes/bras_robotique'):
            self.assertEqual(self.client.get(path, headers=NO_TOKEN).status_code, 401, path)

    def test_scene_context_is_validated_and_passed_on(self):
        seen = []

        async def handle(text, conversation_id=None, source='text', options=None, attachments=None):
            seen.append(options)
            return {'status': 'done', 'answer': 'ok'}
        self.orch.handle_request = handle
        ok = self.client.post('/api/ask', json={'text': 'démonte-le',
                                                'options': {'scene': {'id': 'bras_robotique', 'piece': 'servo_coude'}}})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.client.post('/api/ask', json={'text': 'démonte-le', 'options': {'scene': {'id': 'bras_robotique'}}})
        self.assertEqual(seen, [{'scene': {'id': 'bras_robotique', 'piece': 'servo_coude'}},
                                {'scene': {'id': 'bras_robotique'}}])
        for bad in ({'id': 'Bras robot'}, {'id': 'bras_robotique', 'piece': '../x'}, {'piece': 'socle'},
                    {'id': 'bras_robotique', 'code': 'alert(1)'}, {'id': 'x' * 41}, 'bras_robotique'):
            res = self.client.post('/api/ask', json={'text': 'démonte-le', 'options': {'scene': bad}})
            self.assertEqual(res.status_code, 422, bad)
        self.assertEqual(len(seen), 2)


class ConsentApiTests(ApiTestCase):
    claude_configured = True

    def test_permission_flow_over_http(self):
        res = self.client.post('/api/ask', json={'text': 'Explique la gravité', 'options': {'engine': 'cloud'}},
                               headers=self.auth).json()
        self.assertEqual(res['status'], 'waiting_permission')
        pending = self.client.get('/api/permissions').json()
        self.assertEqual([p['id'] for p in pending], [res['permission']['id']])
        self.assertIn('text', pending[0]['preview'])
        self.assertEqual(self.client.post(f"/api/permissions/{res['permission']['id']}",
                                          json={'decision': 'grant'}, headers=NO_TOKEN).status_code, 401)
        done = self.client.post(f"/api/permissions/{res['permission']['id']}", json={'decision': 'grant'},
                                headers=self.auth).json()
        self.assertEqual(done['status'], 'done')
        self.assertEqual(done['answer'], 'Réponse de Claude.')
        self.assertEqual(self.client.get('/api/permissions').json(), [])
        usage = self.client.get('/api/usage').json()
        self.assertAlmostEqual(usage['today_usd'], 0.0123, places=6)
        self.assertEqual(self.client.post('/api/permissions/777', json={'decision': 'grant'},
                                          headers=self.auth).status_code, 404)


class DataRouteTests(ApiTestCase):
    def test_system_routes_starter_shape(self):
        snap = self.client.get('/api/system')
        self.assertEqual(snap.status_code, 200)
        for key in ('cpu_percent', 'ram_percent', 'disk_percent', 'ram_total_gb'):
            self.assertIn(key, snap.json())
        self.assertIn('points', self.client.get('/api/system/history').json())
        procs = self.client.get('/api/system/processes', params={'limit': 3, 'sort': 'memory'})
        self.assertEqual(procs.status_code, 200)
        self.assertLessEqual(len(procs.json()['processes']), 3)

    def test_network_tool_offline_is_honest(self):
        res = self.client.get('/api/weather', params={'city': 'Lyon'}).json()
        self.assertFalse(res['ok'])
        self.assertTrue(res['error'])
        self.assertEqual(res['data'].get('offline'), True)

    def test_private_mode_blocks_panel_network_calls(self):
        self.client.patch('/api/settings', json={'mode': 'prive'}, headers=self.auth)
        res = self.client.get('/api/quakes').json()
        self.assertFalse(res['ok'])
        self.assertIn('Mode privé', res['summary'])

    def test_starter_tasks_and_memories(self):
        task = self.client.post('/api/tasks', json={'title': 'Tester le PC'}, headers=self.auth)
        self.assertEqual(task.status_code, 201)
        self.assertEqual(task.json()['status'], 'pending')
        patched = self.client.patch(f"/api/tasks/{task.json()['id']}", json={'status': 'done'}, headers=self.auth)
        self.assertEqual(patched.json()['status'], 'done')
        self.assertEqual(self.client.get('/api/tasks').json()[0]['id'], task.json()['id'])
        mem = self.client.post('/api/memories', json={'title': 'Projet Mars', 'detail': 'Sonde orbitale',
                                                      'category': 'projet'}, headers=self.auth)
        self.assertEqual(mem.status_code, 201)
        mid = mem.json()['id']
        self.assertEqual(self.client.get('/api/memories').json()[0]['id'], mid)
        hits = self.client.get('/api/memories/search', params={'q': 'Mars'}).json()
        self.assertEqual(hits[0]['memory']['id'], mid)
        graph = self.client.get('/api/memories/graph').json()
        self.assertEqual(set(graph), {'nodes', 'links', 'totals', 'groups', 'limit', 'tools', 'routines'})
        self.assertEqual(graph['totals']['persistent'] + graph['totals']['semantic'], 1)
        self.assertIn('system.snapshot', [t['name'] for t in graph['tools']])
        self.assertEqual(graph['routines'], [])
        self.assertEqual(self.client.get('/api/memories/graph', params={'limit': 1000}).json()['limit'], 1000)
        self.assertEqual(self.client.get('/api/memories/graph', params={'limit': 1001}).status_code, 422)
        upd = self.client.patch(f'/api/memories/{mid}', json={'pinned': True}, headers=self.auth)
        self.assertEqual(upd.status_code, 200)
        self.assertEqual(self.client.delete(f'/api/memories/{mid}', headers=self.auth).status_code, 204)
        self.assertEqual(self.client.delete(f'/api/memories/{mid}', headers=self.auth).status_code, 404)
        kinds = [e['kind'] for e in self.client.get('/api/events').json()]
        self.assertIn('memory.saved', kinds)
        self.assertIn('task.pending', kinds)
        self.assertIn('system.started', kinds)
        self.assertEqual(self.client.delete('/api/events', headers=self.auth).status_code, 204)
        self.assertEqual(self.client.get('/api/events').json(), [])

    def test_local_voice_route(self):
        from unittest import mock
        from aurora.providers import piper_voice
        with mock.patch.object(piper_voice, 'available', return_value=False):
            self.assertFalse(self.client.get('/api/tts').json()['available'])
            self.assertEqual(self.client.post('/api/tts', json={'text': 'Bonjour'}).status_code, 503)
        wav = b'RIFF\x24\x00\x00\x00WAVEfmt '
        with mock.patch.object(piper_voice, 'available', return_value=True), \
                mock.patch.object(piper_voice, 'synthesize', return_value=wav) as synth:
            res = self.client.post('/api/tts', json={'text': 'Bonjour, je suis AURORA.'})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers['content-type'], 'audio/wav')
            self.assertEqual(res.content, wav)
            synth.assert_called_once_with('Bonjour, je suis AURORA.', speed=None)
            self.client.post('/api/tts', json={'text': 'Bonjour', 'speed': 0.9})
            self.assertEqual(synth.call_args.kwargs, {'speed': 0.9})
            self.assertEqual(self.client.post('/api/tts', json={'text': 'Bonjour', 'speed': 3}).status_code, 422)
            self.assertEqual(self.client.post('/api/tts', json={'text': 'x' * 1501}).status_code, 422)
            self.assertEqual(self.client.post('/api/tts', json={'text': 'Bonjour'}, headers=NO_TOKEN).status_code, 401)

    def test_settings(self):
        current = self.client.get('/api/settings').json()
        self.assertEqual(current['mode'], 'equilibre')
        self.assertEqual(current['cloud_daily_budget_usd'], 1.0)
        self.assertEqual(current['default_city'], 'Paris')
        self.assertFalse(current['llm_router'])
        updated = self.client.patch('/api/settings', json={'mode': 'avance', 'default_city': 'Lyon'},
                                    headers=self.auth).json()
        self.assertEqual((updated['mode'], updated['default_city']), ('avance', 'Lyon'))
        self.assertEqual(self.client.patch('/api/settings', json={'mode': 'turbo'}, headers=self.auth).status_code,
                         422)
        self.assertEqual(self.client.patch('/api/settings', json={'allowed_roots': ['Z:/nexiste/pas']},
                                           headers=self.auth).status_code, 422)
        # 08/11/2026: AURORA opens the tab of the subject by itself, on by default, with a switch to turn it off.
        self.assertTrue(current['auto_open_tab'])
        off = self.client.patch('/api/settings', json={'auto_open_tab': False}, headers=self.auth)
        self.assertEqual(off.status_code, 200, off.text)
        self.assertFalse(off.json()['auto_open_tab'])
        self.assertFalse(self.client.get('/api/settings').json()['auto_open_tab'])

    def test_quality_bench_then_latest(self):
        self.assertIsNone(self.client.get('/api/quality/latest').json())
        self.assertEqual(self.client.post('/api/quality/bench', headers=NO_TOKEN).status_code, 401)
        res = self.client.post('/api/quality/bench', headers=self.auth)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn('rules', res.json())
        latest = self.client.get('/api/quality/latest').json()
        self.assertEqual(latest['generated_at'], res.json()['generated_at'])


class StreamTests(ApiTestCase):
    def test_sse_receives_events_and_unsubscribes(self):
        before = events.bus.subscriber_count
        with self.client.stream('GET', f'/api/stream?token={self.token}') as res:
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.headers['content-type'].startswith('text/event-stream'))
            lines = res.iter_lines()
            self.assertEqual(next(lines), 'retry: 3000')
            deadline = time.time() + 5
            while events.bus.subscriber_count <= before and time.time() < deadline:
                time.sleep(0.02)
            events.emit('system.started', 'Test SSE', subject_type='system', stage='system', payload={'n': 1})
            seen_ping, frame = False, None
            for line in lines:
                if line == ': ping':
                    seen_ping = True
                if line.startswith('data: '):
                    frame = line
                    break
            self.assertIsNotNone(frame)
            self.assertIn('"kind": "system.started"', frame)
            self.assertIn('"payload": {"n": 1}', frame)
            for line in lines:
                if line == ': ping':
                    seen_ping = True
                    break
            self.assertTrue(seen_ping)
        deadline = time.time() + 5
        while events.bus.subscriber_count > before and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(events.bus.subscriber_count, before)

    def test_sse_replays_missed_events(self):
        first = events.emit('system.started', 'Avant', subject_type='system', stage='system')
        missed = events.emit('system.started', 'Manqué', subject_type='system', stage='system')
        with self.client.stream('GET', f"/api/stream?token={self.token}&since_id={first['id']}") as res:
            frames = []
            for line in res.iter_lines():
                if line.startswith('id: '):
                    frames.append(int(line[4:]))
                if line == ': ping' or len(frames) >= 1:
                    break
        self.assertEqual(frames, [missed['id']])


class ConversationDeleteTests(ApiTestCase):
    """The bin icon of the conversation list: the conversation and its messages go, no copy is kept (the user's
    choice); its runs stay in the journal; refused while one of its requests is going on."""

    def test_delete_keeps_the_others_and_the_journal(self):
        from aurora import db
        from aurora.memory import store
        run = self.client.post('/api/ask', json={'text': 'Bonjour'}).json()
        conv_id = run['conversation_id']
        other = store.create_conversation('Autre')
        store.add_message(other['id'], 'user', 'Je reste là')
        res = self.client.delete(f'/api/conversations/{conv_id}')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {'id': conv_id, 'messages': 2})
        self.assertIsNone(store.get_conversation(conv_id))
        self.assertEqual(db.rows('SELECT id FROM messages WHERE conversation_id=?', (conv_id,)), [])
        self.assertEqual(len(store.recent_messages(other['id'])), 1)                        # the others stay
        self.assertEqual(self.client.get(f"/api/runs/{run['task_id']}").status_code, 200)   # journal kept
        self.assertFalse(list((self.env.dir / 'backups').glob('*.db')))                    # no copy, as asked
        self.assertIn('conversation.deleted', [e['kind'] for e in self.client.get('/api/events').json()])
        self.assertEqual(self.client.delete(f'/api/conversations/{conv_id}').status_code, 404)
        self.assertEqual(self.client.delete('/api/conversations/99999999999999999999').status_code, 404)
        self.assertEqual(self.client.delete(f"/api/conversations/{other['id']}", headers=NO_TOKEN).status_code, 401)

    def test_refused_while_one_of_its_requests_is_going_on(self):
        from aurora import db
        from aurora.memory import store
        conv = store.create_conversation('En cours')
        store.add_message(conv['id'], 'user', 'Attends')
        with db.connect() as conn:
            conn.execute("INSERT INTO tasks(title, status, kind, conversation_id) VALUES ('x', 'running', 'request', ?)",
                         (conv['id'],))
        res = self.client.delete(f"/api/conversations/{conv['id']}")
        self.assertEqual(res.status_code, 409)
        self.assertIn('encore en cours', res.json()['detail'])
        self.assertIsNotNone(store.get_conversation(conv['id']))
        with db.connect() as conn:
            conn.execute("UPDATE tasks SET status='done' WHERE conversation_id=?", (conv['id'],))
        self.assertEqual(self.client.delete(f"/api/conversations/{conv['id']}").status_code, 200)


if __name__ == '__main__':
    unittest.main()
