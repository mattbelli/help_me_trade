"""Abnormality scanner: math, base-state, fire rule, baselines, detector, db."""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from signals.abnormality.base_state import classify_base_state
from signals.abnormality.baselines import build_baselines, load_baselines
from signals.abnormality.constants import (
    BASE_HIGH,
    BASE_LOW,
    BASE_NONE,
    CAVEAT,
    KIND_ABNORMALITY,
    KIND_PAIRED,
    LABEL,
    N_BUCKETS,
    WINDOW_BARS,
)
from signals.abnormality.detector import AbnormalityDetector
from signals.abnormality.engine import FireThresholds, evaluate_boundary
from signals.abnormality.math import (
    assign_quintiles,
    parkinson_rv,
    parkinson_rv_rows,
    ratio,
)
from signals.abnormality.outcomes import aggregate, grade_event
from signals.abnormality.replay import replay_session
from signals.abnormality.store import load_5min
from tools.equity_bars_5min_backfill import write_session

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 8, 12)


def _grid(n_sym: int, n_buckets: int = N_BUCKETS, *, seed: int = 0) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    close = 10 + rng.random((n_sym, n_buckets))
    high = close + 0.05
    low = close - 0.05
    vol = rng.integers(100, 500, size=(n_sym, n_buckets)).astype(np.float64)
    return {"high": high, "low": low, "close": close, "volume": vol}


def _write_5min(path: Path, session: date, symbols: list[str], g: dict[str, np.ndarray]) -> None:
    from datetime import datetime as dt

    open_ns = int(dt.combine(session, dtime(9, 30), ET).timestamp() * 1e9)
    parts: dict[str, list] = {}
    for j, sym in enumerate(symbols):
        t, o, h, lo, c, v = [], [], [], [], [], []
        for b in range(g["close"].shape[1]):
            if not np.isfinite(g["close"][j, b]):
                continue
            t.append(open_ns + b * 5 * 60 * 1_000_000_000)
            o.append(g["close"][j, b])
            h.append(g["high"][j, b])
            lo.append(g["low"][j, b])
            c.append(g["close"][j, b])
            v.append(int(g["volume"][j, b]))
        if t:
            parts[sym] = [(
                np.array(t, np.int64),
                np.array(o, np.float64),
                np.array(h, np.float64),
                np.array(lo, np.float64),
                np.array(c, np.float64),
                np.array(v, np.int64),
            )]
    write_session(session, path, symbols, parts, 1, 0.1)


def test_parkinson_zero_range_is_present_and_zero():
    h = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    lo = h.copy()
    assert parkinson_rv(h, lo) == pytest.approx(0.0)


def test_parkinson_absent_when_coverage_fails():
    h = np.full(12, np.nan)
    h[:4] = 10.0
    lo = h.copy()
    lo[:4] = 9.0
    assert np.isnan(parkinson_rv(h, lo))


def test_parkinson_matches_114g_formula():
    from tools.mover_intraday_114g import rv_park

    h = np.array([[10.0, 11.0, 10.5, 12.0, 11.0, 10.8, 11.2, 10.9, 11.1, 10.7, 11.3, 11.0]])
    lo = np.array([[9.5, 10.0, 10.0, 11.0, 10.2, 10.1, 10.4, 10.3, 10.5, 10.0, 10.6, 10.4]])
    got = parkinson_rv_rows(h, lo)
    want = rv_park(h, lo, np.arange(12))
    np.testing.assert_allclose(got, want)


def test_ratio_never_fabricates():
    assert np.isnan(ratio(np.array([1.0]), np.array([0.0]))[0])
    assert np.isnan(ratio(np.array([np.nan]), np.array([1.0]))[0])


def test_quintiles_are_equal_count():
    dvol = np.arange(100, dtype=np.float64) + 1
    q = assign_quintiles(dvol)
    counts = [int((q == i).sum()) for i in range(5)]
    assert min(counts) >= 19 and max(counts) <= 21
    assert int((q < 0).sum()) == 0


def test_base_low_and_base_high_are_mirrors():
    n = 260
    close = np.linspace(10, 20, n)
    # Sit at the low for the last 30 days with escalating volume.
    close[-30:] = 10.2
    high = close + 0.1
    low = close - 0.1
    vol = np.concatenate([np.full(240, 100.0), np.full(20, 200.0)])
    state, strength = classify_base_state(close, high, low, vol)
    assert state == BASE_LOW
    assert strength["vol_trend"] >= 1.3

    close_h = np.linspace(20, 10, n)
    close_h[-30:] = 19.8
    high_h = close_h + 0.1
    low_h = close_h - 0.1
    state_h, _ = classify_base_state(close_h, high_h, low_h, vol)
    assert state_h == BASE_HIGH


