"""Image of a maquette (tools/lab_image.py, 12/11/2026): the PNG of a scene aurora.scene/1, shown to a model that sees.

The PNG is read back here with zlib (no image library), then its pixels are looked at: the tints, the background,
what hides what, the shapes, the place of the pieces and the framing. No network; nothing is written (the maquettes
of ``lab`` are built with ``keep=False``, in a temporary AURORA_HOME).
"""
import copy
import json
import logging
import struct
import time
import unittest
import zlib

import numpy as np

from aurora.tools import lab, lab_image
from tests.doubles_core import TempEnv
from tests.test_vision import png_chunks

SIDE = 420
BACKGROUND, EDGE = (236, 240, 245), (40, 50, 70)
TINTS = {'cyan': (56, 214, 255), 'violet': (167, 123, 255), 'ambre': (255, 181, 71), 'vert': (61, 255, 168)}
BOX = {'type': 'boite', 'l': 1.0, 'h': 1.0, 'p': 1.0}
MIDDLE = [0, .55, 0]   # the point of the object the framing of the trial shows in the middle of each view


def setUpModule():
    logging.getLogger('aurora').setLevel(logging.CRITICAL)


def piece(pid: str, forme: dict, position: list, couleur: str = 'cyan', parent: str | None = None, **more) -> dict:
    return {'id': pid, 'nom': pid, 'groupe': None, 'parent': parent, 'position': position, 'forme': forme,
            'couleur': couleur, 'fonction': 'Une pièce de test.', 'proprietes': [], 'mots': [], **more}


def scene(*pieces) -> dict:
    return {'format': 'aurora.scene/1', 'id': 'essai', 'titre': 'Essai', 'pieces': list(pieces)}


def pixels(data: bytes) -> np.ndarray:
    """The pixels (rows, columns, 3) of a PNG written by vision.png, read back with zlib."""
    chunks = png_chunks(data)
    width, height, depth, kind, *_ = struct.unpack('>IIBBBBB', chunks[b'IHDR'])
    assert (depth, kind) == (8, 2), 'RGB, 8 bits'
    rows = np.frombuffer(zlib.decompress(chunks[b'IDAT']), dtype=np.uint8).reshape(height, 1 + 3 * width)
    assert not rows[:, 0].any(), 'no filter on any row'
    return rows[:, 1:].reshape(height, width, 3)


def views(what: dict) -> tuple[np.ndarray, np.ndarray]:
    """The two views of a scene: three quarters, then the side."""
    image = pixels(lab_image.render_scene(what))
    return image[:, :SIDE], image[:, SIDE:]


def drawn(view: np.ndarray) -> np.ndarray:
    """Where a view shows something: every pixel that is not the background."""
    return (view != BACKGROUND).any(axis=-1)


def shade(view: np.ndarray, tint: tuple) -> np.ndarray:
    """Where a view shows this tint, more or less lit (a face keeps 45 % to 100 % of the tint of its piece)."""
    full = np.array(tint, dtype=float)
    part = view[..., int(full.argmax())] / full.max()
    return (part >= .44) & (part <= 1.0) & (np.abs(view - part[..., None] * full).max(axis=-1) <= 1.5)


def line(view: np.ndarray) -> np.ndarray:
    return (view == EDGE).all(axis=-1)


def spot(view: int, point: list) -> tuple[int, int]:
    """The pixel (row, column) where the framing of the trial shows a point of the object: 168 pixels per unit,
    the point (0, 0.55, 0) in the middle column, at 52 % of the height."""
    x, y, _ = lab_image.VIEWS[view] @ (np.array(point, dtype=float) - MIDDLE)
    return int(SIDE * .52 - 168 * y), int(SIDE / 2 + 168 * x)


class PngTests(unittest.TestCase):
    def test_a_png_of_840_by_420_with_no_text(self):
        data = lab_image.render_scene(scene(piece('caisse', BOX, MIDDLE)))
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        chunks = png_chunks(data)   # each CRC is checked
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[b'IHDR']), (840, 420, 8, 2, 0, 0, 0))
        self.assertIn(b'IEND', chunks)
        self.assertEqual(pixels(data).shape, (420, 840, 3))
        self.assertLess(len(data), 100_000)   # flat tints: a few kilobytes

    def test_the_same_scene_gives_the_same_bytes_and_is_left_as_it_was(self):
        car = scene(piece('caisse', {'type': 'boite', 'l': .7, 'h': .3, 'p': 1.8}, [0, .35, 0]),
                    piece('roue', {'type': 'cylindre', 'rayon': .18, 'hauteur': .1, 'axe': 'x'}, [.4, -.17, .55],
                          'ambre', 'caisse', rotation=[0, 0, 0]),
                    piece('phare', {'type': 'sphere', 'rayon': .05}, [.24, .03, .92], 'violet', 'caisse'),
                    piece('volant', {'type': 'tore', 'rayon': .1, 'epaisseur': .02, 'axe': 'z'}, [0, .3, .2], 'vert'))
        before = copy.deepcopy(car)
        first = lab_image.render_scene(car)
        self.assertEqual(car, before)
        self.assertEqual(lab_image.render_scene(car), first)
        self.assertEqual(lab_image.render_scene(json.loads(json.dumps(car))), first)

    def test_the_constants_follow_the_format(self):
        self.assertEqual(set(lab_image.TINTS), set(lab.COULEURS))
        self.assertEqual(lab_image.TINTS, TINTS)   # the tints of the interface (labo/scene.ts)
        self.assertEqual(lab_image.MAX_PIECES, lab.MAX_PIECES)
        self.assertEqual((lab_image.SIDE, lab_image.BACKGROUND, lab_image.EDGE), (SIDE, BACKGROUND, EDGE))


