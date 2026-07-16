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

# --- Regime-local (Tier-B) sleeves ---------------------------------------------------
# These sleeves are REGIME-LOCAL (References/REGIME_SLEEVES_SPEC.md): qualified on
# 2021–2026 (mostly last-24m) evidence under a founder-directed fork of the research
# process — NOT tier-1 validated edges. They must NEVER appear in strategy_stats.json
# as validated and must never ship a bare win% number: win_prob is None by design and
# the card shows the committed `regime_stats` block (with caveats) instead. The numbers
# below are COMMITTED from the offline backtests — the LLM never generates them.
# strategy_conviction() checks this registry FIRST, so even a (mistaken) future
# strategy_stats.json entry for these ids can never override the Tier-B labeling.
_REGIME_LOCAL_NOTE = "regime-local evidence — expected finite lifetime; kill line armed"

REGIME_LOCAL_CONVICTION: dict[str, dict[str, Any]] = {
    "generals_dip": {
        "basis": "regime_local_backtest",
        "strategy_id": "generals_dip",
        "label": "Generals Dip (regime-local, Tier-B)",
        "win_prob": None,
        "validated": "regime-local",
        "sleeve": "regime_local",
        "as_of": "2026-07-02",
        "regime_stats": {
            "window": "2025-06..2026-06",
            "excess_ev_pct": 3.63,
            "ci": [0.11, 4.66],
            "p_value": 0.021,
            "win_rate_pct": 60,
            "n": 399,
            "horizon": "h=10td fixed hold, excess over SPY, net 10bps",
            "full_window": "2021–26: +0.14%/trade (not significant)",
            "note": _REGIME_LOCAL_NOTE,
            "caveats": (
                "last-12m leans on the 2026 recovery leg; strategy was negative in "
                "2021–2022 tape."
            ),
        },
        "plan": {
            "kind": "generals_dip",
            "exit_basis": "time",
            "max_holding_days": 10,
            "rules": [
                "Enter next open after a top-30 3d flush (close>SMA50, 3d ≤ −3.5%)",
                "Exit at T+11 open — 10 trading-day fixed hold (no stop/target)",
            ],
        },
    },
    "drawdown_stab": {
        "basis": "regime_local_backtest",
        "strategy_id": "vol_event",
        "label": "Vol-Event: drawdown stabilization (regime-local, Tier-B)",
        "win_prob": None,
        "validated": "regime-local",
        "sleeve": "regime_local",
        "trigger_id": "drawdown_stab",
        "as_of": "2026-07-02",
        "regime_stats": {
            "window": "post-2020",
            "episodes": "8/10 wins, median +6.2%/episode",
            "note": _REGIME_LOCAL_NOTE,
            "caveats": "episodic small-n — descriptive only, never a win% probability.",
        },
        "plan": {
            "kind": "vol_event",
            "exit_basis": "price_or_time",
            "max_holding_days": 30,
            "rules": [
                "Enter next open after the first stabilization day in a ≥5% SPY "
                "drawdown with VIX ≥ 25",
                "Exit next open after SPY close recovers the trailing 20d-high level",
                "30 trading-day cap",
            ],
        },
    },
    "term_flip": {
        "basis": "regime_local_backtest",
        "strategy_id": "vol_event",
        "label": "Vol-Event: VIX term-structure flip (regime-local, Tier-B)",
        "win_prob": None,
        "validated": "regime-local",
        "sleeve": "regime_local",
        "trigger_id": "term_flip",
        "as_of": "2026-07-02",
        "regime_stats": {
            "window": "post-2020 (21d hold)",
            "episodes": "13/14 wins, mean +5.0% vs +1.4% unconditional baseline",
            "note": _REGIME_LOCAL_NOTE,
            "caveats": "episodic small-n — descriptive only, never a win% probability.",
        },
        "plan": {
            "kind": "vol_event",
            "exit_basis": "time",
            "max_holding_days": 21,
            "rules": [
                "Enter next open after VIX/VIX3M flips back ≤ 1.0 (≥2 days inverted)",
                "Exit after 21 trading days (fixed hold)",
            ],
        },
    },
}


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

    Regime-local (Tier-B) sleeves are served from REGIME_LOCAL_CONVICTION first:
    win_prob=None + the committed regime_stats block — never from strategy_stats.json.
    """
    regime_local = REGIME_LOCAL_CONVICTION.get(strategy_id)
    if regime_local is not None:
        # Deep-enough copy: the caller may attach/overwrite "plan" on the result.
        return json.loads(json.dumps(regime_local))

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


def strategy_plan(strategy_id: str, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """The strategy's VALIDATED exit plan, expressed as rules, from its live params + kind.

    Honest by construction: this is the backtested exit logic (the same params
    `signals.exit_evaluators` runs), NOT an unvalidated S/R scale-out ladder. Rules are
    percentages/time (no entry price needed — strategy entries are market orders).
    Returns None for kinds without a defined price/time plan (e.g. exploratory).
    """
    from db.base import DEFAULT_DB_PATH
    from db.strategies import get_strategy
    from signals.strategy_kinds import strategy_kind

    strat = get_strategy(strategy_id, db_path=db_path or DEFAULT_DB_PATH)
    if strat is None:
        return None
    params = strat.get("params") or {}
    kind = strategy_kind(strategy_id)

    if kind == "dip":
        stop = abs(float(params.get("stop_pct", -3.0)))
        target = float(params.get("first_target_pct", 3.0))
        trail = float(params.get("trail_pct", 11.0))
        cap = int(params.get("max_holding_days", 20))
        return {
            "kind": "dip",
            "exit_basis": "price",
            "stop_pct": -stop,
            "first_target_pct": target,
            "trail_pct": trail,
            "max_holding_days": cap,
            "rules": [
                f"Stop −{stop:g}%",
                f"Take +{target:g}% (sell half → stop to breakeven)",
                f"Trail {trail:g}%",
                f"Time cap {cap} trading days",
            ],
        }
    if kind == "earnings":
        lead = int(params.get("exit_lead_td", 1))
        cap = int(params.get("max_holding_days", 15))
        return {
            "kind": "earnings",
            "exit_basis": "time",
            "exit_lead_td": lead,
            "max_holding_days": cap,
            "rules": [
                f"Exit ~{lead} trading day{'s' if lead != 1 else ''} before next earnings",
                f"Max hold {cap} trading days",
            ],
        }
    return None
