"""Equity curve from recorded daily Schwab balances: complete days only, today only when fresh."""

from datetime import date, datetime
from pathlib import Path

import pytest

from db.base import connect
from db.schwab_history import init_schwab_history_tables
from signals.schwab_equity import _ET, _compute

TODAY = date(2026, 6, 10)


def _db(tmp_path: Path, rows: list[tuple[str, str, str | None]]) -> Path:
    db = tmp_path / "t.db"
    init_schwab_history_tables(db)
    with connect(db) as conn:
        conn.executemany(
            "INSERT INTO schwab_account_daily (snapshot_date, account_id, liquidation_value, captured_at) "
            "VALUES (?, ?, ?, 'x')",
            rows,
        )
        conn.commit()
    return db


def _live(monkeypatch: pytest.MonkeyPatch, *, stale: bool = False, fetched: date = TODAY) -> None:
    ts = datetime(fetched.year, fetched.month, fetched.day, 12, tzinfo=_ET).timestamp()
    live = {
        "authenticated": True,
        "stale": stale,
        "fetched_at": ts,
        "accounts": [{"id": "a", "liquidation_value": 110}, {"id": "b", "liquidation_value": 55}],
    }
    monkeypatch.setattr("brokers.schwab_accounts.get_schwab_accounts_sync", lambda: live)


def test_complete_days_only_plus_fresh_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path, [
        ("2026-06-08", "a", "100"), ("2026-06-08", "b", "50"),
        ("2026-06-09", "a", "105"),  # b missing: an incomplete aggregate day is omitted
    ])
    _live(monkeypatch)
    out = _compute(None, 30, db_path=db, today=TODAY)
    assert out["points"] == [{"date": "2026-06-08", "equity": 150.0},
                             {"date": "2026-06-10", "equity": 165.0}]
    assert out["history_start"] == "2026-06-08" and out["history_end"] == "2026-06-10"
    assert out["current_liquidation"] == 165.0


def test_stale_or_old_snapshot_is_not_todays_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path, [("2026-06-09", "a", "100"), ("2026-06-09", "b", "50")])
    _live(monkeypatch, stale=True)
    assert [p["date"] for p in _compute(None, 30, db_path=db, today=TODAY)["points"]] == ["2026-06-09"]
    _live(monkeypatch, fetched=date(2026, 6, 9))
    assert [p["date"] for p in _compute(None, 30, db_path=db, today=TODAY)["points"]] == ["2026-06-09"]


def test_ranges_and_single_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db(tmp_path, [
        ("2026-05-01", "a", "90"), ("2026-05-01", "b", "40"),
        ("2026-06-08", "a", "100"), ("2026-06-08", "b", "50"),
    ])
    _live(monkeypatch)
    assert [p["date"] for p in _compute(None, 30, range_key="1W", db_path=db, today=TODAY)["points"]] == [
        "2026-06-08", "2026-06-10"]
    assert len(_compute(None, 30, range_key="ALL", db_path=db, today=TODAY)["points"]) == 3
    assert len(_compute(None, 30, range_key="1D", db_path=db, today=TODAY)["points"]) == 2
    one = _compute("b", 30, range_key="ALL", db_path=db, today=TODAY)
    assert [p["equity"] for p in one["points"]] == [40.0, 50.0, 55.0]
