"""Symbol-universe helpers shared by the backend and out-of-process workers.

Extracted from ``api/main.py`` (R1a, References/RESEARCH_WORKER_DESIGN_2026-07-18.md)
so research jobs can build their symbol sets without importing the app module.
The API process owns settings writes; out-of-process callers use the ``*_fresh``
variant to pick up watchlist edits without a restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import get_watchlists_state, reload_settings

logger = logging.getLogger(__name__)

UNIVERSE_DIR = Path(__file__).resolve().parent / "universes"
LIQUID_CORE_FILES = ("sp500.json", "russell1000.json", "nasdaq100.json")


def all_watchlist_symbols() -> list[str]:
    state = get_watchlists_state()
    syms: set[str] = set()
    for lst in state.get("lists", []):
        for s in lst.get("symbols", []):
            if isinstance(s, str) and s.strip():
                syms.add(s.strip().upper())
    return sorted(syms)


def all_watchlist_symbols_fresh() -> list[str]:
    """Out-of-process variant: drop the settings cache first so watchlist edits
    made in the API are picked up without a worker restart (the flow-collector
    pattern — ``tools/flow_collector.py:watchlist_symbols``)."""
    reload_settings()
    return all_watchlist_symbols()


def liquid_core_symbols() -> list[str]:
    syms: set[str] = set()
    for fname in LIQUID_CORE_FILES:
        try:
            data = json.loads((UNIVERSE_DIR / fname).read_text())
            for s in data.get("symbols", []):
                if isinstance(s, str) and s.strip():
                    syms.add(s.strip().upper())
        except Exception:
            logger.exception("Failed to load universe file %s", fname)
    return sorted(syms)


def profile_universe() -> list[str]:
    return sorted(set(liquid_core_symbols()) | set(all_watchlist_symbols()))
