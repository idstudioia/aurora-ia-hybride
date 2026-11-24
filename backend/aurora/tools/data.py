"""P3 « L'analyse de données » (reviews/proposition-travail-comme-claude-code.md): a table read on this PC, fixed
calculations made by the code.

- The file never goes to an AI: the model only sees its structure (columns, kinds, five example rows with their
  secrets masked) and the results of these calculations.
- The calculations are fixed functions with validated parameters (CLAUDE.md: « seulement des fonctions autorisées,
  avec des paramètres validés »), computed with numpy: describe, group, trend, outliers, compare. Filters are a
  closed list of operators.
- A source is a CSV joined to the message (read back from its request: « jointe:<run>:<n> ») or a CSV of an allowed
  folder (« fichier:<path> », the checks of tools/files._check_file). Nothing is ever written.
French files are the norm: « ; » separator, decimal comma, « 1 234,50 € », dates « 01/09/2026 »; Windows-1252 is
read when the file is not UTF-8.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import os
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .base import Params, Source, ToolError, ToolResult, fr_list, fr_num, now_iso, tool

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLUMNS = 60
MAX_PERIODS = 400
MIN_OUTLIER_VALUES = 8
SEARCH_DEPTH, SEARCH_ENTRIES = 3, 20_000
DELIMITERS = (';', ',', '\t', '|')
ENCODINGS = ('utf-8-sig', 'cp1252')
EXTENSIONS = ('.csv', '.tsv')
AGG_LABELS = {'somme': 'somme', 'moyenne': 'moyenne', 'nombre': 'nombre de lignes', 'min': 'minimum',
              'max': 'maximum', 'mediane': 'médiane'}
Agg = Literal['somme', 'moyenne', 'nombre', 'min', 'max', 'mediane']
Period = Literal['jour', 'semaine', 'mois', 'trimestre', 'annee']
MONTHS_SHORT = ('janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.')
# A column whose name says « amount » is summed; any other number is averaged (a temperature is not added up).
_SUMMED = re.compile(r'montant|prix|total|somme|depense|debit|credit|valeur|quantite|qte|vente|chiffre|cout|recette|'
                     r'revenu|paiement|achat|facture|solde|nombre|nb\b|amount|price|qty|sales|cost', re.I)
_ID_NAME = re.compile(r'^(?:id|n|no|num|numero|ref|reference|code|identifiant)\b', re.I)
DATE_FORMATS = ('%d/%m/%Y', '%d/%m/%Y %H:%M', '%d/%m/%Y %H:%M:%S', '%d/%m/%y', '%d-%m-%Y', '%d.%m.%Y', '%Y/%m/%d',
                '%m/%d/%Y', '%Y-%m')
_ISO_START = re.compile(r'^\d{4}-\d{2}-\d{2}')
_HAS_DATE_SEP = re.compile(r'\d[/.\-]\d')
_CURRENCY = re.compile(r'[€$£]|\beur(?:os?)?\b', re.I)
_SPACES = re.compile(r'[\s  ]')


# --------------------------------------------------------------------------- reading values

def _norm(text: str) -> str:
    folded = unicodedata.normalize('NFD', str(text or '').strip().lower())
    return re.sub(r'\s+', ' ', ''.join(c for c in folded if unicodedata.category(c) != 'Mn'))


def parse_number(raw: str, decimal: str | None = None) -> float | None:
    """« 1 234,50 € » -> 1234.5 ; « (12,5) » -> -12.5 ; « 15 % » -> 15. A code with a leading zero stays text.
    ``decimal``: the decimal mark of the whole column (_decimal_mark: « 1,234 » is 1234 in an English column);
    None judges the value alone, a lone comma being then a decimal comma (French files): « 1,234 » is 1.234."""
    s = _SPACES.sub('', _CURRENCY.sub('', str(raw or '').strip()))
    negative = s.startswith('(') and s.endswith(')')
    if negative:
        s = s[1:-1]
    if s.endswith('%'):
        s = s[:-1]
    if not s or re.fullmatch(r'[+-]?0\d+', s):
        return None
    if decimal == ',':
        s = s.replace('.', '').replace(',', '.') if ',' in s or re.fullmatch(r'[+-]?\d{1,3}(?:\.\d{3})+', s) else s
    elif decimal == '.':
        s = s.replace(',', '') if '.' in s or re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+', s) else s.replace(',', '.')
    elif ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    elif s.count(',') > 1:
        s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    elif s.count('.') > 1:
        s = s.replace('.', '')
    if not re.fullmatch(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?', s, re.I):
        return None
    value = float(s)
    if not math.isfinite(value):
        return None
    return -value if negative else value


def _decimal_mark(values: list[str]) -> str:
    """The decimal mark of a column, voted by its unambiguous values (« 567.89 », « 12,5 », « 1.234,50 »);
    « 2,500 » alone says nothing. French by default."""
    comma = dot = 0
    for raw in values[:2000]:
        s = _SPACES.sub('', _CURRENCY.sub('', str(raw))).strip('()%+-')
        if ',' in s and '.' in s:
            comma, dot = (comma + 1, dot) if s.rfind(',') > s.rfind('.') else (comma, dot + 1)
        elif s.count(',') > 1:
            dot += 1
        elif s.count('.') > 1:
            comma += 1
        elif ',' in s and not re.fullmatch(r'\d{1,3},\d{3}', s):
            comma += 1
        elif '.' in s and not re.fullmatch(r'\d{1,3}\.\d{3}', s):
            dot += 1
    return '.' if dot > comma else ','


def _iso(raw: str) -> datetime | None:
    """An ISO date; one with a zone (« 2026-09-30T22:30:00Z ») is put in the PC's local time first."""
    try:
        found = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if found.tzinfo is not None:
        try:
            found = found.astimezone()
        except (OverflowError, OSError, ValueError):   # before 1970 on Windows: kept as written
            pass
    return found.replace(tzinfo=None)


def parse_date(raw: str, fmt: str | None = None) -> datetime | None:
    s = str(raw or '').strip()
    if not s or not _HAS_DATE_SEP.search(s):
        return None
    if fmt in (None, 'iso') and _ISO_START.match(s):
        found = _iso(s)
        if found is not None or fmt == 'iso':
            return found
    for f in ((fmt,) if fmt and fmt != 'iso' else DATE_FORMATS):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _date_format(sample: list[str]) -> tuple[str | None, int]:
    """The date format that reads most of the sample (ISO first; day before month unless a « month » is above 12)."""
    best, best_hits = None, 0
    for fmt in ('iso',) + DATE_FORMATS:
        hits = sum(1 for s in sample if parse_date(s, fmt) is not None)
        if hits > best_hits:
            best, best_hits = fmt, hits
    return best, best_hits


