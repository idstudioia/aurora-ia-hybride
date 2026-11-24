"""Tool map of the page Tâches (core/capabilities.tool_map, GET /api/tools/map): « elle ne peut pas mentir ».

Read from the real registry, never from a list written here. No network: the capabilities are written by hand for
the function, the route runs offline (AURORA_OFFLINE=1), both on a temporary database. No tool is ever run by the map.
"""
import asyncio
import logging
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from aurora import config, db
from aurora.core import capabilities, planner, router, semantic
from aurora.core.intents import INTENTS
from aurora.security import policy
from aurora.tools import apps, base, studio, youtube_comments
from aurora.tools.base import ToolError, run_tool
from tests.doubles_core import TempEnv
from tests.test_api import NO_TOKEN, ApiTestCase

STATES = {'available', 'disabled', 'absent'}
TOOL_KEYS = {'name', 'title', 'family', 'group', 'description', 'state', 'reason', 'needs', 'example', 'uses',
             'last_used_at'}
TABLES = ('events', 'settings', 'tasks', 'task_steps', 'permission_requests', 'memories', 'conversations')


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def caps(**over) -> dict:
    """What snapshot() gives, written by hand: the map itself never probes anything."""
    return {'mode': 'equilibre', 'internet': True, 'shell': False, 'pc_control': False,
            'checked_at': '2026-11-11T10:00:00.000Z', **over}


def online():
    """Lifts the « hors ligne » of the tests for the map only: no tool is run, so nothing can leave."""
    return mock.patch.dict(os.environ, {'AURORA_OFFLINE': '0'})


def fake(name: str, **over) -> SimpleNamespace:
    return SimpleNamespace(**{'name': name, 'description': f'outil {name}', 'action': 'system.read',
                              'network': False, **over})


class MapCase(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)

    def carte(self, uses: dict | None = None, **over) -> dict:
        with online():
            return capabilities.tool_map(caps(**over), uses={} if uses is None else uses)

    def tool(self, name: str, **over) -> dict:
        return next(t for t in self.carte(**over)['tools'] if t['name'] == name)


