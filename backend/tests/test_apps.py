"""Applications autorisées (tools/apps.py, 9/11/2026) : la liste de Paramètres, l'ouverture par son nom exact.

Rien n'est jamais lancé ici : chaque test part d'une base temporaire (TempEnv), donc d'une instance d'essai, et le
lancement lui-même (apps.start) est remplacé quand un test fait comme sur le vrai AURORA.
"""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora import config
from aurora.core import compose
from aurora.core import planner as planner_mod
from aurora.core.normalize import extract_app
from aurora.security import policy
from aurora.tasks import events
from aurora.tools import apps
from aurora.tools.base import run_tool
from tests.doubles_core import FakeClaude, FakeDecision, FakeEngine, FakeOllama, FakeRouter, TempEnv, make_caps, needs_robot
from tests.test_pc_control import RealTools   # the real registry and run_tool, reused


class WithApps(unittest.IsolatedAsyncioTestCase):
    """A temporary AURORA with one allowed app, « YouTube Manager », whose program is a harmless .bat file."""

    def setUp(self):
        self.env = TempEnv().__enter__()
        self.folder = Path(tempfile.mkdtemp())
        self.program = self.folder / 'start_youtube.bat'
        self.program.write_text('@echo off\r\n', 'utf-8')
        self.started: list[str] = []
        self.patch = mock.patch.object(apps, 'start', side_effect=self.started.append)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.__exit__(None, None, None)

    def allow(self, url: str = '') -> None:
        config.update_settings({'allowed_apps': [{'name': 'YouTube Manager', 'path': str(self.program), 'url': url}]})

    async def launch(self, name: str = 'youtube manager', *, trial: bool = True, model: str | None = None):
        with mock.patch.object(apps, 'trial_instance', return_value=trial):
            return await run_tool('app.launch', {'name': name, **({'model': model} if model else {})})


class ExtractAndRouteTests(unittest.TestCase):
    def test_the_name_asked(self):
        cases = {
            'Ouvre YouTube Manager': 'YouTube Manager',   # as written when the name ends the sentence
            'lance-moi l’application YouTube Manager s’il te plaît': 'youtube manager',
            'peux-tu démarrer le logiciel OBS Studio ?': 'OBS Studio',
            'aurora ouvre l’appli Spotify': 'Spotify',
            'Ouvre une application': None,
            'quelle application pour monter une vidéo ?': None,
        }
        for text, wanted in cases.items():
            self.assertEqual(extract_app(text)['app'], wanted, text)
        self.assertEqual(extract_app('lance : notepad'), {'app': None, 'command_syntax': True})

    def test_router(self):
        from aurora.core.router import get_engine
        engine = get_engine()
        for text in ('Ouvre l’application Spotify', 'lance le logiciel GIMP', 'démarre Steam', 'ouvre une application',
                     'tu peux lancer l’application YouTube Manager ?'):
            self.assertEqual(engine.decide_fast(text).intent, 'app.launch', text)
        for text, other in (('ouvre la mémoire', 'ui.navigate'), ('ouvre les paramètres', 'ui.navigate'),
                            ('ferme l’application Spotify', 'system.kill_process'),
                            ('lance la commande dir', 'shell.run'), ('lance la routine du matin', 'routine.run')):
            self.assertEqual(engine.decide_fast(text).intent, other, text)
        self.assertNotEqual(engine.decide_fast('quelle application pour monter une vidéo ?').intent, 'app.launch')

    def test_conversation_144(self):
        """9/11/2026, ID Studio IA : « elle ouvre bien les applications mais quand on lui demande de l'utiliser elle bug »."""
        long = ('tu peux mettre un petit commentaire sur une vidéo en disant bonjour visiteurs et en les remerciant '
                "de regarder la vidéo de ta part  utiliser l'application Youtube manager")
        self.assertEqual(extract_app(long), {'app': 'Youtube manager', 'task': 'mettre un petit commentaire sur une '
                         'vidéo en disant bonjour visiteurs et en les remerciant de regarder la vidéo'})
        self.assertEqual(extract_app("ouvre l'application YouTube Manager pour mettre un commentaire"),
                         {'app': 'youtube manager', 'task': 'mettre un commentaire'})
        self.assertEqual(extract_app('lance YouTube Manager et mets un commentaire'),
                         {'app': 'youtube manager', 'task': 'mets un commentaire'})
        self.assertEqual(extract_app("utilise l'application YouTube Manager"), {'app': 'YouTube Manager'})
        # A negation: the name is kept apart, nothing is opened.
        for text in ("je veux pas utiliser l'application Youtube manager", 'ne lance pas YouTube Manager',
                     'je ne veux pas que tu ouvres l’appli YouTube Manager'):
            self.assertIsNone(extract_app(text)['app'], text)
            self.assertEqual(apps.app_key(extract_app(text)['refused']), 'youtube manager', text)
        # A story, or « dans l'application » (where a gesture types), is not a request to open.
        for text in ("j'ai utilisé l'application OBS Studio hier", 'tape bonjour dans l’application Discord',
                     'quelle application tu utilises pour la voix ?'):
            self.assertIsNone(extract_app(text)['app'], text)

    def test_the_list_read_in_the_sentence(self):
        listed = [{'name': n} for n in ('Discord', 'ID Studio IA', 'OBS Studio', 'VLC media player',
                                         'YouTube Manager IA')]
        with mock.patch.object(apps, 'allowed_apps', return_value=listed):
            self.assertEqual(apps.match('ouvre VLC'), {'app': 'VLC media player'})   # was « pas dans la liste »
            self.assertEqual(apps.match('ouvre obs')['app'], 'OBS Studio')
            self.assertEqual(apps.match("avec l'application YouTube Manager mets un commentaire sur ma dernière "
                                        'vidéo'),
                             {'app': 'YouTube Manager IA', 'task': 'mets un commentaire sur ma dernière vidéo'})
            self.assertEqual(apps.match("je veux pas utiliser l'application Youtube manager"),
                             {'app': None, 'refused': 'YouTube Manager IA'})
            for text in ('ouvre Discord et OBS', 'ouvre id', 'ouvre Spotify', "j'ai utilisé l'application OBS Studio"):
                self.assertIsNone(apps.match(text), text)

    def test_a_similarity_alone_does_not_open(self):
        from aurora.core.orchestrator import DEFAULT_DECISION, _weak_app_launch
        weak = {**DEFAULT_DECISION, 'intent': 'app.launch', 'method': 'semantic', 'slots': {'app': None},
                'normalized': 'tu peux mettre un petit commentaire sur une video'}
        self.assertEqual(_weak_app_launch(weak)['intent'], 'chat.general')
        for kept in ({**weak, 'method': 'rule'}, {**weak, 'slots': {'app': 'Spotify'}},
                     {**weak, 'slots': {'app': None, 'refused': 'Spotify'}},
                     {**weak, 'normalized': 'ouvre moi une appli'}):
            self.assertEqual(_weak_app_launch(kept)['intent'], 'app.launch', kept)


