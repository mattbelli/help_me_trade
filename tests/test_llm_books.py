"""LLM paper-books lane registration (research/books.py) + origin-law consistency."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    # Matt + LLM is snapshot-only — its pack must not carry a lanes section.
    assert "Your ledger lanes" not in build_book_pack("matt", db_path=tmp_path / "t.db")
    assert "Your ledger lanes" in build_book_pack("panel", db_path=tmp_path / "t.db")


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
