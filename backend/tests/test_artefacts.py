"""P2 « Les artefacts locaux »: an answer becomes a page (tasks/artefacts.py, tasks/artefact_html.py).

What is checked: the figures come from the tool results, never from the AI's text; the AI's text is marked
with its writer; every text is escaped and no link other than http(s) survives; a version is never
overwritten and the same data give the same version back; the routes need the token.
Temporary AURORA_HOME (TempEnv): neither the real base nor ~/.aurora/artefacts is ever opened.
"""
import json
import logging
import unittest
from unittest import mock

from pydantic import ValidationError

from aurora import config
from aurora.tasks import artefact_html, artefacts, runs
from tests.doubles_core import TempEnv

try:   # the HTTP part reuses the API test fixture
    from tests.test_api import NO_TOKEN, ApiTestCase
except Exception:  # pragma: no cover - fastapi missing
    ApiTestCase = None

DIAGNOSIS = {
    'status': 'attention',
    'findings': [{'id': 'cpu.sustained', 'severity': 'warning', 'title': 'Processeur chargé', 'detail': '72 % sur 60 s'}],
    'causes': [{'title': 'game.exe consomme 41 % du processeur', 'confidence': 'élevée'}],
    'suggestions': [{'title': 'Fermer game.exe', 'benefit': 'libère le processeur', 'risk': 'travail non enregistré',
                     'action': {'tool': 'process.terminate', 'label': 'Fermer game.exe'}}],
    'metrics': {'cpu_percent': 72.5, 'ram_percent': 61.0, 'disk_percent': 48.0, 'disk_free_gb': 210.4,
                'uptime_s': 7200, 'process_count': 231},
    'snapshot': {'ram_used_gb': 9.8, 'ram_total_gb': 16.0},
    'top_processes': [{'name': 'game.exe', 'pid': 4321, 'cpu_percent': 41.2, 'memory_mb': 2048.0},
                      {'name': '<b>evil</b>.exe', 'pid': 7, 'cpu_percent': 3.0, 'memory_mb': 12.0}],
    'history': [{'t': '2026-10-29T08:00:00Z', 'cpu': 70.0, 'ram': 60.0},
                {'t': '2026-10-29T08:00:02Z', 'cpu': 75.0, 'ram': 61.0},
                {'t': '2026-10-29T08:00:04Z', 'cpu': 72.5, 'ram': 61.0}],
}


def step(tool: str, data: dict, *, provenance: str = 'psutil', status: str = 'done', ok: bool = True, sources=None) -> dict:
    return {'stage': 'tool', 'status': status, 'tool': tool, 'finished_at': '2026-10-29T08:00:05.000Z',
            'result_json': json.dumps({'ok': ok, 'data': data, 'summary': f'résumé de {tool}', 'provenance': provenance,
                                       'sources': sources or []})}


