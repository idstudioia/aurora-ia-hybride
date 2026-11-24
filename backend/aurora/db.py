"""Durable SQLite storage for AURORA: schema, ASTRA import, connections, events, settings.

Extends the ASTRA v0.1 starter ``db.py``. The starter helpers keep their signatures
(``connect``, ``initialize``, ``rows``, ``add_task``, ``set_task_status``, ``complete_task``,
``add_memory``, ``delete_memory``, ``clear_events``); their events now use the namespaced
kinds of docs/CONTRACT.md §3 (``task.pending``, ``memory.saved``...).
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3   # v3: run_reports (« Signaler », H3)
DEFAULT_HOME = Path.home() / '.aurora'
DEFAULT_DB = DEFAULT_HOME / 'aurora.db'
ASTRA_DEFAULT_DB = Path.home() / '.astra' / 'astra.db'

TASK_STATUSES = ('pending', 'running', 'waiting_permission', 'paused', 'done', 'error',
                 'cancelled', 'interrupted')
TASK_FINAL_STATUSES = ('done', 'error', 'cancelled', 'interrupted')
MEMORY_KINDS = ('fact', 'project', 'preference', 'decision', 'document', 'note', 'summary')
CONFIDENTIALITY_LEVELS = ('public', 'private', 'sensitive')
VERIFICATION_STATES = ('verified', 'unverified', 'user_confirmed', 'contradicted')
LINK_RELATIONS = ('related', 'part_of', 'contradicts', 'updates', 'derived_from')
SUBJECT_TYPES = ('memory', 'task', 'step', 'conversation', 'system', 'permission', 'provider')

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ','now'))"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'note')),
    content TEXT NOT NULL,
    run_id INTEGER NULL,
    meta_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    kind TEXT NOT NULL DEFAULT 'fact'
        CHECK (kind IN ('fact', 'project', 'preference', 'decision', 'document', 'note', 'summary')),
    source TEXT NOT NULL DEFAULT 'user',
    source_ref TEXT NOT NULL DEFAULT '',
    confidentiality TEXT NOT NULL DEFAULT 'private'
        CHECK (confidentiality IN ('public', 'private', 'sensitive')),
    verification TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification IN ('verified', 'unverified', 'user_confirmed', 'contradicted')),
    pinned INTEGER NOT NULL DEFAULT 0,
    recall_count INTEGER NOT NULL DEFAULT 0,
    last_recalled_at TEXT NULL,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, detail, category,
    content='memories', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, detail, category)
    VALUES (new.id, new.title, new.detail, new.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, detail, category)
    VALUES ('delete', old.id, old.title, old.detail, old.category);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF title, detail, category ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, detail, category)
    VALUES ('delete', old.id, old.title, old.detail, old.category);
    INSERT INTO memories_fts(rowid, title, detail, category)
    VALUES (new.id, new.title, new.detail, new.category);
END;
CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation TEXT NOT NULL
        CHECK (relation IN ('related', 'part_of', 'contradicts', 'updates', 'derived_from')),
    weight REAL NOT NULL DEFAULT 0.5,
    created_by TEXT NOT NULL DEFAULT 'auto' CHECK (created_by IN ('user', 'auto')),
    created_at TEXT NOT NULL DEFAULT {_NOW},
    UNIQUE (source_id, target_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'waiting_permission', 'paused', 'done', 'error',
                          'cancelled', 'interrupted')),
    result_text TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'manual' CHECK (kind IN ('manual', 'request', 'plan', 'diagnostic')),
    conversation_id INTEGER NULL,
    input_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    intent TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL DEFAULT '{{}}',
    engine TEXT NOT NULL DEFAULT '',
    parent_id INTEGER NULL,
    started_at TEXT NULL,
    finished_at TEXT NULL,
    duration_ms REAL NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE TABLE IF NOT EXISTS task_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    title TEXT NOT NULL,
    stage TEXT NOT NULL,
    tool TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{{}}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'waiting_permission', 'done', 'error', 'skipped',
                          'cancelled')),
    requires_permission INTEGER NOT NULL DEFAULT 0,
    permission_id INTEGER NULL,
    result_json TEXT NOT NULL DEFAULT '{{}}',
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NULL,
    finished_at TEXT NULL,
    duration_ms REAL NULL
);
CREATE INDEX IF NOT EXISTS idx_task_steps_task ON task_steps(task_id, idx);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL
        CHECK (subject_type IN ('memory', 'task', 'step', 'conversation', 'system', 'permission',
                                'provider')),
    subject_id INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    run_id INTEGER NULL,
    stage TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{{}}',
    duration_ms REAL NULL,
    level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warn', 'error')),
    created_at TEXT NOT NULL DEFAULT {_NOW}
);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

CREATE TABLE IF NOT EXISTS permission_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NULL,
    step_id INTEGER NULL,
    action TEXT NOT NULL,
    risk TEXT NOT NULL CHECK (risk IN ('low', 'medium', 'high')),
    title TEXT NOT NULL,
    preview_json TEXT NOT NULL DEFAULT '{{}}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'granted', 'denied', 'expired')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT {_NOW},
    decided_at TEXT NULL,
    expires_at TEXT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cloud_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT {_NOW}
);

-- v3 (H3): the user's « Signaler » on an answer, one per run (aurora.tasks.reports).
CREATE TABLE IF NOT EXISTS run_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    conversation_id INTEGER NULL,
    text TEXT NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    masked INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    attachments INTEGER NOT NULL DEFAULT 0,
    got_intent TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    expected_intent TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL CHECK (reason IN ('misunderstood', 'wrong_answer', 'off_topic', 'other')),
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT {_NOW},
    updated_at TEXT NOT NULL DEFAULT {_NOW}
);
"""

