"""User files: read-only scan and reversible quarantine, inside allowed folders only.

Allowed roots: Documents, Downloads/Téléchargements and Desktop/Bureau of the current user
(Windows known folders, OneDrive variants when they exist) plus ``settings.allowed_roots``.
Every path is checked with ``resolve()`` + ``is_relative_to``; links and junctions are never
followed; hidden and system files are ignored. ``quarantine`` moves files (never deletes)
to ``AURORA_HOME/quarantine/<run_id>/`` with a manifest; ``restore`` moves them back.
"""
from __future__ import annotations

import heapq
import json
import os
import re
import shutil
import stat
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator

from .base import (Params, Source, ToolError, ToolResult, aurora_home, fr_list, fr_num, fr_sig, get_setting, now_iso,
                   tool)

MAX_ENTRIES = 300_000
SCAN_BUDGET_S = 25.0
MAX_QUARANTINE = 1000
_HIDDEN = getattr(stat, 'FILE_ATTRIBUTE_HIDDEN', 0x2) | getattr(stat, 'FILE_ATTRIBUTE_SYSTEM', 0x4)
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
_CLOUD = getattr(stat, 'FILE_ATTRIBUTE_OFFLINE', 0x1000) | 0x40000 | 0x400000  # offline / recall on open / recall on data access
_SKIP_NAMES = {'desktop.ini', 'thumbs.db', 'ntuser.dat'}
_RUN_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

KNOWN_FOLDERS = {
    'Documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
    'Downloads': '{374DE290-123F-4565-9164-39C4925E467B}',
    'Desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}',
}
FOLDER_LABELS = {'Documents': 'Documents', 'Downloads': 'Téléchargements', 'Desktop': 'Bureau'}
FOLDER_ALIASES = {
    'documents': 'Documents', 'mes documents': 'Documents', 'document': 'Documents',
    'telechargements': 'Downloads', 'telechargement': 'Downloads', 'downloads': 'Downloads', 'download': 'Downloads',
    'bureau': 'Desktop', 'desktop': 'Desktop',
}
LOCAL_NAMES = {'Documents': ('Documents',), 'Downloads': ('Downloads', 'Téléchargements'), 'Desktop': ('Desktop', 'Bureau')}
# Other user folders the router may name: only usable once added to settings.allowed_roots.
OTHER_FOLDERS = {'pictures': ('Images', 'Pictures'), 'images': ('Images', 'Pictures'), 'photos': ('Images', 'Pictures'),
                 'videos': ('Vidéos', 'Videos'), 'music': ('Musique', 'Music'), 'musique': ('Musique', 'Music')}


def _norm(text: str) -> str:
    t = ''.join(c for c in unicodedata.normalize('NFD', (text or '').strip().lower()) if unicodedata.category(c) != 'Mn')
    return re.sub(r'^(?:(?:le|la|les|mon|ma|mes|dossier|repertoire)\s+)+', '', t).strip()


def _known_folder(kind: str) -> Path | None:
    """Real location of a Windows known folder (handles OneDrive / custom redirection)."""
    if sys.platform != 'win32' or kind not in KNOWN_FOLDERS:
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                        ('Data4', ctypes.c_ubyte * 8)]

        u = uuid.UUID(KNOWN_FOLDERS[kind])
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        fn.restype = ctypes.c_long
        ptr = ctypes.c_void_p()
        if fn(ctypes.byref(guid), 0, None, ctypes.byref(ptr)) != 0 or not ptr.value:
            return None
        try:
            return Path(ctypes.wstring_at(ptr.value))
        finally:
            ctypes.windll.ole32.CoTaskMemFree(ptr)
    except Exception:
        return None


def _onedrive_dirs() -> list[Path]:
    out = []
    for var in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        value = os.environ.get(var)
        if value:
            out.append(Path(value))
    out.append(Path.home() / 'OneDrive')
    return out


def _default_candidates() -> list[tuple[str, Path]]:
    home = Path.home()
    cands: list[tuple[str, Path]] = []
    for kind in ('Documents', 'Downloads', 'Desktop'):
        known = _known_folder(kind)
        if known:
            cands.append((kind, known))
        for name in LOCAL_NAMES[kind]:
            cands.append((kind, home / name))
            for od in _onedrive_dirs():
                cands.append((kind, od / name))
    return cands


