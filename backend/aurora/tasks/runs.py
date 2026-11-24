"""Persistence of runs (tasks rows of kind 'request') and their steps (task_steps).

Status transitions are timestamped here (started_at, finished_at, duration_ms).
Terminal transitions emit their run.* event in the same transaction.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .. import db
from . import events

RUN_STATUSES = ('pending', 'running', 'waiting_permission', 'paused', 'done', 'error',
                'cancelled', 'interrupted')
TERMINAL_RUN = ('done', 'error', 'cancelled', 'interrupted')
STEP_STATUSES = ('pending', 'running', 'waiting_permission', 'done', 'error', 'skipped', 'cancelled')
TERMINAL_STEP = ('done', 'error', 'skipped', 'cancelled')
RUN_LABELS = {
    'done': 'Demande terminée',
    'error': 'Demande en erreur',
    'cancelled': 'Demande annulée',
    'interrupted': 'Demande interrompue',
    'resumed': 'Demande reprise',
}


def now_iso() -> str:
    """Same format as SQLite strftime('%Y-%m-%dT%H:%M:%fZ','now')."""
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def elapsed_ms(start: str | None, end: str | None = None) -> float | None:
    begin = parse_iso(start)
    finish = parse_iso(end) if end else datetime.now(timezone.utc)
    if begin is None or finish is None:
        return None
    return round((finish - begin).total_seconds() * 1000, 1)


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# --- runs -------------------------------------------------------------------------

def create_run(text: str, *, conversation_id: int | None = None, source: str = 'text',
               title: str | None = None, kind: str = 'request', parent_id: int | None = None) -> dict:
    """Insert the run (status 'running', started_at now) and emit run.created."""
    clean_title = (title or text or 'Demande').strip().replace('\n', ' ')
    if len(clean_title) > 120:
        clean_title = clean_title[:117] + '…'
    started = now_iso()
    with db.connect() as conn:
        cur = conn.execute(
            'INSERT INTO tasks(title, status, kind, conversation_id, input_text, source, parent_id, started_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (clean_title, 'running', kind, conversation_id, text, source, parent_id, started))
        run_id = int(cur.lastrowid)
        event = events.record(conn, 'run.created', clean_title, subject_type='task', subject_id=run_id,
                              run_id=run_id, stage='input',
                              payload={'text': text[:2000], 'source': source,
                                       'conversation_id': conversation_id})
        task = dict(conn.execute('SELECT * FROM tasks WHERE id=?', (run_id,)).fetchone())
    events.bus.publish(event)
    return task


def get_run(run_id: int) -> dict | None:
    found = db.rows('SELECT * FROM tasks WHERE id=?', (int(run_id),))
    return found[0] if found else None


def list_runs(limit: int = 50, *, include_manual: bool = False) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    if include_manual:
        return db.rows('SELECT * FROM tasks ORDER BY id DESC LIMIT ?', (limit,))
    return db.rows("SELECT * FROM tasks WHERE kind != 'manual' ORDER BY id DESC LIMIT ?", (limit,))


_RUN_FIELDS = ('title', 'result_text', 'kind', 'conversation_id', 'intent', 'decision_json',
               'engine', 'parent_id')


def update_run(run_id: int, **fields: Any) -> dict | None:
    """Update plain columns (no status change). decision_json may be a dict."""
    sets, params = [], []
    for key, value in fields.items():
        if key not in _RUN_FIELDS:
            raise ValueError(f'Champ de run inconnu : {key}')
        if key == 'decision_json' and not isinstance(value, str):
            value = _dumps(value)
        sets.append(f'{key}=?')
        params.append(value)
    if not sets:
        return get_run(run_id)
    sets.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    with db.connect() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*params, int(run_id)))
    return get_run(run_id)


def decision_of(run: dict) -> dict:
    return _loads(run.get('decision_json'), {})


def set_run_status(run_id: int, status: str, *, error: str | None = None,
                   result_text: str | None = None, emit_event: bool = True,
                   label: str | None = None) -> dict | None:
    """Timestamped transition. Terminal statuses emit run.<status> {status, duration_ms, cost_usd, error?}."""
    if status not in RUN_STATUSES:
        raise ValueError(f'Statut de run inconnu : {status}')
    event = None
    with db.connect() as conn:
        found = conn.execute('SELECT * FROM tasks WHERE id=?', (int(run_id),)).fetchone()
        if found is None:
            return None
        task = dict(found)
        now = now_iso()
        sets = ['status=?', "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"]
        params: list[Any] = [status]
        duration = task.get('duration_ms')
        if status == 'running':
            if not task.get('started_at'):
                sets.append('started_at=?')
                params.append(now)
            if task.get('finished_at'):
                sets += ['finished_at=NULL', 'duration_ms=NULL']
        if status in TERMINAL_RUN:
            duration = elapsed_ms(task.get('started_at') or task.get('created_at'), now)
            sets += ['finished_at=?', 'duration_ms=?']
            params += [now, duration]
        if result_text is not None:
            sets.append('result_text=?')
            params.append(result_text)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*params, int(run_id)))
        if emit_event and status in TERMINAL_RUN:
            payload: dict[str, Any] = {'status': status, 'duration_ms': duration,
                                       'cost_usd': round(float(task.get('cost_usd') or 0), 6)}
            if error:
                payload['error'] = error
            level = 'error' if status == 'error' else ('warn' if status == 'interrupted' else 'info')
            event = events.record(conn, f'run.{status}', label or RUN_LABELS.get(status, status),
                                  subject_type='task', subject_id=int(run_id), run_id=int(run_id),
                                  stage='response', payload=payload, duration_ms=duration, level=level)
        updated = dict(conn.execute('SELECT * FROM tasks WHERE id=?', (int(run_id),)).fetchone())
    if event:
        events.bus.publish(event)
    return updated


def mark_resumed(run_id: int) -> dict | None:
    """Back to running + run.resumed."""
    updated = set_run_status(run_id, 'running', emit_event=False)
    if updated is None:
        return None
    events.emit('run.resumed', RUN_LABELS['resumed'], subject_type='task', subject_id=run_id,
                run_id=run_id, stage='response',
                payload={'status': 'running', 'duration_ms': None,
                         'cost_usd': round(float(updated.get('cost_usd') or 0), 6)})
    return updated


def add_cost(run_id: int, cost_usd: float) -> float:
    """Accumulate cloud cost on the run. Returns the new total."""
    with db.connect() as conn:
        conn.execute("UPDATE tasks SET cost_usd = cost_usd + ?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                     'WHERE id=?', (float(cost_usd or 0), int(run_id)))
        found = conn.execute('SELECT cost_usd FROM tasks WHERE id=?', (int(run_id),)).fetchone()
    return float(found['cost_usd']) if found else 0.0


def mark_interrupted_at_startup() -> list[int]:
    """Runs left 'running'/'pending' by a crash or restart become 'interrupted' (resumable)."""
    stale = db.rows("SELECT id FROM tasks WHERE status IN ('running','pending') AND kind != 'manual'")
    ids = [int(item['id']) for item in stale]
    for run_id in ids:
        with db.connect() as conn:
            conn.execute("UPDATE task_steps SET status='pending', started_at=NULL "
                         "WHERE task_id=? AND status='running'", (run_id,))
        set_run_status(run_id, 'interrupted', error='Service redémarré pendant le traitement.')
    return ids


# --- steps ------------------------------------------------------------------------

def add_steps(run_id: int, steps: list[dict], *, start_idx: int | None = None) -> list[dict]:
    """Insert planned steps. A step: {title, stage, tool, params, requires_permission, ...}.

    Everything except title/stage/tool/requires_permission goes to params_json.
    """
    with db.connect() as conn:
        if start_idx is None:
            found = conn.execute('SELECT COALESCE(MAX(idx), -1) AS m FROM task_steps WHERE task_id=?',
                                 (int(run_id),)).fetchone()
            start_idx = int(found['m']) + 1
        else:
            conn.execute('UPDATE task_steps SET idx = idx + ? WHERE task_id=? AND idx >= ?',
                         (len(steps), int(run_id), start_idx))
        for offset, step in enumerate(steps):
            spec = {k: v for k, v in step.items()
                    if k not in ('title', 'stage', 'tool', 'requires_permission')}
            conn.execute(
                'INSERT INTO task_steps(task_id, idx, title, stage, tool, params_json, requires_permission) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (int(run_id), start_idx + offset, str(step.get('title') or 'Étape')[:160],
                 step.get('stage') or 'tool', step.get('tool') or '', _dumps(spec),
                 1 if step.get('requires_permission') else 0))
    return get_steps(run_id)


def get_steps(run_id: int) -> list[dict]:
    return db.rows('SELECT * FROM task_steps WHERE task_id=? ORDER BY idx, id', (int(run_id),))


def get_step(step_id: int) -> dict | None:
    found = db.rows('SELECT * FROM task_steps WHERE id=?', (int(step_id),))
    return found[0] if found else None


def step_spec(step: dict) -> dict:
    return _loads(step.get('params_json'), {})


def step_result(step: dict) -> dict:
    return _loads(step.get('result_json'), {})


def set_step_status(step_id: int, status: str, *, result: dict | None = None, error: str | None = None,
                    permission_id: int | None = None) -> dict | None:
    if status not in STEP_STATUSES:
        raise ValueError(f'Statut d’étape inconnu : {status}')
    with db.connect() as conn:
        found = conn.execute('SELECT * FROM task_steps WHERE id=?', (int(step_id),)).fetchone()
        if found is None:
            return None
        step = dict(found)
        now = now_iso()
        sets, params = ['status=?'], [status]
        if status == 'running':
            sets += ['started_at=?', 'finished_at=NULL', 'duration_ms=NULL']
            params.append(now)
        if status in TERMINAL_STEP:
            sets += ['finished_at=?', 'duration_ms=?']
            params += [now, elapsed_ms(step.get('started_at'), now) if step.get('started_at') else None]
        if result is not None:
            sets.append('result_json=?')
            params.append(_dumps(result))
        if error is not None:
            sets.append('error=?')
            params.append(error[:2000])
        if permission_id is not None:
            sets.append('permission_id=?')
            params.append(int(permission_id))
        conn.execute(f"UPDATE task_steps SET {', '.join(sets)} WHERE id=?", (*params, int(step_id)))
        return dict(conn.execute('SELECT * FROM task_steps WHERE id=?', (int(step_id),)).fetchone())


def update_step(step_id: int, **fields: Any) -> dict | None:
    """Rewrite plain columns (title, stage, tool, requires_permission, params dict, result dict)."""
    sets, params = [], []
    for key, value in fields.items():
        if key == 'params':
            key, value = 'params_json', _dumps(value)
        elif key == 'result':
            key, value = 'result_json', _dumps(value)
        elif key not in ('title', 'stage', 'tool', 'requires_permission', 'error', 'status'):
            raise ValueError(f'Champ d’étape inconnu : {key}')
        if key == 'requires_permission':
            value = 1 if value else 0
        sets.append(f'{key}=?')
        params.append(value)
    if sets:
        with db.connect() as conn:
            conn.execute(f"UPDATE task_steps SET {', '.join(sets)} WHERE id=?", (*params, int(step_id)))
    return get_step(step_id)


def cancel_open_steps(run_id: int) -> None:
    """Every non-terminal step becomes 'cancelled'."""
    now = now_iso()
    with db.connect() as conn:
        conn.execute("UPDATE task_steps SET status='cancelled', finished_at=? "
                     "WHERE task_id=? AND status IN ('pending','running','waiting_permission')",
                     (now, int(run_id)))


def first_open_step(run_id: int) -> dict | None:
    for step in get_steps(run_id):
        if step['status'] not in TERMINAL_STEP:
            return step
    return None


# --- views ------------------------------------------------------------------------

def _permission_view(item: dict) -> dict:
    view = dict(item)
    if 'preview' not in view:
        view['preview'] = _loads(view.pop('preview_json', None), {})
    else:
        view.pop('preview_json', None)
    return view


def permissions_of(run_id: int) -> list[dict]:
    try:
        found = db.rows('SELECT * FROM permission_requests WHERE task_id=? ORDER BY id', (int(run_id),))
    except Exception:
        return []
    return [_permission_view(item) for item in found]


def get_run_detail(run_id: int) -> dict | None:
    """RunDetail = {task, steps, events (chronological), permissions}."""
    task = get_run(run_id)
    if task is None:
        return None
    return {
        'task': task,
        'steps': get_steps(run_id),
        'events': events.list_events(limit=2000, run_id=run_id, ascending=True),
        'permissions': permissions_of(run_id),
    }