@dataclass
class Column:
    name: str
    kind: str            # 'nombre' | 'date' | 'texte'
    raw: list[str]
    values: list         # float | datetime | str | None, one per row
    fmt: str = ''


@dataclass
class Table:
    name: str
    source: str
    columns: list[Column]
    rows: int
    skipped: int
    truncated: bool
    delimiter: str
    encoding: str
    dropped: int = 0          # columns beyond MAX_COLUMNS, left out
    headerless: bool = False  # no title line: the columns are named « Colonne 1… »

    def column(self, wanted: str | None, *, kinds: tuple[str, ...] = ()) -> Column:
        target = _norm(wanted or '')
        found = next((c for c in self.columns if _norm(c.name) == target), None)
        if found is None:
            raise ToolError(f'Colonne « {str(wanted)[:80]} » introuvable. Colonnes du fichier : '
                            f'{fr_list([c.name for c in self.columns])}.')
        if kinds and found.kind not in kinds:
            raise ToolError(f'La colonne « {found.name} » contient du {found.kind} : il faut une colonne de '
                            f'{" ou de ".join(kinds)} pour ce calcul.')
        return found

    def first(self, kind: str) -> Column | None:
        return next((c for c in self.columns if c.kind == kind), None)


def _typed(name: str, raw: list[str]) -> Column:
    filled = [s for s in raw if s.strip()]
    if not filled:
        return Column(name, 'texte', raw, [None] * len(raw))
    sample = filled[:300]
    fmt, hits = _date_format(sample)
    if fmt and hits >= 0.9 * len(sample):
        return Column(name, 'date', raw, [parse_date(s, fmt) if s.strip() else None for s in raw], fmt)
    mark = _decimal_mark(filled)
    numbers = [parse_number(s, mark) for s in raw]
    if sum(1 for s, n in zip(raw, numbers) if s.strip() and n is not None) >= 0.9 * len(filled):
        return Column(name, 'nombre', raw, numbers, mark)
    return Column(name, 'texte', raw, [s.strip() or None for s in raw])


def _decode(raw: bytes) -> tuple[str, str]:
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):   # UTF-16 with its mark (some exports, Excel « Unicode »)
        try:
            return raw.decode('utf-16'), 'utf-16'
        except UnicodeDecodeError:
            pass
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace'), 'utf-8 (caractères illisibles remplacés)'


def _delimiter(text: str) -> tuple[str | None, int]:
    """(separator, columns) giving the same number of columns on most of the first lines; (None, 0) if none."""
    lines = [line for line in text.splitlines()[:40] if line.strip()]
    best, best_cols, best_score = None, 0, 0.0
    for d in DELIMITERS:
        counts = [len(r) for r in csv.reader(lines, delimiter=d)]
        if not counts:
            continue
        mode, freq = Counter(counts).most_common(1)[0]
        if mode < 2:
            continue
        score = freq / len(counts) + min(mode, 30) / 1000
        if score > best_score:
            best, best_cols, best_score = d, mode, score
    return (best, best_cols) if best_score >= 0.6 else (None, 0)


def is_table(text: str) -> bool:
    """Columns read the same way on most lines, two data lines at least, and a column of numbers or dates: prose
    with a comma per line is not a table, nor a list of names (both stay a document to read)."""
    if _delimiter(text or '')[0] is None:
        return False
    try:
        table = parse_text(text, 'texte', 'test')
    except ToolError:
        return False
    return table.rows >= 2 and any(c.kind in ('nombre', 'date') for c in table.columns)


def parse_text(text: str, name: str, source: str, encoding: str = 'utf-8') -> Table:
    delimiter, _cols = _delimiter(text)
    if delimiter is None:
        raise ToolError(f'« {name} » ne ressemble pas à un tableau : aucune colonne séparée par « ; », « , » ou '
                        'une tabulation sur la plupart des lignes.')
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] | None = None
    body: list[list[str]] = []
    skipped, truncated = 0, False
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if header is None:
            header = [cell.strip() for cell in row]
            continue
        if len(row) > len(header) and any(cell.strip() for cell in row[len(header):]):
            skipped += 1
            continue
        if len(body) >= MAX_ROWS:
            truncated = True
            break
        body.append((row + [''] * len(header))[:len(header)])
    if not header or not body:
        raise ToolError(f'« {name} » n’a pas de ligne de données sous ses titres de colonnes.')
    headerless = _headerless(header, body)
    if headerless:   # the first line is data: kept, and the columns get a plain name
        body.insert(0, header)
        header = [''] * len(header)
    dropped = max(0, len(header) - MAX_COLUMNS)
    header, body = header[:MAX_COLUMNS], [row[:MAX_COLUMNS] for row in body]
    names: list[str] = []
    for i, title in enumerate(header):
        clean = title or f'Colonne {i + 1}'
        names.append(clean if clean not in names else f'{clean} ({i + 1})')
    columns = [_typed(n, [row[i] for row in body]) for i, n in enumerate(names)]
    return Table(name=name, source=source, columns=columns, rows=len(body), skipped=skipped, truncated=truncated,
                 delimiter=delimiter, encoding=encoding, dropped=dropped, headerless=headerless)


def _headerless(header: list[str], body: list[list[str]]) -> bool:
    """No title line: a first line read like the data under it (a date over dates, a number over numbers), with no
    title over such a column. A year (« 2025 ») over numbers stays a title (budgets by year)."""
    data_like = title_like = 0
    sample = body[:200]
    for i, cell in enumerate(header):
        below = [row[i] for row in sample if i < len(row) and row[i].strip()]
        if not below or not cell.strip():
            continue
        kind = _typed('', below).kind
        if kind == 'date':
            reads = parse_date(cell) is not None
        elif kind == 'nombre':
            number = parse_number(cell, _decimal_mark(below))
            reads = number is not None and not (float(number).is_integer() and 1900 <= number <= 2100)
        else:
            continue
        data_like, title_like = (data_like + 1, title_like) if reads else (data_like, title_like + 1)
    return data_like > 0 and title_like == 0


# --------------------------------------------------------------------------- sources (never written)

_CACHE: OrderedDict[tuple, Table] = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_SIZE = 4


def _cached(key: tuple, build) -> Table:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    table = build()
    with _CACHE_LOCK:
        _CACHE[key] = table
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return table


def _checked_file(raw: str) -> Path:
    """An allowed-folder file, with the checks of tools/files (no link, no hidden file, not in quarantine)."""
    from ..config import aurora_home
    from . import files
    path, why = files._check_file(raw, files.allowed_roots(), (aurora_home() / 'quarantine').resolve())
    if why or path is None:
        raise ToolError(f'Fichier « {Path(raw).name} » refusé : {why}.')
    if path.suffix.lower() not in EXTENSIONS:
        raise ToolError(f'« {path.name} » n’est pas un fichier CSV (.csv, .tsv).')
    return path


