"""Python legs beside the Rust equity collector: bars reader + sidecar plumbing,
and the detector's boundary fold (no double-count)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from db import equity_tape as tape_db
from db.equity_bars_5min import load_session_grid, through_bucket
from signals.abnormality.constants import WINDOW_BARS
from signals.abnormality.detector import AbnormalityDetector
from signals.equity_stream.sidecar import EquitySidecar

ET = ZoneInfo("America/New_York")

# Mirrors collectors/equity_rs/src/db.rs::BARS_DDL (Rust owns the schema).
_BARS_DDL = """
CREATE TABLE equity_bars_5min (
    session TEXT NOT NULL, symbol TEXT NOT NULL, bucket INTEGER NOT NULL,
    high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (session, symbol, bucket)) WITHOUT ROWID;
CREATE TABLE equity_bars_5min_flush (
    session TEXT PRIMARY KEY, through_bucket INTEGER NOT NULL, ts TEXT NOT NULL);
"""


def _bars_db(tmp_path: Path, session: str, rows, through: int) -> Path:
    p = tmp_path / "bars.db"
    conn = sqlite3.connect(p)
    conn.executescript(_BARS_DDL)
    conn.executemany(
        "INSERT INTO equity_bars_5min VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(session, *r) for r in rows],
    )
    conn.execute("INSERT INTO equity_bars_5min_flush VALUES (?, ?, 'x')", (session, through))
    conn.commit()
    conn.close()
    return p


def test_detector_boundary_fold_does_not_double_count_volume():
    det = AbnormalityDetector()
    s = date(2026, 8, 18)
    det.reset_session(s)
    t = lambda h, m, sec: datetime.combine(s, dtime(h, m, sec), ET)  # noqa: E731
    det.on_print("AAA", 10.0, 100, t(10, 29, 10))  # minute 59 → bucket 11
    det.on_print("AAA", 11.0, 50, t(10, 29, 40))
    # boundary at 10:30:02 folds the in-progress minute (bucket 11 = 150)
    with det._lock:
        st = det._syms["AAA"]
        det._fold_minute(st)
        st.m1_minute = -1  # what _run_boundary does after the fold
    det.on_print("AAA", 12.0, 25, t(10, 29, 59))  # straggler, same minute
    det.on_print("AAA", 12.0, 5, t(10, 30, 1))  # next minute → folds the straggler bar
    st = det._syms["AAA"]
    assert st.m5_v[11] == 175.0  # 150 + 25, never 150 + 175
    assert (st.m5_h[11], st.m5_l[11], st.m5_c[11]) == (12.0, 10.0, 12.0)


def test_bars_reader_grid_and_marker(tmp_path: Path):
    s = date(2026, 8, 18)
    p = _bars_db(tmp_path, "2026-08-18", [("AAA", 0, 11.0, 10.0, 10.5, 150.0), ("BBB", 3, 5.0, 4.0, 4.5, 20.0)], 4)
    assert through_bucket(s, p) == 4
    assert through_bucket(date(2026, 8, 17), p) is None
    g = load_session_grid(s, p)
    assert list(g.symbols) == ["AAA", "BBB"]
    assert g.volume[0, 0] == 150.0 and np.isnan(g.volume[0, 1]) and g.high[1, 3] == 5.0
    assert load_session_grid(s, tmp_path / "missing.db") is None


def test_boundary_due_matches_maybe_boundary_gate():
    det = AbnormalityDetector()
    s = date(2026, 8, 18)
    det.reset_session(s)
    t = lambda h, m, sec: datetime.combine(s, dtime(h, m, sec), ET)  # noqa: E731
    assert det.boundary_due(t(10, 25, 5)) is None  # window not full yet
    assert det.boundary_due(t(10, 30, 1)) is None  # <2 s grace
    assert det.boundary_due(t(10, 30, 5)) == WINDOW_BARS
    det.maybe_boundary(t(10, 30, 5))  # stood down (no baselines) but consumes the bucket
    assert det.boundary_due(t(10, 30, 9)) is None
    assert det.boundary_due(t(10, 35, 3)) == WINDOW_BARS + 1


@pytest.mark.asyncio
async def test_sidecar_convergence_cursor_starts_at_newest_and_parses_json(tmp_path: Path):
    db = tmp_path / "trading.db"
    tape_db.init_db(db)
    tape_db.insert_event({"id": "old", "ts": "2026-08-18T14:00:00Z", "symbol": "AAA", "event_type": "equity_block",
                          "price": 10.0, "size": 10000, "notional_usd": 100000.0}, db_path=db)
    calls: list = []

    async def converge(broker, events, **kw):
        calls.append((events, kw))
        return {"convergence": 1, "solo": 0, "pushes": 0}

    side = EquitySidecar(db_path=db, get_broker=lambda: object(), converge=converge, clock=lambda: 1_787_000_000.0)
    assert (await side.once_convergence())["events"] == 0  # cursor = newest at start
    tape_db.insert_event({"id": "new", "ts": "2026-08-18T14:01:00Z", "symbol": "BBB", "event_type": "dark_pool",
                          "price": 5.0, "size": 20000, "notional_usd": 100000.0,
                          "conditions": ["@"], "detail": {"k": 1}}, db_path=db)
    c = await side.once_convergence()
    assert c["events"] == 1 and c["convergence"] == 1
    ev = calls[0][0][0]
    assert ev["symbol"] == "BBB" and ev["conditions"] == ["@"] and ev["detail"] == {"k": 1}
    assert calls[0][1]["today_et"] == "2026-08-17"  # clock → ET date
    assert (await side.once_convergence())["events"] == 0  # cursor advanced


def test_sidecar_abnormality_waits_for_collector_marker(tmp_path: Path, caplog):
    s = date(2026, 8, 18)
    p = _bars_db(tmp_path, "2026-08-18", [("AAA", 0, 11.0, 10.0, 10.5, 150.0)], WINDOW_BARS - 1)
    side = EquitySidecar(db_path=tmp_path / "t.db", bars_path=p, get_broker=lambda: None)
    side.abnormality.set_baselines(None, s)  # stood down: boundary publishes 'baselines_stale' state
    side.abnormality.reset_session(s)
    now = datetime.combine(s, dtime(10, 30, 5), ET)
    side.once_abnormality(now)
    assert side.abnormality.boundary_due(now) == WINDOW_BARS  # NOT consumed: marker behind
    assert not side.abnormality.primed
    conn = sqlite3.connect(p)
    conn.execute("UPDATE equity_bars_5min_flush SET through_bucket = ?", (WINDOW_BARS,))
    conn.commit()
    conn.close()
    from db import abnormality as abn_db
    abn_db.init_db(tmp_path / "t.db")
    side.once_abnormality(now)
    assert side.abnormality.primed
    assert side.abnormality.boundary_due(now) is None  # consumed