def _extra_roots() -> list[str]:
    value = get_setting('allowed_roots', [])
    if isinstance(value, str):
        value = [value]
    return [str(v) for v in value or [] if str(v).strip()]


def _too_broad(path: Path) -> bool:
    """User-configured roots must not cover the system or AURORA's own data."""
    home = Path.home().resolve()
    if path.parent == path or path == home:
        return True
    blocked = [aurora_home()]
    for var in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData'):
        if os.environ.get(var):
            blocked.append(Path(os.environ[var]))
    for b in blocked:
        try:
            b = b.resolve()
        except OSError:
            continue
        if path == b or path.is_relative_to(b) or b.is_relative_to(path):
            return True
    return path in {home / 'AppData', home / 'AppData' / 'Local', home / 'AppData' / 'Roaming'}


def allowed_roots() -> list[dict]:
    """[{kind, label, path}] of existing, resolved, de-duplicated roots."""
    roots: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, label: str, p: Path, *, check_broad: bool) -> None:
        try:
            rp = p.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return
        if not rp.is_dir() or (check_broad and _too_broad(rp)):
            return
        key = os.path.normcase(str(rp))
        if key in seen:
            return
        seen.add(key)
        roots.append({'kind': kind, 'label': label, 'path': rp})

    for kind, p in _default_candidates():
        add(kind, FOLDER_LABELS[kind], p, check_broad=False)
    for raw in _extra_roots():
        add('custom', Path(raw).name or raw, Path(raw), check_broad=True)
    return roots


def _root_of(path: Path, roots: list[dict]) -> dict | None:
    best = None
    for r in roots:
        if path == r['path'] or path.is_relative_to(r['path']):
            if best is None or len(str(r['path'])) > len(str(best['path'])):
                best = r
    return best


def resolve_folder(folder: str | None, roots: list[dict] | None = None) -> tuple[Path, dict]:
    roots = roots if roots is not None else allowed_roots()
    if not roots:
        raise ToolError('Aucun dossier autorisé n\'est disponible sur ce PC.')
    raw = (folder or '').strip()
    if not raw:
        raw = 'Downloads'
    alias = FOLDER_ALIASES.get(_norm(raw))
    if alias:
        match = next((r for r in roots if r['kind'] == alias), None)
        if match is None:
            raise ToolError(f'Dossier « {raw} » introuvable sur ce PC.')
        return match['path'], match
    custom = next((r for r in roots if r['kind'] == 'custom' and _norm(r['label']) == _norm(raw)), None)
    if custom:
        return custom['path'], custom
    other = OTHER_FOLDERS.get(_norm(raw))
    if other:
        wanted = {_norm(n) for n in other}
        custom = next((r for r in roots if r['kind'] == 'custom' and _norm(r['label']) in wanted), None)
        if custom:
            return custom['path'], custom
        raise ToolError(f"Le dossier {other[0]} n'est pas dans les emplacements autorisés (Documents, Téléchargements, "
                        "Bureau) : ajoutez-le dans les réglages (dossiers autorisés) pour l'analyser.")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ToolError(f'Dossier non reconnu : « {raw} » (utilisez Documents, Téléchargements, Bureau ou un chemin complet).')
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ToolError(f'Dossier introuvable : « {raw} ».') from None
    if not rp.is_dir():
        raise ToolError(f'« {raw} » n\'est pas un dossier.')
    root = _root_of(rp, roots)
    if root is None:
        names = fr_list(list(dict.fromkeys(r['label'] for r in roots)))
        # Same guard as allowed_roots(): a system folder or a drive root can never be added.
        hint = ('ce dossier ne peut pas être ajouté, car il est protégé ou trop large : dossier système, racine de '
                'disque, dossier personnel entier ou données d\'AURORA' if _too_broad(rp)
                else 'un autre dossier peut être ajouté dans Paramètres, panneau « Dossiers et lieu »')
        raise ToolError(f'« {raw} » est hors des emplacements autorisés : analyse refusée, rien n\'a été lu. '
                        f'Dossiers autorisés sur ce PC : {names} ; {hint}')
    return rp, root


def _label(path: Path, root: dict) -> str:
    rel = path.relative_to(root['path'])
    return root['label'] if str(rel) in ('', '.') else f"{root['label']}\\{rel}"


