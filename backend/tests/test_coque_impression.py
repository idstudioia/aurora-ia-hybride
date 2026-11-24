"""Rendre imprimable un morceau de coque (docs/robot-quadrupede/coque_impression.py) : souder, rendre les miettes,
recouper pour le plateau, donner une épaisseur, contrôler que le solide est fermé, écrire le STL.
Des formes simples dont on connaît la réponse ; aucun modèle d’ID Studio IA n'est lu ici."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from aurora.tools import projets

DOSSIER = Path(__file__).resolve().parents[2] / 'docs' / 'robot-quadrupede'
if not (DOSSIER / 'coque_impression.py').is_file():   # 23/11/2026 : le projet de robot n'est pas livré dans le kit
    raise unittest.SkipTest('le projet de robot d’ID Studio IA n’est pas livré dans cette copie d’AURORA')
ci = projets._calc_module(DOSSIER / 'coque_impression.py')     # chargé comme AURORA charge un programme de projet
glb = projets._calc_module(DOSSIER / 'glb_outils.py')          # sa boîte (« pave ») sert de forme d'essai : faces dehors, sommets dédoublés
PLATEAU = (220.0, 220.0, 250.0)


def grille(nx, ny, pas, origine=(0.0, 0.0, 0.0)):
    """Une plaque à plat (z constant), faces vers le haut : (sommets, triangles)."""
    xs, ys = np.meshgrid(np.arange(nx + 1) * pas, np.arange(ny + 1) * pas, indexing='ij')
    sommets = np.c_[xs.ravel(), ys.ravel(), np.zeros(xs.size)] + np.array(origine)
    i, j = np.meshgrid(np.arange(nx), np.arange(ny), indexing='ij')
    a = (i * (ny + 1) + j).ravel()
    b, c, d = a + ny + 1, a + ny + 2, a + 1
    return sommets, np.concatenate([np.c_[a, b, c], np.c_[a, c, d]])


def aire(sommets, triangles):
    a, b, c = (sommets[triangles[:, k]] for k in range(3))
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())


class ThicknessTests(unittest.TestCase):
    def test_an_open_box_becomes_a_closed_solid_with_walls(self):
        points, _normales, triangles = glb.pave(40, 40, 40, centre=(20, 20, 20))
        triangles = triangles[~(points[triangles][:, :, 2] == 40).all(axis=1)]      # sans son dessus
        sommets, renvoi = ci.souder(points)
        self.assertEqual(len(sommets), 8)                       # les coutures sont soudées
        t = ci.sans_plats(renvoi[triangles])
        solide, faces = ci.epaissir(sommets, t, 2.0)
        bilan = ci.bilan_solide(solide, faces)
        self.assertEqual((bilan['aretes_ouvertes'], bilan['aretes_multiples']), (0, 0))
        self.assertTrue(np.allclose(solide[:8], sommets))       # la peau du dehors n'a pas bougé
        self.assertGreater(bilan['volume_mm3'], 0.3 * aire(sommets, t) * 2.0)
        self.assertLess(bilan['volume_mm3'], aire(sommets, t) * 2.0)

    def test_a_plate_thinner_than_two_walls_is_not_crossed(self):
        points, _normales, triangles = glb.pave(40, 40, 1, centre=(20, 20, .5))     # fermée, 1 mm d'épais
        sommets, renvoi = ci.souder(points)
        solide, faces = ci.epaissir(sommets, ci.sans_plats(renvoi[triangles]), 2.0)
        bilan = ci.bilan_solide(solide, faces)
        self.assertEqual(bilan['aretes_ouvertes'], 0)
        self.assertGreater(bilan['volume_mm3'], 0.6 * 1600)     # presque pleine : la paroi s'arrête avant celle d'en face
        self.assertLess(bilan['volume_mm3'], 1600)
        dessus = solide[len(sommets):][sommets[:, 2] == 1]
        self.assertTrue((dessus[:, 2] > 0.5).all())              # la paroi du dessus ne descend pas sous le milieu

    def test_the_stl_has_the_size_its_triangles_give(self):
        sommets, triangles = grille(2, 2, 10)
        solide, faces = ci.epaissir(sommets, triangles, 1.0)
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / 'plaque.stl'
            self.assertEqual(ci.ecrire_stl(chemin, solide, faces), len(faces))
            self.assertEqual(chemin.stat().st_size, 84 + 50 * len(faces))
            self.assertFalse(chemin.read_bytes()[:5] == b'solid')   # un STL binaire ne commence pas comme un STL texte


class CuttingTests(unittest.TestCase):
    def test_a_cut_shares_its_seam_between_both_sides(self):
        sommets, triangles = grille(1, 1, 10)
        complets, dessus, dessous = ci.couper(sommets, triangles, 0, 5.0)
        self.assertAlmostEqual(aire(complets, dessus) + aire(complets, dessous), 100.0)
        self.assertAlmostEqual(aire(complets, dessus), 50.0)
        couture = set(np.unique(dessus[dessus >= 4]).tolist())
        self.assertEqual(couture, set(np.unique(dessous[dessous >= 4]).tolist()))
        self.assertEqual(len(couture), 3)                        # les deux bords et la diagonale, un point chacun
        self.assertTrue(np.allclose(complets[sorted(couture)][:, 0], 5.0))

    def test_a_piece_too_long_for_the_bed_is_cut_until_it_fits(self):
        sommets, triangles = grille(60, 2, 10)                   # 600 × 20 mm
        self.assertFalse(ci.tient(sommets, PLATEAU))
        complets, parties = ci.recouper(sommets, triangles, PLATEAU)
        self.assertGreaterEqual(len(parties), 3)
        self.assertTrue(all(ci.tient(complets[np.unique(p)], PLATEAU) for p in parties))
        self.assertAlmostEqual(sum(aire(complets, p) for p in parties), 12000.0)

    def test_islands_are_printed_apart_and_crumbs_stay_with_the_biggest(self):
        a, ta = grille(10, 10, 5)
        b, tb = grille(10, 10, 5, origine=(100, 0, 0))
        c, tc = grille(1, 1, 5, origine=(300, 0, 0))             # une miette : deux triangles
        sommets = np.vstack([a, b, c])
        triangles = np.concatenate([ta, tb + len(a), tc + len(a) + len(b)])
        _complets, parties = ci.parties_a_imprimer(sommets, triangles, PLATEAU)
        self.assertEqual(sorted(len(p) for p in parties), [200, 202])


class DrillingTests(unittest.TestCase):
    """Le trou rond d'un bouton dans une pièce à imprimer (ID Studio IA, 15/11/2026 : « Oui, perce-le »)."""
    DISQUE = 48 / 2 * 6.0 ** 2 * np.sin(2 * np.pi / 48)          # l'aire d'un trou de 6 mm de rayon, à 48 côtés

    def test_a_round_hole_takes_its_disc_and_leaves_a_closed_wall(self):
        sommets, triangles = grille(20, 20, 2)                   # une plaque de 40 × 40 mm, faces vers le haut
        perces, t, otes = ci.percer(sommets, triangles, (20, 20, 0), (0, 0, 1), 6.0)
        self.assertGreater(otes, 0)
        self.assertAlmostEqual(aire(sommets, triangles) - aire(perces, t), self.DISQUE, places=6)
        bord = perces[len(sommets):]
        self.assertEqual(len(bord), 48)
        self.assertTrue(np.allclose(np.hypot(bord[:, 0] - 20, bord[:, 1] - 20), 6.0))        # le bord du trou est rond
        a, b, c = (perces[t[:, k]] for k in range(3))
        self.assertTrue((np.cross(b - a, c - a)[:, 2] > 0).all())                            # toutes les faces regardent encore dehors
        solide, faces = ci.epaissir(perces, t, 2.0)
        bilan = ci.bilan_solide(solide, faces)
        self.assertEqual((bilan['aretes_ouvertes'], bilan['aretes_multiples']), (0, 0))      # un solide fermé, trou compris
        self.assertAlmostEqual(bilan['volume_mm3'], (1600 - self.DISQUE) * 2.0, places=3)

    def test_a_tilted_hole_is_round_along_its_axis_and_spares_the_wall_behind(self):
        dessus, t_dessus = grille(20, 20, 2)
        fond, t_fond = grille(20, 20, 2, origine=(0, 0, -30))    # la paroi d'en face, 30 mm plus loin
        sommets, triangles = np.vstack([dessus, fond]), np.concatenate([t_dessus, t_fond + len(dessus)])
        penche = np.radians(20)
        perces, t, _otes = ci.percer(sommets, triangles, (20, 20, 0), (0, np.sin(penche), np.cos(penche)), 6.0)
        self.assertAlmostEqual(aire(sommets, triangles) - aire(perces, t), self.DISQUE / np.cos(penche), places=6)
        intacts = {tuple(x) for x in (t_fond + len(dessus)).tolist()}
        self.assertTrue(intacts <= {tuple(x) for x in t.tolist()})                           # la paroi d'en face n'est pas touchée

    def test_a_hole_is_never_half_drilled(self):
        sommets, triangles = grille(20, 20, 2)
        with self.assertRaises(ValueError):                      # à cheval sur le bord de la pièce
            ci.percer(sommets, triangles, (1, 20, 0), (0, 0, 1), 6.0)
        memes, t, otes = ci.percer(sommets, triangles, (200, 200, 0), (0, 0, 1), 6.0)       # la pièce ne passe pas par là
        self.assertEqual((otes, len(memes), t.tolist()), (0, len(sommets), triangles.tolist()))


