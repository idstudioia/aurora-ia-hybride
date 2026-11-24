"""Local speech recognition (voix D3, 9/11/2026): Athéna's own Whisper service, reused as it is.

ID Studio IA asked to take Athéna's voice settings. Athéna already runs faster-whisper in a small HTTP service
(`whisper-service.py`, 127.0.0.1:8191, POST /transcrire) with its own virtual environment and the model already on
disk: Whisper « base », CPU, int8, 4 threads, French, voice-activity filter, its vocabulary prompt, and
`local_files_only` (it never downloads anything). AURORA neither copies nor modifies it: it starts it on demand as a
hidden child process (or uses it when Athéna already started it), sends it the recorded audio and returns the text.
The audio never leaves this PC, and neither the audio nor the text is logged.

Folder: AURORA_WHISPER_DIR, by default Athéna's « outputs » folder. Without it (or without its venv, script or
model), `available()` is False and the interface keeps the browser's recognition.
Limits, said as they are: the service has no token (it listens on 127.0.0.1 only); the « base » model is weak in
French (15 % of words wrong on ten sentences read by Piper, 9/11/2026) and gives no confidence score; the RX 6750 XT
does not accelerate it (no CUDA), about 0.5 s for a 2 s sentence on the Ryzen 7 5700X.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from ..netutil import ssl_context

log = logging.getLogger(__name__)

URL = 'http://127.0.0.1:8191/transcrire'
ENGINE = 'Whisper base CPU int8 (service d’Athéna)'
MAX_AUDIO_BYTES = 5_000_000          # Athéna's own limit (whisper-service.py), refused there above it
START_TIMEOUT_S = 60.0               # import faster-whisper + model load, measured a few seconds; generous
TRANSCRIBE_TIMEOUT_S = 90.0
_DEFAULT_DIR = Path.home() / 'Documents' / 'Codex' / '2026-09-11' / 'realtime-voice-chat' / 'outputs'
_MODEL = Path('whisper-models') / 'models--Systran--faster-whisper-base'

_proc: subprocess.Popen | None = None     # set only when AURORA started the service itself
_start_lock: asyncio.Lock | None = None


class WhisperError(RuntimeError):
    """A failure the interface shows as it is (service missing, not started, audio refused)."""


def service_dir() -> Path:
    raw = os.getenv('AURORA_WHISPER_DIR', '').strip()
    return Path(raw).expanduser() if raw else _DEFAULT_DIR


def _paths() -> tuple[Path, Path, Path]:
    root = service_dir()
    return root / 'whisper-audit-venv' / 'Scripts' / 'python.exe', root / 'whisper-service.py', root / _MODEL


def missing() -> str | None:
    """What is missing for local recognition, in words, or None."""
    python, script, model = _paths()
    if not script.is_file():
        return f'service Whisper d’Athéna introuvable ({script})'
    if not python.is_file():
        return f'environnement Python du service introuvable ({python})'
    if not model.is_dir():
        return f'modèle Whisper « base » absent ({model}) : rien n’est téléchargé sans ton accord'
    return None


def available() -> bool:
    return missing() is None


def _started_here() -> bool:
    return _proc is not None and _proc.poll() is None


async def _alive(timeout: float = 1.5) -> bool:
    """True when something answers on the service's address (an empty POST gets an HTTP error back: it is alive)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, verify=ssl_context()) as client:
            await client.post(URL, content=b'')
        return True
    except httpx.HTTPError:
        return False


async def status() -> dict:
    reason = missing()
    return {
        'available': reason is None,
        'running': await _alive() if reason is None else False,
        'started_by_aurora': _started_here(),
        'engine': ENGINE,
        'local': True,
        'reason': reason or '',
    }


async def ensure_started() -> None:
    """Start the service if nothing answers yet, then wait until it answers (WhisperError otherwise)."""
    global _proc, _start_lock
    reason = missing()
    if reason:
        raise WhisperError(f'Reconnaissance locale indisponible : {reason}.')
    if await _alive():
        return
    if _start_lock is None:
        _start_lock = asyncio.Lock()
    async with _start_lock:
        if await _alive():
            return
        if not _started_here():
            python, script, _ = _paths()
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            try:
                _proc = subprocess.Popen([str(python), str(script)], cwd=str(script.parent), creationflags=flags,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                raise WhisperError(f'Démarrage du service Whisper impossible : {exc}') from None
            log.info('Service Whisper local démarré (pid %s)', _proc.pid)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if await _alive(0.8):
                return
            if _proc is not None and _proc.poll() is not None:
                raise WhisperError(f'Le service Whisper s’est arrêté au démarrage (code {_proc.returncode}).')
            await asyncio.sleep(0.4)
        raise WhisperError(f'Le service Whisper ne répond pas après {START_TIMEOUT_S:.0f} s.')


async def transcribe(audio: bytes) -> dict:
    """Text of a short recording (webm/opus from the browser, or wav), recognised on this PC."""
    if not audio:
        raise WhisperError('Aucun son enregistré.')
    if len(audio) > MAX_AUDIO_BYTES:
        raise WhisperError(f'Enregistrement trop long ({MAX_AUDIO_BYTES // 1_000_000} Mo au plus).')
    await ensure_started()
    body = {'audio': base64.b64encode(audio).decode('ascii'), 'vocabulaire': True}   # Athéna's setting
    try:
        async with httpx.AsyncClient(timeout=TRANSCRIBE_TIMEOUT_S, trust_env=False, verify=ssl_context()) as client:
            response = await client.post(URL, json=body)
    except httpx.HTTPError as exc:
        raise WhisperError(f'Service Whisper injoignable : {type(exc).__name__}.') from None
    if response.status_code != 200:
        raise WhisperError(f'Le service Whisper n’a pas pu lire ce son (HTTP {response.status_code}).')
    data = response.json()
    return {
        'text': str(data.get('texte') or '').strip(),
        'seconds': round(float(data.get('secondes') or 0), 2),
        'audio_seconds': round(float(data.get('dureeAudio') or 0), 2),
        'engine': ENGINE,
    }


def stop() -> None:
    """Stop the service only if AURORA started it (Athéna's own run is left alone)."""
    global _proc
    if _started_here():
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
