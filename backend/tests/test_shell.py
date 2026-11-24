"""Terminal avec autorisation (B2, 04/11/2026) : tools/shell.py, l'extraction de la commande et le
planificateur. Les commandes réelles lancées ici sont inoffensives (l'interpréteur Python courant) et
chaque test part d'une base temporaire (TempEnv). Demande d’ID Studio IA : « le terminal pour le code ».
"""
from __future__ import annotations

import sys
import unittest

from aurora import config
from aurora.core import planner as planner_mod
from aurora.core.normalize import extract_command
from aurora.security import consent
from aurora.tools import shell
from aurora.tools.base import run_tool
from tests.doubles_core import TempEnv

PRINT_OK = f'"{sys.executable}" -c "print(\'aurora-shell-ok\')"'
SLEEP_5 = f'"{sys.executable}" -c "import time; time.sleep(5)"'


class ExtractCommandTests(unittest.TestCase):
    def test_markers_keep_the_command_verbatim(self):
        cases = {
            'exécute : git status': ('git status', None),
            'Exécute la commande git commit -m "Fix: A/B"': ('git commit -m "Fix: A/B"', None),
            'terminal : npm run build': ('npm run build', None),
            'lance la commande python -m pytest': ('python -m pytest', None),
            'Aurora, shell > dir C:\\Users': ('dir C:\\Users', None),
            'exécute : npm test dans C:\\Users\\moi\\projet': ('npm test', 'C:\\Users\\moi\\projet'),
        }
        for text, (command, cwd) in cases.items():
            got = extract_command(text)
            self.assertEqual(got['command'], command, text)
            self.assertEqual(got['cwd'], cwd, text)

    def test_not_a_command(self):
        for text in ('exécute le plan', 'lance une recherche sur la NASA', 'raconte une blague',
                     'ouvre le terminal', 'quelle heure est-il ?'):
            self.assertIsNone(extract_command(text)['command'], text)


class HelperTests(unittest.TestCase):
    def test_risk_is_code_derived(self):
        self.assertEqual(shell.risk_of('del vieux.txt')[0], 'suppression')
        self.assertEqual(shell.risk_of('git clone https://x')[0], 'réseau')
        self.assertEqual(shell.risk_of('mkdir build')[0], 'modification')
        self.assertEqual(shell.risk_of('git status')[0], 'lecture')

    def test_what_it_does(self):
        self.assertIn('Git', shell.what_it_does('git log'))
        self.assertIn('Node', shell.what_it_does('npm test'))

    def test_blacklist(self):
        for bad in ('format C: /q', 'diskpart', 'bcdedit /set x', 'reg delete HKLM\\System', 'rmdir /s C:\\x',
                    'Remove-Item -Recurse C:\\x', 'Set-MpPreference -DisableRealtimeMonitoring $true',
                    'shutdown /s /t 0', 'rm -rf /'):
            self.assertTrue(shell.blacklisted(bad), bad)
        for ok in ('git status', 'npm run build', 'python -m pytest', 'echo bonjour', 'dir'):
            self.assertEqual(shell.blacklisted(ok), '', ok)


