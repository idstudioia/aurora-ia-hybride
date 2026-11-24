"""« Photo vers 3D » (10/11/2026): providers/triposr_local.py and the route /api/lab/photo.

The real TripoSR is never started here: its folder is a temporary one with stand-in files, and the child process is
a small stand-in script run by this Python (same arguments, same last line of JSON as triposr_lanceur.py).
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora.providers import pixal3d_local, triposr_local
from aurora.tools import lab
from tests.test_api import NO_TOKEN, ApiTestCase

JPEG = b'\xff\xd8\xff\xe0' + b'une photo d essai'
OBJ = b'# essai\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'

# What the stand-in does is chosen by a word in the « photo » it receives.
STAND_IN = r'''
import json, os, sys, time
depot, photo, shape, resolution = sys.argv[1:5]
data = open(photo, 'rb').read()
if b'LENT' in data:
    time.sleep(30)
if b'CASSE' in data:
    sys.stderr.write('Traceback: boum\n')
    sys.exit(3)
if b'PANNE' in data:
    print(json.dumps({'ok': False, 'panne': 'RuntimeError: boum'}))
    sys.exit(1)
if b'VIDE' in data:
    print(json.dumps({'ok': False, 'erreur': 'aucun objet n\u2019a \u00e9t\u00e9 trouv\u00e9 sur la photo'}))
    sys.exit(1)
leaks = sorted(k for k in os.environ if k.upper().startswith('AURORA_') or 'KEY' in k.upper())
with open(shape, 'w', encoding='utf-8') as out:
    out.write('# fuite %s\n' % leaks)
    out.write('# reseau %s %s\n' % (os.environ.get('HF_HUB_OFFLINE'), os.environ.get('HTTPS_PROXY')))
    out.write('# appel %s %s %s %s\n' % (os.path.basename(depot), os.path.basename(photo), os.path.basename(shape), resolution))
    out.write('# dossier %s\n' % (os.path.dirname(photo) == os.getcwd()))
    out.write('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
print('un avertissement de biblioth\u00e8que { pas du JSON')
print(json.dumps({'ok': True, 'triangles': 1, 'sommets': 3, 'couverture': 0.31, 'lissage': 10, 'chargement_s': 0.1,
                  'detourage_s': 0.1, 'modele_s': 0.1, 'maillage_s': 0.1, 'cubes': 'essai', 'fils': 1}))
'''

# Run by TripoSR's own Python (trimesh is not in AURORA's environment): a ball crumpled by noise, given to the real
# runner's smoothing. Its last line: the passes done, the folds before and after, the mean radius left.
SMOOTH_CHECK = r'''
import importlib.util, json, sys
import numpy as np
import trimesh
spec = importlib.util.spec_from_file_location('lanceur', sys.argv[1])
lanceur = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lanceur)
folds = lambda m: int((np.degrees(m.face_adjacency_angles) > 28).sum())
ball = trimesh.creation.icosphere(subdivisions=4)
ball.vertices = ball.vertices + np.random.default_rng(1).normal(0, 0.02, ball.vertices.shape)
before = folds(ball)
passes = lanceur._smooth(ball, lanceur.SMOOTH_PASSES)
untouched = trimesh.creation.icosphere(subdivisions=2)
kept = untouched.vertices.copy()
none = lanceur._smooth(untouched, 0)
print(json.dumps({'passes': passes, 'avant': before, 'apres': folds(ball),
                  'rayon': float(np.linalg.norm(ball.vertices, axis=1).mean()),
                  'sans': none, 'intact': bool((untouched.vertices == kept).all())}))
'''


def stand_in_folder(root: Path) -> dict:
    """Stand-ins for TripoSR's code, environment and models (never run); the environment that points to them."""
    (root / 'TripoSR' / 'tsr').mkdir(parents=True)
    (root / 'TripoSR' / 'tsr' / 'system.py').write_text('# stand-in', encoding='utf-8')
    (root / 'venv' / 'Scripts').mkdir(parents=True)
    (root / 'venv' / 'Scripts' / 'python.exe').write_bytes(b'')
    for name in triposr_local._MODELS:
        (root / 'hub' / name).mkdir(parents=True)
    (root / 'u2net').mkdir()
    (root / 'u2net' / 'u2net.onnx').write_bytes(b'')
    return {'AURORA_TRIPOSR_DIR': str(root), 'HF_HUB_CACHE': str(root / 'hub'), 'U2NET_HOME': str(root / 'u2net')}


class MissingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'triposr'
        patch = mock.patch.dict(os.environ, stand_in_folder(self.root))
        patch.start()
        self.addCleanup(patch.stop)

    def test_everything_there_and_what_is_missing_is_said(self):
        self.assertIsNone(triposr_local.missing())
        self.assertTrue(triposr_local.available())
        (self.root / 'u2net' / 'u2net.onnx').unlink()
        self.assertIn('modèle de détourage absent', triposr_local.missing())
        self.assertIn('rien n’est téléchargé sans votre accord', triposr_local.missing())
        (self.root / 'hub' / 'models--facebook--dino-vitb16').rmdir()
        self.assertIn('models--facebook--dino-vitb16', triposr_local.missing())
        self.assertIn('rien n’est téléchargé sans votre accord', triposr_local.missing())
        (self.root / 'venv' / 'Scripts' / 'python.exe').unlink()
        self.assertIn('environnement Python de TripoSR introuvable', triposr_local.missing())
        (self.root / 'TripoSR' / 'tsr' / 'system.py').unlink()
        self.assertIn('TripoSR introuvable', triposr_local.missing())
        self.assertFalse(triposr_local.available())

    def test_without_triposr_nothing_is_started(self):
        with mock.patch.dict(os.environ, {'AURORA_TRIPOSR_DIR': str(self.root / 'absent')}), \
                mock.patch.object(triposr_local.subprocess, 'run', side_effect=AssertionError('lancé')):
            with self.assertRaisesRegex(triposr_local.TripoError, 'Photo vers 3D indisponible sur ce PC') as caught:
                triposr_local.reconstruct(JPEG, 'jpeg')
        self.assertEqual(caught.exception.status, 503)
        self.assertFalse(triposr_local.busy())


