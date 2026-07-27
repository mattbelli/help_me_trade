"""The two counting rules added at schema_version 4 (roadmap 1.2 and 1.3).

Both are pure array logic over 11M prints a session, so they get unit tests
rather than a re-roll: a bucketing mistake here is invisible in the aggregates
and would only surface as a wrong z-score months later.

`tools.flow_corpus_rollup` imports databento lazily inside its file readers, so
importing it here does not require the package.
"""

from __future__ import annotations

import numpy as np

from db.flow_corpus import DTE_BUCKETS, MNY_BUCKETS, SIDES
from tools.flow_corpus_rollup import (
    _bucket_mny,
    _order_groups,
    _order_stats,
)

IDX = MNY_BUCKETS.index("no_spot_index")
MISSING = MNY_BUCKETS.index("no_spot_missing")
ATM = MNY_BUCKETS.index("atm")
OTM2 = MNY_BUCKETS.index("otm2_10")


# --- 1.2: the two no-spot arms are different things -------------------------

def test_no_spot_splits_index_from_missing():
    """The whole point of 1.2. Before this, both landed in one 'unknown' bucket
    that was 38.75% of a session's premium — of which 99.8% was structural index
    flow and 0.07% was the data fault anyone actually wants to see."""
    strike = np.array([5000.0, 100.0])
    spot = np.array([np.nan, np.nan])
    right = np.array([0, 0], dtype=np.int8)
    is_index = np.array([True, False])
    assert list(_bucket_mny(strike, spot, right, is_index)) == [IDX, MISSING]


def test_an_index_root_with_a_spot_is_bucketed_normally():
    """`is_index` selects which no-spot arm applies; it must never pre-empt a real
    moneyness label. If it did, adding a root to INDEX_ROOTS would silently delete
    that name's moneyness structure from the corpus."""
    out = _bucket_mny(
        strike=np.array([100.0]), spot=np.array([100.0]),
        right=np.array([0], dtype=np.int8), is_index=np.array([True]),
    )
    assert out[0] == ATM


def test_moneyness_is_unchanged_for_ordinary_names():
    """A regression fence on the four real buckets: the 1.2 split reorganised the
    no-spot tail and must not have perturbed anything above it."""
    out = _bucket_mny(
        strike=np.array([105.0, 100.0]), spot=np.array([100.0, 100.0]),
        right=np.array([0, 0], dtype=np.int8), is_index=np.array([False, False]),
    )
    assert list(out) == [OTM2, ATM]


# --- 1.3: one sweep is one observation --------------------------------------

def test_same_instrument_same_nanosecond_is_one_order():
    """A sweep crossing three venues arrives as three prints. Counting it three
    times inflates sqrt(N) and manufactures significance — measured at up to 16%
    on thin, heavily-swept names."""
    ts = np.array([1, 1, 1], dtype=np.int64)
    iid = np.array([7, 7, 7], dtype=np.int64)
    gid, n = _order_groups(ts, iid)
    assert n == 1
    assert len(set(gid.tolist())) == 1


def test_different_instruments_at_one_nanosecond_stay_separate():
    """The multi-leg case. `_multileg` flags it; `_order_groups` must NOT merge it
    — a two-leg spread is two contracts traded, not one observation of one."""
    _, n = _order_groups(np.array([1, 1], dtype=np.int64),
                         np.array([7, 8], dtype=np.int64))
    assert n == 2


def test_same_instrument_at_different_nanoseconds_stays_separate():
    _, n = _order_groups(np.array([1, 2], dtype=np.int64),
                         np.array([7, 7], dtype=np.int64))
    assert n == 2


def test_grouping_does_not_depend_on_input_order():
    """The implementation lexsorts and scatters back. If the scatter were wrong
    the group ids would be permuted relative to the prints and every per-bucket
    count would be silently mis-assigned."""
    ts = np.array([5, 1, 5, 1, 3], dtype=np.int64)
    iid = np.array([2, 9, 2, 9, 4], dtype=np.int64)
    gid, n = _order_groups(ts, iid)
    assert n == 3
    assert gid[0] == gid[2]      # (5, 2) twice
    assert gid[1] == gid[3]      # (1, 9) twice
    assert len({gid[0], gid[1], gid[4]}) == 3


def test_orders_never_exceeds_prints():
    """Deduplication can only ever reduce the count. An `orders` above `prints`
    would mean the packing arithmetic overflowed into a neighbouring bucket.
    Groups come from `_order_groups` on synthetic ticks rather than random ints,
    so the real composition is exercised end to end."""
    rng = np.random.default_rng(0)
    ts = rng.integers(0, 40, size=500).astype(np.int64)
    iid = rng.integers(0, 3, size=500).astype(np.int64)
    gid, n_groups = _order_groups(ts, iid)
    inv = rng.integers(0, 6, size=500)
    orders, _ = _order_stats(inv, gid, rng.random(500), n_groups, 6)
    prints = np.bincount(inv, minlength=6)
    assert (orders <= prints).all()
    assert orders.sum() >= 1