class ShapeTests(unittest.TestCase):
    def test_a_box_alone_shows_its_tint_in_the_middle_and_the_background_in_the_corners(self):
        for couleur, tint in TINTS.items():
            with self.subTest(couleur=couleur):
                for view in views(scene(piece('caisse', BOX, MIDDLE, couleur))):
                    self.assertTrue(shade(view[216:221, 208:213], tint).all(), view[218, 210])
                    for corner in (view[:40, :40], view[:40, -40:], view[-40:, :40], view[-40:, -40:]):
                        self.assertTrue((corner == BACKGROUND).all())
                    # Nothing but the piece, its line and the background.
                    self.assertTrue((shade(view, tint) | line(view) | ~drawn(view)).all())

    def test_a_face_is_shaded_by_its_orientation_like_the_trial_did(self):
        three, side = views(scene(piece('caisse', BOX, MIDDLE, 'cyan')))
        # From the side, one face, straight on: 0.45 + 0.55 x 0.5 of the tint (the light of the trial: 0.35, 0.8, 0.5).
        self.assertEqual(tuple(side[218, 210]), tuple(int(c * .725) for c in TINTS['cyan']))
        self.assertEqual(len(np.unique(side[drawn(side) & ~line(side)], axis=0)), 1)
        # From three quarters, three faces and three shades, the top the brightest.
        faces = np.unique(three[drawn(three) & ~line(three)], axis=0)
        self.assertEqual(len(faces), 3)
        top = three[spot(0, [0, 1.05, 0])]
        self.assertEqual(int(top[2]), int(faces[:, 2].max()))

    def test_an_unknown_colour_is_grey(self):
        for couleur in ('rose', '', None, 7, ['cyan']):
            with self.subTest(couleur=couleur):
                _three, side = views(scene(piece('caisse', BOX, MIDDLE, couleur)))
                self.assertEqual(tuple(side[218, 210]), (145, 145, 145))   # 200 x 0.725

    def test_a_box_has_the_size_of_the_trial_and_a_line_on_its_outline(self):
        _three, side = views(scene(piece('caisse', {'type': 'boite', 'l': .3, 'h': .5, 'p': 1.0}, MIDDLE)))
        row = np.flatnonzero(drawn(side)[218])
        self.assertEqual((int(row[0]), int(row[-1])), (126, 293))     # 1 unit deep: 168 pixels around the middle
        column = np.flatnonzero(drawn(side)[:, 210])
        self.assertEqual(len(column), 84)                              # 0.5 unit high
        # The line is drawn on the piece (the silhouette keeps its size), one pixel wide.
        self.assertTrue(line(side)[218, [126, 293]].all())
        self.assertFalse(line(side)[218, 127:293].any())
        self.assertTrue(line(side)[[column[0], column[-1]], 210].all())

    def test_the_line_follows_the_sharp_edges_not_the_round_sides(self):
        def inner(view):   # the pixels of a piece that touch no pixel of the background
            full = drawn(view)
            return full[1:-1, 1:-1] & full[:-2, 1:-1] & full[2:, 1:-1] & full[1:-1, :-2] & full[1:-1, 2:]
        box, _side = views(scene(piece('caisse', BOX, MIDDLE)))
        self.assertGreater(int((line(box)[1:-1, 1:-1] & inner(box)).sum()), 150)   # the three edges of the near corner
        ball, _side = views(scene(piece('boule', {'type': 'sphere', 'rayon': .5}, MIDDLE)))
        self.assertEqual(int((line(ball)[1:-1, 1:-1] & inner(ball)).sum()), 0)
        self.assertGreater(int(line(ball).sum()), 300)                              # its outline
        # A cylinder: the rim of its end, and nothing along its round side.
        _three, side = views(scene(piece('rouleau', {'type': 'cylindre', 'rayon': .3, 'hauteur': 1.2, 'axe': 'z'},
                                         MIDDLE)))
        self.assertEqual(int((line(side)[1:-1, 1:-1] & inner(side)).sum()), 0)
        self.assertGreater(len(np.unique(side[drawn(side) & ~line(side)], axis=0)), 4)   # shaded around its axis

    def test_a_volume_turned_any_way_has_no_crack_between_its_faces(self):
        # A convex volume alone: on each row and each column of pixels, what is drawn is one run, with no hole.
        rng = np.random.default_rng(12112026)
        makers = (lambda s: {'type': 'boite', 'l': s[0], 'h': s[1], 'p': s[2]},
                  lambda s: {'type': 'sphere', 'rayon': s[0] / 2},
                  lambda s: {'type': 'cylindre', 'rayon': s[0] / 2, 'hauteur': s[1], 'axe': 'x'},
                  lambda s: {'type': 'cylindre', 'rayon': s[0] / 2, 'rayon_haut': s[0] / 40, 'hauteur': s[1],
                             'axe': 'z'})
        for n in range(40):
            shape = makers[n % 4](rng.uniform(.05, 1.2, 3).tolist())
            place, turn = rng.uniform(-.3, .3, 3).tolist(), rng.uniform(-180, 180, 3).tolist()
            for view in views(scene(piece('p', shape, place, rotation=turn))):
                full = drawn(view)
                self.assertTrue(full.any(), (n, shape))
                for axis in (0, 1):
                    runs = (np.diff(full.astype(np.int8), axis=axis) == 1).sum(axis=axis) + full.take(0, axis=axis)
                    self.assertLessEqual(int(runs.max()), 1, (n, shape))

    def test_a_sphere_is_round_and_a_cylinder_lies_along_its_axis(self):
        def extent(view):   # width and height of what is drawn
            rows, columns = np.nonzero(drawn(view))
            return int(columns.max() - columns.min() + 1), int(rows.max() - rows.min() + 1)
        for view in views(scene(piece('boule', {'type': 'sphere', 'rayon': .5}, MIDDLE))):
            width, height = extent(view)
            self.assertTrue(164 <= width <= 168 and 164 <= height <= 168, (width, height))
            self.assertAlmostEqual(int(drawn(view).sum()) / (np.pi * 84 ** 2), 1, delta=.03)   # a disc, not a square
        sizes = {}
        for axe in 'xyz':
            _three, side = views(scene(piece('rouleau', {'type': 'cylindre', 'rayon': .2, 'hauteur': 1.2, 'axe': axe},
                                             MIDDLE)))
            sizes[axe] = extent(side)
        self.assertTrue(200 <= sizes['z'][0] <= 202 and 65 <= sizes['z'][1] <= 68, sizes)   # lying, seen along it
        self.assertTrue(65 <= sizes['y'][0] <= 68 and 200 <= sizes['y'][1] <= 202, sizes)   # standing
        self.assertTrue(65 <= sizes['x'][0] <= 68 and 65 <= sizes['x'][1] <= 68, sizes)     # seen from its end: a disc

    def test_a_cone_is_narrower_at_its_tip(self):
        def cone(axe, low, high):
            return scene(piece('cone', {'type': 'cylindre', 'rayon': low, 'rayon_haut': high, 'hauteur': 1.0,
                                        'axe': axe}, MIDDLE, 'ambre'))
        # Standing: the « top » (rayon_haut) is up. From the side, the rows near the tip and near the base.
        _three, side = views(cone('y', .5, .025))
        tip, base = int(drawn(side)[spot(1, [0, .95, 0])[0]].sum()), int(drawn(side)[spot(1, [0, .15, 0])[0]].sum())
        self.assertTrue(0 < tip < base / 4, (tip, base))
        self.assertAlmostEqual(base, 2 * .4525 * 168, delta=4)
        # Lying from back to front: the top is at the front, to the left of the side view.
        _three, side = views(cone('z', .5, .025))
        tip, base = (int(drawn(side)[:, spot(1, [0, .55, z])[1]].sum()) for z in (.4, -.4))
        self.assertTrue(0 < tip < base / 4, (tip, base))
        # Lying along x: the top of the interface is at -x, to the left of the three quarters view…
        three, _side = views(cone('x', .5, .025))
        tip, base = (int(drawn(three)[:, spot(0, [x, .55, 0])[1]].sum()) for x in (-.4, .4))
        self.assertTrue(0 < tip < base / 4, (tip, base))
        # … so the cone that lab._shape writes for « pointing to the right » (rayon small, rayon_haut large) does.
        made = lab._shape('cone', [1.0, 1.0, 1.0], 'x')
        self.assertLess(made['rayon'], made['rayon_haut'])
        three, _side = views(scene(piece('cone', made, MIDDLE)))
        tip, base = (int(drawn(three)[:, spot(0, [x, .55, 0])[1]].sum()) for x in (.4, -.4))
        self.assertTrue(0 < tip < base / 4, (tip, base))

    def test_a_ring_has_a_hole(self):
        _three, side = views(scene(piece('anneau', {'type': 'tore', 'rayon': .4, 'epaisseur': .08, 'axe': 'x'},
                                         MIDDLE, 'vert')))
        row = drawn(side)[218]
        self.assertFalse(row[210 - 50:210 + 50].any())                       # the hole (0.32 unit of radius)
        self.assertTrue(row[210 - 76:210 - 58].all() and row[210 + 58:210 + 76].all())   # the band, on both sides
        self.assertFalse(row[:210 - 84].any() or row[210 + 84:].any())
        # Seen from its edge (its axis across the view), it is a bar as thick as its tube.
        _three, side = views(scene(piece('anneau', {'type': 'tore', 'rayon': .4, 'epaisseur': .08, 'axe': 'z'},
                                         MIDDLE, 'vert')))
        rows, columns = np.nonzero(drawn(side))
        self.assertAlmostEqual(int(columns.max() - columns.min() + 1), .16 * 168, delta=2)
        self.assertAlmostEqual(int(rows.max() - rows.min() + 1), .96 * 168, delta=2)

    def test_a_maillage_is_the_box_of_its_size(self):
        model = {'type': 'maillage', 'fichier': 'imp_objet.stl', 'taille': .8, 'debout': 'z'}
        self.assertEqual(lab_image.render_scene(scene(piece('modele', model, MIDDLE))),
                         lab_image.render_scene(scene(piece('modele', {'type': 'boite', 'l': .8, 'h': .8, 'p': .8},
                                                            MIDDLE))))


