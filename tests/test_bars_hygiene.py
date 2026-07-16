"""Display-path bar hygiene — pins the SPY 2026-02-02 vendor bad-low incident."""

from __future__ import annotations

from brokers.bars_hygiene import sanitize_bar_dicts


def _bar(o: float, h: float, lo: float, c: float, t: str = "2026-02-02T05:00:00Z") -> dict:
    return {"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}


def test_spy_incident_low_clamped() -> None:
    # Alpaca SIP raw daily bar, verified live 2026-07-09.
    bars = [_bar(689.58, 696.93, 69.005, 695.41)]
    out = sanitize_bar_dicts("SPY", bars)
    assert out[0]["low"] == 689.58
    assert out[0]["high"] == 696.93  # untouched


def test_corrupt_high_clamped() -> None:
    bars = [_bar(100.0, 950.0, 99.0, 101.0)]
    out = sanitize_bar_dicts("XYZ", bars)
    assert out[0]["high"] == 101.0
    assert out[0]["low"] == 99.0


def test_real_extreme_but_plausible_wicks_untouched() -> None:
    # -45% flush with recovery and a +90% blowoff stay (inside the ratios).
    bars = [_bar(10.0, 19.0, 5.5, 9.5)]
    out = sanitize_bar_dicts("BIO", bars)
    assert out[0]["low"] == 5.5
    assert out[0]["high"] == 19.0


def test_garbage_rows_never_raise() -> None:
    bars = [
        {"time": "x", "open": None, "high": 1, "low": 1, "close": 1, "volume": 0},
        {"time": "y"},
        _bar(0.0, 0.0, 0.0, 0.0),
    ]
    out = sanitize_bar_dicts("JUNK", bars)
    assert len(out) == 3
