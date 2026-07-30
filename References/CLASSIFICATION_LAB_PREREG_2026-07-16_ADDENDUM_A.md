# Classification-lab prereg — Addendum A: the sign-agreement unit, and unmeasured ≠ failed

*Dated addendum to `CLASSIFICATION_LAB_PREREG_2026-07-16.md`, registered 2026-07-30 — BEFORE any
scoring run: the scorer has executed only on synthetic fixtures; no registered metric has been
computed on real captures. The prereg text stands unedited. No threshold VALUE changes here —
only the unit one threshold is evaluated on, which the prereg never defined and which turned out
to be unevaluable under the natural per-cell reading.*

## A.1 The defect, stated

The prereg gates viability on "window-net sign agreement ≥ 95%" but never says over WHAT
population the percentage is taken. The per-cell reading (one observation per session inside a
method × variant × name × window cell) makes the threshold arithmetically unreachable at the
≥5-ordinary-session gate: five observations resolve only in steps of 20%, so "≥ 95%" silently
becomes "zero sign flips ever" — stricter than the number registered — and under spike
conditioning a cell can be decided on one or two sessions.

## A.2 The unit, registered

- **Sign agreement (the ≥95% gate) is evaluated on the pooled set of (session × name × window)
  net observations within a method × variant × session-class** — at five ordinary sessions,
  thirteen names and two windows that is ~130 observations, where 95% is resolvable.
- Per-cell nets remain computed and reported exactly as the prereg's metrics section describes;
  only the gating percentage pools.
- **Every sign-agreement figure is reported with its realized N and achievable resolution
  (1/N)**, so a 5-of-5 can never be read as "95%".
- The other three thresholds (agreement ≥ 85%, bias ≤ 5pp, magnitude error ≤ 15%) already have
  well-defined units and are untouched. Magnitude error remains a per-cell max.

## A.3 Unmeasured ≠ failed, registered

A cell whose method input is structurally absent — no underlying-trades tape for the
delta-adjusted method, no chain samples for a name outside the sampler's nine roots, a session
with no greeks — is reported **UNMEASURED**, with the absence named. It is not a failure and it
does not enter the method's viability roll-up. The prereg's "if no method passes … the paid feed
is justified" clause reads on methods that were MEASURED and failed; concluding "buy the feed"
from cells that were never measurable would be manufacturing a conclusion from missing data.
SPX/SPXW cells riding the SPY×10 spot/underlying proxy are their own labelled slice
(`underlying_proxy: "SPY x10"`; greeks come from the chain sampler, the proxy supplies the
underlying move), reported separately, excluded from the unproxied roll-up.

## A.4 What would change this

Changing any threshold value, the pooling unit, or the unmeasured/failed boundary — a new dated
addendum, never an edit. If a scoring run is discovered to have preceded this addendum's
registration, the run is void and re-executed after it.
