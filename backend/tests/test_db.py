"""Storage tests (B1): schema of CONTRACT.md §2, events, settings, starter helpers, ASTRA import.

No network. Every test uses a temporary AURORA_DB_PATH and a temporary ASTRA_DB_PATH.
Run: cd backend && python -m unittest tests.test_db
"""
import hashlib
import os
import re
import sqlite3
from contextlib import closing
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora import db
from aurora.memory import store

ISO_UTC = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

EXPECTED_COLUMNS = {
    'conversations': ['id', 'title', 'created_at', 'updated_at'],
    'messages': ['id', 'conversation_id', 'role', 'content', 'run_id', 'meta_json', 'created_at'],
    'memories': ['id', 'title', 'detail', 'category', 'kind', 'source', 'source_ref', 'confidentiality',
                 'verification', 'pinned', 'recall_count', 'last_recalled_at', 'created_at', 'updated_at'],
    'memory_embeddings': ['memory_id', 'model', 'dim', 'vector', 'updated_at'],
    'memory_links': ['id', 'source_id', 'target_id', 'relation', 'weight', 'created_by', 'created_at'],
    'tasks': ['id', 'title', 'status', 'result_text', 'kind', 'conversation_id', 'input_text', 'source',
              'intent', 'decision_json', 'engine', 'parent_id', 'started_at', 'finished_at', 'duration_ms',
              'cost_usd', 'created_at', 'updated_at'],
    'task_steps': ['id', 'task_id', 'idx', 'title', 'stage', 'tool', 'params_json', 'status',
                   'requires_permission', 'permission_id', 'result_json', 'error', 'started_at',
                   'finished_at', 'duration_ms'],
    'events': ['id', 'subject_type', 'subject_id', 'kind', 'label', 'run_id', 'stage', 'payload_json',
               'duration_ms', 'level', 'created_at'],
    'permission_requests': ['id', 'task_id', 'step_id', 'action', 'risk', 'title', 'preview_json', 'status',
                            'reason', 'created_at', 'decided_at', 'expires_at'],
    'settings': ['key', 'value_json'],
    'cloud_usage': ['id', 'task_id', 'provider', 'model', 'input_tokens', 'output_tokens', 'cache_read_tokens',
                    'cache_write_tokens', 'cost_usd', 'created_at'],
}

# Schema of the ASTRA v0.1 starter (backend/app/db.py), used to build fake ASTRA databases.
ASTRA_SCHEMA = '''
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    result_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('memory', 'task')),
    subject_id INTEGER NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
'''


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self.db_file = self.dir / 'aurora.db'
        self.astra_file = self.dir / 'astra' / 'astra.db'
        env = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(self.db_file),
                                           'ASTRA_DB_PATH': str(self.astra_file),
                                           'AURORA_OFFLINE': '1'})
        env.start()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(env.stop)
        # Deterministic event path: direct db.insert_event, SSE publish recorded.
        emitter = mock.patch.object(store, '_events_module', return_value=None)
        emitter.start()
        self.addCleanup(emitter.stop)
        self.published = []
        publish = mock.patch.object(db, 'publish_event', side_effect=self.published.append)
        publish.start()
        self.addCleanup(publish.stop)

    def kinds(self):
        return [r['kind'] for r in db.rows('SELECT kind FROM events ORDER BY id')]


