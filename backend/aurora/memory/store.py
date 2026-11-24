"""Memory store: CRUD, links, recall tracking, the 4-level memory graph, conversations.

Each write records its events in the same transaction (``aurora.tasks.events.record``,
B5, or ``db.insert_event`` when that module is absent) and publishes them to SSE after the
commit: a memory and its events are saved together or not at all, in a single commit.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3

from .. import db
from . import search as _search


EDITABLE_FIELDS = ('title', 'detail', 'category', 'kind', 'source', 'source_ref',
                   'confidentiality', 'verification', 'pinned')
MESSAGE_ROLES = ('user', 'assistant', 'note')
LEVELS = (('immediate', 'Mémoire immédiate'), ('persistent', 'Mémoire persistante'),
          ('semantic', 'Mémoire sémantique'), ('actions', 'Mémoire des actions'))
RUN_KINDS = ('request', 'plan', 'diagnostic')
_MAX_TITLE, _MAX_DETAIL, _MAX_CATEGORY = 300, 50_000, 60
_ISO_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z')
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
_SENSITIVE_LABEL = 'Souvenir sensible'


# --------------------------------------------------------------------------- events

def _events_module():
    try:
        return importlib.import_module('aurora.tasks.events')
    except ImportError:
        return None


def _record(conn: sqlite3.Connection, kind: str, label: str, *, subject_id: int = 0,
            run_id: int | None = None, payload: dict | None = None, subject_type: str = 'memory',
            stage: str = 'memory', level: str = 'info') -> dict:
    """Insert an event inside the caller's open transaction and return it."""
    module = _events_module()
    record = getattr(module, 'record', None) if module is not None else None
    if callable(record):
        return record(conn, kind, label, subject_type=subject_type, subject_id=subject_id, run_id=run_id,
                      stage=stage, payload=payload, level=level)
    return db.insert_event(conn, subject_type=subject_type, subject_id=subject_id, kind=kind,
                           label=label, run_id=run_id, stage=stage, payload=payload, level=level)


def _publish(events: list[dict]) -> None:
    for event in events:
        db.publish_event(event)


def _public_title(memory: dict) -> str:
    return _SENSITIVE_LABEL if memory.get('confidentiality') == 'sensitive' else memory['title']


# --------------------------------------------------------------------------- validation

def _validated(fields: dict) -> dict:
    out: dict = {}
    for key, value in fields.items():
        if key == 'title':
            value = str(value or '').strip()
            if not value:
                raise ValueError('Le titre ne peut pas être vide.')
            if len(value) > _MAX_TITLE:
                raise ValueError(f'Titre trop long ({_MAX_TITLE} caractères maximum).')
        elif key == 'detail':
            value = str(value or '')
            if len(value) > _MAX_DETAIL:
                raise ValueError(f'Détail trop long ({_MAX_DETAIL} caractères maximum).')
        elif key == 'category':
            value = str(value or '').strip() or 'general'
            if len(value) > _MAX_CATEGORY:
                raise ValueError(f'Catégorie trop longue ({_MAX_CATEGORY} caractères maximum).')
        elif key == 'kind':
            if value not in db.MEMORY_KINDS:
                raise ValueError(f'Type de souvenir invalide : {value}')
        elif key == 'confidentiality':
            if value not in db.CONFIDENTIALITY_LEVELS:
                raise ValueError(f'Niveau de confidentialité invalide : {value}')
        elif key == 'verification':
            if value not in db.VERIFICATION_STATES:
                raise ValueError(f'Statut de vérification invalide : {value}')
        elif key == 'pinned':
            value = 1 if value else 0
        elif key == 'source':
            value = str(value or '').strip() or 'user'
        elif key == 'source_ref':
            value = str(value or '').strip()
        out[key] = value
    return out


def _marks(values) -> str:
    return ','.join('?' * len(values))


def _get(conn: sqlite3.Connection, memory_id: int) -> dict | None:
    found = conn.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
    return dict(found) if found is not None else None


# --------------------------------------------------------------------------- links

def _link_row(conn: sqlite3.Connection, source_id: int, target_id: int, relation: str) -> dict:
    return dict(conn.execute('SELECT * FROM memory_links WHERE source_id=? AND target_id=? AND relation=?',
                             (source_id, target_id, relation)).fetchone())