class RegistryTests(MapCase):
    def test_every_tool_of_the_registry_is_on_the_map_once(self):
        carte = self.carte()
        self.assertEqual(set(carte), {'checked_at', 'mode', 'internet', 'groups', 'families', 'tools', 'absent_modules',
                                      'usage'})
        self.assertEqual([t['name'] for t in carte['tools']], sorted(base.REGISTRY))
        families = {f['id'] for f in carte['families']}
        for tool in carte['tools']:
            spec = base.REGISTRY[tool['name']]
            self.assertEqual(set(tool), TOOL_KEYS, tool['name'])
            self.assertEqual(tool['description'], spec.description)             # its own words, not rewritten
            self.assertEqual(tool['needs']['internet'], spec.network, tool['name'])
            self.assertEqual(tool['title'], planner.TOOL_TITLES.get(tool['name'], tool['name']))
            self.assertIn(tool['family'], families, tool['name'])
            self.assertIn(tool['state'], STATES, tool['name'])
            if tool['state'] != 'available':
                self.assertTrue(tool['reason'], tool['name'])                  # never « absent » without saying why
        self.assertEqual((carte['checked_at'], carte['mode'], carte['internet']),
                         ('2026-11-11T10:00:00.000Z', 'equilibre', True))
        self.assertEqual(carte['absent_modules'], [])

    def test_no_real_tool_is_left_without_a_family(self):
        strays = [t['name'] for t in self.carte()['tools'] if t['family'] == capabilities.OTHER_FAMILY[0]]
        self.assertEqual(strays, [], 'outil sans famille : ajouter son préfixe à CAPABILITY_GROUPS ou TOOL_MAP_GROUPS')

    def test_families_come_from_the_capability_groups(self):
        carte = self.carte()
        ids = [f['id'] for f in carte['families']]
        self.assertEqual(len(ids), len(set(ids)))
        by_title = {f['title']: f for f in carte['families']}
        for title, what, prefixes in capabilities.CAPABILITY_GROUPS:
            family = by_title[title]                                            # every line is there, same words
            self.assertEqual((family['description'], family['builtin']), (what, not prefixes), title)
            members = [t['name'] for t in carte['tools'] if t['family'] == family['id']]
            self.assertEqual(family['tools'], len(members), title)
            self.assertEqual(members, [n for n in sorted(base.REGISTRY) if prefixes and n.startswith(prefixes)], title)
        self.assertEqual((by_title['Terminal']['id'], by_title['Terminal']['tools']), ('shell', 1))
        self.assertEqual(by_title['Mémoire'], {'id': 'memoire', 'title': 'Mémoire', 'group': 'memoire', 'builtin': True,
                                                'tools': 0, 'description': by_title['Mémoire']['description']})
        self.assertEqual(sum(f['tools'] for f in carte['families']), len(base.REGISTRY))

    def test_every_family_is_in_one_large_family_and_only_one(self):
        # 11/11/2026 (ID Studio IA : « Oui, regroupe ») : the grouping is written by hand, so it is checked against the families.
        named = [title for _gid, _title, _what, members in capabilities.TOOL_MAP_LARGE for title in members]
        self.assertEqual(len(named), len(set(named)), 'une famille est rangée dans deux grandes familles')
        self.assertEqual(sorted(named), sorted(title for title, _what, _prefixes in capabilities.TOOL_MAP_GROUPS),
                         'famille absente de TOOL_MAP_LARGE, ou nom mal écrit')
        ids = [gid for gid, *_rest in capabilities.TOOL_MAP_LARGE]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(capabilities.OTHER_FAMILY[0], ids)

    def test_large_families_gather_the_families_and_the_tools(self):
        carte = self.carte()
        self.assertEqual([g['id'] for g in carte['groups']], [gid for gid, *_rest in capabilities.TOOL_MAP_LARGE])
        self.assertEqual(sum(g['tools'] for g in carte['groups']), len(base.REGISTRY))
        of_family = {f['id']: f['group'] for f in carte['families']}
        self.assertEqual(sorted(fid for g in carte['groups'] for fid in g['families']), sorted(of_family))
        for group in carte['groups']:
            self.assertEqual({of_family[fid] for fid in group['families']}, {group['id']}, group['title'])
            self.assertEqual(group['tools'], sum(1 for t in carte['tools'] if t['group'] == group['id']), group['title'])
        for tool in carte['tools']:
            self.assertEqual(tool['group'], of_family[tool['family']], tool['name'])
        self.assertEqual((of_family['shell'], of_family['web'], of_family['memoire'], of_family['lab']),
                         ('agir', 'monde', 'memoire', '3d'))

    def test_an_unknown_prefix_is_shown_apart_and_an_empty_family_is_not_listed(self):
        carte = capabilities.tool_map(caps(), uses={}, registry={'zzz.new': fake('zzz.new')})
        self.assertEqual([(t['name'], t['family'], t['title'], t['state']) for t in carte['tools']],
                         [('zzz.new', 'autres', 'zzz.new', 'available')])
        listed = {f['id']: f for f in carte['families']}
        self.assertEqual(listed['autres']['tools'], 1)
        self.assertNotIn('weather', listed)             # no registered tool of that family: not announced
        self.assertTrue(listed['memoire']['builtin'])   # built into AURORA: always there, without tool
        # Large families: only those that hold a listed family, then the tools that none of them gathers, apart.
        # « 3d » is there since 23/11/2026: it holds « Projets », built into AURORA like the memory (the page exists
        # in every AURORA, with its button « Créer un projet »).
        self.assertTrue(listed['projets']['builtin'])
        self.assertEqual([(g['id'], g['tools']) for g in carte['groups']],
                         [('3d', 0), ('agir', 0), ('memoire', 0), ('autres', 1)])
        self.assertEqual(carte['tools'][0]['group'], 'autres')

    def test_a_tool_module_that_did_not_load_is_said_absent_with_its_reason(self):
        with mock.patch.dict(base.LOAD_ERRORS, {'sky': 'ImportError: No module named sgp4'}):
            absent = self.carte()['absent_modules']
        self.assertEqual([a['module'] for a in absent], ['sky'])
        self.assertIn('« sky »', absent[0]['reason'])
        self.assertIn('ImportError: No module named sgp4', absent[0]['reason'])


