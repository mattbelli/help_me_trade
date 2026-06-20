"""Shared performance context sanitization for LLM surfaces."""

from __future__ import annotations

import copy
from typing import Any

_HOLD_TIME_FIELDS = frozenset(
    {"symbol", "open_date", "close_date", "is_option", "asset_class"}
)

_FIFO_TRADE_LISTS = ("largest_wins", "largest_losses", "recent_closed_trades")


def _strip_unreliable_pnl_fields(trade: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(trade, dict):
        return trade
    return {k: v for k, v in trade.items() if k in _HOLD_TIME_FIELDS}


def sanitize_perf_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Remove unreliable FIFO dollar fields before LLM consumption."""
    sanitized = copy.deepcopy(ctx)
    fifo = sanitized.get("approximate_fifo")
    if isinstance(fifo, dict):
        for key in _FIFO_TRADE_LISTS:
            trades = fifo.get(key)
            if isinstance(trades, list):
                fifo[key] = [_strip_unreliable_pnl_fields(t) for t in trades]
    return sanitized


_INCOMPLETE_NOTE = (
    "proceeds only — no cost basis, NOT P&L. Do not include in realized totals."
)


def format_perf_insights_for_llm(ctx: dict[str, Any]) -> dict[str, Any]:
    """Shape sanitized performance context so the LLM leads with reliable numbers."""
    sanitized = sanitize_perf_context(ctx)
    realized = sanitized.get("realized") or {}
    incomplete = realized.get("incomplete_positions") or []
    labeled_incomplete: list[dict[str, Any]] = []
    for pos in incomplete:
        if isinstance(pos, dict):
            labeled_incomplete.append({**pos, "label": _INCOMPLETE_NOTE})

    fifo = sanitized.get("approximate_fifo")
    if isinstance(fifo, dict):
        fifo = {**fifo, "unreliable_fifo": True}

    return {
        "headline": {
            "realized_closed": realized.get("realized_closed"),
            "closed_win_rate": realized.get("closed_win_rate"),
            "note": (
                "Headline realized P&L and win rate — net cash on fully closed positions."
            ),
        },
        "period": sanitized.get("period"),
        "from_date": sanitized.get("from_date"),
        "to_date": sanitized.get("to_date"),
        "realized": {
            **realized,
            "incomplete_positions": labeled_incomplete,
            "incomplete_positions_note": _INCOMPLETE_NOTE,
        },
        "entry_style": sanitized.get("entry_style"),
        "behavioral": sanitized.get("behavioral"),
        "day_of_week": sanitized.get("day_of_week"),
        "asset_classes": sanitized.get("asset_classes"),
        "top_symbols": sanitized.get("top_symbols"),
        "behavioral_flags": sanitized.get("behavioral_flags"),
        "unrealized": sanitized.get("unrealized"),
        "approximate_fifo": fifo,
    }


def sanitize_trade_history_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Separate FIFO win-rate stats from reliable cash-flow fields."""
    out = dict(stats)
    fifo_fields = ("round_trips", "wins", "losses", "win_rate")
    fifo_stats = {key: out.pop(key) for key in fifo_fields if key in out}
    if fifo_stats:
        out["approximate_fifo"] = {
            **fifo_stats,
            "unreliable_fifo": True,
            "note": (
                "FIFO approximate — not headline P&L. "
                "Use get_performance_insights for realized totals."
            ),
        }
    return out