class ListTests(WithApps):
    def test_empty_by_default_and_coerced(self):
        self.assertEqual(config.default_settings()['allowed_apps'], [])
        self.assertEqual(config.load_settings()['allowed_apps'], [])
        self.assertEqual(config._coerce({**config.default_settings(), 'allowed_apps': 'x'})['allowed_apps'], [])

    def test_a_good_entry_is_kept(self):
        self.allow('http://localhost:3000')
        saved = config.load_settings()['allowed_apps']
        self.assertEqual(saved, [{'name': 'YouTube Manager', 'path': str(self.program.resolve()),
                                  'url': 'http://localhost:3000'}])

    def test_refused_entries(self):
        good = {'name': 'Mon appli', 'path': str(self.program)}
        txt = self.folder / 'notes.txt'
        txt.write_text('x', 'utf-8')
        cmd = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe'
        cases = (
            ([{**good, 'name': 'Mémoire'}], 'nom d’une page'),
            ([{**good, 'name': 'les paramètres'}], 'nom d’une page'),
            ([good, {**good, 'name': 'MON APPLI'}], 'même nom'),
            ([{**good, 'name': ''}], 'Nom d’application invalide'),
            ([{**good, 'path': 'start.bat'}], 'non absolu'),
            ([{**good, 'path': str(self.folder / 'absent.exe')}], 'introuvable'),
            ([{**good, 'path': str(txt)}], 'seuls les programmes'),
            ([{**good, 'url': 'https://exemple.com'}], 'seulement http://localhost'),
            ([{**good, 'url': 'http://192.168.1.2:3000'}], 'seulement http://localhost'),
            ([{**good, 'name': f'A{i}'} for i in range(31)], 'Au plus 30'),
            ('YouTube', 'liste'),
        )
        if cmd.is_file():
            cases += (([{**good, 'path': str(cmd)}], 'un terminal'),)
        for value, said in cases:
            with self.assertRaises(ValueError, msg=str(value)[:80]) as caught:
                config.update_settings({'allowed_apps': value})
            self.assertIn(said, str(caught.exception), str(value)[:80])
        self.assertEqual(config.load_settings()['allowed_apps'], [])   # nothing saved by a refused list

    def test_find_by_the_name_or_its_first_words(self):
        listed = [{'name': 'YouTube Manager', 'path': 'x'}]
        self.assertIs(apps.find('youtube  MANAGER', listed), listed[0])
        self.assertIs(apps.find('le YouTube-Manager', listed), listed[0])
        for name in ('youtube manager pro', 'you', 'manager', ''):
            self.assertIsNone(apps.find(name, listed), name)
        # The name of a shortcut (« YouTube Manager IA ») is often longer than the words said (« ouvre YouTube
        # Manager »): its first words find it, when only ONE app of the list starts with them.
        self.assertIs(apps.find('youtube', listed), listed[0])
        ia = [{'name': 'YouTube Manager IA', 'path': 'x'}]
        self.assertIs(apps.find('YouTube Manager', ia), ia[0])
        vlc = [{'name': 'VLC media player', 'path': 'x'}, {'name': 'ID Studio IA', 'path': 'y'}]
        self.assertIs(apps.find('VLC', vlc), vlc[0])   # 3 letters: a whole first word
        self.assertIsNone(apps.find('id', vlc))        # 2 letters: the exact name only
        studios = [{'name': 'Visual Studio 2022', 'path': 'x'}, {'name': 'Visual Studio Code', 'path': 'y'}]
        self.assertIsNone(apps.find('visual studio', studios))   # two candidates: none is guessed
        self.assertIs(apps.find('visual studio code', studios), studios[1])
        self.assertIsNone(apps.find('mémoire', [{'name': 'Mémoire Pro', 'path': 'x'}]))   # stays the page

    def test_the_launch_does_not_pass_aurora_s_own_variables(self):
        with mock.patch.dict(os.environ, {'AURORA_SESSION_TOKEN': 'secret', 'PORT': '8765', 'PATH': 'C:\\x'}):
            env = apps.launch_env()
        self.assertNotIn('AURORA_SESSION_TOKEN', env)
        self.assertNotIn('AURORA_HOME', env)
        self.assertNotIn('PORT', env)
        self.assertEqual(env['PATH'], 'C:\\x')


