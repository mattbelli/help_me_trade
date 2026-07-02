"""Mark-to-market unrealized P&L from FIFO open lots vs broker snapshot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from db.analytics import (
    ai_performance_context,
    compute_open_lots,
    compute_unrealized,
)
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from imports.money import decimal_to_str, parse_dollar


def _trade(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    price: Decimal,
    *,
    key: str,
    fees: Decimal = Decimal("0"),
    row_id: int = 1,
) -> dict:
    qty = quantity
    amount = qty * price if side == "sell" else -(qty * price)
    return {
        "id": row_id,
        "event_type": "trade",
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
        "quantity": decimal_to_str(qty),
        "price": decimal_to_str(price),
        "fees": decimal_to_str(fees),
        "amount": decimal_to_str(amount),
    }


def _parsed_trade(
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


def _position(
    symbol: str,
    *,
    market_value: float,
    unrealized_pl: float,
    qty: float = 10.0,
) -> dict:
    return {
        "symbol": symbol,
        "qty": qty,
        "side": "long",
        "market_value": market_value,
        "unrealized_pl": unrealized_pl,
        "avg_entry_price": 100.0,
        "current_price": market_value / qty if qty else 0.0,
    }


def test_compute_open_lots_buys_only() -> None:
    trades = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1", row_id=1),
    ]

    result = compute_open_lots(trades)

    assert result == {
        "AAPL": {
            "quantity": "10",
            "avg_cost": "100",
            "cost_basis": "1000",
        }
    }


def test_compute_open_lots_partial_sell() -> None:
    trades = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1", row_id=1),
        _trade("2026-01-05", "AAPL", "sell", Decimal("6"), Decimal("110"), key="a2", row_id=2),
    ]

    result = compute_open_lots(trades)

    assert result == {
        "AAPL": {
            "quantity": "4",
            "avg_cost": "100",
            "cost_basis": "400",
        }
    }


def test_compute_open_lots_fully_closed_omits_symbol() -> None:
    trades = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1", row_id=1),
        _trade("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("110"), key="a2", row_id=2),
    ]

    assert compute_open_lots(trades) == {}


def test_compute_open_lots_weighted_average_two_buys() -> None:
    trades = [
        _trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1", row_id=1),
        _trade("2026-01-02", "AAPL", "buy", Decimal("10"), Decimal("120"), key="a2", row_id=2),
    ]

    result = compute_open_lots(trades)

    assert result == {
        "AAPL": {
            "quantity": "20",
            "avg_cost": "110",
            "cost_basis": "2200",
        }
    }


def test_compute_unrealized_matched_open_lot_and_snapshot() -> None:
    open_lots = {
        "AAPL": {
            "quantity": "10",
            "avg_cost": "100",
            "cost_basis": "1000",
        }
    }
    positions = [_position("AAPL", market_value=1200.0, unrealized_pl=180.0)]

    result = compute_unrealized(open_lots, positions)

    assert result["by_symbol"]["AAPL"] == {
        "quantity": "10",
        "cost_basis": "1000",
        "market_value": "1200.0",
        "ledger_unrealized_pl": "200.0",
        "broker_unrealized_pl": "180.0",
    }
    assert result["totals"] == {
        "cost_basis": "1000",
        "market_value": "1200.0",
        "ledger_unrealized_pl": "200.0",
        "broker_unrealized_pl": "180.0",
    }
    assert result["missing_price_symbols"] == []


def test_compute_unrealized_snapshot_only_symbol() -> None:
    open_lots = {
        "AAPL": {
            "quantity": "10",
            "avg_cost": "100",
            "cost_basis": "1000",
        }
    }
    positions = [
        _position("AAPL", market_value=1200.0, unrealized_pl=180.0),
        _position("MSFT", market_value=500.0, unrealized_pl=50.0, qty=5.0),
    ]

    result = compute_unrealized(open_lots, positions)

    assert result["by_symbol"]["MSFT"] == {
        "cost_basis": None,
        "ledger_unrealized_pl": None,
        "broker_unrealized_pl": "50.0",
    }
    assert result["totals"]["cost_basis"] == "1000"
    assert result["totals"]["broker_unrealized_pl"] == "180.0"


def test_compute_unrealized_missing_price_symbols() -> None:
    open_lots = {
        "AAPL": {
            "quantity": "10",
            "avg_cost": "100",
            "cost_basis": "1000",
        },
        "TSLA": {
            "quantity": "5",
            "avg_cost": "200",
            "cost_basis": "1000",
        },
    }
    positions = [_position("AAPL", market_value=1200.0, unrealized_pl=180.0)]

    result = compute_unrealized(open_lots, positions)

    assert result["missing_price_symbols"] == ["TSLA"]
    assert "TSLA" not in result["by_symbol"]
    assert parse_dollar(result["totals"]["ledger_unrealized_pl"]) == Decimal("200.0")


def test_ai_performance_context_includes_unrealized(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trades.db"
    from db import trades

    def _with_db(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

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
    monkeypatch.setattr(
        "db.trades.get_trade_account",
        _with_db(trades.get_trade_account),
    )
    monkeypatch.setattr(
        "db.sqlite.get_latest_snapshot",
        lambda **kwargs: {
            "positions": [
                _position("AAPL", market_value=1100.0, unrealized_pl=100.0, qty=10.0),
            ],
        },
    )

    events = [
        _parsed_trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
    ]
    account = create_trade_account("Test", "alpaca", db_path=db_path)
    import_trade_events(
        account["id"],
        events,
        source="test",
        db_path=db_path,
    )

    context = ai_performance_context(account_id=account["id"], period="all")

    assert "unrealized" in context
    unrealized = context["unrealized"]
    assert unrealized["by_symbol"]["AAPL"]["ledger_unrealized_pl"] == "100.0"
    assert unrealized["by_symbol"]["AAPL"]["broker_unrealized_pl"] == "100.0"
    assert unrealized["missing_price_symbols"] == []


def test_ai_performance_context_real_scope_skips_alpaca_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "trades_real.db"
    from db import trades

    def _with_db(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

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
    monkeypatch.setattr(
        "db.trades.get_trade_account",
        _with_db(trades.get_trade_account),
    )
    monkeypatch.setattr(
        "db.sqlite.get_latest_snapshot",
        lambda **kwargs: {
            "positions": [
                _position("AAPL", market_value=1100.0, unrealized_pl=100.0, qty=10.0),
            ],
        },
    )

    events = [
        _parsed_trade("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("100"), key="a1"),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(
        account["id"],
        events,
        source="test",
        db_path=db_path,
    )

    context = ai_performance_context(
        account_id=account["id"],
        real_only=False,
        paper_only=False,
        period="all",
    )

    assert context["unrealized"] == {
        "unavailable": "no real-time broker snapshot for real accounts",
    }
