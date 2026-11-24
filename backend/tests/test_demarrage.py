"""Démarrer AURORA avec Windows (tools/demarrage.py, E3). Jamais le vrai dossier « Démarrage » de Windows : chaque
essai travaille dans un dossier provisoire, donné par AURORA_STARTUP_DIR."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aurora.tools import demarrage
from tests.test_api import NO_TOKEN, ApiTestCase


class Essai(unittest.TestCase):
    def setUp(self):
        self.dossier = Path(tempfile.mkdtemp())
        remplace = mock.patch.dict(os.environ, {'AURORA_STARTUP_DIR': str(self.dossier)})
        remplace.start()
        self.addCleanup(remplace.stop)
        self.fichier = self.dossier / demarrage.FICHIER


class StateTests(Essai):
    def test_nothing_is_set_at_first(self):
        vue = demarrage.etat()
        self.assertEqual((vue['disponible'], vue['actif'], vue['fenetre'], vue['autre'], vue['pourquoi']), (True, False, False, False, ''))
        self.assertEqual(vue['fichier'], str(self.fichier))
        self.assertTrue(vue['lance'].endswith('start-aurora.ps1') and Path(vue['lance']).is_file())
        self.assertEqual(list(self.dossier.iterdir()), [])          # reading writes nothing

    def test_set_without_the_window_then_read_back_from_the_disk(self):
        vue = demarrage.regler(True)
        self.assertEqual((vue['actif'], vue['fenetre'], vue['cette_copie']), (True, False, True))
        octets = self.fichier.read_bytes()
        texte = octets.decode('utf-8')
        self.assertTrue(texte.startswith('@echo off\r\n'))
        self.assertNotIn(b'\n', octets.replace(b'\r\n', b''))        # every line ends the Windows way
        self.assertIn(f'-File "{demarrage.racine() / "start-aurora.ps1"}" -Hidden -SansFenetre', texte)
        self.assertEqual([p.name for p in self.dossier.iterdir()], [demarrage.FICHIER])   # no leftover

    def test_set_with_the_window(self):
        vue = demarrage.regler(True, True)
        self.assertEqual((vue['actif'], vue['fenetre']), (True, True))
        texte = self.fichier.read_text(encoding='utf-8')
        self.assertIn('" -Hidden', texte)
        self.assertNotIn('-SansFenetre', texte)

    def test_change_then_remove(self):
        demarrage.regler(True, True)
        self.assertFalse(demarrage.regler(True, False)['fenetre'])
        vue = demarrage.regler(False)
        self.assertEqual((vue['actif'], vue['disponible']), (False, True))
        self.assertFalse(self.fichier.exists())
        self.assertFalse(demarrage.regler(False)['actif'])          # removing twice is quiet

    def test_a_file_aurora_did_not_write_is_left_alone(self):
        self.fichier.write_bytes(b'@echo off\r\necho autre chose\r\n')
        vue = demarrage.etat()
        self.assertEqual((vue['actif'], vue['autre']), (False, True))
        self.assertIn('laissé tel quel', vue['pourquoi'])
        for actif in (True, False):
            with self.assertRaises(ValueError):
                demarrage.regler(actif)
        self.assertEqual(self.fichier.read_bytes(), b'@echo off\r\necho autre chose\r\n')

    def test_a_file_of_another_copy_is_told_then_replaced(self):
        demarrage.regler(True)
        ailleurs = self.fichier.read_text(encoding='utf-8').replace(str(demarrage.racine()), r'D:\ancienne\aurora')
        self.fichier.write_bytes(ailleurs.encode('utf-8'))
        vue = demarrage.etat()
        self.assertEqual((vue['actif'], vue['cette_copie']), (True, False))
        self.assertTrue(demarrage.regler(True)['cette_copie'])       # it is AURORA's own file: this copy takes it back

    def test_without_its_folder_nothing_is_possible(self):
        with mock.patch.dict(os.environ, {'AURORA_STARTUP_DIR': str(self.dossier / 'absent')}):
            vue = demarrage.etat()
            self.assertEqual((vue['disponible'], vue['actif']), (False, False))
            self.assertIn('introuvable', vue['pourquoi'])
            with self.assertRaises(ValueError):
                demarrage.regler(True)
        self.assertFalse((self.dossier / 'absent').exists())

    def test_only_under_windows(self):
        sans = {k: v for k, v in os.environ.items() if k != 'AURORA_STARTUP_DIR'}
        with mock.patch.dict(os.environ, sans, clear=True), mock.patch.object(demarrage.sys, 'platform', 'linux'):
            vue = demarrage.etat()
            self.assertEqual((vue['disponible'], vue['fichier']), (False, ''))
            self.assertIn('Windows', vue['pourquoi'])
            with self.assertRaises(ValueError):
                demarrage.regler(True)

    def test_the_real_folder_is_the_startup_folder_of_the_session(self):
        vrai = {k: v for k, v in os.environ.items() if k != 'AURORA_STARTUP_DIR'} | {'APPDATA': r'C:\Users\quelquun\AppData\Roaming'}
        with mock.patch.dict(os.environ, vrai, clear=True), mock.patch.object(demarrage.sys, 'platform', 'win32'):
            self.assertEqual(demarrage.dossier(), Path(r'C:\Users\quelquun\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'))

    def test_a_path_a_command_file_cannot_hold_is_refused(self):
        copie = Path(tempfile.mkdtemp()) / 'aurora & co'
        copie.mkdir()
        (copie / demarrage.LANCEUR).write_text('', encoding='utf-8')
        with mock.patch.object(demarrage, 'racine', return_value=copie):
            with self.assertRaises(ValueError) as refus:
                demarrage.regler(True)
        self.assertIn('caractère', str(refus.exception))
        self.assertEqual(list(self.dossier.iterdir()), [])


class RouteTests(ApiTestCase):
    def setUp(self):
        # Avant super().setUp() : ce remplacement se défait après celui d'ApiTestCase (les nettoyages passent après
        # tearDown). Dans l'autre ordre, il remettrait l'environnement provisoire d'ApiTestCase (hors ligne, base
        # d'essai) pour tous les essais suivants.
        self.dossier = Path(tempfile.mkdtemp())
        remplace = mock.patch.dict(os.environ, {'AURORA_STARTUP_DIR': str(self.dossier)})
        remplace.start()
        self.addCleanup(remplace.stop)
        super().setUp()

    def test_read_set_and_remove(self):
        self.assertEqual(self.client.get('/api/demarrage', headers=NO_TOKEN).status_code, 401)
        self.assertEqual(self.client.put('/api/demarrage', json={'actif': True}, headers=NO_TOKEN).status_code, 401)
        vue = self.client.get('/api/demarrage').json()
        self.assertEqual((vue['disponible'], vue['actif']), (True, False))
        res = self.client.put('/api/demarrage', json={'actif': True, 'fenetre': True})
        self.assertEqual((res.status_code, res.json()['actif'], res.json()['fenetre']), (200, True, True))
        self.assertTrue((self.dossier / demarrage.FICHIER).is_file())
        self.assertTrue(self.client.get('/api/demarrage').json()['actif'])
        res = self.client.put('/api/demarrage', json={'actif': False})
        self.assertEqual((res.status_code, res.json()['actif']), (200, False))
        self.assertEqual(list(self.dossier.iterdir()), [])

    def test_only_the_two_known_fields(self):
        self.assertEqual(self.client.put('/api/demarrage', json={'actif': True, 'commande': 'calc.exe'}).status_code, 422)
        self.assertEqual(self.client.put('/api/demarrage', json={'fenetre': True}).status_code, 422)
        self.assertEqual(list(self.dossier.iterdir()), [])

    def test_a_refusal_says_why_and_changes_nothing(self):
        (self.dossier / demarrage.FICHIER).write_bytes(b'@echo off\r\n')
        res = self.client.put('/api/demarrage', json={'actif': True})
        self.assertEqual(res.status_code, 409)
        self.assertIn('laissé tel quel', res.json()['detail'])
        self.assertEqual((self.dossier / demarrage.FICHIER).read_bytes(), b'@echo off\r\n')


if __name__ == '__main__':
    unittest.main()
