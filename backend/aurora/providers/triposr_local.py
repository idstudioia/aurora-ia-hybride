"""Photo -> 3D shape, on this PC, with TripoSR (10/11/2026, asked by ID Studio IA: « Photo vers 3D »).

TripoSR (VAST-AI-Research, MIT licence) rebuilds the shape of an object from ONE photo, as a mesh of one block. It
was installed for the trials of 10/11/2026 (reviews/essais-image-vers-3d.md) in its own folder, with its own Python
environment: AURORA neither copies nor modifies it, and installs nothing in its own environment. Like Athéna's
Whisper (providers/whisper_local.py), it is started as a hidden child process, here one run per photo:

    <folder>/venv/Scripts/python.exe -I triposr_lanceur.py <folder>/TripoSR <photo> <shape.obj> <resolution>

Fixed arguments, no shell, one run at a time, a time limit, a lowered priority (the PC stays usable). The child may
not use the network (HF_HUB_OFFLINE and a proxy that leads nowhere): the photo and the shape never leave this PC,
and nothing is downloaded. The photo is written in a temporary folder, removed after the run; it is not logged.

Folder: AURORA_TRIPOSR_DIR, by default the folder of the trials. Without it (or without its environment, its code
or its models), ``missing()`` says what is absent and nothing is started.
Limits, said as they are: only the side the photo shows is seen, the rest is invented; no real dimension; a line
drawing or a cluttered photo gives a flat or shapeless block (measured on 15 images, 10/11/2026); about 35 s per
photo on the Ryzen 7 5700X (the RX 6750 XT does not accelerate it: no CUDA), 4.2 GB of memory at most.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

ENGINE = 'TripoSR (IA locale, sur le processeur de ce PC)'
NAME = 'TripoSR'                     # in a sentence; since 10/11/2026 Pixal3D runs first (providers/pixal3d_local.py)
MAX_PHOTO_BYTES = 15 * 1024 * 1024   # as a joined image (tools/vision.MAX_IMAGE_BYTES)
RESOLUTION = 256                     # the grid the shape is cut in (TripoSR's own default)
TIMEOUT_S = 300.0                    # measured: 35 s; a busy PC may take much longer
_DEFAULT_DIR = Path('D:/essais-aurora-3d')
_RUNNER = Path(__file__).with_name('triposr_lanceur.py')
_MODELS = ('models--stabilityai--TripoSR', 'models--facebook--dino-vitb16')   # Hugging Face cache, already on disk
_SECRET_MARKS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASSWD')

_lock = threading.Lock()


class TripoError(RuntimeError):
    """A failure the interface shows as it is. ``status``: 503 when TripoSR cannot run here, 409 when it is busy,
    422 when this photo gave nothing."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def service_dir() -> Path:
    raw = os.getenv('AURORA_TRIPOSR_DIR', '').strip()
    return Path(raw).expanduser() if raw else _DEFAULT_DIR


def _paths() -> tuple[Path, Path]:
    root = service_dir()
    return root / 'venv' / 'Scripts' / 'python.exe', root / 'TripoSR'


def _hub_cache() -> Path:
    raw = os.getenv('HF_HUB_CACHE', '').strip()
    if raw:
        return Path(raw).expanduser()
    home = os.getenv('HF_HOME', '').strip()
    return (Path(home).expanduser() if home else Path.home() / '.cache' / 'huggingface') / 'hub'


def _cutout_model() -> Path:
    raw = os.getenv('U2NET_HOME', '').strip()
    return (Path(raw).expanduser() if raw else Path.home() / '.u2net') / 'u2net.onnx'


def missing() -> str | None:
    """What is missing to rebuild a shape from a photo on this PC, in words, or None."""
    python, depot = _paths()
    if not (depot / 'tsr' / 'system.py').is_file():
        return f'TripoSR introuvable ({depot})'
    if not python.is_file():
        return f'environnement Python de TripoSR introuvable ({python})'
    if not _RUNNER.is_file():
        return f'lanceur de TripoSR introuvable ({_RUNNER})'
    for name in _MODELS:
        if not (_hub_cache() / name).is_dir():
            return f'modèle absent ({_hub_cache() / name}) : rien n’est téléchargé sans votre accord'
    if not _cutout_model().is_file():
        return f'modèle de détourage absent ({_cutout_model()}) : rien n’est téléchargé sans votre accord'
    return None


