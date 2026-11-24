"""Test doubles for the AURORA Core (router, engine selection, tools, providers, capabilities).

Used by test_orchestrator.py and test_api.py. Security (policy, consent, budget, redaction,
untrusted) and memory/db modules are the real ones, on a temporary database.
"""
from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

from pydantic import BaseModel, ConfigDict

import unittest

from aurora.core.planner import DEFAULT_ACTIONS
from aurora.providers.base import ProviderError, ProviderResult

# 23/11/2026, ID Studio IA : « il faut supprimer dans la version à distribuer mon projet de robot ». Le quadrupède reste dans
# son AURORA, mais le kit et la copie à publier ne l'emportent plus (scripts/kit-installation/construire-kit.ps1).
# Les essais qui se servent de lui sont sautés là où il n'est pas.
ROBOT = Path(__file__).resolve().parents[2] / 'docs' / 'robot-quadrupede'
needs_robot = unittest.skipUnless((ROBOT / 'projet.json').is_file(),
                                  'le projet de robot d’ID Studio IA n’est pas livré dans cette copie d’AURORA')

TIERS = {'rapide': {'model': 'claude-haiku-5-5', 'effort': 'low'},
         'standard': {'model': 'claude-sonnet-5-5', 'effort': 'medium'},
         'expert': {'model': 'claude-opus-5-5', 'effort': 'high'}}
NETWORK = {'web.search', 'news.latest', 'weather.current', 'weather.cities', 'quakes.recent'}
KINDS = {'system.status': 'measure', 'system.processes': 'measure', 'system.diagnose': 'measure',
         'system.kill_process': 'action', 'memory.search': 'lookup', 'memory.save': 'action',
         'web.search': 'lookup', 'news.latest': 'lookup', 'weather.current': 'measure',
         'quakes.recent': 'measure', 'calc.evaluate': 'measure', 'calc.convert': 'measure',
         'time.now': 'measure', 'sim.orbit': 'measure', 'sim.solar_system': 'measure', 'files.scan': 'lookup',
         'files.cleanup': 'action', 'doc.analyze': 'generative', 'task.plan': 'generative',
         'ui.navigate': 'action', 'meta.capabilities': 'lookup', 'chat.general': 'generative'}