class ToolTests(WithApps):
    async def test_empty_list(self):
        res = await self.launch()
        self.assertFalse(res.ok)
        self.assertIn('liste d’applications autorisées est vide', res.error)
        self.assertEqual(self.started, [])

    async def test_a_name_outside_the_list_launches_nothing(self):
        self.allow()
        res = await self.launch('Spotify', trial=False)
        self.assertFalse(res.ok)
        self.assertIn('« Spotify » n’est pas dans votre liste', res.error)
        self.assertIn('« YouTube Manager »', res.error)
        self.assertEqual(res.data['allowed'], ['YouTube Manager'])
        self.assertEqual(self.started, [])

    async def test_trial_instance_launches_nothing(self):
        self.allow()
        res = await self.launch()
        self.assertFalse(res.ok)
        self.assertIn('Instance d’essai : je n’ai rien lancé', res.error)
        self.assertTrue(res.data['trial'])
        self.assertEqual(self.started, [])

    async def test_launched_without_an_address(self):
        self.allow()
        res = await self.launch(trial=False)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self.started, [str(self.program.resolve())])
        self.assertIn('J’ai demandé à Windows d’ouvrir « YouTube Manager »', res.summary)
        self.assertIn('je ne peux pas vérifier', res.summary)

    async def test_already_running_is_not_relaunched(self):
        self.allow('http://localhost:3000')
        with mock.patch.object(apps, 'answers', return_value=True):
            res = await self.launch(trial=False)
        self.assertTrue(res.ok)
        self.assertTrue(res.data['already_running'])
        self.assertIn('tourne déjà', res.summary)
        self.assertEqual(self.started, [])

    async def test_the_address_is_watched_after_the_launch(self):
        self.allow('http://localhost:3000')
        with mock.patch.object(apps, 'answers', side_effect=[False, False, True]), \
                mock.patch.object(apps.time, 'sleep'):
            res = await self.launch(trial=False)
        self.assertTrue(res.data['answering'])
        self.assertIn('est ouverte : elle répond sur http://localhost:3000', res.summary)
        with mock.patch.object(apps, 'answers', return_value=False), mock.patch.object(apps, 'WAIT_S', 0.0):
            res = await self.launch(trial=False)
        self.assertFalse(res.data['answering'])
        self.assertIn('ne répond pas encore', res.summary)
        self.assertEqual(len(self.started), 2)

    async def test_a_program_gone_since_the_list_launches_nothing(self):
        self.allow()
        self.program.unlink()
        res = await self.launch(trial=False)
        self.assertFalse(res.ok)
        self.assertIn('introuvable', res.error)
        self.assertIn('rien n’a été lancé', res.error)
        self.assertEqual(self.started, [])

    async def test_windows_refusing_is_said(self):
        self.allow()
        with mock.patch.object(apps, 'start', side_effect=OSError('refus')):
            res = await self.launch(trial=False)
        self.assertFalse(res.ok)
        self.assertIn('Windows n’a pas pu ouvrir', res.error)


class PolicyPlannerTests(unittest.TestCase):
    def test_the_list_is_the_accord(self):
        for mode in policy.MODES:
            decision = policy.evaluate('app.launch', mode=mode)
            self.assertTrue(decision.allowed)
            self.assertFalse(decision.requires_permission)
            self.assertIn('liste autorisée', decision.reason)

    def test_the_agent_cannot_open_an_app(self):
        from aurora.core import agent
        from aurora.tools.base import REGISTRY
        self.assertIn('app.launch', REGISTRY)
        self.assertNotIn('app.launch', agent.actions(REGISTRY, shell=True))

    def test_build_from_the_intent(self):
        def steps(slots: dict) -> list[dict]:
            built = planner_mod.Planner().build({'intent': 'app.launch', 'slots': slots}, {'engine': 'tool'},
                                                text='x', settings=config.default_settings())
            return [s for s in built if s['stage'] == 'tool']
        found = steps({'app': 'YouTube Manager'})
        self.assertEqual([(s['tool'], s['params'], s['requires_permission']) for s in found],
                         [('app.launch', {'name': 'YouTube Manager'}, False)])
        self.assertEqual(steps({'app': None}), [])

    def test_help_without_a_name(self):
        self.assertIn('liste d’applications autorisées est vide', compose.app_launch({'app': None}, None, []))
        self.assertIn('« A », « B »', compose.app_launch({'app': None}, None, ['A', 'B']))
        said = compose.app_launch({'app': None, 'command_syntax': True}, None, ['A'])
        self.assertIn('exécute la commande', said)
        self.assertIn('Rien n’a été lancé', said)

    def test_a_refusal_and_a_task_are_said(self):
        said = compose.app_launch({'app': None, 'refused': 'YouTube Manager IA'}, None, ['YouTube Manager IA'])
        self.assertEqual(said, 'D’accord : je n’ouvre pas « YouTube Manager IA ». Rien n’a été lancé.')
        opened = {'ok': True, 'summary': '« YouTube Manager IA » tourne déjà.', 'data': {'name': 'YouTube Manager IA'}}
        said = compose.app_launch({'app': 'youtube manager', 'task': 'mettre un commentaire'}, opened, [])
        self.assertTrue(said.startswith('« YouTube Manager IA » tourne déjà.\n'), said)
        self.assertIn('pas encore m’en servir à votre place pour « mettre un commentaire »', said)
        self.assertIn(compose.YOUTUBE_COMMENT_HELP, said)   # 9/11/2026: AURORA posts the comment herself
        self.assertIn(compose.PC_CONTROL_HELP, said)        # the gestures in the application stay as the fallback
        other = compose.app_launch({'app': 'youtube manager', 'task': 'monter la vidéo'}, opened, [])
        self.assertIn(compose.PC_CONTROL_HELP, other)
        self.assertNotIn(compose.YOUTUBE_COMMENT_HELP, other)
        # Not opened (a trial instance, a name outside the list): the task is not answered.
        self.assertNotIn('pas encore', compose.app_launch({'app': 'x', 'task': 'y'}, {'ok': False, 'error': 'Non.'}, []))