ASTRA_IMPORT_SETTING = 'astra.import'

_init_lock = threading.RLock()
_ready: set[str] = set()


# --------------------------------------------------------------------------- paths

def config_value(name: str, fallback):
    """``aurora.config.<name>()`` (B5, reads the environment at call time) or ``fallback()``.

    The fallback keeps this module usable on its own (scripts, early tests).
    """
    try:
        func = getattr(importlib.import_module('aurora.config'), name, None)
    except ImportError:
        func = None
    return func() if callable(func) else fallback()


def _env_path(name: str, default: Path) -> Path:
    value = (os.getenv(name) or '').strip()
    return Path(value).expanduser() if value else default


def aurora_home() -> Path:
    return Path(config_value('aurora_home', lambda: _env_path('AURORA_HOME', DEFAULT_HOME)))


def db_path() -> Path:
    return Path(config_value('db_path', lambda: _env_path(
        'AURORA_DB_PATH', _env_path('AURORA_HOME', DEFAULT_HOME) / 'aurora.db')))


def astra_db_path() -> Path:
    return Path(config_value('astra_db_path', lambda: _env_path('ASTRA_DB_PATH', ASTRA_DEFAULT_DB)))


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


# --------------------------------------------------------------------------- connections

def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def _ensure_ready(path: Path) -> None:
    if _key(path) in _ready and path.exists():
        return
    _initialize_path(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Connection with Row factory; commits on success, rolls back on error.

    The schema is created on first use of a database path, so callers never hit a
    missing table even if ``initialize()`` was not called explicitly.
    """
    path = db_path()
    _ensure_ready(path)
    conn = _open(path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize() -> None:
    """Create every table, index and trigger (idempotent) and run the one-time ASTRA import."""
    _initialize_path(db_path())


def _initialize_path(path: Path) -> None:
    to_publish: list[dict] = []
    with _init_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _open(path)
        try:
            _check_legacy_layout(conn, path)
            backup = _backup_before_migration(conn, path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
            conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            if backup is not None:
                to_publish.append(insert_event(
                    conn, subject_type='system', kind='system.backup', level='warn',
                    label=f'Base sauvegardée avant migration : {backup.name}',
                    payload={'path': str(backup), 'schema_version': SCHEMA_VERSION}))
            event = _maybe_import_astra(conn, path)
            if event is not None:
                to_publish.append(event)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
        _ready.add(_key(path))
    for event in to_publish:
        publish_event(event)


def _backup_before_migration(conn: sqlite3.Connection, path: Path) -> Path | None:
    """CLAUDE.md: a copy of an existing database is made before its schema is migrated.

    Only when the database already holds tables and its stored version differs from SCHEMA_VERSION.
    SQLite's online backup gives a consistent copy even in WAL mode, in `backups/` next to the base.
    A base written by a newer AURORA is refused instead of being downgraded."""
    version = int(conn.execute('PRAGMA user_version').fetchone()[0])
    has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0
    if not has_tables or version == SCHEMA_VERSION:
        return None
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"La base {path} vient d'une version plus récente d'AURORA (schéma v{version}, "
                           f"celle-ci connaît v{SCHEMA_VERSION}) : ouverture refusée pour ne pas l'abîmer.")
    from datetime import datetime
    folder = path.parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.stem}-v{version}-avant-v{SCHEMA_VERSION}-{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(str(target))) as copy:
        conn.backup(copy)
    log.warning('Base sauvegardée avant migration (v%s -> v%s) : %s', version, SCHEMA_VERSION, target)
    return target


AUTO_BACKUP_PREFIX = 'aurora-auto-'


def daily_backup(folder: Path | str | None = None, *, now: Any = None, reason: str = '') -> Path | None:
    """The day's automatic copy of the database (H2, reviews/RAPPORT-AURORA.md): at most one per day, in
    ``folder`` (default: ``backups/`` next to the base), never an overwrite nor a deletion of an older copy
    (CLAUDE.md). Same consistent online backup as _backup_before_migration, the base opened read-only, then an
    integrity check of the copy. Returns the new file, or None when today's copy exists or there is no base.
    ``reason`` (« avant-import-athena »…): a copy made before an operation on the data, always, named after it."""
    from datetime import datetime
    path = db_path()
    if not path.exists():
        return None
    target_dir = Path(folder) if folder else path.parent / 'backups'
    target_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    if reason:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', reason):
            raise ValueError(f'Raison de sauvegarde invalide : {reason!r}')
        target = target_dir / f'aurora-{reason}-{now:%Y%m%d-%H%M%S}.db'
    elif any(target_dir.glob(f'{AUTO_BACKUP_PREFIX}{now:%Y%m%d}-*.db')):
        return None
    else:
        target = target_dir / f'{AUTO_BACKUP_PREFIX}{now:%Y%m%d-%H%M%S}.db'
    if target.exists():
        raise FileExistsError(f'La copie {target} existe déjà : jamais d’écrasement.')
    with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(str(target))) as copy:
        source.backup(copy)
        state = copy.execute('PRAGMA integrity_check').fetchone()[0]
    if state != 'ok':
        target.unlink(missing_ok=True)   # our own unusable copy, never an older one
        raise RuntimeError(f'Copie de la base invalide ({state}) : sauvegarde annulée.')
    log.info('Base sauvegardée (%s) : %s', reason or 'copie du jour', target)
    return target


def _check_legacy_layout(conn: sqlite3.Connection, path: Path) -> None:
    columns = {r[1] for r in conn.execute('PRAGMA table_info(events)')}
    if columns and 'run_id' not in columns:
        raise RuntimeError(
            f"La base {path} utilise l'ancien schéma ASTRA v0.1. AURORA a besoin de sa propre base "
            "(AURORA_DB_PATH) ; l'ancienne base est importée automatiquement en lecture seule "
            "depuis ASTRA_DB_PATH.")


def rows(query: str, params=()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def row(query: str, params=()) -> dict | None:
    with connect() as conn:
        found = conn.execute(query, params).fetchone()
        return dict(found) if found is not None else None


def _fetch_one(conn: sqlite3.Connection, query: str, params=()) -> dict | None:
    found = conn.execute(query, params).fetchone()
    return dict(found) if found is not None else None


# --------------------------------------------------------------------------- events

def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def event_dict(record: sqlite3.Row | dict) -> dict:
    """Event row in the contract shape (§3): ``payload`` decoded, no ``payload_json``."""
    data = dict(record)
    payload = _loads(data.pop('payload_json', None), {})
    return {
        'id': data.get('id'),
        'subject_type': data.get('subject_type'),
        'subject_id': data.get('subject_id', 0),
        'kind': data.get('kind'),
        'label': data.get('label'),
        'run_id': data.get('run_id'),
        'stage': data.get('stage', ''),
        'payload': payload if isinstance(payload, dict) else {'value': payload},
        'duration_ms': data.get('duration_ms'),
        'level': data.get('level', 'info'),
        'created_at': data.get('created_at'),
    }


def insert_event(conn: sqlite3.Connection, *, subject_type: str, subject_id: int = 0, kind: str,
                 label: str, run_id: int | None = None, stage: str = '',
                 payload: dict | None = None, duration_ms: float | None = None,
                 level: str = 'info') -> dict:
    """Insert an event in the caller's transaction and return it (payload decoded)."""
    payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
    cur = conn.execute(
        'INSERT INTO events(subject_type, subject_id, kind, label, run_id, stage, payload_json,'
        ' duration_ms, level) VALUES (?,?,?,?,?,?,?,?,?)',
        (subject_type, int(subject_id or 0), kind, str(label), run_id, stage or '', payload_json,
         duration_ms, level))
    return event_dict(conn.execute('SELECT * FROM events WHERE id=?', (cur.lastrowid,)).fetchone())


def publish_event(event: dict) -> None:
    """Forward an already-persisted event to SSE subscribers (aurora.tasks.events, B5)."""
    try:
        events_module = importlib.import_module('aurora.tasks.events')
    except ImportError:
        return
    publish = getattr(events_module, 'publish', None)
    if publish is None:
        return
    try:
        publish(event)
    except Exception:  # broadcasting must never break a database write
        log.exception('Diffusion SSE impossible pour l’événement %s', event.get('id'))


# --------------------------------------------------------------------------- settings

def get_setting(key: str, default: Any = None) -> Any:
    found = row('SELECT value_json FROM settings WHERE key=?', (key,))
    if found is None:
        return default
    return _loads(found['value_json'], default)


def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        _set_setting(conn, key, value)


def _set_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        'INSERT INTO settings(key, value_json) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
        (key, json.dumps(value, ensure_ascii=False, default=str)))


