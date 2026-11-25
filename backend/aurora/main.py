"""AURORA local HTTP API + SSE (docs/CONTRACT.md §8).

Extends the ASTRA v0.1 starter routes (/api/health, /api/system, /api/memories, /api/tasks,
/api/events, /api/tools/diagnostic, /api/chat), which keep their shapes. Every request that
asks AURORA to do something goes through orchestrator.handle_request().

Launch (from backend/):
  %USERPROFILE%\\.aurora\\venv\\Scripts\\python.exe -m uvicorn aurora.main:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from . import config, db
from .config import VERSION
from .core import capabilities
from .tasks import events, runs

log = logging.getLogger('aurora.api')


class _MaskTokenFilter(logging.Filter):
    """The SSE token travels in the query string (EventSource cannot set headers): mask it in access logs."""

    _pattern = re.compile(r'(token=)[^&\s"]+')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._pattern.sub(r'\1***', a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = self._pattern.sub(r'\1***', record.msg)
        return True


logging.getLogger('uvicorn.access').addFilter(_MaskTokenFilter())


def _module(name: str) -> Any:
    """Lazy import of a collaborating module; 503 (French, no trace) if it is missing."""
    try:
        return importlib.import_module(f'aurora.{name}')
    except Exception:
        log.exception('Module aurora.%s indisponible', name)
        raise HTTPException(503, f'Module « {name} » indisponible.') from None


def _orchestrator():
    return _module('core.orchestrator').get_orchestrator()


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# ================================================================== lifecycle

async def _sample_loop(interval: float) -> None:
    """Feeds tools.system's history ring buffer (system.history, /api/system/history)."""
    failed = False
    while True:
        try:
            system = importlib.import_module('aurora.tools.system')
            result = await asyncio.to_thread(system.sample)
            if inspect.isawaitable(result):
                await result
            failed = False
        except asyncio.CancelledError:
            raise
        except Exception:
            if not failed:
                log.exception('Échantillonnage système impossible')
            failed = True
        await asyncio.sleep(interval)


async def _permission_sweep(interval: float) -> None:
    """Expired permissions end their run honestly (at startup, then periodically)."""
    while True:
        try:
            await _module('core.orchestrator').get_orchestrator().expire_waiting()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Balayage des autorisations expirées impossible')
        await asyncio.sleep(interval)


async def _backup_loop(interval: float, delay: float) -> None:
    """The day's copy of the database (db.daily_backup), checked ``delay`` seconds after startup (the start is
    busy enough), then every ``interval`` seconds: a PC left on overnight gets the next day's copy too. In the
    folder of Paramètres › Données."""
    await asyncio.sleep(delay)
    while True:
        try:
            folder = config.load_settings().get('backup_dir') or None
            target = await asyncio.to_thread(db.daily_backup, folder)
            if target is not None:
                events.emit('system.backup', f'Sauvegarde du jour de la base : {target.name}', subject_type='system',
                            stage='system', payload={'path': str(target)})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Sauvegarde du jour de la base impossible')
        await asyncio.sleep(interval)


async def _routine_loop(interval: float, delay: float) -> None:
    """D2: the routines that have a schedule (Paramètres › Routines) are launched when their hour comes, one at a
    time, by tasks.routines.run_scheduled — through the orchestrator's ordinary requests, so with the same
    permission cards as when typed. Checked ``delay`` seconds after startup (a routine whose hour passed while
    AURORA was off is launched then, the same day), then every ``interval`` seconds. The first check is also the
    event « start » (tasks.routines.EVENTS): the routines set « au démarrage d’AURORA » leave then, and only then
    (``started``: the seconds since this startup, given to that first check alone)."""
    began = time.monotonic()
    await asyncio.sleep(delay)
    try:
        _module('tasks.routines').close_interrupted()
    except Exception:
        log.exception('Routines : lancements interrompus non refermés')
    started: float | None = time.monotonic() - began
    while True:
        try:
            module = _module('tasks.routines')
            for routine, slot in module.due(started=started):
                report = await module.run_scheduled(routine, slot, _orchestrator().handle_request)
                why = module.EVENTS.get((routine.get('schedule') or {}).get('event'), 'par son horaire')
                events.emit('routine.scheduled', f"Routine « {routine.get('name')} » lancée {why}",
                            subject_type='system', stage='system', payload={'routine_id': routine.get('id'), **report})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Lancement d’une routine par son horaire impossible')
        started = None
        await asyncio.sleep(interval)


async def _reminder_loop(interval: float, delay: float) -> None:
    """C3 (16/11/2026): the reminders, timers and alarms whose time has come (tasks.reminders.fire_due) are said to
    the interface — event « reminder.due », which shows them and reads them aloud. Checked ``delay`` seconds after
    startup (one whose time passed while AURORA was off is said then, with its hour), then every ``interval``
    seconds: a timer rings within that many seconds of its time. Nothing is run here: a reminder is only words."""
    await asyncio.sleep(delay)
    while True:
        try:
            for item in _module('tasks.reminders').fire_due():
                events.emit('reminder.due', f"Rappel : {item.get('text')}"[:300], subject_type='system', stage='system',
                            payload=dict(item))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Rappels : contrôle impossible')
        await asyncio.sleep(interval)


async def _warmup(settings: dict) -> None:
    """Non-blocking start: load the local model that writes the answers (keep_alive: AURORA_OLLAMA_KEEP_ALIVE) and
    import SciPy in a thread, so the first real request does not pay the cold start. 11/11/2026 (ID Studio IA : « la rapidité
    des réponses en vocal ») : the fast model was the one loaded, and it only classifies requests when `llm_router`
    is on (off by default) — the first answer waited for the other model to load. The fast one is still loaded when
    it is used."""
    try:
        ollama = importlib.import_module('aurora.providers.ollama').ollama
        # 23/11/2026: the starting model follows what is installed (config.note_installed_models): on a card under
        # 10 GB the installer takes the lighter model only, and the settings read before this still named the other.
        try:
            start = config.note_installed_models([m.get('name') for m in await ollama.models()])
            if settings.get('local_model') == config.DEFAULT_LOCAL_MODEL and not config.local_model_chosen():
                settings = {**settings, 'local_model': start}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug('Liste des modèles d’Ollama illisible au démarrage', exc_info=True)
        wanted = [settings.get('local_model'), settings.get('local_fast_model') if settings.get('llm_router') else None]
        for model in dict.fromkeys(m for m in wanted if m):
            await ollama.warmup(model)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.debug('Préchauffage Ollama impossible', exc_info=True)
    try:
        await asyncio.to_thread(importlib.import_module, 'scipy.integrate')
    except asyncio.CancelledError:
        raise
    except Exception:
        log.debug('Préchargement de SciPy impossible', exc_info=True)
    try:   # local neural voice (Piper): the first reading should not wait for the model
        await asyncio.to_thread(importlib.import_module('aurora.providers.piper_voice').warmup)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.debug('Préchargement de la voix locale impossible', exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    events.bus.attach_loop(asyncio.get_running_loop())
    interrupted = runs.mark_interrupted_at_startup()
    sampler = asyncio.create_task(_sample_loop(getattr(app.state, 'sample_interval_s', config.SAMPLE_INTERVAL_S)))
    sweeper = asyncio.create_task(_permission_sweep(getattr(app.state, 'permission_sweep_s', 30.0)))
    backups = asyncio.create_task(_backup_loop(getattr(app.state, 'backup_check_s', 3600.0),
                                               getattr(app.state, 'backup_delay_s', 60.0)))
    scheduled = asyncio.create_task(_routine_loop(getattr(app.state, 'routine_check_s', 30.0),
                                                  getattr(app.state, 'routine_delay_s', 45.0)))
    reminding = asyncio.create_task(_reminder_loop(getattr(app.state, 'reminder_check_s', 2.0),
                                                   getattr(app.state, 'reminder_delay_s', 8.0)))
    settings = config.load_settings()
    warm = asyncio.create_task(_warmup(settings))
    events.emit('system.started', f'AURORA {VERSION} démarré', subject_type='system', stage='system',
                payload={'version': VERSION, 'mode': settings['mode'], 'interrupted_runs': interrupted})
    try:
        # Memories saved without a vector (imported, or Ollama was off): embedded in the background.
        importlib.import_module('aurora.memory.embeddings').schedule_backfill(limit=200)
    except Exception:
        log.exception('Rattrapage des vecteurs mémoire impossible')
    try:
        yield
    finally:
        for background in (sampler, sweeper, backups, scheduled, reminding, warm):
            background.cancel()
            try:
                await background
            except (asyncio.CancelledError, Exception):
                pass
        try:
            orch = importlib.import_module('aurora.core.orchestrator').get_orchestrator()
            await orch.shutdown()
        except Exception:
            log.exception('Arrêt de l’orchestrateur incomplet')
        try:
            importlib.import_module('aurora.providers.whisper_local').stop()   # only if AURORA started it
        except Exception:
            log.exception('Arrêt du service Whisper local incomplet')
        events.bus.detach_loop()


# No public /docs, /redoc or /openapi.json: every request needs the session token (CLAUDE.md);
# the routes are documented in docs/CONTRACT.md §8.
app = FastAPI(title='AURORA API (service local)', version=VERSION, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


class LocalHostOnly:
    """DNS-rebinding guard: the Host header must name the loopback interface."""

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            host = ''
            for key, value in scope.get('headers') or []:
                if key == b'host':
                    host = value.decode('latin-1')
                    break
            try:
                from .security.auth import host_allowed
                allowed = host_allowed(host)
            except Exception:
                allowed = host.split(':')[0] in ('127.0.0.1', 'localhost', '[::1]')
            if not allowed:
                response = JSONResponse({'detail': 'Hôte non autorisé : AURORA n’écoute que la boucle locale.'},
                                        status_code=403)
                await response(scope, receive, send)
                return
        await self.inner(scope, receive, send)


def _cors_origins() -> list[str]:
    """Same source as auth.origin_allowed ('*' removed), so CORS never widens the bootstrap check."""
    try:
        from .security.auth import allowed_origins
        return allowed_origins()
    except Exception:
        return config.allowed_origins()


class SecurityHeaders:
    """No framing by another page (clickjacking of an « Autoriser » button), no MIME sniffing, no
    referrer — on every response, SSE included (pure ASGI: the stream is not buffered)."""
    HEADERS = [(b'x-frame-options', b'DENY'), (b'content-security-policy', b"frame-ancestors 'none'"),
               (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer')]

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.inner(scope, receive, send)
            return

        async def send_with_headers(message):
            if message['type'] == 'http.response.start':
                present = {key.lower() for key, _ in message.get('headers') or []}
                message['headers'] = list(message.get('headers') or []) + [
                    item for item in self.HEADERS if item[0] not in present]
            await send(message)

        await self.inner(scope, receive, send_with_headers)


app.add_middleware(LocalHostOnly)
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_credentials=False,
                   allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'PUT'],
                   allow_headers=['Content-Type', 'X-Aurora-Token', 'Last-Event-ID'],
                   # Globe Monde 3D : the region and the day of a NASA image, read by the interface (reviews/globe-contrat.md).
                   expose_headers=['X-Bbox', 'X-Date-Image', 'X-Satellite-Image'])
app.add_middleware(SecurityHeaders)   # outermost: also on the 403 of LocalHostOnly and on CORS answers


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception):
    log.exception('Erreur non gérée sur %s', request.url.path)
    return JSONResponse({'detail': 'Erreur interne du service AURORA.'}, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = sorted({'.'.join(str(p) for p in err.get('loc', [])[1:]) or 'corps' for err in exc.errors()})
    return JSONResponse({'detail': f"Requête invalide ({', '.join(fields)})."}, status_code=422)


async def require_token(request: Request) -> None:
    try:
        auth = importlib.import_module('aurora.security.auth')
    except Exception:
        raise HTTPException(503, 'Module de sécurité indisponible : action refusée.') from None
    await auth.require_token(request)


LOCKED = [Depends(require_token)]


# ================================================================== models

class Attachment(BaseModel):
    name: str = Field(default='document', max_length=200)
    text: str = Field(max_length=config.MAX_ATTACHMENT_CHARS)


SCENE_ID = r'^[a-z][a-z0-9_]{0,39}$'   # same pattern as tools/lab.py (scene and piece ids)


class SceneContext(BaseModel):
    """The scene shown on the home page (reviews/accueil-holographique.md § 3): « démonte-le », « cette pièce »."""
    model_config = ConfigDict(extra='forbid')
    id: str = Field(max_length=40, pattern=SCENE_ID)
    piece: str | None = Field(default=None, max_length=40, pattern=SCENE_ID)


class AskOptions(BaseModel):
    engine: Literal['auto', 'local', 'cloud'] | None = None
    cloud_tier: Literal['rapide', 'standard', 'expert'] | None = None
    intent: str | None = Field(default=None, max_length=60, pattern=r'^[a-z_]+\.[a-z_]+$')
    scene: SceneContext | None = None   # 08/11/2026: sent only while a 3D scene is shown


class AskInput(BaseModel):
    text: str = Field(default='', max_length=config.MAX_INPUT_CHARS)
    conversation_id: int | None = None
    source: Literal['text', 'voice', 'panel', 'api', 'routine'] = 'text'   # routine: a step sent by a routine (D1)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    options: AskOptions = Field(default_factory=AskOptions)


class ChatInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=config.MAX_INPUT_CHARS)
    mode: Literal['local', 'cloud'] = 'local'


class MemoryInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field(default='', max_length=8000)
    category: str = Field(default='general', pattern=r'^[\w-]{1,30}$')
    kind: Literal['fact', 'project', 'preference', 'decision', 'document', 'note', 'summary'] = 'fact'
    confidentiality: Literal['public', 'private', 'sensitive'] = 'private'


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=140)
    detail: str | None = Field(default=None, max_length=8000)
    category: str | None = Field(default=None, pattern=r'^[\w-]{1,30}$')
    kind: Literal['fact', 'project', 'preference', 'decision', 'document', 'note', 'summary'] | None = None
    confidentiality: Literal['public', 'private', 'sensitive'] | None = None
    verification: Literal['verified', 'unverified', 'user_confirmed', 'contradicted'] | None = None
    pinned: bool | None = None


