"""« Signaler » (H3): reports on runs, masking of sensitive sentences, export in the bench format, migration v2 -> v3.

Cases ported from Athéna's tests/signalements.test.ts: a report counts at once, two clicks make one case, a
report can be corrected and the export follows, a deleted report leaves the export. Every test uses a
temporary database (TempEnv): the real ~/.aurora/aurora.db is never opened.
Run: cd backend && python -m unittest tests.test_reports
"""
import hashlib
import io
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

from aurora import db
from aurora.core import semantic
from aurora.core.normalize import normalize
from aurora.core.router import DecisionEngine
from aurora.memory import store
from aurora.tasks import reports, runs
from tests import bench_routing
from tests.doubles_core import TempEnv

try:   # the HTTP part reuses the API test fixture (orchestrator wired to doubles)
    from tests.test_api import ApiTestCase, NO_TOKEN, ORIGIN
except Exception:  # pragma: no cover - fastapi missing
    ApiTestCase = None


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def make_run(text: str, *, intent: str = 'chat.general', method: str = 'rule', source: str = 'text',
             status: str = 'done', meta: dict | None = None, attachments: list | None = None,
             store_message: bool = True) -> int:
    """A finished run, as the orchestrator leaves it (input_text, intent, decision, user message)."""
    conversation = store.create_conversation(text)
    run_id = int(runs.create_run(text, conversation_id=conversation['id'], source=source)['id'])
    runs.update_run(run_id, intent=intent, decision_json={
        'decision': {'intent': intent, 'method': method}, 'attachments': attachments or [], 'source': source})
    if store_message:
        store.add_message(conversation['id'], 'user', text, run_id=run_id, meta=meta or {'source': source})
    if status != 'running':
        runs.set_run_status(run_id, status, result_text='Réponse.')
    return run_id


def export_lines() -> list[dict]:
    text, count = reports.export_jsonl()
    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(lines) == count
    return lines


