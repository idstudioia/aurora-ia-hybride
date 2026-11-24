"""Les moteurs sans fenêtre de FreeCAD et de KiCad (tools/moteurs_cao.py, 15/11/2026).

ID Studio IA : « il nous faut la connexion au moteur FreeCAD et KiCad ». Un fichier déclaré d'un projet est vérifié par le
moteur du logiciel autorisé, sur une copie. Aucun moteur n'est lancé ici : `_run` est remplacé, et répond ce que le
vrai moteur a répondu sur ce PC le 15/11/2026 (FreeCAD 1.1.4, KiCad 10.0.8).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from aurora import config
from aurora.tools import apps, moteurs_cao, projets
from tests.test_api import NO_TOKEN, ApiTestCase
from tests.test_projets import manifest

TIBIA = {'version': '1.1.4', 'objets': [{'nom': 'socle.step', 'solides': 1, 'valide': True, 'ferme': True, 'volume_mm3': 41720.04,
                                         'surface_mm2': 18196.8, 'boite_mm': [154.0, 30.0, 23.6]}]}
DRC = {'kicad_version': '10.0.8', 'unconnected_items': [{'description': 'piste'}], 'violations': [
    {'description': 'L’empreinte ne correspond pas à la copie en librairie', 'severity': 'warning', 'items': [{'description': 'Empreinte H1'}]},
    {'description': 'Isolation insuffisante', 'severity': 'error', 'items': [{'description': 'Piste'}, {'description': 'Pastille 2 de J1'}]}]}
ERC = {'kicad_version': '10.0.8', 'sheets': [{'path': '/', 'violations': []}]}


class EnginesCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tools = tempfile.TemporaryDirectory()
        self.addCleanup(self.tools.cleanup)
        self.bin = Path(self.tools.name).resolve()      # le nom long : c'est celui qu'AURORA retient du logiciel autorisé
        for name in ('freecad.exe', 'freecadcmd.exe', 'kicad.exe', 'kicad-cli.exe'):
            (self.bin / name).write_bytes(b'')
        config.update_settings({'allowed_apps': [{'name': 'FreeCAD 1.1', 'path': str(self.bin / 'freecad.exe'), 'url': ''},
                                                 {'name': 'KiCad 10.0', 'path': str(self.bin / 'kicad.exe'), 'url': ''}]})
        self.project = Path(os.environ['AURORA_HOME']) / 'projets' / 'lampe'
        files = [{'nom': 'Socle', 'chemin': 'sortie/socle.step', 'role': '', 'genere': True},
                 {'nom': 'Abat-jour', 'chemin': 'sortie/abat_jour.step', 'role': '', 'genere': True},
                 {'nom': 'Schéma', 'chemin': 'carte/lampe.kicad_sch', 'role': ''}, {'nom': 'Carte', 'chemin': 'carte/lampe.kicad_pcb', 'role': ''},
                 {'nom': 'Notes', 'chemin': 'carte/notes.md', 'role': ''}]
        for name in ('sortie/socle.step', 'carte/lampe.kicad_sch', 'carte/lampe.kicad_pcb', 'carte/lampe.kicad_pro', 'carte/notes.md'):
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(f'contenu de {name}', 'utf-8')
        self.files = files
        self.declare([])
        shutil.rmtree(Path(os.environ['AURORA_HOME']) / moteurs_cao.GARDES, ignore_errors=True)   # rien de gardé d'un autre essai
        self.calls: list[dict] = []
        self.answer: dict | str | Exception = TIBIA
        patch = mock.patch.object(moteurs_cao, '_run', side_effect=self.engine)
        patch.start()
        self.addCleanup(patch.stop)

    def declare(self, verifications: list[dict]) -> None:
        """Le projet « lampe » des essais des projets (test_projets.manifest), avec ses fichiers de CAO et ces vérifications."""
        (self.project / 'projet.json').write_text(json.dumps(manifest(
            rubriques=[{'id': 'pieces', 'note': '', 'fichiers': self.files}], verifications=verifications)), 'utf-8')

    def engine(self, args, *, cwd, env=None):
        """Le faux moteur : il note ce qu'on lui donne (et ce qu'il trouve à côté), puis répond comme le vrai."""
        self.calls.append({'args': list(args), 'cwd': Path(cwd), 'env': dict(env or {}), 'beside': sorted(p.name for p in Path(cwd).iterdir())})
        if isinstance(self.answer, Exception):
            raise self.answer
        out = b''
        if '--output' in args:
            if isinstance(self.answer, dict):
                Path(args[args.index('--output') + 1]).write_text(json.dumps(self.answer), 'utf-8')
        else:
            out = (moteurs_cao.MARK + json.dumps(self.answer)).encode() if isinstance(self.answer, dict) else str(self.answer).encode()
        return subprocess.CompletedProcess(args, 0, stdout=b'FreeCAD 1.1.4\n' + out + b'\n', stderr=b'le moteur dit pourquoi')


class PartTests(EnginesCase):
    def test_a_part_is_checked_by_freecad_on_a_copy(self):
        before = (self.project / 'sortie/socle.step').read_bytes()
        found = moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')
        self.assertEqual((found['outil'], found['genre'], found['verdict'], found['moteur']), ('freecad', 'piece', 'conforme', 'FreeCAD 1.1.4'))
        self.assertEqual(found['resume'], '1 solide, valide et fermé · 41,7 cm³ · 154,0 × 30,0 × 23,6 mm')
        self.assertEqual(found['objets'][0]['boite_mm'], [154.0, 30.0, 23.6])
        self.assertIn('ne dit pas', found['limite'])                       # ce que le contrôle ne prouve pas
        call = self.calls[0]
        self.assertEqual(call['args'][0], str(self.bin / 'freecadcmd.exe'))   # le moteur à côté du logiciel autorisé
        given = Path(call['env']['AURORA_CAO_FICHIER'])
        self.assertEqual((given.name, given.parent), ('socle.step', call['cwd']))
        self.assertNotIn(str(self.project), str(given))                    # une copie, jamais le fichier du projet
        self.assertEqual(Path(call['args'][1]).name, 'verifier_piece.py')  # le texte d'AURORA, et lui seul
        self.assertFalse(call['cwd'].exists())                             # le dossier de passage est retiré
        self.assertEqual((self.project / 'sortie/socle.step').read_bytes(), before)

    def test_a_part_that_is_not_a_closed_solid_is_to_review(self):
        self.answer = {'version': '1.1.4', 'objets': [{**TIBIA['objets'][0], 'ferme': False}, {**TIBIA['objets'][0], 'nom': 'Corps'}]}
        found = moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')
        self.assertEqual((found['verdict'], found['resume']), ('a_revoir', '2 formes, dont 1 solides valides et fermés'))


class BoardTests(EnginesCase):
    def test_a_schematic_and_a_board_are_checked_by_kicad(self):
        self.answer = ERC
        schema = moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_sch')
        self.assertEqual((schema['genre'], schema['verdict'], schema['resume'], schema['moteur']), ('schema', 'conforme', '0 erreur, 0 avertissement', 'KiCad 10.0.8'))
        self.assertEqual(self.calls[0]['args'][:3], [str(self.bin / 'kicad-cli.exe'), 'sch', 'erc'])
        self.assertEqual(self.calls[0]['beside'], ['lampe.kicad_pcb', 'lampe.kicad_pro', 'lampe.kicad_sch'])   # ses fichiers KiCad, pas les notes
        self.answer = DRC
        board = moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_pcb')
        self.assertEqual(self.calls[1]['args'][1:3], ['pcb', 'drc'])
        self.assertEqual((board['genre'], board['verdict'], board['erreurs'], board['avertissements'], board['non_connectes']), ('carte', 'a_revoir', 1, 1, 1))
        self.assertEqual(board['resume'], '1 erreur, 1 avertissement, 1 liaison non connectée')
        self.assertEqual(board['violations'][0], {'gravite': 'erreur', 'texte': 'Isolation insuffisante', 'ou': 'Piste, Pastille 2 de J1'})   # les erreurs d'abord
        self.assertEqual(board['violations'][1]['gravite'], 'avertissement')
        self.assertEqual(sorted(p.name for p in (self.project / 'carte').iterdir()), ['lampe.kicad_pcb', 'lampe.kicad_pro', 'lampe.kicad_sch', 'notes.md'])


class RefusalTests(EnginesCase):
    def test_what_cannot_be_checked(self):
        self.assertIsNone(moteurs_cao.verifier('lampe-de-bureau', 'sortie/inconnu.step'))             # pas déclaré
        self.assertIsNone(moteurs_cao.verifier('absent', 'sortie/socle.step'))
        for chemin, word in (('carte/notes.md', 'ne sait faire vérifier'), ('sortie/abat_jour.step', 'se refait en construisant')):
            with self.subTest(chemin), self.assertRaisesRegex(ValueError, word):
                moteurs_cao.verifier('lampe-de-bureau', chemin)
        for answer, word in ((subprocess.TimeoutExpired('freecadcmd', 120), 'il a été arrêté'), (OSError(5, 'Accès refusé'), 'n’a pas pu être lancé'),
                             ('rien de lisible', 'pas rendu de résultat lisible'), ({'erreur': 'OSError : fichier illisible'}, 'n’a pas pu lire ce fichier'),
                             ({'version': '1.1.4', 'objets': []}, 'aucune forme')):
            with self.subTest(word=word), self.assertRaisesRegex(ValueError, word):
                self.answer = answer
                moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')
        self.answer = 'pas de rapport'
        with self.assertRaisesRegex(ValueError, 'pas rendu de rapport pour ce fichier. le moteur dit pourquoi'):
            moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_sch')
        self.assertEqual(len(self.calls), 6)

    def test_only_the_engine_of_an_allowed_app_is_run(self):
        (self.bin / 'freecadcmd.exe').unlink()
        with self.assertRaisesRegex(ValueError, 'freecadcmd.exe\\) est introuvable à côté de « FreeCAD 1.1 »'):
            moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')
        config.update_settings({'allowed_apps': []})
        with self.assertRaisesRegex(ValueError, 'KiCad n’est pas dans vos applications autorisées'):
            moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_sch')
        self.assertEqual(self.calls, [])                                    # rien n'a été lancé

    def test_the_page_knows_which_engines_are_there(self):
        (self.bin / 'kicad-cli.exe').unlink()
        by_id = {t['id']: t for t in apps.design_tools()}
        self.assertEqual((by_id['freecad']['moteur'], by_id['freecad']['verifie']), (True, ['.fcstd', '.step', '.stp']))
        self.assertEqual((by_id['kicad']['moteur'], by_id['kicad']['verifie']), (False, ['.kicad_sch', '.kicad_pcb']))

    def test_the_route(self):
        got = self.client.post('/api/projets/lampe-de-bureau/verification', params={'chemin': 'sortie/socle.step'})
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual((got.json()['verdict'], got.json()['fichier']), ('conforme', 'sortie/socle.step'))
        self.assertEqual(self.client.post('/api/projets/lampe-de-bureau/verification', params={'chemin': 'sortie/inconnu.step'}).status_code, 404)
        refused = self.client.post('/api/projets/lampe-de-bureau/verification', params={'chemin': 'carte/notes.md'})
        self.assertEqual((refused.status_code, 'ne sait faire vérifier' in refused.json()['detail']), (422, True))
        self.assertEqual(self.client.post('/api/projets/lampe-de-bureau/verification', params={'chemin': 'sortie/socle.step'}, headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.post('/api/projets/lampe-de-bureau/verification').status_code, 422)   # sans dire quel fichier


class ComputedTests(EnginesCase):
    """ID Studio IA : « Oui, fais-le » (calculer les vérifications du projet par ces moteurs). Une vérification qui nomme ses
    fichiers n'a plus l'état qu'elle déclare : il vient de ce que le moteur a dit de chaque fichier d'aujourd'hui."""
    PIECE = {'quoi': 'Le socle est un solide fermé', 'etat': 'a_faire', 'detail': '', 'controle': ['sortie/socle.step']}
    CARTE = {'quoi': 'Schéma et carte contrôlés', 'etat': 'bloquante', 'detail': '',
             'controle': ['carte/lampe.kicad_sch', 'carte/lampe.kicad_pcb']}
    DIT = r'le \d\d/\d\d/\d{4} à \d\d:\d\d\.'

    def line(self, index: int = 0) -> dict:
        return projets.load('lampe-de-bureau')['liste_verifications'][index]

    def test_a_check_is_done_only_when_the_engine_said_so_of_today_s_file(self):
        self.declare([self.PIECE, {'quoi': 'Puissance calculée', 'etat': 'faite', 'detail': ''}])
        before = projets.load('lampe-de-bureau')
        self.assertEqual((before['verifications'], before['pret_a_fabriquer']), ({'faites': 1, 'a_faire': 1, 'bloquantes': 0, 'total': 2}, False))
        self.assertEqual(self.line()['calcule']['texte'], '0 fichier sur 1 conforme. Pas encore vérifié : socle.step.')
        self.assertNotIn('calcule', self.line(1))                          # déclarée à la main : rien n'est calculé
        self.assertEqual(self.calls, [])                                   # lire un projet ne lance aucun moteur
        self.assertTrue(moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')['garde'])
        after = projets.load('lampe-de-bureau')
        self.assertEqual((after['verifications']['faites'], after['pret_a_fabriquer']), (2, True))
        self.assertEqual(self.line()['etat'], 'faite')
        self.assertRegex(self.line()['calcule']['texte'], r'^1 fichier sur 1 conforme, dit par FreeCAD 1\.1\.4 ' + self.DIT + '$')
        kept = Path(os.environ['AURORA_HOME']) / moteurs_cao.GARDES / 'lampe-de-bureau.json'
        self.assertEqual(list(json.loads(kept.read_text('utf-8'))), ['sortie/socle.step'])   # gardé chez AURORA…
        self.assertEqual(sorted(p.name for p in (self.project / 'sortie').iterdir()), ['socle.step'])   # … pas dans le projet
        (self.project / 'sortie/socle.step').write_text('une autre pièce, écrite après le contrôle', 'utf-8')
        self.assertEqual((self.line()['etat'], self.line()['calcule']['texte']),
                         ('a_faire', '0 fichier sur 1 conforme. Réécrit depuis son contrôle, à revérifier : socle.step.'))
        self.answer = {'version': '1.1.4', 'objets': [{**TIBIA['objets'][0], 'ferme': False}]}
        moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')       # le moteur dit : pas fermé
        self.assertEqual((self.line()['etat'], self.line()['calcule']['texte']), ('a_faire', '0 fichier sur 1 conforme. À revoir : socle.step.'))
        self.assertFalse(projets.load('lampe-de-bureau')['pret_a_fabriquer'])

    def test_a_declared_state_does_not_stand_in_for_the_engine(self):
        self.declare([{**self.PIECE, 'etat': 'faite'}, self.CARTE])        # « faite » écrite à la main, jamais contrôlée
        state = projets.load('lampe-de-bureau')
        self.assertEqual(state['verifications'], {'faites': 0, 'a_faire': 1, 'bloquantes': 1, 'total': 2})
        self.assertEqual(state['pourquoi_pas'], ['1 vérification bloquante', '1 vérification à faire'])
        self.assertEqual(next(p for p in projets.catalogue()['projets'] if p['id'] == 'lampe-de-bureau')['verifications']['faites'], 0)
        self.answer = ERC
        moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_sch')
        self.answer = DRC                                                  # la carte a une erreur et une liaison ouverte
        moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_pcb')
        self.assertEqual((self.line(1)['etat'], self.line(1)['calcule']['conformes'], self.line(1)['calcule']['total']), ('bloquante', 1, 2))
        self.assertRegex(self.line(1)['calcule']['texte'], r'^1 fichier sur 2 conforme, dit par KiCad 10\.0\.8 ' + self.DIT + r' À revoir : lampe\.kicad_pcb\.$')
        self.assertEqual([f['etat'] for f in self.line(1)['calcule']['fichiers']], ['conforme', 'a_revoir'])
        self.answer = {**DRC, 'violations': [], 'unconnected_items': []}   # la carte corrigée, recontrôlée
        moteurs_cao.verifier('lampe-de-bureau', 'carte/lampe.kicad_pcb')
        self.assertEqual(self.line(1)['etat'], 'faite')
        self.assertRegex(self.line(1)['calcule']['texte'], r'^2 fichiers sur 2 conformes, dit par KiCad 10\.0\.8 ' + self.DIT + '$')

    def test_every_named_file_is_checked_at_once(self):
        self.declare([{**self.PIECE, 'controle': ['sortie/socle.step', 'sortie/abat_jour.step']},
                      {'quoi': 'Notes relues', 'etat': 'a_faire', 'detail': '', 'controle': ['carte/notes.md', 'sortie/socle.step']}])
        done = moteurs_cao.verifier_tout('lampe-de-bureau')
        self.assertEqual([(d['fichier'], d['verdict'], d['moteur']) for d in done['verifies']], [('sortie/socle.step', 'conforme', 'FreeCAD 1.1.4')])
        self.assertEqual([r['fichier'] for r in done['refuses']], ['sortie/abat_jour.step', 'carte/notes.md'])
        self.assertIn('se refait en construisant', done['refuses'][0]['raison'])
        self.assertEqual(len(self.calls), 1)                               # un fichier nommé deux fois n'est vérifié qu'une fois
        self.assertRegex(self.line()['calcule']['texte'], r'^1 fichier sur 2 conforme, dit par FreeCAD 1\.1\.4 ' + self.DIT + r' Absent du dossier : abat_jour\.step\.$')
        self.assertIn('D’un genre qu’aucun moteur ne vérifie : notes.md.', self.line(1)['calcule']['texte'])
        self.assertEqual(self.line(1)['etat'], 'a_faire')
        self.assertIsNone(moteurs_cao.verifier_tout('absent'))
        self.declare([{'quoi': 'Puissance calculée', 'etat': 'faite', 'detail': ''}])
        with self.assertRaisesRegex(ValueError, 'ne nomme de fichier'):
            moteurs_cao.verifier_tout('lampe-de-bureau')

    def test_a_check_names_only_files_the_project_declares(self):
        for controle, word in ((['sortie/inconnu.step'], 'fait contrôler un fichier que le projet ne déclare pas'),
                               (['../ailleurs.step'], 'reste dans le dossier du projet')):
            with self.subTest(controle):
                self.declare([{**self.PIECE, 'controle': controle}])
                refused = [r['raison'] for r in projets.catalogue()['refuses'] if Path(r['dossier']) == self.project]
                self.assertEqual(len(refused), 1)
                self.assertIn(word, refused[0])

    def test_what_is_kept_and_unreadable_counts_for_nothing(self):
        self.declare([self.PIECE])
        moteurs_cao.verifier('lampe-de-bureau', 'sortie/socle.step')
        self.assertEqual(self.line()['etat'], 'faite')
        (Path(os.environ['AURORA_HOME']) / moteurs_cao.GARDES / 'lampe-de-bureau.json').write_text('{ pas du JSON', 'utf-8')
        self.assertEqual((moteurs_cao.gardes('lampe-de-bureau'), self.line()['etat']), ({}, 'a_faire'))

    def test_the_route(self):
        self.declare([self.PIECE])
        got = self.client.post('/api/projets/lampe-de-bureau/verifications/moteurs')
        self.assertEqual(got.status_code, 200, got.text)
        self.assertEqual(([d['verdict'] for d in got.json()['verifies']], got.json()['refuses']), (['conforme'], []))
        read = self.client.get('/api/projets/lampe-de-bureau').json()
        self.assertEqual((read['liste_verifications'][0]['etat'], read['liste_verifications'][0]['calcule']['conformes'], read['pret_a_fabriquer']), ('faite', 1, True))
        self.assertEqual(self.client.post('/api/projets/absent/verifications/moteurs').status_code, 404)
        self.assertEqual(self.client.post('/api/projets/lampe-de-bureau/verifications/moteurs', headers=NO_TOKEN).status_code, 401)
        self.declare([{'quoi': 'Puissance calculée', 'etat': 'faite', 'detail': ''}])
        none = self.client.post('/api/projets/lampe-de-bureau/verifications/moteurs')
        self.assertEqual((none.status_code, 'ne nomme de fichier' in none.json()['detail']), (422, True))
