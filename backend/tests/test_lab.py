"""Laboratoire 3D de l'accueil (08/11/2026, reviews/accueil-holographique.md § 2 et § 3).

The scene format aurora.scene/1 (tools/lab.py: strict validation, cache per file, tools lab.catalogue / lab.scene),
the commands on the scene shown (core/normalize.extract_scene_order), the deterministic answers (core/compose) and
the orchestrator path (options.scene -> ui.navigate + ui.scene). No network; the database is a temporary one.
"""
import copy
import hashlib
import json
import logging
import math
import os
import struct
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aurora import config
from aurora.core import capabilities, compose, rules, verify
from aurora.core.engine_select import select_engine
from aurora.core.normalize import extract_scene_order, extract_slots, scene_subject
from aurora.tools import base, lab
from tests.doubles_core import FakeClaude, FakeDecision, FakeOllama, FakeTools, TempEnv, build
from tests.test_api import NO_TOKEN, ApiTestCase

REAL_FILE = Path(lab.SCENES_DIR) / 'bras_robotique.json'
CTX = {'id': 'bras_robotique'}


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def good() -> dict:
    """A fresh copy of the real scene, read from its file (not from the cache)."""
    return json.loads(REAL_FILE.read_text(encoding='utf-8'))


def piece(data: dict, piece_id: str) -> dict:
    return next(p for p in data['pieces'] if p['id'] == piece_id)


def motor() -> dict:
    """What a model could write for « un moteur électrique » (phase 4), with mistakes the code repairs: a parent
    written after its child, an unknown parent, the same name twice without a group, an unknown colour and shape,
    markup and an address in a text, a group without pieces, sizes in « metres » far too large."""
    def part(pid, nom, forme, taille, position, **more):
        return {'id': pid, 'nom': nom, 'groupe': '', 'parent': '', 'forme': forme, 'taille': taille, 'axe': 'x',
                'position': position, 'couleur': 'cyan', 'mouvement': 'aucun', 'fonction': f'Rôle de {nom}.',
                'mots': [], **more}
    return {
        'titre': 'Moteur électrique', 'domaine': 'electronique',
        'resume': 'Un moteur électrique transforme le courant en rotation.',
        'fonctionnement': 'Le courant traverse les <b>bobines</b> du rotor (voir https://exemple.fr) ; les aimants '
                          'le repoussent et il tourne.',
        'mots': ['moteur a courant continu', 'moteur'],
        'groupes': [{'id': 'bobines', 'nom': 'Bobines', 'mots': ['bobines', 'enroulements']},
                    {'id': 'vide', 'nom': 'Groupe vide', 'mots': ['vide']}],
        'pieces': [
            part('boitier', 'Boîtier', 'cylindre', [12, 10, 10], [0, 5, 0], mots=['boitier', 'carter']),
            part('arbre', 'Arbre', 'cylindre', [18, 1, 1], [0, 5, 0], parent='rotor', couleur='ambre'),
            part('rotor', 'Rotor', 'cylindre', [9, 6, 6], [0, 5, 0], mouvement='tourne', couleur='violet'),
            part('bobine_a', 'Bobine', 'boite', [6, 1, 2], [0, 7.5, 0], parent='rotor', groupe='bobines',
                 mots=['bobine']),
            part('bobine_b', 'Bobine', 'boite', [6, 1, 2], [0, 2.5, 0], parent='rotor', groupe='bobines'),
            part('aimant', 'Aimant', 'boite', [8, 1, 4], [0, 9.5, 0], parent='inconnu', couleur='rouge'),
            part('aimant', 'Aimant', 'boite', [8, 1, 4], [0, 0.5, 0], parent='boitier'),
            part('levier', 'Levier', 'triangle', [1, 3, 1], [0, 11, 2], axe='w', mouvement='oscille'),
        ]}


class ValidationTests(unittest.TestCase):
    def assertRejected(self, data: dict, *words: str, file_id: str | None = 'bras_robotique') -> None:
        why = lab.validate(data, file_id=file_id)
        self.assertIsNotNone(why, 'la description aurait dû être refusée')
        for word in words:
            self.assertIn(word, why)

    def test_the_real_scene_is_valid(self):
        self.assertIsNone(lab.validate(good(), file_id='bras_robotique'))
        self.assertEqual(len(good()['pieces']), 13)

    def test_format_ids_and_file_name(self):
        data = good()
        data['format'] = 'aurora.scene/2'
        self.assertRejected(data, 'format')
        data = good()
        data['id'] = 'Bras-Robot'
        self.assertRejected(data, 'id', file_id=None)
        self.assertRejected(good(), 'ne correspond pas au nom du fichier', file_id='autre_scene')
        data = good()
        data['pieces'][2]['id'] = 'Support!'
        self.assertRejected(data, 'identifiant invalide')

    def test_no_extra_field_no_code(self):
        data = good()
        data['script'] = 'alert(1)'
        self.assertRejected(data, 'champ non autorisé')
        data = good()
        data['pieces'][0]['onclick'] = 'x'
        self.assertRejected(data, 'pièce « socle »', 'champ non autorisé')
        for text in ('<script>alert(1)</script>', 'voir https://exemple.org', 'javascript: x()', 'www.site.fr',
                     'ligne\x00cachée'):
            data = good()
            data['pieces'][1]['fonction'] = text
            self.assertRejected(data, 'pièce « plateau »')

    def test_numbers_are_finite_strict_and_bounded(self):
        for value in (float('nan'), float('inf'), '1', True, None):
            data = good()
            data['pieces'][0]['position'] = [value, 0, 0]
            self.assertRejected(data, 'pièce « socle »')
        data = good()
        data['pieces'][0]['position'] = [15, 15, 0]            # norm > 20
        self.assertRejected(data, 'position')
        data = good()
        data['pieces'][0]['position'] = [0, 0]
        self.assertRejected(data, 'position')
        data = good()
        data['pieces'][0]['rotation'] = [0, 400, 0]
        self.assertRejected(data, 'rotation')
        data = good()
        data['camera']['position'] = [60, 0, 0]
        self.assertRejected(data, 'camera')
        data = good()
        data['pieces'][0]['forme'] = {'type': 'boite', 'l': 0, 'h': 1, 'p': 1}
        self.assertRejected(data, 'pièce « socle »')
        data = good()
        data['pieces'][0]['forme'] = {'type': 'boite', 'l': 11, 'h': 1, 'p': 1}
        self.assertRejected(data, 'pièce « socle »')

    def test_shapes_and_colours_come_from_the_lists(self):
        data = good()
        data['pieces'][2]['forme'] = {'type': 'cone', 'rayon': 1}
        self.assertRejected(data, 'pièce « support »', 'type non autorisé')
        data = good()
        data['pieces'][2]['forme'] = {'rayon': 1}
        self.assertRejected(data, 'type')
        data = good()
        data['pieces'][2]['couleur'] = 'rouge'
        self.assertRejected(data, 'couleur')
        for key, value in (('nature', 'film'), ('domaine', 'magie')):
            data = good()
            data[key] = value
            self.assertRejected(data, key)

    def test_joint_order_min_rest_max(self):
        data = good()
        joint = piece(data, 'servo_coude')['articulation']
        joint['repos'] = joint['max'] + 1
        self.assertRejected(data, 'pièce « servo_coude »')
        data = good()
        joint = piece(data, 'servo_coude')['articulation']
        joint['min'], joint['max'] = joint['max'], joint['min']
        self.assertRejected(data, 'pièce « servo_coude »')
        data = good()
        piece(data, 'servo_coude')['articulation']['axe'] = 'w'
        self.assertRejected(data, 'axe')

    def test_links_between_pieces_and_groups(self):
        data = good()
        data['pieces'][1]['id'] = 'socle'
        self.assertRejected(data, 'pièce « socle » déclarée deux fois')
        data = good()
        data['groupes'].append(copy.deepcopy(data['groupes'][0]))
        self.assertRejected(data, 'groupe « structure » déclaré deux fois')
        data = good()
        data['pieces'][0]['groupe'] = 'moteurs_x'
        self.assertRejected(data, 'groupe « moteurs_x » inconnu')
        data = good()
        data['pieces'][0]['parent'] = 'plateau'                # declared AFTER the socle: no loop possible
        self.assertRejected(data, 'doit être une pièce déclarée avant elle')
        data = good()
        data['pieces'][3]['parent'] = 'inexistant'
        self.assertRejected(data, '« inexistant »')
        data = good()
        data['pieces'][1]['mots'].append('socle')               # one word naming two pieces
        self.assertRejected(data, 'le mot « socle » désigne deux pièces')
        data = good()
        data['groupes'][1]['mots'].append('pince')
        self.assertRejected(data, 'désigne deux groupes')
        data = good()
        data['pieces'][0]['mots'] = ['Socle!']
        self.assertRejected(data, 'pièce « socle »')
        data = good()
        del data['pieces'][0]['parent']                         # required, even when null
        self.assertRejected(data, 'pièce « socle »')

    def test_piece_count_and_text_lengths(self):
        data = good()
        data['pieces'] = []
        self.assertRejected(data, 'pieces')
        data = good()
        extra = [dict(copy.deepcopy(data['pieces'][0]), id=f'p{i}', mots=[]) for i in range(60)]
        data['pieces'] += extra
        self.assertRejected(data, 'pieces')
        for key, size in (('titre', 81), ('resume', 401), ('avertissement', 301), ('fonctionnement', 1501)):
            data = good()
            data[key] = 'x' * size
            self.assertRejected(data, key)
        data = good()
        data['titre'] = '   '
        self.assertRejected(data, 'titre')
        data = good()
        data['pieces'][0]['fonction'] = 'x' * 601
        self.assertRejected(data, 'pièce « socle »')
        self.assertEqual(lab.validate([]), 'objet JSON attendu')


class FileAndCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self._patch = mock.patch.object(lab, 'SCENES_DIR', self.dir)
        self._patch.start()
        lab.clear_cache()

    def tearDown(self):
        self._patch.stop()
        lab.clear_cache()
        self._tmp.cleanup()

    def write(self, name: str, text: str, mtime_ns: int | None = None) -> Path:
        path = self.dir / name
        path.write_text(text, encoding='utf-8')
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def test_unreadable_files_stay_out_with_a_french_reason(self):
        self.write('bras_robotique.json', json.dumps(good(), ensure_ascii=False))
        cases = {'casse.json': '{"format": ', 'nan.json': json.dumps(good(), ensure_ascii=False).replace(
                 '"repos": 62', '"repos": NaN', 1), 'Mauvais-Nom.json': '{}', 'liste.json': '[]'}
        self.assertIn('NaN', cases['nan.json'])
        for name, text in cases.items():
            self.write(name, text)
        with self.assertLogs('aurora.tools.lab', 'WARNING') as logs:
            self.assertEqual([s['id'] for s in lab.catalogue()], ['bras_robotique'])
        self.assertEqual(len(logs.records), 4)
        with self.assertRaises(base.ToolError) as broken:
            lab.load_scene('casse')
        self.assertIn('JSON invalide (ligne 1', str(broken.exception))
        with self.assertRaises(base.ToolError) as nan:
            lab.load_scene('nan')
        self.assertIn('NaN', str(nan.exception))
        self.assertIn('Elle n’est pas affichée', str(nan.exception))
        big = self.write('gros.json', ' ' * (lab.MAX_FILE_BYTES + 1))
        self.assertIn('trop gros', lab._read(big)[1])

    def test_cache_follows_the_file(self):
        text = json.dumps(good(), ensure_ascii=False)
        path = self.write('bras_robotique.json', text, mtime_ns=1_000_000_000_000_000_000)
        self.assertEqual(len(lab.catalogue()), 1)
        with mock.patch.object(lab, '_read', wraps=lab._read) as reads:
            lab.catalogue()
            lab.load_scene('bras_robotique')
            self.assertEqual(reads.call_count, 0)                    # unchanged file: read once
        broken = good()
        broken['pieces'][0]['couleur'] = 'rouge'
        self.write('bras_robotique.json', json.dumps(broken, ensure_ascii=False), mtime_ns=1_000_000_000_100_000_000)
        with self.assertLogs('aurora.tools.lab', 'WARNING'):
            self.assertEqual(lab.catalogue(), [])                    # an invalid scene leaves the catalogue
        result = base.run_tool_sync('lab.scene', {'id': 'bras_robotique'})
        self.assertFalse(result.ok)
        self.assertIn('invalide', result.error)
        self.assertIn('couleur', result.error)
        self.write('bras_robotique.json', text, mtime_ns=1_000_000_000_200_000_000)
        self.assertEqual(len(lab.catalogue()), 1)                    # fixed: back at once
        self.assertTrue(path.exists())

    def test_empty_catalogue_is_said(self):
        result = base.run_tool_sync('lab.catalogue')
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {'scenes': []})
        self.assertEqual(result.summary, 'Aucune scène 3D n’est disponible pour le moment.')


class ToolTests(unittest.TestCase):
    def setUp(self):
        lab.clear_cache()

    def test_registered_without_network_read_only(self):
        for name in ('lab.catalogue', 'lab.scene'):
            spec = base.REGISTRY[name]
            self.assertFalse(spec.network, name)
            self.assertEqual(spec.action, 'system.read', name)

    def test_catalogue_and_scene(self):
        result = base.run_tool_sync('lab.catalogue')
        self.assertTrue(result.ok)
        self.assertEqual(result.data['scenes'][0], {**{k: good()[k] for k in ('id', 'titre', 'domaine', 'nature',
                                                                               'resume', 'mots')},
                                                    'origine': 'predefini'})
        scene = base.run_tool_sync('lab.scene', {'id': 'bras_robotique'})
        self.assertTrue(scene.ok)
        self.assertEqual(scene.data, good())
        self.assertEqual(scene.summary, 'Scène « Bras robotique à 4 axes » : 13 pièces, illustration pédagogique.')
        self.assertEqual(scene.provenance, lab.PROVENANCE)
        self.assertEqual(base.run_tool_sync('lab.scene', {'id': 'bras_robotique'}).data, scene.data)   # deterministic
        scene.data['titre'] = 'modifié'
        self.assertEqual(lab.load_scene('bras_robotique')['titre'], 'Bras robotique à 4 axes')        # a copy

    def test_unknown_or_malformed_id(self):
        unknown = base.run_tool_sync('lab.scene', {'id': 'moteur'})
        self.assertFalse(unknown.ok)
        self.assertIn('Scène « moteur » inconnue', unknown.error)
        self.assertIn('Bras robotique à 4 axes', unknown.error)
        for bad in ({'id': 'Moteur!'}, {'id': '../secrets'}, {}):
            result = base.run_tool_sync('lab.scene', bad)
            self.assertFalse(result.ok, bad)
            self.assertTrue(result.error, bad)

    def test_capability_line_matches_the_catalogue(self):
        line = next(l for l in capabilities.capability_lines(list(base.REGISTRY)) if l.startswith('Laboratoire 3D'))
        scenes = [s for s in lab.catalogue() if s['origine'] == 'predefini']
        self.assertEqual(len(scenes), 3, 'une scène prédéfinie de plus : mettre à jour la ligne « Laboratoire 3D »')
        self.assertIn('trois scènes prédéfinies', line)
        for scene in scenes:   # 24/11/2026: the rocket and the X-SWING are delivered with the robot arm
            self.assertIn(scene['titre'].lower(), line.lower())
        self.assertIn('illustrations pédagogiques (jamais des simulations)', line)
        self.assertIn('construite automatiquement', line)   # phase 4: built on request
        self.assertNotIn('pas encore possible', line)

    def test_origin_is_not_a_measure(self):
        steps = [{'stage': 'tool', 'status': 'done', 'tool': 'lab.scene', 'result': {'ok': True}}]
        self.assertIsNone(verify.origin(steps, llm_text_used=False))


# The table of the contract (§ 3), with a scene shown and nothing selected.
TABLE = {
    'eclater': ['démonte le bras', 'vue éclatée', 'décompose-le', 'sépare les pièces'],
    'assembler': ['remonte-le', 'rassemble les pièces', 'réassemble', 'remets les pièces en place'],
    'animer': ['fais bouger le bras', 'simule son mouvement', 'anime-le'],
    'arreter': ["arrête l'animation", 'stop', 'fige-le', 'arrête ça'],
    'recentrer': ['recentre la vue', 'vue initiale'],
    'noyau': ['reviens au noyau', 'retour au noyau central', 'ferme la scène'],
    'tout_montrer': ['montre toutes les pièces', 'remets tout'],
    'expliquer': ['explique son fonctionnement', 'comment ça marche ?'],
    'interieur': ["montre l'intérieur", 'vue en coupe', 'transparence'],
}
TARGETED = {
    'fais tourner la tourelle': ('animer', 'piece', 'plateau'),
    'montre les servomoteurs': ('montrer', 'groupe', 'servomoteurs'),
    'où est le microcontrôleur ?': ('montrer', 'piece', 'microcontroleur'),
    'zoome sur la pince': ('montrer', 'groupe', 'pince'),
    'cache la pince': ('masquer', 'groupe', 'pince'),
    'enlève la carte': ('masquer', 'piece', 'carte'),
    'montre seulement les servomoteurs': ('isoler', 'groupe', 'servomoteurs'),
    'isole le coude': ('isoler', 'piece', 'servo_coude'),
    'explique le circuit électronique': ('expliquer', 'groupe', 'electronique'),
    'à quoi sert le poignet ?': ('expliquer', 'piece', 'servo_poignet'),
    'cache les doigts': ('masquer', 'groupe', 'pince'),
    'montre le doigt gauche': ('montrer', 'piece', 'doigt_gauche'),   # the longest word wins
}


class SceneOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = lab.load_scene('bras_robotique')

    def test_table_of_the_contract(self):
        for action, phrases in TABLE.items():
            for text in phrases:
                self.assertEqual(extract_scene_order(text, self.scene), {'action': action}, text)
        for text, (action, kind, item) in TARGETED.items():
            self.assertEqual(extract_scene_order(text, self.scene), {'action': action, kind: item}, text)

    def test_this_piece_is_the_selected_one(self):
        for text in ('agrandis cette pièce', 'agrandis celle-ci', 'zoome sur ça'):
            order = extract_scene_order(text, self.scene, 'servo_coude')
            self.assertEqual(order, {'action': 'agrandir' if 'agrandis' in text else 'montrer',
                                     'piece': 'servo_coude'}, text)
        self.assertEqual(extract_scene_order('explique-moi cette pièce', self.scene, 'socle'),
                         {'action': 'expliquer', 'piece': 'socle'})
        self.assertEqual(extract_scene_order('agrandis cette pièce', self.scene),
                         {'action': 'agrandir', 'sans_selection': True})
        self.assertEqual(extract_scene_order('agrandis cette pièce', self.scene, 'piece_absente'),
                         {'action': 'agrandir', 'sans_selection': True})

    def test_unknown_piece_and_other_requests(self):
        self.assertEqual(extract_scene_order('retire le boîtier', self.scene), {'action': 'masquer', 'inconnu': 'boîtier'})
        for text in ('explique-moi comment fonctionne un moteur', 'explique-moi la relativité', 'ouvre la mémoire',
                     'quelle heure est-il ?', 'montre-moi un bras robotique', 'montre-moi un moteur électrique en 3D'):
            self.assertIsNone(extract_scene_order(text, self.scene), text)
        self.assertIsNone(extract_scene_order('démonte-le', None))

    def test_several_commands_in_one_sentence(self):
        order = extract_scene_order('Aurora, retire le boîtier et montre les servomoteurs', self.scene)
        self.assertEqual(order, {'action': 'masquer', 'inconnu': 'boîtier',
                                 'suite': [{'action': 'montrer', 'groupe': 'servomoteurs'}]})
        self.assertEqual(extract_scene_order('démonte-le, puis fais tourner la tourelle', self.scene),
                         {'action': 'eclater', 'suite': [{'action': 'animer', 'piece': 'plateau'}]})
        # A clause naming only a part takes the verb before it.
        self.assertEqual(extract_scene_order('cache la pince et la carte', self.scene),
                         {'action': 'masquer', 'groupe': 'pince', 'suite': [{'action': 'masquer', 'piece': 'carte'}]})
        # One clause is not a command on the scene: the sentence stays one order, as before.
        self.assertEqual(extract_scene_order('démonte-le et explique-moi la relativité', self.scene),
                         {'action': 'eclater'})
        self.assertEqual(extract_scene_order('montre, s’il te plaît, les servomoteurs', self.scene),
                         {'action': 'montrer', 'groupe': 'servomoteurs'})
        self.assertIsNone(extract_scene_order('montre-moi un moteur électrique en 3D et démonte-le', self.scene))


class AnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = base.run_tool_sync('lab.scene', {'id': 'bras_robotique'}).to_dict()

    def view(self, **order):
        return compose.scene_view({'scene': 'bras_robotique', **order}, self.result)

    def test_every_answer_names_the_illustration(self):
        for action in ('ouvrir', 'eclater', 'assembler', 'animer', 'arreter', 'recentrer', 'tout_montrer', 'expliquer',
                       'interieur'):
            answer, ui = self.view(action=action)
            self.assertIn('illustration pédagogique', answer.lower(), action)
            self.assertEqual(ui, {'navigate': 'home', 'scene': {'scene': 'bras_robotique', 'action': action}})
        answer, _ = self.view(action='animer')
        self.assertIn('pas une simulation', answer)               # cahier § 8: never presented as a simulation

    def test_opening_and_explanations(self):
        answer, _ = self.view(action='ouvrir')
        self.assertIn(good()['resume'], answer)
        self.assertIn(good()['avertissement'], answer)
        self.assertIn('« démonte-le », « montre les servomoteurs », « explique son fonctionnement »', answer)
        answer, ui = self.view(action='expliquer', piece='servo_poignet')
        self.assertIn(piece(good(), 'servo_poignet')['fonction'], answer)
        self.assertIn('Couple typique : ≈ 3 kg·cm', answer)
        self.assertEqual(ui['scene']['piece'], 'servo_poignet')
        answer, _ = self.view(action='expliquer')
        self.assertIn(good()['fonctionnement'], answer)
        answer, _ = self.view(action='expliquer', groupe='electronique')
        self.assertIn('Microcontrôleur :', answer)

    def test_nothing_changes_without_a_target(self):
        answer, ui = self.view(action='masquer', inconnu='boîtier')
        self.assertIsNone(ui)
        self.assertIn('Je ne trouve pas « boîtier »', answer)
        self.assertIn('Microcontrôleur', answer)
        answer, ui = self.view(action='agrandir', sans_selection=True)
        self.assertIsNone(ui)
        self.assertIn('Aucune pièce n’est sélectionnée', answer)
        answer, ui = self.view(action='animer', piece='socle')
        self.assertIsNone(ui)
        self.assertIn('fixe', answer)
        answer, ui = compose.scene_view({'scene': 'moteur', 'action': 'ouvrir'},
                                        base.run_tool_sync('lab.scene', {'id': 'moteur'}).to_dict())
        self.assertIsNone(ui)
        self.assertIn('inconnue', answer)

    def test_several_commands_answered_in_order(self):
        answer, ui = self.view(action='eclater', suite=[{'action': 'masquer', 'inconnu': 'boîtier'},
                                                        {'action': 'montrer', 'groupe': 'servomoteurs'}])
        self.assertTrue(answer.startswith('Je démonte « Bras robotique à 4 axes » en vue éclatée'), answer)
        self.assertIn('Je ne trouve pas « boîtier »', answer)
        self.assertTrue(answer.endswith('Je mets en évidence « Servomoteurs ».'), answer)
        self.assertEqual(answer.count('(illustration pédagogique)'), 1)   # said once
        self.assertEqual(ui, {'navigate': 'home', 'scene': {'scene': 'bras_robotique', 'action': 'eclater',
                                                             'suite': [{'action': 'montrer', 'groupe': 'servomoteurs'}]}})
        answer, ui = self.view(action='masquer', inconnu='boîtier', suite=[{'action': 'montrer', 'groupe': 'pince'}])
        self.assertEqual(ui['scene'], {'scene': 'bras_robotique', 'action': 'montrer', 'groupe': 'pince'})
        answer, ui = self.view(action='masquer', inconnu='boîtier', suite=[{'action': 'agrandir', 'inconnu': 'x'}])
        self.assertIsNone(ui)

    def test_absent_subject(self):
        catalogue = base.run_tool_sync('lab.catalogue').to_dict()
        answer = compose.scene_absent('un moteur électrique', catalogue, raison='Ollama ne répond pas.')
        self.assertIn('Je n’ai pas pu construire de scène 3D pour « un moteur électrique » : Ollama ne répond pas. '
                      'Aucune scène ne s’ouvre.', answer)
        self.assertIn('« Bras robotique à 4 axes » (illustration pédagogique)', answer)
        self.assertNotIn('pas encore faite', answer)   # phase 4: the creation on request exists
        nothing = compose.scene_absent('', catalogue)
        self.assertTrue(nothing.startswith('Quel objet voulez-vous voir en 3D ?'), nothing)


