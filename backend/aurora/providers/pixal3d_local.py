"""Photo -> 3D shape, on this PC, with Pixal3D (10/11/2026, asked by ID Studio IA: « Oui, branche-le »).

Pixal3D (Tencent ARC, MIT licence, May 2026) rebuilds an object from ONE photo with far more detail than TripoSR:
on ID Studio IA's fighter plane and satellite, the panels, the nozzle and the antenna are there
(reviews/essais-image-vers-3d.md). It runs here through trellis.cpp (pwilkin, MIT licence), a program of its own
that uses the graphics card by Vulkan: about 190 s on the RX 6750 XT. ID Studio IA installed it for the trials of that day in
its own folder, with its models; AURORA neither copies nor modifies it. One photo is three hidden child processes,
one after the other, each with fixed arguments, no shell, a time limit and a lowered priority:

    1. <TripoSR's python> -I pixal3d_lanceur.py preparer <photo> <image.png>          the cut-out (2 to 4 s)
    2. <folder>/programme/trellis-cli.exe <image.png> <brut.glb> --models <folder>/modeles --model pixal3d
           --res 512 --seed 42 --backend Vulkan --require-gpu --verbose               the shape (about 190 s)
    3. <TripoSR's python> -I pixal3d_lanceur.py finir <brut.glb> <modele.glb> 320000  pose, lighten, level (5 s)

AURORA starts the program itself, not a script in between: a run too long is really stopped. The photo and the
shapes stay in a temporary folder, removed after the run; the photo is never logged.

Network. The two Python steps get TripoSR's closed environment (triposr_local._environment). The program gets the
same variables, but it is a native program and nothing forces it to honour them: AURORA gives it nothing to
download (its models are files on the disk, named by --models) and no address — it does not enforce more.

Folder: AURORA_PIXAL3D_DIR, by default the folder of the trials. ``missing()`` says what is absent. Pixal3D also
needs TripoSR's Python environment, for the cut-out and for the mesh tools.

« Photo vers 3D » calls ``photo_to_shape``: Pixal3D when it is there, TripoSR otherwise, and also when Pixal3D
could not run or broke — the former engine is not removed, it is the way out (ID Studio IA, 29/09/2026). Since 11/11/2026
the person may ask for TripoSR first (« rapide », 35 s instead of 3 minutes): Pixal3D is then the way out.

Limits, said as they are: only the side the photo shows is seen, the rest is invented; no real dimension; the shape
alone, without its colours (at this fineness Pixal3D has no texture model); tried on two drawings whose background
was plain, not yet on a real photo; the angle of view is the program's default (49 degrees); the memory it takes on
the graphics card was not measured: while a local model holds that memory the run may fail, and TripoSR takes over.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import triposr_local
from .triposr_local import TripoError   # the same failure for the two engines: the interface shows it the same way

log = logging.getLogger(__name__)

ENGINE = 'Pixal3D (IA locale, sur la carte graphique de ce PC)'
NAME = 'Pixal3D'
FACES = 320_000           # ID Studio IA, 10/11/2026: « passe plutôt à 320000 faces »
FINENESS, SEED = 512, 42  # the settings of the two trials
DETAILED, FAST = 'detaille', 'rapide'   # the person's choice on the button: Pixal3D first, or TripoSR first
CHOICES = (DETAILED, FAST)
TIMEOUT_S = 900.0         # measured: 189 and 193 s; a busy graphics card may take much longer
STEP_TIMEOUT_S = 180.0    # the cut-out and the finishing: 2 to 5 s each, measured
_DEFAULT_DIR = Path('D:/pixal3d-aurora')
_RUNNER = Path(__file__).with_name('pixal3d_lanceur.py')
# The six files the program loads for this setting (read in the journal of the trials). Already on the disk.
_MODELS = ('dinov3.gguf', 'pixal3d_ss_flow.gguf', 'ss_dec.gguf', 'pixal3d_shape_flow_512.gguf', 'pixal3d_naf.gguf',
           'shape_dec.gguf')


def service_dir() -> Path:
    raw = os.getenv('AURORA_PIXAL3D_DIR', '').strip()
    return Path(raw).expanduser() if raw else _DEFAULT_DIR


def _program() -> list[str]:
    """The program, and nothing before it (the tests put a stand-in here)."""
    return [str(service_dir() / 'programme' / 'trellis-cli.exe')]


def missing() -> str | None:
    """What is missing to rebuild a shape with Pixal3D on this PC, in words, or None."""
    root = service_dir()
    if not (root / 'programme' / 'trellis-cli.exe').is_file():
        return f'programme de Pixal3D introuvable ({root / "programme" / "trellis-cli.exe"})'
    for name in _MODELS:
        if not (root / 'modeles' / name).is_file():
            return f'modèle absent ({root / "modeles" / name}) : rien n’est téléchargé sans votre accord'
    python, _depot = triposr_local._paths()
    if not python.is_file():
        return (f'environnement Python de TripoSR introuvable ({python}) : Pixal3D s’en sert pour le détourage et '
                'pour alléger la forme')
    if not triposr_local._cutout_model().is_file():
        return (f'modèle de détourage absent ({triposr_local._cutout_model()}) : rien n’est téléchargé sans votre '
                'accord')
    if not _RUNNER.is_file():
        return f'lanceur de Pixal3D introuvable ({_RUNNER})'
    return None


def _child(command: list[str], *, cwd: Path, env: dict[str, str], limit: float, what: str):
    """One hidden child process, waited for; TripoError (503) when it could not start or did not finish in time
    (it is then killed). ``what``: the step, in words."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
    try:
        return subprocess.run(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=limit, creationflags=flags, check=False)
    except subprocess.TimeoutExpired:
        raise TripoError(f'Pixal3D n’a pas fini {what} en {limit:.0f} s : il a été arrêté, et aucun modèle n’est '
                         'gardé.', 503) from None
    except OSError as exc:
        log.warning('Pixal3D, %s non lancé : %s', what, exc)
        raise TripoError(f'Pixal3D n’a pas pu lancer {what} sur ce PC.', 503) from None


