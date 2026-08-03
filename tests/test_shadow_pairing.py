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


def test_stopped_shadow_ignores_the_tee():
    # BLOCKER-1 regression pin: the tee stays bound into the runner after a
    # shadow death; a stopped shadow must not accumulate ANY matcher state.
    sp = _shadow()
    sp.stop()
    for i in range(100):
        sp.on_dbn(OCC, 5_000_000_000, i + 1, time.time_ns())
    assert sp.dbn_seen == 0
    assert sp._m.pending_dbn == 0 and not sp._m._keys


def test_matcher_reset_starts_a_clean_epoch():
    # BLOCKER-2 regression pin: after a sidecar gap has offset the k-ranks,
    # a reset must restore correct pairing instead of false matches.
    sp = _shadow()
    ts = time.time_ns()
    # Gap simulation: 3 dbn prints fed with no alpaca twins (twins lost).
    for i in range(3):
        sp.on_dbn(OCC, 5_000_000_000, 10, ts + i)
    assert sp._m.pending_dbn == 3
    sp._reset_matcher()
    assert sp.matcher_resets == 1 and sp._m.pending_dbn == 0
    # New epoch pairs rank-0 with rank-0 — the same-ts twin matches cleanly.
    ts2 = time.time_ns()
    sp.on_dbn(OCC, 5_000_000_000, 10, ts2)
    sp.on_alpaca(OCC, ts2, 500, 10, "S")
    assert sp._m.matched == 1 and sp._m.tolerance_rejects == 0


def test_alpaca_guard_swallows_and_counts():
    sp = _shadow()
    sp.on_alpaca(OCC, None, 500, 10, "S")  # type: ignore[arg-type]
    assert sp.alp_errors == 1


def test_attach_then_resolve_stamps_conditions_in_place():
    # Inc 3 wiring: the spine registers its normalized dict, the twin arrives,
    # and the code lands on the dict — a fresh list, the old one untouched.
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    assert pid is not None
    ref = {"occ_symbol": OCC, "conditions": []}
    old = ref["conditions"]
    sp.attach_print(pid, ref)
    sp.on_alpaca(OCC, ts, 500, 10, "f")
    assert ref["conditions"] == ["f"]
    assert old == []  # reader holding the pre-stamp list saw no mutation
    assert sp.enriched == 1 and not sp._attached


def test_resolve_before_attach_uses_the_parked_code():
    # Inline match: the twin was already pending when on_dbn fed the matcher,
    # so the resolution fires INSIDE on_dbn — before the spine can attach.
    sp = _shadow()
    ts = time.time_ns()
    sp.on_alpaca(OCC, ts, 500, 10, "t")
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    assert sp._m.matched == 1
    ref = {"conditions": []}
    sp.attach_print(pid, ref)
    assert ref["conditions"] == ["t"]
    assert sp.early_hits == 1 and not sp._early_cond


def test_clean_print_stamps_empty_conditions():
    # A matched twin with no condition code means "clean print", not "unknown"
    # — the stamp must still happen so consumers can tell the two apart later.
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    ref = {"conditions": []}
    sp.attach_print(pid, ref)
    sp.on_alpaca(OCC, ts, 500, 10, "")
    assert ref["conditions"] == [] and sp.enriched == 1


def test_metrics_only_never_touches_the_print():
    sp = ShadowPairing(alpaca_key="k", alpaca_secret="s", metrics_only=True)
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    ref = {"conditions": []}
    sp.attach_print(pid, ref)
    sp.on_alpaca(OCC, ts, 500, 10, "f")
    assert ref["conditions"] == []
    assert sp.enriched == 0 and not sp._attached and not sp._early_cond
    assert sp._m.matched == 1  # the measurement surface is unchanged


def test_unmatched_resolution_releases_the_ref_without_stamping():
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    ref = {"conditions": []}
    sp.attach_print(pid, ref)
    with sp._lock:
        sp._absorb([(pid, "", "expired")], time.time_ns())
    assert not sp._attached and ref["conditions"] == []


def test_matcher_reset_releases_attached_refs():
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    sp.attach_print(pid, {"conditions": []})
    sp._reset_matcher()
    assert not sp._attached and not sp._early_cond
    # A twin for the old epoch arriving now must not stamp anything.
    sp.on_alpaca(OCC, ts, 500, 10, "f")
    assert sp.enriched == 0


def test_sweep_ttls_parked_codes_for_prints_that_never_attach():
    # Meta-gate-skipped prints feed the matcher (population correctness) but
    # never attach — their parked codes must not accumulate forever.
    sp = _shadow()
    stale_pid = time.time_ns() - 11_000_000_000  # older than EARLY_COND_TTL_NS
    with sp._lock:
        sp._early_cond[stale_pid] = "f"
    sp._sweep_once()
    assert not sp._early_cond


def test_sweep_ttls_attached_refs_past_the_repair_window():
    # A ref whose twin never came is dropped once even the late repair can't
    # use it — never pinned for the matcher's whole 10-min expiry horizon.
    sp = _shadow()
    stale_pid = time.time_ns() - 31_000_000_000  # older than ATTACH_TTL_NS
    fresh_pid = time.time_ns()
    with sp._lock:
        sp._attached[stale_pid] = {"conditions": []}
        sp._attached[fresh_pid] = {"conditions": []}
    sp._sweep_once()
    assert stale_pid not in sp._attached and fresh_pid in sp._attached


def test_late_multileg_code_queues_the_event_repair():
    # The print's event was already written (_event_id backref) when the twin
    # finally arrives with a package code → the stored row must be flagged.
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    ref = {"conditions": [], "_event_id": "of_2026-08-03_SPY_x_blk_1"}
    sp.attach_print(pid, ref)
    sp.on_alpaca(OCC, ts, 500, 10, "f")  # f = MULTILEG code
    assert ref["conditions"] == ["f"]
    assert sp.drain_repairs() == ["of_2026-08-03_SPY_x_blk_1"]
    assert sp.drain_repairs() == []  # drained is drained
    assert sp.repairs_queued == 1


def test_clean_or_hygiene_late_codes_do_not_queue_repairs():
    sp = _shadow()
    for code in ("", "S", "C"):  # clean, ISO, cancel — none are MULTILEG
        ts = time.time_ns()
        pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
        ref = {"conditions": [], "_event_id": "of_x"}
        sp.attach_print(pid, ref)
        sp.on_alpaca(OCC, ts, 500, 10, code)
    assert sp.drain_repairs() == []


def test_multileg_code_without_event_backref_queues_nothing():
    # Code landed before any event was built — the in-place stamp is the
    # whole fix; there is no row to repair.
    sp = _shadow()
    ts = time.time_ns()
    pid = sp.on_dbn(OCC, 5_000_000_000, 10, ts)
    ref = {"conditions": []}
    sp.attach_print(pid, ref)
    sp.on_alpaca(OCC, ts, 500, 10, "f")
    assert ref["conditions"] == ["f"] and sp.drain_repairs() == []


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
