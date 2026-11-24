"""Paths, environment variables and constants (docs/CONTRACT.md §1).

Every value is read from the environment at call time so tests can redirect
AURORA_HOME / AURORA_DB_PATH after import. Upper-case module constants are
import-time snapshots kept for convenience.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import __version__

VERSION: str = __version__

# --- environment -----------------------------------------------------------------

DEFAULT_OLLAMA_URL = 'http://127.0.0.1:11434'
DEFAULT_LOCAL_MODEL = 'qwen3.5:9b'
DEFAULT_LOCAL_FAST_MODEL = 'qwen3.5:4b'
DEFAULT_EMBED_MODEL = 'bge-m3'
DEFAULT_CLOUD_MODELS = {
    'rapide': 'claude-haiku-5-5',     # routage, demandes simples
    'standard': 'claude-sonnet-5-5',  # défaut
    'expert': 'claude-opus-5-5',      # seulement sur demande explicite
}
DEFAULT_ALLOWED_ORIGINS = ('http://127.0.0.1:5173', 'http://localhost:5173')


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def aurora_home() -> Path:
    """Data folder (quarantine, logs, bench results). Created on demand."""
    path = Path(_env('AURORA_HOME', str(Path.home() / '.aurora'))).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    """SQLite database. Defaults to <AURORA_HOME>/aurora.db (= ~/.aurora/aurora.db)."""
    explicit = os.getenv('AURORA_DB_PATH')
    if explicit and explicit.strip():
        return Path(explicit.strip()).expanduser()
    return aurora_home() / 'aurora.db'


def astra_db_path() -> Path:
    """Legacy ASTRA database, read-only source for the one-time import."""
    return Path(_env('ASTRA_DB_PATH', str(Path.home() / '.astra' / 'astra.db'))).expanduser()


def quarantine_dir() -> Path:
    return aurora_home() / 'quarantine'


def logs_dir() -> Path:
    path = aurora_home() / 'logs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def bench_latest_path() -> Path:
    return aurora_home() / 'bench_latest.json'


def artefacts_dir() -> Path:
    """P2 pages (tasks/artefacts.py): one folder per page, one JSON file per version."""
    return aurora_home() / 'artefacts'


def ollama_url() -> str:
    return _env('AURORA_OLLAMA_URL', DEFAULT_OLLAMA_URL).rstrip('/')


# 23/11/2026, PC du garage d’ID Studio IA (GTX 1080 de 8 Go) : l'installateur y prend qwen3.5:4b, mais AURORA démarrait sur
# qwen3.5:9b, qui n'y est pas : la première question échouait jusqu'au changement dans Paramètres. Tant que la
# personne n'a choisi aucun modèle (ni dans Paramètres, ni par AURORA_LOCAL_MODEL), le modèle de départ suit ce qui est
# installé : qwen3.5:9b s'il y est, sinon qwen3.5:4b s'il y est. Le service le note chaque fois qu'il lit la liste
# d'Ollama (note_installed_models). Gardé avec le dossier d'AURORA du moment : un autre dossier (un essai) repart de
# l'origine.
_installed_default: tuple[str, str] | None = None


def local_model() -> str:
    start = DEFAULT_LOCAL_MODEL
    if _installed_default and _installed_default[0] == str(aurora_home()):
        start = _installed_default[1]
    return _env('AURORA_LOCAL_MODEL', start)


def note_installed_models(names) -> str:
    """Ce que le service vient de lire dans Ollama (les noms des modèles installés). Rend le modèle de départ qui
    vaut maintenant. Rien n'est écrit dans les réglages : un modèle choisi par la personne passe toujours devant."""
    global _installed_default
    have = {str(name).lower() for name in names or []}

    def there(model: str) -> bool:
        return model.lower() in have or (':' not in model and f'{model.lower()}:latest' in have)

    lighter = None if there(DEFAULT_LOCAL_MODEL) or not there(DEFAULT_LOCAL_FAST_MODEL) else DEFAULT_LOCAL_FAST_MODEL
    wanted = (str(aurora_home()), lighter) if lighter else None
    if wanted != _installed_default:
        import logging
        logging.getLogger('aurora.config').info(
            'Modèle local de départ : %s', f'{lighter} ({DEFAULT_LOCAL_MODEL} n’est pas installé)' if lighter else DEFAULT_LOCAL_MODEL)
    _installed_default = wanted
    return local_model()


