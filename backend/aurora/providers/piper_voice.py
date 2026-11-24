"""Local neural voice (Piper): French text -> WAV, generated entirely on this PC (CLAUDE.md: local by default).

The voice files live in AURORA_HOME/voices (AURORA_VOICES_DIR to override): `<voice>.onnx` and
`<voice>.onnx.json`; default voice fr_FR-siwis-medium (SIWIS dataset, CC-BY 4.0). Engine: the `piper-tts`
package (GPL-3.0). The model is loaded once (about 2 s) and kept; synthesis runs about 7 times faster than
real time on this PC and is serialised by a lock. Without the package or the voice, `available()` is False
and the interface keeps the browser voices. The text read aloud is never logged.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import wave
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

DEFAULT_VOICE = 'fr_FR-siwis-medium'
MAX_CHARS = 1500
# 29/10/2026, « le vocal est très rapide » : the voice's own pace (length_scale 1) sounded hurried. Larger = slower.
LENGTH_SCALE = 1.15
SENTENCE_PAUSE_S = 0.28      # silence between two sentences (Piper alone glues them together)
SPEED_RANGE = (0.7, 1.3)     # optional speed asked by the interface, 1 = this default pace
VOICE_LICENSES = {'fr_FR-siwis-medium': 'CC-BY 4.0 (SIWIS)', 'fr_FR-tom-medium': 'voir MODEL_CARD'}

_lock = threading.Lock()
_loaded: dict = {}          # voice name -> PiperVoice


def voices_dir() -> Path:
    raw = os.getenv('AURORA_VOICES_DIR', '').strip()
    return Path(raw).expanduser() if raw else config.aurora_home() / 'voices'


def voice_name() -> str:
    return os.getenv('AURORA_VOICE', '').strip() or DEFAULT_VOICE


def model_path() -> Path:
    return voices_dir() / f'{voice_name()}.onnx'


def _engine_installed() -> bool:
    try:
        import piper  # noqa: F401
    except Exception:
        return False
    return True


def available() -> bool:
    model = model_path()
    return model.is_file() and Path(f'{model}.json').is_file() and _engine_installed()


def status() -> dict:
    name = voice_name()
    return {'available': available(), 'engine': 'piper', 'local': True, 'voice': name,
            'loaded': name in _loaded, 'license': VOICE_LICENSES.get(name, 'voir MODEL_CARD')}


def _voice():
    name = voice_name()
    if name not in _loaded:
        from piper import PiperVoice
        _loaded[name] = PiperVoice.load(str(model_path()))
    return _loaded[name]


def synthesize(text: str, *, speed: float | None = None) -> bytes:
    """WAV bytes of `text`, at a calm pace with a short pause between sentences. `speed` (0.7 to 1.3, 1 = default
    pace) comes from the interface. ValueError (French) for an empty or too long text, RuntimeError if not installed."""
    clean = ' '.join(str(text or '').split())
    if not clean:
        raise ValueError('Texte à lire vide.')
    if len(clean) > MAX_CHARS:
        raise ValueError(f'Texte trop long pour une lecture ({len(clean)} caractères, {MAX_CHARS} au plus).')
    if not available():
        raise RuntimeError('Voix neuronale locale non installée.')
    from piper import SynthesisConfig
    pace = min(max(speed, SPEED_RANGE[0]), SPEED_RANGE[1]) if speed else 1.0
    config = SynthesisConfig(length_scale=LENGTH_SCALE / pace)
    with _lock:
        chunks = list(_voice().synthesize(clean, syn_config=config))   # one chunk per sentence
    if not chunks:
        raise ValueError('Rien à lire dans ce texte.')
    first = chunks[0]
    pause = bytes(int(first.sample_rate * SENTENCE_PAUSE_S) * first.sample_width * first.sample_channels)
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setframerate(first.sample_rate)
        wav.setsampwidth(first.sample_width)
        wav.setnchannels(first.sample_channels)
        for i, chunk in enumerate(chunks):
            if i:
                wav.writeframes(pause)
            wav.writeframes(chunk.audio_int16_bytes)
    return buffer.getvalue()


def warmup() -> bool:
    """Load the voice in advance (called in a thread at start-up), so the first reading is immediate."""
    if not available():
        return False
    try:
        with _lock:
            _voice()
        return True
    except Exception:
        log.warning('Voix neuronale locale : chargement impossible', exc_info=True)
        return False
