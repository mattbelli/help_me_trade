"""Hermetic tests for setup_scan idea outcome tracking."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config.idea_engine import GRADING_TIEBREAK
from db.ai_tables import init_ai_tables, save_research_artifact
from db.base import connect
from db.idea_outcomes import (
    get_summary,
    init_idea_outcomes_tables,
    list_open,
    list_recent_resolved,
    register_idea,
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

    # Minute tape unavailable → the pessimistic daily tie-break stands.
    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=None),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("lost") == 1
    with connect(idea_db) as conn:
        row = conn.execute(
            "SELECT grading_resolution FROM idea_outcomes WHERE status='lost'"
        ).fetchone()
    assert row["grading_resolution"] == "daily"


def _minute(iso: str, *, low: float, high: float) -> SimpleNamespace:
    return SimpleNamespace(
        time=iso, open=(low + high) / 2, low=low, high=high,
        close=(low + high) / 2, volume=1_000,
    )


def test_both_touch_bar_won_when_minute_walk_orders_target_first(idea_db) -> None:
    """2026-07-28: a daily bar touching BOTH stop and target asks the minute
    tape which came first — always-LOST biased the whole mechanical lane on
    the 1×ATR/2×ATR geometry (losers −1.27R, winners capped +2.00R)."""
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of, stop="98", targets=["110"])
    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),  # trigger (01-07)
        _bar(start + timedelta(days=2), low=97, high=112),  # both-touch (01-08)
    ]
    minutes = [
        _minute("2026-01-08T15:31:00+00:00", low=109, high=111),  # target first
        _minute("2026-01-08T19:00:00+00:00", low=97, high=99),  # stop later
    ]
    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=minutes),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    with connect(idea_db) as conn:
        row = conn.execute(
            "SELECT status, exit_reason, grading_resolution, resolution_price "
            "FROM idea_outcomes"
        ).fetchone()
    assert row["status"] == "won"
    assert row["exit_reason"] == "target"
    assert row["grading_resolution"] == "minute_tiebreak"
    assert row["resolution_price"] == "110"


def test_both_touch_bar_lost_when_minute_walk_orders_stop_first(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of, stop="98", targets=["110"])
    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),
        _bar(start + timedelta(days=2), low=97, high=112),
    ]
    minutes = [
        # Pre-market print at the target must NOT decide (RTH filter): 13:00Z
        # = 08:00 ET.
        _minute("2026-01-08T13:00:00+00:00", low=111, high=112),
        _minute("2026-01-08T15:31:00+00:00", low=97, high=99),  # stop first (RTH)
        _minute("2026-01-08T19:00:00+00:00", low=109, high=111),
    ]
    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=minutes),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    with connect(idea_db) as conn:
        row = conn.execute(
            "SELECT status, grading_resolution, resolution_price FROM idea_outcomes"
        ).fetchone()
    assert row["status"] == "lost"
    assert row["grading_resolution"] == "minute_tiebreak"
    assert row["resolution_price"] == "97"  # deciding minute bar's worst extreme (minute-lane convention)


def test_both_touch_single_minute_spanning_both_stays_conservative(idea_db) -> None:
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of, stop="98", targets=["110"])
    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),
        _bar(start + timedelta(days=2), low=97, high=112),
    ]
    minutes = [_minute("2026-01-08T15:31:00+00:00", low=97, high=112)]  # spans both
    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=minutes),
    ):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1  # still ambiguous → win% never flattered


def test_same_bar_zone_and_stop_is_lost_not_invalidated(idea_db) -> None:
    # Conservative rule: a bar that touches the entry zone AND the stop, when
    # the minute tape cannot order the touches, is a real entry that stopped
    # out (lost) — discarding it as invalidated would inflate win%.
    # Zone 100-105, stop 95: bar 94-101 touches both.
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=94, high=101)]

    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=None),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    assert counts.get("invalidated", 0) == 0
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("lost") == 1


def test_entry_bar_stop_first_on_minutes_is_invalidated(idea_db) -> None:
    """Review M3a sibling fix: zone+stop in one daily bar, but the minute tape
    shows the STOP traded before the zone ever did — price crashed through and
    the planned entry never existed → invalidated, not lost."""
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)  # zone 100-105, stop 95
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=94, high=101)]
    minutes = [
        _minute("2026-01-07T15:31:00+00:00", low=94, high=96),  # stop first
        _minute("2026-01-07T19:00:00+00:00", low=100, high=101),  # zone later
    ]
    with (
        patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars),
        patch("research.idea_tracker.fetch_minute_bars", return_value=minutes),
    ):
        counts = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    assert counts.get("invalidated", 0) == 1
    assert counts.get("lost", 0) == 0
    with connect(idea_db) as conn:
        row = conn.execute(
            "SELECT status, grading_resolution FROM idea_outcomes"
        ).fetchone()
    assert row["status"] == "invalidated"
    assert row["grading_resolution"] == "minute_tiebreak"


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

    # REGRESSION (2026-07-06 audit CRITICAL-1): a second sweep over the SAME
    # ambiguous bar must NOT convert the trigger into a win — a win needs a
    # bar strictly after the trigger date to prove it.
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts2 = sweep_open_ideas(now=datetime(2026, 1, 8, 12, 30, 0), db_path=idea_db)

    assert counts2.get("won", 0) == 0
    rows = list_open(db_path=idea_db)
    assert rows[0]["status"] == "triggered"


def test_target_on_later_bar_after_ambiguous_trigger_is_won(idea_db) -> None:
    # The flip side of the conservative rule: a bar strictly AFTER the
    # ambiguous trigger bar that reaches the target IS a win.
    as_of = "2026-01-06T15:00:00+00:00"
    _register(idea_db, as_of=as_of)
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [_bar(trigger_day, low=104, high=111)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    bars.append(_bar(trigger_day + timedelta(days=1), low=106, high=110.5))
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts.get("won", 0) == 1
    summary = get_summary(db_path=idea_db)
    assert summary["counts"].get("won") == 1


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


def test_data_gap_recovers_when_bars_return(idea_db) -> None:
    """H2: a market-hours outage must NOT permanently tombstone an idea. Once a
    later fetch succeeds, the data_gap row is restored to its open status and
    resumes tracking — daily bars mean nothing is lost by waiting."""
    _register(idea_db)
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=None):
        for _ in range(DATA_GAP_FAIL_STREAK):
            sweep_open_ideas(db_path=idea_db)
    assert not list_open(db_path=idea_db)  # parked as data_gap

    # Bars come back (quiet, no zone touch) -> restored to pending, streak clear.
    start = date(2026, 1, 6)
    bars = [_bar(start + timedelta(days=1), low=106, high=108)]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)

    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert int(rows[0]["fetch_fail_streak"] or 0) == 0


def test_data_gap_recovers_to_triggered(idea_db) -> None:
    """H2: recovery restores the PRIOR open status — a row that had already
    triggered before the outage comes back as triggered, not pending."""
    _register(idea_db)
    start = date(2026, 1, 6)
    trigger_bars = [_bar(start + timedelta(days=1), low=104, high=106)]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=trigger_bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
    assert list_open(db_path=idea_db)[0]["status"] == "triggered"

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=None):
        for _ in range(DATA_GAP_FAIL_STREAK):
            sweep_open_ideas(db_path=idea_db)
    assert not list_open(db_path=idea_db)  # gapped

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=trigger_bars):
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)
    rows = list_open(db_path=idea_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "triggered"


def test_register_stores_veto_reason(idea_db) -> None:
    """H1: vetoed structures carry their veto reason into the outcome row;
    ordinary ideas store an empty reason."""
    vetoed = _sample_idea(symbol="VETOD")
    vetoed["origin"] = "structure_vetoed"
    vetoed["veto_reason"] = "already extended > 3 ATR above support"
    ok = _sample_idea(symbol="OKIDEA")
    register_ideas(1, [ok, vetoed], db_path=idea_db)

    rows = {r["symbol"]: r for r in list_open(db_path=idea_db)}
    assert rows["VETOD"]["origin"] == "structure_vetoed"
    assert rows["VETOD"]["veto_reason"] == "already extended > 3 ATR above support"
    assert rows["OKIDEA"]["origin"] == "structure"
    assert (rows["OKIDEA"]["veto_reason"] or "") == ""


def test_vetoed_idea_tracked_but_excluded_from_headline(idea_db) -> None:
    """H1 end-to-end (DB/tracker layer): a vetoed structure is swept the same
    way, appears under by_origin (calibration-INCLUDED), but is kept OUT of the
    headline paper win-rate (display-excluded — vetoed != recommended)."""
    struct = _sample_idea(symbol="STRUCT")
    vetoed = _sample_idea(symbol="VETOD")
    vetoed["origin"] = "structure_vetoed"
    vetoed["veto_reason"] = "poor R/R vs prevailing trend"
    # Single call -> unique idea_index (mirrors setup_scan's combined register).
    register_ideas(1, [struct, vetoed], db_path=idea_db)

    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),  # trigger both
        _bar(start + timedelta(days=2), low=104, high=111),  # target both
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    summary = get_summary(db_path=idea_db)
    # Headline lane sees ONLY the recommended (non-vetoed) win.
    assert summary["counts"].get("won") == 1
    assert summary["resolved"] == 1
    assert summary["win_rate"] == 1.0
    # Calibration read: both lanes measurable, veto has its own report card.
    assert summary["by_origin"]["structure"]["counts"]["won"] == 1
    assert summary["by_origin"]["structure_vetoed"]["counts"]["won"] == 1
    assert summary["by_origin"]["structure_vetoed"]["win_rate"] == 1.0


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


def test_register_ideas_dedupes_open_zone_across_artifacts(idea_db) -> None:
    """A daily scheduled scan re-suggesting yesterday's untouched zone must not
    multi-count the same idea in the calibration ledger."""
    assert _register(idea_db, artifact_id=1) == 1
    assert _register(idea_db, artifact_id=2) == 0
    assert len(list_open(db_path=idea_db)) == 1
    # A different zone for the same symbol IS a new idea.
    assert (
        _register(idea_db, artifact_id=3, entry_low="90", entry_high="95", stop="85")
        == 1
    )
    assert len(list_open(db_path=idea_db)) == 2


def test_register_ideas_same_zone_allowed_after_resolution(idea_db) -> None:
    assert _register(idea_db, artifact_id=1) == 1
    row = list_open(db_path=idea_db)[0]
    update_outcome(
        row["id"], status="expired", resolved_at="2026-01-20", db_path=idea_db
    )
    assert _register(idea_db, artifact_id=2) == 1


def test_sweep_stamps_last_price(idea_db) -> None:
    """Quiet bars (no trigger/stop/expiry) still stamp the newest close so the
    widget can show distance-from-zone on pending rows."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    start = date(2026, 1, 6)
    bars = [_bar(start, low=106, high=108, close=107.5)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 7, 12, 0, 0), db_path=idea_db)

    assert counts["triggered"] == 0
    row = list_open(db_path=idea_db)[0]
    assert row["status"] == "pending"
    assert row["last_price"] == "107.5"
    assert row["last_price_at"] == start.isoformat()

    # Same close again -> no rewrite (updated_at ordering stays stable).
    before = row["updated_at"]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 7, 18, 0, 0), db_path=idea_db)
    assert list_open(db_path=idea_db)[0]["updated_at"] == before