class GenerationTests(unittest.TestCase):
    """Phase 4: a description written by a model, repaired, checked and kept by tools/lab.build_generated."""

    def setUp(self):
        self.env = TempEnv().__enter__()   # the generated scenes go to a temporary AURORA_HOME
        lab.clear_cache()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def build(self, data, sujet='un moteur électrique'):
        text = data if isinstance(data, str) else '```json\n' + json.dumps(data, ensure_ascii=False) + '\n```'
        return lab.build_generated(text, sujet, 'l’IA locale (qwen3.5:9b)')

    def test_a_description_is_repaired_checked_and_kept(self):
        scene, why = self.build(motor())
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id='gen_moteur_electrique'))
        self.assertEqual((scene['id'], scene['nature'], scene['origine']),
                         ('gen_moteur_electrique', 'illustration', 'genere'))
        saved = lab.generated_dir() / 'gen_moteur_electrique.json'
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8')), scene)
        self.assertTrue(str(saved).startswith(str(self.env.dir)))      # never the user's ~/.aurora
        self.assertIn('Objet généré automatiquement par l’IA locale (qwen3.5:9b)', scene['avertissement'])
        self.assertNotIn('<', scene['fonctionnement'])
        self.assertNotIn('https', scene['fonctionnement'])
        # One word alone (« moteur ») would open this scene for any request: only the names of the object.
        self.assertEqual(scene['mots'], ['moteur electrique', 'moteur a courant continu'])
        ids = [p['id'] for p in scene['pieces']]
        self.assertLess(ids.index('rotor'), ids.index('arbre'))         # a parent before its child
        pieces = {p['id']: p for p in scene['pieces']}
        self.assertEqual(pieces['arbre']['parent'], 'rotor')
        self.assertIsNone(pieces['aimant']['parent'])                    # unknown parent
        self.assertEqual((pieces['aimant']['nom'], pieces['aimant_2']['nom']), ('Aimant 1', 'Aimant 2'))
        self.assertEqual((pieces['bobine_a']['nom'], pieces['bobine_b']['nom']), ('Bobine 1', 'Bobine 2'))
        groups = {g['id']: g for g in scene['groupes']}
        self.assertEqual(sorted(groups), ['aimants', 'bobines'])         # « Aimants » made; the empty one dropped
        self.assertEqual((pieces['aimant']['groupe'], pieces['aimant_2']['groupe']), ('aimants', 'aimants'))
        self.assertIn(pieces['aimant']['couleur'], lab.COULEURS)
        self.assertEqual(pieces['levier']['forme']['type'], 'boite')     # unknown shape
        self.assertEqual(pieces['rotor']['articulation']['animation']['continu'], True)
        self.assertEqual(pieces['levier']['articulation']['axe'], 'y')   # unknown axis
        self.assertEqual(pieces['levier']['articulation']['max'], 30.0)
        self.assertNotIn('bobine', pieces['bobine_a']['mots'])           # « les bobines » names the group
        words = [w for p in scene['pieces'] for w in p['mots']]
        self.assertEqual(len(words), len(set(words)))
        # Scaled to the socle: within [-1 ; 1] in x and z, on y = 0, whatever the model's units.
        for p in scene['pieces']:
            for dim in ('l', 'h', 'p', 'rayon', 'hauteur'):
                self.assertLessEqual(p['forme'].get(dim, 0), 2.0 + 1e-6, p['id'])
        self.assertTrue(any(p.get('eclate') for p in scene['pieces']))  # the exploded view, computed
        self.assertEqual(lab.catalogue()[-1]['origine'], 'genere')
        self.assertEqual(lab.kind_label(lab.load_scene('gen_moteur_electrique')),
                         'illustration pédagogique générée automatiquement')
        # The commands of the scene shown find its pieces and groups by these words.
        self.assertEqual(scene_subject('montre-moi un moteur électrique'), 'gen_moteur_electrique')
        self.assertEqual(extract_scene_order('montre les bobines', scene), {'action': 'montrer', 'groupe': 'bobines'})
        self.assertEqual(extract_scene_order('retire le boîtier', scene), {'action': 'masquer', 'piece': 'boitier'})
        self.assertEqual(extract_scene_order('fais tourner le rotor', scene), {'action': 'animer', 'piece': 'rotor'})
        self.assertEqual(extract_scene_order('Retire le boîtier et montre les bobines', scene),   # cahier § 4
                         {'action': 'masquer', 'piece': 'boitier', 'suite': [{'action': 'montrer', 'groupe': 'bobines'}]})

    def test_a_loop_of_parents_is_broken(self):
        data = motor()
        data['pieces'][0]['parent'] = 'levier'
        data['pieces'][-1]['parent'] = 'boitier'
        scene, why = self.build(data)
        self.assertIsNone(why)
        pieces = {p['id']: p for p in scene['pieces']}
        self.assertTrue(pieces['boitier']['parent'] is None or pieces['levier']['parent'] is None)

    def test_a_piece_carried_by_a_turning_one_does_not_turn_twice(self):
        data = motor()
        data['pieces'][1]['mouvement'] = 'tourne'                       # the shaft, carried by the rotor
        data['pieces'][3]['mouvement'] = 'oscille'                      # a coil on the rotor: its own swing stays
        fan = dict(data['pieces'][1], id='ventilateur', nom='Ventilateur', parent='arbre', mots=['ventilateur'])
        data['pieces'].append(fan)                                      # carried by the shaft, two levels down
        scene, why = self.build(data)
        self.assertIsNone(why)
        pieces = {p['id']: p for p in scene['pieces']}
        self.assertTrue(pieces['rotor']['articulation']['animation']['continu'])
        self.assertNotIn('articulation', pieces['arbre'])
        self.assertNotIn('articulation', pieces['ventilateur'])
        self.assertEqual(pieces['bobine_a']['articulation']['max'], 30.0)

    def shapes(self, *parts, sujet='un objet d’essai'):
        """A description of these pieces only -> {id: piece of the scene built}."""
        def part(pid, forme, taille, position, **more):
            return {'id': pid, 'nom': pid.capitalize(), 'groupe': '', 'parent': '', 'forme': forme, 'taille': taille,
                    'axe': 'y', 'position': position, 'copies': 1, 'couleur': 'cyan', 'mouvement': 'aucun',
                    'fonction': 'Rôle.', 'mots': [], **more}
        scene, why = self.build({'titre': 'Objet d’essai', 'domaine': 'mecanique', 'resume': 'Un objet.',
                                 'fonctionnement': 'Il existe.', 'mots': [], 'plan': 'Des pièces posées.',
                                 'groupes': [], 'pieces': [part(*p[:4], **(p[4] if len(p) > 4 else {})) for p in parts]},
                                sujet=sujet)
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id=scene['id']))
        return {p['id']: p for p in scene['pieces']}

    def test_the_size_is_the_envelope_whatever_the_shape(self):
        # 10/11/2026: « taille » is [x, y, z] for every shape; the axis of a round piece is read from it, so a mast
        # stands and a wheel faces us whatever « axe » says (a model copied the axis of the example on every piece).
        pieces = self.shapes(
            ('mat', 'cylindre', [0.1, 1.5, 0.1], [0, 0.75, 0], {'axe': 'z'}),
            ('roue', 'cylindre', [0.6, 0.6, 0.08], [0.6, 0.3, 0], {'axe': 'x', 'mouvement': 'tourne'}),
            ('essieu', 'cylindre', [1.2, 0.05, 0.05], [0, 0.3, 0], {'axe': 'y', 'mouvement': 'tourne'}),
            ('boule', 'sphere', [0.3, 0.3, 0.3], [0, 1.65, 0]),
            ('anneau', 'tore', [0.6, 0.6, 0.1], [-0.6, 0.3, 0]),
            ('toit', 'cone', [0.4, 0.5, 0.4], [0.6, 0.85, 0]),
            ('nez', 'cone', [0.5, 0.2, 0.2], [0, 1.2, 0]),
            ('socle', 'boite', [0.8, 0, 0.4], [0, 0.02, 0.5]),
            ('rond', 'cylindre', [0.3, 0.3, 0.3], [0, 0.15, -0.5], {'axe': 'x'}))
        shape = {pid: p['forme'] for pid, p in pieces.items()}
        self.assertEqual((shape['mat']['axe'], shape['roue']['axe'], shape['essieu']['axe']), ('y', 'z', 'x'))
        self.assertAlmostEqual(shape['mat']['hauteur'] / shape['mat']['rayon'], 30, delta=.3)      # 1.5 / 0.05
        self.assertAlmostEqual(shape['roue']['rayon'] / shape['roue']['hauteur'], 3.75, delta=.05)   # 0.3 / 0.08
        # A wheel turns around its own axis, whatever the model wrote; a rod keeps the axis it was given.
        self.assertEqual(pieces['roue']['articulation']['axe'], 'z')
        self.assertEqual(pieces['essieu']['articulation']['axe'], 'y')
        self.assertAlmostEqual(shape['boule']['rayon'] / shape['mat']['rayon'], 3, delta=.05)        # 0.15 / 0.05
        self.assertEqual(shape['anneau']['axe'], 'z')
        self.assertAlmostEqual((shape['anneau']['rayon'] + shape['anneau']['epaisseur']) / shape['mat']['rayon'], 6,
                               delta=.1)                                                           # 0.3 outside
        # A cone is a cylinder of the format with one end closed: the point up, or to the right when it lies.
        self.assertEqual((shape['toit']['type'], shape['toit']['axe']), ('cylindre', 'y'))
        self.assertLess(shape['toit']['rayon_haut'], shape['toit']['rayon'] / 5)
        self.assertEqual(shape['nez']['axe'], 'x')
        self.assertLess(shape['nez']['rayon'], shape['nez']['rayon_haut'] / 5)
        self.assertAlmostEqual(shape['socle']['h'], shape['socle']['p'], delta=1e-3)   # 0: the smallest one given
        self.assertEqual(shape['rond']['axe'], 'x')                                    # all alike: the model's axis
        self.assertFalse(any('rotation' in p for p in pieces.values()))

    def test_the_example_of_the_instructions_is_built(self):
        # The example the model reads is a description like any other: copies in a circle, a mast that stands.
        scene, why = self.build(lab.GEN_EXAMPLE, sujet='un ventilateur de bureau')
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id='gen_ventilateur_de_bureau'))
        pieces = {p['id']: p for p in scene['pieces']}
        self.assertEqual(sorted(pieces), ['grille', 'moteur', 'moyeu', 'pale', 'pale_2', 'pale_3', 'socle', 'tige'])
        self.assertEqual([pieces[i]['nom'] for i in ('pale', 'pale_2', 'pale_3')], ['Pale 1', 'Pale 2', 'Pale 3'])
        self.assertEqual({pieces[i]['groupe'] for i in ('pale', 'pale_2', 'pale_3')}, {'pales'})
        self.assertEqual((pieces['tige']['forme']['axe'], pieces['moteur']['forme']['axe']), ('y', 'z'))
        self.assertEqual([pieces[i].get('rotation') for i in ('pale', 'pale_2', 'pale_3')],
                         [None, [0.0, 0.0, 120.0], [0.0, 0.0, -120.0]])
        arms = [pieces[i]['position'] for i in ('pale', 'pale_2', 'pale_3')]       # around the hub, their parent
        self.assertEqual({pieces[i]['parent'] for i in ('pale', 'pale_2', 'pale_3')}, {'moyeu'})
        for arm in arms:
            self.assertAlmostEqual(math.hypot(*arm), math.hypot(*arms[0]), delta=1e-3)
            self.assertAlmostEqual(arm[2], 0, delta=1e-6)
        self.assertAlmostEqual(sum(a[0] for a in arms), 0, delta=1e-3)
        self.assertAlmostEqual(sum(a[1] for a in arms), 0, delta=1e-3)
        self.assertTrue(pieces['moyeu']['articulation']['animation']['continu'])   # the hub turns and carries them
        self.assertFalse(any('articulation' in pieces[i] for i in ('pale', 'pale_2', 'pale_3')))
        self.assertEqual(extract_scene_order('montre les pales', scene), {'action': 'montrer', 'groupe': 'pales'})
        text = lab.generation_instructions()
        for p in lab.GEN_EXAMPLE['pieces']:
            self.assertIn(json.dumps(p, ensure_ascii=False), text)

    def test_the_vehicle_example_and_its_mirrored_pairs(self):
        # 11/11/2026 (ID Studio IA, devant un X-wing du modèle local : « rien n'est en place »): the second example the model
        # reads lies down and is symmetrical. « miroir » : a piece written once, on the right, and its twin placed.
        scene, why = self.build(lab.GEN_EXAMPLE_VEHICLE, sujet='un avion de tourisme')
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id='gen_avion_de_tourisme'))
        pieces = {p['id']: p for p in scene['pieces']}
        self.assertEqual(sorted(pieces), [
            'aile_cote_droit', 'aile_cote_gauche', 'cockpit', 'derive', 'fuselage', 'moyeu', 'pale', 'pale_2',
            'roue_cote_droit', 'roue_cote_gauche', 'stabilisateur_cote_droit', 'stabilisateur_cote_gauche'])
        self.assertEqual(pieces['aile_cote_gauche']['nom'], 'Aile côté gauche')
        for pair in ('aile', 'stabilisateur', 'roue'):
            right, left = pieces[f'{pair}_cote_droit'], pieces[f'{pair}_cote_gauche']
            self.assertGreater(right['position'][0], .05, pair)
            self.assertAlmostEqual(left['position'][0], -right['position'][0], delta=1e-3, msg=pair)
            self.assertEqual(left['position'][1:], right['position'][1:], pair)
            self.assertEqual((left['parent'], right['parent'], left['forme']), ('fuselage', 'fuselage', right['forme']))
            self.assertIsNotNone(right['groupe'])
            self.assertEqual(left['groupe'], right['groupe'])
        # The wings touch the sides of the fuselage; the wheels are under it, one on each side; the body lies.
        self.assertEqual(pieces['fuselage']['forme']['axe'], 'z')
        self.assertAlmostEqual(pieces['aile_cote_droit']['position'][0],
                               pieces['fuselage']['forme']['rayon'] + pieces['aile_cote_droit']['forme']['l'] / 2,
                               delta=1e-3)
        self.assertLess(pieces['roue_cote_droit']['position'][1], 0)
        self.assertEqual(extract_scene_order('montre les ailes', scene)['action'], 'montrer')
        text = lab.generation_instructions()
        for p in lab.GEN_EXAMPLE_VEHICLE['pieces']:
            self.assertIn(json.dumps(p, ensure_ascii=False), text)
        self.assertIn('miroir', lab.generation_schema()['properties']['pieces']['items']['required'])
        # A small model borrows the pieces of the example it reads: the lying example is the closest to the object
        # asked, a car for what rolls, a sailing boat for what floats, the plane for the rest.
        for example, sujet, said, ids in (
                (lab.GEN_EXAMPLE_CAR, 'une voiture de sport', '« une voiture »',
                 ['carrosserie', 'habitacle', 'pare_chocs', 'phare_cote_droit', 'phare_cote_gauche',
                  'roue_arriere_cote_droit', 'roue_arriere_cote_gauche', 'roue_avant_cote_droit',
                  'roue_avant_cote_gauche']),
                (lab.GEN_EXAMPLE_BOAT, 'un voilier', '« un voilier »',
                 ['coque', 'etrave', 'foc', 'gouvernail', 'grand_voile', 'mat', 'quille'])):
            built, why = self.build(example, sujet=sujet)
            self.assertIsNone(why, sujet)
            self.assertEqual(sorted(p['id'] for p in built['pieces']), ids)
            shown = lab.generation_instructions(sujet)
            self.assertIn(said, shown)
            self.assertNotIn('avion de tourisme', shown)
            for p in example['pieces']:
                self.assertIn(json.dumps(p, ensure_ascii=False), shown)
        self.assertIn('« un avion de tourisme »', lab.generation_instructions('un X-wing'))
        self.assertIn('« un avion de tourisme »', lab.generation_instructions(None))
        taxi, why = self.build(lab.GEN_EXAMPLE_CAR, sujet='un taxi')
        self.assertIsNone(why)
        car = {p['id']: p for p in taxi['pieces']}
        wheels = [car[f'roue_{end}_cote_{side}'] for end in ('avant', 'arriere') for side in ('droit', 'gauche')]
        self.assertEqual(len({(abs(w['position'][0]), abs(w['position'][2])) for w in wheels}), 1)   # four corners
        self.assertEqual({w['forme']['axe'] for w in wheels}, {'x'})                                  # they stand
        self.assertTrue(all(w['articulation']['animation']['continu'] for w in wheels))               # and they turn

    def test_a_pair_is_mirrored_never_spread_in_a_circle(self):
        pieces = self.shapes(
            ('corps', 'boite', [0.5, 0.4, 1.8], [0, 0.4, 0]),
            ('aile', 'boite', [0.9, 0.04, 0.4], [-0.7, 0.4, 0.1], {'parent': 'corps', 'pose': 'a_gauche',
                                                                  'miroir': True, 'copies': 2}),
            ('canon', 'cone', [0.5, 0.06, 0.06], [1.3, 0.4, 0.1], {'parent': 'aile', 'pose': 'a_droite',
                                                                 'miroir': True, 'axe': 'x'}),
            ('roue', 'cylindre', [0.06, 0.2, 0.2], [0, 0.1, 0.5], {'parent': 'corps', 'pose': 'sous', 'miroir': True}),
            ('derive', 'boite', [0.03, 0.3, 0.2], [0.2, 0.75, -0.8], {'nom': 'Dérive gauche', 'parent': 'corps',
                                                                    'pose': 'sur', 'copies': 2}),
            ('phare_d', 'sphere', [0.08, 0.08, 0.08], [-0.2, 0.4, 0.93], {'nom': 'Phare droit', 'parent': 'corps',
                                                                        'pose': 'devant'}),
            ('phare_g', 'sphere', [0.08, 0.08, 0.08], [-0.2, 0.4, 0.93], {'nom': 'Phare gauche', 'parent': 'corps',
                                                                        'pose': 'devant', 'miroir': True}),
            ('patin', 'boite', [0.05, 0.05, 0.6], [0.1, 0.02, 0], {'parent': 'corps', 'pose': 'sous', 'copies': 4,
                                                                 'axe': 'z'}))
        self.assertEqual(sorted(pieces), [
            'aile_cote_droit', 'aile_cote_gauche', 'canon_cote_droit', 'canon_cote_gauche', 'corps', 'derive',
            'patin_1', 'patin_2', 'patin_3', 'patin_4', 'phare_droit', 'phare_gauche', 'roue_cote_droit',
            'roue_cote_gauche'])
        # Both sides written by the model, one of them « miroir » : one pair as written, each on its own side.
        self.assertGreater(pieces['phare_droit']['position'][0], 0)
        self.assertAlmostEqual(pieces['phare_gauche']['position'][0], -pieces['phare_droit']['position'][0],
                               delta=1e-3)
        # Four « copies » under a body, around a lying axis: two pairs at its four corners, never a circle through it.
        corners = sorted((round(pieces[f'patin_{n}']['position'][0], 3), round(pieces[f'patin_{n}']['position'][2], 3))
                         for n in (1, 2, 3, 4))
        self.assertEqual(corners, [(-corners[3][0], -corners[3][1]), (-corners[3][0], corners[3][1]),
                                   (corners[3][0], -corners[3][1]), corners[3]])
        self.assertTrue(corners[3][0] > .02 and corners[3][1] > .1)
        self.assertFalse(any('rotation' in pieces[f'patin_{n}'] for n in (1, 2, 3, 4)))
        # Written on the left, with « copies » : still one pair, the right one on the right.
        self.assertGreater(pieces['aile_cote_droit']['position'][0], 0)
        self.assertNotIn('rotation', pieces['aile_cote_droit'])
        self.assertAlmostEqual(pieces['aile_cote_gauche']['position'][0], -pieces['aile_cote_droit']['position'][0],
                               delta=1e-3)
        # The twin of a piece carried by a mirrored one is carried by its twin, on its outer side, its point outwards.
        self.assertEqual((pieces['canon_cote_droit']['parent'], pieces['canon_cote_gauche']['parent']),
                         ('aile_cote_droit', 'aile_cote_gauche'))
        self.assertGreater(pieces['canon_cote_droit']['position'][0], 0)
        self.assertAlmostEqual(pieces['canon_cote_gauche']['position'][0], -pieces['canon_cote_droit']['position'][0],
                               delta=1e-3)
        right, left = pieces['canon_cote_droit']['forme'], pieces['canon_cote_gauche']['forme']
        self.assertEqual((left['rayon'], left['rayon_haut']), (right['rayon_haut'], right['rayon']))
        # Two wheels written in the middle, under the body: one on each side of it.
        self.assertGreater(pieces['roue_cote_droit']['position'][0], .05)
        self.assertAlmostEqual(pieces['roue_cote_gauche']['position'][0], -pieces['roue_cote_droit']['position'][0],
                               delta=1e-3)
        # A piece whose name says its side is one piece, whatever « copies » says.
        self.assertEqual(pieces['derive']['nom'], 'Dérive gauche')
        self.assertNotIn('rotation', pieces['derive'])

    def test_a_piece_is_pushed_against_the_face_of_its_parent(self):
        # « pose » : the model says well what stands on what and adds heights badly. Each piece is put against the
        # face of its parent it touches, and kept in front of that face; « dedans » and no parent: as written.
        def places(data, sujet):
            scene, why = self.build(data, sujet=sujet)
            self.assertIsNone(why)
            return {p['id']: p['position'] for p in scene['pieces']}
        right = places(lab.GEN_EXAMPLE, 'un ventilateur de bureau')
        wrong = copy.deepcopy(lab.GEN_EXAMPLE)
        for p in wrong['pieces']:
            if p['id'] in ('tige', 'moteur'):
                p['position'][1] += 3                                    # a stem and a motor floating far above
        self.assertEqual(places(wrong, 'un ventilateur sur pied'), right)
        for p in wrong['pieces']:
            p['pose'] = 'dedans'
        self.assertNotEqual(places(wrong, 'un ventilateur de table'), right)
        aside = copy.deepcopy(lab.GEN_EXAMPLE)
        next(p for p in aside['pieces'] if p['id'] == 'moyeu')['position'][0] += 2   # a hub two metres aside
        moved = places(aside, 'un ventilateur de plafond')
        self.assertLess(abs(moved['moyeu'][0]), .25)                     # back against the motor, at its edge
        self.assertEqual(moved['moyeu'][2], right['moyeu'][2])
        # Moved the least: a post behind a seat still rises above it, a back on one post stays centred.
        chair = self.shapes(
            ('assise', 'boite', [0.5, 0.05, 0.5], [0, 0.525, 0]),
            ('montant', 'cylindre', [0.04, 0.5, 0.04], [-0.22, 1.0, -0.2], {'parent': 'assise', 'pose': 'derriere'}),
            ('dossier', 'boite', [0.44, 0.5, 0.04], [0, 1.275, -0.2], {'parent': 'montant', 'pose': 'sur'}),
            sujet='une chaise d’essai')
        seat, post, back = chair['assise'], chair['montant'], chair['dossier']
        self.assertAlmostEqual(post['position'][1] - post['forme']['hauteur'] / 2, seat['forme']['h'] / 2, delta=2e-3)
        self.assertAlmostEqual(post['position'][2], -(seat['forme']['p'] / 2 + post['forme']['rayon']), delta=2e-3)
        self.assertAlmostEqual(post['position'][0] + back['position'][0], 0, delta=2e-3)   # centred on the seat
        for p in aside['pieces']:
            p['parent'] = ''                                             # without a parent, a pose changes nothing
        alone = places(aside, 'un ventilateur de poche')
        self.assertGreater(alone['moyeu'][0], .5)

    def test_nothing_floats_by_mistake(self):
        # A table as the local model wrote it (10/11/2026): no parent anywhere, the top far above its legs. A piece
        # that touches nothing and hangs above others is set down on them, with all it carries; with nothing beneath
        # (a moon beside the table), it stays where it was written.
        pieces = self.shapes(
            ('plateau', 'boite', [1.0, 0.05, 0.4], [0, 0.98, 0]),
            ('pied_a', 'cylindre', [0.15, 0.35, 0.15], [-0.45, 0.175, 0.1]),
            ('pied_b', 'cylindre', [0.15, 0.35, 0.15], [0.45, 0.175, 0.1]),
            ('vase', 'cylindre', [0.1, 0.2, 0.1], [0, 1.6, 0]),
            ('fleur', 'sphere', [0.08, 0.08, 0.08], [0, 1.74, 0], {'parent': 'vase', 'pose': 'sur'}),
            ('lune', 'sphere', [0.1, 0.1, 0.1], [2.0, 1.2, 0]))
        top, leg, vase, moon = pieces['plateau'], pieces['pied_a'], pieces['vase'], pieces['lune']
        self.assertAlmostEqual(leg['position'][1], leg['forme']['hauteur'] / 2, delta=2e-3)        # on the floor
        table = top['position'][1] + top['forme']['h'] / 2
        self.assertAlmostEqual(top['position'][1] - top['forme']['h'] / 2, leg['forme']['hauteur'], delta=2e-3)
        self.assertAlmostEqual(vase['position'][1] - vase['forme']['hauteur'] / 2, table, delta=2e-3)
        self.assertAlmostEqual(pieces['fleur']['position'][1],                                    # carried along
                               vase['forme']['hauteur'] / 2 + pieces['fleur']['forme']['rayon'], delta=2e-3)
        self.assertGreater(moon['position'][1] - moon['forme']['rayon'],
                           vase['position'][1] + vase['forme']['hauteur'])                        # still up there
        # A bar stool as the model wrote it: the seat between the tops of its posts, 3 cm from them. It is held:
        # it stays there (it was taken for a floating piece and set down on the base, one metre below).
        stool = self.shapes(
            ('base', 'cylindre', [0.6, 0.03, 0.6], [0, 0.02, 0]),
            ('pied_a', 'cylindre', [0.1, 1.1, 0.1], [-0.32, 0.585, 0], {'parent': 'base', 'pose': 'sur'}),
            ('pied_b', 'cylindre', [0.1, 1.1, 0.1], [0.32, 0.585, 0], {'parent': 'base', 'pose': 'sur'}),
            ('siege', 'cylindre', [0.5, 0.12, 0.5], [0, 1.12, 0], {'pose': 'libre'}),
            sujet='un tabouret d’essai')
        self.assertGreater(stool['siege']['position'][1], 5 * stool['base']['position'][1])
        self.assertAlmostEqual(stool['siege']['position'][1] + stool['siege']['forme']['hauteur'] / 2,
                               stool['base']['forme']['hauteur'] + stool['pied_a']['forme']['hauteur'], delta=.15)

    def test_copies_in_a_circle(self):
        # Legs around the vertical axis of the seat: round pieces are placed, not turned. Blades that turn without
        # a hub turn together around the axis of their parent: one pivot on it, the mesh held away (« decalage »).
        pieces = self.shapes(
            ('plateau', 'boite', [0.8, 0.04, 0.8], [0, 0.6, 0]),
            ('pied', 'cylindre', [0.06, 0.58, 0.06], [0.3, 0.29, 0.3], {'parent': 'plateau', 'copies': 4}),
            ('pale', 'boite', [0.1, 0.4, 0.02], [0.3, 0.72, 0], {'parent': 'plateau', 'copies': 3, 'axe': 'y',
                                                               'mouvement': 'tourne'}),
            ('trop', 'sphere', [0.02, 0.02, 0.02], [0, 0.63, 0], {'copies': 400}))
        legs = [pieces[i] for i in ('pied', 'pied_2', 'pied_3', 'pied_4')]
        self.assertEqual([p['nom'] for p in legs], ['Pied 1', 'Pied 2', 'Pied 3', 'Pied 4'])
        self.assertFalse(any('rotation' in p for p in legs))
        corners = sorted((round(p['position'][0], 2), round(p['position'][2], 2)) for p in legs)
        side = abs(corners[0][0])
        self.assertEqual(corners, [(-side, -side), (-side, side), (side, -side), (side, side)])
        self.assertEqual(len({round(p['position'][1], 4) for p in legs}), 1)
        blades = [pieces[i] for i in ('pale', 'pale_2', 'pale_3')]
        self.assertEqual([p.get('rotation') for p in blades], [None, [0.0, 120.0, 0.0], [0.0, -120.0, 0.0]])
        self.assertEqual(len({tuple(p['position']) for p in blades}), 1)             # one pivot, on the axis
        self.assertEqual((blades[0]['position'][0], blades[0]['position'][2]), (0.0, 0.0))
        self.assertEqual(len({tuple(p['forme']['decalage']) for p in blades}), 1)   # each in its own turned frame
        self.assertGreater(blades[0]['forme']['decalage'][0], .1)
        self.assertEqual({p['articulation']['axe'] for p in blades}, {'y'})
        self.assertEqual({p['articulation']['animation']['phase'] for p in blades}, {0.0})
        self.assertEqual(sum(1 for pid in pieces if pid.startswith('trop')), lab.GEN_MAX_COPIES)

    def test_unusable_answers_are_said(self):
        self.assertEqual(self.build('Je ne peux pas.'),
                         (None, 'la réponse du modèle n’est pas une description JSON lisible'))
        one = motor()
        one['pieces'] = one['pieces'][:1]
        self.assertEqual(self.build(one), (None, 'la description du modèle a moins de 2 pièces'))
        self.build(motor())
        self.assertEqual(self.build(motor()), (None, 'une scène 3D porte déjà le nom « moteur électrique »'))
        self.assertEqual(list(lab.generated_dir().glob('*.json')), [lab.generated_dir() / 'gen_moteur_electrique.json'])

    def test_never_overwritten_and_bounded(self):
        lab.generated_dir().mkdir(parents=True)
        taken = lab.generated_dir() / 'gen_moteur_electrique.json'
        taken.write_text('{"illisible": true}', encoding='utf-8')
        with self.assertLogs('aurora.tools.lab', 'WARNING'):
            scene, why = self.build(motor())
        self.assertIsNone(why)
        self.assertEqual(scene['id'], 'gen_moteur_electrique_2')
        self.assertEqual(taken.read_text(encoding='utf-8'), '{"illisible": true}')
        with mock.patch.object(lab, 'MAX_GENERATED', 2):
            scene, why = self.build(motor(), sujet='une éolienne')
        self.assertIsNone(scene)
        self.assertIn('AURORA n’en efface aucune', why)

    # ----- 12/11/2026 (ID Studio IA : « un vrai générateur d'hologramme comme tu fais toi ») : the inventory, the control of
    # the code and the correction of the model.

    def review(self, data: dict, sujet: str) -> list[str]:
        """What the control says of a description, built without keeping anything."""
        notes: list[str] = []
        scene, why = lab.build_generated(json.dumps(data, ensure_ascii=False), sujet, 'un test', keep=False,
                                         remarks=notes)
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id=scene['id']))
        self.assertEqual(list(lab.generated_dir().glob('*.json')), [])      # nothing was written
        return notes

    def test_the_inventory_is_written_before_the_pieces(self):
        schema = lab.generation_schema()
        names = list(schema['properties'])
        self.assertLess(names.index('plan'), names.index('inventaire'))
        self.assertLess(names.index('inventaire'), names.index('pieces'))
        self.assertIn('inventaire', schema['required'])
        self.assertIn('- inventaire :', lab.generation_instructions('une voiture'))
        for example in (lab.GEN_EXAMPLE, lab.GEN_EXAMPLE_VEHICLE, lab.GEN_EXAMPLE_CAR, lab.GEN_EXAMPLE_BOAT):
            self.assertEqual(len(example['inventaire']), len(example['pieces']), example['titre'])

    def test_the_control_has_nothing_to_say_of_the_examples(self):
        for example, sujet in ((lab.GEN_EXAMPLE, 'un ventilateur de bureau'), (lab.GEN_EXAMPLE_CAR, 'une voiture'),
                               (lab.GEN_EXAMPLE_VEHICLE, 'un avion de tourisme'), (lab.GEN_EXAMPLE_BOAT, 'un voilier')):
            self.assertEqual(self.review(example, sujet), [], sujet)

    def test_the_control_says_what_the_repairs_cannot_do(self):
        def car(**changes) -> dict:
            data = copy.deepcopy(lab.GEN_EXAMPLE_CAR)
            for piece_id, change in changes.items():
                piece(data, piece_id).update(change)
            return data

        def extra(pid, nom, parent, pose, taille, position) -> dict:
            return {**copy.deepcopy(lab.GEN_EXAMPLE_CAR['pieces'][0]), 'id': pid, 'nom': nom, 'parent': parent,
                    'pose': pose, 'taille': taille, 'position': position, 'mots': []}
        # A vehicle lies: its body is long from back to front.
        notes = self.review(car(carrosserie={'taille': [0.7, 1.8, 0.3]}), 'une voiture')
        self.assertIn('« Carrosserie », la plus grande pièce, est plus haute que longue', notes[0])
        self.assertEqual(self.review(car(carrosserie={'taille': [0.7, 1.8, 0.3]}), 'une armoire'), [])   # no vehicle
        # A vehicle only when the name of the object begins with one: « une hélice d’avion » is a propeller, and its
        # hub is not told to lie down like a fuselage (review of 12/11/2026).
        for sujet in ('une hélice d’avion', 'un phare de voiture', 'un mât de voilier'):
            self.assertEqual(self.review(lab.GEN_EXAMPLE, sujet), [], sujet)
        # The body is the largest piece nothing carries: a sail larger than the hull is not told to lie down.
        boat = copy.deepcopy(lab.GEN_EXAMPLE_BOAT)
        piece(boat, 'grand_voile')['taille'] = [0.3, 1.3, 0.7]
        self.assertEqual(self.review(boat, 'un voilier'), [])
        # The inventory: a part announced and not placed, a number that is not reached.
        data = car()
        data['inventaire'] += ['1 volant']
        self.assertEqual(self.review(data, 'une voiture'),
                         ['L’inventaire annonce « 1 volant », mais aucune pièce ne représente cette partie : '
                          'ajoute-la.'])
        data = car()
        data['inventaire'] = ['1 carrosserie', '4 roues']
        data['pieces'] = [p for p in data['pieces'] if p['id'] != 'roue_arriere']
        self.assertIn('L’inventaire annonce « 4 roues », mais la description n’en place que 2.',
                      self.review(data, 'une voiture')[0])
        # What rolls stands on its wheels.
        data = car()
        data['pieces'].append(extra('reservoir', 'Réservoir', 'carrosserie', 'sous', [0.3, 0.3, 0.5], [0, 0.05, 0]))
        notes = self.review(data, 'une voiture')
        self.assertEqual(len(notes), 1)
        self.assertIn('Les roues ne touchent pas le sol : « Réservoir » descend plus bas qu’elles.', notes[0])
        # A piece on one side only: the inventory says how many there are. Without it the control says nothing (a
        # steering wheel is on one side ; told to mirror it, a model wrote two — review of 12/11/2026).
        notes = self.review(car(phare={'miroir': False}), 'une voiture')
        self.assertEqual(len(notes), 1)
        self.assertIn('L’inventaire annonce « 2 phares », mais la description n’en place que 1.', notes[0])
        self.assertIn('« copies » sert seulement à des pièces réparties en cercle', notes[0])
        data = car()
        data['inventaire'].append('1 volant')
        data['pieces'].append(extra('volant', 'Volant', 'habitacle', 'libre', [0.1, 0.1, 0.03], [0.18, 0.63, 0.2]))
        piece(data, 'volant')['pose'] = 'dedans'
        self.assertEqual(self.review(data, 'une voiture'), [])
        # A piece nobody will see, unless it is said to be inside.
        data = car()
        data['pieces'].append(extra('moteur', 'Moteur', 'carrosserie', 'libre', [0.3, 0.15, 0.3], [0, 0.35, 0.5]))
        notes = self.review(data, 'une voiture')
        self.assertEqual(len(notes), 1)
        self.assertIn('« Moteur » est entièrement à l’intérieur de « Carrosserie »', notes[0])
        piece(data, 'moteur')['pose'] = 'dedans'
        self.assertEqual(self.review(data, 'une voiture'), [])
        # Two pieces in the same place.
        data = car()
        data['pieces'] += [extra('antenne', 'Antenne', 'habitacle', 'sur', [0.1, 0.1, 0.1], [0, 0.8, 0]),
                           extra('gyrophare', 'Gyrophare', 'habitacle', 'libre', [0.1, 0.1, 0.1], [0, 0.81, 0])]
        self.assertEqual(self.review(data, 'une voiture'),
                         ['« Antenne » et « Gyrophare » occupent la même place : écarte-les, ou retire l’une des '
                          'deux si c’est la même pièce écrite deux fois.'])
        # Two pieces side by side are two pieces, however small beside the whole object (review of 12/11/2026).
        piece(data, 'gyrophare').update(position=[0.12, 0.81, 0])
        self.assertEqual(self.review(data, 'une voiture'), [])
        piece(data, 'gyrophare').update(position=[0, 0.81, 0])
        # The same parent and the same pose: the code glues both against that face, and the remark says so (told
        # only to move them apart, a model moved the positions and kept the pose).
        piece(data, 'gyrophare').update(pose='sur', position=[0, 0.8, 0.1])
        piece(data, 'antenne').update(position=[0, 0.9, 0.1])
        notes = self.review(data, 'une voiture')
        self.assertEqual(len(notes), 1)
        self.assertIn('elles ont le même parent et la même pose ("sur"), et AURORA les colle toutes les deux contre '
                      'cette face', notes[0])
        # Copies written on their axis: all in the same place.
        data = copy.deepcopy(lab.GEN_EXAMPLE)
        piece(data, 'pale').update(pose='devant', position=[0, 1.18, 0.25])
        self.assertEqual(self.review(data, 'un ventilateur de bureau'),
                         ['Les copies de « Pale » sont toutes au même endroit : la position d’une copie est à '
                          'distance de l’axe, pas sur lui.'])
        # Never more than GEN_MAX_REMARKS.
        data = car(carrosserie={'taille': [0.7, 1.8, 0.3]}, phare={'miroir': False})
        data['inventaire'] += [f'1 {name}' for name in ('volant', 'coffre', 'capot', 'antenne', 'toit', 'essuie-glace')]
        self.assertEqual(len(self.review(data, 'une voiture')), lab.GEN_MAX_REMARKS)

    # ------------------------------------------------------------------ 12/11/2026: inclined pieces (« bouts »)

    @staticmethod
    def leaning(titre: str, *parts: dict) -> dict:
        """A description of these pieces only ; each one: what differs from a straight box written far away."""
        return {'titre': titre, 'domaine': 'mecanique', 'resume': 'Un objet.', 'fonctionnement': 'Il existe.',
                'mots': [], 'plan': 'Des pièces.', 'inventaire': [], 'groupes': [], 'pieces': [
                    {'nom': p['id'].capitalize(), 'groupe': '', 'parent': '', 'pose': 'libre', 'forme': 'boite',
                     'axe': 'y', 'position': [9, 9, 9], 'copies': 1, 'miroir': False, 'couleur': 'cyan',
                     'mouvement': 'aucun', 'fonction': 'Rôle.', 'mots': [], **p} for p in parts]}

    @staticmethod
    def where(scene: dict, piece_id: str) -> tuple[list[float], list[float], list[float]]:
        """(the centre of a piece in the object, the lower end of its length, the higher one), as the drawing of the
        server reads the scene — position, rotation and offset along the chain of its parents. Read back from the
        scene: nothing comes from the builder."""
        import numpy as np

        from aurora.tools import lab_image
        index = [p['id'] for p in scene['pieces']].index(piece_id)
        points = np.concatenate([p.reshape(-1, 3) for p, _surfaces in lab_image._solids(scene)[index][0]])
        centre = points.mean(axis=0)
        along = np.linalg.svd(points - centre)[2][0]            # the direction the piece is the longest in
        reach = (points - centre) @ along
        ends = sorted((centre + along * reach.min(), centre + along * reach.max()), key=lambda end: end[1])
        return tuple([round(float(v), 3) + 0.0 for v in point] for point in (centre, *ends))

    def assertPlace(self, found: list[float], wanted: list[float], delta: float = 2e-3) -> None:
        for a, b in zip(found, wanted):
            self.assertAlmostEqual(a, b, delta=delta, msg=f'{found} au lieu de {wanted}')

    def test_an_inclined_piece_is_written_by_its_two_ends(self):
        # ID Studio IA, 12/11/2026 : « Oui continue » (la flèche de la pelleteuse de l'essai était en équerre). The socle is 2
        # long, on the ground and centred: the object is neither scaled nor moved, the numbers stay as written.
        data = self.leaning(
            'Grue d’essai',
            {'id': 'socle', 'taille': [0.5, 0.1, 2.0], 'position': [0, 0.05, 0]},
            {'id': 'fleche', 'taille': [0.1, 0.06, 0], 'parent': 'socle', 'pose': 'sur', 'axe': 'x',
             'mouvement': 'oscille', 'bouts': [[0, 0.1, -0.5], [0, 0.9, 0.1]]},
            {'id': 'balancier', 'forme': 'cylindre', 'taille': [0.08, 0.08, 0], 'parent': 'fleche', 'pose': 'devant',
             'bouts': [[0, 0.9, 0.1], [0, 0.5, 0.4]]},
            {'id': 'godet', 'taille': [0.2, 0.2, 0.2], 'parent': 'balancier', 'pose': 'sous',
             'position': [0, 0.4, 0.45]},
            {'id': 'boule', 'forme': 'sphere', 'taille': [0.1, 0.1, 0.1], 'parent': 'socle',
             'bouts': [[0, 1.0, 0], [0, 1.2, 0]]},
            {'id': 'hauban', 'forme': 'cylindre', 'taille': [0.04, 0.04, 0], 'parent': 'socle', 'miroir': True,
             'bouts': [[0.1, 0.1, -0.8], [0.2, 0.9, -0.6]]})
        scene, why = self.build(data, sujet='une grue d’essai')
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id=scene['id']))
        pieces = {p['id']: p for p in scene['pieces']}
        # The boom: its length from its ends, its section from « taille », turned around x only. It swings around
        # its first end: its pivot is there, and its shape is held half a length away.
        self.assertEqual(pieces['fleche']['forme'],
                         {'type': 'boite', 'l': 0.1, 'h': 0.06, 'p': 1.0, 'decalage': [0.0, 0.0, 0.5]})
        self.assertEqual(pieces['fleche']['position'], [0.0, 0.05, -0.5])      # from the centre of the socle
        self.assertEqual(pieces['fleche']['rotation'], [-53.13, 0.0, 0.0])
        self.assertEqual(pieces['fleche']['articulation']['axe'], 'x')
        # What it carries is written in its frame: the next arm, and a straight piece that stays straight.
        self.assertEqual(pieces['balancier']['forme'], {'type': 'cylindre', 'rayon': 0.04, 'hauteur': 0.5, 'axe': 'z'})
        self.assertEqual(pieces['balancier']['rotation'], [106.26, 0.0, 0.0])
        self.assertEqual(pieces['godet']['rotation'], [-53.13, 0.0, 0.0])
        for pid, centre, low, high in (('fleche', [0, 0.5, -0.2], [0, 0.1, -0.5], [0, 0.9, 0.1]),
                                       ('balancier', [0, 0.7, 0.25], [0, 0.5, 0.4], [0, 0.9, 0.1]),
                                       ('hauban_cote_droit', [0.15, 0.5, -0.7], [0.1, 0.1, -0.8], [0.2, 0.9, -0.6]),
                                       ('hauban_cote_gauche', [-0.15, 0.5, -0.7], [-0.1, 0.1, -0.8], [-0.2, 0.9, -0.6])):
            for found, wanted in zip(self.where(scene, pid), (centre, low, high)):
                self.assertPlace(found, wanted)
        # « pose » pulls no inclined piece, nor a piece written in touch with the box of an inclined parent.
        self.assertPlace(self.where(scene, 'godet')[0], [0, 0.4, 0.45])
        # A ball has no inclination: it is at the middle of its ends.
        self.assertEqual(pieces['boule']['forme'], {'type': 'sphere', 'rayon': 0.05})
        self.assertNotIn('rotation', pieces['boule'])
        self.assertPlace(self.where(scene, 'boule')[0], [0, 1.1, 0])
        # Written far from an inclined parent, a straight piece is still pulled against its box.
        piece(data, 'godet')['position'] = [0, 1.8, -0.9]
        scene, why = self.build(data, sujet='une autre grue d’essai')
        self.assertIsNone(why)
        centre = self.where(scene, 'godet')[0]
        self.assertLess(centre[1], 0.5)                                         # under the arm, not above the object
        self.assertGreater(centre[2], -0.1)                                     # and brought back from the far end

    def test_inclined_copies_turn_with_their_two_ends(self):
        scene, why = self.build(self.leaning(
            'Trépied d’essai',
            {'id': 'tete', 'taille': [0.2, 0.1, 0.2], 'position': [0, 1.05, 0]},
            {'id': 'pied', 'forme': 'cylindre', 'taille': [0.05, 0.05, 0], 'copies': 3,
             'bouts': [[0, 1.0, 0], [0.5, 0, 0]]}), sujet='un trépied d’essai')
        self.assertIsNone(why)
        self.assertIsNone(lab.validate(scene, file_id=scene['id']))
        legs = [self.where(scene, pid) for pid in ('pied', 'pied_2', 'pied_3')]
        for _centre, foot, top in legs:
            self.assertPlace(top, legs[0][2], delta=5e-3)                       # the three legs meet under the head
            self.assertAlmostEqual(foot[1], legs[0][1][1], delta=5e-3)          # and stand at the same height
        spread = [math.dist(a[1], b[1]) for a, b in ((legs[0], legs[1]), (legs[1], legs[2]), (legs[2], legs[0]))]
        self.assertGreater(min(spread), 0.5)
        self.assertAlmostEqual(max(spread), min(spread), delta=5e-3)            # at the three corners of a triangle

    def test_the_control_and_the_inclined_pieces(self):
        self.assertEqual(self.review(lab.GEN_EXAMPLE_LEANING, 'une lampe d’architecte'), [])
        # An inclined piece stays where it is written: one whose two ends touch nothing is told.
        lamp = copy.deepcopy(lab.GEN_EXAMPLE_LEANING)
        piece(lamp, 'bras_haut')['bouts'] = [[0, 2.4, 1.6], [0, 2.9, 2.4]]
        notes = self.review(lamp, 'une lampe d’architecte')
        self.assertEqual([note.kind for note in notes], ['bout'])
        self.assertIn('« Bras du haut » est inclinée et aucun de ses deux bouts ne touche une autre pièce ni le sol',
                      notes[0])

        def bars(*more: dict) -> dict:
            return self.leaning(
                'Chevalet d’essai',
                {'id': 'socle', 'taille': [1.0, 0.1, 1.0], 'position': [0, 0.05, 0]},
                {'id': 'barre', 'taille': [0.06, 0.06, 0], 'parent': 'socle', 'bouts': [[0, 0.1, -0.3], [0, 0.9, 0.3]]},
                *more)
        # Two bars that cross in their middle have the same box and are two pieces ; a small piece inside that box,
        # which is mostly empty, is not hidden.
        crossed = {'id': 'croix', 'taille': [0.06, 0.06, 0], 'parent': 'socle', 'bouts': [[0, 0.1, 0.3], [0, 0.9, -0.3]]}
        light = {'id': 'voyant', 'taille': [0.05, 0.05, 0.05], 'parent': 'socle', 'position': [0, 0.8, -0.2]}
        self.assertEqual(self.review(bars(crossed, light), 'un chevalet d’essai'), [])
        # The same bar written twice is told, like two straight pieces in the same place.
        twice = {'id': 'copie', 'taille': [0.06, 0.06, 0], 'parent': 'socle', 'bouts': [[0, 0.1, -0.3], [0, 0.9, 0.31]]}
        notes = self.review(bars(twice), 'un chevalet d’essai')
        self.assertEqual([note.kind for note in notes], ['double'])
        self.assertIn('« Barre » et « Copie » occupent la même place', notes[0])

    def test_the_rule_of_the_two_ends_is_written_only_where_it_is_asked(self):
        plain, tilted = lab.generation_instructions('une grue'), lab.generation_instructions('une grue', tilted=True)
        self.assertNotIn('bouts', plain)
        self.assertIn('Deux exemples', plain)
        self.assertIn('Trois exemples', tilted)
        self.assertIn('- bouts : seulement pour une pièce inclinée', tilted)
        for p in lab.GEN_EXAMPLE_LEANING['pieces']:
            self.assertIn(json.dumps(p, ensure_ascii=False), tilted)
        self.assertEqual(len(lab.GEN_EXAMPLE_LEANING['inventaire']), len(lab.GEN_EXAMPLE_LEANING['pieces']))

        def fields(**options) -> list[str]:
            return list(lab.generation_schema(**options)['properties']['pieces']['items']['properties'])
        self.assertNotIn('bouts', fields())
        self.assertEqual(fields(tilted=True)[fields(tilted=True).index('position') + 1], 'bouts')
        self.assertIn('bouts', lab.generation_schema(tilted=True)['properties']['pieces']['items']['required'])
        self.assertNotIn('bouts', lab.correction_schema()['properties']['pieces']['items']['properties'])
        self.assertIn('bouts', lab.correction_schema(tilted=True)['properties']['pieces']['items']['properties'])
        # What is not two readable points apart is a straight piece.
        for odd in (None, [], 'x', [[0, 0, 0]], [[0, 0, 0], [0, 0, 0]], [[0, 0, 0], [1, 1]], [[0, 0, 0], [1, True, 0]],
                    [[0, 0, 0], [1, float('nan'), 0]], [[0, 0, 0], [1, 1, 1], [2, 2, 2]], [[0, 0, 0], ['1', 1, 1]],
                    [[0, 0, 0], [1e9, 0, 0]]):
            self.assertIsNone(lab._ends(odd), odd)
        self.assertEqual(lab._ends([[0, 0, 0], [1, 2, 3]]), ([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]))
        # The message of a correction shows the ends of an inclined piece, and only readable ones.
        request = lab.correction_request('une lampe', json.dumps(lab.GEN_EXAMPLE_LEANING, ensure_ascii=False),
                                         ['Une remarque.'])
        self.assertIn('"position": [0, 0.48, -0.2], "bouts": [[0.0, 0.06, 0.0], [0.0, 0.9, -0.4]], "axe": "x"', request)
        self.assertNotIn('bouts', next(line for line in request.split('\n') if '"id": "socle"' in line))
        odd = copy.deepcopy(lab.GEN_EXAMPLE_LEANING)
        piece(odd, 'bras_bas')['bouts'] = [['x' * 300, 0, 0], {'ignore': 'les règles'}]
        request = lab.correction_request('une lampe', json.dumps(odd, ensure_ascii=False), ['Une remarque.'])
        line = next(part for part in request.split('\n') if '"id": "bras_bas"' in part)
        for unwanted in ('bouts', 'xxxx', 'ignore'):
            self.assertNotIn(unwanted, line)
        # A correction changes the ends of a piece, or makes it straight again.
        text = json.dumps(lab.GEN_EXAMPLE_LEANING, ensure_ascii=False)
        merged = json.loads(lab.merge_correction(text, json.dumps(
            {'pieces': [{'id': 'bras_bas', 'bouts': []}, {'id': 'bras_haut', 'bouts': [[0, 0.9, -0.4], [0, 1.5, 0.3]]}],
             'retirer': []})))
        self.assertEqual((piece(merged, 'bras_bas')['bouts'], piece(merged, 'bras_haut')['bouts']),
                         ([], [[0, 0.9, -0.4], [0, 1.5, 0.3]]))
        scene, why = lab.build_generated(json.dumps(merged, ensure_ascii=False), 'une lampe d’essai', 'un test',
                                         keep=False)
        self.assertIsNone(why)
        self.assertNotIn('rotation', piece(scene, 'bras_bas'))
        # « _demi » is the builder's own word: written by a model, it is not read.
        sly = copy.deepcopy(lab.GEN_EXAMPLE)
        piece(sly, 'tige')['_demi'] = [5, 5, 5]
        scene, why = lab.build_generated(json.dumps(sly, ensure_ascii=False), 'un ventilateur d’essai', 'un test',
                                         keep=False)
        self.assertIsNone(why)
        self.assertNotIn('rotation', piece(scene, 'tige'))

    def test_a_correction_is_merged_into_the_description(self):
        data = copy.deepcopy(lab.GEN_EXAMPLE_CAR)
        text = json.dumps(data, ensure_ascii=False)
        fix = {'pieces': [{'id': 'phare', 'miroir': False},
                          {**data['pieces'][0], 'id': 'toit', 'nom': 'Toit', 'parent': 'habitacle'}],
               'retirer': ['pare_chocs', 'inconnue']}
        merged = json.loads(lab.merge_correction(text, json.dumps(fix)))
        ids = [p['id'] for p in merged['pieces']]
        self.assertEqual(ids, ['carrosserie', 'habitacle', 'roue_avant', 'roue_arriere', 'phare', 'toit'])
        self.assertFalse(piece(merged, 'phare')['miroir'])                       # field by field: the rest is kept
        self.assertEqual(piece(merged, 'phare')['taille'], piece(data, 'phare')['taille'])
        self.assertEqual({k: v for k, v in merged.items() if k != 'pieces'},
                         {k: v for k, v in data.items() if k != 'pieces'})
        for useless in ('pas du JSON', '{"pieces": [], "retirer": []}', '{"retirer": ["inconnue"]}',
                        json.dumps({'pieces': [{'id': 'phare', 'miroir': True}]}),     # already so
                        json.dumps({'retirer': [p['id'] for p in data['pieces']][1:]})):   # one piece would be left
            self.assertIsNone(lab.merge_correction(text, useless), useless)
        self.assertIsNone(lab.merge_correction('pas du JSON', json.dumps(fix)))
        # A piece under a new id is added, even with the name of another: the twin that was missing can be written.
        merged = json.loads(lab.merge_correction(text, json.dumps(
            {'pieces': [{**piece(data, 'phare'), 'id': 'phare_gauche'}], 'retirer': []})))
        self.assertEqual([p['id'] for p in merged['pieces']], [p['id'] for p in data['pieces']] + ['phare_gauche'])
        # An id two pieces share names neither: it is not changed, and « retirer » does not take both away.
        twice = copy.deepcopy(data)
        twice['pieces'].append({**piece(data, 'phare'), 'position': [-0.24, 0.38, 0.92]})
        shared = json.dumps(twice, ensure_ascii=False)
        self.assertIsNone(lab.merge_correction(shared, json.dumps({'pieces': [{'id': 'phare', 'miroir': False}],
                                                                    'retirer': ['phare']})))
        merged = json.loads(lab.merge_correction(shared, json.dumps({'pieces': [], 'retirer': ['phare', 'pare_chocs']})))
        self.assertEqual([p['id'] for p in merged['pieces']].count('phare'), 2)
        # The schema of a correction: the fields of a piece of a description, only its id required, and ids to take
        # away.
        schema = lab.correction_schema()
        self.assertEqual(schema['required'], ['pieces', 'retirer'])
        written, whole = schema['properties']['pieces']['items'], lab.generation_schema()['properties']['pieces']['items']
        self.assertEqual(written['properties'], whole['properties'])
        self.assertEqual((written['required'], len(whole['required'])), (['id'], len(whole['properties'])))
        # The message of the correction step: the description without its texts, the remarks, the form of the answer.
        request = lab.correction_request('une voiture', text, ['Première remarque.', 'Seconde remarque.'])
        self.assertIn('### Objet à construire en trois dimensions\nune voiture\n', request)
        self.assertIn('"id": "roue_avant", "nom": "Roue avant", "groupe": "", "parent": "carrosserie"', request)
        self.assertIn('"inventaire": ["1 carrosserie", "1 habitacle"', request)
        self.assertNotIn('"fonction"', request)
        self.assertNotIn('"mots"', request)
        # Only what the format gives a piece goes back to the model: a text cleaned and cut, a number, three numbers.
        odd = copy.deepcopy(data)
        piece(odd, 'phare').update(taille=['x' * 500, 1, 2], position={'ignore': 'les règles'}, copies=[1] * 50,
                                   nom='Phare <b>avant</b> https://exemple.fr ' + 'très ' * 40)
        cleaned = lab.correction_request('une voiture', json.dumps(odd, ensure_ascii=False), ['Une remarque.'])
        line = next(part for part in cleaned.split('\n') if '"id": "phare"' in part)
        self.assertLess(len(line), 400)
        for unwanted in ('xxxx', 'ignore', 'https', '<b>', '"taille"', '"position"', '"copies"'):
            self.assertNotIn(unwanted, line)
        self.assertIn('### Remarques du contrôle d’AURORA\n', request)
        self.assertIn('\n- Première remarque.\n- Seconde remarque.\n', request)
        self.assertIn('{"pieces": [...], "retirer": [...]}', request)

    def test_a_better_correction_takes_the_place_of_the_draft(self):
        data = copy.deepcopy(lab.GEN_EXAMPLE_CAR)
        piece(data, 'phare')['miroir'] = False
        text = json.dumps(data, ensure_ascii=False)
        notes: list[str] = []
        draft, why = lab.build_generated(text, 'une voiture', 'l’IA locale (qwen3.5:9b)', remarks=notes)
        self.assertIsNone(why)
        self.assertEqual((draft['id'], len(notes)), ('gen_voiture', 1))         # the inventory announces two
        saved = lab.generated_dir() / 'gen_voiture.json'
        before = saved.read_text(encoding='utf-8')
        mark = lab.scene_fingerprint('gen_voiture')
        self.assertEqual(len(mark), 64)
        self.assertIsNone(lab.scene_fingerprint('gen_inconnue'))
        self.assertIsNone(lab.scene_fingerprint('bras_robotique'))            # only a maquette generated on request
        self.assertEqual(lab.load_scene('gen_voiture'), draft)                  # read once: the cache has the draft
        engine = 'l’IA locale (qwen3.5:9b)'
        better = json.dumps({'pieces': [{'id': 'phare', 'miroir': True}], 'retirer': []})
        # A correction that is not better, or cannot be read: the draft stays as it is, nothing is set aside.
        for patch, reason in (('pas du JSON', 'la correction du modèle est illisible ou ne change rien'),
                              (json.dumps({'pieces': [{'id': 'habitacle', 'couleur': 'ambre'}], 'retirer': []}),
                               'le contrôle n’a pas moins de remarques sur la description corrigée (1 au lieu de 1)'),
                              # A piece taken away to silence a remark: the maquette would be poorer.
                              (json.dumps({'pieces': [], 'retirer': ['phare']}),
                               'la correction retire des pièces que le contrôle ne disait pas à la place d’une '
                               'autre')):
            self.assertEqual(lab.improve_generated('gen_voiture', text, patch, 'une voiture', engine, fingerprint=mark),
                             (None, [], reason))
            self.assertEqual(saved.read_text(encoding='utf-8'), before)
            self.assertFalse(lab.removed_dir().exists())
        # Not the draft of this request any more (review of 12/11/2026): another file under this id is left alone.
        self.assertEqual(lab.improve_generated('gen_voiture', text, better, 'une voiture', engine, fingerprint='0' * 64),
                         (None, [], 'la première maquette a été retirée ou a changé entre-temps : elle n’est pas '
                                    'remplacée'))
        self.assertEqual(saved.read_text(encoding='utf-8'), before)
        # A better one: the same id, the same file, and a copy of the draft set aside (never deleted).
        scene, left, why = lab.improve_generated('gen_voiture', text, better, 'une voiture', engine, fingerprint=mark)
        self.assertIsNone(why)
        self.assertEqual((scene['id'], left), ('gen_voiture', []))
        self.assertEqual([p.name for p in lab.generated_dir().glob('*.json')], ['gen_voiture.json'])
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8')), scene)
        self.assertEqual(lab.load_scene('gen_voiture'), scene)                  # the cache does not keep the draft
        self.assertEqual(len([p for p in scene['pieces'] if p['nom'].startswith('Phare')]), 2)
        self.assertEqual(scene['mots'], draft['mots'])                          # its own words are still its own
        aside = list(lab.removed_dir().iterdir())
        self.assertEqual(len(aside), 1)
        self.assertTrue(aside[0].name.endswith('_gen_voiture_brouillon'))
        self.assertEqual((aside[0] / 'gen_voiture.json').read_text(encoding='utf-8'), before)
        self.assertEqual(list(lab.generated_dir().glob('*.tmp')), [])
        # A maquette the person removed while the correction was awaited never comes back by itself.
        removed, why = lab.remove_scene('gen_voiture')
        self.assertIsNone(why)
        mark = lab.scene_fingerprint('gen_voiture')
        self.assertIsNone(mark)
        for fingerprint, reason in ((hashlib.sha256(before.encode('utf-8')).hexdigest(),
                                     'la première maquette a été retirée ou a changé entre-temps : elle n’est pas '
                                     'remplacée'),
                                    (None, 'la maquette à corriger n’existe plus : elle n’est pas recréée')):
            self.assertEqual(lab.improve_generated('gen_voiture', text, better, 'une voiture', engine,
                                                   fingerprint=fingerprint), (None, [], reason))
            self.assertFalse(saved.exists())
        draft, why = lab.build_generated(text, 'une voiture', engine)          # asked again: built again
        self.assertIsNone(why)
        # Another scene is never replaced: only the draft named, under its own id.
        other, why = lab.build_generated(json.dumps(lab.GEN_EXAMPLE_BOAT, ensure_ascii=False), 'un voilier', engine)
        self.assertIsNone(why)
        scene, why = lab.build_generated(text, 'une voiture', engine, replaces='gen_voilier')
        self.assertIsNone(scene)
        self.assertEqual(why, 'une scène 3D porte déjà le nom « voiture »')
        self.assertEqual(json.loads((lab.generated_dir() / 'gen_voilier.json').read_text(encoding='utf-8')), other)
        # A piece the control said was in the place of another may leave.
        twice = copy.deepcopy(lab.GEN_EXAMPLE)
        twice['pieces'].append({**piece(twice, 'grille'), 'id': 'grille_bis', 'nom': 'Grille de protection'})
        text = json.dumps(twice, ensure_ascii=False)
        notes = []
        draft, why = lab.build_generated(text, 'un ventilateur de bureau', engine, remarks=notes)
        self.assertIsNone(why)
        self.assertEqual(len(notes), 1)
        self.assertIn('« Grille » et « Grille de protection » occupent la même place', notes[0])
        self.assertEqual([note.kind for note in notes], ['double'])
        scene, left, why = lab.improve_generated(draft['id'], text, json.dumps({'pieces': [], 'retirer': ['grille_bis']}),
                                                 'un ventilateur de bureau', engine)
        self.assertIsNone(why)
        self.assertEqual((left, len(scene['pieces'])), ([], len(draft['pieces']) - 1))
        # The right to take a piece away is never read in the text of a remark, where a model wrote the names: a
        # piece named like a remark earns none (review of 12/11/2026).
        named = copy.deepcopy(lab.GEN_EXAMPLE_BOAT)
        named['inventaire'].append('1 ancre')
        piece(named, 'foc')['nom'] = 'Foc et Mât occupent la même place'
        text = json.dumps(named, ensure_ascii=False)
        notes = []
        draft, why = lab.build_generated(text, 'un bateau', engine, remarks=notes)
        self.assertIsNone(why)
        self.assertEqual([note.kind for note in notes], ['inventaire'])
        self.assertEqual(lab.improve_generated(draft['id'], text, json.dumps({'pieces': [], 'retirer': ['quille']}),
                                               'un bateau', engine)[2],
                         'la correction retire des pièces que le contrôle ne disait pas à la place d’une autre')

    def test_rules_of_a_generated_scene(self):
        scene, _why = self.build(motor())
        for change, words in (({'nature': 'simulation'}, 'jamais un calcul ni une simulation'),
                              ({'id': 'moteur_electrique'}, 'commence par « gen_ »'),
                              ({'origine': 'predefini'}, 'commence par « gen_ »')):
            self.assertIn(words, lab.validate({**scene, **change}) or '', change)
        self.assertIn('commence par « gen_ »', lab.validate({**good(), 'id': 'gen_bras'}) or '')

    def test_schema_and_instructions(self):
        schema = lab.generation_schema()
        self.assertEqual(schema['properties']['pieces']['maxItems'], lab.GEN_MAX_PIECES)
        self.assertEqual(set(schema['required']), set(schema['properties']))
        json.dumps(schema)
        text = lab.generation_instructions()
        for domaine in lab.DOMAINES:
            self.assertIn(domaine, text)
        self.assertIn('Jamais de code', text)

    def test_engine_for_an_object_asked_in_3d(self):
        caps = {'ollama': {'up': True, 'chat_model': 'qwen3.5:9b'}, 'cloud': {'configured': True}}
        asked = SimpleNamespace(intent='ui.navigate', slots={'page': None, 'sujet_3d': 'un moteur électrique'})
        choice = select_engine(asked, mode='equilibre', text='montre-moi un moteur en 3D', caps=caps,
                               budget_remaining_usd=1.0)
        self.assertEqual(choice.engine, 'tool+local')
        self.assertIn('vérifiée par le code', choice.reason)
        claude = select_engine(asked, mode='equilibre', text='utilise Claude pour montrer un moteur en 3D', caps=caps,
                               budget_remaining_usd=1.0)
        self.assertEqual((claude.engine, claude.needs_consent), ('tool+cloud', True))
        down = {'ollama': {'up': False}, 'cloud': {'configured': False}}
        nothing = select_engine(asked, mode='equilibre', text='montre-moi un moteur en 3D', caps=down)
        self.assertEqual(nothing.engine, 'tool')
        self.assertIn('ne peut pas être construite', nothing.reason)
        page = SimpleNamespace(intent='ui.navigate', slots={'page': 'memory'})
        self.assertEqual(select_engine(page, mode='equilibre', text='ouvre la mémoire', caps=caps).engine, 'none')
        empty = SimpleNamespace(intent='ui.navigate', slots={'page': None, 'sujet_3d': ''})
        self.assertEqual(select_engine(empty, mode='equilibre', text='montre en 3D', caps=caps).engine, 'none')