def _auto_link(conn: sqlite3.Connection, memory_id: int) -> list[tuple[dict, dict]]:
    """Create 'related' auto links to the closest memories. Returns [(other_memory, link)]."""
    created = []
    for other, similarity in _search.similar_memories(memory_id, conn=conn):
        exists = conn.execute(
            "SELECT 1 FROM memory_links WHERE relation='related' AND "
            '((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))',
            (memory_id, other['id'], other['id'], memory_id)).fetchone()
        if exists:
            continue
        conn.execute("INSERT INTO memory_links(source_id, target_id, relation, weight, created_by) "
                     "VALUES (?, ?, 'related', ?, 'auto')", (memory_id, other['id'], similarity))
        created.append((other, _link_row(conn, memory_id, other['id'], 'related')))
    return created


def _link_events(conn: sqlite3.Connection, memory: dict, links: list[tuple[dict, dict]],
                 run_id: int | None) -> list[dict]:
    return [_record(conn, 'memory.linked', f'Lien : {_public_title(memory)} ↔ {_public_title(other)}',
                    subject_id=memory['id'], run_id=run_id,
                    payload={'memory_id': memory['id'], 'source_id': link['source_id'],
                             'target_id': link['target_id'], 'relation': link['relation'],
                             'weight': link['weight'], 'created_by': link['created_by'],
                             'title': _public_title(memory), 'category': memory['category']})
            for other, link in links]


def link_memories(source_id: int, target_id: int, relation: str = 'related', weight: float = 0.5,
                  created_by: str = 'user') -> dict:
    """Create or update a link (a user link overrides an automatic one). Emits ``memory.linked``.

    Raises ValueError for an invalid relation/weight/self link, LookupError for a missing memory.
    """
    if relation not in db.LINK_RELATIONS:
        raise ValueError(f'Relation invalide : {relation}')
    if created_by not in ('user', 'auto'):
        raise ValueError(f'Origine de lien invalide : {created_by}')
    weight = float(weight)
    if not 0.0 <= weight <= 1.0:
        raise ValueError('Le poids d’un lien doit être compris entre 0 et 1.')
    source_id, target_id = int(source_id), int(target_id)
    if source_id == target_id:
        raise ValueError('Un souvenir ne peut pas être lié à lui-même.')
    with db.connect() as conn:
        source = _get(conn, source_id)
        target = _get(conn, target_id)
        if source is None or target is None:
            raise LookupError('Souvenir introuvable.')
        conn.execute(
            'INSERT INTO memory_links(source_id, target_id, relation, weight, created_by) VALUES (?,?,?,?,?) '
            'ON CONFLICT(source_id, target_id, relation) DO UPDATE SET weight=excluded.weight, '
            "created_by=CASE WHEN memory_links.created_by='user' THEN 'user' ELSE excluded.created_by END",
            (source_id, target_id, relation, weight, created_by))
        link = _link_row(conn, source_id, target_id, relation)
        events = _link_events(conn, source, [(target, link)], None)
    _publish(events)
    return link


# --------------------------------------------------------------------------- memories

def add_memory(title: str, detail: str = '', category: str = 'general', *, kind: str = 'fact',
               source: str = 'user', source_ref: str = '', confidentiality: str = 'private',
               verification: str = 'unverified', pinned: bool = False, run_id: int | None = None,
               auto_link: bool = True, created_at: str | None = None, updated_at: str | None = None) -> dict:
    """Save a memory; emits ``memory.saved`` then one ``memory.linked`` per automatic link.
    created_at / updated_at (ISO UTC, « …Z »): the original dates of an imported memory (Athéna), else now."""
    fields = _validated({'title': title, 'detail': detail, 'category': category, 'kind': kind,
                         'source': source, 'source_ref': source_ref,
                         'confidentiality': confidentiality, 'verification': verification,
                         'pinned': pinned})
    if run_id is not None and not fields['source_ref']:
        fields['source_ref'] = str(run_id)
    for name, value in (('created_at', created_at), ('updated_at', updated_at or created_at)):
        if value is not None:
            if not _ISO_UTC.fullmatch(str(value)):
                raise ValueError(f'Date invalide pour {name} : attendu AAAA-MM-JJTHH:MM:SS(.mmm)Z.')
            fields[name] = str(value)
    columns = ', '.join(fields)
    with db.connect() as conn:
        cur = conn.execute(f'INSERT INTO memories({columns}) VALUES ({", ".join("?" * len(fields))})',
                           list(fields.values()))
        memory_id = cur.lastrowid
        links = _auto_link(conn, memory_id) if auto_link else []
        memory = _get(conn, memory_id)
        events = [_record(conn, 'memory.saved', _public_title(memory), subject_id=memory_id, run_id=run_id,
                          payload={'memory_id': memory_id, 'title': _public_title(memory),
                                   'category': memory['category'], 'kind': memory['kind'],
                                   'source': memory['source'], 'confidentiality': memory['confidentiality'],
                                   'links': len(links)})]
        events += _link_events(conn, memory, links, run_id)
    _publish(events)
    return memory


