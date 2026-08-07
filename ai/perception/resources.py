"""Generic resource query — registry + dispatcher (read-only)."""

from __future__ import annotations

import asyncio
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

        from api.main import api_scanner_rvol

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
    from datetime import date as _date

    from signals.macro_substrate import macro_snapshot, regime_report

    today = _date.today().isoformat()
    report = regime_report([today])
    axes: dict[str, Any] = {}
    for axis, block in report.get("axes", {}).items():
        observed = block.get("per_date", {}).get(today) or {}
        if observed.get("value") is None:
            continue
        axes[axis] = {
            "series": block.get("series"),
            "value": observed["value"],
            "as_of": observed.get("date_used"),
            "pctile": observed.get("pctile"),
            "bucket": observed.get("bucket"),
            "history_n": (block.get("history") or {}).get("n"),
            "history_since": (block.get("history") or {}).get("first"),
        }
    if not axes:
        return None
    return {
        "axes": axes,
        # The raw latest observation per stored series — the whole curve, so a
        # reader can build any spread rather than only the ones we picked.
        "series": macro_snapshot(),
        "note": (
            "percentiles are self-relative against this store's own history "
            "(see history_since — credit is vendor-truncated to a rolling "
            "window); reporting context, not a calibrated signal"
        ),
    }


def _read_market(filters: dict[str, Any], *, db_path: Path) -> list[dict[str, Any]]:
    del filters
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
    # Fail-soft: term_spread + VIX are the contract; the substrate is enrichment
    # and its own store being absent must never sink the regime read.
    try:
        macro = _macro_block()
    except Exception:
        logger.debug("Macro substrate read failed", exc_info=True)
        macro = None
    if macro is not None:
        row["macro"] = macro
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
            "— run `python -m research.edgar_bulk --download --ingest`, or "
            f"`python -m research.fundamentals --build-financials {symbol}` for just this one."
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
    out["note"] = (
        "as-reported segment values. ⚠️ DERA publishes ~a quarter in arrears, so "
        "the most recent quarter's segments are NOT here yet — for those, read the "
        "`earnings_release` exhibit text."
    )
    return [out]


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

    from db.index_pressure import baseline_days, day_summary

    date = str(filters.get("date") or "").strip() or datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()
    window = str(filters.get("window") or "open").strip().lower()
    if window not in ("open", "close"):
        return [{"error": "window must be 'open' or 'close'"}]
    window_et = "09:30-10:00" if window == "open" else "15:30-16:00"
    rows = day_summary(date, window=window, db_path=db_path)
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
        order=order if order in ("premium", "unusual") else "recent",
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
            "every trade idea on it. Includes a `macro` block: HY and IG credit "
            "spreads (OAS), the full Treasury curve, the 10y real yield, "
            "breakevens and the broad dollar, each with a self-relative "
            "percentile and low/mid/high bucket."
        ),
        "filters": {},
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
            "dimensions). Source is the SEC's DERA quarterly data sets. This is "
            "how you answer 'how fast is AWS growing' or 'which segment carries "
            "the margin': AMZN FY2025 reads AWS revenue $128.72B / operating "
            "income $45.61B vs North America $426.31B / $29.62B. `concept` takes "
            "a canonical metric (revenue, operating_income) or a raw XBRL tag. "
            "⚠️ DERA publishes ~a quarter in arrears — the newest quarter's "
            "segments are in `earnings_release` (as prose), not here."
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
            "aggressor; direction stays the never-guessed at-quote read. The "
            "summary block aggregates the FULL range, not just returned rows."
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
                "unusual_score + underlying_class)"
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
            "date": "optional YYYY-MM-DD — default today (ET)",
            "window": "optional — open (default) | close",
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
}


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
