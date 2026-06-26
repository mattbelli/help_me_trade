"""Validated-strategy backtest stats — the honest "conviction" on a strategy's proposals.

Loads `research/artifacts/strategy_stats.json` (produced OFFLINE on the PC from the
survivorship-free Norgate backtests; gitignored + force-committed like the scorer
artifacts). Maps a live strategy id → its backtested win%/EV/CI so a proposal can carry
the strategy's REAL edge stats instead of the generic per-symbol decision scorer (which
is calibrated-but-edgeless, Brier ≈ base rate).

Display/decision-context only — NOT read by RiskEngine or the execution chokepoint.
Honesty: numbers come straight from the backtest; `validated` says how trustworthy
("validated" = significance-tested, "strong-unverified" = strong but no formal test yet,
"exploratory" = no edge). Never fabricated.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATS_PATH = (
    Path(__file__).resolve().parent.parent / "research" / "artifacts" / "strategy_stats.json"
)

# path -> (mtime, parsed) so we re-read only when the artifact changes
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def load_strategy_stats(path: Path = DEFAULT_STATS_PATH) -> dict[str, Any]:
    """Parse the strategy-stats artifact (mtime-cached). Returns {} if absent/unparseable."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
    except Exception:
        logger.warning("Failed to load %s", path, exc_info=True)
        return {}
    _cache[str(path)] = (mtime, data)
    return data


def get_strategy_stats(
    strategy_id: str, *, path: Path = DEFAULT_STATS_PATH
) -> dict[str, Any] | None:
    """Raw stats dict for a strategy id, or None if not present."""
    data = load_strategy_stats(path)
    return (data.get("strategies") or {}).get(strategy_id)


def strategy_conviction(
    strategy_id: str, *, path: Path = DEFAULT_STATS_PATH
) -> dict[str, Any] | None:
    """A proposal-score-shaped conviction payload from a strategy's backtest stats.

    Includes `win_prob` (= backtested win_rate) for back-compat with the phone/UI that
    read `score.win_prob`, plus explicit fields the generic scorer doesn't have. The CI
    here is the EV-per-trade CI (`ev_ci_pct`), NOT a win-probability CI — kept separate so
    nothing mislabels it. Returns None if the strategy has no stats entry.
    """
    s = get_strategy_stats(strategy_id, path=path)
    if not s:
        return None
    data = load_strategy_stats(path)
    ev_ci = s.get("ev_ci_pct")
    return {
        "basis": "strategy_backtest",
        "strategy_id": strategy_id,
        "label": s.get("label"),
        "win_prob": s.get("win_rate"),  # back-compat (winProb on phone, UI score card)
        "win_rate": s.get("win_rate"),
        "ev_pct": s.get("ev_pct"),
        "ev_ci_pct": ev_ci if isinstance(ev_ci, list) else None,
        "n_setups": s.get("n_setups"),
        "per_year_positive": s.get("per_year_positive"),
        "validated": s.get("validated"),
        "method": s.get("method"),
        "note": s.get("note"),
        "source": data.get("source"),
        "as_of": s.get("as_of") or data.get("as_of"),
    }