def local_model_chosen() -> bool:
    """La personne a-t-elle choisi son modèle de conversation : une valeur enregistrée dans Paramètres, ou la variable
    AURORA_LOCAL_MODEL."""
    if (os.getenv('AURORA_LOCAL_MODEL') or '').strip():
        return True
    try:
        from . import db
        return bool(db.rows('SELECT 1 FROM settings WHERE key = ?', (SETTING_KEYS['local_model'],)))
    except Exception:
        return False


def local_fast_model() -> str:
    return _env('AURORA_LOCAL_FAST_MODEL', DEFAULT_LOCAL_FAST_MODEL)


def embed_model() -> str:
    return _env('AURORA_EMBED_MODEL', DEFAULT_EMBED_MODEL)


def anthropic_api_key() -> str | None:
    """Server-side only. Never logged, never returned by the API."""
    return anthropic_key_status()[0]


def anthropic_key_status() -> tuple[str | None, str | None]:
    """(key, source): the Windows vault first (CLAUDE.md), then ANTHROPIC_API_KEY, kept as a fallback.
    AURORA_VAULT=0 skips the vault (the tests set it, so a key saved by the user never leaks into them)."""
    if _env('AURORA_VAULT', '1') != '0':
        try:
            from .security import vault   # lazy: Windows only, no import cost elsewhere
            key = vault.read()
        except Exception:
            key = None
        if key:
            return key, 'vault'
    value = os.getenv('ANTHROPIC_API_KEY')
    return (value.strip(), 'env') if value and value.strip() else (None, None)


def cloud_models() -> dict[str, str]:
    return {
        'rapide': _env('AURORA_CLOUD_MODEL_RAPIDE', DEFAULT_CLOUD_MODELS['rapide']),
        'standard': _env('AURORA_CLOUD_MODEL_STANDARD', DEFAULT_CLOUD_MODELS['standard']),
        'expert': _env('AURORA_CLOUD_MODEL_EXPERT', DEFAULT_CLOUD_MODELS['expert']),
    }


def offline() -> bool:
    """AURORA_OFFLINE=1: no network call at all (tests)."""
    return _env('AURORA_OFFLINE', '0').lower() in ('1', 'true', 'yes', 'on')


def allowed_origins() -> list[str]:
    """Exact origins only: '*' is never honoured (it would let any web page read local data)."""
    raw = os.getenv('AURORA_ALLOWED_ORIGINS')
    if not raw or not raw.strip():
        return list(DEFAULT_ALLOWED_ORIGINS)
    items = [item.strip().rstrip('/') for item in raw.split(',') if item.strip()]
    if '*' in items:
        import logging
        logging.getLogger('aurora.config').warning("AURORA_ALLOWED_ORIGINS='*' ignoré : indiquez des origines exactes.")
    return [item for item in items if item != '*']


# Import-time snapshots (prefer the functions above in code that tests reconfigure).
OLLAMA_URL = ollama_url()
LOCAL_MODEL = local_model()
LOCAL_FAST_MODEL = local_fast_model()
EMBED_MODEL = embed_model()
CLOUD_MODELS = cloud_models()
ALLOWED_ORIGINS = allowed_origins()

# --- runtime constants ------------------------------------------------------------