def _birth(st: os.stat_result) -> float:
    return getattr(st, 'st_birthtime', None) or (st.st_ctime if sys.platform == 'win32' else st.st_mtime)


def scan(folder: str | None = None, older_than_days: float = 180, min_size_mb: float = 0, limit: int = 100) -> dict:
    """Read-only walk. A file counts as old only if both modified and created long ago."""
    target, root = resolve_folder(folder)
    now = time.time()
    min_bytes = int(min_size_mb * 1024 * 1024)
    started = time.monotonic()
    stats = {'files': 0, 'dirs': 0, 'hidden': 0, 'links': 0, 'errors': 0, 'entries': 0}
    truncated = False
    matched: list[tuple[int, str, float, float, bool]] = []
    largest: tuple[int, str] | None = None     # biggest file seen, said when nothing reaches a size threshold
    stack = [target]
    while stack and not truncated:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            stats['errors'] += 1
            continue
        with it:
            for entry in it:
                stats['entries'] += 1
                if stats['entries'] > MAX_ENTRIES or time.monotonic() - started > SCAN_BUDGET_S:
                    truncated = True
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    stats['errors'] += 1
                    continue
                attrs = getattr(st, 'st_file_attributes', 0)
                if entry.name.startswith('.') or entry.name.lower() in _SKIP_NAMES or attrs & _HIDDEN:
                    stats['hidden'] += 1
                    continue
                if entry.is_symlink():
                    stats['links'] += 1
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if attrs & _REPARSE:      # junction / mount point: never followed
                        stats['links'] += 1
                        continue
                    stats['dirs'] += 1
                    stack.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                cloud = bool(attrs & _CLOUD)
                if attrs & _REPARSE and not cloud:
                    stats['links'] += 1
                    continue
                stats['files'] += 1
                if largest is None or st.st_size > largest[0]:
                    largest = (st.st_size, entry.path)
                ref = max(st.st_mtime, _birth(st))
                age_days = (now - ref) / 86400
                if age_days >= older_than_days and st.st_size >= min_bytes:
                    matched.append((st.st_size, entry.path, st.st_mtime, age_days, cloud))
    by_ext: dict[str, list[float]] = {}
    for size, path, *_ in matched:
        ext = os.path.splitext(path)[1].lower() or '(sans extension)'
        agg = by_ext.setdefault(ext, [0, 0])
        agg[0] += 1
        agg[1] += size
    top = heapq.nlargest(limit, matched)
    files = [{
        'path': path, 'name': os.path.basename(path), 'relative': os.path.relpath(path, target),
        'size_mb': round(size / 1024 ** 2, 2),
        'size_bytes': size,   # exact size: permission previews must not show « 0 octets » for small files
        'modified_at': datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'age_days': int(age), 'extension': os.path.splitext(path)[1].lower(), 'cloud_only': cloud,
    } for size, path, mtime, age, cloud in top]
    total = sum(m[0] for m in matched)
    extensions = sorted(({'extension': k, 'count': v[0], 'size_mb': round(v[1] / 1024 ** 2, 1)} for k, v in by_ext.items()),
                        key=lambda e: -e['size_mb'])[:10]
    return {'root': str(target), 'label': _label(target, root), 'older_than_days': older_than_days,
            'min_size_mb': min_size_mb, 'files': files, 'matched_count': len(matched),
            'matched_total_mb': round(total / 1024 ** 2, 1), 'scanned_files': stats['files'],
            'scanned_dirs': stats['dirs'], 'skipped_hidden': stats['hidden'], 'skipped_links': stats['links'],
            'errors': stats['errors'], 'truncated': truncated, 'by_extension': extensions,
            'largest_file': {'name': os.path.basename(largest[1]), 'path': largest[1], 'size_bytes': largest[0],
                             'size_mb': round(largest[0] / 1024 ** 2, 2)} if largest else None,
            'read_only': True, 'duration_s': round(time.monotonic() - started, 3)}


def _size_text(mb: float) -> str:
    return f'{fr_num(mb / 1024, 1)} Go' if mb >= 1024 else f'{fr_num(mb, 1 if mb < 10 else 0)} Mo'


