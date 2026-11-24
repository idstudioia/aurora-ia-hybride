"""Permission requests stored in `permission_requests` (CONTRACT.md §2, §3, §6).

Only the user grants a permission, through POST /api/permissions/{id} → `decide()`.
A request is decided once: later calls return the stored state unchanged. Pending requests
past `expires_at` become 'expired' and can no longer be granted.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .policy import ACTIONS

log = logging.getLogger(__name__)

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_DECISIONS = {'grant': 'granted', 'granted': 'granted', 'deny': 'denied', 'denied': 'denied'}
_RISKS = ('low', 'medium', 'high')


def _db() -> Any:
    from aurora import db  # lazy: db.py is owned by another module
    return db


def _to_dict(row: Any) -> dict:
    item = dict(row)
    raw = item.pop('preview_json', '{}')
    try:
        preview = json.loads(raw or '{}')
    except ValueError:
        preview = {}
    item['preview'] = preview if isinstance(preview, dict) else {'value': preview}
    return item


def _emit(kind: str, label: str, *, permission_id: int, task_id: int | None, payload: dict,
          level: str = 'info') -> None:
    """Publish through aurora.tasks.events (DB + SSE); fall back to a direct DB insert."""
    try:
        from aurora.tasks import events
    except ImportError:
        events = None
    try:
        if events is not None:
            events.emit(kind, label, subject_type='permission', subject_id=permission_id, run_id=task_id,
                        stage='permission', payload=payload, level=level)
            return
        db = _db()
        with db.connect() as conn:
            db.insert_event(conn, subject_type='permission', subject_id=permission_id, kind=kind, label=label,
                            run_id=task_id, stage='permission', payload=payload, level=level)
    except Exception:
        log.exception('Could not record event %s for permission %s', kind, permission_id)


def expire_stale() -> int:
    """Pending requests past expires_at become 'expired'; each emits permission.denied
    {reason: 'expired'} so the interface and the run learn it (the run is ended by the orchestrator)."""
    db = _db()
    with db.connect() as conn:
        stale = [dict(r) for r in conn.execute(
            f"SELECT id, task_id, action, title FROM permission_requests "
            f"WHERE status='pending' AND expires_at IS NOT NULL AND expires_at <= {NOW_SQL}")]
        if not stale:
            return 0
        conn.execute(
            f"UPDATE permission_requests SET status='expired', decided_at={NOW_SQL}, "
            f"reason='Autorisation expirée sans décision.' WHERE id IN ({','.join('?' * len(stale))}) "
            f"AND status='pending'", [r['id'] for r in stale])
    for item in stale:
        _emit('permission.denied', ('Autorisation expirée : ' + str(item['title'] or ''))[:200],
              permission_id=int(item['id']), task_id=item.get('task_id'),
              payload={'permission_id': int(item['id']), 'action': item['action'], 'reason': 'expired'},
              level='warn')
    return len(stale)


def create_request(*, task_id: int | None, step_id: int | None, action: str, title: str, preview: dict,
                   risk: str | None = None, ttl_minutes: float | None = 30) -> dict:
    """Store a pending request and emit `permission.requested`. `preview` is shown verbatim to the user."""
    if risk not in _RISKS:
        risk = ACTIONS.get(action, {}).get('risk', 'high')
    title = ' '.join(str(title or '').split())[:200] or ACTIONS.get(action, {}).get('label', 'Autorisation requise')
    preview = dict(preview or {})
    preview_json = json.dumps(preview, ensure_ascii=False, default=str)
    ttl_seconds = None if ttl_minutes is None else int(round(float(ttl_minutes) * 60))
    db = _db()
    with db.connect() as conn:
        if ttl_seconds is None:
            cur = conn.execute(
                'INSERT INTO permission_requests(task_id, step_id, action, risk, title, preview_json) '
                'VALUES (?, ?, ?, ?, ?, ?)', (task_id, step_id, action, risk, title, preview_json))
        else:
            cur = conn.execute(
                'INSERT INTO permission_requests(task_id, step_id, action, risk, title, preview_json, expires_at) '
                f"VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now', ?))",
                (task_id, step_id, action, risk, title, preview_json, f'{ttl_seconds:+d} seconds'))
        permission_id = int(cur.lastrowid)
        row = conn.execute('SELECT * FROM permission_requests WHERE id=?', (permission_id,)).fetchone()
    item = _to_dict(row)
    _emit('permission.requested', title, permission_id=permission_id, task_id=task_id,
          payload={'permission_id': permission_id, 'action': action, 'risk': risk, 'title': title,
                   'preview': preview})
    return item


def decide(permission_id: int, decision: str, reason: str = '') -> dict | None:
    """Apply 'grant' or 'deny' to a pending request. Returns the stored request (None if unknown)."""
    status = _DECISIONS.get(str(decision or '').strip().lower())
    if status is None:
        raise ValueError("Décision invalide : 'grant' ou 'deny' attendu.")
    expire_stale()
    db = _db()
    with db.connect() as conn:
        cur = conn.execute(
            f"UPDATE permission_requests SET status=?, reason=?, decided_at={NOW_SQL} WHERE id=? AND status='pending'",
            (status, str(reason or '')[:500], int(permission_id)))
        changed = bool(cur.rowcount)
        row = conn.execute('SELECT * FROM permission_requests WHERE id=?', (int(permission_id),)).fetchone()
    if row is None:
        return None
    item = _to_dict(row)
    if changed:
        kind = 'permission.granted' if status == 'granted' else 'permission.denied'
        label = ('Autorisation accordée : ' if status == 'granted' else 'Autorisation refusée : ') + item['title']
        _emit(kind, label[:200], permission_id=item['id'], task_id=item.get('task_id'),
              payload={'permission_id': item['id'], 'action': item['action']},
              level='info' if status == 'granted' else 'warn')
    return item


def get(permission_id: int) -> dict | None:
    expire_stale()
    row = _db().row('SELECT * FROM permission_requests WHERE id=?', (int(permission_id),))
    return _to_dict(row) if row else None


def is_granted(permission_id: int | None) -> bool:
    if permission_id is None:
        return False
    item = get(permission_id)
    return bool(item and item['status'] == 'granted')


def pending() -> list[dict]:
    expire_stale()
    return [_to_dict(r) for r in _db().rows("SELECT * FROM permission_requests WHERE status='pending' ORDER BY id")]


def for_task(task_id: int) -> list[dict]:
    expire_stale()
    return [_to_dict(r) for r in _db().rows('SELECT * FROM permission_requests WHERE task_id=? ORDER BY id',
                                            (int(task_id),))]


# --- standing cloud.send consent for a whole conversation (CLAUDE.md, modes Équilibré and Avancé) ---
# Each later send still creates its own request with the exact preview (journal + interface);
# the standing consent only grants it without waiting. Revocable at any time; cleared when the
# mode becomes Privé. Sensitive data always needs a separate consent (checked by the orchestrator).
# Équilibré added on 04/11/2026 (ID Studio IA: « si je donne autorisation pas besoin de la redemander »).

CONVERSATION_KEY = 'cloud.conversation_consents'   # settings row: {"<conversation_id>": {...}}


def _conversation_consents() -> dict:
    value = _db().get_setting(CONVERSATION_KEY, {})
    return value if isinstance(value, dict) else {}


def conversation_consent(conversation_id: int | None) -> dict | None:
    if not conversation_id:
        return None
    item = _conversation_consents().get(str(int(conversation_id)))
    return dict(item) if isinstance(item, dict) else None


def grant_conversation(conversation_id: int, permission_id: int) -> dict:
    from aurora.tasks.runs import now_iso
    consents = _conversation_consents()
    item = {'conversation_id': int(conversation_id), 'permission_id': int(permission_id), 'granted_at': now_iso()}
    consents[str(int(conversation_id))] = item
    _db().set_setting(CONVERSATION_KEY, consents)
    _emit('permission.granted', 'Envois à Claude autorisés pour toute la conversation', permission_id=int(permission_id),
          task_id=None, payload={'permission_id': int(permission_id), 'action': 'cloud.send', 'scope': 'conversation',
                                 'conversation_id': int(conversation_id)})
    return item


def revoke_conversation(conversation_id: int, reason: str = 'Révoqué par l’utilisateur') -> bool:
    consents = _conversation_consents()
    item = consents.pop(str(int(conversation_id)), None)
    if item is None:
        return False
    _db().set_setting(CONVERSATION_KEY, consents)
    _emit('permission.revoked', f'Accord de conversation révoqué : {reason}'[:200],
          permission_id=int(item.get('permission_id') or 0), task_id=None, level='warn',
          payload={'action': 'cloud.send', 'scope': 'conversation', 'conversation_id': int(conversation_id),
                   'reason': reason})
    return True


def revoke_all_conversations(reason: str) -> int:
    return sum(revoke_conversation(int(cid), reason) for cid in list(_conversation_consents()))
