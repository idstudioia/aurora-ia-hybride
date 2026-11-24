"""« Photo vers 3D » with Pixal3D (10/11/2026, ID Studio IA: « Oui, branche-le »): providers/pixal3d_local.py, its runner
pixal3d_lanceur.py, the choice of the engine and the route /api/lab/photo.

The real program (trellis.cpp) is never started here: its folder is a temporary one with stand-in files, and the
three child processes are small stand-in scripts run by this Python (same arguments, same last line of JSON as the
real ones). The runner's own functions are tried for real, in TripoSR's Python environment, on shapes made on the
spot: no model is loaded, no photo is read.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora.providers import pixal3d_local, triposr_local
from aurora.tools import lab
from tests.test_api import ApiTestCase
from tests.test_lab import glb
from tests.test_triposr_local import JPEG, OBJ, stand_in_folder

# The two Python steps. What they do is chosen by a word in the « photo », which the stand-ins pass along.
RUNNER = r'''
import json, sys
step = sys.argv[1]
if step == 'preparer':
    photo, image = sys.argv[2:4]
    data = open(photo, 'rb').read()
    if b'VIDE' in data:
        print(json.dumps({'ok': False, 'erreur': 'aucun objet n’a été trouvé sur la photo'}))
        sys.exit(1)
    if b'PANNE' in data:
        print(json.dumps({'ok': False, 'panne': 'RuntimeError: boum'}))
        sys.exit(1)
    open(image, 'wb').write(data)
    print('un avertissement de bibliothèque { pas du JSON')
    print(json.dumps({'ok': True, 'couverture': 0.26, 'detoure': True, 'detourage_s': 0.1}))
else:
    raw, shape, faces = sys.argv[2:5]
    data = open(raw, 'rb').read()
    if b'SANSFORME' in data:
        print(json.dumps({'ok': False, 'erreur': 'Pixal3D n’a tiré aucune forme de cette photo'}))
        sys.exit(1)
    open(shape, 'wb').write(data + ('visees %s\n' % faces).encode())
    print(json.dumps({'ok': True, 'faces_avant': 1000, 'triangles': 10, 'sommets': 8, 'grille': 64,
                      'aplomb_deg': 12.5, 'finition_s': 0.1, 'autre': 'non repris'}))
'''

# The program: it writes in its « model » what it was given, for the test to read.
PROGRAM = r'''
import os, sys, time
image, raw = sys.argv[1:3]
data = open(image, 'rb').read()
if b'LENT' in data:
    time.sleep(30)
if b'CASSE' in data:
    sys.stderr.write('ggml_vulkan: boum\n')
    sys.exit(3)
if b'MUET' in data:
    sys.exit(0)
leaks = sorted(k for k in os.environ if k.upper().startswith('AURORA_') or 'KEY' in k.upper())
rest = [os.path.basename(a) if os.sep in a else a for a in sys.argv[3:]]
with open(raw, 'w', encoding='utf-8', newline='\n') as out:
    out.write('fuite %s\n' % leaks)
    out.write('reseau %s %s\n' % (os.environ.get('HF_HUB_OFFLINE'), os.environ.get('HTTPS_PROXY')))
    out.write('carte %s\n' % os.environ.get('GGML_VK_DISABLE_INTEGER_DOT_PRODUCT'))
    out.write('appel %s\n' % ' '.join([os.path.basename(image), os.path.basename(raw)] + rest))
    out.write('dossier %s\n' % os.path.basename(os.getcwd()))
    if b'SANSFORME' in data:
        out.write('SANSFORME\n')
print('[7/7] write')
'''

# Run by TripoSR's own Python (trimesh is not in AURORA's environment), on the real runner. A pebble of 81 920 faces
# whose top leans 30 degrees toward the viewer, written in the program's own frame; a small ball; three images.
RUNNER_CHECK = r'''
import importlib.util, json, sys, tempfile
from pathlib import Path
import numpy as np
import trimesh
from PIL import Image, ImageDraw
spec = importlib.util.spec_from_file_location('lanceur', sys.argv[1])
lanceur = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lanceur)
work = Path(tempfile.mkdtemp())
pose = np.array(lanceur.POSE, dtype=float)

pebble = trimesh.creation.icosphere(subdivisions=6)
c, s = np.cos(np.radians(30)), np.sin(np.radians(30))
lean = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])            # the top (0, 1, 0) goes to (0, c, s)
pebble.vertices = (pebble.vertices * [1.0, 0.3, 0.6]) @ lean.T @ pose   # « @ pose »: back to the program's frame
pebble.export(str(work / 'galet.glb'), file_type='glb')
said = lanceur.finish(work / 'galet.glb', work / 'galet-fini.glb', 30000)
made = trimesh.load(str(work / 'galet-fini.glb'), force='mesh', process=False)

ball = trimesh.creation.icosphere(subdivisions=2)
ball.export(str(work / 'bille.glb'), file_type='glb')
round_ = lanceur.finish(work / 'bille.glb', work / 'bille-finie.glb', 30000)

Image.new('RGB', (64, 64), 'white').save(work / 'unie.png')
cut = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
ImageDraw.Draw(cut).rectangle((20, 30, 59, 79), fill=(200, 30, 30, 255))     # 40 x 50 points of 10 000
cut.save(work / 'detouree.png')
plain = lanceur.prepare(work / 'unie.png', work / 'unie-sortie.png')
kept = lanceur.prepare(work / 'detouree.png', work / 'detouree-sortie.png')
print(json.dumps({
    'galet': said, 'boite': [round(float(x), 2) for x in made.extents], 'faces': int(len(made.faces)),
    'bille': round_, 'unie': plain, 'unie_ecrite': (work / 'unie-sortie.png').exists(), 'detouree': kept,
    'detouree_mode': Image.open(work / 'detouree-sortie.png').mode, 'grilles': list(lanceur.GRIDS)}))
'''


def stand_in_pixal3d(root: Path) -> dict:
    """Stand-ins for Pixal3D's program and models and for TripoSR's environment (never run); the environment that
    points to them."""
    env = stand_in_folder(root / 'triposr')
    (root / 'pixal3d' / 'programme').mkdir(parents=True)
    (root / 'pixal3d' / 'programme' / 'trellis-cli.exe').write_bytes(b'')
    (root / 'pixal3d' / 'modeles').mkdir()
    for name in pixal3d_local._MODELS:
        (root / 'pixal3d' / 'modeles' / name).write_bytes(b'')
    return {**env, 'AURORA_PIXAL3D_DIR': str(root / 'pixal3d')}


class MissingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, stand_in_pixal3d(self.root))
        patch.start()
        self.addCleanup(patch.stop)

    def test_everything_there_and_what_is_missing_is_said(self):
        self.assertIsNone(pixal3d_local.missing())
        (self.root / 'triposr' / 'u2net' / 'u2net.onnx').unlink()
        self.assertIn('modèle de détourage absent', pixal3d_local.missing())
        (self.root / 'triposr' / 'venv' / 'Scripts' / 'python.exe').unlink()
        self.assertIn('environnement Python de TripoSR introuvable', pixal3d_local.missing())
        self.assertIn('Pixal3D s’en sert pour le détourage', pixal3d_local.missing())
        (self.root / 'pixal3d' / 'modeles' / 'pixal3d_shape_flow_512.gguf').unlink()
        self.assertIn('pixal3d_shape_flow_512.gguf', pixal3d_local.missing())
        self.assertIn('rien n’est téléchargé sans votre accord', pixal3d_local.missing())
        (self.root / 'pixal3d' / 'programme' / 'trellis-cli.exe').unlink()
        self.assertIn('programme de Pixal3D introuvable', pixal3d_local.missing())

    def test_without_pixal3d_nothing_is_started(self):
        with mock.patch.dict(os.environ, {'AURORA_PIXAL3D_DIR': str(self.root / 'absent')}), \
                mock.patch.object(pixal3d_local.subprocess, 'run', side_effect=AssertionError('lancé')):
            with self.assertRaisesRegex(pixal3d_local.TripoError, 'Pixal3D indisponible sur ce PC') as caught:
                pixal3d_local.reconstruct(JPEG, 'jpeg')
        self.assertEqual(caught.exception.status, 503)
        self.assertFalse(triposr_local.busy())


class ReconstructTests(unittest.TestCase):
    """The plumbing of one run, with the three stand-in children: arguments, environment, answer, cleaning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        runner, program = root / 'faux_lanceur.py', root / 'faux_programme.py'
        runner.write_text(RUNNER, encoding='utf-8')
        program.write_text(PROGRAM, encoding='utf-8')
        self.work = root / 'travail'
        self.work.mkdir()
        real = tempfile.mkdtemp
        for p in (mock.patch.dict(os.environ, {**stand_in_pixal3d(root), 'AURORA_ESSAI_SECRET': 'x',
                                               'MA_CLE_API_KEY': 'y'}),
                  mock.patch.object(triposr_local, '_paths', return_value=(Path(sys.executable), root / 'TripoSR')),
                  mock.patch.object(pixal3d_local, '_RUNNER', runner),
                  mock.patch.object(pixal3d_local, '_program', return_value=[sys.executable, str(program)]),
                  mock.patch.object(pixal3d_local, 'missing', return_value=None),
                  mock.patch.object(pixal3d_local.tempfile, 'mkdtemp',
                                    side_effect=lambda prefix: real(prefix=prefix, dir=str(self.work)))):
            p.start()
            self.addCleanup(p.stop)

    def clean(self):
        self.assertEqual(list(self.work.iterdir()), [])      # the photo and the shapes are not left behind
        self.assertFalse(triposr_local.busy())

    def test_a_photo_gives_a_shape_and_its_measures(self):
        made = pixal3d_local.reconstruct(JPEG, 'jpeg')
        text = made.pop('modele').decode('utf-8')
        self.assertGreater(made.pop('secondes'), 0)            # measured around the run, not told by the children
        self.assertGreaterEqual(made.pop('calcul_s'), 0)
        self.assertEqual(made, {'triangles': 10, 'sommets': 8, 'faces_avant': 1000, 'grille': 64, 'aplomb_deg': 12.5,
                                'finition_s': 0.1, 'format': 'glb', 'moteur': pixal3d_local.ENGINE, 'nom': 'Pixal3D',
                                'couverture': 0.26, 'detourage_s': 0.1})
        self.assertIn('fuite []', text)                        # no AURORA variable, nothing that looks like a key
        self.assertIn('reseau 1 http://127.0.0.1:9', text)     # the closed environment of TripoSR's runner
        self.assertIn('carte 1', text)                         # the way round for the RX 6000 cards
        self.assertIn('appel image.png brut.glb --models modeles --model pixal3d --res 512 --seed 42 --backend '
                      'Vulkan --require-gpu --verbose\n', text)
        self.assertIn('dossier programme', text)
        self.assertTrue(text.endswith('visees 320000\n'))      # ID Studio IA: « passe plutôt à 320000 faces »
        self.clean()

    def test_a_photo_that_gives_nothing_says_why(self):
        with self.assertRaisesRegex(pixal3d_local.TripoError, 'Aucun modèle 3D tiré de cette photo : aucun objet '
                                                              'n’a été trouvé sur la photo.') as caught:
            pixal3d_local.reconstruct(JPEG + b'VIDE', 'jpeg')
        self.assertEqual(caught.exception.status, 422)
        self.clean()

    def test_a_step_that_breaks_is_said_without_its_trace(self):
        for word, step in ((b'PANNE', 'le détourage'), (b'CASSE', 'le calcul de la forme'),
                           (b'MUET', 'le calcul de la forme')):
            with self.assertLogs(pixal3d_local.log, 'WARNING') as logs:
                with self.assertRaises(pixal3d_local.TripoError) as caught:
                    pixal3d_local.reconstruct(JPEG + word, 'jpeg')
            self.assertEqual((str(caught.exception), caught.exception.status),
                             (f'Pixal3D s’est arrêté pendant {step} sans donner de modèle (détail dans le journal du '
                              'service).', 503))
            if word != b'MUET':
                self.assertIn('boum', logs.output[0])
            self.assertNotIn('une photo d essai', logs.output[0])   # the photo is never logged
            self.clean()
        # A raw model without a shape: not this photo's fault for sure, so TripoSR will be tried (503).
        with self.assertRaisesRegex(pixal3d_local.TripoError, 'Pixal3D n’a tiré aucune forme de cette photo') as caught:
            pixal3d_local.reconstruct(JPEG + b'SANSFORME', 'jpeg')
        self.assertEqual(caught.exception.status, 503)
        self.clean()

    def test_a_run_too_long_is_stopped(self):
        with mock.patch.object(pixal3d_local, 'TIMEOUT_S', 1.0):
            with self.assertRaisesRegex(pixal3d_local.TripoError, 'n’a pas fini le calcul de la forme en 1 s : il a '
                                                                  'été arrêté') as caught:
                pixal3d_local.reconstruct(JPEG + b'LENT', 'jpeg')
        self.assertEqual(caught.exception.status, 503)
        self.clean()

    def test_one_photo_at_a_time_and_only_a_checked_image(self):
        with mock.patch.object(pixal3d_local.subprocess, 'run', side_effect=AssertionError('lancé')):
            self.assertTrue(triposr_local._lock.acquire(blocking=False))   # TripoSR at work: the same turn
            try:
                with self.assertRaisesRegex(pixal3d_local.TripoError, 'déjà en cours de reconstruction') as caught:
                    pixal3d_local.reconstruct(JPEG, 'jpeg')
                self.assertEqual(caught.exception.status, 409)
                self.assertTrue(triposr_local.busy())
            finally:
                triposr_local._lock.release()
            for image, kind in ((JPEG, 'gif'), (b'', 'jpeg')):
                with self.assertRaisesRegex(pixal3d_local.TripoError, 'Photo non lue : une image PNG ou JPEG'):
                    pixal3d_local.reconstruct(image, kind)
        self.clean()