class ReconstructTests(unittest.TestCase):
    """The plumbing of one run, with the stand-in child: arguments, environment, answer, cleaning."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        runner = root / 'faux_lanceur.py'
        runner.write_text(STAND_IN, encoding='utf-8')
        self.work = root / 'travail'
        self.work.mkdir()
        real = tempfile.mkdtemp
        for p in (mock.patch.object(triposr_local, '_paths', return_value=(Path(sys.executable), root / 'TripoSR')),
                  mock.patch.object(triposr_local, '_RUNNER', runner),
                  mock.patch.object(triposr_local, 'missing', return_value=None),
                  mock.patch.object(triposr_local.tempfile, 'mkdtemp',
                                    side_effect=lambda prefix: real(prefix=prefix, dir=str(self.work))),
                  mock.patch.dict(os.environ, {'AURORA_ESSAI_SECRET': 'x', 'MA_CLE_API_KEY': 'y'})):
            p.start()
            self.addCleanup(p.stop)

    def clean(self):
        self.assertEqual(list(self.work.iterdir()), [])      # the photo and the shape are not left behind
        self.assertFalse(triposr_local.busy())

    def test_a_photo_gives_a_shape_and_its_measures(self):
        made = triposr_local.reconstruct(JPEG, 'jpeg')
        text = made.pop('obj').decode('utf-8')
        self.assertGreater(made.pop('secondes'), 0)           # measured around the run, not told by the child
        self.assertEqual(made, {'triangles': 1, 'sommets': 3, 'couverture': 0.31, 'lissage': 10, 'chargement_s': 0.1,
                                'detourage_s': 0.1, 'modele_s': 0.1, 'maillage_s': 0.1})
        self.assertIn('# fuite []', text)                      # no AURORA variable, nothing that looks like a key
        self.assertIn('# reseau 1 http://127.0.0.1:9', text)   # the network is closed to the child
        self.assertIn(f'# appel TripoSR photo.jpg forme.obj {triposr_local.RESOLUTION}', text)
        self.assertIn('# dossier True', text)
        self.assertTrue(text.rstrip().endswith('f 1 2 3'))
        self.assertIn('# appel TripoSR photo.png', triposr_local.reconstruct(b'\x89PNG\r\n\x1a\n', 'png')['obj'].decode())
        self.clean()

    def test_a_photo_that_gives_nothing_says_why(self):
        with self.assertRaisesRegex(triposr_local.TripoError, 'Aucun modèle 3D tiré de cette photo : aucun objet '
                                                              'n’a été trouvé sur la photo.') as caught:
            triposr_local.reconstruct(JPEG + b'VIDE', 'jpeg')
        self.assertEqual(caught.exception.status, 422)
        self.clean()

    def test_a_run_that_breaks_is_said_without_its_trace(self):
        for word in (b'CASSE', b'PANNE'):
            with self.assertLogs(triposr_local.log, 'WARNING') as logs:
                with self.assertRaises(triposr_local.TripoError) as caught:
                    triposr_local.reconstruct(JPEG + word, 'jpeg')
            self.assertEqual((str(caught.exception), caught.exception.status),
                             ('TripoSR s’est arrêté sans donner de modèle (détail dans le journal du service).', 503))
            self.assertIn('boum', logs.output[0])
            self.assertNotIn('une photo d essai', logs.output[0])   # the photo is never logged
        self.clean()

    def test_a_run_too_long_is_stopped(self):
        with mock.patch.object(triposr_local, 'TIMEOUT_S', 1.0):
            with self.assertRaisesRegex(triposr_local.TripoError, 'n’a pas fini en 1 s : il a été arrêté') as caught:
                triposr_local.reconstruct(JPEG + b'LENT', 'jpeg')
        self.assertEqual(caught.exception.status, 503)
        self.clean()

    def test_one_photo_at_a_time_and_only_a_checked_image(self):
        with mock.patch.object(triposr_local.subprocess, 'run', side_effect=AssertionError('lancé')):
            self.assertTrue(triposr_local._lock.acquire(blocking=False))
            try:
                with self.assertRaisesRegex(triposr_local.TripoError, 'déjà en cours de reconstruction') as caught:
                    triposr_local.reconstruct(JPEG, 'jpeg')
                self.assertEqual(caught.exception.status, 409)
                self.assertTrue(triposr_local.busy())          # the run in progress keeps its turn
            finally:
                triposr_local._lock.release()
            for image, kind, words in ((JPEG, 'gif', 'PNG ou JPEG seulement'), (b'', 'jpeg', 'Photo vide ou trop'),
                                       (b'x' * 11, 'jpeg', 'Photo vide ou trop')):
                with mock.patch.object(triposr_local, 'MAX_PHOTO_BYTES', 10 if image else triposr_local.MAX_PHOTO_BYTES):
                    with self.assertRaisesRegex(triposr_local.TripoError, words):
                        triposr_local.reconstruct(image, kind)
        self.clean()


@unittest.skipUnless(triposr_local.available(), 'TripoSR n’est pas installé sur ce PC')
class SmoothingTests(unittest.TestCase):
    """ID Studio IA, 10/11/2026 (« fais le lissage »): the real runner's smoothing, in TripoSR's own environment (no model is
    loaded, no photo is read: a few seconds)."""

    def test_the_runner_smooths_a_crumpled_shape_and_keeps_its_size(self):
        import subprocess
        python, _depot = triposr_local._paths()
        done = subprocess.run([str(python), '-I', '-c', SMOOTH_CHECK, str(triposr_local._RUNNER)],
                              env=triposr_local._environment(), stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=120, check=False)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace')[-800:])
        seen = triposr_local._last_json(done.stdout)
        self.assertEqual(seen['passes'], 10)
        self.assertGreater(seen['avant'], 1000)                 # the ball really was crumpled
        self.assertLess(seen['apres'], seen['avant'] / 20)      # and no longer is
        self.assertTrue(0.9 < seen['rayon'] < 1.01, seen)       # it did not melt: its radius was 1
        self.assertEqual((seen['sans'], seen['intact']), (0, True))   # zero pass: the shape as it was


class PhotoRouteTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        lab.clear_cache()
        self.calls: list[tuple[bytes, str]] = []

        def fake(image: bytes, kind: str) -> dict:
            self.calls.append((image, kind))
            return {'obj': OBJ, 'secondes': 31.4, 'triangles': 1, 'sommets': 3, 'couverture': 0.31, 'lissage': 10}

        # Pixal3D runs first where it is installed (ID Studio IA's PC): these tests are about TripoSR alone, so it is said
        # absent here, and its real program can never start (tests/test_pixal3d_local.py has its own stand-ins).
        for patch in (mock.patch.object(triposr_local, 'reconstruct', side_effect=fake),
                      mock.patch.object(pixal3d_local, 'missing', return_value='absent pour cet essai')):
            patch.start()
            self.addCleanup(patch.stop)
        self.binary = {'Content-Type': 'application/octet-stream'}

    def test_a_photo_becomes_a_scene_marked_as_a_reconstruction(self):
        res = self.client.post('/api/lab/photo', params={'name': r'C:\Photos\Ma chaise.jpg'}, content=JPEG,
                               headers=self.binary)
        self.assertEqual((res.status_code, res.json()), (200, {
            'id': 'imp_ma_chaise', 'titre': 'Ma chaise', 'deja': False, 'secondes': 31.4, 'triangles': 1,
            'couverture': 0.31, 'moteur': 'TripoSR', 'repli': None}))
        self.assertEqual(self.calls, [(JPEG, 'jpeg')])
        scene = self.client.get('/api/lab/scenes/imp_ma_chaise').json()['data']
        self.assertEqual((scene['origine'], scene['nature'], scene['domaine']), ('reconstruit', 'illustration', 'objet'))
        self.assertIn('les côtés que la photo ne montre pas sont inventés', scene['avertissement'])
        self.assertIn('« Ma chaise.jpg »', scene['avertissement'])
        piece, = scene['pieces']
        self.assertEqual(piece['forme'], {'type': 'maillage', 'fichier': 'imp_ma_chaise.obj', 'taille': 2.0,
                                          'debout': 'y'})
        self.assertEqual({p['nom']: p['valeur'] for p in piece['proprietes']},
                         {'Photo d’origine': 'Ma chaise.jpg', 'Reconstruit par': triposr_local.ENGINE,
                          'Durée de la reconstruction': '31 s', 'Part de la photo prise pour l’objet': '31 %',
                          'Triangles': '1', 'Surface': 'lissée après la reconstruction (10 passes)'})
        self.assertEqual(self.client.get('/api/lab/modeles/imp_ma_chaise.obj').content, OBJ)
        self.assertEqual(sorted(p.name for p in lab.models_dir().iterdir()),
                         ['imp_ma_chaise.json', 'imp_ma_chaise.obj'])     # the photo itself is not kept

    def test_what_is_not_a_photo_never_reaches_triposr(self):
        bad = self.client.post('/api/lab/photo?name=texte.jpg', content=b'pas une image', headers=self.binary)
        self.assertEqual((bad.status_code, bad.json()['detail']),
                         (422, 'Photo vers 3D : format d’image non lu (PNG ou JPEG seulement).'))
        with mock.patch.object(triposr_local, 'MAX_PHOTO_BYTES', 10):
            self.assertEqual(self.client.post('/api/lab/photo?name=a.jpg', content=JPEG, headers=self.binary).status_code,
                             413)
        self.assertEqual(self.client.post('/api/lab/photo?name=a.jpg', content=JPEG, headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.calls, [])
        self.assertFalse(lab.models_dir().exists())

    def test_what_triposr_refuses_is_said_as_it_is(self):
        for status, words in ((503, 'Photo vers 3D indisponible sur ce PC : TripoSR introuvable (D:\\x).'),
                              (409, 'Une photo est déjà en cours de reconstruction : attendez qu’elle soit finie.'),
                              (422, 'Aucun modèle 3D tiré de cette photo : aucun objet n’a été trouvé sur la photo.')):
            with mock.patch.object(triposr_local, 'reconstruct', side_effect=triposr_local.TripoError(words, status)):
                res = self.client.post('/api/lab/photo?name=a.jpg', content=JPEG, headers=self.binary)
            self.assertEqual((res.status_code, res.json()['detail']), (status, words))
        self.assertFalse(lab.models_dir().exists())


if __name__ == '__main__':
    unittest.main()