class LinkInput(BaseModel):
    target_id: int
    relation: Literal['related', 'part_of', 'contradicts', 'updates', 'derived_from'] = 'related'


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)


class StatusInput(BaseModel):
    status: str = Field(pattern=r'^(pending|running|paused|done|error|cancelled)$')


class PermissionDecision(BaseModel):
    decision: Literal['grant', 'deny']
    scope: Literal['once', 'conversation'] = 'once'   # 'conversation': Avancé, cloud.send, non-sensitive only


class OrbitInput(BaseModel):
    body: str = Field(min_length=1, max_length=40)
    altitude_km: float
    eccentricity: float | None = None
    hours: float | None = None


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['prive', 'equilibre', 'avance'] | None = None
    cloud_daily_budget_usd: float | None = None
    cloud_request_budget_usd: float | None = None          # ceilings per mode (CLAUDE.md), validated in config
    cloud_daily_budget_avance_usd: float | None = None
    cloud_request_budget_avance_usd: float | None = None
    default_city: str | None = None
    allowed_roots: list[str] | None = None
    allowed_apps: list[dict] | None = None   # 9/11/2026: [{name, path, url}], validé par tools/apps.clean_list
    local_model: str | None = None
    shell_enabled: bool | None = None   # B2 (04/11/2026): terminal avec autorisation, désactivé par défaut
    pc_control_enabled: bool | None = None   # 9/11/2026: souris et clavier, désactivés par défaut
    youtube_comment_auto: bool | None = None   # 9/11/2026: « Publier sans redemander » (commentaires YouTube), désactivé
    opensky_enabled: bool | None = None   # 24/11/2026: OpenSky Network (trafic mondial, secours), éteint par défaut
    auto_open_tab: bool | None = None   # 08/11/2026: AURORA ouvre seule l'onglet du sujet (Monde 3D…)
    local_fast_model: str | None = None
    local_scene_model: str | None = None   # 13/11/2026: le modèle des maquettes 3D ('' = le modèle principal)
    semantic_router: bool | None = None
    llm_router: bool | None = None
    theme: Literal['dark'] | None = None
    web_contact: str | None = Field(default=None, max_length=200)   # e-mail or URL for the Wikimedia user agent
    backup_dir: str | None = Field(default=None, max_length=1000)   # folder of the daily copy of the base
    verify_mods: dict[str, str] | None = None    # check -> « alerter » | « bloquer », validated in config
    db_path: str | None = Field(default=None, max_length=1000)      # read-only (GET): accepted and ignored


# ================================================================== session + health

@app.get('/api/bootstrap')
async def bootstrap(request: Request, response: Response):
    auth = _module('security.auth')
    if not auth.bootstrap_enabled():
        raise HTTPException(404, 'Route désactivée : le jeton est fourni au démarrage par l’application de bureau.')
    if not auth.origin_allowed(request.headers.get('origin')):
        raise HTTPException(403, 'Origine non autorisée.')
    response.headers['Cache-Control'] = 'no-store'
    return {'token': auth.SESSION_TOKEN, 'version': VERSION, 'mode': config.load_settings()['mode']}


async def _caps() -> dict:
    try:
        return await capabilities.snapshot()
    except Exception:
        log.exception('Capacités illisibles')
        raise HTTPException(503, 'État des capacités indisponible.') from None


@app.get('/api/health', dependencies=LOCKED)
async def health():
    caps = await _caps()
    return {'status': 'ok', 'version': VERSION, 'capabilities': caps,
            'cloud_enabled': bool(caps['cloud'].get('configured'))}


@app.get('/api/capabilities', dependencies=LOCKED)
async def get_capabilities():
    return await _caps()


@app.get('/api/tools/map', dependencies=LOCKED)
async def tools_map():
    """Map of the tools (page Tâches): the families and every tool of the registry, with its state now, what it
    needs and its real uses (journal). Read only: no tool is run, nothing is written."""
    oldest = events.list_events(limit=1, ascending=True)
    return capabilities.tool_map(await _caps(), uses=events.tool_uses(),
                                 since=oldest[0]['created_at'] if oldest else None)


# ================================================================== tools (data for panels)

async def _tool(name: str, params: dict | None = None) -> dict:
    """ToolResult dict. In private mode, network tools are not called from panels."""
    base = _module('tools.base')
    spec = base.REGISTRY.get(name)
    if spec is not None and getattr(spec, 'network', False) and config.load_settings()['mode'] == 'prive':
        message = ('Mode privé : cet accès à Internet demande votre autorisation. '
                   'Faites la demande à AURORA pour l’autoriser.')
        return {'ok': False, 'data': {'private_mode': True}, 'summary': message, 'provenance': name,
                'sources': [], 'duration_ms': 0.0, 'error': message}
    result = await base.run_tool(name, params or {})
    return _jsonable(result.to_dict() if hasattr(result, 'to_dict') else result)


async def _tool_data(name: str, params: dict | None = None) -> dict:
    result = await _tool(name, params)
    if not result.get('ok'):
        raise HTTPException(503, result.get('error') or result.get('summary') or 'Mesure indisponible.')
    return result.get('data') or {}


@app.get('/api/system', dependencies=LOCKED)
async def system_status():
    return await _tool_data('system.snapshot')


@app.get('/api/system/history', dependencies=LOCKED)
async def system_history():
    data = await _tool_data('system.history')
    return {'points': data.get('points') or []}


@app.get('/api/system/processes', dependencies=LOCKED)
async def system_processes(limit: int = Query(12, ge=1, le=50), sort: Literal['cpu', 'memory'] = 'cpu'):
    data = await _tool_data('system.processes', {'limit': limit, 'sort': sort})
    return {'processes': data.get('processes') or []}


