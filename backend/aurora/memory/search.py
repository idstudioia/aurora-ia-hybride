"""Memory search: FTS5/BM25 keyword search (sync) and hybrid keyword + vector search (async).

Keyword side: the user query is reduced to its significant French terms (stop words and
trigger verbs removed), each term becomes a quoted FTS5 token or prefix (never raw FTS
syntax), light typos are corrected against the index vocabulary. Scores are normalised
to 0..1 from term coverage (title weighted) and relative BM25.

Semantic side (optional): cosine similarity on ``memory_embeddings`` when a query vector
can be computed locally (Ollama bge-m3, see embeddings.py). Both rankings are fused with
Reciprocal Rank Fusion.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from .. import db

AUTO_LINK_THRESHOLD = 0.45
RRF_K = 60
_MAX_QUERY_TERMS = 12
_PREFIX_SLACK = 4          # a prefix term matches words at most this many letters longer
_TYPO_CREDIT = 0.7         # coverage credit of a term matched only through typo correction

_STOPWORDS = frozenset("""
a ai as au aux avec avait avais avez avons c ca ce ceci cela celle celles celui ces cet cette
ceux d dans de des du elle elles en est et etait ete etre eu fait faire il ils j je l la le les
leur leurs lui m ma mais me meme mes moi mon n ne ni nos notre nous on ont ou par pas peu plus
pour qu que quel quelle quelles quels qui quoi s sa sans se ses si son sont sur t ta te tes toi
ton tous tout toute toutes tres tu un une vos votre vous y
comment pourquoi combien quand quoi suis es sommes etes sera serait etait etaient avoir eux
cela ici la-bas alors donc car comme aussi encore deja
bonjour bonsoir salut coucou hello hey merci stp svp please
""".split())

# Verbs and fillers that introduce a memory lookup ("retrouve notre ancien projet...").
_TRIGGERS = frozenset("""
retrouve retrouver retrouves retrouvez cherche chercher cherches cherchez souviens souvenir
souvenirs souvient rappelle rappeler rappelles rappelez rappel ancien ancienne anciens anciennes
dis dire parle parler parlais parlions avions concernant propos sujet memoire memoires stp svp
peux pourrais pourrait sais savoir quelque chose explique expliquer expliques
""".split())

# Ambiguous words that are only a trigger when they open the query ("recherche mes notes").
# Request verbs ("analyse ce texte", "utilise Claude pour…") are listed so that a chat request
# does not recall memories just because they contain the same verb.
_LEADING_TRIGGERS = frozenset('recherche rechercher montre montrer affiche afficher trouve trouver '
                              'donne donner aurora astra analyse analyser utilise utiliser resume resumer '
                              'fais faire aide'.split())

_TOKEN_RE = re.compile(r'[^\W_]+', re.UNICODE)


@dataclass
class MemoryHit:
    memory: dict
    score: float
    matched_by: list[str]
    snippet: str

    def to_dict(self) -> dict:
        return {'memory': dict(self.memory), 'score': self.score,
                'matched_by': list(self.matched_by), 'snippet': self.snippet}


# --------------------------------------------------------------------------- text utilities

def fold(text: str) -> str:
    """Lower-case and strip diacritics, like the FTS5 ``unicode61 remove_diacritics 2`` tokenizer."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(fold(text))


def light_stem(token: str) -> str:
    """Very light French stemming used for prefix queries (plural / final e)."""
    if len(token) >= 5 and token[-1] in 'sx':
        token = token[:-1]
    if len(token) >= 6 and token[-1] == 'e':
        token = token[:-1]
    return token


def query_terms(query: str) -> list[str]:
    """Significant terms of a user query (stop words and trigger verbs removed)."""
    tokens = tokenize(query)
    while tokens and tokens[0] in _LEADING_TRIGGERS:
        tokens = tokens[1:]
    seen: list[str] = []
    for tok in tokens:
        if tok in _STOPWORDS or tok in _TRIGGERS:
            continue
        if len(tok) < 2 and not tok.isdigit():
            continue
        if tok not in seen:
            seen.append(tok)
    return seen[:_MAX_QUERY_TERMS]


def content_terms(text: str) -> set[str]:
    """Stemmed significant words of a memory text (used for memory-to-memory similarity)."""
    return {light_stem(t) for t in tokenize(text)
            if t not in _STOPWORDS and (len(t) >= 3 or t.isdigit())}


def _variants(token: str) -> list[str]:
    # unicode61 keeps ligatures: "coeur" and "cœur" are different index terms.
    out = [token]
    for plain, lig in (('oe', 'œ'), ('ae', 'æ')):
        if plain in token:
            out.append(token.replace(plain, lig))
        if lig in token:
            out.append(token.replace(lig, plain))
    return out


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


@dataclass
class _Term:
    token: str
    exact: list[str]        # whole index terms
    prefixes: list[str]     # index-term prefixes
    typos: list[str]        # vocabulary corrections (partial credit)

    def fts_parts(self) -> list[str]:
        parts = [_quote(t) for t in self.exact]
        parts += [_quote(p) + '*' for p in self.prefixes]
        parts += [_quote(t) for t in self.typos]
        return parts

    def credit(self, words: set[str]) -> float:
        if any(w in words for w in self.exact):
            return 1.0
        for p in self.prefixes:
            if any(w.startswith(p) and len(w) <= len(p) + _PREFIX_SLACK for w in words):
                return 1.0
        if any(w in words for w in self.typos):
            return _TYPO_CREDIT
        return 0.0


