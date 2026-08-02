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


FLOW_WATCHLIST_ID = "flow"


def flow_watch_symbols() -> list[str]:
    """Underlyings the options-flow watcher covers.

    The dedicated ``flow`` watchlist (founder-curated, slot-capped — 07-22
    game plan) when it exists: pinned symbols ride FIRST so a downstream
    underlying cap clips unpinned tail before it ever touches a pin. Falls
    back to the union of all lists so the watcher never goes dark if the flow
    list is missing.
    """
    state = get_watchlists_state()
    for lst in state.get("lists", []):
        if lst.get("id") != FLOW_WATCHLIST_ID:
            continue
        symbols = [
            s.strip().upper()
            for s in lst.get("symbols", [])
            if isinstance(s, str) and s.strip()
        ]
        pinned = {
            s.strip().upper() for s in lst.get("pinned", []) if isinstance(s, str)
        }
        return [s for s in symbols if s in pinned] + [
            s for s in symbols if s not in pinned
        ]
    return all_watchlist_symbols()


def flow_watch_symbols_fresh() -> list[str]:
    """Out-of-process variant of :func:`flow_watch_symbols` — see
    :func:`all_watchlist_symbols_fresh` for why the cache is dropped."""
    reload_settings()
    return flow_watch_symbols()


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


def open_episode_symbols() -> list[str]:
    """Symbols carrying an OPEN fusion episode — one cheap indexed read.

    Import-guarded and fail-soft: the universe must never hard-depend on the
    fusion tables existing (out-of-process callers, fresh installs, a worker
    pointed at a db the fusion schema has not been initialized on)."""
    try:
        from db.base import DEFAULT_DB_PATH
        from db.base import connect as _connect

        with _connect(DEFAULT_DB_PATH) as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM fusion_episodes WHERE state = 'open'"
            ).fetchall()
    except Exception:
        logger.debug("Open-episode symbols unavailable", exc_info=True)
        return []
    return sorted({str(r["symbol"]).strip().upper() for r in rows if r["symbol"]})


def profile_universe() -> list[str]:
    """Symbols worth a precomputed volume profile.

    Open fusion episodes are included (2026-07-28): the rvol_ex_prints dot
    divides today's volume by that symbol's baseline, so a name the flow lane
    surfaced from OUTSIDE the watchlist/liquid core has no baseline and no
    reading at all until its profile is built."""
    return sorted(
        set(liquid_core_symbols())
        | set(all_watchlist_symbols())
        | set(open_episode_symbols())
    )
