"""L'atelier (tools/atelier.py, 15/11/2026) : AURORA lance le programme de fabrication d'un projet et suit ses étapes.

Chaque essai fabrique son projet dans le AURORA_HOME temporaire, avec un petit programme qui dit trois étapes. Le
vrai programme du quadrupède n'est jamais lancé ici.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

from aurora.tools import atelier, projets
from tests.test_api import NO_TOKEN
from tests.test_projets import ProjectsCase, manifest, needs_robot

GLB = "b'glTF' + (2).to_bytes(4, 'little') + (20).to_bytes(4, 'little') + bytes(8)"
BUILD = f'''import json, time
from pathlib import Path
ici = Path(__file__).parent
with open(ici / 'lancements.txt', 'a') as f:
    f.write('x')
for n, quoi in enumerate(('Corps', 'Pattes', 'Modèle assemblé'), 1):
    print('AURORA-ETAPE ' + json.dumps({{'n': n, 'sur': 3, 'quoi': quoi}}, ensure_ascii=False), flush=True)
    time.sleep(0.25)
print('une ligne de journal')
(ici / 'sortie').mkdir(exist_ok=True)
(ici / 'sortie' / 'robot.glb').write_bytes({GLB})
print('AURORA-FIN ' + json.dumps({{'modele': 'sortie/robot.glb', 'pieces': 5, 'resume': 'Fini.', 'inconnu': 'x'}}), flush=True)
'''


class AtelierCase(ProjectsCase):
    def setUp(self):
        super().setUp()
        atelier._JOBS.clear()
        self.trusted = mock.patch.object(projets, 'TRUSTED_ORIGINS', ('AURORA', 'personnel'))
        self.trusted.start()
        self.addCleanup(self.trusted.stop)

    def project(self, code: str = BUILD, ident: str = 'lampe-de-bureau'):
        where = self.write(ident, manifest(id=ident, rubriques=[{'id': 'fabrication', 'note': '', 'fichiers': [
            {'nom': 'Programme de construction', 'chemin': 'construire.py', 'role': ''}]}]), files=())
        (where / 'construire.py').write_text(code, 'utf-8')
        return where

    def finished(self, ident: str = 'lampe-de-bureau') -> dict:
        limit = time.monotonic() + 40
        while time.monotonic() < limit:
            state = atelier.etat(ident)
            if state['etat'] != 'en_cours':
                return state
            time.sleep(0.05)
        self.fail('la construction ne finit pas')


class BuildTests(AtelierCase):
    def test_the_steps_are_followed_until_the_model(self):
        where = self.project()
        self.assertEqual(atelier.etat('lampe-de-bureau'), {'projet': 'lampe-de-bureau', 'titre': 'Lampe de bureau',
                                                           'programme': 'construire.py', 'etat': 'jamais'})
        first = atelier.lancer('lampe-de-bureau')
        self.assertEqual((first['etat'], first['programme']), ('en_cours', 'construire.py'))
        again = atelier.lancer('lampe-de-bureau')                      # asked twice: the one that runs, never two
        self.assertEqual(again['etat'], 'en_cours')
        done = self.finished()
        self.assertEqual((done['etat'], done['n'], done['sur'], done['erreur']), ('fini', 3, 3, None))
        self.assertEqual(done['etapes'], ['Corps', 'Pattes', 'Modèle assemblé'])
        self.assertEqual(done['journal'], ['une ligne de journal'])
        self.assertEqual(done['resultat'], {'modele': 'sortie/robot.glb', 'taille': 20, 'resume': 'Fini.', 'pieces': 5})
        self.assertGreaterEqual(done['secondes'], 0.5)
        self.assertEqual((where / 'lancements.txt').read_text('utf-8'), 'x')   # launched once
        atelier.lancer('lampe-de-bureau')                              # finished: it can be launched again
        self.assertEqual(self.finished()['etat'], 'fini')
        self.assertEqual((where / 'lancements.txt').read_text('utf-8'), 'xx')

    def test_a_program_that_fails_is_not_shown_as_finished(self):
        cases = (('import sys\nprint("cassé")\nsys.exit(3)\n', 'une erreur (code 3)'),
                 ('print("AURORA-ETAPE {\\"n\\": 1, \\"sur\\": 2, \\"quoi\\": \\"Corps\\"}")\n', 'pas dit sa ligne de fin'),
                 ('print("AURORA-FIN {\\"modele\\": \\"../../aurora.db\\"}")\n', 'pas dit sa ligne de fin'),      # outside the project
                 ('print("AURORA-FIN {\\"modele\\": \\"sortie/absent.glb\\"}")\n', 'pas dit sa ligne de fin'),   # no such file
                 ('print("AURORA-FIN pas du json")\n', 'pas dit sa ligne de fin'))
        for n, (code, said) in enumerate(cases):
            ident = f'cas-{n}'
            self.project(code, ident)
            atelier.lancer(ident)
            done = self.finished(ident)
            self.assertEqual((done['etat'], done['resultat']), ('echec', None), code)
            self.assertIn(said, done['erreur'], code)
        self.assertIn('Sa dernière ligne : cassé', atelier.etat('cas-0')['erreur'])

    def test_what_is_not_launched(self):
        self.project()
        self.trusted.stop()                                            # a personal project again: its program is not run
        try:
            with self.assertRaises(ValueError) as caught:
                atelier.lancer('lampe-de-bureau')
            self.assertIn('projet personnel', str(caught.exception))
            self.assertEqual(atelier.etat('lampe-de-bureau')['etat'], 'indisponible')
        finally:
            self.trusted.start()
        self.write('sans', manifest(id='sans-programme', rubriques=[]))
        with self.assertRaises(ValueError) as caught:
            atelier.lancer('sans-programme')
        self.assertIn('n’a pas de programme de fabrication', str(caught.exception))
        for unknown in ('absent', '../lampe', ''):
            self.assertIsNone(atelier.lancer(unknown), unknown)
            self.assertIsNone(atelier.etat(unknown), unknown)
        self.assertEqual(atelier._JOBS, {})

    def test_the_model_is_kept_on_request_only(self):
        from aurora.tools import lab
        self.project()
        self.assertEqual(atelier.garder_le_modele('lampe-de-bureau')[1], 'Aucune construction finie pour ce projet : lancez-la d’abord.')
        atelier.lancer('lampe-de-bureau')
        self.finished()
        before = len(lab.catalogue())
        scene, why, known = atelier.garder_le_modele('lampe-de-bureau')
        self.assertIsNone(why)
        self.assertEqual((scene['origine'], known, len(lab.catalogue())), ('importe', False, before + 1))
        self.assertTrue(atelier.garder_le_modele('lampe-de-bureau')[2])   # the same file again: its scene, no copy
        self.assertEqual(len(lab.catalogue()), before + 1)

    @needs_robot
    def test_the_shipped_robot_names_its_program_and_is_not_run_here(self):
        self.assertEqual(atelier.etat('robot-quadrupede'), {
            'projet': 'robot-quadrupede', 'titre': projets.load('robot-quadrupede')['titre'], 'programme': 'coque_glb.py', 'etat': 'jamais'})
        self.assertEqual(projets.load('robot-quadrupede')['fabrication'], 'coque_glb.py')


class RouteTests(AtelierCase):
    def test_the_three_routes(self):
        self.project()
        url = '/api/projets/lampe-de-bureau/fabrication'
        self.assertEqual(self.client.get(url).json()['etat'], 'jamais')
        started = self.client.post(url)
        self.assertEqual((started.status_code, started.json()['etat']), (200, 'en_cours'))
        self.finished()
        self.assertEqual(self.client.get(url).json()['etat'], 'fini')
        kept = self.client.post(url + '/modele')
        self.assertEqual((kept.status_code, kept.json()['deja']), (200, False), kept.text)
        self.assertEqual(self.client.post('/api/projets/absent/fabrication').status_code, 404)
        self.assertEqual(self.client.get('/api/projets/absent/fabrication').status_code, 404)
        self.assertEqual(self.client.post('/api/projets/absent/fabrication/modele').status_code, 422)
        self.write('sans', manifest(id='sans-programme', rubriques=[]))
        self.assertEqual(self.client.post('/api/projets/sans-programme/fabrication').status_code, 422)
        self.assertEqual(self.client.get('/api/projets/sans-programme/fabrication').json()['etat'], 'indisponible')
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(url, headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.post(url + '/modele', headers=NO_TOKEN).status_code, 401)


if __name__ == '__main__':
    unittest.main()
