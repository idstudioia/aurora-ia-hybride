"""Similarity router placed BEHIND the rules.

Default mode: TF-IDF of character n-grams (3 to 5) on normalised text, pure numpy, robust to
typos; k nearest training examples vote, a decision is returned only above a similarity
threshold AND a vote margin. Optional mode: bge-m3 embeddings through Ollama (async only),
example vectors cached in AURORA_HOME.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .intents import INTENTS, INTENT_ORDER
from .normalize import normalize

EXAMPLES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'router_examples.jsonl'

NGRAM_MIN, NGRAM_MAX = 3, 5
K_NEIGHBOURS = 5
# Calibrated by leave-one-out on the training examples (see calibrate()); never on the bench.
DEFAULT_THRESHOLD = 0.40
DEFAULT_MARGIN = 0.30
EMBED_THRESHOLD = 0.62
EMBED_MARGIN = 0.2


@dataclass
class SemanticMatch:
    intent: str
    confidence: float
    similarity: float
    margin: float
    confident: bool
    method: str = 'ngram'
    neighbours: list[tuple[str, str, float]] = field(default_factory=list)   # (intent, text, similarity)
    votes: dict[str, float] = field(default_factory=dict)

    def alternatives(self, limit: int = 3) -> list[tuple[str, float]]:
        ranked = sorted(self.votes.items(), key=lambda kv: (-kv[1], _ORDER.get(kv[0], 999)))
        return [(name, round(value, 3)) for name, value in ranked if name != self.intent][:limit]


_ORDER = {name: i for i, name in enumerate(INTENT_ORDER)}


def aurora_home() -> Path:
    """AURORA_HOME from config.py when available, else the environment (contract §1)."""
    try:
        from aurora import config  # lazy: owned by another module
        for attr in ('AURORA_HOME', 'HOME', 'aurora_home'):
            value = getattr(config, attr, None)
            if callable(value):
                value = value()
            if value:
                return Path(value)
    except Exception:
        pass
    return Path(os.getenv('AURORA_HOME', str(Path.home() / '.aurora'))).expanduser()


def load_examples(path: Path | None = None) -> list[dict]:
    rows: list[dict] = []
    with Path(path or EXAMPLES_PATH).open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get('intent') in INTENTS and item.get('text'):
                rows.append({'text': item['text'], 'intent': item['intent']})
    return rows


def char_ngrams(text: str, nmin: int = NGRAM_MIN, nmax: int = NGRAM_MAX) -> Counter:
    padded = f' {text} '
    grams: Counter = Counter()
    for n in range(nmin, nmax + 1):
        for i in range(len(padded) - n + 1):
            grams[padded[i:i + n]] += 1
    return grams


def _vote(sims: np.ndarray, labels: list[str], k: int) -> tuple[dict[str, float], list[int]]:
    k = min(k, len(sims))
    if k <= 0:
        return {}, []
    idx = np.argpartition(-sims, k - 1)[:k]
    # stable, deterministic order: similarity desc, then example index
    idx = sorted(idx.tolist(), key=lambda i: (-float(sims[i]), i))
    votes: dict[str, float] = {}
    for i in idx:
        if sims[i] > 0:
            votes[labels[i]] = votes.get(labels[i], 0.0) + float(sims[i])
    return votes, idx


def _decide(votes: dict[str, float], sims: np.ndarray, labels: list[str], texts: list[str], idx: list[int],
            threshold: float, margin: float, method: str) -> SemanticMatch | None:
    if not votes:
        return None
    total = sum(votes.values()) or 1.0
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], _ORDER.get(kv[0], 999)))
    best, best_votes = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    share = best_votes / total
    vote_margin = (best_votes - second) / total
    similarity = max(float(sims[i]) for i in idx if labels[i] == best)
    confident = similarity >= threshold and vote_margin >= margin
    neighbours = [(labels[i], texts[i], round(float(sims[i]), 4)) for i in idx]
    return SemanticMatch(intent=best, confidence=round(similarity * share, 4), similarity=round(similarity, 4),
                         margin=round(vote_margin, 4), confident=confident, method=method, neighbours=neighbours,
                         votes={name: round(value / total, 4) for name, value in votes.items()})


class NgramRouter:
    """TF-IDF over character n-grams with an inverted index (numpy only)."""

    def __init__(self, examples: list[dict] | None = None, *, threshold: float | None = None,
                 margin: float | None = None, k: int = K_NEIGHBOURS):
        self.examples = examples if examples is not None else load_examples()
        self.threshold = DEFAULT_THRESHOLD if threshold is None else threshold
        self.margin = DEFAULT_MARGIN if margin is None else margin
        self.k = k
        self.labels = [e['intent'] for e in self.examples]
        self.texts = [e['text'] for e in self.examples]
        self._build()

    def _build(self) -> None:
        docs = [char_ngrams(normalize(t)) for t in self.texts]
        n_docs = len(docs)
        df: Counter = Counter()
        for doc in docs:
            df.update(doc.keys())
        self.idf = {g: math.log((1 + n_docs) / (1 + c)) + 1.0 for g, c in df.items()}
        postings: dict[str, list[tuple[int, float]]] = {}
        for row, doc in enumerate(docs):
            weights = {g: (1.0 + math.log(c)) * self.idf[g] for g, c in doc.items()}
            norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
            for g, w in weights.items():
                postings.setdefault(g, []).append((row, w / norm))
        self.index = {g: (np.fromiter((r for r, _ in items), dtype=np.int32, count=len(items)),
                          np.fromiter((w for _, w in items), dtype=np.float32, count=len(items)))
                      for g, items in postings.items()}
        self.n_docs = n_docs

    def similarities(self, normalized: str) -> np.ndarray:
        sims = np.zeros(self.n_docs, dtype=np.float32)
        grams = char_ngrams(normalized)
        weights = {g: (1.0 + math.log(c)) * self.idf[g] for g, c in grams.items() if g in self.idf}
        # unknown n-grams still count in the query norm (an unseen request is less similar)
        unknown = sum((1.0 + math.log(c)) ** 2 for g, c in grams.items() if g not in self.idf)
        norm = math.sqrt(sum(w * w for w in weights.values()) + unknown) or 1.0
        for g, w in weights.items():
            rows, vals = self.index[g]
            sims[rows] += vals * (w / norm)
        return sims

    def classify(self, normalized: str, *, exclude: int | None = None) -> SemanticMatch | None:
        if not normalized:
            return None
        sims = self.similarities(normalized)
        if exclude is not None:
            sims[exclude] = -1.0
        votes, idx = _vote(sims, self.labels, self.k)
        return _decide(votes, sims, self.labels, self.texts, idx, self.threshold, self.margin, 'ngram')


_ROUTER: NgramRouter | None = None


def get_router() -> NgramRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = NgramRouter()
    return _ROUTER


def classify(normalized: str, *, threshold: float | None = None, margin: float | None = None) -> SemanticMatch | None:
    router = get_router()
    match = router.classify(normalized)
    if match is not None and (threshold is not None or margin is not None):
        t = router.threshold if threshold is None else threshold
        m = router.margin if margin is None else margin
        match.confident = match.similarity >= t and match.margin >= m
    return match


def calibrate(examples: list[dict] | None = None) -> list[dict]:
    """Leave-one-out sweep on the training examples: precision / coverage per (threshold, margin)."""
    router = NgramRouter(examples, threshold=0.0, margin=0.0)
    results = []
    raw = [router.classify(normalize(t), exclude=i) for i, t in enumerate(router.texts)]
    for threshold in (0.3, 0.35, 0.4, 0.42, 0.45, 0.5, 0.55, 0.6):
        for margin in (0.0, 0.1, 0.2, 0.25, 0.3, 0.4):
            accepted = [(m, router.labels[i]) for i, m in enumerate(raw)
                        if m and m.similarity >= threshold and m.margin >= margin]
            correct = sum(1 for m, gold in accepted if m.intent == gold)
            results.append({'threshold': threshold, 'margin': margin, 'accepted': len(accepted),
                            'coverage': round(len(accepted) / max(1, len(raw)), 3),
                            'precision': round(correct / max(1, len(accepted)), 3)})
    return results


# --------------------------------------------------------------------------- embeddings (optional)

async def _embed(texts: list[str], model: str) -> list[list[float]] | None:
    if os.getenv('AURORA_OFFLINE') == '1':
        return None
    try:
        from aurora.providers.ollama import ollama  # lazy: owned by another module
        vectors = await ollama.embed(texts, model=model)
        return vectors or None
    except ImportError:
        pass
    except Exception:
        return None
    try:
        import httpx
        url = os.getenv('AURORA_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
        from ..netutil import ssl_context
        async with httpx.AsyncClient(timeout=60, trust_env=False, verify=ssl_context()) as client:
            response = await client.post(f'{url}/api/embed', json={'model': model, 'input': texts})
            response.raise_for_status()
            return response.json().get('embeddings') or None
    except Exception:
        return None


class EmbeddingRouter:
    """kNN on bge-m3 embeddings. Async; returns None whenever Ollama is unavailable."""

    def __init__(self, examples: list[dict] | None = None, *, model: str | None = None,
                 threshold: float | None = None, margin: float | None = None, k: int = K_NEIGHBOURS,
                 cache_dir: Path | None = None):
        self.examples = examples if examples is not None else load_examples()
        self.model = model or os.getenv('AURORA_EMBED_MODEL', 'bge-m3')
        self.threshold = EMBED_THRESHOLD if threshold is None else threshold
        self.margin = EMBED_MARGIN if margin is None else margin
        self.k = k
        self.labels = [e['intent'] for e in self.examples]
        self.texts = [e['text'] for e in self.examples]
        self.cache_dir = cache_dir
        self.matrix: np.ndarray | None = None
        self._lock = asyncio.Lock()

    def _cache_path(self) -> Path:
        digest = hashlib.sha256((self.model + '\n' + '\n'.join(self.texts)).encode('utf-8')).hexdigest()[:16]
        base = self.cache_dir or (aurora_home() / 'cache')
        return base / f'router_embeddings_{digest}.npy'

    async def ensure_index(self) -> bool:
        if self.matrix is not None:
            return True
        async with self._lock:
            if self.matrix is not None:
                return True
            path = self._cache_path()
            if path.exists():
                try:
                    matrix = np.load(path)
                    if matrix.shape[0] == len(self.texts):
                        self.matrix = matrix
                        return True
                except (OSError, ValueError):
                    pass
            vectors: list[list[float]] = []
            for start in range(0, len(self.texts), 32):
                chunk = await _embed(self.texts[start:start + 32], self.model)
                if not chunk or len(chunk) != len(self.texts[start:start + 32]):
                    return False
                vectors.extend(chunk)
            matrix = np.asarray(vectors, dtype=np.float32)
            matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
            self.matrix = matrix
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, matrix)
            except OSError:
                pass
            return True

    async def classify(self, text: str) -> SemanticMatch | None:
        if not text or not await self.ensure_index():
            return None
        vectors = await _embed([text], self.model)
        if not vectors:
            return None
        query = np.asarray(vectors[0], dtype=np.float32)
        query /= np.linalg.norm(query) + 1e-9
        sims = self.matrix @ query
        votes, idx = _vote(sims, self.labels, self.k)
        return _decide(votes, sims, self.labels, self.texts, idx, self.threshold, self.margin, 'embed')