def _broken(done, what: str, detail: object = None) -> TripoError:
    """A step that broke: its trace goes to the service's log, not to the person (paths, versions)."""
    log.warning('Pixal3D arrêté pendant %s (code %s) : %s | %s | %s', what, done.returncode, detail,
                done.stdout.decode('utf-8', 'replace')[-1200:], done.stderr.decode('utf-8', 'replace')[-1200:])
    return TripoError(f'Pixal3D s’est arrêté pendant {what} sans donner de modèle (détail dans le journal du '
                      'service).', 503)


def _measures(done, what: str, refused: int) -> dict:
    """What a step of pixal3d_lanceur.py measured (its last line of JSON). A photo that gives nothing: TripoError
    with the runner's own reason and the status ``refused``."""
    result = triposr_local._last_json(done.stdout)
    if result is not None and result.get('ok'):
        return result
    reason = str((result or {}).get('erreur') or '').strip()
    if not reason:
        raise _broken(done, what, (result or {}).get('panne'))
    raise TripoError(f'Aucun modèle 3D tiré de cette photo : {reason[:300]}.', refused)


def reconstruct(image: bytes, kind: str) -> dict:
    """The shape Pixal3D rebuilds from this photo (``kind``: « png » or « jpeg », already checked by the caller):
    {modele (bytes of a GLB file, y up, the photographed side toward the viewer), format, moteur, nom, triangles,
    sommets, faces_avant (before the lightening), aplomb_deg (the lean corrected, None when the shape was left as
    it was), couverture, secondes, …}, all measured by the run. Blocking (about 200 s): call it in a thread.
    TripoError (French) otherwise: 503 when Pixal3D cannot run here or broke, 409 when a photo is already being
    rebuilt, 422 when this photo gives nothing."""
    why = missing()
    if why:
        raise TripoError(f'Pixal3D indisponible sur ce PC : {why}.', 503)
    if kind not in ('png', 'jpeg') or not image or len(image) > triposr_local.MAX_PHOTO_BYTES:
        raise TripoError('Photo non lue : une image PNG ou JPEG de '
                         f'{triposr_local.MAX_PHOTO_BYTES // (1024 * 1024)} Mo au plus est attendue.')
    if not triposr_local._lock.acquire(blocking=False):   # one photo at a time, whatever the engine
        raise TripoError('Une photo est déjà en cours de reconstruction : attendez qu’elle soit finie.', 409)
    work = Path(tempfile.mkdtemp(prefix='aurora-photo3d-'))
    try:
        root, (python, _depot), env = service_dir(), triposr_local._paths(), triposr_local._environment()
        photo, cut = work / f'photo.{"png" if kind == "png" else "jpg"}', work / 'image.png'
        raw, shape = work / 'brut.glb', work / 'modele.glb'
        photo.write_bytes(image)
        started = time.perf_counter()
        seen = _measures(_child([str(python), '-I', str(_RUNNER), 'preparer', str(photo), str(cut)], cwd=work,
                                env=env, limit=STEP_TIMEOUT_S, what='le détourage'), 'le détourage', 422)
        t = time.perf_counter()
        # GGML_VK_DISABLE_INTEGER_DOT_PRODUCT: the known way round for the RX 6000 cards under Windows (report
        # n° 50 of trellis.cpp); without it the run stops on this PC.
        done = _child([*_program(), str(cut), str(raw), '--models', str(root / 'modeles'), '--model', 'pixal3d',
                       '--res', str(FINENESS), '--seed', str(SEED), '--backend', 'Vulkan', '--require-gpu',
                       '--verbose'], cwd=root / 'programme',
                      env={**env, 'GGML_VK_DISABLE_INTEGER_DOT_PRODUCT': '1'}, limit=TIMEOUT_S,
                      what='le calcul de la forme')
        if done.returncode != 0 or not raw.is_file() or raw.stat().st_size < 20:
            raise _broken(done, 'le calcul de la forme')
        computed = time.perf_counter() - t
        # A raw model without a shape is not this photo's fault for sure: 503, so that TripoSR is tried.
        made = _measures(_child([str(python), '-I', str(_RUNNER), 'finir', str(raw), str(shape), str(FACES)],
                                cwd=work, env=env, limit=STEP_TIMEOUT_S, what='la finition'), 'la finition', 503)
        try:
            data = shape.read_bytes()
        except OSError:
            raise TripoError('Pixal3D n’a écrit aucun modèle.', 503) from None
        return {**{k: made[k] for k in ('triangles', 'sommets', 'faces_avant', 'grille', 'aplomb_deg', 'finition_s')
                   if k in made},
                'modele': data, 'format': 'glb', 'moteur': ENGINE, 'nom': NAME,
                'couverture': seen.get('couverture'), 'detourage_s': seen.get('detourage_s'),
                'calcul_s': round(computed, 1), 'secondes': round(time.perf_counter() - started, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        triposr_local._lock.release()


def _triposr_shape(image: bytes, kind: str) -> dict:
    """TripoSR's run, said with the same keys as ``reconstruct`` above."""
    made = triposr_local.reconstruct(image, kind)
    shape = made.pop('obj')
    return {**made, 'modele': shape, 'format': 'obj', 'moteur': triposr_local.ENGINE, 'nom': triposr_local.NAME}


def photo_to_shape(image: bytes, kind: str, choice: str = DETAILED) -> dict:
    """What « Photo vers 3D » gives for this photo. ``choice`` is the person's (11/11/2026, ID Studio IA: « une petite icône
    pour sélectionner le moteur ») and only sets which engine is tried first:

    - « detaille »: Pixal3D when it is installed (about 3 minutes), TripoSR otherwise;
    - « rapide »: TripoSR (about 35 s).

    The other engine stays the way out when the first could not run or broke (status 503), and ``repli`` then says
    what the first one said (None otherwise). A photo that gives nothing (422) and a photo already in progress (409)
    are said as they are: the other engine would refuse the same photo for the same reason.
    {modele (bytes), format (« glb » or « obj »), moteur, nom, repli, secondes, triangles, couverture, …}."""
    engines = [(NAME, lambda: missing() is None, reconstruct), (triposr_local.NAME, lambda: True, _triposr_shape)]
    if choice == FAST:
        engines.reverse()
    said = None   # what the first engine said when it could not make the shape
    for rank, (name, ready, run) in enumerate(engines):
        if not ready():
            continue   # not installed: the other one, without a word (TripoSR says by itself what it lacks)
        try:
            return {**run(image, kind), 'repli': said}
        except TripoError as exc:
            if said is not None:
                raise TripoError(f'{said} {name}, essayé ensuite : {exc}', exc.status) from None
            if rank or exc.status != 503:
                raise
            said = str(exc)
    raise TripoError(said, 503)   # the first engine could not, and the other one is not installed
