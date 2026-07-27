"""The two counting rules added at schema_version 4 (roadmap 1.2 and 1.3).

Both are pure array logic over 11M prints a session, so they get unit tests
rather than a re-roll: a bucketing mistake here is invisible in the aggregates
and would only surface as a wrong z-score months later.

`tools.flow_corpus_rollup` imports databento lazily inside its file readers, so
importing it here does not require the package.
"""

from __future__ import annotations

import numpy as np

from db.flow_corpus import MNY_BUCKETS
from tools.flow_corpus_rollup import (
    _bucket_mny,
    _order_groups,
    _orders_per_bucket,
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


def test_a_group_straddling_two_buckets_is_counted_in_both():
    """Legs of one sweep disagree on side in 1.371% of multi-print groups. Those
    land in two rollup buckets, and counting the group once would drop the sell
    half of every mixed sweep."""
    inv = np.array([0, 1])        # same group, two buckets
    gid = np.array([0, 0])
    assert list(_orders_per_bucket(inv, gid, n_groups=1, n_buckets=2)) == [1, 1]


def test_orders_never_exceeds_prints():
    """Deduplication can only ever reduce the count. An `orders` above `prints`
    would mean the packing arithmetic overflowed into a neighbouring bucket."""
    rng = np.random.default_rng(0)
    inv = rng.integers(0, 6, size=500)
    gid = rng.integers(0, 40, size=500)
    orders = _orders_per_bucket(inv, gid, n_groups=40, n_buckets=6)
    prints = np.bincount(inv, minlength=6)
    assert (orders <= prints).all()
    assert orders.sum() >= 1
