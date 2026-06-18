"""Analytics enrichment: profit factor, hold time, streaks, behavioral flags."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from db.analytics import (
    ai_performance_context,
    compute_behavioral_flags,
    compute_hold_times,
    compute_profit_factor,
    compute_round_trips,
    compute_streaks,
)
from db.trades import create_trade_account, import_trade_events, list_trade_events
from imports.base import ParsedTradeEvent
from imports.money import parse_dollar


def _trade(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    *,
    key: str,
    fees: Decimal = Decimal("0"),
) -> ParsedTradeEvent:
    qty = quantity
    amount = qty * price if side == "sell" else -(qty * price)
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        fees=fees,
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={},
    )


def _seed_events(
    tmp_path: Path,
    events: list[ParsedTradeEvent],
) -> tuple[str, list[dict]]:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(
        account["id"],
        events,
        source="test",
        db_path=db_path,
    )
    stored = list_trade_events(account_id=account["id"], db_path=db_path)
    trades = [e for e in stored if e["event_type"] == "trade"]
    return account["id"], compute_round_trips(trades)


def test_compute_profit_factor_mixed_wins_and_losses(tmp_path: Path) -> None:
    events = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
        _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("120"), key="a2"),
        _trade("2026-01-06", "MSFT", "buy", Decimal("10"), Decimal("100"), key="m1"),
        _trade("2026-01-07", "MSFT", "sell", Decimal("10"), Decimal("80"), key="m2"),
        _trade("2026-01-08", "GOOG", "buy", Decimal("5"), Decimal("200"), key="g1"),
        _trade("2026-01-09", "GOOG", "sell", Decimal("5"), Decimal("220"), key="g2"),
    ]
    _, round_trips = _seed_events(tmp_path, events)

    result = compute_profit_factor(round_trips)

    assert parse_dollar(result["gross_wins"]) == Decimal("300")
    assert parse_dollar(result["gross_losses"]) == Decimal("200")
    assert result["profit_factor"] == 1.5


def test_compute_profit_factor_all_wins(tmp_path: Path) -> None:
    events = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
        _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("110"), key="a2"),
    ]
    _, round_trips = _seed_events(tmp_path, events)

    result = compute_profit_factor(round_trips)

    assert parse_dollar(result["gross_wins"]) == Decimal("100")
    assert parse_dollar(result["gross_losses"]) == Decimal("0")
    assert result["profit_factor"] is None


def test_compute_hold_times_single_and_multi(tmp_path: Path) -> None:
    events = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
        _trade("2026-01-10", "AAPL", "sell", Decimal("10"), Decimal("110"), key="a2"),
        _trade("2026-01-02", "MSFT", "buy", Decimal("5"), Decimal("200"), key="m1"),
        _trade("2026-01-04", "MSFT", "sell", Decimal("5"), Decimal("210"), key="m2"),
    ]
    _, round_trips = _seed_events(tmp_path, events)

    result = compute_hold_times(round_trips)

    assert result["count"] == 2
    assert result["min_hold_days"] == 2
    assert result["max_hold_days"] == 9
    assert result["avg_hold_days"] == 5.5


def test_compute_hold_times_empty() -> None:
    result = compute_hold_times([])

    assert result == {
        "avg_hold_days": None,
        "min_hold_days": None,
        "max_hold_days": None,
        "count": 0,
    }


def test_compute_streaks_sequence() -> None:
    # Close order ascending: W, W, L, W, W, W
    round_trips = [
        {"close_date": "2026-01-01", "pnl": "100"},
        {"close_date": "2026-01-02", "pnl": "50"},
        {"close_date": "2026-01-03", "pnl": "-25"},
        {"close_date": "2026-01-04", "pnl": "10"},
        {"close_date": "2026-01-05", "pnl": "20"},
        {"close_date": "2026-01-06", "pnl": "30"},
    ]

    result = compute_streaks(round_trips)

    assert result["longest_win"] == 3
    assert result["longest_loss"] == 1
    assert result["current_type"] == "win"
    assert result["current_len"] == 3


def test_compute_behavioral_flags_overtrading() -> None:
    daily_stats = [
        {"date": "2026-01-01", "event_count": 10},
        {"date": "2026-01-02", "event_count": 10},
    ]

    flags = compute_behavioral_flags([], daily_stats)

    assert len(flags) == 1
    assert flags[0]["key"] == "overtrading"
    assert "10.0" in flags[0]["detail"]


def test_compute_behavioral_flags_concentration() -> None:
    round_trips = [
        {"symbol": "AAPL", "open_date": "2026-01-01", "close_date": "2026-01-02", "pnl": "10"},
        {"symbol": "AAPL", "open_date": "2026-01-03", "close_date": "2026-01-04", "pnl": "10"},
        {"symbol": "AAPL", "open_date": "2026-01-05", "close_date": "2026-01-06", "pnl": "10"},
        {"symbol": "MSFT", "open_date": "2026-01-01", "close_date": "2026-01-02", "pnl": "-5"},
    ]

    flags = compute_behavioral_flags(round_trips, [])

    assert len(flags) == 1
    assert flags[0]["key"] == "concentration"
    assert "AAPL" in flags[0]["detail"]
    assert "75%" in flags[0]["detail"]


def test_compute_behavioral_flags_revenge_trading(tmp_path: Path) -> None:
    events = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
        _trade("2026-01-02", "AAPL", "sell", Decimal("10"), Decimal("80"), key="a2"),
        _trade("2026-01-02", "AAPL", "buy", Decimal("10"), Decimal("80"), key="a3"),
        _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("90"), key="a4"),
    ]
    _, round_trips = _seed_events(tmp_path, events)

    flags = compute_behavioral_flags(round_trips, [])

    assert any(flag["key"] == "revenge_trading" for flag in flags)
    assert any("AAPL" in flag["detail"] for flag in flags if flag["key"] == "revenge_trading")


def test_compute_behavioral_flags_clean() -> None:
    round_trips = [
        {"symbol": "AAPL", "open_date": "2026-01-01", "close_date": "2026-01-05", "pnl": "10"},
        {"symbol": "MSFT", "open_date": "2026-01-02", "close_date": "2026-01-06", "pnl": "-5"},
    ]
    daily_stats = [
        {"date": "2026-01-01", "event_count": 2},
        {"date": "2026-01-02", "event_count": 2},
    ]

    assert compute_behavioral_flags(round_trips, daily_stats) == []


def test_ai_performance_context_includes_enrichment_keys(
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

    monkeypatch.setattr(
        "db.analytics.list_trade_events",
        _with_db(trades.list_trade_events),
    )
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
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
        _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("110"), key="a2"),
    ]
    account_id, _ = _seed_events(tmp_path, events)

    context = ai_performance_context(
        account_id=account_id,
        period="all",
    )

    assert "profit_factor" in context
    assert "hold_time" in context
    assert "streaks" in context
    assert "behavioral_flags" in context
    assert context["profit_factor"]["profit_factor"] is None
    assert context["hold_time"]["count"] == 1
    assert context["streaks"]["current_type"] == "win"
    assert isinstance(context["behavioral_flags"], list)
