"""Stock-split adjustment for FIFO round-trip P&L."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from db.corporate_actions import SplitEvent, get_split_factors
from db.fifo_round_trips import fifo_compute_round_trips, fifo_round_trip_stats
from imports.money import decimal_to_str, parse_dollar

OPTION_CALL = "NVDA 01/17/2026 150.00 C"


def _event(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    amount: Decimal,
    *,
    row_id: int = 1,
    fees: Decimal = Decimal("0"),
) -> dict:
    return {
        "id": row_id,
        "event_type": "trade",
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
        "quantity": decimal_to_str(quantity),
        "price": None,
        "fees": decimal_to_str(fees),
        "amount": decimal_to_str(amount),
    }


def _no_splits(_symbols) -> dict[str, list[SplitEvent]]:
    return {}


def test_nvda_split_adjusted_round_trip_gain() -> None:
    """Pre-split buy must match post-split sell with honest gain, not phantom loss."""
    buy_qty = Decimal("20")
    buy_price = Decimal("915")
    buy_amount = -(buy_qty * buy_price)  # -18300

    sell_qty = Decimal("200")
    sell_price = Decimal("209")
    sell_amount = sell_qty * sell_price  # 41800

    events = [
        _event("2024-03-08", "NVDA", "buy", buy_qty, buy_amount, row_id=1),
        _event("2026-06-15", "NVDA", "sell", sell_qty, sell_amount, row_id=2),
    ]

    nvda_splits = {
        "NVDA": [SplitEvent(date(2024, 6, 10), Decimal("10"))],
    }

    round_trips = fifo_compute_round_trips(events, split_factors=lambda _: nvda_splits)
    assert len(round_trips) == 1

    rt = round_trips[0]
    pnl = parse_dollar(rt["pnl"])
    phantom_loss = Decimal("-14113")

    assert pnl > 0
    assert pnl != phantom_loss
    assert pnl == sell_amount + buy_amount  # amount is split-invariant cash
    assert parse_dollar(rt["quantity"]) == sell_qty


def test_no_split_symbol_regression_unchanged() -> None:
    events = [
        _event("2026-01-01", "AAPL", "buy", Decimal("10"), Decimal("-1000"), row_id=1),
        _event("2026-01-05", "AAPL", "sell", Decimal("10"), Decimal("1100"), row_id=2),
        _event("2026-01-06", "MSFT", "buy", Decimal("5"), Decimal("-500"), row_id=3),
        _event("2026-01-07", "MSFT", "sell", Decimal("5"), Decimal("450"), row_id=4),
    ]

    trips = fifo_compute_round_trips(events, split_factors=_no_splits)
    stats = fifo_round_trip_stats(events, split_factors=_no_splits)

    assert stats == (1, 1, 2)
    assert len(trips) == 2
    pnls = {rt["symbol"]: parse_dollar(rt["pnl"]) for rt in trips}
    assert pnls["AAPL"] == Decimal("100")
    assert pnls["MSFT"] == Decimal("-50")

    empty_map_trips = fifo_compute_round_trips(events, split_factors=lambda _s: {})
    assert empty_map_trips == trips


def test_multiple_splits_compose() -> None:
    symbol = "SYNTH"
    splits = {
        "SYNTH": [
            SplitEvent(date(2020, 1, 1), Decimal("2")),
            SplitEvent(date(2022, 1, 1), Decimal("3")),
        ],
    }

    # Buy 10 pre-split → 10*2*3 = 60 post-split-adjusted shares @ $10/share cost
    events = [
        _event("2019-06-01", symbol, "buy", Decimal("10"), Decimal("-100"), row_id=1),
        _event("2023-06-01", symbol, "sell", Decimal("60"), Decimal("600"), row_id=2),
    ]

    round_trips = fifo_compute_round_trips(events, split_factors=lambda _: splits)
    assert len(round_trips) == 1
    assert parse_dollar(round_trips[0]["pnl"]) == Decimal("500")


def test_options_unaffected_by_split_logic() -> None:
    underlying_splits = {
        "NVDA": [SplitEvent(date(2024, 6, 10), Decimal("10"))],
    }

    events = [
        _event("2024-03-08", OPTION_CALL, "buy", Decimal("1"), Decimal("-500"), row_id=1),
        _event("2024-04-01", OPTION_CALL, "sell", Decimal("1"), Decimal("700"), row_id=2),
    ]

    with_splits = fifo_compute_round_trips(
        events,
        split_factors=lambda _: underlying_splits,
    )
    without_splits = fifo_compute_round_trips(events, split_factors=_no_splits)

    assert with_splits == without_splits
    assert parse_dollar(with_splits[0]["pnl"]) == Decimal("200")


def test_known_splits_seeded_for_nvda() -> None:
    factors = get_split_factors(["NVDA"])
    assert "NVDA" in factors
    assert factors["NVDA"] == [SplitEvent(date(2024, 6, 10), Decimal("10"))]


def test_compute_round_trips_integration() -> None:
    events = [
        _event("2024-03-08", "NVDA", "buy", Decimal("20"), Decimal("-18300"), row_id=1),
        _event("2026-06-15", "NVDA", "sell", Decimal("200"), Decimal("41800"), row_id=2),
    ]
    nvda_splits = {
        "NVDA": [SplitEvent(date(2024, 6, 10), Decimal("10"))],
    }
    trips = fifo_compute_round_trips(events, split_factors=lambda _: nvda_splits)

    assert len(trips) == 1
    assert parse_dollar(trips[0]["pnl"]) == Decimal("23500")
