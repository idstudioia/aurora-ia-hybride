"""Tool registry and runner (CONTRACT §7).

Every measurable answer comes from a tool registered here. ``run_tool`` validates the
parameters with pydantic, runs the tool (sync tools in a worker thread), times it and
never raises: failures come back as ``ToolResult(ok=False)`` with a French message.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger('aurora.tools')

USER_AGENT = 'AURORA/0.2 (assistant local)'
OFFLINE_MESSAGE = "Mode hors ligne : cet outil a besoin d'Internet, aucune requête n'a été envoyée."


# --------------------------------------------------------------------------- data types

@dataclass
class Source:
    title: str
    url: str = ''
    kind: str = ''
    retrieved_at: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ToolResult:
    ok: bool
    data: dict
    summary: str                 # French, factual, built from data
    provenance: str              # 'psutil' | 'open-meteo' | 'wikipedia' | 'usgs' | 'rss:<feed>' | 'scipy' | ...
    sources: list[Source] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'data': self.data,
            'summary': self.summary,
            'provenance': self.provenance,
            'sources': [s.to_dict() if isinstance(s, Source) else dict(s) for s in self.sources],
            'duration_ms': round(float(self.duration_ms), 2),
            'error': self.error,
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    action: str                  # action id of security.policy (system.read, web.fetch, ...)
    params: type[BaseModel]
    run: Callable                # sync or async, receives the validated params instance
    network: bool = False
    deterministic: bool = True
    # Extensions (defaults keep the contract signature valid)
    provenance: str = ''         # used for failures raised before the tool built a result
    timeout_s: float = 30.0
    # 11/11/2026 (carte des outils, core/capabilities.tool_map): what the tool lacks NOW to work (a setting of
    # Paramètres left empty, a key absent…), said by the tool itself in French, or None. Runs nothing, no network.
    missing: Callable[[], str | None] | None = None


class Params(BaseModel):
    """Base class for tool parameters: unknown keys (extra router slots) are ignored."""
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)


class NoParams(Params):
    pass


class ToolError(Exception):
    """Raised inside a tool for an expected failure; ``message_fr`` is shown to the user."""

    def __init__(self, message_fr: str, *, data: dict | None = None, status: int | None = None):
        super().__init__(message_fr)
        self.message_fr = message_fr
        self.data = data or {}
        self.status = status


def failure(message_fr: str, *, provenance: str, data: dict | None = None,
            sources: list[Source] | None = None) -> ToolResult:
    return ToolResult(ok=False, data=data or {}, summary=message_fr, provenance=provenance,
                      sources=sources or [], error=message_fr)


# --------------------------------------------------------------------------- registry

_TOOL_MODULES = ('system', 'diagnose', 'processes', 'calc', 'timeinfo', 'weather', 'web',
                 'news', 'quakes', 'orbit', 'files', 'data', 'studio', 'shell', 'sky', 'events', 'lab',
                 'pc_control',   # 9/11/2026: souris et clavier, désactivés par défaut
                 'apps',         # 9/11/2026: ouvrir une application de la liste autorisée (Paramètres)
                 'youtube_comments',   # 9/11/2026: publier un commentaire YouTube (chaîne reliée, Paramètres › Studio)
                 'projets',      # 13/11/2026: un cahier des charges confronté aux calculs d'un projet
                 'reminders')    # 16/11/2026: un rappel, un minuteur ou un réveil (tasks/reminders.py)
LOAD_ERRORS: dict[str, str] = {}


class _Registry(dict):
    """dict of ToolSpec that imports every tool module on first read access."""

    _lock = threading.RLock()
    _state = 0  # 0 = not loaded, 1 = loading, 2 = loaded

    def _ensure(self) -> None:
        if _Registry._state == 2:
            return
        with _Registry._lock:
            if _Registry._state != 0:
                return  # loaded, or re-entrant access while loading in this thread
            _Registry._state = 1
            try:
                for module in _TOOL_MODULES:
                    try:
                        importlib.import_module(f'{__package__}.{module}')
                    except Exception as exc:  # one broken module must not hide the others
                        LOAD_ERRORS[module] = f'{type(exc).__name__}: {exc}'
                        log.exception('Chargement de l\'outil %s impossible', module)
            finally:
                _Registry._state = 2

    def __getitem__(self, key):
        self._ensure()
        return super().__getitem__(key)

    def __contains__(self, key):
        self._ensure()
        return super().__contains__(key)

    def __iter__(self):
        self._ensure()
        return super().__iter__()

    def __len__(self):
        self._ensure()
        return super().__len__()

    def get(self, key, default=None):
        self._ensure()
        return super().get(key, default)

    def keys(self):
        self._ensure()
        return super().keys()

    def values(self):
        self._ensure()
        return super().values()

    def items(self):
        self._ensure()
        return super().items()

    def __repr__(self) -> str:
        self._ensure()
        return f'REGISTRY({sorted(super().keys())})'


REGISTRY: dict[str, ToolSpec] = _Registry()


def register(spec: ToolSpec) -> ToolSpec:
    dict.__setitem__(REGISTRY, spec.name, spec)
    return spec


def tool(name: str, *, description: str, action: str, params: type[BaseModel] = NoParams,
         network: bool = False, deterministic: bool = True, provenance: str = '',
         timeout_s: float = 30.0, missing: Callable[[], str | None] | None = None) -> Callable[[Callable], Callable]:
    """Decorator registering ``func(params) -> ToolResult`` under ``name``."""

    def decorate(func: Callable) -> Callable:
        register(ToolSpec(name=name, description=description, action=action, params=params, run=func,
                          network=network, deterministic=deterministic, provenance=provenance,
                          timeout_s=timeout_s, missing=missing))
        return func

    return decorate


def list_tools() -> list[dict]:
    """Public description of the registered tools (for planner / prompts / API)."""
    out = []
    for spec in REGISTRY.values():
        out.append({'name': spec.name, 'description': spec.description, 'action': spec.action,
                    'network': spec.network, 'deterministic': spec.deterministic,
                    'params': spec.params.model_json_schema()})
    return sorted(out, key=lambda item: item['name'])


# --------------------------------------------------------------------------- runner

_PYDANTIC_FR = {
    'missing': 'champ requis',
    'int_parsing': 'nombre entier attendu',
    'int_type': 'nombre entier attendu',
    'int_from_float': 'nombre entier attendu',
    'float_parsing': 'nombre attendu',
    'float_type': 'nombre attendu',
    'string_type': 'texte attendu',
    'bool_parsing': 'booléen attendu',
    'list_type': 'liste attendue',
    'string_too_short': 'texte trop court',
    'string_too_long': 'texte trop long',
    'too_long': 'trop d\'éléments',
    'too_short': 'pas assez d\'éléments',
    # 08/11/2026 (scènes 3D, tools/lab.py): strict descriptions
    'extra_forbidden': 'champ non autorisé',
    'finite_number': 'nombre fini attendu',
    'model_type': 'objet attendu',
    'dict_type': 'objet attendu',
    'string_pattern_mismatch': 'format non autorisé',
    'union_tag_not_found': 'champ « type » manquant',
}


def describe_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = '.'.join(str(p) for p in err.get('loc', ()) if p != '__root__') or 'paramètres'
        etype = err.get('type', '')
        ctx = err.get('ctx') or {}
        if etype in _PYDANTIC_FR:
            msg = _PYDANTIC_FR[etype]
        elif etype in ('greater_than_equal', 'greater_than'):
            msg = f"doit être {'≥' if etype.endswith('equal') else '>'} {ctx.get('ge', ctx.get('gt'))}"
        elif etype in ('less_than_equal', 'less_than'):
            msg = f"doit être {'≤' if etype.endswith('equal') else '<'} {ctx.get('le', ctx.get('lt'))}"
        elif etype == 'literal_error':
            msg = f"valeur non autorisée (attendu : {ctx.get('expected', '?')})"
        elif etype == 'union_tag_invalid':
            msg = f"type non autorisé (attendu : {ctx.get('expected_tags', '?')})"
        elif etype == 'value_error':
            msg = str(err.get('msg', '')).removeprefix('Value error, ')
        else:
            msg = str(err.get('msg', 'valeur invalide'))
        parts.append(f'{loc} : {msg}')
    return ' ; '.join(parts)


async def run_tool(name: str, params: dict | None = None) -> ToolResult:
    """Validate, execute (thread if sync), time. Never raises (except task cancellation)."""
    started = time.perf_counter()

    def finish(result: ToolResult) -> ToolResult:
        result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return result

    spec = REGISTRY.get(name)
    if spec is None:
        return finish(failure(f'Outil inconnu : « {name} ».', provenance='aurora'))
    provenance = spec.provenance or name
    if spec.network and is_offline():
        return finish(failure(OFFLINE_MESSAGE, provenance=provenance, data={'offline': True}))
    try:
        model = spec.params.model_validate(params or {})
    except ValidationError as exc:
        return finish(failure(f'Paramètres invalides pour {name} : {describe_validation(exc)}.',
                              provenance=provenance))
    try:
        if inspect.iscoroutinefunction(spec.run):
            awaitable = spec.run(model)
        else:
            awaitable = asyncio.to_thread(spec.run, model)
        result = await asyncio.wait_for(awaitable, timeout=spec.timeout_s)
    except ToolError as exc:
        return finish(failure(exc.message_fr, provenance=provenance, data=exc.data))
    except asyncio.TimeoutError:
        return finish(failure(f"L'outil {name} n'a pas répondu dans le délai imparti ({spec.timeout_s:g} s).",
                              provenance=provenance))
    except httpx.TimeoutException:
        return finish(failure('La source distante ne répond pas (délai dépassé).', provenance=provenance))
    except httpx.HTTPError as exc:
        return finish(failure(f'Source distante injoignable ({type(exc).__name__}).', provenance=provenance))
    except Exception:
        log.exception('Outil %s : erreur inattendue', name)
        return finish(failure(f"Erreur interne de l'outil {name} : la mesure n'a pas pu être faite.",
                              provenance=provenance))
    if isinstance(result, dict):  # tolerate tools returning raw data
        result = ToolResult(ok=True, data=result, summary='', provenance=provenance)
    if not isinstance(result, ToolResult):
        return finish(failure(f"L'outil {name} a renvoyé un résultat inattendu.", provenance=provenance))
    if not result.provenance:
        result.provenance = provenance
    if not result.summary:
        result.summary = result.error or ('Résultat disponible.' if result.ok else 'Échec de l\'outil.')
    if not result.ok and not result.error:
        result.error = result.summary
    return finish(result)


def run_tool_sync(name: str, params: dict | None = None) -> ToolResult:
    """Blocking helper for scripts and sync code paths (no running event loop)."""
    return asyncio.run(run_tool(name, params))


# --------------------------------------------------------------------------- environment

def is_offline() -> bool:
    return os.getenv('AURORA_OFFLINE', '0').strip().lower() in ('1', 'true', 'yes', 'oui')


def get_setting(key: str, default: Any = None) -> Any:
    """db.get_setting when the DB layer provides it, else ``default`` (never raises)."""
    try:
        from .. import db  # lazy: db.py is owned by another module
        getter = getattr(db, 'get_setting', None)
        if getter is None:
            return default
        value = getter(key, default)
        return default if value is None else value
    except Exception:
        return default


def user_agent() -> str:
    """AURORA user agent; a contact (URL or e-mail) is appended only if the user configured one."""
    contact = os.getenv('AURORA_CONTACT', '').strip() or str(get_setting('web.contact', '') or '').strip()
    if contact:
        return f'AURORA/0.2 (assistant local; {contact})'
    return USER_AGENT


def aurora_home():
    """config.aurora_home() (reads AURORA_HOME at call time); same default if config is absent."""
    try:
        from .. import config  # lazy: owned by another module
        return config.aurora_home()
    except Exception:
        from pathlib import Path
        return Path(os.getenv('AURORA_HOME', '').strip() or str(Path.home() / '.aurora')).expanduser()


# --------------------------------------------------------------------------- HTTP helpers

_transport: httpx.AsyncBaseTransport | None = None


def set_http_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests inject an ``httpx.MockTransport`` here; ``None`` restores the network."""
    global _transport
    _transport = transport


