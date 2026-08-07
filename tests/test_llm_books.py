"""LLM paper-books lane registration (research/books.py) + origin-law consistency."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.idea_engine import IDEA_COOLDOWN_MIN
from db.idea_outcomes import JOURNAL_ORIGINS, list_ledger_rows
from research.books import (
    ALL_BOOK_ORIGINS,
    BOOK_HEADLINE_ORIGINS,
    BOOK_JOURNAL_ORIGINS,
    BOOKS,
    register_book_candidates,
)

AS_OF = "2026-07-23T21:00:00+00:00"


def _plan(symbol: str, **extra: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "direction": "long",
        "entry_zone": {"low": "99", "high": "100"},
        "stop": "95",
        "targets": ["105"],
        "thesis": "test thesis",
        "plan_horizon": "5d",
        **extra,
    }


def _origins(db: Path, origin: str) -> list[str]:
    return [r["symbol"] for r in list_ledger_rows(origin=origin, db_path=db)["rows"]]


def test_solo_book_advanced_vs_considered_lanes(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "deepseek",
        [_plan("AAPL", advanced=True), _plan("MSFT")],
        as_of=AS_OF,
        db_path=db,
    )
    assert len(out["registered"]) == 2 and out["skipped"] == 0
    assert _origins(db, "book:deepseek") == ["AAPL"]
    assert _origins(db, "book:deepseek/considered") == ["MSFT"]


def test_panel_advanced_lands_in_seat_and_headline(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "panel",
        [_plan("NVDA", seat="bull", advanced=True), _plan("TSLA", seat="bear")],
        as_of=AS_OF,
        db_path=db,
    )
    assert len(out["registered"]) == 3 and out["skipped"] == 0
    assert _origins(db, "book:panel/bull") == ["NVDA"]
    assert _origins(db, "book:panel") == ["NVDA"]
    assert _origins(db, "book:panel/bear") == ["TSLA"]


def test_panel_invalid_seat_skips_unless_advanced(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "panel",
        [_plan("AMD", seat="quant"), _plan("META", seat="quant", advanced=True)],
        as_of=AS_OF,
        db_path=db,
    )
    # AMD has no valid lane; META still reaches the headline lane.
    assert out["skipped"] == 1
    assert _origins(db, "book:panel") == ["META"]


def test_malformed_candidates_skip_without_dropping_batch(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "fable",
        ["not-a-dict", {"symbol": ""}, _plan("SPY", advanced=True)],
        as_of=AS_OF,
        db_path=db,
    )
    assert len(out["registered"]) == 1 and out["skipped"] == 2
    assert _origins(db, "book:fable") == ["SPY"]


def test_horizon_return_and_short_direction_register(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "fable",
        [
            {
                "symbol": "QQQ",
                "entry_ref_price": "500",
                "plan_horizon": "5d",
                "thesis": "drift",
                "advanced": True,
            },
            {
                "symbol": "IWM",
                "direction": "short",
                "entry_zone": {"low": "199", "high": "200"},
                "stop": "205",
                "targets": ["190"],
                "thesis": "fade",
                "plan_horizon": "5d",
                "advanced": True,
            },
        ],
        as_of=AS_OF,
        db_path=db,
    )
    assert len(out["registered"]) == 2 and out["skipped"] == 0
    rows = list_ledger_rows(origin="book:fable", db_path=db)["rows"]
    assert {r["symbol"] for r in rows} == {"QQQ", "IWM"}


def test_same_cycle_duplicate_dedups(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    first = register_book_candidates(
        "deepseek", [_plan("AAPL", advanced=True)], as_of=AS_OF, db_path=db
    )
    again = register_book_candidates(
        "deepseek", [_plan("AAPL", advanced=True)], as_of=AS_OF, db_path=db
    )
    assert len(first["registered"]) == 1
    assert len(again["registered"]) == 0 and again["skipped"] == 1


def test_matt_and_unknown_books_refuse_ideas(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for bad in ("matt", "nope"):
        try:
            register_book_candidates(bad, [_plan("AAPL")], db_path=db)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_pack_renders_on_empty_db_without_fabricating(tmp_path: Path) -> None:
    """Fail-soft contract: a cold DB yields honest 'unavailable/none' lines,
    never an exception and never a made-up number."""
    from research.books import build_book_pack

    for book_id in BOOKS:
        pack = build_book_pack(book_id, db_path=tmp_path / "t.db")
        assert pack.startswith(f"# Context pack — {BOOKS[book_id].name}")
        assert "no snapshot for this account yet" in pack
        assert "## Market frame" in pack
    # Matt + LLM is snapshot-only — its pack must not carry lanes or a mandate.
    matt_pack = build_book_pack("matt", db_path=tmp_path / "t.db")
    panel_pack = build_book_pack("panel", db_path=tmp_path / "t.db")
    assert "Your ledger lanes" not in matt_pack and "Your mandate" not in matt_pack
    assert "Your ledger lanes" in panel_pack and "Your mandate" in panel_pack


def test_pack_shows_the_stop_trigger_level(tmp_path: Path) -> None:
    """A resting stop is the book's only protection between cycles, so the pack
    must name the PRICE, not just that a stop exists (SERV_HANDOFF_reports 2026-08-06).
    Goes through the real save->read->render path: the level was dropped at the
    snapshot table, so a render-only assertion would pass while the agent stayed blind."""
    from brokers.base import Account, Order
    from db.sqlite import save_snapshot
    from research.books import build_book_pack

    db = tmp_path / "t.db"
    book = BOOKS["fable"]
    save_snapshot(
        Account(id="alp-1", status="ACTIVE", cash=1000.0, buying_power=1000.0,
                portfolio_value=1000.0),
        [],
        [
            Order(id="o1", symbol="AAPL", qty=10, side="sell", order_type="stop",
                  status="new", filled_qty=0, limit_price=None,
                  time_in_force="gtc", stop_price=180.0),
            Order(id="o2", symbol="MSFT", qty=5, side="buy", order_type="market",
                  status="new", filled_qty=0, limit_price=None),
        ],
        broker="alpaca",
        account_id=book.account_id,
        db_path=db,
    )

    pack = build_book_pack("fable", db_path=db)
    assert "stop $180.00" in pack
    assert "GTC" in pack
    # A market order has no level to report and must not grow a fabricated one.
    assert "sell 10 AAPL — stop $180.00" in pack
    assert "buy 5 MSFT (market" in pack


def test_origin_law_consistency() -> None:
    """The three modules that name book lanes can never drift apart."""
    assert BOOK_HEADLINE_ORIGINS == {"book:deepseek", "book:fable", "book:panel"}
    assert ALL_BOOK_ORIGINS == BOOK_HEADLINE_ORIGINS | BOOK_JOURNAL_ORIGINS
    # Every book lane has the nightly-cycle cooldown backstop (1440, the
    # fusion_mech precedent) — the defaultdict fallback (30) means a missing
    # entry fails here, not silently in production.
    for origin in sorted(ALL_BOOK_ORIGINS):
        assert IDEA_COOLDOWN_MIN[origin] == 1440, origin
    # Journal lanes are journal-class; headline lanes must never be.
    assert BOOK_JOURNAL_ORIGINS <= JOURNAL_ORIGINS
    assert not (BOOK_HEADLINE_ORIGINS & JOURNAL_ORIGINS)
    # Matt + LLM takes no machine ideas — no lane may exist for it.
    assert BOOKS["matt"].headline_origin is None
    assert not any("matt" in o for o in ALL_BOOK_ORIGINS)


# --- pausing a book (2026-07-30: Claude Panel held) -----------------------------


def test_paused_book_stays_fully_registered() -> None:
    """Pausing is a flag, not a deletion: the panel keeps its lanes, seats and
    origins so its ledger history stays readable and unpausing is one edit."""
    from research.books import ALL_BOOK_ORIGINS, BOOKS

    panel = BOOKS["panel"]
    assert panel.paused is True
    assert panel.seats and panel.headline_origin == "book:panel"
    assert all(f"book:panel/{seat}" in ALL_BOOK_ORIGINS for seat in panel.seats)
    # the books still running must not have been paused by accident
    assert sorted(b.book_id for b in BOOKS.values() if b.paused) == ["matt", "panel"]
    assert not BOOKS["deepseek"].paused and not BOOKS["fable"].paused


@pytest.mark.asyncio
async def test_dead_man_ignores_a_paused_book(monkeypatch) -> None:
    """Without this the pause costs a warning push every single night for a
    report nobody is expecting."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import api.main as api_main

    sent: list[dict] = []

    async def _notify(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("signals.notify.notify", _notify)
    monkeypatch.setattr("db.reports.report_exists_for_day", lambda *a, **k: False)
    monkeypatch.setattr(
        api_main, "datetime", _FrozenDatetime(datetime(2026, 7, 30, 21, 0, tzinfo=ZoneInfo("America/New_York")))
    )
    api_main._book_deadman_last_date.clear()

    await api_main._book_deadman_once()

    assert len(sent) == 1
    assert "Claude Panel" not in sent[0]["body"]
    assert "Deep Seek" in sent[0]["body"]  # the running books are still guarded


class _FrozenDatetime:
    """datetime stand-in whose now() is fixed (the module calls datetime.now(_ET))."""

    def __init__(self, moment):
        self._moment = moment

    def now(self, tz=None):
        return self._moment.astimezone(tz) if tz else self._moment

    def __getattr__(self, name):
        import datetime as _dt

        return getattr(_dt.datetime, name)


@pytest.mark.asyncio
async def test_paused_book_refuses_to_run_on_every_path(tmp_path: Path) -> None:
    """The pause lives at the _gate chokepoint, so it holds for the in-process
    job, a session submit, and a manual run alike — not just for whichever
    scheduler happens to launch that book."""
    from research.book_driver import run_book_cycle, submit_session_cycle

    db = tmp_path / "paused.db"

    # advisory book, in-process cycle
    assert (await run_book_cycle("matt", db_path=db)) == {
        "status": "skipped",
        "reason": "paused",
    }
    # session book, the path a manually-launched panel session would take
    submitted = await submit_session_cycle(
        "panel", "# report", {"candidates": []}, db_path=db
    )
    assert submitted["status"] == "skipped" and submitted["reason"] == "paused"
