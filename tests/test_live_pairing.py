"""StreamingCondMatcher vs the frozen Addendum E join.

The matcher's one job is to reproduce `tools.condition_labels.match_prints`
per print, live. These tests pin that equivalence on complete tapes (where it
must be exact) and pin the documented deviations — expiry, cap eviction,
idle-key GC — to their intended, counted behavior.
"""

from __future__ import annotations

import numpy as np

from signals.options_flow.live_pairing import StreamingCondMatcher
from tools.condition_labels import TOL_NS, match_prints

MS = 1_000_000  # ns
KEY = (101, 250, 10)  # (cid, price_cents, size)


def stream_session(d_prints, a_trades, **kw):
    """Feed both tapes merged by participant ts (arrival == ts), then flush.

    d_prints: [(key, ts_ns)] in tape order; a_trades: [(key, ts_ns, cond)].
    Returns ({print_id: (cond, reason)}, matcher). Stable sort keeps each
    side's tape order for equal timestamps — the same tie-break the batch
    lexsort applies.
    """
    events = [(ts, "d", i, key, None) for i, (key, ts) in enumerate(d_prints)]
    events += [(ts, "a", j, key, cond) for j, (key, ts, cond) in enumerate(a_trades)]
    events.sort(key=lambda e: e[0])
    m = StreamingCondMatcher(**kw)
    resolved: dict[int, tuple[str, str]] = {}
    for ts, side, idx, key, cond in events:
        if side == "d":
            out = m.on_dbn_print(key, ts, idx, now_ns=ts)
        else:
            out = m.on_alpaca_trade(key, ts, cond, now_ns=ts)
        for pid, c, reason in out:
            assert pid not in resolved, "a print resolved twice"
            resolved[pid] = (c, reason)
    last = events[-1][0] if events else 0
    for pid, c, reason in m.sweep(last + 10 * m._horizon_ns):
        assert pid not in resolved, "a print resolved twice"
        resolved[pid] = (c, reason)
    return resolved, m


def test_simple_match_within_tolerance():
    res, m = stream_session([(KEY, 1000 * MS)], [(KEY, 1050 * MS, "a")])
    assert res == {0: ("a", "matched")}
    assert (m.matched, m.pending_dbn, m.pending_alpaca) == (1, 0, 0)


def test_alpaca_first_arrival_is_symmetric():
    res, _ = stream_session([(KEY, 1050 * MS)], [(KEY, 1000 * MS, "f")])
    assert res == {0: ("f", "matched")}


def test_kth_pair_beyond_tolerance_resolves_empty():
    res, m = stream_session([(KEY, 0)], [(KEY, TOL_NS + 1, "a")])
    assert res == {0: ("", "tolerance")}
    assert m.tolerance_rejects == 1


def test_k_rank_pairs_in_tape_order():
    """Three identical copies each side: k-th pairs k-th, never nearest-ts."""
    d = [(KEY, 0), (KEY, 10 * MS), (KEY, 20 * MS)]
    a = [(KEY, 1 * MS, "a"), (KEY, 11 * MS, "b"), (KEY, 21 * MS, "c")]
    res, _ = stream_session(d, a)
    assert res == {0: ("a", "matched"), 1: ("b", "matched"), 2: ("c", "matched")}


def test_unpaired_copies_expire_unlabeled():
    """DBN has one more copy than Alpaca — the extra k-slot has no twin and
    must resolve '' at the horizon, exactly like a batch non-merge."""
    d = [(KEY, 0), (KEY, 10 * MS)]
    a = [(KEY, 1 * MS, "a")]
    res, m = stream_session(d, a)
    assert res == {0: ("a", "matched"), 1: ("", "expired")}
    assert m.expired == 1