class CleaningTests(unittest.TestCase):
    def test_a_crumb_goes_back_to_the_piece_around_it(self):
        sommets, triangles = grille(20, 20, 5)                   # 800 triangles
        centres = sommets[triangles].mean(axis=1)
        etiquettes = (centres[:, 0] > 50).astype(np.int64)       # deux moitiés
        miette = (centres[:, 0] < 10) & (centres[:, 1] < 10)     # quelques triangles de la moitié 0, donnés à la moitié 1
        etiquettes[miette] = 1
        corrigees, deplaces = ci.rattacher_miettes(triangles, etiquettes)
        self.assertEqual(deplaces, int(miette.sum()))
        self.assertTrue((corrigees == (centres[:, 0] > 50)).all())

    def test_the_biggest_island_of_a_small_piece_is_never_taken(self):
        sommets, triangles = grille(10, 10, 5)                   # 200 triangles
        centres = sommets[triangles].mean(axis=1)
        etiquettes = (centres[:, 0] > 40).astype(np.int64)       # la moitié 1 : 40 triangles, moins qu'une « miette »
        corrigees, deplaces = ci.rattacher_miettes(triangles, etiquettes)
        self.assertEqual((deplaces, corrigees.tolist()), (0, etiquettes.tolist()))

    def test_a_flap_hanging_from_a_closed_shape_is_left_aside(self):
        sommets = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10], [10, 10, 10]], dtype=float)
        tetra = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
        lambeau = np.array([[1, 2, 4]])                          # accroché à l'arête 1-2, libre ailleurs
        garde = ci.sans_lambeaux(np.concatenate([tetra, lambeau]))
        self.assertEqual(garde.tolist(), [True, True, True, True, False])
        self.assertEqual(ci.bilan_solide(sommets, tetra)['aretes_ouvertes'], 0)


if __name__ == '__main__':
    unittest.main()
