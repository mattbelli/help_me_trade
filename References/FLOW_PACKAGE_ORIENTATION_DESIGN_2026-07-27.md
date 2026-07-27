# Package orientation + cross-tape leverage — design proposal, 2026-07-27

*Source: a zero-context ideation conversation (founder ↔ external agent, 2026-07-27), entered into
the record per the prereg discipline BEFORE any code. Companions: `FLOW_ROADMAP.md` (the ordered
plan — this doc does not reorder it), `FLOW_ALPHA_RESEARCH_2026-07-26.md` (read its verification
header first), `FLOW_BASELINE_PREREG_2026-07-26.md` + Addenda A–D.*

**Status: PROPOSAL, awaiting founder review against the corpus facts. Nothing here changes any
registered rule.** The direction rule, the multileg grain, the abstention band and every Addendum
A–D definition stand exactly as registered. Anything below that touches the registered study enters
it only via **Addendum E (or later), never by editing** — the C→D pattern is the law here.

Every claim below carries one of three labels:
**[VERIFIED]** checked against code or measured data · **[RECORDED]** already in the record, cited ·
**[UNVERIFIED]** a hypothesis with a named check. Unverified claims are build *inputs*, never facts.

---

## 1. Reconciliation — what the conversation proposed that the record already holds

The external agent had no repo context, so much of its advice re-derives what is already built or
registered. Recording the mapping so nobody re-litigates it:

| Conversation idea | Where it already lives |
|---|---|
| "Upgrade single-leg signing with the actual prevailing BBO" | **Built and registered.** The midpoint rule on the print's own pre-trade NBBO is the PRIMARY classifier (Addendum A; 87.83% of prints signed). [RECORDED] |
| "Position-in-spread as a continuous aggression feature" | **Built and registered.** That is exactly `u = (price − mid)/half` (Addendum C), stored raw + clipped. [RECORDED] |
| "Sweeps: N prints are one order — don't double-count" | Roadmap 1.3, DONE: `orders` column; one BBO snapshot per sweep (99.4% measured). [RECORDED] |
| "OI deltas give open-vs-close, not direction; rolls/close-flags are the value" | Roadmap Phase 2, including 2.3's known limit. [RECORDED] |
| "Match dealer hedge prints in the equity tape; size ≈ delta × 100 × contracts is the falsifiable test" | Roadmap Phase 3 (3.1–3.4), including the clock-skew measurement and the honest failure modes. [RECORDED] |
| "Cross-feed clocks: match in ms, not ns" | Roadmap 3.1 measures the skew before anything matches. [RECORDED] |
| "Corporate-actions table from day one" | Roadmap 1.4 + the `no_spot_missing` per-root breakdown (with the 07-27 retraction: the bucket mixes four causes; read the breakdown, not the bucket). [RECORDED] |
| "Cboe Open-Close as a validation/label sample" | Named in the research round as the label path. [RECORDED] |

## 2. Conversation claims REFUTED or corrected by corpus facts

These are the facts the founder asked this doc to be reviewed against. The external agent assumed a
condition-code-rich tape; ours is not.

