"""Les projets (tools/projets.py, 13/11/2026) : un dossier, son projet.json, ce que le service en dit.

Rien n'est écrit par le service : chaque essai fabrique ses dossiers dans le AURORA_HOME temporaire.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from aurora.tools import projets
from tests.doubles_core import ROBOT, needs_robot   # the shipped robot, where this copy of AURORA carries it
from tests.test_api import NO_TOKEN, ApiTestCase


def manifest(**changes) -> dict:
    base = {
        'format': 'aurora.projet/1', 'id': 'lampe-de-bureau', 'titre': 'Lampe de bureau', 'resume': 'Une lampe.',
        'stade': 'étude', 'maj': '13/11/2026',
        'cahier': [{'texte': 'Éclairer un bureau', 'etat': 'tenue', 'note': ''}],
        'rubriques': [{'id': 'calculs', 'note': '', 'fichiers': [{'nom': 'Calcul', 'chemin': 'calcul.py', 'role': ''}]},
                      {'id': 'fabrication', 'note': '', 'fichiers': [
                          {'nom': 'Pied', 'chemin': 'sortie/pied.stl', 'role': '', 'genere': True}]}],
        'verifications': [{'quoi': 'Puissance calculée', 'etat': 'faite', 'detail': ''}],
    }
    return {**base, **changes}


class ProjectsCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(os.environ['AURORA_HOME']) / 'projets'

    def write(self, folder: str, data, *, files: tuple[str, ...] = ('calcul.py',)) -> Path:
        where = self.root / folder
        where.mkdir(parents=True, exist_ok=True)
        (where / projets.MANIFEST).write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), 'utf-8')
        for name in files:
            (where / name).parent.mkdir(parents=True, exist_ok=True)
            (where / name).write_text('x', 'utf-8')
        return where

    def mine(self) -> dict:
        return {p['id']: p for p in projets.catalogue()['projets'] if p['origine'] == 'personnel'}


@needs_robot
class ShippedProjectTests(unittest.TestCase):
    def test_the_quadruped_kept_in_aurora_s_folder_is_read(self):
        """Le quadrupède, premier projet : son projet.json est valide, et il n'est pas prêt à fabriquer."""
        found = projets.catalogue()
        self.assertEqual([r for r in found['refuses'] if 'robot-quadrupede' in r['dossier']], [])
        robot = next(p for p in found['projets'] if p['id'] == 'robot-quadrupede')
        self.assertEqual(robot['origine'], 'AURORA')
        self.assertEqual(robot['fichiers']['manquants'], [])        # every file kept in Git is there
        self.assertFalse(robot['pret_a_fabriquer'])
        self.assertGreaterEqual(robot['verifications']['bloquantes'], 1)
        self.assertIn('bloquante', ' '.join(robot['pourquoi_pas']))
        whole = projets.load('robot-quadrupede')
        self.assertIn('recharger', ' '.join(e['texte'] for e in whole['cahier']))   # ID Studio IA : le retour à la base
        self.assertEqual({r['id'] for r in whole['rubriques']}, set(projets.RUBRIQUES))
        for rubrique in whole['rubriques']:
            for fichier in rubrique['fichiers']:
                self.assertTrue(fichier['present'] or fichier['genere'], fichier['chemin'])


