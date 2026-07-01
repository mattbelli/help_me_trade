"""Decision Journal read API — the agent's own paper-call track record AND the user's
real broker trade history, surfaced side-by-side (read-only).

Two separate stores, unified only at this read layer (the founder-agreed shape): the AI
side (``decision_journal``) is proposal + prediction + decision + calibration; the user
side (``my_trades``) is raw broker fills + behavior. Both reuse the perception-layer
readers/summarizers in ``ai.perception.resources`` so the shapes stay in one place.

Places no orders and mutates nothing. Registered by ``api/main`` via
``app.include_router`` (behind SecurityMiddleware), same as ``api/scan_routes``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/journal", tags=["journal"])

_MAX_LIMIT = 50


def _clamp_limit(limit: int) -> int:
    return min(max(limit, 1), _MAX_LIMIT)


@router.get("/decisions")
def api_journal_decisions(
    symbol: str | None = None,
    strategy: str | None = None,
    outcome: str | None = None,
    limit: int = 20,
) -> Any:
    """The agent's recorded decisions (+ an aggregate stats block). AI side."""
    # Lazy import keeps the router decoupled from api.main's import graph.
    from ai.perception.resources import (
        _read_decision_journal,
        _summarize_decision_journal,
    )
    from db.base import DEFAULT_DB_PATH

    if limit < 1 or limit > _MAX_LIMIT:
        return JSONResponse(
            {"error": f"limit must be 1–{_MAX_LIMIT}"}, status_code=400
        )
    filters = {
        "symbol": symbol,
        "strategy": strategy,
        "outcome": outcome,
        "limit": _clamp_limit(limit),
    }
    decisions = _read_decision_journal(filters, db_path=DEFAULT_DB_PATH)
    stats = _summarize_decision_journal(filters, db_path=DEFAULT_DB_PATH)
    return {"count": len(decisions), "decisions": decisions, "stats": stats}


@router.get("/stats")
def api_journal_stats(strategy: str | None = None) -> Any:
    """Per-strategy realized stats (win rate · avg R · EV · n). AI side."""
    from ai.perception.resources import _summarize_decision_journal
    from db.base import DEFAULT_DB_PATH

    return _summarize_decision_journal({"strategy": strategy}, db_path=DEFAULT_DB_PATH)


@router.get("/trades")
def api_journal_trades(symbol: str | None = None, limit: int = 20) -> Any:
    """The user's recent closed round-trips (FIFO-approximate P&L). Me side."""
    from ai.perception.resources import _read_my_trades
    from db.base import DEFAULT_DB_PATH

    if limit < 1 or limit > _MAX_LIMIT:
        return JSONResponse(
            {"error": f"limit must be 1–{_MAX_LIMIT}"}, status_code=400
        )
    trades = _read_my_trades(
        {"symbol": symbol, "limit": _clamp_limit(limit)}, db_path=DEFAULT_DB_PATH
    )
    return {"count": len(trades), "trades": trades}


@router.get("/trades/summary")
def api_journal_trades_summary(
    symbol: str | None = None, entry_style: bool = False
) -> Any:
    """Behavioral profile + net-cash realized headline. Me side.

    ``entry_style`` is opt-in (default off): it needs daily bars, so leaving it off keeps
    the call broker-free — we never burst Schwab for a background read (see schwab-data-ops).
    """
    from ai.perception.resources import _summarize_my_trades
    from db.base import DEFAULT_DB_PATH

    summary = _summarize_my_trades({"symbol": symbol}, db_path=DEFAULT_DB_PATH)
    if entry_style:
        summary["entry_style"] = _entry_style_block(symbol, db_path=DEFAULT_DB_PATH)
    return summary


def _entry_style_block(symbol: str | None, *, db_path: Any) -> dict[str, Any]:
    """Aggregate entry-style lean (dip/breakout/momentum) — broker-gated, fail-soft."""
    try:
        from ai.perception.resources import _my_trade_events
        from brokers.factory import get_broker
        from config.settings import broker_is_configured, primary_broker
        from signals.trade_context import build_entry_style_block

        broker_name = primary_broker()
        if not broker_is_configured(broker_name):
            return {"available": False, "note": "broker not configured"}
        events = _my_trade_events({"symbol": symbol}, db_path=db_path)
        block = build_entry_style_block(events, broker=get_broker(broker_name))
        return {"available": True, "profile": block.get("profile")}
    except Exception as exc:
        return {"available": False, "note": f"entry-style unavailable: {exc}"}
