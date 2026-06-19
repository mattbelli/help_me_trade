"""Reliable net-cash realized P&L, reconciliation, and behavioral facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from db.analytics import ai_performance_context, compute_round_trips
from db.behavioral import behavioral_facts
from db.realized_pnl import realized_summary, reconcile_positions
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from imports.money import decimal_to_str, parse_dollar


def _event(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    *,
    amount: Decimal,
    key: str,
    fees: Decimal = Decimal("0"),
    event_type: str = "trade",
    row_id: int = 1,
) -> dict:
    return {
        "id": row_id,
        "event_type": event_type,
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
        "quantity": decimal_to_str(quantity),
        "price": None,
        "fees": decimal_to_str(fees),
        "amount": decimal_to_str(amount),
    }


def _parsed(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    *,
    amount: Decimal,
    key: str,
    fees: Decimal = Decimal("0"),
) -> ParsedTradeEvent:
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=None,
        fees=fees,
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={},
    )


def test_closed_symbol_net_cash_exact() -> None:
    buy_amount = Decimal("-1005")
    sell_amount = Decimal("1195")
    expected = buy_amount + sell_amount

    events = [
        _event(
            "2026-01-01",
            "AAPL",
            "buy",
            Decimal("100"),
            amount=buy_amount,
            key="b1",
            row_id=1,
        ),
        _event(
            "2026-01-05",
            "AAPL",
            "sell",
            Decimal("100"),
            amount=sell_amount,
            key="s1",
            row_id=2,
        ),
    ]

    recon = reconcile_positions(events)
    assert recon["AAPL"].status == "closed"
    assert recon["AAPL"].net_cash == expected

    summary = realized_summary(events)
    assert parse_dollar(summary["realized_closed"]) == expected
    assert summary["per_symbol"][0]["status"] == "closed"
    assert summary["reliability"]["realized_closed"] == "exact"


def test_incomplete_symbol_excluded_from_realized_closed() -> None:
    events = [
        _event(
            "2026-01-01",
            "PLTR",
            "sell",
            Decimal("50"),
            amount=Decimal("2500"),
            key="s1",
            row_id=1,
        ),
        _event(
            "2026-01-02",
            "PLTR",
            "buy",
            Decimal("10"),
            amount=Decimal("-500"),
            key="b1",
            row_id=2,
        ),
    ]

    recon = reconcile_positions(events)
    assert recon["PLTR"].status == "incomplete"

    summary = realized_summary(events)
    assert parse_dollar(summary["realized_closed"]) == Decimal("0")
    assert len(summary["incomplete_positions"]) == 1
    assert summary["incomplete_positions"][0]["symbol"] == "PLTR"


def test_same_day_flips_and_symbol_concentration() -> None:
    events = [
        _event("2026-01-01", "NVDA", "buy", Decimal("10"), amount=Decimal("-1000"), key="n1", row_id=1),
        _event("2026-01-01", "NVDA", "sell", Decimal("10"), amount=Decimal("1050"), key="n2", row_id=2),
        _event("2026-01-01", "NVDA", "buy", Decimal("5"), amount=Decimal("-500"), key="n3", row_id=3),
        _event("2026-01-02", "AAPL", "buy", Decimal("5"), amount=Decimal("-500"), key="a1", row_id=4),
        _event("2026-01-03", "AAPL", "sell", Decimal("5"), amount=Decimal("520"), key="a2", row_id=5),
        _event("2026-01-04", "MSFT", "buy", Decimal("2"), amount=Decimal("-400"), key="m1", row_id=6),
    ]

    facts = behavioral_facts(events)

    assert facts["same_day_flips"]["total"] == 1
    assert facts["same_day_flips"]["top_symbols"][0]["symbol"] == "NVDA"
    assert facts["symbol_concentration"]["by_trade_count"][0]["symbol"] == "NVDA"
    assert facts["symbol_concentration"]["by_volume"][0]["symbol"] == "NVDA"


def test_ai_performance_context_leads_with_reliable_realized(
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

    buy_amount = Decimal("-1000")
    sell_amount = Decimal("1200")
    events = [
        _parsed("2026-01-01", "AAPL", "buy", Decimal("10"), amount=buy_amount, key="a1"),
        _parsed("2026-01-05", "AAPL", "sell", Decimal("10"), amount=sell_amount, key="a2"),
        _parsed("2026-01-06", "PLTR", "sell", Decimal("20"), amount=Decimal("800"), key="p1"),
    ]
    account = create_trade_account("Test", "schwab", db_path=db_path)
    import_trade_events(account["id"], events, source="test", db_path=db_path)

    context = ai_performance_context(account_id=account["id"], period="all")

    assert "realized" in context
    assert parse_dollar(context["realized"]["realized_closed"]) == buy_amount + sell_amount
    assert context["realized"]["reliability"]["realized_closed"] == "exact"
    assert any(
        item["symbol"] == "PLTR" for item in context["realized"]["incomplete_positions"]
    )
    assert "behavioral" in context
    assert "approximate_fifo" in context
    assert "approximate (hold-time only)" in context["approximate_fifo"]["note"]


def test_nvda_like_flat_position_net_cash_not_fifo() -> None:
    """Scalping-heavy flat NVDA shape: net-cash truth, not FIFO headline totals."""
    nvda = [
        _event("2026-01-02", "NVDA", "buy", Decimal("100"), amount=Decimal("-12000"), key="n1", row_id=1),
        _event("2026-01-02", "NVDA", "sell", Decimal("50"), amount=Decimal("6500"), key="n2", row_id=2),
        _event("2026-01-03", "NVDA", "buy", Decimal("50"), amount=Decimal("-6200"), key="n3", row_id=3),
        _event("2026-01-03", "NVDA", "sell", Decimal("100"), amount=Decimal("13069.60"), key="n4", row_id=4),
    ]
    pltr = [
        _event("2026-01-01", "PLTR", "sell", Decimal("500"), amount=Decimal("25000"), key="p1", row_id=5),
        _event("2026-01-02", "PLTR", "buy", Decimal("100"), amount=Decimal("-5000"), key="p2", row_id=6),
        _event("2026-01-03", "PLTR", "sell", Decimal("100"), amount=Decimal("5500"), key="p3", row_id=7),
        _event("2026-01-04", "PLTR", "buy", Decimal("50"), amount=Decimal("-2500"), key="p4", row_id=8),
    ]
    events = nvda + pltr

    summary = realized_summary(events)
    fifo_total = sum(parse_dollar(rt["pnl"]) for rt in compute_round_trips(events))

    assert parse_dollar(summary["realized_closed"]) == Decimal("1369.60")
    assert reconcile_positions(events)["NVDA"].status == "closed"
    assert fifo_total > parse_dollar(summary["realized_closed"])
    assert summary["incomplete_positions"][0]["symbol"] == "PLTR"