class ReadingTests(ProjectsCase):
    def test_a_project_ready_to_be_made(self):
        self.write('lampe', manifest())
        lampe = self.mine()['lampe-de-bureau']
        self.assertTrue(lampe['pret_a_fabriquer'], lampe['pourquoi_pas'])
        self.assertEqual(lampe['fichiers'], {'presents': 1, 'a_refaire': 1, 'manquants': []})   # a made file is not a lack
        self.assertEqual(lampe['verifications'], {'faites': 1, 'a_faire': 0, 'bloquantes': 0, 'total': 1})
        whole = projets.load('lampe-de-bureau')
        files = {f['chemin']: f for r in whole['rubriques'] for f in r['fichiers']}
        self.assertEqual((files['calcul.py']['present'], files['calcul.py']['taille']), (True, 1))
        self.assertEqual((files['sortie/pied.stl']['present'], files['sortie/pied.stl']['taille']), (False, None))

    def test_what_keeps_a_project_from_being_made(self):
        cases = (
            ({'verifications': [{'quoi': 'Cote lue', 'etat': 'bloquante', 'detail': ''}]}, ('calcul.py',), '1 vérification bloquante'),
            ({'verifications': [{'quoi': 'Essai', 'etat': 'a_faire', 'detail': ''}] * 2}, ('calcul.py',), '2 vérifications à faire'),
            ({'cahier': [{'texte': 'Tenir 2 h', 'etat': 'non_tenue', 'note': ''}]}, ('calcul.py',), '1 exigence non tenue'),
            ({}, (), '1 fichier manquant'),
            ({'verifications': []}, ('calcul.py',), 'aucune vérification écrite'),
        )
        for n, (changes, files, said) in enumerate(cases):
            self.write(f'cas-{n}', manifest(id=f'cas-{n}', **changes), files=files)
            found = self.mine()[f'cas-{n}']
            self.assertFalse(found['pret_a_fabriquer'], said)
            self.assertIn(said, found['pourquoi_pas'], said)

    def test_a_refused_manifest_is_said_not_hidden(self):
        bad = {
            'pas-json': '{ pas du json',
            'chemin-dehors': manifest(id='dehors', rubriques=[{'id': 'calculs', 'note': '', 'fichiers': [
                {'nom': 'Secret', 'chemin': '../../aurora.db', 'role': ''}]}]),
            'disque': manifest(id='disque', rubriques=[{'id': 'calculs', 'note': '', 'fichiers': [
                {'nom': 'Ailleurs', 'chemin': 'C:/Windows/win.ini', 'role': ''}]}]),
            'deux-fois': manifest(id='deux-fois', rubriques=[{'id': 'calculs', 'note': '', 'fichiers': []}] * 2),
            'champ-en-trop': {**manifest(id='en-trop'), 'commande': 'del /s'},
            'autre-format': manifest(id='autre', format='aurora.scene/1'),
        }
        for folder, data in bad.items():
            self.write(folder, data)
        self.write('bon', manifest())
        self.write('double', manifest())                       # the same id in another folder
        found = projets.catalogue()
        refused = {Path(r['dossier']).name: r['raison'] for r in found['refuses']}
        self.assertEqual(set(refused), {*bad, 'double'})
        self.assertIn('JSON lisible', refused['pas-json'])
        self.assertIn('reste dans le dossier du projet', refused['chemin-dehors'])
        self.assertIn('reste dans le dossier du projet', refused['disque'])
        self.assertIn('deux fois', refused['deux-fois'])
        self.assertIn('porte déjà l’identifiant', refused['double'])
        self.assertEqual(set(self.mine()), {'lampe-de-bureau'})
        for unknown in ('absent', '../lampe', '', 'LAMPE'):
            self.assertIsNone(projets.load(unknown), unknown)


