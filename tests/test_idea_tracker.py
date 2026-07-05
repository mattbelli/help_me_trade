"""Hermetic tests for setup_scan idea outcome tracking."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from db.ai_tables import init_ai_tables, save_research_artifact
from db.idea_outcomes import (
    get_summary,
    init_idea_outcomes_tables,
    list_open,
    register_ideas,
    update_outcome,
)
from research.idea_tracker import (
    DATA_GAP_FAIL_STREAK,
    IDEA_TTL_DAYS,
    RESOLVE_TTL_DAYS,
    sweep_open_ideas,
)


def _bar(day: date, *, low: float, high: float, close: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        time=day.isoformat(),
        open=(low + high) / 2,
        low=low,
        high=high,
        close=close if close is not None else (low + high) / 2,
        volume=1_000_000,
    )


def _sample_idea(
    *,
    symbol: str = "AAPL",
    as_of: str = "2026-01-06T15:00:00+00:00",
    entry_low: str = "100",
    entry_high: str = "105",
    stop: str = "95",
    targets: list[str] | None = None,
) -> dict:
    targets = targets or ["110"]
    return {
        "symbol": symbol,
        "direction": "long",
        "thesis": "Test thesis for mechanical tracking.",
        "entry_zone": {"low": entry_low, "high": entry_high},
        "stop": stop,
        "targets": targets,
        "confidence": "medium",
        "confidence_reason": "test fixture",
        "entry_condition": {"type": "price_in_zone", "low": entry_low, "high": entry_high},
        "status": "candidate",
        "risks": ["test risk"],
        "evidence": [],
        "as_of": as_of,
    }


def _trading_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture
def idea_db(tmp_path):
    db = tmp_path / "ideas.db"
    init_ai_tables(db)
    init_idea_outcomes_tables(db)
    return db


def _register(db, artifact_id: int = 1, **idea_kwargs) -> int:
    idea = _sample_idea(**idea_kwargs)
    return register_ideas(artifact_id, [idea], db_path=db)


def test_register_ideas_snapshot(idea_db) -> None:
    inserted = _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    assert inserted == 1
    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAPL"
    assert row["entry_low"] == "100"
    assert row["entry_high"] == "105"
    assert row["stop"] == "95"
    assert row["targets"] == ["110"]
    assert row["status"] == "pending"


def test_register_ideas_idempotent(idea_db) -> None:
    assert _register(idea_db) == 1
    assert _register(idea_db) == 0
    assert len(list_open(db_path=idea_db)) == 1


def test_trigger_on_zone_touch(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [
        _bar(start, low=106, high=108),
        _bar(start + timedelta(days=1), low=104, high=106),
    ]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts["triggered"] == 1
    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "triggered"
    assert rows[0]["triggered_at"] == (start + timedelta(days=1)).isoformat()


def test_stop_before_trigger_invalidated(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=94, high=99)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts["invalidated"] == 1
    assert not list_open(db_path=idea_db)


def test_same_bar_stop_and_target_after_trigger_is_lost(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of, stop="98", targets=["110"])
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [
        _bar(trigger_day, low=102, high=103),
        _bar(trigger_day + timedelta(days=1), low=97, high=112),
    ]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("lost") == 1


def test_same_bar_zone_and_stop_is_lost_not_invalidated(idea_db) -> None:
    # Conservative rule: a bar that touches the entry zone AND the stop is a
    # real entry that stopped out (lost) — discarding it as invalidated would
    # inflate win%. Zone 100-105, stop 95: bar 94-101 touches both.
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=94, high=101)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    assert counts.get("invalidated", 0) == 0
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("lost") == 1


def test_same_bar_trigger_and_target_is_only_triggered(idea_db) -> None:
    # Conservative rule: the trigger bar also touching the target does NOT
    # count as a win — the run to target may have happened before the dip
    # into the zone. Zone 100-105, target 110: bar 104-111 touches both.
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=104, high=111)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts.get("won", 0) == 0
    assert counts["triggered"] == 1
    rows = list_open(db_path=idea_db)
    assert rows[0]["status"] == "triggered"


def test_win_at_first_target(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),
        _bar(start + timedelta(days=2), low=104, high=111),
    ]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("won") == 1
    assert summary["win_rate"] == 1.0


def test_pre_trigger_ttl_expiry(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    quiet_days = _trading_days(start, IDEA_TTL_DAYS + 2)
    bars = [_bar(d, low=106, high=108) for d in quiet_days if d >= start]

    expiry_day = quiet_days[IDEA_TTL_DAYS]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(
            now=datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, 0, 0),
            db_path=idea_db,
        )

    assert counts["expired"] == 1
    assert not list_open(db_path=idea_db)


def test_post_trigger_expiry_records_mfe_mae(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    post_days = _trading_days(trigger_day, RESOLVE_TTL_DAYS + 3)
    bars = [_bar(trigger_day, low=102, high=103)]
    for d in post_days[1:]:
        bars.append(_bar(d, low=103, high=106))

    expiry_day = post_days[RESOLVE_TTL_DAYS]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(
            now=datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, 0, 0),
            db_path=idea_db,
        )

    assert counts["expired"] == 1
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("expired") == 1
    from db.idea_outcomes import list_recent_resolved

    resolved = list_recent_resolved(db_path=idea_db)
    assert resolved[0]["mfe_pct"] is not None
    assert resolved[0]["mae_pct"] is not None


def test_idempotent_resweep(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=94, high=99)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        first = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        second = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert first["invalidated"] == 1
    assert second["invalidated"] == 0
    assert second["examined"] == 0


def test_snapshot_survives_artifact_deletion(idea_db) -> None:
    artifact = save_research_artifact(
        topic="scan",
        symbol=None,
        markdown="# scan",
        status="completed",
        report_json={"recipe": "setup_scan", "ideas": [_sample_idea()]},
        db_path=idea_db,
    )
    artifact_id = int(artifact["id"])
    _register(idea_db, artifact_id=artifact_id)

    from db.base import connect

    with connect(idea_db) as conn:
        conn.execute("DELETE FROM research_artifacts WHERE id = ?", (artifact_id,))
        conn.commit()

    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    assert rows[0]["artifact_id"] == artifact_id


def test_data_gap_after_repeated_fetch_failures(idea_db) -> None:
    _register(idea_db)
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=None):
        for _ in range(DATA_GAP_FAIL_STREAK - 1):
            counts = sweep_open_ideas(db_path=idea_db)
            assert counts.get("data_gap", 0) == 0
            assert list_open(db_path=idea_db)
        counts = sweep_open_ideas(db_path=idea_db)

    assert counts["data_gap"] == 1
    assert not list_open(db_path=idea_db)


def test_closed_market_empty_bars_is_benign_not_data_gap(idea_db) -> None:
    """Fetch OK but no session since as_of (weekend/holiday) must never count
    toward the data_gap streak — this killed all 4 day-one ideas over July 4th."""
    _register(idea_db)
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=[]):
        for _ in range(DATA_GAP_FAIL_STREAK + 2):
            counts = sweep_open_ideas(db_path=idea_db)
            assert counts.get("data_gap", 0) == 0
    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    assert int(rows[0]["fetch_fail_streak"] or 0) == 0


def test_empty_bars_resets_prior_fail_streak(idea_db) -> None:
    """A successful fetch (even with zero new bars) clears an accumulated
    failure streak, so outage + weekend can't combine into a false data_gap."""
    _register(idea_db)
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=None):
        for _ in range(DATA_GAP_FAIL_STREAK - 1):
            sweep_open_ideas(db_path=idea_db)
    rows = list_open(db_path=idea_db)
    assert int(rows[0]["fetch_fail_streak"]) == DATA_GAP_FAIL_STREAK - 1
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=[]):
        sweep_open_ideas(db_path=idea_db)
    rows = list_open(db_path=idea_db)
    assert int(rows[0]["fetch_fail_streak"] or 0) == 0


