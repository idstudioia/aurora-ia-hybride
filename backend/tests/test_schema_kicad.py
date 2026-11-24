"""Le schéma de câblage KiCad du quadrupède (docs/robot-quadrupede/schema_kicad.py) : ce que le dessin relie, ce qu'il
refuse, et ce qu'il écrit. Aucun moteur n'est lancé ici : KiCad contrôle le fichier ailleurs (tools/moteurs_cao.py)."""
import tempfile
import unittest
from pathlib import Path

from aurora.tools import projets

DOSSIER = Path(__file__).resolve().parents[2] / 'docs' / 'robot-quadrupede'
if not (DOSSIER / 'schema_kicad.py').is_file():       # 23/11/2026 : le projet de robot n'est pas livré dans le kit
    raise unittest.SkipTest('le projet de robot d’ID Studio IA n’est pas livré dans cette copie d’AURORA')
sk = projets._calc_module(DOSSIER / 'schema_kicad.py')         # chargé comme AURORA charge un programme de projet
SCHEMA, SYMBOLES, PROJET, TABLE = (f'{sk.NOM}.kicad_sch', f'{sk.NOM}.kicad_sym', f'{sk.NOM}.kicad_pro', 'sym-lib-table')


def solde_des_parentheses(texte):
    """Parenthèses ouvertes moins parenthèses fermées, hors des textes entre guillemets : 0 pour un fichier bien formé."""
    solde, entre_guillemets, echappe = 0, False, False
    for c in texte:
        if echappe:
            echappe = False
        elif c == '\\':
            echappe = entre_guillemets
        elif c == '"':
            entre_guillemets = not entre_guillemets
        elif not entre_guillemets:
            solde += (c == '(') - (c == ')')
    return solde


class DrawingTests(unittest.TestCase):
    def setUp(self):
        self.feuille = sk.construire()
        self.reseaux = self.feuille.reseaux()

    def test_the_wires_are_those_of_the_file_of_principle(self):
        r = self.reseaux
        self.assertEqual(len(self.feuille.pieces), 26)                       # 14 pièces et 12 servomoteurs
        self.assertEqual(set(r['BUS_SERVOS']), {'A2.S'} | {f'M{n}.S' for n in range(1, 13)})
        self.assertEqual(r['+5V'], ['A1.5V', 'A4.5V', 'PS1.VOUT'])           # la carte de commande et la caméra
        self.assertEqual(r['+3V3'], ['A1.3V3', 'A3.VIN'])
        self.assertEqual(len(r['GND']), 26)                                  # chaque pièce alimentée a sa masse
        self.assertEqual(sum(len(bornes) > 1 for bornes in r.values()), 21)

    def test_the_battery_reaches_the_robot_only_through_its_protection(self):
        """ID Studio IA, 15/11/2026 : « Oui, ajoute-la » (la carte de protection 3S). Le plus et le moins de la batterie ne
        vont qu'à la protection ; la masse du robot part de son plot P-, sinon la protection serait contournée."""
        r = self.reseaux
        self.assertEqual((r['(BMS1.BP)'], r['(BMS1.BN)']), (['BMS1.BP', 'BT1.1'], ['BMS1.BN', 'BT1.2']))
        self.assertEqual(r['(BMS1.PP)'], ['BMS1.PP', 'SW1.1'])               # puis l'interrupteur, le fusible, le bornier
        self.assertIn('BMS1.PN', r['GND'])
        self.assertNotIn('BT1.2', r['GND'])
        self.assertEqual((r['(A5.1)'], r['(A5.2)']), (['A5.1', 'BMS1.B1', 'BT1.B1'], ['A5.2', 'BMS1.B2', 'BT1.B2']))

    def test_the_emergency_stop_cuts_the_motors_and_nothing_else(self):
        """ID Studio IA, 15/11/2026 : « Oui, ajoute-le ». Le 12 V des moteurs n'arrive que par le contact du relais, et la
        bobine du relais que par le bouton ; le régulateur et la carte du bus sont avant le relais."""
        r = self.reseaux
        self.assertEqual(set(r['+12V']), {'F1.2', 'J1.P', 'PS1.VIN', 'A2.VIN', 'K1.30', 'SW2.C'})
        self.assertEqual(set(r['+12V_MOTEURS']), {'K1.87', 'SW2.LA'} | {f'M{n}.V' for n in range(1, 13)})   # et le voyant du bouton
        self.assertEqual(r['(D1.K)'], ['D1.K', 'K1.86', 'SW2.NC'])           # la bobine, par le contact à ouverture du bouton
        self.assertTrue({'D1.A', 'K1.85', 'SW2.LK'} <= set(r['GND']))
        self.assertIn(('SW2', 'NO'), self.feuille.libres)                    # son contact à fermeture ne sert pas
        self.assertEqual(len(self.feuille.drapeaux), 2)                      # un par tension qui arrive par un contact

    def test_the_brain_is_the_esp32_s3_of_the_concept(self):
        """ID Studio IA, 15/11/2026 : « Passer à l'ESP32-S3 ». Deux liaisons série, chacune d'une sortie vers une entrée."""
        r, pieces = self.reseaux, self.feuille.pieces
        self.assertEqual((pieces['A1'][0], pieces['A4'][0]), ('ESP32_S3_DevKitC_1', 'Realtek_AMB82_Mini'))
        self.assertEqual((r['ESP_TX_BUS'], r['ESP_RX_BUS']), (['A1.17', 'A2.TX'], ['A1.18', 'A2.RX']))
        self.assertEqual((r['ESP_TX_CAM'], r['ESP_RX_CAM']), (['A1.5', 'A4.22'], ['A1.4', 'A4.21']))
        genres = {f'{repere}.{numero}': genre for repere, piece in pieces.items() for numero, _nom, genre, *_ in sk.SYMBOLES[piece[0]][3]}
        for fil in ('ESP_TX_BUS', 'ESP_RX_BUS', 'ESP_TX_CAM', 'ESP_RX_CAM'):
            self.assertEqual(sorted(genres[borne] for borne in r[fil]), ['input', 'output'], fil)
        self.assertFalse(any('Raspberry' in piece[3] or 'USB' in nom for piece in pieces.values() for nom in r))

    def test_the_current_of_the_motors_does_not_cross_the_bus_card(self):
        self.assertIn(('A2', 'V'), self.feuille.libres)
        self.assertEqual(self.reseaux['(A2.V)'], ['A2.V'])                   # seule sur son réseau : rien n'y est branché

    def test_the_motors_are_numbered_as_on_the_bus(self):
        valeurs = {repere: piece[3] for repere, piece in self.feuille.pieces.items()}
        self.assertEqual((valeurs['M1'], valeurs['M2'], valeurs['M3']), ('n° 1 · épaule', 'n° 2 · hanche', 'n° 3 · genou'))
        self.assertEqual(valeurs['M12'], 'n° 12 · genou')

    def test_a_loose_wire_or_a_forgotten_terminal_is_refused(self):
        en_l_air = sk.construire()
        en_l_air.fil('donnees', (10, 10), (12, 10))                          # un fil qui ne va nulle part
        sans_fil = sk.construire()
        sans_fil.fils = [fil for fil in sans_fil.fils if fil[1] != sans_fil.borne('A1', '8')]     # la borne SDA oubliée
        libre_et_reliee = sk.construire()
        libre_et_reliee.libre('M1', 'V')
        au_milieu = sk.construire()
        au_milieu.fil('batterie', (62, 20), (62, 18))                        # un bout posé au milieu du fil du 12 V
        for feuille in (en_l_air, sans_fil, libre_et_reliee, au_milieu):
            with self.assertRaises(AssertionError):
                feuille.schema()


