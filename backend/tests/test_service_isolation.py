"""The running service never imports the « tests » package (28/10/2026, found while merging H3).

tests/__init__.py sets TEST defaults (Windows vault, SearXNG and page reading off). « Lancer le banc » in
Paramètres imported tests.bench_routing, and so did the « Signaler » report list: one click and the running
service lost the Claude key, SearXNG and page reading until restart. The bench is now loaded by its file path
(core/bench.py). Checked in a fresh process with a clean environment, as the real service starts.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
WATCHED = ('AURORA_VAULT', 'AURORA_SEARXNG_URL', 'AURORA_READ_PAGES')

PROBE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from aurora import db, main
from aurora.core import normalize
from aurora.tasks import reports
db.initialize()
result = main._run_bench()                                   # the « Lancer le banc » button
bench, _training = reports._known_sentences(normalize.normalize)   # the « Signaler » list
print(json.dumps({'tests_imported': any(m == 'tests' or m.startswith('tests.') for m in sys.modules),
                  'env': {k: os.environ.get(k) for k in sys.argv[2].split(',')},
                  'bench_ran': isinstance(result, dict), 'bench_sentences': len(bench)}))
'''


class ServiceIsolationTests(unittest.TestCase):
    def test_bench_and_reports_leave_the_service_settings_alone(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
            env = {k: v for k, v in os.environ.items() if k not in WATCHED}
            env.update({'AURORA_HOME': home, 'AURORA_DB_PATH': str(Path(home) / 'aurora.db'), 'PYTHONIOENCODING': 'utf-8'})
            done = subprocess.run([sys.executable, '-c', PROBE, str(BACKEND), ','.join(WATCHED)], env=env,
                                  capture_output=True, text=True, encoding='utf-8', timeout=240)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        seen = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertFalse(seen['tests_imported'])
        self.assertEqual(seen['env'], {k: None for k in WATCHED})
        self.assertTrue(seen['bench_ran'])
        self.assertGreater(seen['bench_sentences'], 100)


if __name__ == '__main__':
    unittest.main()