@app.get('/api/weather', dependencies=LOCKED)
async def weather(city: str | None = Query(None, max_length=80)):
    return await _tool('weather.current', {'city': city or config.load_settings()['default_city']})


@app.get('/api/weather/cities', dependencies=LOCKED)
async def weather_cities():
    return await _tool('weather.cities')


@app.get('/api/news', dependencies=LOCKED)
async def news(topic: str | None = Query(None, max_length=80)):
    return await _tool('news.latest', {'topic': topic} if topic else {})


@app.get('/api/quakes', dependencies=LOCKED)
async def quakes():
    return await _tool('quakes.recent')


# ================================================================== globe hologramme — le ciel (tools/sky.py)
# Routes /api/globe/avions, monde, satellites, detail, nasa : voir reviews/globe-contrat.md.

@app.get('/api/globe/avions', dependencies=LOCKED)
async def globe_planes(lat: float | None = Query(None), lon: float | None = Query(None)):
    """Avions en vol autour du point ; sans point, autour de la ville par défaut des réglages."""
    return await _tool('sky.planes', {key: value for key, value in (('lat', lat), ('lon', lon)) if value is not None})


@app.get('/api/globe/monde', dependencies=LOCKED)
async def globe_traffic():
    return await _tool('sky.traffic')


@app.get('/api/globe/satellites', dependencies=LOCKED)
async def globe_satellites():
    return await _tool('sky.satellites')


_GLOBE_IMAGE_PRIVATE = ('Mode privé : aucune image n’est demandée à la NASA, Internet n’est pas utilisé. '
                        'Le globe garde sa carte locale.')


def _globe_image_error(status: int, message: str, **data) -> JSONResponse:
    """JSON error of an image route: « detail » like the other routes, « ok/error/data » like _tool()."""
    return JSONResponse({'ok': False, 'detail': message, 'error': message, 'data': data}, status_code=status)


@app.get('/api/globe/detail', dependencies=LOCKED)
async def globe_detail(bbox: str = Query('', max_length=200)):
    """Carte détaillée d'une région (NASA GIBS Blue Marble) ; la région servie, recalée sur une grille, est dans X-Bbox."""
    if config.load_settings()['mode'] == 'prive':   # no network call at all, like _tool()
        return _globe_image_error(403, _GLOBE_IMAGE_PRIVATE, private_mode=True)
    sky = _module('tools.sky')
    try:
        image, served = await sky.detail_image(sky.parse_bbox(bbox))
    except ValueError as exc:
        return _globe_image_error(400, str(exc))
    except sky.ToolError as exc:
        return _globe_image_error(503, exc.message_fr)
    return Response(image, media_type='image/jpeg',
                    headers={'X-Bbox': sky.bbox_text(served), 'Cache-Control': 'private, max-age=86400'})


@app.get('/api/globe/nasa', dependencies=LOCKED)
async def globe_nasa():
    """Image NASA GIBS (VIIRS : Suomi NPP, NOAA-20 ou NOAA-21) du monde, la veille en temps universel ; sa date est
    dans X-Date-Image, le satellite qui l'a prise dans X-Satellite-Image."""
    if config.load_settings()['mode'] == 'prive':
        return _globe_image_error(403, _GLOBE_IMAGE_PRIVATE, private_mode=True)
    sky = _module('tools.sky')
    try:
        image, day, satellite = await sky.daily_image()
    except sky.ToolError as exc:
        return _globe_image_error(503, exc.message_fr)
    return Response(image, media_type='image/jpeg',
                    headers={'X-Date-Image': day, 'X-Satellite-Image': satellite, 'Cache-Control': 'no-store'})



# ================================================================== Studio (the user's creator activity)

@app.get('/api/studio', dependencies=LOCKED)
async def studio_overview(refresh: bool = False):
    """Every source through _tool (private mode: no network call from the page). « refresh » empties the caches
    at most once a minute (GitHub allows 60 requests per hour without an account)."""
    studio = _module('tools.studio')
    if refresh:
        studio.clear_cache(min_interval_s=60)
    keys = list(studio.SOURCES)
    results = await asyncio.gather(*(_tool(studio.SOURCES[key]) for key in keys))
    view = await asyncio.to_thread(studio.overview, dict(zip(keys, results)), mode=config.load_settings()['mode'])
    return _jsonable(view)


@app.get('/api/studio/settings', dependencies=LOCKED)
async def studio_settings():
    return _module('tools.studio').settings_view()


@app.put('/api/studio/settings', dependencies=LOCKED)
async def save_studio_settings(item: dict[str, Any]):
    try:
        return _module('tools.studio').save_settings(item)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get('/api/search', dependencies=LOCKED)
async def web_search(q: str = Query(..., min_length=1, max_length=300)):
    return await _tool('web.search', {'query': q})


@app.get('/api/sim/solar', dependencies=LOCKED)
async def sim_solar(date: str | None = Query(None, max_length=40)):
    return await _tool('sim.solar_system', {'date': date} if date else {})


@app.post('/api/sim/orbit', dependencies=LOCKED)
async def sim_orbit(item: OrbitInput):
    return await _tool('sim.orbit', item.model_dump(exclude_none=True))


@app.post('/api/files/restore/{run_id}', dependencies=LOCKED)
async def files_restore(run_id: int):
    return await _tool('files.restore', {'run_id': run_id})


# ================================================================== joined documents (E4)

async def _read_body(request: Request, limit: int, too_big: str) -> bytes:
    """Raw request body, refused with 413 as soon as it passes `limit` (declared or streamed)."""
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, too_big)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, too_big)
    return bytes(body)


@app.post('/api/attachments/extract', dependencies=LOCKED)
async def extract_attachment(request: Request, name: str = Query(..., min_length=1, max_length=200)):
    """Text of a joined PDF or Word file (its raw bytes in the body), which the interface then joins as an ordinary
    attachment (tools/documents.py). Nothing is kept and the text is never logged."""
    documents = _module('tools.documents')
    body = await _read_body(request, documents.MAX_BYTES,
                            f'Fichier trop volumineux ({documents.MAX_BYTES // (1024 * 1024)} Mo au plus) : il n’a pas été lu.')
    try:
        return await asyncio.wait_for(asyncio.to_thread(documents.extract, name, body), timeout=30)
    except documents.NoText as exc:
        # 23/11/2026: a PDF made of images. When this PC can draw its pages and has a vision model, the interface
        # is told so (`images`) and asks for the pages one by one (/api/attachments/page); else the refusal says why.
        state = await _module('tools.vision').status()
        why = documents.page_reader() or state['reason']
        if why:
            raise HTTPException(422, f'{exc} AURORA ne peut pas lire ses pages comme des images sur ce PC : '
                                     f'{why}.') from None
        return {'name': name, 'kind': 'pdf', 'text': '', 'pages': exc.pages, 'pages_read': 0, 'chars': 0,
                'total_chars': 0, 'truncated': False, 'notes': [],
                'images': {'model': state['model'], 'max_pages': documents.MAX_VISION_PAGES,
                           'max_chars': config.MAX_ATTACHMENT_CHARS}}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except asyncio.TimeoutError:
        raise HTTPException(422, 'Document trop long à lire (plus de 30 s) : il n’a pas été lu.') from None


@app.post('/api/attachments/page', dependencies=LOCKED)
async def read_attachment_page(request: Request, page: int = Query(..., ge=1, le=100000)):
    """One page of a joined PDF made of images (its raw bytes in the body): drawn as an image by Windows' own PDF
    reader, then copied on this PC by the local vision model (tools/documents.py read_page). The interface asks for
    the pages one after the other and joins what was read as an ordinary attachment, said to be a model's reading.
    Nothing is kept, and neither the page nor its text is logged."""
    documents, vision = _module('tools.documents'), _module('tools.vision')
    body = await _read_body(request, documents.MAX_BYTES,
                            f'Fichier trop volumineux ({documents.MAX_BYTES // (1024 * 1024)} Mo au plus) : il n’a pas été lu.')
    try:
        return await documents.read_page(body, page)
    except (documents.PageError, vision.VisionError) as exc:
        raise HTTPException(422, str(exc)) from None


# ================================================================== globe hologramme — la Terre (events, weather, news, web)
# Routes /api/globe/meteo, evenements, vent, air, actualites, guide : voir reviews/globe-contrat.md.
# Toutes passent par _tool (mode Privé : aucun appel réseau) ; `data` a la forme des réponses d'Athéna.