def result_for(run_id: int, steps: list[dict], *, answer: str = 'Réponse.', origin=None, verification=None,
               status: str = 'done', **extra) -> dict:
    return {'task_id': run_id, 'conversation_id': None, 'status': status, 'answer': answer, 'steps': steps,
            'origin': origin, 'verification': verification, 'sources': [], 'memories_used': [], 'ui': None,
            'duration_ms': 1200.0, 'cost_usd': 0.0, 'shows_sensitive': False, **extra}


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def run_with(self, text: str, **fields) -> int:
        run = runs.create_run(text)
        if fields:
            runs.update_run(run['id'], **fields)
        return run['id']

    def test_the_figures_come_from_the_tools_never_from_the_ai_text(self):
        rid = self.run_with('Pourquoi mon PC rame ?')
        result = result_for(rid, [step('system.diagnose', DIAGNOSIS)], answer='Ton processeur est à 99 % <script>x</script>',
                            origin={'kinds': ['mesure'], 'written_by': 'local'},
                            verification={'ok': False, 'checks': [{'name': 'mesures', 'label': 'Chiffres mesurés', 'ok': False,
                                                                   'detail': '99 % absent des mesures'}]})
        page, key = artefacts.build_page(result, lambda _i: None)
        self.assertEqual((page.kind, key, page.status), ('diagnostic', 'diagnostic', 'attention'))
        stats = page.blocks[0]
        self.assertEqual([s.value for s in stats.items[:3]], [72.5, 61.0, 48.0])   # the tool's, not the AI's 99
        self.assertEqual(stats.items[0].tone, 'warn')
        self.assertEqual((stats.origin.tool, stats.origin.provenance), ('system.diagnose', 'psutil'))
        line = next(b for b in page.blocks if b.type == 'line')
        self.assertEqual([len(s.points) for s in line.series], [3, 3])
        note = next(b for b in page.blocks if b.type == 'note')
        self.assertEqual((note.written_by, note.verified), ('local', False))
        self.assertIn('99 % absent des mesures', note.warnings[0])
        html = artefact_html.render(page.model_dump(mode='json'), page_id='diagnostic-x', version=1, versions=1,
                                    created_at='2026-10-29T08:01:00Z')
        self.assertIn('72,5', html)
        self.assertIn('rédigé par l’IA locale', html)
        self.assertNotIn('<script', html)
        self.assertIn('&lt;b&gt;evil&lt;/b&gt;.exe', html)     # a process name is text, never markup
        self.assertIn("default-src 'none'", html)

    def test_a_search_page_keeps_web_links_only_and_each_source_once(self):
        rid = self.run_with('Cherche les découvertes sur Mars')
        first = {'query': 'Mars', 'engine': 'searxng', 'results': [
            {'title': 'NASA', 'url': 'https://nasa.gov/mars', 'domain': 'nasa.gov', 'extract': 'Eau', 'read': True},
            {'title': 'Piège', 'url': 'javascript:alert(1)', 'domain': 'x'},
            {'title': 'Trop long', 'url': 'https://x.fr/' + 'a' * 3000},              # relecture: never refuses the page
            {'title': 'Sans adresse'}, {'title': 'Sans adresse non plus'}]}
        second = {'query': 'Mars eau', 'engine': 'searxng', 'results': [
            {'title': 'NASA bis', 'url': 'https://nasa.gov/mars', 'domain': 'nasa.gov'}]}
        result = result_for(rid, [step('web.search', first, provenance='searxng'),
                                  step('web.search', second, provenance='searxng')],
                            answer='Voir [la NASA](https://nasa.gov/mars) et [ceci](javascript:alert(1)).',
                            origin={'kinds': ['source'], 'written_by': 'cloud'}, verification={'ok': True, 'checks': []})
        page, key = artefacts.build_page(result, lambda _i: None)
        self.assertEqual((page.kind, key), ('recherche', f'run:{rid}'))
        sources = next(b for b in page.blocks if b.type == 'sources')
        self.assertEqual([s.url for s in sources.items], ['https://nasa.gov/mars', '', '', '', ''])
        self.assertEqual(page.blocks[0].items[1].value, 5)                          # both results without address kept
        self.assertEqual(page.blocks[0].items[2].value, 1)                          # pages read
        html = artefact_html.render(page.model_dump(mode='json'), page_id='r', version=1, versions=1, created_at='')
        self.assertNotIn('javascript:', html.replace('[ceci](javascript:alert(1))', ''))
        self.assertIn('href="https://nasa.gov/mars" target="_blank" rel="noopener noreferrer nofollow"', html)
        self.assertIn('rédigé par Claude', html)

    def test_a_routine_page_reads_its_steps_in_the_journal(self):
        launch = runs.create_run('Lance la routine Matin', conversation_id=None)
        runs.update_run(launch['id'], intent='routine.run')
        one = runs.create_run('Quelle heure est-il ?', source='routine')
        two = runs.create_run('Météo à Paris', source='routine')
        later = runs.create_run('Lance la routine Matin')
        runs.update_run(later['id'], intent='routine.run')
        runs.create_run('Quelle heure est-il ?', source='routine')                   # the next launch's
        steps = {one['id']: result_for(one['id'], [step('time.now', {'iso': 'x'}, provenance='clock')], answer='Il est 8 h.',
                                       origin={'kinds': ['mesure']}),
                 two['id']: result_for(two['id'], [], status='error', answer='Météo indisponible.')}
        result = result_for(launch['id'], [], answer='Routine « Matin » : 3 étapes.',
                            ui={'routine': {'id': 4, 'name': 'Matin', 'steps': ['Quelle heure est-il ?', 'Météo à Paris',
                                                                                 'Quoi de neuf ?']}})
        self.assertEqual(artefacts.routine_step_runs(result, 3), [one['id'], two['id']])
        page, key = artefacts.build_page(result, steps.get)
        self.assertEqual((page.kind, key), ('routine', 'routine:4'))
        table = next(b for b in page.blocks if b.type == 'table')
        self.assertEqual([r[2] for r in table.rows], ['terminée', 'en erreur', 'pas encore lancée'])
        self.assertEqual(table.rows[0][3], 'Date et heure')
        self.assertEqual([s.value for s in page.blocks[0].items[:4]], [3, 1, 1, 0])
        # the same journal gives the same version; a step that ends since gives the next one
        self.assertEqual(artefacts.save(page, key)['version'], 1)
        self.assertFalse(artefacts.save(artefacts.build_page(result, steps.get)[0], key)['created'])
        steps[two['id']] = result_for(two['id'], [], answer='Il pleut.')
        self.assertEqual(artefacts.save(artefacts.build_page(result, steps.get)[0], key)['version'], 2)
        # relecture: a step not over has no duration yet (RunResult's elapsed time grows at each reading)
        for elapsed in (100.0, 1600.0):
            steps[two['id']] = result_for(two['id'], [], status='waiting_permission', answer='Accord ?',
                                          duration_ms=elapsed)
            again = artefacts.build_page(result, steps.get)[0]
            self.assertEqual(artefacts.save(again, key)['version'], 3)
        self.assertEqual(next(b for b in again.blocks if b.type == 'table').rows[1][4], None)
        self.assertEqual(again.blocks[0].items[4].value, 1.2)                       # the finished step only

    def test_any_answer_makes_a_page_and_a_secret_of_the_request_is_masked(self):
        rid = self.run_with('Ma carte 4111 1111 1111 1111 expire quand ?')
        result = result_for(rid, [step('calc.evaluate', {'value': 2})], answer='Je ne sais pas.',
                            origin={'kinds': ['modele'], 'written_by': 'local', 'unverified': ['2027']})
        page, key = artefacts.build_page(result, lambda _i: None)
        self.assertEqual((page.kind, key), ('reponse', f'run:{rid}'))
        self.assertTrue(page.sensitive)
        self.assertNotIn('4111 1111 1111 1111', page.source.request)
        self.assertNotIn('4111 1111 1111 1111', page.title)
        html = artefact_html.render(page.model_dump(mode='json'), page_id='p', version=1, versions=1, created_at='')
        self.assertIn('contient des données sensibles', html)
        self.assertIn('non vérifié : 2027', html)

    def test_versions_are_kept_and_the_same_data_give_the_same_version(self):
        rid = self.run_with('Diagnostic')
        make = lambda data: artefacts.make_from_run(rid, lambda _i: result_for(rid, [step('system.diagnose', data)]))
        first = make(DIAGNOSIS)
        self.assertEqual((first['version'], first['created'], first['artefact']['versions']), (1, True, 1))
        self.assertEqual((make(DIAGNOSIS)['version'], make(DIAGNOSIS)['created']), (1, False))
        # the same request laid out differently (a newer AURORA) keeps its version: its data did not change
        self.assertEqual(make({**DIAGNOSIS, 'metrics': {**DIAGNOSIS['metrics'], 'cpu_percent': 12.0}})['version'], 1)
        other = self.run_with('Diagnostic plus tard')
        changed = artefacts.make_from_run(other, lambda _i: result_for(other, [step('system.diagnose', {
            **DIAGNOSIS, 'metrics': {**DIAGNOSIS['metrics'], 'cpu_percent': 12.0}})]))
        self.assertEqual((changed['version'], changed['created']), (2, True))
        page_id = first['artefact']['id']
        folder = config.artefacts_dir() / page_id
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['meta.json', 'v1.json', 'v2.json'])
        detail = artefacts.get_page(page_id)
        self.assertEqual([v['version'] for v in detail['history']], [2, 1])
        self.assertEqual(artefacts.read_version(page_id, 1)['page']['blocks'][0]['items'][0]['value'], 72.5)
        self.assertEqual(artefacts.read_version(page_id)['page']['blocks'][0]['items'][0]['value'], 12.0)
        self.assertIsNone(artefacts.read_version(page_id, 9))
        self.assertEqual([p['id'] for p in artefacts.list_pages()], [page_id])
        with self.assertRaises(FileExistsError):                                    # a version is never overwritten
            with open(folder / 'v1.json', 'x'):
                pass
        # relecture: a v3.json left without its line in meta.json (service stopped) is skipped, never blocking
        (folder / 'v3.json').write_text('{}', encoding='utf-8')
        later = self.run_with('Diagnostic encore plus tard')
        third = artefacts.make_from_run(later, lambda _i: result_for(later, [step('system.diagnose', {
            **DIAGNOSIS, 'metrics': {**DIAGNOSIS['metrics'], 'cpu_percent': 30.0}})]))
        self.assertEqual(third['version'], 4)
        self.assertEqual((folder / 'v3.json').read_text(encoding='utf-8'), '{}')
        self.assertTrue(artefacts.delete_page(page_id))
        self.assertFalse(artefacts.delete_page(page_id))
        self.assertEqual(artefacts.list_pages(), [])

    def test_refusals(self):
        rid = self.run_with('Météo')
        with self.assertRaises(artefacts.NotFinished):
            artefacts.make_from_run(rid, lambda _i: result_for(rid, [], status='running'))
        with self.assertRaisesRegex(artefacts.NothingToShow, 'pas abouti'):
            artefacts.make_from_run(rid, lambda _i: result_for(rid, [], status='error'))
        self.assertIsNone(artefacts.make_from_run(999, lambda _i: None))
        for bad in ('../x', 'A', '', 'x' * 65, 'a/b', 'abc\n'):
            self.assertIsNone(artefacts.get_page(bad), bad)
            self.assertIsNone(artefacts.read_version(bad), bad)
            self.assertFalse(artefacts.delete_page(bad), bad)
        with self.assertRaises(ValidationError):
            artefacts.TableBlock(title='t', columns=[artefacts.Column(label='a')], rows=[['x', 'y']])

    def test_the_bench_page(self):
        self.assertIsNone(artefacts.make_from_bench())
        report = {'generated_at': '2026-10-28T03:34:00Z', 'corpus': {'n': 254, 'intents': 22},
                  'rules': {'correct': 240, 'n': 254, 'accuracy': 0.9449,
                            'by_tag': {'normal': {'correct': 150, 'n': 152, 'accuracy': 0.9868}},
                            'by_intent': {'web.search': {'correct': 9, 'n': 12}},
                            'confusions': [{'expected': 'system.processes', 'got': 'system.status', 'count': 2,
                                            'examples': ['Montre les processus']}],
                            'failures': [{'text': 'Montre les processus', 'expected': 'system.processes',
                                          'got': 'system.status', 'tag': 'normal'}]},
                  'latency_rules': {'p50_ms': 0.42, 'p95_ms': 1.1}, 'determinism': {'runs': 2, 'mismatches': 0},
                  'recommendation': {'reason': 'Garder les règles seules.'}}
        config.bench_latest_path().parent.mkdir(parents=True, exist_ok=True)
        config.bench_latest_path().write_text(json.dumps(report), encoding='utf-8')
        made = artefacts.make_from_bench()
        self.assertEqual((made['artefact']['kind'], made['version']), ('banc', 1))
        stored = artefacts.read_version(made['artefact']['id'])['page']
        self.assertAlmostEqual(stored['blocks'][0]['items'][0]['value'], 94.49)
        html = artefact_html.render(stored, page_id=made['artefact']['id'], version=1, versions=1, created_at='')
        self.assertIn('94,5', html)
        self.assertIn('Garder les règles seules.', html)


