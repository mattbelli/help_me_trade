"""Attention join ledger — build, fence, dedup, ranking (design §4.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from db.abnormality import insert_events as abn_insert
from db.abnormality import upsert_state
from db.attention import get_row, list_rows
from db.options_flow import init_db as flow_init
from db.options_flow import insert_event as flow_insert
from signals.attention import build_attention_rows, refresh_attention

SESSION = "2026-08-25"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "attention_test.db"
    flow_init(path)
    return path


def _abn_event(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"abn_{SESSION}_NXE_24",
        "ts": f"{SESSION}T10:50:03-04:00",
        "symbol": "NXE",
        "kind": "abnormality",
        "session": SESSION,
        "bucket": 24,
        "repeat": 0,
        "range_ratio": 3.2,
        "vol_ratio": 8.9,
        "rvol_session": 2.1,
        "range_pctile": 96.5,
        "vol_pctile": 91.0,
        "quintile": 4,
        "base_state": "none",
        "label": "l",
        "caveat": "c",
    }
    base.update(over)
    return base


def _flow_event(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"of_{SESSION}_NXE_swp_1",
        "ts": f"{SESSION}T14:50:10.000Z",
        "underlying": "NXE",
        "occ_symbol": "NXE261120C00010000",
        "right": "call",
        "strike": 10.0,
        "expiry": "2026-11-20",
        "dte": 87,
        "event_type": "sweep",
        "contracts": 500,
        "premium_usd": 150_000.0,
        "max_print_usd": 90_000.0,
        "direction": "buy_side",
        "direction_basis": "midpoint",
        "n_prints": 6,
        "exchanges": ["A", "B", "C"],
        "reason": "r",
        "caveat": "c",
        "rarity_p": 0.02,
        "rarity_cell": "C/31-90/otm10p",
        "rarity_n": 200,
        "rarity_asof": SESSION,
    }
    base.update(over)
    return base


def test_join_lights_both_angles_and_ranks_by_max_percentile(db: Path) -> None:
    abn_insert([_abn_event()], db_path=db)
    flow_insert(_flow_event(), db_path=db)
    rows = build_attention_rows(SESSION, db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["angles"] == 2
    assert row["abn_lit"] == 1 and row["flow_lit"] == 1
    # rank = max(abn range pctile 96.5, flow (1 - 0.02) * 100 = 98)
    assert row["rank_pctile"] == pytest.approx(98.0)
    assert row["quintile"] == 4
    # abnormality ET stamp and flow Z stamp both normalize to UTC:
    # 10:50:03 ET = 14:50:03 UTC, seven seconds before the 14:50:10Z print.
    assert row["abn_first_ts"].startswith(f"{SESSION}T14:50:03")
    assert row["first_lit_ts"] == row["abn_first_ts"]
    assert row["label"] == "attention — unusual range + rare options flow"


def test_flow_aggregation_dedups_cells_and_splits_premium(db: Path) -> None:
    flow_insert(_flow_event(), db_path=db)
    flow_insert(
        _flow_event(
            id="of_x_2", ts=f"{SESSION}T15:00:00.000Z", rarity_p=0.008,
            premium_usd=80_000.0, max_print_usd=None, right="put", dte=5,
            rarity_cell="P/0-7/atm",
        ),
        db_path=db,
    )
    flow_insert(  # same cell as the first — dedups to one cell-day
        _flow_event(
            id="of_x_3", ts=f"{SESSION}T15:10:00.000Z", rarity_p=0.04,
            premium_usd=60_000.0, max_print_usd=55_000.0,
        ),
        db_path=db,
    )
    (row,) = build_attention_rows(SESSION, db_path=db)
    assert row["flow_events"] == 3
    assert row["flow_cell_days"] == 2
    assert row["flow_min_p"] == pytest.approx(0.008)
    # max_print falls back to premium_usd on pre-restart rows (order sum bound)
    assert row["flow_max_print_usd"] == pytest.approx(90_000.0)
    assert row["flow_call_prem"] == pytest.approx(210_000.0)
    assert row["flow_put_prem"] == pytest.approx(80_000.0)
    assert row["flow_short_dated_share"] == pytest.approx(1 / 3)


def test_rare_p_threshold_and_unranked_rows_do_not_light(db: Path) -> None:
    flow_insert(_flow_event(rarity_p=0.06), db_path=db)  # not rare
    flow_insert(_flow_event(id="of_x_2", rarity_p=None), db_path=db)  # unranked
    assert build_attention_rows(SESSION, db_path=db) == []


def test_etf_and_index_roots_are_fenced_out(db: Path) -> None:
    flow_insert(
        _flow_event(id="of_spy", underlying="SPY", occ_symbol="SPY261120C00650000"),
        db_path=db,
    )
    flow_insert(
        _flow_event(id="of_spx", underlying="SPX", occ_symbol="SPXW261120C06500000"),
        db_path=db,
    )
    assert build_attention_rows(SESSION, db_path=db) == []


def test_repeats_count_but_never_relight_and_state_backfills_quintile(db: Path) -> None:
    abn_insert(
        [
            _abn_event(),
            _abn_event(id="abn_rep_1", ts=f"{SESSION}T11:20:03-04:00", bucket=30, repeat=1),
            _abn_event(id="abn_rep_2", ts=f"{SESSION}T11:50:03-04:00", bucket=36, repeat=1),
        ],
        db_path=db,
    )
    # flow-only name with no fire row — quintile comes from the live state row
    flow_insert(
        _flow_event(id="of_hrow", underlying="HROW", occ_symbol="HROW261120C00030000"),
        db_path=db,
    )
    upsert_state(
        [
            {
                "symbol": "HROW", "ts": f"{SESSION}T15:00:00-04:00", "session": SESSION,
                "bucket": 66, "quintile": 2, "status": "live", "label": "l", "caveat": "c",
            }
        ],
        db_path=db,
    )
    rows = {r["symbol"]: r for r in build_attention_rows(SESSION, db_path=db)}
    assert rows["NXE"]["abn_repeats"] == 2
    assert rows["NXE"]["abn_bucket"] == 24  # the first fire, not a repeat
    assert rows["NXE"]["angles"] == 1
    assert rows["HROW"]["quintile"] == 2


def test_ledger_upsert_ranking_and_quintile_floor(db: Path) -> None:
    abn_insert([_abn_event()], db_path=db)
    flow_insert(_flow_event(), db_path=db)
    flow_insert(
        _flow_event(
            id="of_hrow", underlying="HROW", occ_symbol="HROW261120C00030000",
            rarity_p=0.001,
        ),
        db_path=db,
    )
    assert refresh_attention(SESSION, db_path=db) == 2
    # Both-angle NXE outranks single-angle HROW despite HROW's better percentile.
    ranked = list_rows(SESSION, min_quintile=0, min_angles=1, db_path=db)
    assert [r["symbol"] for r in ranked] == ["NXE", "HROW"]
    # HROW has no quintile → the display floor drops it (deny-by-default);
    # the row itself stays in the ledger.
    floored = list_rows(SESSION, min_quintile=3, min_angles=1, db_path=db)
    assert [r["symbol"] for r in floored] == ["NXE"]
    assert get_row("HROW", SESSION, db_path=db) is not None
    # Re-running upserts in place — no duplicate rows.
    assert refresh_attention(SESSION, db_path=db) == 2
    assert len(list_rows(SESSION, min_quintile=0, min_angles=1, db_path=db)) == 2
    # min_angles=2 keeps only the join.
    assert [r["symbol"] for r in list_rows(SESSION, min_quintile=0, min_angles=2, db_path=db)] == ["NXE"]


def test_empty_session_writes_nothing(db: Path) -> None:
    assert refresh_attention(SESSION, db_path=db) == 0
    assert list_rows(SESSION, min_quintile=0, min_angles=0, db_path=db) == []


# --- Phase 3: outcomes on every pool name-day + the bounded queue --------------


def _fake_baselines(symbols: list[str], pool: list[bool], quintiles: list[int]):
    import numpy as np

    from signals.abnormality.baselines import Baselines
    from signals.abnormality.constants import N_BUCKETS

    n = len(symbols)
    return Baselines(
        session=__import__("datetime").date.fromisoformat(SESSION),
        symbols=np.array(symbols),
        median_rv=np.ones((n, N_BUCKETS)),
        median_window_vol=np.ones((n, N_BUCKETS)),
        median_cum_vol=np.ones((n, N_BUCKETS)),
        median_fwd_2h=np.full((n, N_BUCKETS), 0.01),
        median_daily_vol=np.ones(n),
        median_dvol=np.ones(n),
        quintile=np.array(quintiles, dtype=np.int8),
        base_state=np.array(["none"] * n),
        base_weeks=np.zeros(n, dtype=np.int16),
        vol_trend=np.ones(n),
        baseline_close=np.full(n, 100.0),
        n_sessions=np.full(n, 20, dtype=np.int16),
        pool=np.array(pool, dtype=bool),
    )


def _fake_5min_store(tmp_path: Path, symbols: list[str]) -> Path:
    """The sparse COO layout `load_5min` reads: one bar per (sym, bucket)."""
    import numpy as np

    from signals.abnormality.constants import N_BUCKETS
    from signals.abnormality.store import FIVE_MIN_NS, session_open_ns

    d = tmp_path / "bars5"
    d.mkdir()
    n = len(symbols)
    open_ns = session_open_ns(__import__("datetime").date.fromisoformat(SESSION))
    sym_idx = np.repeat(np.arange(n, dtype=np.int32), N_BUCKETS)
    buckets = np.tile(np.arange(N_BUCKETS, dtype=np.int64), n)
    close = np.full(n * N_BUCKETS, 100.0)
    # symbol 0 moves 2% from bucket 30 on
    close[(sym_idx == 0) & (buckets >= 30)] = 102.0
    np.savez_compressed(
        d / f"{SESSION}.npz",
        symbols=np.array(symbols),
        t_ns=open_ns + buckets * FIVE_MIN_NS,
        high=close, low=close, close=close,
        volume=np.full(n * N_BUCKETS, 1000.0),
        sym_idx=sym_idx,
    )
    return d


def test_outcomes_grade_every_pool_name_day(db: Path, tmp_path: Path) -> None:
    from datetime import date

    from db.attention import outcomes_by_angles
    from signals.attention_outcomes import sweep_attention_outcomes

    abn_insert([_abn_event(bucket=30)], db_path=db)  # NXE lit, anchored bucket 30
    flow_insert(_flow_event(), db_path=db)
    refresh_attention(SESSION, db_path=db)
    syms = ["NXE", "AAA", "BBB", "ZZZ"]
    bl = _fake_baselines(syms, pool=[True, True, True, False], quintiles=[4, 2, -1, 3])
    bars = _fake_5min_store(tmp_path, syms)
    n = sweep_attention_outcomes(
        date.fromisoformat(SESSION), bars_5min_dir=bars, baselines=bl, db_path=db
    )
    assert n == 3  # ZZZ is out of the pool
    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM attention_outcomes")}
    assert rows["NXE"]["angles"] == 2 and rows["NXE"]["anchor"] == "abn_first"
    assert rows["NXE"]["bucket"] == 30
    # 2% move / 0.01 norm ≈ 1.98 excess (log return)
    assert rows["NXE"]["excess_2h"] == pytest.approx(1.98, abs=0.02)
    # Non-lit peers graded at the session-median lit anchor, angles 0
    assert rows["AAA"]["angles"] == 0 and rows["AAA"]["anchor"] == "session_median"
    assert rows["AAA"]["bucket"] == 30
    assert rows["AAA"]["quintile"] == 2
    assert rows["BBB"]["quintile"] is None  # -1 sentinel never leaks
    stats = {s["angles"]: s for s in outcomes_by_angles(sessions=30, db_path=db)}
    assert stats[0]["n"] == 2 and stats[2]["n"] == 1


def test_queue_takes_only_both_angle_floor_names_once(db: Path) -> None:
    from db.attention import mark_queue_item, next_pending

    abn_insert([_abn_event()], db_path=db)
    flow_insert(_flow_event(), db_path=db)  # NXE: both angles, Q4
    flow_insert(
        _flow_event(id="of_hrow", underlying="HROW", occ_symbol="HROW261120C00030000"),
        db_path=db,
    )  # flow-only — never queued
    refresh_attention(SESSION, db_path=db)
    item = next_pending(db_path=db)
    assert item is not None and item["symbol"] == "NXE"
    mark_queue_item("NXE", SESSION, status="done", note="n", db_path=db)
    assert next_pending(db_path=db) is None
    # Re-running the 60s tick never re-queues a consumed name-day.
    refresh_attention(SESSION, db_path=db)
    assert next_pending(db_path=db) is None


def test_failed_queue_item_retires_after_three_attempts(db: Path) -> None:
    from db.attention import mark_queue_item, next_pending

    abn_insert([_abn_event()], db_path=db)
    flow_insert(_flow_event(), db_path=db)
    refresh_attention(SESSION, db_path=db)
    for _ in range(3):
        assert next_pending(db_path=db) is not None
        mark_queue_item("NXE", SESSION, status="pending", db_path=db)
    assert next_pending(db_path=db) is None
