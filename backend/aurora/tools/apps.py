"""Ouvrir une application de la liste autorisée (Paramètres › Applications autorisées), 9/11/2026.

ID Studio IA : « tu peux me faire ouvrir des applications choisies dans une liste où je mets les applications autorisées
dans la configuration ». L'idée vient du lanceur d'Athéna (lanceurApplications.ts, lu seulement, jamais modifié) :
- une liste FERMÉE, tenue par la personne dans Paramètres : une phrase mal comprise, une page web ou un document
  ne peuvent rien ouvrir d'autre (le nom doit être exactement celui de la liste) ;
- l'agent n'a pas cet outil (agent.actions), seule une demande écrite ou dite par la personne l'appelle ;
- une application qui a une adresse locale et y répond « tourne déjà » : rien n'est relancé ;
- un fichier disparu : rien n'est lancé, la réponse le dit ;
- une instance d'essai (tests, serveurs de test) ne lance jamais rien (pc_control.trial_instance, réutilisé).
La liste vaut accord : pas de nouvelle carte à chaque ouverture (security/policy.py, 'app.launch'), mais chaque
ouverture laisse sa trace dans le journal des étapes. Un terminal, l'éditeur du registre ou une fenêtre de sécurité
de Windows (pc_control.PROTECTED_PROGRAMS, réutilisé) n'entrent jamais dans la liste.

Le lancement passe par explorer.exe, comme un double-clic : l'application reçoit l'environnement de la session
Windows, pas celui d'AURORA (son jeton, ses variables).

13/11/2026, ID Studio IA : « brancher mon logiciel Creality à AURORA […] pour faire une impression ». « Ouvre le tibia dans
Creality Print » donne à une application de la liste le fichier d'UN modèle 3D gardé par AURORA (STL ou OBJ : ce
qu'un logiciel d'impression 3D ouvre). Le programme est alors lancé lui-même avec ce seul fichier, comme Windows le
fait pour un double-clic sur le fichier. Le chemin vient toujours du dossier des modèles d'AURORA, jamais de la
phrase. AURORA ne règle rien, ne tranche pas et ne lance aucune impression : cela reste dans l'application.

13/11/2026 encore, ID Studio IA, après avoir installé FreeCAD 1.1 et KiCad 10 : « FreeCAD et KiCad d'abord ». « Ouvre le tibia
du quadrupède dans FreeCAD » donne au logiciel de conception de la liste UN fichier déclaré par un projet
(tools/projets.py) : une pièce STEP pour FreeCAD, un projet KiCad pour KiCad. Le logiciel est reconnu au nom de son
programme (freecad.exe, kicad.exe), le chemin vient du projet.json, jamais de la phrase. AURORA ne modifie pas le
fichier : ce qui s'y change se fait dans le logiciel, par la personne.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import Field

from .base import Params, Source, ToolError, ToolResult, now_iso, tool
from .pc_control import PROTECTED_PROGRAMS, trial_instance

MAX_APPS = 30
MAX_NAME_CHARS = 60
SUFFIXES = ('.exe', '.bat', '.cmd', '.lnk', '.url')
LOCAL_URL = re.compile(r'^http://(?:localhost|127\.0\.0\.1)(?::\d{1,5})?(?:/\S*)?$')
WAIT_S = 10.0          # after the launch, how long the local address is watched before saying it does not answer yet
_ENV_DROP_PREFIXES = ('AURORA_', 'ANTHROPIC_', 'OLLAMA_')
_ENV_DROP = {'PORT', 'NODE_OPTIONS', 'VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONUTF8'}
_ARTICLE = re.compile(r'^(?:l|le|la|les|mon|ma|mes|notre) ')
# Said by the tool when asked to open something, and by the tool map as what it lacks (ToolSpec.missing).
EMPTY_LIST = ('Votre liste d’applications autorisées est vide : ajoutez-y une application dans Paramètres › '
              'Applications autorisées (son nom et le chemin de son programme).')
# 13/11/2026 (ID Studio IA : « brancher mon logiciel Creality à AURORA […] pour faire une impression ») : « ouvre le tibia dans
# Creality Print » donne à une application de la liste le fichier d'UN modèle 3D gardé par AURORA (tools/lab.py).
PRINTABLE = ('.stl', '.obj')   # ce qu'un trancheur d'imprimante 3D ouvre parmi les modèles gardés (pas un GLB)
_IN_APP = re.compile(r'^(?:(?:modele(?: 3d)?|fichier|piece) (?:de |du |d )?)?(?P<model>.+) (?:dans|avec|sur) '
                     r'(?:l |le |la |mon |ma |notre )?(?:application |appli |app |logiciel |programme )?(?:de |d |du )?'
                     r'(?P<app>.+)$')
# 13/11/2026 (ID Studio IA : « FreeCAD et KiCad d'abord ») : les logiciels de conception qui reçoivent un fichier de PROJET.
# La clé est le nom du programme sans « .exe » ; puis le nom dit à la personne et ce que le logiciel ouvre.
CAO = {'freecad': ('FreeCAD', ('.fcstd', '.step', '.stp')), 'kicad': ('KiCad', ('.kicad_pro',))}
# 15/11/2026 (ID Studio IA : « il nous faut la connexion au moteur FreeCAD et KiCad ») : le moteur sans fenêtre de chacun, rangé
# à côté du programme lui-même, et les fichiers de projet qu'il sait vérifier (tools/moteurs_cao.py).
MOTEURS = {'freecad': ('freecadcmd.exe', ('.fcstd', '.step', '.stp')), 'kicad': ('kicad-cli.exe', ('.kicad_sch', '.kicad_pcb'))}
_PROJECT_FILLERS = {'de', 'du', 'd', 'des', 'le', 'la', 'l', 'projet'}   # « le tibia DU PROJET quadrupède »


def app_key(name: str) -> str:
    """The name as compared: normalised words (accents, case and punctuation folded), without a leading article."""
    from ..core.normalize import normalize   # lazy: the core modules import the tools
    words = ' '.join(re.sub(r'[^a-z0-9]+', ' ', normalize(str(name or ''))).split())
    return _ARTICLE.sub('', words).strip()


def allowed_apps() -> list[dict]:
    from .. import config  # lazy: owned by another module
    try:
        value = config.load_settings().get('allowed_apps')
    except Exception:
        return []
    return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []


def find(name: str, apps: list[dict]) -> dict | None:
    """The app of the list with this name; else the ONE app whose name starts with these words (« youtube manager »
    for « YouTube Manager IA », « vlc » for « VLC media player »: 3 letters at least, conversation 144 où « ouvre
    VLC » était refusé), never a page name of AURORA (« ouvre monde » stays the globe). None otherwise."""
    wanted = app_key(name)
    if not wanted:
        return None
    exact = next((a for a in apps if app_key(a.get('name', '')) == wanted), None)
    if exact or len(wanted) < 3:
        return exact
    from ..core import rules   # lazy, like clean_list
    if re.fullmatch(rf'(?:page |onglet )?{rules.PAGE}', wanted):
        return None
    starts = [a for a in apps if app_key(a.get('name', '')).startswith(wanted + ' ')]
    return starts[0] if len(starts) == 1 else None


def program_of(app: dict) -> Path:
    """The program an app of the list runs: its own file, or the target of its shortcut."""
    program = Path(str(app.get('path') or ''))
    if program.suffix.lower() == '.lnk':
        program = Path(shortcut_target(program)[0] or program)
    return program


def design_tool(app: dict) -> str | None:
    """'freecad' or 'kicad' when this app runs that program (a key of CAO), whatever the list calls it; else None."""
    stem = program_of(app).stem.lower()
    return stem if stem in CAO else None


def engine_of(app: dict) -> Path | None:
    """The windowless engine of a design tool of the list (freecadcmd next to FreeCAD, kicad-cli next to KiCad): its
    file when it is there, else None. The engine is used only through an app the person has allowed."""
    tool = design_tool(app)
    engine = program_of(app).parent / MOTEURS[tool][0] if tool else None
    return engine if engine is not None and engine.is_file() else None


def project_file(words: str, tool: str) -> dict | None:
    """The ONE file declared by a project (projets.fichiers) that these words name, among those this design tool
    opens: by its name in the project or by its file name, alone (« tibia ») or followed by words of its project
    (« tibia du quadrupède », « tibia du projet robot-quadrupede »). None when they name no file or several."""
    from . import projets   # lazy: the projects are owned by another tool
    wanted, found = app_key(words), {}
    for f in projets.fichiers():
        if Path(f['chemin']).suffix.lower() not in CAO[tool][1]:
            continue
        project = set(app_key(f"{f['titre']} {f['projet']}").split())
        for key in {app_key(f['nom']), app_key(Path(f['chemin']).stem)}:
            rest = set(wanted[len(key):].split()) - _PROJECT_FILLERS if wanted.startswith(key + ' ') else set()
            if key and (wanted == key or (rest and rest <= project)):
                found[(f['projet'], f['chemin'])] = f
    return next(iter(found.values())) if len(found) == 1 else None


def model_in_app(name: str, apps: list[dict]) -> dict | None:
    """« le tibia dans Creality Print » (13/11/2026) : {'app': the name of the list, 'model': the id of the ONE 3D model
    kept by AURORA that these words name}. None when the words after « dans / avec / sur » are not an app of the
    list, or when the words before name no model or several: nothing is guessed, the usual routing goes on.
    For FreeCAD or KiCad, a file of a project comes first: {'app', 'projet', 'fichier'} (project_file)."""
    cut = _IN_APP.match(app_key(name))
    app = find(cut.group('app'), apps) if cut else None
    if app is None:
        return None
    tool = design_tool(app)
    kept = project_file(cut.group('model'), tool) if tool else None
    if kept:
        return {'app': app['name'], 'projet': kept['projet'], 'fichier': kept['chemin']}
    from . import lab   # lazy: the models are owned by another tool
    wanted, folder = app_key(cut.group('model')), lab.models_dir()
    exact, starts = [], []
    for scene in lab.catalogue():
        if scene.get('origine') not in ('importe', 'reconstruit'):
            continue   # only a model that came with a file can be handed over
        keys = {app_key(scene['titre']), *(app_key(m) for m in scene['mots'])}
        if wanted in keys:
            exact.append(scene['id'])
        elif len(wanted) >= 3 and any(k.startswith(wanted + ' ') for k in keys):
            starts.append(scene['id'])
    # The GLB that is shown and the STL to print often share a name (« Tibia quadrupede », « Tibia quadrupede 2 »):
    # the model a slicer can open comes first, by its exact name, else by its first words.
    opens = lambda ids: [i for i in ids if any((folder / f'{i}{ext}').is_file() for ext in PRINTABLE)]   # noqa: E731
    for found in (opens(exact), opens(starts), exact, starts):   # none can be opened: the one named (the tool says why)
        if found:
            return {'app': app['name'], 'model': found[0]} if len(found) == 1 else None
    return None


def match(text: str) -> dict | None:
    """The slots of a request about an app of the list (« ouvre YouTube Manager », « utilise l'application YouTube
    Manager pour mettre un commentaire », « je veux pas utiliser l'application YouTube Manager »): the extracted slots
    (normalize.extract_app), the name replaced by the one of the list ('app', or 'refused' when it is said with a
    negation: nothing is opened). None when no name of the list is asked (the usual routing goes on)."""
    from ..core.normalize import as_written, extract_app   # lazy, like app_key
    slots = extract_app(text)
    key = 'refused' if slots.get('refused') else 'app'
    if not slots.get(key):
        return None
    apps = allowed_apps()
    app = find(slots[key], apps)
    if app is None and key == 'app':   # 13/11/2026: « ouvre le tibia dans Creality Print »
        handed = model_in_app(slots[key], apps)
        if handed:
            return {**slots, **handed}
    if app is None:
        # « avec l'application YouTube Manager mets un commentaire »: no « pour » after the name, the first words
        # are the name of the list and the rest (two words at least, not « et OBS ») is what to do in it.
        words = app_key(slots[key]).split()
        for k in range(len(words) - 2, 0, -1):
            app = find(' '.join(words[:k]), apps) if words[k] not in ('et', 'ou') else None
            if app:
                slots = {**slots, 'task': slots.get('task') or as_written(text, ' '.join(words[k:]))}
                break
    return {**slots, key: app['name']} if app else None


def clean_list(value) -> list[dict]:
    """The validated list for config._validate. Raises ValueError with a French message."""
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ValueError('allowed_apps doit être une liste d’applications {nom, chemin, adresse}.')
    if len(value) > MAX_APPS:
        raise ValueError(f'Au plus {MAX_APPS} applications autorisées.')
    from ..core import rules   # lazy: the page names of the interface (« ouvre la mémoire » stays a page)
    out, seen = [], set()
    for raw in value:
        name = str(raw.get('name') or '').strip()
        if not 1 <= len(name) <= MAX_NAME_CHARS:
            raise ValueError(f'Nom d’application invalide (1 à {MAX_NAME_CHARS} caractères).')
        key = app_key(name)
        if not key:
            raise ValueError(f'Le nom « {name} » ne contient ni lettre ni chiffre.')
        if key in seen:
            raise ValueError(f'Deux applications portent le même nom : « {name} ».')
        if re.fullmatch(rf'(?:la |le |l |les )?(?:page |onglet )?{rules.PAGE}', key):
            raise ValueError(f'« {name} » est le nom d’une page d’AURORA : choisissez un autre nom.')
        seen.add(key)
        path = Path(str(raw.get('path') or '').strip().strip('"')).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f'Programme introuvable ou chemin non absolu pour « {name} » : {raw.get("path")}')
        if path.suffix.lower() not in SUFFIXES:
            raise ValueError(f'« {path.name} » : seuls les programmes {", ".join(SUFFIXES)} sont acceptés.')
        reason = why_refused(path)
        if reason:
            raise ValueError(f'« {path.name} » est {reason} : il ne peut pas être ouvert par AURORA.')
        url = str(raw.get('url') or '').strip()
        if url and not LOCAL_URL.match(url):
            raise ValueError(f'Adresse de « {name} » : seulement http://localhost ou http://127.0.0.1 (ou vide).')
        out.append({'name': name, 'path': str(path.resolve()), 'url': url})
    return out


def answers(url: str, timeout: float = 1.5) -> bool:
    """True when the local address answers (any HTTP status: something listens there). Never a proxy."""
    if not url or not LOCAL_URL.match(url):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def launch_env() -> dict[str, str]:
    """AURORA's environment without its own variables (token, home, ports, Python of the venv)."""
    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith(_ENV_DROP_PREFIXES) and k.upper() not in _ENV_DROP}