class RouteTests(ProjectsCase):
    def test_the_two_routes(self):
        self.write('lampe', manifest())
        listed = self.client.get('/api/projets')
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn('lampe-de-bureau', [p['id'] for p in listed.json()['projets']])
        one = self.client.get('/api/projets/lampe-de-bureau')
        self.assertEqual(one.status_code, 200, one.text)
        self.assertEqual(one.json()['cahier'][0]['texte'], 'Éclairer un bureau')
        self.assertEqual(self.client.get('/api/projets/absent').status_code, 404)
        self.assertEqual(self.client.get('/api/projets/PAS_UN_ID').status_code, 422)
        self.assertEqual(self.client.get('/api/projets', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.get('/api/projets/lampe-de-bureau', headers=NO_TOKEN).status_code, 401)


class SheetTests(ProjectsCase):
    """15/11/2026, ID Studio IA : « ajoute un visuel de fiche technique […] tout en haut ». La fiche d'un projet est son fichier
    déclaré qui s'appelle « fiche-technique », en image ; le service la rend telle quelle, et rien d'autre."""
    def with_sheet(self, chemin: str) -> dict:
        return manifest(rubriques=[{'id': 'dossier', 'note': '', 'fichiers': [
            {'nom': 'Notes', 'chemin': 'notes.md', 'role': ''}, {'nom': 'Fiche technique', 'chemin': chemin, 'role': ''}]}])

    def test_a_declared_sheet_is_named_and_served(self):
        self.write('lampe', self.with_sheet('images/fiche-technique.png'), files=('notes.md', 'images/fiche-technique.png'))
        self.assertEqual(self.mine()['lampe-de-bureau']['fiche'], 'images/fiche-technique.png')
        self.assertEqual(projets.load('lampe-de-bureau')['fiche'], 'images/fiche-technique.png')
        got = self.client.get('/api/projets/lampe-de-bureau/fiche')
        self.assertEqual((got.status_code, got.headers['content-type'], got.content), (200, 'image/png', b'x'))
        self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.get('/api/projets/absent/fiche').status_code, 404)

    def test_no_sheet_no_image(self):
        for chemin, files, why in (('fiche-technique.png', ('notes.md',), 'déclarée mais absente du dossier'),
                                   ('fiche-technique.txt', ('notes.md', 'fiche-technique.txt'), 'pas une image'),
                                   ('autre-image.png', ('notes.md', 'autre-image.png'), 'un autre nom')):
            with self.subTest(why):
                self.write('lampe', self.with_sheet(chemin), files=files)
                self.assertIsNone(projets.load('lampe-de-bureau')['fiche'])
                self.assertIsNone(projets.fiche_technique('lampe-de-bureau'))
                self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche').status_code, 404)

    def test_a_sheet_too_big_is_not_served(self):
        self.write('lampe', self.with_sheet('fiche-technique.jpg'), files=('notes.md', 'fiche-technique.jpg'))
        with mock.patch.object(projets, 'MAX_FICHE', 0):
            self.assertIsNone(projets.fiche_technique('lampe-de-bureau'))
            self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche').status_code, 404)


