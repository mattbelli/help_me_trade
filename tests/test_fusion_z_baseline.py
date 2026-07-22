"""fusion_z_baseline backfill harness (Stage-2 baseline lens prep).

Covers the contract that makes backfilled history trustworthy: raw pre-dot
days replay through the SAME shaping+scorer as live, dot days take live
parity, the stationarity floor is a hard clamp, and rebuilds are idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from db.base import connect
from db.equity_tape import init_db as init_equity
from db.fusion import init_fusion_tables
from db.options_flow import init_db as init_options
from tools.fusion_z_baseline import BASELINE_FLOOR_DAY, rebuild, write_rows

RAW_DAY = "2026-07-16"  # ≥ floor, no dots → raw_events source
DOT_DAY = "2026-07-20"  # has evidence_dots → dots source


def _init(db_path: Path) -> None:
    init_fusion_tables(db_path)
    init_options(db_path)
    init_equity(db_path)


def _insert_flow_event(
    db_path: Path,
    event_id: str,
    ts: str,
    *,
    underlying: str = "AAA",
    right: str = "C",
    direction: str = "buy_side",
    premium: float = 1_000_000.0,
    dte: int = 30,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO options_flow_events(id, ts, underlying, occ_symbol,
                   right, dte, event_type, premium_usd, direction, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'block', ?, ?, ?)""",
            (event_id, ts, underlying, f"{underlying}260821C1", right, dte, premium, direction, ts),
        )
        conn.commit()


