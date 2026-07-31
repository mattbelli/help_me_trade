# H1 pre-registration — opening-classified flow on a quiet tape

*⚠️ DRAFT v1 — NOT LAW until its adversarial gauntlet converges. This is the registered
follow-up hypothesis H1 of `FLOW_1_14F_FINDINGS_REGISTRATION.md` §R.4 (LAW 2026-07-31),
written BEFORE any H1 statistic exists. Together with the Addendum G scoring-run grade it
constitutes the registered fair trial of the Databento `tcbbo` subscription — and per R.4's
law: **the subscription consequence is NON-ADMISSIBLE in this measurement** (no coordinate,
cell, reading rule, or re-run may be chosen, widened, or repeated because the subscription
outcome depends on it; a null on the first registered run is a complete discharge;
first-run-governs). Orchestrator, 2026-07-31.*

**Mechanism sentence (per the 2026-07-30 standing rule):** a trader with genuine private
information prefers to size BEFORE the move, on a quiet tape (better prices, lower impact),
and prefers OPENING new positions to closing old ones; the counterparty is a market-maker
quoting a calm book who will hedge mechanically, not a crowd chasing a move. The
1.14f/1.14g record showed the unconditioned tape is dominated by reactive flow riding
names already moving — this design measures the complement those results could not see.
The persistence question (why isn't this arbitraged away?) is answered by the instrument
itself: opening-vs-closing cannot be observed live by anyone using standard feeds — it
requires next-day OI reconciliation — so the niche is structurally under-exploited at
retail-relevant capacity.

**Instrument ceiling, inherited from roadmap §2.3 and carried on every surface:** the
open-share estimate `f_open` is a T+1 DAILY AGGREGATE — ΔOI reconciled against the day's
prints, published pre-open for the prior session, never a per-print or intraday label, and
blind to which party opened. **This is a RESEARCH measurement on that proxy.** A null
attributable to the instrument (coverage, aggregation) is recorded as instrument-limited,
not as evidence against the mechanism — but it still discharges the fair trial (R.4).
`f_open` is used CROSS-SECTIONALLY ONLY (its >0.5 level is an OI-decay artifact — the
registered §2.3/§2.4 reading; never thresholded on level).

---

## H1.1 The registered event definition (one parameterization, no variants)

Pipeline: the **obligation-3 fenced equity pipeline verbatim** — v7 fence
(`SPREAD_MAX_FRAC = 0.50` + quote validity), package prints masked per F.1, the frozen
`root_classification.json` equity universe with identities resolved (date-scoped flow,
terminal-symbol reads), classified premium operative, $250k day floor on the EVENT day
only, ≥15-of-20 presence. Same cache (`data/order_grain_null_v7_equity/`), same single
implementation — nothing re-derived.

A name-day (identity, session `d`) is an **H1 event** when ALL of:
1. **Opening-heavy:** `f_open_pct(d)` — §2.3's volume-weighted open-share, re-keyed onto
   identities as built at obligation 3b — is at or above the **80th cross-sectional
   percentile** of that session's eligible name-days (within the same session, across the
   whole eligible equity universe; percentile computed only over name-days with an OI
   match — unmatched name-days are INELIGIBLE and counted).
2. **Quiet tape:** same-session Parkinson range RV (`rv_park`, the 1.14g stratifier,
   5-min bars, per-bin averaged) is at or below the **33rd cross-sectional percentile**
   of the same eligible set — the name is in the calm third of that day's market, while
   its options tape is in the open-heavy fifth.
3. **Floor:** ≥ $250k classified premium on `d` (the registered floor, event day only).

Both percentiles are WITHIN-SESSION cross-sectional ranks (no trailing calibration, no
tunable window) — chosen so the definition has zero free time-series parameters. The
event's **direction** is the sign of the day's classified call/put premium tilt (base
prereg 68-72's `dir`, RAW; exact-zero tilt ⇒ the event is direction-less and enters the
magnitude arm only, counted).

## H1.2 Outcome, metric, and the two registered arms