def load(source: str) -> Table:
    kind, _sep, rest = str(source or '').partition(':')
    if kind == 'jointe':
        run_id, _sep, index = rest.partition(':')
        from ..tasks import runs   # lazy: tasks import tools
        run = runs.get_run(int(run_id)) if run_id.isdigit() else None
        attachments = (runs.decision_of(run).get('attachments') or []) if run else []
        if not index.isdigit() or int(index) >= len(attachments):
            raise ToolError('Le fichier joint à cette demande est introuvable.')
        doc = attachments[int(index)]
        text = str(doc.get('text') or '')
        key = ('jointe', run_id, index, hashlib.sha1(text.encode('utf-8')).hexdigest())
        return _cached(key, lambda: parse_text(text, str(doc.get('name') or 'fichier joint'), source))
    if kind == 'fichier':
        path = _checked_file(rest)
        try:
            st = path.stat()
        except OSError as exc:
            raise ToolError(f'Lecture de « {path.name} » impossible : {exc.strerror or exc}.') from None
        if st.st_size > MAX_FILE_BYTES:
            raise ToolError(f'« {path.name} » pèse {fr_num(st.st_size / 1024 / 1024, 1)} Mo : '
                            f'{MAX_FILE_BYTES // 1024 // 1024} Mo au plus.')
        key = ('fichier', os.path.normcase(str(path)), st.st_mtime_ns, st.st_size)

        def build() -> Table:
            try:
                raw = path.read_bytes()
            except OSError as exc:   # access refused, file locked by another program…
                raise ToolError(f'Lecture de « {path.name} » impossible : {exc.strerror or exc}.') from None
            text, encoding = _decode(raw)
            return parse_text(text, path.name, f'fichier:{path}', encoding)
        return _cached(key, build)
    raise ToolError('Source de données inconnue.')


def text_sample(source: str, limit: int = 200_000) -> str:
    """The beginning of the text of an allowed-folder file: its sensitivity is judged on it, as a joined file's is
    on its whole text (orchestrator). '' when unreadable (the reading step says why)."""
    kind, _sep, rest = str(source or '').partition(':')
    if kind != 'fichier':
        return ''
    try:
        with open(_checked_file(rest), 'rb') as handle:
            raw = handle.read(limit * 2)
    except (ToolError, OSError):
        return ''
    return _decode(raw)[0][:limit]


_QUOTED = re.compile(r'[«"“\']\s*([^«»"“”\'\n]{1,200}?\.(?:csv|tsv))\s*[»"”\']', re.I)
_ABSOLUTE = re.compile(r'(?<![\w])([A-Za-z]:[\\/][^\n"<>|?*]{1,400}?\.(?:csv|tsv))\b', re.I)
_IN_FOLDER = re.compile(r'\b(documents|t[ée]l[ée]chargements|downloads|bureau|desktop)[\\/]([^\n"<>|?*]{1,300}?\.(?:csv|tsv))\b',
                        re.I)
_EXTENSION = re.compile(r'\.(?:csv|tsv)\b', re.I)
_EXCEL = re.compile(r'\.(?:xlsx|xlsm|xls|ods)\b|\b(?:fichier|classeur) excel\b', re.I)
_FOLDER_WORD = re.compile(r'\b(documents|t[ée]l[ée]chargements|downloads|bureau|desktop)\b', re.I)
# Words that introduce a file name and are never part of it (« Analyse le fichier budget 2026.csv »).
_LEAD_WORDS = {'analyse', 'analyser', 'etudie', 'etudier', 'regarde', 'ouvre', 'lis', 'calcule', 'compare', 'fais',
               'donne', 'resume', 'montre', 'le', 'la', 'les', 'l', 'un', 'une', 'mon', 'ma', 'mes', 'ce', 'cet',
               'cette', 'du', 'de', 'des', 'd', 'dans', 'sur', 'pour', 'avec', 'moi', 'stp', 'svp', 'fichier',
               'tableau', 'donnees', 'csv', 'aurora', 'et', 'en', 'a'}
EXCEL_NOT_YET = ('Les fichiers Excel (.xlsx) ne sont pas encore lus. Enregistre-le en CSV depuis Excel (Fichier › '
                 'Enregistrer sous › CSV), puis redemande l’analyse avec le fichier .csv.')


def file_mentions(text: str) -> list[str]:
    """The names a request may give to its CSV, the longest first: « budget 2026.csv » before « 2026.csv »,
    « relevé d'octobre.csv » before « octobre.csv »; a quoted name, a path or a folder\\name is taken as it is."""
    for pattern in (_ABSOLUTE, _IN_FOLDER, _QUOTED):
        found = pattern.search(text or '')
        if found:
            return [found.group(0) if pattern is _IN_FOLDER else found.group(1)]
    end = _EXTENSION.search(text or '')
    if not end:
        return []
    words = text[:end.start()].split()[-6:]
    while words and _norm(words[0]).strip('«»"“”\'’(),;:') in _LEAD_WORDS:
        words = words[1:]
    names: list[str] = []
    for i in range(len(words)):
        stem = ' '.join(words[i:]).strip('«»"“”(),;:')
        for variant in (stem, re.split(r"['’]", stem)[-1]):
            name = variant + end.group(0)
            if variant and not re.search(r'[\\/:*?"<>|]', variant) and name not in names:
                names.append(name)
    return names


def _search(root: Path, wanted: set[str]) -> dict[str, list[Path]]:
    """Files named one of ``wanted`` (lower case) under ``root``: depth ≤ SEARCH_DEPTH, hidden folders skipped,
    at most SEARCH_ENTRIES entries."""
    found: dict[str, list[Path]] = {}
    seen = 0
    for current, dirs, names in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = [d for d in dirs if not d.startswith('.') and depth < SEARCH_DEPTH]
        seen += len(names) + len(dirs)
        for n in names:
            if n.lower() in wanted:
                found.setdefault(n.lower(), []).append(Path(current) / n)
        if seen > SEARCH_ENTRIES:
            break
    return found


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_file(text: str) -> tuple[str | None, str]:
    """(« fichier:<path> », '') for the CSV the request names in an allowed folder, or (None, why in French).
    The longest name found wins; the same name in several places: the most recent file."""
    from . import files
    names = file_mentions(text)
    if not names:
        return None, ''
    roots = files.allowed_roots()
    if _ABSOLUTE.fullmatch(names[0]):
        return f'fichier:{_checked_file(names[0])}', ''
    in_folder = _IN_FOLDER.fullmatch(names[0])
    if in_folder:
        folder, _root = files.resolve_folder(in_folder.group(1), roots)
        return f'fichier:{_checked_file(str(folder / in_folder.group(2)))}', ''
    named = _FOLDER_WORD.search(text or '')
    places = [files.resolve_folder(named.group(1), roots)[0]] if named else [r['path'] for r in roots]
    found: dict[str, list[Path]] = {}
    for place in places:
        for name, paths in _search(place, {n.lower() for n in names}).items():
            found.setdefault(name, []).extend(paths)
    for name in names:
        if found.get(name.lower()):
            return f'fichier:{_checked_file(str(max(found[name.lower()], key=_mtime)))}', ''
    where = fr_list([files._label(p, files._root_of(p, roots) or {'path': p, 'label': p.name}) for p in places]) \
        if places else 'aucun dossier autorisé'
    return None, (f'Je n’ai pas trouvé « {names[0]} » dans {where} (sous-dossiers compris, sur {SEARCH_DEPTH} '
                  'niveaux). Donne son chemin complet, ou joins le fichier au message.')


