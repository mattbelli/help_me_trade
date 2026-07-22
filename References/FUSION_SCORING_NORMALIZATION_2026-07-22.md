# Fusion Scoring Normalization — the liquidity-bias fix (2026-07-22)

**Status: STAGE 1 BUILT 2026-07-22** (was: SETTLED DESIGN, 3-pass converged — orchestrator + founder +
zero-context Grok cross-check). Delta ON `FUSION_HORIZON_REDESIGN_2026-07-21.md` §maturity; supersedes
`maturity = thesis_gross × conviction` as the ranking statistic.

## As built (07-22) — deltas on the slice below

- Build slice 1–4 shipped: `z` in `score_decayed()` (both role blocks, `_z()` beside `_conviction()`),
  `/api/fusion/radar` sorts by thesis z (+ `net`/`z` in rows), Desk = Z primary + Net beside (Mat dropped
  from the widget, kept in the payload), replay tool is z-native — its cut-line is now the DUAL-GATE
  table (per T: ranked = peak z ≥ T vs armable = peak z ≥ T at an instant where conviction ≥ bar).
- **Stage-2 backfill harness shipped in-round** (founder ask, Grok-cross-checked): `fusion_z_baseline`
  table (DERIVED, rebuildable) + `tools/fusion_z_baseline.py`. Pre-dot days replay the RAW stores through
  the SAME intake shaping + scorer — one code path, no live-vs-backfill drift. **Floor = 2026-07-16,
  a LATCH** (pre-break collection poisons baselines: alphabetical sub clip, SPY/TSLA unwatched; bump on
  any future collection change — shaping changes never move it, raw days re-replay through current code).
  **Frozen summary rule**: 5-min ET grid instants; `peak_z` (peak-vs-peak feeds the intraday Stage-2
  percentile), `close_z` (EOD/grading), `peak_armable_z` (T-table fuel); `source` labels dots vs
  raw_events fidelity (raw = flow+tape only, no storm-shed/unstampable drops). **Stage-2 percentiles stay
  ADVISORY below MIN_BASELINE_DAYS=10 rows/symbol** — a percentile over 4 values is a rank, not a
  distribution. 3,310 symbol-days live (07-16..21; false after-hours-only zero-rows are skipped, not written).
- T tables for the founder pick: `research/fusion_rescore/z_baseline_2026-07-16_2026-07-21.md` (full
  universe, 4 sessions) + `rescore_2026-07-21_2026-07-21.md` (at-decision lens, investigated subset).
  Live-parity dot days: T=3.0 arms ~4–13 symbol-days/day, T=4.0 ~3–4. 07-17 QQQ = the design working:
  peak z 5.0 ranked, armable 0.0 (contested all day).
- The LIVE investigate gate still runs `score_dots` — rewiring to z lands with the founder T pick
  (Round-5 ARM at the earliest), never silently.

## The disease (founder-called, live-confirmed)

Raw `|bull − bear|` net mass is denominated in **absolute flow units** — it has no idea what's normal
for the symbol. One disease, three symptoms:

1. **Cancellation-death**: balanced megacap flow → net ≈ 0 → NVDA reads dead all day.
2. **Absolute-dominance**: a modest *proportional* lean on ocean flow → giant absolute net → QQQ tops
   the live radar at maturity 103.7 **while contested** (conv 0.363, 435 dots).
3. **Thin-name wild swings**: three one-sided prints → score spikes from nothing (the INTC
   conviction-1.0 artifact; partly fixed by the shrinkage prior, `CONVICTION_PRIOR_MASS`).

## Stage 1 — significance normalization (this round, zero history needed)

**Ranking statistic** (per clock; thesis lane is the one that ranks):

```
z = |bull_mass − bear_mass| / sqrt(gross + CONVICTION_PRIOR_MASS)
```

Interpretation: multiples of the bull-bear drift pure chance would produce given this much total flow
(null: undirected flow, drift ~ √gross). Self-normalizing across symbols with **no baseline history**.
Reuses the existing shrinkage prior in the denominator — no new dial.

- Radar/Desk **rank by z**; raw net + masses + conviction stay visible (bars unchanged).
- **T is re-derived on the z scale** (old cut-lines VOID again) via `tools/fusion_rescore_replay.py`.
  T is an interpretable dial, **not a literal Gaussian σ** — dots are log-premium, collapsed, bursty
  prints, not i.i.d. coin flips. Calibrate from replay ranks; never present z as a p-value.

### ⚠ The dual gate (Grok catch, folded in — REQUIRED, not optional)

For a fixed lean fraction L, `z ≈ L·√gross` — so a weak lean on a huge ocean still climbs the board.
z alone reintroduces megacap dominance with better branding. Therefore **arming/attention eligibility
(Rounds 4–5) requires BOTH**:

```
z ≥ T  AND  conviction ≥ CONVICTION_BAR        # significance AND effect size
```

(For gross ≥ noise floor this is exactly "z ≥ T AND not contested" — the existing contested flag is
the effect-size gate; no new mechanism.) The radar still *shows* high-z contested names — visible,
honest, amber `contested` — they just can't arm.

**Live proof it works (07-22 radar, 5-session)**: under the dual gate at T≈3, tonight's armable set is
**XLE alone** (z 3.0, conv 0.527) — QQQ (z 6.2), NVDA (z 3.0), SPY, AMD, NFLX, MU all sit below
`CONVICTION_BAR` → contested → ranked but not armable. Verify this exact behavior post-ship: if QQQ
can arm at conviction 0.25, half the fix is missing.

## Stage 2 — per-symbol baseline lens (NECESSARY sequel, not polish; self-populating)

Today's z vs **this symbol's own trailing distribution** of z (RVOL-style percentile — the academic
prior: unusualness-vs-baseline, never absolute floors). Catches what a universal null can't:
chronically-skewed names and index products that look "always leaning" on days normal for them.
Dots persist → the column **back-computes** as history accrues; meaningful at ~2 weeks of dots.
Ships as a second radar/Desk column beside z; the graded ledger referees the two orderings ordinally.

## Index ETFs (QQQ/SPY)

Stay on the radar (hiding real signal is worse than clutter) **and** index-ETF lean also feeds the
regime/context channel so single-name dossiers can cite "index flow leaning bear against your long"
— connecting the dots is the product. If the desk feels noisy the knob is a tag/row-group for ETFs,
never a scoring rewrite. Founder veto open.

## Rejected (complexity theater on a week of data)

Per-symbol learned weights; liquidity buckets as score fudge factors; ad-hoc exclusion lists.
Door open later: **cohort-stratified calibration** (mega/mid/thin shadow tables) if the √-null
miscalibrates in the tails — that's measurement on the ledger side, not a formula change.

## Build slice (small round)

1. `signals/fusion/scorer.py`: add `z` to `score_decayed()` output (thesis + trigger blocks).
2. `/api/fusion/radar`: sort by thesis z desc; keep net/masses/conviction in rows.
3. Desk widget: z = primary column, raw net beside it for a week of side-by-side.
4. Re-run the replay → present the T cut-line table on the z scale (founder dial, as a diff).
5. Verify: the QQQ-contested / XLE-armable live check above.