class MarkdownTests(unittest.TestCase):
    def test_only_the_safe_subset_becomes_markup(self):
        html = artefact_html.markdown('# Titre\n**gras** et `code` <img src=x onerror=y>\n\n- un\n- deux\n\n'
                                      '| a | b |\n|---|---|\n| 1 | <i>2</i> |\n\n```\n<script>\n```\n'
                                      '[lien](https://exemple.fr/?a=1&b="2") [mal](javascript:x)')
        self.assertIn('<p class="md-h">Titre</p>', html)
        self.assertIn('<strong>gras</strong>', html)
        self.assertIn('<ul><li>un</li><li>deux</li></ul>', html)
        self.assertIn('<td>&lt;i&gt;2&lt;/i&gt;</td>', html)
        self.assertIn('<pre><code>&lt;script&gt;</code></pre>', html)
        self.assertNotIn('<img', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('href="https://exemple.fr/?a=1&amp;b=&quot;2&quot;"', html)
        numbered = artefact_html.markdown('1. **Un**\n- détail\n2. **Deux**')
        self.assertEqual(numbered, '<ol><li><strong>Un</strong></li></ol><ul><li>détail</li></ul>'
                                   '<ol start="2"><li><strong>Deux</strong></li></ol>')


if ApiTestCase is not None:
    class ArtefactApiTests(ApiTestCase):
        def test_routes_require_the_token(self):
            for method, path in (('get', '/api/artefacts'), ('get', '/api/artefacts/x'), ('get', '/api/artefacts/x/html'),
                                 ('post', '/api/artefacts/from-run/1'), ('post', '/api/artefacts/bench'),
                                 ('delete', '/api/artefacts/x')):
                self.assertEqual(getattr(self.client, method)(path, headers=NO_TOKEN).status_code, 401, path)

        def test_a_diagnosis_becomes_a_page_over_http(self):
            run = self.client.post('/api/tools/diagnostic').json()['run']
            self.assertEqual(self.client.post('/api/artefacts/from-run/99999').status_code, 404)
            self.assertEqual(self.client.post('/api/artefacts/from-run/9223372036854775808').status_code, 404)
            made = self.client.post(f"/api/artefacts/from-run/{run['task_id']}")
            self.assertEqual(made.status_code, 200, made.text)
            body = made.json()
            self.assertEqual((body['artefact']['kind'], body['version'], body['created']), ('diagnostic', 1, True))
            self.assertFalse(self.client.post(f"/api/artefacts/from-run/{run['task_id']}").json()['created'])
            page_id = body['artefact']['id']
            self.assertEqual([p['id'] for p in self.client.get('/api/artefacts').json()], [page_id])
            self.assertEqual(self.client.get(f'/api/artefacts/{page_id}').json()['history'][0]['version'], 1)
            html = self.client.get(f'/api/artefacts/{page_id}/html')
            self.assertEqual(html.status_code, 200)
            self.assertTrue(html.headers['content-type'].startswith('text/html'))
            self.assertIn('sandbox', html.headers['content-security-policy'])
            self.assertIn('—', html.text)                      # the fake diagnosis has no metrics: nothing invented
            self.assertEqual(self.client.get(f'/api/artefacts/{page_id}/html?version=7').status_code, 404)
            self.assertEqual(self.client.delete(f'/api/artefacts/{page_id}').status_code, 204)
            self.assertEqual(self.client.get(f'/api/artefacts/{page_id}').status_code, 404)

        def test_unfinished_and_empty_requests_are_refused_in_french(self):
            with mock.patch.object(self.orch, 'run_result', return_value=result_for(5, [], status='running')):
                refused = self.client.post('/api/artefacts/from-run/5')
            self.assertEqual(refused.status_code, 409)
            self.assertIn('pas terminée', refused.json()['detail'])
            self.assertEqual(self.client.post('/api/artefacts/bench').status_code, 404)


if __name__ == '__main__':
    unittest.main()