class SchemaTests(TempDbCase):
    def test_tables_and_columns_match_contract(self):
        db.initialize()
        with db.connect() as conn:
            for table, expected in EXPECTED_COLUMNS.items():
                columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
                self.assertEqual(columns, expected, table)

    def test_wal_and_foreign_keys(self):
        db.initialize()
        with db.connect() as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_event_indexes_fts_and_triggers(self):
        db.initialize()
        with db.connect() as conn:
            indexed = set()
            for index in conn.execute('PRAGMA index_list(events)').fetchall():
                cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{index[1]}')")]
                indexed.add(tuple(cols))
            self.assertIn(('run_id',), indexed)
            self.assertIn(('created_at',), indexed)
            fts_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='memories_fts'").fetchone()[0]
            self.assertIn("content='memories'", fts_sql)
            self.assertIn("content_rowid='id'", fts_sql)
            self.assertIn("remove_diacritics 2", fts_sql)
            triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertTrue({'memories_ai', 'memories_ad', 'memories_au'} <= triggers)

    def test_backup_is_made_before_a_schema_migration(self):
        # CLAUDE.md: « Faire une sauvegarde avant toute migration de base. »
        db.initialize()
        backups = self.dir / 'backups'
        self.assertFalse(backups.exists())                      # fresh base: nothing to save
        db.initialize()
        self.assertFalse(backups.exists())                      # same version: no migration
        with db.connect() as conn:
            conn.execute("INSERT INTO memories(title, detail, category) VALUES ('Projet Hélios', 'à garder', 'projet')")
        with closing(sqlite3.connect(str(self.db_file))) as raw:
            raw.execute('PRAGMA user_version=1')                # a base written by an older schema
            raw.commit()
        db.initialize()
        copies = list(backups.glob(f'aurora-v1-avant-v{db.SCHEMA_VERSION}-*.db'))
        self.assertEqual(len(copies), 1)
        with closing(sqlite3.connect(str(copies[0]))) as copy:
            self.assertEqual(copy.execute('SELECT title FROM memories').fetchone()[0], 'Projet Hélios')
            self.assertEqual(copy.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertIn('system.backup', self.kinds())
        with closing(sqlite3.connect(str(self.db_file))) as raw:
            raw.execute('PRAGMA user_version=99')               # written by a newer AURORA
            raw.commit()
        with self.assertRaises(RuntimeError):
            db.initialize()
        with closing(sqlite3.connect(str(self.db_file))) as raw:
            self.assertEqual(raw.execute('PRAGMA user_version').fetchone()[0], 99)   # left untouched

    def test_initialize_is_idempotent(self):
        for _ in range(3):
            db.initialize()
        self.assertEqual(db.rows('SELECT COUNT(*) AS n FROM events')[0]['n'], 0)
        self.assertEqual(db.get_setting(db.ASTRA_IMPORT_SETTING)['status'], 'absent')

    def test_connect_creates_schema_on_first_use(self):
        self.assertFalse(self.db_file.exists())
        self.assertEqual(db.rows('SELECT COUNT(*) AS n FROM memories'), [{'n': 0}])

    def test_paths_from_environment(self):
        self.assertEqual(db.db_path(), self.db_file)
        with mock.patch.dict(os.environ, {'AURORA_HOME': str(self.dir / 'home')}):
            os.environ.pop('AURORA_DB_PATH')
            self.assertEqual(db.db_path(), self.dir / 'home' / 'aurora.db')
            self.assertEqual(db.aurora_home(), self.dir / 'home')
        self.assertEqual(db.astra_db_path(), self.astra_file)

    def test_rollback_on_error(self):
        db.initialize()
        with self.assertRaises(RuntimeError):
            with db.connect() as conn:
                conn.execute("INSERT INTO memories(title) VALUES ('temporaire')")
                raise RuntimeError('échec simulé')
        self.assertIsNone(db.row("SELECT * FROM memories WHERE title='temporaire'"))

    def test_check_constraints(self):
        db.initialize()
        bad = [("INSERT INTO memories(title, kind) VALUES ('x', 'rumeur')", ()),
               ("INSERT INTO memories(title, confidentiality) VALUES ('x', 'secret')", ()),
               ("INSERT INTO events(subject_type, kind, label) VALUES ('ufo', 'k', 'l')", ()),
               ("INSERT INTO events(subject_type, kind, label, level) VALUES ('task', 'k', 'l', 'debug')", ()),
               ("INSERT INTO tasks(title, kind) VALUES ('x', 'magie')", ())]
        for sql, params in bad:
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                with db.connect() as conn:
                    conn.execute(sql, params)

    def test_foreign_key_cascades(self):
        db.initialize()
        with db.connect() as conn:
            conn.execute("INSERT INTO conversations(id, title) VALUES (1, 'c')")
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (1, 'user', 'bonjour')")
            conn.execute("INSERT INTO memories(id, title) VALUES (1, 'a'), (2, 'b')")
            conn.execute("INSERT INTO memory_links(source_id, target_id, relation) VALUES (1, 2, 'related')")
            conn.execute("INSERT INTO memory_embeddings(memory_id, model, dim, vector) VALUES (1, 'm', 1, x'00000000')")
            conn.execute("INSERT INTO tasks(id, title) VALUES (1, 't')")
            conn.execute("INSERT INTO task_steps(task_id, idx, title, stage) VALUES (1, 0, 's', 'tool')")
        with db.connect() as conn:
            conn.execute('DELETE FROM conversations WHERE id=1')
            conn.execute('DELETE FROM memories WHERE id=1')
            conn.execute('DELETE FROM tasks WHERE id=1')
        for table in ('messages', 'memory_links', 'memory_embeddings', 'task_steps'):
            self.assertEqual(db.rows(f'SELECT COUNT(*) AS n FROM {table}')[0]['n'], 0, table)

    def test_fts_follows_inserts_updates_and_deletes(self):
        db.initialize()
        match = "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?"
        with db.connect() as conn:
            conn.execute("INSERT INTO memories(id, title, detail) VALUES (7, 'Télescope', 'Observation de Jupiter')")
        self.assertEqual(db.rows(match, ('telescope',)), [{'rowid': 7}])
        with db.connect() as conn:
            conn.execute("UPDATE memories SET title='Lunette' WHERE id=7")
            conn.execute("UPDATE memories SET recall_count=recall_count+1 WHERE id=7")
        self.assertEqual(db.rows(match, ('telescope',)), [])
        self.assertEqual(db.rows(match, ('lunette',)), [{'rowid': 7}])
        self.assertEqual(db.rows(match, ('jupiter',)), [{'rowid': 7}])
        with db.connect() as conn:
            conn.execute('DELETE FROM memories WHERE id=7')
        self.assertEqual(db.rows(match, ('lunette',)), [])

    def test_legacy_astra_layout_is_rejected_with_clear_message(self):
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_file) as conn:
            conn.executescript(ASTRA_SCHEMA)
        conn.close()
        with self.assertRaises(RuntimeError) as ctx:
            db.initialize()
        self.assertIn('ancien schéma ASTRA', str(ctx.exception))


