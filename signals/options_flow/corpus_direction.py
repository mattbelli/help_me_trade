"""Read a bucket's direction from the flow corpus, or abstain.

Pure functions over already-aggregated numbers: no I/O, no clocks, no store access
(the `rolling.py` discipline). Callers pass the three measures a `flow_rollup` row
group already carries.

Registered in `References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md` and
corrected by Addendum D. The correction matters, so it leads:

**The three measures are one signed axis at three weightings, not three votes.**
Each is `sum(sign(u_i) * w(|u_i|) * premium_i)` with `w = 1` for `net_premium`,
`w = min(|u|, 1)` for `u_premium_clipped`, and `w = |u|` for `u_premium`. And
`sign(u)` IS the Addendum-A midpoint side by construction: `u = (price-mid)/half`
with `half > 0` under `good_q`, so dividing never changes the sign. They share
every print's sign and differ only in how they weight it.

So their agreement tests exactly one property: that the bucket's direction is
invariant to the weighting choice. That is worth testing — raw u is unbounded
(measured [-61, +90]), 0.40% of prints carry 171.92% of `sum(u*premium)`, and the
disagreement concentrates in the buckets holding the money (0.24% of buckets,
15.8% of premium). It is NOT three independent confirmations, and must never be
reported as though a 2-of-3 majority meant anything: two of the three are
sign-clones of each other, so a majority among them is one opinion counted twice.

**What consensus cannot protect against is print-level sign error**, which enters
all three identically. The measured case is multi-leg allocation legs — see
`multileg` in `db/flow_corpus.py`. That is handled as a grain in the rollup, not
here; this module reads whatever slice it is handed.

Abstain is an OUTCOME, not missing data. It is never coerced to neutral and never
resolved by preferring one measure, because "pick the one that gives an answer" is
exactly the move the addendum exists to forbid.

The per-measure calls are returned alongside the consensus so a later study can
grade each measure independently and find out which one actually predicts. That
comparison cannot be run retroactively, which is why the calls are emitted from
the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Call = Literal["long", "short", "abstain"]

LONG: Call = "long"
SHORT: Call = "short"
ABSTAIN: Call = "abstain"

# Weighting spectrum, lightest tail-exposure first: net premium weights every
# classified dollar 1, clipped u saturates at the touch, raw u is unbounded.
MEASURES = ("net_premium", "u_premium_clipped", "u_premium")

AbstainState = Literal["", "no_opinion", "weighting_unstable", "contested"]

#: Every measure read exactly zero. Honestly directionless, not conflicted.
NO_OPINION: AbstainState = "no_opinion"
#: The two intensity weightings disagree — the |u| tail is steering the sum.
WEIGHTING_UNSTABLE: AbstainState = "weighting_unstable"
#: Intensity agrees but the dollar majority does not.
CONTESTED: AbstainState = "contested"

ABSTAIN_STATES = (NO_OPINION, WEIGHTING_UNSTABLE, CONTESTED)


@dataclass(frozen=True)
class DirectionRead:
    """Consensus call, what each weighting said, and why an abstain abstained.

    ``by_measure`` is the empirical arm: grading it per measure is how we learn
    which estimator predicts, rather than arguing about which is prettier.
    """

    call: Call
    by_measure: dict[str, Call]
    agree: bool
    abstain_state: AbstainState

    @property
    def unanimous_abstain(self) -> bool:
        """No measure had an opinion. Distinct from disagreement and must not be
        reported as it — on 2025-07-28 this was 2,858 of 6,421 abstaining buckets,
        made up entirely of at-the-mid prints where `side` is `unclassified` and
        `u` is exactly 0 by construction."""
        return self.abstain_state == NO_OPINION


def _sign_call(x: float) -> Call:
    """A measure's own directional opinion. Exactly zero abstains (deny-by-default):
    a bucket with no net lean has no direction, and rounding it to one would invent a
    signal out of a tie."""
    if x > 0:
        return LONG
    if x < 0:
        return SHORT
    return ABSTAIN


def direction_read(
    net_premium: float,
    u_premium_clipped: float,
    u_premium: float,
) -> DirectionRead:
    """Apply the Addendum C rule, reported with the Addendum D abstain taxonomy.

    Directional only when all three weightings are non-zero and agree. The rule is
    unchanged by Addendum D; only what an abstain records about itself is new.
    """
    by_measure = {
        "net_premium": _sign_call(net_premium),
        "u_premium_clipped": _sign_call(u_premium_clipped),
        "u_premium": _sign_call(u_premium),
    }
    net_c, clip_c, raw_c = (by_measure[m] for m in MEASURES)
    agree = net_c == clip_c == raw_c and net_c != ABSTAIN

    if agree:
        state: AbstainState = ""
    elif net_c == clip_c == raw_c == ABSTAIN:
        state = NO_OPINION
    elif raw_c != clip_c:
        # Checked before `contested`: when the intensity weighting is internally
        # unstable, any disagreement with net premium is a symptom of the same
        # |u| tail, not a separate dollars-vs-urgency conflict.
        state = WEIGHTING_UNSTABLE
    else:
        state = CONTESTED

    return DirectionRead(
        call=net_c if agree else ABSTAIN,
        by_measure=by_measure,
        agree=agree,
        abstain_state=state,
    )


def abstention_share(reads: list[tuple[DirectionRead, float]]) -> float:
    """Premium-weighted abstention rate over (read, premium) pairs.

    Premium-weighted deliberately. The count-weighted rate is the trap Addendum C
    records: on 2025-07-28 raw-vs-clipped disagreement covered 0.24% of buckets and
    15.8% of premium, so a count-weighted check calls the fragility a rounding error
    and is wrong by two orders of magnitude.

    Returns 0.0 on no premium — an empty set abstains on nothing.
    """
    total = sum(p for _, p in reads)
    if total <= 0:
        return 0.0
    return sum(p for r, p in reads if r.call == ABSTAIN) / total


def abstention_by_state(
    reads: list[tuple[DirectionRead, float]],
) -> dict[AbstainState, float]:
    """Premium share of each abstain state, reported separately.

    Addendum D requires the split and forbids pooling it: `no_opinion` is a bucket
    with nothing to say, while `weighting_unstable` is the |u| tail steering the
    sum — 0.24% of buckets but 15.8% of premium, plausibly the dominant abstain
    driver by dollars. Summed into one number those are indistinguishable, and the
    20-35% band would hide its own most interesting failure mode.

    The values sum to ``abstention_share``. Absent states are omitted rather than
    zero-filled, so a caller cannot read "never happened" as "not measured".
    """
    total = sum(p for _, p in reads)
    if total <= 0:
        return {}
    out: dict[AbstainState, float] = {}
    for r, p in reads:
        if r.call == ABSTAIN:
            out[r.abstain_state] = out.get(r.abstain_state, 0.0) + p
    return {s: v / total for s, v in out.items()}