class PlannerTests(unittest.TestCase):
    def test_shell_steps_are_a_read_only_preview_then_a_permissioned_run(self):
        steps = planner_mod.Planner(needs_permission=lambda a: a in ('shell.run',)).shell_steps('git status')
        self.assertEqual([s['stage'] for s in steps], ['tool', 'tool'])
        self.assertEqual([s['tool'] for s in steps], ['shell.run', 'shell.run'])
        self.assertTrue(steps[0]['params']['dry_run'])
        self.assertFalse(steps[0]['requires_permission'])          # the preview asks nothing
        self.assertFalse(steps[1]['params'].get('dry_run'))
        self.assertTrue(steps[1]['requires_permission'])           # the real run needs the user's accord
        self.assertEqual(steps[1].get('derive'), 'shell_from_dry_run')


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def _grant(self, command: str) -> int:
        item = consent.create_request(task_id=None, step_id=None, action='shell.run',
                                      title='Exécuter', preview={'command': command})
        consent.decide(int(item['id']), 'grant')
        return int(item['id'])

    async def test_disabled_by_default_never_runs(self):
        res = await shell.run(PRINT_OK, dry_run=True)
        self.assertFalse(res.ok)
        self.assertTrue(res.data['disabled'])
        self.assertIn('désactivé', res.summary)

    async def test_dry_run_preview_once_enabled(self):
        config.update_settings({'shell_enabled': True})
        res = await shell.run('git status', dry_run=True)
        self.assertTrue(res.ok)
        self.assertEqual(res.data['risk'], 'lecture')
        self.assertFalse(res.data['disabled'])

    async def test_outside_allowed_flag(self):
        config.update_settings({'shell_enabled': True})
        outside = await shell.run('git status', cwd=str(self.env.dir), dry_run=True)   # temp dir, outside
        self.assertTrue(outside.data['outside_allowed'])
        from aurora.tools.files import allowed_roots   # a real allowed root (Documents/Downloads/Desktop)
        roots = allowed_roots()
        if roots:
            inside = await shell.run('git status', cwd=str(roots[0]['path']), dry_run=True)
            self.assertFalse(inside.data['outside_allowed'])

    async def test_real_run_needs_permission(self):
        config.update_settings({'shell_enabled': True})
        res = await shell.run(PRINT_OK)   # no permission_id
        self.assertFalse(res.ok)
        self.assertIn('Autorisation', res.summary)

    async def test_real_run_executes_and_captures_output(self):
        config.update_settings({'shell_enabled': True})
        pid = self._grant(PRINT_OK)
        res = await shell.run(PRINT_OK, permission_id=pid)
        self.assertTrue(res.ok, res.summary)
        self.assertEqual(res.data['returncode'], 0)
        self.assertIn('aurora-shell-ok', res.data['stdout'])

    async def test_blacklisted_refused_even_with_permission(self):
        config.update_settings({'shell_enabled': True})
        pid = self._grant('diskpart')
        res = await shell.run('diskpart', permission_id=pid)
        self.assertFalse(res.ok)
        self.assertIn('formatage', res.summary)
        self.assertNotIn('returncode', res.data)   # never launched

    async def test_timeout_kills_the_command(self):
        config.update_settings({'shell_enabled': True})
        pid = self._grant(SLEEP_5)
        res = await shell.run(SLEEP_5, permission_id=pid, timeout_s=1)
        self.assertFalse(res.ok)
        self.assertTrue(res.data['timed_out'])

    async def test_command_changed_since_the_preview_is_refused(self):
        config.update_settings({'shell_enabled': True})
        pid = self._grant('echo a')
        res = await run_tool('shell.run', {'command': 'echo b', 'approved_command': 'echo a',
                                           'permission_id': pid})
        self.assertFalse(res.ok)
        self.assertIn('changé', res.summary)


class AgentShellTests(unittest.TestCase):
    """Option 2 (04/11/2026): the P1 agent may propose a command, only when the terminal is enabled, with the
    same card and guards, marked as proposed by the model."""

    def test_shell_in_the_agent_actions_only_when_enabled(self):
        from aurora.core import agent
        from aurora.tools.base import REGISTRY
        self.assertNotIn('shell.run', agent.actions(REGISTRY, shell=False))
        self.assertIn('shell.run', agent.actions(REGISTRY, shell=True))
        self.assertNotIn('shell.run', agent.actions(REGISTRY, shell=True, final=True))      # final turn: answer only
        self.assertNotIn('shell.run', agent.actions(REGISTRY, shell=True, toolset='data'))   # P3: analysis tools only

    def test_agent_command_builds_steps_marked_model_proposed(self):
        steps, note = planner_mod.Planner().llm_tool_steps('shell.run', {'command': 'git status'}, title='Commande',
                                                           registry=None, run_id=1, has_scan=False, shell=True)
        self.assertIsNone(note)
        self.assertTrue(steps[0]['params']['dry_run'])
        self.assertNotIn('dry_run', steps[1]['params'])
        self.assertEqual(steps[1]['params']['origin'], 'agent')     # AURORA sets the origin, not the model
        self.assertTrue(steps[1].get('from_llm_plan'))              # the card warns: proposed by the model
        self.assertTrue(steps[1]['requires_permission'])

    def test_agent_shell_refused_when_terminal_disabled(self):
        steps, note = planner_mod.Planner().llm_tool_steps('shell.run', {'command': 'git status'}, title='x',
                                                           registry=None, run_id=1, has_scan=False, shell=False)
        self.assertEqual(steps, [])
        self.assertIn('non autorisé', note)

    def test_agent_shell_blacklist_refused_before_any_card(self):
        steps, note = planner_mod.Planner().llm_tool_steps('shell.run', {'command': 'format C: /q'}, title='x',
                                                           registry=None, run_id=1, has_scan=False, shell=True)
        self.assertEqual(steps, [])
        self.assertIn('formatage', note)


if __name__ == '__main__':
    unittest.main()
