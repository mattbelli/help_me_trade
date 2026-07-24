"""Intraday book watch — trigger cut-lines, the wakeup budget, and the dedup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from db.book_watch import claim_watch_event, events_for_day, fired_today
from research.book_watch import (
    BOOK_WATCH_DAILY_BUDGET,
    _book_snapshot,
    _extract_verdict,
    evaluate_triggers,
)
from research.books import BOOKS, register_book_candidates

_ET = ZoneInfo("America/New_York")
# A regular Wednesday session, mid-afternoon.
NOW = datetime(2026, 7, 22, 18, 30, tzinfo=timezone.utc)
AS_OF = "2026-07-21T21:00:00+00:00"
BOOK = BOOKS["deepseek"]


def _position(symbol: str, avg_entry: float, current: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": 10,
        "side": "long",
        "market_value": current * 10,
        "unrealized_pl": (current - avg_entry) * 10,
        "avg_entry_price": avg_entry,
        "current_price": current,
    }


def _snapshot(*positions: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": BOOK.account_id,
        "captured_at": NOW.isoformat(),
        "positions": list(positions),
        "open_orders": [],
    }


def _register_plan(db: Path, symbol: str, *, stop: str, targets: list[str]) -> None:
    register_book_candidates(
        "deepseek",
        [
            {
                "symbol": symbol,
                "direction": "long",
                "entry_zone": {"low": "99", "high": "100"},
                "stop": stop,
                "targets": targets,
                "thesis": "t",
                "plan_horizon": "5d",
                "advanced": True,
            }
        ],
        as_of=AS_OF,
        db_path=db,
    )


def test_price_triggers_fire_on_their_own_cut_lines(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _register_plan(db, "AAPL", stop="95", targets=["130"])   # stop is 1.05% away
    _register_plan(db, "MSFT", stop="50", targets=["130"])   # far from stop, deep drawdown
    _register_plan(db, "NVDA", stop="50", targets=["105"])   # first target in reach

    triggers = evaluate_triggers(
        BOOK,
        _snapshot(
            _position("AAPL", 100.0, 96.0),
            _position("MSFT", 100.0, 91.0),
            _position("NVDA", 100.0, 104.5),
        ),
        now=NOW,
        db_path=db,
    )
    fired = {(t.symbol, t.trigger) for t in triggers}
    assert ("AAPL", "stop_proximity") in fired
    assert ("MSFT", "drawdown") in fired
    assert ("NVDA", "target_proximity") in fired
    # AAPL is only 4% under its entry — the drawdown line is 8% and must not trip.
    assert ("AAPL", "drawdown") not in fired
    # Most urgent first: the budget must spend on the stop, not the target.
    assert triggers[0].trigger == "stop_proximity"


def test_quiet_position_trips_nothing(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _register_plan(db, "AAPL", stop="80", targets=["130"])
    assert (
        evaluate_triggers(
            BOOK, _snapshot(_position("AAPL", 100.0, 101.0)), now=NOW, db_path=db
        )
        == []
    )


def test_position_without_a_plan_still_gets_the_drawdown_trigger(tmp_path: Path) -> None:
    """No ledger plan (a manual take, or the advisory book) means no stop/target
    reading — but the book's own average entry is always known."""
    db = tmp_path / "t.db"
    triggers = evaluate_triggers(
        BOOK, _snapshot(_position("TSLA", 100.0, 85.0)), now=NOW, db_path=db
    )
    assert [(t.symbol, t.trigger) for t in triggers] == [("TSLA", "drawdown")]


def test_snapshot_from_another_account_is_refused(tmp_path: Path, monkeypatch: Any) -> None:
    """get_latest_snapshot falls back to the newest run for the broker when an
    account has never polled — that must never become this book's positions."""
    import db.sqlite as sqlite_mod

    monkeypatch.setattr(
        sqlite_mod,
        "get_latest_snapshot",
        lambda *a, **k: {**_snapshot(), "account_id": "acct_someone_else"},
    )
    assert _book_snapshot(BOOK, tmp_path / "t.db") is None