class ChoiceTests(unittest.TestCase):
    """Which engine rebuilds the photo: Pixal3D first, TripoSR as the way out (ID Studio IA, 29/09/2026: the former system
    is not removed)."""

    def setUp(self):
        self.asked: list[str] = []
        self.fine = {'modele': glb(), 'format': 'glb', 'moteur': pixal3d_local.ENGINE, 'nom': 'Pixal3D',
                     'secondes': 196.4, 'triangles': 279440}

        def rough(image: bytes, kind: str) -> dict:
            self.asked.append('TripoSR')
            return {'obj': OBJ, 'secondes': 31.4, 'triangles': 1, 'lissage': 10}

        patch = mock.patch.object(triposr_local, 'reconstruct', side_effect=rough)
        patch.start()
        self.addCleanup(patch.stop)

    def pixal3d(self, missing: str | None, outcome):
        def run(image: bytes, kind: str) -> dict:
            self.asked.append('Pixal3D')
            if isinstance(outcome, Exception):
                raise outcome
            return dict(outcome)
        return mock.patch.multiple(pixal3d_local, missing=mock.Mock(return_value=missing),
                                   reconstruct=mock.Mock(side_effect=run))

    def test_pixal3d_first_when_it_is_installed(self):
        with self.pixal3d(None, self.fine):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg')
        self.assertEqual((self.asked, made), (['Pixal3D'], {**self.fine, 'repli': None}))

    def test_triposr_when_pixal3d_is_not_installed(self):
        with self.pixal3d('programme de Pixal3D introuvable', self.fine):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg')
        self.assertEqual(self.asked, ['TripoSR'])
        self.assertEqual(made, {'modele': OBJ, 'format': 'obj', 'moteur': triposr_local.ENGINE, 'nom': 'TripoSR',
                                'repli': None, 'secondes': 31.4, 'triangles': 1, 'lissage': 10})

    def test_triposr_takes_over_when_pixal3d_could_not_run_and_says_why(self):
        why = 'Pixal3D s’est arrêté pendant le calcul de la forme sans donner de modèle (détail dans le journal du service).'
        with self.pixal3d(None, pixal3d_local.TripoError(why, 503)):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg')
        self.assertEqual(self.asked, ['Pixal3D', 'TripoSR'])
        self.assertEqual((made['nom'], made['format'], made['modele'], made['repli']), ('TripoSR', 'obj', OBJ, why))

    def test_a_photo_that_gives_nothing_or_a_busy_turn_is_not_tried_twice(self):
        for status in (422, 409):
            self.asked.clear()
            with self.pixal3d(None, pixal3d_local.TripoError('refus', status)):
                with self.assertRaisesRegex(pixal3d_local.TripoError, '^refus$') as caught:
                    pixal3d_local.photo_to_shape(JPEG, 'jpeg')
            self.assertEqual((self.asked, caught.exception.status), (['Pixal3D'], status))

    def test_when_both_fail_both_reasons_are_said(self):
        with self.pixal3d(None, pixal3d_local.TripoError('Pixal3D n’a pas pu.', 503)), \
                mock.patch.object(triposr_local, 'reconstruct',
                                  side_effect=triposr_local.TripoError('TripoSR introuvable.', 503)):
            with self.assertRaises(pixal3d_local.TripoError) as caught:
                pixal3d_local.photo_to_shape(JPEG, 'jpeg')
        self.assertEqual((str(caught.exception), caught.exception.status),
                         ('Pixal3D n’a pas pu. TripoSR, essayé ensuite : TripoSR introuvable.', 503))

    # 11/11/2026 (ID Studio IA : « une petite icône pour sélectionner le moteur ») : « rapide » met TripoSR en premier.
    def test_the_detailed_choice_is_the_default_one(self):
        with self.pixal3d(None, self.fine):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'detaille')
        self.assertEqual((self.asked, made), (['Pixal3D'], {**self.fine, 'repli': None}))
        self.assertEqual(pixal3d_local.CHOICES, ('detaille', 'rapide'))

    def test_the_fast_choice_asks_triposr_alone(self):
        with self.pixal3d(None, self.fine):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'rapide')
        self.assertEqual(self.asked, ['TripoSR'])                         # Pixal3D is installed, and left alone
        self.assertEqual((made['nom'], made['format'], made['modele'], made['repli']), ('TripoSR', 'obj', OBJ, None))

    def test_pixal3d_takes_over_when_the_fast_engine_could_not_run_and_says_why(self):
        why = 'TripoSR n’a pas fini en 600 s : il a été arrêté.'
        with self.pixal3d(None, self.fine), \
                mock.patch.object(triposr_local, 'reconstruct', side_effect=triposr_local.TripoError(why, 503)):
            made = pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'rapide')
        self.assertEqual(self.asked, ['Pixal3D'])
        self.assertEqual(made, {**self.fine, 'repli': why})

    def test_the_fast_choice_says_what_triposr_said_when_pixal3d_is_not_installed(self):
        with self.pixal3d('programme de Pixal3D introuvable', self.fine), \
                mock.patch.object(triposr_local, 'reconstruct',
                                  side_effect=triposr_local.TripoError('TripoSR introuvable.', 503)):
            with self.assertRaisesRegex(pixal3d_local.TripoError, '^TripoSR introuvable\\.$') as caught:
                pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'rapide')
        self.assertEqual((self.asked, caught.exception.status), ([], 503))

    def test_the_fast_choice_does_not_try_twice_a_photo_that_gives_nothing(self):
        for status in (422, 409):
            with self.pixal3d(None, self.fine), \
                    mock.patch.object(triposr_local, 'reconstruct',
                                      side_effect=triposr_local.TripoError('refus', status)):
                with self.assertRaisesRegex(pixal3d_local.TripoError, '^refus$') as caught:
                    pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'rapide')
            self.assertEqual((self.asked, caught.exception.status), ([], status))

    def test_when_both_fail_on_the_fast_choice_both_reasons_are_said_in_their_order(self):
        with self.pixal3d(None, pixal3d_local.TripoError('Pixal3D n’a pas pu.', 503)), \
                mock.patch.object(triposr_local, 'reconstruct',
                                  side_effect=triposr_local.TripoError('TripoSR introuvable.', 503)):
            with self.assertRaises(pixal3d_local.TripoError) as caught:
                pixal3d_local.photo_to_shape(JPEG, 'jpeg', 'rapide')
        self.assertEqual((str(caught.exception), caught.exception.status),
                         ('TripoSR introuvable. Pixal3D, essayé ensuite : Pixal3D n’a pas pu.', 503))