def _build_term(token: str) -> _Term:
    if len(token) <= 3 or token.isdigit():
        return _Term(token, _variants(token), [], [])
    if len(token) == 4:
        return _Term(token, _variants(token) + [token + 's'], [], [])
    return _Term(token, [], _variants(light_stem(token)), [])


# --------------------------------------------------------------------------- typo correction

def _ensure_vocab(conn: sqlite3.Connection) -> None:
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS temp.memories_vocab '
                 'USING fts5vocab(main, memories_fts, row)')


def _osa_distance(a: str, b: str, cap: int) -> int:
    """Optimal string alignment distance (Levenshtein + adjacent transposition), capped."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _term_in_vocab(conn: sqlite3.Connection, term: _Term) -> bool:
    for exact in term.exact:
        if conn.execute('SELECT 1 FROM temp.memories_vocab WHERE term=?', (exact,)).fetchone():
            return True
    for prefix in term.prefixes:
        upper = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        found = conn.execute('SELECT term FROM temp.memories_vocab WHERE term>=? AND term<? LIMIT 64',
                             (prefix, upper)).fetchall()
        if any(len(r[0]) <= len(prefix) + _PREFIX_SLACK for r in found):
            return True
    return False


def _typo_candidates(conn: sqlite3.Connection, token: str) -> list[str]:
    """Close vocabulary terms (same first letter, distance 1, or 2 for long words)."""
    if len(token) < 4 or token.isdigit():
        return []
    cap = 2 if len(token) >= 8 else 1
    first = token[0]
    upper = chr(ord(first) + 1)
    found = conn.execute(
        'SELECT term, doc FROM temp.memories_vocab WHERE term>=? AND term<? '
        'AND length(term) BETWEEN ? AND ?',
        (first, upper, len(token) - cap, len(token) + cap)).fetchall()
    scored = []
    for term, doc_count in found:
        dist = _osa_distance(token, term, cap)
        if dist <= cap:
            scored.append((dist, -doc_count, term))
    scored.sort()
    return [t for _, _, t in scored[:2]]


def _prepare_terms(conn: sqlite3.Connection, query: str) -> list[_Term]:
    terms = [_build_term(tok) for tok in query_terms(query)]
    if not terms:
        return []
    _ensure_vocab(conn)
    for term in terms:
        if not _term_in_vocab(conn, term):
            term.typos = _typo_candidates(conn, term.token)
    return terms


# --------------------------------------------------------------------------- keyword search

def _fts_rows(conn: sqlite3.Connection, match: str, limit: int, exclude_id: int | None = None):
    sql = ('SELECT m.*, bm25(memories_fts, 8.0, 3.0, 1.5) AS _bm25, '
           "snippet(memories_fts, 1, '', '', '…', 16) AS _snippet "
           'FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid '
           'WHERE memories_fts MATCH ?')
    params: list = [match]
    if exclude_id is not None:
        sql += ' AND m.id != ?'
        params.append(exclude_id)
    sql += ' ORDER BY _bm25 LIMIT ?'
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _fallback_snippet(memory: dict, size: int = 160) -> str:
    text = (memory.get('detail') or memory.get('title') or '').strip()
    return text if len(text) <= size else text[:size].rstrip() + '…'


def search(query: str, *, limit: int = 8, mode: str = 'hybrid', min_score: float = 0.0) -> list[MemoryHit]:
    """Synchronous keyword search (FTS5 BM25). ``mode`` is accepted for API symmetry."""
    limit = max(1, min(int(limit or 8), 200))
    with db.connect() as conn:
        terms = _prepare_terms(conn, query)
        parts = [p for term in terms for p in term.fts_parts()]
        if not parts:
            return []
        found = _fts_rows(conn, ' OR '.join(parts), max(limit * 4, 24))
    if not found:
        return []
    raws = [max(0.0, -float(r['_bm25'])) for r in found]
    top_raw = max(raws)
    hits: list[MemoryHit] = []
    for record, raw in zip(found, raws):
        memory = dict(record)
        snippet = memory.pop('_snippet', '') or ''
        memory.pop('_bm25', None)
        title_words = set(tokenize(memory['title']))
        all_words = title_words | set(tokenize(memory['detail'])) | set(tokenize(memory['category']))
        coverage = sum(t.credit(all_words) for t in terms) / len(terms)
        if coverage <= 0:
            continue  # matched only through an over-long prefix
        title_cov = sum(t.credit(title_words) for t in terms) / len(terms)
        rel = raw / top_raw if top_raw > 1e-9 else 1.0
        score = round(min(1.0, 0.65 * coverage + 0.2 * title_cov + 0.15 * rel), 4)
        if score < min_score:
            continue
        hits.append(MemoryHit(memory, score, ['fts'], snippet.strip() or _fallback_snippet(memory)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def similar_memories(memory_id: int, *, limit: int = 3, threshold: float = AUTO_LINK_THRESHOLD,
                     conn: sqlite3.Connection | None = None) -> list[tuple[dict, float]]:
    """Memories close to ``memory_id`` by shared vocabulary, for automatic 'related' links.

    Candidates come from FTS5 (OR of the memory's terms); similarity mixes the Dice
    coefficient of both term sets and how much of one title is covered by the other memory.
    """
    if conn is None:
        with db.connect() as own:
            return similar_memories(memory_id, limit=limit, threshold=threshold, conn=own)
    base = conn.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
    if base is None:
        return []
    base_title = content_terms(base['title'])
    base_all = base_title | content_terms(base['detail'])
    if not base_all:
        return []
    ordered = sorted(base_title) + sorted(base_all - base_title)
    parts = []
    for stem in ordered[:32]:
        parts.append(_quote(stem) + ('*' if len(stem) >= 5 else ''))
    found = _fts_rows(conn, ' OR '.join(parts), 25, exclude_id=memory_id)
    scored: list[tuple[dict, float]] = []
    for record in found:
        other = dict(record)
        other.pop('_bm25', None)
        other.pop('_snippet', None)
        other_title = content_terms(other['title'])
        other_all = other_title | content_terms(other['detail'])
        if not other_all:
            continue
        common = base_all & other_all
        if not common:
            continue
        dice = 2 * len(common) / (len(base_all) + len(other_all))
        cover_a = len(base_title & other_all) / len(base_title) if base_title else 0.0
        cover_b = len(other_title & base_all) / len(other_title) if other_title else 0.0
        similarity = round(0.5 * dice + 0.5 * max(cover_a, cover_b), 4)
        if similarity >= threshold:
            scored.append((other, similarity))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


# --------------------------------------------------------------------------- hybrid search

async def semantic_search(query: str, *, limit: int = 24, backfill_missing: int = 0
                          ) -> list[tuple[dict, float, float]] | None:
    """[(memory, cosine, normalised score)] or None when no query vector can be computed.

    Ollama just answered, so memories still lacking a vector are embedded: right now when
    ``backfill_missing`` > 0, otherwise in a background task that this call does not wait for.
    """
    from . import embeddings
    vectors = await embeddings.embed_texts([query])
    if not vectors:
        return None
    if backfill_missing > 0:
        await embeddings.backfill(limit=backfill_missing)
    else:
        embeddings.schedule_backfill()
    return embeddings.nearest(vectors[0], limit=limit)


def _fuse(fts_hits: list[MemoryHit], semantic: list[tuple[dict, float, float]], limit: int) -> list[MemoryHit]:
    by_id: dict[int, dict] = {}
    for rank, hit in enumerate(fts_hits):
        entry = by_id.setdefault(hit.memory['id'], {'memory': hit.memory, 'rrf': 0.0, 'fts': 0.0,
                                                    'sem': 0.0, 'snippet': hit.snippet, 'by': []})
        entry['rrf'] += 1.0 / (RRF_K + rank + 1)
        entry['fts'] = hit.score
        entry['by'].append('fts')
    for rank, (memory, _cosine, score) in enumerate(semantic):
        entry = by_id.setdefault(memory['id'], {'memory': memory, 'rrf': 0.0, 'fts': 0.0, 'sem': 0.0,
                                                'snippet': _fallback_snippet(memory), 'by': []})
        entry['rrf'] += 1.0 / (RRF_K + rank + 1)
        entry['sem'] = score
        entry['by'].append('semantic')
    fused = sorted(by_id.values(), key=lambda e: (e['rrf'], max(e['fts'], e['sem'])), reverse=True)
    hits = []
    for entry in fused[:limit]:
        best, other = max(entry['fts'], entry['sem']), min(entry['fts'], entry['sem'])
        score = round(min(1.0, best + 0.15 * other), 4)
        hits.append(MemoryHit(entry['memory'], score, entry['by'], entry['snippet']))
    return hits


async def search_async(query: str, *, limit: int = 8, mode: str = 'hybrid', min_score: float = 0.0,
                       backfill_missing: int = 0) -> list[MemoryHit]:
    """Hybrid search: FTS5 + cosine similarity when embeddings are available (RRF fusion).

    Without Ollama/embeddings it silently returns the keyword results (``matched_by`` tells
    which method really matched). ``backfill_missing`` > 0 embeds that many memories that
    still lack a vector before the semantic lookup.
    """
    if not (query or '').strip():
        return []
    limit = max(1, min(int(limit or 8), 200))
    pool = max(limit * 3, 24)
    fts_hits = search(query, limit=pool)
    if mode == 'fts':
        return [h for h in fts_hits if h.score >= min_score][:limit]
    semantic = await semantic_search(query, limit=pool, backfill_missing=backfill_missing)
    if semantic is None:
        return [h for h in fts_hits if h.score >= min_score][:limit]
    if mode == 'semantic':
        fts_hits = []
    hits = _fuse(fts_hits, semantic, pool)
    return [h for h in hits if h.score >= min_score][:limit]
