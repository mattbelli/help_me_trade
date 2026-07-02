"""Boundary-delta period realized P&L carries cost basis across window edges."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from db.analytics import (
    daily_boundary_realized_stats,
    performance_dashboard,
    realized_between,
)
from db.realized_pnl import realized_summary
from db.trades import create_trade_account, import_trade_events
from imports.base import ParsedTradeEvent
from imports.money import parse_dollar


def _event(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    *,
    amount: Decimal,
    key: str,
    row_id: int = 1,
) -> dict:
    return {
        "id": row_id,
        "event_type": "trade",
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
        "quantity": str(quantity),
        "price": None,
        "fees": "0",
        "amount": str(amount),
    }


def _parsed(
    trade_date: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    *,
    amount: Decimal,
    key: str,
) -> ParsedTradeEvent:
    return ParsedTradeEvent(
        event_type="trade",
        trade_date=date.fromisoformat(trade_date),
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=None,
        fees=Decimal("0"),
        amount=amount,
        description=f"{side} {symbol}",
        external_key=key,
        raw={},
    )


@pytest.fixture
def golden_history() -> list[dict]:
    """Buy 100 X @ $10 day 1; sell 100 X @ $12 day 2."""
    return [
        _event(
            "2026-01-01",
            "X",
            "buy",
            Decimal("100"),
            amount=Decimal("-1000"),
            key="x-b",
            row_id=1,
        ),
        _event(
            "2026-01-02",
            "X",
            "sell",
            Decimal("100"),
            amount=Decimal("1200"),
            key="x-s",
            row_id=2,
        ),
    ]


def test_day2_realized_carries_basis(golden_history: list[dict]) -> None:
    result = realized_between(golden_history, "2026-01-02", "2026-01-02")
    assert parse_dollar(result["realized_closed"]) == Decimal("200")


def test_day1_realized_zero_while_open(golden_history: list[dict]) -> None:
    result = realized_between(golden_history, "2026-01-01", "2026-01-01")
    assert parse_dollar(result["realized_closed"]) == Decimal("0")


def test_full_period_realized(golden_history: list[dict]) -> None:
    result = realized_between(golden_history, "2026-01-01", "2026-01-02")
    assert parse_dollar(result["realized_closed"]) == Decimal("200")


def test_per_day_sum_equals_period(golden_history: list[dict]) -> None:
    daily = daily_boundary_realized_stats(
        golden_history,
        [
            {"date": "2026-01-01", "net_amount": "-1000", "event_count": 1},
            {"date": "2026-01-02", "net_amount": "1200", "event_count": 1},
        ],
        from_date="2026-01-01",
        to_date="2026-01-02",
    )
    day_map = {row["date"]: row for row in daily}
    assert parse_dollar(day_map["2026-01-01"]["realized"]) == Decimal("0")
    assert parse_dollar(day_map["2026-01-02"]["realized"]) == Decimal("200")
    assert parse_dollar(day_map["2026-01-02"]["net_amount"]) == Decimal("1200")

    per_day_total = sum(parse_dollar(row["realized"]) for row in daily)
    period = realized_between(golden_history, "2026-01-01", "2026-01-02")
    assert per_day_total == parse_dollar(period["realized_closed"])


def test_open_and_incomplete_symbols_contribute_zero() -> None:
    history = [
        _event(
            "2026-01-01",
            "OPEN",
            "buy",
            Decimal("10"),
            amount=Decimal("-500"),
            key="o-b",
            row_id=1,
        ),
        _event(
            "2026-01-02",
            "NOBASIS",
            "sell",
            Decimal("5"),
            amount=Decimal("250"),
            key="n-s",
            row_id=2,
        ),
    ]
    for from_d, to_d in [("2026-01-01", "2026-01-01"), ("2026-01-02", "2026-01-02")]:
        result = realized_between(history, from_d, to_d)
        assert parse_dollar(result["realized_closed"]) == Decimal("0")


def test_boundary_identity_property(golden_history: list[dict]) -> None:
    periods = [
        (None, "2026-01-01"),
        ("2026-01-01", "2026-01-01"),
        ("2026-01-02", "2026-01-02"),
        ("2026-01-01", "2026-01-02"),
    ]
    for from_date, to_date in periods:
        between = realized_between(golden_history, from_date, to_date)
        end_events = [
            e for e in golden_history if to_date is None or e["trade_date"] <= to_date
        ]
        end_full = parse_dollar(realized_summary(end_events)["realized_closed"])
        if from_date is None:
            start_full = Decimal("0")
        else:
            start_cutoff = "2025-12-31" if from_date == "2026-01-01" else "2026-01-01"
            start_events = [e for e in golden_history if e["trade_date"] <= start_cutoff]
            start_full = parse_dollar(realized_summary(start_events)["realized_closed"])
        assert parse_dollar(between["realized_closed"]) == end_full - start_full


def _patch_analytics_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    from db import trades

    def _with_db(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("db_path", db_path)
            return fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(
        "db.analytics.list_trade_events", _with_db(trades.list_trade_events)
    )
    monkeypatch.setattr(
        "db.analytics.trade_summary_stats", _with_db(trades.trade_summary_stats)
    )
    monkeypatch.setattr(
        "db.analytics.daily_trade_stats", _with_db(trades.daily_trade_stats)
    )
    monkeypatch.setattr(
        "db.trades.symbol_trade_stats", _with_db(trades.symbol_trade_stats)
    )
    monkeypatch.setattr(
        "db.trades.get_trade_account", _with_db(trades.get_trade_account)
    )


@pytest.fixture
def scoped_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "period_realized.db"
    _patch_analytics_db(monkeypatch, db_path)

    real = create_trade_account("Schwab", "schwab", db_path=db_path)
    paper = create_trade_account("Paper", "alpaca", db_path=db_path)

    import_trade_events(
        real["id"],
        [
            _parsed(
                "2026-01-01",
                "X",
                "buy",
                Decimal("100"),
                amount=Decimal("-1000"),
                key="r-b",
            ),
            _parsed(
                "2026-01-02",
                "X",
                "sell",
                Decimal("100"),
                amount=Decimal("1200"),
                key="r-s",
            ),
        ],
        source="test",
        db_path=db_path,
    )
    import_trade_events(
        paper["id"],
        [
            _parsed(
                "2026-01-02",
                "PAPERX",
                "buy",
                Decimal("10"),
                amount=Decimal("-500"),
                key="p-b",
            ),
            _parsed(
                "2026-01-03",
                "PAPERX",
                "sell",
                Decimal("10"),
                amount=Decimal("600"),
                key="p-s",
            ),
        ],
        source="test",
        db_path=db_path,
    )

    return {"db_path": db_path, "real_id": real["id"], "paper_id": paper["id"]}


def test_dashboard_day2_headline_realized(scoped_db: dict[str, Any]) -> None:
    dash = performance_dashboard(
        account_id=scoped_db["real_id"],
        period="custom",
        custom_from="2026-01-02",
        custom_to="2026-01-02",
    )
    assert parse_dollar(dash["realized_closed"]) == Decimal("200")
    assert parse_dollar(dash["in_window_recon"]["realized_closed"]) == Decimal("0")
    day2 = next(d for d in dash["daily_realized"] if d["date"] == "2026-01-02")
    assert parse_dollar(day2["realized"]) == Decimal("200")
    assert parse_dollar(day2["net_amount"]) == Decimal("1200")


def test_real_scope_firewall_realized_between(scoped_db: dict[str, Any]) -> None:
    dash = performance_dashboard(
        account_id=scoped_db["real_id"],
        period="all",
    )
    assert parse_dollar(dash["realized_closed"]) == Decimal("200")
    assert "PAPERX" not in str(dash)

    paper_dash = performance_dashboard(
        account_id=scoped_db["paper_id"],
        paper_only=True,
        period="all",
    )
    assert parse_dollar(paper_dash["realized_closed"]) == Decimal("100")
    assert "X" not in str(paper_dash["daily_realized"])


def test_retraded_symbol_counts_in_period_win_rate() -> None:
    """A symbol closed BEFORE the window that re-opens and re-closes inside it
    must count in the period win rate by its NEW round trip (delta), not be
    silently skipped (adversarial-review find)."""
    history = [
        _event("2026-06-01", "X", "buy", Decimal(10), amount=Decimal(-1000), key="b1"),
        _event("2026-06-02", "X", "sell", Decimal(10), amount=Decimal(1100), key="s1"),
        # re-trade inside the window: +200 new round trip
        _event("2026-06-10", "X", "buy", Decimal(10), amount=Decimal(-1000), key="b2"),
        _event("2026-06-11", "X", "sell", Decimal(10), amount=Decimal(1200), key="s2"),
    ]
    period = realized_between(history, "2026-06-10", "2026-06-11")
    assert parse_dollar(period["realized_closed"]) == Decimal(200)
    assert period["closed_win_rate"] == 1.0

    # An untouched previously-closed symbol still doesn't count.
    quiet = realized_between(history, "2026-06-05", "2026-06-06")
    assert parse_dollar(quiet["realized_closed"]) == Decimal(0)
    assert quiet["closed_win_rate"] is None


def test_never_flat_books_at_final_close_no_double_count() -> None:
    """Round trip on day D while still short never flat until final cover — no double count."""
    history = [
        _event(
            "2026-01-01",
            "S",
            "sell",
            Decimal("6"),
            amount=Decimal("600"),
            key="s0",
            row_id=1,
        ),
        _event(
            "2026-03-15",
            "S",
            "buy",
            Decimal("6"),
            amount=Decimal("-300"),
            key="b1",
            row_id=2,
        ),
        _event(
            "2026-03-15",
            "S",
            "sell",
            Decimal("6"),
            amount=Decimal("800"),
            key="s1",
            row_id=3,
        ),
        _event(
            "2026-09-01",
            "S",
            "buy",
            Decimal("6"),
            amount=Decimal("-600"),
            key="b2",
            row_id=4,
        ),
    ]
    day_d = "2026-03-15"
    close_date = "2026-09-01"
    day_after_d = "2026-03-16"

    day_d_realized = parse_dollar(
        realized_between(history, day_d, day_d)["realized_closed"]
    )
    tail_realized = parse_dollar(
        realized_between(history, day_after_d, close_date)["realized_closed"]
    )
    covering = parse_dollar(
        realized_between(history, day_d, close_date)["realized_closed"]
    )
    all_time = parse_dollar(
        realized_between(history, None, close_date)["realized_closed"]
    )

    assert day_d_realized == Decimal("0")
    assert day_d_realized + tail_realized == covering
    assert covering == all_time == Decimal("500")


def test_lifetime_dump_splits_across_windows() -> None:
    """Completed round trips book in the window they go flat, not at final close."""
    history = [
        _event(
            "2024-06-01",
            "T",
            "buy",
            Decimal("10"),
            amount=Decimal("-1000"),
            key="t-b1",
            row_id=1,
        ),
        _event(
            "2024-06-02",
            "T",
            "sell",
            Decimal("10"),
            amount=Decimal("2000"),
            key="t-s1",
            row_id=2,
        ),
        _event(
            "2026-07-01",
            "T",
            "buy",
            Decimal("5"),
            amount=Decimal("-500"),
            key="t-b2",
            row_id=3,
        ),
        _event(
            "2026-07-02",
            "T",
            "sell",
            Decimal("5"),
            amount=Decimal("800"),
            key="t-s2",
            row_id=4,
        ),
    ]
    early = parse_dollar(
        realized_between(history, "2024-06-01", "2024-06-02")["realized_closed"]
    )
    middle = parse_dollar(
        realized_between(history, "2025-01-01", "2025-01-31")["realized_closed"]
    )
    later = parse_dollar(
        realized_between(history, "2026-07-01", "2026-07-02")["realized_closed"]
    )
    covering = parse_dollar(
        realized_between(history, "2024-06-01", "2026-07-02")["realized_closed"]
    )

    assert early == Decimal("1000")
    assert middle == Decimal("0")
    assert later == Decimal("300")
    assert covering == Decimal("1300")


def _daily_sum_equals_period(
    history: list[dict],
    from_date: str,
    to_date: str,
) -> None:
    days = sorted({e["trade_date"] for e in history if from_date <= e["trade_date"] <= to_date})
    daily = daily_boundary_realized_stats(
        history,
        [{"date": d, "net_amount": "0", "event_count": 0} for d in days],
        from_date=from_date,
        to_date=to_date,
    )
    per_day_total = sum(parse_dollar(row["realized"]) for row in daily)
    period = parse_dollar(
        realized_between(history, from_date, to_date)["realized_closed"]
    )
    assert per_day_total == period


@pytest.mark.parametrize(
    ("history", "from_date", "to_date"),
    [
        (
            [
                _event(
                    "2026-01-01",
                    "X",
                    "buy",
                    Decimal("100"),
                    amount=Decimal("-1000"),
                    key="x-b",
                    row_id=1,
                ),
                _event(
                    "2026-01-02",
                    "X",
                    "sell",
                    Decimal("100"),
                    amount=Decimal("1200"),
                    key="x-s",
                    row_id=2,
                ),
            ],
            "2026-01-01",
            "2026-01-02",
        ),
        (
            [
                _event("2026-01-01", "S", "sell", Decimal("6"), amount=Decimal("600"), key="s0"),
                _event("2026-03-15", "S", "buy", Decimal("6"), amount=Decimal("-300"), key="b1"),
                _event("2026-03-15", "S", "sell", Decimal("6"), amount=Decimal("800"), key="s1"),
                _event("2026-09-01", "S", "buy", Decimal("6"), amount=Decimal("-600"), key="b2"),
            ],
            "2026-01-01",
            "2026-09-01",
        ),
        (
            [
                _event("2024-06-01", "T", "buy", Decimal("10"), amount=Decimal("-1000"), key="t-b1"),
                _event("2024-06-02", "T", "sell", Decimal("10"), amount=Decimal("2000"), key="t-s1"),
                _event("2026-07-01", "T", "buy", Decimal("5"), amount=Decimal("-500"), key="t-b2"),
                _event("2026-07-02", "T", "sell", Decimal("5"), amount=Decimal("800"), key="t-s2"),
            ],
            "2024-06-01",
            "2026-07-02",
        ),
    ],
)
def test_daily_sum_equals_period_all_fixtures(
    history: list[dict],
    from_date: str,
    to_date: str,
) -> None:
    _daily_sum_equals_period(history, from_date, to_date)


def test_open_core_intra_window_round_trip_contributes_zero() -> None:
    history = [
        _event(
            "2026-01-01",
            "CORE",
            "buy",
            Decimal("100"),
            amount=Decimal("-5000"),
            key="c-b0",
            row_id=1,
        ),
        _event(
            "2026-06-01",
            "CORE",
            "buy",
            Decimal("50"),
            amount=Decimal("-2500"),
            key="c-b1",
            row_id=2,
        ),
        _event(
            "2026-06-02",
            "CORE",
            "sell",
            Decimal("50"),
            amount=Decimal("3000"),
            key="c-s1",
            row_id=3,
        ),
    ]
    result = realized_between(history, "2026-06-01", "2026-06-02")
    assert parse_dollar(result["realized_closed"]) == Decimal("0")


def test_all_history_includes_prior_flat_excludes_open_tail() -> None:
    history = [
        _event(
            "2026-01-01",
            "M",
            "buy",
            Decimal("10"),
            amount=Decimal("-1000"),
            key="m-b1",
            row_id=1,
        ),
        _event(
            "2026-01-02",
            "M",
            "sell",
            Decimal("10"),
            amount=Decimal("1500"),
            key="m-s1",
            row_id=2,
        ),
        _event(
            "2026-06-01",
            "M",
            "buy",
            Decimal("20"),
            amount=Decimal("-2000"),
            key="m-b2",
            row_id=3,
        ),
    ]
    all_history = parse_dollar(
        realized_between(history, None, "2026-06-01")["realized_closed"]
    )
    closed_only = parse_dollar(
        realized_summary(history)["realized_closed"]
    )

    assert all_history == Decimal("500")
    assert closed_only == Decimal("0")