def test_base_state_warmup_is_none():
    close = np.linspace(10, 12, 40)
    state, info = classify_base_state(close, close + 0.1, close - 0.1, np.full(40, 100.0))
    assert state == BASE_NONE
    assert info["reason"] == "warmup"


def test_fire_rule_needs_both_cell_and_floor(tmp_path: Path):
    n = 40
    high = np.ones((n, N_BUCKETS)) * 10.1
    low = np.ones((n, N_BUCKETS)) * 10.0
    vol = np.ones((n, N_BUCKETS)) * 100
    # One name with a huge window.
    high[0, 0:WINDOW_BARS] = 20.0
    vol[0, 0:WINDOW_BARS] = 10_000
    median_rv = np.full(n, parkinson_rv(np.full(WINDOW_BARS, 10.1), np.full(WINDOW_BARS, 10.0)))
    median_wvol = np.full(n, 100.0 * WINDOW_BARS)
    median_cum = np.full(n, 100.0 * WINDOW_BARS)
    quintile = np.zeros(n, np.int8)
    result = evaluate_boundary(
        high=high, low=low, volume=vol, bucket=WINDOW_BARS,
        median_rv=median_rv, median_window_vol=median_wvol, median_cum_vol=median_cum,
        quintile=quintile, base_state=np.zeros(n, np.int8),
        thresholds=FireThresholds(range_p=50, vol_p=50, range_floor=2.0, vol_floor=3.0, top_n=10),
    )
    assert 0 in set(result.fire_idx.tolist())
    assert result.fire_kind[list(result.fire_idx).index(0)] == KIND_ABNORMALITY


def test_paired_kind_when_base_state_set():
    n = 20
    high = np.ones((n, N_BUCKETS)) * 10.1
    low = np.ones((n, N_BUCKETS)) * 10.0
    vol = np.ones((n, N_BUCKETS)) * 100
    high[0, :WINDOW_BARS] = 20.0
    vol[0, :WINDOW_BARS] = 10_000
    median_rv = np.full(n, parkinson_rv(np.full(WINDOW_BARS, 10.1), np.full(WINDOW_BARS, 10.0)))
    base = np.zeros(n, np.int8)
    base[0] = 1
    result = evaluate_boundary(
        high=high, low=low, volume=vol, bucket=WINDOW_BARS,
        median_rv=median_rv,
        median_window_vol=np.full(n, 100.0 * WINDOW_BARS),
        median_cum_vol=np.full(n, 100.0 * WINDOW_BARS),
        quintile=np.zeros(n, np.int8),
        base_state=base,
        thresholds=FireThresholds(range_p=50, vol_p=50, range_floor=2.0, vol_floor=3.0, top_n=5),
    )
    assert KIND_PAIRED in result.fire_kind


def test_baselines_idempotent_and_warmup(tmp_path: Path):
    bars = tmp_path / "5min"
    daily = tmp_path / "daily"
    out = tmp_path / "abn"
    bars.mkdir()
    daily.mkdir()
    symbols = ["AAA", "BBB"]
    sessions = [date(2026, 7, 1) + __import__("datetime").timedelta(days=i) for i in range(40)]
    sessions = [d for d in sessions if d.weekday() < 5][:15]
    for s in sessions:
        g = _grid(2, seed=s.toordinal())
        _write_5min(bars, s, symbols, g)
        from tools.equity_bars_5min_backfill import write_session as ws
        parts = {}
        for j, sym in enumerate(symbols):
            t = int(datetime.combine(s, dtime(16, 0), ET).timestamp() * 1e9)
            parts[sym] = [(
                np.array([t], np.int64),
                np.array([g["close"][j, -1]]),
                np.array([g["high"][j, -1]]),
                np.array([g["low"][j, -1]]),
                np.array([g["close"][j, -1]]),
                np.array([int(g["volume"][j].sum())], np.int64),
            )]
        ws(s, daily, symbols, parts, 1, 0.1)
    a = build_baselines(bars_5min_dir=bars, daily_dir=daily, out_dir=out, as_of=sessions[-1])
    b = build_baselines(bars_5min_dir=bars, daily_dir=daily, out_dir=out, as_of=sessions[-1])
    np.testing.assert_allclose(a.median_rv, b.median_rv, equal_nan=True)
    loaded = load_baselines(out)
    assert loaded is not None
    assert loaded.session == sessions[-1]
    assert not loaded.stale(sessions[-1])