- **"MLET/MLAT/MLCT/MLFT condition codes mark multi-leg prints; TL\* codes mark tied-to-stock."**
  **Not in our dataset.** [VERIFIED — Addendum D / `tools/tcbbo_flags_probe.py`]: DBN `flags` is
  constant at 194 across all 6,954,842 prints of 2025-07-28; OPRA `tcbbo` via Databento carries **no
  complex-trade condition code and no tied-to-stock marker**. Package detection here is the
  registered ns-collision heuristic (same `ts_event`, different instrument), and roadmap **1.9**
  (validate that heuristic from `ts_recv`/quote-size/granularity, free) is the prerequisite for
  everything in §3. The TL\*-as-advance-notice "cheat code" and the MLAT auction-print customer
  prior are **unavailable** unless we later buy condition-code days (already queued in the research
  round's build list — unchanged by this doc).
- **`side` is 'N' on 100% of prints** [VERIFIED 07-26] — direction is quote-derived by necessity;
  nothing in the conversation changes that.
- **The four-critic kill on package direction stands at the per-leg level.** Nothing below claims
  per-leg buy/sell recovery. What §3 attacks is a different quantity the kill never evaluated.

## 3. The new mechanism — orientation from the NET price in the synthetic band

**The one genuinely new, cheap, on-disk-testable idea in the conversation.** [UNVERIFIED — probe
specified below.]

The four critics killed per-leg direction because leg-price *allocation* is arbitrary. But the
allocation must **sum to the negotiated net package price** — the per-leg prices can be shifted
against each other, their difference cannot. So the net is the quantity allocation cannot destroy,
and the net has a market to be located in:

For a two-leg group with legs A and B (allocated prices `pA`, `pB`, per-leg pre-trade BBOs on each
print — [VERIFIED: `bid_px_00`/`ask_px_00` ride every print in the raw DBN files, retained on
disk]):

```
synthetic band for "buy A / sell B":  [ bidA − askB ,  askA − bidB ]
d = pA − pB
o = (d − (bidA − askB)) / ((askA − bidB) − (bidA − askB))   # position in the band, 0..1
```

`o` near 1 ⇒ the aggressor bought A and sold B; near 0 ⇒ the reverse. A bull call spread and a bear
call spread — identical instruments, identical OI footprint — sit at **opposite ends of the same
band**. Orientation, recovered from outside the corrupted per-leg signs.

Honest limits, stated in advance:

- **"Allocation sums to the net" is an assumption about exchange reporting**, not a verified fact.
  [UNVERIFIED] The probe tests it implicitly: if leg-price differences of flagged packages were
  arbitrary, `o` would not concentrate anywhere; real packages should pile up near the band edges
  far more than shuffled leg-pairings do. That distribution is the first output.
- **Complex orders often fill inside the synthetic NBBO** (COB matching, price improvement), so `o`
  is a **continuous feature feeding a classifier, never a rule**. Same doctrine as `u`.
- **Scope first pass: 2-leg, equal-size, same-underlying groups.** Ratio spreads and k>2 legs need a
  size-weighted net and a 2^(k−1) hypothesis space — measure what premium share the simple case
  covers before generalizing.
- **Sign-convention hazard** (from the conversation, worth recording): which leg is "A" silently
  flips the entire label set. Required sanity check: aggregate `o`-implied direction must skew the
  right way on days with known subsequent moves, and later against Cboe Open-Close buckets.
- **This is perception until validated.** If it is ever graded against outcomes, the grading spec
  enters Addendum E (or its own prereg) first.

**Probe (no schema change, no re-roll):** `tools/package_orientation_probe.py` reading raw DBN
files for a handful of already-rolled sessions — reassemble 2-leg groups via the registered
heuristic, compute the `o` distribution vs a shuffled-pairing null, report premium coverage of the
simple case. Runs where the data lives (serv/dev tree — no corpus data in this worktree).
**Gated behind roadmap 1.9** — the flag it builds on must be validated first, and 1.9 is already
next in the phase order.

## 4. The classifier frame (how the pieces compose, later)

The conversation's framing, adopted as the end-state for the package grain: **do not hand-craft a
direction rule for packages — build a calibrated classifier.** Features: `o` (§3), matched hedge
prints (Phase 3), OI deltas (Phase 2), time-of-day, size, moneyness. Labels: Cboe Open-Close daily
buy/sell × open/close by customer bucket — aggregate, but on strike-days dominated by one large
package it is effectively a per-trade label; bootstrap there, score the ambiguous rest with
calibrated confidence. Output is a **probability, labelled as one** — never a hard call, never a
fabricated certainty. This turns Addendum D's fenced grain into a *probabilistically labeled* grain,
which is the honest ceiling. **Sequencing: after Phases 1–3 deliver the features; not before.**

## 5. Phase extensions proposed (each slots into the existing order, none reorders it)

- **Phase 2 (+)** — **Roll detector**: one leg's OI down, next expiry's up, same direction — the
  "paying to maintain a position" intent stream. Nearly free once 2.3 exists. ⚠️ OI is **T+1
  information**; any backtest use must see it next-morning only. That leakage rule belongs in
  Addendum E alongside 1.4's terminal-value rule.
- **Phase 3 (+)** — **QCT condition codes on the Alpaca SIP tape**: tied-to-stock equity prints may
  carry the Qualified Contingent Trade condition, which would pre-identify hedge prints and sharpen
  3.2's matching. [UNVERIFIED — check whether Alpaca passes QCT through in historical trades before
  building anything on it.] Also from the conversation: handle **cancels/corrections (busted
  trades) on both tapes** before any matched sample is graded.
- **Phase 3 (++, research spikes after 3.2 passes)** — **Hedge-lag/completeness as a signal**
  (instant full-size hedge = facilitated, low info; slow/absent = inventory absorption, dealer
  positioning info) and **empirical dealer-gamma from observed hedges**. ⚠️ The research round
  killed "dealer-gamma state from *accumulated aggressor flow*" — this is **directly observed**
  hedges, a different mechanism, but it stays fenced until 3.2's size test passes.
- **1c (+)** — **Intraday lead-lag**: 1.11b's ±10-day lag scan, repeated at intraday resolution on
  the ns tapes around large prints. Same arrow-of-time question, finer clock. Runs under the looks
  ledger like every other 1c scan.

## 6. New operational fact — record before anyone designs around the live/historic seam

**Databento historical publishes in exact 10-minute blocks with a 23–33 minute sawtooth lag**
(founder-measured, 2026-07-27). [RECORDED here as the founder's measurement.] Consequence: a
**delayed-live lane** — polling the historic API intraday — can deliver whole-market `tcbbo` at
~30 min latency for $0 inside the L1 window. That is useless for sweep alerts, possibly useful for
30-min-cadence regime/aggregate reads (Phase 4 consumes daily aggregates; an intraday version would
ride this). Not proposed for build now — recorded so the option is priced correctly when Phase 4
arrives, and so nobody assumes the historic API is end-of-day-only.

## 7. What this doc explicitly does NOT do

- No change to the direction rule, the three weightings, the abstention band or its states
  (Addenda C/D). Any future change = **new addendum, never an edit**.
- No change to the multileg grain or its heuristic definition. A validated vendor indicator or a
  purchased condition-code day would change it via its own addendum, exactly as Addendum D declares.
- No reordering of `FLOW_ROADMAP.md`. Phase 1 (corpus integrity → four measurements → 1c discovery
  → Addendum E) still gates everything; the §3 probe is gated behind 1.9 specifically.
- No grading, no backtest, no win% — §3's probe output is a distribution and a coverage number,
  nothing more.