class DepthTests(unittest.TestCase):
    # Apart (the far one stops at x = 0.2, the near one starts at x = 0.3) and one before the other in both views:
    # the eye is on the side of x positive, from three quarters as from the side.
    NEAR = piece('proche', {'type': 'boite', 'l': .4, 'h': .4, 'p': .4}, [.5, .55, -.1], 'vert')
    FAR = piece('lointaine', {'type': 'boite', 'l': 1.2, 'h': 1.2, 'p': 1.2}, [-.4, .55, -.4], 'violet')

    def test_a_near_piece_hides_a_far_one_whatever_their_order(self):
        alone, behind = views(scene(self.NEAR)), views(scene(self.FAR))
        for order in ((self.NEAR, self.FAR), (self.FAR, self.NEAR)):
            for n, both in enumerate(views(scene(*order))):
                with self.subTest(first=order[0]['id'], view=n):
                    here = drawn(alone[n])
                    self.assertGreater(int(here.sum()), 4000)
                    # The near piece is whole: every one of its pixels is as when it is alone.
                    self.assertTrue((both[here] == alone[n][here]).all())
                    # The far one has lost what the near one covers, and nothing else.
                    lost = int(shade(behind[n], TINTS['violet']).sum()) - int(shade(both, TINTS['violet']).sum())
                    self.assertGreater(lost, 500)
                    self.assertTrue((both[~here] == behind[n][~here]).all())

    def test_a_piece_that_goes_through_another_is_hidden_only_where_it_is_behind(self):
        # A wall and a rod through it: sorting faces could only draw the whole rod before or after the wall.
        wall = piece('mur', {'type': 'boite', 'l': .1, 'h': 1.0, 'p': 1.0}, MIDDLE, 'cyan')
        rod = piece('tige', {'type': 'boite', 'l': 1.6, 'h': .2, 'p': .2}, MIDDLE, 'ambre')
        for order in ((wall, rod), (rod, wall)):
            three, side = views(scene(*order))
            self.assertTrue(shade(three[spot(0, [.5, .55, 0])], TINTS['ambre']))    # in front of the wall
            self.assertTrue(shade(three[spot(0, [-.2, .55, 0])], TINTS['cyan']))    # behind it: the wall is seen
            self.assertTrue(shade(three[spot(0, [-.7, .55, 0])], TINTS['ambre']))   # beyond its edge: seen again
            self.assertTrue(shade(side[218, 210], TINTS['ambre']))                  # from the side: the end of the rod
            self.assertTrue(shade(side[218, 150], TINTS['cyan']))