class OrchestratorTests(WithApps):
    """The whole path: the allowed name is recognised before the router, opened without a card, and said."""

    def setUp(self):
        super().setUp()
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        events.bus.detach_loop()

    def orch(self, decision: FakeDecision):
        from aurora.core.orchestrator import Deps, Orchestrator
        deps = Deps(decision_engine=FakeEngine(decision), router=FakeRouter, tools=RealTools(),
                    ollama=FakeOllama(), claude=FakeClaude(configured=False), capabilities=make_caps())
        return Orchestrator(deps)

    async def test_an_allowed_name_opens_without_a_card(self):
        self.allow()
        orch = self.orch(FakeDecision('chat.general'))   # the router would not know: the list is matched first
        with mock.patch.object(apps, 'trial_instance', return_value=False):
            result = await orch.handle_request('Ouvre YouTube Manager')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertIsNone(result.get('permission'))
        self.assertIn('J’ai demandé à Windows d’ouvrir « YouTube Manager »', result['answer'])
        self.assertEqual(self.started, [str(self.program.resolve())])

    async def test_trial_instance_answer(self):
        self.allow()
        result = await self.orch(FakeDecision('chat.general')).handle_request('lance YouTube Manager')
        self.assertIn('Instance d’essai : je n’ai rien lancé', result['answer'])
        self.assertEqual(self.started, [])

    async def test_a_name_outside_the_list(self):
        self.allow()
        text = 'ouvre l’application Spotify'
        orch = self.orch(FakeDecision('app.launch', method='rule', slots=extract_app(text)))
        with mock.patch.object(apps, 'trial_instance', return_value=False):
            result = await orch.handle_request(text)
        self.assertIn('« Spotify » n’est pas dans votre liste', result['answer'])
        self.assertIn('Paramètres › Applications autorisées', result['answer'])
        self.assertEqual(self.started, [])

    async def test_conversation_144(self):
        """The two sentences of ID Studio IA that got « Dites quelle application ouvrir » (9/11/2026, 15:40 and 15:42)."""
        self.allow()
        orch = self.orch(FakeDecision('app.launch', method='semantic'))   # what the similarity had chosen
        with mock.patch.object(apps, 'trial_instance', return_value=False):
            result = await orch.handle_request("je veux pas utiliser l'application Youtube manager")
            self.assertEqual(result['answer'], 'D’accord : je n’ouvre pas « YouTube Manager ». Rien n’a été lancé.')
            self.assertEqual(self.started, [])
            result = await orch.handle_request(
                'tu peux mettre un petit commentaire sur une vidéo en disant bonjour visiteurs et en les remerciant '
                "de regarder la vidéo de ta part  utiliser l'application Youtube manager")
        self.assertIn('J’ai demandé à Windows d’ouvrir « YouTube Manager »', result['answer'])
        self.assertIn('pas encore m’en servir à votre place pour « mettre un petit commentaire sur une vidéo',
                      result['answer'])
        self.assertNotIn('Dites quelle application ouvrir', result['answer'])
        self.assertEqual(self.started, [str(self.program.resolve())])


def make_lnk(path: Path, target: str, args: str = '', *, wide: bool = False) -> Path:
    """A minimal Windows shortcut (MS-SHLLINK: header, LinkInfo with a local base path, the arguments)."""
    le = lambda n, size=4: n.to_bytes(size, 'little')   # noqa: E731
    flags = 0x02 | 0x80 | (0x20 if args else 0)   # HasLinkInfo, IsUnicode, HasArguments
    header = le(0x4C) + bytes(16) + le(flags) + bytes(52)
    head = 0x24 if wide else 0x1C
    ansi = (b'' if wide else target.encode('ascii')) + b'\0'
    unicode = target.encode('utf-16-le') + b'\0\0' if wide else b''
    suffix = head + len(ansi)
    fields = [head, 1, 0, head, 0, suffix] + ([suffix + 1, 0] if wide else [])
    body = ansi + b'\0' + unicode
    info = le(head + len(body)) + b''.join(le(f) for f in fields) + body
    strings = le(len(args), 2) + args.encode('utf-16-le') if args else b''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + info + strings)
    return path


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.tools = self.folder / 'tools'
        self.tools.mkdir()
        for exe in ('cmd.exe', 'python.exe', 'pythonw.exe', 'obs64.exe'):
            (self.tools / exe).write_bytes(b'')

    def test_the_target_and_arguments_are_read_from_the_file(self):
        lnk = make_lnk(self.folder / 'YouTube Manager IA.lnk', r'C:\Windows\System32\cmd.exe',
                       r'/k "C:\vita1\youtube-manager-ia\start_youtube.bat"')
        self.assertEqual(apps.shortcut_target(lnk),
                         (r'C:\Windows\System32\cmd.exe', r'/k "C:\vita1\youtube-manager-ia\start_youtube.bat"'))
        wide = make_lnk(self.folder / 'Café.lnk', r'C:\Jeux\Café Studio\café.exe', wide=True)
        self.assertEqual(apps.shortcut_target(wide), (r'C:\Jeux\Café Studio\café.exe', ''))
        not_a_link = self.folder / 'faux.lnk'
        not_a_link.write_bytes(b'pas un raccourci')
        self.assertEqual(apps.shortcut_target(not_a_link), ('', ''))
        self.assertEqual(apps.shortcut_target(self.folder / 'absent.lnk'), ('', ''))

    def test_what_can_never_be_in_the_list(self):
        cmd = str(self.tools / 'cmd.exe')
        python = str(self.tools / 'python.exe')
        refused = {
            make_lnk(self.folder / 'Mon outil.lnk', cmd): 'un raccourci vers un terminal',
            make_lnk(self.folder / 'Invite de commandes.lnk', str(self.tools / 'obs64.exe')): 'vers un terminal',
            make_lnk(self.folder / 'Python 3.11.lnk', python): 'interpréteur',
            make_lnk(self.folder / 'A.lnk', str(apps.PROJECT_ROOT / 'AURORA.bat')): 'AURORA elle-même',
            apps.PROJECT_ROOT / 'AURORA.bat': 'AURORA elle-même',
            Path(r'C:\Windows\regedit.exe'): 'registre',
            Path(r'C:\Outils\node.exe'): 'interpréteur',
        }
        for path, said in refused.items():
            self.assertIn(said, apps.why_refused(path), path.name)
        accepted = (
            make_lnk(self.folder / 'YouTube Manager IA.lnk', cmd, r'/k "C:\x\start_youtube.bat"'),   # a script
            make_lnk(self.folder / 'IDLE.lnk', str(self.tools / 'pythonw.exe'), r'"C:\Python\Lib\idlelib\idle.pyw"'),
            make_lnk(self.folder / 'OBS Studio.lnk', str(self.tools / 'obs64.exe')),
            self.tools / 'obs64.exe',
        )
        for path in accepted:
            self.assertEqual(apps.why_refused(path), '', path.name)

    def test_the_list_refuses_a_shortcut_to_a_terminal(self):
        with TempEnv():
            lnk = make_lnk(self.folder / 'Mon outil.lnk', str(self.tools / 'cmd.exe'))
            with self.assertRaises(ValueError) as caught:
                config.update_settings({'allowed_apps': [{'name': 'Mon outil', 'path': str(lnk)}]})
            self.assertIn('un raccourci vers un terminal', str(caught.exception))
            ok = make_lnk(self.folder / 'YouTube Manager IA.lnk', str(self.tools / 'cmd.exe'), '/k "C:\\x\\a.bat"')
            config.update_settings({'allowed_apps': [{'name': 'YouTube Manager IA', 'path': str(ok)}]})
            self.assertEqual(config.load_settings()['allowed_apps'][0]['name'], 'YouTube Manager IA')