def test_budget_stops_at_the_ceiling_but_still_leaves_a_row(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    day = "2026-07-22"
    for i in range(BOOK_WATCH_DAILY_BUDGET):
        assert claim_watch_event(
            book_id="deepseek", market_day=day, symbol=f"S{i}",
            trigger="drawdown", decision="fired", db_path=db,
        )
    assert fired_today("deepseek", day, db_path=db) == BOOK_WATCH_DAILY_BUDGET

    # The refused trigger is still written — "quiet" and "out of budget" are
    # different numbers on the page.
    assert claim_watch_event(
        book_id="deepseek", market_day=day, symbol="EXTRA",
        trigger="drawdown", decision="budget_dropped", db_path=db,
    )
    assert fired_today("deepseek", day, db_path=db) == BOOK_WATCH_DAILY_BUDGET
    decisions = [e["decision"] for e in events_for_day("deepseek", day, db_path=db)]
    assert decisions.count("budget_dropped") == 1


def test_same_trigger_twice_in_a_day_is_one_event(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    day = "2026-07-22"
    first = claim_watch_event(
        book_id="deepseek", market_day=day, symbol="AAPL",
        trigger="stop_proximity", decision="fired", db_path=db,
    )
    second = claim_watch_event(
        book_id="deepseek", market_day=day, symbol="AAPL",
        trigger="stop_proximity", decision="fired", db_path=db,
    )
    assert first and second is None
    # A DIFFERENT trigger on the same name is a different event.
    assert claim_watch_event(
        book_id="deepseek", market_day=day, symbol="AAPL",
        trigger="news", decision="fired", db_path=db,
    )
    assert len(events_for_day("deepseek", day, db_path=db)) == 2


def _rest_entry(db: Path, symbol: str, *, submitted: str, limit: float) -> None:
    """An approved buy the broker still holds unfilled."""
    from db.base import connect as _connect
    from db.order_executions import init_execution_tables

    init_execution_tables(db)
    with _connect(db) as conn:
        conn.execute(
            "INSERT INTO order_executions (id, proposal_id, symbol, side, qty, "
            "order_type, limit_price, execution_mode, status, broker, "
            "submitted_at, created_at, updated_at, account_id) "
            "VALUES (?, ?, ?, 'buy', 10, 'limit', ?, 'paper', 'submitted', "
            "'alpaca', ?, ?, ?, ?)",
            (
                f"exec_{symbol}", f"p_{symbol}", symbol, limit,
                submitted, submitted, submitted, BOOK.account_id,
            ),
        )
        conn.commit()


def test_resting_entry_fires_only_on_the_day_it_crosses_and_its_multiples(
    tmp_path: Path,
) -> None:
    """A GTC entry is invisible everywhere else: not a position, not a pending
    proposal. It must surface — without taxing the budget every single day."""
    db = tmp_path / "t.db"
    # Submitted Fri 2026-07-17; NOW is Wed 2026-07-22 → 3 trading days resting.
    _rest_entry(db, "XLF", submitted="2026-07-17T22:00:00+00:00", limit=55.5)
    triggers = evaluate_triggers(BOOK, _snapshot(), now=NOW, db_path=db)
    assert [(t.symbol, t.trigger) for t in triggers] == [("XLF", "resting_entry")]

    # One trading day later (4 days resting) it stays quiet; day 6 speaks again.
    day4 = NOW.replace(day=23)
    assert evaluate_triggers(BOOK, _snapshot(), now=day4, db_path=db) == []


class _StubLLM:
    """Sync .complete() like the real provider seam (called via to_thread)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, messages: Any, max_tokens: int = 0) -> str:
        self.calls += 1
        return self.reply


def _arm_watch(monkeypatch: Any, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Point the watcher at a fabricated snapshot, open the market, and capture
    deliveries instead of pushing to a real phone."""
    import research.book_driver as driver_mod
    import research.book_watch as watch_mod

    delivered: list[dict[str, Any]] = []

    async def _capture(report: dict[str, Any], book_name: str, db_path: Any) -> None:
        delivered.append(report)

    # is_market_open is imported at CALL time inside run_book_watch, so the
    # patch has to land on trading.market_hours (the caller does that).
    monkeypatch.setattr(watch_mod, "_book_snapshot", lambda book, db: snapshot)
    monkeypatch.setattr(driver_mod, "deliver_report", _capture)
    return delivered


def test_wakeup_writes_a_report_and_spends_exactly_one_budget_slot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import trading.market_hours as hours_mod

    db = tmp_path / "t.db"
    _register_plan(db, "AAPL", stop="95", targets=["130"])
    snapshot = _snapshot(_position("AAPL", 100.0, 96.0))
    delivered = _arm_watch(monkeypatch, snapshot)
    monkeypatch.setattr(hours_mod, "is_market_open", lambda now=None: True)
    llm = _StubLLM('Stop is close.\n```json\n{"verdict": "exit", "reason": "stop broken"}\n```')

    import asyncio

    from research.book_watch import run_book_watch

    out = asyncio.run(run_book_watch("deepseek", now=NOW, db_path=db, llm=llm))
    assert out["status"] == "ok" and len(out["fired"]) == 1
    assert out["fired"][0]["verdict"] == "exit"
    assert llm.calls == 1 and len(delivered) == 1

    day = NOW.astimezone(_ET).date().isoformat()
    events = events_for_day("deepseek", day, db_path=db)
    assert len(events) == 1
    assert events[0]["decision"] == "fired" and events[0]["verdict"] == "exit"
    assert events[0]["report_id"]

    # Second pass, same trigger, same day: the dedup must spend nothing.
    again = asyncio.run(run_book_watch("deepseek", now=NOW, db_path=db, llm=llm))
    assert again["deduped"] == 1 and again["fired"] == []
    assert llm.calls == 1
    assert fired_today("deepseek", day, db_path=db) == 1


def test_stale_snapshot_never_wakes_anyone(tmp_path: Path, monkeypatch: Any) -> None:
    import trading.market_hours as hours_mod

    db = tmp_path / "t.db"
    _register_plan(db, "AAPL", stop="95", targets=["130"])
    stale = {**_snapshot(_position("AAPL", 100.0, 96.0)), "captured_at": "2026-07-22T12:00:00+00:00"}
    _arm_watch(monkeypatch, stale)
    monkeypatch.setattr(hours_mod, "is_market_open", lambda now=None: True)
    llm = _StubLLM("should never be called")

    import asyncio

    from research.book_watch import run_book_watch

    out = asyncio.run(run_book_watch("deepseek", now=NOW, db_path=db, llm=llm))
    assert out["status"] == "skipped" and out["reason"] == "stale_snapshot"
    assert llm.calls == 0
    assert events_for_day("deepseek", NOW.astimezone(_ET).date().isoformat(), db_path=db) == []


def test_unreadable_note_is_never_an_all_clear() -> None:
    assert _extract_verdict("no block here")[0] == "watch"
    assert _extract_verdict('```json\n{"verdict": "sell"}\n```')[0] == "watch"
    assert _extract_verdict('```json\n{"verdict": "exit", "reason": "r"}\n```') == ("exit", "r")
