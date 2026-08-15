"""Symbol context builder — normalized facts for the perception layer (read-only)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from brokers.alpaca import StockBar
from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from db.base import DEFAULT_DB_PATH
from db.chart_features import list_price_alerts
from db.earnings_calendar import last_earnings_date, next_earnings_date
from signals.indicators import Bar, atr, closes, distance_from_high, highs, lows, rsi, sma
from signals.price_levels import compute_extended_levels
from signals.session_node_strategy import compute_session_levels
from signals.volume_profile import load_profile, rvol_now
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

_SYMBOL_FIELD_GROUPS = (
    "quote",
    "rvol",
    "abnormality",
    "session_nodes",
    "technicals",
    "fundamentals",
    "earnings",
    "news",
    "strategy_signals",
    "decision_score",
    "active_alerts",
)


def list_capabilities() -> dict[str, Any]:
    """Self-describing manifest of what the agent can read."""
    from ai.perception.resources import list_resources

    resources = list_resources()
    return {
        "context_scopes": {"symbol": list(_SYMBOL_FIELD_GROUPS)},
        "resources": resources,
        "write_actions": {
            "propose_order": (
                "Propose a paper trade for phone approval (does not execute directly)"
            ),
            "create_rule": (
                "Create AND enable a signal/alert rule immediately — confirm "
                "parameters with the user before calling"
            ),
            "create_price_alert": (
                "Create a price alert (notification, not an order)"
            ),
            "update_price_alert": "Edit an existing price alert",
            "delete_price_alert": "Remove a price alert",
            "create_watchlist": (
                "Create a new watchlist (becomes the active list)"
            ),
            "add_watchlist_symbols": (
                "Add symbols to a watchlist (list = name or id; default active)"
            ),
            "remove_watchlist_symbols": "Remove symbols from a watchlist",
            "rename_watchlist": "Rename a watchlist",
        },
        "notes": (
            "Use get_context for a single symbol snapshot; use query(resource, filters) "
            "for: " + ", ".join(sorted(resources)) + ". Prefer get_context over "
            "web_search for prices, RVOL, abnormality, session-node levels, technicals, earnings, "
            "news, strategy signals, decision scores, and active alerts."
        ),
    }


def _market_session() -> str:
    """US equities session label — mirrors api/main._market_session."""
    now = datetime.now(_ET)
    wd = now.weekday()
    minutes = now.hour * 60 + now.minute
    if wd <= 4:
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "pre"
        if 9 * 60 + 30 <= minutes < 16 * 60:
            return "regular"
        if 16 * 60 <= minutes < 20 * 60:
            return "post"
    if (wd == 4 and minutes >= 20 * 60) or wd == 5 or (wd == 6 and minutes < 20 * 60):
        return "closed"
    return "overnight"


def _to_bar(stockbar: StockBar) -> Bar:
    return {
        "o": stockbar.open,
        "h": stockbar.high,
        "l": stockbar.low,
        "c": stockbar.close,
        "v": stockbar.volume,
    }


def _vs_ma_pct(close: float, ma: float | None) -> float | None:
    if ma is None or ma == 0:
        return None
    return round((close / ma - 1) * 100, 2)


def _infer_trend(close: float, sma50_val: float | None, sma200_val: float | None) -> str | None:
    if sma50_val is None or sma200_val is None:
        return None
    above50 = close >= sma50_val
    above200 = close >= sma200_val
    if above50 and above200:
        return "uptrend"
    if not above50 and not above200:
        return "downtrend"
    return "mixed"


def _compute_rvol_field(symbol: str, intraday_bars: list[StockBar]) -> dict | None:
    """RVOL snapshot — same logic as api/main._compute_rvol_field."""
    profile = load_profile(symbol)
    if profile is None or not intraday_bars:
        return None
    result = rvol_now(profile, intraday_bars)
    if result is None or result.value is None:
        if result is not None:
            return {"value": None, "state": result.state}
        return None
    state = result.state
    if state == "settled":
        latest_date = max(
            datetime.fromisoformat(b.time.replace("Z", "+00:00")).astimezone(_ET).date()
            for b in intraday_bars
        )
        if latest_date < datetime.now(_ET).date():
            state = "overnight"
    return {"value": round(result.value, 2), "state": state}


def _compute_technicals(daily_bars: list[StockBar]) -> dict[str, Any] | None:
    if not daily_bars:
        return None
    bars = [_to_bar(b) for b in daily_bars]
    c = closes(bars)
    h = highs(bars)
    low_vals = lows(bars)
    close = c[-1]
    sma50_val = sma(c, 50)
    sma200_val = sma(c, 200)
    rsi14 = rsi(c, 14)
    atr14 = atr(h, low_vals, c, 14)
    dist_20d = distance_from_high(c, 20)

    out: dict[str, Any] = {}
    if sma50_val is not None:
        out["sma50"] = round(sma50_val, 2)
    if sma200_val is not None:
        out["sma200"] = round(sma200_val, 2)
    vs_pct = _vs_ma_pct(close, sma200_val)
    if vs_pct is not None:
        out["vs_sma200_pct"] = vs_pct
    if rsi14 is not None:
        out["rsi14"] = round(rsi14, 1)
    if atr14 is not None:
        out["atr14"] = round(atr14, 2)
    if dist_20d is not None:
        out["dist_from_20d_high_pct"] = round(dist_20d, 2)
    trend = _infer_trend(close, sma50_val, sma200_val)
    if trend is not None:
        out["trend"] = trend
    return out or None


def _flatten_session_nodes(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flatten the producer's levels to {lowercased name: price}.

    Name-driven, NOT a fixed key list: PWH/PWL shipped to the producer, the chart and
    the fusion cache on 07-21 and were silently dropped here for two weeks because
    this function enumerated five keys — the copilot told the founder we had no
    previous-week high while $420.35 sat in the row it had just fetched. Any level
    the producer learns to emit now rides through on its own.
    """
    if not payload:
        return None
    levels = {item["name"]: item["price"] for item in payload.get("levels") or []}
    if not levels:
        return None
    flat: dict[str, Any] = {name.lower(): price for name, price in levels.items()}
    flat["session_date"] = payload.get("session_date")
    return flat


