"""Image of a maquette of the laboratoire 3D (12/11/2026, ID Studio IA : « vérifier la forme avec des images »).

``render_scene`` draws a scene ``aurora.scene/1`` as a PNG, to be shown to a model that sees, beside a photo of the
real object: the model compares the two silhouettes and corrects the maquette. The trial of 12/11/2026 showed that
this comparison works, with a drawing made by a trial script that uses Pillow. Pillow is not in AURORA's environment
and nothing is downloaded: this is the same drawing with numpy alone, encoded by ``vision.png`` (zlib).

The image: two views of 420 x 420 side by side on a light background, with no text (their order is fixed, and said
by the message that carries the image). Left: three quarters, from the front and a little above. Right: from the
side, the front of the object to the left. Solid volumes, one tint per piece, each face shaded by its orientation; a
depth buffer per pixel decides what hides what; a dark line follows the outline of each piece and its sharp edges.

The framing is the one of the trial (an object that fits in [-1 ; 1], standing on y = 0) whenever the whole object
fits in it. A taller or larger one is centred and reduced, by the same scale in both views, so that nothing is cut.

Robust on purpose: this draws what it is given and validates nothing (``lab.validate`` does). A piece that cannot
be drawn (unknown shape, a number that is not finite, an unknown parent…) is left out, and nothing raises.
Deterministic: the same scene gives the same bytes. No network, no file, and no import of ``lab`` (which may call
this module).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .vision import png

SIDE = 420                       # one view is SIDE x SIDE pixels; the image is the views side by side
BACKGROUND = (236, 240, 245)
EDGE = (40, 50, 70)              # the line on the outline of a piece and on its sharp edges
# The four colours of the format (lab.COULEURS), as the interface shows them (frontend, labo/scene.ts).
TINTS = {'cyan': (56, 214, 255), 'violet': (167, 123, 255), 'ambre': (255, 181, 71), 'vert': (61, 255, 168)}
OTHER_TINT = (200, 200, 200)     # a colour the format does not have
MAX_PIECES = 64                  # the bound of the format (lab.MAX_PIECES): the pieces beyond it are not drawn

# More sides than the trial had (18, and 8 x 14): no line is drawn between them, they only round the outline.
_ROUND = 32                      # sides of a cylinder, a cone and a ring
_BANDS, _SLICES = 12, 24         # a sphere: bands from pole to pole, slices around
_FAR = 1000.0                    # no number of a scene is that large (the format stops at 20)
_LIGHT = np.array([.35, .8, .5])   # in the frame of the view: from above, a little to the right, towards the eye
_SHADE = .45                     # the tint of a face the light does not reach, as a part of the full tint
_SCALE = SIDE * .40              # the framing of the trial: pixels per unit…
_CENTRE = np.array([0., .55, 0.])  # … the point of the object shown in the middle…
_MIDDLE = SIDE * .52             # … and the row of pixels where it is shown
_MARGIN = 12                     # pixels left free around an object that had to be reduced
_TOUCH = 1e-6                    # pixels: two faces that share an edge both take a pixel that lies on it (no crack)
_NEARER = 1e-7                   # units: of two faces at the same depth, the first one drawn stays


def _turn(rotation: list[float]) -> np.ndarray:
    """The matrix of a rotation [rx, ry, rz] in degrees, in the order x, y, z (like the interface)."""
    rx, ry, rz = (math.radians(v) for v in rotation)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return (np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            @ np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]))


# What a view does to a point of the object: x to the right of the image, y up, z towards the eye. The front of
# the object is towards z positive. Three quarters, then the side (the front to the left).
VIEWS = (_turn([22, 0, 0]) @ _turn([0, -38, 0]), _turn([0, -90, 0]))


# --------------------------------------------------------------------------- the faces of a shape

def _normals(faces: np.ndarray) -> np.ndarray:
    """For flat faces (F, k, 3): a vector along the normal of each, as long as twice its area (zero: no area)."""
    return np.cross(faces, np.roll(faces, -1, axis=1)).sum(axis=1)


def _outwards(faces: np.ndarray, inside: Any = 0.0) -> np.ndarray:
    """The same faces (F, k, 3), each wound so that its normal points away from ``inside`` (one point for all the
    faces, or one per face). The drawing relies on it: a face that turns its back to the eye is not drawn."""
    centre = faces.mean(axis=1)
    back = np.einsum('fi,fi->f', _normals(faces - centre[:, None, :]), centre - inside) < 0
    faces = faces.copy()
    faces[back] = faces[back, ::-1]
    return faces


def _cube() -> np.ndarray:
    """The six faces of the cube of side 1 around the origin."""
    faces = np.zeros((6, 4, 3))
    for k in range(3):
        for n, side in enumerate((-.5, .5)):
            faces[2 * k + n, :, k] = side
            faces[2 * k + n, :, (k + 1) % 3] = (-.5, .5, .5, -.5)
            faces[2 * k + n, :, (k + 2) % 3] = (-.5, -.5, .5, .5)
    return _outwards(faces)


def _ball() -> np.ndarray:
    """The faces of the sphere of radius 1: bands between two parallels, cut in slices (at a pole, triangles)."""
    up = np.linspace(-np.pi / 2, np.pi / 2, _BANDS + 1)[:, None]
    around = (2 * np.pi * np.arange(_SLICES) / _SLICES)[None, :]
    grid = np.stack([np.cos(up) * np.cos(around), np.sin(up) + 0 * around, np.cos(up) * np.sin(around)], axis=-1)
    after = np.roll(grid, -1, axis=1)
    return _outwards(np.stack([grid[:-1], after[:-1], after[1:], grid[1:]], axis=2).reshape(-1, 4, 3))


_CUBE, _BALL = _cube(), _ball()
_CIRCLE = np.stack([np.cos(2 * np.pi * np.arange(_ROUND) / _ROUND), np.sin(2 * np.pi * np.arange(_ROUND) / _ROUND)],
                   axis=1)


def _numbers(value: Any, count: int = 3) -> list[float] | None:
    """``count`` numbers of a scene (finite, never a boolean), or None."""
    if not isinstance(value, (list, tuple)) or len(value) != count:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > _FAR for v in value):
        return None
    return [float(v) for v in value]


def _size(value: Any) -> float | None:
    """A dimension of a shape (a number above zero), or None."""
    found = _numbers([value], 1)
    return found[0] if found and found[0] > 0 else None


def _across(axe: str, along: float, circle: np.ndarray) -> np.ndarray:
    """The points of a circle around the axis of a round shape, at ``along`` on this axis."""
    a, b, s = circle[:, 0], circle[:, 1], np.full(len(circle), along)
    return np.stack({'x': (s, a, b), 'y': (a, s, b), 'z': (a, b, s)}[axe], axis=1)


def _faces(shape: Any) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """The faces of a shape in its own frame, around its pivot: [(points (F, k, 3), surfaces (F,))], every face flat,
    convex and wound outwards. ``surfaces`` numbers (below 8) the smooth surface each face is a part of: the line is
    drawn where the surface changes (the edge of a box, the rim of a cylinder), not between two faces of a round
    side. None: a shape that cannot be drawn. A « maillage » (an imported model) is the box of its size."""
    if not isinstance(shape, dict):
        return None
    kind = shape.get('type')
    if kind in ('boite', 'maillage'):
        size = [_size(shape.get(k)) for k in (('l', 'h', 'p') if kind == 'boite' else ('taille',) * 3)]
        return None if None in size else [(_CUBE * np.array(size), np.arange(6))]
    if kind == 'sphere':
        radius = _size(shape.get('rayon'))
        return None if radius is None else [(_BALL * radius, np.zeros(len(_BALL), dtype=np.intp))]
    axe = shape.get('axe', 'y')
    if kind not in ('cylindre', 'tore') or axe not in ('x', 'y', 'z'):
        return None
    if kind == 'cylindre':   # a cone is a cylinder with a « rayon_haut »
        top = shape.get('rayon_haut')
        low, length = _size(shape.get('rayon')), _size(shape.get('hauteur'))
        high = low if top is None else _size(top)
        if None in (low, high, length):
            return None
        end = -length / 2 if axe == 'x' else length / 2   # the « top » of a cylinder lying along x is at -x (interface)
        base, tip = _across(axe, -end, low * _CIRCLE), _across(axe, end, high * _CIRCLE)
        side = np.stack([base, np.roll(base, -1, axis=0), np.roll(tip, -1, axis=0), tip], axis=1)
        return [(_outwards(side), np.zeros(_ROUND, dtype=np.intp)),       # its round side, one surface
                (_outwards(np.stack([base, tip])), np.array([1, 2]))]     # its two ends
    # A ring, drawn like the trial did: a flat band of square section (the tube of the interface is round).
    big, small = _size(shape.get('rayon')), _size(shape.get('epaisseur'))
    if None in (big, small):
        return None
    corners = [_across(axe, along, radius * _CIRCLE) for radius in (big + small, max(big - small, 1e-4))
               for along in (-small, small)]   # outside then inside, each one below then above
    after = [np.roll(c, -1, axis=0) for c in corners]
    bands = [np.stack([corners[i], after[i], after[j], corners[j]], axis=1)
             for i, j in ((0, 1), (2, 3), (0, 2), (1, 3))]   # the outer side, the inner side, below, above
    core = np.mean(bands[:2], axis=(0, 2))   # the middle of the section, face by face: what « inside » is for a ring
    return [(_outwards(np.concatenate(bands), np.tile(core, (4, 1))), np.repeat(np.arange(4), _ROUND))]


# --------------------------------------------------------------------------- the pieces, in the frame of the object

def _solids(scene: Any) -> list[tuple[list[tuple[np.ndarray, np.ndarray]], tuple[int, int, int]]]:
    """The pieces that can be drawn, as the interface assembles them at rest: for each, its faces in the frame of the
    object and its tint. The position of a piece is relative to its parent, in the parent's frame; its rotation
    turns it and its children; the rest angle of its articulation too; « decalage » moves its shape only."""
    pieces = scene.get('pieces') if isinstance(scene, dict) else None
    frames: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    out = []
    for piece in (pieces if isinstance(pieces, list) else [])[:MAX_PIECES]:
        if not isinstance(piece, dict):
            continue
        parent = piece.get('parent')
        if parent is None or parent == '':
            origin, basis = np.zeros(3), np.eye(3)
        elif isinstance(parent, str) and parent in frames:
            origin, basis = frames[parent]
        else:
            continue   # an unknown parent (or one declared after its child): nowhere to put the piece
        position = _numbers(piece.get('position'))
        rotation = [0.0, 0.0, 0.0] if piece.get('rotation') is None else _numbers(piece.get('rotation'))
        if position is None or rotation is None:
            continue
        pivot, basis = origin + basis @ position, basis @ _turn(rotation)
        joint = piece.get('articulation')
        rest = _numbers([joint.get('repos')], 1) if isinstance(joint, dict) else None
        if rest and joint.get('axe') in ('x', 'y', 'z'):
            basis = basis @ _turn([rest[0] if a == joint['axe'] else 0.0 for a in 'xyz'])
        if isinstance(piece.get('id'), str):
            frames[piece['id']] = (pivot, basis)   # its children are placed even if its own shape cannot be drawn
        shape = piece.get('forme')
        faces = _faces(shape)
        offset = [0.0, 0.0, 0.0] if faces is None or shape.get('decalage') is None else _numbers(shape.get('decalage'))
        if faces is None or offset is None:
            continue
        colour = piece.get('couleur')
        out.append(([((points + offset) @ basis.T + pivot, surfaces) for points, surfaces in faces],
                    TINTS.get(colour, OTHER_TINT) if isinstance(colour, str) else OTHER_TINT))
    return out


def _frames(solids: list) -> list[tuple[float, float, float]]:
    """For each view: the pixels per unit, then the column and the row of pixels where the origin of the object is
    shown. The framing of the trial if every piece fits in it; else the object centred and reduced."""
    middle = [view @ _CENTRE for view in VIEWS]
    trial = [(_SCALE, SIDE / 2 - _SCALE * m[0], _MIDDLE + _SCALE * m[1]) for m in middle]
    if not solids:
        return trial
    points = np.concatenate([p.reshape(-1, 3) for faces, _tint in solids for p, _surfaces in faces])
    boxes = []   # per view: the left, the right, the bottom and the top of the object
    for view in VIEWS:
        flat = points @ view.T
        boxes.append((flat[:, 0].min(), flat[:, 0].max(), flat[:, 1].min(), flat[:, 1].max()))
    if all(min(x + scale * left, y - scale * top) >= _MARGIN
           and max(x + scale * right, y - scale * bottom) <= SIDE - _MARGIN
           for (scale, x, y), (left, right, bottom, top) in zip(trial, boxes)):
        return trial
    largest = max(max(right - left, top - bottom) for left, right, bottom, top in boxes)
    scale = min(_SCALE, (SIDE - 2 * _MARGIN) / max(largest, 1e-9))
    return [(scale, SIDE / 2 - scale * (left + right) / 2, SIDE / 2 + scale * (bottom + top) / 2)
            for left, right, bottom, top in boxes]


# --------------------------------------------------------------------------- the drawing

_PIXELS = np.arange(SIDE) + .5   # the centre of each pixel, along a row or a column


def _outline(surface: np.ndarray, depth: np.ndarray) -> np.ndarray:
    """The pixels of the dark line: where two neighbours do not show the same surface, the one nearer to the eye
    (the line is drawn on the piece, never around it: a silhouette keeps its size)."""
    line = np.zeros(surface.shape, dtype=bool)
    for one, other in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                       ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
        differ = surface[one] != surface[other]
        nearer = depth[one] >= depth[other]
        line[one] |= differ & nearer
        line[other] |= differ & ~nearer
    return line


def _draw(solids: list, view: np.ndarray, scale: float, x0: float, y0: float) -> np.ndarray:
    """One view, (SIDE, SIDE, 3): each face that looks at the eye is filled where it is the nearest one."""
    depth = np.full((SIDE, SIDE), -np.inf)
    shown = np.zeros((SIDE, SIDE), dtype=np.intp)   # the face each pixel shows (0: the background)
    colours, surfaces, count = [np.array([BACKGROUND], dtype=np.uint8)], [np.zeros(1, dtype=np.intp)], 1
    for number, (faces, tint) in enumerate(solids):
        for points, smooth in faces:
            seen = points @ view.T
            centre = seen.mean(axis=1)
            normal = _normals(seen - centre[:, None, :])
            length = np.linalg.norm(normal, axis=1)
            front = np.flatnonzero(normal[:, 2] > 1e-6 * length)   # a closed volume never shows the back of a face
            if not len(front):
                continue
            seen, centre, normal = seen[front], centre[front], normal[front]
            light = np.abs(normal @ _LIGHT) / length[front]
            colours.append(np.minimum(np.outer(_SHADE + (1 - _SHADE) * light, tint), 255).astype(np.uint8))
            surfaces.append(1 + 8 * number + smooth[front])
            x, y, z = x0 + scale * seen[:, :, 0], y0 - scale * seen[:, :, 1], seen[:, :, 2]
            # Each side of a face, as a distance in pixels: positive inside (a face seen from the front turns
            # clockwise on the image). A side without length (the pole of a sphere) keeps every pixel.
            a, b = np.roll(y, -1, axis=1) - y, x - np.roll(x, -1, axis=1)
            size = np.hypot(a, b)
            real = size > 1e-9
            size[~real] = 1.0
            a, b = np.where(real, a / size, 0.0), np.where(real, b / size, 0.0)
            c = np.where(real, -(a * x + b * y), 1.0)
            a, b, c = a[:, :, None, None], b[:, :, None], c[:, :, None]
            # The depth of the face at a pixel: its plane, kept between its nearest and farthest corners.
            slope_x, slope_y = -normal[:, 0] / (normal[:, 2] * scale), normal[:, 1] / (normal[:, 2] * scale)
            level = centre[:, 2] - slope_x * (x0 + scale * centre[:, 0]) - slope_y * (y0 - scale * centre[:, 1])
            slope_x, slope_y, level = slope_x.tolist(), slope_y.tolist(), level.tolist()
            far, near = z.min(axis=1).tolist(), z.max(axis=1).tolist()
            left = np.clip(np.ceil(x.min(axis=1) - .5 - _TOUCH), 0, SIDE).astype(int).tolist()
            right = np.clip(np.floor(x.max(axis=1) - .5 + _TOUCH), -1, SIDE - 1).astype(int).tolist()
            top = np.clip(np.ceil(y.min(axis=1) - .5 - _TOUCH), 0, SIDE).astype(int).tolist()
            bottom = np.clip(np.floor(y.max(axis=1) - .5 + _TOUCH), -1, SIDE - 1).astype(int).tolist()
            for f in range(len(front)):
                if left[f] > right[f] or top[f] > bottom[f]:
                    continue
                columns, rows = slice(left[f], right[f] + 1), slice(top[f], bottom[f] + 1)
                xs, ys = _PIXELS[columns], _PIXELS[rows]
                inside = (a[f] * xs + (b[f] * ys + c[f])[:, :, None]).min(axis=0) >= -_TOUCH
                here = slope_x[f] * xs + (slope_y[f] * ys + level[f])[:, None]
                np.clip(here, far[f], near[f], out=here)
                zone = depth[rows, columns]
                inside &= here > zone + _NEARER
                np.copyto(zone, here, where=inside)
                np.copyto(shown[rows, columns], count + f, where=inside)
            count += len(front)
    image = np.concatenate(colours)[shown]
    image[_outline(np.concatenate(surfaces)[shown], depth)] = EDGE
    return image


def render_scene(scene: dict) -> bytes:
    """The PNG (840 x 420) of a scene ``aurora.scene/1``: on the left the object seen from three quarters (from the
    front, a little above), on the right seen from the side, its front to the left. Never raises: what cannot be
    drawn is left out (a scene without a piece gives the background alone)."""
    solids = _solids(scene)
    image = np.empty((SIDE, SIDE * len(VIEWS), 3), dtype=np.uint8)
    for n, (view, frame) in enumerate(zip(VIEWS, _frames(solids))):
        image[:, n * SIDE:(n + 1) * SIDE] = _draw(solids, view, *frame)
    return png(image)