def update_memory(memory_id: int, **fields) -> dict | None:
    """Partial update of editable fields (others are ignored). Emits ``memory.updated``.

    Changing the title or detail drops the stale embedding and recomputes automatic links.
    """
    run_id = fields.pop('run_id', None)
    changes = _validated({k: v for k, v in fields.items() if k in EDITABLE_FIELDS})
    links: list[tuple[dict, dict]] = []
    with db.connect() as conn:
        current = _get(conn, memory_id)
        if current is None:
            return None
        diff = {k: v for k, v in changes.items() if current[k] != v}
        if not diff:
            return current
        assignments = ', '.join(f'{k}=?' for k in diff)
        conn.execute(f'UPDATE memories SET {assignments}, updated_at={_NOW} WHERE id=?',
                     [*diff.values(), memory_id])
        if 'title' in diff or 'detail' in diff:
            conn.execute('DELETE FROM memory_embeddings WHERE memory_id=?', (memory_id,))
            conn.execute("DELETE FROM memory_links WHERE created_by='auto' AND (source_id=? OR target_id=?)",
                         (memory_id, memory_id))
            links = _auto_link(conn, memory_id)
        memory = _get(conn, memory_id)
        events = [_record(conn, 'memory.updated', _public_title(memory), subject_id=memory_id, run_id=run_id,
                          payload={'memory_id': memory_id, 'title': _public_title(memory),
                                   'category': memory['category'], 'fields': sorted(diff)})]
        events += _link_events(conn, memory, links, run_id)
    _publish(events)
    return memory


def delete_memory(memory_id: int) -> bool:
    """Delete a memory (links and vector cascade). The event keeps no title."""
    with db.connect() as conn:
        if conn.execute('DELETE FROM memories WHERE id=?', (memory_id,)).rowcount == 0:
            return False
        event = _record(conn, 'memory.deleted', 'Mémoire supprimée', subject_id=memory_id,
                        payload={'memory_id': memory_id, 'title': 'Mémoire supprimée'})
    _publish([event])
    return True


def get_memory(memory_id: int) -> dict | None:
    return db.row('SELECT * FROM memories WHERE id=?', (memory_id,))


def list_memories(*, category: str | None = None, kind: str | None = None, limit: int = 150) -> list[dict]:
    clauses, params = [], []
    if category:
        clauses.append('category=?')
        params.append(category)
    if kind:
        clauses.append('kind=?')
        params.append(kind)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
    params.append(max(1, min(int(limit or 150), 1000)))
    return db.rows(f'SELECT * FROM memories{where} ORDER BY id DESC LIMIT ?', params)


def memory_links(memory_id: int) -> list[dict]:
    """Links touching a memory, with the other memory's title (for detail panels)."""
    return db.rows(
        'SELECT l.*, CASE WHEN l.source_id=? THEN l.target_id ELSE l.source_id END AS other_id, '
        'o.title AS other_title FROM memory_links l JOIN memories o ON o.id = '
        'CASE WHEN l.source_id=? THEN l.target_id ELSE l.source_id END '
        'WHERE l.source_id=? OR l.target_id=? ORDER BY l.weight DESC',
        (memory_id, memory_id, memory_id, memory_id))


def mark_recalled(memory_ids: list[int], run_id: int | None, method: str) -> None:
    """Count a recall of these memories (context injection, search). Emits ``memory.recalled``."""
    wanted: list[int] = []
    for value in memory_ids or []:
        if value is not None and int(value) not in wanted:
            wanted.append(int(value))
    if not wanted:
        return
    with db.connect() as conn:
        present = {r['id'] for r in conn.execute(f'SELECT id FROM memories WHERE id IN ({_marks(wanted)})',
                                                 wanted)}
        ids = [i for i in wanted if i in present]
        if not ids:
            return
        conn.execute(f'UPDATE memories SET recall_count=recall_count+1, last_recalled_at={_NOW} '
                     f'WHERE id IN ({_marks(ids)})', ids)
        event = _record(conn, 'memory.recalled', f'{len(ids)} souvenir(s) rappelé(s)', subject_id=ids[0],
                        run_id=run_id, payload={'memory_ids': ids, 'method': method})
    _publish([event])


