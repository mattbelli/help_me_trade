"""The Addendum C direction rule.

These pin the behaviours the addendum forbids, not just the happy path: abstain must
never be resolvable by preferring a measure, and the abstention rate must be
premium-weighted.
"""

from __future__ import annotations

import pytest

from signals.options_flow.corpus_direction import (
    ABSTAIN,
    LONG,
    SHORT,
    abstention_share,
    direction_read,
)


def test_all_three_positive_is_long():
    r = direction_read(net_premium=500.0, u_premium_clipped=95.0, u_premium=366.0)
    assert r.call == LONG
    assert r.agree
    assert r.dissenters == ()


def test_all_three_negative_is_short():
    r = direction_read(net_premium=-1025.0, u_premium_clipped=-400.0, u_premium=-846.0)
    assert r.call == SHORT
    assert r.agree


def test_lone_raw_u_dissent_abstains_and_is_named():
    """The real-world case: clipping flips only the unbounded measure. The bucket must
    abstain rather than take the 2-of-3 majority — 2-of-3 is a different rule and would
    need its own addendum."""
    r = direction_read(net_premium=500.0, u_premium_clipped=95.0, u_premium=-20.0)
    assert r.call == ABSTAIN
    assert not r.agree
    assert r.dissenters == ("u_premium",)


def test_zero_on_any_measure_abstains():
    """Deny-by-default: a tie is not a direction."""
    assert direction_read(0.0, 95.0, 366.0).call == ABSTAIN
    assert direction_read(500.0, 0.0, 366.0).call == ABSTAIN
    assert direction_read(500.0, 95.0, 0.0).call == ABSTAIN


def test_per_measure_calls_survive_abstention():
    """The empirical arm depends on this: an abstained bucket must still record what
    each measure would have said, or the estimators can never be graded against each
    other."""
    r = direction_read(net_premium=500.0, u_premium_clipped=-95.0, u_premium=-366.0)
    assert r.call == ABSTAIN
    assert r.by_measure == {
        "net_premium": LONG,
        "u_premium_clipped": SHORT,
        "u_premium": SHORT,
    }


def test_unanimous_abstain_is_not_disagreement():
    """A bucket of purely at-the-mid prints has side='unclassified' and u=0 by
    construction, so every measure reads zero. That is 'no opinion', not conflict —
    2,858 of 6,421 abstaining buckets on 2025-07-28 — and reporting it as
    disagreement would overstate how contested the corpus is."""
    r = direction_read(0.0, 0.0, 0.0)
    assert r.call == ABSTAIN
    assert r.unanimous_abstain
    assert r.dissenters == ()

    contested = direction_read(500.0, 95.0, -20.0)
    assert contested.call == ABSTAIN
    assert not contested.unanimous_abstain
    assert contested.dissenters == ("u_premium",)


def test_three_way_disorder_names_every_measure():
    r = direction_read(net_premium=500.0, u_premium_clipped=0.0, u_premium=-366.0)
    assert r.call == ABSTAIN
    assert set(r.dissenters) == {"net_premium", "u_premium_clipped", "u_premium"}


def test_abstention_share_is_premium_weighted_not_count_weighted():
    """Addendum C's central trap: few buckets, most of the money. One abstaining
    bucket in four, carrying 90% of premium, is a 90% abstention rate — not 25%."""
    agree = direction_read(10.0, 10.0, 10.0)
    disagree = direction_read(10.0, 10.0, -10.0)
    reads = [(agree, 10.0), (agree, 10.0), (agree, 10.0), (disagree, 270.0)]
    assert abstention_share(reads) == pytest.approx(0.9)


def test_abstention_share_empty_and_zero_premium():
    assert abstention_share([]) == 0.0
    assert abstention_share([(direction_read(1.0, 1.0, -1.0), 0.0)]) == 0.0