class InteractiveSheetTests(ProjectsCase):
    """15/11/2026, ID Studio IA : « fait la fiche technique interactive avec les model 3D et les animation quant on clique sur le
    cadre […] si je veux crée un nouveaux projet et que les cadre change suivent le model du projet ». Le projet écrit
    ses cadres dans « fiche-technique.json » ; le service les valide et ne donne que le modèle GLB qu'il déclare."""
    SHEET = {'format': 'aurora.fiche/1', 'titre': 'Lampe de bureau', 'sous_titre': 'fiche technique', 'modele': 'modele.glb',
             'cadres': [{'type': 'vue', 'titre': 'VUE DE FACE', 'azimut': 0, 'cote_largeur': '120 mm', 'place': [1, 1, 8, 9],
                         'reperes': [{'n': 1, 'piece': 'Pied'}]},
                        {'type': 'tableau', 'titre': 'SPÉCIFICATIONS', 'lignes': [['Puissance', '9 W']], 'note': 'Calculé.'},
                        {'type': 'etat', 'titre': 'ÉTAT DU PROJET', 'accent': 'orange'}],
             'cartouche': [['Projet', 'Lampe']], 'note': 'Rien n’est mesuré.'}

    def project(self, sheet, *, files=('fiche-technique.json', 'modele.glb'), model='modele.glb') -> None:
        declared = [{'nom': 'Fiche', 'chemin': 'fiche-technique.json', 'role': '', 'genere': True}, {'nom': 'Modèle', 'chemin': model, 'role': ''}]
        where = self.write('lampe', manifest(rubriques=[{'id': 'dossier', 'note': '', 'fichiers': declared}]), files=files)
        for left in ('fiche-technique.json', 'modele.glb', 'autre.glb', 'modele.txt'):       # le dossier sert à plusieurs cas
            if left not in files:
                (where / left).unlink(missing_ok=True)
        if 'fiche-technique.json' in files:
            (where / 'fiche-technique.json').write_text(sheet if isinstance(sheet, str) else json.dumps(sheet, ensure_ascii=False), 'utf-8')

    def test_the_frames_and_the_model_of_a_project(self):
        self.project(self.SHEET)
        self.assertEqual(projets.load('lampe-de-bureau')['fiche_donnees'], 'fiche-technique.json')
        got = self.client.get('/api/projets/lampe-de-bureau/fiche/donnees')
        self.assertEqual(got.status_code, 200, got.text)
        sheet = got.json()
        self.assertEqual([c['type'] for c in sheet['fiche']['cadres']], ['vue', 'tableau', 'etat'])
        self.assertEqual((sheet['fiche']['cadres'][0]['place'], sheet['fiche']['cadres'][0]['reperes']), ([1, 1, 8, 9], [{'n': 1, 'piece': 'Pied'}]))
        self.assertEqual(sheet['modele'], {'chemin': 'modele.glb', 'taille': 1})          # jamais son chemin sur le disque
        model = self.client.get('/api/projets/lampe-de-bureau/fiche/modele')
        self.assertEqual((model.status_code, model.headers['content-type'], model.content), (200, 'model/gltf-binary', b'x'))
        for route in ('donnees', 'modele'):
            self.assertEqual(self.client.get(f'/api/projets/lampe-de-bureau/fiche/{route}', headers=NO_TOKEN).status_code, 401)
            self.assertEqual(self.client.get(f'/api/projets/absent/fiche/{route}').status_code, 404)

    def test_only_a_declared_glb_is_given(self):
        for sheet, files, model, why in (
                ({**self.SHEET, 'modele': 'autre.glb'}, ('fiche-technique.json', 'modele.glb', 'autre.glb'), 'modele.glb', 'un fichier que le projet ne déclare pas'),
                ({**self.SHEET, 'modele': 'modele.txt'}, ('fiche-technique.json', 'modele.txt'), 'modele.txt', 'pas un fichier GLB'),
                (self.SHEET, ('fiche-technique.json',), 'modele.glb', 'déclaré mais absent'),
                ({**self.SHEET, 'modele': ''}, ('fiche-technique.json', 'modele.glb'), 'modele.glb', 'une fiche sans modèle')):
            with self.subTest(why):
                self.project(sheet, files=files, model=model)
                self.assertIsNone(self.client.get('/api/projets/lampe-de-bureau/fiche/donnees').json()['modele'])
                self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche/modele').status_code, 404)
        self.project(self.SHEET)
        with mock.patch.object(projets, 'MAX_MODELE_FICHE', 0):
            self.assertIsNone(projets.fiche_donnees('lampe-de-bureau')['modele'])           # trop gros : pas donné

    def test_a_refused_sheet_says_why(self):
        for sheet, word in (({**self.SHEET, 'script': 'alert(1)'}, 'refusée'), ({**self.SHEET, 'modele': '../ailleurs.glb'}, 'refusée'),
                            ({**self.SHEET, 'cadres': [{'type': 'vue', 'titre': 'X', 'place': [20, 1, 8, 9]}]}, 'grille de 24 colonnes'),
                            ({**self.SHEET, 'cadres': []}, 'refusée'), ('ceci n’est pas du JSON', 'JSON lisible')):
            with self.subTest(word=word, sheet=str(sheet)[:40]):
                self.project(sheet)
                got = self.client.get('/api/projets/lampe-de-bureau/fiche/donnees')
                self.assertEqual(got.status_code, 422, got.text)
                self.assertIn(word, got.json()['detail'])
                self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche/modele').status_code, 422)

    def test_a_project_without_sheet_data(self):
        self.project(self.SHEET, files=('modele.glb',))
        self.assertIsNone(projets.load('lampe-de-bureau')['fiche_donnees'])
        self.assertIsNone(projets.fiche_donnees('lampe-de-bureau'))
        self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche/donnees').status_code, 404)


