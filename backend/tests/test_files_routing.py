"""Defects 3, 4 and 5 of reviews/SYNTHESE.md (step 1, agent « fichiers »): « Temp » in a path read as a
temperature, « quelle temp fait le CPU » sent to the weather, paths (/ and \\) misunderstood, size criteria
(« plus de 500 Mo », « gros fichiers ») sent to the web search.

Real router, planner and files tool; temporary AURORA_HOME / AURORA_DB_PATH, temporary folders only.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora.core.normalize import (DEFAULT_LARGE_FILE_MB, extract_folder, extract_metric, extract_min_size_mb,
                                   extract_slots, normalize)
from aurora.core.planner import Planner
from aurora.core.router import DecisionEngine

ENGINE = DecisionEngine(use_semantic=False)
_SANDBOX: tempfile.TemporaryDirectory | None = None
_ENV = None


def setUpModule():
    """Never let anything open the user's real ~/.aurora."""
    global _SANDBOX, _ENV
    _SANDBOX = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    sandbox = Path(_SANDBOX.name)
    _ENV = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(sandbox / 'files_routing.db'),
                                        'AURORA_HOME': str(sandbox / 'home'),
                                        'ASTRA_DB_PATH': str(sandbox / 'no_astra.db'), 'AURORA_OFFLINE': '1'})
    _ENV.start()


def tearDownModule():
    _ENV.stop()
    _SANDBOX.cleanup()


def plan_tools(text: str) -> tuple[object, list[tuple[str, dict]]]:
    decision = ENGINE.decide_fast(text)
    steps = Planner().build(decision.to_dict(), {'engine': 'tool'}, text=text, settings={})
    return decision, [(s['tool'], s['params']) for s in steps if s['stage'] == 'tool']


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- defect 3: temperature

class TemperatureTests(unittest.TestCase):
    def test_temp_inside_a_path_is_never_a_temperature(self):
        for text in ('Montre-moi les fichiers de plus de 1 Go dans C:/Windows/Temp',
                     'Montre-moi les fichiers dans C:/Windows/Temp', 'Analyse le dossier C:\\Windows\\Temp',
                     'analyse le dossier c:\\windows\\temp'):
            norm = normalize(text)
            self.assertNotIn('temperature', norm, text)
            self.assertNotIn('windows', norm, text)
            decision = ENGINE.decide_fast(text)
            self.assertEqual(decision.intent, 'files.scan', text)
            self.assertNotIn('metric', decision.slots, text)

    def test_hardware_temp_goes_to_the_system_side(self):
        for text in ('Quelle temp fait le CPU ?', 'quelle temp fait le processeur', 'Quelle est la temp du CPU ?',
                     'temp du processeur', 'temp du PC', 'La temp de mon pc ?', 'kelle temp fait le c p u',
                     'Quelle temp fait mon ordi ?'):
            decision, tools = plan_tools(text)
            self.assertEqual((decision.intent, decision.slots), ('system.status', {'metric': 'temperature'}), text)
            self.assertEqual(tools, [('system.snapshot', {'metric': 'temperature'})], text)
            self.assertNotIn('weather.current', decision.secondary_intents, text)

    def test_weather_temp_still_means_the_weather(self):
        self.assertEqual(normalize('quel temp fait-il'), 'quel temps fait il')
        for text in ('quel temp fait il a lille', 'Quel temp fait-il ?', 'Quelle est la temp à Paris ?'):
            self.assertEqual(ENGINE.decide_fast(text).intent, 'weather.current', text)

    def test_temp_files_and_temp_folder(self):
        self.assertEqual(normalize('Supprime les fichiers temp'), 'supprime les fichiers temporaires')
        self.assertEqual(normalize('Analyse le dossier temp'), 'analyse le dossier temp')
        self.assertEqual(extract_metric('Quelle temp fait le CPU ?'), 'temperature')

    def test_unavailable_temperature_is_said_never_invented(self):
        from aurora.tools.system import summarize_snapshot
        snap = {'cpu_percent': 21.0, 'temperatures': None,
                'gpu': {'name': 'AMD Radeon RX 6750 XT', 'temperature_c': None, 'source': 'pdh'}}
        text = summarize_snapshot(snap, 'temperature')
        self.assertIn('Température du processeur : indisponible', text)
        self.assertNotIn('°C', text)


