"""Generic resource query — registry + dispatcher (read-only)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from config.settings import get_watchlists_state
from db.base import DEFAULT_DB_PATH
from db.chart_features import list_price_alerts
from db.paper_orders import get_proposal, list_pending_proposals
from db.reports import list_reports
from db.strategies import list_strategies

logger = logging.getLogger(__name__)

_MAX_ROWS = 50
_PREVIEW_LEN = 200

ReaderFn = Callable[..., list[dict[str, Any]]]


def _cap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[:_MAX_ROWS]


def _read_watchlists(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path
    state = get_watchlists_state()
    rows = [
        {
            "id": wl["id"],
            "name": wl["name"],
            "symbols": wl.get("symbols", []),
        }
        for wl in state.get("lists", [])
        if isinstance(wl, dict) and wl.get("id") and wl.get("name") is not None
    ]
    name_filter = filters.get("name")
    if name_filter:
        name_lower = str(name_filter).lower()
        rows = [r for r in rows if str(r["name"]).lower() == name_lower]
    return _cap_rows(rows)


def _read_strategies(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    enabled_only = bool(filters.get("enabled_only"))
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "enabled": s["enabled"],
            "account_id": s.get("account_id"),
            "notional_usd": s["notional_usd"],
            "exit_mode": s["exit_mode"],
            "universe": s["universe"],
            "max_concurrent_positions": s.get("max_concurrent_positions"),
            "max_deployed_usd": s.get("max_deployed_usd"),
        }
        for s in list_strategies(enabled_only=enabled_only, db_path=db_path)
    ]
    return _cap_rows(rows)


def _read_price_alerts(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path
    symbol = filters.get("symbol")
    sym = str(symbol).strip().upper() if symbol else None
    active_only = filters.get("active_only", True)
    if not isinstance(active_only, bool):
        active_only = bool(active_only)
    rows = [
        {
            "id": a.get("id"),
            "symbol": a.get("symbol"),
            "price": a.get("price"),
            "direction": a.get("direction"),
            "repeat": bool(a.get("repeat")),
            "note": a.get("note") or "",
            "triggered": bool(a.get("triggered")),
        }
        for a in list_price_alerts(symbol=sym, active_only=active_only)
    ]
    return _cap_rows(rows)


def _read_proposals(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    account_id = filters.get("account_id")
    acct = str(account_id) if account_id else None
    rows: list[dict[str, Any]] = []
    for summary in list_pending_proposals(account_id=acct, db_path=db_path):
        full = get_proposal(summary["id"], db_path=db_path) or {}
        rows.append(
            {
                "id": full.get("id") or summary["id"],
                "symbol": full.get("symbol") or summary["symbol"],
                "side": full.get("side") or summary["side"],
                "qty": full.get("qty") or summary["qty"],
                "order_type": full.get("order_type"),
                "status": full.get("status"),
                "source": full.get("source"),
                "created_at": full.get("created_at"),
            }
        )
    return _cap_rows(rows)


def _trim_preview(text: str | None) -> str | None:
    if not text:
        return None
    trimmed = text.strip()
    if len(trimmed) <= _PREVIEW_LEN:
        return trimmed
    return trimmed[:_PREVIEW_LEN].rstrip() + "…"


def _read_reports(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    limit = min(max(int(filters.get("limit", 10)), 1), _MAX_ROWS)
    rows: list[dict[str, Any]] = []
    for report in list_reports(limit=limit, db_path=db_path):
        row: dict[str, Any] = {
            "id": report["id"],
            "kind": report["kind"],
            "title": report["title"],
            "symbol": report.get("symbol"),
            "created_at": report.get("generated_at"),
        }
        preview = _trim_preview(report.get("body"))
        if preview:
            row["preview"] = preview
        rows.append(row)
    return rows


# --- Decision Journal (the agent's OWN paper calls) --------------------------------------
# Reads the write-once decision_journal (CONTEXT/DECISION/OUTCOME per proposal). Empty until
# proposals resolve — the honest state, not a fabricated one. Reuses db.decision_journal's
# J1 query fns; never re-derives P&L.

_DECISION_SUMMARY_WINDOW = 500
_MIN_DECIDED_FOR_STATS = 5


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _compact_decision(row: dict[str, Any]) -> dict[str, Any]:
    score = row.get("score")
    return {
        "proposal_id": row.get("proposal_id"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "strategy_id": row.get("strategy_id"),
        "source": row.get("source"),
        "thesis": _trim_preview(row.get("thesis")),
        "win_prob": score.get("win_prob") if isinstance(score, dict) else None,
        "ev_pct": score.get("ev_pct") if isinstance(score, dict) else None,
        "decision": row.get("decision"),
        "outcome": row.get("outcome"),
        "realized_pnl": row.get("realized_pnl"),
        "r_multiple": row.get("r_multiple"),
        "holding_days": row.get("holding_days"),
        "created_at": row.get("created_at"),
    }


def _read_decision_journal(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    from db.decision_journal import by_strategy, by_symbol, list_recent

    symbol = filters.get("symbol")
    strategy = filters.get("strategy")
    outcome = filters.get("outcome")
    limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    # Over-fetch when the outcome filter is applied client-side so we still return ~limit.
    fetch = min(max(limit, 100), 500) if outcome else limit
    if symbol:
        rows = by_symbol(str(symbol), limit=fetch, db_path=db_path)
    elif strategy:
        rows = by_strategy(str(strategy), limit=fetch, db_path=db_path)
    else:
        rows = list_recent(limit=fetch, db_path=db_path)
    if outcome:
        want = str(outcome).strip().lower()
        rows = [r for r in rows if (r.get("outcome") or "") == want]
    return [_compact_decision(r) for r in rows[:limit]]


def _aggregate_decision_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = losses = r_n = pnl_n = 0
    r_total = 0.0
    pnl_total = Decimal(0)
    for r in rows:
        outcome = r.get("outcome")
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        rm = r.get("r_multiple")
        if rm is not None:
            r_total += float(rm)
            r_n += 1
        pnl = _decimal_or_none(r.get("realized_pnl"))
        if pnl is not None:
            pnl_total += pnl
            pnl_n += 1
    decided = wins + losses
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / decided) if decided else None,
        "avg_r_multiple": (r_total / r_n) if r_n else None,
        "net_realized_pnl": str(pnl_total) if pnl_n else None,
        "note": None if decided >= _MIN_DECIDED_FOR_STATS else "insufficient data",
    }


def _summarize_decision_journal(
    filters: dict[str, Any], *, db_path: Path
) -> dict[str, Any]:
    from db.decision_journal import list_recent, stats_by_strategy

    strategy = filters.get("strategy")
    if strategy:
        return {
            "overall": stats_by_strategy(str(strategy), db_path=db_path),
            "by_strategy": [],
        }
    rows = list_recent(limit=_DECISION_SUMMARY_WINDOW, db_path=db_path)
    overall = _aggregate_decision_stats(rows)
    strategies = sorted(
        {str(r.get("strategy_id")) for r in rows if r.get("strategy_id")}
    )
    by_strategy = [stats_by_strategy(s, db_path=db_path) for s in strategies[:20]]
    return {"overall": overall, "by_strategy": by_strategy}


# --- My trades (the USER's real broker fills) --------------------------------------------
# Surfaces the honest analysis that already exists — does NOT re-derive P&L. Recent trades =
# FIFO closed round-trips (flagged approximate); the summary leads with net-cash realized
# (the source of truth) + behavioral pattern. Net-cash truth vs FIFO honesty is preserved.


def _win_label(pnl: Any) -> str | None:
    dec = _decimal_or_none(pnl)
    if dec is None:
        return None
    return "win" if dec > 0 else "loss" if dec < 0 else "scratch"


def _hold_days(open_date: Any, close_date: Any) -> int | None:
    from datetime import date

    try:
        return (
            date.fromisoformat(str(close_date)) - date.fromisoformat(str(open_date))
        ).days
    except (ValueError, TypeError):
        return None


def _compact_trade(rt: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": rt.get("symbol"),
        "asset_class": rt.get("asset_class"),
        "side": "option" if rt.get("is_option") else "long",
        "quantity": rt.get("quantity"),
        "open_date": rt.get("open_date"),
        "close_date": rt.get("close_date"),
        "hold_days": _hold_days(rt.get("open_date"), rt.get("close_date")),
        "pnl": rt.get("pnl"),
        "outcome": _win_label(rt.get("pnl")),
        "pnl_basis": "fifo_approximate",
    }


def _my_trade_events(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from db.accounts import list_accounts
    from db.trades import list_trade_events

    symbol = filters.get("symbol")
    sym = str(symbol).strip().upper() if symbol else None
    account_id = filters.get("account_id")
    if account_id:
        return list_trade_events(
            symbol=sym, account_id=str(account_id), limit=100_000, db_path=db_path
        )
    # Default = the user's REAL trading. EXCLUDE the paper Alpaca sandbox so the
    # agent never blends fake-money trades into the real track record — mirrors the
    # UI's paper↔real firewall. (Denylist, not allowlist: a real trade with an
    # unrecognized/other broker still counts.)
    paper_ids = {a["id"] for a in list_accounts(broker="alpaca", db_path=db_path)}
    events = list_trade_events(symbol=sym, limit=100_000, db_path=db_path)
    return [e for e in events if e.get("account_id") not in paper_ids]


def _read_my_trades(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    from db.analytics import compute_round_trips

    limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    events = _my_trade_events(filters, db_path=db_path)
    round_trips = compute_round_trips(events)  # already sorted close_date DESC
    return [_compact_trade(rt) for rt in round_trips[:limit]]


def _summarize_my_trades(
    filters: dict[str, Any], *, db_path: Path
) -> dict[str, Any]:
    from db.behavioral import behavioral_facts
    from db.realized_pnl import realized_summary

    events = _my_trade_events(filters, db_path=db_path)
    recon = realized_summary(events)
    behavior = behavioral_facts(events)
    return {
        "net_cash_realized": {
            "realized_closed": recon["realized_closed"],
            "closed_win_rate": recon["closed_win_rate"],
            "reliability": recon["reliability"],
        },
        "behavioral": {
            "trade_count": behavior["trade_count"],
            "date_range": behavior["date_range"],
            "asset_mix": behavior["asset_mix"],
            "top_symbols": behavior["symbol_concentration"]["by_trade_count"][:3],
            "same_day_flips": behavior["same_day_flips"]["total"],
            "buy_sell_ratio": behavior["buy_sell_ratio"]["overall"],
        },
        "honesty": (
            "Net-cash realized is the source of truth; per-trade round-trip P&L is "
            "FIFO-approximate (unreliable on date-only exports)."
        ),
    }


def _read_schwab_accounts(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    """Live balances + open positions for the user's REAL Schwab accounts.

    READ-ONLY — surfaces what's held; the agent has no path to trade these."""
    from brokers.schwab_accounts import get_schwab_accounts_sync

    data = get_schwab_accounts_sync()
    if not data.get("authenticated"):
        return []
    return [
        {
            "account": a.get("label"),
            "type": a.get("account_type"),
            "money_type": "real",
            "read_only": True,
            "portfolio_value": a.get("liquidation_value"),
            "cash": a.get("cash"),
            "long_market_value": a.get("long_market_value"),
            "unrealized_pl": a.get("unrealized_pl"),
            "positions": [
                {
                    "symbol": p.get("symbol"),
                    "qty": p.get("qty"),
                    "market_value": p.get("market_value"),
                    "unrealized_pl": p.get("unrealized_pl"),
                }
                for p in a.get("positions", [])
            ],
        }
        for a in data.get("accounts", [])
    ]