class CreationTests(ProjectsCase):
    """23/11/2026, ID Studio IA : « une icône création de projet ; on laisse la fiche technique vide sans les modèles 3D ».
    Un projet vide, écrit dans les projets de la personne, jamais par-dessus un dossier qui existe."""

    def test_a_project_is_created_empty_in_the_persons_own_projects(self):
        made = projets.creer('  Lampe de bureau à LED  ', 'Une lampe pour mon établi.')
        self.assertEqual((made['id'], made['titre'], made['resume'], made['origine']),
                         ('lampe-de-bureau-a-led', 'Lampe de bureau à LED', 'Une lampe pour mon établi.', 'personnel'))
        folder = self.root / 'lampe-de-bureau-a-led'
        self.assertEqual(Path(made['dossier']), folder)
        written = json.loads((folder / projets.MANIFEST).read_text('utf-8'))
        self.assertEqual((written['format'], written['stade']), ('aurora.projet/1', 'idée'))
        self.assertEqual([r['id'] for r in written['rubriques']], list(projets.RUBRIQUES))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [projets.MANIFEST])   # nothing else: no sheet, no model
        self.assertFalse(made['pret_a_fabriquer'])                                       # and it says so
        self.assertEqual(made['pourquoi_pas'], ['aucune vérification écrite'])
        self.assertEqual((made['fiche'], made['fiche_donnees'], made['calcul'], made['fabrication']), (None, None, None, None))
        self.assertEqual(self.mine()['lampe-de-bureau-a-led']['verifications']['total'], 0)

    def test_a_name_already_taken_gets_a_number_and_nothing_is_overwritten(self):
        first = projets.creer('Lampe')
        before = (self.root / 'lampe' / projets.MANIFEST).read_text('utf-8')
        second = projets.creer('Lampe !')
        (self.root / 'lampe-3').mkdir()               # a folder that is not a project yet: not taken over either
        third = projets.creer('lampe')
        self.assertEqual((first['id'], second['id'], third['id']), ('lampe', 'lampe-2', 'lampe-4'))
        self.assertEqual((self.root / 'lampe' / projets.MANIFEST).read_text('utf-8'), before)
        self.assertEqual(projets._identifiant('Éolienne de 2 kW — étude'), 'eolienne-de-2-kw-etude')
        self.assertEqual(projets._identifiant('日本語'), 'projet')
        self.assertEqual(len(projets._identifiant('x' * 90)), 40)

    def test_what_is_refused(self):
        for titre, resume in (('', ''), (' a ', ''), ('x' * 101, ''), ('Lampe', 'r' * 601)):
            with self.assertRaises(ValueError) as caught:
                projets.creer(titre, resume)
            self.assertIn('de 2 à 100 caractères', str(caught.exception))
        self.assertEqual(self.mine(), {})                                                # nothing was written
        with mock.patch.object(projets, 'MAX_PROJECTS', 0), self.assertRaises(ValueError) as caught:
            projets.creer('Lampe')
        self.assertIn('projets au plus', str(caught.exception))

    def test_the_route(self):
        res = self.client.post('/api/projets', json={'titre': 'Lampe de bureau', 'resume': 'Une lampe.'})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()['id'], res.json()['origine']), ('lampe-de-bureau', 'personnel'))
        self.assertIn('lampe-de-bureau', [p['id'] for p in self.client.get('/api/projets').json()['projets']])
        self.assertEqual(self.client.get('/api/projets/lampe-de-bureau/fiche').status_code, 404)   # empty: no sheet
        bad = self.client.post('/api/projets', json={'titre': ' '})
        self.assertEqual(bad.status_code, 422)
        self.assertIn('de 2 à 100 caractères', bad.json()['detail'])
        self.assertEqual(self.client.post('/api/projets', json={'titre': 'Lampe', 'dossier': 'C:/x'}).status_code, 422)
        self.assertEqual(self.client.post('/api/projets', json={'titre': 'Lampe'}, headers=NO_TOKEN).status_code, 401)


