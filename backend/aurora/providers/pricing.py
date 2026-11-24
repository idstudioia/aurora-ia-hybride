"""Claude list prices in USD per million tokens (MTok).

Sources: claude-api skill (models.md / model-migration.md, 2026-10). cache_write is the
5-minute ephemeral write rate (1.25x input); AURORA only uses 5-minute cache entries.
"""
from __future__ import annotations

PRICES: dict[str, dict[str, float]] = {
    'claude-haiku-5-5': {'input': 0.10, 'output': 0.50, 'cache_read': 0.01, 'cache_write': 0.125},
    'claude-sonnet-5-5': {'input': 2.00, 'output': 10.00, 'cache_read': 0.20, 'cache_write': 2.50},
    'claude-opus-5-5': {'input': 4.00, 'output': 20.00, 'cache_read': 0.20, 'cache_write': 5.00},
    'claude-fable-5-1': {'input': 10.00, 'output': 50.00, 'cache_read': 0.25, 'cache_write': 12.50},
    # Server-side refusal fallback targets ($5 / $25; cache at the standard 0.1x / 1.25x ratios).
    'claude-opus-5': {'input': 5.00, 'output': 25.00, 'cache_read': 0.50, 'cache_write': 6.25},
    'claude-opus-4-8': {'input': 5.00, 'output': 25.00, 'cache_read': 0.50, 'cache_write': 6.25},
    # Other active models a tier may be configured with (AURORA_CLOUD_MODEL_*), claude-api skill catalog
    # 2026-10 (cache at the standard 0.1x / 1.25x ratios; Claude Fable 5 reads cache at $1/MTok).
    'claude-fable-5': {'input': 10.00, 'output': 50.00, 'cache_read': 1.00, 'cache_write': 12.50},
    'claude-opus-4-7': {'input': 5.00, 'output': 25.00, 'cache_read': 0.50, 'cache_write': 6.25},
    'claude-opus-4-6': {'input': 5.00, 'output': 25.00, 'cache_read': 0.50, 'cache_write': 6.25},
    'claude-opus-4-5': {'input': 5.00, 'output': 25.00, 'cache_read': 0.50, 'cache_write': 6.25},
    'claude-sonnet-5': {'input': 2.00, 'output': 10.00, 'cache_read': 0.20, 'cache_write': 2.50},
    'claude-sonnet-4-6': {'input': 3.00, 'output': 15.00, 'cache_read': 0.30, 'cache_write': 3.75},
    'claude-sonnet-4-5': {'input': 3.00, 'output': 15.00, 'cache_read': 0.30, 'cache_write': 3.75},
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00, 'cache_read': 0.10, 'cache_write': 1.25},
}

# Prompt-length dependent rate cards: above `threshold` prompt tokens the whole request
# is billed at `prices` (Haiku 5.5: $0.50 / $2.50, cache at 0.1x / 1.25x of that input rate).
LONG_PROMPT: dict[str, dict] = {
    'claude-haiku-5-5': {
        'threshold': 100_000,
        'prices': {'input': 0.50, 'output': 2.50, 'cache_read': 0.05, 'cache_write': 0.625},
    },
}

# Server web search tool: $10 per 1,000 searches (Anthropic public pricing; not listed in the skill).
WEB_SEARCH_USD_PER_REQUEST = 0.01

# Unknown models are priced at the most expensive known card so budgets are never underestimated.
_CONSERVATIVE = max(PRICES.values(), key=lambda p: p['output'])


def resolve_model(model: str | None) -> str | None:
    """Map a model id (possibly dated or prefixed, e.g. 'anthropic.claude-opus-5-5') to a PRICES key."""
    if not model:
        return None
    name = model.strip().lower()
    if name.startswith('anthropic.'):
        name = name[len('anthropic.'):]
    if name in PRICES:
        return name
    # Longest key first so 'claude-opus-5-5-…' never resolves to 'claude-opus-5'.
    for key in sorted(PRICES, key=len, reverse=True):
        if name.startswith(key + '-') or name.startswith(key + '@'):
            return key
    return None


def is_known(model: str | None) -> bool:
    return resolve_model(model) is not None


def prices_for(model: str | None, prompt_tokens: int = 0) -> dict[str, float]:
    key = resolve_model(model)
    if key is None:
        return dict(_CONSERVATIVE)
    long = LONG_PROMPT.get(key)
    if long and prompt_tokens > long['threshold']:
        return dict(long['prices'])
    return dict(PRICES[key])


def cost_breakdown(model: str | None, input_tokens: int, output_tokens: int,
                   cache_read: int = 0, cache_write: int = 0, web_searches: int = 0) -> dict:
    """Per-component cost in USD; prompt length (input + cache) selects the rate card."""
    input_tokens, output_tokens = max(0, int(input_tokens or 0)), max(0, int(output_tokens or 0))
    cache_read, cache_write = max(0, int(cache_read or 0)), max(0, int(cache_write or 0))
    p = prices_for(model, input_tokens + cache_read + cache_write)
    parts = {
        'input': input_tokens * p['input'] / 1e6,
        'output': output_tokens * p['output'] / 1e6,
        'cache_read': cache_read * p['cache_read'] / 1e6,
        'cache_write': cache_write * p['cache_write'] / 1e6,
        'web_search': max(0, int(web_searches or 0)) * WEB_SEARCH_USD_PER_REQUEST,
    }
    total = sum(parts.values())
    return {**{k: round(v, 8) for k, v in parts.items()}, 'total': round(total, 8),
            'known_model': is_known(model)}


def cost_usd(model: str | None, input_tokens: int, output_tokens: int,
             cache_read: int = 0, cache_write: int = 0) -> float:
    return round(cost_breakdown(model, input_tokens, output_tokens, cache_read, cache_write)['total'], 6)
