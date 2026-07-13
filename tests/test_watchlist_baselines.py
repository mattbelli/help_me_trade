"""Watchlist 52w/ATR/SMA baseline computation (slow tier of /api/watchlist/market)."""

from types import SimpleNamespace

from api.main import _compute_wl_baseline


def _bars(n: int, start: float = 100.0):
    out = []
    for i in range(n):
        px = start + i
        out.append(
            SimpleNamespace(
                open=px, high=px + 1.0, low=px - 1.0, close=px, volume=1000
            )
        )
    return out


def test_baseline_too_few_bars():
    assert _compute_wl_baseline([]) is None
    assert _compute_wl_baseline(_bars(19)) is None


def test_baseline_values_on_ramp():
    bars = _bars(300)  # closes 100..399
    b = _compute_wl_baseline(bars)
    assert b is not None
    # last 252 bars: closes 148..399 → high = 399+1, low = 148-1
    assert b["w52_high"] == 400.0
    assert b["w52_low"] == 147.0
    # steady +1/day ramp with 2-pt daily range → ATR ~2, SMAs centered
    assert b["atr14"] is not None and 1.5 <= b["atr14"] <= 3.0
    assert b["sma50"] == sum(range(350, 400)) / 50
    assert b["sma200"] == sum(range(200, 400)) / 200


def test_baseline_short_history_partial():
    # 60 bars: enough for atr/sma50, not sma200 — partial baselines, no crash.
    b = _compute_wl_baseline(_bars(60))
    assert b is not None
    assert b["sma50"] is not None
    assert b["sma200"] is None