class SizingTests(ProjectsCase):
    """13/11/2026, « les calculs en premier » : un cahier des charges confronté au programme de calcul du projet.
    Les chiffres viennent du programme (docs/robot-quadrupede/quadrupede.py), jamais d'une IA."""

    @needs_robot
    def test_the_quadruped_answers_a_requirement(self):
        asked = projets.dimensionner('robot-quadrupede', 5, 120, 12)   # ID Studio IA : « cinq kilos, douze articulations, deux heures »
        self.assertEqual((asked['projet'], asked['programme'], asked['verdict']), ('robot-quadrupede', 'quadrupede.py', 'pas_faisable'))
        self.assertEqual([line['etat'] for line in asked['lignes']], ['tenue', 'non_tenue', 'non_tenue', 'non_tenue', 'tenue'])
        self.assertEqual(asked['demande'], {'masse_kg': 5.0, 'autonomie_min': 120.0, 'articulations': 12})
        self.assertGreater(asked['energie']['batteries'], asked['energie']['places'])
        self.assertGreaterEqual(asked['energie']['autonomie_obtenue_min'], 120)
        self.assertGreater(asked['servomoteur']['besoin_trot_kgcm'], asked['servomoteur']['nominal_kgcm'])
        self.assertAlmostEqual(sum(p['grammes'] for p in asked['masse']['postes']) / 1000, asked['masse']['total_kg'], delta=0.01)
        self.assertTrue(asked['hypotheses'])
        light = projets.dimensionner('robot-quadrupede', 3, 20)
        self.assertEqual((light['verdict'], light['energie']['batteries']), ('faisable', 1))
        self.assertTrue(all(line['etat'] == 'tenue' for line in light['lignes']))
        # A heavier robot loads its joints more, and the limit said for the trot is the same whatever is asked.
        parts = [[c['part_nominal_pct'] for c in r['couples']] for r in (light, asked)]
        self.assertTrue(all(a < b for a, b in zip(*parts)))
        self.assertEqual(light['servomoteur']['masse_max_trot_kg'], asked['servomoteur']['masse_max_trot_kg'])
        between = projets.dimensionner('robot-quadrupede', 4, 15)      # walks, cannot trot
        self.assertEqual(between['verdict'], 'marche_seulement')
        self.assertEqual(projets.dimensionner('robot-quadrupede', 3, 20, 8)['lignes'][0]['etat'], 'non_tenue')
        self.assertEqual(projets.load('robot-quadrupede')['calcul'], 'quadrupede.py')

    def test_what_is_refused(self):
        self.write('lampe', manifest())                                 # a personal project: its program is not run
        self.assertIsNone(self.mine()['lampe-de-bureau']['calcul'])
        with self.assertRaises(ValueError) as caught:
            projets.dimensionner('lampe-de-bureau', 3, 20)
        self.assertIn('projet personnel', str(caught.exception))
        self.write('sans', manifest(id='sans-calcul', rubriques=[]))
        with self.assertRaises(ValueError) as caught:
            projets.dimensionner('sans-calcul', 3, 20)
        self.assertIn('n’a pas de programme de calcul', str(caught.exception))
        if (ROBOT / projets.MANIFEST).is_file():                        # the shipped robot, where it is shipped
            with self.assertRaises(ValueError) as caught:
                projets.dimensionner('robot-quadrupede', 500, 20)       # the program itself refuses the request
            self.assertIn('La masse va de', str(caught.exception))
        for unknown in ('absent', '../lampe', ''):
            self.assertIsNone(projets.dimensionner(unknown, 3, 20), unknown)

    def test_a_program_that_fails_gives_no_figure(self):
        where = self.write('lampe', manifest())
        program = where / 'calcul.py'
        with mock.patch.object(projets, 'TRUSTED_ORIGINS', ('AURORA', 'personnel')), \
                mock.patch.object(projets.log, 'exception'):   # the failures below are wanted: no noise in the output
            cases = (('x = 1\n', 'ne sait pas répondre'),
                     ('def dimensionner(m, a, n):\n    return 1 / 0\n', 'a échoué (ZeroDivisionError)'),
                     ('def dimensionner(m, a, n):\n    return {"verdict": "faisable"}\n', 'pas rendu un résultat lisible'),
                     ('import nulle_part\n', 'a échoué (ModuleNotFoundError)'))
            for n, (code, said) in enumerate(cases):
                program.write_text(code, 'utf-8')
                os.utime(program, (1_700_000_000 + n, 1_700_000_000 + n))   # a changed file is loaded again
                with self.assertRaises(ValueError) as caught:
                    projets.dimensionner('lampe-de-bureau', 3, 20)
                self.assertIn(said, str(caught.exception), code)
            program.write_text('def dimensionner(m, a, n):\n    return {"verdict": "faisable", "lignes": [], "m": m}\n', 'utf-8')
            os.utime(program, (1_700_000_100, 1_700_000_100))
            self.assertEqual(projets.dimensionner('lampe-de-bureau', 3, 20),
                             {'projet': 'lampe-de-bureau', 'titre': 'Lampe de bureau', 'programme': 'calcul.py',
                              'verdict': 'faisable', 'lignes': [], 'm': 3})

    @needs_robot
    def test_the_route(self):
        ok = self.client.get('/api/projets/robot-quadrupede/calcul', params={'masse_kg': 5, 'autonomie_min': 120})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual((ok.json()['verdict'], ok.json()['demande']['articulations']), ('pas_faisable', 12))
        get = lambda url, **params: self.client.get(url, params=params).status_code   # noqa: E731
        self.assertEqual(get('/api/projets/absent/calcul', masse_kg=5, autonomie_min=120), 404)
        self.assertEqual(get('/api/projets/robot-quadrupede/calcul', masse_kg=0, autonomie_min=120), 422)
        self.assertEqual(get('/api/projets/robot-quadrupede/calcul', masse_kg=5), 422)
        self.write('lampe', manifest())
        refused = self.client.get('/api/projets/lampe-de-bureau/calcul', params={'masse_kg': 5, 'autonomie_min': 120})
        self.assertEqual(refused.status_code, 422)
        self.assertIn('projet personnel', refused.text)
        self.assertEqual(self.client.get('/api/projets/robot-quadrupede/calcul?masse_kg=5&autonomie_min=120',
                                         headers=NO_TOKEN).status_code, 401)


