"""Session token and origin checks for the local HTTP API (CONTRACT.md §6, §8).

The token is minted at process start and handed out only by `/api/bootstrap` to an allowed
browser origin. Mutating routes require it in `X-Aurora-Token` (or `?token=` for SSE, since
EventSource cannot set headers). A request with no Origin header is NOT an allowed origin:
the frontend (port 5173) always calls the API (port 8765) cross-origin, so browsers send it.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

TOKEN_HEADER = 'X-Aurora-Token'
TOKEN_QUERY = 'token'
DEFAULT_ALLOWED_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
LOCAL_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1', '[::1]'})
_TOKEN_SHAPE = re.compile(r'[A-Za-z0-9_\-]{32,256}')


def _initial_token() -> str:
    """A launcher (the planned Tauri desktop app) may hand the token to the service at start-up
    (AURORA_SESSION_TOKEN, 32+ URL-safe characters) and inject it into the interface, so that no
    HTTP route has to give it out (AURORA_BOOTSTRAP=0). Otherwise a random token per process."""
    given = os.getenv('AURORA_SESSION_TOKEN', '').strip()
    return given if _TOKEN_SHAPE.fullmatch(given) else secrets.token_urlsafe(32)


SESSION_TOKEN: str = _initial_token()


def bootstrap_enabled() -> bool:
    """AURORA_BOOTSTRAP=0: the token comes from the launcher only and /api/bootstrap answers 404."""
    return os.getenv('AURORA_BOOTSTRAP', '1').strip() != '0'


def _loopback_origin(origin: str) -> bool:
    """Only the local AURORA interface can be an allowed origin: http(s) on the loopback interface,
    exact scheme://host:port without path. A misconfigured public origin is ignored, never trusted."""
    try:
        parts = urlsplit(origin)
        host = (parts.hostname or '').lower()
    except ValueError:
        return False
    return (parts.scheme in ('http', 'https') and not parts.path.strip('/') and not parts.query
            and (host in LOCAL_HOSTS or host.endswith('.localhost')))


def allowed_origins() -> list[str]:
    """Environment variable first, then aurora.config.ALLOWED_ORIGINS (str or list), then default."""
    raw = os.getenv('AURORA_ALLOWED_ORIGINS', '').strip()
    if raw:
        items = raw.split(',')
    else:
        try:
            from aurora import config  # optional, owned by another module
            configured = getattr(config, 'ALLOWED_ORIGINS', None)
        except Exception:
            configured = None
        if isinstance(configured, str) and configured.strip():
            items = configured.split(',')
        elif isinstance(configured, (list, tuple)) and configured:
            items = [str(o) for o in configured]
        else:
            items = DEFAULT_ALLOWED_ORIGINS.split(',')
    cleaned = [o.strip().rstrip('/').lower() for o in items if o and o.strip() and o.strip() != '*']
    kept = [o for o in cleaned if _loopback_origin(o)]
    if len(kept) != len(cleaned):
        logging.getLogger(__name__).warning('Origines non locales ignorées : %s',
                                            ', '.join(o for o in cleaned if o not in kept))
    return kept


def origin_allowed(origin: str | None) -> bool:
    """Exact match (scheme, host, port) against AURORA_ALLOWED_ORIGINS; '*' is never honoured."""
    if not origin or not isinstance(origin, str):
        return False
    value = origin.strip().rstrip('/').lower()
    if value == 'null':
        return False
    return value in allowed_origins()


def host_allowed(host: str | None) -> bool:
    """Defence against DNS rebinding: the Host header must name the loopback interface."""
    if not host:
        return False
    try:
        hostname = urlsplit(f'//{host.strip()}').hostname or ''
    except ValueError:
        return False
    return hostname.lower() in LOCAL_HOSTS


def token_valid(candidate: str | None) -> bool:
    if not candidate or not isinstance(candidate, str):
        return False
    return secrets.compare_digest(candidate.encode('utf-8'), SESSION_TOKEN.encode('utf-8'))


def _extract(request: Request) -> str | None:
    header = request.headers.get(TOKEN_HEADER)
    if header:
        return header.strip()
    query = request.query_params.get(TOKEN_QUERY)
    return query.strip() if query else None


async def require_token(request: Request) -> None:
    """FastAPI dependency: 401 unless the session token is presented."""
    if not token_valid(_extract(request)):
        raise HTTPException(status_code=401, detail='Jeton de session manquant ou invalide.')
