"""A4 (reviews/etapes-restantes.md, M1 of the report): the answer checks become « mods » with a level.

Form of Athéna's server/services/modsAtelier.ts (05/10/2026): a name, what it guards, a level. « alerter »
keeps the behaviour so far (the answer is shown with a note); « bloquer » withholds the AI's text and shows
what the tools returned themselves. check() is unchanged: the levels are applied to its result.
Temporary AURORA_HOME (TempEnv): the real base is never opened.
"""
import logging
import re
import unittest
from pathlib import Path

from aurora import config, db
from aurora.core import verify
from aurora.tasks import events
from tests import test_origin
from tests.doubles_core import FakeDecision, FakeOllama, FakeTools, TempEnv, build, snapshot_response
from tests.test_orchestrator import CoreTestCase

try:   # the HTTP part reuses the API test fixture
    from tests.test_api import ApiTestCase
except Exception:  # pragma: no cover - fastapi missing
    ApiTestCase = None


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


SNAPSHOT = {'status': 'done', 'stage': 'tool', 'tool': 'system.snapshot', 'idx': 0, 'requires_permission': False,
            'permission_id': None, 'result': {'ok': True, 'data': {'cpu_percent': 23.5, 'ram_percent': 61},
                                              'summary': 'CPU 23,5 %, RAM 61 %.', 'provenance': 'psutil'}}
WRONG = 'Je n’ai pas accès à Internet. J’ai supprimé 3 fichiers. CPU à 80 % [2].'


def checked(answer: str = WRONG, steps=(SNAPSHOT,), mods=None, **given) -> dict:
    """``given``: what check() also takes (22/11/2026: ``documents``, tests/test_sources_document.py)."""
    return verify.apply_levels(verify.check(answer=answer, steps=list(steps), caps={'internet': True},
                                            mode='equilibre', permissions={}, llm_text_used=True, **given), mods)


class ModsTests(unittest.TestCase):
    def test_every_check_is_a_described_mod(self):
        names = [c['name'] for c in checked()['checks']]
        self.assertEqual(sorted(names), sorted(verify.MODS))
        for name, mod in verify.MODS.items():
            with self.subTest(name):
                self.assertTrue(mod['name'] and mod['description'])

    def test_the_interface_shows_the_same_mods(self):
        """frontend/src/components/chat/labels.ts VERIFY_MOD_LABELS mirrors MODS (Paramètres › Qualité)."""
        labels = Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'components' / 'chat' / 'labels.ts'
        if not labels.exists():
            self.skipTest('interface absente')
        block = labels.read_text(encoding='utf-8').split('VERIFY_MOD_LABELS', 1)[1].split('};', 1)[0]
        self.assertEqual(re.findall(r'^\s*(\w+): \{ name:', block, re.M), list(verify.MODS))
        for name, mod in verify.MODS.items():
            with self.subTest(name):
                self.assertIn(f"{name}: {{ name: '{mod['name']}', description: '{mod['description']}' }}", block)

    def test_levels_default_to_alert_and_ignore_what_is_unknown(self):
        self.assertEqual(set(verify.levels().values()), {'alerter'})
        chosen = verify.levels({'mesures': 'bloquer', 'actions': 'supprimer', 'inconnu': 'bloquer'})
        self.assertEqual(chosen['mesures'], 'bloquer')
        self.assertEqual(chosen['actions'], 'alerter')
        self.assertNotIn('inconnu', chosen)
        self.assertEqual(verify.levels('pas un dictionnaire'), verify.levels())

    def test_only_a_failed_check_set_to_block_blocks(self):
        alert = checked()
        self.assertEqual(alert['blocked'], [])                                 # the behaviour so far
        self.assertFalse(alert['ok'])
        blocked = checked(mods={'mesures': 'bloquer', 'provenance': 'bloquer'})
        self.assertEqual(blocked['blocked'], ['mesures'])                     # provenance passed: it does not block
        by_name = {c['name']: c for c in blocked['checks']}
        self.assertEqual((by_name['mesures']['level'], by_name['provenance']['level']), ('bloquer', 'bloquer'))
        self.assertEqual(by_name['mesures']['label'], verify.MODS['mesures']['name'])
        self.assertEqual(checked('Le CPU est à 23,5 %.', mods={'mesures': 'bloquer'})['blocked'], [])

    def test_the_withheld_answer_says_why_and_shows_the_tools_data(self):
        result = checked(mods={'mesures': 'bloquer', 'actions': 'bloquer'})
        text = verify.withheld_answer(result, [SNAPSHOT, SNAPSHOT])
        self.assertTrue(text.startswith('Réponse de l’IA retenue par la vérification AURORA : '))
        self.assertIn('Les chiffres viennent des mesures — Valeurs absentes des données mesurées : 3 fichiers, 80 %', text)
        self.assertIn('Pas d’action annoncée sans l’avoir faite', text)
        self.assertEqual(text.count('- CPU 23,5 %, RAM 61 %.'), 1)             # each tool summary once
        self.assertNotIn('supprimé 3 fichiers', text)                          # nothing of the AI's text
        alone = verify.withheld_answer(checked(steps=(), mods={'actions': 'bloquer'}), [])
        self.assertIn('Aucun outil n’a fourni de donnée', alone)


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def test_default_partial_update_and_refusals(self):
        self.assertEqual(config.load_settings()['verify_mods'], verify.levels())
        config.update_settings({'verify_mods': {'mesures': 'bloquer'}})
        config.update_settings({'verify_mods': {'actions': 'bloquer'}})        # a partial update keeps the first
        saved = config.load_settings()['verify_mods']
        self.assertEqual((saved['mesures'], saved['actions'], saved['sources']), ('bloquer', 'bloquer', 'alerter'))
        for bad in ({'inconnu': 'bloquer'}, {'mesures': 'désactivé'}, ['mesures']):
            with self.subTest(bad), self.assertRaises(ValueError):
                config.update_settings({'verify_mods': bad})
        self.assertEqual(config.load_settings()['verify_mods'], saved)          # a refusal changes nothing

    def test_a_damaged_stored_value_falls_back_to_alert(self):
        db.set_setting('verify.mods', {'mesures': 'bloquer', 'actions': 42, 'autre': 'bloquer'})
        self.assertEqual(config.load_settings()['verify_mods'], {**verify.levels(), 'mesures': 'bloquer'})