def summarize_scan(d: dict) -> str:
    crit = []
    if d['older_than_days']:
        crit.append(f"non modifiés depuis plus de {fr_num(d['older_than_days'], 0)} jours")
    if d['min_size_mb']:
        mb = d['min_size_mb']
        crit.append("d'au moins " + (f'{fr_sig(mb / 1024)} Go' if mb >= 1024 else f'{fr_sig(mb)} Mo'))
    crit_text = (' ' + ' et '.join(crit)) if crit else ''
    text = (f"Dans {d['label']} : {fr_num(d['matched_count'], 0)} fichier(s){crit_text} "
            f"({_size_text(d['matched_total_mb'])} au total) sur {fr_num(d['scanned_files'], 0)} analysés.")
    if d['min_size_mb'] and d.get('size_default'):
        text += " Aucune taille n'était précisée : seuil par défaut appliqué pour « gros fichiers »."
    if d['files']:
        text += ' Les plus gros : ' + fr_list([f"{f['name']} ({_size_text(f['size_mb'])})" for f in d['files'][:3]]) + '.'
    elif d['min_size_mb'] and d.get('largest_file'):
        big = d['largest_file']
        text += f" Le plus gros fichier analysé est {big['name']} ({_size_text(big['size_mb'])})."
    if d['truncated']:
        text += ' Analyse interrompue (dossier très volumineux) : résultats partiels.'
    return text + " Lecture seule : rien n'a été modifié."


# --------------------------------------------------------------------------- quarantine

def _quarantine_dir(run_id: str) -> Path:
    if not _RUN_ID.match(run_id):
        raise ToolError('Identifiant de run invalide pour la quarantaine.')
    return aurora_home() / 'quarantine' / run_id


def _load_manifest(qdir: Path) -> dict | None:
    path = qdir / 'manifest.json'
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ToolError('Manifeste de quarantaine illisible : aucune restauration automatique possible.') from None


def _write_manifest(qdir: Path, manifest: dict) -> None:
    manifest['updated_at'] = now_iso()
    tmp = qdir / 'manifest.json.tmp'
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, qdir / 'manifest.json')


def _check_file(raw: str, roots: list[dict], qroot: Path) -> tuple[Path | None, str | None]:
    p = Path(raw)
    if not p.is_absolute():
        return None, 'chemin relatif refusé'
    try:
        st = p.lstat()
    except OSError:
        return None, 'fichier introuvable'
    attrs = getattr(st, 'st_file_attributes', 0)
    if stat.S_ISLNK(st.st_mode) or (attrs & _REPARSE and not attrs & _CLOUD):
        return None, 'lien ou point de jonction refusé'
    if not stat.S_ISREG(st.st_mode):
        return None, "ce n'est pas un fichier"
    if attrs & _HIDDEN or p.name.lower() in _SKIP_NAMES or p.name.startswith('.'):
        return None, 'fichier caché ou système'
    if attrs & _CLOUD:
        return None, 'fichier OneDrive non téléchargé sur ce PC'
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, 'fichier introuvable'
    if rp.is_relative_to(qroot):
        return None, 'fichier déjà en quarantaine'
    if _root_of(rp, roots) is None:
        return None, 'hors des dossiers autorisés'
    return rp, None


def _changed_since_preview(st: os.stat_result, expected: dict | None) -> bool:
    """True if size or modification time differ from the file shown in the approved preview."""
    if not expected:
        return False
    size = expected.get('size')
    if isinstance(size, (int, float)) and int(size) != st.st_size:
        return True
    modified = expected.get('modified')
    if modified:
        try:
            shown = datetime.fromisoformat(str(modified).replace('Z', '+00:00')).timestamp()
        except ValueError:
            return False
        if abs(int(st.st_mtime) - shown) > 1:
            return True
    return False


