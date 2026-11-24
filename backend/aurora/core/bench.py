"""The routing bench (tests/bench_routing.py) for the running service, loaded by its file path.

Importing it as « tests.bench_routing » first runs tests/__init__.py, whose TEST settings (Windows vault,
local SearXNG and page reading turned off) then stay in the running service until it restarts: one click on
« Lancer le banc » in Paramètres cut the Claude key off. Found on 28/10/2026 while merging H3 (« Signaler »),
whose report list read the bench the same way. Loaded here by path, under its own name, outside the « tests »
package: tests/__init__.py never runs in the service.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

BENCH_FILE = Path(__file__).resolve().parents[2] / 'tests' / 'bench_routing.py'
_NAME = 'aurora_routing_bench'


def module() -> ModuleType:
    """tests/bench_routing.py as a module of its own (it imports only aurora.*). ImportError if absent."""
    loaded = sys.modules.get(_NAME)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(_NAME, BENCH_FILE)
    if spec is None or spec.loader is None or not BENCH_FILE.is_file():
        raise ImportError(f'Banc de routage introuvable : {BENCH_FILE}')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = loaded
    try:
        spec.loader.exec_module(loaded)
    except BaseException:
        sys.modules.pop(_NAME, None)
        raise
    return loaded