class ReportCase(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        self.addCleanup(self.env.__exit__, None, None, None)


class SaveAndCorrectTests(ReportCase):
    def test_a_report_counts_at_once_with_every_field(self):
        run_id = make_run('Mets la musique plus fort', intent='system.status')
        saved = reports.save_report(run_id, 'misunderstood', 'chat.general', 'Il a mesuré le PC.')
        self.assertEqual(saved['run_id'], run_id)
        self.assertEqual(saved['text'], 'Mets la musique plus fort')
        self.assertEqual(saved['got_intent'], 'system.status')
        self.assertEqual(saved['expected_intent'], 'chat.general')
        self.assertEqual(saved['reason'], 'misunderstood')
        self.assertEqual(saved['reason_label'], 'Mauvaise compréhension de la demande')
        self.assertEqual(saved['comment'], 'Il a mesuré le PC.')
        self.assertEqual(saved['method'], 'rule')
        self.assertFalse(saved['sensitive'])
        self.assertFalse(saved['masked'])
        self.assertTrue(saved['created_at'].endswith('Z'))
        self.assertEqual(reports.get_report(run_id)['id'], saved['id'])
        self.assertEqual([r['run_id'] for r in reports.list_reports()], [run_id])

    def test_two_clicks_on_the_same_answer_make_one_case(self):
        run_id = make_run('Quelle heure est-il à Tokyo ?', intent='time.now')
        first = reports.save_report(run_id, 'wrong_answer')
        second = reports.save_report(run_id, 'wrong_answer')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM run_reports')['n'], 1)

    def test_a_report_is_corrected_and_the_export_follows(self):
        run_id = make_run('Ouvre la page de mes souvenirs', intent='memory.search')
        first = reports.save_report(run_id, 'misunderstood', 'memory.search', 'mal choisi')
        self.assertEqual(export_lines(), [{'text': 'Ouvre la page de mes souvenirs', 'intent': 'memory.search',
                                           'tag': 'normal'}])
        corrected = reports.save_report(run_id, 'off_topic', 'ui.navigate', '')
        self.assertEqual(corrected['id'], first['id'])
        self.assertEqual(corrected['created_at'], first['created_at'])      # a correction, not a new case
        self.assertEqual((corrected['reason'], corrected['expected_intent'], corrected['comment']),
                         ('off_topic', 'ui.navigate', ''))
        self.assertEqual([line['intent'] for line in export_lines()], ['ui.navigate'])
        cleared = reports.save_report(run_id, 'other')                      # expected intent withdrawn
        self.assertIsNone(cleared['expected_intent'])
        self.assertEqual(export_lines(), [])

    def test_a_cancelled_report_leaves_the_list_and_the_export(self):
        run_id = make_run('Range mon bureau numérique', intent='chat.general')
        reports.save_report(run_id, 'misunderstood', 'files.cleanup')
        self.assertEqual(len(export_lines()), 1)
        self.assertTrue(reports.delete_report(run_id))
        self.assertIsNone(reports.get_report(run_id))
        self.assertEqual(reports.list_reports(), [])
        self.assertEqual(export_lines(), [])
        self.assertFalse(reports.delete_report(run_id))                      # deleting twice: nothing left

    def test_invalid_reports_are_refused(self):
        run_id = make_run('Bonjour', intent='chat.general')
        with self.assertRaises(ValueError):
            reports.save_report(run_id, 'pas-une-raison')
        with self.assertRaises(ValueError):
            reports.save_report(run_id, 'other', 'shell.exec')
        self.assertIsNone(reports.save_report(424242, 'other'))              # unknown run
        manual = db.add_task('Acheter du pain')                             # a manual task is not an answer
        self.assertIsNone(reports.save_report(manual['id'], 'other'))
        running = make_run('Analyse mon disque', status='running')
        with self.assertRaises(reports.RunInProgress):
            reports.save_report(running, 'wrong_answer')
        self.assertEqual(reports.list_reports(), [])
        with self.assertRaises(sqlite3.IntegrityError):                     # the table enforces the reasons too
            with db.connect() as conn:
                conn.execute("INSERT INTO run_reports(run_id, text, reason) VALUES (?, 'x', 'bad')", (run_id,))

    def test_a_report_follows_its_run(self):
        run_id = make_run('Bonjour', intent='chat.general')
        reports.save_report(run_id, 'other')
        with db.connect() as conn:
            conn.execute('DELETE FROM tasks WHERE id=?', (run_id,))
        self.assertIsNone(reports.get_report(run_id))                        # ON DELETE CASCADE


class SensitiveTests(ReportCase):
    SECRET = 'Retiens que mon mot de passe Wi-Fi est Soleil2024!'

    def test_a_sentence_marked_sensitive_is_masked_before_it_is_stored(self):
        run_id = make_run(self.SECRET, intent='chat.general', meta={'source': 'text', 'sensitive': True})
        saved = reports.save_report(run_id, 'misunderstood', 'memory.save')
        self.assertTrue(saved['sensitive'])
        self.assertTrue(saved['masked'])
        self.assertNotIn('Soleil2024', saved['text'])
        self.assertIn('[MOT_DE_PASSE]', saved['text'])
        stored = db.row('SELECT text FROM run_reports WHERE run_id=?', (run_id,))['text']
        self.assertNotIn('Soleil2024', stored)                              # in the database itself
        line = export_lines()[0]                                            # the masked sentence is exportable
        self.assertNotIn('Soleil2024', line['text'])
        self.assertEqual(line['intent'], 'memory.save')

    def test_a_secret_is_masked_even_without_the_mark(self):
        key = 'sk-ant-api03-abcdefghijklmnop1234'
        run_id = make_run(f'Garde cette clé : {key}', intent='chat.general', store_message=False)
        saved = reports.save_report(run_id, 'wrong_answer', 'memory.save')
        self.assertTrue(saved['sensitive'])
        self.assertNotIn(key, saved['text'])
        self.assertIn('[CLÉ_API]', saved['text'])

    def test_a_sensitive_sentence_that_cannot_be_cleaned_is_never_exported(self):
        run_id = make_run('Que penses-tu de mes symptômes de grippe ?', intent='web.search',
                          meta={'source': 'text', 'sensitive': True})
        saved = reports.save_report(run_id, 'off_topic', 'chat.general')
        self.assertTrue(saved['sensitive'])
        self.assertFalse(saved['masked'])
        self.assertEqual(reports.list_reports()[0]['export'], 'sensitive')
        self.assertEqual(export_lines(), [])

    def test_the_comment_is_masked_too(self):
        run_id = make_run('Bonjour', intent='chat.general')
        saved = reports.save_report(run_id, 'other', None, 'mon mot de passe est Hibou-77 et mail a@b.fr')
        self.assertNotIn('Hibou-77', saved['comment'])
        self.assertNotIn('a@b.fr', saved['comment'])

    def test_an_ordinary_sentence_is_kept_as_typed(self):
        run_id = make_run('Quel temps fera-t-il à Lyon demain ?', intent='chat.general')
        saved = reports.save_report(run_id, 'misunderstood', 'weather.current')
        self.assertEqual(saved['text'], 'Quel temps fera-t-il à Lyon demain ?')
        self.assertFalse(saved['sensitive'])


class ExportTests(ReportCase):
    def test_export_has_the_bench_format_and_loads_in_the_bench(self):
        voice = make_run('aurora met la meteo de lile', intent='chat.general', source='voice')
        typed = make_run('Fais-moi un résumé de la situation au Japon', intent='chat.general')
        reports.save_report(voice, 'misunderstood', 'weather.current')
        reports.save_report(typed, 'wrong_answer', 'news.latest')
        text, count = reports.export_jsonl()
        self.assertEqual(count, 2)
        self.assertTrue(text.endswith('\n'))
        lines = [json.loads(line) for line in text.splitlines()]
        for line in lines:
            self.assertEqual(list(line), ['text', 'intent', 'tag'])          # same keys, same order as the corpus
            self.assertIn(line['tag'], bench_routing.TAGS)
        self.assertEqual([line['tag'] for line in lines], ['voix', 'normal'])   # oldest first
        self.assertIn('é', text)                                              # UTF-8 like the corpus, not é
        with tempfile.TemporaryDirectory() as tmp:                           # the bench reads it as it is
            path = Path(tmp) / 'extra.jsonl'
            path.write_text(text, encoding='utf-8')
            self.assertEqual(bench_routing.load_bench(path), lines)

    def test_only_true_routing_cases_are_exported(self):
        cases = {
            'no_expected': (make_run('Coucou toi', intent='chat.general'), None),
            'explicit': (make_run('Monte-Cristo', intent='web.search', method='explicit'), 'chat.general'),
            'attachment': (make_run('Analyse ce document.', intent='doc.analyze',
                                    attachments=[{'name': 'a.txt', 'text': 'x'}]), 'chat.general'),
            'in_bench': (make_run('Quelle quantité de RAM est utilisée ?', intent='chat.general'), 'system.status'),
            'in_training': (make_run('Combien de mémoire vive me reste-t-il ?', intent='chat.general'),
                            'system.status'),
        }
        for run_id, expected in cases.values():
            reports.save_report(run_id, 'misunderstood', expected)
        old = make_run('Calcule vingt pourcent de 80', intent='chat.general')
        new = make_run('calcule vingt pourcent de 80 !', intent='chat.general')   # same normalised sentence
        reports.save_report(old, 'misunderstood', 'calc.convert')
        reports.save_report(new, 'misunderstood', 'calc.evaluate')
        states = {r['run_id']: r['export'] for r in reports.list_reports()}
        for state, (run_id, _) in cases.items():
            self.assertEqual(states[run_id], state)
        self.assertEqual((states[new], states[old]), ('ok', 'duplicate'))   # the latest report of a sentence wins
        self.assertTrue(all(r['export_label'] for r in reports.list_reports()))
        self.assertEqual(export_lines(), [{'text': 'calcule vingt pourcent de 80 !', 'intent': 'calc.evaluate',
                                           'tag': 'normal'}])

    def test_export_never_writes_into_the_repository(self):
        corpus = bench_routing.BENCH_PATH
        before = (hashlib.sha256(corpus.read_bytes()).hexdigest(), corpus.stat().st_mtime_ns)
        run_id = make_run('Éteins la lumière du salon', intent='chat.general')
        reports.save_report(run_id, 'misunderstood', 'chat.general')
        reports.export_jsonl()
        reports.list_reports()
        self.assertEqual((hashlib.sha256(corpus.read_bytes()).hexdigest(), corpus.stat().st_mtime_ns), before)

    def test_export_still_works_without_the_bench_corpus(self):
        run_id = make_run('Quelle quantité de RAM est utilisée ?', intent='chat.general')
        reports.save_report(run_id, 'misunderstood', 'system.status')
        from aurora.core import bench   # the bench is loaded by path (core/bench.py), never as « tests »

        def no_bench():
            raise ImportError('banc absent')
        with mock.patch.object(bench, 'module', side_effect=no_bench):
            self.assertEqual(len(export_lines()), 1)                         # unchecked against the bench, not lost


class ReplayTests(ReportCase):
    """A1: the « ok » reports are replayed by the bench (« Lancer le banc d’essai »), apart from the corpus score."""

    FIXED, WRONG = 'Allume la radio dans la cuisine', 'Mets la musique plus fort dans le salon'

    def reported(self) -> tuple[int, int]:
        """One report the rules now get right, one they still get wrong, whatever the rules say today."""
        now = DecisionEngine(use_semantic=False).decide_fast
        fixed = make_run(self.FIXED, intent='weather.current')
        wrong = make_run(self.WRONG, intent='chat.general')
        reports.save_report(fixed, 'misunderstood', now(self.FIXED).intent)
        reports.save_report(wrong, 'misunderstood', 'time.now' if now(self.WRONG).intent != 'time.now' else 'calc.evaluate')
        self.assertEqual([r['export'] for r in reports.list_reports()], ['ok', 'ok'])
        return fixed, wrong

    def test_bench_rows_are_the_exported_reports_with_their_run(self):
        fixed, wrong = self.reported()
        rows = reports.bench_rows()
        self.assertEqual([r['run_id'] for r in rows], [fixed, wrong])                 # oldest first, like the export
        self.assertEqual(rows[0]['got_intent'], 'weather.current')                    # understood when reported
        self.assertEqual([{k: r[k] for k in ('text', 'intent', 'tag')} for r in rows], export_lines())

    def test_the_bench_replays_them_apart_from_the_corpus(self):
        fixed, wrong = self.reported()
        alone = bench_routing.run_bench(use_semantic=False, write=False)
        replayed = bench_routing.run_bench(use_semantic=False, write=False, reports=reports.bench_rows())
        self.assertNotIn('reports', alone)                                            # the command line default
        for key in ('correct', 'n', 'failures'):                                      # never in the corpus score
            self.assertEqual(replayed['rules'][key], alone['rules'][key])
        block = replayed['reports']
        self.assertEqual((block['n'], block['ignored'], block['rules']['correct']), (2, 0, 1))
        cases = {case['run_id']: case for case in block['cases']}
        self.assertEqual(cases[fixed]['now'], cases[fixed]['expected'])
        self.assertEqual(cases[fixed]['reported'], 'weather.current')
        self.assertNotEqual(cases[wrong]['now'], cases[wrong]['expected'])
        self.assertEqual([f['text'] for f in block['rules']['failures']], [self.WRONG])
        json.dumps(replayed)

    def test_no_report_is_said_and_unreadable_lines_are_counted(self):
        self.assertEqual(bench_routing.run_bench(use_semantic=False, write=False, reports=[])['reports']['n'], 0)
        block = bench_routing._replay_reports(
            [{'text': ''}, 'x', {'text': 'Quelle heure est-il ?'},
             {'text': 'Quelle heure est-il ?', 'intent': 'time.now'}], use_semantic=True)
        self.assertEqual((block['n'], block['ignored']), (1, 3))
        self.assertIn('rules_semantic', block)
        self.assertIn('now_semantic', block['cases'][0])


class CorpusAddTests(unittest.TestCase):
    """A1: « --add-reports » appends an export to the corpus under BenchCorpusTests' rules (temporary copy)."""

    NEW = {'text': 'Allume la radio dans la cuisine', 'intent': 'chat.general', 'tag': 'normal'}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.corpus = Path(tmp.name) / 'routing_bench.jsonl'
        self.corpus.write_bytes(bench_routing.BENCH_PATH.read_bytes())
        self.real = hashlib.sha256(bench_routing.BENCH_PATH.read_bytes()).hexdigest()

    def tearDown(self):   # the repository corpus never moves in a test
        self.assertEqual(hashlib.sha256(bench_routing.BENCH_PATH.read_bytes()).hexdigest(), self.real)

    def test_only_new_valid_sentences_are_appended(self):
        before = self.corpus.read_bytes()
        rows = [self.NEW,
                {'text': 'allume la radio dans la cuisine !', 'intent': 'chat.general', 'tag': 'normal'},
                {'text': 'Quelle quantité de RAM est utilisée ?', 'intent': 'system.status', 'tag': 'normal'},
                {'text': 'Combien de mémoire vive me reste-t-il ?', 'intent': 'system.status', 'tag': 'normal'},
                {'text': 'Lance un terminal', 'intent': 'shell.exec', 'tag': 'normal'},
                {'text': 'Joue du jazz', 'intent': 'chat.general', 'tag': 'poème'},
                {'text': '  ', 'intent': 'chat.general'},
                'pas un objet']
        result = bench_routing.add_to_corpus(rows, self.corpus)
        self.assertEqual(result['added'], [self.NEW])
        self.assertEqual([s['reason'] for s in result['skipped']],
                         ['déjà dans le banc', 'déjà dans le banc', 'identique à un exemple d’entraînement',
                          'intention inconnue : shell.exec', 'étiquette inconnue : poème', 'phrase vide', 'phrase vide'])
        after = self.corpus.read_bytes()
        self.assertTrue(after.startswith(before))                                     # appended, nothing rewritten
        self.assertEqual(after[len(before):].decode('utf-8'), json.dumps(self.NEW, ensure_ascii=False) + '\n')
        self.assertEqual(bench_routing.add_to_corpus(rows, self.corpus)['added'], [])  # twice adds nothing
        self.assertEqual(self.corpus.read_bytes(), after)
        rows_after = bench_routing.load_bench(self.corpus)                           # BenchCorpusTests still holds
        training = {normalize(r['text']) for r in semantic.load_examples()}
        self.assertEqual([r['text'] for r in rows_after if normalize(r['text']) in training], [])
        self.assertTrue({r['tag'] for r in rows_after} <= set(bench_routing.TAGS))

    def test_a_corpus_without_final_newline_keeps_one_line_per_sentence(self):
        self.corpus.write_bytes(self.corpus.read_bytes().rstrip(b'\n'))
        bench_routing.add_to_corpus([{'text': self.NEW['text'], 'intent': 'chat.general'}], self.corpus)
        rows = bench_routing.load_bench(self.corpus)
        self.assertEqual(rows[-1], self.NEW)                                          # tag « normal » by default
        self.assertEqual(len(rows), len(bench_routing.load_bench()) + 1)

    def test_the_command_line_adds_an_export_and_replays_one(self):
        export = self.corpus.with_name('signalements-banc.jsonl')
        export.write_text(json.dumps(self.NEW, ensure_ascii=False) + '\n', encoding='utf-8')
        out = io.StringIO()
        with mock.patch.object(bench_routing, 'BENCH_PATH', self.corpus), redirect_stdout(out):
            self.assertEqual(bench_routing.main(['--add-reports', str(export)]), 0)
        self.assertIn('1 phrase(s) ajoutée(s)', out.getvalue())
        self.assertEqual(bench_routing.load_bench(self.corpus)[-1], self.NEW)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bench_routing.main(['--reports', str(export), '--no-semantic', '--no-write']), 0)
        self.assertIn('Signalements rejoués, hors score du corpus', out.getvalue())