# --- the packing arithmetic: no coverage before review flagged it ------------

def test_pack_and_unpack_round_trip_over_every_field_combination():
    """The single most dangerous arithmetic here, and it had no test. Pack and
    unpack were two independent sets of radix literals; this diff had to change
    one of them 5 -> 6. Changing pack without unpack is SILENT — every field
    shifts, but MNY_BUCKETS[s_mny] and DTE_BUCKETS[s_dte] still index legally, so
    rows emerge attributed to the WRONG UNDERLYING and nothing raises."""
    n_r, n_d = 2, len(DTE_BUCKETS)
    n_m, n_s, n_ml = len(MNY_BUCKETS), len(SIDES), 2
    combos = [
        (u, r, d, m, sd, st, ml)
        for u in (0, 1, 5_928)
        for r in range(n_r) for d in range(n_d) for m in range(n_m)
        for sd in range(n_s) for st in range(n_s) for ml in range(n_ml)
    ]
    a = np.array(combos, dtype=np.int64).T
    und, right, dte_b, mny_b, side, strict, ml = a

    key = (((((und * n_r + right) * n_d + dte_b) * n_m
             + mny_b) * n_s + side) * n_s + strict) * n_ml + ml
    assert len(np.unique(key)) == len(combos), "keys collide — fields overlap"

    k = key
    k, s_ml = np.divmod(k, n_ml)
    k, s_strict = np.divmod(k, n_s)
    k, s_side = np.divmod(k, n_s)
    k, s_mny = np.divmod(k, n_m)
    k, s_dte = np.divmod(k, n_d)
    s_und, s_right = np.divmod(k, n_r)

    for got, want, name in ((s_und, und, "underlying"), (s_right, right, "right"),
                            (s_dte, dte_b, "dte"), (s_mny, mny_b, "mny"),
                            (s_side, side, "side"), (s_strict, strict, "strict"),
                            (s_ml, ml, "multileg")):
        assert (got == want).all(), f"{name} does not survive the round trip"


def test_the_order_grain_second_moment_is_never_negative():
    """F1, the defect review caught. `premium_sq` is a sum of per-print squares;
    dividing it by `orders` broke Cauchy-Schwarz and produced a negative variance
    on 1.55% of live rows (sqrt -> NaN). `premium_sq_orders` squares each
    (bucket, group) pair's TOTAL, so it is the second moment of exactly the
    population `orders` counts and the variance is non-negative by construction."""
    inv = np.array([0, 0, 0])          # one bucket
    gid = np.array([0, 0, 1])          # a 2-venue sweep, plus a lone order
    premium = np.array([100.0, 100.0, 50.0])
    orders, sq = _order_stats(inv, gid, premium, n_groups=2, n_buckets=1)
    assert orders[0] == 2
    assert sq[0] == 200.0 ** 2 + 50.0 ** 2          # pairs summed THEN squared
    var = sq[0] / orders[0] - (premium.sum() / orders[0]) ** 2
    assert var >= 0

    # the print-grain column on the same data is what went negative
    print_sq = float((premium ** 2).sum())
    assert print_sq / orders[0] - (premium.sum() / orders[0]) ** 2 < 0


def test_order_stats_agree_with_the_pair_definition():
    """Straddling groups must contribute to both buckets in BOTH returns, or a
    bucket ends up with premium and no N, or N and no dispersion."""
    inv = np.array([0, 1])             # one group, two buckets (side disagreed)
    gid = np.array([0, 0])
    orders, sq = _order_stats(inv, gid, np.array([10.0, 40.0]),
                              n_groups=1, n_buckets=2)
    assert list(orders) == [1, 1]
    assert list(sq) == [100.0, 1600.0]


def test_empty_input_does_not_blow_up_the_batch():
    """A definitions file from the wrong day drops every print at the join. With
    no try/except in main(), an exception here abandons the rest of a 260-session
    unattended run."""
    gid, n = _order_groups(np.array([], dtype=np.int64), np.array([], dtype=np.int64))
    assert n == 0 and len(gid) == 0
    orders, sq = _order_stats(np.array([], dtype=np.int64), gid,
                              np.array([]), n_groups=0, n_buckets=3)
    assert list(orders) == [0, 0, 0] and list(sq) == [0.0, 0.0, 0.0]
