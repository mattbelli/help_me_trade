"""Opening index-pressure gauge — pure aggregator, calibration store, API route."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from db.index_pressure import (
    baseline_days,
    day_summary,
    minute_series,
    upsert_minutes,
)
from signals.options_flow.opening_pressure import (
    PressureGauge,
    window_minute,
)

_ET = ZoneInfo("America/New_York")


def _ts(h: int, m: int, s: int = 0) -> int:
    """ts_ms for a fixed ET wall time on a fixed trading day."""
    return int(datetime(2026, 7, 16, h, m, s, tzinfo=_ET).timestamp() * 1000)


def _print(
    *,
    sym: str = "SPY",
    right: str = "call",
    direction: str = "buy_side",
    price: float = 2.0,
    size: int = 100,
    ts_ms: int | None = None,
    **extra,
) -> dict:
    p = {
        "underlying": sym,
        "right": right,
        "direction": direction,
        "price": price,
        "size": size,
        "ts_ms": ts_ms if ts_ms is not None else _ts(9, 45),
        "conditions": [],
    }
    p.update(extra)
    return p


# --- window geometry ---------------------------------------------------------------


def test_window_minute_edges():
    assert window_minute(_ts(9, 29, 59)) is None
    assert window_minute(_ts(9, 30, 0)) == 0
    assert window_minute(_ts(9, 45, 30)) == 15
    assert window_minute(_ts(9, 59, 59)) == 29
    assert window_minute(_ts(10, 0, 0)) is None


def test_close_window_edges():
    assert window_minute(_ts(15, 29, 59), "close") is None
    assert window_minute(_ts(15, 30, 0), "close") == 0
    assert window_minute(_ts(15, 59, 59), "close") == 29
    assert window_minute(_ts(16, 0, 0), "close") is None


def test_close_gauge_collects_only_its_window():
    g = PressureGauge("close")
    g.add_print(_print(ts_ms=_ts(9, 45)))  # open-window print — ignored
    g.add_print(_print(ts_ms=_ts(15, 45)))
    snap = g.snapshot()["SPY"]
    assert snap["n_prints"] == 1
    assert snap["bull_usd"] == 20000.0


# --- bull/bear mapping ---------------------------------------------------------------


def test_direction_right_mapping():
    op = PressureGauge()
    prem = 2.0 * 100 * 100.0  # $20k each
    op.add_print(_print(right="call", direction="buy_side"))  # bull
    op.add_print(_print(right="put", direction="sell_side"))  # bull
    op.add_print(_print(right="put", direction="buy_side"))  # bear
    op.add_print(_print(right="call", direction="sell_side"))  # bear
    op.add_print(_print(right="call", direction="undetermined"))  # unclassified

    snap = op.snapshot()["SPY"]
    assert snap["bull_usd"] == prem * 2
    assert snap["bear_usd"] == prem * 2
    assert snap["unclassified_usd"] == prem
    assert snap["net_usd"] == 0.0
    assert snap["n_prints"] == 5
    assert abs(snap["classified_frac"] - 0.8) < 1e-9


def test_filters_universe_window_spreads_dust():
    op = PressureGauge()
    op.add_print(_print(sym="XYZ"))  # not in universe
    op.add_print(_print(ts_ms=_ts(10, 30)))  # outside window
    op.add_print(_print(likely_spread=True))  # spread-tagged
    op.add_print(_print(spread_leg=True))
    op.add_print(_print(price=0.01, size=1))  # dust ($1)
    op.add_print(_print(price=-1.0))  # garbage
    assert op.snapshot() == {}


def test_minute_rows_shape():
    op = PressureGauge()
    op.add_print(_print(ts_ms=_ts(9, 30, 5)))
    op.add_print(_print(ts_ms=_ts(9, 30, 40)))
    op.add_print(_print(sym="QQQ", ts_ms=_ts(9, 59, 1), direction="sell_side"))
    rows = op.minute_rows("2026-07-16")
    assert rows == [
        ("QQQ", "2026-07-16", 29, 0.0, 20000.0, 0.0, 1, None),
        ("SPY", "2026-07-16", 0, 40000.0, 0.0, 0.0, 2, None),
    ]


# --- calibration store ---------------------------------------------------------------


def test_upsert_is_last_write_wins(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 100.0, 0.0, 0.0, 1, "gauge_lane")], db_path=db
    )
    # same minute re-flushed with grown totals — TOTALS replace, never accumulate;
    # coverage is provenance and degrades to 'mixed' rather than being relabeled.
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 250.0, 50.0, 25.0, 3, "market_wide")], db_path=db
    )
    rows = minute_series("2026-07-16", "SPY", db_path=db)
    assert rows == [
        {
            "minute": 0,
            "bull_usd": 250.0,
            "bear_usd": 50.0,
            "unclassified_usd": 25.0,
            "n_prints": 3,
            "coverage": "mixed",
        }
    ]


def test_upsert_never_relabels_coverage(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [("SPY", "2026-07-16", 5, 100.0, 0.0, 0.0, 1, "gauge_lane")], db_path=db
    )
    # A post-restart flush that lost the mode (None) must not erase provenance.
    upsert_minutes([("SPY", "2026-07-16", 5, 90.0, 0.0, 0.0, 1, None)], db_path=db)
    (row,) = minute_series("2026-07-16", "SPY", db_path=db)
    assert row["coverage"] == "gauge_lane"
    assert row["bull_usd"] == 90.0  # totals still last-write-wins


def test_coverage_stamped_at_accrual_and_mixes():
    g = PressureGauge()
    g.add_print(_print(ts_ms=_ts(9, 40, 5)), mode="gauge_lane")
    g.add_print(_print(ts_ms=_ts(9, 40, 30)), mode="gauge_lane")
    (row,) = g.minute_rows("2026-07-16")
    assert row[7] == "gauge_lane"
    g.add_print(_print(ts_ms=_ts(9, 40, 50)), mode="market_wide")
    (row,) = g.minute_rows("2026-07-16")
    assert row[7] == "mixed"


def test_record_day_tracks_writers_and_finalizes_once(tmp_path: Path):
    from db.index_pressure import day_record, record_day

    db = tmp_path / "ip.db"
    record_day(
        date="2026-07-16", window="open", session_type="full", minutes_observed=12,
        syms=5, coverage_modes="gauge_lane", writer_pid=100, finalized=False, db_path=db,
    )
    record_day(
        date="2026-07-16", window="open", session_type="full", minutes_observed=30,
        syms=9, coverage_modes="gauge_lane", writer_pid=200, finalized=True, db_path=db,
    )
    rec = day_record("2026-07-16", "open", db_path=db)
    assert rec["writers"] == 2  # pid change = restart marker
    assert rec["minutes_observed"] == 30
    first_final = rec["finalized_at"]
    assert first_final is not None
    record_day(
        date="2026-07-16", window="open", session_type="full", minutes_observed=30,
        syms=9, coverage_modes="gauge_lane", writer_pid=200, finalized=True, db_path=db,
    )
    assert day_record("2026-07-16", "open", db_path=db)["finalized_at"] == first_final


def test_is_early_close_dates():
    from datetime import date

    from trading.market_calendar import is_early_close

    assert is_early_close(date(2026, 11, 27))  # day after Thanksgiving 2026
    assert is_early_close(date(2026, 12, 24))  # Thursday Christmas Eve
    assert is_early_close(date(2025, 7, 3))  # Thursday, July 4th on Friday
    assert not is_early_close(date(2026, 7, 3))  # observed holiday (Jul 4 = Sat)
    assert not is_early_close(date(2027, 12, 24))  # observed holiday (Dec 25 = Sat)
    assert not is_early_close(date(2026, 7, 16))  # ordinary Thursday


def test_day_summary_totals_and_order(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [
            ("SPY", "2026-07-16", 0, 100.0, 300.0, 100.0, 2, None),
            ("SPY", "2026-07-16", 1, 100.0, 0.0, 0.0, 1, None),
            ("QQQ", "2026-07-16", 5, 900.0, 100.0, 0.0, 4, None),
        ],
        db_path=db,
    )
    rows = day_summary("2026-07-16", db_path=db)
    assert [r["underlying"] for r in rows] == ["QQQ", "SPY"]  # biggest |net| first
    qqq, spy = rows
    assert qqq["net_usd"] == 800.0
    assert spy["net_usd"] == -100.0
    assert spy["unclassified_usd"] == 100.0
    assert spy["minutes_with_data"] == 2
    assert abs(spy["classified_frac"] - (500.0 / 600.0)) < 1e-9
    assert baseline_days(db_path=db) == 1
    assert day_summary("2026-07-15", db_path=db) == []


# --- API route ------------------------------------------------------------------------


def test_api_index_pressure_route():
    from api.main import app

    # A synthetic far-past date so the shared test DB can't collide with other tests.
    upsert_minutes(
        [
            ("SPY", "2020-01-02", 0, 1_000_000.0, 250_000.0, 500_000.0, 40, "gauge_lane"),
            ("SPY", "2020-01-02", 3, 200_000.0, 0.0, 0.0, 5, "gauge_lane"),
        ],
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/index-pressure", params={"date": "2020-01-02", "series": "spy"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2020-01-02"
    assert body["window_et"] == "09:30-10:00"
    (row,) = body["rows"]
    assert row["underlying"] == "SPY"
    assert row["net_usd"] == 950_000.0
    assert row["n_prints"] == 45
    assert body["series"]["underlying"] == "SPY"
    assert [m["minute"] for m in body["series"]["minutes"]] == [0, 3]
    assert isinstance(body["baseline_days"], int)


# --- perception resource ---------------------------------------------------------------


def test_perception_resource_reads_and_caveats(tmp_path: Path):
    from ai.perception.resources import _read_index_pressure

    db = tmp_path / "ip.db"
    upsert_minutes(
        [("QQQ", "2026-07-16", 2, 750_000.0, 250_000.0, 0.0, 9, "gauge_lane")], db_path=db
    )
    rows = _read_index_pressure({"date": "2026-07-16"}, db_path=db)
    (row,) = rows
    assert row["underlying"] == "QQQ"
    assert row["net_usd"] == 500_000.0
    assert "uncalibrated" in row["caveat"]  # 1 baseline day << 20
    assert "100% side-classified" in row["caveat"]

    empty = _read_index_pressure({"date": "2026-07-14"}, db_path=db)
    assert empty == [
        {
            "date": "2026-07-14",
            "unavailable": True,
            "window": "open",
            "window_et": "09:30-10:00",
        }
    ]


def test_windows_are_separate_series(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 100.0, 0.0, 0.0, 1, None)], db_path=db
    )
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 0.0, 900.0, 0.0, 2, None)], window="close", db_path=db
    )
    assert day_summary("2026-07-16", db_path=db)[0]["net_usd"] == 100.0
    assert day_summary("2026-07-16", window="close", db_path=db)[0]["net_usd"] == -900.0
    assert baseline_days(db_path=db) == 1
    assert baseline_days(window="close", db_path=db) == 1


def test_api_close_window_param():
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/index-pressure", params={"date": "2020-01-02", "window": "close"}
        )
        bad = client.get("/api/index-pressure", params={"window": "midday"})
    assert resp.status_code == 200
    assert resp.json()["window_et"] == "15:30-16:00"
    assert resp.json()["rows"] == []  # only the open window was seeded for this date
    assert bad.status_code == 400


def test_rolling_detectors_still_exclude_index_zero_dte():
    """The gauge lane subscribes index 0-1 DTE FOR THE GAUGE ONLY — bursts/clusters
    must not change character the day the lane ships (audit F1 pin)."""
    from signals.options_flow.rolling import RollingFlow

    r = RollingFlow()
    r.add_print(_print(dte=0, occ_symbol="SPY260716C00630000"))
    r.add_print(_print(sym="NVDA", dte=0, occ_symbol="NVDA260716C00170000"))
    assert "SPY" not in r._buckets  # index 0DTE pinned out
    assert "NVDA" in r._buckets  # single names keep 0DTE, as before


def test_rolling_pin_covers_spxw():
    """Review H1: SPXW is the parsed underlying of SPX-root contracts — the
    detector pin must exclude its 0-1 DTE too."""
    from signals.options_flow.rolling import RollingFlow

    r = RollingFlow()
    r.add_print(_print(sym="SPXW", dte=0, occ_symbol="SPXW260716C05500000"))
    assert "SPXW" not in r._buckets


def test_record_day_is_monotone_never_clobbered(tmp_path: Path):
    """Review H2: a post-restart writer that observed less (or nothing) must never
    shrink a completeness row an earlier writer recorded."""
    from db.index_pressure import day_record, record_day

    db = tmp_path / "ip.db"
    record_day(
        date="2026-07-16", window="open", session_type="full", minutes_observed=30,
        syms=13, coverage_modes="gauge_lane", writer_pid=100, finalized=True, db_path=db,
    )
    # fresh evening process, empty gauges, would-be zero row
    record_day(
        date="2026-07-16", window="open", session_type="full", minutes_observed=0,
        syms=0, coverage_modes=None, writer_pid=200, finalized=True, db_path=db,
    )
    rec = day_record("2026-07-16", "open", db_path=db)
    assert rec["minutes_observed"] == 30  # not clobbered to 0
    assert rec["syms"] == 13
    assert rec["coverage_modes"] == "gauge_lane"  # NULL never erases provenance
    assert rec["writers"] == 2  # the restart is still visible
