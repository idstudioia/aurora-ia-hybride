"""Child script of providers/pixal3d_local.py: what Pixal3D needs before and after its own program (10/11/2026).

AURORA never imports this file: it runs in TripoSR's Python environment (rembg, trimesh, numpy, PIL), started by
``pixal3d_local.reconstruct`` with fixed arguments, once before trellis.cpp's program and once after:

    <TripoSR's python> -I pixal3d_lanceur.py preparer <photo> <image to write, PNG>
        the photo without its background: the cut-out and the refusals of TripoSR's runner (triposr_lanceur.py);
    <TripoSR's python> -I pixal3d_lanceur.py finir <raw model, GLB> <model to write, GLB> <faces at most>
        the model put the way AURORA shows a shape (y up, the photographed side toward the viewer), made lighter,
        then stood upright.

It starts no program, downloads nothing (the caller forbids the network) and writes nothing but the file it is asked
for. The last line printed is one JSON object, in ASCII, as triposr_lanceur.py does: the measures of the step, or
{"ok": false, "erreur": "..."} when this photo gives nothing (a French reason, said to the person), or
{"ok": false, "panne": "..."} when the step broke.

The pose, the lightening and the levelling come from the trials of 10/11/2026 with ID Studio IA (« corrige les défauts »,
« la génération se fait souvent pencher », « passe plutôt à 320000 faces »): reviews/essais-image-vers-3d.md.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

# trellis.cpp writes its models in its own frame. Rows: the x, y, z of AURORA. Found on ID Studio IA's fighter plane by two
# measures that agree: compared in volume with the model of Pixal3D's online demonstration (overlap 0.61 for this
# pose, 0.12 for the next one), then by its outline seen from the front (0.525).
POSE = ((1, 0, 0), (0, 0, -1), (0, 1, 0))
GRIDS = (448, 384, 320, 288, 256, 224, 192, 160, 128, 96, 64)   # finest first: the first that is light enough is kept
ROUND = 1.3          # longest spread / shortest spread below which a shape is too round to have a « top »
UP, TOWARD = 0.2, -0.2   # a « top » points up in the image, and never to the back of the scene


def _shared():
    """TripoSR's runner, loaded from its file next to this one (``-I`` leaves this folder out of the import path,
    and putting it there would let its other files hide real libraries): the same refusals for the two engines."""
    spec = importlib.util.spec_from_file_location('triposr_lanceur', Path(__file__).with_name('triposr_lanceur.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(photo: Path, sortie: Path) -> dict:
    """The photo, its background removed, written as a PNG with transparency: Pixal3D's program keeps a
    transparency it is given, and looks only at what is left."""
    import numpy as np
    from PIL import Image

    shared = _shared()
    seen = Image.open(photo)
    refusal = shared.photo_refusal(seen)
    if refusal:
        return {'ok': False, 'erreur': refusal}
    t = time.perf_counter()
    if seen.mode == 'RGBA' and seen.getextrema()[3][0] < 255:
        cut, removed = seen, False            # its background is already removed: kept as it is
    else:
        import rembg
        cut, removed = rembg.remove(seen.convert('RGB'), session=rembg.new_session()), True
    cover = float((np.asarray(cut)[:, :, 3] > 127).mean())   # the share of the photo kept as « the object »
    if cover < shared.MIN_COVER:
        return {'ok': False, 'erreur': 'aucun objet n’a été trouvé sur la photo'}
    cut.save(str(sortie), format='PNG')
    return {'ok': True, 'couverture': round(cover, 3), 'detoure': removed, 'largeur': int(cut.width),
            'hauteur': int(cut.height), 'detourage_s': round(time.perf_counter() - t, 1)}


def lighten(mesh, target: int):
    """(the mesh with ``target`` faces at most, the grid used) — (the mesh as it is, 0) when it is light enough.
    At its 512 fineness Pixal3D does not simplify its shape: more than a million faces. The points are gathered by
    small cells (each cell keeps the middle of its points), on a coarser and coarser grid, until the faces left fit.
    Measured on ID Studio IA's plane: 1 093 856 faces -> 288 041 on the 256 grid, in 2 s."""
    import numpy as np
    import trimesh

    v, f = np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces)
    if len(f) <= target:
        return mesh, 0
    lo = v.min(axis=0)
    span = float((v.max(axis=0) - lo).max()) or 1.0
    nv, nf, grid = v, f, 0
    for grid in GRIDS:
        q = np.floor((v - lo) / span * grid).astype(np.int64)
        key = (q[:, 0] * (grid + 1) + q[:, 1]) * (grid + 1) + q[:, 2]
        _, group, count = np.unique(key, return_inverse=True, return_counts=True)
        group = group.reshape(-1)
        nv = np.zeros((len(count), 3))
        np.add.at(nv, group, v)
        nv /= count[:, None]
        nf = group[f]
        nf = nf[(nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])]   # faces crushed to a line
        _, kept = np.unique(np.sort(nf, axis=1), axis=0, return_index=True)                 # the same face twice
        nf = nf[np.sort(kept)]
        if len(nf) <= target:
            break
    light = trimesh.Trimesh(nv, nf, process=False)
    light.remove_unreferenced_vertices()
    return light, grid