# --------------------------------------------------------------------------- graph


def graph(limit: int = 120) -> dict:
    """Projection of the 4 memory levels for the brain view (recomputed from the database).
    `totals` and `groups` are the true counts, whatever the number of nodes sent (rings view, 29/10/2026)."""
    limit = max(1, min(int(limit or 120), 1000))
    max_conversations, max_runs = max(10, limit // 12), max(30, limit // 4)
    nodes: list[dict] = [{'id': 'core', 'type': 'core', 'label': 'AURORA', 'weight': 1.0}]
    links: list[dict] = []
    for level, label in LEVELS:
        nodes.append({'id': f'level:{level}', 'type': 'level', 'label': label, 'level': level, 'weight': 0.9})
        links.append({'source': f'level:{level}', 'target': 'core', 'relation': 'level', 'weight': 1.0})

    with db.connect() as conn:
        memories = [dict(r) for r in conn.execute(
            'SELECT m.id, m.title, m.category, m.kind, m.pinned, m.verification, m.recall_count, m.created_at, '
            '(e.memory_id IS NOT NULL) AS has_vector FROM memories m '
            'LEFT JOIN memory_embeddings e ON e.memory_id = m.id '
            'ORDER BY m.pinned DESC, m.id DESC LIMIT ?', (limit,))]
        memory_ids = [m['id'] for m in memories]
        memory_links_rows = [dict(r) for r in conn.execute(
            f'SELECT source_id, target_id, relation, weight FROM memory_links '
            f'WHERE source_id IN ({_marks(memory_ids)}) AND target_id IN ({_marks(memory_ids)})',
            memory_ids + memory_ids)] if memory_ids else []
        conversations = [dict(r) for r in conn.execute(
            'SELECT c.id, c.title, c.created_at, c.updated_at, '
            '(SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS message_count '
            'FROM conversations c ORDER BY c.updated_at DESC, c.id DESC LIMIT ?', (max_conversations,))]
        runs = [dict(r) for r in conn.execute(
            f'SELECT id, title, status, kind, intent, conversation_id, created_at FROM tasks '
            f'WHERE kind IN ({_marks(RUN_KINDS)}) ORDER BY id DESC LIMIT ?', (*RUN_KINDS, max_runs))]
        counted = [dict(r) for r in conn.execute(
            "SELECT CASE WHEN e.memory_id IS NULL THEN 'persistent' ELSE 'semantic' END AS level, "
            'm.category AS category, COUNT(*) AS count FROM memories m '
            'LEFT JOIN memory_embeddings e ON e.memory_id = m.id GROUP BY level, m.category')]
        counted.append({'level': 'immediate', 'category': 'conversations',
                        'count': conn.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]})
        counted += [dict(r) for r in conn.execute(
            f"SELECT 'actions' AS level, COALESCE(NULLIF(intent, ''), 'autre') AS category, COUNT(*) AS count "
            f'FROM tasks WHERE kind IN ({_marks(RUN_KINDS)}) GROUP BY category', RUN_KINDS)]
        run_ids = [r['id'] for r in runs]
        run_events = [dict(r) for r in conn.execute(
            f"SELECT run_id, kind, subject_id, payload_json FROM events WHERE run_id IN ({_marks(run_ids)}) "
            "AND kind IN ('memory.recalled', 'memory.saved')", run_ids)] if run_ids else []
        sourced = [dict(r) for r in conn.execute(
            f'SELECT id, source_ref FROM memories WHERE source_ref IN ({_marks(run_ids)})',
            [str(i) for i in run_ids])] if run_ids else []

    degree: dict[int, int] = {}
    for link in memory_links_rows:
        degree[link['source_id']] = degree.get(link['source_id'], 0) + 1
        degree[link['target_id']] = degree.get(link['target_id'], 0) + 1

    for m in memories:
        level = 'semantic' if m['has_vector'] else 'persistent'
        weight = 0.35 + 0.08 * min(m['recall_count'], 5) + (0.15 if m['pinned'] else 0.0) \
            + 0.04 * min(degree.get(m['id'], 0), 5)
        nodes.append({'id': f"m:{m['id']}", 'type': 'memory', 'label': m['title'],
                      'category': m['category'], 'kind': m['kind'], 'level': level,
                      'weight': round(min(1.0, weight), 3), 'created_at': m['created_at'],
                      'pinned': bool(m['pinned']), 'verification': m['verification']})
        links.append({'source': f"m:{m['id']}", 'target': f'level:{level}', 'relation': 'level', 'weight': 0.3})
    for link in memory_links_rows:
        links.append({'source': f"m:{link['source_id']}", 'target': f"m:{link['target_id']}",
                      'relation': link['relation'], 'weight': link['weight']})

    conversation_ids = set()
    for c in conversations:
        conversation_ids.add(c['id'])
        label = c['title'] or f"Conversation du {c['created_at'][:10]}"
        nodes.append({'id': f"c:{c['id']}", 'type': 'conversation', 'label': label, 'level': 'immediate',
                      'weight': round(min(1.0, 0.3 + 0.05 * c['message_count']), 3),
                      'created_at': c['created_at']})
        links.append({'source': f"c:{c['id']}", 'target': 'level:immediate', 'relation': 'level', 'weight': 0.3})

    for r in runs:
        nodes.append({'id': f"r:{r['id']}", 'type': 'run', 'label': r['title'], 'kind': r['kind'],
                      'category': r['intent'] or 'autre', 'level': 'actions', 'weight': 0.5,
                      'created_at': r['created_at'], 'status': r['status']})
        links.append({'source': f"r:{r['id']}", 'target': 'level:actions', 'relation': 'level', 'weight': 0.3})
        if r['conversation_id'] in conversation_ids:
            links.append({'source': f"r:{r['id']}", 'target': f"c:{r['conversation_id']}",
                          'relation': 'in_conversation', 'weight': 0.4})

    present = set(memory_ids)
    run_memory: dict[tuple[int, int], str] = {}
    for event in run_events:
        payload = db._loads(event['payload_json'], {})
        if event['kind'] == 'memory.recalled':
            for mid in payload.get('memory_ids') or []:
                if isinstance(mid, int) and mid in present:
                    run_memory.setdefault((event['run_id'], mid), 'recalled')
        else:
            mid = payload.get('memory_id', event['subject_id'])
            if isinstance(mid, int) and mid in present:
                run_memory[(event['run_id'], mid)] = 'created'
    for item in sourced:
        if item['id'] in present:
            run_memory[(int(item['source_ref']), item['id'])] = 'created'
    for (run_id, mid), relation in sorted(run_memory.items()):
        links.append({'source': f'r:{run_id}', 'target': f'm:{mid}', 'relation': relation,
                      'weight': 0.8 if relation == 'created' else 0.6})

    shown: dict[tuple[str, str], int] = {}
    for node in nodes:
        if node['type'] in ('memory', 'conversation', 'run'):
            key = (node['level'], 'conversations' if node['type'] == 'conversation'
                   else (node.get('category') or 'autre'))
            shown[key] = shown.get(key, 0) + 1
    groups = [{'level': g['level'], 'category': g['category'] or 'autre', 'count': int(g['count']),
               'shown': shown.get((g['level'], g['category'] or 'autre'), 0)} for g in counted if g['count']]
    totals = {level: sum(g['count'] for g in groups if g['level'] == level) for level, _label in LEVELS}
    return {'nodes': nodes, 'links': links, 'totals': totals, 'groups': groups, 'limit': limit}


