"""Text of a joined PDF or Word file (E4, 29/10/2026): the user joins mostly these, rarely a CSV.

The interface sends the file's bytes to POST /api/attachments/extract; the text that comes back becomes an ordinary
attachment, so everything after is unchanged: marked as data, never an instruction, checked for sensitive data,
read by doc.analyze. Nothing is stored and nothing leaves the PC.
- Word (.docx) is a zip of XML: read with zipfile and ElementTree, any DOCTYPE/ENTITY refused (as for the feeds),
  the uncompressed part capped (no zip bomb). Old .doc files are refused with how to convert them.
- PDF: pypdf (BSD-3, pure Python, no network). A password-protected PDF is refused; a PDF with no text at all
  (scanned, or printed as drawings) raises NoText: its pages are then read one by one as images (read_page, below).
Long texts are cut at config.MAX_ATTACHMENT_CHARS, and the cut is said (pages read, characters kept).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import config
from .news import _CDATA, _FORBIDDEN

log = logging.getLogger(__name__)

MAX_BYTES = 15 * 1024 * 1024
MAX_XML_BYTES = 40 * 1024 * 1024      # uncompressed word/document.xml
MAX_PAGES = 300
KINDS = {'.pdf': 'pdf', '.docx': 'docx'}
OLD_WORD = ('Ancien format Word (.doc) : ouvrez-le dans Word et enregistrez-le en .docx ou en PDF '
            '(Fichier › Enregistrer sous), puis joignez-le.')

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def kind_of(name: str) -> str | None:
    low = (name or '').lower()
    return next((kind for ext, kind in KINDS.items() if low.endswith(ext)), None)


def _docx_paragraph(p: ET.Element) -> str:
    out = []
    for el in p.iter():
        tag = el.tag
        if tag == f'{_W}t' and el.text:
            out.append(el.text)
        elif tag == f'{_W}tab':
            out.append('\t')
        elif tag in (f'{_W}br', f'{_W}cr'):
            out.append('\n')
    return ''.join(out).strip()


def _docx_text(content: bytes) -> tuple[str, int | None]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise ValueError('Fichier Word illisible : ce n’est pas un .docx valide (protégé par un mot de passe, '
                         'ou ancien format .doc renommé ?).') from None
    with archive:
        try:
            info = archive.getinfo('word/document.xml')
        except KeyError:
            raise ValueError('Fichier Word illisible : contenu principal absent.') from None
        if info.file_size > MAX_XML_BYTES:
            raise ValueError('Fichier Word trop volumineux une fois décompressé : il n’a pas été lu.')
        with archive.open(info) as part:
            xml = part.read(MAX_XML_BYTES + 1)
    if len(xml) > MAX_XML_BYTES:
        raise ValueError('Fichier Word trop volumineux une fois décompressé : il n’a pas été lu.')
    if _FORBIDDEN.search(_CDATA.sub(b'', xml)):
        raise ValueError('Fichier Word refusé : déclaration DOCTYPE/ENTITY interdite.')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('Fichier Word illisible (XML invalide).') from None
    body = root.find(f'{_W}body')
    blocks: list[str] = []
    for el in (body if body is not None else []):
        if el.tag == f'{_W}p':
            text = _docx_paragraph(el)
            if text:
                blocks.append(text)
        elif el.tag == f'{_W}tbl':   # a table: one line per row, cells separated by « | »
            for row in el.iter(f'{_W}tr'):
                cells = [' '.join(_docx_paragraph(p) for p in cell.iter(f'{_W}p')).strip()
                         for cell in row.iter(f'{_W}tc')]
                if any(cells):
                    blocks.append(' | '.join(cells))
    return '\n\n'.join(blocks), None


def _pdf_text(content: bytes) -> tuple[str, int | None, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('PDF protégé par un mot de passe : enregistrez-en une copie sans protection, puis '
                             'joignez-la.')
        pages = list(reader.pages)
    except ValueError:
        raise
    except (PdfReadError, OSError, KeyError, TypeError, AttributeError, NotImplementedError):
        raise ValueError('PDF illisible ou abîmé : il n’a pas été lu.') from None
    except Exception as exc:   # pypdf raises its own errors for crypto it cannot handle
        if 'crypt' in type(exc).__name__.lower() or 'crypt' in str(exc).lower():
            raise ValueError('PDF chiffré d’une façon que AURORA ne sait pas ouvrir : enregistrez-en une copie '
                             'sans protection.') from None
        raise ValueError('PDF illisible ou abîmé : il n’a pas été lu.') from None
    blocks, read = [], 0
    for number, page in enumerate(pages[:MAX_PAGES], start=1):
        try:
            text = page.extract_text() or ''
        except Exception:
            text = ''
        read = number
        text = re.sub(r'[ \t]+\n', '\n', text).strip()
        if text:
            blocks.append(f'[Page {number}]\n{text}')
    return '\n\n'.join(blocks), len(pages), read


def extract(name: str, content: bytes) -> dict:
    """{name, kind, text, pages, pages_read, chars, truncated, notes}. ValueError (French) when the file cannot
    become text: unknown or old format, too big, protected, damaged, or a scan without text."""
    low = (name or '').lower()
    if low.endswith('.doc'):
        raise ValueError(OLD_WORD)
    kind = kind_of(low)
    if kind is None:
        raise ValueError('Format non pris en charge ici : seuls les PDF et les fichiers Word (.docx) sont lus.')
    if not content:
        raise ValueError('Fichier vide.')
    if len(content) > MAX_BYTES:
        raise ValueError(f'Fichier trop volumineux ({len(content) // (1024 * 1024)} Mo, {MAX_BYTES // (1024 * 1024)} Mo '
                         'au plus).')
    pages = read = None
    if kind == 'docx':
        text, pages = _docx_text(content)
    else:
        text, pages, read = _pdf_text(content)
    text = text.replace('\x00', '').strip()
    notes: list[str] = []
    visible = len(re.sub(r'\[Page \d+\]|\s', '', text))
    if kind == 'pdf' and not visible:
        raise NoText('Ce PDF ne contient pas de texte lisible (document scanné, ou fait d’images).', pages or 0)
    if not text:
        raise ValueError('Ce document ne contient pas de texte.')
    if kind == 'pdf' and pages and pages > 1 and visible < 15 * pages:
        # a one-line PDF is fine; many pages with almost no text are probably scanned (29/10/2026: a short PDF
        # was refused as a scan)
        notes.append(f'Très peu de texte trouvé ({visible} caractères sur {pages} pages) : certaines pages sont '
                     'peut-être scannées. Leur image n’est pas lue : seul un PDF sans aucun texte est lu page par '
                     'page par le modèle de vision.')
    if kind == 'pdf' and pages and read is not None and read < pages:
        notes.append(f'Seules les {read} premières pages sur {pages} ont été lues.')
    total = len(text)
    truncated = total > config.MAX_ATTACHMENT_CHARS
    if truncated:
        cut = text[:config.MAX_ATTACHMENT_CHARS]
        last_page = re.findall(r'\[Page (\d+)\]', cut)
        text = cut.rsplit('\n', 1)[0] if '\n' in cut else cut
        where = f', jusqu’à la page {last_page[-1]}' if last_page and pages else ''
        notes.append(f'Texte coupé à {config.MAX_ATTACHMENT_CHARS:,} caractères sur {total:,}{where}.'.replace(',', ' '))
    return {'name': name, 'kind': kind, 'text': text, 'pages': pages, 'pages_read': read, 'chars': len(text),
            'total_chars': total, 'truncated': truncated, 'notes': notes}


# ---------------------------------------------------------------- a PDF made of images, read page by page
# 23/11/2026. ID Studio IA joined an article of 13 pages printed by « Microsoft: Print To PDF »: no text in it, only drawings,
# and AURORA refused it (« j'arrive pas à le joindre au chat »). Each page is now drawn as an image by Windows' own
# PDF reader (pdf_page.ps1: nothing to install, the PDF is only read), then read on this PC by the local vision model
# (tools/vision.py). What comes back is a model's reading of the page, not the page's text: the interface says so
# with the attachment. Measured on that article (RX 6750 XT, ornith-1.5:9b), pages 1, 5 and 9 compared by eye with
# what the model wrote: 2 s to draw a page; read at 1280 points in 21 to 25 s, every number and the table right,
# three small words changed on page 1 (« Laissez » for « Laissé »); at 1600 points, 35 to 39 s for the same text.

PAGE_SIDE = 1280              # longest side of the page image, in pixels: the size AURORA gives its vision model
MAX_VISION_PAGES = 30         # about 12 minutes at 25 s a page; the person can stop before
PAGE_TOKENS = 2000            # a full page is 600 to 1 000 tokens; a description stops at 1 200
DRAW_TIMEOUT_S = 60
PAGE_QUESTION = ('Voici une page d’un document. Recopie tout le texte de cette page, mot pour mot, dans l’ordre '
                 'de lecture.')
PAGE_RULES = ('Consignes :\n'
              '- Ne résume pas, n’ajoute rien, ne commente pas et ne traduis pas : garde la langue de la page.\n'
              '- Écris [illisible] à la place d’un mot que tu ne peux pas lire, au lieu de deviner.\n'
              '- Recopie un tableau ligne par ligne, les cases séparées par « | ».\n'
              '- Pour une figure, une photo ou un schéma, dis en une ligne ce qu’il montre, entre crochets.\n'
              '- Si la page ne porte aucun texte, réponds seulement : [page sans texte].')
_PAGE_SCRIPT = Path(__file__).with_name('pdf_page.ps1')


class NoText(ValueError):
    """A PDF whose pages hold no text at all (scanned, or printed as drawings). ``pages`` is how many it has; they
    can be read one by one as images (read_page)."""

    def __init__(self, message: str, pages: int):
        super().__init__(message)
        self.pages = pages


class PageError(ValueError):
    """A page that could not be drawn as an image, said in French."""


def _powershell() -> Path:
    """Windows PowerShell by its full path (never the first « powershell » found on the PATH)."""
    return Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'


def page_reader() -> str:
    """'' when this PC can draw the page of a PDF as an image, else why it cannot (French)."""
    if sys.platform != 'win32':
        return 'les pages d’un PDF ne sont dessinées en images que sous Windows'
    if not _powershell().is_file() or not _PAGE_SCRIPT.is_file():
        return 'le lecteur PDF de Windows n’a pas été trouvé'
    return ''


def page_image(content: bytes, page: int, side: int = PAGE_SIDE) -> tuple[bytes, int]:
    """(JPEG of page ``page``, counted from 1, its longest side ``side`` pixels; number of pages of the PDF), drawn
    by Windows' own PDF reader through the fixed script pdf_page.ps1. The PDF is written in a temporary folder of
    AURORA, removed at the end. Blocking (about 2 s): call it in a thread. PageError (French) otherwise."""
    why = page_reader()
    if why:
        raise PageError(f'Cette page n’a pas pu être dessinée : {why}.')
    if not content or len(content) > MAX_BYTES:
        raise PageError(f'PDF vide ou trop volumineux ({MAX_BYTES // (1024 * 1024)} Mo au plus).')
    page, side = int(page), max(200, min(int(side), 2400))
    work = Path(tempfile.mkdtemp(prefix='aurora-pdf-'))
    try:
        pdf, image = work / 'document.pdf', work / 'page.jpg'
        pdf.write_bytes(content)
        try:
            done = subprocess.run(
                [str(_powershell()), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 str(_PAGE_SCRIPT), '-Pdf', str(pdf), '-Image', str(image), '-Page', str(page), '-Side', str(side)],
                cwd=str(work), stdin=subprocess.DEVNULL, capture_output=True, timeout=DRAW_TIMEOUT_S,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
        except subprocess.TimeoutExpired:
            raise PageError(f'Windows n’a pas dessiné la page {page} en {DRAW_TIMEOUT_S} s : elle n’a pas été '
                            'lue.') from None
        except OSError as exc:
            log.warning('lecteur PDF de Windows non lancé : %s', exc)
            raise PageError('Le lecteur PDF de Windows n’a pas pu être lancé sur ce PC.') from None
        result: dict = {}
        for line in reversed(done.stdout.decode('utf-8', 'replace').splitlines()):
            try:
                found = json.loads(line)
            except ValueError:
                continue
            if isinstance(found, dict):
                result = found
                break
        if not result.get('ok'):
            if result.get('reason') == 'page':
                raise PageError(f'Ce PDF a {result.get("pages")} page(s) : pas de page {page}.')
            # Windows' own words (their language and detail vary) go to the service's log, not to the person
            log.warning('page de PDF non dessinée (code %s) : %s | %s', done.returncode, result.get('detail'),
                        done.stderr.decode('utf-8', 'replace')[-600:])
            raise PageError('Windows n’a pas pu ouvrir ce PDF pour en dessiner les pages (protégé par un mot de '
                            'passe, ou abîmé ?).')
        try:
            return image.read_bytes(), int(result.get('pages') or 0)
        except OSError:
            raise PageError(f'Windows n’a écrit aucune image pour la page {page}.') from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def read_page(content: bytes, page: int) -> dict:
    """One page of a PDF made of images: {page, pages, text, model, seconds, processor, truncated}. The page is
    drawn (page_image), then copied by the local vision model: ``text`` is that model's reading, to be shown as
    such. Nothing is kept. PageError, or vision.VisionError, said in French."""
    import asyncio
    from . import vision   # lazy: only a PDF without text needs it
    started = time.perf_counter()
    image, pages = await asyncio.to_thread(page_image, content, page)
    drawn = time.perf_counter() - started
    seen = await vision.describe(image, PAGE_QUESTION, max_tokens=PAGE_TOKENS, rules=PAGE_RULES)
    return {'page': int(page), 'pages': pages, 'text': seen['text'], 'model': seen['model'],
            'seconds': round(drawn + seen['seconds'], 1), 'processor': seen['processor'],
            'truncated': seen['truncated']}


def parts(text: str, size: int) -> list[str]:
    """16/11/2026 (suite of E4): a long text cut into consecutive parts of at most ``size`` characters, so that a
    model with a small window can read it whole, part after part. Each cut falls at the end of a paragraph when
    the last third of the part has one, else of a line, of a sentence, of a word. Nothing is lost nor repeated:
    ''.join(parts) is the text."""
    size = max(1, int(size))
    out, start = [], 0
    while len(text) - start > size:
        window = text[start:start + size]
        cut = 0
        for mark in ('\n\n', '\n', '. ', ' '):
            at = window.rfind(mark, size * 2 // 3)
            if at != -1:
                cut = at + len(mark)
                break
        cut = cut or size
        out.append(window[:cut])
        start += cut
    out.append(text[start:])
    return out