def test_get_summary_win_rate(idea_db) -> None:
    _register(idea_db, symbol="WIN")
    _register(
        idea_db,
        artifact_id=2,
        symbol="LOSE",
        entry_low="200",
        entry_high="205",
        stop="190",
        targets=["220"],
    )
    rows = list_open(db_path=idea_db)
    by_symbol = {r["symbol"]: r for r in rows}
    update_outcome(
        int(by_symbol["WIN"]["id"]),
        status="won",
        resolved_at="2026-01-10",
        db_path=idea_db,
    )
    update_outcome(
        int(by_symbol["LOSE"]["id"]),
        status="lost",
        resolved_at="2026-01-10",
        db_path=idea_db,
    )

    summary = get_summary(db_path=idea_db)
    assert summary["counts"]["won"] == 1
    assert summary["counts"]["lost"] == 1
    assert summary["resolved"] == 2
    assert summary["win_rate"] == 0.5


def test_register_ideas_stores_origin(idea_db) -> None:
    idea = _sample_idea()
    idea["origin"] = "llm_conviction"
    register_ideas(1, [idea], db_path=idea_db)
    rows = list_open(db_path=idea_db)
    assert rows[0]["origin"] == "llm_conviction"


def test_origin_migration_adds_column_to_old_db(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    with __import__("sqlite3").connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE idea_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id INTEGER NOT NULL,
                idea_index INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                as_of TEXT NOT NULL,
                entry_low TEXT NOT NULL,
                entry_high TEXT NOT NULL,
                stop TEXT NOT NULL,
                targets_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT NOT NULL,
                UNIQUE(artifact_id, idea_index)
            );
            """
        )
        conn.commit()

    init_idea_outcomes_tables(db)
    with __import__("sqlite3").connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(idea_outcomes)")}
    assert "origin" in cols


def test_get_summary_splits_by_origin(idea_db) -> None:
    struct_idea = _sample_idea(symbol="STRUCT")
    struct_idea["origin"] = "structure"
    llm_idea = _sample_idea(symbol="LLM1")
    llm_idea["origin"] = "llm_conviction"
    register_ideas(1, [struct_idea], db_path=idea_db)
    register_ideas(2, [llm_idea], db_path=idea_db)
    rows = list_open(db_path=idea_db)
    struct_row = next(r for r in rows if r["symbol"] == "STRUCT")
    llm_row = next(r for r in rows if r["symbol"] == "LLM1")
    update_outcome(
        int(struct_row["id"]),
        status="won",
        resolved_at="2026-01-10",
        db_path=idea_db,
    )
    update_outcome(
        int(llm_row["id"]),
        status="lost",
        resolved_at="2026-01-10",
        db_path=idea_db,
    )

    summary = get_summary(db_path=idea_db)
    assert "by_origin" in summary
    assert summary["by_origin"]["structure"]["counts"]["won"] == 1
    assert summary["by_origin"]["structure"]["win_rate"] == 1.0
    assert summary["by_origin"]["llm_conviction"]["counts"]["lost"] == 1
    assert summary["by_origin"]["llm_conviction"]["win_rate"] == 0.0