def source_for(text: str, attachments: list[dict], run_id: int) -> tuple[str | None, str]:
    """The table of a request: its first joined table, else the CSV it names in an allowed folder."""
    for i, doc in enumerate(attachments or []):
        if is_table(str(doc.get('text') or '')):
            return f'jointe:{run_id}:{i}', ''
    if _EXCEL.search(text or '') and not _EXTENSION.search(text or ''):
        return None, EXCEL_NOT_YET
    try:
        return find_file(text)
    except ToolError as exc:
        return None, exc.message_fr


def source_label(source: str) -> dict:
    """{'source': 'jointe'|'fichier', 'path': str|None} for the interface and the page."""
    kind, _sep, rest = str(source or '').partition(':')
    return {'source': 'fichier' if kind == 'fichier' else 'jointe', 'path': rest if kind == 'fichier' else None}


# --------------------------------------------------------------------------- filters

class Filter(BaseModel):
    """One condition on a column. ``value`` is a list of two values for « entre »."""
    model_config = ConfigDict(extra='forbid')
    column: str = Field(min_length=1, max_length=120)
    op: Literal['=', '!=', '>', '>=', '<', '<=', 'contient', 'entre'] = '='
    value: str | float | int | list[str | float | int]


def _date_range(raw: Any) -> tuple[datetime, datetime] | None:
    """[start, end) of « 2026-09-14 », « 14/09/2026 », « 2026-09 » (a month) or « 2026 » (a year)."""
    s = str(raw).strip()
    if re.fullmatch(r'\d{4}', s):
        return datetime(int(s), 1, 1), datetime(int(s) + 1, 1, 1)
    month = re.fullmatch(r'(\d{4})-(\d{1,2})', s) or re.fullmatch(r'(\d{1,2})/(\d{4})', s)
    if month:
        y, m = (int(month.group(1)), int(month.group(2))) if len(month.group(1)) == 4 else \
            (int(month.group(2)), int(month.group(1)))
        if 1 <= m <= 12:
            return datetime(y, m, 1), datetime(y + (m == 12), m % 12 + 1, 1)
    day = parse_date(s)
    if day is None:
        return None
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _matcher(col: Column, f: Filter):
    values = f.value if isinstance(f.value, list) else [f.value]
    if f.op == 'entre' and len(values) != 2:
        raise ToolError(f'Le filtre « entre » sur « {col.name} » demande deux valeurs.')
    if col.kind == 'nombre' and f.op != 'contient':
        nums = [parse_number(str(v)) if not isinstance(v, (int, float)) else float(v) for v in values]
        if any(n is None for n in nums):
            raise ToolError(f'Valeur de filtre non numérique pour la colonne « {col.name} ».')
        a = nums[0]
        tests = {'=': lambda x: x == a, '!=': lambda x: x != a, '>': lambda x: x > a, '>=': lambda x: x >= a,
                 '<': lambda x: x < a, '<=': lambda x: x <= a, 'entre': lambda x: min(nums) <= x <= max(nums)}
        return lambda i: col.values[i] is not None and tests[f.op](col.values[i])
    if col.kind == 'date' and f.op != 'contient':
        ranges = [_date_range(v) for v in values]
        if any(r is None for r in ranges):
            raise ToolError(f'Date de filtre illisible pour la colonne « {col.name} » (exemples : 2026-09, '
                            '14/09/2026).')
        (s0, e0) = ranges[0]
        tests = {'=': lambda d: s0 <= d < e0, '!=': lambda d: not s0 <= d < e0, '>': lambda d: d >= e0,
                 '>=': lambda d: d >= s0, '<': lambda d: d < s0, '<=': lambda d: d < e0,
                 'entre': lambda d: min(r[0] for r in ranges) <= d < max(r[1] for r in ranges)}
        return lambda i: col.values[i] is not None and tests[f.op](col.values[i])
    wanted = [_norm(str(v)) for v in values]
    if f.op == 'contient':
        return lambda i: any(w in _norm(col.raw[i]) for w in wanted)
    if f.op in ('=', '!='):
        same = (lambda i: _norm(col.raw[i]) in wanted)
        return same if f.op == '=' else (lambda i: not same(i))
    raise ToolError(f'L’opérateur « {f.op} » ne s’applique pas au texte de la colonne « {col.name} ».')


def rows_of(table: Table, filters: list[Filter]) -> list[int]:
    tests = [_matcher(table.column(f.column), f) for f in filters]
    return [i for i in range(table.rows) if all(t(i) for t in tests)]


def _filters_text(filters: list[Filter]) -> str:
    if not filters:
        return ''
    parts = [f"{f.column} {f.op} {', '.join(map(str, f.value)) if isinstance(f.value, list) else f.value}"
             for f in filters]
    return ' ; filtre : ' + ' et '.join(parts)


# --------------------------------------------------------------------------- the calculations

def _aggregate(values: list[float], agg: str, count: int) -> float | None:
    if agg == 'nombre':
        return float(count)
    if not values:
        return 0.0 if agg == 'somme' else None
    arr = np.asarray(values, dtype=float)
    return float({'somme': np.sum, 'moyenne': np.mean, 'min': np.min, 'max': np.max, 'mediane': np.median}[agg](arr))


def _value_column(table: Table, value: str | None, agg: str) -> tuple[Column | None, str]:
    if agg == 'nombre' or not value:
        return None, 'nombre'
    return table.column(value, kinds=('nombre',)), agg


def _round(x: float | None, digits: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, digits)


def _redacted(text: str) -> str:
    from ..security import redaction   # lazy: security imports nothing from tools, but keep tools light
    return redaction.redact(str(text))[0]