class TempEnv:
    """Temporary AURORA_HOME / AURORA_DB_PATH, offline, initialised database."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self._tmp.name)
        self._patch = mock.patch.dict(os.environ, {
            'AURORA_HOME': str(self.dir / 'home'), 'AURORA_DB_PATH': str(self.dir / 'aurora.db'),
            'ASTRA_DB_PATH': str(self.dir / 'absent-astra.db'), 'AURORA_OFFLINE': '1'})
        self._patch.start()
        os.environ.pop('ANTHROPIC_API_KEY', None)
        from aurora import db
        db.initialize()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self._tmp.cleanup()
        return False


@dataclass
class FakeDecision:
    intent: str
    confidence: float = 0.9
    method: str = 'rule'
    slots: dict = field(default_factory=dict)
    alternatives: list = field(default_factory=list)
    secondary_intents: list = field(default_factory=list)
    normalized: str = ''
    duration_ms: float = 1.5
    explanation: str = 'décision de test'

    def to_dict(self) -> dict:
        return asdict(self)


class FakeEngine:
    """route(text) -> FakeDecision; intent_hint wins (method 'explicit')."""

    def __init__(self, route: Callable[[str], FakeDecision] | FakeDecision):
        self.route = route if callable(route) else (lambda _text: route)
        self.calls: list[tuple[str, str | None]] = []

    async def decide(self, text: str, *, intent_hint: str | None = None, context=None) -> FakeDecision:
        self.calls.append((text, intent_hint))
        if intent_hint:
            return FakeDecision(intent_hint, 1.0, 'explicit')
        return self.route(text)


FakeRouter = SimpleNamespace(Decision=FakeDecision)


class FakeEngineSelect:
    """Contract rules of core/engine_select.py, simplified."""

    calls: list[dict] = []

    @classmethod
    def select_engine(cls, decision, *, mode, text, caps, user_pref='auto', cloud_tier_pref=None,
                      sensitivity='normal', budget_remaining_usd=0.0):
        cls.calls.append({'intent': decision.intent, 'mode': mode, 'sensitivity': sensitivity,
                          'user_pref': user_pref, 'budget': budget_remaining_usd})
        kind = KINDS.get(decision.intent, 'generative')
        up = caps['ollama']['up']
        slots = getattr(decision, 'slots', None) or {}
        if decision.intent == 'ui.navigate' and str(slots.get('sujet_3d') or '').strip():   # phase 4 (3D lab)
            if up:
                return SimpleNamespace(engine='tool+local', cloud_tier=None, model='qwen3.5:9b', needs_consent=False,
                                       reason='Description de l’objet par l’IA locale.')
            return SimpleNamespace(engine='tool', cloud_tier=None, model=None, needs_consent=False,
                                   reason='Aucune IA disponible pour décrire l’objet.')
        if kind == 'measure':
            engine = 'tool+local' if decision.intent == 'system.diagnose' and up else 'tool'
            return SimpleNamespace(engine=engine, cloud_tier=None, model=None, needs_consent=False,
                                   reason='Mesure : outil + gabarit déterministe.')
        if kind == 'action':
            return SimpleNamespace(engine='tool', cloud_tier=None, model=None, needs_consent=False,
                                   reason='Action : outil du registre.')
        cloud_ok = (user_pref == 'cloud' and mode != 'prive' and caps['cloud']['configured']
                    and budget_remaining_usd > 0)
        if cloud_ok:
            tier = cloud_tier_pref or 'standard'
            return SimpleNamespace(engine='cloud', cloud_tier=tier, model=TIERS[tier]['model'],
                                   needs_consent=mode == 'equilibre' or sensitivity == 'sensitive',
                                   reason='Claude demandé explicitement.')
        if up:
            return SimpleNamespace(engine='local', cloud_tier=None, model='qwen3.5:9b', needs_consent=False,
                                   reason='IA locale d’abord.')
        return SimpleNamespace(engine='none', cloud_tier=None, model=None, needs_consent=False,
                               reason='Aucun modèle disponible.')


class AnyParams(BaseModel):
    model_config = ConfigDict(extra='allow')


class TerminateParams(BaseModel):
    pid: int | None = None
    name: str | None = None
    dry_run: bool = False
    permission_id: int | None = None


class FakeResult:
    def __init__(self, ok: bool, data: dict | None = None, summary: str = '', provenance: str = 'test',
                 sources: list | None = None, duration_ms: float = 3.0, error: str | None = None):
        self.ok, self.data, self.summary, self.provenance = ok, data or {}, summary, provenance
        self.sources, self.duration_ms, self.error = sources or [], duration_ms, error

    def to_dict(self) -> dict:
        return {'ok': self.ok, 'data': self.data, 'summary': self.summary, 'provenance': self.provenance,
                'sources': self.sources, 'duration_ms': self.duration_ms, 'error': self.error}


class FakeTools:
    """REGISTRY + run_tool. responses[name] is a dict or a callable(params) -> dict."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, dict]] = []
        names = set(DEFAULT_ACTIONS) | set(self.responses)
        self.REGISTRY = {
            name: SimpleNamespace(name=name, description=f'outil {name}', action=DEFAULT_ACTIONS.get(name, 'system.read'),
                                  network=name in NETWORK,
                                  params=TerminateParams if name == 'process.terminate' else AnyParams)
            for name in names}

    async def run_tool(self, name: str, params: dict | None = None) -> FakeResult:
        self.calls.append((name, dict(params or {})))
        response = self.responses.get(name)
        if callable(response):
            response = response(dict(params or {}))
        if response is None:
            return FakeResult(False, summary='Outil absent du test.', provenance=name, error='Outil absent du test.')
        return FakeResult(**response)

    def called(self, name: str) -> list[dict]:
        return [params for tool, params in self.calls if tool == name]


class FakeOllama:
    def __init__(self, *, up: bool = True, reply: str | Callable[[list], str] = 'Réponse locale de test.',
                 gate: asyncio.Event | None = None, started: asyncio.Event | None = None):
        self.up, self.reply, self.gate, self.started = up, reply, gate, started
        self.calls: list[dict] = []

    async def available(self) -> bool:
        return self.up

    async def models(self) -> list[dict]:
        return [{'name': 'qwen3.5:9b'}, {'name': 'bge-m3:latest'}] if self.up else []

    async def chat(self, messages, *, system: str, model: str | None = None, max_tokens: int = 1024,
                   on_token=None, timeout: float = 120, options: dict | None = None,
                   format: dict | None = None) -> ProviderResult:
        self.calls.append({'messages': messages, 'system': system, 'model': model, 'format': format,
                           'options': options, 'timeout': timeout, 'max_tokens': max_tokens})
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if not self.up:
            raise ProviderError('unavailable', 'Ollama ne répond pas.', provider='ollama')
        text = self.reply(messages) if callable(self.reply) else self.reply
        if on_token is not None:   # B4: the real providers stream; the double sends its reply word by word
            for piece in re.findall(r'\S+\s*', text):
                await on_token(piece)
        return ProviderResult(text=text, provider='ollama', model=model or 'qwen3.5:9b', input_tokens=11,
                              output_tokens=22, duration_ms=4.0, stop_reason='end_turn')

    async def classify(self, text, labels, *, instructions, timeout=8):
        return None

    async def context_window(self, model):   # /api/ps of a loaded model (cadre Contexte)
        return 8192 if self.up else None


