"""Resolve a strategy's trading universe.

Decoupled from the cockpit watchlist on purpose: the watchlist is what the user
eyeballs and can switch at will, while a strategy's universe is where its
*validated edge* lives and must be stable. A strategy stores a ``universe`` spec:

- ``"sp500"``         — the built-in broad market list (default; faithful to the
                        broad-universe backtest, ~500 large-caps).
- ``"watchlist:<id>"``— a SPECIFIC named watchlist, pinned by id (NOT whichever
                        list happens to be active — that would silently repoint a
                        live strategy).
- ``"active_watchlist"`` — legacy/explicit: follow the active watchlist.
- empty / unknown     — falls back to the default broad universe.

Architected so additional built-ins (e.g. a dynamic liquid screen) can be added
as new specs without touching callers.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from config.settings import get_active_watchlist, get_watchlists_state

logger = logging.getLogger(__name__)

DEFAULT_UNIVERSE = "sp500"
_WATCHLIST_PREFIX = "watchlist:"
_UNIVERSE_DIR = Path(__file__).resolve().parent / "universes"

# Built-in (non-watchlist) universes, in display order.
_BUILTINS: tuple[tuple[str, str], ...] = (("sp500", "S&P 500 (broad market)"),)


@lru_cache(maxsize=None)
def _load_builtin_symbols(name: str) -> tuple[str, ...]:
    path = _UNIVERSE_DIR / f"{name}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load built-in universe %s", name)
        return ()
    symbols = data.get("symbols") if isinstance(data, dict) else None
    return tuple(
        str(s).strip().upper() for s in (symbols or []) if s and str(s).strip()
    )


def _watchlist_symbols(list_id: str, *, state: dict[str, Any] | None = None) -> list[str]:
    state = state or get_watchlists_state()
    for item in state.get("lists", []):
        if isinstance(item, dict) and item.get("id") == list_id:
            return [str(s).strip().upper() for s in item.get("symbols", []) if s]
    logger.warning("Strategy universe references unknown watchlist id %r", list_id)
    return []


def resolve_universe_spec(spec: str | None) -> list[str]:
    """Resolve a universe spec string to a sorted, de-duplicated symbol list."""
    normalized = (spec or "").strip()
    if not normalized:
        normalized = DEFAULT_UNIVERSE

    if normalized == "active_watchlist":
        symbols = [str(s).strip().upper() for s in get_active_watchlist().get("symbols", [])]
    elif normalized.startswith(_WATCHLIST_PREFIX):
        symbols = _watchlist_symbols(normalized[len(_WATCHLIST_PREFIX) :])
    else:
        builtin = _load_builtin_symbols(normalized)
        if not builtin and normalized != DEFAULT_UNIVERSE:
            logger.warning(
                "Unknown strategy universe %r — falling back to %s",
                normalized,
                DEFAULT_UNIVERSE,
            )
            builtin = _load_builtin_symbols(DEFAULT_UNIVERSE)
        symbols = list(builtin)

    return sorted({s for s in symbols if s})


def available_universes() -> list[dict[str, str]]:
    """Universe options for the UI picker — built-ins + each named watchlist."""
    options: list[dict[str, str]] = [
        {"id": key, "label": label, "kind": "builtin"} for key, label in _BUILTINS
    ]
    state = get_watchlists_state()
    for item in state.get("lists", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        name = item.get("name") or item["id"]
        options.append(
            {
                "id": f"{_WATCHLIST_PREFIX}{item['id']}",
                "label": f"Watchlist: {name}",
                "kind": "watchlist",
            }
        )
    return options
