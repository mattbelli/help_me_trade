"""The back-half sidecar (stage 3 of the Rust flow collector): enrich → rarity →
convergence off `options_flow_events` rows the spine wrote, on an ingest_seq cursor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import db.options_flow as of_db
from signals.options_flow.backhalf import BackHalf


def _event(event_id: str, event_type: str, **over) -> dict:
    ev = {
        "id": event_id,
        "ts": "2026-08-18T13:31:00+00:00",
        "underlying": "AAPL",
        "occ_symbol": "AAPL260918C00200000",
        "right": "call",
        "strike": 200.0,
        "expiry": "2026-09-18",
        "dte": 31,
        "event_type": event_type,
        "contracts": 300,
        "premium_usd": 90000.0,
        "direction": "buy_side",
        "direction_basis": "dbn_nbbo",
        "n_prints": 3,
        "exchanges": ["1"],
        "reason": "test",
        "coverage": "market_wide",
        "detail": None,
    }
    ev.update(over)
    return ev


def _row(db: Path, event_id: str) -> dict:
    with of_db._connect(db) as conn:
        r = conn.execute(
            "SELECT detail, otm_pct, rarity_p FROM options_flow_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return dict(r)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "trading.db"
    of_db.init_db(path)
    return path


def _sidecar(db: Path, calls: dict) -> BackHalf:
    async def enrich(events, broker):
        calls.setdefault("enriched", []).extend(ev["id"] for ev in events)
        for ev in events:
            ev["detail"] = {"underlying_price": 190.5, "oi": 12.0}

    async def converge(broker, events, **kw):
        calls.setdefault("converged", []).append([ev["id"] for ev in events])
        calls["converge_kw"] = kw
        return {"convergence": 1, "solo": 0, "pushes": 0}

    return BackHalf(
        db_path=db, get_broker=lambda: object(), enrich=enrich, converge=converge,
        clock=lambda: 1_787_000_000.0,
    )


@pytest.mark.asyncio
async def test_backhalf_enriches_new_sweep_and_block_rows_only(db: Path) -> None:
    calls: dict = {}
    side = _sidecar(db, calls)
    # Cursor starts at the newest row: pre-existing (already enriched) history is
    # never re-touched.
    of_db.insert_event(_event("old", "sweep", detail={"iv": 0.3}), db_path=db)
    assert (await side.once())["events"] == 0

    of_db.insert_event(_event("sw1", "sweep"), db_path=db)
    of_db.insert_event(_event("bl1", "block", n_prints=1), db_path=db)
    of_db.insert_event(
        _event("bu1", "flow_burst", detail={"z": 4.2}, premium_usd=1e6), db_path=db
    )
    out = await side.once()
    assert out["events"] == 2 and out["convergence"] == 1
    assert calls["enriched"] == ["sw1", "bl1"]
    assert calls["converged"] == [["sw1", "bl1"]]
    assert calls["converge_kw"]["today_et"] == "2026-08-17"
    assert calls["converge_kw"]["db_path"] == db
    assert json.loads(_row(db, "sw1")["detail"]) == {"underlying_price": 190.5, "oi": 12.0}
    assert json.loads(_row(db, "bl1")["detail"]) == {"underlying_price": 190.5, "oi": 12.0}
    # Burst rows keep the detail the spine wrote.
    assert json.loads(_row(db, "bu1")["detail"]) == {"z": 4.2}
    assert json.loads(_row(db, "old")["detail"]) == {"iv": 0.3}
    # Idempotent: nothing new → nothing done.
    assert (await side.once())["events"] == 0
    assert calls["converged"] == [["sw1", "bl1"]]


@pytest.mark.asyncio
async def test_backhalf_restart_picks_up_recent_unenriched_rows(db: Path) -> None:
    calls: dict = {}
    of_db.insert_event(_event("done", "sweep", detail={"iv": 0.2}), db_path=db)
    of_db.insert_event(_event("gap1", "sweep"), db_path=db)  # written while the sidecar was down
    of_db.insert_event(_event("gap2", "block", n_prints=1), db_path=db)
    side = _sidecar(db, calls)
    out = await side.once()
    assert out["events"] == 2
    assert calls["enriched"] == ["gap1", "gap2"]
    assert json.loads(_row(db, "done")["detail"]) == {"iv": 0.2}


@pytest.mark.asyncio
async def test_backhalf_without_broker_still_writes_and_skips_convergence(db: Path) -> None:
    calls: dict = {}

    async def converge(*a, **k):
        calls["converged"] = True
        return {}

    def no_broker():
        raise RuntimeError("no alpaca")

    side = BackHalf(db_path=db, get_broker=no_broker, converge=converge)
    of_db.insert_event(_event("anchor", "sweep", detail={"iv": 0.1}), db_path=db)
    await side.once()
    of_db.insert_event(_event("sw", "sweep"), db_path=db)
    out = await side.once()
    assert out["events"] == 1 and out["convergence"] == 0
    assert "converged" not in calls
    assert _row(db, "sw")["detail"] is None
