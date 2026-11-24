"""Claude through the official `anthropic` SDK (AsyncAnthropic, streaming).

Request rules for the 2026 models (claude-api skill, model-migration.md):
- never send `thinking` (adaptive is the default; disabled/budget_tokens return 400 on
  Opus 5.5 / Fable 5.1) nor temperature/top_p/top_k (400 since API 2026-10-21);
  depth and cost are controlled with `output_config={"effort": ...}`;
- `stop_reason` is checked before reading content ('refusal' arrives as HTTP 200);
- server-side refusal fallback `fallbacks="default"` + beta `server-side-fallback-2026-07-01`
  (beta endpoint) for the models that support it; Haiku 5.5 has no server-side fallback.
Credentials: the Windows vault first (security/vault.py, CLAUDE.md), then ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import pricing
from .base import ProviderError, ProviderResult, TokenCallback, config_value, offline, validate_messages

log = logging.getLogger(__name__)

PROVIDER = 'anthropic'
# Owner's rule (CLAUDE.md): Haiku 5.5 for routing, Sonnet 5.5 by default, Opus 5.5 only on request.
TIERS: dict[str, dict[str, str]] = {
    'rapide': {'model': 'claude-haiku-5-5', 'effort': 'low', 'env': 'AURORA_CLOUD_MODEL_RAPIDE',
               'label': 'Rapide (routage)'},
    'standard': {'model': 'claude-sonnet-5-5', 'effort': 'medium', 'env': 'AURORA_CLOUD_MODEL_STANDARD',
                 'label': 'Standard'},
    'expert': {'model': 'claude-opus-5-5', 'effort': 'high', 'env': 'AURORA_CLOUD_MODEL_EXPERT',
               'label': 'Expert (sur demande)'},
}
DEFAULT_TIER = 'standard'
FALLBACK_BETA = 'server-side-fallback-2026-07-01'      # pairs with the scalar form fallbacks="default"
FALLBACK_MODELS = frozenset({'claude-opus-5-5', 'claude-fable-5-1', 'claude-sonnet-5-5'})
WEB_SEARCH_TOOL = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}
MAX_CONTINUATIONS = 3          # pause_turn resumptions of a server-tool turn
CHARS_PER_TOKEN = 3.0          # conservative for French with the 2026 tokenizers
EXPECTED_OUTPUT_TOKENS = {'rapide': 600, 'standard': 1500, 'expert': 3000}   # heuristic, thinking included
IMAGE_CHARS = 6000   # what one image weighs in the pre-send estimate (about 1 700 tokens, the most a photo costs)

REFUSAL_CATEGORIES_FR = {
    'cyber': 'cybersécurité',
    'bio': 'biologie',
    'reasoning_extraction': 'extraction du raisonnement interne',
    'frontier_llm': "développement de modèles d'IA concurrents",
    'general_harms': "politique d'usage",
}
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean(text: Any, limit: int) -> str:
    return _CONTROL.sub('', str(text or '')).strip()[:limit]


def _system_blocks(system: str | list, system_dynamic: str) -> list[dict]:
    """Stable system text first, marked for prompt caching; volatile state after it, uncached."""
    blocks: list[dict] = []
    if isinstance(system, list):
        for item in system:
            if isinstance(item, str) and item.strip():
                blocks.append({'type': 'text', 'text': item})
            elif isinstance(item, dict) and item.get('type') == 'text' and item.get('text'):
                blocks.append({'type': 'text', 'text': str(item['text'])})
    elif isinstance(system, str) and system.strip():
        blocks.append({'type': 'text', 'text': system})
    if blocks:
        blocks[-1]['cache_control'] = {'type': 'ephemeral'}
    if system_dynamic and system_dynamic.strip():
        blocks.append({'type': 'text', 'text': system_dynamic})
    return blocks


def _echo_content(content: list) -> list:
    """Assistant content to send back when resuming a paused turn.

    After a mid-output fallback, model-internal blocks that precede the last `fallback`
    block (thinking, redacted_thinking, tool_use, unpaired server_tool_use) are omitted.
    """
    content = list(content or [])
    last_fallback = max((i for i, b in enumerate(content) if _attr(b, 'type') == 'fallback'), default=-1)
    if last_fallback < 0:
        return content
    result_ids = {_attr(b, 'tool_use_id') for b in content if str(_attr(b, 'type', '')).endswith('_tool_result')}
    kept = []
    for index, block in enumerate(content):
        kind = _attr(block, 'type')
        if index < last_fallback:
            if kind in ('thinking', 'redacted_thinking', 'tool_use'):
                continue
            if kind == 'server_tool_use' and _attr(block, 'id') not in result_ids:
                continue
        kept.append(block)
    return kept


def _collect(content: list, sources: dict[str, dict], errors: list[str]) -> str:
    """Return the text of a response and gather web-search sources (cited first)."""
    texts = []
    retrieved = _now_iso()
    for block in content or []:
        kind = _attr(block, 'type')
        if kind == 'text':
            texts.append(str(_attr(block, 'text', '') or ''))
            for citation in _attr(block, 'citations', None) or []:
                url = str(_attr(citation, 'url', '') or '')
                if _attr(citation, 'type') != 'web_search_result_location' or not url.startswith(('http://', 'https://')):
                    continue
                entry = sources.setdefault(url, {'title': _clean(_attr(citation, 'title') or url, 200), 'url': url,
                                                 'kind': 'web_search', 'retrieved_at': retrieved, 'cited': False})
                entry['cited'] = True
                cited = _clean(_attr(citation, 'cited_text', ''), 300)
                if cited and not entry.get('cited_text'):
                    entry['cited_text'] = cited
        elif kind == 'web_search_tool_result':
            body = _attr(block, 'content')
            if isinstance(body, list):
                for item in body:
                    url = str(_attr(item, 'url', '') or '')
                    if _attr(item, 'type') == 'web_search_result' and url.startswith(('http://', 'https://')):
                        sources.setdefault(url, {'title': _clean(_attr(item, 'title') or url, 200), 'url': url,
                                                 'kind': 'web_search', 'retrieved_at': retrieved, 'cited': False,
                                                 'page_age': _clean(_attr(item, 'page_age', ''), 40)})
            elif body is not None:
                errors.append(str(_attr(body, 'error_code', 'unknown')))
    return ''.join(texts)


class _Usage:
    """Accumulates billed usage across attempts (fallback iterations) and pause_turn continuations."""

    def __init__(self) -> None:
        self.input = self.output = self.cache_read = self.cache_write = self.web_searches = 0
        self.cost = 0.0
        self.models: list[str] = []
        self.fallback_ran = False

    def add(self, message: Any) -> None:
        usage = _attr(message, 'usage')
        model = str(_attr(message, 'model', '') or '')
        iterations = list(_attr(usage, 'iterations', None) or [])
        entries = iterations or [usage]
        for entry in entries:
            if entry is None:
                continue
            entry_model = str(_attr(entry, 'model', '') or model)
            i, o = _int(_attr(entry, 'input_tokens')), _int(_attr(entry, 'output_tokens'))
            cr, cw = _int(_attr(entry, 'cache_read_input_tokens')), _int(_attr(entry, 'cache_creation_input_tokens'))
            self.input += i
            self.output += o
            self.cache_read += cr
            self.cache_write += cw
            self.cost += pricing.cost_usd(entry_model, i, o, cr, cw)
            if _attr(entry, 'type') == 'fallback_message':
                self.fallback_ran = True
        searches = _int(_attr(_attr(usage, 'server_tool_use'), 'web_search_requests'))
        self.web_searches += searches
        self.cost += searches * pricing.WEB_SEARCH_USD_PER_REQUEST
        if model:
            self.models.append(model)

    def result(self, text: str, model: str, started: float, stop_reason: str, meta: dict) -> ProviderResult:
        return ProviderResult(
            text=text, provider=PROVIDER, model=model, input_tokens=self.input, output_tokens=self.output,
            cache_read_tokens=self.cache_read, cache_write_tokens=self.cache_write,
            cost_usd=round(self.cost, 6), duration_ms=round((time.perf_counter() - started) * 1000, 1),
            stop_reason=stop_reason, meta=meta,
        )


class AnthropicProvider:
    name = PROVIDER

    def __init__(self, client: Any = None, *, budget_guard: bool = True, redact_outgoing: bool = True) -> None:
        self._client = client            # tests inject a fake; otherwise AsyncAnthropic() on first use
        self._injected = client is not None
        self.budget_guard = budget_guard
        self.redact_outgoing = redact_outgoing

    # -- configuration --
    def configured(self) -> bool:
        if self._injected:
            return True
        from aurora import config   # key: Windows vault first, then ANTHROPIC_API_KEY (CLAUDE.md)
        return bool(config.anthropic_api_key() or os.getenv('ANTHROPIC_AUTH_TOKEN', '').strip())

    def tier(self, tier: str) -> dict:
        if tier not in TIERS:
            raise ProviderError('bad_request', f'Palier cloud inconnu : « {_clean(tier, 30)} ».', provider=PROVIDER)
        spec = TIERS[tier]
        model = config_value(spec['env'], spec['model'], spec['env'].replace('AURORA_', ''))
        return {'model': model, 'effort': spec['effort'], 'label': spec['label'],
                'fallback': self._fallback_enabled(model)}

    def tiers(self) -> dict:
        return {name: self.tier(name) for name in TIERS}

    def _fallback_enabled(self, model: str) -> bool:
        if config_value('AURORA_CLOUD_FALLBACKS', '1', 'CLOUD_FALLBACKS').lower() in ('0', 'false', 'non', 'no'):
            return False
        return pricing.resolve_model(model) in FALLBACK_MODELS

    def estimate_cost(self, text_chars: int, tier: str) -> float:
        """Rough pre-send estimate (input from character count + typical output for the tier)."""
        tier = tier if tier in TIERS else DEFAULT_TIER
        input_tokens = math.ceil(max(0, int(text_chars or 0)) / CHARS_PER_TOKEN)
        return pricing.cost_usd(self.tier(tier)['model'], input_tokens, EXPECTED_OUTPUT_TOKENS[tier])

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            from aurora import config
            key = config.anthropic_api_key()
            self._client = anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
        return self._client

    async def context_window(self, model: str) -> int | None:
        """Context size of a Claude model, from Anthropic's models API (`max_input_tokens`), kept per model.
        Only the model name leaves (no user data); None when unknown (cadre Contexte du Chat, 29/10/2026)."""
        known = self.__dict__.setdefault('_windows', {})
        if model not in known:
            try:
                info = await self._get_client().models.retrieve(model)
                size = _int(_attr(info, 'max_input_tokens'))
                known[model] = size or None
            except Exception:   # the measure is optional: never fails the answer it describes
                return None
        return known[model]

    def reset(self) -> None:
        """The key changed (saved in or removed from the vault): the next call builds a new client."""
        if not self._injected:
            self._client = None

    # -- guards --
    def _check_budget(self, estimate: float, model: str) -> None:
        if not self.budget_guard:
            return
        try:
            from aurora.security import budget
            remaining = float(budget.remaining_usd())
        except Exception:
            log.exception('Cloud budget unreadable')
            raise ProviderError('budget', 'Budget cloud illisible : envoi bloqué par précaution.',
                                provider=PROVIDER, model=model) from None
        if remaining <= 0 or estimate > remaining:
            raise ProviderError('budget', f'Budget cloud du jour insuffisant (reste {max(0.0, remaining):.4f} $, '
                                          f'estimation {estimate:.4f} $).', provider=PROVIDER, model=model)

    def _redact(self, messages: list[dict], system_dynamic: str) -> tuple[list[dict], str, list[dict]]:
        if not self.redact_outgoing:
            return messages, system_dynamic, []
        from aurora.security.redaction import redact
        totals: dict[str, int] = {}

        def apply(text: str) -> str:
            masked, findings = redact(text)
            for f in findings:
                totals[f['type']] = totals.get(f['type'], 0) + int(f['count'])
            return masked

        out = []
        for m in messages:
            content = m['content']
            if isinstance(content, str):
                content = apply(content)
            else:
                content = [({**b, 'text': apply(str(b.get('text', '')))}
                            if isinstance(b, dict) and b.get('type') == 'text' else b) for b in content]
            out.append({'role': m['role'], 'content': content})
        dynamic = apply(system_dynamic) if system_dynamic else system_dynamic
        return out, dynamic, [{'type': k, 'count': v} for k, v in sorted(totals.items())]

    # -- errors (most specific SDK class first) --
    def _sdk_error(self, exc: BaseException, model: str) -> ProviderError:
        import anthropic
        status = getattr(exc, 'status_code', None)
        request_id = getattr(exc, 'request_id', None)
        log.warning('Claude error %s status=%s type=%s request_id=%s', type(exc).__name__, status,
                    getattr(exc, 'type', None), request_id)

        def err(kind: str, message: str) -> ProviderError:
            return ProviderError(kind, message, provider=PROVIDER, model=model, status=status, request_id=request_id)

        if isinstance(exc, anthropic.AuthenticationError):
            if os.getenv('ANTHROPIC_API_KEY') and os.getenv('ANTHROPIC_AUTH_TOKEN'):
                return err('auth', 'Identifiants Claude refusés : ANTHROPIC_API_KEY et ANTHROPIC_AUTH_TOKEN sont '
                                   'définies toutes les deux, gardez-en une seule.')
            return err('auth', 'Identifiants Claude refusés (clé invalide ou révoquée).')
        if isinstance(exc, anthropic.PermissionDeniedError):
            return err('auth', "Accès refusé par Anthropic pour ce modèle ou cette organisation.")
        if isinstance(exc, anthropic.NotFoundError):
            return err('bad_request', f'Modèle Claude « {model} » introuvable ou non disponible pour ce compte.')
        if isinstance(exc, anthropic.RequestTooLargeError):
            return err('bad_request', 'Requête trop volumineuse pour Claude : réduisez le texte envoyé.')
        if isinstance(exc, anthropic.RateLimitError):
            return err('rate_limit', 'Limite de débit Claude atteinte : réessayez dans un instant.')
        if isinstance(exc, anthropic.OverloadedError):
            return err('overloaded', 'Claude est momentanément surchargé : réessayez plus tard.')
        if isinstance(exc, anthropic.ServiceUnavailableError):
            return err('unavailable', 'Service Claude momentanément indisponible.')
        if isinstance(exc, anthropic.DeadlineExceededError):
            return err('timeout', "Claude n'a pas répondu dans le délai imparti.")
        if isinstance(exc, anthropic.InternalServerError):
            return err('unavailable', 'Erreur interne du service Claude : réessayez plus tard.')
        if isinstance(exc, (anthropic.BadRequestError, anthropic.UnprocessableEntityError, anthropic.ConflictError)):
            return err('bad_request', 'Requête refusée par Claude (paramètres invalides).')
        if isinstance(exc, anthropic.APIStatusError):
            if status == 402:
                return err('budget', 'Compte Anthropic : problème de facturation (crédit épuisé ?).')
            if status is not None and status >= 500:
                return err('unavailable', 'Erreur du service Claude : réessayez plus tard.')
            return err('bad_request', 'Requête refusée par Claude.')
        if isinstance(exc, anthropic.APITimeoutError):
            return err('timeout', "Claude n'a pas répondu dans le délai imparti.")
        if isinstance(exc, anthropic.APIConnectionError):
            return err('connection', 'Connexion à Claude impossible (réseau indisponible ?).')
        if isinstance(exc, anthropic.CredentialsError):
            return err('auth', 'Identifiants Claude introuvables côté serveur.')
        if isinstance(exc, anthropic.APIResponseValidationError):
            return err('unavailable', 'Réponse de Claude inattendue.')
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return err('timeout', "Claude n'a pas répondu dans le délai imparti.")
        return err('unavailable', 'Service Claude indisponible.')

    # -- chat --
    async def _stream_once(self, params: dict, use_fallback: bool, on_token: TokenCallback | None,
                           timeout: float) -> Any:
        client = self._get_client()
        if use_fallback:
            manager = client.beta.messages.stream(**params, betas=[FALLBACK_BETA], fallbacks='default', timeout=timeout)
        else:
            manager = client.messages.stream(**params, timeout=timeout)
        async with manager as stream:
            async for event in stream:
                if _attr(event, 'type') == 'content_block_delta':
                    delta = _attr(event, 'delta')
                    if _attr(delta, 'type') == 'text_delta' and on_token is not None:
                        piece = str(_attr(delta, 'text', '') or '')
                        if piece:
                            await on_token(piece)
            return await stream.get_final_message()

    async def chat(self, messages: list[dict], *, system: str | list = '', tier: str = DEFAULT_TIER,
                   max_tokens: int = 4096, on_token: TokenCallback | None = None, web_search: bool = False,
                   timeout: float = 180, system_dynamic: str = '') -> ProviderResult:
        spec = self.tier(tier)
        model, effort = spec['model'], spec['effort']
        if not self.configured():
            raise ProviderError('unavailable', "Claude n'est pas configuré (aucune clé dans le coffre Windows ni dans ANTHROPIC_API_KEY).",
                                provider=PROVIDER, model=model)
        if offline() and not self._injected:
            raise ProviderError('unavailable', "Mode hors ligne : Claude n'est pas appelé.", provider=PROVIDER,
                                model=model)
        msgs = validate_messages(messages, provider=PROVIDER)
        msgs, system_dynamic, redactions = self._redact(msgs, system_dynamic)
        system_list = system if isinstance(system, list) else [system]
        # An image block is its base64 text here, far longer than the tokens it costs (12/11/2026, the reference
        # photos of a maquette): counted as a flat number of characters, not letter by letter.
        chars = sum(len(m['content']) if isinstance(m['content'], str)
                    else sum(IMAGE_CHARS if isinstance(b, dict) and b.get('type') == 'image' else len(str(b))
                             for b in m['content']) for m in msgs)
        chars += sum(len(str(s)) for s in system_list) + len(system_dynamic or '')
        self._check_budget(self.estimate_cost(chars, tier), model)

        params: dict[str, Any] = {'model': model, 'max_tokens': int(max_tokens), 'messages': msgs,
                                  'output_config': {'effort': effort}}
        blocks = _system_blocks(system, system_dynamic)
        if blocks:
            params['system'] = blocks
        if web_search:
            params['tools'] = [dict(WEB_SEARCH_TOOL)]
        use_fallback = self._fallback_enabled(model)

        started = time.perf_counter()
        usage = _Usage()
        sources: dict[str, dict] = {}
        search_errors: list[str] = []
        texts: list[str] = []
        switches: list[dict] = []
        final: Any = None
        continuations = 0
        convo = list(msgs)
        try:
            async with asyncio.timeout(timeout):
                while True:
                    final = await self._stream_once({**params, 'messages': convo}, use_fallback, on_token, timeout)
                    usage.add(final)
                    content = list(_attr(final, 'content', None) or [])
                    for block in content:
                        if _attr(block, 'type') == 'fallback':
                            switches.append({'from': str(_attr(_attr(block, 'from_'), 'model', '') or ''),
                                             'to': str(_attr(_attr(block, 'to'), 'model', '') or '')})
                    stop_reason = str(_attr(final, 'stop_reason', '') or '')
                    if stop_reason == 'refusal':
                        texts.append(_collect(content, sources, search_errors))
                        break
                    texts.append(_collect(content, sources, search_errors))
                    if stop_reason == 'pause_turn' and continuations < MAX_CONTINUATIONS:
                        continuations += 1
                        convo = convo + [{'role': 'assistant', 'content': _echo_content(content)}]
                        continue
                    break
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to typed, safe errors below
            import anthropic
            if isinstance(exc, (anthropic.AnthropicError, TimeoutError, asyncio.TimeoutError)):
                raise self._sdk_error(exc, model) from None
            raise

        stop_reason = str(_attr(final, 'stop_reason', '') or '')
        served_by = str(_attr(final, 'model', '') or model)
        meta: dict[str, Any] = {
            'tier': tier, 'effort': effort, 'requested_model': model, 'message_id': _attr(final, 'id'),
            'fallback': {'enabled': use_fallback, 'used': usage.fallback_ran or bool(switches),
                         'served_by': served_by, 'switches': switches},
            'web_search': bool(web_search), 'web_search_requests': usage.web_searches,
            'continuations': continuations, 'redactions': redactions,
        }
        if web_search:
            ordered = sorted(sources.values(), key=lambda s: not s.get('cited'))
            meta['sources'] = ordered
            if search_errors:
                meta['web_search_errors'] = search_errors
        text = ''.join(texts)

        if stop_reason == 'refusal':
            details = _attr(final, 'stop_details')
            category = _attr(details, 'category')
            label = REFUSAL_CATEGORIES_FR.get(str(category), 'non précisée') if category else 'non précisée'
            meta.update({'refusal_category': category, 'partial_discarded': bool(text)})
            partial = usage.result(text, served_by, started, stop_reason, meta)
            raise ProviderError('refusal', f'Claude a refusé cette demande (catégorie : {label}). '
                                           "Reformulez-la ou utilisez l'IA locale.",
                                provider=PROVIDER, model=served_by, category=category, result=partial)
        if stop_reason == 'max_tokens':
            meta.update({'truncated': True, 'warning_fr': 'Réponse tronquée : limite de jetons atteinte.'})
        elif stop_reason == 'model_context_window_exceeded':
            meta.update({'truncated': True, 'warning_fr': 'Réponse tronquée : fenêtre de contexte du modèle atteinte.'})
        elif stop_reason == 'pause_turn':
            meta.update({'truncated': True, 'warning_fr': 'Recherche interrompue : trop de reprises nécessaires.'})
        return usage.result(text, served_by, started, stop_reason, meta)


claude = AnthropicProvider()
