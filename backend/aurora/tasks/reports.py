"""« Signaler » (H3): the user's verdict on an answer, kept as a case for the routing bench.

Logic ported from Athéna's signalementService (not its code):
- the user's click is the verdict: a report counts at once, nothing is « to be decided »;
- one report per run (UNIQUE run_id): reporting again corrects it, it never makes a second case;
- deleting a report cancels it, and the export follows.

The user's sentence is masked by security.redaction before it is stored when the message was marked
sensitive (orchestrator meta['sensitive']) or holds a secret. Nothing here writes into the repository:
export_jsonl() only returns the text of a download, in the format of tests/data/routing_bench.jsonl.
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from .. import db
from ..core import intents
from ..security import redaction
from . import runs

log = logging.getLogger(__name__)

REASONS: dict[str, str] = {
    'misunderstood': 'Mauvaise compréhension de la demande',
    'wrong_answer': 'Réponse fausse',
    'off_topic': 'Hors sujet',
    'other': 'Autre',
}
MAX_COMMENT = 1000
# Bench tags (tests/bench_routing.TAGS): a dictated sentence keeps its voice errors.
VOICE_TAG, DEFAULT_TAG = 'voix', 'normal'
# Why a report is (or is not) a line of the export. Order = order of the checks.
EXPORT_STATES: dict[str, str] = {
    'ok': 'Exportable vers le banc',
    'no_expected': 'Type de demande attendu non précisé',
    'explicit': 'Intention imposée par un bouton ou une option : le routeur n’a pas choisi',
    'attachment': 'Demande avec un document joint : le banc ne rejoue que la phrase',
    'sensitive': 'Phrase sensible que le masquage n’a pas pu nettoyer : jamais exportée',
    'duplicate': 'Même phrase qu’un signalement plus récent',
    'in_bench': 'Phrase déjà présente dans le banc',
    'in_training': 'Phrase identique à un exemple d’entraînement du routeur',
}
_OPEN_RUN = ('pending', 'running')
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


class RunInProgress(RuntimeError):
    """The run has not finished yet: its answer cannot be judged."""


# --------------------------------------------------------------------------- write

def save_report(run_id: int, reason: str, expected_intent: str | None = None, comment: str = '') -> dict | None:
    """Create or correct the report of a run. None when the run does not exist.

    Raises ValueError (unknown reason or intent) and RunInProgress (run not finished)."""
    if reason not in REASONS:
        raise ValueError(f'Raison de signalement inconnue : {reason}')
    expected = (expected_intent or '').strip()
    if expected and not intents.is_intent(expected):
        raise ValueError(f'Intention inconnue : {expected}')
    run = runs.get_run(int(run_id))
    if run is None or run.get('kind') == 'manual':
        return None
    if run.get('status') in _OPEN_RUN:
        raise RunInProgress('La demande est encore en cours : signalez la réponse une fois terminée.')
    text, sensitive, masked = _user_sentence(run)
    dj = runs.decision_of(run)
    decision = dj.get('decision') if isinstance(dj.get('decision'), dict) else {}
    got = str(run.get('intent') or decision.get('intent') or '')
    clean_comment, _ = redaction.redact(str(comment or '').strip()[:MAX_COMMENT])
    with db.connect() as conn:
        conn.execute(
            'INSERT INTO run_reports(run_id, conversation_id, text, sensitive, masked, source, attachments,'
            ' got_intent, method, expected_intent, reason, comment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)'
            ' ON CONFLICT(run_id) DO UPDATE SET text=excluded.text, sensitive=excluded.sensitive,'
            ' masked=excluded.masked, got_intent=excluded.got_intent, method=excluded.method,'
            ' expected_intent=excluded.expected_intent, reason=excluded.reason, comment=excluded.comment,'
            f' updated_at={_NOW}',
            (int(run_id), run.get('conversation_id'), text, int(sensitive), int(masked),
             str(run.get('source') or ''), len(dj.get('attachments') or []), got,
             str(decision.get('method') or ''), expected, reason, clean_comment))
        found = conn.execute('SELECT * FROM run_reports WHERE run_id=?', (int(run_id),)).fetchone()
    return _view(dict(found))


def delete_report(run_id: int) -> bool:
    """Cancel the report of a run. False when there was none."""
    with db.connect() as conn:
        return conn.execute('DELETE FROM run_reports WHERE run_id=?', (int(run_id),)).rowcount > 0


def _user_sentence(run: dict) -> tuple[str, bool, bool]:
    """(sentence to store, sensitive, masked). Masked by redact() when sensitive or holding a secret."""
    text = str(run.get('input_text') or run.get('title') or '').strip()
    message = db.row("SELECT meta_json FROM messages WHERE run_id=? AND role='user' ORDER BY id LIMIT 1",
                     (int(run['id']),))
    meta = _loads(message['meta_json'] if message else None)
    # The orchestrator's mark first; sensitivity_of() again for a run without stored message.
    sensitive = bool(meta.get('sensitive')) or redaction.sensitivity_of(text) == 'sensitive'
    if not sensitive:
        return text, False, False
    masked, _ = redaction.redact(text)
    return masked, True, masked != text


# --------------------------------------------------------------------------- read

def get_report(run_id: int) -> dict | None:
    found = db.row('SELECT * FROM run_reports WHERE run_id=?', (int(run_id),))
    return _view(found) if found else None


def list_reports(limit: int = 500) -> list[dict]:
    """Newest first, each with its export state (``export``) for the bench."""
    found = db.rows('SELECT * FROM run_reports ORDER BY updated_at DESC, id DESC LIMIT ?',
                    (max(1, min(int(limit or 500), 5000)),))
    items = [_view(item) for item in found]
    _mark_export_states(items)
    return items


def bench_rows(limit: int = 5000) -> list[dict]:
    """The « ok » reports as bench rows, oldest first: an expected intent given by the user, chosen by the
    router from the sentence alone, not already in the bench nor in the training examples, each sentence once.

    Read by export_jsonl() and replayed by the « Lancer le banc d'essai » button (A1, main._run_bench):
    ``run_id`` and ``got_intent`` (understood when it was reported) ride along with the corpus keys."""
    return [{'text': item['text'], 'intent': item['expected_intent'], 'tag': item['tag'],
             'run_id': item['run_id'], 'got_intent': item['got_intent']}
            for item in reversed(list_reports(limit)) if item['export'] == 'ok']


def export_jsonl(limit: int = 5000) -> tuple[str, int]:
    """(JSONL text, line count) in the format of tests/data/routing_bench.jsonl, oldest first (bench_rows)."""
    lines = [json.dumps({'text': row['text'], 'intent': row['intent'], 'tag': row['tag']}, ensure_ascii=False)
             for row in bench_rows(limit)]
    return ''.join(f'{line}\n' for line in lines), len(lines)


def _view(row: dict) -> dict:
    item = dict(row)
    item['sensitive'] = bool(item.get('sensitive'))
    item['masked'] = bool(item.get('masked'))
    item['expected_intent'] = item.get('expected_intent') or None
    item['reason_label'] = REASONS.get(item.get('reason'), item.get('reason'))
    item['tag'] = VOICE_TAG if item.get('source') == 'voice' else DEFAULT_TAG
    return item


def _mark_export_states(items: list[dict]) -> None:
    """``export`` / ``export_label`` on each report (newest first: the latest of a sentence wins)."""
    normalize = _normalizer()
    bench, training = _known_sentences(normalize)
    seen: set[str] = set()
    for item in items:
        key = normalize(item['text'])
        if not item['expected_intent']:
            state = 'no_expected'
        elif item.get('method') == 'explicit':
            state = 'explicit'
        elif item.get('attachments'):
            state = 'attachment'
        elif item['sensitive'] and not item['masked']:
            state = 'sensitive'
        elif key in seen:
            state = 'duplicate'
        elif key in bench:
            state = 'in_bench'
        elif key in training:
            state = 'in_training'
        else:
            state = 'ok'
            seen.add(key)
        item['export'] = state
        item['export_label'] = EXPORT_STATES[state]


def _normalizer():
    """Same comparison as tests/test_router.BenchCorpusTests (core.normalize), else a plain fold."""
    try:
        return importlib.import_module('aurora.core.normalize').normalize
    except Exception:
        log.exception('Normalisation du routeur indisponible')
        return lambda text: ' '.join(str(text or '').lower().split())


def _known_sentences(normalize) -> tuple[set[str], set[str]]:
    """Normalised sentences of the bench corpus and of the router training examples (read only)."""
    bench: set[str] = set()
    training: set[str] = set()
    try:
        # By file path, never « tests.bench_routing »: core/bench.py explains why.
        bench = {normalize(r['text']) for r in importlib.import_module('aurora.core.bench').module().load_bench()}
    except Exception:   # packaged without tests/: the export is still built, unchecked against the bench
        log.warning('Corpus du banc illisible : doublons avec le banc non vérifiés')
    try:
        training = {normalize(r['text']) for r in importlib.import_module('aurora.core.semantic').load_examples()}
    except Exception:
        log.warning('Exemples d’entraînement illisibles : chevauchement non vérifié')
    return bench, training


def _loads(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
