"""Read a bucket's direction from the flow corpus, or abstain.

Pure functions over already-aggregated numbers: no I/O, no clocks, no store access
(the `rolling.py` discipline). Callers pass the three measures a `flow_rollup` row
group already carries.

Registered in `References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md`. The short
version: `u = (price-mid)/half` is a ratio over the half-spread and is unbounded
(measured [-61, +90]), so 0.40% of prints carry 171.92% of `sum(u*premium)`. Which
direction a bucket "has" therefore depends on which summary you pick — and the
disagreement is concentrated in the buckets holding the money (0.24% of buckets,
15.8% of premium). So no single measure is trusted: all three must agree, or the
bucket abstains.

Abstain is an OUTCOME, not missing data. It is never coerced to neutral and never
resolved by preferring one measure, because "pick the one that gives an answer" is
exactly the move the addendum exists to forbid.

The per-measure calls are returned alongside the consensus so a later study can grade
each measure independently and find out which one actually predicts. That comparison
cannot be run retroactively, which is why the calls are emitted from the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Call = Literal["long", "short", "abstain"]

LONG: Call = "long"
SHORT: Call = "short"
ABSTAIN: Call = "abstain"

# Order is the registered robustness order: net premium is bounded by construction,
# clipped u saturates at the touch, raw u is unbounded. Consensus needs all three.
MEASURES = ("net_premium", "u_premium_clipped", "u_premium")


@dataclass(frozen=True)
class DirectionRead:
    """Consensus call plus what each measure said on its own.

    ``by_measure`` is the empirical arm: grading it per measure is how we learn which
    estimator predicts, rather than arguing about which is prettier.
    """

    call: Call
    by_measure: dict[str, Call]
    agree: bool

    @property
    def unanimous_abstain(self) -> bool:
        """Every measure read exactly zero — no conflict, just no opinion.

        Distinct from disagreement and must not be reported as it. Measured on
        2025-07-28 this was 2,858 of 6,421 abstaining buckets: buckets made up
        entirely of at-the-mid prints, where `side` is `unclassified` and `u` is
        exactly 0 by construction. Those buckets are honestly directionless; a bucket
        where the measures actively contradict each other is a different finding.
        """
        return all(c == ABSTAIN for c in self.by_measure.values())

    @property
    def dissenters(self) -> tuple[str, ...]:
        """Measures that disagree with the majority.

        Empty when all three agree AND when no measure has an opinion — check
        ``unanimous_abstain`` to tell those apart. A lone raw-u dissent is a very
        different story from three-way disorder.
        """
        if self.agree or self.unanimous_abstain:
            return ()
        counts: dict[Call, int] = {}
        for c in self.by_measure.values():
            counts[c] = counts.get(c, 0) + 1
        top = max(counts.values())
        majority = {c for c, n in counts.items() if n == top}
        if len(majority) != 1:
            return tuple(MEASURES)
        winner = majority.pop()
        return tuple(m for m in MEASURES if self.by_measure[m] != winner)


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
    """Apply the Addendum C rule to one bucket (or to any aggregate of buckets).

    Directional only when all three measures are non-zero and agree; otherwise abstain.
    """
    by_measure = {
        "net_premium": _sign_call(net_premium),
        "u_premium_clipped": _sign_call(u_premium_clipped),
        "u_premium": _sign_call(u_premium),
    }
    calls = set(by_measure.values())
    agree = len(calls) == 1 and ABSTAIN not in calls
    return DirectionRead(
        call=by_measure["net_premium"] if agree else ABSTAIN,
        by_measure=by_measure,
        agree=agree,
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
