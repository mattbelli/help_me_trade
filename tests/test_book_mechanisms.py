"""Closed mechanism vocab + n-gated scoreboard (Keeper 2)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from db.base import connect
from db.idea_outcomes import (
    init_idea_outcomes_tables,
    list_ledger_rows,
    register_idea,
    update_outcome,
)
from research.book_driver import BookCandidate
from research.book_mechanisms import (
    MECHANISMS,
    MIN_SAMPLES,
    mechanism_scoreboard,
    normalize_failure_modes,
    normalize_mechanism,
    render_mechanism_ledger,
)
from research.books import build_book_pack, register_book_candidates

ORIGINS = ("book:fable", "book:fable/considered")


def _seed(
    db: Path,
    *,
    symbol: str,
    mechanism: str | None,
    status: str = "pending",
    origin: str = "book:fable",
    alpha: float | None = 1.0,
    synthetic: int = 0,
    failure_modes: list[str] | None = None,
) -> int:
    idea_id = register_idea(
        origin=origin,
        signal_family="llm_book",
        symbol=symbol,
        direction="long",
        entry_zone={"low": "99", "high": "100"},
        stop="95",
        targets=["110"],
        thesis="t",
        plan_horizon="5d",
        mechanism=mechanism,
        failure_modes=failure_modes,
        synthetic=synthetic,
        db_path=db,
    )
    assert idea_id is not None
    if status != "pending":
        assert update_outcome(
            idea_id, status=status, alpha_pct=alpha, db_path=db
        )
    return idea_id


def test_aliases_and_unknown_drop() -> None:
    assert normalize_mechanism("gap-fill") == "gap_fill"
    assert normalize_mechanism("PULLBACK") == "trend_pullback"
    assert normalize_mechanism("vibes") is None
    assert normalize_mechanism("") is None
    assert normalize_failure_modes(
        ["breaks_level", "made_up", "thesis_date", "earnings_print", "regime_flip"]
    ) == ["breaks_level", "thesis_date", "earnings_print"]


def test_candidate_requires_closed_mechanism() -> None:
    base = {
        "symbol": "AAPL",
        "thesis": "dip",
        "plan_horizon": "5d",
        "entry_ref_price": "200",
    }
    with pytest.raises(ValidationError):
        BookCandidate.model_validate(base)
    with pytest.raises(ValidationError):
        BookCandidate.model_validate({**base, "mechanism": "vibes"})
    cand = BookCandidate.model_validate({**base, "mechanism": "gap-fill"})
    assert cand.mechanism == "gap_fill"
    messy = BookCandidate.model_validate(
        {
            **base,
            "mechanism": "trend_pullback",
            "failure_modes": ["breaks_level", "nope", "time_stop"],
        }
    )
    assert messy.failure_modes == ["breaks_level", "time_stop"]
    assert messy.to_registration()["mechanism"] == "trend_pullback"


def test_register_unknown_mechanism_stores_null(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    out = register_book_candidates(
        "fable",
        [
            {
                "symbol": "AAPL",
                "direction": "long",
                "entry_zone": {"low": "99", "high": "100"},
                "stop": "95",
                "targets": ["110"],
                "thesis": "t",
                "plan_horizon": "5d",
                "advanced": True,
                "mechanism": "vibes",
                "failure_modes": ["breaks_level", "nope"],
            }
        ],
        db_path=db,
    )
    assert len(out["registered"]) == 1
    with connect(db) as conn:
        row = conn.execute(
            "SELECT mechanism, failure_modes_json FROM idea_outcomes WHERE symbol='AAPL'"
        ).fetchone()
    assert row["mechanism"] is None
    assert row["failure_modes_json"] == '["breaks_level"]'

    out2 = register_book_candidates(
        "fable",
        [
            {
                "symbol": "MSFT",
                "direction": "long",
                "entry_zone": {"low": "99", "high": "100"},
                "stop": "95",
                "targets": ["110"],
                "thesis": "t",
                "plan_horizon": "5d",
                "advanced": False,
                "mechanism": "trend_pullback",
            }
        ],
        db_path=db,
    )
    assert len(out2["registered"]) == 1
    rows = list_ledger_rows(origin="book:fable/considered", db_path=db)["rows"]
    assert rows[0]["symbol"] == "MSFT" and rows[0]["mechanism"] == "trend_pullback"


def test_n_gate_is_insufficient_until_ten(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    init_idea_outcomes_tables(db)
    for i in range(9):
        _seed(db, symbol=f"G{i}", mechanism="gap_fill", status="won")
    board = mechanism_scoreboard(ORIGINS, db_path=db)
    row = next(r for r in board if r["mechanism"] == "gap_fill")
    assert row["n"] == 9 and row["status"] == "insufficient"
    assert row["wilson"] is None
    md = render_mechanism_ledger(ORIGINS, db_path=db)
    assert "insufficient" in md
    assert "confidence 0" not in md.lower()
    assert "| gap_fill |" in md and "—" in md

    _seed(db, symbol="G9", mechanism="gap_fill", status="won")
    row = next(
        r
        for r in mechanism_scoreboard(ORIGINS, db_path=db)
        if r["mechanism"] == "gap_fill"
    )
    assert row["n"] == 10 and row["status"] == "working"
    assert row["wilson"] is not None and row["wilson"][0] > 0.5
    md10 = render_mechanism_ledger(ORIGINS, db_path=db)
    assert "working" in md10
    assert "confidence 0" not in md10.lower()


def test_failing_and_mixed_after_ten(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for i in range(10):
        _seed(db, symbol=f"L{i}", mechanism="mean_revert", status="lost")
    row = next(
        r
        for r in mechanism_scoreboard(ORIGINS, db_path=db)
        if r["mechanism"] == "mean_revert"
    )
    assert row["status"] == "failing" and row["wilson"][1] < 0.5

    db2 = tmp_path / "t2.db"
    for i in range(5):
        _seed(db2, symbol=f"W{i}", mechanism="flow_follow", status="won")
        _seed(db2, symbol=f"X{i}", mechanism="flow_follow", status="lost")
    mixed = next(
        r
        for r in mechanism_scoreboard(ORIGINS, db_path=db2)
        if r["mechanism"] == "flow_follow"
    )
    assert mixed["n"] == 10 and mixed["status"] == "mixed"


def test_considered_origin_counts_and_synthetic_does_not(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for i in range(5):
        _seed(
            db,
            symbol=f"H{i}",
            mechanism="trend_pullback",
            status="won",
            origin="book:fable",
        )
        _seed(
            db,
            symbol=f"C{i}",
            mechanism="trend_pullback",
            status="won",
            origin="book:fable/considered",
        )
    _seed(
        db,
        symbol="SYN",
        mechanism="trend_pullback",
        status="won",
        synthetic=1,
    )
    _seed(db, symbol="OPN", mechanism="trend_pullback", status="pending")
    row = next(
        r
        for r in mechanism_scoreboard(ORIGINS, db_path=db)
        if r["mechanism"] == "trend_pullback"
    )
    assert row["n"] == 10 and row["open"] == 1 and row["status"] == "working"


def test_expired_does_not_enter_n(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    for i in range(3):
        _seed(db, symbol=f"E{i}", mechanism="catalyst_fade", status="expired")
    row = next(
        r
        for r in mechanism_scoreboard(ORIGINS, db_path=db)
        if r["mechanism"] == "catalyst_fade"
    )
    assert row["n"] == 0 and row["expired"] == 3 and row["status"] == "insufficient"


def test_pack_section_lists_vocab_and_never_a_belief_number(tmp_path: Path) -> None:
    pack = build_book_pack("fable", db_path=tmp_path / "t.db")
    assert "## Your mechanism ledger" in pack
    for mech in MECHANISMS:
        assert mech in pack
    assert "_no mechanism-tagged ideas yet_" in pack
    assert "confidence 0" not in pack.lower()
    assert MIN_SAMPLES == 10


def test_scoreboard_does_not_import_tracker_or_fusion() -> None:
    import research.book_mechanisms as mod

    src = inspect.getsource(mod)
    import_lines = [
        line for line in src.splitlines() if line.lstrip().startswith(("import ", "from "))
    ]
    blob = "\n".join(import_lines).lower()
    assert "idea_tracker" not in blob
    assert "fusion" not in blob
    assert "memories" not in blob
    assert "save_memory" not in src