@unittest.skipUnless(triposr_local.available(), 'TripoSR n’est pas installé sur ce PC')
class RunnerTests(unittest.TestCase):
    """The real runner's own steps, in TripoSR's Python environment: a few seconds, no model, no program."""

    def test_the_runner_poses_lightens_and_levels_a_shape_and_keeps_a_cut_out(self):
        python, _depot = triposr_local._paths()
        done = subprocess.run([str(python), '-I', '-c', RUNNER_CHECK, str(pixal3d_local._RUNNER)],
                              env=triposr_local._environment(), stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=180, check=False)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace')[-800:])
        seen = triposr_local._last_json(done.stdout)
        pebble = seen['galet']
        self.assertEqual((pebble['ok'], pebble['faces_avant']), (True, 81920))
        self.assertLessEqual(pebble['triangles'], 30000)                 # lightened under what was asked
        self.assertEqual(pebble['triangles'], seen['faces'])             # and what is said is what is written
        self.assertIn(pebble['grille'], seen['grilles'])
        self.assertTrue(29 < pebble['aplomb_deg'] < 31, pebble)          # it leaned by 30 degrees
        for got, want in zip(seen['boite'], (2.0, 0.6, 1.2)):            # upright: its top is the vertical again
            self.assertAlmostEqual(got, want, delta=0.04, msg=seen['boite'])
        ball = seen['bille']                                             # light and round: left as it is
        self.assertEqual((ball['ok'], ball['faces_avant'], ball['triangles'], ball['grille'], ball['aplomb_deg']),
                         (True, 320, 320, 0, None))
        self.assertEqual((seen['unie'], seen['unie_ecrite']),            # refused before any model, in TripoSR's words
                         ({'ok': False, 'erreur': 'la photo est unie, il n’y a aucun objet à reconstruire'}, False))
        kept = seen['detouree']                                          # a background already removed is kept
        self.assertEqual((kept['ok'], kept['detoure'], kept['couverture'], seen['detouree_mode']),
                         (True, False, 0.2, 'RGBA'))


class PhotoRouteTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        lab.clear_cache()
        self.binary = {'Content-Type': 'application/octet-stream'}
        self.fine = {'modele': glb(), 'format': 'glb', 'moteur': pixal3d_local.ENGINE, 'nom': 'Pixal3D',
                     'secondes': 196.4, 'couverture': 0.354, 'triangles': 279440, 'sommets': 99294,
                     'faces_avant': 1514724, 'aplomb_deg': 45.5}

    def test_a_photo_becomes_a_pixal3d_scene(self):
        with mock.patch.multiple(pixal3d_local, missing=mock.Mock(return_value=None),
                                 reconstruct=mock.Mock(return_value=dict(self.fine))), \
                mock.patch.object(triposr_local, 'reconstruct', side_effect=AssertionError('TripoSR lancé')):
            res = self.client.post('/api/lab/photo', params={'name': 'Satellite.jpg'}, content=JPEG, headers=self.binary)
        self.assertEqual((res.status_code, res.json()), (200, {
            'id': 'imp_satellite', 'titre': 'Satellite', 'deja': False, 'secondes': 196.4, 'triangles': 279440,
            'couverture': 0.354, 'moteur': 'Pixal3D', 'repli': None}))
        scene = self.client.get('/api/lab/scenes/imp_satellite').json()['data']
        self.assertEqual(scene['origine'], 'reconstruit')
        self.assertIn('par une IA (Pixal3D)', scene['avertissement'])
        piece, = scene['pieces']
        self.assertEqual(piece['forme'], {'type': 'maillage', 'fichier': 'imp_satellite.glb', 'taille': 2.0,
                                          'debout': 'y'})
        self.assertEqual({p['nom']: p['valeur'] for p in piece['proprietes']},
                         {'Photo d’origine': 'Satellite.jpg', 'Reconstruit par': pixal3d_local.ENGINE,
                          'Durée de la reconstruction': '196 s', 'Part de la photo prise pour l’objet': '35 %',
                          'Triangles': '279 440', 'Allègement': '1 514 724 faces à l’origine, ramenées à 279 440',
                          'Aplomb': 'forme redressée de 46° après la reconstruction'})
        self.assertEqual(self.client.get('/api/lab/modeles/imp_satellite.glb').content, glb())
        self.assertEqual(sorted(p.name for p in lab.models_dir().iterdir()),
                         ['imp_satellite.glb', 'imp_satellite.json'])     # the photo itself is not kept

    def test_when_pixal3d_breaks_triposr_makes_the_shape_and_the_answer_says_so(self):
        why = 'Pixal3D n’a pas fini le calcul de la forme en 900 s : il a été arrêté, et aucun modèle n’est gardé.'
        with mock.patch.multiple(pixal3d_local, missing=mock.Mock(return_value=None),
                                 reconstruct=mock.Mock(side_effect=pixal3d_local.TripoError(why, 503))), \
                mock.patch.object(triposr_local, 'reconstruct',
                                  return_value={'obj': OBJ, 'secondes': 31.4, 'triangles': 1, 'couverture': 0.31,
                                                'lissage': 10}):
            res = self.client.post('/api/lab/photo?name=avion.jpg', content=JPEG, headers=self.binary)
        self.assertEqual((res.status_code, res.json()), (200, {
            'id': 'imp_avion', 'titre': 'Avion', 'deja': False, 'secondes': 31.4, 'triangles': 1, 'couverture': 0.31,
            'moteur': 'TripoSR', 'repli': why}))
        scene = self.client.get('/api/lab/scenes/imp_avion').json()['data']
        self.assertIn('par une IA (TripoSR)', scene['avertissement'])
        self.assertEqual(scene['pieces'][0]['forme']['fichier'], 'imp_avion.obj')

    def test_what_pixal3d_refuses_is_said_as_it_is(self):
        words = 'Aucun modèle 3D tiré de cette photo : aucun objet n’a été trouvé sur la photo.'
        with mock.patch.multiple(pixal3d_local, missing=mock.Mock(return_value=None),
                                 reconstruct=mock.Mock(side_effect=pixal3d_local.TripoError(words, 422))), \
                mock.patch.object(triposr_local, 'reconstruct', side_effect=AssertionError('TripoSR lancé')):
            res = self.client.post('/api/lab/photo?name=a.jpg', content=JPEG, headers=self.binary)
        self.assertEqual((res.status_code, res.json()['detail']), (422, words))
        self.assertFalse(lab.models_dir().exists())

    def test_the_choice_of_the_person_reaches_the_engines(self):
        seen: list[tuple] = []

        def shape(image: bytes, kind: str, choice: str) -> dict:
            seen.append((kind, choice))
            return {'modele': OBJ, 'format': 'obj', 'moteur': triposr_local.ENGINE, 'nom': 'TripoSR', 'repli': None,
                    'secondes': 31.4, 'triangles': 1, 'couverture': 0.31, 'lissage': 10}

        with mock.patch.object(pixal3d_local, 'photo_to_shape', side_effect=shape):
            fast = self.client.post('/api/lab/photo', params={'name': 'velo.jpg', 'choix': 'rapide'}, content=JPEG,
                                    headers=self.binary)
            usual = self.client.post('/api/lab/photo?name=veste.jpg', content=JPEG, headers=self.binary)
            unknown = self.client.post('/api/lab/photo?name=a.jpg&choix=vite', content=JPEG, headers=self.binary)
        self.assertEqual((fast.status_code, fast.json()['moteur'], usual.status_code), (200, 'TripoSR', 200))
        self.assertEqual(seen, [('jpeg', 'rapide'), ('jpeg', 'detaille')])   # no choice given: the detailed one
        self.assertEqual((unknown.status_code, unknown.json()['detail']),    # and nothing starts on an unknown one
                         (422, 'Photo vers 3D : choix inconnu (« detaille » ou « rapide »). Aucun modèle 3D n’a été fait.'))


if __name__ == '__main__':
    unittest.main()
