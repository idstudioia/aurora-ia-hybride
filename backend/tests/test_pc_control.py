"""Souris et clavier (9/11/2026) : tools/pc_control.py, l'extraction du geste, le routeur, le planificateur et
le parcours complet par l'orchestrateur (aperçu, carte d'accord, geste). Demande d’ID Studio IA : « fais-moi le contrôle
directement par la souris et le clavier ».

Aucun test ne bouge la vraie souris : ``pc_control.BACKEND`` est remplacé par ``FakeInput`` (un faux Windows qui
note les gestes), et chaque test part d'une base temporaire (TempEnv), donc d'une instance d'essai.
"""
from __future__ import annotations

import logging
import unittest
from unittest import mock

from aurora import config
from aurora.core import planner as planner_mod
from aurora.core.normalize import extract_gesture
from aurora.security import consent
from aurora.tasks import events
from aurora.tools import pc_control
from aurora.tools.base import run_tool
from tests.doubles_core import FakeClaude, FakeDecision, FakeEngine, FakeOllama, FakeRouter, TempEnv, make_caps

WINDOWS = (
    {'hwnd': 101, 'title': 'Sans titre - Bloc-notes', 'class': 'Notepad', 'pid': 11, 'exe': 'notepad.exe',
     'rect': (0, 0, 800, 600), 'minimized': False},
    {'hwnd': 102, 'title': 'Windows PowerShell', 'class': 'CASCADIA_HOSTING_WINDOW_CLASS', 'pid': 12,
     'exe': 'WindowsTerminal.exe', 'rect': (800, 0, 1600, 600), 'minimized': False},
    {'hwnd': 103, 'title': 'AURORA — Assistant IA hybride - Google Chrome', 'class': 'Chrome_WidgetWin_1', 'pid': 13,
     'exe': 'chrome.exe', 'rect': (0, 600, 800, 1080), 'minimized': False},
    {'hwnd': 104, 'title': 'main.py - Visual Studio Code', 'class': 'Chrome_WidgetWin_1', 'pid': 14, 'exe': 'Code.exe',
     'rect': (800, 600, 1600, 1080), 'minimized': False},
    {'hwnd': 105, 'title': 'Claude', 'class': 'Chrome_WidgetWin_1', 'pid': 15, 'exe': 'claude.exe',
     'rect': (1600, 0, 1920, 1080), 'minimized': False},
)


class FakeInput:
    """A fake Windows (screen 1920 × 1080, five windows side by side): notes every gesture, never moves anything."""

    def __init__(self):
        self.windows = [dict(w) for w in WINDOWS]
        self.done: list[tuple] = []

    def acts(self) -> list[tuple]:
        return [d for d in self.done if d[0] != 'front']

    def screen_size(self):
        return 1920, 1080

    def cursor(self):
        return 300, 200

    def open_windows(self):
        return [dict(w) for w in self.windows]

    def _get(self, hwnd):
        return next((w for w in self.windows if w['hwnd'] == hwnd), None)

    def window_info(self, hwnd):
        return dict(self._get(hwnd) or {})

    def window_at(self, x, y):
        for w in self.windows:
            left, top, right, bottom = w['rect']
            if left <= x < right and top <= y < bottom:
                return dict(w)
        return None

    def exists(self, hwnd):
        return self._get(hwnd) is not None

    def bring_to_front(self, hwnd):
        self.done.append(('front', hwnd))
        return True

    def move_to(self, x, y):
        self.done.append(('move', x, y))

    def press_buttons(self, button, clicks):
        self.done.append(('click', button, clicks))

    def roll_wheel(self, notches):
        self.done.append(('wheel', notches))

    def press_keys(self, names):
        self.done.append(('keys', list(names)))

    def type_text(self, text):
        self.done.append(('type', text))


