"""Event log + live bus (docs/CONTRACT.md §3).

emit() persists an event through db.insert_event, then publish() fans it out to
SSE subscribers. Only db.py is imported here (no cycle). publish() is safe from
any thread: delivery always happens on the attached event loop.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from .. import config

STAGES = ('input', 'decision', 'tool', 'local_ai', 'cloud_ai', 'permission', 'verify',
          'response', 'memory', 'system', '')
EVENT_KEYS = ('id', 'subject_type', 'subject_id', 'kind', 'label', 'run_id', 'stage',
              'payload', 'duration_ms', 'level', 'created_at')


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Contract JSON shape (identical in REST and SSE): payload_json -> payload dict."""
    event = dict(raw)
    if 'payload' not in event or event.get('payload') is None:
        text = event.pop('payload_json', None)
        try:
            event['payload'] = json.loads(text) if text else {}
        except (TypeError, ValueError):
            event['payload'] = {}
    else:
        event.pop('payload_json', None)
        if isinstance(event['payload'], str):
            try:
                event['payload'] = json.loads(event['payload'])
            except ValueError:
                event['payload'] = {}
    for key in EVENT_KEYS:
        event.setdefault(key, None if key in ('run_id', 'duration_ms', 'created_at') else '')
    if event.get('payload') in ('', None):
        event['payload'] = {}
    if event.get('subject_id') in ('', None):
        event['subject_id'] = 0
    if event.get('level') in ('', None):
        event['level'] = 'info'
    return event


class EventBus:
    """Bounded per-subscriber queues; a slow subscriber loses its oldest events."""

    def __init__(self, maxsize: int | None = None) -> None:
        self._maxsize = maxsize or config.SSE_QUEUE_MAX
        self._subs: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.dropped = 0

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def detach_loop(self) -> None:
        self._loop = None

    def subscribe(self, maxsize: int | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize or self._maxsize)
        with self._lock:
            self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            # No server loop (scripts, unit tests): deliver in the caller's thread.
            self._deliver(event)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(event)
            return
        try:
            loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            pass  # loop shutting down: the event stays persisted in SQLite

    def _deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._subs)
        for queue in queues:
            while True:
                try:
                    queue.put_nowait(event)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        self.dropped += 1
                    except asyncio.QueueEmpty:
                        break


bus = EventBus()


def publish(event: dict[str, Any]) -> None:
    """Broadcast an already persisted event (no DB write). Thread-safe."""
    bus.publish(normalize_event(event))


def broadcast_only(kind: str, label: str, *, run_id: int, stage: str = '', payload: dict | None = None) -> None:
    """B4: an event sent live to the interface and never written (no id, no journal line), through publish():
    the text of an answer while the model writes it would otherwise make one database row per word. Its SSE
    frame has no id (main._sse_frame), so a reconnection never asks for it again."""
    publish({'id': 0, 'transient': True, 'kind': kind, 'label': label[:300], 'run_id': run_id, 'subject_type': 'task',
             'subject_id': run_id, 'stage': stage, 'payload': payload or {}, 'level': 'info'})


def tool_uses() -> dict[str, dict]:
    """{tool: {uses, last_used_at}} counted from the stored tool.result events (rings of the brain view)."""
    from .. import db
    found = db.rows("SELECT json_extract(payload_json, '$.tool') AS tool, COUNT(*) AS uses, "
                    "MAX(created_at) AS last_used_at FROM events WHERE kind = 'tool.result' GROUP BY tool")
    return {str(r['tool']): {'uses': int(r['uses']), 'last_used_at': r['last_used_at']} for r in found if r['tool']}


def record(conn, kind: str, label: str, *, subject_type: str = 'task', subject_id: int = 0,
           run_id: int | None = None, stage: str = '', payload: dict | None = None,
           duration_ms: float | None = None, level: str = 'info') -> dict[str, Any]:
    """Insert an event inside an open transaction. Call publish() after commit."""
    from .. import db
    event = db.insert_event(conn, subject_type=subject_type, subject_id=int(subject_id or 0),
                            kind=kind, label=label[:300], run_id=run_id, stage=stage,
                            payload=payload or {}, duration_ms=duration_ms, level=level)
    return normalize_event(event)


def emit(kind: str, label: str, *, subject_type: str = 'task', subject_id: int = 0,
         run_id: int | None = None, stage: str = '', payload: dict | None = None,
         duration_ms: float | None = None, level: str = 'info') -> dict[str, Any]:
    """Persist (db.insert_event) then publish. Returns the event in contract shape."""
    from .. import db
    with db.connect() as conn:
        event = record(conn, kind, label, subject_type=subject_type, subject_id=subject_id,
                       run_id=run_id, stage=stage, payload=payload, duration_ms=duration_ms,
                       level=level)
    bus.publish(event)
    return event


def list_events(*, limit: int = 200, run_id: int | None = None, since_id: int | None = None,
                ascending: bool = False) -> list[dict[str, Any]]:
    from .. import db
    clauses, params = [], []
    if run_id is not None:
        clauses.append('run_id = ?')
        params.append(int(run_id))
    if since_id is not None:
        clauses.append('id > ?')
        params.append(int(since_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    order = 'ASC' if ascending else 'DESC'
    limit = max(1, min(int(limit), 2000))
    found = db.rows(f'SELECT * FROM events {where} ORDER BY id {order} LIMIT ?', (*params, limit))
    return [normalize_event(item) for item in found]