class ModelInAppTests(WithApps):
    """13/11/2026, ID Studio IA : « brancher mon logiciel Creality à AURORA […] pour faire une impression ». « Ouvre le tibia
    dans Creality Print » : un modèle 3D gardé par AURORA, donné à une application de la liste. Rien n'est lancé ici."""

    def setUp(self):
        super().setUp()
        from aurora.tools import lab
        self.lab = lab
        self.patch.stop()   # the program and the file it is given are both kept
        self.patch = mock.patch.object(apps, 'start', side_effect=lambda *given: self.started.append(given))
        self.patch.start()
        self.slicer = self.folder / 'CrealityPrint.exe'
        self.slicer.write_bytes(b'')
        self.listed = [{'name': 'Creality Print 6.3', 'path': str(self.slicer), 'url': ''},
                       {'name': 'YouTube Manager', 'path': str(self.program), 'url': ''}]
        config.update_settings({'allowed_apps': self.listed})
        stl = bytes(80) + (1).to_bytes(4, 'little') + bytes(50)
        glb = b'glTF' + (2).to_bytes(4, 'little') + (20).to_bytes(4, 'little') + bytes(8)
        self.to_print = lab.import_model('Tibia a imprimer.stl', stl)[0]['id']
        self.shown = lab.import_model('Tibia quadrupede.glb', glb)[0]['id']

    def test_the_sentence_names_the_app_and_the_model(self):
        wanted = {'app': 'Creality Print 6.3', 'model': self.to_print}
        for text in ('ouvre le tibia à imprimer dans Creality Print', 'Ouvre le tibia dans Creality Print',
                     'ouvre le modèle 3D tibia a imprimer avec le logiciel Creality Print',
                     'peux-tu ouvrir le tibia sur Creality Print ?'):
            self.assertEqual(apps.match(text), wanted, text)
        # The exact name of the other model is that model, even if no slicer opens it (the tool says so).
        self.assertEqual(apps.match('ouvre le tibia quadrupede dans Creality Print')['model'], self.shown)
        self.assertEqual(apps.match('ouvre Creality Print'), {'app': 'Creality Print 6.3'})
        # No model of that name, or an application outside the list: the usual routing goes on.
        for text in ('ouvre la gourde dans Creality Print', 'ouvre le tibia dans Blender', 'ouvre la météo dans Chrome'):
            self.assertIsNone(apps.match(text), text)
        # The same piece kept twice under one name, to show (GLB) and to print (STL, « Tibia quadrupede 2 »): the name
        # of the first gives the one a slicer opens.
        twin = self.lab.import_model('Tibia quadrupede.stl', bytes(80) + (2).to_bytes(4, 'little') + bytes(100))[0]
        self.assertEqual(twin['titre'], 'Tibia quadrupede 2')
        self.assertEqual(apps.match('ouvre le tibia quadrupede dans Creality Print')['model'], twin['id'])
        self.assertIsNone(apps.match('ouvre le tibia dans Creality Print'))   # two can be opened now: none is guessed

    async def test_the_file_of_the_model_is_given_to_the_program(self):
        res = await self.launch('Creality Print', trial=False, model=self.to_print)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self.started, [(str(self.slicer.resolve()), str(self.lab.models_dir() / f'{self.to_print}.stl'))])
        self.assertIn('« Tibia a imprimer »', res.summary)
        self.assertIn('dans « Creality Print 6.3 »', res.summary)
        self.assertIn('par vous', res.summary)   # the print itself is never launched by AURORA
        self.assertEqual((res.data['model'], res.data['file']), (self.to_print, f'{self.to_print}.stl'))

    async def test_a_shortcut_gives_its_program(self):
        lnk = make_lnk(self.folder / 'Creality Print 6.3.lnk', str(self.slicer), wide=True)
        config.update_settings({'allowed_apps': [{'name': 'Creality Print 6.3', 'path': str(lnk), 'url': ''}]})
        res = await self.launch('Creality Print', trial=False, model=self.to_print)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self.started[0][0], str(self.slicer))

    async def test_trial_instance_launches_nothing_with_a_model(self):
        res = await self.launch('Creality Print', model=self.to_print)
        self.assertFalse(res.ok)
        self.assertIn('Instance d’essai : je n’ai rien lancé', res.error)
        self.assertIn('« Tibia a imprimer »', res.error)
        self.assertEqual(self.started, [])

    async def test_what_is_refused(self):
        cases = ((self.shown, 'Creality Print', 'est un fichier .glb'),            # not a file a slicer opens
                 (self.to_print, 'YouTube Manager', 'ne peut pas recevoir un fichier'),   # a script takes no file
                 ('imp_absent', 'Creality Print', 'imp_absent'),                    # not a model of AURORA
                 (self.to_print, 'Spotify', 'n’est pas dans votre liste'))
        for model, name, said in cases:
            res = await self.launch(name, trial=False, model=model)
            self.assertFalse(res.ok, (model, name))
            self.assertIn(said, res.error, (model, name))
        self.slicer.unlink()   # the program gone since the list
        res = await self.launch('Creality Print', trial=False, model=self.to_print)
        self.assertIn('introuvable', res.error)
        self.assertEqual(self.started, [])

    def test_the_plan_carries_the_model(self):
        built = planner_mod.Planner().build({'intent': 'app.launch', 'slots': {'app': 'Creality Print 6.3',
                                                                                'model': self.to_print}},
                                            {'engine': 'tool'}, text='x', settings=config.default_settings())
        found = [s for s in built if s['stage'] == 'tool']
        self.assertEqual([(s['tool'], s['params'], s['requires_permission']) for s in found],
                         [('app.launch', {'name': 'Creality Print 6.3', 'model': self.to_print}, False)])

    async def test_the_whole_path(self):
        from aurora.core.orchestrator import Deps, Orchestrator
        logging.getLogger('aurora').setLevel(logging.CRITICAL)
        events.bus.detach_loop()
        orch = Orchestrator(Deps(decision_engine=FakeEngine(FakeDecision('chat.general')), router=FakeRouter,
                                 tools=RealTools(), ollama=FakeOllama(), claude=FakeClaude(configured=False),
                                 capabilities=make_caps()))
        with mock.patch.object(apps, 'trial_instance', return_value=False):
            result = await orch.handle_request('Ouvre le tibia dans Creality Print')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertIsNone(result.get('permission'))
        self.assertIn('« Tibia a imprimer »', result['answer'])
        self.assertIn('dans « Creality Print 6.3 »', result['answer'])
        self.assertEqual(len(self.started), 1)