class StateTests(MapCase):
    def test_switches_of_parametres(self):
        off = caps()
        shell, gesture = self.tool('shell.run'), self.tool('pc.gesture')
        self.assertEqual((shell['state'], shell['reason']), ('disabled', capabilities.terminal_state(off)))
        self.assertIn('Paramètres › Terminal', shell['reason'])
        self.assertEqual((gesture['state'], gesture['reason']), ('disabled', capabilities.pc_control_state(off)))
        self.assertIn('Paramètres › Souris et clavier', gesture['reason'])
        self.assertEqual(self.tool('pc.pointer')['state'], 'available')   # read only: the switch does not gate it
        for name in ('shell.run', 'pc.gesture'):
            on = self.tool(name, shell=True, pc_control=True)
            self.assertEqual((on['state'], on['reason']), ('available', None), name)
            self.assertEqual((on['needs']['permission'], on['needs']['risk']), (True, 'high'), name)
        # the same switch as the tool itself: default settings, the real tool refuses with « désactivé »
        self.assertFalse(config.load_settings()['shell_enabled'])
        refused = asyncio.run(run_tool('shell.run', {'command': 'echo test', 'dry_run': True}))
        self.assertFalse(refused.ok)
        self.assertIn('désactivé', refused.error)

    def test_a_network_tool_follows_internet(self):
        self.assertEqual((self.tool('weather.current')['state'], self.tool('weather.current')['reason']),
                         ('available', None))
        down = self.tool('weather.current', internet=False)
        self.assertEqual(down['state'], 'absent')
        self.assertIn('Internet ne répond pas', down['reason'])
        offline = next(t for t in capabilities.tool_map(caps(), uses={})['tools'] if t['name'] == 'weather.current')
        self.assertEqual(offline['state'], 'absent')          # AURORA_OFFLINE=1 (TempEnv), whatever the probe says
        self.assertIn('Mode hors ligne', offline['reason'])
        local = next(t for t in capabilities.tool_map(caps(internet=False), uses={})['tools']
                     if t['name'] == 'calc.evaluate')
        self.assertEqual((local['state'], local['needs']['internet']), ('available', False))

    def test_private_mode(self):
        weather = self.tool('weather.current', mode='prive', internet=None)   # snapshot does not probe in private
        self.assertEqual(weather['state'], 'available')
        self.assertIn('non vérifié (mode privé)', weather['reason'])
        self.assertTrue(weather['needs']['permission'])
        self.assertEqual(weather['needs']['permission_reason'], policy.evaluate('web.fetch', mode='prive').reason)
        comment = self.tool('youtube.comment', mode='prive', internet=None)
        self.assertEqual((comment['state'], comment['reason']),
                         ('disabled', policy.evaluate('youtube.comment', mode='prive').reason))
        self.assertIsNone(comment['needs']['permission'])     # forbidden in this mode: no consent unlocks it

    def test_permission_comes_from_the_policy_of_the_current_mode(self):
        for name, spec in base.REGISTRY.items():
            for mode in policy.MODES:
                decision = policy.evaluate(spec.action, mode=mode)
                needs = self.tool(name, mode=mode, internet=None if mode == 'prive' else True)['needs']
                self.assertEqual(needs['permission'], decision.requires_permission if decision.allowed else None,
                                 (name, mode))
                self.assertEqual((needs['permission_reason'], needs['risk']), (decision.reason, decision.risk))

    def test_what_a_tool_lacks_is_said_with_its_own_words(self):
        launch = self.tool('app.launch')
        self.assertEqual((launch['state'], launch['reason']), ('absent', apps.EMPTY_LIST))
        refused = asyncio.run(run_tool('app.launch', {'name': 'Bloc-notes'}))      # the real tool, empty list
        self.assertTrue(refused.error.startswith(apps.EMPTY_LIST))
        with mock.patch.object(apps, 'allowed_apps', return_value=[{'name': 'Bloc-notes', 'path': 'C:/x.exe'}]):
            self.assertEqual(self.tool('app.launch')['state'], 'available')

        comment = self.tool('youtube.comment')
        self.assertEqual((comment['state'], comment['reason']), ('absent', youtube_comments.NOT_CONNECTED))
        with mock.patch.object(youtube_comments, 'status', return_value={'connected': True}):
            self.assertEqual(self.tool('youtube.comment')['state'], 'available')

        # 23/11/2026: a new AURORA has no account in its Studio (they were ID Studio IA's own, written in the code): the
        # reading is absent and says which field to fill. With accounts given (invented ones here), it is available.
        github = self.tool('studio.github')
        self.assertEqual((github['state'], github['reason']), ('absent', studio.NOT_INDICATED['github']))
        given = mock.patch.dict(studio.DEFAULTS, {'github_users': ['studio-exemple'],
                                                  'youtube_channel': 'UCexemple0000000000000AA',
                                                  'site_url': 'https://studio-exemple.org'})
        given.start()
        self.addCleanup(given.stop)
        self.assertEqual(self.tool('studio.github')['state'], 'available')
        for name, why, secret in (('studio.comments', studio.YOUTUBE_NOT_CONNECTED, 'youtube_key'),
                                  ('studio.site', studio.NETLIFY_NOT_CONNECTED, 'netlify_token')):
            found = self.tool(name)
            self.assertEqual((found['state'], found['reason']), ('absent', why), name)
            with mock.patch.object(studio, secret, return_value='x'):
                self.assertEqual(self.tool(name)['state'], 'available', name)
        with self.assertRaises(ToolError) as raised:                                # the reading itself, no key
            asyncio.run(studio.comments_overview(studio.load_settings().youtube_channel))
        self.assertEqual(raised.exception.message_fr, studio.YOUTUBE_NOT_CONNECTED)

    def test_studio_fields_left_empty(self):
        studio.save_settings({'github_users': [], 'release_repos': [], 'discord_invite': '', 'youtube_channel': '',
                              'site_url': '', 'netlify_site': ''})
        with mock.patch.object(studio, 'youtube_key', return_value='x'), \
                mock.patch.object(studio, 'netlify_token', return_value='x'):
            got = {name: self.tool(f'studio.{name}') for name in studio.SOURCES}
        expected = {'github': 'github', 'downloads': 'downloads', 'discord': 'discord', 'youtube': 'youtube',
                    'comments': 'youtube', 'site': 'site'}
        self.assertEqual(set(got), set(expected))
        for name, key in expected.items():
            self.assertEqual((got[name]['state'], got[name]['reason']), ('absent', studio.NOT_INDICATED[key]), name)
        with self.assertRaises(ToolError) as raised:                                # the reading says the same
            asyncio.run(studio.github_overview([]))
        self.assertEqual(raised.exception.message_fr, studio.NOT_INDICATED['github'])

    def test_a_check_that_fails_is_never_given_as_available(self):
        def broken():
            raise RuntimeError('base illisible')
        registry = {'zzz.broken': fake('zzz.broken', missing=broken),
                    'zzz.lacking': fake('zzz.lacking', missing=lambda: 'Il manque le fichier.'),
                    'zzz.ready': fake('zzz.ready', missing=lambda: None)}
        got = {t['name']: t for t in capabilities.tool_map(caps(), uses={}, registry=registry)['tools']}
        self.assertEqual(got['zzz.broken']['state'], 'absent')
        self.assertIn('n’a pas pu être vérifié', got['zzz.broken']['reason'])
        self.assertNotIn('base illisible', got['zzz.broken']['reason'])             # no internals in the answer
        self.assertEqual((got['zzz.lacking']['state'], got['zzz.lacking']['reason']), ('absent', 'Il manque le fichier.'))
        self.assertEqual((got['zzz.ready']['state'], got['zzz.ready']['reason']), ('available', None))


