"""Phase C1c — the once-daily session-levels PRODUCER (api.main).

The structure sibling of the fusion ATR producer: it stamps each pending symbol's
latest completed session nodes into ``fusion_session_levels`` so the hot intake path
only ever READS them. These tests pin pending-symbol selection, the happy-path
store+count, skipping symbols the batch could not compute, sweep-failure isolation,
the same-day no-op, the free re-ask that replaced the old per-symbol back-off, and the
round-trip contract that the stored level dicts are exactly what ``nearest_level``
consumes.

Zero network: ``compute_session_levels_batch`` is always monkeypatched, and every DB
write is redirected to a tmp database (the producer hardcodes DEFAULT_DB_PATH like its
ATR twin, so we steer its collaborators rather than thread a db_path through it).
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


def _fake_batch(
    *, only: set[str] | None = None, seen: list[list[str]] | None = None
):
    """Stand-in for compute_session_levels_batch: returns a payload for each requested
    symbol, omitting any outside ``only`` (how the real batch reports "no usable bars").
    ``seen`` records the symbol list of each sweep so a test can pin re-ask behaviour."""

    async def _batch(symbols: list[str]) -> dict[str, dict]:
        if seen is not None:
            seen.append(list(symbols))
        return {
            sym: _levels_payload(sym)
            for sym in symbols
            if only is None or sym in only
        }

    return _batch


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
    monkeypatch.setattr(main, "compute_session_levels_batch", _fake_batch())

    await main._fusion_levels_refresh_once()

    stored = get_session_levels("AAA", db_path=db)
    assert [lvl["name"] for lvl in stored] == ["POC", "PDH", "PDL"]
    assert get_session_levels("BBB", db_path=db)  # both symbols stamped
    # Stamped under today's as_of -> nothing left pending for the day.
    assert main._fusion_levels_pending_symbols_sync(_today()) == []

    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 2.0
    assert meters.get("levels_refresh_failed", 0.0) == 0.0


@pytest.mark.asyncio
async def test_symbol_with_no_levels_is_skipped_and_others_continue(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The batch omits symbols it could not compute; they must not be stamped, and must
    # not cost their slice-mates a stamp.
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["GOOD", "EMPTY"])
    monkeypatch.setattr(main, "compute_session_levels_batch", _fake_batch(only={"GOOD"}))

    await main._fusion_levels_refresh_once()

    assert get_session_levels("GOOD", db_path=db)
    assert get_session_levels("EMPTY", db_path=db) == []

    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 1.0
    assert meters.get("levels_refresh_failed", 0.0) == 0.0


@pytest.mark.asyncio
async def test_sweep_failure_is_counted_and_never_raises(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Per-slice failures are absorbed inside the batch; a failure reaching the job means
    # the whole sweep broke. It must be counted and swallowed, never raised into the
    # job runner, and must leave everyone pending for the next pass.
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["AAA"])

    async def boom(_symbols: list[str]) -> dict:
        raise RuntimeError("provider blew up")

    monkeypatch.setattr(main, "compute_session_levels_batch", boom)

    await main._fusion_levels_refresh_once()

    assert get_session_levels("AAA", db_path=db) == []
    assert "AAA" in main._fusion_levels_pending_symbols_sync(_today())
    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_refresh_failed") == 1.0


@pytest.mark.asyncio
async def test_second_run_same_day_no_ops(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["AAA"])
    seen: list[list[str]] = []
    monkeypatch.setattr(main, "compute_session_levels_batch", _fake_batch(seen=seen))

    await main._fusion_levels_refresh_once()
    await main._fusion_levels_refresh_once()

    # Second pass found nothing pending -> the batch is never even called again.
    assert seen == [["AAA"]]
    meters = lane_day("fusion_intake", et_today(), db_path=db)
    assert meters.get("levels_stamped") == 1.0


@pytest.mark.asyncio
async def test_uncomputable_symbol_is_reattempted_every_cycle(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This replaces a back-off that only existed because each symbol cost its own
    # request. Batched, re-asking is free, so the symbol is swept EVERY cycle and
    # recovers the moment its data appears — no interval to tune, no state to lose
    # across a restart.
    db = env
    monkeypatch.setattr(main, "_all_watchlist_symbols", lambda: ["LATE"])
    seen: list[list[str]] = []
    monkeypatch.setattr(
        main, "compute_session_levels_batch", _fake_batch(only=set(), seen=seen)
    )

    await main._fusion_levels_refresh_once()
    await main._fusion_levels_refresh_once()
    assert seen == [["LATE"], ["LATE"]]  # re-asked, not silenced
    assert get_session_levels("LATE", db_path=db) == []

    # Its data shows up mid-day; the very next cycle stamps it.
    monkeypatch.setattr(main, "compute_session_levels_batch", _fake_batch(seen=seen))
    await main._fusion_levels_refresh_once()

    assert get_session_levels("LATE", db_path=db)
    assert main._fusion_levels_pending_symbols_sync(_today()) == []


def test_reader_refuses_a_stale_row_but_serves_yesterdays(tmp_path: Path) -> None:
    # The defect this guard closes (2026-08-12 audit): a symbol whose refresh keeps
    # failing — delisted, or too thin for a volume profile — kept serving its LAST
    # successful stamp as current, so "PDH" named a day that was not the caller's
    # prior day. One day of slack is deliberate: it absorbs the ET-midnight window
    # while the sweep restamps the whole universe.
    db = tmp_path / "stale.db"
    init_fusion_tables(db)
    levels = [{"name": "PDH", "price": 320.5}]
    put_session_levels("FRESH", "2026-08-12", levels, db_path=db)
    put_session_levels("YDAY", "2026-08-11", levels, db_path=db)
    put_session_levels("DEAD", "2026-08-09", levels, db_path=db)

    assert get_session_levels("FRESH", as_of="2026-08-12", db_path=db) == levels
    assert get_session_levels("YDAY", as_of="2026-08-12", db_path=db) == levels
    assert get_session_levels("DEAD", as_of="2026-08-12", db_path=db) == []
    # ...and the same DEAD row reads fine to a caller whose own session is that day.
    assert get_session_levels("DEAD", as_of="2026-08-09", db_path=db) == levels


def test_reader_refuses_a_row_newer_than_the_caller(tmp_path: Path) -> None:
    # get_atr's upper bound, for the same reason: replaying an old event must never
    # see levels that did not exist yet.
    db = tmp_path / "lookahead.db"
    init_fusion_tables(db)
    put_session_levels("TSLA", "2026-08-12", [{"name": "PDH", "price": 320.5}], db_path=db)

    assert get_session_levels("TSLA", as_of="2026-07-20", db_path=db) == []


def test_stored_levels_round_trip_through_nearest_level(tmp_path: Path) -> None:
    # Store EXACTLY the producer's output (compute_session_levels' "levels" list),
    # read it back through the intake accessor, and prove nearest_level consumes it.
    db = tmp_path / "roundtrip.db"
    init_fusion_tables(db)
    producer_levels = _levels_payload("TSLA")["levels"]
    put_session_levels("TSLA", "2026-07-20", producer_levels, db_path=db)

    read_back = get_session_levels("TSLA", as_of="2026-07-20", db_path=db)

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