class ProjectFileInAppTests(WithApps):
    """13/11/2026, ID Studio IA : « FreeCAD et KiCad d'abord ». « Ouvre le socle de la lampe dans FreeCAD » : un fichier
    déclaré par un projet, donné au logiciel de conception de la liste. Rien n'est lancé ici."""

    def setUp(self):
        super().setUp()
        import json
        self.patch.stop()   # the program and the file it is given are both kept
        self.patch = mock.patch.object(apps, 'start', side_effect=lambda *given: self.started.append(given))
        self.patch.start()
        self.freecad, self.kicad, slicer = (self.folder / n for n in ('freecad.exe', 'kicad.exe', 'CrealityPrint.exe'))
        for program in (self.freecad, self.kicad, slicer):
            program.write_bytes(b'')
        lnk = make_lnk(self.folder / 'FreeCAD 1.1.lnk', str(self.freecad), wide=True)   # as on the Desktop
        config.update_settings({'allowed_apps': [
            {'name': 'FreeCAD 1.1', 'path': str(lnk), 'url': ''}, {'name': 'Mon KiCad', 'path': str(self.kicad), 'url': ''},
            {'name': 'Creality Print 6.3', 'path': str(slicer), 'url': ''}]})
        self.project = Path(os.environ['AURORA_HOME']) / 'projets' / 'lampe'
        (self.project / 'sortie').mkdir(parents=True)
        files = [{'nom': 'Socle', 'chemin': 'sortie/socle.step', 'role': '', 'genere': True},
                 {'nom': 'Abat-jour', 'chemin': 'sortie/abat_jour.step', 'role': '', 'genere': True},
                 {'nom': 'Carte de la lampe', 'chemin': 'carte/lampe.kicad_pro', 'role': ''},
                 {'nom': 'Calcul', 'chemin': 'calcul.py', 'role': ''}]
        (self.project / 'projet.json').write_text(json.dumps({
            'format': 'aurora.projet/1', 'id': 'lampe-de-bureau', 'titre': 'Lampe de bureau',
            'rubriques': [{'id': 'pieces', 'note': '', 'fichiers': files}]}), 'utf-8')
        for name in ('sortie/socle.step', 'carte/lampe.kicad_pro', 'calcul.py'):   # the shade is not made yet
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text('x', 'utf-8')

    async def hand(self, name: str, fichier: str, *, trial: bool = False, projet: str = 'lampe-de-bureau'):
        with mock.patch.object(apps, 'trial_instance', return_value=trial):
            return await run_tool('app.launch', {'name': name, 'projet': projet, 'fichier': fichier})

    def test_the_sentence_names_the_tool_and_the_file(self):
        socle = {'app': 'FreeCAD 1.1', 'projet': 'lampe-de-bureau', 'fichier': 'sortie/socle.step'}
        for text in ('ouvre le socle dans FreeCAD', 'Ouvre le socle de la lampe dans FreeCAD 1.1',
                     'ouvre « Socle » du projet lampe-de-bureau dans FreeCAD 1.1',   # what the page « Projets » sends
                     'peux-tu ouvrir la pièce socle avec le logiciel FreeCAD ?'):
            self.assertEqual(apps.match(text), socle, text)
        self.assertEqual(apps.match('ouvre abat_jour dans FreeCAD')['fichier'], 'sortie/abat_jour.step')   # its file name
        # The tool is known by its program (kicad.exe), whatever the list calls it; each tool has its own files.
        self.assertEqual(apps.match('ouvre la carte de la lampe dans Mon KiCad'),
                         {'app': 'Mon KiCad', 'projet': 'lampe-de-bureau', 'fichier': 'carte/lampe.kicad_pro'})
        for text in ('ouvre le socle dans Mon KiCad', 'ouvre la carte de la lampe dans FreeCAD',
                     'ouvre le calcul dans FreeCAD', 'ouvre le socle du robot dans FreeCAD',
                     'ouvre le socle dans Creality Print', 'ouvre le socle dans Blender'):
            self.assertIsNone(apps.match(text), text)
        self.assertEqual(apps.match('ouvre FreeCAD'), {'app': 'FreeCAD 1.1'})

    @needs_robot
    def test_the_shipped_quadruped_gives_its_step_files(self):
        self.assertEqual(apps.match('ouvre le tibia du quadrupède dans FreeCAD'),
                         {'app': 'FreeCAD 1.1', 'projet': 'robot-quadrupede', 'fichier': 'sortie/patte/tibia.step'})

    async def test_the_file_of_the_project_is_given_to_the_program(self):
        res = await self.hand('FreeCAD', 'sortie/socle.step')
        self.assertTrue(res.ok, res.error)
        self.assertEqual(self.started, [(str(self.freecad), str((self.project / 'sortie' / 'socle.step').resolve()))])
        for said in ('« Socle » du projet « Lampe de bureau »', 'dans « FreeCAD 1.1 »', 'AURORA ne modifie pas ce fichier',
                     'enregistrez vos retouches sous un autre nom'):   # a made file is made again by its script
            self.assertIn(said, res.summary)
        self.assertEqual((res.data['projet'], res.data['fichier']), ('lampe-de-bureau', 'sortie/socle.step'))
        res = await self.hand('Mon KiCad', 'carte/lampe.kicad_pro')
        self.assertTrue(res.ok, res.error)
        self.assertNotIn('retouches', res.summary)
        self.assertEqual(self.started[1][0], str(self.kicad.resolve()))

    async def test_what_is_refused(self):
        cases = (('FreeCAD', 'carte/lampe.kicad_pro', 'n’ouvre pas un fichier .kicad_pro de projet'),
                 ('Mon KiCad', 'sortie/socle.step', 'n’ouvre pas un fichier .step de projet'),
                 ('Creality Print', 'sortie/socle.step', 'je ne donne un fichier de projet qu’à FreeCAD'),
                 ('FreeCAD', 'calcul.py', 'n’ouvre pas un fichier .py de projet'),        # never a script
                 ('FreeCAD', 'sortie/abat_jour.step', 'n’est pas sur le disque. Il est fabriqué par un script'),
                 ('FreeCAD', 'sortie/absent.step', 'ne déclare aucun fichier'),            # only what the project declares
                 ('FreeCAD', '../../aurora.db', 'ne déclare aucun fichier'),
                 ('Blender', 'sortie/socle.step', 'n’est pas dans votre liste'))
        for name, fichier, said in cases:
            res = await self.hand(name, fichier)
            self.assertFalse(res.ok, (name, fichier))
            self.assertIn(said, res.error, (name, fichier))
        res = await self.hand('FreeCAD', 'sortie/socle.step', projet='absent')
        self.assertIn('ne déclare aucun fichier', res.error)
        res = await self.hand('FreeCAD', 'sortie/socle.step', trial=True)
        self.assertIn('Instance d’essai : je n’ai rien lancé', res.error)
        self.assertIn('« Socle » du projet « Lampe de bureau »', res.error)
        self.assertEqual(self.started, [])

    def test_the_plan_carries_the_project_file(self):
        slots = {'app': 'FreeCAD 1.1', 'projet': 'lampe-de-bureau', 'fichier': 'sortie/socle.step'}
        built = planner_mod.Planner().build({'intent': 'app.launch', 'slots': slots}, {'engine': 'tool'}, text='x',
                                            settings=config.default_settings())
        found = [s for s in built if s['stage'] == 'tool']
        self.assertEqual([(s['tool'], s['params'], s['requires_permission']) for s in found], [('app.launch', {
            'name': 'FreeCAD 1.1', 'projet': 'lampe-de-bureau', 'fichier': 'sortie/socle.step'}, False)])
        self.assertIn('avec un fichier de projet', found[0]['title'])

    def test_the_design_tools_of_the_page(self):
        by_id = {t['id']: t for t in apps.design_tools()}
        self.assertEqual((by_id['freecad']['autorise'], by_id['kicad']['autorise']), ('FreeCAD 1.1', 'Mon KiCad'))
        self.assertEqual(by_id['freecad']['ouvre'], ['.fcstd', '.step', '.stp'])
        # Not in the list yet: the shortcuts of this PC that run the program are named, nothing is added.
        config.update_settings({'allowed_apps': []})
        desk = self.folder / 'Bureau'
        make_lnk(desk / 'KiCad 10.0.lnk', str(self.kicad))
        make_lnk(desk / 'Éditeur de schémas 10.0.lnk', str(self.folder / 'CrealityPrint.exe'))   # another program
        with mock.patch.object(apps, '_pc_folders', return_value=[('Bureau', desk, False)]):
            by_id = {t['id']: t for t in apps.design_tools()}
        self.assertEqual((by_id['kicad']['autorise'], by_id['kicad']['sur_ce_pc']), (None, ['KiCad 10.0']))
        self.assertEqual((by_id['freecad']['autorise'], by_id['freecad']['sur_ce_pc']), (None, []))
        self.assertEqual(config.load_settings()['allowed_apps'], [])