def describe(table: Table) -> dict:
    cols = []
    for c in table.columns:
        filled = [v for v in c.values if v is not None]
        info: dict[str, Any] = {'name': c.name, 'kind': c.kind, 'filled': len(filled),
                                'missing': table.rows - len(filled), 'distinct': len({str(v) for v in filled})}
        if c.kind == 'nombre' and filled:
            arr = np.asarray(filled, dtype=float)
            info.update(min=_round(float(arr.min())), max=_round(float(arr.max())), mean=_round(float(arr.mean())),
                        median=_round(float(np.median(arr))), sum=_round(float(arr.sum())),
                        std=_round(float(arr.std(ddof=1))) if len(arr) > 1 else None)
        elif c.kind == 'date' and filled:
            info.update(first=min(filled).isoformat(), last=max(filled).isoformat(),
                        span_days=(max(filled) - min(filled)).days)
        elif c.kind == 'texte' and filled:
            info['top'] = [{'value': _redacted(v)[:80], 'count': n} for v, n in Counter(filled).most_common(5)]
        cols.append(info)
    rows = list(zip(*[c.raw for c in table.columns]))
    duplicates = sum(n - 1 for n in Counter(rows).values() if n > 1)
    examples = [{c.name: _redacted(row[i])[:60] for i, c in enumerate(table.columns)} for row in rows[:5]]
    return {'name': table.name, **source_label(table.source), 'rows': table.rows, 'columns': cols,
            'skipped': table.skipped, 'truncated': table.truncated, 'duplicates': duplicates,
            'dropped_columns': table.dropped, 'headerless': table.headerless,
            'delimiter': {';': 'point-virgule', ',': 'virgule', '\t': 'tabulation', '|': 'barre'}[table.delimiter],
            'encoding': table.encoding, 'examples': examples,
            'calc': f"Lecture de « {table.name} » : {table.rows} lignes, {len(table.columns)} colonnes, "
                    f"séparateur {('point-virgule', 'virgule', 'tabulation', 'barre')[DELIMITERS.index(table.delimiter)]}."}


def group(table: Table, by: str, value: str | None = None, agg: str = 'somme', filters: list[Filter] = (),
          top: int = 10) -> dict:
    key_col = table.column(by, kinds=('texte', 'nombre'))
    val_col, agg = _value_column(table, value, agg)
    idx = rows_of(table, list(filters))
    buckets: dict[str, list[int]] = {}
    for i in idx:
        buckets.setdefault(key_col.raw[i].strip() or '(vide)', []).append(i)
    groups = []
    for key, members in buckets.items():
        nums = [val_col.values[i] for i in members if val_col.values[i] is not None] if val_col else []
        groups.append({'group': _redacted(key)[:120], 'value': _round(_aggregate(nums, agg, len(members))),
                       'count': len(members)})
    groups.sort(key=lambda g: (g['value'] is None, -(g['value'] or 0)))
    additive = agg in ('somme', 'nombre')
    total = _round(sum(g['value'] or 0 for g in groups)) if additive else None
    # A share means something only when every group adds to the total: a bank statement mixing an income (+) and
    # expenses (−) would give « 312 % » and « −112 % ».
    if additive and total and all((g['value'] or 0) >= 0 for g in groups):
        for g in groups:
            g['share'] = _round((g['value'] or 0) / total * 100, 2)
    rest = groups[top:]
    what = f"{AGG_LABELS[agg]} de « {val_col.name} »" if val_col else 'nombre de lignes'
    return {'by': key_col.name, 'value': val_col.name if val_col else None, 'agg': agg, 'rows_used': len(idx),
            'groups_total': len(groups), 'groups': groups[:top], 'total': total,
            'other': {'groups': len(rest), 'value': _round(sum(g['value'] or 0 for g in rest))}
            if rest and additive else None,
            'calc': f"{what[0].upper()}{what[1:]} par « {key_col.name} », sur {len(idx)} lignes{_filters_text(list(filters))}."}


def _period_start(d: datetime, period: str) -> date:
    if period == 'jour':
        return d.date()
    if period == 'semaine':
        return (d - timedelta(days=d.weekday())).date()
    if period == 'mois':
        return date(d.year, d.month, 1)
    if period == 'trimestre':
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return date(d.year, 1, 1)


