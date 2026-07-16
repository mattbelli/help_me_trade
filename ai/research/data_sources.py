"""First-party research data sources — sync, thread-safe, fail-soft."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
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
    fetch_watchlist_news,
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
    sym = normalize_research_symbol(symbol)
    if not sym or not finnhub_is_configured():
        return None

    metrics = _run_async(afetch_company_metrics(sym)) or {}
    profile = _run_async(afetch_company_profile(sym)) or {}
    if not metrics and not profile:
        return None

    content = {"metrics": metrics, "profile": profile}
    name = profile.get("name") or sym
    pe = metrics.get("pe_ttm")
    snippet = f"{name} fundamentals"
    if pe is not None:
        snippet += f", P/E {pe:.1f}"

    return {
        "id": _next_source_id("fundamentals"),
        "type": "fundamentals_api",
        "title": f"{sym} fundamentals (Finnhub)",
        "url": None,
        "snippet": snippet,
        "content": content,
    }


@_fail_soft
def fetch_news_source(symbol: str) -> dict[str, Any] | None:
    sym = normalize_research_symbol(symbol)
    if not sym or not finnhub_is_configured():
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
        "title": f"{sym} recent news (Finnhub)",
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
