"""Optional local embeddings (bge-m3 through Ollama) for semantic memory search.

Everything works without Ollama: ``embed_texts`` then returns None and callers fall back
to keyword search. Vectors are stored in ``memory_embeddings`` as float32 little-endian
blobs (``numpy.ndarray.tobytes()``).

Live measurement (2026-10-28, bge-m3 via Ollama): dimension 1024, vectors already unit
length, ~15-20 ms per warm single query, ~115 ms for a batch of 6 short memories, ~2.4 s
cold start. Related French texts score 0.44-0.57 cosine, unrelated ones mostly < 0.42,
hence an absolute floor plus a margin relative to the best match.
08/11/2026, on the 23 real memories (mostly one project, VITA): unrelated sentences reach 0.475 cosine, so the
prompt context only keeps a semantic-only match from 0.50 (context.MIN_MEMORY_SCORE).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import math
import os
import sqlite3
import threading

import numpy as np

from .. import db

log = logging.getLogger(__name__)

DEFAULT_MODEL = 'bge-m3'
SEMANTIC_MIN_COSINE = 0.40     # below: never a semantic match
SEMANTIC_MARGIN = 0.08         # keep matches within this distance of the best cosine
_SCORE_ZERO, _SCORE_FULL = 0.30, 0.70   # cosine -> 0..1 score mapping
_BATCH = 16
_MAX_TEXT_CHARS = 4000

_cache_lock = threading.Lock()
_matrix_cache: dict[tuple, tuple] = {}


def embed_model() -> str:
    return db.config_value('embed_model', lambda: (os.getenv('AURORA_EMBED_MODEL') or '').strip() or DEFAULT_MODEL)


def _offline() -> bool:
    return bool(db.config_value('offline', lambda: os.getenv('AURORA_OFFLINE') == '1'))


def _provider():
    """Shared Ollama provider (providers/ollama.py, B3) or None if not importable."""
    try:
        module = importlib.import_module('aurora.providers.ollama')
    except ImportError:
        return None
    provider = getattr(module, 'ollama', None)
    return provider if provider is not None and hasattr(provider, 'embed') else None


def memory_text(memory: dict) -> str:
    text = f"{memory.get('title') or ''}\n{memory.get('detail') or ''}".strip()
    return text[:_MAX_TEXT_CHARS]


def to_blob(vector) -> bytes:
    return np.asarray(vector, dtype='<f4').tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype='<f4')


async def embed_texts(texts: list[str], *, timeout: float = 30.0) -> list[list[float]] | None:
    """Vectors for ``texts`` through the local provider, or None (offline, no Ollama, error)."""
    texts = [str(t) for t in texts]
    if not texts:
        return []
    if _offline():
        return None
    provider = _provider()
    if provider is None:
        return None
    try:
        available = getattr(provider, 'available', None)
        if available is not None and not await available():
            return None
        vectors = await asyncio.wait_for(provider.embed(texts, model=embed_model()), timeout)
    except Exception as exc:  # provider down, timeout, bad model: semantic search is optional
        log.info('Embeddings indisponibles : %s', type(exc).__name__)
        return None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        return None
    out: list[list[float]] = []
    dim = None
    for vec in vectors:
        try:
            values = [float(x) for x in vec]
        except (TypeError, ValueError):
            return None
        if not values or (dim is not None and len(values) != dim) \
                or not all(math.isfinite(v) for v in values):
            return None
        dim = len(values)
        out.append(values)
    return out


def _store(conn: sqlite3.Connection, memory_id: int, model: str, vector: list[float]) -> None:
    conn.execute(
        'INSERT INTO memory_embeddings(memory_id, model, dim, vector) VALUES (?,?,?,?) '
        'ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, '
        "vector=excluded.vector, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        (memory_id, model, len(vector), to_blob(vector)))


def _store_if_unchanged(conn: sqlite3.Connection, memory: dict, model: str, vector) -> bool:
    # The memory may have been edited or deleted while the vector was being computed.
    current = conn.execute('SELECT title, detail FROM memories WHERE id=?', (memory['id'],)).fetchone()
    if current is None or current['title'] != memory['title'] or current['detail'] != memory['detail']:
        return False
    _store(conn, memory['id'], model, vector)
    return True


async def ensure_embedding(memory_id: int) -> bool:
    """Make sure the memory has a vector for the current model. False if impossible now."""
    memory = db.row('SELECT id, title, detail FROM memories WHERE id=?', (memory_id,))
    if memory is None:
        return False
    model = embed_model()
    if db.row('SELECT 1 AS ok FROM memory_embeddings WHERE memory_id=? AND model=?', (memory_id, model)):
        return True
    vectors = await embed_texts([memory_text(memory)])
    if not vectors:
        return False
    with db.connect() as conn:
        return _store_if_unchanged(conn, memory, model, vectors[0])


async def backfill(limit: int = 200) -> int:
    """Embed memories that have no vector for the current model. Returns how many were stored."""
    model = embed_model()
    pending = db.rows(
        'SELECT m.id, m.title, m.detail FROM memories m '
        'LEFT JOIN memory_embeddings e ON e.memory_id = m.id '
        'WHERE e.memory_id IS NULL OR e.model != ? ORDER BY m.pinned DESC, m.id DESC LIMIT ?',
        (model, max(0, int(limit))))
    stored = 0
    for start in range(0, len(pending), _BATCH):
        batch = pending[start:start + _BATCH]
        vectors = await embed_texts([memory_text(m) for m in batch])
        if not vectors:
            break
        with db.connect() as conn:
            stored += sum(_store_if_unchanged(conn, m, model, v) for m, v in zip(batch, vectors))
    return stored


_background: asyncio.Task | None = None


def missing_count() -> int:
    """Memories without a vector for the current model."""
    found = db.row('SELECT COUNT(*) AS n FROM memories m LEFT JOIN memory_embeddings e ON e.memory_id = m.id '
                   'WHERE e.memory_id IS NULL OR e.model != ?', (embed_model(),))
    return int(found['n']) if found else 0


def schedule_backfill(limit: int = 32) -> bool:
    """Start one background ``backfill`` on the running loop if vectors are missing.

    Never awaited by the caller (no added latency); at most one runs at a time. Returns
    True when a task was started. Without a running loop it does nothing.
    """
    global _background
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _background is not None and not _background.done():
        return False
    if missing_count() == 0:
        return False

    def _done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            log.warning('Calcul des vecteurs en arrière-plan interrompu : %s', type(task.exception()).__name__)

    _background = loop.create_task(backfill(limit=limit))   # strong reference: tasks are weakly held
    _background.add_done_callback(_done)
    return True


def _matrix(model: str, dim: int) -> tuple[list[int], np.ndarray]:
    """Unit-normalised vectors of every memory for ``model``/``dim`` (cached until they change)."""
    signature = db.row(
        "SELECT COUNT(*) AS n, COALESCE(SUM(memory_id), 0) AS s, COALESCE(MAX(updated_at), '') AS u "
        'FROM memory_embeddings WHERE model=? AND dim=?', (model, dim))
    key = (str(db.db_path()), model, dim)
    sig = (signature['n'], signature['s'], signature['u'])
    with _cache_lock:
        cached = _matrix_cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1], cached[2]
    records = db.rows('SELECT memory_id, vector FROM memory_embeddings WHERE model=? AND dim=? '
                      'ORDER BY memory_id', (model, dim))
    ids = [r['memory_id'] for r in records]
    if records:
        matrix = np.vstack([from_blob(r['vector']) for r in records]).astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms > 0, norms, 1.0)
    else:
        matrix = np.zeros((0, dim), dtype=np.float32)
    with _cache_lock:
        _matrix_cache[key] = (sig, ids, matrix)
    return ids, matrix


def cosine_to_score(cosine: float) -> float:
    return round(min(1.0, max(0.0, (cosine - _SCORE_ZERO) / (_SCORE_FULL - _SCORE_ZERO))), 4)


def nearest(query_vector, *, limit: int = 24) -> list[tuple[dict, float, float]]:
    """[(memory, cosine, score)] most similar to ``query_vector`` among stored vectors."""
    q = np.asarray(query_vector, dtype=np.float32)
    norm = float(np.linalg.norm(q))
    if q.ndim != 1 or norm == 0.0:
        return []
    ids, matrix = _matrix(embed_model(), int(q.shape[0]))
    if not ids:
        return []
    sims = matrix @ (q / norm)
    order = np.argsort(-sims)[:max(1, limit)]
    best = float(sims[order[0]])
    floor = max(SEMANTIC_MIN_COSINE, best - SEMANTIC_MARGIN)
    chosen = [(ids[i], float(sims[i])) for i in order if float(sims[i]) >= floor]
    if not chosen:
        return []
    placeholders = ','.join('?' * len(chosen))
    memories = {m['id']: m for m in db.rows(f'SELECT * FROM memories WHERE id IN ({placeholders})',
                                            [mid for mid, _ in chosen])}
    return [(memories[mid], cos, cosine_to_score(cos)) for mid, cos in chosen if mid in memories]