**Metric: Addendum G §G.2's, verbatim** — `alpha = [(close_{d+10}/open_{d+1} − 1) −
(SPY same)] × open_{d+1} / ATR14` with G.2's pinned ATR14 (14 TRs through `d`), entry at
next open, 10td horizon of record, and the FOUR-case terminal-value rule + benchmark
blocking rule applied identically to events and baseline. Grid 3/21d reported,
non-authoritative.

- **PRIMARY (kill authority): the DIRECTIONAL arm.** Event observations signed by the
  event's tilt direction; the no-signal baseline = all eligible name-days of the SAME
  session set that fail condition 1 or 2 (but clear the floor), signed by their own tilt
  sign, zero-tilt excluded and counted. Estimand: difference of equal-weighted
  per-event-date means, event minus baseline (the G.4 estimand shape). One-sided,
  registered-positive. The mechanism claims a SIDE; magnitude alone cannot confirm it.
- **SECONDARY (reported, labelled, no kill authority): the MAGNITUDE arm** — |alpha|
  version of the same contrast, for continuity with the 1.14f magnitude record.

## H1.3 Windows, ordering, and the touch rule

- **Training window** (calibration only): 2025-07-28 → 2026-03-31 (G.4's dated split).
- **Judgment window: the test split 2026-06-01 → 2026-07-24 minus 2026-07-23 (37 dates)**
  — the same window as Addendum G's cell, under these ORDERING RULES: (i) this
  registration must be LAW before the close of 2026-08-07 — i.e. before ANY outcome
  statistic exists on the split (G.10's touch rule); (ii) the H1 scoring run executes
  STRICTLY AFTER the Addendum G scoring run's verdict is recorded (no interleaving, no
  peeking at G's intermediate outputs); (iii) H1's run reads the same frozen artifacts
  (fenced cache, classification snapshot, OI reconcile) and computes no new statistic on
  any window other than those registered here.
- The G.2 scoring run's outcome grade of the split does NOT contaminate H1: H1's design
  is frozen before that grade exists, and no H1 coordinate can react to it.

## H1.4 Inference — G.4's machinery by reference, new seeds

Collapse to the per-event-date difference series; **Newey-West HAC, 9 lags, SPY-session
gap, G.4's exact written formula** (t>s pair set, 1/G normalizer, zero-pair gaps
contribute 0, fallback `LRV = Var(x, ddof=1)` on the series in hand, denominator
`sqrt(LRV/G)`). **Cutoff: size-calibrated exactly as G.4** — training-window per-date
difference series at THESE coordinates, demeaned, circular moving-block bootstrap (block
10, ⌈G/10⌉ draws truncated to G), 100,000 replications, **seed 20260818**, identical
in-replicate variance rule, cutoff = the 99th percentile of signed replicate t. **MDE
simulated on the 0.01-ATR grid, ≥80% power, seed 20260819; MDE > 1.00 ATR ⇒
POWER-LIMITED.** Calibration series = training dates carrying ≥1 gradeable H1 event;
the calibration panel starts at the corpus's first session (short-lookback dates
retained). Recorded before the scoring run; first run governs.

## H1.5 Verdict — ordered checklist, every input lands exactly once

1. **Evaluability:** ≥ 20 distinct complete-or-terminated event dates AND ≥ 30 events in
   the judgment window; the f_open match-coverage on event days ≥ 70% (below ⇒ the
   instrument cannot support the measurement); the regime-span rule of G.2 (both ±2% SPY
   tails represented on evaluable event dates, composition reported); not POWER-LIMITED.
   ANY failure ⇒ **UNEVALUABLE — instrument-limited or under-powered, recorded as such;
   no substitute cell, no re-declaration, no re-run** (and per R.4 it still discharges
   the fair trial).
2. **The test:** the directional per-event-date difference series against the calibrated
   one-sided cutoff — pass ⇒ **ALIVE**; fail ⇒ **DEAD**. DEAD reads "opening-heavy flow
   on a quiet tape carries no directional edge at 10td ON THE T+1 AGGREGATE PROXY" —
   never more than that. A large wrong-way t is DEAD and may never be called significant.
   The magnitude arm changes no verdict.

No dilution gate: the event rule here is a fixed cross-sectional rank, not a
threshold-vs-null selection, so the G.9b machinery does not apply; the evaluability
coverage floor plays the analogous honesty role.

## H1.6 Pre-run obligations (in order, each recorded, first-run-governs)

1. This registration LAW (gauntlet converged) — before the close of 2026-08-07.
2. **Feasibility record (input-side only, no outcomes):** training-window event counts
   per session, f_open match coverage on event days, the percentile cut values realized,
   baseline sizes — recorded so the evaluability bars are checked against reality before
   the run. If training-window events average < 1 per session, the pre-run record says so
   and the run proceeds anyway (the bars in H1.5 govern; no redefinition).
3. The G.4-style calibration + MDE record at the recorded date count and gap vector.
4. The scoring run: after the Addendum G verdict is recorded, no earlier than the close
   of 2026-08-07.

## H1.7 What this registers and what it does not

Changing any coordinate, percentile, window, seed, or the verdict grammar = a new dated
addendum. This document creates NO gate on the Addendum G run and inherits R.4's
non-admissibility clause verbatim. The two fair-trial verdicts (G's grade + this one) go
to the founder as a record; the subscription decision is his, made on that record.
