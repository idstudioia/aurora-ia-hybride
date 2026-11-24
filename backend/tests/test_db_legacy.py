"""Run: cd backend && python -m unittest discover -s tests -t .

ASTRA v0.1 starter test, adapted to AURORA: package ``aurora`` instead of ``app``,
namespaced event kinds, and the database path variable is now AURORA_DB_PATH
(ASTRA_DB_PATH is the read-only import source, pointed at a missing file here).
"""
import os
import tempfile
import unittest
from pathlib import Path
from aurora import db

class StorageTests(unittest.TestCase):
    def test_task_events_and_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = {name: os.environ.get(name) for name in ('AURORA_DB_PATH', 'ASTRA_DB_PATH')}
            os.environ['AURORA_DB_PATH'] = str(Path(temp) / 'test.db')
            os.environ['ASTRA_DB_PATH'] = str(Path(temp) / 'no-astra.db')
            try:
                db.initialize()
                task = db.add_task('Tester le PC')
                self.assertEqual(task['status'], 'pending')
                db.set_task_status(task['id'], 'running')
                db.set_task_status(task['id'], 'done')
                events = db.rows('SELECT kind FROM events ORDER BY id')
                self.assertEqual([event['kind'] for event in events],
                                 ['task.pending', 'task.running', 'task.done'])
                memory = db.add_memory('Mon projet', 'ASTRA', 'projet')
                self.assertEqual(memory['category'], 'projet')
                self.assertTrue(db.delete_memory(memory['id']))
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

if __name__ == '__main__':
    unittest.main()
