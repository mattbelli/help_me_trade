"""Strategy-kind registry — uniform entry dispatch for live strategy scans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from db.base import DEFAULT_DB_PATH
from db.earnings_calendar import earnings_dates_for
from research.strategy_features import (
    compute_market_context,
    is_earnings_drift_entry,
    is_strategy_entry,
)
from signals.indicators import closes, pct_change
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
    return is_strategy_entry(
        ctx.sym_bars,
        ctx.idx,
        spy_bars=ctx.spy_bars,
        spy_idx=ctx.spy_idx,
        **ctx.params,
    )


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


DIP_ENTRY_PARAM_KEYS = (
    "healthy_required",
    "oversold_3d_max",
    "mkt_down_3d_max",
    "side",
    "far_support_min",
)
EARNINGS_ENTRY_PARAM_KEYS = (
    "blowout_pct",
    "entry_lead_td",
    "exit_lead_td",
    "min_gap_td",
    "max_gap_td",
)

STRATEGY_KINDS: dict[str, StrategyKind] = {
    "dip": StrategyKind(
        "dip", DIP_ENTRY_PARAM_KEYS, _dip_check, _dip_rationale
    ),
    "earnings": StrategyKind(
        "earnings", EARNINGS_ENTRY_PARAM_KEYS, _earnings_check, _earnings_rationale
    ),
}


def strategy_kind(strategy_id: str) -> str | None:
    """Infer kind from id. Mirrors trading/exit_engine._strategy_kind (keep them consistent)."""
    sid = (strategy_id or "").lower()
    if "earnings" in sid:
        return "earnings"
    if "dip" in sid:
        return "dip"
    return None


def kind_spec(strategy_id: str) -> StrategyKind | None:
    k = strategy_kind(strategy_id)
    return STRATEGY_KINDS.get(k) if k else None


def filter_entry_params(spec: StrategyKind, params: dict[str, Any]) -> dict[str, Any]:
    return {k: params[k] for k in spec.entry_param_keys if k in params}
