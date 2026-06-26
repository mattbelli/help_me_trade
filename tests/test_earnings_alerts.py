"""Earnings milestone push alerts — api/main._earnings_alert_once + db dedup."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

_ET = ZoneInfo("America/New_York")
_TODAY = date(2026, 6, 24)


def _init_db(tmp_path: Path) -> Path:
    from db.earnings_alerts import init_earnings_alert_tables
    from db.multi_account import migrate_multi_account
    from db.order_executions import init_execution_tables
    from db.paper_orders import init_paper_order_tables
    from db.sqlite import init_db
    from db.trades import init_trade_tables

    db = tmp_path / "earnings_alerts.db"
    init_db(db)
    init_trade_tables(db)
    init_execution_tables(db)
    init_paper_order_tables(db)
    migrate_multi_account(db)
    init_earnings_alert_tables(db)
    return db


def _freeze_today(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 24, 10, 0, tzinfo=_ET)

    class _FixedDatetime:
        @staticmethod
        def now(tz=None):
            if tz is not None:
                return fixed.astimezone(tz)
            return fixed.replace(tzinfo=None)

    monkeypatch.setattr("api.main.datetime", _FixedDatetime)


def _mock_calendar(
    monkeypatch: pytest.MonkeyPatch,
    *,
    next_date: str | None,
    last_date: str | None = None,
) -> None:
    monkeypatch.setattr(
        "api.main.last_earnings_date",
        lambda sym, on_date, **kw: last_date,
    )
    monkeypatch.setattr(
        "api.main.next_earnings_date",
        lambda sym, on_date, **kw: next_date,
    )


@pytest.mark.asyncio
async def test_t14_fires_once_and_dedups_same_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    _freeze_today(monkeypatch)
    _mock_calendar(monkeypatch, next_date="2026-07-08")
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["MU"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    call = push_mock.await_args
    assert call.kwargs["data"]["milestone"] == "t14"
    assert call.kwargs["data"]["symbol"] == "MU"
    assert call.kwargs["data"]["earnings_date"] == "2026-07-08"
    assert "2 weeks" in call.kwargs["body"]

    push_mock.reset_mock()
    await _earnings_alert_once()
    push_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_t1_fires_one_day_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    _freeze_today(monkeypatch)
    _mock_calendar(monkeypatch, next_date="2026-06-25")
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["AAPL"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("api.main.earnings_hour", lambda sym, d, **kw: "amc")

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["data"]["milestone"] == "t1"
    assert ", AMC" in push_mock.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_t0_fires_day_of_with_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    _freeze_today(monkeypatch)
    _mock_calendar(
        monkeypatch,
        next_date="2026-07-31",
        last_date=_TODAY.isoformat(),
    )
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["NVDA"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("api.main.earnings_hour", lambda sym, d, **kw: "bmo")

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["data"]["milestone"] == "t0"
    assert "TODAY" in push_mock.await_args.kwargs["body"]
    assert "before open" in push_mock.await_args.kwargs["body"]


@pytest.mark.asyncio
async def test_no_alert_when_more_than_14_days_or_past(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    _freeze_today(monkeypatch)
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["MSFT"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    _mock_calendar(monkeypatch, next_date="2026-07-15")
    await _earnings_alert_once()
    push_mock.assert_not_awaited()

    _mock_calendar(monkeypatch, next_date="2026-06-20")
    await _earnings_alert_once()
    push_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_earnings_date_rearms_milestones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _init_db(tmp_path)
    _freeze_today(monkeypatch)
    _mock_calendar(monkeypatch, next_date="2026-07-08")
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["MU"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["data"]["earnings_date"] == "2026-07-08"

    push_mock.reset_mock()
    await _earnings_alert_once()
    push_mock.assert_not_awaited()

    _mock_calendar(monkeypatch, next_date="2026-07-07")
    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["data"]["earnings_date"] == "2026-07-07"
    assert push_mock.await_args.kwargs["data"]["milestone"] == "t14"


@pytest.mark.asyncio
async def test_t14_catches_up_when_loop_lands_on_day_13(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """<=14 window: t14 still fires if the loop missed the exact day-14 boundary."""
    db = _init_db(tmp_path)
    day13 = datetime(2026, 6, 25, 10, 0, tzinfo=_ET)

    class _Day13Datetime:
        @staticmethod
        def now(tz=None):
            if tz is not None:
                return day13.astimezone(tz)
            return day13.replace(tzinfo=None)

    monkeypatch.setattr("api.main.datetime", _Day13Datetime)
    _mock_calendar(monkeypatch, next_date="2026-07-08")
    monkeypatch.setattr("api.main._all_watchlist_symbols", lambda: ["MU"])
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)

    push_mock = AsyncMock(return_value=1)
    monkeypatch.setattr("signals.push.send_alert_push", push_mock)

    from api.main import _earnings_alert_once

    await _earnings_alert_once()
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["data"]["milestone"] == "t14"
