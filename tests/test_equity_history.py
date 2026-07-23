"""Tests for db.equity_history (snapshot-backed equity curves + window P&L)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.base import connect
from db.equity_history import equity_history
from db.sqlite import init_db
from db.trades import create_trade_account

NOW = datetime(2026, 7, 14, 20, 0, 0, tzinfo=timezone.utc)  # 16:00 ET


def _seed_poll(
    db_path,
    account_id: str,
    captured_at: datetime,
    portfolio_value: float,
    unrealized: list[float] = (),
) -> None:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO poll_runs (captured_at, broker, account_id) VALUES (?, 'alpaca', ?)",
            (captured_at.isoformat(), account_id),
        )
        run_id = cur.lastrowid
        conn.execute(
            """
            INSERT INTO account_snapshots (
                poll_run_id, account_id, status, cash, buying_power, portfolio_value
            ) VALUES (?, 'uuid-ignored', 'ACTIVE', 0, 0, ?)
            """,
            (run_id, portfolio_value),
        )
        for upl in unrealized:
            conn.execute(
                """
                INSERT INTO position_snapshots (
                    poll_run_id, symbol, qty, side, market_value, unrealized_pl,
                    avg_entry_price, current_price
                ) VALUES (?, 'TEST', 1, 'long', 0, ?, 0, 0)
                """,
                (run_id, upl),
            )
        conn.commit()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    return path


def _utc(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


def test_all_paper_sums_accounts_and_decomposes_pnl(db):
    a = create_trade_account("P1", "alpaca", db_path=db)["id"]
    b = create_trade_account("P2", "alpaca", db_path=db)["id"]
    # Window start state: A=100 (upl 0), B=200 (upl 10)
    _seed_poll(db, a, _utc(13), 100.0, [0.0])
    _seed_poll(db, b, _utc(13), 200.0, [10.0])
    # End state: A=110 (upl 5), B=230 (upl 25)
    _seed_poll(db, a, _utc(19), 110.0, [5.0])
    _seed_poll(db, b, _utc(19), 230.0, [15.0, 10.0])

    out = equity_history("all_paper", "1W", db_path=db, now=NOW)
    assert out is not None
    assert out["start_equity"] == 300.0
    assert out["end_equity"] == 340.0
    assert out["change"] == 40.0
    assert out["unrealized_change"] == 20.0
    assert out["realized"] == 20.0
    # Both buckets have both accounts → summed points at both ends.
    equities = [p["equity"] for p in out["points"]]
    assert equities[0] == 300.0
    assert equities[-1] == 340.0
    assert out["points"][0]["profit_loss"] == 0.0
    assert out["points"][-1]["profit_loss"] == 40.0


def test_single_account_scope(db):
    a = create_trade_account("P1", "alpaca", db_path=db)["id"]
    b = create_trade_account("P2", "alpaca", db_path=db)["id"]
    _seed_poll(db, a, _utc(13), 100.0, [2.0])
    _seed_poll(db, a, _utc(19), 110.0, [7.0])
    _seed_poll(db, b, _utc(13), 999.0)  # other account must not leak in

    out = equity_history(a, "1W", db_path=db, now=NOW)
    assert out["change"] == 10.0
    assert out["unrealized_change"] == 5.0
    assert out["realized"] == 5.0
    assert all(p["equity"] <= 110.0 for p in out["points"])


def test_1d_window_starts_at_midnight_eastern(db):
    a = create_trade_account("P1", "alpaca", db_path=db)["id"]
    # 03:00 UTC = 23:00 ET on Jul 13 — previous ET day, must be excluded.
    _seed_poll(db, a, _utc(3), 50.0)
    # 05:00 UTC = 01:00 ET Jul 14 — first poll of the ET day.
    _seed_poll(db, a, _utc(5), 100.0)
    _seed_poll(db, a, _utc(19), 130.0)

    out = equity_history(a, "1D", db_path=db, now=NOW)
    assert out["start_equity"] == 100.0
    assert out["change"] == 30.0


def test_partial_bucket_dropped_when_account_missing(db):
    a = create_trade_account("P1", "alpaca", db_path=db)["id"]
    b = create_trade_account("P2", "alpaca", db_path=db)["id"]
    _seed_poll(db, a, _utc(13), 100.0)
    _seed_poll(db, b, _utc(13), 200.0)
    # Only A reports later — that bucket must not appear as a fake 100 drop.
    _seed_poll(db, a, _utc(19), 105.0)

    out = equity_history("all_paper", "1W", db_path=db, now=NOW)
    equities = [p["equity"] for p in out["points"]]
    assert equities == [300.0]


def test_non_paper_and_unknown_scopes_return_none(db):
    create_trade_account("Schwab", "schwab", db_path=db)
    schwab_id = create_trade_account("Schwab2", "schwab", db_path=db)["id"]
    assert equity_history(schwab_id, "1W", db_path=db, now=NOW) is None
    assert equity_history("acct_nope", "1W", db_path=db, now=NOW) is None
    assert equity_history("all_real", "1W", db_path=db, now=NOW) is None


def test_empty_window_returns_empty_payload(db):
    a = create_trade_account("P1", "alpaca", db_path=db)["id"]
    _seed_poll(db, a, _utc(3, day=1), 50.0)  # far outside a 1D window

    out = equity_history(a, "1D", db_path=db, now=NOW)
    assert out["points"] == []
    assert out["change"] == 0.0
