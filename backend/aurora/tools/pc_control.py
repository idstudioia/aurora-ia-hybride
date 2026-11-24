"""Souris et clavier (E2, suite — 9/11/2026, ID Studio IA : « fais-moi le contrôle directement par la souris et le clavier »).

AURORA agit sur le PC un geste à la fois, sur le modèle du terminal avec autorisation (tools/shell.py) :

1. Désactivé par défaut : le réglage ``pc_control_enabled`` (Paramètres › Souris et clavier) ; sans lui, rien ne bouge.
2. Une liste fermée de gestes aux paramètres validés : cliquer, double-cliquer, clic droit, déplacer la souris,
   faire défiler, taper un texte, appuyer sur une touche ou un raccourci de la liste ``KEY_NAMES``.
3. Un aperçu construit par le code (``dry_run=True``) : le geste en mots simples, la fenêtre visée (titre et programme
   lus par Windows), la position, le texte exact ; puis un accord par geste dans l'interface, dans tous les modes.
4. Le geste exécuté est exactement celui de la carte (``gesture_fingerprint``) et la fenêtre visée est relue juste
   avant : si elle a changé, ou si une autre fenêtre est passée sous le point, rien n'est fait.
5. Refusé même avec accord (``protection``) : les terminaux et la fenêtre Exécuter (pas de terminal libre : seul le
   terminal avec autorisation lance des commandes) ; le texte et les touches dans les éditeurs de code (ils
   contiennent un terminal) ; l'application Claude (un clic y validerait ses propres demandes d'accord) ; le
   gestionnaire des tâches, l'éditeur du registre, la console d'administration, Sécurité Windows, les fenêtres de mot
   de passe et de contrôle de compte ; et AURORA elle-même (elle ne clique jamais sur ses propres cartes d'accord).
   La touche Windows n'est pas dans la liste.
6. Ce qu'AURORA lit de l'écran (titres des fenêtres) est une donnée, jamais une consigne.
7. Une instance d'essai (dossier AURORA temporaire : tests, bancs) ne bouge jamais la vraie souris : la leçon
   d'Athéna du 09/10/2026, quand un banc a ouvert YouTube Manager sur l'écran d’ID Studio IA.

Réutilise : l'accord de tools/processes.py (``_permission_granted``, élargi à 'pc.control'), ses noms d'applications
(``ALIASES``, « bloc-notes » -> notepad) et l'effacement des accents de security/untrusted.py (``_fold``).
Seulement sous Windows, sans bibliothèque ajoutée (ctypes et psutil, déjà là). L'écran principal seulement, en
vrais pixels (comme la capture de tools/vision.py).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..security.untrusted import _fold
from .base import Params, Source, ToolError, ToolResult, now_iso, tool
from .processes import ALIASES, _permission_granted, norm_name

MAX_TEXT_CHARS = 500
MAX_SCROLL = 20
GESTURES = ('click', 'double_click', 'right_click', 'move', 'scroll', 'type', 'key')

# --------------------------------------------------------------------------- the closed list of keys

KEY_NAMES: dict[str, tuple[int, str]] = {   # name -> (virtual-key code, label shown on the card)
    'entree': (0x0D, 'Entrée'), 'echap': (0x1B, 'Échap'), 'tab': (0x09, 'Tab'), 'espace': (0x20, 'Espace'),
    'retour': (0x08, 'Retour arrière'), 'suppr': (0x2E, 'Suppr'), 'inser': (0x2D, 'Inser'),
    'haut': (0x26, 'Flèche haut'), 'bas': (0x28, 'Flèche bas'), 'gauche': (0x25, 'Flèche gauche'),
    'droite': (0x27, 'Flèche droite'), 'debut': (0x24, 'Début'), 'fin': (0x23, 'Fin'),
    'page_haut': (0x21, 'Page précédente'), 'page_bas': (0x22, 'Page suivante'),
    **{f'f{i}': (0x6F + i, f'F{i}') for i in range(1, 13)},
}
MODIFIERS: dict[str, tuple[int, str]] = {'ctrl': (0x11, 'Ctrl'), 'maj': (0x10, 'Maj'), 'alt': (0x12, 'Alt')}
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
_SYNONYMS = {
    'enter': 'entree', 'return': 'entree', 'valider': 'entree', 'validation': 'entree',
    'esc': 'echap', 'escape': 'echap', 'echappe': 'echap', 'echappement': 'echap', 'tabulation': 'tab',
    'space': 'espace', 'barre espace': 'espace', 'barre d espace': 'espace', 'backspace': 'retour',
    'retour arriere': 'retour', 'effacer': 'retour', 'supprimer': 'suppr', 'suppression': 'suppr', 'delete': 'suppr',
    'del': 'suppr', 'insert': 'inser', 'insertion': 'inser', 'up': 'haut', 'down': 'bas', 'left': 'gauche',
    'right': 'droite', 'fleche haut': 'haut', 'fleche du haut': 'haut', 'fleche vers le haut': 'haut',
    'fleche bas': 'bas', 'fleche du bas': 'bas', 'fleche vers le bas': 'bas', 'fleche gauche': 'gauche',
    'fleche de gauche': 'gauche', 'fleche droite': 'droite', 'fleche de droite': 'droite', 'home': 'debut',
    'end': 'fin', 'page up': 'page_haut', 'pageup': 'page_haut', 'page precedente': 'page_haut',
    'page down': 'page_bas', 'pagedown': 'page_bas', 'page suivante': 'page_bas',
    'control': 'ctrl', 'controle': 'ctrl', 'ctl': 'ctrl', 'shift': 'maj', 'majuscule': 'maj', 'majuscules': 'maj',
}
_FORBIDDEN_KEYS = {'win', 'windows', 'touche windows', 'super', 'meta', 'cmd', 'menu demarrer', 'demarrer', 'start'}
_FORBIDDEN_COMBOS = {
    frozenset(('ctrl', 'alt', 'suppr')): 'Ctrl+Alt+Suppr (écran de sécurité de Windows)',
    frozenset(('ctrl', 'maj', 'echap')): 'Ctrl+Maj+Échap (gestionnaire des tâches)',
}
# What a key does, said by the code (rule 3) — only the ones whose effect is well known.
_KEY_EFFECTS = {
    ('entree',): 'valide (comme un clic sur le bouton par défaut)', ('echap',): 'annule ou ferme un menu',
    ('suppr',): 'supprime l’élément ou le texte sélectionné', ('alt', 'f4'): 'ferme la fenêtre (le travail non '
    'enregistré peut être perdu)', ('ctrl', 'w'): 'ferme l’onglet ou le document', ('ctrl', 's'): 'enregistre',
    ('ctrl', 'z'): 'annule la dernière action', ('ctrl', 'y'): 'rétablit l’action annulée',
    ('ctrl', 'c'): 'copie la sélection', ('ctrl', 'x'): 'coupe la sélection', ('ctrl', 'v'): 'colle le presse-papiers',
    ('ctrl', 'a'): 'sélectionne tout', ('ctrl', 'f'): 'ouvre la recherche', ('ctrl', 'p'): 'ouvre l’impression',
    ('ctrl', 'n'): 'ouvre un nouveau document ou une nouvelle fenêtre', ('ctrl', 't'): 'ouvre un nouvel onglet',
    ('alt', 'tab'): 'passe à une autre fenêtre', ('tab',): 'passe au champ suivant',
}


def fold(text: str) -> str:
    """security.untrusted._fold (lower case, no accents), plus single spaces and a plain apostrophe."""
    return ' '.join(_fold(text).replace('’', "'").split())


def parse_keys(spec: str) -> tuple[list[str], str]:
    """« Ctrl + S », « alt f4 », « la touche Entrée » -> (['ctrl', 's'], '') or ([], why it is refused)."""
    s = fold(spec)
    s = re.sub(r"^(?:sur )?(?:la |les )?(?:touches? |raccourci |combinaison )?", '', s).strip(" .!'\"«»")
    if not s:
        return [], 'Aucune touche indiquée.'
    words = [w for w in re.split(r'\s*\+\s*|\s+(?:puis|et)\s+|\s+', s.replace("'", ' ')) if w]
    names: list[str] = []
    i = 0
    while i < len(words):
        for size in (3, 2, 1):
            if i + size > len(words):
                continue
            chunk = ' '.join(words[i:i + size])
            if chunk in _FORBIDDEN_KEYS:
                return [], 'La touche Windows n’est pas autorisée (elle ouvre le menu Démarrer et la fenêtre Exécuter).'
            name = _SYNONYMS.get(chunk, chunk)
            if name in KEY_NAMES or name in MODIFIERS or re.fullmatch(r'[a-z0-9]', name):
                names.append(name)
                i += size
                break
        else:
            sample = ', '.join(label for _code, label in list(KEY_NAMES.values())[:15])
            return [], f'Touche inconnue : « {words[i]} ». Touches possibles : {sample}, F1 à F12, Ctrl, Alt, Maj.'
    mods = [n for n in names if n in MODIFIERS]
    keys = [n for n in names if n not in MODIFIERS]
    if len(keys) != 1:
        return [], 'Un raccourci compte une seule touche principale (par exemple Ctrl+S ou Entrée).'
    if len(set(mods)) != len(mods) or len(names) > 4:
        return [], 'Raccourci invalide (touche répétée ou trop de touches).'
    if re.fullmatch(r'[a-z0-9]', keys[0]) and not ({'ctrl', 'alt'} & set(mods)):
        return [], 'Une lettre ou un chiffre seul s’écrit avec « tape dans <fenêtre> : … », pas comme une touche.'
    ordered = [m for m in ('ctrl', 'alt', 'maj') if m in mods] + keys
    combo = _FORBIDDEN_COMBOS.get(frozenset(ordered))
    if combo:
        return [], f'Raccourci interdit même avec accord : {combo}.'
    return ordered, ''


def key_label(names: list[str]) -> str:
    return '+'.join(MODIFIERS[n][1] if n in MODIFIERS else KEY_NAMES[n][1] if n in KEY_NAMES else n.upper()
                    for n in names)


def key_effect(names: list[str]) -> str:
    return _KEY_EFFECTS.get(tuple(names), '')


# --------------------------------------------------------------------------- protected windows (rule 5)

_TERMINAL = 'un terminal (pas de terminal libre : seul le terminal avec autorisation lance des commandes)'
PROTECTED_PROGRAMS: dict[str, str] = {
    **{exe: _TERMINAL for exe in ('cmd.exe', 'powershell.exe', 'powershell_ise.exe', 'pwsh.exe', 'windowsterminal.exe',
                                  'openconsole.exe', 'conhost.exe', 'wt.exe', 'bash.exe', 'wsl.exe', 'wslhost.exe',
                                  'mintty.exe', 'alacritty.exe', 'wezterm-gui.exe', 'conemu.exe', 'conemu64.exe',
                                  'hyper.exe', 'tabby.exe', 'putty.exe')},
    'regedit.exe': 'l’éditeur du registre de Windows',
    'taskmgr.exe': 'le gestionnaire des tâches (arrêter un programme passe par sa propre demande d’accord)',
    'mmc.exe': 'une console d’administration de Windows',
    'consent.exe': 'le contrôle de compte de Windows', 'credentialuibroker.exe': 'une fenêtre de mot de passe de Windows',
    'logonui.exe': 'l’écran de connexion de Windows', 'lockapp.exe': 'l’écran de verrouillage de Windows',
    'sechealthui.exe': 'Sécurité Windows (l’antivirus)', 'securityhealthhost.exe': 'Sécurité Windows (l’antivirus)',
    # A click there could approve one of Claude's own requests to run a command: an AI never approves for another.
    'claude.exe': 'l’application Claude (elle lance des commandes sur ce PC après ses propres demandes d’accord)',
}
# Text and keys only: these programs hold a terminal where typed text followed by Entrée would run a command.
TYPING_PROTECTED: dict[str, str] = {
    exe: 'un éditeur de code qui contient un terminal' for exe in (
        'code.exe', 'code - insiders.exe', 'cursor.exe', 'windsurf.exe', 'idea64.exe', 'pycharm64.exe',
        'webstorm64.exe', 'devenv.exe', 'sublime_text.exe', 'zed.exe')
}
_TERMINAL_CLASSES = {'consolewindowclass', 'cascadia_hosting_window_class', 'pseudoconsolewindow'}
AURORA_TITLE = 'aurora — assistant ia hybride'   # frontend/index.html <title>, never changed by the interface
_RUN_TITLES = {'executer', 'run'}


def protection(window: dict, gesture: str) -> str:
    """Why a gesture on this window is refused even with the user's accord ('' when it is allowed)."""
    exe = str(window.get('exe') or '').lower()
    title = fold(window.get('title') or '')
    cls = str(window.get('class') or '').lower()
    if AURORA_TITLE in title:
        return 'la fenêtre d’AURORA elle-même (elle ne clique ni ne tape jamais sur ses propres cartes d’accord)'
    if exe in PROTECTED_PROGRAMS:
        return PROTECTED_PROGRAMS[exe]
    if cls in _TERMINAL_CLASSES:
        return _TERMINAL
    if exe == 'explorer.exe' and cls == '#32770' and title in _RUN_TITLES:
        return 'la fenêtre Exécuter de Windows (elle lance n’importe quel programme)'
    if gesture in ('type', 'key') and exe in TYPING_PROTECTED:
        return TYPING_PROTECTED[exe]
    return ''


