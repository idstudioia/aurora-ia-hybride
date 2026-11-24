"""Shared types for AI providers (CONTRACT.md §6): results, typed errors, provider protocol."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

TokenCallback = Callable[[str], Awaitable[None]]

ERROR_KINDS = (
    'unavailable', 'auth', 'rate_limit', 'overloaded', 'bad_request',
    'timeout', 'refusal', 'budget', 'connection',
)
# Kinds worth retrying later without changing the request.
RETRYABLE_KINDS = frozenset({'rate_limit', 'overloaded', 'timeout', 'connection', 'unavailable'})


@dataclass
class ProviderResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    stop_reason: str = ''
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ProviderError(Exception):
    """Provider failure with a message that is always safe to show in the UI.

    `message_fr` never contains credentials, request headers or raw provider payloads.
    `result` carries token usage when the call was billed anyway (e.g. a refusal
    after partial output), so the caller can still record the spend.
    """

    def __init__(self, kind: str, message_fr: str, *, provider: str = '', model: str = '',
                 status: int | None = None, request_id: str | None = None,
                 category: str | None = None, result: ProviderResult | None = None) -> None:
        if kind not in ERROR_KINDS:
            kind = 'unavailable'
        super().__init__(message_fr)
        self.kind = kind
        self.message_fr = message_fr
        self.provider = provider
        self.model = model
        self.status = status
        self.request_id = request_id
        self.category = category
        self.result = result

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def __str__(self) -> str:
        return self.message_fr

    def to_dict(self) -> dict:
        return {
            'kind': self.kind, 'message': self.message_fr, 'provider': self.provider,
            'model': self.model, 'status': self.status, 'category': self.category,
            'retryable': self.retryable,
        }


@runtime_checkable
class ChatProvider(Protocol):
    """Minimal interface shared by OllamaProvider and AnthropicProvider."""

    async def chat(self, messages: list[dict], *, system: str, max_tokens: int = ...,
                   on_token: TokenCallback | None = None, timeout: float = ...) -> ProviderResult: ...


def config_value(env_name: str, default: str, *config_attrs: str) -> str:
    """Resolve a setting: explicit environment variable, then aurora.config, then default.

    The environment wins so tests and the launcher can override at runtime; aurora.config
    (owned by another module) is imported lazily and is optional.
    """
    value = os.getenv(env_name)
    if value is not None and value.strip():
        return value.strip()
    try:
        from aurora import config  # type: ignore[attr-defined]
    except Exception:
        config = None
    if config is not None:
        for attr in config_attrs or (env_name,):
            candidate = getattr(config, attr, None)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return default


def offline() -> bool:
    """AURORA_OFFLINE=1 forbids every network call (tests, air-gapped use)."""
    return config_value('AURORA_OFFLINE', '0', 'OFFLINE', 'AURORA_OFFLINE').lower() in ('1', 'true', 'yes', 'oui')


def validate_messages(messages: Any, *, provider: str) -> list[dict]:
    """Check the shared message shape [{role: user|assistant, content: str | blocks}].

    History windows cut from a conversation may start with an assistant turn or contain
    'note' rows (messages table): those are dropped, since the first turn must be the user's.
    """
    if not isinstance(messages, list) or not messages:
        raise ProviderError('bad_request', 'Aucun message à envoyer au modèle.', provider=provider)
    clean: list[dict] = []
    for item in messages:
        role = item.get('role') if isinstance(item, dict) else None
        if role == 'note':
            continue
        if role not in ('user', 'assistant'):
            raise ProviderError('bad_request', 'Format de message invalide (rôle user ou assistant attendu).',
                                provider=provider)
        content = item.get('content')
        if not isinstance(content, (str, list)):
            raise ProviderError('bad_request', 'Format de message invalide (contenu texte attendu).',
                                provider=provider)
        if not clean and role == 'assistant':
            continue
        clean.append({'role': role, 'content': content})
    if not clean:
        raise ProviderError('bad_request', 'La conversation doit contenir un message utilisateur.',
                            provider=provider)
    return clean
