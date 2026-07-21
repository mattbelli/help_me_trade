"""Phase C1c — the once-daily session-levels PRODUCER (api.main).

The structure sibling of the fusion ATR producer: it stamps each pending symbol's
latest completed session nodes into ``fusion_session_levels`` so the hot intake path
only ever READS them. These tests pin pending-symbol selection, the happy-path
store+count, per-symbol failure isolation, the same-day no-op, and the round-trip
contract that the stored level dicts are exactly what ``nearest_level`` consumes.

Zero network: ``compute_session_levels`` is always monkeypatched, and every DB write
is redirected to a tmp database (the producer hardcodes DEFAULT_DB_PATH like its ATR
twin, so we steer its collaborators rather than thread a db_path through it).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import api.main as main
import db.base as db_base
import db.fusion as fusion_mod
import db.lane_stats as lane_stats_mod
from db.fusion import get_session_levels, init_fusion_tables, put_session_levels
from db.lane_stats import day as lane_day
from db.lane_stats import et_today
from db.options_flow import init_db as init_options_flow
from db.options_flow import insert_event as insert_flow
from signals.options_flow.convergence import nearest_level
from trading.market_hours import _ET


def _today() -> str:
    return datetime.now(_ET).date().isoformat()


def _yesterday() -> str:
    return (datetime.now(_ET).date() - timedelta(days=1)).isoformat()


def _flow_event(event_id: str, symbol: str) -> dict:
    ts = "2026-07-20T14:30:00+00:00"
    return {
        "id": event_id,
        "ts": ts,
        "underlying": symbol,
        "occ_symbol": f"{symbol}260821C00300000",
        "right": "call",
        "event_type": "sweep",
        "premium_usd": 1_000_000,
        "direction": "buy_side",
        "created_at": ts,
    }


def _levels_payload(symbol: str) -> dict:
    """Exactly what signals.session_node_strategy.compute_session_levels emits."""
    return {
        "symbol": symbol,
        "session_date": "2026-07-20",
        "levels": [
            {"name": "POC", "price": 315.0, "kind": "poc"},
            {"name": "PDH", "price": 320.5, "kind": "prior_high"},
            {"name": "PDL", "price": 305.0, "kind": "prior_low"},
        ],
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp DB with every producer collaborator steered to it (no real DB touched)."""
    db = tmp_path / "fusion.db"
    init_fusion_tables(db)
    init_options_flow(db)

    # The pending-symbols sync reads DEFAULT_DB_PATH via a call-time local import.
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    # The refresh job local-imports these; redirect their frozen default db_path to tmp.
    real_put = fusion_mod.put_session_levels
    monkeypatch.setattr(
        fusion_mod,
        "put_session_levels",
        lambda symbol, as_of_date, levels, **_kw: real_put(
            symbol, as_of_date, levels, db_path=db
        ),
    )
    real_bump = lane_stats_mod.bump
    monkeypatch.setattr(
        lane_stats_mod,
        "bump",
        lambda lane, counter, n=1, **_kw: real_bump(lane, counter, n, db_path=db),
    )
    # Deterministic universe; individual tests override as needed.
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: [])
    return db


def test_pending_selects_unstamped_and_prior_day_not_stamped_today(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["AAA", "BBB", "CCC"])
    put_session_levels("BBB", _today(), [{"name": "PDH", "price": 1.0}], db_path=db)
    put_session_levels("CCC", _yesterday(), [{"name": "PDH", "price": 1.0}], db_path=db)

    pending = main._fusion_levels_pending_symbols_sync(_today())

    assert "AAA" in pending  # never stamped → pending
    assert "CCC" in pending  # stamped a prior day (as_of_date < today) → pending
    assert "BBB" not in pending  # stamped today → excluded


def test_pending_includes_event_store_symbols(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Universe is the flow/tape event stores UNION the watchlist — same as ATR.
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: [])
    insert_flow(_flow_event("evt-1", "EVT"), db)

    pending = main._fusion_levels_pending_symbols_sync(_today())

    assert "EVT" in pending


@pytest.mark.asyncio
async def test_happy_path_stores_and_counts(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["AAA", "BBB"])

    async def fake_compute(symbol: str, **_kw: object) -> dict:
        return _levels_payload(symbol)

    monkeypatch.setattr(main, "compute_session_levels", fake_compute)

    await main._fusion_levels_refresh_once()

    stored = get_session_levels("AAA", db_path=db)
    assert [lvl["name"] for lvl in stored] == ["POC", "PDH", "PDL"]
    assert get_session_levels("BBB", db_path=db)  # both symbols stamped
    # Stamped under today's as_of → nothing left pending for the day.
    assert main._fusion_levels_pending_symbols_sync(_today()) == []

    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 2.0
    assert meters.get("levels_refresh_failed", 0.0) == 0.0


@pytest.mark.asyncio
async def test_per_symbol_failure_is_counted_and_others_continue(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["GOOD", "BAD"])

    async def fake_compute(symbol: str, **_kw: object) -> dict:
        if symbol == "BAD":
            raise RuntimeError("provider blew up")
        return _levels_payload(symbol)

    monkeypatch.setattr(main, "compute_session_levels", fake_compute)

    # A per-symbol failure must never propagate out of the job.
    await main._fusion_levels_refresh_once()

    assert get_session_levels("GOOD", db_path=db)  # good symbol stored
    assert get_session_levels("BAD", db_path=db) == []  # failed symbol skipped

    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 1.0
    assert meters.get("levels_refresh_failed") == 1.0


@pytest.mark.asyncio
async def test_second_run_same_day_no_ops(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["AAA"])
    calls: list[str] = []

    async def fake_compute(symbol: str, **_kw: object) -> dict:
        calls.append(symbol)
        return _levels_payload(symbol)

    monkeypatch.setattr(main, "compute_session_levels", fake_compute)

    await main._fusion_levels_refresh_once()
    await main._fusion_levels_refresh_once()

    # Second pass found nothing pending → compute never called again, no re-stamp.
    assert calls == ["AAA"]
    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 1.0


def test_stored_levels_round_trip_through_nearest_level(tmp_path: Path) -> None:
    # Store EXACTLY the producer's output (compute_session_levels' "levels" list),
    # read it back through the intake accessor, and prove nearest_level consumes it.
    db = tmp_path / "roundtrip.db"
    init_fusion_tables(db)
    producer_levels = _levels_payload("TSLA")["levels"]
    put_session_levels("TSLA", "2026-07-20", producer_levels, db_path=db)

    read_back = get_session_levels("TSLA", db_path=db)

    # ATR 10 → 0.5×10 = 5.0 threshold; 320.4 is 0.1 from PDH (320.5).
    hit = nearest_level(price=320.4, levels=read_back, alert_prices=[], atr_value=10.0)
    assert hit is not None
    assert hit["name"] == "PDH"
    assert hit["price"] == 320.5
    # Nothing within 0.5×ATR of 400 → no near level.
    assert (
        nearest_level(price=400.0, levels=read_back, alert_prices=[], atr_value=10.0)
        is None
    )