def test_sweep_collects_transitions(idea_db) -> None:
    """Status changes surface through the transitions out-param so the caller
    can notify; quiet sweeps collect nothing."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    start = date(2026, 1, 6)

    quiet = [_bar(start, low=106, high=108)]
    transitions: list[dict] = []
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=quiet):
        sweep_open_ideas(
            now=datetime(2026, 1, 7, 12, 0, 0), db_path=idea_db,
            transitions=transitions,
        )
    assert transitions == []

    touch = quiet + [_bar(start + timedelta(days=1), low=104, high=106)]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=touch):
        sweep_open_ideas(
            now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db,
            transitions=transitions,
        )
    assert len(transitions) == 1
    t = transitions[0]
    assert t["status"] == "triggered"
    assert t["symbol"] == "AAPL"
    assert t["entry_low"] is not None and t["stop"] is not None


def test_post_close_idea_is_not_graded_by_its_own_birth_bar(idea_db) -> None:
    """REGRESSION (NTSK, 2026-07-14): an idea minted AFTER the close must not be
    resolved against that day's already-complete bar.

    The nightly scan ran at 17:44 ET on a +11% breakout day (open 12.48, low
    12.40, close 14.27) and proposed buying the 14.23-14.37 zone with a 13.59
    stop. The sweep replayed that same bar and marked it LOST on the 12.40
    morning low — price action from hours BEFORE the breakout the idea was
    built on. It was declared dead four minutes after it was created.
    """
    as_of = "2026-07-14T21:44:09+00:00"  # 17:44 ET — after the 16:00 close
    _register(
        idea_db,
        symbol="NTSK",
        as_of=as_of,
        entry_low="14.23",
        entry_high="14.37",
        stop="13.59",
        targets=["16.75", "23.63"],
    )
    birth_bar = [_bar(date(2026, 7, 14), low=12.40, high=14.478, close=14.27)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=birth_bar):
        counts = sweep_open_ideas(now=datetime(2026, 7, 14, 21, 48, 20), db_path=idea_db)

    assert counts.get("lost", 0) == 0
    assert counts.get("triggered", 0) == 0
    rows = list_open(db_path=idea_db)
    assert rows[0]["status"] == "pending"


def test_post_close_idea_grades_normally_from_the_next_session(idea_db) -> None:
    """The flip side: the NEXT session's bar resolves it as usual."""
    _register(
        idea_db,
        symbol="NTSK",
        as_of="2026-07-14T21:44:09+00:00",
        entry_low="14.23",
        entry_high="14.37",
        stop="13.59",
        targets=["16.75", "23.63"],
    )
    bars = [
        _bar(date(2026, 7, 14), low=12.40, high=14.478, close=14.27),  # birth bar
        _bar(date(2026, 7, 15), low=13.00, high=14.40, close=13.10),  # real stop-out
    ]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 7, 15, 21, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    rows = list_recent_resolved(db_path=idea_db)
    assert rows[0]["resolved_at"] == "2026-07-15"