def quarantine(paths: list[str], run_id: str, expected: list[dict] | None = None) -> dict:
    """expected: [{path, size, modified}] of the approved preview; a file changed since is refused."""
    run_id = str(run_id)
    shown = {str(e.get('path')): e for e in expected or [] if isinstance(e, dict) and e.get('path')}
    qdir = _quarantine_dir(run_id)
    qroot = (aurora_home() / 'quarantine').resolve()
    roots = allowed_roots()
    if len(paths) > MAX_QUARANTINE:
        raise ToolError(f'Trop de fichiers en une fois (limite : {MAX_QUARANTINE}).')
    manifest = _load_manifest(qdir) or {'run_id': run_id, 'created_at': now_iso(), 'items': [],
                                        'note': 'Fichiers déplacés par AURORA (jamais supprimés). Restauration : files.restore.'}
    moved, refused = [], []
    for raw in dict.fromkeys(str(x) for x in paths):
        rp, reason = _check_file(raw, roots, qroot)
        if reason:
            refused.append({'path': raw, 'reason': reason})
            continue
        files_dir = qdir / 'files'
        files_dir.mkdir(parents=True, exist_ok=True)
        index = len(manifest['items'])
        dest = files_dir / f'{index:04d}_{rp.name}'
        st = rp.stat()
        if _changed_since_preview(st, shown.get(raw)):
            refused.append({'path': raw, 'reason': "modifié depuis l'aperçu (taille ou date)"})
            continue
        try:
            shutil.move(str(rp), str(dest))
        except OSError as exc:
            refused.append({'path': raw, 'reason': f'déplacement impossible ({type(exc).__name__})'})
            continue
        item = {'original': str(rp), 'stored': dest.relative_to(qdir).as_posix(), 'size': st.st_size,
                'mtime': st.st_mtime, 'moved_at': now_iso(), 'status': 'quarantined', 'restored_at': None}
        manifest['items'].append(item)
        _write_manifest(qdir, manifest)  # after each move: the manifest always matches the disk
        moved.append({'original': item['original'], 'stored': str(dest), 'size_mb': round(st.st_size / 1024 ** 2, 2)})
    return {'run_id': run_id, 'quarantine_dir': str(qdir), 'manifest': str(qdir / 'manifest.json'),
            'moved': moved, 'refused': refused, 'moved_total_mb': round(sum(m['size_mb'] for m in moved), 2),
            'reversible': True}


def _ancestor_allowed(path: Path, roots: list[dict]) -> bool:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        probe = probe.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return _root_of(probe, roots) is not None


def quarantined_items(run_id: str) -> list[dict]:
    """Manifest items still held by the quarantine of a run (original, size, moved_at); [] when there is none
    or its manifest is unreadable (restore() reports that when asked directly). Read only."""
    try:
        manifest = _load_manifest(_quarantine_dir(str(run_id)))
    except ToolError:
        return []
    return [i for i in (manifest or {}).get('items', []) if i.get('status') == 'quarantined']


def latest_quarantine() -> str | None:
    """The run of the most recent quarantine that still holds files (« restaure les fichiers mis en quarantaine »,
    B4), or None. Read only."""
    base = aurora_home() / 'quarantine'
    best: tuple[str, str] | None = None
    for qdir in base.iterdir() if base.is_dir() else ():
        if not qdir.is_dir() or not _RUN_ID.match(qdir.name):
            continue
        held = quarantined_items(qdir.name)
        if held:
            moved = max(str(i.get('moved_at') or '') for i in held)
            if best is None or moved > best[0]:
                best = (moved, qdir.name)
    return best[1] if best else None


def restore(run_id: str) -> dict:
    run_id = str(run_id)
    qdir = _quarantine_dir(run_id)
    manifest = _load_manifest(qdir)
    if manifest is None:
        raise ToolError(f'Aucune quarantaine trouvée pour le run {run_id}.')
    roots = allowed_roots()
    qdir_resolved = qdir.resolve()
    restored, failed = [], []
    for item in manifest.get('items', []):
        if item.get('status') != 'quarantined':
            continue
        stored = (qdir / item.get('stored', '')).resolve()
        original = Path(item.get('original', ''))
        if not stored.is_relative_to(qdir_resolved) or not stored.is_file():
            failed.append({'original': str(original), 'reason': 'fichier absent de la quarantaine'})
            continue
        if not original.is_absolute() or not _ancestor_allowed(original, roots):
            failed.append({'original': str(original), 'reason': 'emplacement d\'origine hors des dossiers autorisés'})
            continue
        if original.exists():
            failed.append({'original': str(original), 'reason': 'un fichier existe déjà à cet emplacement'})
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(original))
        except OSError as exc:
            failed.append({'original': str(original), 'reason': f'déplacement impossible ({type(exc).__name__})'})
            continue
        item['status'], item['restored_at'] = 'restored', now_iso()
        _write_manifest(qdir, manifest)
        restored.append({'original': str(original), 'size_mb': round(item.get('size', 0) / 1024 ** 2, 2)})
    remaining = sum(1 for i in manifest.get('items', []) if i.get('status') == 'quarantined')
    return {'run_id': run_id, 'restored': restored, 'failed': failed, 'remaining': remaining,
            'quarantine_dir': str(qdir)}


