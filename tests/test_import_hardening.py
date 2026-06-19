"""Import hardening: options FIFO, atomicity, broker_txn_id dedup."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from db.analytics import compute_round_trips
from db.fifo_round_trips import fifo_round_trip_stats
from db.trades import (
    create_trade_account,
    import_trade_events,
    list_trade_events,
    trade_summary_stats,
)
from imports.base import ParsedTradeEvent
from imports.money import decimal_to_str, parse_dollar
from imports.option_symbol import parse_option_symbol
from imports.schwab_transactions import parse_schwab_transactions_json

OPTION_CALL = "SPXW 02/13/2026 6950.00 C"
OPTION_PUT = "TSLA 02/20/2026 410.00 P"


def _event(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    amount: Decimal,
    *,
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
    amount: Decimal,
    *,
    key: str,
    fees: Decimal = Decimal("0"),
    event_type: str = "trade",
    broker_txn_id: str | None = None,
) -> ParsedTradeEvent:
    return ParsedTradeEvent(
        event_type=event_type,
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=None,
        fees=fees,
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={"activityId": broker_txn_id} if broker_txn_id else {},
        broker_txn_id=broker_txn_id,
    )


def _legacy_equity_fifo(trade_events: list[dict]) -> tuple[int, int, int]:
    """Pre-R1a equity-only FIFO (skips symbols with spaces)."""
    from collections import defaultdict

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for event in trade_events:
        symbol = event.get("symbol")
        if not symbol or " " in symbol:
            continue
        by_symbol[symbol].append(event)

    wins = 0
    losses = 0
    round_trips = 0

    for _symbol, rows in by_symbol.items():
        sorted_rows = sorted(rows, key=lambda r: (r["trade_date"], r["id"]))
        lots: list[tuple[Decimal, Decimal, Decimal]] = []

        for row in sorted_rows:
            side = (row.get("side") or "").lower()
            qty = parse_dollar(row.get("quantity"))
            if qty <= 0:
                continue
            fees = parse_dollar(row.get("fees"))
            amount = parse_dollar(row.get("amount"))

            if side == "buy":
                cost_per = (-amount - fees) / qty if qty else Decimal("0")
                lots.append((qty, cost_per, fees))
                continue
            if side != "sell":
                continue

            remaining = qty
            proceeds = amount
            sell_fees = fees
            cost_basis = Decimal("0")
            matched_qty = Decimal("0")

            while remaining > 0 and lots:
                lot_qty, lot_cost, _lot_fees = lots[0]
                take = min(remaining, lot_qty)
                cost_basis += take * lot_cost
                matched_qty += take
                remaining -= take
                if take == lot_qty:
                    lots.pop(0)
                else:
                    lots[0] = (lot_qty - take, lot_cost, _lot_fees)

            if matched_qty > 0:
                pnl = proceeds - cost_basis - sell_fees
                round_trips += 1
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1

    return wins, losses, round_trips


def test_parse_option_symbol_calls_and_puts() -> None:
    call = parse_option_symbol(OPTION_CALL)
    assert call is not None
    assert call.underlying == "SPXW"
    assert call.expiration == date(2026, 2, 13)
    assert call.strike == Decimal("6950.00")
    assert call.right == "C"
    assert call.multiplier == 100

    put = parse_option_symbol(OPTION_PUT)
    assert put is not None
    assert put.underlying == "TSLA"
    assert put.expiration == date(2026, 2, 20)
    assert put.strike == Decimal("410.00")
    assert put.right == "P"

    assert parse_option_symbol("AAPL") is None


def test_options_round_trip_sell_to_close(tmp_path: Path) -> None:
    symbol = OPTION_PUT
    events = [
        _event(
            "2026-02-01",
            symbol,
            "buy",
            Decimal("2"),
            Decimal("-500"),
            key="o1",
            row_id=1,
        ),
        _event(
            "2026-02-10",
            symbol,
            "sell",
            Decimal("2"),
            Decimal("600"),
            key="o2",
            row_id=2,
        ),
    ]

    round_trips = compute_round_trips(events)
    assert len(round_trips) == 1
    rt = round_trips[0]
    assert rt["symbol"] == symbol
    assert rt["is_option"] is True
    assert rt["asset_class"] == "option"
    assert parse_dollar(rt["pnl"]) == Decimal("100")

    db_path = tmp_path / "trades.db"
    account = create_trade_account("Opt", "schwab", db_path=db_path)
    parsed = [
        _parsed(
            "2026-02-01",
            symbol,
            "buy",
            Decimal("2"),
            Decimal("-500"),
            key="o1",
        ),
        _parsed(
            "2026-02-10",
            symbol,
            "sell",
            Decimal("2"),
            Decimal("600"),
            key="o2",
        ),
    ]
    import_trade_events(account["id"], parsed, source="test", db_path=db_path)
    stats = trade_summary_stats(account_id=account["id"], db_path=db_path)
    assert stats["round_trips"] == 1


def test_options_round_trip_expired_long_loss(tmp_path: Path) -> None:
    symbol = OPTION_CALL
    events = [
        _event(
            "2026-01-30",
            symbol,
            "buy",
            Decimal("2"),
            Decimal("-500"),
            key="e1",
            row_id=1,
        ),
        _event(
            "2026-01-30",
            symbol,
            "",
            Decimal("2"),
            Decimal("0"),
            event_type="option_expired",
            key="e2",
            row_id=2,
        ),
    ]

    round_trips = compute_round_trips(events)
    assert len(round_trips) == 1
    assert round_trips[0]["is_option"] is True
    assert parse_dollar(round_trips[0]["pnl"]) == Decimal("-500")


def test_equity_round_trip_regression_unchanged() -> None:
    events = [
        _event("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("-1000"), key="a1", row_id=1),
        _event("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("1100"), key="a2", row_id=2),
        _event("2026-01-06", "MSFT", "buy", Decimal("5"), Decimal("-500"), key="m1", row_id=3),
        _event("2026-01-07", "MSFT", "sell", Decimal("5"), Decimal("450"), key="m2", row_id=4),
    ]

    legacy = _legacy_equity_fifo(events)
    current_stats = fifo_round_trip_stats(events)
    current_trips = compute_round_trips(events)

    assert current_stats == legacy
    assert len(current_trips) == 2
    pnls = {rt["symbol"]: parse_dollar(rt["pnl"]) for rt in current_trips}
    assert pnls["AAPL"] == Decimal("100")
    assert pnls["MSFT"] == Decimal("-50")
    assert all(not rt["is_option"] for rt in current_trips)


def test_import_atomicity_rolls_back_on_error(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Atomic", "schwab", db_path=db_path)
    events = [
        _parsed("2026-01-01", "AAPL", "buy", Decimal("1"), Decimal("-100"), key="good"),
        _parsed("2026-01-02", "MSFT", "buy", Decimal("1"), Decimal("-50"), key=""),
    ]

    result = import_trade_events(
        account["id"],
        events,
        source="test",
        db_path=db_path,
    )

    assert result.inserted == 0
    assert result.batch_id is None
    assert result.failed == [{"row_index": 1, "reason": "missing external_key"}]
    assert list_trade_events(account_id=account["id"], db_path=db_path) == []


def test_dedup_by_broker_txn_id(tmp_path: Path) -> None:
    db_path = tmp_path / "trades.db"
    account = create_trade_account("Dedup", "schwab", db_path=db_path)

    row = {
        "Date": "01/02/2026",
        "Action": "Buy",
        "Symbol": "AAPL",
        "Description": "APPLE INC",
        "Quantity": "10",
        "Price": "$100.00",
        "Fees & Comm": "",
        "Amount": "-$1,000.00",
        "activityId": "schwab-act-9001",
    }
    parsed_once = parse_schwab_transactions_json(
        {"BrokerageTransactions": [row]},
        account_id=account["id"],
    )
    assert parsed_once.events[0].broker_txn_id == "schwab-act-9001"

    first = import_trade_events(
        account["id"],
        parsed_once.events,
        source="schwab_json_import",
        db_path=db_path,
    )
    assert first.inserted == 1

    row_variant = dict(row)
    row_variant["Amount"] = "-$1,001.00"
    parsed_twice = parse_schwab_transactions_json(
        {"BrokerageTransactions": [row_variant]},
        account_id=account["id"],
    )

    second = import_trade_events(
        account["id"],
        parsed_twice.events,
        source="schwab_json_import",
        db_path=db_path,
    )
    assert second.inserted == 0
    assert second.duplicates == 1
    assert len(list_trade_events(account_id=account["id"], db_path=db_path)) == 1