def test_intraday_idea_still_uses_its_own_session(idea_db) -> None:
    """Pre-close ideas are unaffected: the session is still ahead of them."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")  # 10:00 ET
    bars = [_bar(date(2026, 1, 6), low=94, high=101)]

    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 6, 20, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1


# --------------------------------------------------------------------------- #
# Grading Law v1 (spec §4) — SPY benchmark, exit_reason, tie-break, no_fill,
# horizon_return, direction-aware short grading, lane meters
# --------------------------------------------------------------------------- #
def _split_fetch(symbol_bars, spy_bars):
    """A fetch mock that returns a distinct SPY series from the idea's series so
    the benchmark leg can be asserted independently."""

    def _fx(symbol, since):
        return spy_bars if symbol == "SPY" else symbol_bars

    return _fx


def _register_horizon(
    db,
    *,
    symbol: str = "AAPL",
    entry_ref: str = "100",
    horizon: str = "5d",
    as_of: str = "2026-01-06T15:00:00+00:00",
    direction: str = "long",
) -> int:
    return register_idea(
        origin="strategy:momentum_sp500",
        signal_family="momentum_sp500",
        symbol=symbol,
        direction=direction,
        entry_zone=None,
        stop=None,
        targets=None,
        grading_basis="horizon_return",
        entry_ref_price=entry_ref,
        plan_horizon=horizon,
        as_of=as_of,
        db_path=db,
    )


def _register_short(
    db,
    *,
    symbol: str = "TSLA",
    as_of: str = "2026-01-06T15:00:00+00:00",
    entry_low: str = "100",
    entry_high: str = "105",
    stop: str = "110",
    targets: list[str] | None = None,
) -> int:
    return register_idea(
        origin="convergence",
        signal_family="flow_conv",
        symbol=symbol,
        direction="short",
        entry_zone={"low": entry_low, "high": entry_high},
        stop=stop,
        targets=targets or ["95", "90"],
        as_of=as_of,
        db_path=db,
    )


def test_benchmark_alpha_stamped_on_win(idea_db) -> None:
    """SPY leg: a resolved long win stamps ret_pct (entry-mid → target),
    spy_ret_pct over [first_evaluable, resolved] from the ONE cached SPY series,
    and alpha = ret - spy."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")  # 100-105, stop 95, tgt 110
    start = date(2026, 1, 6)
    symbol_bars = [
        _bar(start + timedelta(days=1), low=102, high=103),  # 2026-01-07 trigger
        _bar(start + timedelta(days=2), low=104, high=111),  # 2026-01-08 win @110
    ]
    spy_bars = [
        _bar(start, low=399, high=401, close=400),  # 2026-01-06
        _bar(start + timedelta(days=1), low=403, high=405, close=404),
        _bar(start + timedelta(days=2), low=407, high=409, close=408),  # 01-08
    ]
    fx = _split_fetch(symbol_bars, spy_bars)
    with patch("research.idea_tracker.fetch_daily_bars_since", side_effect=fx):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["exit_reason"] == "target"
    assert row["fill_status"] == "filled"
    assert row["ret_pct"] == pytest.approx((110 - 102.5) / 102.5 * 100, rel=1e-4)
    assert row["spy_ret_pct"] == pytest.approx(2.0, rel=1e-6)  # 400 -> 408
    assert row["alpha_pct"] == pytest.approx(
        (110 - 102.5) / 102.5 * 100 - 2.0, rel=1e-4
    )


