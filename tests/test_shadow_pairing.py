"""ShadowPairing — the Inc 2 shadow's metric surface, no network.

Matcher semantics (k-ranks, tolerance, expiry, GC) are pinned in
test_live_pairing.py against the batch join; these tests cover what the shadow
adds around it: the two-thread feed, the never-raise tee guard, wire-message
parsing, and the counters the live measurement will be read from.
"""

from __future__ import annotations

import threading
import time

from signals.options_flow.shadow_pairing import ShadowPairing


def _shadow() -> ShadowPairing:
    return ShadowPairing(alpaca_key="k", alpaca_secret="s")


OCC = "SPY260728C00500000"


def test_twin_pair_counts_match_and_enrich_bucket():
    sp = _shadow()
    ts = time.time_ns()
    # price_raw is DBN fixed-point 1e9: $5.00 → 500 cents on both sides.
    sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    sp.on_alpaca(OCC, ts, 500, 10, "S")
    assert sp.dbn_seen == 1 and sp.alp_seen == 1
    assert sp._m.matched == 1
    # Resolution landed microseconds after the print arrived → first bucket.
    assert sp.enrich_buckets[0] == 1 and sum(sp.enrich_buckets) == 1
    assert sp.d_roots == {"SPY": 1} and sp.a_roots == {"SPY": 1}


def test_participant_ts_beyond_tolerance_rejects():
    sp = _shadow()
    ts = time.time_ns()
    sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    sp.on_alpaca(OCC, ts + 200_000_000, 500, 10, "S")  # 200ms > TOL
    assert sp._m.matched == 0 and sp._m.tolerance_rejects == 1
    assert sum(sp.enrich_buckets) == 0


def test_tee_guard_swallows_garbage():
    sp = _shadow()
    sp.on_dbn(OCC, None, 10, time.time_ns())  # type: ignore[arg-type]
    assert sp.tee_errors == 1
    # Still healthy afterwards.
    ts = time.time_ns()
    sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    sp.on_alpaca(OCC, ts, 500, 10, "S")
    assert sp._m.matched == 1


def test_trade_msg_parsing_feeds_matcher():
    sp = _shadow()
    ts = time.time_ns()
    sp.on_dbn(OCC, 1_230_000_000, 3, ts)
    sp._on_trade_msg({"T": "t", "S": OCC, "t": ts, "p": 1.23, "s": 3, "c": "f"})
    assert sp._m.matched == 1
    # Non-int timestamp (not timestamp=2 decoded) and missing symbol are dropped.
    sp._on_trade_msg({"T": "t", "S": OCC, "t": "2026-07-28", "p": 1.0, "s": 1})
    sp._on_trade_msg({"T": "t", "S": "", "t": ts, "p": 1.0, "s": 1})
    assert sp.alp_seen == 1


def test_two_thread_feed_is_consistent():
    sp = _shadow()
    base = time.time_ns()
    n = 5_000

    def dbn_side():
        for i in range(n):
            sp.on_dbn(OCC, 5_000_000_000, i % 50 + 1, base + i)

    t = threading.Thread(target=dbn_side)
    t.start()
    for i in range(n):
        sp.on_alpaca(OCC, base + i, 500, i % 50 + 1, "S")
    t.join()
    assert sp.dbn_seen == n and sp.alp_seen == n
    assert sp.tee_errors == 0
    m = sp._m
    # Every pairing outcome is accounted; nothing lost between the threads.
    resolved = m.matched + m.tolerance_rejects
    assert resolved + m.pending_dbn == n
    assert m.pending_alpaca == n - resolved - m.cond_after_expiry
    sp._log_stats(final=True)  # formats without raising on live counters
