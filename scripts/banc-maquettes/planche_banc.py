"""Planche du banc des maquettes (11/11/2026) : une ligne par objet, une colonne par variante ; dans chaque case la
maquette vue de trois quarts et de dessus. Une case reste vide quand la maquette manque.

    <python avec Pillow> -I planche_banc.py <sortie.jpg> <dossier des résultats> <variante=titre> [<variante=titre> ...]
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from croquis import ARETES, VUES, volumes  # noqa: E402

OBJETS = [('x_wing', 'X-wing'), ('voiture', 'Voiture'), ('avion', 'Avion de ligne'), ('helicoptere', 'Hélicoptère'),
          ('voilier', 'Voilier'), ('eolienne', 'Éolienne'), ('robot', 'Robot')]
COTE, MARGE, ENTETE = 200, 10, 30
DEUX = ['trois quarts', 'dessus (avant en bas)']


def case(scene: dict | None) -> Image.Image:
    image = Image.new('RGB', (COTE * 2, COTE), (5, 11, 28))
    if scene is None:
        return image
    plume = ImageDraw.Draw(image)
    solides = volumes(scene)
    for n, nom in enumerate(DEUX):
        x0, y0 = n * COTE + COTE / 2, COTE * .8
        plume.line([(n * COTE + 6, y0), ((n + 1) * COTE - 6, y0)], fill=(40, 60, 95))
        for coins, teinte in solides:
            points = [VUES[nom](c) for c in coins]
            for a, b in ARETES:
                plume.line([(x0 + points[a][0] * COTE * .43, y0 - points[a][1] * COTE * .37),
                            (x0 + points[b][0] * COTE * .43, y0 - points[b][1] * COTE * .37)], fill=teinte)
    return image


def main() -> None:
    sortie, dossier = sys.argv[1], Path(sys.argv[2])
    variantes = [v.split('=', 1) for v in sys.argv[3:]]
    try:
        police, petite = ImageFont.truetype('arial.ttf', 16), ImageFont.truetype('arial.ttf', 13)
    except OSError:
        police = petite = ImageFont.load_default()
    gauche = 110
    planche = Image.new('RGB', (gauche + len(variantes) * (COTE * 2 + MARGE) + MARGE,
                                ENTETE + len(OBJETS) * (COTE + MARGE) + MARGE), (9, 16, 36))
    plume = ImageDraw.Draw(planche)
    for k, (_cle, titre) in enumerate(variantes):
        plume.text((gauche + k * (COTE * 2 + MARGE) + 6, 8), titre, fill=(190, 225, 255), font=police)
    for ligne, (objet, nom) in enumerate(OBJETS):
        y = ENTETE + ligne * (COTE + MARGE)
        plume.text((8, y + COTE / 2 - 8), nom, fill=(190, 225, 255), font=petite)
        for k, (cle, _titre) in enumerate(variantes):
            fichier = dossier / cle / f'{objet}.json'
            scene = json.loads(fichier.read_text(encoding='utf-8')) if fichier.is_file() else None
            planche.paste(case(scene), (gauche + k * (COTE * 2 + MARGE), y))
            if scene is not None:
                plume.text((gauche + k * (COTE * 2 + MARGE) + 6, y + COTE - 17), f'{len(scene["pieces"])} pièces',
                           fill=(110, 140, 180), font=petite)
    planche.save(sortie, quality=90)
    print(planche.size)


if __name__ == '__main__':
    main()
