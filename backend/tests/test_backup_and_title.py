"""H2 and H4 of reviews/RAPPORT-AURORA.md (28/10/2026).

H2 — the day's copy of the database: one per day at most, checked, never an overwrite nor a deletion of an
older copy (CLAUDE.md), in the folder chosen in Paramètres › Données.
H4 — a conversation title never shows a secret typed in the first message: masked, then cut.
Temporary AURORA_HOME (tests.doubles_core.TempEnv); the real base is never touched.
"""
import asyncio
import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from aurora import config, db, main
from aurora.memory import store
from tests.doubles_core import FakeDecision, TempEnv, build
from tests.test_review_fixes import CoreCase


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


class DailyBackupTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        db.initialize()
        store.create_conversation('Première conversation')
        self.folder = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_one_checked_copy_per_day_and_older_ones_kept(self):
        monday, tuesday = datetime(2026, 10, 26, 9, 0), datetime(2026, 10, 27, 9, 0)
        first = db.daily_backup(self.folder, now=monday)
        self.assertIsNotNone(first)
        with closing(sqlite3.connect(str(first))) as copy:
            self.assertEqual(copy.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(copy.execute('SELECT title FROM conversations').fetchone()[0], 'Première conversation')
        stamp = first.stat().st_mtime_ns
        self.assertIsNone(db.daily_backup(self.folder, now=monday.replace(hour=18)))   # same day: nothing
        second = db.daily_backup(self.folder, now=tuesday)
        self.assertNotEqual(first, second)
        self.assertEqual(first.stat().st_mtime_ns, stamp)                              # never overwritten
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ['aurora-auto-20261026-090000.db', 'aurora-auto-20261027-090000.db'])

    def test_default_folder_is_next_to_the_base(self):
        copy = db.daily_backup()
        self.assertEqual(copy.parent, db.db_path().parent / 'backups')

    def test_the_folder_setting_must_exist(self):
        with self.assertRaises(ValueError):
            config.update_settings({'backup_dir': str(self.folder / 'absent')})
        with self.assertRaises(ValueError):
            config.update_settings({'backup_dir': 'relatif\\dossier'})
        self.assertEqual(config.update_settings({'backup_dir': str(self.folder)})['backup_dir'], str(self.folder.resolve()))
        self.assertEqual(config.update_settings({'backup_dir': ''})['backup_dir'], '')

    def test_the_service_loop_makes_the_copy_in_the_chosen_folder(self):
        config.update_settings({'backup_dir': str(self.folder)})

        async def one_round():
            task = asyncio.create_task(main._backup_loop(3600, 0))
            for _ in range(200):
                await asyncio.sleep(0.02)
                if any(self.folder.iterdir()):
                    break
            task.cancel()
        asyncio.run(one_round())
        self.assertEqual(len(list(self.folder.glob('aurora-auto-*.db'))), 1)


class TitleTests(CoreCase):
    def test_a_secret_never_reaches_the_title(self):
        for text in ('Retiens que mon mot de passe Wi-Fi est Soleil2024!',
                     'Retiens que mon mot de passe est ' + 'x' * 70 + 'Soleil2024!'):   # cut after masking
            with self.subTest(text[:30]):
                title = store.create_conversation(text, size=80)['title']
                self.assertNotIn('Soleil2024', title)
                self.assertLessEqual(len(title), 81)
        self.assertEqual(store.create_conversation('Quelle heure est-il ?')['title'], 'Quelle heure est-il ?')

    async def test_the_first_message_of_a_real_request(self):
        orch, *_ = build(FakeDecision('chat.general'))
        result = await orch.handle_request('Retiens que mon mot de passe Wi-Fi est Soleil2024!')
        title = store.get_conversation(result['conversation_id'])['title']
        self.assertIn('[MOT_DE_PASSE]', title)
        self.assertNotIn('Soleil2024', title)


if __name__ == '__main__':
    unittest.main()