class EventAndSettingTests(TempDbCase):
    def test_insert_event_returns_contract_shape(self):
        with db.connect() as conn:
            event = db.insert_event(conn, subject_type='task', subject_id=8, kind='tool.result',
                                    label='Mesure système', run_id=8, stage='tool',
                                    payload={'ok': True, 'texte': 'é'}, duration_ms=12.4)
        self.assertEqual(set(event), {'id', 'subject_type', 'subject_id', 'kind', 'label', 'run_id', 'stage',
                                      'payload', 'duration_ms', 'level', 'created_at'})
        self.assertEqual(event['payload'], {'ok': True, 'texte': 'é'})
        self.assertEqual((event['run_id'], event['stage'], event['level']), (8, 'tool', 'info'))
        self.assertRegex(event['created_at'], ISO_UTC)
        stored = db.event_dict(db.row('SELECT * FROM events WHERE id=?', (event['id'],)))
        self.assertEqual(stored, event)

    def test_settings_roundtrip_json(self):
        self.assertEqual(db.get_setting('mode', 'equilibre'), 'equilibre')
        db.set_setting('mode', 'prive')
        db.set_setting('allowed_roots', ['C:/Données', 'D:/'])
        db.set_setting('cloud', {'budget': 1.5, 'actif': False})
        db.set_setting('mode', 'avance')
        self.assertEqual(db.get_setting('mode'), 'avance')
        self.assertEqual(db.get_setting('allowed_roots'), ['C:/Données', 'D:/'])
        self.assertEqual(db.get_setting('cloud'), {'budget': 1.5, 'actif': False})
        self.assertIsNone(db.get_setting('absent'))