class MigrationTests(unittest.TestCase):
    """CLAUDE.md: a base is saved before its schema is migrated (v2 -> v3 adds run_reports)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db_file = self.dir / 'aurora.db'
        env = mock.patch.dict(os.environ, {'AURORA_DB_PATH': str(self.db_file), 'AURORA_HOME': str(self.dir),
                                           'ASTRA_DB_PATH': str(self.dir / 'absent.db'), 'AURORA_OFFLINE': '1'})
        env.start()
        self.addCleanup(env.stop)

    def test_v2_base_is_saved_then_migrated(self):
        self.assertEqual(db.SCHEMA_VERSION, 3)
        db.initialize()
        with db.connect() as conn:                                           # a v2 base: no run_reports yet
            conn.execute('DROP TABLE run_reports')
            conn.execute("INSERT INTO memories(title, detail) VALUES ('Projet Hélios', 'à garder')")
        with closing(sqlite3.connect(str(self.db_file))) as raw:
            raw.execute('PRAGMA user_version=2')
            raw.commit()
        db.initialize()
        copies = list((self.dir / 'backups').glob('aurora-v2-avant-v3-*.db'))
        self.assertEqual(len(copies), 1)
        with closing(sqlite3.connect(str(copies[0]))) as copy:               # the copy is the v2 base, intact
            self.assertEqual(copy.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(copy.execute('SELECT title FROM memories').fetchone()[0], 'Projet Hélios')
            self.assertIsNone(copy.execute("SELECT 1 FROM sqlite_master WHERE name='run_reports'").fetchone())
        with closing(sqlite3.connect(str(self.db_file))) as raw:             # the base is migrated, data kept
            self.assertEqual(raw.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(raw.execute('SELECT title FROM memories').fetchone()[0], 'Projet Hélios')
            columns = [r[1] for r in raw.execute('PRAGMA table_info(run_reports)')]
        self.assertEqual(columns, ['id', 'run_id', 'conversation_id', 'text', 'sensitive', 'masked', 'source',
                                   'attachments', 'got_intent', 'method', 'expected_intent', 'reason', 'comment',
                                   'created_at', 'updated_at'])
        kinds = [r['kind'] for r in db.rows("SELECT kind FROM events WHERE kind='system.backup'")]
        self.assertEqual(kinds, ['system.backup'])
        db.initialize()                                                      # same version: no second copy
        self.assertEqual(len(list((self.dir / 'backups').glob('*.db'))), 1)


if ApiTestCase is not None:
    class ReportApiTests(ApiTestCase):
        def _run(self, text='Quelle quantité de RAM est utilisée ?') -> int:
            res = self.client.post('/api/ask', json={'text': text}, headers=self.auth)
            self.assertEqual(res.status_code, 200, res.text)
            return res.json()['task_id']

        def test_routes_require_the_token(self):
            run_id = self._run()
            body = {'reason': 'misunderstood'}
            self.assertEqual(self.client.put(f'/api/runs/{run_id}/report', json=body, headers=NO_TOKEN).status_code,
                             401)
            self.assertEqual(self.client.get(f'/api/runs/{run_id}/report', headers=NO_TOKEN).status_code, 401)
            self.assertEqual(self.client.delete(f'/api/runs/{run_id}/report', headers=NO_TOKEN).status_code, 401)
            for path in ('/api/quality/reports', '/api/quality/reports/export'):
                self.assertEqual(self.client.get(path, headers=NO_TOKEN).status_code, 401, path)
            self.assertIsNone(reports.get_report(run_id))                    # nothing written without the token

        def test_report_list_export_and_cancel_over_http(self):
            run_id = self._run('Allume la radio dans la cuisine')
            self.assertIsNone(self.client.get(f'/api/runs/{run_id}/report').json())
            res = self.client.put(f'/api/runs/{run_id}/report',
                                  json={'reason': 'misunderstood', 'expected_intent': 'system.status',
                                        'comment': 'test'})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()['got_intent'], 'chat.general')
            again = self.client.put(f'/api/runs/{run_id}/report', json={'reason': 'wrong_answer',
                                                                        'expected_intent': 'system.status'})
            self.assertEqual(again.json()['id'], res.json()['id'])
            self.assertEqual(self.client.get(f'/api/runs/{run_id}/report').json()['reason'], 'wrong_answer')
            listed = self.client.get('/api/quality/reports').json()
            self.assertEqual([(r['run_id'], r['export']) for r in listed], [(run_id, 'ok')])
            export = self.client.get('/api/quality/reports/export')
            self.assertEqual(export.status_code, 200)
            self.assertIn('attachment', export.headers['content-disposition'])
            self.assertIn('.jsonl', export.headers['content-disposition'])
            self.assertEqual([json.loads(x) for x in export.text.splitlines()],
                             [{'text': 'Allume la radio dans la cuisine', 'intent': 'system.status', 'tag': 'normal'}])
            self.assertEqual(self.client.delete(f'/api/runs/{run_id}/report').status_code, 204)
            self.assertEqual(self.client.delete(f'/api/runs/{run_id}/report').status_code, 404)
            self.assertEqual(self.client.get('/api/quality/reports/export').text, '')

        def test_the_bench_button_replays_the_reports(self):
            run_id = self._run('Allume la radio dans la cuisine')
            self.client.put(f'/api/runs/{run_id}/report',
                            json={'reason': 'misunderstood', 'expected_intent': 'system.status'})
            res = self.client.post('/api/quality/bench')
            self.assertEqual(res.status_code, 200, res.text)
            block = res.json()['reports']
            self.assertEqual((block['n'], block['cases'][0]['run_id']), (1, run_id))
            self.assertEqual(block['cases'][0]['expected'], 'system.status')
            self.assertEqual(res.json()['corpus']['n'], len(bench_routing.load_bench()))   # corpus unchanged
            self.assertEqual(self.client.get('/api/quality/latest').json()['reports']['n'], 1)
            with mock.patch.object(reports, 'bench_rows', side_effect=RuntimeError('base illisible')):
                res = self.client.post('/api/quality/bench')
            self.assertEqual(res.status_code, 200)                                    # the bench still runs
            self.assertIn('illisibles', res.json()['reports']['error'])

        def test_invalid_input_is_refused_in_french(self):
            run_id = self._run()
            for body in ({'reason': 'nope'}, {'reason': 'other', 'expected_intent': 'shell.exec'},
                         {'reason': 'other', 'extra': 1}, {'reason': 'other', 'comment': 'x' * 1001}, {}):
                res = self.client.put(f'/api/runs/{run_id}/report', json=body)
                self.assertEqual(res.status_code, 422, body)
                self.assertIsInstance(res.json()['detail'], str)
            self.assertEqual(self.client.put('/api/runs/987654/report', json={'reason': 'other'}).status_code, 404)

        def test_cors_allows_put_and_delete_from_the_interface(self):
            for method in ('PUT', 'DELETE'):
                res = self.client.options('/api/runs/1/report', headers={
                    'Origin': ORIGIN, 'Access-Control-Request-Method': method,
                    'Access-Control-Request-Headers': 'x-aurora-token,content-type'})
                self.assertEqual(res.status_code, 200, method)
                self.assertIn(method, res.headers.get('access-control-allow-methods', ''))


if __name__ == '__main__':
    unittest.main()