@needs_robot
class SentenceTests(ProjectsCase):
    """La même demande dans le chat : les nombres sont lus dans la phrase, le programme du projet répond."""

    def test_the_numbers_of_the_sentence(self):
        robot = 'robot-quadrupede'
        cases = {
            'Aurora, conçois-moi un robot quadrupède de cinq kilos, avec douze articulations et deux heures d’autonomie.': (5.0, 120.0, 12),
            'Dimensionne un quadrupède de 3,5 kg avec 45 minutes d’autonomie': (3.5, 45.0, 12),
            'Est-ce qu’un robot de 4 kg qui tient 1 h 30 est faisable ?': (4.0, 90.0, 12),
            'calcule un robot quadrupède de 2.7 kg, 12 servomoteurs, 26 min': (2.7, 26.0, 12),
            'conçois un robot de trois kilos et demi avec une heure et demie d’autonomie': (3.5, 90.0, 12),
            'un robot de 5kg avec 2h d’autonomie, c’est possible ?': (5.0, 120.0, 12),
            'étudie un robot de 3 kg, 2 heures, 8 articulations': (3.0, 120.0, 8),
            'conçois-moi un robot de 4 kilos': (4.0, None, 12),                  # no autonomy said: the answer asks
        }
        for text, (masse, autonomie, articulations) in cases.items():
            self.assertEqual(projets.demande_de_calcul(text), {'projet': robot, 'masse_kg': masse, 'autonomie_min': autonomie,
                                                               'articulations': articulations}, text)
        for text in ('montre-moi le robot quadrupède', 'combien pèse un robot de 5 kg ?', 'calcule 5 kg en livres',
                     'conçois-moi un robot', 'ouvre le tibia du quadrupède dans FreeCAD', ''):
            self.assertIsNone(projets.demande_de_calcul(text), text)

    def test_the_plan_and_the_tool(self):
        from aurora import config
        from aurora.core import compose
        from aurora.core import planner as planner_mod
        from aurora.tools.base import run_tool_sync
        slots = {'projet': 'robot-quadrupede', 'masse_kg': 5.0, 'autonomie_min': 120.0, 'articulations': 12}
        # Under the existing intent « Calcul », with the slot « cahier » (as a 3D scene is under « Navigation »).
        built = planner_mod.Planner().build({'intent': 'calc.evaluate', 'slots': {'cahier': True, **slots}}, {'engine': 'tool'},
                                            text='x', settings=config.default_settings())
        found = [s for s in built if s['stage'] == 'tool']
        self.assertEqual([(s['tool'], s['params'], s['requires_permission']) for s in found], [('calc.projet', slots, False)])
        no_autonomy = planner_mod.Planner().build({'intent': 'calc.evaluate', 'slots': {'cahier': True, **slots, 'autonomie_min': None}},
                                                  {'engine': 'tool'}, text='x', settings=config.default_settings())
        self.assertEqual([s for s in no_autonomy if s['stage'] == 'tool'], [])
        self.assertIn('dites aussi l’autonomie', compose.project_sizing({**slots, 'autonomie_min': None}, None))
        res = run_tool_sync('calc.projet', slots)
        self.assertTrue(res.ok, res.error)
        self.assertEqual((res.data['verdict'], res.provenance), ('pas_faisable', 'projet:quadrupede.py'))
        self.assertIn('quadrupede.py', res.sources[0].title)
        said = compose.project_sizing(slots, res.to_dict())
        for part in ('Pas faisable', '- Non tenu : Trotter', '- Tenu : Masse de 5 kg', 'jamais d’une IA'):
            self.assertIn(part, said)
        too_heavy = run_tool_sync('calc.projet', {**slots, 'masse_kg': 500.0})
        self.assertFalse(too_heavy.ok)
        self.assertIn('La masse va de', too_heavy.error)
        self.assertIn('Aucun calcul', too_heavy.error)


