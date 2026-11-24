"""Vision bench: the fake 4K screen (ecran-4k.bgra, made by faire_image.ps1) reduced by AURORA's own code to several
widths, read by the local vision model with AURORA's own call. For each: the times Ollama reports, the length of the
description and how many of the 12 known texts of the screen it names. Nothing of the real screen is read.
Usage (from backend/): python mesurer_vision.py <dossier de l'image> [tirages]"""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix='aurora-banc-vision-')
os.environ.update(AURORA_HOME=HOME, AURORA_DB_PATH=str(Path(HOME) / 'aurora.db'))
sys.path.insert(0, '.')
import numpy as np  # noqa: E402

from aurora.providers.ollama import ollama  # noqa: E402
from aurora.tools import vision  # noqa: E402

KNOWN = {'23,5 °C': r'23[,.]5', '450 €': r'\b450\b', 'n° 78412': r'78412', '14 h 30': r'14 ?h ?30', 'rapport-orion-v2.pdf': r'rapport-orion-v2',
         '3000 mAh': r'3000', 'Version 4.12.7': r'4\.12\.7', 'Port 8812': r'8812', 'Lina Morel': r'Lina Morel',
         '1 298,00 € (très petit)': r'1[\s  ]?298', 'DS18B20 (très petit)': r'DS18B20', '28 novembre (petit)': r'28 novembre'}
SHORT = ('Voici une capture de l’écran de mon ordinateur. Dis en douze lignes au plus ce qui est affiché : les fenêtres '
         'ouvertes et les textes, nombres et noms lisibles, sans phrase d’introduction.')


async def one(label: str, image: bytes, prompt: str) -> None:
    result = await vision._read(ollama, MODEL, f'{prompt}\n\n{vision.RULES}', image, vision.OPTIONS)
    text = vision.without_reasoning(result.text)
    meta = result.meta
    found = [name for name, pattern in KNOWN.items() if re.search(pattern, text)]
    missing = [name for name in KNOWN if name not in found]
    print(f"{label} · {result.duration_ms / 1000:5.1f} s (chargement {(meta.get('load_ms') or 0) / 1000:.1f}, lecture de l'image "
          f"{(meta.get('prompt_eval_ms') or 0) / 1000:.1f}, écriture {(meta.get('eval_ms') or 0) / 1000:.1f}) · {result.input_tokens} jetons "
          f"lus · {len(text)} caractères{' · COUPÉE' if meta.get('truncated') else ''} · textes retrouvés {len(found)}/12"
          f"{' · manque : ' + ', '.join(missing) if missing else ''}", flush=True)


async def main() -> None:
    global MODEL
    folder, draws = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 2
    raw = np.fromfile(folder / 'ecran-4k.bgra', dtype=np.uint8).reshape(2160, 3840, 4)
    rgb = np.ascontiguousarray(raw[:, :, 2::-1])     # BGRA -> RGB
    MODEL = await vision.vision_model()
    print('modèle de vision :', MODEL)
    for max_side in (1600, 1024, 1920):
        reduced = vision.reduce(rgb, max_side)
        image = vision.png(reduced)
        size = f'{reduced.shape[1]}x{reduced.shape[0]}'
        for draw in range(draws):
            await one(f'{size} ({len(image) // 1024} Ko), consigne actuelle, tirage {draw + 1}', image, vision.SCREEN_QUESTION)
        if max_side == 1600:
            for draw in range(draws):
                await one(f'{size}, consigne courte, tirage {draw + 1}', image, SHORT)


MODEL = ''
asyncio.run(main())