API_HOST = '127.0.0.1'
API_PORT = 8765
INTERNET_CHECK_TTL_S = 60.0
INTERNET_CHECK_TIMEOUT_S = 2.5
INTERNET_CHECK_URLS = ('https://api.open-meteo.com/v1/forecast', 'https://fr.wikipedia.org/')
SAMPLE_INTERVAL_S = 2.0
SSE_PING_S = 15.0
SSE_QUEUE_MAX = 500
MAX_INPUT_CHARS = 12000
MAX_ATTACHMENT_CHARS = 60000

MODES = ('prive', 'equilibre', 'avance')
PAGES = ('home', 'chat', 'search', 'computer', 'world', 'simulations', 'memory', 'tasks', 'settings')

# --- user settings (SQLite `settings` table) ---------------------------------------

# API name -> storage key. The budget key is shared with security/budget.py.
SETTING_KEYS = {
    'mode': 'mode',
    'cloud_daily_budget_usd': 'cloud.daily_budget_usd',
    # Ceilings per mode (CLAUDE.md « Les trois modes »), keys shared with security/budget.py.
    'cloud_request_budget_usd': 'cloud.request_budget_usd',
    'cloud_daily_budget_avance_usd': 'cloud.daily_budget_avance_usd',
    'cloud_request_budget_avance_usd': 'cloud.request_budget_avance_usd',
    'default_city': 'default_city',
    'allowed_roots': 'allowed_roots',
    'allowed_apps': 'apps.allowed',   # 9/11/2026: applications qu'AURORA peut ouvrir (tools/apps.py), vide par défaut
    'local_model': 'local_model',
    'local_fast_model': 'local_fast_model',
    # 13/11/2026 (ID Studio IA : « tu peux changer le modèle local si besoin ») : the model that writes the 3D maquettes,
    # '' = the main one. Two draws of the bench: gemma4:12b builds the hard objects better, three times slower.
    'local_scene_model': 'local_scene_model',
    'semantic_router': 'semantic_router',
    'llm_router': 'llm_router',
    'shell_enabled': 'shell.enabled',   # B2 (04/11/2026): terminal avec autorisation, désactivé par défaut
    'pc_control_enabled': 'pc.enabled',   # 9/11/2026: souris et clavier (tools/pc_control.py), désactivés par défaut
    'youtube_comment_auto': 'youtube.comment_auto',   # 9/11/2026: « Publier sans redemander » (tools/youtube_comments.py)
    # 24/11/2026: OpenSky Network (trafic mondial et secours des avions, tools/sky.py), éteint par défaut. Ses
    # conditions réservent son API à la recherche et à l'enseignement sans but lucratif : chaque personne l'allume
    # elle-même, après les avoir lues (ID Studio IA : « si on le propose […] par utilisateur ? »).
    'opensky_enabled': 'sky.opensky_enabled',
    'theme': 'theme',
    'web_contact': 'web.contact',   # Wikimedia asks for a contact in the user agent (tools/base.py)
    'backup_dir': 'backup.dir',     # folder of the daily copy of the base ('' = backups/ next to it)
    'verify_mods': 'verify.mods',   # level of each answer check, « alerter » or « bloquer » (core/verify.py MODS)
    # 08/11/2026: AURORA opens by itself the tab of the subject (météo, actualités → Monde 3D). Read by the interface.
    'auto_open_tab': 'ui.auto_open_tab',
}