class PlacementTests(unittest.TestCase):
    SMALL = {'type': 'boite', 'l': .2, 'h': .2, 'p': .2}

    def where(self, view: np.ndarray) -> tuple[float, float]:
        rows, columns = np.nonzero(drawn(view))
        return float(columns.mean()), float(rows.mean())

    def test_the_front_is_to_the_left_and_the_top_is_up(self):
        three, side = views(scene(piece('milieu', self.SMALL, MIDDLE)))
        middle = self.where(three), self.where(side)
        self.assertAlmostEqual(middle[1][0], 210, delta=1)
        self.assertAlmostEqual(middle[1][1], 218, delta=1)
        self.assertAlmostEqual(middle[0][0], 210, delta=1)
        front = [self.where(v) for v in views(scene(piece('avant', self.SMALL, [0, .55, .8])))]
        self.assertAlmostEqual(front[1][0], 210 - .8 * 168, delta=1)      # from the side: straight to the left
        self.assertAlmostEqual(front[1][1], middle[1][1], delta=1)
        self.assertLess(front[0][0], middle[0][0] - 60)                   # three quarters: to the left, and lower
        self.assertGreater(front[0][1], middle[0][1] + 25)
        high = [self.where(v) for v in views(scene(piece('haut', self.SMALL, [0, 1.35, 0])))]
        self.assertAlmostEqual(high[1][1], middle[1][1] - .8 * 168, delta=1)
        self.assertLess(high[0][1], middle[0][1] - 110)
        right = [self.where(v) for v in views(scene(piece('droite', self.SMALL, [.8, .55, 0])))]
        self.assertGreater(right[0][0], middle[0][0] + 90)                # three quarters: its right side is seen
        self.assertAlmostEqual(right[1][0], middle[1][0], delta=1)        # from the side: towards the eye, same place

    def test_a_child_is_placed_in_the_frame_of_its_parent(self):
        mast = piece('mat', {'type': 'boite', 'l': .6, 'h': .1, 'p': .1}, [0, .3, 0], rotation=[0, 0, 90])
        flag = {'type': 'boite', 'l': .2, 'h': .4, 'p': .3}
        carried = scene(mast, piece('drapeau', flag, [.6, 0, 0], 'vert', 'mat'))
        said = scene(mast, piece('drapeau', flag, [0, .9, 0], 'vert', rotation=[0, 0, 90]))
        self.assertEqual(lab_image.render_scene(carried), lab_image.render_scene(said))
        _three, side = views(carried)
        self.assertTrue(shade(side[spot(1, [0, .9, 0])], TINTS['vert']))
        # Three levels, the rotations follow one another (x, then z in the turned frame).
        chain = scene(piece('a', self.SMALL, [0, .2, 0], rotation=[90, 0, 0]),
                      piece('b', self.SMALL, [0, 0, -.5], 'ambre', 'a', rotation=[0, 0, 90]),   # up, in the frame of a
                      piece('c', self.SMALL, [.5, 0, 0], 'vert', 'b'))
        _three, side = views(chain)
        self.assertTrue(shade(side[spot(1, [0, .7, 0])], TINTS['ambre']))
        self.assertTrue(shade(side[spot(1, [0, .7, .5])], TINTS['vert']))   # x of b is y of a, which is z of the object

    def test_the_rest_angle_of_an_articulation_turns_the_piece_and_its_children(self):
        arm = {'type': 'boite', 'l': .8, 'h': .1, 'p': .1, 'decalage': [.4, 0, 0]}
        joint = {'axe': 'z', 'min': -120, 'max': 120, 'repos': 90,
                 'animation': {'amplitude': 20, 'periode': 4, 'phase': 0}}
        hand = piece('main', self.SMALL, [.8, 0, 0], 'vert', 'bras')
        jointed = scene(piece('bras', arm, [0, .2, 0], articulation=joint), hand)
        turned = scene(piece('bras', arm, [0, .2, 0], rotation=[0, 0, 90]), hand)
        self.assertEqual(lab_image.render_scene(jointed), lab_image.render_scene(turned))
        _three, side = views(jointed)
        self.assertTrue(shade(side[spot(1, [0, 1.0, 0])], TINTS['vert']))
        # An articulation that says nothing usable leaves the piece as its rotation puts it.
        for odd in ({'axe': 'w', 'repos': 90}, {'axe': 'z', 'repos': float('nan')}, {'axe': 'z'}, 'coude', 5):
            self.assertEqual(lab_image.render_scene(scene(piece('bras', arm, [0, .2, 0], articulation=odd), hand)),
                             lab_image.render_scene(scene(piece('bras', arm, [0, .2, 0]), hand)))

    def test_the_offset_moves_the_shape_and_not_its_children(self):
        moved = scene(piece('socle', {**BOX, 'h': .2, 'decalage': [0, .35, 0]}, [0, .2, 0]),
                      piece('pion', self.SMALL, [0, .8, 0], 'ambre', 'socle'))
        plain = scene(piece('socle', {**BOX, 'h': .2}, [0, .55, 0]),
                      piece('pion', self.SMALL, [0, .45, 0], 'ambre', 'socle'))
        self.assertEqual(lab_image.render_scene(moved), lab_image.render_scene(plain))

    def test_an_object_too_tall_for_the_framing_of_the_trial_is_reduced_not_cut(self):
        roof = {'type': 'cylindre', 'rayon': .3, 'rayon_haut': .02, 'hauteur': .5, 'decalage': [0, .25, 0]}
        tower = scene(piece('tour', {'type': 'boite', 'l': .4, 'h': 2.6, 'p': .4}, [0, 1.3, 0]),
                      piece('toit', roof, [0, 1.3, 0], 'ambre', 'tour'))   # 3.1 units from the ground to the tip
        heights, widths = [], []
        for view in views(tower):
            full = drawn(view)
            self.assertFalse(full[:10].any() or full[-10:].any() or full[:, :10].any() or full[:, -10:].any())
            rows = np.flatnonzero(full.any(axis=1))
            heights.append(int(rows[-1] - rows[0] + 1))
            widths.append(int(full[210].sum()))                              # the tower, at mid-height
            self.assertAlmostEqual((rows[0] + rows[-1]) / 2, 210, delta=2)   # centred
        # From the side its 3.1 units fill the view but a margin of 12 pixels; the same scale from three quarters,
        # where it is seen from above (shorter) and from a corner (wider).
        scale = (SIDE - 2 * 12) / 3.1
        self.assertAlmostEqual(heights[1], 396, delta=1)
        self.assertAlmostEqual(heights[0], 2.98 * scale, delta=3)
        self.assertAlmostEqual(widths[1], .4 * scale, delta=1)
        self.assertAlmostEqual(widths[0], .5616 * scale, delta=1)
        # A small object is not enlarged: it keeps the scale of the trial (168 pixels per unit).
        _three, side = views(scene(piece('de', self.SMALL, [0, .1, 0])))
        self.assertEqual(int(drawn(side)[spot(1, [0, .1, 0])[0]].sum()), 34)


