"""Daily cloud budget: limit in settings, spend summed from the `cloud_usage` table.

« Today » is the user's local calendar day (timestamps are stored in UTC).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from aurora.providers import pricing

log = logging.getLogger(__name__)

SETTING_KEY = 'cloud.daily_budget_usd'           # mode Équilibré (and the historical single budget)
DEFAULT_BUDGET_USD = 1.0
LOCAL_PROVIDERS = frozenset({'ollama', 'local', 'tool', ''})

# CLAUDE.md « Les trois modes » : the Avancé mode has higher ceilings, per day and per request.
AVANCE_SETTING_KEY = 'cloud.daily_budget_avance_usd'
DEFAULT_AVANCE_BUDGET_USD = 3.0
REQUEST_SETTING_KEY = 'cloud.request_budget_usd'
DEFAULT_REQUEST_BUDGET_USD = 0.25
REQUEST_AVANCE_SETTING_KEY = 'cloud.request_budget_avance_usd'
DEFAULT_REQUEST_AVANCE_BUDGET_USD = 1.0
MIN_CLOUD_OUTPUT_TOKENS = 1024   # adaptive thinking counts in max_tokens: below this, no useful answer
CHARS_PER_TOKEN = 3.0            # same conservative ratio as the Claude provider's estimate


def _db() -> Any:
    from aurora import db  # lazy: db.py is owned by another module
    return db


def _as_budget(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number < 0:
        return None
    return number


def _setting_usd(key: str, default: float) -> float:
    value = _as_budget(_db().get_setting(key, default))
    return default if value is None else value


def _mode(mode: str | None) -> str:
    from .policy import normalize_mode   # same mode resolution as the permission policy
    return normalize_mode(mode or _db().get_setting('mode', 'equilibre'))


def daily_budget_usd(mode: str | None = None) -> float:
    """Daily ceiling of the given mode (the current one by default)."""
    if _mode(mode) == 'avance':
        return _setting_usd(AVANCE_SETTING_KEY, DEFAULT_AVANCE_BUDGET_USD)
    return _setting_usd(SETTING_KEY, DEFAULT_BUDGET_USD)


def request_budget_usd(mode: str | None = None) -> float:
    """Per-request ceiling of the given mode (the current one by default)."""
    if _mode(mode) == 'avance':
        return _setting_usd(REQUEST_AVANCE_SETTING_KEY, DEFAULT_REQUEST_AVANCE_BUDGET_USD)
    return _setting_usd(REQUEST_SETTING_KEY, DEFAULT_REQUEST_BUDGET_USD)


def set_daily_budget_usd(value: float) -> float:
    number = _as_budget(value)
    if number is None:
        raise ValueError('Le budget cloud doit être un nombre positif ou nul.')
    _db().set_setting(SETTING_KEY, round(number, 4))
    return number


def day_start_utc(now: datetime | None = None) -> str:
    """Local midnight of the current day, as the ISO UTC string format used in the database."""
    local = (now or datetime.now(timezone.utc)).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def spent_today_usd() -> float:
    row = _db().row('SELECT COALESCE(SUM(cost_usd), 0) AS total FROM cloud_usage WHERE created_at >= ?',
                    (day_start_utc(),))
    return round(float((row or {}).get('total') or 0.0), 6)


def remaining_usd() -> float:
    return round(max(0.0, daily_budget_usd() - spent_today_usd()), 6)


def can_spend(amount_usd: float) -> bool:
    remaining = remaining_usd()
    return remaining > 0 and float(amount_usd or 0) <= remaining


def _usd_fr(value: float) -> str:
    return f'{value:.3f}'.replace('.', ',') + ' $'


def request_output_cap(model: str | None, prompt_chars: int, ceiling: int, cap_usd: float | None = None) -> dict:
    """Largest max_tokens keeping the worst case of one request under the per-request ceiling.

    Worst case = the whole prompt billed as uncached input + `max_tokens` of output (thinking
    included). The generation stops at that limit, so the ceiling can never be exceeded.
    `max_tokens` is None when the ceiling cannot buy MIN_CLOUD_OUTPUT_TOKENS: nothing must be sent.
    """
    cap = request_budget_usd() if cap_usd is None else max(0.0, float(cap_usd))
    input_tokens = math.ceil(max(0, int(prompt_chars or 0)) / CHARS_PER_TOKEN)
    prices = pricing.prices_for(model, input_tokens)
    input_usd = input_tokens * prices['input'] / 1_000_000
    per_output = prices['output'] / 1_000_000
    affordable = math.floor((cap - input_usd) / per_output) if per_output > 0 else int(ceiling)
    max_tokens = min(int(ceiling), affordable)
    out = {'cap_usd': round(cap, 6), 'input_usd': round(input_usd, 6), 'limited': affordable < int(ceiling)}
    if max_tokens < MIN_CLOUD_OUTPUT_TOKENS:
        return {**out, 'max_tokens': None, 'max_cost_usd': round(input_usd, 6),
                'reason': f'Plafond de dépense par requête ({_usd_fr(cap)}) trop bas pour cette demande : '
                          f'le texte seul coûterait {_usd_fr(input_usd)}, sans place pour une réponse.'}
    return {**out, 'max_tokens': max_tokens, 'max_cost_usd': round(input_usd + max_tokens * per_output, 6),
            'reason': ''}


def record_usage(result: Any, task_id: int | None) -> None:
    """Persist the billed usage of a cloud call (ProviderResult). Local providers are ignored."""
    if result is None or str(getattr(result, 'provider', '') or '') in LOCAL_PROVIDERS:
        return
    tokens = [int(getattr(result, name, 0) or 0) for name in
              ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')]
    cost = float(getattr(result, 'cost_usd', 0.0) or 0.0)
    if cost <= 0 and any(tokens):
        cost = pricing.cost_usd(getattr(result, 'model', ''), *tokens)
    if cost <= 0 and not any(tokens):
        return
    db = _db()
    with db.connect() as conn:
        conn.execute(
            'INSERT INTO cloud_usage(task_id, provider, model, input_tokens, output_tokens, cache_read_tokens, '
            'cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (task_id, str(result.provider), str(getattr(result, 'model', '') or ''), *tokens, round(cost, 6)))


def usage_summary() -> dict:
    """Shape of GET /api/usage: {today_usd, budget_usd, remaining_usd, by_model:[{model, cost_usd, calls, ...}]}."""
    budget = daily_budget_usd()
    rows = _db().rows(
        'SELECT model, COUNT(*) AS calls, COALESCE(SUM(cost_usd), 0) AS cost_usd, '
        'COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens '
        'FROM cloud_usage WHERE created_at >= ? GROUP BY model ORDER BY cost_usd DESC', (day_start_utc(),))
    # known_model False: priced at the most expensive card (estimate, not the real list price).
    by_model = [{'model': r['model'], 'cost_usd': round(float(r['cost_usd']), 6), 'calls': int(r['calls']),
                 'input_tokens': int(r['input_tokens']), 'output_tokens': int(r['output_tokens']),
                 'known_model': pricing.is_known(r['model'])} for r in rows]
    today = round(sum(item['cost_usd'] for item in by_model), 6)
    return {'today_usd': today, 'budget_usd': budget, 'remaining_usd': round(max(0.0, budget - today), 6),
            'by_model': by_model}