def default_settings() -> dict[str, Any]:
    return {
        'mode': 'equilibre',
        'cloud_daily_budget_usd': 1.0,
        'cloud_request_budget_usd': 0.25,
        'cloud_daily_budget_avance_usd': 3.0,
        'cloud_request_budget_avance_usd': 1.0,
        'default_city': 'Paris',
        'allowed_roots': [],
        'allowed_apps': [],   # [{name, path, url}] : la liste tenue par ID Studio IA vaut accord pour ces seules applications
        'local_model': local_model(),
        'local_fast_model': local_fast_model(),
        'local_scene_model': '',   # the maquettes are written by the main model until ID Studio IA chooses another one
        'semantic_router': _router_default(('SEMANTIC_DEFAULT', 'DEFAULT_SEMANTIC_ROUTER'), False),
        'llm_router': _router_default(('DEFAULT_LLM_ROUTER',), False),
        'shell_enabled': False,   # terminal avec autorisation : désactivé tant qu’ID Studio IA ne l'active pas
        'pc_control_enabled': False,   # souris et clavier : pareil, un geste à la fois après accord
        'youtube_comment_auto': False,   # chaque commentaire YouTube attend l'accord, tant qu’ID Studio IA ne l'active pas
        'opensky_enabled': False,   # OpenSky Network : jamais interrogé tant que la personne ne l'a pas allumé
        'theme': 'dark',
        'web_contact': '',
        'backup_dir': '',
        'verify_mods': {},   # every check « alerter » (_coerce fills them in)
        'auto_open_tab': True,   # ID Studio IA, 08/11/2026 : « toujours, avec un réglage pour couper »
    }


def _router_default(names: tuple[str, ...], fallback: bool) -> bool:
    """Router defaults are decided by the routing bench (core/router.py)."""
    try:
        from .core import router  # lazy: router is written by another module owner
    except Exception:
        return fallback
    for name in names:
        value = getattr(router, name, None)
        if isinstance(value, bool):
            return value
    return fallback


def load_settings() -> dict[str, Any]:
    """Current settings, stored values over defaults (one query)."""
    import json
    from . import db
    result = default_settings()
    by_storage = {storage: api for api, storage in SETTING_KEYS.items()}
    marks = ','.join('?' * len(by_storage))
    try:
        found = db.rows(f'SELECT key, value_json FROM settings WHERE key IN ({marks})', tuple(by_storage))
    except Exception:
        found = []
    for item in found:
        try:
            value = json.loads(item['value_json'])
        except (TypeError, ValueError):
            continue
        if value is not None:
            result[by_storage[item['key']]] = value
    return _coerce(result)