class RealMaquetteTests(unittest.TestCase):
    """The four examples of the instructions (tools/lab.py), built like any description of a model."""
    EXAMPLES = (('un ventilateur de bureau', 'GEN_EXAMPLE'), ('une voiture', 'GEN_EXAMPLE_CAR'),
                ('un avion de tourisme', 'GEN_EXAMPLE_VEHICLE'), ('un voilier', 'GEN_EXAMPLE_BOAT'))

    def setUp(self):
        self.env = TempEnv().__enter__()   # a temporary AURORA_HOME: build_generated reads the kept scenes
        lab.clear_cache()

    def tearDown(self):
        self.env.__exit__(None, None, None)

    def test_each_example_is_drawn_whole_with_its_tints(self):
        for sujet, name in self.EXAMPLES:
            with self.subTest(sujet=sujet):
                built, why = lab.build_generated(json.dumps(getattr(lab, name), ensure_ascii=False), sujet, 'un test',
                                                 keep=False)
                self.assertIsNone(why)
                self.assertFalse(list(lab.generated_dir().glob('*')))   # nothing was written
                data = lab_image.render_scene(built)
                self.assertEqual(lab_image.render_scene(built), data)
                for view in views(built):
                    full = drawn(view)
                    self.assertGreater(int(full.sum()), 4000)           # measured: 8 500 to 38 000 pixels a view
                    self.assertGreater(int(line(view).sum()), 600)
                    # Nothing is cut: the ventilateur, 2 units tall, does not fit in the framing of the trial.
                    self.assertFalse(full[:5].any() or full[-5:].any() or full[:, :5].any() or full[:, -5:].any())
                three, _side = views(built)
                for couleur in {p['couleur'] for p in built['pieces']}:
                    self.assertGreater(int(shade(three, TINTS[couleur]).sum()), 40, couleur)

    def test_the_car_is_long_from_back_to_front_and_stands_on_its_wheels(self):
        built, why = lab.build_generated(json.dumps(lab.GEN_EXAMPLE_CAR, ensure_ascii=False), 'une voiture', 'un test',
                                         keep=False)
        self.assertIsNone(why)
        _three, side = views(built)
        rows, columns = np.nonzero(drawn(side))
        self.assertAlmostEqual(int(columns.max() - columns.min() + 1), 2 * 168, delta=3)   # 2 units long
        self.assertEqual(int(rows.max()), spot(1, [0, 0, 0])[0])                            # on y = 0
        wheels = shade(side, TINTS['ambre'])
        self.assertGreater(int(wheels[:, :210].sum()), 1500)   # a wheel at the front, a wheel at the back
        self.assertGreater(int(wheels[:, 210:].sum()), 1500)
        self.assertFalse(wheels[:240].any())                    # and none above the body

    def test_the_predefined_arm_is_drawn_whole(self):
        arm = lab.load_scene('bras_robotique')
        for view in views(arm):
            full = drawn(view)
            self.assertGreater(int(full.sum()), 10000)
            self.assertFalse(full[:5].any() or full[-5:].any() or full[:, :5].any() or full[:, -5:].any())
            for couleur in TINTS:
                self.assertGreater(int(shade(view, TINTS[couleur]).sum()), 40, couleur)