@needs_robot
class WholePathTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_sentence_is_answered_by_the_program(self):
        import logging

        from aurora.core.orchestrator import Deps, Orchestrator
        from aurora.tasks import events
        from tests.doubles_core import FakeClaude, FakeDecision, FakeEngine, FakeOllama, FakeRouter, TempEnv, make_caps
        from tests.test_pc_control import RealTools
        with TempEnv():
            logging.getLogger('aurora').setLevel(logging.CRITICAL)
            events.bus.detach_loop()
            orch = Orchestrator(Deps(decision_engine=FakeEngine(FakeDecision('chat.general')), router=FakeRouter,
                                     tools=RealTools(), ollama=FakeOllama(), claude=FakeClaude(configured=False),
                                     capabilities=make_caps()))
            result = await orch.handle_request('Conçois-moi un robot quadrupède de cinq kilos, avec douze articulations '
                                               'et deux heures d’autonomie')
            self.assertEqual(result['status'], 'done', result.get('answer'))
            self.assertIsNone(result.get('permission'))
            for part in ('quadrupede.py', 'Pas faisable', '7 batteries', 'jamais d’une IA'):
                self.assertIn(part, result['answer'])
            asked = await orch.handle_request('conçois-moi un robot de 4 kilos')
            self.assertIn('dites aussi l’autonomie', asked['answer'])


if __name__ == '__main__':
    unittest.main()
