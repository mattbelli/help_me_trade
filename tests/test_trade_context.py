"""Tests for deterministic per-trade market context and entry-style profile."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from db.analytics import ai_performance_context
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from signals.trade_context import (
    MIN_BARS_FOR_CONTEXT,
    DatedBar,
    build_trade_context,
    entry_style_profile,
    fetch_bars_for_trade_context,
)


def _event(
    trade_date: str,
    symbol: str,
    side: str,
    *,
    event_type: str = "trade",
) -> dict:
    return {
        "event_type": event_type,
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
    }


def _dated_bars_from_closes(
    close_series: list[float],
    start_date: date,
    *,
    volume: float = 1000.0,
) -> list[DatedBar]:
    bars: list[DatedBar] = []
    d = start_date
    for i, c in enumerate(close_series):
        prev_c = close_series[i - 1] if i > 0 else c
        bars.append(
            {
                "d": d.isoformat(),
                "o": prev_c if i > 0 else c,
                "h": c + 1.0,
                "l": c - 1.0,
                "c": c,
                "v": volume,
            }
        )
        d += timedelta(days=1)
    return bars


def _pullback_bars() -> tuple[list[DatedBar], str]:
    """Uptrend with a 3-day dip; buy on the dip day (not near 52w high)."""
    n = MIN_BARS_FOR_CONTEXT + 8
    closes: list[float] = []
    for i in range(n - 6):
        if i < n - 30:
            closes.append(100.0 + i * 0.35)
        else:
            closes.append(closes[-1])
    peak = closes[-1]
    closes.extend([peak * 0.99, peak * 0.975, peak * 0.96, peak * 0.945, peak * 0.93, peak * 0.915])
    start = date(2024, 1, 1)
    bars = _dated_bars_from_closes(closes, start)
    trade_date = bars[-1]["d"]
    return bars, trade_date


def _dip_below_trend_bars() -> tuple[list[DatedBar], str]:
    """Declining series with price below a falling SMA200."""
    n = MIN_BARS_FOR_CONTEXT + 5
    closes = [220.0 - i * 0.45 for i in range(n)]
    start = date(2024, 1, 1)
    bars = _dated_bars_from_closes(closes, start)
    return bars, bars[-1]["d"]


def _breakout_bars() -> tuple[list[DatedBar], str]:
    """Flat base then rally to a new 52-week high."""
    n = MIN_BARS_FOR_CONTEXT + 3
    closes = [100.0] * (n - 4) + [102.0, 106.0, 110.0, 115.0]
    start = date(2024, 1, 1)
    bars = _dated_bars_from_closes(closes, start)
    return bars, bars[-1]["d"]


def test_pullback_buy_on_dip() -> None:
    bars, trade_date = _pullback_bars()
    events = [_event(trade_date, "AAPL", "buy")]
    context = build_trade_context(events, {"AAPL": bars})
    row = context[0]

    assert row["context_available"] is True
    assert row["entry_context"] == "pullback"
    assert row["prior_return_3d"] is not None
    assert row["prior_return_3d"] < 0
    assert row["vs_sma200"] == "above"


def test_dip_below_falling_sma200() -> None:
    bars, trade_date = _dip_below_trend_bars()
    events = [_event(trade_date, "XYZ", "buy")]
    context = build_trade_context(events, {"XYZ": bars})
    row = context[0]

    assert row["context_available"] is True
    assert row["entry_context"] == "dip_below_trend"
    assert row["vs_sma200"] == "below"

    profile = entry_style_profile(events, context)
    assert profile["fights_trend_pct"] == 100.0
    assert profile["style_lean"] == "mean_reversion"


def test_breakout_near_52w_high() -> None:
    bars, trade_date = _breakout_bars()
    events = [_event(trade_date, "NVDA", "buy")]
    context = build_trade_context(events, {"NVDA": bars})
    row = context[0]

    assert row["context_available"] is True
    assert row["entry_context"] == "breakout"
    assert row["dist_from_52w_high_pct"] is not None
    assert row["dist_from_52w_high_pct"] >= -5.0

    profile = entry_style_profile(events, context)
    assert profile["near_high_pct"] == 100.0
    assert profile["style_lean"] == "momentum"


def test_missing_and_short_bars_honest_coverage() -> None:
    events = [
        _event("2025-06-01", "MISSING", "buy"),
        _event("2025-06-02", "SHORT", "buy"),
    ]
    short_bars = _dated_bars_from_closes([100.0, 101.0, 102.0], date(2025, 5, 1))
    context = build_trade_context(events, {"SHORT": short_bars})

    missing_row = next(r for r in context if r["symbol"] == "MISSING")
    short_row = next(r for r in context if r["symbol"] == "SHORT")

    for row in (missing_row, short_row):
        assert row["context_available"] is False
        assert row["prior_return_3d"] is None
        assert row["entry_context"] is None

    profile = entry_style_profile(events, context)
    assert profile["coverage"]["total_equity_buys"] == 2
    assert profile["coverage"]["with_context"] == 0
    assert profile["coverage"]["without_context"] == 2


def _momentum_bars() -> tuple[list[DatedBar], str]:
    """Multi-day rally into entry, but still well below the 52-week high."""
    n = MIN_BARS_FOR_CONTEXT + 15
    closes: list[float] = []
    for i in range(n - 30):
        closes.append(100.0 + i * 0.35)
    peak = closes[-1]
    for _ in range(18):
        closes.append(peak * 0.78)
    base = closes[-1]
    closes.extend([base * 1.012, base * 1.024, base * 1.038, base * 1.052, base * 1.065])
    start = date(2024, 1, 1)
    bars = _dated_bars_from_closes(closes, start)
    return bars, bars[-1]["d"]


def test_entry_style_profile_mixed_distribution() -> None:
    pb_bars, pb_date = _pullback_bars()
    br_bars, br_date = _breakout_bars()
    dip_bars, dip_date = _dip_below_trend_bars()
    mom_bars, mom_date = _momentum_bars()

    events = [
        _event(pb_date, "AAPL", "buy"),
        _event(br_date, "NVDA", "buy"),
        _event(dip_date, "XYZ", "buy"),
        _event(mom_date, "MSFT", "buy"),
        _event(pb_date, "AAPL", "sell"),
    ]
    bars_by_symbol = {
        "AAPL": pb_bars,
        "NVDA": br_bars,
        "XYZ": dip_bars,
        "MSFT": mom_bars,
    }
    context = build_trade_context(events, bars_by_symbol)
    profile = entry_style_profile(events, context)

    assert profile["coverage"]["with_context"] == 4
    assert profile["buys_by_context"]["pullback"]["count"] == 1
    assert profile["buys_by_context"]["breakout"]["count"] == 1
    assert profile["buys_by_context"]["dip_below_trend"]["count"] == 1
    assert profile["buys_by_context"]["momentum"]["count"] == 1
    assert profile["style_lean"] == "mixed"
    assert profile["avg_prior_move_into_buys"] is not None


def test_skips_options_deferred() -> None:
    events = [
        _event("2025-06-01", "TSLA 02/20/2026 410.00 P", "buy"),
        _event("2025-06-01", "TSLA", "buy"),
    ]
    bars, trade_date = _pullback_bars()
    context = build_trade_context(events, {"TSLA": bars})

    assert len(context) == 1
    assert context[0]["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_fetch_bars_warmup_chunked() -> None:
    events = [
        _event("2024-03-15", "AAPL", "buy"),
        _event("2024-04-01", "MSFT", "buy"),
    ]

    class FakeBar:
        def __init__(self, d: date, close: float) -> None:
            self.time = f"{d.isoformat()}T00:00:00Z"
            self.open = close
            self.high = close + 1
            self.low = close - 1
            self.close = close
            self.volume = 1000.0

    start = date(2023, 1, 1)
    series = [100.0 + i * 0.1 for i in range(300)]

    class FakeBroker:
        async def aget_stock_bars_batch(self, symbols, **kwargs):
            assert kwargs["timeframe"] == "1Day"
            assert kwargs["days"] >= 252 + 30
            out = {}
            for sym in symbols:
                out[sym] = [
                    FakeBar(start + timedelta(days=i), c) for i, c in enumerate(series)
                ]
            return out

    broker = FakeBroker()
    bars = await fetch_bars_for_trade_context(events, broker)

    assert set(bars) == {"AAPL", "MSFT"}
    assert len(bars["AAPL"]) == 300
    assert bars["AAPL"][0]["d"] == start.isoformat()


def test_ai_performance_context_default_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trades.db"
    from db import trades

    def _with_db(fn):
        def wrapped(**kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(**kwargs)

        return wrapped

    monkeypatch.setattr("db.analytics.list_trade_events", _with_db(trades.list_trade_events))
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats",
        _with_db(trades.trade_summary_stats),
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats",
        _with_db(trades.daily_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats",
        _with_db(trades.symbol_trade_stats),
    )

    events = [
        ParsedTradeEvent(
            event_type="trade",
            trade_date=date(2026, 1, 1),
            symbol="AAPL",
            side="buy",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fees=Decimal("0"),
            amount=Decimal("-1000"),
            description="buy AAPL",
            external_key="a1",
            raw={},
        ),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(account["id"], events, source="test", db_path=db_path)

    context = ai_performance_context(account_id=account["id"], period="all")
    assert "entry_style" not in context


def test_ai_performance_context_include_trade_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trades.db"
    from db import trades

    def _with_db(fn):
        def wrapped(**kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(**kwargs)

        return wrapped

    monkeypatch.setattr("db.analytics.list_trade_events", _with_db(trades.list_trade_events))
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats",
        _with_db(trades.trade_summary_stats),
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats",
        _with_db(trades.daily_trade_stats),
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats",
        _with_db(trades.symbol_trade_stats),
    )

    pb_bars, pb_date = _pullback_bars()
    events = [
        ParsedTradeEvent(
            event_type="trade",
            trade_date=date.fromisoformat(pb_date),
            symbol="AAPL",
            side="buy",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fees=Decimal("0"),
            amount=Decimal("-1000"),
            description="buy AAPL",
            external_key="a1",
            raw={},
        ),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(account["id"], events, source="test", db_path=db_path)

    monkeypatch.setattr(
        "signals.trade_context.fetch_bars_for_trade_context_sync",
        lambda ev, broker: {"AAPL": pb_bars},
    )
    monkeypatch.setattr("config.settings.broker_is_configured", lambda _name: True)
    monkeypatch.setattr("config.settings.primary_broker", lambda: "alpaca")
    monkeypatch.setattr("brokers.factory.get_broker", lambda _name: object())

    context = ai_performance_context(
        account_id=account["id"],
        period="all",
        include_trade_context=True,
    )

    assert "entry_style" in context
    assert context["entry_style"]["profile"]["buys_by_context"]["pullback"]["count"] == 1
