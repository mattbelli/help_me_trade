"""Hermetic tests for tools/hedge_clock_skew.py (roadmap 3.1, spec 2026-08-01)."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pytest

from tools.hedge_clock_skew import (
    event_t0_ns,
    fetch_equity_trades,
    granularity,
    onset,
    parse_t_ns,
    summarize,
)

NS = 1_000_000_000
T0 = 1_800_000_000 * NS  # arbitrary epoch anchor


def _tape(baseline_per_bin: float, burst_at_ms: int | None, burst_count: int = 50) -> np.ndarray:
    """Synthetic tape: uniform baseline over [T0-60s, T0), optional burst bin."""
    ts = []
    n_base = int(baseline_per_bin * 600)  # 600 baseline bins of 100ms
    if n_base:
        # integer spacing — float linspace at 1.8e18 ns rounds by ~256ns
        offsets = np.arange(n_base, dtype=np.int64) * (60 * NS // n_base)
        ts.extend((T0 - 60 * NS + offsets).tolist())
    if burst_at_ms is not None:
        start = T0 + burst_at_ms * 1_000_000
        ts.extend(range(start, start + burst_count * 1_000_000, 1_000_000))  # 1ms apart
    return np.sort(np.array(ts, dtype=np.int64))


class TestOnset:
    def test_burst_detected_first_trade_in_bin(self):
        r = onset(_tape(2.0, burst_at_ms=230), T0)
        assert r["burst"] is True
        # burst bin is [200,300)ms; first trade inside it is at 230ms
        assert r["onset_ms"] == pytest.approx(230.0)
        assert r["baseline_per_bin"] == pytest.approx(2.0)
        assert r["threshold"] == pytest.approx(10.0)

    def test_no_burst_reports_not_fabricates(self):
        r = onset(_tape(2.0, burst_at_ms=None), T0)
        assert r["burst"] is False
        assert r["onset_ns"] is None and r["onset_ms"] is None

    def test_min_trades_floor_on_dead_baseline(self):
        # zero baseline -> threshold is min_trades, not 0; 4 trades never qualify
        r = onset(_tape(0.0, burst_at_ms=100, burst_count=4), T0)
        assert r["threshold"] == 5.0
        assert r["burst"] is False
        r2 = onset(_tape(0.0, burst_at_ms=100, burst_count=5), T0)
        assert r2["burst"] is True

    def test_sub_threshold_elevation_never_qualifies(self):
        # 4.9x baseline stays below the 5x rule
        r = onset(_tape(10.0, burst_at_ms=0, burst_count=49), T0)
        assert r["burst"] is False

    def test_first_trade_delta_is_secondary_not_onset(self):
        # lone early print at +5ms, real burst at +400ms: onset is the burst
        tape = np.sort(
            np.concatenate([_tape(2.0, burst_at_ms=400), [T0 + 5 * 1_000_000]])
        ).astype(np.int64)
        r = onset(tape, T0)
        assert r["first_trade_ms"] == pytest.approx(5.0)
        assert r["onset_ms"] == pytest.approx(400.0)

    def test_empty_horizon(self):
        r = onset(_tape(2.0, burst_at_ms=None)[:1200], T0)
        assert r["burst"] is False and r["first_trade_ms"] is None


class TestGranularity:
    def test_ns_feed(self):
        g = granularity(np.array([T0 + 1, T0 + 123456789], dtype=np.int64))
        assert g["sub_us_digits_pct"] == 1.0

    def test_ms_truncated_feed(self):
        g = granularity((np.arange(10, dtype=np.int64) * 1_000_000) + T0)
        assert g["ms_multiple_pct"] == 1.0
        assert g["sub_us_digits_pct"] == 0.0

    def test_empty(self):
        assert granularity(np.array([], dtype=np.int64)) == {"n": 0}


class TestTimestamps:
    def test_parse_ns_precision_survives(self):
        ns = parse_t_ns("2026-07-29T18:00:00.123456789Z")
        assert ns % NS == 123456789

    def test_event_t0_dst_honest(self):
        # winter: 14:00 ET = 19:00 UTC; summer: 14:00 ET = 18:00 UTC
        winter = event_t0_ns(date(2025, 12, 10), "14:00")
        summer = event_t0_ns(date(2026, 7, 29), "14:00")
        assert winter == parse_t_ns("2025-12-10T19:00:00Z")
        assert summer == parse_t_ns("2026-07-29T18:00:00Z")


class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeClient:
    """Two pages behind next_page_token; records requested params."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params)))
        if params.get("page_token"):
            return _FakeResp(
                {
                    "trades": [
                        {"t": "2026-07-29T18:00:00.000000200Z", "p": 2.0, "s": 7, "x": "D"}
                    ],
                    "next_page_token": None,
                }
            )
        return _FakeResp(
            {
                "trades": [
                    {"t": "2026-07-29T17:59:59.000000100Z", "p": 1.0, "s": 5, "x": "V"}
                ],
                "next_page_token": "tok",
            }
        )


class TestFetch:
    def test_pagination_stitches_and_keeps_fields(self):
        client = _FakeClient()
        t0 = parse_t_ns("2026-07-29T18:00:00Z")
        out = fetch_equity_trades(client, "http://x", "SPY", t0 - 60 * NS, t0 + 10 * NS)
        assert len(client.calls) == 2
        assert client.calls[1][1]["page_token"] == "tok"
        assert out["t_ns"].tolist() == [
            parse_t_ns("2026-07-29T17:59:59.000000100Z"),
            parse_t_ns("2026-07-29T18:00:00.000000200Z"),
        ]
        assert out["size"].tolist() == [5, 7]
        assert out["exchange"].tolist() == ["V", "D"]

    def test_window_is_rfc3339_utc(self):
        client = _FakeClient()
        t0 = parse_t_ns("2026-07-29T18:00:00Z")
        fetch_equity_trades(client, "http://x", "SPY", t0 - 60 * NS, t0 + 10 * NS)
        params = client.calls[0][1]
        assert params["start"] == "2026-07-29T17:59:00.000000000Z"
        assert params["end"] == "2026-07-29T18:00:10.000000000Z"


class TestSummarize:
    def _row(self, label, delta):
        return {"label": label, "delta_ms": delta}

    def test_grain_rule_floor_100ms(self):
        s = summarize([self._row("fomc", 3.0), self._row("fomc", -8.0)])
        assert s["min_leadlag_grain_ms"] == 100.0
        assert s["stop_investigate"] is False

    def test_grain_rule_p90_binds_and_stop_fires(self):
        rows = [self._row("fomc", 1500.0)] + [self._row("fomc", 10.0)] * 9
        s = summarize(rows)
        assert s["min_leadlag_grain_ms"] > 100.0
        assert s["stop_investigate"] is True

    def test_no_burst_rows_contribute_nothing(self):
        s = summarize([{"label": "ism", "delta_ms": None, "status": "NO-BURST (option)"}])
        assert s["fomc"] is None and s["all"] is None
        assert "min_leadlag_grain_ms" not in s

    def test_fomc_primary_over_all(self):
        s = summarize([self._row("fomc", 10.0), self._row("ism", 900.0)])
        # grain from the FOMC (primary) distribution, stop checks every event
        assert s["min_leadlag_grain_ms"] == 100.0
        assert s["stop_investigate"] is False