def real(name: str):
    """FakeTools response that runs the real tool (no network: data/scenes)."""
    def respond(params: dict) -> dict:
        # run_tool_sync starts its own event loop: in a thread, outside the loop of the orchestrator
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(base.run_tool_sync, name, params).result().to_dict()
        return {k: result[k] for k in ('ok', 'data', 'summary', 'provenance', 'error')}
    return respond


def real_route(text: str) -> FakeDecision:
    found = rules.analyze(text)
    return FakeDecision(found.intent, found.confidence, slots=extract_slots(found.intent, text))


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        lab.clear_cache()
        tools = FakeTools({'lab.scene': real('lab.scene'), 'lab.catalogue': real('lab.catalogue'),
                           'weather.current': {'ok': True, 'data': {'city': 'Paris'}, 'summary': 'À Paris : 12 °C.',
                                               'provenance': 'open-meteo'}})
        self.orch, self.tools, self.ollama, _ = build(real_route, tools=tools)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def check(self, result: dict) -> None:
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertTrue(result['verification']['ok'], result['verification'])
        self.assertEqual(self.ollama.calls, [])          # deterministic: no model wrote the answer

    async def test_opening_without_context(self):
        result = await self.orch.handle_request('Montre-moi comment fonctionne un bras robotique')
        self.check(result)
        self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'bras_robotique', 'action': 'ouvrir'}})
        self.assertIn('illustration pédagogique', result['answer'])
        self.assertEqual(self.tools.called('lab.scene'), [{'id': 'bras_robotique'}])   # real provenance
        step = [s for s in result['steps'] if s['tool'] == 'lab.scene'][0]
        self.assertEqual(step['status'], 'done')
        self.assertNotIn('mesure', (result.get('origin') or {}).get('kinds') or [])

    async def test_subject_without_scene_is_honest(self):
        # Phase 4: the local model is asked; its answer is no description (« Réponse locale de test. »).
        result = await self.orch.handle_request('Montre-moi un moteur électrique en 3D')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertIsNone(result['ui'])
        self.assertIn('Je n’ai pas pu construire de scène 3D pour « un moteur électrique » : la réponse du modèle '
                      'n’est pas une description JSON lisible', result['answer'])
        self.assertIn('Bras robotique à 4 axes', result['answer'])
        self.assertEqual(self.tools.called('lab.catalogue'), [{}])
        self.assertEqual(len(self.ollama.calls), 1)
        call = self.ollama.calls[0]
        self.assertEqual(call['format'], lab.generation_schema())          # only a description can be written
        self.assertNotIn('bouts', call['system'])           # 12/11/2026: the inclined pieces are asked of Claude only
        self.assertNotIn('bouts', json.dumps(call['format']))
        self.assertEqual(call['timeout'], 300)
        self.assertIsNone(call['options'])          # the loaded model already has 8192 tokens of context
        self.assertIn('Format attendu, un seul objet JSON', call['system'])
        # 11/11/2026: the state of the capabilities is left out of this step (room for the description), and the
        # lying example is chosen from the object asked.
        self.assertNotIn('État réel des capacités', call['system'])
        self.assertIn('« un avion de tourisme »', call['system'])
        self.assertIn('### Objet à construire en trois dimensions\nun moteur électrique',
                      call['messages'][0]['content'])
        step = next(s for s in result['steps'] if s['stage'] == 'local_ai')
        self.assertEqual(step['status'], 'error')
        self.assertEqual(list(lab.generated_dir().glob('*.json')), [])     # nothing kept

    async def test_the_maquettes_can_have_their_own_model(self):
        """13/11/2026, ID Studio IA : « tu peux changer le modèle local si besoin ». Deux tirages du banc : gemma4:12b construit
        mieux les objets difficiles, trois fois plus lentement. Les maquettes ont leur réglage, le chat garde le sien."""
        self.assertEqual(config.load_settings()['local_scene_model'], '')          # by default: the main model
        with self.assertRaises(ValueError):
            config.update_settings({'local_scene_model': 'bge-m3'})                # an embedding model writes nothing
        self.assertEqual(config.update_settings({'local_scene_model': ' gemma4:12b '})['local_scene_model'], 'gemma4:12b')
        self.assertEqual(config.load_settings()['local_model'], config.local_model())   # the chat keeps its model
        self.ollama.reply = json.dumps(motor(), ensure_ascii=False)
        result = await self.orch.handle_request('Montre-moi un moteur électrique en trois dimensions')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertEqual({c['model'] for c in self.ollama.calls}, {'gemma4:12b'})   # the maquette, and its correction
        self.assertIn('Objet généré automatiquement par l’IA locale (gemma4:12b)', result['answer'])
        # The engine shown before the maquette is written names the model that writes it, not the one of the chat.
        caps = {'ollama': {'up': True, 'chat_model': 'qwen3.5:9b', 'scene_model': 'gemma4:12b'}, 'cloud': {}}
        asked = SimpleNamespace(intent='ui.navigate', slots={'page': None, 'sujet_3d': 'un robot'})
        choice = select_engine(asked, mode='equilibre', text='fais-moi un modèle 3D d’un robot', caps=caps)
        self.assertEqual((choice.engine, choice.model), ('tool+local', 'gemma4:12b'))
        self.assertIn('(gemma4:12b)', choice.reason)
        chat = select_engine(SimpleNamespace(intent='chat.general', slots={}), mode='equilibre', text='bonjour', caps=caps)
        self.assertEqual(chat.model, 'qwen3.5:9b')
        # '' gives the maquettes back to the main model.
        self.assertEqual(config.update_settings({'local_scene_model': ''})['local_scene_model'], '')

    async def test_object_built_on_request_then_commanded(self):
        # The cahier's example: « montre-moi un moteur électrique en trois dimensions », then commands on it.
        self.ollama.reply = json.dumps(motor(), ensure_ascii=False)
        result = await self.orch.handle_request('Montre-moi un moteur électrique en trois dimensions')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'gen_moteur_electrique',
                                                                      'action': 'ouvrir'}})
        self.assertIn('J’affiche « Moteur électrique » sur l’accueil (illustration pédagogique générée '
                      'automatiquement).', result['answer'])
        self.assertIn('Objet généré automatiquement par l’IA locale (qwen3.5:9b)', result['answer'])
        self.assertNotIn('"pieces"', result['answer'])                     # the JSON is never the answer
        step = next(s for s in result['steps'] if s['stage'] == 'local_ai')
        self.assertEqual(step['status'], 'done')
        self.assertTrue((lab.generated_dir() / 'gen_moteur_electrique.json').is_file())
        context = {'id': 'gen_moteur_electrique'}
        for text, order, words in (
                ('Retire le boîtier', {'action': 'masquer', 'piece': 'boitier'}, 'Je masque « Boîtier »'),
                ('Montre les bobines', {'action': 'montrer', 'groupe': 'bobines'}, 'Je mets en évidence « Bobines »'),
                ('Fais tourner le rotor', {'action': 'animer', 'piece': 'rotor'}, 'J’anime « Rotor »')):
            result = await self.orch.handle_request(text, options={'scene': context})
            self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'gen_moteur_electrique', **order}},
                             text)
            self.assertIn(words, result['answer'], text)
        result = await self.orch.handle_request('Montre-moi un moteur électrique')   # kept: opened, not rebuilt
        self.assertEqual(result['ui']['scene'], {'scene': 'gen_moteur_electrique', 'action': 'ouvrir'})
        # 12/11/2026: two calls for the first request, the description then ONE correction (the control found the
        # rotor inside the housing). The double answered the same description again: nothing changed, the first
        # maquette stayed.
        self.assertEqual(len(self.ollama.calls), 2)
        self.assertEqual(self.ollama.calls[1]['format'], lab.correction_schema())
        self.assertIn('« Rotor » est entièrement à l’intérieur de « Boîtier »',
                      self.ollama.calls[1]['messages'][0]['content'])
        self.assertFalse(lab.removed_dir().exists())

    def car_with_one_headlight(self) -> tuple[str, str]:
        """A description the control has one remark on (a headlight on one side only), and the correction a model
        would answer with."""
        faulty = copy.deepcopy(lab.GEN_EXAMPLE_CAR)
        piece(faulty, 'phare')['miroir'] = False
        return (json.dumps(faulty, ensure_ascii=False),
                json.dumps({'pieces': [{'id': 'phare', 'miroir': True}], 'retirer': []}))

    @staticmethod
    def headlights(scene_id: str = 'gen_voiture') -> int:
        return len([p for p in lab.load_scene(scene_id)['pieces'] if p['nom'].startswith('Phare')])

    async def test_a_maquette_is_checked_then_corrected_once(self):
        # 12/11/2026 (ID Studio IA : « un vrai générateur d'hologramme comme tu fais toi »): the description, the control of
        # the code, then ONE correction by the same model. The corrected maquette takes the place of the first.
        description, patch = self.car_with_one_headlight()
        self.ollama.reply = lambda messages: (patch if '### Correction demandée' in messages[0]['content']
                                              else description)
        result = await self.orch.handle_request('Montre-moi une voiture en 3D')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'gen_voiture', 'action': 'ouvrir'}})
        self.assertEqual(len(self.ollama.calls), 2)
        first, fix = self.ollama.calls
        self.assertEqual((first['format'], fix['format']), (lab.generation_schema(), lab.correction_schema()))
        self.assertIn('Format attendu, un seul objet JSON', fix['system'])      # the same rules of the format
        content = fix['messages'][0]['content']
        self.assertIn('### Objet à construire en trois dimensions\nune voiture\n', content)
        self.assertIn('L’inventaire annonce « 2 phares », mais la description n’en place que 1.', content)
        self.assertEqual([(s['title'], s['status']) for s in result['steps'] if s['stage'] == 'local_ai'],
                         [('Construction de la scène 3D — IA locale', 'done'),
                          ('Correction de la maquette — IA locale', 'done')])
        self.assertEqual(self.headlights(), 2)
        self.assertIn('AURORA a contrôlé cette maquette et l’a fait corriger.', result['answer'])
        self.assertNotIn('garde des remarques', result['answer'])
        self.assertEqual([p.name for p in lab.generated_dir().glob('*.json')], ['gen_voiture.json'])
        self.assertEqual(len(list(lab.removed_dir().iterdir())), 1)             # the draft: moved aside, not deleted
        # A description the control has nothing to say of is not sent back to the model.
        self.ollama.calls.clear()
        self.ollama.reply = json.dumps(lab.GEN_EXAMPLE_BOAT, ensure_ascii=False)
        result = await self.orch.handle_request('Montre-moi un voilier en 3D')
        self.assertEqual(result['ui']['scene'], {'scene': 'gen_voilier', 'action': 'ouvrir'})
        self.assertEqual(len(self.ollama.calls), 1)
        self.assertNotIn('contrôl', result['answer'])
        self.assertEqual([s['title'] for s in result['steps'] if s['stage'] == 'local_ai'],
                         ['Construction de la scène 3D — IA locale'])

    async def test_a_correction_that_fails_keeps_the_first_maquette(self):
        description, _patch = self.car_with_one_headlight()
        config.update_settings({'mode': 'equilibre'})
        tools = FakeTools({'lab.catalogue': real('lab.catalogue')})
        for answer, status in (('Je ne sais pas corriger.', 'done'), ('', 'error')):
            lab.clear_cache()
            for kept in lab.generated_dir().glob('*.json'):
                kept.unlink()
            ollama = FakeOllama(reply=lambda messages, answer=answer: (
                answer if '### Correction demandée' in messages[0]['content'] else description))
            orch, _, ollama, claude = build(real_route, tools=tools, ollama=ollama, claude=FakeClaude(configured=True))
            result = await orch.handle_request('Montre-moi une voiture en 3D')
            self.assertEqual(result['status'], 'done', result.get('answer'))
            self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'gen_voiture', 'action': 'ouvrir'}})
            self.assertEqual(self.headlights(), 1)                              # the first maquette, as it was
            # The answer says which: a correction that was not kept, or no correction at all.
            self.assertIn('Le contrôle d’AURORA garde des remarques sur cette maquette'
                          + (' : la correction demandée n’a pas été retenue.' if status == 'done'
                             else ', qui n’a pas été corrigée.'), result['answer'])
            steps = [s for s in result['steps'] if s['tool'] == 'llm']
            self.assertEqual([(s['stage'], s['status']) for s in steps], [('local_ai', 'done'), ('local_ai', status)])
            self.assertEqual(claude.calls, [])              # Claude is never asked to describe the object again
            self.assertFalse(lab.removed_dir().exists())

    async def test_claude_corrects_its_maquette_with_a_consent_of_its_own(self):
        description, patch = self.car_with_one_headlight()
        config.update_settings({'mode': 'equilibre'})
        tools = FakeTools({'lab.catalogue': real('lab.catalogue')})
        for decision in ('grant', 'deny'):
            lab.clear_cache()
            for kept in lab.generated_dir().glob('*.json'):
                kept.unlink()
            orch, _, ollama, claude = build(real_route, tools=tools, claude=FakeClaude(configured=True,
                                                                                         reply=description))
            # The local model first (its answer is no description), then Claude with the person's consent.
            result = await orch.handle_request('Montre-moi une voiture en 3D')
            self.assertEqual(result['status'], 'waiting_permission')
            result = await orch.decide_permission(result['permission']['id'], 'grant')
            # The maquette is built and kept. Its correction is another sending: its own preview, its own consent.
            self.assertEqual(result['status'], 'waiting_permission', result.get('answer'))
            self.assertEqual(self.headlights(), 1)
            preview = result['permission']['preview']
            self.assertEqual(result['permission']['action'], 'cloud.send')
            self.assertIn('L’inventaire annonce « 2 phares », mais la description n’en place que 1.', preview['text'])
            self.assertIn('"id": "phare"', preview['text'])
            claude.reply = patch
            done = await orch.decide_permission(result['permission']['id'], decision)
            self.assertEqual(done['status'], 'done', done.get('answer'))
            self.assertEqual(done['ui'], {'navigate': 'home', 'scene': {'scene': 'gen_voiture', 'action': 'ouvrir'}})
            self.assertEqual(len(ollama.calls), 1)          # its first try only: it never replaces a correction
            self.assertNotIn('### Correction demandée', ollama.calls[0]['messages'][0]['content'])
            self.assertIn('Objet généré automatiquement par Claude (claude-sonnet-5-5)', done['answer'])
            if decision == 'grant':
                self.assertEqual(len(claude.calls), 2)
                self.assertEqual(claude.calls[1]['messages'][0]['content'], preview['text'])
                self.assertEqual(self.headlights(), 2)
                self.assertIn('AURORA a contrôlé cette maquette et l’a fait corriger.', done['answer'])
                self.assertAlmostEqual(done['cost_usd'], 2 * 0.0123, places=6)
            else:
                self.assertEqual(len(claude.calls), 1)
                self.assertEqual(self.headlights(), 1)
                self.assertIn('Le contrôle d’AURORA garde des remarques sur cette maquette, qui n’a pas été corrigée.',
                              done['answer'])
                self.assertEqual([(s['stage'], s['status']) for s in done['steps'] if s['tool'] == 'llm'],
                                 [('local_ai', 'error'), ('cloud_ai', 'done'), ('cloud_ai', 'skipped')])
                self.assertIn('La maquette déjà construite est gardée telle quelle.', done['answer'])

    async def test_a_maquette_removed_while_its_correction_waits_stays_removed(self):
        # Review of 12/11/2026: the card of a correction may wait thirty minutes. The person removes the maquette
        # meanwhile, then accepts the card: the maquette does not come back by itself.
        description, patch = self.car_with_one_headlight()
        config.update_settings({'mode': 'equilibre'})
        tools = FakeTools({'lab.catalogue': real('lab.catalogue')})
        orch, _, _ollama, claude = build(real_route, tools=tools, claude=FakeClaude(configured=True, reply=description))
        result = await orch.handle_request('Montre-moi une voiture en 3D')
        result = await orch.decide_permission(result['permission']['id'], 'grant')
        self.assertEqual(result['status'], 'waiting_permission', result.get('answer'))
        _removed, why = lab.remove_scene('gen_voiture')
        self.assertIsNone(why)
        claude.reply = patch
        done = await orch.decide_permission(result['permission']['id'], 'grant')
        self.assertFalse((lab.generated_dir() / 'gen_voiture.json').exists())
        self.assertIsNone(done['ui'])
        step = next(s for s in orch._load(done['task_id'])['steps'] if s['title'].startswith('Correction de la maquette'))
        self.assertIn('la première maquette a été retirée ou a changé entre-temps', step['result']['text'])
        self.assertNotIn('l’a fait corriger', done['answer'])

    async def test_no_ai_to_describe_the_object(self):
        tools = FakeTools({'lab.catalogue': real('lab.catalogue')})
        orch, tools, ollama, _ = build(real_route, tools=tools, ollama=FakeOllama(up=False))
        result = await orch.handle_request('Montre-moi un moteur électrique en 3D')
        self.assertEqual(result['status'], 'done', result.get('answer'))
        self.assertIsNone(result['ui'])
        self.assertIn('Je n’ai pas pu construire de scène 3D pour « un moteur électrique » : Aucune IA disponible '
                      'pour décrire l’objet', result['answer'])
        self.assertEqual(ollama.calls, [])

    async def test_every_example_of_the_table_with_a_scene_shown(self):
        engine = self.orch.deps.decision_engine
        for action, phrases in TABLE.items():
            for text in phrases:
                result = await self.orch.handle_request(text, options={'scene': CTX})
                self.check(result)
                self.assertEqual(result['ui'], {'navigate': 'home', 'scene': {'scene': 'bras_robotique',
                                                                              'action': action}}, text)
                self.assertEqual(result['decision']['intent'], 'ui.navigate')
        for text, (action, kind, item) in TARGETED.items():
            result = await self.orch.handle_request(text, options={'scene': CTX})
            self.check(result)
            self.assertEqual(result['ui']['scene'], {'scene': 'bras_robotique', 'action': action, kind: item}, text)
        self.assertEqual(engine.calls, [])               # read as scene commands, before the usual routing

    async def test_designated_piece(self):
        context = {'id': 'bras_robotique', 'piece': 'servo_coude'}
        result = await self.orch.handle_request('Agrandis cette pièce', options={'scene': context})
        self.check(result)
        self.assertEqual(result['ui']['scene'], {'scene': 'bras_robotique', 'action': 'agrandir', 'piece': 'servo_coude'})
        self.assertIn('Servomoteur du coude', result['answer'])
        result = await self.orch.handle_request('Explique celle-ci', options={'scene': context})
        self.check(result)
        self.assertIn(piece(good(), 'servo_coude')['fonction'], result['answer'])
        result = await self.orch.handle_request('Agrandis cette pièce', options={'scene': CTX})
        self.check(result)
        self.assertIsNone(result['ui'])
        self.assertIn('Aucune pièce n’est sélectionnée', result['answer'])

    async def test_unknown_piece(self):
        result = await self.orch.handle_request('Retire le boîtier', options={'scene': CTX})
        self.check(result)
        self.assertIsNone(result['ui'])
        self.assertIn('Je ne trouve pas « boîtier »', result['answer'])
        self.assertIn('Carte de commande', result['answer'])

    async def test_without_context_or_with_another_request(self):
        result = await self.orch.handle_request('Démonte-le')         # no scene shown: a conversation
        self.assertNotIn('scene', result.get('ui') or {})
        self.assertEqual(result['decision']['intent'], 'chat.general')
        for text, intent in (('Affiche la météo', 'weather.current'), ('Ouvre la mémoire', 'ui.navigate'),
                             ('Explique-moi comment fonctionne un moteur', 'chat.general')):
            result = await self.orch.handle_request(text, options={'scene': CTX})
            self.assertEqual(result['decision']['intent'], intent, text)
            self.assertNotIn('scene', result.get('ui') or {}, text)
        result = await self.orch.handle_request('Démonte-le', options={'scene': {'id': 'moteur'}})   # unknown scene
        self.assertEqual(result['decision']['intent'], 'chat.general')

    async def test_scene_that_became_unreadable(self):
        self.tools.responses['lab.scene'] = {'ok': False, 'summary': 'Scène « bras_robotique » invalide.',
                                             'provenance': lab.PROVENANCE, 'error': 'Scène « bras_robotique » invalide.'}
        result = await self.orch.handle_request('Montre-moi un bras robotique')
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['ui'])
        self.assertIn('Scène 3D indisponible', result['answer'])