# --------------------------------------------------------------------------- tools

def _sources() -> list[Source]:
    return [Source(title='Système de fichiers local', kind='local', retrieved_at=now_iso())]


class ScanParams(Params):
    folder: str | None = Field(default=None, max_length=500, validation_alias=AliasChoices('folder', 'root', 'path'))
    older_than_days: float = Field(default=180, ge=0, le=36500)
    min_size_mb: float = Field(default=0, ge=0, le=1_000_000)
    size_default: bool = False     # min_size_mb is the default « gros fichier » threshold (said in the summary)
    limit: int = Field(default=100, ge=1, le=1000)


@tool('files.scan', description='Liste en lecture seule les vieux / gros fichiers d\'un dossier autorisé '
      '(Documents, Téléchargements, Bureau, dossiers ajoutés).', action='files.scan', params=ScanParams,
      provenance='filesystem', timeout_s=60)
def scan_tool(p: ScanParams) -> ToolResult:
    data = scan(p.folder, p.older_than_days, p.min_size_mb, p.limit)
    data['size_default'] = bool(p.size_default and p.min_size_mb)
    return ToolResult(ok=True, data=data, summary=summarize_scan(data), provenance='filesystem', sources=_sources())


class QuarantineParams(Params):
    paths: list[str] = Field(min_length=1, max_length=MAX_QUARANTINE)
    run_id: str = Field(min_length=1, max_length=64)
    expected: list[dict] | None = Field(default=None, max_length=MAX_QUARANTINE)   # approved preview

    @field_validator('run_id', mode='before')
    @classmethod
    def _str(cls, v):
        return str(v) if v is not None else v


@tool('files.quarantine', description='Déplace des fichiers vers la quarantaine AURORA (réversible, permission '
      'obligatoire, dossiers autorisés uniquement).', action='files.quarantine', params=QuarantineParams,
      provenance='filesystem', timeout_s=180)
def quarantine_tool(p: QuarantineParams) -> ToolResult:
    data = quarantine(p.paths, p.run_id, p.expected)
    n, r = len(data['moved']), len(data['refused'])
    if n == 0:
        reasons = fr_list(sorted({x['reason'] for x in data['refused']}))
        msg = f'Aucun fichier déplacé : {reasons}.'
        return ToolResult(ok=False, data=data, summary=msg, provenance='filesystem', sources=_sources(), error=msg)
    summary = (f"{n} fichier(s) déplacé(s) en quarantaine ({_size_text(data['moved_total_mb'])}), restaurables à tout "
               f"moment (run {p.run_id}).")
    if r:
        summary += f' {r} refusé(s) : ' + fr_list(sorted({x["reason"] for x in data['refused']})) + '.'
    return ToolResult(ok=True, data=data, summary=summary + ' Aucun fichier supprimé.', provenance='filesystem',
                      sources=_sources())


class RestoreParams(Params):
    run_id: str = Field(min_length=1, max_length=64)

    @field_validator('run_id', mode='before')
    @classmethod
    def _str(cls, v):
        return str(v) if v is not None else v


@tool('files.restore', description='Remet à leur place les fichiers mis en quarantaine par un run.',
      action='files.quarantine', params=RestoreParams, provenance='filesystem', timeout_s=180)
def restore_tool(p: RestoreParams) -> ToolResult:
    data = restore(p.run_id)
    n, f = len(data['restored']), len(data['failed'])
    if n == 0 and f == 0:
        summary = f'Rien à restaurer pour le run {p.run_id} (déjà restauré).'
        return ToolResult(ok=True, data=data, summary=summary, provenance='filesystem', sources=_sources())
    summary = f'{n} fichier(s) restauré(s) à leur emplacement d\'origine.'
    if f:
        summary += f' {f} non restauré(s) : ' + fr_list(sorted({x["reason"] for x in data['failed']})) + '.'
    ok = n > 0
    return ToolResult(ok=ok, data=data, summary=summary, provenance='filesystem', sources=_sources(),
                      error=None if ok else summary)