def test_replay_counts_are_reproducible(tmp_path: Path):
    bars = tmp_path / "5min"
    daily = tmp_path / "daily"
    out = tmp_path / "abn"
    bars.mkdir()
    daily.mkdir()
    symbols = [f"S{i:02d}" for i in range(12)]
    sessions = [date(2026, 7, 6) + __import__("datetime").timedelta(days=i) for i in range(20)]
    sessions = [d for d in sessions if d.weekday() < 5][:12]
    for s in sessions:
        g = _grid(12, seed=s.toordinal())
        g["high"][0, :WINDOW_BARS] = g["close"][0, :WINDOW_BARS] * 2
        _write_5min(bars, s, symbols, g)
        parts = {
            sym: [(
                np.array([int(datetime.combine(s, dtime(16, 0), ET).timestamp() * 1e9)], np.int64),
                np.array([g["close"][j, -1]]),
                np.array([g["high"][j, -1]]),
                np.array([g["low"][j, -1]]),
                np.array([g["close"][j, -1]]),
                np.array([100], np.int64),
            )]
            for j, sym in enumerate(symbols)
        }
        write_session(s, daily, symbols, parts, 1, 0.1)
    bl = build_baselines(bars_5min_dir=bars, daily_dir=daily, out_dir=out, as_of=sessions[-1])
    path = bars / f"{sessions[-1].isoformat()}.npz"
    a = replay_session(path, bl, FireThresholds(range_p=80, vol_p=50, range_floor=1.0, vol_floor=1.0))
    b = replay_session(path, bl, FireThresholds(range_p=80, vol_p=50, range_floor=1.0, vol_floor=1.0))
    assert a["fires"] == b["fires"]
    assert a["session"] == sessions[-1].isoformat()


def test_detector_stands_down_when_baselines_stale():
    det = AbnormalityDetector()
    det.set_baselines(None, date(2026, 8, 12))
    assert det.stood_down
    now = datetime(2026, 8, 12, 11, 0, 5, tzinfo=ET)
    det.reset_session(date(2026, 8, 12))
    assert det.run_boundary_for_test(WINDOW_BARS, now) is None
    _, state = det.take_pending()
    assert state and state[0]["status"] == "baselines_stale"


def test_honesty_labels_persist(tmp_path: Path):
    from db.abnormality import events_today, init_db, insert_events, upsert_state

    db = tmp_path / "t.db"
    init_db(db)
    insert_events(
        [{
            "id": "AAA:2026-08-12:12:abnormality",
            "ts": "2026-08-12T15:00:00-04:00",
            "symbol": "AAA",
            "kind": KIND_ABNORMALITY,
            "session": "2026-08-12",
            "bucket": 12,
            "range_ratio": 6.0,
            "vol_ratio": 8.0,
            "rvol_session": 3.0,
            "range_pctile": 99.5,
            "vol_pctile": 97.0,
            "composite": 48.0,
            "base_state": "none",
            "quintile": 3,
            "label": LABEL,
            "caveat": CAVEAT,
        }],
        db_path=db,
    )
    upsert_state(
        [{
            "symbol": "AAA",
            "ts": "2026-08-12T15:00:00-04:00",
            "session": "2026-08-12",
            "bucket": 12,
            "range_ratio": 6.0,
            "vol_ratio": 8.0,
            "rvol_session": 3.0,
            "range_pctile": 99.5,
            "vol_pctile": 97.0,
            "composite": 48.0,
            "base_state": "none",
            "quintile": 3,
            "status": "live",
            "label": LABEL,
            "caveat": CAVEAT,
        }],
        db_path=db,
    )
    ev = events_today("2026-08-12", db_path=db)
    assert ev[0]["label"] == LABEL
    assert "coin flip" in ev[0]["caveat"]
    # Default is the ET calendar day, not MAX(session) — otherwise Monday's
    # book pack would keep listing Friday's fires.
    assert events_today(db_path=db) == []


def test_grade_event_excess_and_direction():
    close = np.full(N_BUCKETS, 10.0)
    high = np.full(N_BUCKETS, 10.0)
    low = np.full(N_BUCKETS, 10.0)
    high[WINDOW_BARS:WINDOW_BARS + 10] = 12.0
    close[WINDOW_BARS + 23] = 11.0
    graded = grade_event(close=close, high=high, low=low, bucket=WINDOW_BARS, fwd_norm=0.1)
    assert graded["ok"]
    assert graded["move_2h"] == pytest.approx(0.2)
    assert graded["excess_2h"] == pytest.approx(2.0)
    assert graded["direction"] == 1


def test_aggregate_wilson_bounds_direction():
    rows = [{"excess_2h": 1.2, "direction": 1} for _ in range(10)]
    rows += [{"excess_2h": 0.8, "direction": -1} for _ in range(10)]
    stats = aggregate(rows)
    assert stats["n"] == 20
    lo, hi = stats["direction"]["up_wilson_95"]
    assert lo < 0.5 < hi