def _compact_decision_score(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact score_symbol's output under the same honesty contract as
    signals/push.py:_la_content_state — a win% NEVER travels without the
    qualifiers that say whether it means anything.

    This is the GENERIC 22-feature scorer, which research/artifacts/README.md
    records as edgeless (Brier ~= base rate). Stripping `sufficient` here was
    the 07-08 leak in another costume: the bare number reaches the LLM's
    context and a UI tile labelled "Win %", and both read it as validated.
    So when the bucket is insufficient or there is no win_prob, ship ONLY the
    verdict and no number; when it is trustworthy, its qualifiers ride along.
    """
    if raw is None:
        return None
    out: dict[str, Any] = {"top_factors": raw.get("top_factors")}
    win_prob = raw.get("win_prob")
    if not raw.get("sufficient") or win_prob is None:
        out["calibration"] = "unproven"
        return out
    out["calibration"] = "calibrated"
    out["win_prob"] = win_prob
    out["ev"] = raw.get("ev_pct")
    out["n_samples"] = raw.get("n_samples")
    out["base_rate"] = raw.get("base_rate")
    out["brier"] = raw.get("brier")
    out["low_confidence"] = raw.get("low_confidence")
    ci_lo = raw.get("ci_low")
    ci_hi = raw.get("ci_high")
    if ci_lo is not None or ci_hi is not None:
        out["ci"] = [ci_lo, ci_hi]
    return out


def _compact_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.get("id"),
            "price": row.get("price"),
            "direction": row.get("direction"),
            "repeat": bool(row.get("repeat")),
            "note": row.get("note") or "",
        }
        for row in rows
    ]


def _compact_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "headline": item.get("headline"),
            "source": item.get("source"),
            "datetime": item.get("datetime"),
            "url": item.get("url"),
        }
        for item in items[:5]
        if item.get("headline") and item.get("url")
    ]


def _entry_params_for_kind(kind: str, *, db_path: Path) -> dict[str, Any]:
    from db.strategies import list_strategies
    from signals.strategy_kinds import STRATEGY_KINDS, filter_entry_params, strategy_kind

    spec = STRATEGY_KINDS[kind]
    for strategy in list_strategies(db_path=db_path):
        if strategy_kind(strategy["id"]) == kind:
            return filter_entry_params(spec, strategy.get("params") or {})
    return {}


# SPY bars back every strategy_signals check; cache briefly so report loops
# (up to 8 get_context calls back-to-back) cost ONE SPY fetch, not eight —
# the Schwab WAF punishes bursts.
_SPY_BARS_TTL_S = 600.0
_spy_bars_cache: tuple[float, list[StockBar]] | None = None


async def _get_spy_bars(provider: Any) -> list[StockBar]:
    global _spy_bars_cache
    from signals.strategy_scan import SPY_SYMBOL

    now = time.monotonic()
    if _spy_bars_cache is not None and now - _spy_bars_cache[0] < _SPY_BARS_TTL_S:
        return _spy_bars_cache[1]
    bars_by_symbol = await provider.aget_stock_bars_batch([SPY_SYMBOL], days=420)
    bars = bars_by_symbol.get(SPY_SYMBOL, [])
    if bars:
        _spy_bars_cache = (now, bars)
    return bars


async def _build_strategy_signals(
    symbol: str,
    provider: Any,
    *,
    db_path: Path,
    sym_raw: list[StockBar],
) -> list[dict[str, Any]] | None:
    from signals.strategy_kinds import STRATEGY_KINDS, EntryContext
    from signals.strategy_scan import _stock_bars_to_dated
    from signals.trade_context import MIN_BARS_FOR_CONTEXT

    sym = symbol.strip().upper()
    spy_raw = await _get_spy_bars(provider)
    if len(sym_raw) < MIN_BARS_FOR_CONTEXT or len(spy_raw) < MIN_BARS_FOR_CONTEXT:
        return None

    sym_bars = _stock_bars_to_dated(sym_raw)
    spy_bars = _stock_bars_to_dated(spy_raw)
    sym_idx = len(sym_bars) - 1
    spy_idx = len(spy_bars) - 1
    if sym_bars[sym_idx]["d"] != spy_bars[spy_idx]["d"]:
        return None

    signals_out: list[dict[str, Any]] = []
    for kind, spec in STRATEGY_KINDS.items():
        try:
            params = _entry_params_for_kind(kind, db_path=db_path)
            ctx = EntryContext(
                sym=sym,
                sym_bars=sym_bars,
                idx=sym_idx,
                spy_bars=spy_bars,
                spy_idx=spy_idx,
                params=params,
                db_path=db_path,
            )
            would_fire = spec.check_entry(ctx)
            rationale = spec.build_rationale(ctx, kind)
            signals_out.append(
                {
                    "strategy": kind,
                    "would_fire": would_fire,
                    "rationale": rationale,
                }
            )
        except Exception:
            logger.debug(
                "Strategy signal check failed for %s kind=%s",
                sym,
                kind,
                exc_info=True,
            )
            signals_out.append(
                {
                    "strategy": kind,
                    "would_fire": False,
                    "rationale": "unavailable",
                }
            )
    return signals_out


async def _fetch_ext_quotes(provider: Any, symbol: str) -> dict[str, Any]:
    fetch = getattr(provider, "aget_quotes", None)
    if not callable(fetch):
        return {}
    try:
        quotes = await fetch([symbol])
        return quotes.get(symbol, {}) if isinstance(quotes, dict) else {}
    except Exception:
        logger.debug("Extended quote fetch failed for %s", symbol, exc_info=True)
        return {}


async def build_symbol_context(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Assemble normalized symbol facts from existing app sources (fail-soft per block)."""
    sym = symbol.strip().upper()
    ctx: dict[str, Any] = {"symbol": sym}
    today_iso = date.today().isoformat()
    session = _market_session()

    provider: Any | None = None
    try:
        provider = get_market_data_provider()
    except BrokerError:
        logger.debug("Market data provider unavailable for %s", sym, exc_info=True)
    except Exception:
        logger.debug("Market data provider lookup failed for %s", sym, exc_info=True)

    ext_quote: dict[str, Any] = {}
    if provider is not None:
        ext_quote = await _fetch_ext_quotes(provider, sym)

    if provider is not None:
        try:
            bars_by_symbol = await provider.aget_stock_bars_batch([sym], days=12)
            daily_bars = bars_by_symbol.get(sym, [])
            if daily_bars:
                closes_list = [b.close for b in daily_bars]
                price = closes_list[-1]
                prev = closes_list[-2] if len(closes_list) >= 2 else price
                change_pct = ((price - prev) / prev * 100) if prev else 0.0
                ext_price = (
                    ext_quote.get("ext_last")
                    if session in ("pre", "post", "overnight")
                    else None
                )
                ext_change_pct = (
                    ext_quote.get("ext_change_pct")
                    if session in ("pre", "post", "overnight")
                    else None
                )
                ctx["quote"] = {
                    "price": round(price, 2),
                    "change_pct": round(change_pct, 2),
                    "market_session": session,
                    "ext_price": round(ext_price, 2) if ext_price is not None else None,
                    "ext_change_pct": round(ext_change_pct, 2)
                    if ext_change_pct is not None
                    else None,
                }
        except Exception:
            logger.debug("Quote block failed for %s", sym, exc_info=True)

    if provider is not None:
        try:
            intraday_by_symbol = await provider.aget_stock_bars_batch(
                [sym], timeframe="5Min", days=4
            )
            rvol = _compute_rvol_field(sym, intraday_by_symbol.get(sym, []))
            if rvol is not None:
                ctx["rvol"] = rvol
        except Exception:
            logger.debug("RVOL block failed for %s", sym, exc_info=True)

    try:
        from db.abnormality import get_state

        abn = get_state(sym, db_path=db_path)
        if abn:
            ctx["abnormality"] = {
                "range_ratio": abn.get("range_ratio"),
                "vol_ratio": abn.get("vol_ratio"),
                "rvol_session": abn.get("rvol_session"),
                "range_pctile": abn.get("range_pctile"),
                "vol_pctile": abn.get("vol_pctile"),
                "composite": abn.get("composite"),
                "base_state": abn.get("base_state"),
                "status": abn.get("status"),
                "bucket": abn.get("bucket"),
                "session": abn.get("session"),
                "label": abn.get("label"),
                "caveat": abn.get("caveat"),
            }
    except Exception:
        logger.debug("Abnormality block failed for %s", sym, exc_info=True)

    try:
        session_payload = await compute_session_levels(sym, db_path=db_path)
        nodes = _flatten_session_nodes(session_payload)
        if nodes is not None:
            ctx["session_nodes"] = nodes
    except Exception:
        logger.debug("Session nodes block failed for %s", sym, exc_info=True)

    # Fetched once, shared by technicals + strategy_signals (Schwab WAF: no re-fetch).
    daily_bars_long: list[StockBar] = []
    if provider is not None:
        try:
            # ~420 calendar days ≈ 289 trading days — enough for a 200-day SMA
            # (250 calendar days is only ~172 trading days → sma200 came back null)
            # and for the 252-bar strategy-signal minimum.
            daily_bars_long = await provider.aget_stock_bars(
                sym, timeframe="1Day", days=420
            )
            technicals = _compute_technicals(daily_bars_long)
            if technicals is not None:
                ctx["technicals"] = technicals
        except Exception:
            logger.debug("Technicals block failed for %s", sym, exc_info=True)

        # Month/year levels off the bars already in hand (no extra fetch). Merged into
        # session_nodes so the agent has ONE place to look for "what levels matter" —
        # the key name predates the ladder and is now a slight misnomer.
        # Equities only, same fence compute_session_levels puts on PWH/PWL: a futures
        # root's daily history spans contract ROLLS, so a "52-week high" off it is an
        # artifact of the roll, not a level anyone traded.
        try:
            extended = [] if sym.startswith("/") else compute_extended_levels(daily_bars_long)
            if extended:
                nodes_block = ctx.setdefault("session_nodes", {})
                for level in extended:
                    nodes_block[level["name"].lower()] = level["price"]
        except Exception:
            logger.debug("Extended levels block failed for %s", sym, exc_info=True)

    if ext_quote:
        try:
            shares_outstanding = ext_quote.get("shares_outstanding")
            market_cap: float | None = None
            price_for_cap = ctx.get("quote", {}).get("price")
            if shares_outstanding is not None and price_for_cap is not None:
                try:
                    market_cap = round(float(shares_outstanding) * float(price_for_cap), 2)
                except (TypeError, ValueError):
                    market_cap = None
            fundamentals: dict[str, Any] = {}
            for key, src in (
                ("pe", "pe_ratio"),
                ("eps", "eps"),
                ("div_yield", "div_yield"),
            ):
                val = ext_quote.get(src)
                if val is not None:
                    fundamentals[key] = round(float(val), 4) if key != "eps" else val
            if market_cap is not None:
                fundamentals["market_cap"] = market_cap
            if fundamentals:
                ctx["fundamentals"] = fundamentals
        except Exception:
            logger.debug("Fundamentals block failed for %s", sym, exc_info=True)

    try:
        from research.fundamentals import statement_headline

        headline = statement_headline(sym)
        if headline:
            block = ctx.setdefault("fundamentals", {})
            block["statements"] = headline
    except Exception:
        logger.debug("EDGAR statements block failed for %s", sym, exc_info=True)

    try:
        earnings: dict[str, Any] = {}
        last = last_earnings_date(sym, today_iso, db_path=db_path)
        nxt = next_earnings_date(sym, today_iso, db_path=db_path)
        if last is not None:
            earnings["last"] = last
        if nxt is not None:
            earnings["next"] = nxt
        if earnings:
            ctx["earnings"] = earnings
    except Exception:
        logger.debug("Earnings block failed for %s", sym, exc_info=True)

    try:
        from signals.decision_scorer import score_symbol

        # score_symbol is sync and heavy — ~800 days of bars for the symbol AND
        # SPY (network fetch + SQLite cache write). Inline it parked the event
        # loop for 2-7s at market open (loop_probe stack, 2026-07-27).
        ctx["decision_score"] = _compact_decision_score(
            await asyncio.to_thread(score_symbol, sym, db_path=db_path)
        )
    except Exception:
        logger.debug("Decision score block failed for %s", sym, exc_info=True)
        ctx["decision_score"] = None

    try:
        alerts = _compact_alerts(list_price_alerts(symbol=sym, active_only=True))
        ctx["active_alerts"] = alerts
    except Exception:
        logger.debug("Active alerts block failed for %s", sym, exc_info=True)
        ctx["active_alerts"] = []

    try:
        from signals.news import afetch_company_news

        news_items = await afetch_company_news(sym, days=7, limit=5)
        compact = _compact_news_items(news_items)
        ctx["news"] = compact if compact else []
    except Exception:
        logger.debug("News block failed for %s", sym, exc_info=True)
        ctx["news"] = {"unavailable": True}

    if provider is not None and daily_bars_long:
        try:
            strategy_signals = await _build_strategy_signals(
                sym, provider, db_path=db_path, sym_raw=daily_bars_long
            )
            if strategy_signals is not None:
                ctx["strategy_signals"] = strategy_signals
        except Exception:
            logger.debug("Strategy signals block failed for %s", sym, exc_info=True)

    if "decision_score" not in ctx:
        ctx["decision_score"] = None
    if "active_alerts" not in ctx:
        ctx["active_alerts"] = []

    return ctx
