"""Shared HTTP helpers (leaf module: imports nothing from AURORA)."""
from __future__ import annotations

import functools
import ssl

import httpx


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Verified TLS context (system CA bundle, as httpx does by default), built once.

    httpx builds a new one for every client it creates, which costs about 190 ms on this
    machine (CA bundle loading) even for plain-http calls to Ollama. Passing this context as
    ``verify=`` keeps certificate verification identical and removes that cost.
    """
    return httpx.create_ssl_context()