@app.get('/api/globe/meteo', dependencies=LOCKED)
async def globe_meteo(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    return await _tool('weather.point', {'lat': lat, 'lon': lon})


@app.get('/api/globe/evenements', dependencies=LOCKED)
async def globe_evenements():
    return await _tool('events.active')


@app.get('/api/globe/vent', dependencies=LOCKED)
async def globe_vent():
    return await _tool('weather.grid')


@app.get('/api/globe/air', dependencies=LOCKED)
async def globe_air():
    return await _tool('weather.air')


@app.get('/api/globe/actualites', dependencies=LOCKED)
async def globe_actualites():
    return await _tool('news.world')


class GlobeArticleInput(BaseModel):
    lien: str = Field(min_length=8, max_length=2000)
    mode: Literal['lire', 'expliquer'] = 'lire'


@app.post('/api/globe/article', dependencies=LOCKED)
async def globe_article(body: GlobeArticleInput):
    """11/11/2026 (Athéna payante, « Lis-le-moi » / « Explique-le »): the text of an article of the globe's news,
    read on the newspaper's page, or its explanation by the local model. Never in Private mode (`_tool`)."""
    return await _tool('news.article', {'lien': body.lien, 'mode': body.mode})


@app.get('/api/globe/guide', dependencies=LOCKED)
async def globe_guide(titre: str = Query(..., min_length=1, max_length=200),
                      projet: Literal['wikipedia', 'wikivoyage'] = 'wikipedia'):
    return await _tool('web.wiki_summary', {'projet': projet, 'titre': titre})


# 08/11/2026 (laboratoire 3D de l'accueil) : validated descriptions aurora.scene/1, never code (tools/lab.py).
@app.get('/api/lab/scenes', dependencies=LOCKED)
async def lab_scenes():
    return await _tool('lab.catalogue')


@app.get('/api/lab/scenes/{scene_id}', dependencies=LOCKED)
async def lab_scene(scene_id: str = PathParam(..., max_length=40, pattern=SCENE_ID)):
    return await _tool('lab.scene', {'id': scene_id})


# 10/11/2026 (ID Studio IA : ouvrir ses modèles 3D existants). Same pattern as tools/lab.py MODEL_FILE: a name, never a path.
MODEL_FILE = r'^imp_[a-z0-9_]{1,36}\.(?:stl|glb|obj)$'


@app.delete('/api/lab/scenes/{scene_id}', dependencies=LOCKED)
async def lab_remove_scene(scene_id: str = PathParam(..., max_length=40, pattern=SCENE_ID)):
    """The bin icon of the list of kept models (11/11/2026), after a confirmation in the interface (CLAUDE.md), like
    the one of a conversation. A kept model or a generated scene leaves the catalogue; its files are set aside in
    AURORA's quarantine, never deleted (tools/lab.remove_scene). Only the interface calls this: no tool of the AI
    removes a scene."""
    gone, why = await asyncio.to_thread(_module('tools.lab').remove_scene, scene_id)
    if gone is None:
        raise HTTPException(404 if why == 'scène introuvable' else 422, f'Scène non retirée : {why}.')
    return gone


@app.post('/api/lab/modeles', dependencies=LOCKED)
async def lab_import_model(request: Request, name: str = Query(..., min_length=1, max_length=200)):
    """A 3D model the person chose with « Joindre un fichier » (STL, GLB or OBJ: its raw bytes in the body). It is
    checked, copied into AURORA_HOME/modeles_3d and kept as a scene of one block (tools/lab.import_model). The
    person's own file is not touched, nothing is executed and nothing leaves the PC."""
    lab = _module('tools.lab')
    body = await _read_body(request, lab.MAX_MODEL_BYTES,
                            f'Modèle 3D trop volumineux ({lab.MAX_MODEL_BYTES // (1024 * 1024)} Mo au plus) : il n’a '
                            'pas été ouvert.')
    scene, why, known = await asyncio.to_thread(lab.import_model, name, body)
    if scene is None:
        raise HTTPException(422, f'Modèle 3D non ouvert : {why}.')
    return {'id': scene['id'], 'titre': scene['titre'], 'deja': known}


@app.get('/api/lab/modeles/{fichier}', dependencies=LOCKED)
async def lab_model_file(fichier: str = PathParam(..., max_length=48, pattern=MODEL_FILE)):
    """The bytes of an imported model, for the interface that draws it (the scene names the file)."""
    lab = _module('tools.lab')
    try:
        path = lab.model_path(fichier)
    except lab.ToolError as exc:
        raise HTTPException(404, str(exc)) from None
    return FileResponse(path, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})


@app.post('/api/lab/photo', dependencies=LOCKED)
async def lab_photo_model(request: Request, name: str = Query(..., min_length=1, max_length=200),
                          choix: str = Query('detaille', max_length=20)):
    """« Photo vers 3D » (10/11/2026): a photo the person chose (its raw PNG or JPEG bytes in the body) is given to
    a local AI on this PC, one photo at a time: Pixal3D when it is installed (providers/pixal3d_local.py: the
    graphics card, about 3 minutes), TripoSR otherwise or when Pixal3D could not run (providers/triposr_local.py:
    about 35 s). ``choix`` (11/11/2026) is the person's: « detaille », the default, or « rapide », TripoSR first
    and Pixal3D as the way out. The shape rebuilt is kept as a scene of one block (tools/lab.import_model), marked
    as a reconstruction; the answer says which engine made it (``moteur``) and, when the other one took over, what
    the first said (``repli``). The photo is not kept and never logged; its name is in the address of the request,
    like the name of a joined document."""
    engines, triposr, lab = _module('providers.pixal3d_local'), _module('providers.triposr_local'), _module('tools.lab')
    if choix not in engines.CHOICES:   # said before the photo is read: nothing starts on a choice not understood
        raise HTTPException(422, 'Photo vers 3D : choix inconnu (« detaille » ou « rapide »). Aucun modèle 3D n’a '
                                 'été fait.')
    body = await _read_body(request, triposr.MAX_PHOTO_BYTES,
                            f'Photo trop lourde ({triposr.MAX_PHOTO_BYTES // (1024 * 1024)} Mo au plus) : aucun modèle '
                            '3D n’a été fait.')
    kind = _module('tools.vision').image_kind(body)
    if kind is None:
        raise HTTPException(422, 'Photo vers 3D : format d’image non lu (PNG ou JPEG seulement).')
    try:
        made = await asyncio.to_thread(engines.photo_to_shape, body, kind, choix)
    except triposr.TripoError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    scene, why, known = await asyncio.to_thread(
        lab.import_model, name, made['modele'],
        photo={k: made.get(k) for k in ('moteur', 'nom', 'format', 'secondes', 'couverture', 'lissage', 'triangles',
                                        'faces_avant', 'aplomb_deg')})
    if scene is None:
        raise HTTPException(422, f'Modèle 3D non gardé : {why}.')
    return {'id': scene['id'], 'titre': scene['titre'], 'deja': known, 'secondes': made['secondes'],
            'triangles': made.get('triangles'), 'couverture': made.get('couverture'), 'moteur': made.get('nom'),
            'repli': made.get('repli')}


class ModelAdjustInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    titre: str | None = Field(default=None, min_length=1, max_length=80)
    debout: Literal['x', 'y', 'z', '-x', '-y', '-z'] | None = None
    quart: int | None = Field(default=None, ge=0, le=3)


@app.put('/api/lab/modeles/{scene_id}', dependencies=LOCKED)
async def lab_adjust_model(item: ModelAdjustInput,
                           scene_id: str = PathParam(..., max_length=40, pattern=r'^imp_[a-z0-9_]{1,36}$')):
    """« Redresser et renommer » (10/11/2026): a kept model is stood another way or named otherwise, because the
    person asked (a button of the laboratory, or « redresse-le », « renomme-le en … »). Only the description of the
    scene is rewritten (tools/lab.adjust_model); the model file is never touched."""
    if item.titre is None and item.debout is None and item.quart is None:
        raise HTTPException(422, 'Rien à régler : donnez un nom, une position debout ou un quart de tour.')
    lab = _module('tools.lab')
    scene, why = await asyncio.to_thread(lab.adjust_model, scene_id, titre=item.titre, debout=item.debout,
                                         quart=item.quart)
    if scene is None:
        raise HTTPException(422, f'Modèle 3D non modifié : {why}.')
    return {'id': scene['id'], 'titre': scene['titre']}


# ================================================================== requests (orchestrator)

@app.post('/api/ask', dependencies=LOCKED)
async def ask(item: AskInput):
    if item.options.intent:
        try:
            intents = importlib.import_module('aurora.core.intents')
            if not intents.is_intent(item.options.intent):
                raise HTTPException(422, f'Intention inconnue : {item.options.intent}')
        except ImportError:
            pass
    try:
        return await _orchestrator().handle_request(
            item.text, item.conversation_id, item.source, item.options.model_dump(exclude_none=True),
            [a.model_dump() for a in item.attachments])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/chat', dependencies=LOCKED)
async def chat(item: ChatInput):
    """Starter compatibility: {prompt, mode} -> {answer, mode, run}. Same single decision path."""
    run = await _orchestrator().handle_request(item.prompt, None, 'api',
                                               {'engine': 'cloud' if item.mode == 'cloud' else 'local'}, [])
    used = 'cloud' if 'cloud' in (run.get('engine') or {}).get('engine', '') else 'local'
    return {'answer': run['answer'], 'mode': used, 'run': run}


@app.post('/api/tools/diagnostic', dependencies=LOCKED)
async def run_diagnostic():
    """Starter compatibility: explicit, read-only diagnostic, through /api/ask (system.diagnose)."""
    run = await _orchestrator().handle_request('Diagnostic ponctuel du PC', None, 'panel',
                                               {'intent': 'system.diagnose'}, [])
    metrics = {}
    for step in run.get('steps') or []:
        if step.get('tool') == 'system.snapshot' and step.get('status') == 'done':
            metrics = runs.step_result(step).get('data') or {}
    return {'task': runs.get_run(run['task_id']), 'metrics': metrics, 'run': run}


@app.get('/api/runs', dependencies=LOCKED)
async def list_runs(limit: int = Query(50, ge=1, le=500)):
    return runs.list_runs(limit)


@app.get('/api/runs/{run_id}', dependencies=LOCKED)
async def run_detail(run_id: int):
    detail = runs.get_run_detail(run_id)
    if detail is None:
        raise HTTPException(404, 'Demande introuvable.')
    return detail


@app.post('/api/runs/{run_id}/cancel', dependencies=LOCKED)
async def cancel_run(run_id: int):
    result = await _orchestrator().cancel_run(run_id)
    if result is None:
        raise HTTPException(404, 'Demande introuvable.')
    return result


@app.post('/api/runs/{run_id}/resume', dependencies=LOCKED)
async def resume_run(run_id: int):
    result = await _orchestrator().resume_run(run_id)
    if result is None:
        raise HTTPException(404, 'Demande introuvable.')
    return result


@app.get('/api/permissions', dependencies=LOCKED)
async def permissions(status: Literal['pending', 'granted', 'denied', 'expired', 'all'] = 'pending'):
    consent = _module('security.consent')
    if status == 'pending':
        return consent.pending()
    consent.expire_stale()
    where = '' if status == 'all' else 'WHERE status=?'
    params = () if status == 'all' else (status,)
    found = db.rows(f'SELECT * FROM permission_requests {where} ORDER BY id DESC LIMIT 200', params)
    return [runs._permission_view(item) for item in found]


