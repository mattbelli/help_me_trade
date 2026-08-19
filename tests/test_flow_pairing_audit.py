"""flow_pairing_audit — parser, session summary, verdicts, intraday checks.

The stats-line fixtures mirror the REAL journald format emitted by
signals/options_flow/shadow_pairing._log_stats (verified against the
2026-08-06 journal): dbn/alp are process-cumulative, matched/resolved are
per-matcher-epoch (reset on reconnect), `a p50` is a per-minute window.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tools.flow_pairing_audit import (
    BATCH_FLOOR,
    GAP_MAX,
    LIVE_FLOOR,
    Sample,
    intraday_alarms,
    parse_stats_lines,
    session_summary,
    verdict,
)


def _line(ts: str, pid: int, dbn: int, alp: int, matched: int, resolved: int,
          a_p50: int, resets: int, final: bool = False) -> str:
    pct = 100.0 * matched / resolved if resolved else 0.0
    return (
        f"{ts}+00:00 serv python[{pid}]: 2026-08-06 00:00:00,000 INFO "
        f"signals.options_flow.shadow_pairing: pairing shadow"
        f"{' FINAL' if final else ''}: dbn={dbn:,} alp={alp:,} "
        f"matched={matched:,} ({pct:.1f}% of {resolved:,} resolved) "
        f"enriched=1 att_pend=0 repairs=0 tol=0 expired=0 evicted=0 "
        f"late_twin=0 orphans=0 | enrich ≤10ms=1(100.0%) | "
        f"wire_lag_ms d p50=0 p95=2 a p50={a_p50} p95={a_p50 * 2} | "
        f"pending d=0 a=0 keys_peak=1 | roots d=1 a=1 idx_d/a: SPX=1/1 | "
        f"top_a=SPY=1 | acked_wide=True resets={resets} tee_err=0 "
        f"alp_err=0 reconnects=1 rss=1000MB"
    )


def _sample(minute: int, pid: int, dbn: int, alp: int, matched: int,
            resolved: int, a_p50: float, resets: int) -> Sample:
    return Sample(
        ts=datetime(2026, 8, 6, 13, minute, tzinfo=timezone.utc),
        pid=pid, dbn=dbn, alp=alp, matched=matched, resolved=resolved,
        a_p50=a_p50, resets=resets,
    )


class TestParse:
    def test_real_format_roundtrip(self):
        text = "\n".join([
            "-- Boot 0abc --",
            _line("2026-08-06T13:00:58", 2976228, 12_174_255, 12_169_678,
                  0, 0, 13, 1),
            "unrelated journal noise",
            _line("2026-08-06T13:01:58", 2976228, 12_200_000, 12_195_000,
                  25_000, 25_500, 12, 1, final=True),
        ])
        samples = parse_stats_lines(text)
        assert len(samples) == 2
        s = samples[0]
        assert s.pid == 2976228
        assert s.dbn == 12_174_255
        assert s.alp == 12_169_678
        assert s.matched == 0
        assert s.resolved == 0
        assert s.a_p50 == 13
        assert s.resets == 1
        assert s.ts == datetime(2026, 8, 6, 13, 0, 58, tzinfo=timezone.utc)
        assert samples[1].matched == 25_000

    def test_negative_lag_parses(self):
        samples = parse_stats_lines(
            _line("2026-08-06T13:00:58", 1, 100, 100, 5, 5, 2, 0)
            .replace("a p50=2", "a p50=-3"))
        assert samples[0].a_p50 == -3


class TestSessionSummary:
    def test_epoch_deltas_and_process_restart(self):
        # pid 1, epoch resets=1 spans the window start: baseline subtracted.
        # Mid-session reconnect (resets=2): fresh epoch, baseline 0.
        # Process restart (pid 2, resets=0): dbn/alp restart from 0 too.
        samples = [
            _sample(0, 1, 1_000_000, 1_000_000, 500, 600, 10, 1),
            _sample(1, 1, 1_050_000, 1_049_000, 40_500, 41_600, 12, 1),
            _sample(2, 1, 1_100_000, 1_098_000, 20_000, 20_500, 11, 2),
            _sample(3, 2, 30_000, 29_000, 28_000, 28_800, 13, 0),
        ]
        summ = session_summary(samples)
        # matched: (40_500-500) + 20_000 + 28_000
        assert summ["live_matched"] == 88_000
        # resolved: (41_600-600) + 20_500 + 28_800
        assert summ["live_resolved"] == 90_300
        assert summ["live_rate"] == 88_000 / 90_300
        # dbn: (1_100_000-1_000_000) + 30_000
        assert summ["dbn_prints"] == 130_000
        assert summ["alp_prints"] == 127_000
        assert summ["epochs"] == 3

    def test_lag_share_counts_active_minutes_only(self):
        # 3 active minutes (dbn delta >= threshold), one degraded;
        # the idle minute's lag never counts.
        samples = [
            _sample(0, 1, 0, 0, 0, 0, 5, 0),
            _sample(1, 1, 50_000, 50_000, 100, 100, 10, 0),
            _sample(2, 1, 100_000, 100_000, 200, 200, 5_000, 0),
            _sample(3, 1, 150_000, 150_000, 300, 300, 15, 0),
            _sample(4, 1, 150_100, 150_100, 301, 301, 9_000, 0),  # idle
        ]
        summ = session_summary(samples)
        assert summ["active_minutes"] == 3
        assert summ["lag_share_degraded"] == 1 / 3
        assert summ["lag_p50_median_ms"] == 15

    def test_empty(self):
        assert session_summary([]) is None


class TestVerdict:
    BATCH_OK = {"join_rate_dbn": 0.9961}

    def _summ(self, rate: float, lag_share: float = 0.01) -> dict:
        matched = int(1_000_000 * rate)
        return {
            "live_matched": matched, "live_resolved": 1_000_000,
            "live_rate": rate, "dbn_prints": 1_050_000,
            "alp_prints": 1_040_000, "active_minutes": 390,
            "lag_p50_median_ms": 13.0, "lag_share_degraded": lag_share,
            "epochs": 1,
        }

    def test_healthy_day_no_alarms(self):
        row, alarms = verdict("2026-08-04", self._summ(0.975),
                              self.BATCH_OK, recent=True)
        assert alarms == []
        assert row["live_rate"] == 0.975
        assert row["batch_rate"] == 0.9961
        assert row["alarms"] == []

    def test_friday_pattern_trips_all_three(self):
        # 08-07 ground truth: live 74.3%, batch 47.8%, lag degraded all day.
        row, alarms = verdict("2026-08-07", self._summ(0.743, lag_share=0.95),
                              {"join_rate_dbn": 0.478134}, recent=True)
        text = " ".join(alarms).lower()
        assert len(alarms) == 4
        assert "live" in text and "batch" in text
        # batch WORSE than live = vendor tape hole, not a daemon drop
        assert "tape" in text
        assert "lag" in text
        assert row["alarms"] == alarms

    def test_daemon_drop_direction(self):
        # live well under batch = the daemon is dropping codes.
        _, alarms = verdict("2026-08-10", self._summ(0.85),
                            {"join_rate_dbn": 0.995}, recent=True)
        text = " ".join(alarms).lower()
        assert any("daemon" in a.lower() for a in alarms)
        assert "tape" not in text

    def test_missing_live_stats_recent_session_alarms(self):
        row, alarms = verdict("2026-08-07", None, self.BATCH_OK, recent=True)
        assert any("no live pairing stats" in a for a in alarms)
        assert row["live_rate"] is None

    def test_missing_live_stats_old_session_quiet(self):
        _, alarms = verdict("2026-08-04", None, self.BATCH_OK, recent=False)
        assert alarms == []

    def test_thresholds_are_calibrated(self):
        # Pin the measured separation: normal days must stay silent.
        assert LIVE_FLOOR < 0.966       # worst healthy epoch measured 08-04..06
        assert BATCH_FLOOR < 0.9616     # ledger p10
        assert GAP_MAX > 0.03           # 08-04 measured gap ~3pts


class TestIntraday:
    def _window(self, a_p50: float, alp_ratio: float = 1.0) -> list[Sample]:
        out = []
        for i in range(16):
            out.append(_sample(
                i, 1, i * 40_000, int(i * 40_000 * alp_ratio),
                i * 1_000, i * 1_000, a_p50, 0))
        return out

    def test_healthy_window_quiet(self):
        assert intraday_alarms(self._window(15.0)) == []

    def test_quiet_market_no_alarm(self):
        # Too few active minutes (holiday / pre-open): never alarm.
        samples = [_sample(i, 1, i * 10, i * 10, 0, 0, 9_000, 0)
                   for i in range(16)]
        assert intraday_alarms(samples) == []

    def test_degraded_lag_alarms(self):
        alarms = intraday_alarms(self._window(12_000.0))
        assert any("lag" in a.lower() for a in alarms)

    def test_sidecar_starved_alarms(self):
        alarms = intraday_alarms(self._window(15.0, alp_ratio=0.3))
        assert any("starv" in a.lower() for a in alarms)


def test_parse_flow_rs_line_format() -> None:
    """flow_rs (production spine since 08-18) logs `pairing: dbn=1234 …` — no
    'shadow', no thousands separators, no logger prefix. Verbatim from journald."""
    line = (
        "2026-08-19T04:30:19+00:00 serv python[1283130]: pairing: dbn=4451631 "
        "alp=4439079 matched=4392918 (98.7% of 4451631 resolved) enriched=4392918 "
        "att_pend=0 repairs=3 tol=26711 expired=32002 evicted=0 late_twin=18 "
        "orphans=18541 | enrich ≤10ms=2955861(67.3%) ≤100ms=1368413(31.2%) | "
        "wire_lag_ms d p50=NaN p95=NaN a p50=15 p95=17 | pending d=0 a=891 "
        "keys_peak=280270 | roots d=3990 a=3988 idx_d/a: SPX=19729/19925 | "
        "top_a=SPXW=629294 | gth_w=16097 gth_drop=0 | acked_wide=true resets=0 "
        "reconnects=0 rss=447MB"
    )
    (s,) = parse_stats_lines(line)
    assert (s.pid, s.dbn, s.alp, s.matched, s.resolved) == (
        1283130, 4451631, 4439079, 4392918, 4451631)
    assert s.a_p50 == 15.0 and s.resets == 0