def start(path: str, file: str | None = None) -> None:
    """Like a double-click in the Explorer. With ``file`` (13/11/2026): the program itself, given this ONE file to
    open, as Windows does for a double-click on a file of that program. Replaced in the tests: nothing is ever
    launched there."""
    subprocess.Popen([path, file] if file else ['explorer.exe', path], env=launch_env(), cwd=str(Path(path).parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- the apps of this PC (9/11/2026)
# ID Studio IA, after the first version: « tu peux pas ajouter les liens dans applications autorisées plutôt avec une liste
# de mes applications sur mon PC ». The shortcuts of the Desktop and of the Start menu are offered in Paramètres;
# one click adds one to the list (which stays the only accord). Nothing is ever launched by this listing.

PROJECT_ROOT = Path(__file__).resolve().parents[3]   # AURORA's own folder: never opened by itself
# Athéna's launcher list (lanceurApplications.ts, read only): its address, so « tourne déjà » works at once.
KNOWN_ADDRESSES = {'youtube manager': 'http://localhost:3000'}
_SCRIPT_IN_ARGS = re.compile(r'\.(?:bat|cmd|ps1|py|pyw|js|mjs|vbs)\b', re.I)
_TERMINAL_WORDS = re.compile(r'\b(?:command prompt|invite de commandes?|powershell|terminal|console|bash|shell|cmd)\b')
_NOISE_WORDS = re.compile(r'\b(?:uninstall|uninstaller|desinstaller|desinstallation|install|installer|installation|'
                          r'read me|readme|lisez moi|documentation|(?<!google )docs?|manuals?|manuel|help|aide|'
                          r'website|site web|site officiel|official site|release notes|notes de version|faq|'
                          r'frequently asked|license|licence|samples?|support center|bug report|reset)\b')
_NOISE_FILES = ('uninst', 'documentation', 'readme')   # in the target's file name (« …ToolsDocumentation.url »)
# A bare interpreter is a console (« Python 3.11 (64-bit) », « Node.js »): refused like a terminal. With a script
# (« IDLE » = pythonw.exe idle.pyw), it is that program's launcher.
INTERPRETERS = {'python.exe', 'pythonw.exe', 'py.exe', 'pyw.exe', 'node.exe', 'ruby.exe', 'perl.exe', 'php.exe',
                'lua.exe', 'deno.exe', 'bun.exe'}
_SYSTEM_FOLDERS = {'administrative tools', 'windows administrative tools', 'windows tools', 'system tools',
                   'outils d administration windows', 'outils windows', 'outils systeme windows', 'startup', 'demarrage'}
MAX_PC_APPS = 400


def _read_string(block: bytes, offset: int, wide: bool) -> str:
    if not 0 < offset < len(block):
        return ''
    if wide:
        end = offset
        while end + 1 < len(block) and block[end:end + 2] != b'\x00\x00':
            end += 2
        return block[offset:end].decode('utf-16-le', errors='replace')
    end = block.find(b'\x00', offset)
    return block[offset:end if end >= 0 else len(block)].decode('mbcs' if os.name == 'nt' else 'latin-1',
                                                              errors='replace')


def shortcut_target(path: Path) -> tuple[str, str]:
    """(target, arguments) of a Windows shortcut (.lnk), read from the file itself (format MS-SHLLINK, no COM and no
    program run). ('', '') when unreadable, or for a shortcut to a Store app (no file target)."""
    try:
        data = Path(path).read_bytes()[:131072]
    except OSError:
        return '', ''
    if len(data) < 76 or data[:4] != b'\x4c\x00\x00\x00':
        return '', ''
    flags = int.from_bytes(data[20:24], 'little')
    pos, target = 76, ''
    if flags & 0x01:   # HasLinkTargetIDList
        pos += 2 + int.from_bytes(data[pos:pos + 2], 'little')
    if flags & 0x02 and pos + 28 <= len(data):   # HasLinkInfo: LocalBasePath (+ CommonPathSuffix)
        info = data[pos:pos + int.from_bytes(data[pos:pos + 4], 'little')]
        header = int.from_bytes(info[4:8], 'little')
        if int.from_bytes(info[8:12], 'little') & 0x01:   # VolumeIDAndLocalBasePath
            wide = header >= 0x24
            base = _read_string(info, int.from_bytes(info[28:32], 'little'), True) if wide else ''
            base = base or _read_string(info, int.from_bytes(info[16:20], 'little'), False)
            suffix = _read_string(info, int.from_bytes(info[24:28], 'little'), False)
            target = base + suffix
        pos += len(info)
    strings: dict[str, str] = {}
    wide = bool(flags & 0x80)   # IsUnicode
    for bit, key in ((0x04, 'name'), (0x08, 'relative'), (0x10, 'workdir'), (0x20, 'args'), (0x40, 'icon')):
        if flags & bit and pos + 2 <= len(data):
            count = int.from_bytes(data[pos:pos + 2], 'little') * (2 if wide else 1)
            raw = data[pos + 2:pos + 2 + count]
            strings[key] = raw.decode('utf-16-le' if wide else 'latin-1', errors='replace')
            pos += 2 + count
    if not target and strings.get('relative'):
        target = os.path.normpath(os.path.join(os.path.dirname(str(path)), strings['relative']))
    return target, strings.get('args', '')


def _inside_aurora(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(PROJECT_ROOT)
        return True
    except (ValueError, OSError):
        return False


def why_refused(path: Path) -> str:
    """Why this program can never be in the list ('' when it can): AURORA itself, a protected program
    (pc_control.PROTECTED_PROGRAMS: terminals, Registry Editor, Task Manager, security windows), or a shortcut that
    opens one of them bare. A shortcut that runs a SCRIPT through a terminal (« cmd /k start_youtube.bat », the
    Desktop icon of YouTube Manager) is that script's launcher, so it is accepted."""
    path = Path(path)
    if _inside_aurora(path):
        return 'AURORA elle-même'
    reason = PROTECTED_PROGRAMS.get(path.name.lower())
    if reason:
        return reason
    if path.name.lower() in INTERPRETERS:
        return _INTERPRETER
    if path.suffix.lower() == '.lnk':
        if _TERMINAL_WORDS.search(app_key(path.stem)):
            return 'un raccourci vers un terminal'
        target, args = shortcut_target(path)
        if target and _inside_aurora(Path(target)):
            return 'un raccourci vers AURORA elle-même'
        exe = Path(target).name.lower() if target else ''
        reason = PROTECTED_PROGRAMS.get(exe) or (_INTERPRETER if exe in INTERPRETERS else None)
        if reason and not _SCRIPT_IN_ARGS.search(args):
            return f'un raccourci vers {reason}'
    return ''


_INTERPRETER = 'un interpréteur de commandes (une console : pas de terminal libre)'


def _pc_folders() -> list[tuple[str, Path, bool]]:
    """(source shown, folder, recursive): the Desktops (the person's, OneDrive's, the shared one), the Start menus."""
    home = Path(os.environ.get('USERPROFILE') or Path.home())
    folders = [('Bureau', home / 'Desktop', False), ('Bureau', home / 'OneDrive' / 'Desktop', False),
               ('Bureau', home / 'OneDrive' / 'Bureau', False),
               ('Bureau', Path(os.environ.get('PUBLIC') or r'C:\Users\Public') / 'Desktop', False)]
    for env in ('APPDATA', 'ProgramData'):
        if os.environ.get(env):
            folders.append(('Menu Démarrer', Path(os.environ[env]) / 'Microsoft' / 'Windows' / 'Start Menu' /
                            'Programs', True))
    return folders


def pc_apps() -> dict:
    """The applications of this PC to choose from in Paramètres: the shortcuts and programs of the Desktop, the
    shortcuts of the Start menu. Left aside, and counted: terminals and protected programs (why_refused),
    uninstallers and documentation, the system tools of Windows, broken shortcuts. Read only: nothing is launched."""
    from ..core import rules   # lazy: a page name of AURORA cannot be an app name (clean_list)
    found: dict[str, dict] = {}
    targets: set[tuple[str, str]] = set()   # (target, arguments) already offered: « OBS Studio (64bit) » = « OBS Studio »
    noise = 'installation, désinstallation ou documentation'
    skipped = {'terminal ou outil protégé': 0, noise: 0, 'outil système de Windows': 0, 'raccourci cassé': 0}
    for source, folder, recursive in _pc_folders():
        if not folder.is_dir():
            continue
        suffixes = ('.lnk', '.url') if recursive else SUFFIXES
        files = folder.rglob('*') if recursive else folder.iterdir()
        for path in files:
            try:
                if path.suffix.lower() not in suffixes or not path.is_file():
                    continue
            except OSError:
                continue
            parts = {app_key(p) for p in path.relative_to(folder).parts[:-1]}
            if parts & _SYSTEM_FOLDERS:
                skipped['outil système de Windows'] += 1
                continue
            name = re.sub(r'\s+-\s+(?:raccourci|shortcut)$', '', path.stem, flags=re.I).strip()[:MAX_NAME_CHARS]
            key = app_key(name)
            if not key or key in found:
                continue
            if key in _SYSTEM_FOLDERS:
                skipped['outil système de Windows'] += 1
                continue
            target, args = shortcut_target(path) if path.suffix.lower() == '.lnk' else ('', '')
            if _NOISE_WORDS.search(key) or any(w in Path(target or path).name.lower() for w in _NOISE_FILES):
                skipped[noise] += 1
                continue
            if re.fullmatch(rf'(?:la |le |l |les )?(?:page |onglet )?{rules.PAGE}', key) or why_refused(path):
                skipped['terminal ou outil protégé'] += 1
                continue
            if target and not Path(target).exists():
                skipped['raccourci cassé'] += 1
                continue
            if target and (target.lower(), args.strip()) in targets:
                continue
            targets.add((target.lower(), args.strip()))
            detail = path.name
            if path.suffix.lower() == '.lnk':
                script = _SCRIPT_IN_ARGS.search(args)
                if script:
                    word = args[:script.end()].replace('"', ' ').split()[-1]
                    detail = f'lance {Path(word).name}'
                elif target:
                    detail = Path(target).name
            url = next((u for k, u in KNOWN_ADDRESSES.items() if key == k or key.startswith(k + ' ')), '')
            found[key] = {'name': name, 'path': str(path), 'source': source, 'detail': detail, 'url': url}
            if len(found) >= MAX_PC_APPS:
                break
    apps_found = sorted(found.values(), key=lambda a: app_key(a['name']))
    return {'apps': apps_found, 'skipped': {k: v for k, v in skipped.items() if v},
            'folders': [str(f) for _, f, _ in _pc_folders() if f.is_dir()]}


def design_tools() -> list[dict]:
    """FreeCAD and KiCad for the page « Projets »: the app of the allowed list that runs each ('autorise': it alone
    can be given a project file), else the shortcuts of this PC that would ('sur_ce_pc', to add in Paramètres).
    Read only: nothing is launched, and no program is run to ask its version."""
    allowed, seen, out = allowed_apps(), None, []
    for key, (label, extensions) in CAO.items():
        listed = [a for a in allowed if design_tool(a) == key]
        if not listed and seen is None:
            seen = pc_apps()['apps']
        out.append({'id': key, 'nom': label, 'ouvre': list(extensions), 'autorise': listed[0]['name'] if listed else None,
                    'sur_ce_pc': [] if listed else [a['name'] for a in seen if design_tool(a) == key],
                    # Son moteur sans fenêtre : les fichiers qu'il vérifie, et s'il est là (à côté du programme autorisé).
                    'verifie': list(MOTEURS[key][1]), 'moteur': bool(listed and engine_of(listed[0]))})
    return out


class LaunchParams(Params):
    name: str = Field(min_length=1, max_length=120)
    # 13/11/2026: the id of ONE 3D model kept by AURORA, given to the application to open (never a free path).
    model: str | None = Field(default=None, min_length=1, max_length=60)
    # 13/11/2026: ONE file declared by a project (its id, the path as its projet.json writes it), for FreeCAD or KiCad.
    projet: str | None = Field(default=None, pattern=r'^[a-z0-9][a-z0-9-]{1,39}$')
    fichier: str | None = Field(default=None, min_length=1, max_length=200)


def _sources(app: dict | None = None) -> list[Source]:
    title = 'Liste des applications autorisées (Paramètres)'
    return [Source(title=f"{title} › {app['name']}" if app else title, kind='local', retrieved_at=now_iso())]


@tool('app.launch', description='Ouvrir une application de la liste autorisée par la personne (Paramètres › '
      'Applications autorisées), par son nom exact ; rien d’autre ne peut être lancé.', action='app.launch',
      params=LaunchParams, provenance='apps', timeout_s=30,
      missing=lambda: None if allowed_apps() else EMPTY_LIST)
def launch_tool(p: LaunchParams) -> ToolResult:
    prov = 'apps'
    apps = allowed_apps()
    if not apps:
        msg = f'{EMPTY_LIST} Rien n’a été lancé.'
        return ToolResult(ok=False, data={'empty': True, 'asked': p.name}, summary=msg, provenance=prov,
                          sources=_sources(), error=msg)
    names = [a['name'] for a in apps]
    app = find(p.name, apps)
    if app is None:
        raise ToolError(f'« {p.name} » n’est pas dans votre liste d’applications autorisées '
                        f'({", ".join(f"« {n} »" for n in names)}). Rien n’a été lancé.',
                        data={'unknown': p.name, 'allowed': names})
    data = {'name': app['name'], 'path': app['path'], 'url': app.get('url') or ''}
    if p.projet:
        # 13/11/2026: a file DECLARED by a project (projets.fichier: never a free path), for the design tool of the
        # list that opens it. AURORA only hands it over: what changes in it is done in the tool, by the person.
        from . import projets   # lazy: the projects are owned by another tool
        kept = projets.fichier(p.projet, p.fichier or '')
        if kept is None:
            raise ToolError(f'Le projet « {p.projet} » ne déclare aucun fichier « {p.fichier} ». Rien n’a été lancé.',
                            data=data)
        handed, what, tool = kept['path'], f"« {kept['nom']} » du projet « {kept['titre']} »", design_tool(app)
        data = {**data, 'projet': kept['projet'], 'fichier': kept['chemin'], 'title': kept['nom'], 'file': handed.name}
        if tool is None or handed.suffix.lower() not in CAO[tool][1]:
            known = ' ou à '.join(f"{label} ({', '.join(exts)})" for label, exts in CAO.values())
            raise ToolError(f"« {app['name']} » n’ouvre pas un fichier {handed.suffix} de projet : je ne donne un "
                            f'fichier de projet qu’à {known}. Rien n’a été lancé.', data=data)
        if not kept['present']:
            redo = ' Il est fabriqué par un script du projet : relancez ce script.' if kept['genere'] else ''
            raise ToolError(f"Le fichier « {kept['chemin']} » du projet « {kept['titre']} » n’est pas sur le disque."
                            f'{redo} Rien n’a été lancé.', data=data)
        after = ('Ce que vous y changez se fait dans le logiciel, par vous : AURORA ne modifie pas ce fichier.'
                 + (' Il est refait par un script du projet : enregistrez vos retouches sous un autre nom.'
                    if kept['genere'] else ''))
    elif p.model:
        # 13/11/2026: the file is one of AURORA's own models (lab.model_path checks the name: no folder, no « .. »),
        # and the program is the one of the list, or the target of its shortcut. A script takes no file.
        from . import lab   # lazy: the models are owned by another tool
        scene = lab.load_scene(p.model)
        handed = lab.model_path(str((scene['pieces'][0].get('forme') or {}).get('fichier') or ''))
        what = f"« {scene['titre']} »"
        data = {**data, 'model': scene['id'], 'title': scene['titre'], 'file': handed.name}
        if handed.suffix.lower() not in PRINTABLE:
            raise ToolError(f"« {scene['titre']} » est un fichier {handed.suffix} : je ne donne à une application que "
                            f"des fichiers {' ou '.join(PRINTABLE)}, ceux qu’un logiciel d’impression 3D ouvre. "
                            'Donnez à AURORA le fichier STL de la pièce. Rien n’a été lancé.', data=data)
        after = 'Les réglages, le tranchage et le lancement de l’impression se font dans l’application, par vous.'
    if p.projet or p.model:
        program = Path(app['path'])
        if not program.is_file():
            raise ToolError(f"Le programme de « {app['name']} » est introuvable ({app['path']}) : rien n’a été lancé. "
                            'Corrigez son chemin dans Paramètres › Applications autorisées.', data=data)
        program = program_of(app)
        if program.suffix.lower() != '.exe' or not program.is_file() or why_refused(program):
            raise ToolError(f"« {app['name']} » ne peut pas recevoir un fichier : il faut un programme (.exe) ou un "
                            'raccourci vers un programme. Rien n’a été lancé.', data=data)
        if trial_instance():
            msg = (f"Instance d’essai : je n’ai rien lancé. Sur le vrai AURORA, j’aurais ouvert {what} "
                   f"({handed.name}) dans « {app['name']} ».")
            return ToolResult(ok=False, data={**data, 'trial': True}, summary=msg, provenance=prov,
                              sources=_sources(app), error=msg)
        try:
            start(str(program), str(handed))
        except OSError as exc:
            raise ToolError(f"Windows n’a pas pu ouvrir « {app['name']} » ({type(exc).__name__}) : rien n’a été "
                            'lancé.', data=data) from None
        msg = (f"J’ai demandé à Windows d’ouvrir {what} ({handed.name}) dans « {app['name']} ». Je ne "
               f'peux pas vérifier que le fichier s’y est ouvert. {after}')
        return ToolResult(ok=True, data={**data, 'started': True}, summary=msg, provenance=prov, sources=_sources(app))
    if data['url'] and answers(data['url']):
        msg = f"« {app['name']} » tourne déjà : elle répond sur {data['url']}. Rien n’a été relancé."
        return ToolResult(ok=True, data={**data, 'already_running': True}, summary=msg, provenance=prov,
                          sources=_sources(app))
    if not Path(app['path']).is_file():
        raise ToolError(f"Le programme de « {app['name']} » est introuvable ({app['path']}) : rien n’a été lancé. "
                        'Corrigez son chemin dans Paramètres › Applications autorisées.', data=data)
    if trial_instance():
        msg = (f"Instance d’essai : je n’ai rien lancé. Sur le vrai AURORA, j’aurais ouvert « {app['name']} » "
               f"({app['path']}).")
        return ToolResult(ok=False, data={**data, 'trial': True}, summary=msg, provenance=prov,
                          sources=_sources(app), error=msg)
    try:
        start(app['path'])
    except OSError as exc:
        raise ToolError(f"Windows n’a pas pu ouvrir « {app['name']} » ({type(exc).__name__}) : rien n’a été "
                        'lancé.', data=data) from None
    if not data['url']:
        msg = (f"J’ai demandé à Windows d’ouvrir « {app['name']} » ({app['path']}). Sans adresse locale dans la "
               'liste, je ne peux pas vérifier qu’elle a fini de s’ouvrir.')
        return ToolResult(ok=True, data={**data, 'started': True, 'answering': None}, summary=msg,
                          provenance=prov, sources=_sources(app))
    deadline = time.monotonic() + WAIT_S
    up = False
    while time.monotonic() < deadline:
        if answers(data['url'], timeout=1.0):
            up = True
            break
        time.sleep(0.5)
    msg = (f"« {app['name']} » est ouverte : elle répond sur {data['url']}." if up else
           f"J’ai demandé à Windows d’ouvrir « {app['name']} ». Son adresse {data['url']} ne répond pas encore "
           f'après {WAIT_S:g} s : elle démarre peut-être encore.')
    return ToolResult(ok=True, data={**data, 'started': True, 'answering': up}, summary=msg, provenance=prov,
                      sources=_sources(app))