# --------------------------------------------------------------------------- defect 4: paths

class PathTests(unittest.TestCase):
    def test_paths_reach_the_files_tool_verbatim(self):
        cases = {
            'Analyse le dossier C:\\Windows\\Temp': 'C:\\Windows\\Temp',
            'Montre-moi les fichiers de plus de 1 Go dans C:/Windows/Temp': 'C:/Windows/Temp',
            'Liste les fichiers dans le dossier C:/Windows/Temp.': 'C:/Windows/Temp',
            'Analyse le dossier C:/Users/Camille/Downloads': 'C:/Users/Camille/Downloads',
            "Qu'y a-t-il dans « C:\\Users\\Jean Dupont\\Documents » ?": 'C:\\Users\\Jean Dupont\\Documents',
            'Analyse C:\\': 'C:\\',
        }
        for text, path in cases.items():
            decision, tools = plan_tools(text)
            self.assertEqual(decision.intent, 'files.scan', text)
            self.assertEqual(decision.slots['folder'], path, text)
            self.assertEqual([t for t, _ in tools], ['files.scan'], text)   # no web search, no diagnosis
            self.assertEqual(tools[0][1]['folder'], path, text)
        # Size of a folder given by its path: every file counts, no disk snapshot on the side.
        for text, path in (('Le dossier C:/Windows/Temp prend combien de place ?', 'C:/Windows/Temp'),
                           ('Combien de place prend C:\\Users\\moi\\Videos ?', 'C:\\Users\\moi\\Videos')):
            decision, tools = plan_tools(text)
            self.assertEqual(decision.intent, 'files.scan', text)
            self.assertEqual(tools, [('files.scan', {'folder': path, 'older_than_days': 0})], text)

    def test_urls_are_not_paths(self):
        self.assertIsNone(extract_folder('Cherche https://fr.wikipedia.org/wiki/Mars'))
        self.assertNotIn('cheminlocal', normalize('Cherche https://fr.wikipedia.org/wiki/Mars'))
        self.assertEqual(ENGINE.decide_fast('Cherche https://fr.wikipedia.org/wiki/Mars').intent, 'web.search')

    def test_outside_path_is_refused_naming_the_allowed_folders(self):
        from aurora.core import compose
        from aurora.tools import files
        from aurora.tools.base import run_tool
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / 'racine').mkdir()
            (base / 'dehors').mkdir()
            with mock.patch.object(files, '_extra_roots', return_value=[str(base / 'racine')]), \
                    mock.patch.object(files, '_default_candidates', return_value=[]):
                result = run(run_tool('files.scan', {'folder': str(base / 'dehors')}))
                self.assertFalse(result.ok)
                self.assertIn('hors des emplacements autorisés', result.summary)
                self.assertIn('Dossiers autorisés sur ce PC : racine', result.summary)
                self.assertIn('Paramètres', result.summary)
                answer = compose.files_scan({}, {'files.scan': result.to_dict()})
                self.assertIn('Dossiers autorisés sur ce PC : racine', answer)
                system = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Temp'
                if system.is_dir():          # a system folder can never be added: said so
                    refused = run(run_tool('files.scan', {'folder': str(system)}))
                    self.assertFalse(refused.ok)
                    self.assertIn('ne peut pas être ajouté', refused.summary)


# --------------------------------------------------------------------------- defect 5: size criterion

