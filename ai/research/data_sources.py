"""First-party research data sources — sync, thread-safe, fail-soft."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from functools import wraps
from typing import Any, Callable, TypeVar

from ai.journal_review import compute_calibration
from ai.research.sources import _next_source_id, normalize_research_symbol
from brokers.errors import BrokerError
from brokers.factory import get_broker
from config.settings import broker_is_configured
from db.earnings_calendar import earnings_hour, last_earnings_date, next_earnings_date
from research.levels import level_features
from signals.finnhub import (
    afetch_company_metrics,
    afetch_company_profile,
    afetch_eps_surprises,
    finnhub_is_configured,
)
from signals.indicators import (
    atr,
    distance_from_high,
    highs,
    lows,
    rsi,
    sma,
    volume_vs_avg,
    volumes,
)
from signals.news import fetch_watchlist_news
from signals.regime import market_regime, regime_is_fresh
from signals.volume_profile import load_profile, rvol_now

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])


def _run_async(coro: Any) -> Any | None:
    """Run async coroutine from sync worker threads (no running loop)."""
    try:
        return asyncio.run(coro)
    except Exception as exc:
        logger.warning("Async data fetch failed: %s", exc)
        return None


def _fail_soft(fetcher: _F) -> _F:
    @wraps(fetcher)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fetcher(*args, **kwargs)
        except Exception as exc:
            logger.warning("%s failed: %s", fetcher.__name__, exc)
            return None

    return wrapper  # type: ignore[return-value]


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


def _fetch_bars(symbol: str, *, days: int = 365) -> list[Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym or not broker_is_configured("alpaca"):
        return None
    try:
        broker = get_broker("alpaca")
        bars = broker.get_stock_bars(sym, days=days)
    except (BrokerError, AttributeError, TypeError) as exc:
        logger.warning("Bars fetch failed for %s: %s", sym, exc)
        return None
    return bars or None


def _bar_date(bar: Any) -> date | None:
    raw = str(getattr(bar, "time", "") or "").strip()
    if not raw:
        return None
    if "T" in raw:
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.date()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def fetch_daily_bars_since(symbol: str, since: date) -> list[Any] | None:
    """Daily Alpaca bars on/after ``since`` (shared by research + idea tracker).

    Tri-state result — callers must distinguish the empty list from None:
    ``None`` = the FETCH failed; ``[]`` = fetch OK but no trading session on/after
    ``since`` yet (weekend/holiday). Collapsing both to None made the idea
    tracker count every closed-market sweep as a fetch failure and data_gap
    weekend-registered ideas before their first session.
    """
    sym = normalize_research_symbol(symbol)
    if not sym:
        return None
    lookback_days = max(30, (date.today() - since).days + 14)
    bars = _fetch_bars(sym, days=lookback_days)
    if not bars:
        return None
    return [b for b in bars if (_bar_date(b) or date.min) >= since]


def fetch_minute_bars(
    symbol: str, start: datetime, end: datetime
) -> list[Any] | None:
    """Minute (1Min) Alpaca bars in [start, end] — the Grading Law's minute lane.

    Same tri-state contract as ``fetch_daily_bars_since``: ``None`` = the FETCH
    failed (the caller falls back to daily); ``[]`` = fetch OK but no minute bars
    in the window yet (a fresh intraday idea, or after-hours) — NOT a failure.
    Alpaca-only. NOTE: Alpaca minute bars INCLUDE pre/post-market prints (the
    bars endpoint has no session filter) — the GRADER filters to RTH; this
    function stays a dumb transport.

    Deliberately DUMB — no caching layer: the reason lane is <=5 ideas/day over a
    tiny open-idea symbol set, so a per-row fetch is fine. Do not add a cache
    here speculatively.
    """
    sym = normalize_research_symbol(symbol)
    if not sym or not broker_is_configured("alpaca"):
        return None
    try:
        broker = get_broker("alpaca")
        bars = broker.get_stock_bars_range(
            sym, timeframe="1Min", start=start, end=end
        )
    except (BrokerError, AttributeError, TypeError) as exc:
        logger.warning("Minute bars fetch failed for %s: %s", sym, exc)
        return None
    return list(bars) if bars is not None else []


def fetch_daily_bars_batch(
    symbols: list[str], since: date
) -> dict[str, list[Any] | None]:
    """Batched ``fetch_daily_bars_since`` for register-everything populations.

    One Alpaca batch request per page instead of a call per symbol (spec §2.4
    named work item: the fusion_mech sweep would otherwise be thousands of
    calls). Same tri-state PER SYMBOL as the single fetch: ``None`` = no bars
    (fetch failed or symbol absent from the response), list = bars on/after
    ``since`` (possibly empty when no session yet).
    """
    cleaned = sorted({normalize_research_symbol(s) for s in symbols} - {""})
    if not cleaned:
        return {}
    if not broker_is_configured("alpaca"):
        return {s: None for s in cleaned}
    lookback_days = max(30, (date.today() - since).days + 14)
    try:
        broker = get_broker("alpaca")
        by_symbol = broker.get_stock_bars_batch(
            cleaned, timeframe="1Day", days=lookback_days
        )
    except (BrokerError, AttributeError, TypeError) as exc:
        logger.warning("Batch bars fetch failed for %d symbols: %s", len(cleaned), exc)
        return {s: None for s in cleaned}
    out: dict[str, list[Any] | None] = {}
    for sym in cleaned:
        bars = by_symbol.get(sym)
        if not bars:
            out[sym] = None
        else:
            out[sym] = [b for b in bars if (_bar_date(b) or date.min) >= since]
    return out


def _bars_to_indicator_bars(bars: list[Any]) -> list[dict[str, float]]:
    return [
        {"o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
        for b in bars
    ]


@_fail_soft
def fetch_technicals_source(symbol: str) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym:
        return None
    bars = _fetch_bars(sym, days=365)
    if not bars:
        return None

    ibars = _bars_to_indicator_bars(bars)
    close_series = [b["c"] for b in ibars]
    high_series = highs(ibars)
    low_series = lows(ibars)
    vol_series = volumes(ibars)
    idx = len(ibars) - 1

    levels = level_features(ibars, idx, include_prices=True)
    profile = load_profile(sym)
    rvol = rvol_now(profile, [])

    content: dict[str, Any] = {
        "symbol": sym,
        "latest_close": close_series[-1],
        "latest_date": bars[-1].time,
        "sma50": sma(close_series, 50),
        "sma200": sma(close_series, 200),
        "rsi14": rsi(close_series, 14),
        "atr14": atr(high_series, low_series, close_series, 14),
        "distance_from_high_252": distance_from_high(close_series, 252),
        "volume_vs_avg_20": volume_vs_avg(vol_series, 20),
        "levels": levels,
    }
    if rvol is not None:
        content["rvol"] = {"value": rvol.value, "state": rvol.state}

    sma50 = content["sma50"]
    rsi14 = content["rsi14"]
    snippet_parts = [f"Close ${close_series[-1]:.2f}"]
    if sma50 is not None:
        snippet_parts.append(f"SMA50 ${sma50:.2f}")
    if rsi14 is not None:
        snippet_parts.append(f"RSI14 {rsi14:.1f}")

    return {
        "id": _next_source_id("technicals"),
        "type": "technicals_internal",
        "title": f"{sym} technicals (internal)",
        "url": None,
        "snippet": ", ".join(snippet_parts),
        "content": content,
    }


@_fail_soft
def fetch_earnings_source(symbol: str) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym:
        return None

    today = date.today().isoformat()
    nxt = next_earnings_date(sym, today)
    last = last_earnings_date(sym, today)
    hour = earnings_hour(sym, nxt) if nxt else None

    eps_surprises: list[dict[str, Any]] = []
    if finnhub_is_configured():
        raw = _run_async(afetch_eps_surprises(sym, limit=8))
        if isinstance(raw, list):
            eps_surprises = raw

    if not any((nxt, last, hour, eps_surprises)):
        return None

    snippet = f"Next earnings {nxt or 'unknown'}"
    if last:
        snippet += f", last {last}"

    return {
        "id": _next_source_id("earnings"),
        "type": "earnings_internal",
        "title": f"{sym} earnings calendar (internal)",
        "url": None,
        "snippet": snippet,
        "content": {
            "next": nxt,
            "last": last,
            "hour": hour,
            "eps_surprises": eps_surprises,
        },
    }


@_fail_soft
def fetch_fundamentals_source(symbol: str) -> dict[str, Any] | None:
    """Company fundamentals: our own EDGAR statements first, vendor ratios second.

    The vendor half used to gate the WHOLE source — no Finnhub key meant the
    research engine saw no fundamentals at all, while 124M as-reported facts sat
    on disk unread. The filings are the primary record and are free and keyless,
    so they stand alone; Finnhub's ratios (P/E, margins) are a bonus that is
    allowed to be absent.
    """
    sym = normalize_research_symbol(symbol)
    if not sym:
        return None

    statements = _edgar_statements(sym)
    metrics: dict[str, Any] = {}
    profile: dict[str, Any] = {}
    if finnhub_is_configured():
        metrics = _run_async(afetch_company_metrics(sym)) or {}
        profile = _run_async(afetch_company_profile(sym)) or {}
    if not statements and not metrics and not profile:
        return None

    content: dict[str, Any] = {"metrics": metrics, "profile": profile}
    if statements:
        content["edgar"] = statements
    name = profile.get("name") or sym
    parts = [f"{name} fundamentals"]
    if statements:
        parts.append(f"{len(statements.get('metrics') or {})} as-reported EDGAR series")
    pe = metrics.get("pe_ttm")
    if pe is not None:
        parts.append(f"P/E {pe:.1f}")

    sources = ["SEC EDGAR"] if statements else []
    if metrics or profile:
        sources.append("Finnhub")
    return {
        "id": _next_source_id("fundamentals"),
        "type": "fundamentals_api",
        "title": f"{sym} fundamentals ({' + '.join(sources) or 'unavailable'})",
        "url": None,
        "snippet": ", ".join(parts),
        "content": content,
    }


def _edgar_statements(sym: str) -> dict[str, Any] | None:
    """Recent as-reported statements from the EDGAR corpus, trimmed for a prompt."""
    from research.fundamentals import latest_financials

    out = latest_financials(sym, periods=4)
    if not out or not out.get("metrics"):
        return None
    trimmed: dict[str, Any] = {}
    for metric, entry in out["metrics"].items():
        block: dict[str, Any] = {"unit": entry["unit"]}
        for bucket in ("annual", "quarterly"):
            if entry[bucket]:
                block[bucket] = [
                    {"period_end": i["period_end"], "val": i["val"], "filed": i["filed"]}
                    for i in entry[bucket]
                ]
        # An abandoned concept still has a newest row and reads as current
        # unless the staleness flag travels with it.
        if entry.get("stale"):
            block["stale"] = entry["stale"]
        trimmed[metric] = block
    return {
        "cik": out["cik"],
        "sector": out["sector"],
        "sic_desc": out["sic_desc"],
        "metrics": trimmed,
        "note": "as-reported SEC filings; `filed` is when each number became public",
    }


@_fail_soft
def fetch_segments_source(symbol: str) -> dict[str, Any] | None:
    """Segment revenue/operating income — the breakdown no other source here has.

    "Which segment carries the margin" is a research question the engine could
    not previously touch: the vendor metrics are company-level and the XBRL
    corpus strips dimensions.
    """
    from research.fundamentals import segment_series

    sym = normalize_research_symbol(symbol)
    if not sym:
        return None
    out = segment_series(sym, concept="revenue", periods=4)
    if not out or not out["series"]:
        return None

    reported = [s for s in out["series"] if s["axis"] in ("BusinessSegments", "Geographical")]
    if not reported:
        reported = out["series"][:8]
    content = [
        {
            "axis": s["axis"],
            "member": s["member"],
            "unit": s["unit"],
            "annual": [
                {"period_end": a["period_end"], "val": a["val"]} for a in s["annual"][:4]
            ],
        }
        for s in reported[:12]
    ]
    members = ", ".join(dict.fromkeys(s["member"] for s in reported))[:160]
    return {
        "id": _next_source_id("segments"),
        "type": "segments_api",
        "title": f"{sym} segment breakdown (SEC DERA)",
        "url": None,
        "snippet": f"{sym} revenue by segment — {members}",
        "content": {
            "segments": content,
            "note": (
                "as-reported segment values; DERA publishes ~a quarter in arrears, "
                "so the newest quarter is not here yet"
            ),
        },
    }


@_fail_soft
def fetch_news_source(symbol: str) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym or not broker_is_configured("alpaca"):
        return None

    raw = _run_async(fetch_watchlist_news(symbol=sym, limit=15))
    if not raw:
        return None

    items = [
        {
            "headline": item.get("headline"),
            "source": item.get("source"),
            "datetime": item.get("datetime"),
            "url": item.get("url"),
        }
        for item in raw
        if item.get("headline")
    ]
    if not items:
        return None

    return {
        "id": _next_source_id("news"),
        "type": "news_api",
        "title": f"{sym} recent news (Alpaca)",
        "url": None,
        "snippet": f"{len(items)} recent headlines",
        "content": items,
    }


@_fail_soft
def fetch_market_regime_source() -> dict[str, Any] | None:
    regime = market_regime()
    if regime is None:
        return None

    term_spread = float(regime["term_spread"])
    vix = float(regime["vix"])
    fresh = regime_is_fresh()
    label = _regime_label(term_spread, vix)

    return {
        "id": _next_source_id("market"),
        "type": "market_internal",
        "title": "Market regime (internal)",
        "url": None,
        "snippet": f"{label}, VIX {vix:.1f}, spread {term_spread:+.2f}",
        "content": {
            "term_spread": term_spread,
            "vix": vix,
            "regime_label": label,
            "fresh": fresh,
        },
    }


@_fail_soft
def fetch_calibration_source() -> dict[str, Any] | None:
    calibration = compute_calibration()
    if calibration.get("n_resolved", 0) == 0:
        return None

    n = calibration["n_resolved"]
    return {
        "id": _next_source_id("calibration"),
        "type": "calibration_internal",
        "title": "Agent calibration: predicted vs realized (internal)",
        "url": None,
        "snippet": f"{n} resolved trades — predicted vs realized win rates",
        "content": calibration,
    }


@_fail_soft
def fetch_sector_peers_source(symbol: str) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym or not finnhub_is_configured():
        return None

    profile = _run_async(afetch_company_profile(sym)) or {}
    industry = profile.get("finnhubIndustry") or profile.get("industry")
    if not industry:
        return None

    return {
        "id": _next_source_id("sector"),
        "type": "sector_internal",
        "title": f"{sym} sector (internal)",
        "url": None,
        "snippet": f"Industry: {industry}",
        "content": {"symbol": sym, "industry": industry},
    }
