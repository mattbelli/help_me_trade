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
    OpeningPressure,
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


# --- bull/bear mapping ---------------------------------------------------------------


def test_direction_right_mapping():
    op = OpeningPressure()
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
    op = OpeningPressure()
    op.add_print(_print(sym="XYZ"))  # not in universe
    op.add_print(_print(ts_ms=_ts(10, 30)))  # outside window
    op.add_print(_print(likely_spread=True))  # spread-tagged
    op.add_print(_print(spread_leg=True))
    op.add_print(_print(price=0.01, size=1))  # dust ($1)
    op.add_print(_print(price=-1.0))  # garbage
    assert op.snapshot() == {}


def test_minute_rows_shape():
    op = OpeningPressure()
    op.add_print(_print(ts_ms=_ts(9, 30, 5)))
    op.add_print(_print(ts_ms=_ts(9, 30, 40)))
    op.add_print(_print(sym="QQQ", ts_ms=_ts(9, 59, 1), direction="sell_side"))
    rows = op.minute_rows("2026-07-16")
    assert rows == [
        ("QQQ", "2026-07-16", 29, 0.0, 20000.0, 0.0, 1),
        ("SPY", "2026-07-16", 0, 40000.0, 0.0, 0.0, 2),
    ]


# --- calibration store ---------------------------------------------------------------


def test_upsert_is_last_write_wins(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 100.0, 0.0, 0.0, 1)], coverage="watchlist_capped", db_path=db
    )
    # same minute re-flushed with grown totals — replaces, never accumulates
    upsert_minutes(
        [("SPY", "2026-07-16", 0, 250.0, 50.0, 25.0, 3)], coverage="market_wide", db_path=db
    )
    rows = minute_series("2026-07-16", "SPY", db_path=db)
    assert rows == [
        {"minute": 0, "bull_usd": 250.0, "bear_usd": 50.0, "unclassified_usd": 25.0, "n_prints": 3}
    ]


def test_day_summary_totals_and_order(tmp_path: Path):
    db = tmp_path / "ip.db"
    upsert_minutes(
        [
            ("SPY", "2026-07-16", 0, 100.0, 300.0, 100.0, 2),
            ("SPY", "2026-07-16", 1, 100.0, 0.0, 0.0, 1),
            ("QQQ", "2026-07-16", 5, 900.0, 100.0, 0.0, 4),
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
            ("SPY", "2020-01-02", 0, 1_000_000.0, 250_000.0, 500_000.0, 40),
            ("SPY", "2020-01-02", 3, 200_000.0, 0.0, 0.0, 5),
        ],
        coverage="market_wide",
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
    upsert_minutes([("QQQ", "2026-07-16", 2, 750_000.0, 250_000.0, 0.0, 9)], db_path=db)
    rows = _read_index_pressure({"date": "2026-07-16"}, db_path=db)
    (row,) = rows
    assert row["underlying"] == "QQQ"
    assert row["net_usd"] == 500_000.0
    assert "uncalibrated" in row["caveat"]  # 1 baseline day << 20
    assert "100% side-classified" in row["caveat"]

    empty = _read_index_pressure({"date": "2026-07-14"}, db_path=db)
    assert empty == [
        {"date": "2026-07-14", "unavailable": True, "window_et": "09:30-10:00"}
    ]
