"""Import of Athéna's memories (memory/athena_import.py, 29/10/2026), on a FAKE Athéna base built here.

Preview first (nothing written), then the import: backup before, original dates kept, never twice, never merged
on a title, sensitive ones marked, the memories that describe Athéna herself kept aside.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aurora import db
from aurora.memory import athena_import, context, store
from aurora.tasks import events
from tests.doubles_core import TempEnv

SCHEMA = ('CREATE TABLE souvenirs (id INTEGER PRIMARY KEY, categorie TEXT, sujet TEXT, contenu TEXT, mots_cles TEXT, '
          "importance INTEGER DEFAULT 3, source TEXT DEFAULT 'conversation', cree_le TEXT, maj_le TEXT, "
          'nb_rappels INTEGER DEFAULT 0, UNIQUE (categorie, sujet))')
ROWS = [
    # id, categorie, sujet, contenu, mots_cles, importance, source, cree_le, maj_le
    (2, 'preference', 'Mode sombre', 'Je préfère le mode sombre partout.', 'sombre theme interface', 5,
     'conversation', '2026-09-03T10:00:00.000Z', '2026-10-15T08:00:00.000Z'),
    (5, 'materiel', 'Capteur DS18B20', 'Sonde de température numérique du projet.', 'sonde onewire', 4,
     'projet VITA (documentation avril 2026)', '2026-09-02T09:00:00.000Z', '2026-09-02T09:00:00.000Z'),
    (7, 'projet', 'Dossier VITA', 'Taille 2,3 Go, 410 fichiers.', 'dossier', 3, 'inventaire des projets',
     '2026-09-10T09:00:00.000Z', None),
    (9, 'projet', 'Athéna, ses capacités', 'Ce que sait faire Athéna.', 'capacites', 5, 'conversation',
     '2026-09-04T09:00:00.000Z', None),
    (11, 'personne', 'Qui est l’utilisateur', 'Créateur du projet VITA.', 'utilisateur', 5, 'conversation',
     '2026-09-05T09:00:00.000Z', None),
    (12, 'projet', 'Dépôt du brevet', 'Brevet déposé à l’INPI.', 'brevet inpi', 5,
     'abrege du brevet INPI, verifie le 6 sept', '2026-09-06T09:00:00.000Z', None),
    (14, 'probleme', 'Accès au routeur', 'Le mot de passe du routeur est noté ailleurs.', 'routeur', 4,
     'confirme par ID Studio IA le 10 septembre 2026', '2026-09-10T11:00:00.000Z', None),
    (15, 'decision', 'Choix du microcontrôleur', 'ESP32 retenu ; Athéna l’avait conseillé.', 'esp32', 4,
     'appris en conversation', '2026-09-11T09:00:00.000Z', None),
    (16, 'probleme', 'Pauses', 'Faire une pause pour la santé toutes les deux heures.', 'pause', 3, 'conversation',
     '2026-09-12T09:00:00.000Z', None),
]


def fake_athena(folder: Path) -> Path:
    path = folder / 'athena-memoire.db'
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany('INSERT INTO souvenirs(id, categorie, sujet, contenu, mots_cles, importance, source, cree_le, '
                    'maj_le) VALUES (?,?,?,?,?,?,?,?,?)', ROWS)
    con.commit()
    con.close()
    return path


class AthenaImportTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        events.bus.detach_loop()
        self.athena = fake_athena(self.env.dir)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def planned(self, **kw):
        rows = athena_import.read_rows(athena_import.copy_base(self.athena, self.env.dir / 'copie'))
        return athena_import.plan(rows, **kw)

    def by_id(self, items):
        return {c.athena_id: c for c in items}

    def test_the_preview_sorts_marks_and_writes_nothing(self):
        p = self.planned()
        self.assertEqual(p['read'], 9)
        self.assertEqual(sorted(self.by_id(p['excluded'])), [7, 9])   # folder inventory, Athéna herself
        todo = self.by_id(p['import'])
        self.assertEqual(sorted(todo), [2, 5, 11, 12, 14, 15, 16])
        self.assertEqual((todo[2].verification, todo[2].pinned, todo[2].kind), ('user_confirmed', True, 'preference'))
        self.assertEqual((todo[5].verification, todo[5].pinned, todo[5].kind), ('unverified', False, 'fact'))
        self.assertEqual(todo[11].confidentiality, 'sensitive')    # a person
        self.assertEqual(todo[12].confidentiality, 'sensitive')    # the patent
        self.assertEqual(todo[14].family, 'confirmation')
        self.assertEqual(todo[14].confidentiality, 'sensitive')    # « mot de passe »: the detector marks it
        self.assertTrue(todo[16].reread)                            # mentions health: to reread
        self.assertEqual(todo[16].confidentiality, 'private')
        self.assertTrue(any('Athéna' in r for r in todo[15].reread))
        self.assertEqual(todo[2].created_at, '2026-09-03T10:00:00.000Z')
        detail = todo[5].detail
        self.assertIn('Sonde de température numérique du projet.', detail)
        self.assertIn('Mots-clés : onewire', detail)                # only the words that are not in the text
        self.assertIn('Provenance (Athéna) : projet VITA (documentation avril 2026), enregistré le 02/09/2026', detail)
        text = athena_import.preview_markdown(p)
        self.assertIn('**Importés : 7**', text)
        self.assertIn('Rien n’est encore écrit', text)
        self.assertIn('« Athéna, ses capacités » : décrit Athéna elle-même', text)
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM memories')['n'], 0)
        self.assertEqual(sorted(self.by_id(self.planned(keep_about_athena=True)['import'])),
                         [2, 5, 9, 11, 12, 14, 15, 16])

    def test_the_import_keeps_dates_and_never_writes_twice(self):
        done = athena_import.apply(self.planned(), source_path=str(self.athena))
        self.assertEqual((done['imported'], done['sensitive'], done['excluded']), (7, 3, 2))
        rows = db.rows("SELECT * FROM memories WHERE source = 'athena' ORDER BY source_ref")
        self.assertEqual(len(rows), 7)
        first = next(r for r in rows if r['source_ref'] == 'athena:souvenir:2')
        self.assertEqual((first['created_at'], first['updated_at']), ('2026-09-03T10:00:00.000Z',
                                                                      '2026-10-15T08:00:00.000Z'))
        self.assertEqual(first['pinned'], 1)
        setting = db.get_setting(athena_import.SETTING)
        self.assertEqual((setting['status'], setting['imported']), ('done', 7))
        kinds = [e['kind'] for e in events.list_events(limit=50)]
        self.assertIn('system.imported', kinds)
        self.assertEqual(kinds.count('memory.saved'), 7)
        again = self.planned()
        self.assertEqual((len(again['import']), len(again['already'])), (0, 7))
        self.assertEqual(athena_import.apply(again)['imported'], 0)
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM memories WHERE source = 'athena'")['n'], 7)

    def test_never_merged_on_a_title(self):
        mine = store.add_memory('Mode sombre', 'Ma propre note, à garder telle quelle.', 'preference')
        athena_import.apply(self.planned())
        self.assertEqual(store.get_memory(mine['id'])['detail'], 'Ma propre note, à garder telle quelle.')
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM memories WHERE title = 'Mode sombre'")['n'], 2)

    def test_removed_memories_never_come_back_and_mine_are_refused(self):
        # 29/10/2026: the user asked to delete the device sheets and the early-September project states.
        athena_import.apply(self.planned())
        mine = store.add_memory('Ma note à moi', 'À garder.', 'general')
        ids = {r['source_ref']: r['id'] for r in db.rows("SELECT id, source_ref FROM memories WHERE source = 'athena'")}
        done = athena_import.remove([ids['athena:souvenir:5'], ids['athena:souvenir:14'], mine['id']])
        self.assertEqual([x['title'] for x in done['removed']], ['Capteur DS18B20', 'Accès au routeur'])
        self.assertEqual(done['refused'], [mine['id']])
        self.assertIsNotNone(store.get_memory(mine['id']))
        again = self.planned()
        self.assertEqual(sorted(c.athena_id for c in again['excluded'] if c.excluded.startswith('retiré')), [5, 14])
        self.assertEqual(again['import'], [])
        athena_import.apply(self.planned())   # a new import keeps the list of what was removed
        self.assertEqual(sorted(db.get_setting(athena_import.SETTING)['removed_refs']),
                         ['athena:souvenir:14', 'athena:souvenir:5'])
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM memories WHERE source = 'athena'")['n'], 5)

    def test_the_removal_needs_a_backup(self):
        athena_import.apply(self.planned())
        one = db.row("SELECT id FROM memories WHERE source_ref = 'athena:souvenir:5'")['id']
        self.assertEqual(athena_import.main(['retirer', '--ids', str(one)]), 2)
        self.assertIsNotNone(store.get_memory(one))
        saves = self.env.dir / 'sauvegardes'
        self.assertEqual(athena_import.main(['retirer', '--ids', str(one), '--sauvegarde', str(saves)]), 0)
        self.assertIsNone(store.get_memory(one))
        copies = list(saves.glob('aurora-avant-retrait-souvenirs-*.db'))
        self.assertEqual(len(copies), 1)
        with sqlite3.connect(copies[0]) as check:   # the copy holds the memory as it was before the removal
            self.assertEqual(check.execute('SELECT COUNT(*) FROM memories WHERE id = ?', (one,)).fetchone()[0], 1)

    def test_athena_is_read_from_a_copy_only(self):
        before = os.stat(self.athena).st_mtime_ns
        copy = athena_import.copy_base(self.athena, self.env.dir / 'copie2')
        self.assertNotEqual(copy, self.athena)
        self.assertEqual(len(athena_import.read_rows(copy)), 9)
        self.assertEqual(os.stat(self.athena).st_mtime_ns, before)
        with self.assertRaises(FileNotFoundError):
            athena_import.copy_base(self.env.dir / 'absente.db', self.env.dir / 'copie3')

    def test_the_command_refuses_to_write_without_a_backup(self):
        self.assertEqual(athena_import.main(['importer', '--athena', str(self.athena)]), 2)
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM memories')['n'], 0)
        out = self.env.dir / 'apercu.md'
        self.assertEqual(athena_import.main(['apercu', '--athena', str(self.athena), '--sortie', str(out)]), 0)
        self.assertIn('Aperçu de l’import', out.read_text(encoding='utf-8'))
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM memories')['n'], 0)
        saves = self.env.dir / 'sauvegardes'
        self.assertEqual(athena_import.main(['importer', '--athena', str(self.athena), '--sauvegarde', str(saves)]), 0)
        copies = list(saves.glob('aurora-avant-import-athena-*.db'))
        self.assertEqual(len(copies), 1)
        with sqlite3.connect(copies[0]) as check:   # the copy is the base as it was before the import
            self.assertEqual(check.execute("SELECT COUNT(*) FROM memories WHERE source = 'athena'").fetchone()[0], 0)
        self.assertEqual(db.row("SELECT COUNT(*) AS n FROM memories WHERE source = 'athena'")['n'], 7)


EXCHANGES = [
    # id, conversation_id, role, message, cree_le
    (1, 'conv-1', 'user', 'parle-moi des modules Peltier', '2026-09-10T08:00:00.000Z'),
    (2, 'conv-1', 'assistant', 'Un module Peltier refroidit une face et chauffe l’autre.', '2026-09-10T08:00:05.000Z'),
    (3, 'conv-2', 'user', 'et leur rendement ?', '2026-09-10T08:10:00.000Z'),          # 10 min later: same session
    (4, 'conv-2', 'assistant', 'Faible, autour de 10 %.', '2026-09-10T08:10:05.000Z'),
    (5, 'banc-x', 'user', 'parle-moi des modules Peltier', '2026-09-10T08:20:00.000Z'),  # a bench: never imported
    (6, 'banc-x', 'assistant', 'Réponse de banc.', '2026-09-10T08:20:05.000Z'),
    (7, 'evaluation', 'user', 'question de test', '2026-09-10T08:30:00.000Z'),
    (8, 'conv-3', 'user', 'le code du portail est 4512, retiens-le', '2026-09-11T19:00:00.000Z'),   # next day
    (9, 'conv-3', 'assistant', 'D’accord, c’est retenu pour la suite.', '2026-09-11T19:00:04.000Z'),
    (10, 'conv-3', 'user', '   ', '2026-09-11T19:01:00.000Z'),                                # empty: skipped
    # Poor answers (reviews/athena-qualite-echanges.md): set aside with their question.
    (11, 'conv-4', 'user', 'quel temps fait-il demain ?', '2026-09-12T10:00:00.000Z'),
    (12, 'conv-4', 'assistant', 'Je n’ai pas accès à internet, je ne peux pas le savoir.', '2026-09-12T10:00:05.000Z'),
    (13, 'conv-4', 'user', 'qui es-tu ?', '2026-09-12T10:01:00.000Z'),
    (14, 'conv-4', 'assistant', 'Je suis Athéna, une IA hybride qui fonctionne localement.', '2026-09-12T10:01:05.000Z'),
    (15, 'conv-4', 'user', 'combien font 12 x 8', '2026-09-12T10:02:00.000Z'),
    (16, 'conv-4', 'assistant', '12 x 8 font 86, je pense.', '2026-09-12T10:02:05.000Z'),
]


def add_exchanges(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE echanges (id INTEGER PRIMARY KEY, conversation_id TEXT, role TEXT, message TEXT, '
                'cree_le TEXT)')
    con.executemany('INSERT INTO echanges VALUES (?,?,?,?,?)', EXCHANGES)
    con.execute('CREATE TABLE signalements (id INTEGER PRIMARY KEY, conversation_id TEXT, question TEXT, reponse TEXT, '
                'verdict TEXT)')
    con.execute("INSERT INTO signalements VALUES (1, 'conv-4', 'combien font 12 x 8', '12 x 8 font 86, je pense.', "
                "'faute')")
    con.commit()
    con.close()


class AthenaExchangesTests(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv().__enter__()
        events.bus.detach_loop()
        self.athena = fake_athena(self.env.dir)
        add_exchanges(self.athena)

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def planned(self):
        copy = athena_import.copy_base(self.athena, self.env.dir / 'copie')
        return athena_import.plan_exchanges(athena_import.read_exchanges(copy), athena_import.read_reports(copy))

    def test_the_benches_are_set_aside_and_the_sessions_grouped(self):
        p = self.planned()
        self.assertEqual((p['read'], p['bench'], p['messages'], len(p['sessions'])), (16, 3, 6, 2))
        self.assertEqual([(x['id'], x['why']) for x in p['poor']],
                         [(11, 'nie avoir Internet (faux : Athéna l’avait)'), (13, 'parle d’Athéna elle-même'),
                          (15, 'faute confirmée par Athéna (signalement)')])   # conv-4 is not imported at all
        first, second = p['sessions']
        self.assertEqual([m['id'] for m in first['messages']], [1, 2, 3, 4])   # conv-1 and conv-2: one session
        self.assertEqual(first['title'], '[Athéna] parle-moi des modules Peltier')
        self.assertEqual((first['first'], first['last'], first['questions']),
                         ('2026-09-10T08:00:00.000Z', '2026-09-10T08:10:05.000Z', 2))
        self.assertEqual(p['sensitive'], 1)                                     # « le code du portail est 4512 »
        text = athena_import.preview_exchanges_markdown(p)
        self.assertIn('**À importer : 6 messages (3 questions) en 2 conversation(s)**', text)
        self.assertIn('Écartés (bancs de test) : 3', text)
        self.assertIn('Écartés pour la qualité : 3 question(s) et leur réponse', text)
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM messages')['n'], 0)

    def test_the_import_keeps_dates_marks_and_never_writes_twice(self):
        done = athena_import.apply_exchanges(self.planned())
        self.assertEqual((done['conversations'], done['messages'], done['sensitive']), (2, 6, 1))
        conv = db.row("SELECT * FROM conversations WHERE title LIKE '[Athéna] parle-moi%'")
        self.assertEqual((conv['created_at'], conv['updated_at']), ('2026-09-10T08:00:00.000Z', '2026-09-10T08:10:05.000Z'))
        rows = db.rows('SELECT role, created_at, meta_json FROM messages WHERE conversation_id = ? ORDER BY id', (conv['id'],))
        self.assertEqual(rows[0]['created_at'], '2026-09-10T08:00:00.000Z')
        meta = json.loads(rows[0]['meta_json'])
        self.assertEqual((meta['imported_from'], meta['athena_id'], meta['athena_conversation']), ('athena', 1, 'conv-1'))
        secret = db.row("SELECT meta_json FROM messages WHERE content LIKE 'le code du portail%'")
        self.assertTrue(json.loads(secret['meta_json'])['sensitive'])
        again = self.planned()
        self.assertEqual((again['messages'], again['already']), (0, 6))
        self.assertEqual(athena_import.apply_exchanges(again)['messages'], 0)
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM messages')['n'], 6)
        self.assertEqual(db.get_setting(athena_import.EXCHANGES_SETTING)['status'], 'done')

    def test_an_interrupted_conversation_is_completed_not_duplicated(self):
        conv = store.create_conversation('[Athéna] parle-moi des modules Peltier', created_at='2026-09-10T08:00:00.000Z')
        store.add_message(conv['id'], 'user', 'parle-moi des modules Peltier',
                          meta={'imported_from': 'athena', 'athena_id': 1, 'athena_conversation': 'conv-1'},
                          created_at='2026-09-10T08:00:00.000Z')
        done = athena_import.apply_exchanges(self.planned())
        self.assertEqual((done['conversations'], done['messages']), (1, 5))   # only conv-3 is a new conversation
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?', (conv['id'],))['n'], 4)

    def test_the_command_needs_a_backup_and_the_recall_names_athena(self):
        self.assertEqual(athena_import.main(['importer-echanges', '--athena', str(self.athena)]), 2)
        self.assertEqual(db.row('SELECT COUNT(*) AS n FROM messages')['n'], 0)
        saves = self.env.dir / 'sauvegardes'
        self.assertEqual(athena_import.main(['importer-echanges', '--athena', str(self.athena),
                                             '--sauvegarde', str(saves)]), 0)
        self.assertEqual(len(list(saves.glob('aurora-avant-import-athena-echanges-*.db'))), 1)
        from aurora.memory import recall
        found = recall.recall_for('on avait parlé des modules Peltier', conversation_id=None, memories=[])
        self.assertIn('Athéna avait répondu (réponse d’Athéna, non vérifiée par AURORA)', recall.answer(found))


class HelpersTests(unittest.TestCase):
    def test_families_and_labels(self):
        self.assertEqual(athena_import.family_of('inventaire des projets'), 'inventaire')
        self.assertEqual(athena_import.family_of('Restauration audit 13 septembre : lien confirmé'), 'confirmation')
        self.assertEqual(athena_import.family_of('thermodynamique - bilan energetique'), 'note')
        self.assertEqual(context.source_label('athena'), 'import Athéna')
        self.assertTrue(context._is_external({'source': 'athena'}))   # data, never instructions

    def test_dates_and_backup_reasons_are_checked(self):
        with TempEnv():
            with self.assertRaises(ValueError):
                store.add_memory('X', created_at='hier')
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                db.daily_backup(folder, reason='../ailleurs')


if __name__ == '__main__':
    unittest.main()