def stl(triangles: int = 12, seed: int = 0) -> bytes:
    """A binary STL file: 80 bytes of title, the count, then 50 bytes per triangle."""
    one = struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    return b'essai AURORA'.ljust(80, bytes([seed])) + struct.pack('<I', triangles) + one * triangles


def glb(json_text: str = '{"asset":{"version":"2.0"}}') -> bytes:
    """A GLB file with its JSON block only (the envelope the service checks)."""
    block = json_text.encode('utf-8')
    block += b' ' * (-len(block) % 4)
    return b'glTF' + struct.pack('<II', 2, 12 + 8 + len(block)) + struct.pack('<I', len(block)) + b'JSON' + block


OBJ = b'# essai\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'


class ImportedModelTests(unittest.TestCase):
    """10/11/2026 (ID Studio IA : ouvrir ses modèles 3D existants): a model file kept as a scene of one block."""

    def setUp(self):
        self.env = TempEnv().__enter__()   # the models go to a temporary AURORA_HOME
        lab.clear_cache()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_a_model_is_checked_kept_and_listed(self):
        data = stl(12)
        scene, why, known = lab.import_model(r'C:\Users\moi\..\Ma veste-VITA_v2.stl', data)
        self.assertIsNone(why)
        self.assertFalse(known)
        self.assertIsNone(lab.validate(scene, file_id='imp_ma_veste_vita_v2'))
        self.assertEqual((scene['id'], scene['titre'], scene['origine'], scene['domaine'], scene['nature']),
                         ('imp_ma_veste_vita_v2', 'Ma veste VITA v2', 'importe', 'objet', 'illustration'))
        piece, = scene['pieces']
        self.assertEqual(piece['forme'], {'type': 'maillage', 'fichier': 'imp_ma_veste_vita_v2.stl', 'taille': 2.0,
                                          'debout': 'z'})
        self.assertNotIn('eclate', piece)                                 # one block: nothing to take apart
        # What the panel says of the file is counted by the code, and the folders of the name are not kept.
        self.assertEqual({p['nom']: p['valeur'] for p in piece['proprietes']},
                         {'Fichier d’origine': 'Ma veste-VITA_v2.stl', 'Taille du fichier': '1 Ko', 'Triangles': '12'})
        self.assertIn('Ma veste-VITA_v2.stl', scene['avertissement'])
        self.assertNotIn('Users', json.dumps(scene, ensure_ascii=False))
        kept = lab.model_path('imp_ma_veste_vita_v2.stl')
        self.assertEqual(kept.read_bytes(), data)
        self.assertTrue(str(kept).startswith(str(self.env.dir)))          # never the user's ~/.aurora
        self.assertEqual(lab.load_scene('imp_ma_veste_vita_v2'), scene)
        self.assertEqual(lab.catalogue()[-1]['origine'], 'importe')
        self.assertEqual(lab.kind_label(scene), 'modèle 3D importé')
        self.assertIn('« Ma veste VITA v2 » (modèle 3D importé)', lab.available_text())
        # Its words open it, with or without the end of the file name.
        self.assertEqual(scene['mots'], ['ma veste vita v2', 'ma veste'])
        self.assertEqual(scene_subject('montre-moi ma veste vita v2'), 'imp_ma_veste_vita_v2')
        self.assertEqual(scene_subject('ouvre ma veste'), 'imp_ma_veste_vita_v2')

    def test_the_same_file_again_reopens_its_scene(self):
        first, _, _ = lab.import_model('gourde.stl', stl(4))
        again, why, known = lab.import_model('autre nom.stl', stl(4))
        self.assertEqual((again, why, known), (first, None, True))
        self.assertEqual(sorted(p.name for p in lab.models_dir().iterdir()), ['imp_gourde.json', 'imp_gourde.stl'])
        other, why, known = lab.import_model('gourde.stl', stl(4, seed=1))   # same name, another content
        self.assertEqual((other['id'], other['titre'], other['mots'], why, known),
                         ('imp_gourde_2', 'Gourde 2', ['gourde 2'], None, False))
        self.assertEqual(lab.model_path('imp_gourde.stl').read_bytes(), stl(4))   # the first one is not overwritten
        self.assertEqual(scene_subject('montre-moi la gourde 2'), 'imp_gourde_2')

    def test_every_kind_and_the_axis_that_points_up(self):
        for name, data, faces, up in (('boitier.glb', glb(), None, 'y'), ('piece.obj', OBJ, ('Faces', '1'), 'y'),
                                      ('texte.stl', b'solid a\nfacet normal 0 0 1\nendfacet\nendsolid a\n',
                                       ('Triangles', '1'), 'z')):
            scene, why, _ = lab.import_model(name, data)
            self.assertIsNone(why, name)
            piece, = scene['pieces']
            self.assertEqual(piece['forme']['debout'], up, name)
            props = {p['nom']: p['valeur'] for p in piece['proprietes']}
            self.assertEqual(faces is None or props.get(faces[0]) == faces[1], True, name)
            self.assertEqual(lab.model_path(piece['forme']['fichier']).read_bytes(), data)

    def test_what_is_not_a_model_is_refused_and_said(self):
        for name, data, words in (
                ('virus.exe', stl(), 'type de fichier non pris en charge'),
                ('vide.stl', b'', 'le fichier est vide'),
                ('faux.stl', b'ceci n est pas un modele', 'pas un fichier STL lisible'),
                ('coupe.stl', stl(12)[:-7], 'pas un fichier STL lisible'),
                ('faux.glb', b'PK\x03\x04' + b'\0' * 40, 'en-tête « glTF » manque'),
                ('coupe.glb', glb()[:-4], 'fichier GLB incomplet'),
                ('vieux.glb', b'glTF' + struct.pack('<II', 1, 20) + b'\0' * 8, 'version 1'),
                ('binaire.obj', b'\x00\x01\x02' * 30, 'pas un fichier OBJ'),
                ('sans_face.obj', b'v 0 0 0\nv 1 0 0\nv 0 1 0\n', 'aucune face')):
            scene, why, known = lab.import_model(name, data)
            self.assertIsNone(scene, name)
            self.assertIn(words, why, name)
            self.assertFalse(known)
        with mock.patch.object(lab, 'MAX_MODEL_BYTES', 100):
            self.assertIn('dépasse', lab.import_model('gros.stl', stl(12))[1])
        self.assertFalse(lab.models_dir().exists())                         # nothing was written
        # A file name that gives no word to call it by (« un », « 1 ») : « Modèle importé ».
        scene, why, _ = lab.import_model('un.stl', stl(1))
        self.assertEqual((scene['id'], scene['titre'], scene['mots'], why),
                         ('imp_modele_importe', 'Modèle importé', ['modele importe'], None))
        with mock.patch.object(lab, 'MAX_MODELS', 1):
            self.assertIn('AURORA n’en efface aucun', lab.import_model('second.stl', stl(2))[1])

    def test_only_an_imported_scene_may_show_its_own_file(self):
        scene, _, _ = lab.import_model('gourde.stl', stl(4))
        mesh = scene['pieces'][0]['forme']
        self.assertIn('n’est permis que dans un modèle importé',
                      lab.validate({**good(), 'pieces': [{**good()['pieces'][0], 'forme': mesh}]}) or '')
        for fichier, words in (('imp_autre.stl', 'porte le nom de sa scène'), ('../gourde.stl', 'invalide'),
                               ('C:/x/imp_gourde.stl', 'invalide'), ('imp_gourde.exe', 'invalide')):
            changed = {**scene, 'pieces': [{**scene['pieces'][0], 'forme': {**mesh, 'fichier': fichier}}]}
            self.assertIn(words, lab.validate(changed) or '', fichier)
        for change, words in (({'id': 'gourde'}, 'commence par « imp_ »'), ({'origine': 'genere'}, 'gen_'),
                              ({'nature': 'simulation'}, 'jamais comme un calcul ni une simulation')):
            self.assertIn(words, lab.validate({**scene, **change}) or '', change)
        for name in ('../imp_gourde.stl', 'imp_gourde.json', 'imp_absent.stl', r'..\..\aurora.db'):
            with self.assertRaises(base.ToolError):
                lab.model_path(name)

    def test_answers_of_a_scene_of_one_block(self):
        scene, _, _ = lab.import_model('gourde.stl', stl(4))
        result = {'ok': True, 'data': scene}
        text, ui = compose.scene_view({'scene': scene['id'], 'action': 'ouvrir'}, result)
        self.assertIn('J’affiche « Gourde » sur l’accueil (modèle 3D importé).', text)
        self.assertIn('AURORA ne l’a ni construit ni vérifié', text)
        self.assertIn('Essayez : « redresse-le », « tourne-le », « renomme-le en … »', text)   # what a kept model takes
        self.assertNotIn('démonte', text)
        self.assertEqual(ui, {'navigate': 'home', 'scene': {'scene': 'imp_gourde', 'action': 'ouvrir'}})
        text, ui = compose.scene_view({'scene': scene['id'], 'action': 'eclater'}, result)
        self.assertEqual((text, ui), ('« Gourde » est en un seul bloc : il n’y a aucune pièce à écarter '
                                      '(modèle 3D importé).', None))
        text, _ui = compose.scene_view({'scene': scene['id'], 'action': 'expliquer'}, result)
        self.assertIn('Modèle 3D importé, pas une simulation.', text)

    def test_a_shape_rebuilt_from_a_photo_says_so(self):
        """« Photo vers 3D » (10/11/2026): the same block, kept the same way, but never called a file of the user."""
        scene, why, known = lab.import_model(r'D:\Photos\Chaise de bureau.JPG', OBJ,
                                             photo={'moteur': 'TripoSR (essai)', 'secondes': 34.5, 'couverture': 0.295,
                                                    'lissage': 10})
        self.assertEqual((why, known), (None, False))
        self.assertIsNone(lab.validate(scene, file_id='imp_chaise_de_bureau'))
        self.assertEqual((scene['id'], scene['titre'], scene['origine'], scene['nature'], scene['mots']),
                         ('imp_chaise_de_bureau', 'Chaise de bureau', 'reconstruit', 'illustration',
                          ['chaise de bureau', 'chaise de']))
        piece, = scene['pieces']
        self.assertEqual(piece['forme'], {'type': 'maillage', 'fichier': 'imp_chaise_de_bureau.obj', 'taille': 2.0,
                                          'debout': 'y'})
        self.assertEqual({p['nom']: p['valeur'] for p in piece['proprietes']},
                         {'Photo d’origine': 'Chaise de bureau.JPG', 'Reconstruit par': 'TripoSR (essai)',
                          'Durée de la reconstruction': '34 s', 'Part de la photo prise pour l’objet': '30 %',
                          'Triangles': '1', 'Surface': 'lissée après la reconstruction (10 passes)'})   # 10/11/2026
        rough, _, _ = lab.import_model('brute.png', OBJ + b'# 3\n', photo={'lissage': 0})   # not smoothed: not said
        self.assertNotIn('Surface', [p['nom'] for p in rough['pieces'][0]['proprietes']])
        bare, _, _ = lab.import_model('sans mesure.png', OBJ + b'# 2\n', photo={})   # nothing measured: nothing said
        self.assertEqual([p['nom'] for p in bare['pieces'][0]['proprietes']],
                         ['Photo d’origine', 'Reconstruit par', 'Triangles'])
        self.assertIn('par une IA (TripoSR)', bare['avertissement'])           # the engine of before, when none is named
        # 10/11/2026 (ID Studio IA: « Oui, branche-le »): Pixal3D gives a GLB, lightened and stood upright by its runner.
        fine, why, _ = lab.import_model('Satellite.jpg', glb(), photo={
            'moteur': 'Pixal3D (essai)', 'nom': 'Pixal3D', 'format': 'glb', 'secondes': 196.4, 'couverture': 0.354,
            'triangles': 279440, 'faces_avant': 1514724, 'aplomb_deg': 45.5, 'lissage': None})
        self.assertIsNone(why)
        self.assertEqual(fine['pieces'][0]['forme']['fichier'], 'imp_satellite.glb')
        self.assertEqual({p['nom']: p['valeur'] for p in fine['pieces'][0]['proprietes']},
                         {'Photo d’origine': 'Satellite.jpg', 'Reconstruit par': 'Pixal3D (essai)',
                          'Durée de la reconstruction': '196 s', 'Part de la photo prise pour l’objet': '35 %',
                          'Triangles': '279 440', 'Allègement': '1 514 724 faces à l’origine, ramenées à 279 440',
                          'Aplomb': 'forme redressée de 46° après la reconstruction'})
        self.assertIn('par une IA (Pixal3D)', fine['avertissement'])
        self.assertIn('par une IA locale (Pixal3D)', fine['fonctionnement'])
        self.assertEqual((fine['origine'], lab.models_dir().joinpath('imp_satellite.glb').read_bytes()),
                         ('reconstruit', glb()))
        flat, _, _ = lab.import_model('droit.jpg', glb('{"asset":{"version":"2.0"},"extras":1}'),
                                      photo={'format': 'glb', 'nom': 'Pixal3D', 'triangles': 5, 'faces_avant': 5})
        self.assertEqual([p['nom'] for p in flat['pieces'][0]['proprietes']],      # nothing lightened, nothing leaned
                         ['Photo d’origine', 'Reconstruit par', 'Triangles'])
        self.assertNotIn('Photos', json.dumps(scene, ensure_ascii=False))   # the folders of the photo are not kept
        self.assertEqual(lab.kind_label(scene), 'forme 3D reconstruite depuis une photo')
        self.assertEqual(lab.catalogue()[-1]['origine'], 'reconstruit')
        result = {'ok': True, 'data': scene}
        text, ui = compose.scene_view({'scene': scene['id'], 'action': 'ouvrir'}, result)
        self.assertIn('J’affiche « Chaise de bureau » sur l’accueil (forme 3D reconstruite depuis une photo).', text)
        self.assertIn('les côtés que la photo ne montre pas sont inventés, et aucune dimension n’est réelle', text)
        self.assertEqual(ui, {'navigate': 'home', 'scene': {'scene': 'imp_chaise_de_bureau', 'action': 'ouvrir'}})
        text, ui = compose.scene_view({'scene': scene['id'], 'action': 'eclater'}, result)
        self.assertEqual((text, ui), ('« Chaise de bureau » est en un seul bloc : il n’y a aucune pièce à écarter '
                                      '(forme 3D reconstruite depuis une photo).', None))
        # The rules of a kept model hold for it too, and a file of the user stays « importe ».
        for change, words in (({'id': 'chaise'}, 'commence par « imp_ »'), ({'origine': 'predefini'}, 'imp_'),
                              ({'nature': 'simulation'}, 'jamais comme un calcul ni une simulation')):
            self.assertIn(words, lab.validate({**scene, **change}) or '', change)
        plain, _, _ = lab.import_model('piece.obj', OBJ + b'# autre\n')
        self.assertEqual(plain['origine'], 'importe')

    def test_a_kept_model_is_stood_up_turned_and_renamed(self):
        """ID Studio IA, 10/11/2026 (« redresser et renommer »): only the description changes, at the user's request."""
        scene, _, _ = lab.import_model('Cooling_Jacket_0521.stl', stl(4))
        sid = scene['id']
        model = lab.model_path(f'{sid}.stl').read_bytes()
        self.assertNotIn('quart', scene['pieces'][0]['forme'])           # not turned by hand yet
        stood, why = lab.adjust_model(sid, debout='-y', quart=2)
        self.assertIsNone(why)
        self.assertEqual(stood['pieces'][0]['forme'], {'type': 'maillage', 'fichier': f'{sid}.stl', 'taille': 2.0,
                                                       'debout': '-y', 'quart': 2})
        self.assertEqual(lab.load_scene(sid), stood)                      # kept for the next time
        named, why = lab.adjust_model(sid, titre='veste VITA')
        self.assertEqual((why, named['id'], named['titre'], named['mots']), (None, sid, 'Veste VITA', ['veste vita']))
        piece, = named['pieces']
        self.assertEqual((piece['nom'], piece['forme']['debout'], piece['forme']['quart']), ('Veste VITA', '-y', 2))
        self.assertTrue(named['resume'].startswith('« Veste VITA » est un modèle 3D que vous avez donné'))
        self.assertEqual({p['nom']: p['valeur'] for p in piece['proprietes']}['Fichier d’origine'],
                         'Cooling_Jacket_0521.stl')                       # where it came from stays said
        self.assertEqual(lab.load_scene(sid), named)
        self.assertEqual(scene_subject('montre-moi la veste vita'), sid)
        self.assertNotEqual(scene_subject('montre-moi cooling jacket'), sid)
        self.assertEqual(lab.model_path(f'{sid}.stl').read_bytes(), model)   # the model file is never touched
        self.assertEqual(sorted(p.name for p in lab.models_dir().iterdir()), [f'{sid}.json', f'{sid}.stl'])
        # What is refused is said, and changes nothing.
        lab.import_model('gourde.stl', stl(4, seed=1))
        for change, words in (({'titre': 'Gourde'}, 'une autre scène 3D y répond déjà'),
                              ({'titre': ' « » '}, 'le nouveau nom est vide'), ({'titre': 'x'}, 'ne peut pas servir de nom'),
                              ({'debout': 'w'}, 'réglage refusé'), ({'quart': 7}, 'réglage refusé'),
                              ({'quart': 1.5}, 'réglage refusé')):
            self.assertIn(words, lab.adjust_model(sid, **change)[1] or '', change)
        self.assertEqual(lab.load_scene(sid), named)
        self.assertEqual(lab.adjust_model(sid), (named, None))             # nothing asked: nothing written
        for bad in ('bras_robotique', 'imp_absent', '../imp_x', 12):
            self.assertIsNone(lab.adjust_model(bad, titre='Robot')[0], bad)
        self.assertEqual(lab.load_scene('bras_robotique')['titre'], 'Bras robotique à 4 axes')

    def test_the_words_that_stand_up_turn_and_rename_a_kept_model(self):
        scene, _, _ = lab.import_model('gourde.stl', stl(4))
        stand, turn = {'action': 'redresser'}, {'action': 'tourner'}
        for text, order in (
                ('redresse-le', stand), ('Il est couché', stand), ('mets-le debout', stand), ('retourne-le', stand),
                ('remets le modèle à l’endroit', stand),
                ('tourne-le', turn), ('fais-le tourner d’un quart de tour', turn), ('montre-moi l’autre côté', turn),
                ('renomme-le en « Veste VITA »', {'action': 'renommer', 'titre': 'Veste VITA'}),
                ('Renomme le modèle : Bidon d’eau', {'action': 'renommer', 'titre': "Bidon d'eau"}),   # as AURORA reads it
                ('appelle-le Veste VITA v2', {'action': 'renommer', 'titre': 'Veste VITA v2'}),
                ('renomme la gourde en bidon', {'action': 'renommer', 'titre': 'bidon'}),
                ('AURORA, renomme-le en Montre 3D', {'action': 'renommer', 'titre': 'Montre 3D'}),
                ('renomme-le', {'action': 'renommer', 'sans_titre': True}),
                ('renomme-le en Veste et gilet', {'action': 'renommer', 'titre': 'Veste et gilet'}),
                ('renomme-le en Veste et redresse-le', {'action': 'renommer', 'titre': 'Veste', 'suite': [stand]}),
                ('fais-le tourner', {'action': 'animer'}),                 # still an animation
                ('retourne au noyau', {'action': 'noyau'}), ('recentre la vue', {'action': 'recentrer'})):
            self.assertEqual(extract_scene_order(text, scene), order, text)
        arm = lab.load_scene('bras_robotique')   # on any other scene these words keep their usual meaning
        for text in ('redresse-le', 'tourne-le', 'renomme-le en Robot', 'il est couché'):
            self.assertIsNone(extract_scene_order(text, arm), text)

    def test_answers_when_a_kept_model_is_stood_up_or_renamed(self):
        scene, _, _ = lab.import_model('gourde.stl', stl(4))
        lab.import_model('drone.stl', stl(4, seed=1))
        sid, result = scene['id'], {'ok': True, 'data': scene}
        text, ui = compose.scene_view({'scene': sid, 'action': 'redresser'}, result)
        self.assertIn('Je redresse « Gourde » : je le pose dans une autre position (il y en a six).', text)
        self.assertEqual(ui, {'navigate': 'home', 'scene': {'scene': sid, 'action': 'redresser'}})
        text, ui = compose.scene_view({'scene': sid, 'action': 'tourner'}, result)
        self.assertIn('Je tourne « Gourde » d’un quart de tour sur lui-même.', text)
        self.assertEqual(ui['scene'], {'scene': sid, 'action': 'tourner'})
        # 11/11/2026: « anime-le » turns a kept model on its stand (one block, no joint), and says what it is.
        text, ui = compose.scene_view({'scene': sid, 'action': 'animer'}, result)
        self.assertIn('Je fais tourner « Gourde » sur son socle, comme sur un présentoir.', text)
        self.assertIn('ce n’est pas le mouvement de l’objet', text)
        self.assertEqual(ui['scene'], {'scene': sid, 'action': 'animer'})
        text, ui = compose.scene_view({'scene': sid, 'action': 'renommer', 'titre': 'bidon d’eau'}, result)
        self.assertEqual(text, 'Je renomme « Gourde » en « Bidon d’eau ». Pour le rouvrir plus tard : « montre-moi '
                               'bidon d’eau ».')
        self.assertEqual(ui['scene'], {'scene': sid, 'action': 'renommer', 'titre': 'Bidon d’eau'})
        for order, words in (({'action': 'renommer', 'sans_titre': True}, 'Quel nom donner à « Gourde » ?'),
                             ({'action': 'renommer', 'titre': 'gourde'}, '« Gourde » porte déjà ce nom.'),
                             ({'action': 'renommer', 'titre': 'Drone'}, '« Gourde » garde son nom : « Drone » ne peut '
                                                                        'pas servir de nom')):
            text, ui = compose.scene_view({'scene': sid, **order}, result)
            self.assertIn(words, text, order)
            self.assertIsNone(ui, order)                                   # nothing is asked of the interface
        # Several commands in one sentence: once renamed, the model is called by its new name.
        text, ui = compose.scene_view({'scene': sid, 'action': 'renommer', 'titre': 'bidon', 'suite': [
            {'action': 'redresser'}]}, result)
        self.assertIn('Je renomme « Gourde » en « Bidon ».', text)
        self.assertIn('Je redresse « Bidon » :', text)
        self.assertEqual(ui['scene'], {'scene': sid, 'action': 'renommer', 'titre': 'Bidon',
                                       'suite': [{'action': 'redresser'}]})
        arm = {'ok': True, 'data': lab.load_scene('bras_robotique')}
        text, ui = compose.scene_view({'scene': 'bras_robotique', 'action': 'redresser'}, arm)
        self.assertIn('n’est pas un modèle que vous avez donné', text)
        self.assertIsNone(ui)
        self.assertFalse((lab.models_dir() / 'imp_gourde.json.tmp').exists())
        self.assertEqual(lab.load_scene(sid), scene)                       # answering writes nothing


