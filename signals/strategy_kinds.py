"""Strategy-kind registry — uniform entry dispatch for live strategy scans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from db.base import DEFAULT_DB_PATH
from db.earnings_calendar import earnings_dates_for
from db.regime import recent_observations
from research.strategy_features import (
    compute_market_context,
    is_earnings_drift_entry,
    is_strategy_entry,
    is_vix_spike_entry,
)
from signals.indicators import closes, pct_change
from signals.regime import market_regime, regime_is_fresh
from signals.trade_context import DatedBar


@dataclass(frozen=True)
class EntryContext:
    sym: str
    sym_bars: list[DatedBar]
    idx: int
    spy_bars: list[DatedBar]
    spy_idx: int
    params: dict[str, Any]
    db_path: Path = DEFAULT_DB_PATH


@dataclass(frozen=True)
class StrategyKind:
    name: str
    entry_param_keys: tuple[str, ...]
    check_entry: Callable[[EntryContext], bool]
    build_rationale: Callable[[EntryContext, str], str]


def _dip_check(ctx: EntryContext) -> bool:
    yc_min = ctx.params.get("yc_min_spread")
    entry_params = {k: v for k, v in ctx.params.items() if k != "yc_min_spread"}
    if not is_strategy_entry(
        ctx.sym_bars,
        ctx.idx,
        spy_bars=ctx.spy_bars,
        spy_idx=ctx.spy_idx,
        **entry_params,
    ):
        return False
    if yc_min is not None:
        if not regime_is_fresh():
            return False
        regime = market_regime()
        if regime is None or regime["term_spread"] <= float(yc_min):
            return False
    return True


def _dip_rationale(ctx: EntryContext, strategy_name: str) -> str:
    ret_3d = pct_change(closes(ctx.sym_bars[: ctx.idx + 1]), 3)
    mkt_ctx = compute_market_context(ctx.spy_bars, ctx.spy_idx)
    mkt_ret_3d = mkt_ctx.get("mkt_ret_3d") if mkt_ctx else None
    ret_str = f"{ret_3d:.1f}" if ret_3d is not None else "?"
    mkt_str = f"{mkt_ret_3d:.1f}" if mkt_ret_3d is not None else "?"
    return (
        f"{strategy_name}: {ctx.sym} {ret_str}%/3d, SPY {mkt_str}%/3d, >SMA200"
    )


def _earnings_check(ctx: EntryContext) -> bool:
    dates = earnings_dates_for(ctx.sym, db_path=ctx.db_path)
    if not dates:
        return False
    return is_earnings_drift_entry(
        ctx.sym_bars, ctx.idx, earnings_dates=dates, **ctx.params
    )


def _earnings_rationale(ctx: EntryContext, strategy_name: str) -> str:
    dates = earnings_dates_for(ctx.sym, db_path=ctx.db_path)
    today = ctx.sym_bars[ctx.idx]["d"]
    next_date = next((d for d in dates if d > today), None)
    next_str = next_date if next_date is not None else "n/a"
    return f"{strategy_name}: {ctx.sym} pre-earnings drift, next earnings {next_str}"


def _vix_spike_check(ctx: EntryContext) -> bool:
    # SPY-only, regime-driven. FAIL-CLOSED: no fresh regime / not enough VIX history ⇒ don't fire.
    if not regime_is_fresh(db_path=ctx.db_path):
        return False
    lookback = int(ctx.params.get("vix_calm_lookback", 12))
    obs = recent_observations("VIXCLS", limit=lookback + 1, db_path=ctx.db_path)
    if len(obs) < lookback + 1:
        return False
    return is_vix_spike_entry(
        obs[0][1],  # newest VIX = today
        [v for _, v in obs[1:]],  # the prior closes (newest-first; count-only)
        vix_spike_min=float(ctx.params.get("vix_spike_min", 28.0)),
        vix_calm_max=float(ctx.params.get("vix_calm_max", 22.0)),
        vix_calm_lookback=lookback,
        vix_calm_min_days=int(ctx.params.get("vix_calm_min_days", 8)),
    )


def _vix_spike_rationale(ctx: EntryContext, strategy_name: str) -> str:
    obs = recent_observations("VIXCLS", limit=1, db_path=ctx.db_path)
    vix_str = f"{obs[0][1]:.1f}" if obs else "?"
    return f"{strategy_name}: VIX spike {vix_str} from calm — buy-fear {ctx.sym} long"


DIP_ENTRY_PARAM_KEYS = (
    "healthy_required",
    "oversold_3d_max",
    "mkt_down_3d_max",
    "side",
    "far_support_min",
    "yc_min_spread",
)
EARNINGS_ENTRY_PARAM_KEYS = (
    "blowout_pct",
    "entry_lead_td",
    "exit_lead_td",
    "min_gap_td",
    "max_gap_td",
)
VIX_SPIKE_ENTRY_PARAM_KEYS = (
    "vix_spike_min",
    "vix_calm_max",
    "vix_calm_lookback",
    "vix_calm_min_days",
)

STRATEGY_KINDS: dict[str, StrategyKind] = {
    "dip": StrategyKind(
        "dip", DIP_ENTRY_PARAM_KEYS, _dip_check, _dip_rationale
    ),
    "earnings": StrategyKind(
        "earnings", EARNINGS_ENTRY_PARAM_KEYS, _earnings_check, _earnings_rationale
    ),
    "vix_spike": StrategyKind(
        "vix_spike", VIX_SPIKE_ENTRY_PARAM_KEYS, _vix_spike_check, _vix_spike_rationale
    ),
}


# Regime-local (Tier-B) sleeves — they run on their own post-close loop
# (signals.regime_sleeves), NOT through the generic strategy scan; kind_spec() stays
# None for them on purpose. NOTE: trading/exit_engine._strategy_kind still maps
# "generals_dip" → "dip" (substring), which is exactly the wiring the sleeve relies on
# for its 10-trading-day fixed-hold exit (all price legs None). vol_event exits are
# managed by the sleeve module itself (the exit engine skips unknown kinds).
REGIME_SLEEVE_KINDS = ("generals_dip", "vol_event")


def strategy_kind(strategy_id: str) -> str | None:
    """Infer kind from id. NOTE: trading/exit_engine._strategy_kind covers only dip/earnings
    (it's reached only AFTER is_portfolio_kind filters momentum out) — they diverge by design."""
    sid = (strategy_id or "").lower()
    if sid.startswith("momentum"):
        return "momentum"
    # Regime sleeves BEFORE the substring checks ("generals_dip" must not match "dip").
    if "generals" in sid:
        return "generals_dip"
    if "vol_event" in sid:
        return "vol_event"
    if "vix" in sid:
        return "vix_spike"
    if "earnings" in sid:
        return "earnings"
    if "dip" in sid:
        return "dip"
    return None


def is_portfolio_kind(strategy_id: str) -> bool:
    """True for portfolio-rebalance strategies (rank → diff vs holdings)."""
    return strategy_kind(strategy_id) == "momentum"


def kind_spec(strategy_id: str) -> StrategyKind | None:
    k = strategy_kind(strategy_id)
    return STRATEGY_KINDS.get(k) if k else None


def filter_entry_params(spec: StrategyKind, params: dict[str, Any]) -> dict[str, Any]:
    return {k: params[k] for k in spec.entry_param_keys if k in params}