class FakeClaude:
    def __init__(self, *, configured: bool = True, reply: str = 'Réponse de Claude.', cost: float = 0.0123,
                 fail: ProviderError | None = None):
        self._configured, self.reply, self.cost, self.fail = configured, reply, cost, fail
        self.calls: list[dict] = []

    def configured(self) -> bool:
        return self._configured

    def tiers(self) -> dict:
        return {k: dict(v) for k, v in TIERS.items()}

    def estimate_cost(self, text_chars: int, tier: str) -> float:
        return 0.002

    async def context_window(self, model):   # Anthropic models API, max_input_tokens (cadre Contexte)
        return 1_000_000

    async def chat(self, messages, *, system: str = '', tier: str = 'standard', max_tokens: int = 4096,
                   on_token=None, web_search: bool = False, timeout: float = 180,
                   system_dynamic: str = '') -> ProviderResult:
        self.calls.append({'messages': messages, 'system': system, 'tier': tier, 'max_tokens': max_tokens,
                           'system_dynamic': system_dynamic})
        if self.fail is not None:
            raise self.fail
        return ProviderResult(text=self.reply, provider='anthropic', model=TIERS[tier]['model'], input_tokens=120,
                              output_tokens=60, cost_usd=self.cost, duration_ms=15.0, stop_reason='end_turn')


def make_caps(*, ollama_up: bool = True, cloud: bool = False, internet: bool = True):
    async def caps(settings: dict) -> dict:
        return {'internet': internet,
                'ollama': {'up': ollama_up, 'models': ['qwen3.5:9b'] if ollama_up else [],
                           'chat_model': settings['local_model'], 'embed_model': 'bge-m3', 'embed_ready': False},
                'cloud': {'configured': cloud, 'provider': 'anthropic', 'tiers': {k: dict(v) for k, v in TIERS.items()}},
                'mode': settings['mode'], 'shell': bool(settings.get('shell_enabled')), 'voice_hint': '',
                'checked_at': '2026-10-28T09:00:00.000Z'}
    return caps


def build(route, *, tools: FakeTools | None = None, ollama: FakeOllama | None = None,
          claude: FakeClaude | None = None, caps=None):
    """Orchestrator wired to doubles; returns (orchestrator, tools, ollama, claude)."""
    from aurora.core.orchestrator import Deps, Orchestrator
    tools = tools or FakeTools()
    ollama = ollama or FakeOllama()
    claude = claude or FakeClaude(configured=False)
    caps = caps or make_caps(ollama_up=ollama.up, cloud=claude.configured())
    deps = Deps(decision_engine=FakeEngine(route), router=FakeRouter, engine_select=FakeEngineSelect,
                tools=tools, ollama=ollama, claude=claude, capabilities=caps)
    return Orchestrator(deps), tools, ollama, claude


SNAPSHOT = {'cpu_percent': 23.5, 'ram_percent': 49.0, 'disk_percent': 61.2, 'ram_total_gb': 16.0,
            'ram_used_gb': 7.8, 'ram_available_gb': 8.2, 'disk_total_gb': 475.0, 'disk_used_gb': 290.7,
            'net_down_kbps': 120.0, 'net_up_kbps': 30.0, 'cpu_count': 8, 'cpu_freq_mhz': 2400, 'uptime_s': 3600,
            'battery_percent': None, 'gpu': None, 'temperatures': None}


def snapshot_response(params: dict | None = None) -> dict:
    return {'ok': True, 'data': dict(SNAPSHOT),
            'summary': 'Mémoire vive : 7,8 Go utilisés sur 16 Go (49 %), 8,2 Go disponibles.',
            'provenance': 'psutil', 'sources': [{'title': 'Mesure locale (psutil)', 'url': '', 'kind': 'local',
                                                 'retrieved_at': '2026-10-28T09:00:00Z'}]}
