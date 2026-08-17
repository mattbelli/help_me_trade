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


@pytest.fixture
def seated_panel(monkeypatch: pytest.MonkeyPatch):
    """The panel runs SOLO since 2026-08-16 (local model). Seat routing is still real
    code (the Claude four-seat version in tools/book_session.sh) — pin a seated panel
    so these tests keep proving it."""
    from dataclasses import replace

    from research.books import BOOKS, PANEL_SEATS

    monkeypatch.setitem(BOOKS, "panel", replace(BOOKS["panel"], seats=PANEL_SEATS))


def test_panel_advanced_lands_in_seat_and_headline(tmp_path: Path, seated_panel) -> None:
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


def test_panel_invalid_seat_skips_unless_advanced(tmp_path: Path, seated_panel) -> None:
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
    assert "Your mechanism ledger" not in matt_pack
    assert "Your ledger lanes" in panel_pack and "Your mandate" in panel_pack
    assert "Your mechanism ledger" in panel_pack
    assert "Closed vocabulary" in panel_pack


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


_REPORT_SECTIONS = (
    "## TL;DR",
    "## Tonight's calls",
    "## What changed",
    "## Positions",
    "## Passed on",
    "## Market frame",
)


def test_both_book_prompts_carry_the_same_report_format() -> None:
    """Neither prompt said anything about report structure beyond a single clause,
    and the measured result was Deep Seek writing 0 headings in 6,804 chars while
    Fable wrote 1 h1 / 6 h2 / 2 h3 in 5,207. Pinned in BOTH: the books are compared
    against each other, so a format that drifts onto one of them is a confound."""
    from research.book_driver import _CHARTER

    fable = (Path(__file__).resolve().parents[1] / "research/book_prompts/fable.md").read_text()
    for section in _REPORT_SECTIONS:
        assert section in fable, f"missing from fable.md: {section}"
        assert section in _CHARTER, f"missing from _CHARTER: {section}"


def test_journal_instruction_is_not_a_markdown_heading_inside_json() -> None:
    """_CHARTER told the model to end its report with a '## Desk journal' heading
    *inside the json block* — a markdown heading inside JSON, when `journal` is
    already a plain string field of the contract."""
    from research.book_driver import _CHARTER

    assert "## Desk journal" not in _CHARTER
    assert "'journal' field of the decision block" in _CHARTER


def test_report_format_forbids_pasting_the_decision_block_into_prose() -> None:
    """Belt to the save-path braces: the body strip removes the block, and the
    prompt tells the analyst not to write one into the prose in the first place."""
    from research.book_driver import _CHARTER

    fable = (Path(__file__).resolve().parents[1] / "research/book_prompts/fable.md").read_text()
    assert "Never paste raw JSON" in _CHARTER and "Never paste raw JSON" in fable


def test_both_book_prompts_carry_the_same_order_change_contract() -> None:
    """The order-management verbs (move_stop / cancel_entry / trim) are pinned in
    BOTH prompts the same way the report format is — a verb one book knows and
    the other doesn't is a between-books confound, and a claim about what the
    desk refuses must match the code (trading/book_orders.py)."""
    from research.book_driver import _DECISION_CONTRACT

    fable = (Path(__file__).resolve().parents[1] / "research/book_prompts/fable.md").read_text()
    for needle in (
        "order_changes",
        "move_stop",
        "cancel_entry",
        "trim",
        "below the market, or it is refused",
        "the remainder stays stopped",
        "the block is what the desk executes",
    ):
        assert needle in fable, f"missing from fable.md: {needle}"
        assert needle in _DECISION_CONTRACT, f"missing from _DECISION_CONTRACT: {needle}"
    # The interim rule ("say so in holds_review, the founder acts") is gone.
    assert "founder acts on your request" not in fable
    assert "founder acts on your request" not in _DECISION_CONTRACT


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
    # 2026-08-16: the panel is RUNNING again as a SOLO local-model book
    # (tools/book_local_run.py); matt is the paused example now.
    assert panel.paused is False and not panel.seats
    assert panel.headline_origin == "book:panel"
    assert "book:panel/considered" in ALL_BOOK_ORIGINS
    assert BOOKS["matt"].paused is True
    # the books still running must not have been paused by accident
    assert sorted(b.book_id for b in BOOKS.values() if b.paused) == ["matt"]
    assert not BOOKS["deepseek"].paused and not BOOKS["fable"].paused


@pytest.mark.asyncio
async def test_dead_man_ignores_a_paused_book(monkeypatch) -> None:
    """Without this the pause costs a warning push every single night for a
    report nobody is expecting."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import api.ops_jobs as ops_jobs

    sent: list[dict] = []

    async def _notify(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("signals.notify.notify", _notify)
    monkeypatch.setattr("db.reports.report_exists_for_day", lambda *a, **k: False)
    monkeypatch.setattr(
        ops_jobs, "datetime", _FrozenDatetime(datetime(2026, 7, 30, 21, 0, tzinfo=ZoneInfo("America/New_York")))
    )
    # Pin the panel paused for this test — it is running again since 2026-08-16.
    from dataclasses import replace

    from research.books import BOOKS

    monkeypatch.setitem(BOOKS, "panel", replace(BOOKS["panel"], paused=True))
    ops_jobs._book_deadman_last_date.clear()

    await ops_jobs._book_deadman_once()

    assert len(sent) == 1
    assert "Local Panel" not in sent[0]["body"]
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
async def test_paused_book_refuses_to_run_on_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pause lives at the _gate chokepoint, so it holds for the in-process
    job, a session submit, and a manual run alike — not just for whichever
    scheduler happens to launch that book."""
    from dataclasses import replace

    from research.book_driver import run_book_cycle, submit_session_cycle
    from research.books import BOOKS

    # The panel runs again since 2026-08-16 — pin it paused to prove the session path.
    monkeypatch.setitem(BOOKS, "panel", replace(BOOKS["panel"], paused=True))
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


