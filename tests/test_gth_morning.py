"""GTH morning line — census math, robust-z baseline, report gating.

The z window is the census window (4:00-9:15 ET): live-tape rows and the
npz-derived nightly.json history must stay interchangeable, so the census
metrics here pin the same definitions tools/gth_overnight_census.py measured
260 nights with.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import signals.reports as reports_mod
from signals.options_flow import gth_morning
from signals.reports import generate_overnight_flow_report

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 8, 11)  # a Tuesday


def _ns(y, m, d, hh, mm) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp() * 1e9)


def _row(t_ns: int, sym: str, cents: int, size: int, cond: str = "I") -> dict:
    return {"t": t_ns, "s": sym, "p": cents, "z": size, "c": cond, "x": "C"}


def _night_rows() -> list[dict]:
    return [
        # Evening slice (Mon 22:00 ET) — full-night only, never in the z slice.
        _row(_ns(2026, 8, 10, 22, 0), "XSP260811C00640000", 100, 2),
        # 0DTE put at 05:00 — $2,000.
        _row(_ns(2026, 8, 11, 5, 0), "SPXW260811P06300000", 200, 10),
        # Long-dated call in the 8:30 macro window — $50,000.
        _row(_ns(2026, 8, 11, 8, 45), "SPX260918C06500000", 10000, 5, "f"),
    ]


def test_census_from_rows_matches_census_tool_definitions() -> None:
    c = gth_morning.census_from_rows(_night_rows(), SESSION)
    assert c["prints"] == 2 and c["contracts"] == 15
    assert c["notional"] == 52_000.0
    assert abs(c["put_share_notional"] - 2000 / 52000) < 1e-4
    assert abs(c["zero_dte_share_notional"] - 2000 / 52000) < 1e-4
    assert abs(c["macro_window_share"] - 50000 / 52000) < 1e-4
    assert set(c["by_root"]) == {"SPXW", "SPX"}
    assert c["top_prints"][0]["sym"] == "SPX260918C06500000"
    assert c["codes"] == {"I": 1, "f": 1}
    # The evening slice rides in full_night context, never the z slice.
    assert c["full_night"] == {
        "prints": 3, "contracts": 17, "notional": 52_200.0,
        "evening_notional": 200.0,
    }


def _history(n_low: int = 30, n_high: int = 30) -> list[dict]:
    """61 nights: 30x$80M, 1x$100M, 30x$120M -> median $100M, MAD $20M."""
    rows = []
    vals = [80e6] * n_low + [100e6] + [120e6] * n_high
    for i, v in enumerate(vals):
        rows.append(
            {"session": f"2026-06-{i + 1:02d}" if i < 30 else f"2026-07-{i - 29:02d}",
             "prints": 1000, "notional": v, "put_share_notional": 0.5}
        )
    return rows


def test_baseline_z_is_robust_median_mad() -> None:
    sigma = 1.4826 * 20e6
    tonight = {"session": "2026-08-11", "prints": 500,
               "notional": 100e6 + 5 * sigma, "put_share_notional": 0.9}
    b = gth_morning.baseline_block(tonight, _history())
    assert b["nights"] == 61
    assert abs(b["z"] - 5.0) < 0.01
    assert b["z"] >= gth_morning.ALERT_Z
    assert b["put_share_pctile"] == 1.0  # more put-tilted than every baseline night


def test_baseline_withheld_below_min_nights_and_on_zero_sigma() -> None:
    tonight = {"session": "2026-08-11", "prints": 500, "notional": 900e6}
    thin = gth_morning.baseline_block(tonight, _history()[:10])
    assert thin["z"] is None and "baseline building" in thin["note"]
    flat = [dict(r, notional=100e6) for r in _history()]
    degenerate = gth_morning.baseline_block(tonight, flat)
    assert degenerate["z"] is None  # MAD 0 — never divide by a fake yardstick


def test_append_history_replaces_same_session_and_skips_empty(tmp_path) -> None:
    path = tmp_path / "nightly.json"
    gth_morning.append_history({"session": "2026-08-11", "prints": 5, "notional": 1.0}, path)
    gth_morning.append_history({"session": "2026-08-11", "prints": 9, "notional": 2.0}, path)
    gth_morning.append_history({"session": "2026-08-12", "prints": 0}, path)  # feed failure
    rows = json.loads(path.read_text())
    assert [r["session"] for r in rows] == ["2026-08-11"]
    assert rows[0]["prints"] == 9


def test_compute_morning_line_end_to_end(tmp_path) -> None:
    tape_dir = tmp_path / "tape"
    tape_dir.mkdir()
    (tape_dir / "2026-08-11.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _night_rows()) + "\ngarbage-tail\n"
    )
    history_path = tmp_path / "nightly.json"
    history_path.write_text(json.dumps(_history()))
    line = gth_morning.compute_morning_line(
        SESSION, tape_dir=tape_dir, history_path=history_path
    )
    assert line["slice"]["prints"] == 2
    assert line["baseline"]["nights"] == 61
    assert line["caveats"]  # honesty strings survive into every transport
    assert gth_morning.compute_morning_line(
        date(2026, 8, 12), tape_dir=tape_dir, history_path=history_path
    ) is None  # no tape file -> scheduler retries


def _canned_line(z: float | None) -> dict:
    return {
        "session": "2026-08-11",
        "generated_at": "2026-08-11T09:20:00-04:00",
        "window_et": "04:00-09:15",
        "slice": {
            "session": "2026-08-11", "prints": 25_000, "notional": 142e6,
            "put_share_notional": 0.57, "zero_dte_share_notional": 0.24,
            "macro_window_share": 0.21,
            "full_night": {"prints": 31_000, "contracts": 90_000,
                           "notional": 168e6, "evening_notional": 26e6},
            "top_prints": [{"sym": "SPXW260811P06300000", "size": 1200,
                            "price": 3.4, "notional": 408000.0, "cond": "f",
                            "et": "06:42:11"}],
        },
        "baseline": {"nights": 120, "z": z, "median": 130e6, "sigma": 24e6},
        "caveats": list(gth_morning.CAVEATS),
    }


def test_report_gates_on_gth_close_and_trading_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(reports_mod, "_overnight_line_sync", lambda s: _canned_line(0.5))
    db = tmp_path / "t.db"
    early = datetime(2026, 8, 11, 9, 10, tzinfo=ET)  # GTH still open
    assert asyncio.run(generate_overnight_flow_report(db_path=db, now_et=early)) is None
    sunday = datetime(2026, 8, 9, 10, 0, tzinfo=ET)
    assert asyncio.run(generate_overnight_flow_report(db_path=db, now_et=sunday)) is None


def test_report_saves_line_and_pushes_only_on_outlier(tmp_path, monkeypatch) -> None:
    import signals.notify as notify_mod

    pushes: list[dict] = []

    async def _capture(**kwargs):
        pushes.append(kwargs)

    monkeypatch.setattr(notify_mod, "notify", _capture)
    db = tmp_path / "t.db"
    now = datetime(2026, 8, 11, 9, 20, tzinfo=ET)

    monkeypatch.setattr(reports_mod, "_overnight_line_sync", lambda s: _canned_line(0.5))
    report = asyncio.run(generate_overnight_flow_report(db_path=db, now_et=now))
    assert report["kind"] == "overnight_flow"
    assert "z +0.5" in report["title"] and "OUTLIER" not in report["title"]
    assert report["payload"]["dedup_key"] == "overnight_flow"
    assert "$142.0M" in report["body"] and "puts 57%" in report["body"]
    assert pushes == []  # ordinary night: inbox report only, phone stays quiet

    monkeypatch.setattr(reports_mod, "_overnight_line_sync", lambda s: _canned_line(6.1))
    report = asyncio.run(generate_overnight_flow_report(db_path=db, now_et=now))
    assert "OUTLIER" in report["title"]
    assert len(pushes) == 1
    assert pushes[0]["severity"] == "warning"
    assert "not a prediction" in pushes[0]["body"]


def test_latest_line_prefers_file_then_history(tmp_path) -> None:
    line_path = tmp_path / "morning_line.json"
    hist_path = tmp_path / "nightly.json"
    hist = [
        {"session": "2026-08-06", "prints": 5, "notional": 100.0, "put_share_notional": 0.5},
        {"session": "2026-08-07", "prints": 7, "notional": 240.0, "put_share_notional": 0.6},
        {"session": "2026-08-08", "prints": 0},  # feed-failure night never surfaces
    ]
    hist_path.write_text(json.dumps(hist), encoding="utf-8")

    line = gth_morning.latest_line(path=line_path, history_path=hist_path)
    assert line["session"] == "2026-08-07"
    assert line["source"] == "history_fallback"
    assert line["generated_at"] is None
    assert line["slice"]["notional"] == 240.0
    # The fallback is z-scored only against nights BEFORE its own session.
    assert line["baseline"]["nights"] == 1

    # A written morning line always wins over the fallback.
    line_path.write_text(json.dumps({"session": "2026-08-10"}), encoding="utf-8")
    assert (
        gth_morning.latest_line(path=line_path, history_path=hist_path)["session"]
        == "2026-08-10"
    )


def test_latest_line_none_without_history(tmp_path) -> None:
    assert (
        gth_morning.latest_line(
            path=tmp_path / "missing.json", history_path=tmp_path / "nohist.json"
        )
        is None
    )