def _next_start(start: date, period: str) -> date:
    if period == 'jour':
        return start + timedelta(days=1)
    if period == 'semaine':
        return start + timedelta(days=7)
    months = {'mois': 1, 'trimestre': 3, 'annee': 12}[period]
    m = start.month - 1 + months
    return date(start.year + m // 12, m % 12 + 1, 1)


def period_key(start: date, period: str) -> str:
    if period == 'jour':
        return start.isoformat()
    if period == 'semaine':
        return f'{start.isocalendar()[0]}-S{start.isocalendar()[1]:02d}'
    if period == 'mois':
        return f'{start.year}-{start.month:02d}'
    if period == 'trimestre':
        return f'{start.year}-T{(start.month - 1) // 3 + 1}'
    return str(start.year)


def period_label(start: date, period: str) -> str:
    if period == 'jour':
        return start.strftime('%d/%m/%Y')
    if period == 'semaine':
        return f"sem. du {start.strftime('%d/%m/%Y')}"
    if period == 'mois':
        return f'{MONTHS_SHORT[start.month - 1]} {start.year}'
    if period == 'trimestre':
        return f'T{(start.month - 1) // 3 + 1} {start.year}'
    return str(start.year)


def _auto_period(first: datetime, last: datetime) -> str:
    days = (last - first).days
    return 'jour' if days <= 31 else 'semaine' if days <= 92 else 'mois' if days <= 1100 else 'trimestre' \
        if days <= 3650 else 'annee'


def _prev_start(start: date, period: str) -> date:
    if period == 'jour':
        return start - timedelta(days=1)
    if period == 'semaine':
        return start - timedelta(days=7)
    m = start.month - 1 - {'mois': 1, 'trimestre': 3, 'annee': 12}[period]
    return date(start.year + m // 12, m % 12 + 1, 1)


def _series(table: Table, date_col: Column, val_col: Column | None, agg: str, idx: list[int],
            period: str) -> tuple[list[dict], str]:
    """(points, cut): one point per period, gaps included, the MAX_PERIODS most recent ones; ``cut`` says in French
    what was left out before them (a stray old date, « 01/01/1900 », never pushes the recent periods out)."""
    dated = [i for i in idx if date_col.values[i] is not None]
    if not dated:
        return [], ''
    first = min(date_col.values[i] for i in dated)
    last = max(date_col.values[i] for i in dated)
    members: dict[date, list[int]] = {}
    for i in dated:
        members.setdefault(_period_start(date_col.values[i], period), []).append(i)
    first_start, start = _period_start(first, period), _period_start(last, period)
    starts = [start]
    while start > first_start and len(starts) < MAX_PERIODS and start.year > 1:
        start = _prev_start(start, period)
        starts.append(start)
    starts.reverse()
    points = []
    for start in starts:
        rows = members.get(start, [])
        nums = [val_col.values[i] for i in rows if val_col.values[i] is not None] if val_col else []
        end = _next_start(start, period)
        points.append({'period': period_key(start, period), 'label': period_label(start, period),
                       'start': start.isoformat(), 'value': _round(_aggregate(nums, agg, len(rows))) if rows or
                       agg in ('somme', 'nombre') else None, 'count': len(rows),
                       'partial': period != 'jour' and (first.date() > start or last.date() < end - timedelta(days=1))})
    cut = ''
    if starts[0] > first_start:
        left = sum(1 for i in dated if _period_start(date_col.values[i], period) < starts[0])
        cut = (f"seules les {MAX_PERIODS} dernières périodes sont gardées : {left} ligne(s) plus ancienne(s), dès le "
               f"{first.strftime('%d/%m/%Y')}, laissée(s) de côté")
    return points, cut


def trend(table: Table, date: str | None = None, value: str | None = None, agg: str = 'somme', period: str = 'auto',
          filters: list[Filter] = ()) -> dict:
    date_col = table.column(date, kinds=('date',)) if date else table.first('date')
    if date_col is None:
        raise ToolError('Ce fichier n’a pas de colonne de dates : pas de tendance dans le temps possible.')
    val_col, agg = _value_column(table, value, agg)
    idx = rows_of(table, list(filters))
    dated = [date_col.values[i] for i in idx if date_col.values[i] is not None]
    if not dated:
        raise ToolError('Aucune ligne datée après les filtres.')
    if period == 'auto':
        period = _auto_period(min(dated), max(dated))
    points, cut = _series(table, date_col, val_col, agg, idx, period)
    # Complete periods only: an incomplete first or last month would make a change out of a shorter duration.
    used = [p for p in points if not p['partial'] and p['value'] is not None]
    slope = r2 = slope_pct = change_pct = None
    if len(used) >= 3:
        x = np.array([points.index(p) for p in used], dtype=float)
        y = np.array([p['value'] for p in used], dtype=float)
        coef = np.polyfit(x, y, 1)
        fitted = np.polyval(coef, x)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - float(np.sum((y - fitted) ** 2)) / ss_tot if ss_tot else 0.0
        slope = float(coef[0])
        slope_pct = slope / abs(float(y.mean())) * 100 if y.mean() else None
    if len(used) >= 2 and used[0]['value']:
        change_pct = (used[-1]['value'] - used[0]['value']) / abs(used[0]['value']) * 100
    direction = 'stable ou irrégulière' if len(used) >= 3 else 'indéterminée (moins de trois périodes complètes)'
    if slope_pct is not None and r2 is not None and r2 >= 0.3:
        direction = 'hausse' if slope_pct > 5 else 'baisse' if slope_pct < -5 else 'stable'
    what = f"{AGG_LABELS[agg]} de « {val_col.name} »" if val_col else 'nombre de lignes'
    partial = [p['label'] for p in points if p['partial']]
    return {'date': date_col.name, 'value': val_col.name if val_col else None, 'agg': agg, 'period': period,
            'rows_used': len(idx), 'points': points, 'slope_per_period': _round(slope), 'slope_pct': _round(slope_pct, 2),
            'r2': _round(r2, 3), 'change_pct': _round(change_pct, 2), 'direction': direction,
            'first_complete': used[0]['label'] if used else None, 'last_complete': used[-1]['label'] if used else None,
            'calc': f"{what[0].upper()}{what[1:]} par {period} selon « {date_col.name} », sur {len(idx)} lignes"
                    f"{_filters_text(list(filters))} ; pente et variation sur les {len(used)} période(s) complète(s)"
                    + (f" (incomplètes, écartées : {fr_list(partial)})" if partial else '')
                    + (f" ; {cut}" if cut else '') + '.'}


def outliers(table: Table, column: str, filters: list[Filter] = ()) -> dict:
    col = table.column(column, kinds=('nombre',))
    idx = [i for i in rows_of(table, list(filters)) if col.values[i] is not None]
    base = {'column': col.name, 'rows_used': len(idx)}
    if len(idx) < MIN_OUTLIER_VALUES:
        return {**base, 'count': 0, 'outliers': [], 'enough': False,
                'calc': f"Trop peu de valeurs ({len(idx)}) pour repérer des valeurs anormales : {MIN_OUTLIER_VALUES} au moins."}
    arr = np.asarray([col.values[i] for i in idx], dtype=float)
    q1, q3 = (float(x) for x in np.percentile(arr, [25, 75]))
    iqr = q3 - q1
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    context_cols = [c for c in table.columns if c is not col and c.kind in ('date', 'texte')][:3]
    found = []

    def add(i: int, distance: float, level: str, side: str) -> None:
        found.append({'line': i + 2, 'value': _round(col.values[i]), 'level': level, 'side': side, 'distance': distance,
                      'context': {c.name: _redacted(c.raw[i])[:60] for c in context_cols}})

    if iqr > 0:
        for i in idx:
            v = col.values[i]
            if v < low or v > high:
                distance = (low - v) if v < low else (v - high)
                add(i, distance, 'fort' if distance > 1.5 * iqr else 'modéré', 'bas' if v < low else 'haut')
        rule = (f"anormal sous Q1 − 1,5 × IQR = {fr_num(low, 2)} ou au-dessus de Q3 + 1,5 × IQR = {fr_num(high, 2)} "
                f"(Q1 = {fr_num(q1, 2)}, Q3 = {fr_num(q3, 2)}).")
    else:
        # Q1 = Q3: most values are the same (a subscription of 9,99): the rule sees nothing, but a value that differs
        # while at most a quarter do (a double debit of 99,90) is what the user wants to see.
        median = float(np.median(arr))
        differ = [i for i in idx if col.values[i] != median]
        if 0 < len(differ) <= 0.25 * len(idx):
            for i in differ:
                v = col.values[i]
                add(i, abs(v - median), 'fort' if abs(v - median) > abs(median) else 'modéré',
                    'bas' if v < median else 'haut')
            rule = (f"la moitié centrale des valeurs vaut {fr_num(median, 2)} (Q1 = Q3) : les {len(differ)} valeur(s) "
                    'différente(s) sont signalées.')
        else:
            rule = (f"Q1 = Q3 = {fr_num(q1, 2)}, mais plus d’un quart des valeurs en diffèrent : aucune n’est "
                    'signalée comme anormale.')
    found.sort(key=lambda o: -o['distance'])
    for o in found:
        o.pop('distance')
    return {**base, 'enough': True, 'q1': _round(q1), 'q3': _round(q3), 'iqr': _round(iqr), 'low': _round(low),
            'high': _round(high), 'count': len(found), 'outliers': found[:20],
            'calc': f"Règle de Tukey sur « {col.name} » ({len(idx)} valeurs{_filters_text(list(filters))}) : {rule}"}


def compare(table: Table, date: str | None = None, value: str | None = None, agg: str = 'somme', period: str = 'mois',
            first: str | None = None, second: str | None = None, by: str | None = None,
            filters: list[Filter] = ()) -> dict:
    date_col = table.column(date, kinds=('date',)) if date else table.first('date')
    if date_col is None:
        raise ToolError('Ce fichier n’a pas de colonne de dates : pas de comparaison de périodes possible.')
    val_col, agg = _value_column(table, value, agg)
    by_col = table.column(by, kinds=('texte', 'nombre')) if by else None
    idx = rows_of(table, list(filters))
    points, cut = _series(table, date_col, val_col, agg, idx, period)
    by_key = {p['period']: p for p in points}
    if first and second:
        missing = [k for k in (first, second) if k not in by_key]
        if missing:
            raise ToolError(f"Période absente du fichier : {fr_list(missing)}. Périodes présentes : "
                            f"{fr_list([p['period'] for p in points][:12])}.")
        a, b = by_key[first], by_key[second]
    else:
        complete = [p for p in points if not p['partial']]
        if len(complete) < 2:
            raise ToolError(f'Il faut au moins deux périodes complètes ({period}) pour comparer : le fichier en a '
                            f'{len(complete)}.')
        a, b = complete[-2], complete[-1]
    skipped = [p['label'] for p in points if p['partial'] and p['start'] > b['start']]
    # Periods named by the request (« septembre et octobre ») may be incomplete: said, never hidden.
    incomplete = [p['label'] for p in (a, b) if p['partial']]
    dated = [date_col.values[i] for i in idx if date_col.values[i] is not None]
    diff = None if a['value'] is None or b['value'] is None else b['value'] - a['value']
    pct = None if diff is None or not a['value'] else diff / abs(a['value']) * 100
    changes = []
    if by_col is not None:
        def per_group(p: dict) -> dict[str, float | None]:
            start = datetime.fromisoformat(p['start'])
            end = datetime.combine(_next_start(start.date(), period), datetime.min.time())
            rows = [i for i in idx if date_col.values[i] is not None and start <= date_col.values[i] < end]
            out: dict[str, list[int]] = {}
            for i in rows:
                out.setdefault(by_col.raw[i].strip() or '(vide)', []).append(i)
            return {k: _aggregate([val_col.values[i] for i in m if val_col.values[i] is not None] if val_col else [],
                                  agg, len(m)) for k, m in out.items()}
        ga, gb = per_group(a), per_group(b)
        absent = 0.0 if agg in ('somme', 'nombre') else None   # a group absent from a period adds nothing to it
        for key in set(ga) | set(gb):
            va, vb = ga.get(key, absent), gb.get(key, absent)
            d = (vb or 0) - (va or 0) if agg in ('somme', 'nombre') else (None if va is None or vb is None else vb - va)
            changes.append({'group': _redacted(key)[:120], 'first': _round(va), 'second': _round(vb), 'diff': _round(d),
                            'diff_pct': _round(d / abs(va) * 100, 2) if d is not None and va else None})
        changes.sort(key=lambda c: -abs(c['diff'] or 0))
    what = f"{AGG_LABELS[agg]} de « {val_col.name} »" if val_col else 'nombre de lignes'
    return {'date': date_col.name, 'value': val_col.name if val_col else None, 'agg': agg, 'period': period,
            'first': {k: a[k] for k in ('period', 'label', 'value', 'count')},
            'second': {k: b[k] for k in ('period', 'label', 'value', 'count')},
            'diff': _round(diff), 'diff_pct': _round(pct, 2), 'by': by_col.name if by_col else None,
            'changes': changes[:10], 'incomplete': incomplete,
            'calc': f"{what[0].upper()}{what[1:]} en {a['label']} puis en {b['label']} (« {date_col.name} »)"
                    f"{_filters_text(list(filters))}, écart = {b['label']} − {a['label']}"
                    + (f" ; {fr_list(skipped)} incomplet(s) dans le fichier, donc écarté(s)" if skipped else '')
                    + (f" ; attention, {fr_list(incomplete)} incomplet(s) dans le fichier (données du "
                       f"{min(dated).strftime('%d/%m/%Y')} au {max(dated).strftime('%d/%m/%Y')}) : l’écart compare des "
                       'durées différentes' if incomplete and dated else '')
                    + (f" ; {cut}" if cut else '') + '.'}


# --------------------------------------------------------------------------- the base set, chosen by the code

def _id_like(col: Column, rows: int) -> bool:
    """A numbering, not a quantity: named like one (id, n°, code…), or 1, 2, 3… one per line."""
    filled = [v for v in col.values if v is not None]
    if _ID_NAME.match(_norm(col.name)):
        return True
    if len(filled) != rows or rows < 3 or not all(float(v).is_integer() for v in filled):
        return False
    ordered = sorted(filled)
    return all(b - a == 1 for a, b in zip(ordered, ordered[1:]))


def main_value(table: Table) -> Column | None:
    numbers = [c for c in table.columns if c.kind == 'nombre' and not _id_like(c, table.rows)]
    return next((c for c in numbers if _SUMMED.search(_norm(c.name))), numbers[0] if numbers else None)


def category(table: Table) -> Column | None:
    for c in table.columns:
        distinct = len({v for v in c.values if v is not None})
        if c.kind == 'texte' and 2 <= distinct <= 30 and distinct < table.rows:
            return c
    return None


def standard_calls(table: Table) -> list[tuple[str, dict]]:
    """The base calculations, chosen from the kinds of the columns (the model may add 1 to 3 targeted ones)."""
    dates = table.first('date')
    value = main_value(table)
    cat = category(table)
    agg = 'somme' if value is not None and _SUMMED.search(_norm(value.name)) else 'moyenne'
    calls: list[tuple[str, dict]] = []
    if dates is not None and value is not None:
        calls.append(('data.trend', {'date': dates.name, 'value': value.name, 'agg': agg, 'period': 'auto'}))
    if cat is not None:
        calls.append(('data.group', {'by': cat.name, 'value': value.name, 'agg': agg} if value is not None
                      else {'by': cat.name, 'agg': 'nombre'}))
    if value is not None:
        calls.append(('data.outliers', {'column': value.name}))
    if dates is not None and value is not None:
        filled = [d for d in dates.values if d is not None]
        if filled and (max(filled) - min(filled)).days >= 59:
            calls.append(('data.compare', {'date': dates.name, 'value': value.name, 'agg': agg, 'period': 'mois',
                                           **({'by': cat.name} if cat is not None else {})}))
    return calls


# --------------------------------------------------------------------------- the tools

class SourceParams(Params):
    source: str = Field(min_length=3, max_length=2100)   # set by AURORA, never chosen by a model


class FilteredParams(SourceParams):
    filters: list[Filter] = Field(default_factory=list, max_length=6)


class GroupParams(FilteredParams):
    by: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    agg: Agg = 'somme'
    top: int = Field(default=10, ge=1, le=30)


class TrendParams(FilteredParams):
    date: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    agg: Agg = 'somme'
    period: Literal['auto', 'jour', 'semaine', 'mois', 'trimestre', 'annee'] = 'auto'


class OutliersParams(FilteredParams):
    column: str = Field(min_length=1, max_length=120)


class CompareParams(FilteredParams):
    date: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=120)
    agg: Agg = 'somme'
    period: Period = 'mois'
    first: str | None = Field(default=None, max_length=20)
    second: str | None = Field(default=None, max_length=20)
    by: str | None = Field(default=None, max_length=120)


def _sources(table: Table) -> list[Source]:
    where = 'joint au message' if table.source.startswith('jointe:') else 'lu dans un dossier autorisé'
    return [Source(title=f'Fichier « {table.name} » ({table.rows} lignes), {where}, sur ce PC', kind='local',
                   retrieved_at=now_iso())]


def _result(table: Table, data: dict, summary: str) -> ToolResult:
    return ToolResult(ok=True, data={'file': table.name, **data}, summary=summary, provenance='csv',
                      sources=_sources(table))


def _money(value: float | None, name: str | None) -> str:
    return fr_num(value, 2) if value is not None and not float(value).is_integer() else fr_num(value, 0)


FILTER_HELP = ('Filtres possibles (liste) : [{"column": "Catégorie", "op": "=", "value": "Restaurant"}] ; op : = != > '
               '>= < <= contient entre (entre : value = [a, b]) ; dates : 2026-09, 14/09/2026 ou 2026.')


@tool('data.describe', description='Décrit le tableau : colonnes, types, valeurs manquantes, minimum, maximum, '
      'moyenne, valeurs les plus fréquentes, cinq lignes d’exemple.', action='files.scan', params=SourceParams,
      provenance='csv', timeout_s=60)
def describe_tool(p: SourceParams) -> ToolResult:
    table = load(p.source)
    data = describe(table)
    kinds = Counter(c['kind'] for c in data['columns'])
    summary = (f"« {table.name} » : {table.rows} lignes, {len(table.columns)} colonnes "
               f"({fr_list([f'{n} {k}' for k, n in kinds.items()])}) : "
               f"{fr_list([c.name for c in table.columns][:12])}.")
    if table.skipped:
        summary += f' {table.skipped} ligne(s) mal formée(s) ignorée(s).'
    if table.truncated:
        summary += f' Seules les {MAX_ROWS} premières lignes sont lues.'
    if table.dropped:
        summary += f' {table.dropped} colonne(s) au-delà de la {MAX_COLUMNS}e laissée(s) de côté.'
    if table.headerless:
        summary += ' Pas de ligne de titres : les colonnes sont nommées « Colonne 1 », « Colonne 2 »…'
    return _result(table, data, summary)


@tool('data.group', description='Regroupe les lignes par une colonne (by) et calcule pour chaque groupe la somme, '
      'la moyenne, le nombre, le minimum, le maximum ou la médiane (agg) d’une colonne de nombres (value). '
      + FILTER_HELP, action='files.scan', params=GroupParams, provenance='csv', timeout_s=60)
def group_tool(p: GroupParams) -> ToolResult:
    table = load(p.source)
    data = group(table, p.by, p.value, p.agg, p.filters, p.top)
    head = ', '.join(f"{g['group']} {_money(g['value'], data['value'])}" for g in data['groups'][:5])
    return _result(table, data, f"{data['calc']} En tête : {head}." if head else f"{data['calc']} Aucun groupe.")


@tool('data.trend', description='Évolution dans le temps : agrège une colonne de nombres (value, agg) par jour, '
      'semaine, mois, trimestre ou année (period, auto par défaut) selon une colonne de dates (date), et calcule '
      'la pente. ' + FILTER_HELP, action='files.scan', params=TrendParams, provenance='csv', timeout_s=60)
def trend_tool(p: TrendParams) -> ToolResult:
    table = load(p.source)
    data = trend(table, p.date, p.value, p.agg, p.period, p.filters)
    change = f", variation {fr_num(data['change_pct'], 1)} % de {data['first_complete']} à {data['last_complete']}" \
        if data['change_pct'] is not None else ''
    # The latest periods in the summary: the model's view of the data stops at 1 800 characters (core/agent.py),
    # which would show it the oldest periods only.
    recent = ' ; '.join(f"{p['label']}{' (incomplète)' if p['partial'] else ''} : {_money(p['value'], None)}"
                        for p in data['points'][-4:])
    return _result(table, data, f"{data['calc']} {len(data['points'])} période(s), tendance {data['direction']}{change}."
                                f" Dernières périodes : {recent}.")


@tool('data.outliers', description='Repère les valeurs anormales d’une colonne de nombres (column) par la règle de '
      'Tukey (en dehors de Q1 − 1,5 × IQR et Q3 + 1,5 × IQR), avec leur ligne dans le fichier. ' + FILTER_HELP,
      action='files.scan', params=OutliersParams, provenance='csv', timeout_s=60)
def outliers_tool(p: OutliersParams) -> ToolResult:
    table = load(p.source)
    data = outliers(table, p.column, p.filters)
    found = ', '.join(f"ligne {o['line']} : {_money(o['value'], data['column'])}" for o in data['outliers'][:5])
    return _result(table, data, f"{data['calc']} {data['count']} valeur(s) anormale(s)" + (f' : {found}.' if found else '.'))


@tool('data.compare', description='Compare deux périodes (period : mois par défaut) : les deux dernières complètes, '
      'ou first et second (ex. 2026-09 et 2026-10) ; avec by, l’écart de chaque groupe. ' + FILTER_HELP,
      action='files.scan', params=CompareParams, provenance='csv', timeout_s=60)
def compare_tool(p: CompareParams) -> ToolResult:
    table = load(p.source)
    data = compare(table, p.date, p.value, p.agg, p.period, p.first, p.second, p.by, p.filters)
    a, b = data['first'], data['second']
    pct = f" ({'+' if (data['diff_pct'] or 0) > 0 else ''}{fr_num(data['diff_pct'], 1)} %)" \
        if data['diff_pct'] is not None else ''
    return _result(table, data, f"{data['calc']} {a['label']} : {_money(a['value'], data['value'])} ; "
                                f"{b['label']} : {_money(b['value'], data['value'])}{pct}.")


DATA_TOOLS = ('data.describe', 'data.group', 'data.trend', 'data.outliers', 'data.compare')
