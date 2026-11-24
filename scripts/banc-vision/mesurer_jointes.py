"""The same fake screen as a joined image, at 1600, 1280 and 1024 points wide, read by the local vision model with
AURORA's own call and its question for an image.  Usage (from backend/): python mesurer_jointes.py <dossier>"""
import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix='aurora-banc-vision-')
os.environ.update(AURORA_HOME=HOME, AURORA_DB_PATH=str(Path(HOME) / 'aurora.db'))
sys.path.insert(0, '.')
from aurora.providers.ollama import ollama  # noqa: E402
from aurora.tools import vision  # noqa: E402

KNOWN = {'23,5 °C': r'23[,.]5', '450 €': r'\b450\b', 'n° 78412': r'78412', '14 h 30': r'14 ?h ?30', 'rapport-orion-v2.pdf': r'rapport-orion-v2',
         '3000 mAh': r'3000', 'Version 4.12.7': r'4\.12\.7', 'Port 8812': r'8812', 'Lina Morel': r'Lina Morel',
         '1 298,00 € (très petit)': r'1[\s  ]?298', 'DS18B20 (très petit)': r'DS18B20', '28 novembre (petit)': r'28 novembre'}


async def main() -> None:
    folder = Path(sys.argv[1])
    model = await vision.vision_model()
    for name in ('jointe-1600.png', 'jointe-1280.png', 'jointe-1024.png'):
        image = (folder / name).read_bytes()
        result = await vision._read(ollama, model, f'{vision.IMAGE_QUESTION}\n\n{vision.RULES}', image, vision.OPTIONS)
        text, meta = vision.without_reasoning(result.text), result.meta
        found = [k for k, pattern in KNOWN.items() if re.search(pattern, text)]
        print(f"{name} · {result.duration_ms / 1000:5.1f} s (chargement {(meta.get('load_ms') or 0) / 1000:.1f}, lecture de l'image "
              f"{(meta.get('prompt_eval_ms') or 0) / 1000:.1f}, écriture {(meta.get('eval_ms') or 0) / 1000:.1f}) · {result.input_tokens} jetons "
              f"lus · {len(text)} caractères · textes retrouvés {len(found)}/12 · manque : "
              f"{', '.join(k for k in KNOWN if k not in found) or 'rien'}", flush=True)


asyncio.run(main())