# --------------------------------------------------------------------------- ASTRA import

_ASTRA_KIND_BY_CATEGORY = {
    'projet': 'project', 'project': 'project', 'projets': 'project',
    'preference': 'preference', 'préférence': 'preference', 'preferences': 'preference',
    'decision': 'decision', 'décision': 'decision',
    'document': 'document', 'documents': 'document', 'note': 'note', 'notes': 'note',
}


def _maybe_import_astra(conn: sqlite3.Connection, aurora_path: Path) -> dict | None:
    """Copy ASTRA v0.1 data into a brand-new AURORA database (once). Returns the event."""
    if conn.execute('SELECT 1 FROM settings WHERE key=?', (ASTRA_IMPORT_SETTING,)).fetchone():
        return None
    has_data = any(conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                   for table in ('memories', 'tasks', 'events', 'conversations'))
    source = astra_db_path()
    if has_data:
        _set_setting(conn, ASTRA_IMPORT_SETTING, {'status': 'skipped', 'reason': 'existing_data'})
        return None
    if not source.is_file():
        _set_setting(conn, ASTRA_IMPORT_SETTING, {'status': 'absent', 'source': str(source)})
        return None
    if _key(source) == _key(aurora_path):
        _set_setting(conn, ASTRA_IMPORT_SETTING, {'status': 'skipped', 'reason': 'same_file'})
        return None
    try:
        data = _read_astra(source)
    except sqlite3.Error as exc:
        log.warning('Import ASTRA impossible depuis %s : %s', source, exc)
        _set_setting(conn, ASTRA_IMPORT_SETTING,
                     {'status': 'error', 'source': str(source), 'error': str(exc)})
        return insert_event(
            conn, subject_type='system', kind='system.imported', stage='system', level='warn',
            label='Import ASTRA impossible : base illisible',
            payload={'ok': False, 'source': str(source), 'error': str(exc)})
    counts = _write_astra(conn, data)
    _set_setting(conn, ASTRA_IMPORT_SETTING, {'status': 'done', 'source': str(source), **counts})
    label = (f"Import ASTRA : {counts['memories']} souvenir(s), {counts['tasks']} tâche(s), "
             f"{counts['events']} événement(s)")
    return insert_event(conn, subject_type='system', kind='system.imported', stage='system',
                        label=label, payload={'ok': True, 'source': str(source), **counts})