def _summarize_schwab_accounts(
    filters: dict[str, Any], *, db_path: Path
) -> dict[str, Any]:
    from brokers.schwab_accounts import get_schwab_accounts_sync

    data = get_schwab_accounts_sync()
    if not data.get("authenticated"):
        return {"authenticated": False, "note": "Schwab not connected."}
    totals = data.get("totals", {})
    return {
        "money_type": "real",
        "read_only": True,
        "account_count": totals.get("account_count"),
        "total_liquidation_value": totals.get("liquidation_value"),
        "total_cash": totals.get("cash"),
        "total_long_market_value": totals.get("long_market_value"),
        "note": (
            "Real funded Schwab accounts — READ-ONLY. The agent can see these "
            "balances/positions but cannot place, cancel, or modify any order here."
        ),
    }


# ⚠️ MAINTENANCE CONTRACT: when you add a new data source / feature to the app that the
# copilot should be able to read, ADD AN ENTRY HERE (description + filters + a fail-soft
# reader reusing the existing function). That one line is what makes the agent able to
# query it — the perception layer does NOT auto-discover new data. (Same idea as
# get_context for per-symbol fields.) See References/PERCEPTION_LAYER.md.
RESOURCES: dict[str, dict[str, Any]] = {
    "watchlists": {
        "description": "User watchlists and their symbols.",
        "filters": {"name": "optional — filter by watchlist name"},
        "read": _read_watchlists,
    },
    "strategies": {
        "description": "Configured trading strategies and enablement.",
        "filters": {"enabled_only": "optional bool — only enabled strategies"},
        "read": _read_strategies,
    },
    "price_alerts": {
        "description": "Chart price alerts (active or triggered).",
        "filters": {
            "symbol": "optional — filter by ticker",
            "active_only": "optional bool — default true",
        },
        "read": _read_price_alerts,
    },
    "proposals": {
        "description": "Paper order proposals pending phone approval.",
        "filters": {"account_id": "optional — filter by paper account id"},
        "read": _read_proposals,
    },
    "reports": {
        "description": "Stored reports (morning levels, research, etc.).",
        "filters": {"limit": "optional int — default 10, max 50"},
        "read": _read_reports,
    },
    "decision_journal": {
        "description": (
            "The agent's OWN paper-trade track record — recorded proposals with the "
            "win%/EV it predicted, the approve/reject decision, and the resolved "
            "outcome (win/loss, P&L, R-multiple). Empty until proposals resolve."
        ),
        "filters": {
            "symbol": "optional — filter by ticker",
            "strategy": "optional — filter by strategy_id",
            "outcome": "optional — win|loss|scratch|open|never_filled|closed_incomplete",
            "limit": "optional int — default 20, max 50",
        },
        "read": _read_decision_journal,
        "summarize": _summarize_decision_journal,
    },
    "my_trades": {
        "description": (
            "The USER's REAL broker trade history (the paper Alpaca sandbox is "
            "excluded) — recent closed round-trips plus a behavioral profile "
            "(cadence, concentration, asset mix) and the net-cash realized P&L "
            "headline (source of truth; per-trade P&L is FIFO-approximate)."
        ),
        "filters": {
            "symbol": "optional — filter by ticker",
            "account_id": "optional — one real account (default: all real/Schwab accounts)",
            "limit": "optional int — default 20, max 50",
        },
        "read": _read_my_trades,
        "summarize": _summarize_my_trades,
    },
    "schwab_accounts": {
        "description": (
            "The user's REAL Schwab accounts — live balances, cash, and open "
            "positions (READ-ONLY: the agent can see them but CANNOT trade them). "
            "Real money; kept separate from the paper Alpaca sandbox — never blend them."
        ),
        "filters": {},
        "read": _read_schwab_accounts,
        "summarize": _summarize_schwab_accounts,
    },
}


