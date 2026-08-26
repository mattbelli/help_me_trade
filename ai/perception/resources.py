"""Generic resource query — registry + dispatcher (read-only)."""

from __future__ import annotations

import asyncio
import json
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
        # my_trades promises REAL history — refuse a paper (alpaca) account id.
        paper_ids = {a["id"] for a in list_accounts(broker="alpaca", db_path=db_path)}
        if str(account_id) in paper_ids:
            return []
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


# --- Screener (candidate generator — cached bars / RVOL scanner paths) ---------------------

_SCREENER_MAX = 30
_PRESET_IDS = (
    "oversold",
    "overbought",
    "unusual_volume",
    "below_200dma",
    "above_200dma",
    "near_52w_high",
    "near_52w_low",
    "big_movers",
)


def _compact_screener_row(row: dict[str, Any], *, reason: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "symbol": row.get("symbol"),
        "price": row.get("price"),
        "reason": reason,
    }
    if row.get("change_pct") is not None:
        out["change_pct"] = row.get("change_pct")
    if row.get("rvol") is not None:
        out["rvol"] = row.get("rvol")
    return out


async def _read_screener(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    del db_path
    limit = min(max(int(filters.get("limit") or 15), 1), _SCREENER_MAX)
    use_rvol = bool(filters.get("rvol"))

    if use_rvol:
        from fastapi.responses import JSONResponse

        from api.scanner_routes import api_scanner_rvol

        min_rvol = float(filters.get("min_rvol", 1.5))
        payload = await api_scanner_rvol(min_rvol=min_rvol, limit=limit)
        if isinstance(payload, JSONResponse):
            return []
        rows: list[dict[str, Any]] = []
        for row in payload.get("results") or []:
            rvol = row.get("rvol")
            reason = f"RVOL {rvol}×" if rvol is not None else "unusual volume"
            rows.append(_compact_screener_row(row, reason=reason))
        return rows

    from api.scan_routes import _make_bars_getter, _screen_universe
    from signals.screener import get_preset, run_scan

    preset_id = str(filters.get("preset") or "oversold").strip().lower()
    preset = get_preset(preset_id)
    if preset is None:
        return []

    symbols = _screen_universe()
    get_bars = _make_bars_getter()
    scan = await run_scan(preset_id, symbols, get_bars, limit=limit)
    rows = []
    for row in scan.get("results") or []:
        metric_val = row.get("value")
        reason = (
            f"{preset.metric_label} {metric_val}"
            if metric_val is not None
            else preset.label
        )
        rows.append(_compact_screener_row(row, reason=reason))
    return rows


def _regime_label(term_spread: float, vix: float) -> str:
    if term_spread > 0.5:
        yc = "steep_yc"
    elif term_spread > 0:
        yc = "normal_yc"
    elif term_spread > -0.25:
        yc = "flat_yc"
    else:
        yc = "inverted_yc"
    if vix >= 28:
        vol = "elevated_vix"
    elif vix >= 20:
        vol = "moderate_vix"
    else:
        vol = "calm_vix"
    return f"{yc}_{vol}"


def _macro_block() -> dict[str, Any] | None:
    """The macro substrate (data/macro.db) — credit spreads, the full Treasury
    curve, the 10y real yield, breakevens and the broad dollar.

    Rides ``regime_report`` rather than re-deriving buckets: it already carries
    the honesty fence's reference-depth disclosure (``history``), which matters
    because ICE truncates the OAS series to a rolling window, so a credit
    percentile is shallow-by-construction and must say so. Buckets are the
    house 33/80 REPORTING cuts, never calibrated signals.
    """
    from datetime import datetime as _datetime

    from signals.macro_substrate import event_calendar, macro_snapshot, regime_report
    from trading.market_hours import _ET

    # ET, not the box clock — serv runs UTC, so between 00:00 and 04:00 UTC
    # `date.today()` is already tomorrow in New York terms and shifts the whole
    # staleness window by a day. Same trap `_fetch_cboe` documents.
    today = _datetime.now(_ET).date().isoformat()
    report = regime_report([today])
    axes: dict[str, Any] = {}
    for axis, block in report.get("axes", {}).items():
        observed = block.get("per_date", {}).get(today) or {}
        # Stale axes are STATED, never dropped — the contract signals/dossier.py
        # already claims for this same data. Dropping them used to empty `axes`,
        # and an empty `axes` returned None for the whole block, which silently
        # took the 29-series snapshot with it even though every series still had
        # data. A FRED publication halt over a week (a shutdown does exactly
        # this) removed the entire macro surface from the agent's view.
        axes[axis] = {
            "series": block.get("series"),
            "value": observed.get("value"),
            "as_of": observed.get("date_used"),
            "pctile": observed.get("pctile"),
            "bucket": observed.get("bucket"),
            "ranked_against": observed.get("ranked_against"),
            "history_n": (block.get("history") or {}).get("n"),
            "history_since": (block.get("history") or {}).get("first"),
        }
    series = macro_snapshot()
    if not axes and not series:
        return None
    return {
        "axes": axes,
        # The raw latest observation per stored series — the whole curve, so a
        # reader can build any spread rather than only the ones we picked.
        "series": series,
        # Scheduled releases. On 2026-08-11 neither paper book mentioned that CPI
        # printed the next morning — not a reasoning failure, they had no calendar
        # to read. Sizing into a print is a different decision from sizing on a
        # quiet Tuesday, so this rides the same block as the regime it modifies.
        "events": event_calendar(today),
        "note": (
            "percentiles are self-relative against this store's own history and "
            "TRUNCATED at the reported date (see history_since / ranked_against "
            "— windows differ per series, and credit is vendor-truncated to a "
            "rolling window, so its percentile contains neither 2008 nor 2020); "
            "a null pctile means too little history to rank, not zero; an axis "
            "with a null value is stale, not absent; reporting context, never a "
            "calibrated signal"
        ),
    }


_MACRO_HISTORY_DEFAULT = 120  # ~6 months of trading days
_MACRO_HISTORY_MAX = 1000


def _read_macro_history(name: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """One macro series or axis over time — the drill-down behind the snapshot.

    The snapshot told an agent that credit spreads sit at the 4.9th percentile of
    4,700 observations and then gave it no way to SEE those observations: 196,058
    rows on disk, reachable only as today's number. An agent that cannot look at the
    series behind a percentile has to take the percentile on faith, and cannot answer
    "what did this do in March 2020" about data we hold.
    """
    from signals.macro_substrate import DEPTH_NOTES, REPORT_AXES, history, known_names

    rows = history(name)
    if rows is None:
        return [{"error": f"unknown series or axis {name!r}", **known_names()}]
    depth_note = DEPTH_NOTES.get(REPORT_AXES.get(name, name))
    since = str(filters.get("since") or "").strip()
    until = str(filters.get("until") or "").strip()
    window = [
        (d, v) for d, v in rows if (not since or d >= since) and (not until or d <= until)
    ]
    try:
        limit = max(1, min(int(filters.get("limit") or _MACRO_HISTORY_DEFAULT),
                           _MACRO_HISTORY_MAX))
    except (TypeError, ValueError):
        limit = _MACRO_HISTORY_DEFAULT
    if not window:
        note = (f"no observations in that window; the series runs "
                f"{rows[0][0]}..{rows[-1][0]} ({len(rows)} observations)")
        return [{
            "series": name, "since": since or None, "until": until or None,
            "unavailable": True,
            "note": f"{note} — {depth_note}" if depth_note else note,
        }]
    # NEWEST first, deliberately: the result cap drops from the tail, so the oldest
    # end of the window is the right end to lose when one has to be lost.
    kept = window[-limit:][::-1]
    # No "rows returned" field here: the cap can drop rows AFTER this reader runs, and
    # a count written before that is simply wrong. `count` and the rows themselves say
    # how many arrived; this says how many existed to choose from.
    #
    # The extremes are STATED, not left to be inferred from the rows. Measured on a
    # live DeepSeek turn 2026-08-11: asked for 1,000 observations of HY OAS, it got 496
    # after the cap (back to 2024-09-18) and reported its visible high of 4.61 as "the
    # most stressed reading in the entire window" — the real one is 5.99 in July 2022,
    # off the end of what it could see. Every disclosure was present and correct and it
    # generalised anyway, so the fix is to remove the inference, not to disclose harder.
    lo = min(window, key=lambda r: r[1])
    hi = max(window, key=lambda r: r[1])
    context: dict[str, Any] = {
        "series": name,
        "observations_in_window": len(window),
        "rows_are": "the NEWEST slice of the window, newest first — the window's own "
                    "high/low are given here because they may pre-date the oldest row",
        "window_low": {"date": lo[0], "value": lo[1]},
        "window_high": {"date": hi[0], "value": hi[1]},
        "series_span": f"{rows[0][0]}..{rows[-1][0]} ({len(rows)} observations)",
    }
    if depth_note:
        context["depth"] = depth_note
    return [{**context, "date": d, "value": v} for d, v in kept]


# A second, shorter wording of gamma_exposure.CONVENTION rather than a reuse of it:
# that one points at field names ("call_gamma and put_gamma below") which do not
# exist in this block, and a caveat that names the wrong fields is worse than none.
# The claim must stay identical in both — the net is an assumption, not an
# observation — even though the sentence does not.
_GEX_CONVENTION_SHORT = (
    "net ASSUMES dealers are long every call and short every put — the industry "
    "convention, not measured inventory; call/put above are the unconditional facts"
)


def _read_market(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    series = str(filters.get("series") or "").strip()
    if series:
        return _read_macro_history(series, filters)
    from db.regime import recent_observations
    from signals.regime import market_regime, regime_is_fresh

    regime = market_regime(db_path=db_path)
    if regime is None:
        return [{"unavailable": True, "fresh": False}]

    fresh = regime_is_fresh(db_path=db_path)
    term_spread = float(regime["term_spread"])
    vix = float(regime["vix"])
    row: dict[str, Any] = {
        "as_of": regime.get("as_of"),
        "term_spread": round(term_spread, 4),
        "vix": round(vix, 2),
        "regime_label": _regime_label(term_spread, vix),
        "fresh": fresh,
    }
    vix_hist = recent_observations("VIXCLS", limit=252, db_path=db_path)
    if vix_hist:
        values = [val for _, val in vix_hist]
        at_or_below = sum(1 for val in values if val <= vix)
        row["vix_percentile"] = round(100 * at_or_below / len(values), 1)
        # Name the window. This field and `macro.axes.vol.pctile` rank the SAME
        # VIX and disagree by ~26 points (8.5 vs 35.0 today) purely because this
        # one looks back a year and that one to 1990. An unlabelled percentile
        # sitting next to a labelled one reads as a contradiction.
        row["vix_percentile_window"] = f"{len(values)}d trailing"
    # Fail-soft: term_spread + VIX are the contract; the substrate is enrichment
    # and its own store being absent must never sink the regime read.
    try:
        macro = _macro_block()
    except Exception:
        logger.debug("Macro substrate read failed", exc_info=True)
        macro = None
    if macro is not None:
        row["macro"] = macro
    # Index gamma regime, from the snapshot store — NOT recomputed here. Pricing the
    # board is a 2.3 s broker fetch and this resource is the fast always-on regime
    # read; the stored row carries its own timestamps so its staleness is visible,
    # and `gex` recomputes live for anyone who needs the current board.
    try:
        from db.gamma_exposure import history as gex_history

        stored = (gex_history(limit=1) or [None])[0]
    except Exception:
        logger.debug("Gamma exposure read failed", exc_info=True)
        stored = None
    if stored:
        row["index_gamma"] = {
            "net_usd_per_1pct_b": stored["net_b"],
            "call_usd_per_1pct_b": stored["call_b"],
            "put_usd_per_1pct_b": stored["put_b"],
            "flip_spot": stored["flip"],
            "spot_above_flip_pct": stored["spot_above_flip_pct"],
            # Null on rows stored before these two existed — an old snapshot has no
            # vanna, which is not the same as a board whose vanna was zero.
            "net_vanna_usd_per_volpt_b": stored["net_vanna_b"],
            "net_charm_usd_per_day_b": stored["net_charm_b"],
            "spot": stored["spot"],
            "priced_at": stored["as_of"],
            "oi_as_of": stored["oi_as_of"],
            "convention": _GEX_CONVENTION_SHORT,
            "live_and_deeper": "query the `gex` resource",
        }
    return [row]


_EDGAR_SOURCE = "SEC EDGAR XBRL companyfacts"
_EDGAR_NOTE = (
    "as-reported filing values, not vendor-adjusted; `filed` is when each "
    "number became public, so pass as_of=YYYY-MM-DD for a point-in-time read"
)


def _read_fundamentals(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # EDGAR statements live in their own research store
    from research.fundamentals import (
        company_concepts,
        concept_series,
        latest_financials,
        search_concepts,
    )

    # Concept discovery needs no symbol — "what is this called in XBRL" is a
    # question about the taxonomy, not about a company.
    search = str(filters.get("search") or "").strip()
    if search:
        hits = search_concepts(search)
        if not hits:
            return [{"search": search, "matches": [], "note": "no concept matched that text"}]
        return [{"search": search, "matches": hits, "source": _EDGAR_SOURCE}]

    symbol = str(filters.get("symbol") or "").strip().upper()
    if not symbol:
        return [{"error": "symbol is required (or pass `search` to look up concept names)"}]
    try:
        periods = max(1, min(int(filters.get("periods") or 4), 8))
    except (TypeError, ValueError):
        periods = 4
    as_of = str(filters.get("as_of") or "").strip() or None

    if filters.get("list_concepts"):
        reported = company_concepts(symbol)
        if reported is None:
            return [_unresolved_symbol(symbol)]
        return [{"symbol": symbol, "concepts": reported, "source": _EDGAR_SOURCE}]

    concept = str(filters.get("concept") or "").strip()
    if concept:
        series = concept_series(symbol, concept, as_of=as_of, periods=periods)
        if series is None:
            return [_unresolved_symbol(symbol)]
        if not (series["annual"] or series["quarterly"]):
            # Wrong tag name is the common case — hand back what this filer
            # does report that matches, rather than an empty result the agent
            # would read as "the company has no such number".
            candidates = [
                c for c in (company_concepts(symbol) or [])
                if concept.lower().replace(" ", "") in c["concept"].lower()
                or concept.lower() in (c["label"] or "").lower()
            ][:15]
            return [
                {
                    "symbol": symbol,
                    "concept": concept,
                    "matched": False,
                    "note": (
                        f"{symbol} files no facts under that exact tag. "
                        "Concept names vary by filer — re-ask with one of `candidates`, "
                        "or pass list_concepts=true for everything it reports."
                    ),
                    "candidates": candidates,
                }
            ]
        series["source"] = _EDGAR_SOURCE
        series["note"] = _EDGAR_NOTE
        return [series]

    out = latest_financials(symbol, as_of=as_of, periods=periods)
    if out is None:
        return [_unresolved_symbol(symbol)]
    out["source"] = _EDGAR_SOURCE
    out["note"] = _EDGAR_NOTE
    stale = {m: e["stale"] for m, e in out["metrics"].items() if e.get("stale")}
    if stale:
        # Loud, because the failure mode is silent: an abandoned concept still
        # has a newest row and reads as current unless something says otherwise.
        out["stale_series"] = stale
        out["note"] += (
            f". WARNING — {symbol} stopped filing these series and the newest value "
            f"in each is years old: {stale}. Do not quote them as current; pass "
            "list_concepts=true to find the concept it reports instead "
            "(banks, for example, file NoninterestIncome + InterestIncomeExpenseNet "
            "rather than Revenues)"
        )
    if not out["metrics"]:
        # An empty `metrics` is ambiguous and used to read as "this company has
        # no financials". Say which of the two it actually is.
        reported = company_concepts(symbol, limit=1) or []
        out["note"] = (
            f"{symbol} resolves to CIK {out['cik']} but no XBRL facts are ingested for it "
            "— the nightly companyfacts CRC diff should pick the filer up; "
            "do not per-symbol REST scrape SEC for this."
            if not reported
            else (
                f"{symbol} files XBRL, but none of the twelve canonical metrics matched"
                + (f" as of {as_of}" if as_of else "")
                + ". Concept coverage varies by filer — pass list_concepts=true to see "
                "what it actually reports."
            )
        )
    return [out]


def _read_segments(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # segment facts live in the EDGAR research store
    from research.fundamentals import segment_axes, segment_series

    symbol = str(filters.get("symbol") or "").strip().upper()
    if not symbol:
        return [{"error": "symbol is required"}]

    if filters.get("list_axes"):
        axes = segment_axes(symbol)
        if axes is None:
            return [_unresolved_symbol(symbol)]
        if not axes:
            return [_no_segments(symbol)]
        return [{"symbol": symbol, "axes": axes, "source": _DERA_SOURCE}]

    concept = str(filters.get("concept") or "revenue").strip()
    axis = str(filters.get("axis") or "").strip() or None
    as_of = str(filters.get("as_of") or "").strip() or None
    try:
        periods = max(1, min(int(filters.get("periods") or 6), 12))
    except (TypeError, ValueError):
        periods = 6

    out = segment_series(
        symbol, concept=concept, axis=axis, as_of=as_of, periods=periods
    )
    if out is None:
        return [_unresolved_symbol(symbol)]
    if not out["series"]:
        return [_no_segments(symbol, concept=concept, axis=axis)]
    # One row per series, not one giant row: the result cap trims by dropping
    # tail ROWS, and a single-row payload over the cap trimmed to rows:[] —
    # exactly how a GOOGL segments query (23.8k chars) read back "empty" to the
    # chat agent on 2026-08-15. Business segments sort first so a trim costs the
    # least-loaded axes, and the envelope repeats per row for _hoist_shared to
    # collapse.
    axis_rank = {"BusinessSegments": 0, "Geographical": 1, "ProductOrService": 2}

    def newest(entry: dict[str, Any]) -> int:
        dates = [p["period_end"] for p in (entry["annual"][:1] + entry["quarterly"][:1])]
        return int(max(dates).replace("-", "")) if dates else 0

    series = sorted(
        out["series"],
        key=lambda e: (
            axis_rank.get(e["axis"], 9),
            -newest(e),
            -(len(e["annual"]) + len(e["quarterly"])),
            e["member"],
        ),
    )
    envelope = {
        "symbol": out["symbol"],
        "cik": out["cik"],
        "concept": out["concept"],
        "as_of": out["as_of"],
        "axes": out["axes"],
        "source": out["source"],
        "note": (
            "as-reported segment values. Fresh 10-Q/10-K filings land here "
            "nightly (~1-2 days after filing) via per-filing XBRL; SEC DERA "
            "quarterly sets backfill history. Same-day numbers on the print "
            "evening itself are in `earnings_release` (as prose)."
        ),
    }
    return [{**envelope, **entry} for entry in series]


def _no_segments(symbol: str, *, concept: str | None = None, axis: str | None = None) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "unavailable": True,
        "note": (
            f"no segment facts for {symbol}"
            + (f" under concept {concept!r}" if concept else "")
            + (f" on axis {axis!r}" if axis else "")
            + " — single-segment companies report none at all, and the concept name "
            "varies by filer. Try list_axes=true to see what it does break out."
        ),
    }


_DERA_SOURCE = "SEC DERA Financial Statement Data Sets"


def _read_earnings_release(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # releases live in the research earnings store
    from signals.earnings_exhibit import latest_releases

    symbol = str(filters.get("symbol") or "").strip().upper() or None
    with_body = bool(filters.get("body"))
    try:
        limit = max(1, min(int(filters.get("limit") or 10), 25))
    except (TypeError, ValueError):
        limit = 10
    # The body is ~40k chars; a handful of them would blow the caller's context.
    if with_body:
        limit = min(limit, 3)

    rows = latest_releases(symbol, limit=limit, with_body=with_body)
    if not rows:
        return [
            {
                "symbol": symbol,
                "unavailable": True,
                "note": (
                    "no stored release"
                    + (f" for {symbol}" if symbol else "")
                    + " — the reader only holds filings furnished since it started "
                    "polling; it is not a historical archive"
                ),
            }
        ]
    for row in rows:
        row["source"] = "SEC EDGAR 8-K Item 2.02 exhibit (EX-99.1)"
    return rows


def _unresolved_symbol(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "unavailable": True,
        "note": (
            "no SEC filer maps to that ticker — it may be an ETF, a fund, or "
            "delisted (EDGAR's ticker map is current-registrant only)"
        ),
    }


def _read_index_pressure(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from db.index_pressure import baseline_days, day_summary, latest_date

    history = int(filters.get("history") or 0)
    if history > 0:
        from signals.options_flow.pressure_history import pressure_trend

        return [
            {
                **r,
                "caveat": "daily gauge tilt next to SPY's day result — "
                          "co-occurrence display, never an accuracy score",
            }
            for r in pressure_trend(min(history, 120), db_path=db_path)
        ]

    date = str(filters.get("date") or "").strip()
    explicit = bool(date)
    date = date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    window = str(filters.get("window") or "open").strip().lower()
    if window not in ("open", "close"):
        return [{"error": "window must be 'open' or 'close'"}]
    window_et = "09:30-10:00" if window == "open" else "15:30-16:00"
    rows = day_summary(date, window=window, db_path=db_path)
    if not rows and not explicit:
        # Carry the last session (weekend / pre-open) — same fallback the API
        # serves, labeled with the served date, never presented as today.
        prior = latest_date(window=window, db_path=db_path)
        if prior and prior != date:
            date, rows = prior, day_summary(prior, window=window, db_path=db_path)
    if not rows:
        return [{"date": date, "unavailable": True, "window": window, "window_et": window_et}]
    days = baseline_days(window=window, db_path=db_path)
    out = []
    for r in rows:
        r = dict(r)
        r["date"] = date
        r["window"] = window
        r["window_et"] = window_et
        r["baseline_days"] = days
        # Honesty stamp: raw premium sums, not a validated signal; the unclassified
        # remainder is real flow whose aggressor side could not be determined.
        caveat = (
            f"raw {window}-window premium; {r['classified_frac']:.0%} side-classified"
            "; index 0DTE = top-N contracts by $vol (ATM cluster), not the full chain"
        )
        if r.get("coverage"):
            caveat += f"; lane: {r['coverage']}"
        if days < 20:
            caveat += f"; uncalibrated ({days} baseline days)"
        r["caveat"] = caveat
        out.append(r)
    return out


_GEX_VIEWS = ("summary", "strikes", "expiries", "profile")
# Keys the drill-down context block occupies. A row field of the same name would
# silently win the merge below and overwrite it — which is exactly what the profile
# view's "spot" did before it was renamed. Named here so the test guards the real
# list rather than a copy of it.
_GEX_CONTEXT_KEYS = ("index", "spot", "oi_as_of", "convention", "units")


def _read_gex(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # live chain + the OI research store, not the trading DB
    from signals.gamma_exposure import (
        cached_board,
        expiry_rows,
        profile_rows,
        strike_rows,
        summarize,
    )

    view = str(filters.get("view") or "summary").strip().lower()
    if view not in _GEX_VIEWS:
        return [{"error": f"unknown view {view!r}", "views": list(_GEX_VIEWS)}]
    try:
        limit = max(1, min(int(filters.get("limit") or 20), _MAX_ROWS))
    except (TypeError, ValueError):
        limit = 20

    board = cached_board()
    if board is None:
        return [{
            "unavailable": True,
            "note": "no priced board — needs the OPRA OI store (data/oi_walls.db) "
                    "AND a live index chain; either being absent means no answer, "
                    "not a flat one",
        }]
    if view == "summary":
        from db.gamma_exposure import percentiles

        row = summarize(board)
        # Ranked against our OWN stored snapshots, which start the day the first one
        # was taken. The block reports its observation count either way so an absent
        # percentile reads as "not enough history yet", never as "unremarkable".
        row["history"] = percentiles({
            "net_b": row["net_gamma_usd_per_1pct_b"],
            "net_vanna_b": row["net_vanna_usd_per_volpt_b"],
            "net_charm_b": row["net_charm_usd_per_day_b"],
        })
        return [row]

    # Drill-down rows travel without the summary, so each one carries the context
    # needed to read it. Identical on every row, so _hoist_shared lifts it into
    # `shared` and it costs one copy — the lesson from the macro-history drill-down,
    # where a model with a partial view generalised past what it could see.
    from signals.gamma_exposure import CONVENTION

    context = dict(zip(
        _GEX_CONTEXT_KEYS,
        (
            "SPX",
            board.spot,
            board.oi_as_of,
            CONVENTION,
            "billions of dollars of dealer delta; _per_1pct_ = per 1% move in the "
            "index, _per_volpt_ = per 1 volatility point of implied vol, _per_day_ "
            "= per calendar day with nothing else moving. Do not compare the three "
            "to each other — same units, different denominators",
        ),
        strict=True,
    ))
    if view == "strikes":
        rows = strike_rows(board, limit=limit)
    elif view == "expiries":
        rows = expiry_rows(board, limit=limit)
    else:
        rows = profile_rows(board)
    return [{**context, **row} for row in rows]


def _read_oi_walls(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # own research store, not the trading DB
    from db.oi_walls import OI_WALLS_DB_PATH, standing_walls, store_status

    # `symbol` is the filter name every other resource uses, and the live model reached
    # for it here on its first real question — an ignored filter would have quietly
    # returned every source's walls under a question about one root.
    root = str(filters.get("root") or filters.get("symbol") or "").strip().upper() or None
    expiry = str(filters.get("expiry") or "").strip() or None
    try:
        limit = max(1, min(int(filters.get("limit") or 20), _MAX_ROWS))
    except (TypeError, ValueError):
        limit = 20

    if not OI_WALLS_DB_PATH.exists():
        return [{
            "unavailable": True,
            "note": "the condensed OI store has not been built on this machine "
                    "(data/oi_walls.db — see tools/oi_condense.py)",
        }]
    status = {s["source"]: s for s in store_status()}
    rows = standing_walls(root=root, expiry=expiry, limit=limit)
    if not rows:
        return [{
            "unavailable": True,
            "root": root,
            "expiry": expiry,
            "note": "no standing OI rows matched on the latest stored date",
            "store": list(status.values()),
        }]
    for r in rows:
        # Perception, never a level to trade: the pin/magnet study is pre-registered
        # and UNRUN, so any support/resistance reading here would be folklore.
        caveat = (
            "standing open interest only — no pin, magnet or support/resistance edge "
            "has been measured for these strikes; open interest is T+1 (published "
            "pre-open, describing the prior close's positions)"
        )
        st = status.get(r["source"], {})
        if st.get("stale"):
            caveat += (
                f"; STALE — the {r['source']} half of the store was last updated "
                f"{r['as_of']} ({st['as_of_age_days']}d ago), so these are the walls "
                "as of that date, not today"
            )
        if r.get("structure") == "collar_shaped_triple":
            caveat += (
                "; structure is SHAPE-matched from our own rows (three near-equal-OI "
                "legs reading put/put/call) over the rows returned — it is not an "
                "identification of any named fund or trade"
            )
        r["caveat"] = caveat
    return rows


def _read_flow_perception(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from db.flow_perception import rows_for_session

    session = str(filters.get("session") or "").strip() or None
    symbol = str(filters.get("symbol") or "").strip().upper() or None
    try:
        window = int(filters["window"]) if filters.get("window") else None
    except (TypeError, ValueError):
        return [{"error": "window must be 5, 10 or 20"}]
    try:
        limit = int(filters.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    rows = rows_for_session(
        session, window=window, symbol=symbol, limit=limit, db_path=db_path
    )
    if not rows:
        return [
            {
                "unavailable": True,
                "session": session or "latest",
                "symbol": symbol,
                # Absence is the common case and it is informative: only names that
                # cleared their peer cell's top decile get a row.
                "note": "no name-day cleared the peer top decile (or no scan has run)",
            }
        ]
    return rows


def _read_flow_history(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path  # lives in data/flow_corpus.db, not trading.db
    from db.flow_corpus import history_query

    return history_query(filters)


def _read_flow_report(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from db.reports import recent_reports_by_kind

    date = str(filters.get("date") or "").strip()
    reports = recent_reports_by_kind("flow_decomp", limit=10, db_path=db_path)
    if date:
        reports = [r for r in reports if (r.get("payload") or {}).get("et_date") == date]
    if not reports:
        return [{"unavailable": True, "date": date or "latest"}]
    r = reports[0]
    payload = dict(r.get("payload") or {})
    # The structured decomposition IS the payload; body is presentation HTML.
    payload["title"] = r.get("title")
    payload["report_id"] = r.get("id")
    payload["generated_at"] = r.get("generated_at")
    return [payload]


def _read_overnight_flow(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    del db_path  # file-backed: the morning job writes JSON, no DB involved
    from signals.options_flow.gth_morning import latest_line, load_history

    line = latest_line()
    if line is None:
        return [{
            "unavailable": True,
            "note": "no overnight line yet — first one lands ~9:16 ET on the "
                    "first trading morning after the tape starts filling",
        }]
    out = dict(line)
    s = dict(out.get("slice") or {})
    s["top_prints"] = (s.get("top_prints") or [])[:8]
    out["slice"] = s
    rows: list[dict[str, Any]] = [out]
    nights = int(filters.get("nights") or 0)
    if nights > 0:
        for r in load_history()[-min(nights, 30):]:
            rows.append({
                "session": r.get("session"),
                "notional": r.get("notional"),
                "prints": r.get("prints"),
                "put_share_notional": r.get("put_share_notional"),
                "zero_dte_share_notional": r.get("zero_dte_share_notional"),
                "macro_window_share": r.get("macro_window_share"),
            })
    return rows


async def _read_news(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del db_path
    from signals.news import fetch_watchlist_news

    limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    symbol = filters.get("symbol")
    sym = str(symbol).strip().upper() if symbol else None
    return await fetch_watchlist_news(limit=limit, symbol=sym)


def _read_calibration(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    del filters
    from ai.journal_review import compute_calibration

    return [compute_calibration(db_path=db_path)]


def _read_options_flow(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    from db.options_flow import recent_events

    limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    symbol = filters.get("symbol")
    event_type = filters.get("event_type")
    min_premium = filters.get("min_premium")
    since = filters.get("since")
    until = filters.get("until")
    order = str(filters.get("order") or "recent")
    universe = filters.get("universe")
    return recent_events(
        symbol=str(symbol).strip().upper() if symbol else None,
        event_type=str(event_type) if event_type else None,
        min_premium=float(min_premium) if min_premium is not None else None,
        since=str(since) if since else None,
        until=str(until) if until else None,
        order=order if order in ("premium", "unusual", "rarity", "conviction") else "recent",
        universe=str(universe) if universe in ("equity", "etf", "index") else None,
        limit=limit,
        db_path=db_path,
    )


def _summarize_options_flow(
    filters: dict[str, Any], *, db_path: Path
) -> dict[str, Any]:
    """Flow events grouped by underlying (facts only — no win%/EV).

    Aggregates in SQL over EVERY row in the filter range — the old version
    grouped a 50-row recency slice and presented it as the range's summary.
    """
    from datetime import UTC, datetime

    from db.options_flow import summarize_events

    today = datetime.now(UTC).date().isoformat()
    since = filters.get("since") or f"{today}T00:00:00Z"
    until = filters.get("until")
    symbol = filters.get("symbol")
    event_type = filters.get("event_type")
    min_premium = filters.get("min_premium")
    return summarize_events(
        symbol=str(symbol).strip().upper() if symbol else None,
        event_type=str(event_type) if event_type else None,
        min_premium=float(min_premium) if min_premium is not None else None,
        since=str(since),
        until=str(until) if until else None,
        db_path=db_path,
    )


def _read_equity_tape(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    from db.equity_tape import recent_events

    limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    symbol = filters.get("symbol")
    event_type = filters.get("event_type")
    min_notional = filters.get("min_notional")
    since = filters.get("since")
    return recent_events(
        symbol=str(symbol).strip().upper() if symbol else None,
        event_type=str(event_type) if event_type else None,
        min_notional=float(min_notional) if min_notional is not None else None,
        since=str(since) if since else None,
        limit=limit,
        db_path=db_path,
    )


async def _read_options_chain(
    filters: dict[str, Any], *, db_path: Path
) -> list[dict[str, Any]]:
    """Live OPRA chain for one underlying + expiry — same builder as the widget."""
    del db_path
    import asyncio as _asyncio

    from brokers.factory import get_broker
    from brokers.options_chain import build_options_chain_sync

    symbol = str(filters.get("symbol") or "").strip().upper()
    if not symbol:
        return [{"error": "symbol filter required (e.g. {\"symbol\": \"SPY\"})"}]

    broker = get_broker("alpaca")
    if broker is None:
        return [{"unavailable": True, "reason": "alpaca not configured"}]

    expiration = filters.get("expiration") or filters.get("expiry")
    expiry = str(expiration).strip()[:10] if expiration else None
    try:
        strikes = min(max(int(filters.get("strikes") or 10), 2), 30)
    except (TypeError, ValueError):
        strikes = 10

    chain = await _asyncio.to_thread(
        build_options_chain_sync, broker, symbol, expiry, strikes
    )
    return [chain]


_BARS_MAX = 120
# Calendar days spanned per bar, used to size the fetch window from the bar count asked
# for. Intraday factors are 1/(bars per RTH session); daily+ carry weekend/holiday slack.
_BARS_DAY_SPAN: dict[str, float] = {
    "5Min": 1 / 78,
    "15Min": 1 / 26,
    "1Hour": 1 / 7,
    "1Day": 1.5,
    "1Week": 7.5,
    "1Month": 32.0,
}
_BARS_TIMEFRAMES: dict[str, str] = {
    "5min": "5Min",
    "15min": "15Min",
    "1hour": "1Hour",
    "hourly": "1Hour",
    "1day": "1Day",
    "daily": "1Day",
    "1week": "1Week",
    "weekly": "1Week",
    "1month": "1Month",
    "monthly": "1Month",
}


async def _read_bars(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    """Bounded OHLC candles for SHAPE questions — basing, gaps, what-happened-after.

    Deliberately thin. Every NUMBER the agent quotes must come from a deterministic
    producer (get_context technicals + session_nodes), never from the model doing
    arithmetic over these rows: on 08-03 the copilot, denied a prior-week high,
    reverse-engineered "~$437" out of a rounded percentage and presented it as a
    level. Bars answer "what did this do", not "what is the number".
    """
    del db_path
    import math

    from brokers.factory import get_market_data_provider

    symbol = str(filters.get("symbol") or "").strip().upper()
    if not symbol:
        return [{"error": 'symbol filter required (e.g. {"symbol": "TSM"})'}]

    raw_tf = str(filters.get("timeframe") or "1Day").strip().lower()
    timeframe = _BARS_TIMEFRAMES.get(raw_tf)
    if timeframe is None:
        return [
            {
                "error": f"unsupported timeframe {raw_tf!r}",
                "supported": sorted(set(_BARS_TIMEFRAMES.values())),
            }
        ]

    try:
        limit = min(max(int(filters.get("limit") or 30), 1), _BARS_MAX)
    except (TypeError, ValueError):
        limit = 30

    provider = get_market_data_provider()
    span = _BARS_DAY_SPAN[timeframe]
    pad = 3 if span < 1 else 7
    days = max(2, math.ceil(limit * span) + pad)
    bars = await provider.aget_stock_bars(symbol, timeframe=timeframe, days=days)
    if not bars:
        return [{"symbol": symbol, "timeframe": timeframe, "count": 0, "bars": []}]

    window = bars[-limit:]
    return [
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(window),
            "period_high": round(max(b.high for b in window), 4),
            "period_low": round(min(b.low for b in window), 4),
            "first_open": round(window[0].open, 4),
            "last_close": round(window[-1].close, 4),
            "bars": [
                {
                    "t": b.time,
                    "o": round(b.open, 4),
                    "h": round(b.high, 4),
                    "l": round(b.low, 4),
                    "c": round(b.close, 4),
                    "v": b.volume,
                }
                for b in window
            ],
        }
    ]


def _read_dossier(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    """The symbol's deterministic claim matrix, built fresh from its OPEN episode.

    Ephemeral by design: builds via signals.dossier.build_dossier (store reads
    only — zero LLM, zero network) and stores nothing; ``put_dossier`` stays
    REASON-lane-only. Claims are returned COMPLETE, never row-capped — the claim
    matrix is the contract consumers cite claim_ids from, and a silently dropped
    claim would be both invisible and uncitable (the founder ruling recorded in
    signals/dossier.py's module docstring).
    """
    from db.fusion import get_open_episode
    from signals.dossier import build_dossier

    symbol = str(filters.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("symbol filter is required, e.g. {'symbol': 'NVDA'}")
    episode = get_open_episode(symbol, db_path=db_path)
    if episode is None:
        raise ValueError(
            f"no open fusion episode for {symbol} — a dossier exists only while "
            "an evidence stack is accumulating; query the 'episodes' resource "
            "to list the symbols that have one open"
        )
    return list(build_dossier(episode, db_path=db_path)["claims"])


def _read_episodes(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    """OPEN fusion episodes, ranked like the evidence-stack widget (score-first)."""
    from db.fusion import list_open_episodes

    symbol = str(filters.get("symbol") or "").strip().upper() or None
    try:
        limit = min(max(int(filters.get("limit") or 20), 1), _MAX_ROWS)
    except (TypeError, ValueError):
        limit = 20
    return list_open_episodes(symbol=symbol, limit=limit, db_path=db_path)


def _summarize_episodes(filters: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    """The row cap is never silent: say how many open episodes exist in all."""
    del filters
    from db.fusion import count_open_episodes

    return {"open_total": count_open_episodes(db_path=db_path)}


def _read_abnormality(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from db.abnormality import events_today, get_state, list_state
    from signals.abnormality.constants import CAVEAT, LABEL

    symbol = str(filters.get("symbol") or "").strip().upper()
    limit = min(max(int(filters.get("limit") or 30), 1), 100)
    if symbol:
        row = get_state(symbol, db_path=db_path)
        rows: list[dict[str, Any]] = []
        if row:
            rows.append({**row, "row_kind": "state", "label": LABEL, "caveat": CAVEAT})
        for ev in events_today(db_path=db_path):
            if ev["symbol"] == symbol:
                rows.append({**ev, "row_kind": "event"})
        return rows[:limit]
    rows = list_state(
        fired=bool(filters.get("fired")),
        paired=bool(filters.get("paired")),
        base_low=bool(filters.get("base_low")),
        base_high=bool(filters.get("base_high")),
        limit=limit,
        db_path=db_path,
    )
    return [{**r, "label": LABEL, "caveat": CAVEAT} for r in rows]


def _summarize_abnormality(filters: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    from db.abnormality import events_today, recent_outcomes
    from signals.abnormality.constants import CAVEAT, LABEL
    from signals.abnormality.outcomes import aggregate

    del filters
    return {
        "label": LABEL,
        "caveat": CAVEAT,
        # Name-days lit (first fires; same-day repeats excluded).
        "fires_today": len(events_today(db_path=db_path, first_only=True)),
        "outcomes": aggregate(recent_outcomes(500, db_path=db_path)),
    }


def _read_attention(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from db.attention import get_row, list_rows

    symbol = str(filters.get("symbol") or "").strip().upper()
    session = str(filters.get("session") or "").strip() or datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()
    if symbol:
        row = get_row(symbol, session, db_path=db_path)
        return [row] if row else []
    limit = min(max(int(filters.get("limit") or 50), 1), 200)
    min_angles = int(filters.get("min_angles") or 1)
    min_quintile = int(filters.get("min_quintile") if filters.get("min_quintile") is not None else 3)
    return list_rows(
        session,
        min_quintile=min(max(min_quintile, 0), 4),
        min_angles=min(max(min_angles, 0), 2),
        limit=limit,
        db_path=db_path,
    )


def _summarize_attention(filters: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from db.attention import list_rows
    from signals.attention import CAVEAT

    del filters
    session = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    rows = list_rows(session, min_quintile=0, min_angles=1, limit=500, db_path=db_path)
    both = [r["symbol"] for r in rows if r["angles"] >= 2]
    return {
        "caveat": CAVEAT,
        "session": session,
        "lit_names": len(rows),
        "both_angles": both[:20],
        "both_angles_n": len(both),
    }


def _read_social(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    """Reddit/StockTwits attention — perception only, never a forecast."""
    from db.social_mentions import (
        get_buzz,
        get_trending,
        recent_posts_for_symbol,
        user_overlap_neighbors,
    )

    symbol = str(filters.get("symbol") or "").strip().upper()
    overlap = filters.get("overlap")
    if isinstance(overlap, str):
        overlap = overlap.strip().lower() in {"1", "true", "yes"}
    else:
        overlap = bool(overlap)
    limit = min(max(int(filters.get("limit") or 20), 1), 20)

    if overlap and symbol:
        return user_overlap_neighbors(symbol, db_path=db_path)
    if symbol:
        buzz = get_buzz(symbol, db_path=db_path)
        posts = recent_posts_for_symbol(symbol, limit=limit, db_path=db_path)
        return [
            {"row_kind": "buzz", **buzz},
            *({"row_kind": "post", **p} for p in posts),
        ]
    return get_trending(db_path=db_path, limit=limit)


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
    "screener": {
        "description": (
            "Preset technical scans over the cached S&P/watchlist universe — "
            "the agent's candidate generator."
        ),
        "filters": {
            "preset": (
                "optional — one of: "
                + ", ".join(_PRESET_IDS)
                + " (default oversold; ignored when rvol=true)"
            ),
            "limit": "optional int — default 15, max 30",
            "rvol": "optional bool — run the RVOL scan instead of a preset",
            "min_rvol": "optional float — RVOL threshold when rvol=true (default 1.5)",
        },
        "read": _read_screener,
    },
    "market": {
        "description": (
            "Current market regime (yield-curve term spread + VIX) — condition "
            "every trade idea on it. The `macro` block carries eight axes. Read "
            "these two FIRST, because they say whether single-name work pays at "
            "all: `correlation` (mean pairwise correlation of S&P names over 21 "
            "sessions — HIGH means everything trades as one macro blob and stock "
            "selection is just leveraged beta; LOW means names move on their own "
            "news, which is when selection pays) and `breadth` (percent of names "
            "above their 200-day average — NARROW means the index is carried by a "
            "handful of names, so a mid-cap book is probably not in them). Then "
            "vol, "
            "vol_term (VIX minus VIX3M; POSITIVE = inverted = stress priced now, "
            "not someday), credit (HY OAS), funding (SOFR minus IORB; the GAP, "
            "not the level — the resting ample-reserves state is a small "
            "NEGATIVE gap, so a rising gap means funding firming, and because "
            "this window starts 2021 a `high` bucket means firm-vs-recent-years, "
            "never 2019-style stress), curve (10y-2y), dollar — and `series` "
            "carries every raw series (full Treasury curve, IG OAS, real yield, "
            "breakevens, cross-asset vol, VIX1D/9D/6M/VVIX, and Cboe IMPLIED "
            "correlation COR1M/COR3M — the market's forecast, an independent "
            "cross-check on the realized `correlation` axis) so any spread can be "
            "built. Every entry in both has a self-relative percentile + bucket "
            "ranked against history TRUNCATED at the reported date; check "
            "`history_since` before comparing two series, since their windows "
            "differ. Too little history to rank says `pctile: null` rather than "
            "inventing one, and a null `value` means stale, not absent. An "
            "`events` block lists the scheduled macro releases that reprice the "
            "whole tape — what prints TODAY and what is coming in the next 10 "
            "days (CPI, PPI, jobs, GDP, PCE, retail sales, claims, JOLTS, FOMC "
            "projections) — so sizing into a print is never mistaken for sizing "
            "on a quiet day; read `events.not_covered` before concluding no Fed "
            "meeting is scheduled. "
            "An `index_gamma` block carries the SPX option board's dealer-exposure "
            "regime — net/call/put gamma per 1% move, the gamma-flip level, and net "
            "vanna (per volatility point) and charm (per calendar day) — read from "
            "the LAST STORED post-close snapshot, so check `priced_at` before "
            "treating it as live, and note every net rests on a stated CONVENTION "
            "about who holds what, never on measured inventory. Query the `gex` "
            "resource for a live board and its strike/expiry/spot-ladder "
            "drill-downs. "
            "★ Pass `series` to DRILL DOWN into any of it over time — the snapshot "
            "ranks a number against thousands of observations, and `series` is how "
            "you actually look at them ('what did credit do in March 2020', 'is this "
            "VIX move big for this year'). Takes an axis name (credit, curve, vol, "
            "vol_term, funding, correlation, breadth, dollar) or a raw series id; an "
            "axis resolves to exactly the series the snapshot ranked, and a spread "
            "axis comes back date-joined rather than as two levels to subtract. An "
            "unknown name returns the full list of what resolves."
        ),
        "filters": {
            "series": "optional — an axis name or series id; returns that one over "
                      "time instead of the snapshot. Unknown names list what exists",
            "since": "optional YYYY-MM-DD lower bound (with `series`)",
            "until": "optional YYYY-MM-DD upper bound (with `series`)",
            "limit": "optional observations, newest first — default 120, max 1000",
        },
        "read": _read_market,
    },
    "fundamentals": {
        "description": (
            "Company financials from the full SEC EDGAR XBRL corpus — every "
            "concept every US filer has ever reported, not a curated handful. "
            "Symbol alone returns the headline series (revenue, gross profit, "
            "operating income, net income, diluted EPS, assets, liabilities, "
            "equity, cash, long-term debt, R&D, operating cash flow) as recent "
            "annual and quarterly buckets with sector/SIC. Beyond those, a "
            "filer typically reports ~250 more concepts (lease obligations, "
            "buybacks, deferred revenue, segment counts) — reach any of them "
            "with `concept`, find its name with `search`, or list what a filer "
            "reports with `list_concepts`. As-reported, free and keyless; every "
            "value carries its `filed` date so `as_of` is a true "
            "point-in-time read. Does NOT include segment breakdowns (the "
            "companyfacts feed strips dimensions, so 'AWS revenue' is absent)."
        ),
        "filters": {
            "symbol": "ticker — required for everything except `search`",
            "concept": "optional XBRL tag (e.g. OperatingLeaseLiabilityNoncurrent) — "
                       "returns that one series; unmatched names come back with candidates",
            "search": "optional text — find concept names by label/definition, no symbol needed",
            "list_concepts": "optional bool — every concept this filer actually reports",
            "periods": "optional int — periods per bucket, default 4, max 8",
            "as_of": "optional YYYY-MM-DD — only what had been FILED by then",
        },
        "read": _read_fundamentals,
    },
    "segments": {
        "description": (
            "Segment-level financials — revenue and operating income broken out "
            "by business segment, geography and product line, which the main "
            "`fundamentals` corpus cannot hold (companyfacts strips XBRL "
            "dimensions). Sourced from each filing's own XBRL nightly — fresh "
            "~1-2 days after a 10-Q/10-K lands — with SEC DERA quarterly sets "
            "as deep history. This is how you answer 'how fast is AWS growing' "
            "or 'which segment carries the margin': AMZN FY2025 reads AWS "
            "revenue $128.72B / operating income $45.61B vs North America "
            "$426.31B / $29.62B. `concept` takes a canonical metric (revenue, "
            "operating_income) or a raw XBRL tag. Same-day numbers on the print "
            "evening itself: `earnings_release` (as prose)."
        ),
        "filters": {
            "symbol": "REQUIRED — ticker",
            "concept": "optional — canonical metric or XBRL tag, default 'revenue'",
            "axis": "optional — e.g. BusinessSegments, Geographical, ProductOrService",
            "list_axes": "optional bool — what breakdowns this filer reports",
            "periods": "optional int — periods per bucket, default 6, max 12",
            "as_of": "optional YYYY-MM-DD — only what had been FILED by then",
        },
        "read": _read_segments,
    },
    "earnings_release": {
        "description": (
            "The actual earnings press release, straight off EDGAR — the EX-99.1 "
            "exhibit of an Item 2.02 8-K, stored within minutes of being furnished "
            "(measured ~5 min behind the tape). This is where the numbers the XBRL "
            "corpus cannot hold live: SEGMENT results ('AWS segment sales "
            "increased 37%'), guidance, and management's framing — companyfacts "
            "strips XBRL dimensions, so segment revenue is absent there by "
            "construction. `accepted_et` is a true Eastern timestamp with its "
            "offset, and `session` (bmo/amc/dmh) is derived from it. Full text "
            "with body=true; omit it for a list."
        ),
        "filters": {
            "symbol": "optional — filter to one ticker",
            "body": "optional bool — include the full release text (long)",
            "limit": "optional int — default 10, max 25",
        },
        "read": _read_earnings_release,
    },
    "news": {
        "description": (
            "Recent watchlist headlines (Alpaca/Benzinga news store, realtime "
            "websocket-fed, deduped by story id). Headlines only — no "
            "sentiment scores."
        ),
        "filters": {
            "symbol": "optional — filter to one ticker",
            "limit": "optional int — default 20, max 50",
        },
        "read": _read_news,
    },
    "calibration": {
        "description": (
            "How honest the agent's own predicted win% has been — per-bucket "
            "predicted vs realized (Wilson CIs, verdicts, n). Quote this when "
            "stating confidence."
        ),
        "filters": {},
        "read": _read_calibration,
    },
    "options_flow": {
        "description": (
            "Unusual options activity across the WHOLE market (sweeps/blocks/"
            "clusters cover every OPRA name; only event_type=unusual_volume is "
            "watchlist-capped) — facts about the tape, NOT a buy/sell signal; "
            "carries a caveat, never a win% or confidence. For 'did anything "
            "unusual happen?' use order=unusual — a HEURISTIC surfacing rank "
            "(rewards known aggressor side, short-dated, multi-exchange/many-"
            "print sweeps; log-tames raw premium; discounts index/ETF hedging "
            "noise) — unvalidated, for surfacing only. order=premium is raw "
            "size. Rows also carry mid_side (buy|sell|unclassified|"
            "wide_spread): the corpus midpoint rule beside DIR-1 direction — "
            "an annotation measured ~85% against stock-leg ground truth on "
            "checkable prints (o-sign Step 1', 2026-08-05), NOT a confirmed "
            "aggressor; direction stays the never-guessed at-quote read. "
            "Sweep/block rows may carry rarity_p (Laplace rank of the event's "
            "LARGEST SINGLE PRINT vs that name's own trailing-120 session-peak "
            "prints in the same right/DTE/moneyness cell — single-leg "
            "reference, prior-close moneyness; smaller = rarer; NULL = "
            "unranked) plus rarity_sentence and max_print_usd — a description "
            "of the tape, never a win% or a move forecast. DTE/OTM/side are "
            "facts on the row, not rank inputs (the old conviction shape "
            "kernel is retired). The summary block aggregates the FULL range, "
            "not just returned rows."
        ),
        "filters": {
            "symbol": "optional — filter by underlying ticker",
            "event_type": (
                "optional — sweep|block|flow_cluster|flow_burst|"
                "directional_pressure|unusual_volume (comma-list OK; "
                "unusual_volume is the one watchlist-capped type)"
            ),
            "min_premium": "optional float — minimum premium_usd",
            "since": "optional ISO timestamp — events at or after",
            "until": "optional ISO timestamp — events before",
            "order": (
                "optional — recent (default) | premium (largest first) | "
                "unusual (heuristic interestingness rank; rows carry "
                "unusual_score + underlying_class) | rarity (rarest "
                "largest-print first, unranked last, equity roots only by "
                "default; perception, not a forecast)"
            ),
            "universe": (
                "optional — equity|etf|index: keep only that root class "
                "(registered fence); universe=equity drops index/ETF plumbing"
            ),
            "limit": "optional int — default 20, max 50",
        },
        "read": _read_options_flow,
        "summarize": _summarize_options_flow,
    },
    "equity_tape": {
        "description": (
            "Large prints on the STOCK tape for watched symbols — lit-exchange "
            "blocks and off-exchange dark-pool prints (FINRA TRF). Facts about "
            "the tape with a caveat, never a buy/sell signal."
        ),
        "filters": {
            "symbol": "optional — filter by ticker",
            "event_type": "optional — equity_block|dark_pool",
            "min_notional": "optional float — minimum notional_usd",
            "since": "optional ISO timestamp — events at or after",
            "limit": "optional int — default 20, max 50",
        },
        "read": _read_equity_tape,
    },
    "options_chain": {
        "description": (
            "Live option chain for ONE underlying — expirations list plus "
            "bid/ask/IV/greeks/volume/OI per strike for the chosen expiry. "
            "Quotes only, facts not signals."
        ),
        "filters": {
            "symbol": "required — underlying ticker",
            "expiration": "optional YYYY-MM-DD — default nearest tradeable expiry",
            "strikes": "optional int — strikes around the money (default 10, max 30)",
        },
        "read": _read_options_chain,
    },
    "index_pressure": {
        "description": (
            "Session-bookend options-flow pressure for the index complex "
            "(SPY/QQQ/IWM/DIA/SPX) + saturated megacaps — premium-weighted net "
            "imbalance (bull = call buys + put sells, bear = put buys + call "
            "sells) with the unclassified remainder surfaced. Windows: open "
            "(09:30-10:00 ET) and close (15:30-16:00 ET). Raw perception with a "
            "caveat, never a signal or score."
        ),
        "filters": {
            "date": "optional YYYY-MM-DD — default today (ET), falling back "
                    "to the latest session with data when today has none",
            "window": "optional — open (default) | close",
            "history": "optional int — instead of one day's rows, return that "
                       "many trailing daily trend rows (open+close net tilt "
                       "joined with SPY's day %), max 120",
        },
        "read": _read_index_pressure,
    },
    "flow_report": {
        "description": (
            "Post-close flow decomposition report: the index-pressure gauge's "
            "premium broken into parent orders, lanes (index vs single names), "
            "tenor buckets, and concentration, plus rule-based characterizations "
            "(e.g. 'bear premium was N long-tenor blocks — consistent with "
            "institutional hedging, not directional flow'). Heuristic reads with "
            "caveats attached — perception, never a validated signal."
        ),
        "filters": {
            "date": "optional YYYY-MM-DD — default latest available",
        },
        "read": _read_flow_report,
    },
    "overnight_flow": {
        "description": (
            "Last night's overnight (Cboe GTH) index-option activity — "
            "SPX/SPXW/XSP/VIX prints from the 4:00-9:15 ET slice, z-scored "
            "against a trailing ~120-night baseline (robust median/MAD), plus "
            "put/0DTE/8:30-macro-window shares and top prints. ACTIVITY only, "
            "never direction: no overnight quotes exist on any lane we hold, "
            "so there is no buy/sell read. The z is context for the open, not "
            "a signal — measured census co-occurrence: 10 of 260 nights were "
            ">=5 sigma and 9 preceded a |SPY gap| >=0.9% or an outsized day; "
            "overnight futures moves drive hedging flow drives the gap, so "
            "this describes an unusual night, it does not predict the day. "
            "Single-source Alpaca websocket (the Databento spine has no GTH "
            "session); reconnect gaps undercount."
        ),
        "filters": {
            "nights": "optional int — append that many trailing baseline "
                      "nights (compact rows), max 30",
        },
        "read": _read_overnight_flow,
    },
    "flow_perception": {
        "description": (
            "Daily unusual-vs-peers flow scan: names whose trailing-window option "
            "flow or equity volume was in the top decile of their own liquidity peer "
            "group (session x dollar-volume quintile) on four components — "
            "short-dated share, single-day burst concentration, abnormal largest "
            "print, equity volume spike — with the earnings-adjacency regime and "
            "days to earnings. PERCEPTION ONLY: describes unusualness, never "
            "predicts. Forward screening on these components was pre-registered NULL "
            "twice (FLOW_ROADMAP 1.14c/1.14d), so there is no probability, win rate "
            "or signal here; each row carries its own disclaimer plus "
            "session_age_days, which says how stale the scanned session is. A symbol "
            "with no row was simply not unusual versus its peers. "
            "days_to_earnings may be PROJECTED from the name's own SEC filing "
            "cadence — earnings_estimated=1 means it is an estimate, never a "
            "confirmed date, and must be described that way; earnings_state says "
            "whether we have a date ('dated'), know the name files no earnings at "
            "all ('non_filer', e.g. an ETF), or have never looked ('unknown')."
        ),
        "filters": {
            "session": "optional YYYY-MM-DD — default the latest scanned session",
            "symbol": "optional ticker",
            "window": "optional trailing sessions — 5 | 10 | 20",
            "limit": "optional row cap — default 50",
        },
        "read": _read_flow_perception,
    },
    "flow_history": {
        "description": (
            "Whole-market OPRA directional-flow HISTORY for one name: 16GB rollup "
            "corpus (every listed underlying, not the watchlist), session × right × "
            "DTE × moneyness × side. Default answer is MARGINALS — weight on each "
            "axis separately, plus a daily series — because the crossed grain is "
            "unreadable. Pass premium_usd + right + dte_bucket + mny_bucket for a "
            "rarity RANK of one print against that name's own matching orders. "
            "Rarity is measurable; significance is not. Forward screens on this "
            "fingerprint were pre-registered NULL twice (FLOW_ROADMAP 1.14c/1.14d) "
            "— no direction word, no probability, no score. side is a "
            "midpoint/quote classification, not sentiment (packages ~53% of "
            "premium; a multi-leg flip reverses the sign). Vocab: right=C|P; "
            "dte_bucket=0-7|8-30|31-90|90+; mny_bucket=atm|itm|otm2_10|otm10p|"
            "no_spot_index|no_spot_missing; side/strict_side=buy|sell|"
            "unclassified|wide_spread; multileg=0|1. An invalid filter returns "
            "the valid set. symbol is REQUIRED — the only index is "
            "(underlying, date); a market-wide group-by is a full-table scan. "
            "For market-wide unusual-vs-peers, query flow_perception. "
            "Every row carries corpus_through + stale_days so a stopped corpus "
            "cannot read as 'nothing happened'."
        ),
        "filters": {
            "symbol": "REQUIRED — ticker (the only index is underlying, date)",
            "since": "optional ISO date — default trailing 60 sessions",
            "until": "optional ISO date — default latest corpus session",
            "group_by": "optional ONE of right|dte_bucket|mny_bucket|side|"
                        "strict_side|cond|multileg — never a list",
            "limit": "optional int — cap on drill/daily rows, default 400",
            "premium_usd": "optional — with right+dte_bucket+mny_bucket, rarity rank",
            "right": "optional C|P (required for rarity)",
            "dte_bucket": "optional 0-7|8-30|31-90|90+",
            "mny_bucket": "optional atm|itm|otm2_10|otm10p|…",
            "side": "optional buy|sell|unclassified|wide_spread",
            "strict_side": "optional, same vocab as side",
            "cond": "optional Alpaca condition code",
            "multileg": "optional 0|1",
        },
        "read": _read_flow_history,
    },
    "abnormality": {
        "description": (
            "Vol/volume abnormality perception — a MAGNITUDE TELL, not a verdict. "
            "Fires = the REGISTERED 1.14f/g rule: trailing-60-min Parkinson range "
            ">= p90 of the (boundary x $-vol quintile) peer cell, options-eligible "
            "pool only, half-hour boundaries 10:30-14:00 ET, first fire per "
            "name-day (repeats flagged repeat=1). Measured in-sample: fired "
            "names' next-2h max |cum log move| ran ~1.3x their own time-of-day "
            "norm vs ~0.94x for non-fired peers; direction was a coin flip. "
            "Own-norm units only, not a forecast of a large absolute move. "
            "vol_ratio/rvol_session are displayed facts, never gates. No win%, "
            "no probability, no direction implied. `base_state` pairing "
            "(accumulation/distribution) is a labelled founder thesis, not a "
            "measurement. Surfaces-only: these are not alerts."
        ),
        "filters": {
            "symbol": "optional ticker — that name's live state + today's fires",
            "fired": "optional bool — only names that fired today",
            "paired": "optional bool — only base-state-paired fires",
            "base_low": "optional bool — accumulation-candidate base",
            "base_high": "optional bool — distribution-candidate base",
            "limit": "optional row cap — default 30, max 100",
        },
        "read": _read_abnormality,
        "summarize": _summarize_abnormality,
    },
    "attention": {
        "description": (
            "The attention ledger — one row per (name, session) lit today by "
            "either registered angle, ATTENTION_JOIN_DESIGN §4.2. Angle 1 "
            "(range): first abnormality fire of the day (trailing-60-min range "
            ">= p90 of its time-of-day x $-vol peer cell, options-eligible pool, "
            "half-hour grid). Angle 2 (flow): rare-sized sweep/block prints "
            "(largest single print rarer than 95% of the name's own session "
            "peaks in its bucket), equity roots only, deduped by cell-day. "
            "angles (0-2) + within-angle percentile rank the list — NEVER a "
            "composite score, no win%, no direction. Facts per row: range/vol "
            "ratios, rvol_session, base_state annotation, rare-print count / "
            "min p / largest print / call vs put premium / short-dated share "
            "(<=30 DTE — the corpus's one replicated positive tenor). "
            "Written every 60s during RTH; empty before the first fire/print."
        ),
        "filters": {
            "symbol": "optional ticker — that name's ledger row for the session",
            "session": "optional ET date — default today",
            "min_angles": "optional 0-2 — default 1 (any angle lit)",
            "min_quintile": "optional 0-4 $-vol display floor — default 3 (top 40%); 0 = everything",
            "limit": "optional row cap — default 50, max 200",
        },
        "read": _read_attention,
        "summarize": _summarize_attention,
    },
    "social": {
        "description": (
            "Reddit + StockTwits attention — mention counts, recent post titles, "
            "optional author-overlap neighbors. Perception / attention only — "
            "not a forecast, not a win%. Titles + flags only; no post bodies."
        ),
        "filters": {
            "symbol": "optional ticker — buzz + recent posts",
            "overlap": "optional bool — with symbol, author-overlap neighbors",
            "limit": "optional — posts/trending cap, default 20",
        },
        "read": _read_social,
    },
    "gex": {
        "description": (
            "Index dealer exposure — the SPX board's standing open interest priced "
            "through our own Black-76 greeks, as REGIME axes: how option inventory "
            "makes hedging behave, not where the index is going. THREE axes off one "
            "board, all in billions of dollars of dealer delta and differing only in "
            "what must move: GAMMA per 1% index move (with the gamma-flip level and "
            "how far spot sits above it), VANNA per 1 volatility point, CHARM per "
            "calendar day with nothing moving. Same unit, different denominators — "
            "never compare them to each other. "
            "★ EVERY NET RESTS ON A CONVENTION, NOT A MEASUREMENT: netting calls "
            "against puts assumes dealers are long every call and short every put, "
            "and we have no dealer-inventory data. Quote the convention whenever you "
            "quote a net and never call it observed positioning — call_gamma and "
            "put_gamma are facts, the net is fact PLUS assumption. Alpaca publishes "
            "no greeks at all on index options (0 of 12,563 measured), so every greek "
            "here is OUR model output off the chain's own parity forward, each checked "
            "against a finite difference of the pricer; greeks_source says black76. "
            "Open interest is published overnight, so it is always the prior session's "
            "positioning (oi_as_of + age on every row). ⚠️ charm_from_expiring_b is "
            "the part of charm in contracts expiring within the day — those RESOLVE "
            "rather than drift (26% of the board when measured), so subtract it before "
            "reading the rest as smooth overnight flow. PERCEPTION ONLY: nothing here "
            "is tested against forward returns, so there is no edge, probability or "
            "win rate to quote. "
            "★ Pass `view` to DRILL DOWN: strikes (where exposure concentrates, ranked "
            "by gross gamma because hedging happens against the gross), expiries (the "
            "term structure — the DTE cut moves net gamma by 2x, and gamma is a "
            "front-week number while vanna sits further out), profile (net gamma "
            "across a ladder of hypothetical index levels — how fast the board crosses "
            "the flip, which one level cannot say)."
        ),
        "filters": {
            "view": "optional — summary (default) | strikes | expiries | profile",
            "limit": "optional int for strikes/expiries — default 20, max 50",
        },
        "read": _read_gex,
    },
    "oi_walls": {
        "description": (
            "Standing option open-interest walls on the index complex — the largest "
            "OI strikes on the latest stored date, per source, with how long each has "
            "stood, the previous session's value and the change. Sources: OPRA "
            "(SPX/SPXW, per-strike daily OI) and SME (CME S&P 500 Month-End options — "
            "the venue the large quarterly index collar migrated to in March 2026, "
            "which is why a collar the SPX chain no longer shows is visible here). "
            "Legs whose OI matches within ~5% across a put/put/call triple at one "
            "expiry are flagged structure=collar_shaped_triple — a shape measured off "
            "these rows over the rows RETURNED (a small limit can hide a triple), "
            "never a named fund. PERCEPTION ONLY: standing OI is a fact about "
            "positioning, not a level that pins, magnets or supports price — that "
            "study is pre-registered and unrun, so quote no edge, probability or "
            "win rate from it. Every row carries its as-of date and says when the "
            "source has gone stale."
        ),
        "filters": {
            "root": "optional — SPX | SPXW | SME (alias: symbol)",
            "expiry": "optional YYYY-MM or YYYY-MM-DD prefix",
            "limit": "optional int per source — default 20, max 50",
        },
        "read": _read_oi_walls,
    },
    "episodes": {
        "description": (
            "OPEN fusion episodes — the living evidence stacks, one per symbol, "
            "ranked score-first like the evidence-stack widget. The discovery "
            "surface for `dossier`: list what is open (or check one symbol), then "
            "query dossier for the full claim matrix. Rows are a top slice "
            "(default 20, max 50); summary.open_total says how many exist in all. "
            "opened_at is the stack's BIRTH — stacks live across sessions, so an "
            "old opened_at means long accumulation, not staleness "
            "(last_evidence_at is the freshness signal). PERCEPTION, NOT A "
            "VERDICT: score ranks attention, it is never a win rate."
        ),
        "filters": {
            "symbol": "optional — just that symbol's open episode (no rows = none open)",
            "limit": "optional int — rows returned, default 20, max 50",
        },
        "read": _read_episodes,
        "summarize": _summarize_episodes,
    },
    "dossier": {
        "description": (
            "Deterministic claim matrix for a symbol's OPEN fusion episode — every "
            "perception angle (flow, tape, structure, attention, catalyst, news, "
            "fundamentals, context, portfolio, history, evidence) rendered as dated, "
            "citable claims with freshness flags and contradiction links. Built on "
            "demand from the stores we already hold (zero LLM, zero network, nothing "
            "persisted). PERCEPTION, NOT A VERDICT: angles are stated so you can "
            "weigh them — nothing here is a scored signal, a win rate, or a "
            "recommendation. Requires an open fusion episode for the symbol "
            "(the `episodes` resource lists them)."
        ),
        "filters": {"symbol": "required — ticker with an open fusion episode"},
        "read": _read_dossier,
    },
    "bars": {
        "description": (
            "OHLC candles for ONE symbol — 5Min/15Min/1Hour/1Day/1Week/1Month, "
            "newest last, plus the returned window's high/low/first-open/last-close. "
            "Use these for SHAPE questions the numbers can't answer: has it been "
            "basing, did it gap and fill, what did it do after the last earnings. "
            "Do NOT compute indicators, moving averages or levels from these rows — "
            "get_context already returns those computed exactly (sma50/sma200/rsi14/"
            "atr14/trend under technicals, and POC/VAH/VAL/PDH/PDL/PWH/PWL/PMH/PML/"
            "52WH/52WL under session_nodes). A level derived by reading candles is a "
            "guess wearing the clothes of data; quote the computed one instead."
        ),
        "filters": {
            "symbol": "required — ticker",
            "timeframe": (
                "optional — 5Min | 15Min | 1Hour | 1Day (default) | 1Week | 1Month"
            ),
            "limit": "optional int — bars returned, default 30, max 120",
        },
        "read": _read_bars,
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


# Natural-language synonyms the LLM guesses for resource keys (weaker models like
# DeepSeek especially): the tool guidance says "market regime", "trades", "alerts", but
# the registered keys are "market", "my_trades", "price_alerts". Without these, a
# reasonable guess returns {"error": "unknown resource"} and the tool shows FAILED — the
# "market_regime fails on mobile (works on web)" bug: the stronger web model recovers off
# the returned `available` list, the weaker mobile model doesn't. Map guesses -> real key.
_RESOURCE_ALIASES = {
    "market_regime": "market",
    "regime": "market",
    "trades": "my_trades",
    "alerts": "price_alerts",
    "journal": "decision_journal",
    "accounts": "schwab_accounts",
    "flow": "options_flow",
    "chain": "options_chain",
    "option_chain": "options_chain",
    "greeks": "options_chain",
    "financials": "fundamentals",
    "earnings_financials": "fundamentals",
    "earnings_report": "earnings_release",
    "press_release": "earnings_release",
    "segment": "segments",
    "business_segments": "segments",
    "geographic_revenue": "segments",
    "tape": "equity_tape",
    "dark_pool": "equity_tape",
    "blocks": "equity_tape",
    "pressure": "index_pressure",
    "index_flow": "index_pressure",
    "opening_pressure": "index_pressure",
    "closing_pressure": "index_pressure",
    "gauge": "index_pressure",
    "flow_decomp": "flow_report",
    "flow_decomposition": "flow_report",
    "daily_flow_report": "flow_report",
    "claims": "dossier",
    "claim_matrix": "dossier",
    "open_episodes": "episodes",
    "fusion_episodes": "episodes",
    "evidence_stacks": "episodes",
    "stacks": "episodes",
    "gamma": "gex",
    "gamma_exposure": "gex",
    "dealer_gamma": "gex",
    "gamma_flip": "gex",
    "zero_gamma": "gex",
    # The resource is named for gamma but answers all three; a model asked about
    # vanna will type "vanna" long before it thinks to look under "gex".
    "vanna": "gex",
    "charm": "gex",
    "dealer_exposure": "gex",
    "collar": "oi_walls",
    "jpm_collar": "oi_walls",
    "open_interest": "oi_walls",
    "oi": "oi_walls",
    "walls": "oi_walls",
    "strike_walls": "oi_walls",
    "candles": "bars",
    "candlesticks": "bars",
    "ohlc": "bars",
    "ohlcv": "bars",
    "weekly_candle": "bars",
    "unusual_flow": "flow_perception",
    "unusual_options_flow": "flow_perception",
    "flow_scan": "flow_perception",
    "flow_corpus": "flow_history",
    "flow_hist": "flow_history",
    "historical_flow": "flow_history",
    "flow_baseline": "flow_history",
    "vol_scanner": "abnormality",
    "unusual_activity": "abnormality",
    "abnormality_scanner": "abnormality",
    "unusual": "attention",
    "unusual_today": "attention",
    "attention_list": "attention",
}


# Characters a repeated field must waste before it is worth hoisting out of the rows.
# Low enough to catch every real offender measured 2026-08-11 (the shortest was
# oi_walls' 12-char `as_of` at 468 wasted chars), high enough that short flags stay
# where a reader expects them.
_HOIST_MIN_SAVING = 300


def fit_to_cap(output: Any, *, cap: int, indent: int | None = None) -> Any:
    """Trim an over-cap agent result by dropping whole ROWS, never mid-object.

    Both agent surfaces cap what one tool call may pull into context, and both used
    to do it by slicing the rendered JSON at a character index — which hands the model
    an unparseable fragment and says nothing about what went missing. Measured
    2026-08-11 against the shared 20,000-char cap, three resources were over it and
    `flow_perception` was 2.9x. Readers order rows most-significant-first, so the tail
    is the right end to drop. A payload with no `rows` list has no rows to drop and
    Only what can be cut WITHOUT corrupting it is cut. A string (web search, a report)
    slices safely. A payload with `rows` drops rows. A structured payload with neither
    is returned WHOLE and logged, because slicing it is the very bug this replaces —
    `list_capabilities` is 20,364 chars of nested dicts, and a cap that cut it at
    20,000 would hand the model a broken manifest instead of a long one.

    `indent` must match how the caller will finally render it — the CLI prints
    indent=2, ~40% more characters than compact, so measuring compact would silently
    overrun its own cap.
    """
    def size(payload: Any) -> int:
        return len(json.dumps(payload, indent=indent, default=str))

    rendered = output if isinstance(output, str) else json.dumps(
        output, indent=indent, default=str
    )
    if len(rendered) <= cap:
        return output
    if isinstance(output, str):
        return rendered[:cap] + f"\n…(truncated at {cap:,} chars)"
    rows = output.get("rows") if isinstance(output, dict) else None
    if not isinstance(rows, list) or not rows:
        logger.info("over-cap payload has no rows to drop (%d chars)", len(rendered))
        return output
    # The note is measured too: sizing the rows alone lets the explanation itself push
    # the result back over the cap it just enforced.
    kept = list(rows)
    while kept:
        kept.pop()
        fitted = {
            **output,
            "rows": kept,
            "truncated": (
                f"{len(rows) - len(kept)} of {len(rows)} rows dropped to fit the "
                f"{cap:,}-char result cap — rows are ordered most-significant-first, "
                "so these are the top ones; narrow the filters (symbol, root, limit, "
                "expiry) to reach further down"
            ),
        }
        if size(fitted) <= cap:
            break
    return fitted


def _hoist_shared(rows: list[Any]) -> tuple[dict[str, Any], list[Any]]:
    """State once what every row says identically; return (shared, slimmed rows).

    Readers stamp per-source facts and honesty caveats onto every row — right at the
    row level, ruinous at the payload level. Measured 2026-08-11 against the agents'
    20,000-char result cap: `oi_walls` spent 44% of 22,388 chars on ONE caveat repeated
    40 times, `flow_perception` 38% of 57,781 on one disclaimer repeated 50 times, and
    `options_flow` 16% of 19,230. Nothing is dropped — the model reads the same
    envelope in the same breath — it is just not said 50 times.

    Only fields identical across EVERY row hoist, only from 3 rows up, and only when
    the repetition actually costs something. That last gate is what keeps the shape
    STABLE: `strategies` happens to have `enabled: false` on every row today, and
    hoisting a five-character field would have moved it in and out of `rows` depending
    on whether the user had enabled one — churn for a saving of ten characters.
    """
    if len(rows) < 3 or not all(isinstance(r, dict) for r in rows):
        return {}, rows
    shared = {
        k: v
        for k, v in rows[0].items()
        if all(k in r and r[k] == v for r in rows[1:])
        and len(json.dumps(v, default=str)) * (len(rows) - 1) >= _HOIST_MIN_SAVING
    }
    if not shared:
        return {}, rows
    return shared, [{k: v for k, v in r.items() if k not in shared} for r in rows]


async def query(
    resource: str,
    filters: dict | None = None,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Dispatch a read-only query to a registered resource (fail-soft)."""
    key = resource.strip().lower()
    key = _RESOURCE_ALIASES.get(key, key)
    if key not in RESOURCES:
        return {
            "error": "unknown resource",
            "available": sorted(RESOURCES.keys()),
        }

    entry = RESOURCES[key]
    reader = entry["read"]
    try:
        result = reader(filters or {}, db_path=db_path)
        if asyncio.iscoroutine(result):
            rows = await result
        else:
            rows = result
    except Exception as exc:
        logger.debug("Resource read failed for %s", key, exc_info=True)
        return {"resource": key, "error": str(exc), "rows": []}

    shared, rows = _hoist_shared(rows)
    result: dict[str, Any] = {"resource": key, "count": len(rows), "rows": rows}
    if shared:
        result["shared"] = shared
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