def test_expired_slot_stays_consumed_for_the_late_twin():
    """A twin arriving after its slot expired must be discarded — NOT paired
    with the next print in line, which would misalign every k behind it."""
    m = StreamingCondMatcher(horizon_ns=100 * MS)
    assert m.on_dbn_print(KEY, 0, 0, now_ns=0) == []
    # a second print keeps the key ACTIVE (last_ns fresh) so idle-key GC does
    # not fire — this test pins the tombstone, not the GC rank-reset path
    assert m.on_dbn_print(KEY, 300 * MS, 1, now_ns=95 * MS) == []
    out = m.sweep(150 * MS)  # only slot 0 is past the horizon
    assert out == [(0, "", "expired")]
    # the late twin for slot 0 lands after its slot expired
    out = m.on_alpaca_trade(KEY, 90 * MS, "a", now_ns=160 * MS)
    assert out == []  # consumed by the tombstone, no resolution
    assert m.cond_after_expiry == 1
    # slot 1's real twin still pairs correctly
    out = m.on_alpaca_trade(KEY, 305 * MS, "b", now_ns=170 * MS)
    assert out == [(1, "b", "matched")]


def test_pending_cap_evicts_oldest_with_counter():
    m = StreamingCondMatcher(max_pending=2)
    keys = [(i, 100, 1) for i in range(3)]
    out: list = []
    for i, key in enumerate(keys):
        out += m.on_dbn_print(key, i * MS, i, now_ns=i * MS)
    assert out == [(0, "", "evicted")]
    assert m.evicted == 1 and m.pending_dbn == 2


def test_idle_key_gc_counts_alpaca_orphans():
    """A cond whose DBN twin never existed sits pending until the key goes
    idle, then is dropped and counted — pending_alpaca returns to zero."""
    m = StreamingCondMatcher(horizon_ns=100 * MS)
    m.on_alpaca_trade(KEY, 0, "a", now_ns=0)
    assert m.pending_alpaca == 1
    m.sweep(150 * MS)  # expiry pass; GC not yet due at this cadence
    m.sweep(300 * MS)  # key idle past the horizon: GC'd
    assert m.pending_alpaca == 0
    assert m.alpaca_orphans == 1


def test_streaming_equals_batch_on_complete_tapes():
    """The load-bearing pin: on tapes where every copy exists on both sides,
    streaming must reproduce `match_prints` cond-for-cond — including
    tolerance rejects and rank exhaustion. Randomized but seeded."""
    rng = np.random.default_rng(7)
    d_list: list[tuple[tuple, int]] = []
    a_list: list[tuple[tuple, int, str]] = []
    conds = np.array(list("abcfghijst"))
    for cid in range(60):
        key = (cid, int(rng.integers(1, 500)), int(rng.integers(1, 50)))
        n_d = int(rng.integers(1, 6))
        n_a = int(rng.integers(1, 6))  # unequal counts exercise exhaustion
        base = rng.integers(0, 10_000, size=max(n_d, n_a)) * MS
        for i in range(n_d):
            d_list.append((key, int(base[i])))
        for i in range(n_a):
            # jitter straddles TOL_NS so both accept and reject paths fire
            jit = int(rng.integers(-2 * TOL_NS, 2 * TOL_NS))
            a_list.append((key, int(base[i]) + jit, str(rng.choice(conds))))

    res, m = stream_session(d_list, a_list)
    assert len(res) == len(d_list), "every DBN print resolves exactly once"

    d_cid = np.array([k[0] for k, _ in d_list], dtype=np.int64)
    d_cents = np.array([k[1] for k, _ in d_list], dtype=np.int64)
    d_size = np.array([k[2] for k, _ in d_list], dtype=np.int64)
    d_ts = np.array([ts for _, ts in d_list], dtype=np.int64)
    a_cid = np.array([k[0] for k, _, _ in a_list], dtype=np.int64)
    a_cents = np.array([k[1] for k, _, _ in a_list], dtype=np.int64)
    a_size = np.array([k[2] for k, _, _ in a_list], dtype=np.int64)
    a_ts = np.array([ts for _, ts, _ in a_list], dtype=np.int64)
    a_cond = np.array([c for _, _, c in a_list], dtype="U1")

    cond_batch = np.full(len(d_list), "", dtype="U1")
    d_rows, a_rows = match_prints(d_cid, d_cents, d_size, d_ts,
                                  a_cid, a_cents, a_size, a_ts)
    cond_batch[d_rows] = a_cond[a_rows]

    cond_stream = np.array([res[i][0] for i in range(len(d_list))], dtype="U1")
    assert (cond_stream == cond_batch).all()
    assert m.matched == len(d_rows)