class StarterCompatTests(TempDbCase):
    def test_task_lifecycle_events(self):
        task = db.add_task('Analyser le PC')
        self.assertEqual((task['status'], task['kind'], task['source']), ('pending', 'manual', 'manual'))
        running = db.set_task_status(task['id'], 'running')
        self.assertIsNotNone(running['started_at'])
        self.assertIsNotNone(db.set_task_status(task['id'], 'running'))  # unchanged: no new event
        done = db.complete_task(task['id'], 'CPU 12 %')
        self.assertEqual((done['status'], done['result_text']), ('done', 'CPU 12 %'))
        self.assertIsNotNone(done['finished_at'])
        self.assertIsNotNone(done['duration_ms'])
        self.assertEqual(self.kinds(), ['task.pending', 'task.running', 'task.done'])
        events = db.rows('SELECT run_id, subject_id, subject_type FROM events')
        self.assertTrue(all(e['run_id'] == task['id'] == e['subject_id'] for e in events))
        self.assertEqual([e['kind'] for e in self.published], self.kinds())

    def test_task_error_and_validation(self):
        task = db.add_task('Tâche fragile')
        db.set_task_status(task['id'], 'error')
        event = db.event_dict(db.row("SELECT * FROM events WHERE kind='task.error'"))
        self.assertEqual(event['level'], 'error')
        with self.assertRaises(ValueError):
            db.set_task_status(task['id'], 'bientot')
        self.assertIsNone(db.set_task_status(999, 'done'))
        self.assertIsNone(db.complete_task(999, 'x'))

    def test_memory_helpers_and_clear_events(self):
        memory = db.add_memory('Mon projet', 'ASTRA', 'projet')
        self.assertEqual((memory['category'], memory['source']), ('projet', 'user'))
        self.assertTrue(db.delete_memory(memory['id']))
        self.assertFalse(db.delete_memory(memory['id']))
        self.assertEqual(self.kinds(), ['memory.saved', 'memory.deleted'])
        self.assertEqual(db.clear_events(), 2)
        self.assertEqual(self.kinds(), [])