def update_settings(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a partial update. Raises ValueError with a French message."""
    from . import db
    clean = _validate(fields)
    for api_key, value in clean.items():
        db.set_setting(SETTING_KEYS[api_key], value)
    return load_settings()


def _coerce(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    if out.get('mode') not in MODES:
        out['mode'] = 'equilibre'
    try:
        out['cloud_daily_budget_usd'] = float(out['cloud_daily_budget_usd'])
    except (TypeError, ValueError):
        out['cloud_daily_budget_usd'] = 1.0
    for key, fallback in (('cloud_request_budget_usd', 0.25), ('cloud_daily_budget_avance_usd', 3.0),
                          ('cloud_request_budget_avance_usd', 1.0)):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            out[key] = fallback
    if not isinstance(out.get('allowed_roots'), list):
        out['allowed_roots'] = []
    if not isinstance(out.get('allowed_apps'), list):
        out['allowed_apps'] = []
    out['local_scene_model'] = str(out.get('local_scene_model') or '').strip()[:120]
    for key in ('semantic_router', 'llm_router', 'shell_enabled', 'pc_control_enabled', 'youtube_comment_auto',
                'opensky_enabled', 'auto_open_tab'):
        out[key] = bool(out.get(key))
    out['theme'] = 'dark'
    from .core import verify  # lazy: the checks live in core/verify.py
    out['verify_mods'] = verify.levels(out.get('verify_mods'))
    return out


def _embedding_only(model: str) -> bool:
    """The embedding model (or a name of that family) can never be the conversation model."""
    base = model.lower().split(':')[0]
    if base == embed_model().lower().split(':')[0]:
        return True
    try:
        from .core.capabilities import is_embedding_name  # lazy: same heuristic as capabilities.chat_models
        return is_embedding_name(model)
    except Exception:
        return 'embed' in base or base.startswith('bge')


_CONTACT_RE = re.compile(r'^(?:[^@\s;()]+@[^@\s;()]+\.[^@\s;()]+|https?://[^\s;()]+)$')


def _validate(fields: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key not in SETTING_KEYS:
            raise ValueError(f'Réglage inconnu : {key}')
        if key == 'mode':
            if value not in MODES:
                raise ValueError('Mode invalide (prive, equilibre ou avance).')
        elif key in ('cloud_daily_budget_usd', 'cloud_daily_budget_avance_usd'):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError('Budget cloud invalide.') from None
            if not 0 <= value <= 1000:
                raise ValueError('Le budget cloud quotidien doit être compris entre 0 et 1000 $.')
        elif key in ('cloud_request_budget_usd', 'cloud_request_budget_avance_usd'):
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError('Plafond par requête invalide.') from None
            if not 0.01 <= value <= 100:
                raise ValueError('Le plafond de dépense par requête doit être compris entre 0,01 et 100 $.')
        elif key == 'default_city':
            value = str(value).strip()
            if not 1 <= len(value) <= 80:
                raise ValueError('Ville par défaut invalide.')
        elif key == 'allowed_roots':
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError('allowed_roots doit être une liste de chemins.')
            roots = []
            for raw in value:
                path = Path(raw).expanduser()
                if not path.is_absolute() or not path.is_dir():
                    raise ValueError(f'Dossier introuvable ou non absolu : {raw}')
                roots.append(str(path.resolve()))
            value = roots
        elif key == 'allowed_apps':
            from .tools.apps import clean_list   # lazy: the rules of the list live with the tool that uses it
            value = clean_list(value)
        elif key in ('local_model', 'local_fast_model'):
            value = str(value).strip()
            if not 1 <= len(value) <= 120:
                raise ValueError('Nom de modèle invalide.')
            if _embedding_only(value):
                raise ValueError(f'« {value} » est un modèle d’embedding (recherche sémantique) : il ne sait pas '
                                 'converser. Choisissez un modèle de conversation.')
        elif key == 'local_scene_model':   # '' = the main model writes the maquettes
            value = str(value).strip()
            if len(value) > 120:
                raise ValueError('Nom de modèle invalide.')
            if value and _embedding_only(value):
                raise ValueError(f'« {value} » est un modèle d’embedding (recherche sémantique) : il ne sait pas '
                                 'écrire une maquette. Choisissez un modèle de conversation.')
        elif key in ('semantic_router', 'llm_router', 'shell_enabled', 'pc_control_enabled', 'youtube_comment_auto',
                     'opensky_enabled', 'auto_open_tab'):
            if not isinstance(value, bool):
                raise ValueError(f'{key} doit être un booléen.')
        elif key == 'theme':
            if value != 'dark':
                raise ValueError('Seul le thème « dark » est disponible.')
        elif key == 'web_contact':
            value = str(value).strip()
            if value and (len(value) > 200 or not _CONTACT_RE.match(value)):
                raise ValueError('Contact web invalide : une adresse e-mail ou une URL http(s).')
        elif key == 'backup_dir':
            value = str(value).strip()
            if value:
                path = Path(value).expanduser()
                if not path.is_absolute() or not path.is_dir():
                    raise ValueError(f'Dossier de sauvegarde introuvable ou non absolu : {value}')
                value = str(path.resolve())
        elif key == 'verify_mods':
            from .core import verify  # lazy, like _coerce
            if not isinstance(value, dict):
                raise ValueError('verify_mods doit associer chaque contrôle à « alerter » ou « bloquer ».')
            for name, level in value.items():
                if name not in verify.MODS:
                    raise ValueError(f'Contrôle de vérification inconnu : {name}')
                if level not in verify.LEVELS:
                    raise ValueError(f'Niveau invalide pour « {name} » : « alerter » ou « bloquer ».')
            value = {**load_settings()['verify_mods'], **value}   # a partial update keeps the other checks
        clean[key] = value
    return clean
