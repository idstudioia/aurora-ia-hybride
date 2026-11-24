"""Local vision (E2, 9/11/2026): an image, or a capture of the screen asked by a click, read on this PC by a vision
model that Ollama runs. Nothing leaves the PC, and nothing is downloaded (only installed models are used).

Port of Athéna's visionService.ts (read, not modified), with its rules and settings:
- **a model that really sees the image answers, or AURORA says plainly that it cannot**: a text model never
  « describes » an image it has not seen (Athéna's veste VITA described as « un circuit avec des résistances ») ;
- the same order of models: ornith-1.5 first (6/09/2026: no invented reference marks on the INPI plates, where
  qwen2.5vl made up 91), then qwen2.5vl, llava… and gemma3; then any installed model that declares « vision » ;
- the same prompt (describe only what is really seen, read the texts, say what is unreadable), temperature 0.2,
  no reasoning, 8192 tokens of context, the model kept loaded 30 min, the graphics card first and one more try
  on the processor if the card fails (the RX 6750 XT Vulkan « device lost » of August 2026).
The description becomes an ordinary attachment in the interface: data, never an instruction, even when the image
or the screen shows text that gives orders. The screen is captured only when the person clicks the button: no
request, page or document can trigger a capture. The image and the description are never logged or kept.
"""
from __future__ import annotations

import ctypes
import re
import struct
import sys
import zlib

from ..providers.base import ProviderError, config_value

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1600                     # the screen is reduced to this width at most (text stays legible)
TIMEOUT_S = 300.0                   # first load of a 9B vision model included
VISION_MODEL_PRIORITY = ('ornith-1.5', 'ornith', 'qwen2.5vl', 'qwen2-vl', 'llama3.2-vision', 'llava-llama3',
                         'llava', 'minicpm-v', 'moondream', 'gemma3')
OPTIONS = {'temperature': 0.2, 'num_ctx': 8192}   # Athéna: precision, not creativity
KEEP_ALIVE = '30m'

RULES = ('Consignes :\n'
         '- Décris UNIQUEMENT ce que tu vois réellement sur l’image.\n'
         '- Lis les textes, titres, numéros de repère et légendes présents.\n'
         '- Si un élément est illisible ou ambigu, dis-le au lieu de deviner.\n'
         '- Réponds en français.')
IMAGE_QUESTION = 'Décris cette image en détail.'
# 22/11/2026 : « en vingt lignes au plus ». L'écriture de la description était la plus longue des trois étapes d'une
# lecture d'écran (12 s sur 29,6 s sur un faux écran, bien plus sur un écran chargé : 3 500 à 4 000 caractères le
# 9/11). Mesuré sur le même faux écran avec « douze lignes » : 7,2 s d'écriture au lieu de 12, mêmes textes retrouvés.
SCREEN_QUESTION = ('Voici une capture de l’écran de mon ordinateur. Dis en vingt lignes au plus ce qui est affiché : '
                   'les fenêtres et applications ouvertes, les textes, nombres et noms lisibles, les boutons et les '
                   'menus visibles. Pas de phrase d’introduction.')


class VisionError(RuntimeError):
    """A failure said as it is (no vision model, image refused, capture impossible)."""


def without_reasoning(raw: str) -> str:
    """Athéna's sansRaisonnement: drop a model's draft, even one that only closes with </think> (Ornith 1.5)."""
    full = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.I)
    end = full.lower().rfind('</think>')
    useful = full if end == -1 else full[end + len('</think>'):]
    return re.sub(r'</?think>', '', useful, flags=re.I).strip()