def _read_astra(source: Path, wanted: tuple[str, ...] = ('memories', 'tasks', 'events')) -> dict[str, list[dict]]:
    """Read tables of another base through a read-only URI connection (never writes to it): ASTRA, and the
    copy of Athéna's base (memory/athena_import.py)."""
    uri = source.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as src:
        src.row_factory = sqlite3.Row
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        data: dict[str, list[dict]] = {}
        for table in wanted:
            data[table] = ([dict(r) for r in src.execute(f'SELECT * FROM {table} ORDER BY id')]
                           if table in tables else [])
    return data


def _write_astra(conn: sqlite3.Connection, data: dict[str, list[dict]]) -> dict[str, int]:
    counts = {'memories': 0, 'tasks': 0, 'events': 0, 'skipped': 0}
    now = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
    for item in data['memories']:
        title = str(item.get('title') or '').strip()
        if not title or item.get('id') is None:
            counts['skipped'] += 1
            continue
        category = str(item.get('category') or 'general')
        kind = _ASTRA_KIND_BY_CATEGORY.get(category.strip().lower(), 'fact')
        created = item.get('created_at')
        conn.execute(
            'INSERT INTO memories(id, title, detail, category, kind, source, source_ref, created_at,'
            f' updated_at) VALUES (?,?,?,?,?,?,?, COALESCE(?, {now}), COALESCE(?, {now}))',
            (int(item['id']), title, str(item.get('detail') or ''), category, kind, 'astra-import',
             f"astra:memory:{item['id']}", created, created))
        counts['memories'] += 1
    for item in data['tasks']:
        title = str(item.get('title') or '').strip()
        if not title or item.get('id') is None:
            counts['skipped'] += 1
            continue
        status = item.get('status') if item.get('status') in TASK_STATUSES else 'interrupted'
        created = item.get('created_at')
        conn.execute(
            'INSERT INTO tasks(id, title, status, result_text, kind, source, created_at, updated_at)'
            f" VALUES (?,?,?,?, 'manual', 'astra-import', COALESCE(?, {now}), COALESCE(?, {now}))",
            (int(item['id']), title, status, str(item.get('result_text') or ''), created,
             item.get('updated_at') or created))
        counts['tasks'] += 1
    for item in data['events']:
        if item.get('subject_type') not in SUBJECT_TYPES or not item.get('kind'):
            counts['skipped'] += 1
            continue
        conn.execute(
            'INSERT INTO events(id, subject_type, subject_id, kind, label, payload_json, created_at)'
            f' VALUES (?,?,?,?,?,?, COALESCE(?, {now}))',
            (item.get('id'), item['subject_type'], int(item.get('subject_id') or 0),
             str(item['kind']), str(item.get('label') or ''),
             json.dumps({'imported_from': 'astra'}), item.get('created_at')))
        counts['events'] += 1
    return counts