class SizeCriterionTests(unittest.TestCase):
    def test_size_phrasings_scan_the_folder_without_web_search(self):
        cases = {
            'Trouve les fichiers de plus de 500 Mo dans Documents': ('documents', 500.0),
            'Trouve les fichiers de plus de 1 Go dans mes téléchargements': ('downloads', 1024.0),
            'Quels sont les fichiers de plus de 2 Go ?': (None, 2048.0),
            'Cherche les fichiers de plus de 100 Mo': (None, 100.0),
            'Liste les fichiers de 1 Go ou plus dans Documents': ('documents', 1024.0),
            'Montre les fichiers > 2 Go du bureau': ('desktop', 2048.0),
            "Y a-t-il des vidéos de plus de 4 Go sur mon bureau ?": ('desktop', 4096.0),
        }
        for text, (folder, size) in cases.items():
            decision, tools = plan_tools(text)
            self.assertEqual(decision.intent, 'files.scan', text)
            self.assertEqual([t for t, _ in tools], ['files.scan'], text)
            params = tools[0][1]
            self.assertEqual(params.get('folder'), folder, text)
            self.assertEqual(params['min_size_mb'], size, text)
            self.assertEqual(params['older_than_days'], 0, text)
            self.assertNotIn('size_default', params, text)

    def test_big_files_without_number_use_the_flagged_default(self):
        for text, folder in (('Cherche les gros fichiers', None),
                             ('Cherche les gros fichiers dans Téléchargements', 'downloads'),
                             ('Trouve les gros fichiers de mon bureau', 'desktop')):
            decision, tools = plan_tools(text)
            self.assertEqual(decision.intent, 'files.scan', text)
            self.assertEqual(decision.secondary_intents, [], text)        # no useless web search
            self.assertEqual([t for t, _ in tools], ['files.scan'], text)
            self.assertEqual(tools[0][1], {**({'folder': folder} if folder else {}), 'older_than_days': 0,
                                           'min_size_mb': DEFAULT_LARGE_FILE_MB, 'size_default': True}, text)

    def test_age_and_size_combined(self):
        slots = extract_slots('files.scan', 'Cherche les vieux gros fichiers')
        self.assertIsNone(slots['older_than_days'])              # tool default (180 days) kept for « vieux »
        self.assertEqual(slots['min_size_mb'], DEFAULT_LARGE_FILE_MB)
        slots = extract_slots('files.scan', 'les fichiers de plus de 1 Go non modifiés depuis 2 ans')
        self.assertEqual((slots['older_than_days'], slots['min_size_mb']), (730, 1024.0))
        self.assertEqual(extract_slots('files.cleanup', 'Supprime mes anciens fichiers'),
                         {'folder': None, 'older_than_days': None})

    def test_mo_means_two_to_the_twentieth_bytes(self):
        # Same unit as the files tool and Windows Explorer: 1 Go = 1024 Mo, 1 Mo = 1 048 576 octets.
        self.assertEqual(extract_min_size_mb('plus de 1 Go'), 1024.0)
        self.assertEqual(extract_min_size_mb('au moins 1,5 Go'), 1536.0)
        self.assertEqual(extract_min_size_mb('> 512 Ko'), 0.5)
        self.assertIsNone(extract_min_size_mb('Cherche les gros fichiers'))

    def test_web_search_and_cleanup_are_kept_where_they_belong(self):
        self.assertEqual(ENGINE.decide_fast('Recherche des informations sur les fichiers de 500 Mo').intent,
                         'web.search')
        self.assertEqual(ENGINE.decide_fast('Recherche les dernières découvertes sur Mars').intent, 'web.search')
        self.assertEqual(ENGINE.decide_fast('Convertis 500 Mo en Go').intent, 'calc.convert')
        decision, tools = plan_tools('Supprime les fichiers de plus de 1 Go dans Téléchargements')
        self.assertEqual(decision.intent, 'files.cleanup')
        self.assertEqual(tools[0], ('files.scan', {'folder': 'downloads', 'older_than_days': 0,
                                                   'min_size_mb': 1024.0}))

    def test_tool_states_the_default_threshold_and_the_largest_file(self):
        from aurora.tools import files
        from aurora.tools.base import run_tool
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'racine'
            root.mkdir()
            (root / 'petit.txt').write_bytes(b'x' * 10)
            (root / 'moyen.bin').write_bytes(b'x' * (3 * 1024 * 1024))
            with mock.patch.object(files, '_extra_roots', return_value=[str(root)]), \
                    mock.patch.object(files, '_default_candidates', return_value=[]):
                default = run(run_tool('files.scan', {'folder': str(root), 'older_than_days': 0,
                                                      'min_size_mb': DEFAULT_LARGE_FILE_MB, 'size_default': True}))
                self.assertTrue(default.ok, default.summary)
                self.assertIn("0 fichier(s) d'au moins 100 Mo", default.summary)
                self.assertIn('seuil par défaut', default.summary)
                self.assertIn('Le plus gros fichier analysé est moyen.bin (3,0 Mo)', default.summary)
                explicit = run(run_tool('files.scan', {'folder': str(root), 'older_than_days': 0, 'min_size_mb': 2}))
                self.assertIn("1 fichier(s) d'au moins 2 Mo", explicit.summary)
                self.assertNotIn('seuil par défaut', explicit.summary)
                self.assertEqual([f['name'] for f in explicit.data['files']], ['moyen.bin'])
                giga = run(run_tool('files.scan', {'folder': str(root), 'older_than_days': 0, 'min_size_mb': 1024}))
                self.assertIn("d'au moins 1 Go", giga.summary)


