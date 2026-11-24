"""Child script of providers/triposr_local.py: one photo -> one 3D shape (.obj), with TripoSR, on the processor.

AURORA never imports this file: it runs in TripoSR's own Python environment (torch, rembg, trimesh), started by
``triposr_local.reconstruct`` with fixed arguments:

    <TripoSR's python> -I triposr_lanceur.py <TripoSR folder> <photo> <shape to write> <resolution>

Nothing is downloaded (the caller forbids the network; the models are already on this PC) and nothing leaves the PC.
The last line printed is one JSON object, in ASCII: the measures of the run, or {"ok": false, "erreur": "..."} when
this photo gives nothing (a French reason, said to the person), or {"ok": false, "panne": "..."} when the run broke.
"""
import json
import sys
import time
import types
from pathlib import Path

MAX_PIXELS = 40_000_000     # a photo, not a decompression bomb
MIN_COVER = 0.002           # below: nothing was found on the photo once its background is removed
MIN_CONTRAST = 2.0          # standard deviation of the grey levels (0 to 255) below which the photo is plain
SMOOTH_PASSES = 10          # passes of plain smoothing on the rebuilt surface (0: the shape as TripoSR made it)


def _smooth(mesh, passes: int) -> int:
    """ID Studio IA, 10/11/2026 (« fais le lissage »): TripoSR's surface comes out crumpled. On ID Studio IA's fighter plane, 8 778
    edges folded by more than 28 degrees; 845 are left after ten passes, each moving every point half-way toward the
    middle of its neighbours (trimesh's plain Laplacian filter, without its volume correction: AURORA resizes the
    shape anyway). Measured that day on four shapes (plane, chair, bike, drone): the volume drops by 2 to 7 %, the
    box by 1 to 4 %, and the thin parts stay (chair legs, drone arms). A true Taubin filter keeps the volume but
    leaves three to fourteen times more folds (reviews/essais-image-vers-3d.md).
    Returns the passes done: 0 when the filter failed, the shape being then left as TripoSR made it."""
    if passes <= 0:
        return 0
    import numpy as np
    import trimesh
    before = mesh.vertices.copy()
    try:
        trimesh.smoothing.filter_laplacian(mesh, lamb=0.5, iterations=passes, volume_constraint=False)
        if not np.isfinite(mesh.vertices).all():
            raise ValueError('des points ne sont plus des nombres')
    except Exception:   # noqa: BLE001 — a shape that could not be smoothed is still a shape
        mesh.vertices = before
        return 0
    return passes


def photo_refusal(seen) -> str | None:
    """Why this photo can give no shape, seen before any model is loaded (French, said to the person), or None.
    Shared with pixal3d_lanceur.py (10/11/2026): the two engines refuse the same photos, in the same words."""
    import numpy as np
    if seen.width * seen.height > MAX_PIXELS:
        return 'photo trop grande (plus de 40 millions de points)'
    # A plain image (all white, all black): the cut-out would still keep a blob in its middle (measured: 52 % of a
    # white image), and TripoSR would make a box of it.
    if float(np.asarray(seen.convert('L'), dtype=np.float32).std()) < MIN_CONTRAST:
        return 'la photo est unie, il n’y a aucun objet à reconstruire'
    return None


def _marching_cubes() -> str:
    """TripoSR asks for ``torchmcubes``, which could not be compiled on this Windows PC (10/11/2026): the same
    function is then given by PyMCubes, on the processor. torchmcubes returns the points in the (z, y, x) order of
    the volume; TripoSR puts them back in (x, y, z) afterwards."""
    try:
        import torchmcubes  # noqa: F401
        return 'torchmcubes'
    except Exception:   # noqa: BLE001
        import mcubes
        import numpy as np
        import torch

        def marching_cubes(volume, threshold):
            points, faces = mcubes.marching_cubes(volume.detach().cpu().numpy(), threshold)
            return (torch.from_numpy(np.ascontiguousarray(points[:, ::-1]).astype(np.float32)),
                    torch.from_numpy(faces.astype(np.int64)))

        module = types.ModuleType('torchmcubes')
        module.marching_cubes = marching_cubes
        sys.modules['torchmcubes'] = module
        return 'PyMCubes'


def run(depot: Path, photo: Path, sortie: Path, resolution: int) -> dict:
    sys.path.insert(0, str(depot))
    cubes = _marching_cubes()
    import numpy as np
    import rembg
    import torch
    import trimesh
    from PIL import Image
    from tsr.system import TSR
    from tsr.utils import remove_background, resize_foreground

    seen = Image.open(photo)
    refusal = photo_refusal(seen)
    if refusal:
        return {'ok': False, 'erreur': refusal}
    t = time.perf_counter()
    model = TSR.from_pretrained('stabilityai/TripoSR', config_name='config.yaml', weight_name='model.ckpt')
    model.renderer.set_chunk_size(8192)
    model.to('cpu')
    session = rembg.new_session()
    chargement = time.perf_counter() - t

    t = time.perf_counter()
    cut = remove_background(seen.convert('RGB'), session)
    cover = float((np.array(cut)[:, :, 3] > 127).mean())   # the share of the photo kept as « the object »
    if cover < MIN_COVER:
        return {'ok': False, 'erreur': 'aucun objet n’a été trouvé sur la photo'}
    cut = resize_foreground(cut, 0.85)
    a = np.array(cut).astype(np.float32) / 255.0
    a = a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5
    view = Image.fromarray((a * 255.0).astype(np.uint8))
    detourage = time.perf_counter() - t

    t = time.perf_counter()
    with torch.no_grad():
        codes = model([view], device='cpu')
    modele = time.perf_counter() - t

    t = time.perf_counter()
    mesh = model.extract_mesh(codes, False, resolution=resolution)[0]
    if len(mesh.faces) < 4:
        return {'ok': False, 'erreur': 'TripoSR n’a tiré aucune forme de cette photo'}
    lissage = _smooth(mesh, SMOOTH_PASSES)
    # TripoSR's own frame (z up, the photographed side along x) -> y up, the photographed side toward the viewer:
    # the two turns TripoSR itself applies before showing a shape (tsr.utils.to_gradio_3d_orientation).
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    mesh.export(str(sortie), file_type='obj')
    maillage = time.perf_counter() - t
    return {'ok': True, 'sommets': int(len(mesh.vertices)), 'triangles': int(len(mesh.faces)),
            'couverture': round(cover, 3), 'lissage': lissage,
            'chargement_s': round(chargement, 1), 'detourage_s': round(detourage, 1),
            'modele_s': round(modele, 1), 'maillage_s': round(maillage, 1), 'cubes': cubes,
            'fils': int(torch.get_num_threads())}


def main(argv: list[str]) -> int:
    try:
        depot, photo, sortie, resolution = Path(argv[0]), Path(argv[1]), Path(argv[2]), int(argv[3])
        result = run(depot, photo, sortie, resolution)
    except Exception as exc:   # noqa: BLE001 — « panne »: for the service's log; « erreur »: said to the person
        result = {'ok': False, 'panne': f'{type(exc).__name__}: {exc}'[:400]}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