class WithFakeInput(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.fake = FakeInput()
        self.patch = mock.patch.object(pc_control, 'BACKEND', self.fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.__exit__(None, None, None)

    def plan(self, **params) -> dict:
        return pc_control.build_plan(pc_control.GestureParams(**params))

    def refused(self, **params) -> str:
        with self.assertRaises(pc_control.ToolError) as caught:
            self.plan(**params)
        return str(caught.exception)


class KeyTests(unittest.TestCase):
    def test_closed_list_of_keys(self):
        self.assertEqual(pc_control.parse_keys('Ctrl + S'), (['ctrl', 's'], ''))
        self.assertEqual(pc_control.parse_keys('alt f4'), (['alt', 'f4'], ''))
        self.assertEqual(pc_control.parse_keys('la touche Entrée'), (['entree'], ''))
        self.assertEqual(pc_control.parse_keys('maj+ctrl+tab'), (['ctrl', 'maj', 'tab'], ''))
        self.assertEqual(pc_control.key_label(['ctrl', 's']), 'Ctrl+S')
        self.assertEqual(pc_control.key_effect(['ctrl', 's']), 'enregistre')

    def test_refused_keys(self):
        for spec, why in (('la touche Windows', 'Windows'), ('ctrl alt suppr', 'interdit'), ('ctrl maj echap', 'interdit'),
                          ('a', 'tape dans'), ('ctrl s v', 'seule touche'), ('hyperespace', 'inconnue'), ('', 'Aucune')):
            names, reason = pc_control.parse_keys(spec)
            self.assertEqual(names, [], spec)
            self.assertIn(why, reason, spec)


class ExtractAndRouteTests(unittest.TestCase):
    def test_gesture_read_from_the_raw_text(self):
        cases = {
            'clique en 640, 400': {'gesture': 'click', 'x': 640, 'y': 400},
            'fais un clic droit en 300 x 500': {'gesture': 'right_click', 'x': 300, 'y': 500},
            'double-clique en 100,200 dans Bloc-notes': {'gesture': 'double_click', 'x': 100, 'y': 200,
                                                          'window': 'Bloc-notes'},
            'tape dans Bloc-notes : Bonjour, ça va ?': {'gesture': 'type', 'text': 'Bonjour, ça va ?',
                                                         'window': 'Bloc-notes'},
            'fais Ctrl+S dans Bloc-notes': {'gesture': 'key', 'keys': 'Ctrl+S', 'window': 'Bloc-notes'},
            'fais défiler vers le bas de 5 crans dans Chrome': {'gesture': 'scroll', 'direction': 'bas', 'amount': 5,
                                                                 'window': 'Chrome'},
            'déplace la souris en 50, 60': {'gesture': 'move', 'x': 50, 'y': 60},
            'où est la souris ?': {'gesture': 'position'},
        }
        for text, wanted in cases.items():
            got = extract_gesture(text)['gesture']
            for key, value in wanted.items():
                self.assertEqual(got.get(key), value, f'{text} [{key}]')

    def test_a_named_target_is_kept_apart(self):
        got = extract_gesture('clique sur le bouton Enregistrer dans Bloc-notes')['gesture']
        self.assertEqual((got['target'], got['window']), ('Enregistrer', 'Bloc-notes'))

    def test_router(self):
        from aurora.core.router import get_engine
        engine = get_engine()
        for text in ('clique en 640, 400', 'fais un clic droit en 300 x 500', 'tape dans Bloc-notes : bonjour',
                     'appuie sur Entrée dans Bloc-notes', 'fais Ctrl+S dans Bloc-notes', 'où est la souris ?',
                     'déplace la souris en 50, 60', 'peux-tu cliquer en 10, 20'):
            self.assertEqual(engine.decide_fast(text).intent, 'pc.control', text)
        for text in ('écris dans ta mémoire que j aime le thé', 'écris un poème sur la mer',
                     'tape une lettre de motivation', 'quelle est la position de la Lune ?',
                     'fais défiler les actualités', 'retiens que mon chat s appelle Tom'):
            self.assertNotEqual(engine.decide_fast(text).intent, 'pc.control', text)


class PlannerTests(unittest.TestCase):
    def test_gesture_steps_are_a_read_only_preview_then_a_permissioned_gesture(self):
        steps = planner_mod.Planner().gesture_steps({'gesture': 'click', 'x': 1, 'y': 2, 'window': None})
        self.assertEqual([s['tool'] for s in steps], ['pc.gesture', 'pc.gesture'])
        self.assertTrue(steps[0]['params']['dry_run'])
        self.assertFalse(steps[0]['requires_permission'])
        self.assertNotIn('dry_run', steps[1]['params'])
        self.assertNotIn('window', steps[1]['params'])   # empty values are not passed
        self.assertTrue(steps[1]['requires_permission'])
        self.assertEqual(steps[1]['derive'], 'gesture_from_dry_run')

    def test_build_from_the_intent(self):
        def tools(slots: dict) -> list[str]:
            steps = planner_mod.Planner().build({'intent': 'pc.control', 'slots': slots}, {'engine': 'tool'},
                                                text='x', settings=config.default_settings())
            return [s['tool'] for s in steps if s['stage'] == 'tool']   # compose and verify always follow
        self.assertEqual(tools({'gesture': {'gesture': 'position'}}), ['pc.pointer'])
        self.assertEqual(tools({'gesture': {'gesture': 'key', 'keys': 'entree', 'window': 'Bloc-notes'}}),
                         ['pc.gesture', 'pc.gesture'])
        for slots in ({'gesture': None}, {'gesture': {'gesture': 'click', 'target': 'OK'}}):
            self.assertEqual(tools(slots), [], slots)

    def test_help_when_the_gesture_is_not_understood(self):
        from aurora.core import compose
        self.assertEqual(compose.pc_control({'gesture': None}, None, None, None, permission_status=None),
                         compose.PC_CONTROL_HELP)


class PlanTests(WithFakeInput):
    def test_click_in_a_named_window(self):
        plan = self.plan(gesture='click', x=100, y=200, window='Bloc-notes')   # alias of notepad.exe
        self.assertEqual(plan['window']['exe'], 'notepad.exe')
        self.assertEqual((plan['x'], plan['y'], plan['button'], plan['clicks']), (100, 200, 'gauche', 1))
        self.assertIn('Cliquer', plan['does'])
        self.assertEqual(len(plan['fingerprint']), 24)
        self.assertEqual(self.fake.done, [])   # building the card reads only

    def test_window_under_the_point_when_none_is_named(self):
        self.assertEqual(self.plan(gesture='move', x=10, y=10)['window']['hwnd'], 101)

    def test_fingerprint_follows_the_gesture(self):
        a = self.plan(gesture='type', text='bonjour', window='Bloc-notes')['fingerprint']
        self.assertEqual(a, self.plan(gesture='type', text='bonjour', window='Bloc-notes')['fingerprint'])
        self.assertNotEqual(a, self.plan(gesture='type', text='bonsoir', window='Bloc-notes')['fingerprint'])

    def test_protected_windows_refused_even_with_accord(self):
        self.assertIn('terminal', self.refused(gesture='click', x=900, y=100))
        self.assertIn('terminal', self.refused(gesture='type', text='dir', window='PowerShell'))
        self.assertIn('AURORA', self.refused(gesture='click', x=100, y=700))
        self.assertIn('Claude', self.refused(gesture='click', x=1700, y=100))
        self.assertIn('éditeur de code', self.refused(gesture='type', text='x', window='Visual Studio Code'))
        self.assertEqual(self.plan(gesture='click', x=900, y=700)['window']['exe'], 'Code.exe')   # a click is allowed

    def test_impossible_gestures(self):
        self.assertIn('hors de l’écran', self.refused(gesture='click', x=5000, y=10))
        self.assertIn('n’est pas dans la fenêtre', self.refused(gesture='click', x=900, y=100, window='Bloc-notes'))
        self.assertIn('Position manquante', self.refused(gesture='click'))
        self.assertIn('Dites dans quelle fenêtre', self.refused(gesture='type', text='bonjour'))
        self.assertIn('caractère de contrôle', self.refused(gesture='type', text='a\x1bb', window='Bloc-notes'))
        self.assertIn('Aucune fenêtre ouverte', self.refused(gesture='type', text='x', window='Photoshop'))
        self.fake.windows.append({**WINDOWS[0], 'hwnd': 106, 'title': 'notes.txt - Bloc-notes'})
        self.assertIn('Plusieurs fenêtres', self.refused(gesture='type', text='x', window='Bloc-notes'))


class ToolTests(WithFakeInput):
    def _grant(self, fingerprint: str) -> int:
        item = consent.create_request(task_id=None, step_id=None, action='pc.control', title='Geste',
                                      preview={'fingerprint': fingerprint})
        consent.decide(int(item['id']), 'grant')
        return int(item['id'])

    async def gesture(self, **params):
        return await run_tool('pc.gesture', params)

    async def test_disabled_by_default_never_acts(self):
        res = await self.gesture(gesture='click', x=10, y=10, dry_run=True)
        self.assertFalse(res.ok)
        self.assertTrue(res.data['disabled'])
        self.assertIn('désactivés', res.summary)
        self.assertEqual(self.fake.done, [])

    async def test_only_the_gesture_of_the_granted_card(self):
        config.update_settings({'pc_control_enabled': True})
        dry = await self.gesture(gesture='type', text='bonjour', window='Bloc-notes', dry_run=True)
        self.assertTrue(dry.ok, dry.summary)
        fp = dry.data['fingerprint']
        no_card = await self.gesture(gesture='type', text='bonjour', window='Bloc-notes')
        self.assertFalse(no_card.ok)
        self.assertIn('Autorisation absente', no_card.summary)
        other = self._grant('0' * 24)   # granted, but for another gesture
        res = await self.gesture(gesture='type', text='bonjour', window='Bloc-notes', permission_id=other)
        self.assertFalse(res.ok)
        changed = await self.gesture(gesture='type', text='bonsoir', window='Bloc-notes', approved_fingerprint=fp,
                                     permission_id=self._grant(fp))
        self.assertFalse(changed.ok)
        self.assertIn('changé', changed.summary)
        self.assertEqual(self.fake.done, [])

    async def test_trial_instance_does_nothing(self):
        config.update_settings({'pc_control_enabled': True})
        fp = (await self.gesture(gesture='click', x=10, y=10, dry_run=True)).data['fingerprint']
        res = await self.gesture(gesture='click', x=10, y=10, approved_fingerprint=fp, permission_id=self._grant(fp))
        self.assertFalse(res.ok)
        self.assertIn('Instance d’essai', res.summary)
        self.assertEqual(self.fake.done, [])

    async def test_granted_gesture_is_done_once_checked(self):
        config.update_settings({'pc_control_enabled': True})
        with mock.patch.object(pc_control, 'trial_instance', return_value=False):
            for params, acts in (
                    ({'gesture': 'double_click', 'x': 50, 'y': 60}, [('move', 50, 60), ('click', 'gauche', 2)]),
                    ({'gesture': 'scroll', 'window': 'Bloc-notes', 'amount': 2}, [('move', 400, 300), ('wheel', -2)]),
                    ({'gesture': 'key', 'keys': 'ctrl+s', 'window': 'Bloc-notes'}, [('keys', ['ctrl', 's'])]),
                    ({'gesture': 'type', 'text': 'ligne 1\nligne 2', 'window': 'Bloc-notes'},
                     [('type', 'ligne 1\nligne 2')])):
                self.fake.done.clear()
                fp = (await self.gesture(**params, dry_run=True)).data['fingerprint']
                res = await self.gesture(**params, approved_fingerprint=fp, permission_id=self._grant(fp))
                self.assertTrue(res.ok, res.summary)
                self.assertTrue(res.data['done'])
                self.assertEqual(self.fake.acts(), acts, params)
                self.assertEqual(self.fake.done[0], ('front', 101))   # the window first comes to the front

    async def test_window_changed_or_covered_since_the_card(self):
        config.update_settings({'pc_control_enabled': True})
        plan = self.plan(gesture='click', x=50, y=60)
        self.fake.windows[0]['title'] = 'Mot de passe'
        with self.assertRaises(pc_control.ToolError) as caught:
            pc_control.perform(plan)
        self.assertIn('a changé', str(caught.exception))
        self.fake.windows[0]['title'] = WINDOWS[0]['title']
        self.fake.windows.insert(0, {**WINDOWS[3], 'hwnd': 107, 'rect': (0, 0, 100, 100)})   # now on top at (50, 60)
        with self.assertRaises(pc_control.ToolError) as caught:
            pc_control.perform(plan)
        self.assertIn('Une autre fenêtre', str(caught.exception))
        self.assertEqual(self.fake.acts(), [])

    async def test_pointer_reads_only(self):
        res = await run_tool('pc.pointer', {})
        self.assertTrue(res.ok)
        self.assertEqual((res.data['x'], res.data['y'], res.data['window']['exe']), (300, 200, 'notepad.exe'))
        self.assertEqual(self.fake.done, [])


class RealTools:
    """The real registry and run_tool (tools/base.py), so the orchestrator drives the real pc.gesture."""

    def __init__(self):
        from aurora.tools import base
        self.REGISTRY = base.REGISTRY
        self.run_tool = base.run_tool


class OrchestratorTests(WithFakeInput):
    """The whole path, real tool and real engine selection: dry run, card, accord, gesture (on the fake Windows)."""

    def setUp(self):
        super().setUp()
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        events.bus.detach_loop()

    def orch(self, text: str):
        from aurora.core.orchestrator import Deps, Orchestrator
        decision = FakeDecision('pc.control', method='rule', slots=extract_gesture(text))
        deps = Deps(decision_engine=FakeEngine(decision), router=FakeRouter, tools=RealTools(),
                    ollama=FakeOllama(), claude=FakeClaude(configured=False), capabilities=make_caps())
        return Orchestrator(deps)

    async def test_card_shows_the_gesture_then_it_is_done(self):
        config.update_settings({'pc_control_enabled': True})
        text = 'tape dans Bloc-notes : Bonjour, ça va ?'
        orch = self.orch(text)
        waiting = await orch.handle_request(text)
        self.assertEqual(waiting['status'], 'waiting_permission', waiting.get('answer'))
        perm = waiting['permission']
        self.assertEqual(perm['action'], 'pc.control')
        preview = perm['preview']
        self.assertEqual(preview['text'], 'Bonjour, ça va ?')
        self.assertEqual(preview['window'], {'title': 'Sans titre - Bloc-notes', 'exe': 'notepad.exe'})
        self.assertEqual(len(preview['fingerprint']), 24)
        self.assertIn('Taper 16 caractère(s)', perm['title'])
        self.assertEqual(self.fake.done, [])   # nothing before the accord
        with mock.patch.object(pc_control, 'trial_instance', return_value=False):
            done = await orch.decide_permission(perm['id'], 'grant')
        self.assertEqual(done['status'], 'done')
        self.assertEqual(self.fake.acts(), [('type', 'Bonjour, ça va ?')])
        self.assertIn('Fait : Taper', done['answer'])
        self.assertIn('Juste après, la fenêtre est', done['answer'])

    async def test_trial_instance_says_nothing_was_done(self):
        config.update_settings({'pc_control_enabled': True})
        orch = self.orch('clique en 50, 60')
        waiting = await orch.handle_request('clique en 50, 60')
        done = await orch.decide_permission(waiting['permission']['id'], 'grant')
        self.assertIn('Instance d’essai : je n’ai rien fait', done['answer'])
        self.assertEqual(self.fake.done, [])

    async def test_denied(self):
        config.update_settings({'pc_control_enabled': True})
        orch = self.orch('clique en 50, 60')
        waiting = await orch.handle_request('clique en 50, 60')
        done = await orch.decide_permission(waiting['permission']['id'], 'deny')
        self.assertIn('Autorisation refusée : rien n’a été fait', done['answer'])
        self.assertIn('cliquer (bouton gauche) en (50, 60)', done['answer'])
        self.assertEqual(self.fake.done, [])

    async def test_no_card_when_disabled_protected_or_named_target(self):
        for text, enabled, said in (
                ('clique en 50, 60', False, 'désactivés'),
                ('tape dans PowerShell : dir', True, 'Refusé même avec accord'),
                ('clique sur le bouton OK', True, 'pas encore fait'),
                ('clique', True, 'Position manquante')):
            config.update_settings({'pc_control_enabled': enabled})
            result = await self.orch(text).handle_request(text)
            self.assertNotEqual(result['status'], 'waiting_permission', text)
            self.assertIsNone(result.get('permission'), text)
            self.assertIn(said, result['answer'], text)
            self.assertIn('ien n’a été fait', result['answer'], text)
        self.assertEqual(self.fake.done, [])

    async def test_where_is_the_mouse(self):
        result = await self.orch('où est la souris ?').handle_request('où est la souris ?')
        self.assertIn('La souris est en (300, 200)', result['answer'])


if __name__ == '__main__':
    unittest.main()