def available() -> bool:
    return missing() is None


def busy() -> bool:
    return _lock.locked()


def _environment() -> dict[str, str]:
    """The child's environment: AURORA's own, without anything that looks like a secret, and without the network
    (the Hugging Face libraries stay on their cache; any other attempt meets a proxy that leads nowhere)."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('AURORA_', 'ANTHROPIC_')) and not any(m in k.upper() for m in _SECRET_MARKS)}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9', ALL_PROXY='http://127.0.0.1:9')
    env.pop('NO_PROXY', None)
    return env


def _last_json(output: bytes) -> dict | None:
    for line in reversed(output.decode('utf-8', 'replace').splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                found = json.loads(line)
            except ValueError:
                return None
            return found if isinstance(found, dict) else None
    return None


def reconstruct(image: bytes, kind: str) -> dict:
    """The shape TripoSR rebuilds from this photo (``kind``: « png » or « jpeg », already checked by the caller):
    {obj (bytes, y up, the photographed side toward the viewer), triangles, sommets, couverture, lissage (the
    smoothing passes the runner did on the surface, 0 if none), secondes, …}, all measured by the run. Blocking (about 35 s): call it in a thread. TripoError (French) otherwise."""
    why = missing()
    if why:
        raise TripoError(f'Photo vers 3D indisponible sur ce PC : {why}.', 503)
    if kind not in ('png', 'jpeg'):
        raise TripoError('Format d’image non lu : PNG ou JPEG seulement.')
    if not image or len(image) > MAX_PHOTO_BYTES:
        raise TripoError(f'Photo vide ou trop lourde ({MAX_PHOTO_BYTES // (1024 * 1024)} Mo au plus).')
    if not _lock.acquire(blocking=False):
        raise TripoError('Une photo est déjà en cours de reconstruction : attendez qu’elle soit finie.', 409)
    work = Path(tempfile.mkdtemp(prefix='aurora-photo3d-'))
    try:
        python, depot = _paths()
        photo, shape = work / f'photo.{"png" if kind == "png" else "jpg"}', work / 'forme.obj'
        photo.write_bytes(image)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        started = time.perf_counter()
        try:
            done = subprocess.run([str(python), '-I', str(_RUNNER), str(depot), str(photo), str(shape), str(RESOLUTION)],
                                  cwd=str(work), env=_environment(), stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=TIMEOUT_S, creationflags=flags, check=False)
        except subprocess.TimeoutExpired:
            raise TripoError(f'TripoSR n’a pas fini en {TIMEOUT_S:.0f} s : il a été arrêté, et aucun modèle n’est '
                             'gardé.', 503) from None
        except OSError as exc:
            log.warning('TripoSR non lancé : %s', exc)
            raise TripoError('TripoSR n’a pas pu être lancé sur ce PC.', 503) from None
        seconds = time.perf_counter() - started
        result = _last_json(done.stdout)
        if result is None or not result.get('ok'):
            reason = str((result or {}).get('erreur') or '').strip()
            if not reason:   # a crash: its trace goes to the service's log, not to the person (paths, versions)
                log.warning('TripoSR arrêté (code %s) : %s | %s', done.returncode, (result or {}).get('panne'),
                            done.stderr.decode('utf-8', 'replace')[-1500:])
                raise TripoError('TripoSR s’est arrêté sans donner de modèle (détail dans le journal du service).', 503)
            raise TripoError(f'Aucun modèle 3D tiré de cette photo : {reason[:300]}.')
        try:
            obj = shape.read_bytes()
        except OSError:
            raise TripoError('TripoSR n’a écrit aucun modèle.', 503) from None
        return {**{k: result[k] for k in ('triangles', 'sommets', 'couverture', 'lissage', 'chargement_s',
                                          'detourage_s', 'modele_s', 'maillage_s') if k in result},
                'obj': obj, 'secondes': round(seconds, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _lock.release()