def http_client(timeout: float = 10.0, *, follow_redirects: bool = True, trust_env: bool = True) -> httpx.AsyncClient:
    """follow_redirects=False: the caller checks every hop itself; trust_env=False: no system proxy (both for
    tools/webpage.py, public pages only, connection pinned to a checked address)."""
    from ..netutil import ssl_context   # one TLS context for all clients (~190 ms saved per client)
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 6.0)), verify=ssl_context(),
                             headers={'User-Agent': user_agent(), 'Accept-Language': 'fr,en;q=0.7'},
                             follow_redirects=follow_redirects, trust_env=trust_env, transport=_transport)


@dataclass
class Fetched:
    content: bytes
    url: str
    status: int
    content_type: str


async def fetch(url: str, *, params: dict | None = None, source: str = 'La source distante',
                timeout: float = 10.0, max_bytes: int = 5_000_000, accept: str | None = None,
                headers: dict | None = None) -> Fetched:
    """GET with a size cap; every failure becomes a French ``ToolError``."""
    if is_offline():
        raise ToolError(OFFLINE_MESSAGE, data={'offline': True})
    headers = {**({'Accept': accept} if accept else {}), **(headers or {})} or None
    try:
        async with http_client(timeout) as client:
            async with client.stream('GET', url, params=params, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise ToolError(f'{source} a répondu par une erreur HTTP {resp.status_code}.',
                                    status=resp.status_code)
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ToolError(f'Réponse de {source} trop volumineuse (plus de {max_bytes // 1_000_000} Mo), ignorée.')
                return Fetched(bytes(buf), str(resp.url), resp.status_code, resp.headers.get('content-type', ''))
    except ToolError:
        raise
    except httpx.TimeoutException:
        raise ToolError(f'{source} ne répond pas (délai dépassé).') from None
    except httpx.HTTPError as exc:
        raise ToolError(f'{source} est injoignable ({type(exc).__name__}).') from None


async def fetch_json(url: str, *, params: dict | None = None, source: str = 'La source distante',
                     timeout: float = 10.0, max_bytes: int = 5_000_000, headers: dict | None = None) -> Any:
    got = await fetch(url, params=params, source=source, timeout=timeout, max_bytes=max_bytes,
                      accept='application/json', headers=headers)
    try:
        return json.loads(got.content)
    except ValueError:
        raise ToolError(f'Réponse illisible de {source} (JSON invalide).') from None


# --------------------------------------------------------------------------- small cache

class TTLCache:
    """Thread-safe time-based cache; only successful results should be stored."""

    def __init__(self, ttl_s: float, maxsize: int = 128):
        self.ttl_s = ttl_s
        self.maxsize = maxsize
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        _CACHES.append(self)

    def get(self, key: Any) -> Any:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            if hit[0] < time.monotonic():
                self._data.pop(key, None)
                return None
            return hit[1]

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.maxsize:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.monotonic() + self.ttl_s, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_CACHES: list[TTLCache] = []


def clear_caches() -> None:
    for cache in _CACHES:
        cache.clear()


# --------------------------------------------------------------------------- French formatting

NBSP = ' '


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def fr_num(value: float | int | None, digits: int = 1) -> str:
    """12345.6 -> '12 345,6' (non-breaking space for thousands)."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return '—'
    if isinstance(value, int) and not isinstance(value, bool) and digits == 0:
        text = f'{value:,d}'  # exact for big integers (no float conversion)
    else:
        text = f'{value:,.{digits}f}'
    return text.replace(',', NBSP).replace('.', ',')


def fr_sig(value: float | int | None, sig: int = 4) -> str:
    """Significant-digit French formatting, scientific notation for extremes."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return '—'
    if isinstance(value, int) and abs(value) < 10 ** 15:
        return fr_num(value, 0)
    v = float(value)
    if v == 0:
        return '0'
    mag = math.floor(math.log10(abs(v)))
    if mag >= 15 or mag <= -5:
        mantissa = f'{v:.{max(sig - 1, 0)}e}'
        base, exp = mantissa.split('e')
        base = base.rstrip('0').rstrip('.') if '.' in base else base
        return f"{base.replace('.', ',')} × 10^{int(exp)}"
    digits = max(sig - 1 - mag, 0)
    text = fr_num(round(v, digits), digits)
    if ',' in text:
        text = text.rstrip('0').rstrip(',')
    return text


def fr_duration(seconds: float | None) -> str:
    if seconds is None:
        return '—'
    s = int(round(seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f'{days} j {hours} h' if hours else f'{days} j'
    if hours:
        return f'{hours} h {minutes:02d} min'
    if minutes:
        return f'{minutes} min {secs:02d} s' if secs else f'{minutes} min'
    return f'{secs} s'


def fr_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    return ', '.join(items[:-1]) + ' et ' + items[-1]