def make_pc(folder: Path) -> list[tuple[str, Path, bool]]:
    """A Desktop and a Start menu, as _pc_folders gives them, with what a real PC holds."""
    tools = folder / 'tools'
    tools.mkdir(parents=True)
    for exe in ('cmd.exe', 'python.exe', 'pythonw.exe', 'obs64.exe'):
        (tools / exe).write_bytes(b'')
    desk, start = folder / 'Desktop', folder / 'Start' / 'Programs'
    make_lnk(desk / 'YouTube Manager IA.lnk', str(tools / 'cmd.exe'), r'/k "C:\vita1\yt\start_youtube.bat"')
    make_lnk(desk / 'OBS Studio.lnk', str(tools / 'obs64.exe'))
    make_lnk(desk / 'Invite de commandes.lnk', str(tools / 'cmd.exe'))          # a terminal
    make_lnk(desk / 'Mémoire.lnk', str(tools / 'obs64.exe'))                    # a page of AURORA
    make_lnk(desk / 'Ancien jeu.lnk', str(tools / 'absent.exe'))                # broken
    (desk / 'Polyedge.bat').write_text('@echo off\r\n', 'utf-8')
    (desk / 'notes.txt').write_text('x', 'utf-8')                                 # not a program
    make_lnk(start / 'OBS Studio (64bit).lnk', str(tools / 'obs64.exe'))         # same program as the Desktop one
    make_lnk(start / 'OBS Studio' / 'Uninstall OBS.lnk', str(tools / 'obs64.exe'), '/uninstall.bat')
    make_lnk(start / 'Administrative Tools' / 'Registry Editor.lnk', str(tools / 'obs64.exe'))
    make_lnk(start / 'Python 3.11' / 'Python 3.11 (64-bit).lnk', str(tools / 'python.exe'))   # bare interpreter
    make_lnk(start / 'Python 3.11' / 'IDLE (Python 3.11 64-bit).lnk', str(tools / 'pythonw.exe'),
             r'"C:\Python\Lib\idlelib\idle.pyw"')
    (start / 'Site.url').write_text('[InternetShortcut]\r\nURL=https://exemple.com\r\n', 'utf-8')
    (start / 'Polyedge.lnk').write_bytes(b'')   # same name as the Desktop one: the Desktop one is kept
    return [('Bureau', desk, False), ('Bureau', folder / 'absent', False), ('Menu Démarrer', start, True)]