def test_parity_detector_vs_replay(tmp_path: Path):
    """Live detector primed from a session store matches replay on that store."""
    bars = tmp_path / "5min"
    daily = tmp_path / "daily"
    out = tmp_path / "abn"
    bars.mkdir()
    daily.mkdir()
    symbols = [f"S{i:02d}" for i in range(8)]
    sessions = [date(2026, 7, 6) + __import__("datetime").timedelta(days=i) for i in range(18)]
    sessions = [d for d in sessions if d.weekday() < 5][:12]
    for s in sessions:
        g = _grid(8, seed=s.toordinal() + 7)
        g["high"][0, :WINDOW_BARS] = g["close"][0, :WINDOW_BARS] * 3
        g["volume"][0, :WINDOW_BARS] = 50_000
        _write_5min(bars, s, symbols, g)
        parts = {
            sym: [(
                np.array([int(datetime.combine(s, dtime(16, 0), ET).timestamp() * 1e9)], np.int64),
                np.array([g["close"][j, -1]]),
                np.array([g["high"][j, -1]]),
                np.array([g["low"][j, -1]]),
                np.array([g["close"][j, -1]]),
                np.array([100], np.int64),
            )]
            for j, sym in enumerate(symbols)
        }
        write_session(s, daily, symbols, parts, 1, 0.1)
    bl = build_baselines(bars_5min_dir=bars, daily_dir=daily, out_dir=out, as_of=sessions[-1])
    last = sessions[-1]
    path = bars / f"{last.isoformat()}.npz"
    replayed = replay_session(
        path, bl, FireThresholds(range_p=80, vol_p=50, range_floor=1.0, vol_floor=1.0, top_n=10)
    )
    grid = load_5min(path)
    det = AbnormalityDetector()
    det.set_baselines(bl, last)
    det.reset_session(last)
    det._thresholds = FireThresholds(range_p=80, vol_p=50, range_floor=1.0, vol_floor=1.0, top_n=10)
    det.prime_from_grid(grid.symbols, grid.high, grid.low, grid.close, grid.volume)
    now = datetime.combine(last, dtime(10, 30, 5), ET)
    det.run_boundary_for_test(WINDOW_BARS, now)
    events, _ = det.take_pending()
    replay_syms = sorted(e["symbol"] for e in replayed["examples"] if e["bucket"] == WINDOW_BARS)
    live_syms = sorted(e["symbol"] for e in events)
    assert live_syms == replay_syms


@pytest.mark.asyncio
async def test_scanner_and_perception_carry_honesty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from db import abnormality as abn_mod
    from db.abnormality import init_db, insert_events, upsert_state

    db = tmp_path / "t.db"
    init_db(db)
    monkeypatch.setattr(abn_mod, "DEFAULT_DB_PATH", db)
    upsert_state(
        [{
            "symbol": "AAA",
            "ts": "2026-08-12T15:00:00-04:00",
            "session": "2026-08-12",
            "bucket": 12,
            "range_ratio": 6.0,
            "vol_ratio": 8.0,
            "rvol_session": 3.0,
            "range_pctile": 99.5,
            "vol_pctile": 97.0,
            "composite": 48.0,
            "base_state": "base_low",
            "quintile": 3,
            "status": "live",
            "label": LABEL,
            "caveat": CAVEAT,
        }],
        db_path=db,
    )
    insert_events(
        [{
            "id": "AAA:2026-08-12:12:abnormality_paired",
            "ts": "2026-08-12T15:00:00-04:00",
            "symbol": "AAA",
            "kind": KIND_PAIRED,
            "session": "2026-08-12",
            "bucket": 12,
            "range_ratio": 6.0,
            "vol_ratio": 8.0,
            "composite": 48.0,
            "base_state": "base_low",
            "label": LABEL,
            "caveat": CAVEAT,
        }],
        db_path=db,
    )
    from api.main import api_scanner_abnormality

    payload = await api_scanner_abnormality()
    assert payload["count"] == 1
    assert payload["results"][0]["symbol"] == "AAA"
    assert "coin flip" in payload["caveat"]
    assert payload["label"] == LABEL

    from ai.perception.resources import query

    result = await query("abnormality", {"symbol": "AAA"}, db_path=db)
    assert result["resource"] == "abnormality"
    assert result["count"] >= 1
    aliased = await query("vol_scanner", db_path=db)
    assert aliased["resource"] == "abnormality"


def test_5min_universe_flag_uses_calendar(monkeypatch: pytest.MonkeyPatch):
    from tools import equity_bars_5min_backfill as bf

    days = bf.load_calendar_sessions("2026-08-07", "2026-08-10")
    assert [d.isoformat() for d in days] == ["2026-08-07", "2026-08-10"]