# --------------------------------------------------------------------------- conversations

def _title_from(content: str, size: int = 60) -> str:
    line = re.sub(r'\s+', ' ', content or '').strip()
    return line if len(line) <= size else line[:size].rstrip() + '…'


def create_conversation(title: str = '', *, size: int = 120, created_at: str | None = None) -> dict:
    """A title stays on screen in the list: a secret in it is masked first, then the title is cut (a cut secret
    could slip past the masking). H4 of reviews/RAPPORT-AURORA.md; fails closed if the masking fails.
    created_at (ISO UTC « …Z »): the original date of an imported conversation (Athéna), else now."""
    try:
        from ..security.redaction import redact  # lazy: owned by another module
        title = redact(title or '')[0]
    except Exception:
        title = 'Nouvelle conversation' if title else ''
    if created_at is not None and not _ISO_UTC.fullmatch(str(created_at)):
        raise ValueError('Date invalide pour created_at : attendu AAAA-MM-JJTHH:MM:SS(.mmm)Z.')
    with db.connect() as conn:
        cur = conn.execute(f'INSERT INTO conversations(title, created_at, updated_at) VALUES (?, COALESCE(?, {_NOW}), '
                           f'COALESCE(?, {_NOW}))', (_title_from(title, size), created_at, created_at))
        return dict(conn.execute('SELECT * FROM conversations WHERE id=?', (cur.lastrowid,)).fetchone())