class RemovedSceneTests(unittest.TestCase):
    """11/11/2026 (ID Studio IA : « retirer un modèle gardé depuis l’interface »): a kept scene is set aside, never deleted."""

    def setUp(self):
        self.env = TempEnv().__enter__()   # the scenes and the quarantine go to a temporary AURORA_HOME
        lab.clear_cache()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_a_kept_model_leaves_the_catalogue_and_its_files_are_set_aside(self):
        data = stl(12)
        lab.import_model('gourde.stl', data)
        self.assertIn('imp_gourde', [s['id'] for s in lab.catalogue()])
        gone, why = lab.remove_scene('imp_gourde')
        self.assertIsNone(why)
        self.assertEqual((gone['id'], gone['titre'], gone['origine'], gone['fichiers']),
                         ('imp_gourde', 'Gourde', 'importe', ['imp_gourde.json', 'imp_gourde.stl']))
        self.assertNotIn('imp_gourde', [s['id'] for s in lab.catalogue()])
        self.assertEqual(list(lab.models_dir().iterdir()), [])
        with self.assertRaises(base.ToolError):
            lab.load_scene('imp_gourde')
        aside = Path(gone['mis_de_cote'])
        self.assertEqual(aside.parent, lab.removed_dir())
        self.assertTrue(str(aside).startswith(str(self.env.dir)))          # never the user's ~/.aurora
        self.assertEqual((aside / 'imp_gourde.stl').read_bytes(), data)    # moved, not deleted
        # The same file given again is a scene again; removed again, it never overwrites the first one set aside.
        lab.import_model('gourde.stl', data)
        again, why = lab.remove_scene('imp_gourde')
        self.assertIsNone(why)
        self.assertNotEqual(again['mis_de_cote'], gone['mis_de_cote'])
        self.assertEqual(len(list(lab.removed_dir().iterdir())), 2)
        # Put back by hand, the scene is there again.
        for kept in Path(again['mis_de_cote']).iterdir():
            os.replace(kept, lab.models_dir() / kept.name)
        self.assertEqual(lab.load_scene('imp_gourde')['titre'], 'Gourde')

    def test_a_generated_scene_is_set_aside_too(self):
        scene, why = lab.build_generated('```json\n' + json.dumps(motor(), ensure_ascii=False) + '\n```',
                                         'un moteur électrique', 'l’IA locale (qwen3.5:9b)')
        self.assertIsNone(why)
        gone, why = lab.remove_scene(scene['id'])
        self.assertEqual((why, gone['origine'], gone['fichiers']), (None, 'genere', ['gen_moteur_electrique.json']))
        self.assertEqual(lab._generated_files(), [])
        self.assertTrue((Path(gone['mis_de_cote']) / 'gen_moteur_electrique.json').is_file())

    def test_what_may_not_be_removed_is_said_and_nothing_moves(self):
        for scene_id, words in (('bras_robotique', 'prédéfinie'), ('imp_absent', 'introuvable'),
                                ('gen_absent', 'introuvable'), ('../imp_x', 'invalide'), (12, 'invalide')):
            gone, why = lab.remove_scene(scene_id)
            self.assertIsNone(gone, scene_id)
            self.assertIn(words, why, scene_id)
        self.assertFalse(lab.removed_dir().exists())
        self.assertEqual(lab.load_scene('bras_robotique')['id'], 'bras_robotique')   # a predefined scene stays

    def test_all_or_nothing_when_the_disk_refuses(self):
        lab.import_model('gourde.stl', stl(12))
        real, calls = os.replace, []

        def flaky(source, target):
            calls.append(Path(source).name)
            if len(calls) == 2:            # the description went, the model file does not follow
                raise OSError('disque plein')
            return real(source, target)

        with mock.patch.object(lab.os, 'replace', side_effect=flaky), \
                self.assertLogs('aurora.tools.lab', level='WARNING'):
            gone, why = lab.remove_scene('imp_gourde')
        self.assertEqual((gone, why), (None, 'la scène n’a pas pu être mise de côté sur le disque'))
        self.assertEqual(sorted(p.name for p in lab.models_dir().iterdir()), ['imp_gourde.json', 'imp_gourde.stl'])
        self.assertEqual(list(lab.removed_dir().iterdir()), [])                       # no empty folder left behind
        self.assertEqual(lab.load_scene('imp_gourde')['titre'], 'Gourde')


