"""The Addendum C direction rule, with the Addendum D abstain taxonomy.

These pin the behaviours the addenda forbid, not just the happy path: abstain must
never be resolvable by preferring a measure, the abstention rate must be
premium-weighted, and an abstain must say WHY — a bucket with nothing to say and a
bucket whose |u| tail is steering the sum are different findings.
"""

from __future__ import annotations

import pytest

from signals.options_flow.corpus_direction import (
    ABSTAIN,
    CONTESTED,
    LONG,
    NO_OPINION,
    SHORT,
    WEIGHTING_UNSTABLE,
    abstention_by_state,
    abstention_share,
    direction_read,
)


def test_all_three_positive_is_long():
    r = direction_read(net_premium=500.0, u_premium_clipped=95.0, u_premium=366.0)
    assert r.call == LONG
    assert r.agree
    assert r.abstain_state == ""


def test_all_three_negative_is_short():
    r = direction_read(net_premium=-1025.0, u_premium_clipped=-400.0, u_premium=-846.0)
    assert r.call == SHORT
    assert r.agree


def test_raw_vs_clipped_flip_is_weighting_unstable():
    """The real-world case: clipping flips only the unbounded measure. The bucket
    must abstain rather than take a 2-of-3 majority — and 2-of-3 is not merely a
    different rule, it is incoherent here, because all three share their per-print
    sign by construction and a 'majority' counts one opinion twice."""
    r = direction_read(net_premium=500.0, u_premium_clipped=95.0, u_premium=-20.0)
    assert r.call == ABSTAIN
    assert not r.agree
    assert r.abstain_state == WEIGHTING_UNSTABLE


def test_intensity_agrees_but_dollars_dissent_is_contested():
    """SPX calls 31-90 DTE on 2025-07-28: buys $338.5M at mean u +2.27, sells
    $912.8M at mean u -0.65. The dollar majority is short, the aggressively-priced
    dollars are long. Recorded as its own state, carrying no predictive claim."""
    r = direction_read(net_premium=-574.0, u_premium_clipped=90.0, u_premium=176.0)
    assert r.call == ABSTAIN
    assert r.abstain_state == CONTESTED


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
    # clipped and raw agree; only the dollar weighting dissents
    assert r.abstain_state == CONTESTED


def test_no_opinion_is_not_disagreement():
    """A bucket of purely at-the-mid prints has side='unclassified' and u=0 by
    construction, so every measure reads zero. That is 'no opinion', not conflict —
    2,858 of 6,421 abstaining buckets on 2025-07-28 — and reporting it as
    disagreement would overstate how contested the corpus is."""
    r = direction_read(0.0, 0.0, 0.0)
    assert r.call == ABSTAIN
    assert r.abstain_state == NO_OPINION
    assert r.unanimous_abstain

    contested = direction_read(500.0, 95.0, -20.0)
    assert not contested.unanimous_abstain


def test_weighting_instability_outranks_contested():
    """Order matters. When raw and clipped already disagree, net premium's dissent
    is a symptom of the same |u| tail — labelling it 'contested' would report one
    fragility as two independent findings."""
    r = direction_read(net_premium=-500.0, u_premium_clipped=95.0, u_premium=-366.0)
    assert r.abstain_state == WEIGHTING_UNSTABLE


def test_abstention_share_is_premium_weighted_not_count_weighted():
    """Addendum C's central trap: few buckets, most of the money. One abstaining
    bucket in four, carrying 90% of premium, is a 90% abstention rate — not 25%."""
    agree = direction_read(10.0, 10.0, 10.0)
    disagree = direction_read(10.0, 10.0, -10.0)
    reads = [(agree, 10.0), (agree, 10.0), (agree, 10.0), (disagree, 270.0)]
    assert abstention_share(reads) == pytest.approx(0.9)


def test_abstention_by_state_splits_and_sums_to_the_total():
    """Addendum D forbids pooling the states. The split must still reconcile to the
    headline rate the 20-35% band is declared against, or the two numbers drift."""
    reads = [
        (direction_read(10.0, 10.0, 10.0), 100.0),    # directional
        (direction_read(0.0, 0.0, 0.0), 50.0),        # no_opinion
        (direction_read(10.0, 10.0, -10.0), 200.0),   # weighting_unstable
        (direction_read(-10.0, 10.0, 10.0), 50.0),    # contested
    ]
    by = abstention_by_state(reads)
    assert by == {
        NO_OPINION: pytest.approx(0.125),
        WEIGHTING_UNSTABLE: pytest.approx(0.5),
        CONTESTED: pytest.approx(0.125),
    }
    assert sum(by.values()) == pytest.approx(abstention_share(reads))


def test_absent_states_are_omitted_not_zero_filled():
    """'Never happened' and 'not measured' must not look the same."""
    assert abstention_by_state([(direction_read(1.0, 1.0, 1.0), 10.0)]) == {}


def test_abstention_share_empty_and_zero_premium():
    assert abstention_share([]) == 0.0
    assert abstention_share([(direction_read(1.0, 1.0, -1.0), 0.0)]) == 0.0
    assert abstention_by_state([]) == {}