def list_resources() -> dict[str, dict[str, Any]]:
    """Catalog for the manifest: {name: {description, filters}}."""
    return {
        name: {
            "description": entry["description"],
            "filters": entry["filters"],
        }
        for name, entry in RESOURCES.items()
    }


async def query(
    resource: str,
    filters: dict | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Dispatch a read-only query to a registered resource (fail-soft)."""
    key = resource.strip().lower()
    if key not in RESOURCES:
        return {
            "error": "unknown resource",
            "available": sorted(RESOURCES.keys()),
        }

    entry = RESOURCES[key]
    reader: ReaderFn = entry["read"]
    try:
        rows = reader(filters or {}, db_path=db_path)
    except Exception as exc:
        logger.debug("Resource read failed for %s", key, exc_info=True)
        return {"resource": key, "error": str(exc), "rows": []}

    result: dict[str, Any] = {"resource": key, "count": len(rows), "rows": rows}
    # Opt-in aggregate block for resources that register a ``summarize`` fn (e.g. the
    # journal's stats / the user's behavioral profile). Fail-soft — a summary failure
    # never sinks the rows.
    summarize = entry.get("summarize")
    if summarize is not None:
        try:
            result["summary"] = summarize(filters or {}, db_path=db_path)
        except Exception:
            logger.debug("Resource summarize failed for %s", key, exc_info=True)
    return result