def get_conversation(conversation_id: int) -> dict | None:
    return db.row('SELECT * FROM conversations WHERE id=?', (conversation_id,))


RUN_GOING_ON = ('pending', 'running', 'waiting_permission', 'paused')


def delete_conversation(conversation_id: int) -> dict | None:
    """Delete a conversation and its messages; None if it does not exist. The runs keep their journal (Tâches).
    RuntimeError (French) while one of its requests is still going on: its answer would have nowhere to go.
    Like delete_memory, the event keeps no title (it may hold private words)."""
    with db.connect() as conn:
        if conn.execute('SELECT 1 FROM conversations WHERE id=?', (conversation_id,)).fetchone() is None:
            return None
        busy = conn.execute(f'SELECT COUNT(*) FROM tasks WHERE conversation_id=? AND status IN '
                            f'({",".join("?" * len(RUN_GOING_ON))})', (conversation_id, *RUN_GOING_ON)).fetchone()[0]
        if busy:
            raise RuntimeError('Une demande de cette conversation est encore en cours : '
                               'attendez sa fin (ou annulez-la dans Tâches) avant de la supprimer.')
        count = conn.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
        conn.execute('DELETE FROM messages WHERE conversation_id=?', (conversation_id,))
        conn.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))
        event = _record(conn, 'conversation.deleted', 'Conversation supprimée', subject_type='conversation',
                        subject_id=conversation_id, payload={'conversation_id': conversation_id, 'messages': count})
    _publish([event])
    return {'id': conversation_id, 'messages': int(count)}


def add_message(conversation_id: int, role: str, content: str, *, run_id: int | None = None,
                meta: dict | None = None, created_at: str | None = None) -> dict:
    """Append a message; the first user message titles an untitled conversation.
    created_at (ISO UTC « …Z »): the original date of an imported message, which also dates the conversation."""
    if role not in MESSAGE_ROLES:
        raise ValueError(f'Rôle de message invalide : {role}')
    if created_at is not None and not _ISO_UTC.fullmatch(str(created_at)):
        raise ValueError('Date invalide pour created_at : attendu AAAA-MM-JJTHH:MM:SS(.mmm)Z.')
    content = str(content or '')
    with db.connect() as conn:
        conversation = conn.execute('SELECT id, title FROM conversations WHERE id=?',
                                    (conversation_id,)).fetchone()
        if conversation is None:
            raise LookupError('Conversation introuvable.')
        cur = conn.execute(
            'INSERT INTO messages(conversation_id, role, content, run_id, meta_json, created_at) '
            f'VALUES (?,?,?,?,?, COALESCE(?, {_NOW}))',
            (conversation_id, role, content, run_id, json.dumps(meta or {}, ensure_ascii=False, default=str),
             created_at))
        new_title = conversation['title']
        if not new_title and role == 'user' and content.strip():
            new_title = _title_from(content)
        conn.execute(f'UPDATE conversations SET title=?, updated_at=COALESCE(?, {_NOW}) WHERE id=?',
                     (new_title, created_at, conversation_id))
        return dict(conn.execute('SELECT * FROM messages WHERE id=?', (cur.lastrowid,)).fetchone())


def recent_messages(conversation_id: int, limit: int = 12) -> list[dict]:
    """Last ``limit`` messages of a conversation, oldest first."""
    found = db.rows('SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?',
                    (conversation_id, max(1, min(int(limit or 12), 500))))
    return list(reversed(found))


def list_conversations(limit: int = 50) -> list[dict]:
    return db.rows(
        'SELECT c.*, (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS message_count '
        'FROM conversations c ORDER BY c.updated_at DESC, c.id DESC LIMIT ?',
        (max(1, min(int(limit or 50), 500)),))
