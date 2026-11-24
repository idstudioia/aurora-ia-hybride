"""Croquis en fil de fer de scènes « aurora.scene/1 » (11/11/2026) : trois quarts, face et dessus, pour juger d'un coup
d'œil si les pièces d'une maquette sont à leur place. N'utilise que Pillow : aucune ligne d'AURORA.

    <python avec Pillow> -I croquis.py <sortie.png> <scene.json> [<scene.json> ...]      une ligne par scène
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TEINTES = {'cyan': (56, 214, 255), 'violet': (167, 123, 255), 'ambre': (255, 181, 71), 'vert': (61, 255, 168)}
ARETES = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
VUES = {
    'trois quarts': lambda p: (.78 * p[0] - .62 * p[2], p[1] + .28 * (.62 * p[0] + .78 * p[2])),
    'face': lambda p: (p[0], p[1]),
    'dessus (avant en bas)': lambda p: (p[0], -p[2] + 1.0),
}


def demi_tailles(forme: dict) -> list[float]:
    """La demi-étendue d'une forme selon x, y et z (comme tools/lab._half_sizes)."""
    genre, axe = forme['type'], forme.get('axe', 'y')
    if genre == 'boite':
        return [forme['l'] / 2, forme['h'] / 2, forme['p'] / 2]
    if genre == 'sphere':
        return [forme['rayon']] * 3
    if genre == 'maillage':
        return [forme['taille'] / 2] * 3
    if genre == 'cylindre':
        rayon, long = max(forme['rayon'], forme.get('rayon_haut') or 0), forme['hauteur'] / 2
    else:
        rayon, long = forme['rayon'] + forme['epaisseur'], forme['epaisseur']
    return [long if a == axe else rayon for a in 'xyz']


def produit(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def appliquer(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def tourner(rotation):
    """La matrice d'une rotation [rx, ry, rz] en degrés, dans l'ordre x, y, z (comme l'interface)."""
    rx, ry, rz = (math.radians(v) for v in (rotation or [0, 0, 0]))
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return produit(produit([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]),
                   [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])


def volumes(scene: dict) -> list[tuple[list[list[float]], tuple]]:
    """Pour chaque pièce : ses huit coins dans le repère de l'objet (un cône : un bout resserré), et sa teinte."""
    places: dict[str, tuple[list[float], list[list[float]]]] = {}
    vus = []
    for piece in scene['pieces']:
        origine, repere = places.get(piece.get('parent') or '', ([0.0, 0.0, 0.0], tourner(None)))
        pivot = [origine[i] + appliquer(repere, piece['position'])[i] for i in range(3)]
        repere = produit(repere, tourner(piece.get('rotation')))
        places[piece['id']] = (pivot, repere)
        forme = piece['forme']
        demi = demi_tailles(forme)
        decalage = forme.get('decalage') or [0.0, 0.0, 0.0]
        coins = [[a * demi[0], b * demi[1], c * demi[2]] for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]
        haut = forme.get('rayon_haut')
        if forme['type'] == 'cylindre' and haut is not None and haut != forme['rayon']:
            k = 'xyz'.index(forme.get('axe', 'y'))
            bout = (-1 if k == 0 else 1) * (1 if haut < forme['rayon'] else -1)   # le côté du petit bout
            mince = min(forme['rayon'], haut) / max(forme['rayon'], haut)
            coins = [[v if i == k or (c[k] > 0) != (bout > 0) else v * mince for i, v in enumerate(c)] for c in coins]
        vus.append(([[pivot[i] + appliquer(repere, [c[j] + decalage[j] for j in range(3)])[i] for i in range(3)]
                     for c in coins], TEINTES.get(piece['couleur'], (200, 200, 200))))
    return vus


def croquis(scene: dict, titre: str, cote: int = 250) -> Image.Image:
    image = Image.new('RGB', (cote * len(VUES), cote + 26), (5, 11, 28))
    plume = ImageDraw.Draw(image)
    try:
        police = ImageFont.truetype('arial.ttf', 13)
    except OSError:
        police = ImageFont.load_default()
    plume.text((6, 5), titre[:120], fill=(190, 225, 255), font=police)
    solides = volumes(scene)
    for n, (nom, vue) in enumerate(VUES.items()):
        x0, y0 = n * cote + cote / 2, 26 + cote * .82
        plume.line([(n * cote + 8, y0), ((n + 1) * cote - 8, y0)], fill=(40, 60, 95))
        plume.text((n * cote + 6, 26 + cote - 16), nom, fill=(110, 140, 180), font=police)
        for coins, teinte in solides:
            points = [vue(c) for c in coins]
            for a, b in ARETES:
                plume.line([(x0 + points[a][0] * cote * .42, y0 - points[a][1] * cote * .36),
                            (x0 + points[b][0] * cote * .42, y0 - points[b][1] * cote * .36)], fill=teinte)
    return image


def main() -> None:
    lignes = []
    for chemin in sys.argv[2:]:
        fichier = Path(chemin)
        if not fichier.is_file():
            continue
        scene = json.loads(fichier.read_text(encoding='utf-8'))
        titre = scene.get('_banc') or scene['titre']
        lignes.append(croquis(scene, f'{titre} | {len(scene["pieces"])} pièces'))
    if not lignes:
        sys.exit('aucune scène à dessiner')
    planche = Image.new('RGB', (lignes[0].width, sum(l.height for l in lignes)), (5, 11, 28))
    y = 0
    for ligne in lignes:
        planche.paste(ligne, (0, y))
        y += ligne.height
    planche.save(sys.argv[1])
    print(planche.size)


if __name__ == '__main__':
    main()
