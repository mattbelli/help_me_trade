"""Outcome filler: forward returns from synthetic bars (no network)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from db import options_flow as of_db
from signals.options_flow.outcomes import fill_outcomes


@dataclass
class _Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _bar(time: str, close: float) -> _Bar:
    return _Bar(time=time, open=close, high=close, low=close, close=close, volume=1)


class _FakeAdapter:
    """Minute bars 14:30-15:35 stepping +0.1/min from 100; daily closes 100→110."""

    def get_stock_bars(self, symbol, *, timeframe="1Day", days=180):
        assert timeframe == "1Day"
        return [
            _bar("2026-07-10T04:00:00Z", 100.0),  # event day
            _bar("2026-07-13T04:00:00Z", 102.0),  # +1 trading day
            _bar("2026-07-14T04:00:00Z", 104.0),
            _bar("2026-07-15T04:00:00Z", 106.0),
            _bar("2026-07-16T04:00:00Z", 108.0),
            _bar("2026-07-17T04:00:00Z", 110.0),  # +5 trading days
        ]

    def get_stock_bars_range(self, symbol, *, timeframe, start, end):
        assert timeframe == "1Min"
        out = []
        base = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
        for i in range(66):
            t = base.timestamp() + i * 60
            iso = datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(_bar(iso, 100.0 + 0.1 * i))
        return out


def _event(id: str, ts: str, **kw) -> dict:
    ev = {
        "id": id, "ts": ts, "underlying": "NVDA",
        "occ_symbol": "NVDA260710C00200000", "right": "call", "strike": 200.0,
        "expiry": "2026-07-10", "dte": 0, "otm_pct": 1.0, "event_type": "sweep",
        "contracts": 100, "premium_usd": 50_000.0, "vol_oi_ratio": None,
        "vol_zscore": None, "direction": "buy_side", "direction_basis": "stream_quote",
        "spread_flag": 0, "n_prints": 3, "exchanges": ["C"], "reason": "t",
        "caveat": "t",
    }
    ev.update(kw)
    return ev


def test_fill_outcomes_intraday_and_daily(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    # tape event at 14:30 (base close 100.0)
    of_db.insert_event(_event("e1", "2026-07-10T14:30:00Z"), db_path=db)
    # EOD-stamped event: no intraday horizons
    of_db.insert_event(
        _event("e2", "2026-07-10T21:00:00Z", event_type="unusual_volume"), db_path=db
    )

    now = datetime(2026, 7, 20, tzinfo=UTC)  # all horizons elapsed
    summary = fill_outcomes(_FakeAdapter(), now_utc=now, db_path=db)
    assert summary["stamped"] == 2

    rows = {r["id"]: r for r in of_db.recent_events(limit=10, db_path=db)}
    e1 = rows["e1"]
    assert abs(e1["ret_15m"] - 0.015) < 1e-9  # 100 -> 101.5
    assert abs(e1["ret_1h"] - 0.060) < 1e-9   # 100 -> 106.0
    assert abs(e1["ret_eod"] - 0.0) < 1e-9    # 100 -> daily close 100
    assert abs(e1["ret_1d"] - 0.02) < 1e-9    # 100 -> 102
    assert abs(e1["ret_5d"] - 0.10) < 1e-9    # 100 -> 110

    e2 = rows["e2"]
    assert e2["ret_15m"] is None and e2["ret_1h"] is None and e2["ret_eod"] is None
    assert abs(e2["ret_1d"] - 0.02) < 1e-9

    # both fully resolved -> the work queue is empty
    assert of_db.events_missing_outcomes(db_path=db) == []


def test_fill_outcomes_partial_until_horizons_elapse(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    of_db.init_db(db)
    of_db.insert_event(_event("e1", "2026-07-10T14:30:00Z"), db_path=db)

    class _NoFutureDaily(_FakeAdapter):
        def get_stock_bars(self, symbol, *, timeframe="1Day", days=180):
            return [_bar("2026-07-10T04:00:00Z", 100.0)]  # only event day so far

    now = datetime(2026, 7, 11, tzinfo=UTC)  # weekend after the event
    fill_outcomes(_NoFutureDaily(), now_utc=now, db_path=db)
    rows = {r["id"]: r for r in of_db.recent_events(limit=10, db_path=db)}
    e1 = rows["e1"]
    assert e1["ret_15m"] is not None and e1["ret_1d"] is None
    # not resolved -> stays in the queue for future runs
    assert [e["id"] for e in of_db.events_missing_outcomes(db_path=db)] == ["e1"]