class UsesAndExamplesTests(MapCase):
    def test_uses_are_the_counted_ones_or_none(self):
        counted = {'calc.evaluate': {'uses': 4, 'last_used_at': '2026-11-10T08:00:00.000Z'}}
        with online():
            carte = capabilities.tool_map(caps(), uses=counted, since='2026-10-01T07:00:00.000Z')
        got = {t['name']: (t['uses'], t['last_used_at']) for t in carte['tools']}
        self.assertEqual(got.pop('calc.evaluate'), (4, '2026-11-10T08:00:00.000Z'))
        self.assertEqual(set(got.values()), {(0, None)})
        self.assertEqual(carte['usage'], {'source': 'journal', 'since': '2026-10-01T07:00:00.000Z',
                                          'note': capabilities.TOOL_USES_NOTE})
        unknown = capabilities.tool_map(caps(), uses=None)                          # no counter: never a made-up 0
        self.assertIsNone(unknown['usage'])
        self.assertEqual({t['uses'] for t in unknown['tools']}, {None})

    def test_an_example_is_a_real_sentence_that_the_router_sends_to_the_tool(self):
        trained = {(e['text'], e['intent']) for e in semantic.load_examples()}
        engine = router.DecisionEngine(use_semantic=False)                          # the rules alone, the default
        intent_of = {tool: intent for intent, tool in planner.SIMPLE_TOOL_INTENTS.items()}
        with mock.patch('aurora.tools.files.latest_quarantine', return_value=7):    # something to put back
            tools = self.carte()['tools']
            for tool in tools:
                name, example = tool['name'], tool['example']
                intent = intent_of.get(name) or (name if name in INTENTS else None)
                if intent is None:
                    self.assertIsNone(example, name)                                # nothing in the code: nothing said
                    continue
                self.assertIn((example, intent), trained, name)                     # a real sentence, never a new one
                decision = engine.decide_fast(example)
                self.assertEqual((decision.intent, decision.method), (intent, 'rule'), (name, example))
                self.assertGreaterEqual(decision.confidence, router.RULE_CONFIDENT, (name, example))
                steps = planner.Planner().build(decision.to_dict(), {'engine': 'tool'}, text=example,
                                                settings=config.load_settings())
                self.assertIn(name, [s['tool'] for s in steps if s['stage'] == 'tool'], (name, example))
        given = {t['name']: t['example'] for t in tools if t['example']}
        self.assertEqual(len(given), 18, sorted(given))   # 16/11/2026: + reminder.set (C3)
        self.assertEqual(given['reminder.set'], 'Rappelle-moi à 18 h d\'appeler Paul')
        self.assertEqual(given['system.snapshot'], 'Combien de mémoire vive me reste-t-il ?')
        # the first news.latest sentence is answered with news.world: not given for news.latest
        self.assertEqual(given['news.latest'], 'Donne-moi les dernières nouvelles scientifiques')
        self.assertIsNone(self.tool('sky.planes')['example'])
        # true now: with nothing in quarantine, the sentence would not reach files.restore; nor a comment in private
        self.assertIsNone(self.tool('files.restore')['example'])
        self.assertTrue(self.tool('youtube.comment')['example'])
        config.update_settings({'mode': 'prive'})
        self.assertIsNone(self.tool('youtube.comment', mode='prive', internet=None)['example'])