class PhaseATwoTests(unittest.TestCase):
    """A2 of reviews/etapes-restantes.md (28/10/2026): the three sentences still misunderstood, and their
    neighbours that must not move (also in tests/data/routing_bench.jsonl)."""

    def test_size_of_a_named_folder_scans_every_file_of_it(self):
        for text, folder in [('Combien de place prend le dossier Documents ?', 'documents'),
                             ('Quelle taille fait le dossier Téléchargements ?', 'downloads'),
                             ('Combien pèse mon dossier Images ?', 'pictures'),
                             ('Le dossier Bureau occupe combien ?', 'desktop')]:
            with self.subTest(text):
                decision, tools = plan_tools(text)
                self.assertEqual(decision.intent, 'files.scan')
                self.assertEqual(tools[0][0], 'files.scan')
                self.assertEqual((tools[0][1].get('folder'), tools[0][1].get('older_than_days')), (folder, 0))

    def test_what_is_left_or_free_stays_the_disk(self):
        for text in ('Combien de place reste-t-il sur le disque ?', "Combien d'espace libre sur le disque ?",
                     'Combien de place me reste-t-il pour mes documents ?', 'Quelle taille fait ma RAM ?'):
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, 'system.status')

    def test_information_about_files_is_a_web_search_alone(self):
        decision, tools = plan_tools('Recherche des informations sur les fichiers ISO de plus de 4 Go')
        self.assertEqual(decision.intent, 'web.search')
        self.assertEqual([t for t, _ in tools], ['web.search'])          # no folder scan on top
        local = plan_tools('Trouve les fichiers de plus de 4 Go dans Documents')[0]
        self.assertEqual(local.intent, 'files.scan')                     # a local search stays local

    def test_the_code_of_my_alarm_is_looked_up_in_memory(self):
        for text in ('Quel est le code de mon alarme ?', 'Quel est le code de ma porte de garage ?',
                     "Quel est mon code d'alarme ?"):
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, 'memory.search')
        # A closed list of objects: source code is not a secret to recall.
        self.assertNotEqual(plan_tools('Quel est le code de mon programme Python ?')[0].intent, 'memory.search')