def test_benchmark_null_when_spy_missing(idea_db) -> None:
    """SPY fetch failure → spy_ret_pct + alpha_pct stay NULL (never 0), the row
    still resolves on its own series with ret_pct stamped."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    start = date(2026, 1, 6)
    symbol_bars = [
        _bar(start + timedelta(days=1), low=102, high=103),
        _bar(start + timedelta(days=2), low=104, high=111),
    ]

    def fx(symbol, since):
        return None if symbol == "SPY" else symbol_bars

    with patch("research.idea_tracker.fetch_daily_bars_since", side_effect=fx):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["ret_pct"] is not None
    assert row["spy_ret_pct"] is None
    assert row["alpha_pct"] is None


def test_tiebreak_stop_wins_same_bar(idea_db) -> None:
    """TIE-BREAK LAW: one bar (after trigger) touching BOTH stop and target
    grades LOST — stop wins (GRADING_TIEBREAK)."""
    assert GRADING_TIEBREAK == "stop"
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00", stop="98", targets=["110"])
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [
        _bar(trigger_day, low=102, high=103),  # trigger
        _bar(trigger_day + timedelta(days=1), low=97, high=112),  # stop 98 AND tgt 110
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "lost"
    assert row["exit_reason"] == "stop"
    assert row["fill_status"] == "filled"
    assert row["resolution_price"] == "97"  # str(bar_low) — the long stop-out


def test_no_fill_on_unfilled_expiry(idea_db) -> None:
    """A pending idea whose zone is never touched by expiry → expired +
    fill_status='no_fill' + exit_reason='time'."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")  # zone 100-105
    start = date(2026, 1, 6)
    quiet = _trading_days(start, IDEA_TTL_DAYS + 2)
    bars = [_bar(d, low=106, high=108) for d in quiet if d >= start]  # never touch zone
    expiry_day = quiet[IDEA_TTL_DAYS]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(
            now=datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, 0, 0),
            db_path=idea_db,
        )

    assert counts["expired"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "expired"
    assert row["fill_status"] == "no_fill"
    assert row["exit_reason"] == "time"
    # No fill = no position = no return: benchmark legs stay NULL, never a
    # phantom ret/alpha folded into cell aggregates (review finding).
    assert row["ret_pct"] is None
    assert row["spy_ret_pct"] is None
    assert row["alpha_pct"] is None


def test_fill_status_filled_on_triggered_expiry(idea_db) -> None:
    """A row that triggered then timed out → expired + fill_status='filled'."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    post = _trading_days(trigger_day, RESOLVE_TTL_DAYS + 3)
    bars = [_bar(trigger_day, low=102, high=103)]
    for d in post[1:]:
        bars.append(_bar(d, low=103, high=106))  # never hits tgt 110 or stop 95
    expiry_day = post[RESOLVE_TTL_DAYS]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(
            now=datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, 0, 0),
            db_path=idea_db,
        )

    assert counts["expired"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["fill_status"] == "filled"
    assert row["exit_reason"] == "time"


def test_horizon_return_resolves_at_exact_trading_day_won(idea_db) -> None:
    """horizon_return: resolves at the close of first_evaluable + N trading days;
    positive signed return → won, exit_reason='time', fill_status='filled'."""
    assert isinstance(_register_horizon(idea_db, entry_ref="100", horizon="5d"), int)
    start = date(2026, 1, 6)  # first_evaluable
    resolve_day = _trading_days(start, 6)[5]  # +5 trading days -> 2026-01-13
    pre_bars = [_bar(d, low=99, high=103, close=102) for d in _trading_days(start, 5)]

    # No bar has reached the horizon yet — the row stays open, never guessed.
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=pre_bars):
        c1 = sweep_open_ideas(now=datetime(2026, 1, 12, 16, 0, 0), db_path=idea_db)
    assert c1.get("won", 0) == 0 and c1.get("lost", 0) == 0
    assert list_open(db_path=idea_db)[0]["status"] == "pending"

    bars = pre_bars + [_bar(resolve_day, low=104, high=108, close=106)]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        c2 = sweep_open_ideas(now=datetime(2026, 1, 14, 16, 0, 0), db_path=idea_db)

    assert c2["won"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["resolved_at"] == resolve_day.isoformat()
    assert row["exit_reason"] == "time"
    assert row["fill_status"] == "filled"
    assert row["ret_pct"] == pytest.approx(6.0, rel=1e-6)  # (106-100)/100


def test_horizon_return_lost_when_return_negative(idea_db) -> None:
    """horizon_return: a negative signed return at the horizon close → lost."""
    _register_horizon(idea_db, entry_ref="100", horizon="5d")
    start = date(2026, 1, 6)
    resolve_day = _trading_days(start, 6)[5]
    bars = [_bar(d, low=90, high=101, close=95) for d in _trading_days(start, 5)]
    bars.append(_bar(resolve_day, low=90, high=99, close=94))  # below ref 100
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 1, 14, 16, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "lost"
    assert row["exit_reason"] == "time"
    assert row["ret_pct"] == pytest.approx(-6.0, rel=1e-6)  # (94-100)/100


def test_horizon_return_unparseable_horizon_stays_open(idea_db) -> None:
    """A named/unparseable horizon leaves the row OPEN (never guess a date)."""
    assert isinstance(_register_horizon(idea_db, horizon="exit_lead"), int)
    start = date(2026, 1, 6)
    bars = [
        _bar(d, low=90, high=110, close=100 + i)
        for i, d in enumerate(_trading_days(start, 40))
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        counts = sweep_open_ideas(now=datetime(2026, 3, 1, 16, 0, 0), db_path=idea_db)

    assert counts.get("won", 0) == 0
    assert counts.get("lost", 0) == 0
    assert list_open(db_path=idea_db)[0]["status"] == "pending"


def test_short_plan_triggers_and_wins(idea_db) -> None:
    """[ADR-6] short grading: trigger on zone touch, a target BELOW entry → won;
    ret_pct signed from the short perspective (a fall is positive)."""
    _register_short(idea_db)  # zone 100-105, stop 110, targets 95/90
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [
        _bar(trigger_day, low=101, high=103),  # touch zone
        _bar(trigger_day + timedelta(days=1), low=94, high=99),  # short tgt 95 hit
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["won"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["direction"] == "short"
    assert row["exit_reason"] == "target"
    assert row["resolution_price"] == "95"
    assert row["ret_pct"] == pytest.approx((102.5 - 95) / 102.5 * 100, rel=1e-4)


def test_short_plan_stops_out(idea_db) -> None:
    """[ADR-6] short grading: a stop ABOVE entry hit → lost, exit_reason='stop',
    resolution price is the bar HIGH (mirror of the long bar-low stop-out)."""
    _register_short(idea_db)  # stop 110 above the zone
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [
        _bar(trigger_day, low=101, high=103),
        _bar(trigger_day + timedelta(days=1), low=104, high=112),  # high 112 >= stop 110
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "lost"
    assert row["exit_reason"] == "stop"
    assert row["resolution_price"] == "112"


def test_short_plan_tiebreak_stop_wins_inverted(idea_db) -> None:
    """[ADR-6] short grading: one bar touching BOTH the short target (below) and
    the short stop (above) grades LOST — the tie-break is direction-symmetric."""
    _register_short(idea_db, targets=["95"])  # stop 110, target 95
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    bars = [
        _bar(trigger_day, low=101, high=103),
        _bar(trigger_day + timedelta(days=1), low=94, high=111),  # tgt 95 AND stop 110
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        counts = sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    assert counts["lost"] == 1
    assert list_recent_resolved(db_path=idea_db)[0]["exit_reason"] == "stop"


def test_short_plan_mfe_mae_signs(idea_db) -> None:
    """[ADR-6] short MFE/MAE: favorable = price falling (MFE from the lowest low),
    adverse = price rising (MAE from the highest high) — both positive."""
    _register_short(idea_db)  # entry mid 102.5, stop 110, targets 95/90
    start = date(2026, 1, 6)
    trigger_day = start + timedelta(days=1)
    post = _trading_days(trigger_day, RESOLVE_TTL_DAYS + 3)
    bars = [_bar(trigger_day, low=101, high=103)]
    bars.append(_bar(post[1], low=98, high=104))  # dip (favorable), no target
    bars.append(_bar(post[2], low=100, high=108))  # rise (adverse), no stop
    for d in post[3:]:
        bars.append(_bar(d, low=100, high=104))
    expiry_day = post[RESOLVE_TTL_DAYS]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        sweep_open_ideas(
            now=datetime(expiry_day.year, expiry_day.month, expiry_day.day, 16, 0, 0),
            db_path=idea_db,
        )

    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "expired"
    assert row["mfe_pct"] == pytest.approx((102.5 - 98) / 102.5 * 100, rel=1e-4)
    assert row["mae_pct"] == pytest.approx((108 - 102.5) / 102.5 * 100, rel=1e-4)


def test_legacy_long_row_grades_identically(idea_db) -> None:
    """REGRESSION: a default long structure idea grades byte-identically to the
    pre-Grading-Law behavior — unchanged status/triggered_at/resolved_at/price;
    the new benchmark fields ride alongside without altering the grade."""
    _register(idea_db, as_of="2026-01-06T15:00:00+00:00")
    start = date(2026, 1, 6)
    bars = [
        _bar(start + timedelta(days=1), low=102, high=103),
        _bar(start + timedelta(days=2), low=104, high=111),
    ]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        sweep_open_ideas(now=datetime(2026, 1, 8, 12, 0, 0), db_path=idea_db)
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    row = list_recent_resolved(db_path=idea_db)[0]
    assert row["status"] == "won"
    assert row["direction"] == "long"
    assert row["triggered_at"] == (start + timedelta(days=1)).isoformat()
    assert row["resolved_at"] == (start + timedelta(days=2)).isoformat()
    assert row["resolution_price"] == "110"
    assert row["first_target_hit"] == 0
    assert row["exit_reason"] == "target"
    assert row["fill_status"] == "filled"


def test_max_open_age_gauge_excludes_synthetic(idea_db) -> None:
    """LANE METER: max_open_age_days is the max trading-day age of OPEN
    non-synthetic rows — an older OPEN synthetic canary must not inflate it."""
    from db import lane_stats

    # Real open idea aged 3 trading days at now=2026-01-09.
    register_idea(
        origin="convergence",
        signal_family="flow_conv",
        symbol="AAPL",
        entry_zone={"low": "100", "high": "105"},
        stop="95",
        targets=["110"],
        as_of="2026-01-06T15:00:00+00:00",
        db_path=idea_db,
    )
    # Synthetic canary: OLDER (aged 7) but still open — must be excluded.
    register_idea(
        origin="canary",
        signal_family="canary",
        symbol="MSFT",
        entry_zone={"low": "100", "high": "105"},
        stop="95",
        targets=["110"],
        as_of="2025-12-30T15:00:00+00:00",
        synthetic=1,
        db_path=idea_db,
    )

    quiet = [_bar(date(2026, 1, 7), low=106, high=108)]  # nothing resolves
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=quiet):
        sweep_open_ideas(now=datetime(2026, 1, 9, 12, 0, 0), db_path=idea_db)

    # Both rows are still open; the synthetic's larger age is excluded.
    assert {r["symbol"] for r in list_open(db_path=idea_db)} == {"AAPL"}
    meters = lane_stats.day("idea_tracker", lane_stats.et_today(), db_path=idea_db)
    assert meters["max_open_age_days"] == 3.0


def test_alpha_benchmark_is_direction_consistent() -> None:
    """A beta-one short in a crash earns ~0 alpha, not +2x the market move
    (GPT-5.6 audit finding 10): alpha measures against the direction-consistent
    passive alternative (long -> spy, short -> -spy)."""
    from decimal import Decimal

    from research.idea_tracker import _stamp_benchmark

    spy = {
        date(2026, 1, 5): Decimal("100"),
        date(2026, 1, 9): Decimal("90"),  # -10% market crash
    }

    # Short that fell exactly with the market: no edge over shorting SPY.
    patch_short = {"resolved_at": "2026-01-09T21:00:00+00:00", "resolution_price": "90"}
    _stamp_benchmark(
        patch_short,
        entry_ref=Decimal("100"),
        direction="short",
        first_evaluable=date(2026, 1, 5),
        bars=[],
        spy_by_date=spy,
    )
    assert patch_short["ret_pct"] == pytest.approx(10.0)
    assert patch_short["spy_ret_pct"] == pytest.approx(-10.0)  # stored market-long
    assert patch_short["alpha_pct"] == pytest.approx(0.0)

    # Long that fell with the market: no alpha either way (unchanged behavior).
    patch_long = {"resolved_at": "2026-01-09T21:00:00+00:00", "resolution_price": "90"}
    _stamp_benchmark(
        patch_long,
        entry_ref=Decimal("100"),
        direction="long",
        first_evaluable=date(2026, 1, 5),
        bars=[],
        spy_by_date=spy,
    )
    assert patch_long["alpha_pct"] == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Counterfactual expiry (Fusion §4) — armed-never-triggered → graded into the
# ONE existing ledger; invalidated stays out of _GRADED_STATUSES.
# --------------------------------------------------------------------------- #
def test_grade_counterfactual_stamps_benchmark_leg(idea_db) -> None:
    from research.idea_tracker import grade_counterfactual

    idea_id = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol="AAPL",
        direction="long",
        entry_zone={"low": "100", "high": "100"},
        stop="95",
        targets=["110"],
        plan_horizon="9d",
        as_of="2026-01-06T15:00:00+00:00",
        db_path=idea_db,
    )
    assert idea_id is not None
    row = {
        "id": idea_id,
        "symbol": "AAPL",
        "direction": "long",
        "origin": "reason_v1",
        "grading_basis": "plan",
        "as_of": "2026-01-06T15:00:00+00:00",
        "armed_at": "2026-01-06T15:00:00+00:00",
        "entry_low": "100",
        "entry_high": "100",
    }
    symbol_bars = [_bar(date(2026, 1, d), low=100 + d, high=101 + d, close=100.5 + d) for d in (7, 8, 9)]
    spy_bars = [_bar(date(2026, 1, d), low=400 + d, high=401 + d, close=400.5 + d) for d in (7, 8, 9)]
    with patch(
        "research.idea_tracker.fetch_daily_bars_since",
        side_effect=_split_fetch(symbol_bars, spy_bars),
    ):
        patch_out = grade_counterfactual(row, datetime(2026, 1, 9, 20, 0, 0), db_path=idea_db)

    assert patch_out["exit_reason"] == "trigger_ttl"
    assert patch_out["fill_status"] == "counterfactual"
    resolved = list_recent_resolved(db_path=idea_db)[0]
    assert resolved["status"] == "expired"
    assert resolved["lifecycle_state"] == "expired"
    assert resolved["exit_reason"] == "trigger_ttl"
    assert resolved["fill_status"] == "counterfactual"
    # expired ∈ _GRADED_STATUSES + fill_status != no_fill → benchmark leg stamped.
    assert resolved["ret_pct"] is not None
    assert resolved["spy_ret_pct"] is not None
    assert resolved["alpha_pct"] is not None


def test_invalidated_stays_out_of_graded_statuses() -> None:
    from research.idea_tracker import _GRADED_STATUSES

    assert "invalidated" not in _GRADED_STATUSES
    assert "expired" in _GRADED_STATUSES


def test_grade_counterfactual_creates_no_new_table(idea_db) -> None:
    from research.idea_tracker import grade_counterfactual

    def _tables() -> set[str]:
        with connect(idea_db) as conn:
            return {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

    idea_id = register_idea(
        origin="reason_v1",
        signal_family="fusion",
        symbol="MSFT",
        direction="long",
        entry_zone={"low": "100", "high": "100"},
        stop="95",
        targets=["110"],
        plan_horizon="9d",
        as_of="2026-01-06T15:00:00+00:00",
        db_path=idea_db,
    )
    before = _tables()
    row = {
        "id": idea_id,
        "symbol": "MSFT",
        "direction": "long",
        "origin": "reason_v1",
        "grading_basis": "plan",
        "as_of": "2026-01-06T15:00:00+00:00",
        "armed_at": "2026-01-06T15:00:00+00:00",
        "entry_low": "100",
        "entry_high": "100",
    }
    bars = [_bar(date(2026, 1, d), low=100 + d, high=101 + d, close=100.5 + d) for d in (7, 8, 9)]
    with patch("research.idea_tracker.fetch_daily_bars_since", return_value=bars):
        grade_counterfactual(row, datetime(2026, 1, 9, 20, 0, 0), db_path=idea_db)
    assert _tables() == before  # one ledger, no bypass table