def _stub_frame_db_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence the frame's other DB readers so the assertions are about the two
    lines under test, not about which table a tmp DB happens to be missing."""
    import db.reports as reports_mod
    import signals.earnings_exhibit as exhibit_mod
    import signals.fusion.attention as attention_mod

    monkeypatch.setattr(attention_mod, "hot_lane_rows", lambda **_k: [])
    monkeypatch.setattr(reports_mod, "recent_reports_by_kind", lambda *_a, **_k: [])
    # A real earnings.db on the box must not leak today's 8-Ks into these tests.
    monkeypatch.setattr(
        exhibit_mod, "DEFAULT_RELEASES_DB_PATH", Path("/nonexistent/earnings.db")
    )


def test_market_frame_carries_the_macro_calendar_and_the_gamma_regime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both shipped to the `market` perception resource FIRST and to this pack only
    afterwards — and the pack is what a book reads before it asks anything. Measured
    on the 2026-08-11 packs: no CPI line on the eve of CPI, and the one book that
    mentioned it had gone to the open web for a date sitting in our own store."""
    import db.gamma_exposure as gex_mod
    import signals.macro_substrate as macro_mod
    from research.books import _market_frame_section

    _stub_frame_db_reads(monkeypatch)
    monkeypatch.setattr(macro_mod, "event_calendar", lambda *_a, **_k: {
        "today": [], "not_covered": "FOMC meetings without projections",
        "upcoming": [{"date": "2026-08-12", "release": "CPI", "in_days": 1}],
    })
    monkeypatch.setattr(gex_mod, "history", lambda **_k: [{
        "as_of": "2026-08-11T19:59:59Z", "oi_as_of": "2026-08-11", "spot": 7725.35,
        "net_b": 65.8, "call_b": 285.6, "put_b": 219.8, "flip": 7617.7,
        "spot_above_flip_pct": 1.41,
    }])

    frame = _market_frame_section(tmp_path / "t.db")
    assert "CPI in 1d (2026-08-12)" in frame
    assert "- abnormality fires today:" in frame
    assert "not covered" in frame  # an absent FOMC must not read as "no meeting"
    assert "net gamma $+65.8B per 1%" in frame
    assert "flip 7,618" in frame
    # The convention travels with the number or the number is misread as measured.
    assert "ASSUMES dealers long calls / short puts" in frame
    # This stub row predates vanna and charm, as the real stored rows do. A snapshot
    # that never measured them must print nothing, not a fabricated $0.0B.
    assert "vanna" not in frame


def test_market_frame_prints_vanna_and_charm_once_a_snapshot_carries_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import db.gamma_exposure as gex_mod
    import signals.macro_substrate as macro_mod
    from research.books import _market_frame_section

    _stub_frame_db_reads(monkeypatch)
    monkeypatch.setattr(macro_mod, "event_calendar", lambda *_a, **_k: None)
    monkeypatch.setattr(gex_mod, "history", lambda **_k: [{
        "as_of": "2026-08-12T19:59:59Z", "oi_as_of": "2026-08-12", "spot": 7725.35,
        "net_b": 65.8, "call_b": 285.6, "put_b": 219.8, "flip": 7617.7,
        "spot_above_flip_pct": 1.41, "net_vanna_b": 50.2, "net_charm_b": -18.6,
    }])

    frame = _market_frame_section(tmp_path / "t.db")
    assert "net vanna $+50.2B per vol pt" in frame
    assert "net charm $-18.6B per day" in frame


def test_market_frame_stays_honest_when_those_two_stores_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import db.gamma_exposure as gex_mod
    import signals.macro_substrate as macro_mod
    from research.books import _market_frame_section

    _stub_frame_db_reads(monkeypatch)
    monkeypatch.setattr(gex_mod, "history", lambda **_k: [])
    monkeypatch.setattr(macro_mod, "event_calendar", lambda *_a, **_k: None)
    frame = _market_frame_section(tmp_path / "t.db")
    assert "- macro calendar: _unavailable_" in frame
    assert "- SPX dealer exposure: _no stored snapshot_" in frame
    assert "- today's 8-K earnings exhibits: _unavailable_" in frame


def test_market_frame_names_todays_8k_exhibits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`earnings_release` shipped to the perception resource first; the pack is
    what a book reads before it asks anything. Same miss as the 08-11 calendar."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import db.gamma_exposure as gex_mod
    import signals.earnings_exhibit as exhibit_mod
    import signals.macro_substrate as macro_mod
    from research.books import _market_frame_section

    _stub_frame_db_reads(monkeypatch)
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    store = tmp_path / "earnings.db"
    store.touch()
    monkeypatch.setattr(exhibit_mod, "DEFAULT_RELEASES_DB_PATH", store)
    monkeypatch.setattr(
        exhibit_mod,
        "latest_releases",
        lambda **_k: [
            {
                "symbol": "AMZN",
                "session": "amc",
                "company": "Amazon.com, Inc.",
                "accepted_et": f"{today}T16:06:23-04:00",
            }
        ],
    )
    monkeypatch.setattr(macro_mod, "event_calendar", lambda *_a, **_k: None)
    monkeypatch.setattr(gex_mod, "history", lambda **_k: [])

    frame = _market_frame_section(tmp_path / "t.db")
    assert "today's 8-K earnings exhibits: AMZN amc" in frame
    assert "query earnings_release" in frame
    assert "query fundamentals" in frame