@app.post('/api/permissions/{permission_id}', dependencies=LOCKED)
async def decide_permission(permission_id: int, item: PermissionDecision):
    try:
        result = await _orchestrator().decide_permission(permission_id, item.decision, item.scope)
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if result is None:
        raise HTTPException(404, 'Autorisation introuvable.')
    return result


# ================================================================== memory

@app.get('/api/memories', dependencies=LOCKED)
async def memories(category: str | None = None, kind: str | None = None):
    return _module('memory.store').list_memories(category=category or None, kind=kind or None)


@app.post('/api/memories', status_code=201, dependencies=LOCKED)
async def create_memory(item: MemoryInput):
    if not item.title.strip():
        raise HTTPException(422, 'Le titre ne peut pas être vide.')
    try:
        return _module('memory.store').add_memory(item.title.strip(), item.detail, item.category, kind=item.kind,
                                                  confidentiality=item.confidentiality, source='user')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get('/api/memories/search', dependencies=LOCKED)
async def search_memories(q: str = Query(..., min_length=1, max_length=300), limit: int = Query(8, ge=1, le=50)):
    hits = await _module('memory.search').search_async(q, limit=limit)
    return [h.to_dict() if hasattr(h, 'to_dict') else h for h in hits]


@app.get('/api/memories/graph', dependencies=LOCKED)
async def memory_graph(limit: int = Query(120, ge=1, le=1000)):
    """Graph of the brain view; the rings view adds the tools (real uses) and the saved routines."""
    graph = _module('memory.store').graph(limit=limit)
    uses = events.tool_uses()
    titles = importlib.import_module('aurora.core.planner').TOOL_TITLES
    graph['tools'] = [{'name': name, 'title': titles.get(name) or name,
                       'uses': (uses.get(name) or {}).get('uses', 0),
                       'last_used_at': (uses.get(name) or {}).get('last_used_at')}
                      for name in sorted(_module('tools.base').REGISTRY)]
    graph['routines'] = [{'id': r['id'], 'name': r['name'], 'steps': len(r.get('steps') or []),
                          'runs': int(r.get('runs') or 0), 'last_run_at': r.get('last_run_at')}
                         for r in _module('tasks.routines').list_routines()]
    return graph


@app.patch('/api/memories/{memory_id}', dependencies=LOCKED)
async def update_memory(memory_id: int, item: MemoryPatch):
    fields = item.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(422, 'Aucun champ à modifier.')
    try:
        result = _module('memory.store').update_memory(memory_id, **fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if result is None:
        raise HTTPException(404, 'Mémoire introuvable')
    return result


@app.delete('/api/memories/{memory_id}', status_code=204, dependencies=LOCKED)
async def remove_memory(memory_id: int):
    if not _module('memory.store').delete_memory(memory_id):
        raise HTTPException(404, 'Mémoire introuvable')
    return Response(status_code=204)


@app.post('/api/memories/{memory_id}/links', status_code=201, dependencies=LOCKED)
async def link_memory(memory_id: int, item: LinkInput):
    store = _module('memory.store')
    if store.get_memory(memory_id) is None or store.get_memory(item.target_id) is None:
        raise HTTPException(404, 'Mémoire introuvable')
    try:
        return store.link_memories(memory_id, item.target_id, item.relation, created_by='user')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


# ================================================================== starter tasks + events

@app.get('/api/tasks', dependencies=LOCKED)
async def tasks():
    return db.rows('SELECT * FROM tasks ORDER BY id DESC LIMIT 150')


@app.post('/api/tasks', status_code=201, dependencies=LOCKED)
async def create_task(item: TaskInput):
    if not item.title.strip():
        raise HTTPException(422, 'Le titre ne peut pas être vide.')
    return db.add_task(item.title.strip())


@app.patch('/api/tasks/{item_id}', dependencies=LOCKED)
async def update_task(item_id: int, item: StatusInput):
    current = db.row('SELECT kind FROM tasks WHERE id=?', (item_id,))
    if current is not None and current['kind'] != 'manual':
        raise HTTPException(409, 'Cette tâche est une demande AURORA : utilisez /api/runs/{id}/cancel ou /resume.')
    result = db.set_task_status(item_id, item.status)
    if result is None:
        raise HTTPException(404, 'Tâche introuvable')
    return result


@app.get('/api/events', dependencies=LOCKED)
async def list_events(limit: int = Query(200, ge=1, le=2000), run_id: int | None = None,
                      since_id: int | None = None):
    return events.list_events(limit=limit, run_id=run_id, since_id=since_id)


@app.delete('/api/events', status_code=204, dependencies=LOCKED)
async def remove_event_history():
    db.clear_events()
    return Response(status_code=204)


def _sse_frame(event: dict) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    if event.get('transient'):   # B4: live text, never persisted: no id, so Last-Event-ID keeps the last real one
        return f'event: aurora\ndata: {data}\n\n'
    return f"event: aurora\nid: {event.get('id')}\ndata: {data}\n\n"


async def sse_stream(request: Request, queue: asyncio.Queue, *, ping_s: float, since_id: int | None = None):
    """`event: aurora` frames; `: ping` comment every ping_s; unsubscribes on disconnect.

    since_id (Last-Event-ID or ?since_id=) replays the persisted events missed while disconnected.
    """
    last_id = 0
    try:
        yield 'retry: 3000\n\n'
        if since_id is not None:
            for event in events.list_events(limit=500, since_id=since_id, ascending=True):
                last_id = max(last_id, int(event.get('id') or 0))
                yield _sse_frame(event)
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=ping_s)
            except asyncio.TimeoutError:
                yield ': ping\n\n'
                continue
            if last_id and not event.get('transient') and int(event.get('id') or 0) <= last_id:
                continue   # already sent by the replay
            yield _sse_frame(event)
    finally:
        events.bus.unsubscribe(queue)