class ImportedModelRouteTests(ApiTestCase):
    def test_the_route_that_removes_a_kept_scene(self):
        lab.clear_cache()
        binary = {'Content-Type': 'application/octet-stream'}
        kept = self.client.post('/api/lab/modeles?name=flacon.stl', content=stl(5, seed=7), headers=binary).json()
        scene_id = kept['id']
        self.assertEqual(self.client.delete(f'/api/lab/scenes/{scene_id}', headers=NO_TOKEN).status_code, 401)
        self.assertIn(scene_id, [s['id'] for s in self.client.get('/api/lab/scenes').json()['data']['scenes']])
        res = self.client.delete(f'/api/lab/scenes/{scene_id}')
        self.assertEqual(res.status_code, 200)
        gone = res.json()
        self.assertEqual((gone['id'], gone['titre'], gone['fichiers']),
                         (scene_id, kept['titre'], [f'{scene_id}.json', f'{scene_id}.stl']))
        self.assertTrue((Path(gone['mis_de_cote']) / f'{scene_id}.stl').is_file())   # set aside, not deleted
        self.assertNotIn(scene_id, [s['id'] for s in self.client.get('/api/lab/scenes').json()['data']['scenes']])
        again = self.client.delete(f'/api/lab/scenes/{scene_id}')
        self.assertEqual((again.status_code, again.json()['detail']), (404, 'Scène non retirée : scène introuvable.'))
        fixed = self.client.delete('/api/lab/scenes/bras_robotique')
        self.assertEqual(fixed.status_code, 422)
        self.assertIn('prédéfinie', fixed.json()['detail'])
        self.assertTrue(self.client.get('/api/lab/scenes/bras_robotique').json()['ok'])

    def test_routes(self):
        lab.clear_cache()
        binary = {'Content-Type': 'application/octet-stream'}
        res = self.client.post('/api/lab/modeles?name=gourde.stl', content=stl(4), headers=binary)
        self.assertEqual((res.status_code, res.json()), (200, {'id': 'imp_gourde', 'titre': 'Gourde', 'deja': False}))
        again = self.client.post('/api/lab/modeles?name=gourde.stl', content=stl(4), headers=binary)
        self.assertEqual(again.json()['deja'], True)
        got = self.client.get('/api/lab/modeles/imp_gourde.stl')
        self.assertEqual((got.status_code, got.content, got.headers['cache-control']), (200, stl(4), 'no-store'))
        scene = self.client.get('/api/lab/scenes/imp_gourde').json()
        self.assertEqual(scene['data']['pieces'][0]['forme']['fichier'], 'imp_gourde.stl')
        bad = self.client.post('/api/lab/modeles?name=faux.stl', content=b'rien', headers=binary)
        self.assertEqual((bad.status_code, bad.json()['detail']),
                         (422, 'Modèle 3D non ouvert : ce n’est pas un fichier STL lisible.'))
        with mock.patch.object(lab, 'MAX_MODEL_BYTES', 10):
            self.assertEqual(self.client.post('/api/lab/modeles?name=a.stl', content=stl(4), headers=binary).status_code,
                             413)
        for path in ('imp_absent.stl', 'imp_gourde.json', 'gourde.stl'):
            self.assertIn(self.client.get(f'/api/lab/modeles/{path}').status_code, (404, 422), path)
        self.assertEqual(self.client.get('/api/lab/modeles/imp_gourde.stl', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.post('/api/lab/modeles?name=a.stl', content=stl(4), headers=NO_TOKEN).status_code,
                         401)

    def test_the_route_that_stands_up_and_renames(self):
        lab.clear_cache()
        binary = {'Content-Type': 'application/octet-stream'}
        self.client.post('/api/lab/modeles?name=gourde.stl', content=stl(4), headers=binary)
        put = self.client.put('/api/lab/modeles/imp_gourde', json={'debout': 'x', 'quart': 1})
        self.assertEqual((put.status_code, put.json()), (200, {'id': 'imp_gourde', 'titre': 'Gourde'}))
        forme = self.client.get('/api/lab/scenes/imp_gourde').json()['data']['pieces'][0]['forme']
        self.assertEqual((forme['debout'], forme['quart']), ('x', 1))
        put = self.client.put('/api/lab/modeles/imp_gourde', json={'titre': 'bidon'})
        self.assertEqual((put.status_code, put.json()), (200, {'id': 'imp_gourde', 'titre': 'Bidon'}))
        self.assertEqual(self.client.get('/api/lab/scenes/imp_gourde').json()['data']['titre'], 'Bidon')
        self.assertEqual(self.client.get('/api/lab/modeles/imp_gourde.stl').content, stl(4))   # the file: untouched
        for path, body, words in (('imp_gourde', {}, 'Rien à régler'), ('imp_gourde', {'titre': 'x'}, 'non modifié'),
                                  ('imp_absent', {'titre': 'Veste'}, 'non modifié'), ('imp_gourde', {'debout': 'w'}, ''),
                                  ('imp_gourde', {'quart': 4}, ''), ('imp_gourde', {'titre': 'Veste', 'autre': 1}, ''),
                                  ('bras_robotique', {'titre': 'Robot'}, '')):
            res = self.client.put(f'/api/lab/modeles/{path}', json=body)
            self.assertEqual(res.status_code, 422, (path, body))
            self.assertIn(words, str(res.json().get('detail')), (path, body))
        self.assertEqual(self.client.put('/api/lab/modeles/imp_gourde', json={'titre': 'Veste'},
                                         headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.get('/api/lab/scenes/imp_gourde').json()['data']['titre'], 'Bidon')


if __name__ == '__main__':
    unittest.main()