class BlockFlowTests(CoreTestCase):
    """A real run: an AI answer citing a CPU value that was never measured (as test_invented_number_fails_verification)."""

    async def ask(self, reply: str) -> dict:
        tools = FakeTools({'system.snapshot': snapshot_response,
                           'system.processes': {'ok': True, 'data': {'processes': []}, 'summary': 'Aucun processus lourd.',
                                                'provenance': 'psutil'},
                           'system.diagnose': {'ok': True, 'data': {'causes': []}, 'summary': 'RAS.',
                                               'provenance': 'psutil'}})
        orch, *_ = build(FakeDecision('system.diagnose'), tools=tools, ollama=FakeOllama(reply=reply))
        return await orch.handle_request('Pourquoi mon PC rame ?')

    async def test_a_blocking_mod_withholds_the_ai_text(self):
        config.update_settings({'verify_mods': {'mesures': 'bloquer'}})
        result = await self.ask('Votre processeur est à 97 % et la RAM à 49 %.')
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['verification']['blocked'], ['mesures'])
        self.assertTrue(result['answer'].startswith('Réponse de l’IA retenue par la vérification AURORA'))
        self.assertIn('- RAS.', result['answer'])                              # what the tools returned
        self.assertNotIn('97 %', result['answer'].split('Ce que les outils')[1])
        self.assertIn('97 %', result['withheld_answer'])                      # kept apart, viewable
        self.assertEqual((result['origin']['kinds'], result['origin']['written_by']), (['mesure'], None))
        failed = [e for e in events.list_events(limit=200, run_id=result['task_id']) if e['kind'] == 'verify.failed']
        self.assertEqual(failed[0]['label'], 'Vérification : réponse de l’IA retenue')
        self.assertEqual(failed[0]['payload']['blocked'], ['mesures'])
        content = db.rows("SELECT content FROM messages WHERE run_id=? AND role='assistant'", (result['task_id'],))
        self.assertEqual(content[-1]['content'], result['answer'])            # the history shows the same
        self.assertEqual(test_origin.OriginFlowTests.stored_origin(self, result['task_id']), result['origin'])

    async def test_alert_level_keeps_the_behaviour_so_far(self):
        result = await self.ask('Votre processeur est à 97 % et la RAM à 49 %.')
        self.assertEqual(result['verification']['blocked'], [])
        self.assertIn('97 %', result['answer'])
        self.assertIn('Vérification AURORA', result['answer'])
        self.assertIsNone(result['withheld_answer'])
        self.assertEqual({c['level'] for c in result['verification']['checks']}, {'alerter'})

    async def test_a_passing_answer_is_not_blocked(self):
        config.update_settings({'verify_mods': {name: 'bloquer' for name in verify.MODS}})
        result = await self.ask('Rien d’anormal dans les mesures.')
        self.assertTrue(result['verification']['ok'], result['verification'])
        self.assertIn('Rien d’anormal', result['answer'])
        self.assertIsNone(result['withheld_answer'])


if ApiTestCase is not None:
    class ModsApiTests(ApiTestCase):
        def test_levels_are_read_and_set_over_http(self):
            self.assertEqual(self.client.get('/api/settings').json()['verify_mods'], verify.levels())
            res = self.client.patch('/api/settings', json={'verify_mods': {'consentement': 'bloquer'}})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()['verify_mods']['consentement'], 'bloquer')
            bad = self.client.patch('/api/settings', json={'verify_mods': {'consentement': 'ignorer'}})
            self.assertEqual(bad.status_code, 422)
            self.assertIn('alerter', bad.json()['detail'])


if __name__ == '__main__':
    unittest.main()
