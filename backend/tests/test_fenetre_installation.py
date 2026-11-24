"""The installation window (fenetre-installation.ps1, 22/11/2026) reads what installer-aurora.ps1 writes and shows the
step in course. Tried here without any window: « -Analyser <journal> » reads a recorded output and prints the final
state. The journals of tests/installation are written with the installer's own sentences: a PC with no Python, a
check on a card of 8 GB, a whole installation, and pip failing. Nothing is installed and nothing is downloaded."""
import json
import os
import subprocess
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
WINDOW = PROJECT / 'fenetre-installation.ps1'
JOURNALS = Path(__file__).resolve().parent / 'installation'


def final_state(journal: str, code: int = 0) -> dict:
    done = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(WINDOW),
                           '-Analyser', str(JOURNALS / journal), '-CodeRejoue', str(code)],
                          capture_output=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr.decode('utf-8', 'replace'))
    return json.loads(done.stdout.decode('utf-8'))


@unittest.skipUnless(os.name == 'nt', 'the installation window is a Windows PowerShell script')
class WhatTheInstallationWindowReads(unittest.TestCase):
    def test_a_pc_without_python_is_told_what_is_missing_and_where_to_take_it(self):
        state = final_state('verification-python-manque.txt', 1)
        self.assertEqual(state['phase'], 'manque')
        self.assertEqual(state['prerequis'], ['Python'])
        self.assertEqual(state['etats']['1'], 'echec')
        self.assertEqual(state['etats']['2'], 'attente')

    def test_a_check_lists_what_would_be_downloaded_before_anything_is(self):
        state = final_state('verification-carte-8-go.txt')
        self.assertEqual(state['phase'], 'pret')
        self.assertEqual(state['aTelecharger'], ['bibliotheques', 'interface'])
        self.assertEqual(state['modeles'], ['qwen3.5:4b', 'bge-m3'])
        self.assertEqual(state['prerequis'], [])
        self.assertIn('GTX 1080', state['carte'])
        self.assertEqual(state['etats']['1'], 'fait')
        self.assertEqual(state['etats']['3'], 'a faire')

    def test_a_whole_installation_ends_with_every_step_done_and_what_is_left_to_do(self):
        state = final_state('installation-reussie.txt')
        self.assertEqual((state['phase'], state['fin']), ('fin', 'ok'))
        self.assertEqual(set(state['etats'].values()), {'fait'})
        # 23/11/2026: on a card under 10 GB AURORA takes the installed model itself; nothing is left to set by hand.
        self.assertEqual(state['remarques'], [])
        self.assertTrue(state['termine'])

    # 23/11/2026, ID Studio IA: « La bibliothèque de la voix reste obligatoire : si elle refuse de s'installer, tout s'arrête. »
    # The voice (backend/requirements-voix.txt) is now installed after the rest, and its failure is a remark.
    def test_the_voice_failing_does_not_stop_the_installation(self):
        state = final_state('installation-voix-non-installee.txt')
        self.assertEqual((state['phase'], state['fin']), ('fin', 'ok'))
        self.assertEqual(set(state['etats'].values()), {'fait'})
        self.assertEqual(len(state['remarques']), 1)
        self.assertIn('Voix neuronale, facultative', state['remarques'][0])

    def test_pip_failing_stops_at_its_step_and_keeps_its_words(self):
        state = final_state('installation-pip-echoue.txt', 1)
        self.assertEqual((state['phase'], state['fin']), ('fin', 'erreur'))
        self.assertEqual(state['etats']['3'], 'echec')
        self.assertEqual(state['etats']['4'], 'attente')
        self.assertTrue(any('pip a échoué' in line for line in state['dernieres']))

    def test_a_failing_end_is_never_shown_as_installed_whatever_the_journal_says(self):
        state = final_state('installation-reussie.txt', 1)
        self.assertEqual(state['fin'], 'erreur')

    # 23/11/2026, ID Studio IA's garage PC: Python 3.15.2 there, for which the voice library does not exist; pip stopped. The
    # installer now takes the programs itself (« [à prendre] … »), and a check lists them as downloads, not as a stop.
    def test_a_python_too_recent_is_a_download_to_come_not_a_stop(self):
        state = final_state('verification-python-trop-recent.txt')
        self.assertEqual(state['phase'], 'pret')
        self.assertEqual(state['programmes'], ['Python'])
        self.assertEqual(state['prerequis'], [])
        self.assertEqual(state['aTelecharger'], ['bibliotheques', 'interface'])
        self.assertEqual(state['modeles'], [])
        self.assertEqual((state['etats']['1'], state['etats']['2']), ('a faire', 'a faire'))

    def test_a_new_pc_lists_the_three_programs_and_the_models_before_anything_is_taken(self):
        state = final_state('verification-pc-neuf.txt')
        self.assertEqual(state['phase'], 'pret')
        self.assertEqual(state['programmes'], ['Python', 'Node.js', 'Ollama'])
        self.assertEqual(state['modeles'], ['qwen3.5:9b', 'bge-m3', 'qwen3.5:4b'])
        self.assertEqual(state['prerequis'], [])

    def test_a_program_taken_by_the_installer_ends_like_any_installation(self):
        state = final_state('installation-programme-pris.txt')
        self.assertEqual((state['phase'], state['fin']), ('fin', 'ok'))
        self.assertEqual(set(state['etats'].values()), {'fait'})
        self.assertEqual(state['programmes'], ['Python'])
        self.assertEqual(len(state['remarques']), 1)
        self.assertIn('mis de côté', state['remarques'][0])

    def test_a_program_that_could_not_be_taken_stops_there_with_its_page(self):
        state = final_state('installation-programme-non-pris.txt', 1)
        self.assertEqual((state['phase'], state['fin']), ('fin', 'erreur'))
        self.assertEqual(state['prerequis'], ['Python'])
        self.assertEqual(state['etats']['1'], 'echec')
        self.assertEqual(state['etats']['2'], 'attente')
        self.assertTrue(any('téléchargement a échoué' in line for line in state['dernieres']))


if __name__ == '__main__':
    unittest.main()