# --------------------------------------------------------------------------- starter compat

def add_memory(title: str, detail: str = '', category: str = 'general') -> dict:
    """Starter helper, now backed by memory.store (FTS, auto-links, ``memory.saved``)."""
    from .memory import store
    return store.add_memory(title, detail, category)


def delete_memory(item_id: int) -> bool:
    from .memory import store
    return store.delete_memory(item_id)


def add_task(title: str) -> dict:
    """Manual task (starter ``/api/tasks``); emits ``task.pending``."""
    with connect() as conn:
        cur = conn.execute("INSERT INTO tasks(title, kind, source) VALUES (?, 'manual', 'manual')",
                           (title,))
        task_id = cur.lastrowid
        event = insert_event(conn, subject_type='task', subject_id=task_id, kind='task.pending',
                             label=title, run_id=task_id)
        task = _fetch_one(conn, 'SELECT * FROM tasks WHERE id=?', (task_id,))
    publish_event(event)
    return task


def set_task_status(item_id: int, status: str) -> dict | None:
    """Change a task status; emits ``task.<status>`` only when the status really changes."""
    if status not in TASK_STATUSES:
        raise ValueError(f'Statut de tâche invalide : {status}')
    event = None
    with connect() as conn:
        task = _fetch_one(conn, 'SELECT * FROM tasks WHERE id=?', (item_id,))
        if task is None:
            return None
        if task['status'] != status:
            conn.execute(
                f"UPDATE tasks SET status=?, updated_at={_NOW},"
                f" started_at=CASE WHEN ?='running' AND started_at IS NULL THEN {_NOW} ELSE started_at END,"
                f" finished_at=CASE WHEN ? IN {TASK_FINAL_STATUSES} THEN {_NOW} ELSE finished_at END,"
                f" duration_ms=CASE WHEN ? IN {TASK_FINAL_STATUSES} AND started_at IS NOT NULL"
                "  THEN (julianday('now') - julianday(started_at)) * 86400000.0 ELSE duration_ms END"
                ' WHERE id=?',
                (status, status, status, status, item_id))
            event = insert_event(conn, subject_type='task', subject_id=item_id,
                                 kind=f'task.{status}', label=task['title'], run_id=item_id,
                                 payload={'status': status},
                                 level='error' if status == 'error' else 'info')
        task = _fetch_one(conn, 'SELECT * FROM tasks WHERE id=?', (item_id,))
    if event is not None:
        publish_event(event)
    return task


def complete_task(item_id: int, result_text: str) -> dict | None:
    """Mark a task done with its verifiable result; emits ``task.done``."""
    with connect() as conn:
        task = _fetch_one(conn, 'SELECT * FROM tasks WHERE id=?', (item_id,))
        if task is None:
            return None
        conn.execute(
            f"UPDATE tasks SET status='done', result_text=?, updated_at={_NOW}, finished_at={_NOW},"
            " duration_ms=CASE WHEN started_at IS NOT NULL"
            "  THEN (julianday('now') - julianday(started_at)) * 86400000.0 ELSE duration_ms END"
            ' WHERE id=?',
            (result_text, item_id))
        event = insert_event(conn, subject_type='task', subject_id=item_id, kind='task.done',
                             label=task['title'], run_id=item_id, payload={'status': 'done'})
        task = _fetch_one(conn, 'SELECT * FROM tasks WHERE id=?', (item_id,))
    publish_event(event)
    return task


def clear_events() -> int:
    """Purge the event history (user request). Returns the number of deleted events."""
    with connect() as conn:
        return conn.execute('DELETE FROM events').rowcount