# --------------------------------------------------------------------------- the real Windows input (one backend)

class WindowsInput:
    """Windows calls only (user32 through ctypes). Tests replace ``BACKEND`` with a fake: nothing real moves."""

    def __init__(self) -> None:
        self._ready = False

    def _user32(self):
        if sys.platform != 'win32':
            raise ToolError('La souris et le clavier ne sont pilotés que sous Windows.')
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        if not self._ready:
            try:
                user32.SetProcessDPIAware()   # the real pixels, as the capture of tools/vision.py
            except (AttributeError, OSError):
                pass
            for fn, res, args in (
                    (user32.WindowFromPoint, wintypes.HWND, [wintypes.POINT]),
                    (user32.GetAncestor, wintypes.HWND, [wintypes.HWND, wintypes.UINT]),
                    (user32.GetWindowTextLengthW, ctypes.c_int, [wintypes.HWND]),
                    (user32.GetWindowTextW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
                    (user32.GetClassNameW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
                    (user32.GetWindowThreadProcessId, wintypes.DWORD, [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]),
                    (user32.GetWindowRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]),
                    (user32.IsWindow, wintypes.BOOL, [wintypes.HWND]),
                    (user32.IsWindowVisible, wintypes.BOOL, [wintypes.HWND]),
                    (user32.IsIconic, wintypes.BOOL, [wintypes.HWND]),
                    (user32.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int]),
                    (user32.SetForegroundWindow, wintypes.BOOL, [wintypes.HWND]),
                    (user32.BringWindowToTop, wintypes.BOOL, [wintypes.HWND]),
                    (user32.GetForegroundWindow, wintypes.HWND, []),
                    (user32.AttachThreadInput, wintypes.BOOL, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]),
                    (user32.SetCursorPos, wintypes.BOOL, [ctypes.c_int, ctypes.c_int]),
                    (user32.GetCursorPos, wintypes.BOOL, [ctypes.POINTER(wintypes.POINT)]),
                    (user32.SendInput, wintypes.UINT, [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]),
                    (user32.MapVirtualKeyW, wintypes.UINT, [wintypes.UINT, wintypes.UINT]),
                    (user32.GetSystemMetrics, ctypes.c_int, [ctypes.c_int])):
                fn.restype, fn.argtypes = res, args
            self._ready = True
        return user32, wintypes

    # --- reading (no effect on the screen) ---------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        user32, _ = self._user32()
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))

    def cursor(self) -> tuple[int, int]:
        user32, wintypes = self._user32()
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ToolError('Position de la souris illisible.')
        return int(point.x), int(point.y)

    def window_info(self, hwnd: int) -> dict:
        user32, wintypes = self._user32()
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, title, length + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        exe = ''
        try:
            import psutil
            exe = psutil.Process(int(pid.value)).name()
        except Exception:
            exe = ''
        return {'hwnd': int(hwnd), 'title': title.value, 'class': cls.value, 'pid': int(pid.value), 'exe': exe,
                'rect': [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)],
                'minimized': bool(user32.IsIconic(hwnd))}

    def window_at(self, x: int, y: int) -> dict | None:
        user32, wintypes = self._user32()
        child = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        root = user32.GetAncestor(child, 2) if child else None   # GA_ROOT: the top-level window
        return self.window_info(root) if root else None

    def open_windows(self) -> list[dict]:
        """Visible top-level windows with a title, not cloaked (hidden modern apps), in Z order."""
        user32, wintypes = self._user32()
        found: list[int] = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and not _cloaked(hwnd):
                found.append(int(hwnd))
            return True

        user32.EnumWindows(proc(collect), 0)
        return [self.window_info(h) for h in found]

    def exists(self, hwnd: int) -> bool:
        user32, _ = self._user32()
        return bool(user32.IsWindow(hwnd))

    def foreground(self) -> int:
        user32, _ = self._user32()
        return int(user32.GetForegroundWindow() or 0)

    # --- acting (only after the checks of perform()) -----------------------------------------

    def bring_to_front(self, hwnd: int) -> bool:
        user32, _ = self._user32()
        kernel32 = ctypes.windll.kernel32
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        front = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(front, None) if front else 0
        attached = bool(theirs and theirs != mine and user32.AttachThreadInput(theirs, mine, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(theirs, mine, False)
        for _ in range(20):
            if int(user32.GetForegroundWindow() or 0) == int(hwnd):
                return True
            time.sleep(0.05)
        return False

    def move_to(self, x: int, y: int) -> None:
        user32, _ = self._user32()
        if not user32.SetCursorPos(int(x), int(y)):
            raise ToolError('Windows a refusé de déplacer la souris.')

    def press_buttons(self, button: str, clicks: int) -> None:
        down, up = (0x0008, 0x0010) if button == 'droit' else (0x0002, 0x0004)
        inputs = []
        for _ in range(clicks):
            inputs += [_mouse(down), _mouse(up)]
        self._send_input(inputs)

    def roll_wheel(self, notches: int) -> None:
        self._send_input([_mouse(0x0800, data=int(notches) * 120)])   # MOUSEEVENTF_WHEEL, WHEEL_DELTA

    def press_keys(self, names: list[str]) -> None:
        user32, _ = self._user32()
        codes = [MODIFIERS[n][0] if n in MODIFIERS else KEY_NAMES[n][0] if n in KEY_NAMES else ord(n.upper())
                 for n in names]
        down = [_key(c, user32.MapVirtualKeyW(c, 0), up=False) for c in codes]
        up = [_key(c, user32.MapVirtualKeyW(c, 0), up=True) for c in reversed(codes)]
        self._send_input(down + up)

    def type_text(self, text: str) -> None:
        user32, _ = self._user32()
        enter = user32.MapVirtualKeyW(0x0D, 0)
        for ch in text:
            if ch == '\n':
                self._send_input([_key(0x0D, enter, up=False), _key(0x0D, enter, up=True)])
            else:
                units = ch.encode('utf-16-le')
                for i in range(0, len(units), 2):
                    code = int.from_bytes(units[i:i + 2], 'little')
                    self._send_input([_unicode(code, up=False), _unicode(code, up=True)])
            time.sleep(0.004)   # a few ms per character: slow applications miss nothing

    def _send_input(self, inputs: list) -> None:
        user32, _ = self._user32()
        array = (_INPUT * len(inputs))(*inputs)
        sent = user32.SendInput(len(inputs), ctypes.byref(array), ctypes.sizeof(_INPUT))
        if sent != len(inputs):
            raise ToolError('Windows a bloqué le geste (fenêtre d’un niveau de sécurité supérieur ?).')


def _cloaked(hwnd: int) -> bool:
    try:
        value = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 14, ctypes.byref(value),
                                                   ctypes.sizeof(value))   # DWMWA_CLOAKED
        return bool(value.value)
    except Exception:
        return False


if sys.platform == 'win32':
    from ctypes import wintypes as _wt

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', _wt.LONG), ('dy', _wt.LONG), ('mouseData', _wt.DWORD), ('dwFlags', _wt.DWORD),
                    ('time', _wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [('wVk', _wt.WORD), ('wScan', _wt.WORD), ('dwFlags', _wt.DWORD), ('time', _wt.DWORD),
                    ('dwExtraInfo', ctypes.c_size_t)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [('uMsg', _wt.DWORD), ('wParamL', _wt.WORD), ('wParamH', _wt.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [('mi', _MOUSEINPUT), ('ki', _KEYBDINPUT), ('hi', _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [('type', _wt.DWORD), ('u', _UNION)]

    def _mouse(flags: int, data: int = 0):
        return _INPUT(type=0, u=_UNION(mi=_MOUSEINPUT(0, 0, ctypes.c_uint32(data).value, flags, 0, 0)))

    def _key(vk: int, scan: int, *, up: bool):
        flags = (0x0002 if up else 0) | (0x0001 if vk in _EXTENDED else 0)   # KEYUP, EXTENDEDKEY
        return _INPUT(type=1, u=_UNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0)))

    def _unicode(code: int, *, up: bool):
        return _INPUT(type=1, u=_UNION(ki=_KEYBDINPUT(0, code, 0x0004 | (0x0002 if up else 0), 0, 0)))


BACKEND = WindowsInput()


# --------------------------------------------------------------------------- settings and test instances

def pc_control_enabled() -> bool:
    from .. import config  # lazy: owned by another module
    try:
        return bool(config.load_settings().get('pc_control_enabled'))
    except Exception:
        return False


def trial_instance() -> bool:
    """A temporary AURORA folder (tests, benches, test servers) never moves the real mouse (rule 7), unless the
    person running a manual test sets AURORA_PC_REAL_INPUT=1 on purpose."""
    if os.getenv('AURORA_PC_REAL_INPUT', '').strip() == '1':
        return False
    home = os.getenv('AURORA_HOME', '').strip()
    if not home:
        return False
    try:
        Path(home).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return True
    except (ValueError, OSError):
        return False


# --------------------------------------------------------------------------- the plan of one gesture

def say_window(window: dict | None) -> str:
    if not window:
        return 'aucune fenêtre'
    exe = f" ({window['exe']})" if window.get('exe') else ''
    return f"« {window.get('title') or 'sans titre'} »{exe}"


def find_window(name: str, backend=None) -> tuple[dict | None, str]:
    """The open window named by the person (a part of its title, or its program: « Bloc-notes » is notepad.exe
    through processes.ALIASES) -> (window, '') or (None, why)."""
    backend = backend or BACKEND
    wanted = fold(name).strip(' «»"\'')
    if not wanted:
        return None, 'Nom de fenêtre vide.'
    program = ALIASES.get(wanted, norm_name(wanted))
    every = backend.open_windows()
    exact = [w for w in every if fold(w.get('title')) == wanted]
    found = exact or [w for w in every if wanted in fold(w.get('title'))
                      or norm_name(str(w.get('exe') or '')) == program]
    if not found:
        return None, f'Aucune fenêtre ouverte ne s’appelle « {name} ».'
    if len(found) > 1:
        titles = ', '.join(f"« {w.get('title')} »" for w in found[:5])
        return None, f'Plusieurs fenêtres correspondent à « {name} » : {titles}. Précisez avec une partie du titre.'
    return found[0], ''


def gesture_fingerprint(plan: dict) -> str:
    """The gesture exactly as shown on the card: what is done, where, and in which window (rule 4)."""
    window = plan.get('window') or {}
    keyed = {k: plan.get(k) for k in ('gesture', 'x', 'y', 'button', 'clicks', 'keys', 'text', 'notches')}
    keyed['window'] = {k: window.get(k) for k in ('hwnd', 'exe', 'title')}
    return hashlib.sha256(json.dumps(keyed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def build_plan(p: 'GestureParams', backend=None) -> dict:
    """The gesture resolved by the code (window, position, keys): what the card shows. Reads only, never acts.
    Raises ToolError with the reason when the gesture is impossible or refused."""
    backend = backend or BACKEND
    gesture = p.gesture
    width, height = backend.screen_size()
    plan: dict = {'gesture': gesture, 'origin': p.origin, 'screen': [width, height], 'x': None, 'y': None,
                  'button': None, 'clicks': None, 'keys': None, 'text': None, 'notches': None, 'window': None}
    window = None
    if p.window:
        window, why = find_window(p.window, backend)
        if not window:
            raise ToolError(why)
    if gesture in ('click', 'double_click', 'right_click', 'move', 'scroll'):
        x, y = p.x, p.y
        if (x is None) != (y is None):
            raise ToolError('Il faut les deux coordonnées : x et y (par exemple « clique en 640, 400 »).')
        if x is None:
            if gesture != 'scroll' or not window:
                raise ToolError('Position manquante : donnez-la en pixels (par exemple « clique en 640, 400 »).')
            left, top, right, bottom = window['rect']
            x, y = (left + right) // 2, (top + bottom) // 2
        if not (0 <= x < width and 0 <= y < height):
            raise ToolError(f'Le point ({x}, {y}) est hors de l’écran principal ({width} × {height} pixels).')
        plan['x'], plan['y'] = int(x), int(y)
        if window:
            left, top, right, bottom = window['rect']
            if not window.get('minimized') and not (left <= x < right and top <= y < bottom):
                raise ToolError(f'Le point ({x}, {y}) n’est pas dans la fenêtre {say_window(window)}.')
        else:
            window = backend.window_at(x, y)
            if not window:
                raise ToolError(f'Aucune fenêtre sous le point ({x}, {y}).')
        if gesture in ('click', 'double_click', 'right_click'):
            plan['button'] = 'droit' if gesture == 'right_click' else 'gauche'
            plan['clicks'] = 2 if gesture == 'double_click' else 1
        if gesture == 'scroll':
            plan['notches'] = (p.amount or 3) * (-1 if (p.direction or 'bas') == 'bas' else 1)
    elif gesture == 'type':
        text = p.text or ''
        if not text:
            raise ToolError('Aucun texte à taper (écrivez-le après deux-points : « tape dans Bloc-notes : bonjour »).')
        if any((ord(c) < 32 and c != '\n') or 0x7F <= ord(c) < 0xA0 for c in text):
            raise ToolError('Le texte contient un caractère de contrôle : il n’est pas tapé.')
        plan['text'] = text
    elif gesture == 'key':
        names, why = parse_keys(p.keys or '')
        if why:
            raise ToolError(why)
        plan['keys'] = names
    if gesture in ('type', 'key') and not window:
        raise ToolError('Dites dans quelle fenêtre (« tape dans Bloc-notes : bonjour », « appuie sur Entrée dans '
                        'Bloc-notes ») : AURORA ne tape jamais dans sa propre fenêtre.')
    reason = protection(window, gesture)
    if reason:
        raise ToolError(f'Refusé même avec accord : la fenêtre visée {say_window(window)} est {reason}.',
                        data={'protected': True, 'window': {k: window.get(k) for k in ('title', 'exe')}})
    plan['window'] = {k: window.get(k) for k in ('hwnd', 'title', 'exe', 'class', 'rect', 'minimized')}
    plan['does'] = describe_gesture(plan)
    plan['risk'], plan['risk_label'] = gesture_risk(plan)
    plan['undo'] = ('Un geste ne s’annule pas tout seul ; dans la plupart des applications, Ctrl+Z annule une saisie '
                    'ou une modification.')
    plan['fingerprint'] = gesture_fingerprint(plan)
    return plan


def describe_gesture(plan: dict) -> str:
    """The gesture in plain words, by the code (rule 3)."""
    gesture, where = plan['gesture'], say_window(plan.get('window'))
    at = f"en ({plan.get('x')}, {plan.get('y')})"
    if gesture == 'click':
        return f'Cliquer (bouton gauche) {at}, dans la fenêtre {where}.'
    if gesture == 'double_click':
        return f'Double-cliquer {at}, dans la fenêtre {where}.'
    if gesture == 'right_click':
        return f'Clic droit {at}, dans la fenêtre {where} (ouvre en général un menu).'
    if gesture == 'move':
        return f'Déplacer la souris {at}, au-dessus de la fenêtre {where}, sans cliquer.'
    if gesture == 'scroll':
        way = 'vers le bas' if (plan.get('notches') or 0) < 0 else 'vers le haut'
        return f'Faire défiler {way} de {abs(plan.get("notches") or 0)} crans {at}, dans la fenêtre {where}.'
    if gesture == 'type':
        text = plan.get('text') or ''
        lines = text.count('\n')
        extra = (f' ; {lines} retour(s) à la ligne, tapés avec la touche Entrée (ce qui peut valider ou envoyer)'
                 if lines else '')
        return f'Taper {len(text)} caractère(s) dans la fenêtre {where}, comme au clavier{extra}.'
    names = plan.get('keys') or []
    effect = key_effect(names)
    return f'Appuyer sur {key_label(names)} dans la fenêtre {where}' + (f' : {effect}.' if effect else '.')


def gesture_risk(plan: dict) -> tuple[str, str]:
    gesture = plan['gesture']
    if gesture == 'move':
        return 'faible', 'déplace seulement la souris'
    if gesture == 'scroll':
        return 'faible', 'fait seulement défiler'
    if gesture == 'key':
        if tuple(plan.get('keys') or []) in (('alt', 'f4'), ('ctrl', 'w'), ('suppr',)):
            return 'élevé', 'peut fermer ou supprimer'
        return 'moyen', 'agit dans l’application comme au clavier'
    if gesture == 'type' and '\n' in (plan.get('text') or ''):
        return 'élevé', 'tape puis valide avec Entrée'
    return 'moyen', 'agit dans l’application (un clic peut valider, envoyer ou supprimer)'


def card_matches(permission_id: int | None, plan: dict, action: str = 'pc.control') -> bool:
    """processes._permission_granted for ``action``, and the granted card is exactly this plan (fingerprint).
    ``action``: 9/11/2026, also a YouTube comment (tools/youtube_comments.py, 'youtube.comment')."""
    if permission_id is None or not _permission_granted(permission_id, action):
        return False
    try:
        from ..security import consent  # lazy: owned by another module
        perm = consent.get(permission_id) or {}
    except Exception:
        return False
    return str((perm.get('preview') or {}).get('fingerprint') or '') == plan.get('fingerprint')


def perform(plan: dict, backend=None) -> dict:
    """Do the gesture of the card (after every check). Returns what Windows shows right after."""
    backend = backend or BACKEND
    window = plan['window']
    hwnd = int(window['hwnd'])
    if not backend.exists(hwnd):
        raise ToolError(f'La fenêtre {say_window(window)} est fermée depuis l’aperçu : rien n’a été fait.')
    now = backend.window_info(hwnd)   # checked before the window is even brought to the front
    if (now.get('title'), now.get('exe')) != (window.get('title'), window.get('exe')) or protection(now, plan['gesture']):
        raise ToolError(f'La fenêtre visée a changé depuis l’aperçu (maintenant {say_window(now)}) : rien n’a été fait.')
    if not backend.bring_to_front(hwnd):
        raise ToolError(f'Windows n’a pas laissé passer la fenêtre {say_window(window)} au premier plan : rien '
                        'n’a été fait (cliquez une fois dessus, puis redemandez).')
    gesture = plan['gesture']
    if plan.get('x') is not None:
        under = backend.window_at(plan['x'], plan['y'])
        if not under or int(under.get('hwnd') or 0) != hwnd:
            raise ToolError(f"Une autre fenêtre ({say_window(under)}) est au point ({plan['x']}, {plan['y']}) : "
                            'rien n’a été fait.')
        backend.move_to(plan['x'], plan['y'])
        if gesture in ('click', 'double_click', 'right_click'):
            backend.press_buttons(plan['button'], plan['clicks'])
        elif gesture == 'scroll':
            backend.roll_wheel(plan['notches'])
    elif gesture == 'type':
        backend.type_text(plan['text'])
    elif gesture == 'key':
        backend.press_keys(plan['keys'])
    after = backend.window_info(hwnd) if backend.exists(hwnd) else None
    return {'done': True, 'window_after': {k: after.get(k) for k in ('title', 'exe')} if after else None}


# --------------------------------------------------------------------------- the tools

class GestureParams(Params):
    gesture: Literal['click', 'double_click', 'right_click', 'move', 'scroll', 'type', 'key']
    x: int | None = Field(default=None, ge=0, le=100000)
    y: int | None = Field(default=None, ge=0, le=100000)
    text: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    keys: str | None = Field(default=None, max_length=60)
    direction: Literal['haut', 'bas'] | None = None
    amount: int = Field(default=3, ge=1, le=MAX_SCROLL)
    window: str | None = Field(default=None, max_length=160)
    dry_run: bool = False
    origin: str = Field(default='user', max_length=20)
    permission_id: int | None = None
    approved_fingerprint: str | None = Field(default=None, max_length=64)


def _input_sources() -> list[Source]:
    return [Source(title='Souris et clavier de Windows (user32)', kind='local', retrieved_at=now_iso())]


@tool('pc.gesture', description='Un geste de la souris ou du clavier sur le PC (cliquer, double-cliquer, clic droit, '
      'déplacer, faire défiler, taper un texte, une touche de la liste fermée), après autorisation ; désactivé par '
      'défaut ; terminaux, fenêtres de sécurité et AURORA refusés même avec accord.', action='pc.control',
      params=GestureParams, provenance='pc_control', timeout_s=60)
def gesture_tool(p: GestureParams) -> ToolResult:
    prov = 'pc_control'
    if not pc_control_enabled():
        msg = ('La souris et le clavier sont désactivés. Activez-les dans Paramètres › Souris et clavier pour '
               'qu’AURORA puisse agir sur le PC (chaque geste vous sera montré et attendra votre accord).')
        return ToolResult(ok=False, data={'disabled': True, 'gesture': p.gesture}, summary=msg, provenance=prov,
                          sources=_input_sources(), error=msg)
    plan = build_plan(p)   # a ToolError (impossible or protected) becomes an honest failure in run_tool
    if p.dry_run:
        return ToolResult(ok=True, data=plan, summary=plan['does'], provenance=prov, sources=_input_sources())
    if p.approved_fingerprint is not None and p.approved_fingerprint != plan['fingerprint']:
        raise ToolError('Le geste ou la fenêtre visée a changé depuis l’aperçu autorisé : rien n’a été fait.')
    if not card_matches(p.permission_id, plan):
        msg = 'Autorisation absente, refusée, ou donnée pour un autre geste : rien n’a été fait.'
        return ToolResult(ok=False, data=plan, summary=msg, provenance=prov, sources=_input_sources(), error=msg)
    if trial_instance():
        msg = f'Instance d’essai : je n’ai rien fait. Sur le vrai AURORA, j’aurais fait ce geste : {plan["does"]}'
        return ToolResult(ok=False, data={**plan, 'trial': True}, summary=msg, provenance=prov,
                          sources=_input_sources(), error=msg)
    done = perform(plan)
    return ToolResult(ok=True, data={**plan, **done}, summary=f'Fait : {plan["does"]}', provenance=prov,
                      sources=_input_sources())


@tool('pc.pointer', description='Position de la souris et fenêtre sous elle (lecture seule, mesurée par Windows).',
      action='system.read', provenance='pc_control', timeout_s=10)
def pointer_tool(_p: Params) -> ToolResult:
    x, y = BACKEND.cursor()
    width, height = BACKEND.screen_size()
    window = BACKEND.window_at(x, y)
    data = {'x': x, 'y': y, 'screen': [width, height],
            'window': {k: window.get(k) for k in ('title', 'exe')} if window else None}
    return ToolResult(ok=True, data=data, summary=f'La souris est en ({x}, {y}), sur la fenêtre {say_window(window)}.',
                      provenance='pc_control', sources=_input_sources())