class AstraImportTests(TempDbCase):
    def make_astra(self, schema=ASTRA_SCHEMA):
        self.astra_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.astra_file)
        conn.executescript(schema)
        conn.executemany('INSERT INTO memories(id, title, detail, category, created_at) VALUES (?,?,?,?,?)', [
            (3, 'Projet Mars', 'Simulation orbitale de la sonde', 'projet', '2026-09-01T10:00:00.000Z'),
            (5, 'Mode sombre', 'Je préfère le thème sombre', 'preference', '2026-09-02T10:00:00.000Z')])
        conn.executemany('INSERT INTO tasks(id, title, status, result_text, created_at, updated_at) VALUES (?,?,?,?,?,?)', [
            (1, 'Diagnostic ponctuel du PC', 'done', 'CPU 10 %', '2026-09-03T10:00:00.000Z', '2026-09-03T10:00:01.000Z'),
            (2, 'Ranger les notes', 'running', '', '2026-09-04T10:00:00.000Z', '2026-09-04T10:00:00.000Z')])
        conn.executemany('INSERT INTO events(id, subject_type, subject_id, kind, label, created_at) VALUES (?,?,?,?,?,?)', [
            (1, 'memory', 3, 'saved', 'Projet Mars', '2026-09-01T10:00:00.000Z'),
            (2, 'task', 1, 'pending', 'Diagnostic ponctuel du PC', '2026-09-03T10:00:00.000Z'),
            (3, 'task', 1, 'running', 'Diagnostic ponctuel du PC', '2026-09-03T10:00:00.500Z'),
            (4, 'task', 1, 'done', 'Diagnostic ponctuel du PC', '2026-09-03T10:00:01.000Z'),
            (5, 'memory', 9, 'deleted', 'Mémoire supprimée', '2026-09-05T10:00:00.000Z')])
        conn.commit()
        conn.close()

    def protect(self):
        """Make the ASTRA file read-only: the import must not need write access."""
        before = (_sha256(self.astra_file), self.astra_file.stat().st_mtime_ns)
        os.chmod(self.astra_file, stat.S_IREAD)
        self.addCleanup(os.chmod, self.astra_file, stat.S_IREAD | stat.S_IWRITE)
        return before

    def test_import_copies_memories_tasks_events_read_only(self):
        self.make_astra()
        before = self.protect()
        db.initialize()

        memories = db.rows('SELECT * FROM memories ORDER BY id')
        self.assertEqual([(m['id'], m['title'], m['source']) for m in memories],
                         [(3, 'Projet Mars', 'astra-import'), (5, 'Mode sombre', 'astra-import')])
        self.assertEqual(memories[0]['created_at'], '2026-09-01T10:00:00.000Z')
        self.assertEqual((memories[0]['kind'], memories[1]['kind']), ('project', 'preference'))
        self.assertEqual(memories[0]['source_ref'], 'astra:memory:3')

        tasks = db.rows('SELECT * FROM tasks ORDER BY id')
        self.assertEqual([(t['id'], t['status'], t['kind'], t['source']) for t in tasks],
                         [(1, 'done', 'manual', 'astra-import'), (2, 'running', 'manual', 'astra-import')])
        self.assertEqual(tasks[0]['result_text'], 'CPU 10 %')

        events = [db.event_dict(r) for r in db.rows('SELECT * FROM events ORDER BY id')]
        self.assertEqual([e['kind'] for e in events],
                         ['saved', 'pending', 'running', 'done', 'deleted', 'system.imported'])
        imported = events[-1]
        self.assertEqual((imported['subject_type'], imported['stage']), ('system', 'system'))
        self.assertEqual({k: imported['payload'][k] for k in ('memories', 'tasks', 'events')},
                         {'memories': 2, 'tasks': 2, 'events': 5})
        self.assertTrue(imported['payload']['ok'])
        self.assertEqual(self.published[-1]['kind'], 'system.imported')

        # FTS index covers imported rows; new ids continue after the imported ones.
        self.assertEqual(db.rows("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'orbitale'"),
                         [{'rowid': 3}])
        self.assertGreater(store.add_memory('Nouveau', auto_link=False)['id'], 5)

        # ASTRA untouched, no journal/WAL files created next to it.
        os.chmod(self.astra_file, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual((_sha256(self.astra_file), self.astra_file.stat().st_mtime_ns), before)
        self.assertEqual(sorted(p.name for p in self.astra_file.parent.iterdir()), ['astra.db'])
        self.assertEqual(db.get_setting(db.ASTRA_IMPORT_SETTING)['status'], 'done')

    def test_import_runs_once(self):
        self.make_astra()
        db.initialize()
        db.initialize()
        self.assertEqual(db.rows('SELECT COUNT(*) AS n FROM memories')[0]['n'], 2)
        self.assertEqual(db.rows("SELECT COUNT(*) AS n FROM events WHERE kind='system.imported'")[0]['n'], 1)

    def test_no_import_into_existing_aurora_database(self):
        db.initialize()                      # ASTRA absent at creation time
        store.add_memory('Souvenir AURORA', auto_link=False)
        self.make_astra()
        db.initialize()
        self.assertEqual([m['title'] for m in db.rows('SELECT title FROM memories')], ['Souvenir AURORA'])
        # Even if the marker setting is lost, existing data blocks the import.
        with db.connect() as conn:
            conn.execute('DELETE FROM settings')
        db.initialize()
        self.assertEqual(db.rows('SELECT COUNT(*) AS n FROM memories')[0]['n'], 1)
        self.assertEqual(db.get_setting(db.ASTRA_IMPORT_SETTING)['status'], 'skipped')

    def test_invalid_statuses_and_subjects_are_sanitised(self):
        loose = ASTRA_SCHEMA.replace("CHECK (subject_type IN ('memory', 'task'))", '')
        self.make_astra(loose)
        conn = sqlite3.connect(self.astra_file)
        conn.execute("INSERT INTO tasks(id, title, status) VALUES (9, 'Étrange', 'bizarre')")
        conn.execute("INSERT INTO events(subject_type, subject_id, kind, label) VALUES ('ufo', 1, 'x', 'y')")
        conn.commit()
        conn.close()
        db.initialize()
        self.assertEqual(db.row('SELECT status FROM tasks WHERE id=9')['status'], 'interrupted')
        imported = db.event_dict(db.row("SELECT * FROM events WHERE kind='system.imported'"))
        self.assertEqual(imported['payload']['skipped'], 1)

    def test_unreadable_astra_file_does_not_block_startup(self):
        self.astra_file.parent.mkdir(parents=True, exist_ok=True)
        self.astra_file.write_bytes(b'ceci n est pas une base sqlite' * 50)
        with self.assertLogs('aurora.db', level='WARNING'):
            db.initialize()
        setting = db.get_setting(db.ASTRA_IMPORT_SETTING)
        self.assertEqual(setting['status'], 'error')
        event = db.event_dict(db.row("SELECT * FROM events WHERE kind='system.imported'"))
        self.assertEqual(event['level'], 'warn')
        self.assertFalse(event['payload']['ok'])

    def test_same_file_is_never_imported_into_itself(self):
        with mock.patch.dict(os.environ, {'ASTRA_DB_PATH': str(self.db_file)}):
            db.initialize()
        self.assertEqual(db.get_setting(db.ASTRA_IMPORT_SETTING)['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
