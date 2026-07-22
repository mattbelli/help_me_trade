# Fusion Scoring Normalization — the liquidity-bias fix (2026-07-22)

**Status: SETTLED DESIGN** — 3-pass converged (orchestrator + founder + zero-context Grok cross-check,
07-22). Builds as its own small round immediately after Round 3 (lifecycle) lands — same files, one
tree-writer. Delta ON `FUSION_HORIZON_REDESIGN_2026-07-21.md` §maturity; supersedes
`maturity = thesis_gross × conviction` as the ranking statistic.

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