def _insert_dot(
    db_path: Path,
    dot_id: str,
    ts: str,
    *,
    symbol: str = "BBB",
    source_store: str = "options_flow",
    component: str = "option_block",
    actor_group: str = "options_actor",
    raw_value: str = "1000000",
    detail_json: str = "{}",
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO fusion_episodes(id, symbol, market_day, opened_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO NOTHING""",
            (f"ep-{symbol}", symbol, DOT_DAY, ts, ts),
        )
        conn.execute(
            """INSERT INTO evidence_dots(
                   id, episode_id, source_store, source_event_id, source_ingest_seq,
                   symbol, ts, component, actor_group, dot_kind, direction,
                   raw_value, points, detail_json, stamped, created_at
               ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 'event', 'bull', ?, 0, ?, 1, ?)""",
            (
                dot_id,
                f"ep-{symbol}",
                source_store,
                f"ev-{dot_id}",
                symbol,
                ts,
                component,
                actor_group,
                raw_value,
                detail_json,
                ts,
            ),
        )
        conn.commit()


def test_raw_day_replays_through_intake_shaping(tmp_path: Path) -> None:
    """A pre-dot day builds from raw events: an at-ask call block becomes a
    bull thesis dot through _event_shape and posts z through score_decayed —
    one code path with live, no parallel backfill math."""
    db_path = tmp_path / "t.db"
    _init(db_path)
    # 14:00 UTC = 10:00 ET on the raw day; point 2.0 (1M / 250k base).
    _insert_flow_event(db_path, "ev1", f"{RAW_DAY}T14:00:00+00:00")

    report = rebuild(db_path, RAW_DAY, RAW_DAY)
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["symbol"] == "AAA"
    assert row["market_day"] == RAW_DAY
    assert row["source"] == "raw_events"
    # z = 2/√(2+2) = 1.0 at the first grid instant after arrival, decaying
    # after; the peak is the earliest reading.
    assert 0.9 < row["peak_z"] <= 1.0
    assert row["peak_z_ts"] is not None
    assert 0 < row["close_z"] < row["peak_z"]  # decayed by 16:00 ET
    # conviction 2/(2+2)=0.5 ≥ bar → this instant is armable.
    assert row["peak_armable_z"] == row["peak_z"]


def test_dot_day_prefers_dots_and_floor_is_hard(tmp_path: Path) -> None:
    """A day with evidence_dots sources 'dots' (live parity), and asking for
    history before the stationarity floor is CLAMPED — pre-break rows must
    never exist, they'd poison exactly the names Stage 2 normalizes."""
    db_path = tmp_path / "t.db"
    _init(db_path)
    # Pre-floor raw event that must NEVER produce a row.
    _insert_flow_event(db_path, "old", "2026-07-10T14:00:00+00:00", underlying="OLD")
    _insert_dot(db_path, "d1", f"{DOT_DAY}T14:00:00+00:00")

    report = rebuild(db_path, "2026-07-01", DOT_DAY)
    assert report["start"] == BASELINE_FLOOR_DAY
    assert {row["market_day"] for row in report["rows"]} == {DOT_DAY}
    row = report["rows"][0]
    assert row["symbol"] == "BBB"
    assert row["source"] == "dots"
    assert row["peak_z"] > 0.9


def test_dots_day_equity_dot_honors_detail_floor_usd(tmp_path: Path) -> None:
    """Live parity for equity dots: the scorer's per-event base (floor_usd)
    lives in detail_json, and the loader MUST parse it — with the parse
    skipped, a $35M block against a $10M floor over-scores on the $1M fallback
    (log2(35) ≈ 5.13 capped to 4.0 vs the true log2(3.5) ≈ 1.81)."""
    db_path = tmp_path / "t.db"
    _init(db_path)
    _insert_dot(
        db_path,
        "eq1",
        f"{DOT_DAY}T14:00:00+00:00",
        symbol="EQT",
        source_store="equity_tape",
        component="equity_block",
        actor_group="equity_actor",
        raw_value="35000000",
        detail_json='{"floor_usd": 10000000}',
    )

    report = rebuild(db_path, DOT_DAY, DOT_DAY)
    row = next(r for r in report["rows"] if r["symbol"] == "EQT")
    # point ≈ 1.807 → z = 1.807/√(1.807+2) ≈ 0.926. The fallback-base bug
    # would post point 4.0 → z ≈ 1.633.
    assert 0.85 < row["peak_z"] < 1.0


def test_after_hours_only_day_writes_no_false_zero_row(tmp_path: Path) -> None:
    """A symbol whose only dots land after 16:00 ET (empty trailing window)
    never evaluates on the RTH grid — it must be SKIPPED, not written as a
    z=0 row that reads as 'contested all day' in the Stage-2 distribution."""
    db_path = tmp_path / "t.db"
    _init(db_path)
    # 21:00 UTC = 17:00 ET — after the 16:00 grid end.
    _insert_dot(db_path, "late", f"{DOT_DAY}T21:00:00+00:00", symbol="AH")

    report = rebuild(db_path, DOT_DAY, DOT_DAY)
    assert not [r for r in report["rows"] if r["symbol"] == "AH"]


def test_peak_z_ts_is_utc(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    _init(db_path)
    _insert_flow_event(db_path, "ev1", f"{RAW_DAY}T14:00:00+00:00")
    report = rebuild(db_path, RAW_DAY, RAW_DAY)
    assert report["rows"][0]["peak_z_ts"].endswith("+00:00")


def test_write_rows_idempotent_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    _init(db_path)
    _insert_flow_event(db_path, "ev1", f"{RAW_DAY}T14:00:00+00:00")

    first = rebuild(db_path, RAW_DAY, RAW_DAY)
    assert write_rows(first, db_path) == 1
    second = rebuild(db_path, RAW_DAY, RAW_DAY)
    assert write_rows(second, db_path) == 1

    # A stale in-window row (from an older, buggier computation) must be
    # purged by the range-DELETE — rebuild semantics, not append semantics.
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO fusion_z_baseline(symbol, market_day, peak_z,
                   peak_armable_z, close_z, dot_count, source, computed_at)
               VALUES ('STALE', ?, 9.9, 9.9, 9.9, 1, 'raw_events', 'old')""",
            (RAW_DAY,),
        )
        conn.commit()
    assert write_rows(second, db_path) == 1

    conn = sqlite3.connect(db_path)
    try:
        count, peak = conn.execute(
            "SELECT count(*), max(peak_z) FROM fusion_z_baseline"
        ).fetchone()
    finally:
        conn.close()
    assert count == 1  # the STALE row is gone; one row per (symbol, day)
    assert abs(peak - first["rows"][0]["peak_z"]) < 1e-9