class FilesTests(unittest.TestCase):
    def test_two_runs_write_the_same_well_formed_files(self):
        textes = sk.fichiers(sk.construire())
        self.assertEqual(textes, sk.fichiers(sk.construire()))
        self.assertEqual(sorted(textes), sorted([SCHEMA, SYMBOLES, PROJET, TABLE]))
        for nom in (SCHEMA, SYMBOLES, TABLE):
            self.assertEqual(solde_des_parentheses(textes[nom]), 0, nom)
        self.assertEqual(textes[SCHEMA].count(f'(symbol (lib_id "{sk.NOM}:Servomoteur_STS3250")'), 12)
        self.assertEqual(textes[SCHEMA].count('(no_connect '), len(sk.construire().libres))
        for symbole in sk.SYMBOLES:                                          # chaque pièce posée a son symbole dans la bibliothèque
            self.assertIn(f'(symbol "{symbole}"', textes[SYMBOLES])
            self.assertIn(f'(symbol "{sk.NOM}:{symbole}"', textes[SCHEMA])
        self.assertIn(f'{sk.NOM}.kicad_sym', textes[TABLE])

    def test_a_file_retouched_in_kicad_is_not_overwritten(self):
        textes = sk.fichiers(sk.construire())
        with tempfile.TemporaryDirectory() as dossier:
            ecrits, inchanges = sk.ranger(dossier, textes)
            self.assertEqual((sorted(ecrits), inchanges), (sorted(textes), []))
            self.assertEqual(sk.ranger(dossier, textes), ([], list(textes)))             # un second lancement ne réécrit rien
            schema, projet = Path(dossier) / SCHEMA, Path(dossier) / PROJET
            schema.write_text(textes[SCHEMA] + '(retouche)\n', encoding='utf-8', newline='\n')
            projet.write_text('{"meta": {}, "board": {}}', encoding='utf-8')            # KiCad y a rangé ses réglages
            with self.assertRaises(FileExistsError):
                sk.ranger(dossier, textes)
            self.assertTrue(schema.read_text(encoding='utf-8').endswith('(retouche)\n'))  # rien n'a été touché
            self.assertEqual(sk.ranger(dossier, textes, remplacer=True)[0], [SCHEMA])
            self.assertEqual(schema.read_text(encoding='utf-8'), textes[SCHEMA])
            self.assertEqual(projet.read_text(encoding='utf-8'), '{"meta": {}, "board": {}}')   # le projet reste à KiCad