class OddSceneTests(unittest.TestCase):
    GOOD = piece('bonne', BOX, MIDDLE, 'ambre')

    def test_nothing_to_draw_gives_the_background_alone(self):
        empty = lab_image.render_scene({'pieces': []})
        self.assertTrue((pixels(empty) == BACKGROUND).all())
        for odd in ({}, {'pieces': None}, {'pieces': 'boite'}, {'pieces': {'a': 1}}, {'pieces': [None, 3, 'x', [], {}]},
                    None, [], 'scene', 12):
            with self.subTest(scene=odd):
                self.assertEqual(lab_image.render_scene(odd), empty)

    def test_a_piece_that_cannot_be_drawn_is_left_out(self):
        nan, inf = float('nan'), float('inf')
        odd = [
            piece('p', {'type': 'pyramide', 'l': 1, 'h': 1, 'p': 1}, MIDDLE),          # an unknown shape
            piece('p', {'l': 1, 'h': 1, 'p': 1}, MIDDLE), piece('p', None, MIDDLE), piece('p', 'boite', MIDDLE),
            piece('p', ['boite'], MIDDLE), piece('p', {'type': ['boite']}, MIDDLE), piece('p', {'type': None}, MIDDLE),
            piece('p', BOX, [0, nan, 0]), piece('p', BOX, [inf, 0, 0]), piece('p', BOX, [0, 0, -inf]),   # not finite
            piece('p', BOX, [0, 0, 1e400]), piece('p', BOX, [10 ** 400, 0, 0]), piece('p', BOX, [1e9, 0, 0]),
            piece('p', BOX, [True, 0, 0]), piece('p', BOX, ['0', 0, 0]), piece('p', BOX, [0, 0]), piece('p', BOX, None),
            piece('p', BOX, 'ici'), piece('p', BOX, [0, [0], 0]), piece('p', BOX, [0, 0, 0, 0]),
            piece('p', BOX, MIDDLE, rotation=[0, nan, 0]), piece('p', BOX, MIDDLE, rotation=[0, 90]),
            piece('p', BOX, MIDDLE, rotation='x'), piece('p', BOX, MIDDLE, rotation=[inf, 0, 0]),
            *(piece('p', {**BOX, 'l': bad}, MIDDLE) for bad in (0, -1, nan, inf, None, '1', True, [1], 10 ** 400)),
            piece('p', {'type': 'boite', 'l': 1, 'h': 1}, MIDDLE),
            piece('p', {'type': 'sphere', 'rayon': 0}, MIDDLE), piece('p', {'type': 'sphere'}, MIDDLE),
            piece('p', {'type': 'cylindre', 'rayon': .2}, MIDDLE),
            piece('p', {'type': 'cylindre', 'rayon': .2, 'hauteur': 1, 'rayon_haut': nan}, MIDDLE),
            piece('p', {'type': 'cylindre', 'rayon': .2, 'hauteur': 1, 'rayon_haut': 0}, MIDDLE),
            *(piece('p', {'type': 'cylindre', 'rayon': .2, 'hauteur': 1, 'axe': bad}, MIDDLE)
              for bad in ('w', 'xy', 5, None, ['x'], {'x': 1})),
            piece('p', {'type': 'tore', 'rayon': .4, 'epaisseur': -1}, MIDDLE),
            piece('p', {'type': 'tore', 'rayon': .4, 'epaisseur': .1, 'axe': 'haut'}, MIDDLE),
            piece('p', {'type': 'maillage', 'fichier': 'imp_objet.stl', 'taille': 0}, MIDDLE),
            piece('p', {'type': 'maillage', 'fichier': 'imp_objet.stl'}, MIDDLE),
            piece('p', {**BOX, 'decalage': [0, nan, 0]}, MIDDLE), piece('p', {**BOX, 'decalage': 'haut'}, MIDDLE),
            piece('p', {**BOX, 'decalage': [0, 0]}, MIDDLE),
            piece('p', BOX, MIDDLE, parent='inconnue'), piece('p', BOX, MIDDLE, parent=5),         # an unknown parent
            piece('p', BOX, MIDDLE, parent=['bonne']), piece('p', BOX, MIDDLE, parent={'id': 'bonne'}),
            piece('p', BOX, MIDDLE, parent='p'),
            {'id': 'p'}, {}, None, 'piece', 3, [piece('p', BOX, MIDDLE)],
        ]
        alone = lab_image.render_scene(scene(self.GOOD))
        for n, bad in enumerate(odd):
            with self.subTest(n=n, piece=str(bad)[:140]):
                self.assertEqual(lab_image.render_scene(scene(bad)), lab_image.render_scene(scene()))
                self.assertEqual(lab_image.render_scene(scene(self.GOOD, bad)), alone)
                self.assertEqual(lab_image.render_scene(scene(bad, self.GOOD)), alone)

    def test_the_children_of_a_piece_left_out(self):
        small = {'type': 'boite', 'l': .3, 'h': .3, 'p': .3}
        orphan = piece('orpheline', BOX, MIDDLE, parent='absente')
        alone = lab_image.render_scene(scene(self.GOOD))
        # No place for a piece: no place for its children either.
        self.assertEqual(lab_image.render_scene(scene(self.GOOD, orphan, piece('petite', small, MIDDLE, 'vert',
                                                                              'orpheline'))), alone)
        # A parent declared after its child: the child has no place yet, the parent is drawn.
        self.assertEqual(lab_image.render_scene(scene(piece('petite', small, [0, 1, 0], 'vert', 'bonne'), self.GOOD)),
                         alone)
        # A piece with a place and no shape that can be drawn: it is not drawn, its children are, at their place.
        ghost = piece('fantome', {'type': 'nuage'}, [0, .2, 0], rotation=[0, 0, 90])
        self.assertEqual(
            lab_image.render_scene(scene(ghost, piece('petite', small, [.6, 0, 0], 'vert', 'fantome'))),
            lab_image.render_scene(scene(piece('petite', small, [0, .8, 0], 'vert', rotation=[0, 0, 90]))))

    def test_odd_names_and_sizes_do_not_raise(self):
        alone = lab_image.render_scene(scene(self.GOOD))
        for pid in (None, 5, ['a'], {'a': 1}, ''):
            self.assertEqual(lab_image.render_scene(scene({**self.GOOD, 'id': pid})), alone)
        self.assertEqual(lab_image.render_scene(scene(self.GOOD, {**self.GOOD})), alone)   # the same piece twice
        for size in (1e-9, 1e-3, 900.0):
            data = lab_image.render_scene(scene(piece('p', {'type': 'sphere', 'rayon': size}, MIDDLE),
                                                piece('q', {'type': 'tore', 'rayon': size, 'epaisseur': size}, MIDDLE),
                                                piece('r', {'type': 'cylindre', 'rayon': size, 'hauteur': size,
                                                            'rayon_haut': size / 50, 'axe': 'x'}, [size, 0, -size])))
            self.assertEqual(pixels(data).shape, (420, 840, 3))
        # A ring thicker than its radius has no hole: still a volume.
        _three, side = views(scene(piece('q', {'type': 'tore', 'rayon': .1, 'epaisseur': .3, 'axe': 'x'}, MIDDLE)))
        self.assertTrue(drawn(side)[218, 150:270].all())

    def test_the_pieces_beyond_the_bound_of_the_format_are_not_drawn(self):
        small = {'type': 'boite', 'l': .1, 'h': .1, 'p': .1}
        many = [piece(f'p{n}', small, [-.9 + .2 * (n % 10), .1 + .2 * (n // 10), 0], lab.COULEURS[n % 4])
                for n in range(80)]
        self.assertEqual(lab_image.render_scene(scene(*many)), lab_image.render_scene(scene(*many[:lab.MAX_PIECES])))
        self.assertNotEqual(lab_image.render_scene(scene(*many)), lab_image.render_scene(scene(*many[:63])))


class TimeTests(unittest.TestCase):
    SHAPES = ({'type': 'boite', 'l': .3, 'h': .2, 'p': .25},
              {'type': 'cylindre', 'rayon': .14, 'hauteur': .3, 'axe': 'x'},
              {'type': 'cylindre', 'rayon': .14, 'rayon_haut': .01, 'hauteur': .3, 'axe': 'y'},
              {'type': 'sphere', 'rayon': .15},
              {'type': 'tore', 'rayon': .12, 'epaisseur': .03, 'axe': 'z'})

    def maquette(self, shapes) -> dict:
        return scene(*(piece(f'p{n}', shapes[n % len(shapes)],
                             [-.8 + .32 * (n % 6), .2 + .45 * (n // 6), -.6 + .4 * (n % 4)], lab.COULEURS[n % 4])
                       for n in range(24)))

    def test_a_maquette_of_24_pieces_is_drawn_in_far_less_than_2_seconds(self):
        # Measured on this PC (12/11/2026): 0.08 s for the mix of shapes, 0.2 s for 24 spheres, the slowest shape.
        for name, shapes in (('mix', self.SHAPES), ('spheres', self.SHAPES[3:4])):
            what = self.maquette(shapes)
            start = time.perf_counter()
            data = lab_image.render_scene(what)
            seconds = time.perf_counter() - start
            self.assertLess(seconds, 2.0, f'{name}: {seconds:.2f} s')
            for view in views(what):
                self.assertGreater(int(drawn(view).sum()), 20000)
            self.assertTrue(data.startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