def image_kind(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    return None


async def vision_model() -> str | None:
    """The installed vision model to use, or None. AURORA_VISION_MODEL forces one (if it is installed)."""
    from ..providers.ollama import ollama   # lazy: owned by another module
    names = [m['name'] for m in await ollama.models()]
    forced = config_value('AURORA_VISION_MODEL', '')
    if forced and forced in names:
        return forced
    for prefix in VISION_MODEL_PRIORITY:
        match = next((n for n in names if n.lower().startswith(prefix)), None)
        if match:
            return match
    caps = await ollama.model_capabilities(names)
    return next((n for n in names if 'vision' in caps.get(n, [])), None)


async def status() -> dict:
    from ..providers.ollama import ollama
    if not await ollama.available():
        return {'available': False, 'model': '', 'reason': 'Ollama local arrêté'}
    model = await vision_model()
    return {'available': bool(model), 'model': model or '',
            'reason': '' if model else 'aucun modèle de vision installé dans Ollama (rien n’est téléchargé sans ton accord)'}


async def describe(image: bytes, question: str = '', *, max_tokens: int = 1200, rules: str = RULES) -> dict:
    """What a local vision model sees on a PNG or JPEG image, in French. ``rules`` and ``max_tokens`` (23/11/2026)
    are for the page of a PDF made of images, which is copied word for word in its own language and is longer
    than a description (tools/documents.py read_page)."""
    from ..providers.ollama import ollama
    if not image:
        raise VisionError('Image vide.')
    if len(image) > MAX_IMAGE_BYTES:
        raise VisionError(f'Image trop lourde ({MAX_IMAGE_BYTES // (1024 * 1024)} Mo au plus).')
    if image_kind(image) is None:
        raise VisionError('Format d’image non lu : PNG ou JPEG seulement.')
    if not await ollama.available():
        raise VisionError('Ollama local est arrêté : aucune image ne peut être lue (rien n’est envoyé ailleurs).')
    model = await vision_model()
    if not model:
        raise VisionError('Aucun modèle de vision installé dans Ollama : AURORA ne décrit pas une image qu’elle '
                          'ne voit pas. Rien n’est téléchargé sans ton accord.')
    prompt = f'{(question or IMAGE_QUESTION).strip()}\n\n{rules}'
    processor = 'carte graphique'
    try:
        result = await _read(ollama, model, prompt, image, OPTIONS, max_tokens)
    except ProviderError as exc:
        if exc.kind not in ('unavailable', 'connection', 'timeout') or 'non installé' in exc.message_fr:
            raise VisionError(f'Lecture de l’image impossible : {exc.message_fr}') from None
        processor = 'processeur'     # Athéna: the card may fail; one more try on the processor
        try:
            result = await _read(ollama, model, prompt, image, {**OPTIONS, 'num_gpu': 0}, max_tokens)
        except ProviderError as again:
            raise VisionError(f'Lecture de l’image impossible : {again.message_fr}') from None
    text = without_reasoning(result.text)
    if not text:
        raise VisionError(f'Le modèle de vision ({result.model}) n’a rien répondu.')
    # 22/11/2026: where the seconds went — loading the model, reading the image, writing the description (what Ollama
    # reports). Kept with the answer of the route, never logged: they say nothing of what the image shows.
    parts = {name: round(float(result.meta.get(key) or 0) / 1000, 1)
             for name, key in (('load', 'load_ms'), ('image', 'prompt_eval_ms'), ('writing', 'eval_ms'))}
    return {'text': text, 'model': result.model, 'seconds': round(result.duration_ms / 1000, 1),
            'processor': processor, 'truncated': bool(result.meta.get('truncated')), 'timings': parts}


async def _read(ollama, model: str, prompt: str, image: bytes, options: dict, max_tokens: int = 1200):
    return await ollama.chat([{'role': 'user', 'content': prompt}], system='', model=model, max_tokens=max_tokens,
                             timeout=TIMEOUT_S, options=options, images=[image], keep_alive=KEEP_ALIVE)


# ---------------------------------------------------------------- the screen, on a click

def png(rgb) -> bytes:
    """PNG of an RGB uint8 array (h, w, 3), written with zlib: no image library needed."""
    import numpy as np
    h, w, _ = rgb.shape
    rows = np.concatenate([np.zeros((h, 1), dtype=np.uint8), np.ascontiguousarray(rgb).reshape(h, w * 3)], axis=1)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows.tobytes(), 6)) + chunk(b'IEND', b''))


def reduce(rgb, max_side: int = MAX_SIDE):
    """Average whole blocks of pixels until the width fits (text stays more legible than by skipping pixels)."""
    import numpy as np
    h, w, _ = rgb.shape
    factor = -(-w // max_side) if w > max_side else 1
    if factor == 1:
        return rgb
    h2, w2 = h // factor, w // factor
    blocks = rgb[:h2 * factor, :w2 * factor].reshape(h2, factor, w2, factor, 3).astype(np.uint16)
    return blocks.mean(axis=(1, 3)).astype(np.uint8)


def capture_screen() -> tuple[bytes, int, int]:
    """PNG of the main screen (Windows GDI), reduced to MAX_SIDE wide, with its real width and height."""
    if sys.platform != 'win32':
        raise VisionError('La capture d’écran n’est faite que sous Windows.')
    import numpy as np
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for fn, res, args in ((user32.GetDC, wintypes.HDC, [wintypes.HWND]),
                          (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC]),
                          (gdi32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC]),
                          (gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_int, ctypes.c_int]),
                          (gdi32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]),
                          (gdi32.BitBlt, wintypes.BOOL, [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                                         wintypes.DWORD]),
                          (gdi32.GetDIBits, ctypes.c_int, [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]),
                          (gdi32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ]),
                          (gdi32.DeleteDC, wintypes.BOOL, [wintypes.HDC])):
        fn.restype, fn.argtypes = res, args

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                    ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    try:
        user32.SetProcessDPIAware()           # the real pixels, not the 125 % scaled size
    except (AttributeError, OSError):
        pass
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    if width <= 0 or height <= 0:
        raise VisionError('Écran introuvable (session sans bureau ?).')
    screen = user32.GetDC(None)
    if not screen:
        raise VisionError('Accès à l’écran refusé par Windows.')
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        if not gdi32.BitBlt(memory, 0, 0, width, height, screen, 0, 0, 0x00CC0020 | 0x40000000):   # SRCCOPY|CAPTUREBLT
            raise VisionError('Capture de l’écran refusée par Windows.')
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                                  biPlanes=1, biBitCount=32, biCompression=0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0) != height:
            raise VisionError('Lecture des pixels de l’écran impossible.')
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    bgra = np.frombuffer(pixels.raw, dtype=np.uint8).reshape(height, width, 4)
    return png(reduce(bgra[:, :, 2::-1])), width, height