def upright(mesh):
    """(the rotation that stands the shape upright, the angle it corrects in degrees) — (None, 0.0) when nothing
    says where its top is (a shape too round, or no axis that fits).

    ID Studio IA, 10/11/2026: « la génération se fait souvent pencher ». The model takes the angle of the image: an object
    drawn three-quarter comes out leaning toward the viewer. The three own axes of the shape (where it is longest,
    widest, thinnest) are the candidates for « the top of the object ». An image is nearly always taken from a
    little above: the true top points up in the image AND a little toward the viewer, never to the back. Among the
    six directions, those that respect this are kept, then the most vertical one. The shape turns by just what it
    takes for that direction to be the vertical: it keeps the side it showed.
    Measured that day: 21.5 degrees corrected on the plane, 45.5 on the satellite. Not tried on a tall narrow
    object."""
    import numpy as np
    import trimesh

    points, _ = trimesh.sample.sample_surface(mesh, 120_000, seed=5)
    values, axes = np.linalg.eigh(np.cov((points - points.mean(axis=0)).T))
    if values[2] / max(float(values[0]), 1e-12) < ROUND:
        return None, 0.0
    fits = [c for c in (sign * axes[:, i] for i in range(3) for sign in (1.0, -1.0)) if c[1] >= UP and c[2] >= TOWARD]
    if not fits:
        return None, 0.0
    top = max(fits, key=lambda c: c[1])
    cosine = float(np.clip(top[1], -1.0, 1.0))
    axis = np.cross(top, [0.0, 1.0, 0.0])
    sine = float(np.linalg.norm(axis))
    if sine < 1e-8:
        return np.eye(3), 0.0
    k = axis / sine
    cross = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + sine * cross + (1 - cosine) * (cross @ cross), float(np.degrees(np.arccos(cosine)))


def finish(raw: Path, sortie: Path, target: int) -> dict:
    """The raw model of the program -> the model AURORA keeps: posed, lightened, upright, written as a GLB."""
    import numpy as np
    import trimesh

    t = time.perf_counter()
    mesh = trimesh.load(str(raw), force='mesh', process=False)
    before = int(len(mesh.faces))
    if before < 4:
        return {'ok': False, 'erreur': 'Pixal3D n’a tiré aucune forme de cette photo'}
    mesh.vertices = np.asarray(mesh.vertices, dtype=np.float64) @ np.array(POSE, dtype=float).T
    light, grid = lighten(mesh, target)
    turn, angle = upright(light)
    if turn is not None:
        light.vertices = np.asarray(light.vertices) @ turn.T
    if not np.isfinite(np.asarray(light.vertices)).all():
        return {'ok': False, 'panne': 'des points du modele ne sont plus des nombres'}
    light.export(str(sortie), file_type='glb')
    return {'ok': True, 'faces_avant': before, 'triangles': int(len(light.faces)), 'sommets': int(len(light.vertices)),
            'grille': int(grid), 'aplomb_deg': None if turn is None else round(angle, 1),
            'finition_s': round(time.perf_counter() - t, 1)}


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[0] == 'preparer':
            result = prepare(Path(argv[1]), Path(argv[2]))
        elif len(argv) == 4 and argv[0] == 'finir':
            result = finish(Path(argv[1]), Path(argv[2]), int(argv[3]))
        else:
            result = {'ok': False, 'panne': 'arguments inattendus'}
    except Exception as exc:   # noqa: BLE001 — « panne »: for the service's log; « erreur »: said to the person
        result = {'ok': False, 'panne': f'{type(exc).__name__}: {exc}'[:400]}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