class RouteTests(ApiTestCase):
    def counts(self) -> dict:
        return {table: db.row(f'SELECT COUNT(*) AS n FROM {table}')['n'] for table in TABLES}

    def test_route_is_locked_and_reads_only(self):
        self.assertEqual(self.client.get('/api/tools/map', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.post('/api/tools/map', headers=self.auth).status_code, 405)
        before = self.counts()
        res = self.client.get('/api/tools/map')
        self.assertEqual(res.status_code, 200, res.text)
        carte = res.json()
        self.assertEqual(set(carte), {'checked_at', 'mode', 'internet', 'groups', 'families', 'tools', 'absent_modules',
                                      'usage'})
        self.assertEqual([t['name'] for t in carte['tools']], sorted(base.REGISTRY))   # the real registry
        self.assertEqual((carte['mode'], carte['internet']), ('equilibre', False))     # offline: measured
        by_name = {t['name']: t for t in carte['tools']}
        self.assertEqual(by_name['weather.current']['state'], 'absent')
        self.assertIn('Mode hors ligne', by_name['weather.current']['reason'])
        self.assertEqual(by_name['calc.evaluate']['state'], 'available')
        self.assertEqual(by_name['shell.run']['state'], 'disabled')
        self.assertEqual(self.tools.calls, [])               # no tool was run to draw the map
        self.assertEqual(self.counts(), before)              # and nothing was written

    def test_a_switch_turned_on_in_parametres_shows_at_once(self):
        self.assertEqual(self.client.patch('/api/settings', json={'shell_enabled': True}).status_code, 200)
        shell = next(t for t in self.client.get('/api/tools/map').json()['tools'] if t['name'] == 'shell.run')
        self.assertEqual((shell['state'], shell['reason']), ('available', None))

    def test_uses_are_the_real_ones_of_the_journal(self):
        def snapshot() -> dict:
            carte = self.client.get('/api/tools/map').json()
            return {**next(t for t in carte['tools'] if t['name'] == 'system.snapshot'), 'usage': carte['usage']}
        first = snapshot()
        self.assertEqual((first['uses'], first['last_used_at']), (0, None))
        run = self.client.post('/api/ask', json={'text': 'Quelle quantité de RAM est utilisée ?'}).json()
        self.assertEqual(run['status'], 'done')
        used = snapshot()
        self.assertEqual(used['uses'], 1)
        self.assertTrue(used['last_used_at'])
        self.assertEqual(used['usage']['source'], 'journal')
        self.assertTrue(used['usage']['since'])
        rings = next(t for t in self.client.get('/api/memories/graph').json()['tools'] if t['name'] == 'system.snapshot')
        self.assertEqual({k: used[k] for k in rings}, rings)   # same name, title and count as the rings of Mémoire
        self.assertEqual(self.client.delete('/api/events').status_code, 204)
        emptied = snapshot()                                   # journal emptied: said, not hidden
        self.assertEqual((emptied['uses'], emptied['last_used_at'], emptied['usage']['since']), (0, None, None))


if __name__ == '__main__':
    unittest.main()