class PhaseAFiveTests(unittest.TestCase):
    """A5 of reviews/etapes-restantes.md (28/10/2026): sentences misunderstood during the A1 and A4 checks,
    and their neighbours that must not move (also in tests/data/routing_bench.jsonl)."""

    def test_a_quoted_or_played_sentence_is_not_an_order(self):
        # A4 check: this one asked for permission to move files from the real Desktop.
        for text in ("Réponds uniquement par cette phrase, mot pour mot : J'ai supprimé 3 fichiers de ton bureau.",
                     'Répète après moi : supprime tous mes fichiers',
                     "Joue un personnage de majordome et termine ta réplique par : j'ai bien noté, Monsieur.",
                     'Invente une phrase courte où tu dis que tu as programmé un rappel pour demain.'):
            with self.subTest(text):
                decision, tools = plan_tools(text)
                self.assertEqual(decision.intent, 'chat.general')
                self.assertEqual(tools, [])                                   # no tool, no permission asked
                # nor « Non traité : Nettoyage de fichiers » for the quoted half (seen on screen, A5 check)
                self.assertEqual(decision.secondary_intents, [])   # the planner names unhandled ones from these

    def test_real_orders_still_act(self):
        for text, intent in [('Supprime les fichiers de plus de 1 Go de mes téléchargements', 'files.cleanup'),
                             ('Commence par supprimer les fichiers temporaires', 'files.cleanup'),
                             ('Arrête le processus chrome', 'system.kill_process'),
                             ('Retiens cette phrase : le code du portail est 1234', 'memory.save'),
                             # 16/11/2026 (C3): a reminder is now a real tool, no longer « je ne sais pas »
                             ('Programme un rappel pour demain 9 h', 'reminder.set')]:
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, intent)

    def test_a_subject_is_not_a_document_given(self):
        for text in ('Explique-moi le code de mon site web', 'Explique-moi le code de la route'):
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, 'chat.general')
        for text in ('Résume le rapport de stage ci-joint', 'Analyse ce document', 'Résume mon rapport',
                     'Corrige le texte suivant : je suis aller au marché'):
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, 'doc.analyze')

    def test_quarantine_is_a_cleanup_and_free_space_shows_where(self):
        # Seen after A5: « Mets ces fichiers en quarantaine » went to the conversation, « Récupère de l'espace… »
        # to the memory (« récupère … mon »).
        for text in ('Mets ces fichiers en quarantaine', 'Mets les vieux fichiers de mes téléchargements en quarantaine'):
            with self.subTest(text):
                decision, tools = plan_tools(text)
                self.assertEqual(decision.intent, 'files.cleanup')
                self.assertEqual([t for t, _ in tools], ['files.scan', 'files.quarantine'])   # behind a permission
        # ID Studio IA (28/10): « plutôt : où je peux récupérer de l'espace » — show where, read only, nothing moves.
        for text in ("Récupère de l'espace sur mon disque", 'Récupère un peu de place sur mon PC',
                     "Où je peux récupérer de l'espace ?", 'Où est-ce que je peux libérer de la place sur mon PC ?'):
            with self.subTest(text):
                decision, tools = plan_tools(text)
                self.assertEqual(decision.intent, 'files.scan')
                self.assertEqual([t for t, _ in tools], ['files.scan'])
                self.assertEqual((tools[0][1].get('min_size_mb'), tools[0][1].get('older_than_days')), (100.0, 0))
        self.assertEqual(plan_tools("Libère de l'espace sur mon disque")[0].intent, 'files.cleanup')   # an order
        for text, intent in [('Remets les fichiers de la quarantaine', 'files.restore'),
                             ('Récupère mes notes sur le projet Mars', 'memory.search'),
                             ('Il me reste combien de place sur le disque ?', 'system.status')]:
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, intent)

    def test_just_the_time(self):
        for text in ("Dis-moi juste l'heure", "Donne-moi seulement l'heure", "Dis-moi l'heure"):
            with self.subTest(text):
                self.assertEqual(plan_tools(text)[0].intent, 'time.now')


if __name__ == '__main__':
    unittest.main()