@app.get('/api/stream', dependencies=LOCKED)
async def stream(request: Request, since_id: int | None = Query(None, ge=0)):
    queue = events.bus.subscribe()   # before the replay query: nothing falls between the two
    if since_id is None:
        header = request.headers.get('last-event-id', '').strip()
        since_id = int(header) if header.isdigit() else None
    ping = float(getattr(request.app.state, 'sse_ping_s', config.SSE_PING_S))
    return StreamingResponse(sse_stream(request, queue, ping_s=ping, since_id=since_id),
                             media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ================================================================== conversations, settings, usage

@app.get('/api/conversations', dependencies=LOCKED)
async def conversations():
    return _module('memory.store').list_conversations()


@app.get('/api/conversations/{conversation_id}/messages', dependencies=LOCKED)
async def conversation_messages(conversation_id: int):
    store = _module('memory.store')
    if store.get_conversation(conversation_id) is None:
        raise HTTPException(404, 'Conversation introuvable.')
    return store.recent_messages(conversation_id, limit=500)


@app.delete('/api/conversations/{conversation_id}', dependencies=LOCKED)
async def remove_conversation(conversation_id: int):
    """The bin icon of the list, after a confirmation in the interface (CLAUDE.md). No copy of the base: the user
    asked for none (29/10/2026), the deletion is theirs. Its standing cloud consent goes too."""
    store = _module('memory.store')
    if not 1 <= conversation_id < 2 ** 63:   # beyond SQLite's integers: no such conversation (never a 500)
        raise HTTPException(404, 'Conversation introuvable.')
    try:
        gone = store.delete_conversation(conversation_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    if gone is None:
        raise HTTPException(404, 'Conversation introuvable.')
    _module('security.consent').revoke_conversation(conversation_id, 'Conversation supprimée')
    return gone


@app.get('/api/conversations/{conversation_id}/cloud-consent', dependencies=LOCKED)
async def conversation_cloud_consent(conversation_id: int):
    """Standing cloud consent of a conversation (Équilibré, Avancé): {active, granted_at, applies}."""
    item = _module('security.consent').conversation_consent(conversation_id)
    return {'active': item is not None, 'granted_at': (item or {}).get('granted_at'),
            'applies': config.load_settings().get('mode') in ('equilibre', 'avance')}


@app.delete('/api/conversations/{conversation_id}/cloud-consent', dependencies=LOCKED)
async def revoke_conversation_cloud_consent(conversation_id: int):
    _module('security.consent').revoke_conversation(conversation_id)
    return await conversation_cloud_consent(conversation_id)


def _settings_view(settings: dict) -> dict:
    """Settings + read-only db_path (real database location, contract §8)."""
    return {**settings, 'db_path': str(config.db_path())}


@app.get('/api/settings', dependencies=LOCKED)
async def get_settings():
    return _settings_view(config.load_settings())


@app.patch('/api/settings', dependencies=LOCKED)
async def patch_settings(item: SettingsPatch):
    fields = item.model_dump(exclude_none=True)
    fields.pop('db_path', None)   # read-only
    try:
        settings = config.update_settings(fields)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    capabilities.invalidate()     # the next /api/health reflects the new mode / models at once
    if 'mode' in fields and settings.get('mode') == 'prive':
        # Conversation-wide cloud consents exist in Équilibré and Avancé: Privé revokes them (CLAUDE.md).
        _module('security.consent').revoke_all_conversations('mode Privé activé')
    return _settings_view(settings)


@app.get('/api/apps/pc', dependencies=LOCKED)
async def pc_apps():
    """The applications of this PC (Desktop, Start menu) to add in one click to Paramètres › Applications
    autorisées. Read only: nothing is launched, nothing is added until the person saves the list."""
    return await asyncio.to_thread(_module('tools.apps').pc_apps)


@app.get('/api/apps/conception', dependencies=LOCKED)
async def design_tools():
    """13/11/2026: FreeCAD and KiCad for the page « Projets »: which app of the allowed list runs each, else which
    shortcuts of this PC would. Read only: nothing is launched."""
    return {'outils': await asyncio.to_thread(_module('tools.apps').design_tools)}


class StartupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actif: bool
    fenetre: bool = False       # open the AURORA window too at the opening of the Windows session


@app.get('/api/demarrage', dependencies=LOCKED)
async def startup_state():
    """16/11/2026 (E3): does AURORA start with Windows? Read on the disk (tools/demarrage.py), never a stored
    setting: {disponible, pourquoi, actif, fenetre, cette_copie, autre, fichier, lance}."""
    return await asyncio.to_thread(_module('tools.demarrage').etat)


@app.put('/api/demarrage', dependencies=LOCKED)
async def set_startup(item: StartupInput):
    """Paramètres › Démarrage only: writes, or removes, AURORA's own command file in the Windows « Démarrage »
    folder. 409 with the reason, and nothing changed, when it is not possible."""
    try:
        return await asyncio.to_thread(_module('tools.demarrage').regler, item.actif, item.fenetre)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


# ================================================================== local neural voice (Piper)

class SpeechInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1500)
    speed: float | None = Field(default=None, ge=0.7, le=1.3)   # 1 = the calm default pace (piper_voice.LENGTH_SCALE)


@app.get('/api/tts', dependencies=LOCKED)
async def speech_status():
    return _module('providers.piper_voice').status()


@app.post('/api/tts', dependencies=LOCKED)
async def speech(item: SpeechInput):
    """WAV of a short text, generated on this PC (nothing leaves it); the text is not logged."""
    voice = _module('providers.piper_voice')
    if not voice.available():
        raise HTTPException(503, 'Voix neuronale locale non installée.')
    try:
        audio = await asyncio.to_thread(voice.synthesize, item.text, speed=item.speed)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(content=audio, media_type='audio/wav', headers={'Cache-Control': 'no-store'})


# ================================================================== local speech recognition (Whisper, voix D3)

@app.get('/api/stt', dependencies=LOCKED)
async def recognition_status():
    return await _module('providers.whisper_local').status()


@app.post('/api/stt/prepare', dependencies=LOCKED)
async def recognition_prepare():
    """Starts Athéna's Whisper service while the viewer speaks, so the text is ready right after (nothing is sent)."""
    whisper = _module('providers.whisper_local')
    try:
        await whisper.ensure_started()
    except whisper.WhisperError as exc:
        raise HTTPException(503, str(exc)) from None
    return await whisper.status()


@app.post('/api/stt', dependencies=LOCKED)
async def recognition(request: Request):
    """Text of a recording made by the interface (its raw bytes in the body), recognised on this PC by Whisper.
    The audio is not kept and neither the audio nor the text is logged."""
    whisper = _module('providers.whisper_local')
    body = await _read_body(request, whisper.MAX_AUDIO_BYTES,
                            f'Enregistrement trop long ({whisper.MAX_AUDIO_BYTES // 1_000_000} Mo au plus) : il n’a pas été lu.')
    try:
        return await whisper.transcribe(body)
    except whisper.WhisperError as exc:
        raise HTTPException(503, str(exc)) from None


# ================================================================== local vision (E2): an image, or the screen on a click

@app.get('/api/vision', dependencies=LOCKED)
async def vision_status():
    return await _module('tools.vision').status()


@app.post('/api/vision/describe', dependencies=LOCKED)
async def vision_describe(request: Request):
    """What a local vision model sees on a joined image (its raw PNG/JPEG bytes in the body). The interface joins
    the description as an ordinary attachment (data, never an instruction). Nothing is kept or logged."""
    vision = _module('tools.vision')
    body = await _read_body(request, vision.MAX_IMAGE_BYTES,
                            f'Image trop lourde ({vision.MAX_IMAGE_BYTES // (1024 * 1024)} Mo au plus) : elle n’a pas été lue.')
    try:
        return await vision.describe(body)
    except vision.VisionError as exc:
        raise HTTPException(422, str(exc)) from None


@app.post('/api/vision/screen', dependencies=LOCKED)
async def vision_screen():
    """The main screen, captured now because the person clicked « Capture d'écran » (no request, page or document
    can call this by itself), read by the local vision model. The capture comes back so the person sees exactly
    what was read; it is not kept, and neither it nor the description is logged."""
    vision = _module('tools.vision')
    try:
        image, width, height = await asyncio.to_thread(vision.capture_screen)
        seen = await vision.describe(image, vision.SCREEN_QUESTION)
    except vision.VisionError as exc:
        raise HTTPException(422, str(exc)) from None
    return {**seen, 'width': width, 'height': height,
            'image': 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')}


# ================================================================== Projets (13/11/2026, tools/projets.py)
# ID Studio IA : « chaque projet conserve ses fichiers techniques modifiables […] avant toute fabrication, les calculs et les
# circuits devront être vérifiés ». Lecture seule : rien n'est écrit ni exécuté par ces deux routes.
PROJET_ID = r'^[a-z0-9][a-z0-9-]{1,39}$'


@app.get('/api/projets', dependencies=LOCKED)
async def projects_list():
    """The projects found on this PC (their counts, and whether each is ready to be made), and the folders whose
    projet.json is refused, with the reason."""
    return await asyncio.to_thread(_module('tools.projets').catalogue)


class NewProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    titre: str = Field(max_length=300)        # the real bounds, and their French message, are in tools/projets.py
    resume: str = Field('', max_length=3000)


@app.post('/api/projets', dependencies=LOCKED)
async def project_create(item: NewProjectInput):
    """23/11/2026, ID Studio IA : « une icône création de projet ». The button « Créer un projet » of the page: an empty
    project (its title, its summary, its seven empty sections) in the person's own projects, never in AURORA's folder
    and never over an existing one. No sheet and no 3D model: the page shows them once the project declares them."""
    try:
        return await asyncio.to_thread(_module('tools.projets').creer, item.titre, item.resume)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get('/api/projets/{projet_id}', dependencies=LOCKED)
async def project_detail(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """One project whole: its requirements, its files one by one (present or not), its checks."""
    found = await asyncio.to_thread(_module('tools.projets').load, projet_id)
    if found is None:
        raise HTTPException(404, 'Projet introuvable.')
    return found


@app.get('/api/projets/{projet_id}/fiche', dependencies=LOCKED)
async def project_sheet(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """15/11/2026, ID Studio IA : « ajoute un visuel de fiche technique […] tout en haut ». The image of the project's technical
    sheet: the file its projet.json declares under the name « fiche-technique », read only, never outside its folder."""
    found = await asyncio.to_thread(_module('tools.projets').fiche_technique, projet_id)
    if found is None:
        raise HTTPException(404, 'Ce projet n’a pas de fiche technique.')
    return FileResponse(found['path'], media_type=found['type'], headers={'Cache-Control': 'no-store'})


async def _project_sheet_data(projet_id: str) -> dict:
    """The interactive technical sheet of a project, read and validated by tools/projets.fiche_donnees; 404 when the
    project has none, 422 with the reason when its file is refused."""
    try:
        found = await asyncio.to_thread(_module('tools.projets').fiche_donnees, projet_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, 'Ce projet n’a pas de fiche technique interactive.')
    return found


@app.get('/api/projets/{projet_id}/fiche/donnees', dependencies=LOCKED)
async def project_sheet_frames(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """15/11/2026, ID Studio IA : « fait la fiche technique interactive avec les model 3D et les animation quant on clique sur le
    cadre ». The frames of the sheet (views, tables, lists), as the project's own program wrote them, and whether a 3D
    model goes with them. Texts and numbers only; nothing is executed."""
    found = await _project_sheet_data(projet_id)
    model = found['modele']
    return {'fiche': found['fiche'], 'modele': {'chemin': model['chemin'], 'taille': model['taille']} if model else None}


@app.get('/api/projets/{projet_id}/fiche/modele', dependencies=LOCKED)
async def project_sheet_model(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """The 3D model (GLB) the sheet's views are drawn from: a file the project declares, read only."""
    model = (await _project_sheet_data(projet_id))['modele']
    if model is None:
        raise HTTPException(404, 'Cette fiche technique n’a pas de modèle 3D.')
    return FileResponse(model['path'], media_type='model/gltf-binary', headers={'Cache-Control': 'no-store'})


@app.post('/api/projets/{projet_id}/verification', dependencies=LOCKED)
async def project_file_check(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID),
                             chemin: str = Query(..., min_length=1, max_length=200)):
    """15/11/2026, ID Studio IA : « il nous faut la connexion au moteur FreeCAD et KiCad ». One declared file of a project,
    checked by the windowless engine of its own design tool (tools/moteurs_cao.py): a part by FreeCAD, a schematic or
    a board by KiCad. Only the engine of an app the person has allowed is run, on a copy; the click is the request."""
    try:
        found = await asyncio.to_thread(_module('tools.moteurs_cao').verifier, projet_id, chemin)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, 'Ce projet ne déclare pas ce fichier.')
    return found


@app.post('/api/projets/{projet_id}/verifications/moteurs', dependencies=LOCKED)
async def project_checks_by_engines(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """15/11/2026, ID Studio IA : « Oui, fais-le » (calculer les vérifications du projet par ces moteurs). Every file that a
    verification of the project names to be checked is given to its engine, one after the other; each result is kept
    with the fingerprint of the file, and the project's verifications are computed from what is kept (projets.load)."""
    try:
        done = await asyncio.to_thread(_module('tools.moteurs_cao').verifier_tout, projet_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if done is None:
        raise HTTPException(404, 'Projet introuvable.')
    return done


@app.get('/api/projets/{projet_id}/calcul', dependencies=LOCKED)
async def project_sizing(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID),
                         masse_kg: float = Query(..., ge=0.5, le=50), autonomie_min: float = Query(..., ge=1, le=1440),
                         articulations: int = Query(12, ge=1, le=64)):
    """13/11/2026, « les calculs en premier »: a requirement (mass, autonomy, joints) against the project's own
    calculation program. Every figure comes from that program (tools/projets.dimensionner), never from an AI."""
    try:
        found = await asyncio.to_thread(_module('tools.projets').dimensionner, projet_id, masse_kg, autonomie_min,
                                        articulations)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, 'Projet introuvable.')
    return found


# 15/11/2026, the workshop (tools/atelier.py). ID Studio IA : « avec une ligne de progression, style animation, on voit que ça
# travaille petit à petit jusqu'à arriver à la finalité du robot. Par AURORA, bien sûr. » Only the project's own
# fabrication program is run, without any argument, one at a time; the click of the person is the request.
@app.get('/api/projets/{projet_id}/fabrication', dependencies=LOCKED)
async def project_build_state(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """Where the construction of a project is: running (its step), finished, failed, or never launched."""
    found = await asyncio.to_thread(_module('tools.atelier').etat, projet_id)
    if found is None:
        raise HTTPException(404, 'Projet introuvable.')
    return found


@app.post('/api/projets/{projet_id}/fabrication', dependencies=LOCKED)
async def project_build_start(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """Launches the construction (or gives the one already running). The interface then reads its state."""
    try:
        found = await asyncio.to_thread(_module('tools.atelier').lancer, projet_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, 'Projet introuvable.')
    return found


@app.post('/api/projets/{projet_id}/fabrication/modele', dependencies=LOCKED)
async def project_build_keep_model(projet_id: str = PathParam(..., max_length=40, pattern=PROJET_ID)):
    """The model of the last finished construction, kept among AURORA's 3D models (tools/lab.import_model): copied,
    nothing overwritten, the same file given twice reopens its scene."""
    scene, why, known = await asyncio.to_thread(_module('tools.atelier').garder_le_modele, projet_id)
    if scene is None:
        raise HTTPException(422, f'Modèle 3D non gardé : {why}')
    return {'id': scene['id'], 'titre': scene['titre'], 'deja': known}


# ================================================================== Claude key (Windows vault, CLAUDE.md)

class AnthropicKeyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr = Field(min_length=20, max_length=400)   # SecretStr: never echoed in errors or logs


_ANTHROPIC_KEY = re.compile(r'sk-ant-[A-Za-z0-9_\-]{20,}')


def _key_status() -> dict:
    """Where the Claude key comes from — never the key itself."""
    key, source = config.anthropic_key_status()
    return {'configured': key is not None, 'source': source,
            'vault_available': _module('security.vault').available()}


def _cloud_key_changed() -> None:
    try:
        from .providers.anthropic_provider import claude
        claude.reset()
    except Exception:
        log.exception('Claude client reset failed')
    capabilities.invalidate()


@app.get('/api/secrets/anthropic', dependencies=LOCKED)
async def anthropic_key_status():
    return _key_status()


@app.put('/api/secrets/anthropic', dependencies=LOCKED)
async def store_anthropic_key(item: AnthropicKeyInput):
    key = item.api_key.get_secret_value().strip()
    if not _ANTHROPIC_KEY.fullmatch(key):
        raise HTTPException(422, 'Clé Anthropic invalide : elle commence par « sk-ant- » (console.anthropic.com).')
    try:
        _module('security.vault').write(key)
    except OSError as exc:
        raise HTTPException(503, str(exc)) from None
    _cloud_key_changed()
    return _key_status()


@app.delete('/api/secrets/anthropic', dependencies=LOCKED)
async def delete_anthropic_key():
    try:
        _module('security.vault').delete()
    except OSError as exc:
        raise HTTPException(503, str(exc)) from None
    _cloud_key_changed()
    return _key_status()


class StudioSecretInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: SecretStr = Field(min_length=20, max_length=200)   # SecretStr: never echoed in errors or logs


_SECRET_HELP = {
    'netlify': 'Jeton Netlify invalide : copiez le jeton personnel créé dans app.netlify.com › User settings › Applications.',
    'youtube': 'Clé API YouTube invalide : elle commence par « AIza » (Google Cloud Console › API et services › Identifiants).',
}


def _secret_status(name: str) -> dict:
    """Whether a Studio secret (tools/studio.SECRETS) is saved — never the secret itself."""
    return {'configured': _module('tools.studio').secret(name) is not None,
            'vault_available': _module('security.vault').available()}


def _store_secret(name: str, item: StudioSecretInput) -> dict:
    studio = _module('tools.studio')
    value = item.token.get_secret_value().strip()
    if not studio.valid_secret(name, value):
        raise HTTPException(422, _SECRET_HELP[name])
    try:
        _module('security.vault').write(value, studio.SECRETS[name][0])
    except OSError as exc:
        raise HTTPException(503, str(exc)) from None
    studio.clear_cache()
    return _secret_status(name)


def _delete_secret(name: str) -> dict:
    studio = _module('tools.studio')
    try:
        _module('security.vault').delete(studio.SECRETS[name][0])
    except OSError as exc:
        raise HTTPException(503, str(exc)) from None
    studio.clear_cache()
    return _secret_status(name)


@app.get('/api/secrets/netlify', dependencies=LOCKED)
async def netlify_token_status():
    return _secret_status('netlify')


@app.put('/api/secrets/netlify', dependencies=LOCKED)
async def store_netlify_token(item: StudioSecretInput):
    return _store_secret('netlify', item)


@app.delete('/api/secrets/netlify', dependencies=LOCKED)
async def delete_netlify_token():
    return _delete_secret('netlify')


@app.get('/api/secrets/youtube', dependencies=LOCKED)
async def youtube_key_status():
    return _secret_status('youtube')


@app.put('/api/secrets/youtube', dependencies=LOCKED)
async def store_youtube_key(item: StudioSecretInput):
    return _store_secret('youtube', item)


@app.delete('/api/secrets/youtube', dependencies=LOCKED)
async def delete_youtube_key():
    return _delete_secret('youtube')


# The OAuth client of AURORA's Google connection (tools/youtube_comments.py), same handling as the keys above.
# 13/11/2026: also the keys of the two online 3D services (ID Studio IA : « place 2 clés API dans les paramètres, meshy.ai et
# huggingface »), by the same three routes.
OAuthSecretName = Literal['youtube_client_id', 'youtube_client_secret', 'meshy', 'huggingface']
_SECRET_HELP.update({
    'youtube_client_id': 'ID client invalide : il finit par « .apps.googleusercontent.com » (Google Cloud Console › '
                         'API et services › Identifiants › votre client OAuth).',
    'youtube_client_secret': 'Code secret du client invalide : il commence d’ordinaire par « GOCSPX- » (même page '
                             'que l’ID client).',
    'meshy': 'Clé Meshy invalide : elle commence par « msy_ » (meshy.ai › API › clés).',
    'huggingface': 'Jeton Hugging Face invalide : il commence par « hf_ » (huggingface.co › Settings › Access Tokens).',
})


@app.get('/api/secrets/{name}', dependencies=LOCKED)
async def oauth_secret_status(name: OAuthSecretName):
    return _secret_status(name)


@app.put('/api/secrets/{name}', dependencies=LOCKED)
async def store_oauth_secret(name: OAuthSecretName, item: StudioSecretInput):
    return _store_secret(name, item)


@app.delete('/api/secrets/{name}', dependencies=LOCKED)
async def delete_oauth_secret(name: OAuthSecretName):
    return _delete_secret(name)


# ================================================================== YouTube: AURORA's own Google connection
# Every route needs the session token (CLAUDE.md): Google sends the browser back to the interface
# (/youtube-retour), which gives the code to POST /api/youtube/retour with its token.

class YoutubeReturnInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: str = Field('', max_length=200)
    code: str = Field('', max_length=2000)
    error: str = Field('', max_length=200)


@app.get('/api/youtube/etat', dependencies=LOCKED)
async def youtube_connection_status(request: Request):
    return await asyncio.to_thread(_module('tools.youtube_comments').status, request.headers.get('origin'))


@app.post('/api/youtube/connexion', dependencies=LOCKED)
async def youtube_connect(request: Request):
    """The address of Google's consent page; the interface shows it as a link (no pop-up window)."""
    yt = _module('tools.youtube_comments')
    if config.load_settings()['mode'] == 'prive':
        raise HTTPException(409, yt.PRIVATE_MODE)
    try:
        return await asyncio.to_thread(yt.start_connection, request.headers.get('origin'))
    except yt.ToolError as exc:
        raise HTTPException(409, exc.message_fr) from None


@app.post('/api/youtube/retour', dependencies=LOCKED)
async def youtube_return(item: YoutubeReturnInput, request: Request):
    """What Google gave the interface page /youtube-retour: the single-use state, then the code or the refusal."""
    yt = _module('tools.youtube_comments')
    if config.load_settings()['mode'] == 'prive':
        raise HTTPException(409, yt.PRIVATE_MODE)
    try:
        await yt.finish_connection(item.state, item.code, item.error)
    except yt.ToolError as exc:
        # 400: a refusal (state, Google, the person); 503: the network or Google did not answer.
        raise HTTPException(503 if (exc.status or 0) >= 500 else 400, exc.message_fr) from None
    return await asyncio.to_thread(yt.status, request.headers.get('origin'))


@app.delete('/api/youtube/connexion', dependencies=LOCKED)
async def youtube_disconnect(request: Request):
    """AURORA forgets the access; Google is asked to withdraw it, except in private mode (no network call)."""
    yt = _module('tools.youtube_comments')
    try:
        done = await yt.disconnect(call_google=config.load_settings()['mode'] != 'prive')
    except yt.ToolError as exc:
        raise HTTPException(503, exc.message_fr) from None
    view = await asyncio.to_thread(yt.status, request.headers.get('origin'))
    return {**view, **done}


@app.get('/api/usage', dependencies=LOCKED)
async def usage():
    return _module('security.budget').usage_summary()


def _run_bench() -> Any:
    # Loaded by its file path, never as the « tests » package: tests/__init__.py would switch the running
    # service to test settings (vault, SearXNG, page reading off). core/bench.py explains it.
    try:
        fn = getattr(_module('core.bench').module(), 'run_bench')
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f'Banc d’essai introuvable ({exc})') from None
    params = inspect.signature(fn).parameters
    kwargs: dict[str, Any] = {name: False for name in ('use_llm', 'llm', 'use_embed') if name in params}  # no LLM
    unread = None
    if 'reports' in params:     # A1: the user's real reports, replayed apart from the corpus score
        try:
            kwargs['reports'] = _module('tasks.reports').bench_rows()
        except Exception:
            log.exception('Signalements illisibles : banc lancé sans eux')
            unread = {'error': 'Signalements illisibles : ils n’ont pas été rejoués.'}
    result = fn(**kwargs)
    if unread is not None:
        result['reports'] = unread
    return result


@app.post('/api/quality/bench', dependencies=LOCKED)
async def quality_bench():
    try:
        result = await asyncio.to_thread(_run_bench)
    except Exception:
        log.exception('Banc d’essai en échec')
        raise HTTPException(503, 'Banc d’essai indisponible.') from None
    return _jsonable(result)


@app.get('/api/quality/latest', dependencies=LOCKED)
async def quality_latest():
    path = config.bench_latest_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(503, 'Dernier résultat du banc illisible.') from None


# ================================================================== « Signaler » (H3, tasks/reports.py)
# Thin HTTP wrappers: the logic and its validation (reports.REASONS, intents.is_intent) live in tasks/reports.py.

class ReportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=40)
    expected_intent: str | None = Field(default=None, max_length=60)
    comment: str = Field(default='', max_length=1000)


@app.get('/api/runs/{run_id}/report', dependencies=LOCKED)
async def run_report(run_id: int):
    """The report of a run, or null when it was not reported."""
    return _module('tasks.reports').get_report(run_id)


@app.put('/api/runs/{run_id}/report', dependencies=LOCKED)
async def save_run_report(run_id: int, item: ReportInput):
    """Report an answer, or correct its report (one per run: a second click never makes a second case)."""
    reports = _module('tasks.reports')
    try:
        saved = reports.save_report(run_id, item.reason, item.expected_intent, item.comment)
    except reports.RunInProgress as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if saved is None:
        raise HTTPException(404, 'Demande introuvable.')
    return saved


@app.delete('/api/runs/{run_id}/report', status_code=204, dependencies=LOCKED)
async def delete_run_report(run_id: int):
    if not _module('tasks.reports').delete_report(run_id):
        raise HTTPException(404, 'Aucun signalement pour cette demande.')


@app.get('/api/quality/reports', dependencies=LOCKED)
async def quality_reports(limit: int = Query(500, ge=1, le=5000)):
    """Reports, newest first, each with its export state for the routing bench."""
    return await asyncio.to_thread(_module('tasks.reports').list_reports, limit)


@app.get('/api/quality/reports/export', dependencies=LOCKED)
async def quality_reports_export():
    """Download in the format of tests/data/routing_bench.jsonl. Never writes into the repository."""
    text, _count = await asyncio.to_thread(_module('tasks.reports').export_jsonl)
    return Response(text, media_type='application/x-ndjson; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="signalements-banc.jsonl"'})


# ================================================================== routines (D1)

class RoutineSchedule(BaseModel):
    """D2: when a routine is launched by itself — the days (0 = lundi … 6 = dimanche), and either an hour
    (« 08:30 ») or an event of tasks.routines.EVENTS (« start »: when AURORA starts). Checked in tasks/routines."""
    model_config = ConfigDict(extra='forbid')
    time: str = Field('', max_length=5)
    event: str = Field('', max_length=20)
    days: list[int] = Field(max_length=7)


class RoutineInput(BaseModel):
    """A routine: a name, the phrases that launch it, its steps (ordinary requests). Validated in tasks/routines.
    ``schedule`` (D2) is read only when it is sent: null removes the schedule, leaving it out keeps it.
    ``propose`` (D2, the proposal « réussi 5 fois de suite »): false = « Ne plus proposer » for this routine, true
    brings the proposal back; left out, nothing changes."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=200)
    triggers: list[str] = Field(default_factory=list, max_length=20)
    steps: list[str] = Field(max_length=40)
    schedule: RoutineSchedule | None = None
    propose: bool | None = None


@app.get('/api/routines', dependencies=LOCKED)
async def routines_list():
    module = _module('tasks.routines')
    return [module.view(routine) for routine in module.list_routines()]   # with « next »: its next launch (D2)


@app.post('/api/routines', dependencies=LOCKED)
async def routine_create(item: RoutineInput):
    module = _module('tasks.routines')
    try:
        return module.view(module.save_routine(item.model_dump(exclude_unset=True)))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.put('/api/routines/{routine_id}', dependencies=LOCKED)
async def routine_update(routine_id: int, item: RoutineInput):
    module = _module('tasks.routines')
    try:
        return module.view(module.save_routine(item.model_dump(exclude_unset=True), routine_id))
    except ValueError as exc:
        raise HTTPException(404 if 'introuvable' in str(exc) else 422, str(exc)) from None


@app.delete('/api/routines/{routine_id}', status_code=204, dependencies=LOCKED)
async def routine_delete(routine_id: int):
    if not _module('tasks.routines').delete_routine(routine_id):
        raise HTTPException(404, 'Routine introuvable.')


# ================================================================== reminders (C3, 16/11/2026)

class ReminderSnooze(BaseModel):
    """« Dans 10 min » on a reminder that rang: how many minutes from now it comes back."""
    model_config = ConfigDict(extra='forbid')
    minutes: int = Field(default=10, ge=1, le=1440)


@app.get('/api/reminders', dependencies=LOCKED)
async def reminders_list():
    """The reminders, timers and alarms kept by the service (tasks/reminders.py): those to come (« waiting ») and
    those that rang and wait for « Fait » (« due »). ``now``: the PC's local clock, in the same form as ``due``,
    so that the interface counts what is left without guessing a time zone."""
    module = _module('tasks.reminders')
    return {'now': module._local_now().strftime('%Y-%m-%d %H:%M:%S'), 'items': module.list_reminders()}


@app.delete('/api/reminders/{reminder_id}', status_code=204, dependencies=LOCKED)
async def reminder_remove(reminder_id: int):
    """« Fait » on a reminder that rang, or « Supprimer » on one still to come: the person's own click."""
    if _module('tasks.reminders').remove(reminder_id) is None:
        raise HTTPException(404, 'Rappel introuvable.')


@app.post('/api/reminders/{reminder_id}/snooze', dependencies=LOCKED)
async def reminder_snooze(reminder_id: int, body: ReminderSnooze):
    item = _module('tasks.reminders').snooze(reminder_id, body.minutes)
    if item is None:
        raise HTTPException(404, 'Rappel introuvable.')
    return item


# ================================================================== pages (P2, tasks/artefacts.py)
# Thin HTTP wrappers: building and storing live in tasks/artefacts.py, the HTML in tasks/artefact_html.py.

@app.get('/api/artefacts', dependencies=LOCKED)
async def artefacts_list():
    return await asyncio.to_thread(_module('tasks.artefacts').list_pages)


@app.get('/api/artefacts/{page_id}', dependencies=LOCKED)
async def artefact_detail(page_id: str):
    found = await asyncio.to_thread(_module('tasks.artefacts').get_page, page_id)
    if found is None:
        raise HTTPException(404, 'Page introuvable.')
    return found


@app.get('/api/artefacts/{page_id}/html', dependencies=LOCKED)
async def artefact_page_html(page_id: str, version: int | None = Query(None, ge=1, le=100_000)):
    """The standalone page, shown by the interface in an <iframe sandbox> without scripts."""
    stored = await asyncio.to_thread(_module('tasks.artefacts').read_version, page_id, version)
    if stored is None:
        raise HTTPException(404, 'Page ou version introuvable.')
    rendering = _module('tasks.artefact_html')
    text = rendering.render(stored['page'], page_id=page_id, version=stored['version'], versions=stored['versions'],
                            created_at=stored['created_at'])
    return Response(text, media_type='text/html; charset=utf-8', headers={
        # Replaces SecurityHeaders' CSP for this response, so its frame-ancestors is kept here.
        'Content-Security-Policy': f"{rendering.CSP}; frame-ancestors 'none'; sandbox allow-popups allow-popups-to-escape-sandbox",
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


def _made(build: Any, *args: Any) -> Any:
    artefacts = _module('tasks.artefacts')
    try:
        return build(*args)
    except artefacts.NotFinished as exc:
        raise HTTPException(409, str(exc)) from None
    except artefacts.NothingToShow as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError:   # a block refused by the model (pydantic): a bug of a builder, never shown half-made
        log.exception('Page refusée par son modèle')
        raise HTTPException(422, 'La page n’a pas pu être construite : des données ont une forme inattendue.') from None
    except OSError:
        log.exception('Page impossible à enregistrer')
        raise HTTPException(503, 'La page n’a pas pu être enregistrée sur le disque.') from None


@app.post('/api/artefacts/from-run/{run_id}', dependencies=LOCKED)
async def artefact_from_run(run_id: int):
    """The page of a finished request (diagnosis, web search, routine launch, or any answer)."""
    if not 1 <= run_id < 2 ** 63:   # beyond SQLite's integers: no such request (never a 500)
        raise HTTPException(404, 'Demande introuvable.')
    made = await asyncio.to_thread(_made, _module('tasks.artefacts').make_from_run, run_id,
                                   _orchestrator().run_result)
    if made is None:
        raise HTTPException(404, 'Demande introuvable.')
    return made


@app.post('/api/artefacts/bench', dependencies=LOCKED)
async def artefact_from_bench():
    made = await asyncio.to_thread(_made, _module('tasks.artefacts').make_from_bench)
    if made is None:
        raise HTTPException(404, 'Aucun résultat du banc pour l’instant : lancez-le d’abord (Paramètres › Qualité).')
    return made


@app.delete('/api/artefacts/{page_id}', status_code=204, dependencies=LOCKED)
async def artefact_delete(page_id: str):
    if not await asyncio.to_thread(_module('tasks.artefacts').delete_page, page_id):
        raise HTTPException(404, 'Page introuvable.')
