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