class PcAppsTests(unittest.TestCase):
    def test_the_apps_of_the_pc(self):
        with mock.patch.object(apps, '_pc_folders', return_value=make_pc(Path(tempfile.mkdtemp()))):
            found = apps.pc_apps()
        names = [a['name'] for a in found['apps']]
        self.assertEqual(names, ['IDLE (Python 3.11 64-bit)', 'OBS Studio', 'Polyedge', 'Site', 'YouTube Manager IA'])
        by_name = {a['name']: a for a in found['apps']}
        youtube = by_name['YouTube Manager IA']
        self.assertEqual((youtube['source'], youtube['detail'], youtube['url']),
                         ('Bureau', 'lance start_youtube.bat', 'http://localhost:3000'))   # Athéna's address
        self.assertEqual((by_name['IDLE (Python 3.11 64-bit)']['source'], by_name['IDLE (Python 3.11 64-bit)']['detail']),
                         ('Menu Démarrer', 'lance idle.pyw'))
        self.assertEqual(by_name['OBS Studio']['source'], 'Bureau')
        self.assertEqual(by_name['Polyedge']['detail'], 'Polyedge.bat')
        self.assertEqual(found['skipped'], {'terminal ou outil protégé': 3,
                                            'installation, désinstallation ou documentation': 1,
                                            'outil système de Windows': 1, 'raccourci cassé': 1})
        self.assertEqual(len(found['folders']), 2)   # the missing folder is not listed

    def test_each_app_offered_is_accepted_by_the_list(self):
        with TempEnv(), mock.patch.object(apps, '_pc_folders', return_value=make_pc(Path(tempfile.mkdtemp()))):
            offered = apps.pc_apps()['apps']
            config.update_settings({'allowed_apps': [{k: a[k] for k in ('name', 'path', 'url')} for a in offered]})
            self.assertEqual(len(config.load_settings()['allowed_apps']), len(offered))


from tests.test_api import ApiTestCase   # noqa: E402  (after the doubles above: its own imports come first)


class RouteTests(ApiTestCase):
    def test_the_pc_list_route(self):
        with mock.patch.object(apps, '_pc_folders', return_value=make_pc(Path(tempfile.mkdtemp()))):
            res = self.client.get('/api/apps/pc')
            self.assertEqual(res.status_code, 200)
            self.assertIn('YouTube Manager IA', [a['name'] for a in res.json()['apps']])
            self.assertEqual(self.client.get('/api/apps/pc', headers={'X-Aurora-Token': ''}).status_code, 401)
        self.assertEqual(config.load_settings()['allowed_apps'], [])   # reading the PC adds nothing

    def test_the_design_tools_route(self):
        with mock.patch.object(apps, '_pc_folders', return_value=make_pc(Path(tempfile.mkdtemp()))):
            res = self.client.get('/api/apps/conception')
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual([(t['id'], t['autorise'], t['sur_ce_pc']) for t in res.json()['outils']],
                             [('freecad', None, []), ('kicad', None, [])])
            self.assertEqual(self.client.get('/api/apps/conception', headers={'X-Aurora-Token': ''}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
