# Study C — the horizon × universe 2×2, at breadth (PRE-REGISTERED 2026-07-25)

*Status: CURRENT. Companion to `IV_RANK_BASELINE_PREREG_2026-07-25.md` (Study A, + `ADDENDUM_A`)
and `IV_LEVEL_PREREG_2026-07-25.md` (Study B). Written and committed BEFORE the run.*

## Why this exists

Study A's H4 arm computed forward realized vol from **our own bars over 21 sessions** instead of
the vendor's 30-session `hv_current` column. It ran on **37 symbols** — not because of a data
limit, but because the code sourced its symbol list from the chain sampler's universe, which was
53 names at the time. Alpaca serves daily bars for any ticker; the cap was an implementation
choice that nobody revisited.

That leaves the study's central 2×2 with one empty cell:

| | vendor horizon (30 sessions) | own bars (21 sessions) |
|---|---|---|
| **wide** (~1,700 corpus names) | **+0.0205** (Study A primary) | **← this study** |
| **narrow** (37 names) | −0.0105 | −0.0012 |

The narrow row is flat at both horizons, and the wide/narrow gap at a fixed horizon is large,
which is why Study A concluded *universe, not horizon*. **That inference is not in question here.**
What is untested is whether the wide-universe premium itself survives the horizon correction —
i.e. whether +0.0205, and Study B's +0.0418 tradable-band figure, are partly artifacts of
comparing a ~30-**calendar**-day implied against a 30-**session** (~42-calendar-day) realized.

A second, incidental gain: the own-bars arm takes its forward window from bars, not from the
corpus, so it is immune to the corpus date-truncation that forced Addendum A1. This panel should
span the full ~7 years without the nearest-date workaround.

## Panel construction (fixed here, before the run)

- **Symbols**: every corpus symbol in `volatility_history` for which Alpaca returns at least
  `H4_SESSIONS + 2` daily bars within `H4_BAR_DAYS = 2500`. Symbols returning fewer are dropped
  and **counted**, never backfilled or interpolated.
- **IV**: the corpus `iv_current` column. This study does not and cannot re-derive IV — the
  corpus carries no strikes, quotes or expiries. The IV side stays vendor-sourced; only the
  realized side becomes ours.
- **Forward RV**: `stdev(next 21 daily log returns) × √252`, from our bars. An observation whose
  forward window runs off the end of the bar series is dropped and counted.
- **VRP** = IV − forward RV, in annualized vol points. Positive = the option was rich.
- **Inference**: month-block bootstrap, 2,000 draws, `SEED = 20260725`. Effective n reported in
  **symbol-months**, never row count — the 21-session window overlaps ~21× and names crash together.
- **COVID**: every headline number reported twice, with and without `2020-02-01 .. 2020-04-30`,
  as the parent prereg mandates.
- **Tails are mandatory** on every mean: median, p05, share negative.

## Hypotheses, thresholds, and the consequence of each outcome

### C1 — the missing cell: does a broad VRP survive the own-bars horizon?
Mean VRP over the wide own-bars panel.
**PASS** if the bootstrap 95% CI lower bound > 0, in **both** the full and ex-2020 arms.

- PASS → the broad premium is not a horizon artifact. Study A's +0.0205 / Addendum A1's ex-2020
  +0.0306 stand as reported.
- FAIL → those figures are **horizon-dependent** and must be restated as such wherever they are
  recorded: `BUILD_LOG.md`, `board.json`, and the `iv-rank-is-a-level-effect` memory.

### C2 — is the horizon choice material at all?
`|mean VRP(own bars, 21s) − mean VRP(vendor, 30s)|`, computed on the **intersection of
(symbol, date) observations present in both panels** — a strict like-for-like, not merely the
same symbol list.
**PASS** (horizon immaterial) if the gap ≤ **0.0100** vol pts, roughly a quarter of Study B's
headline effect.

- PASS → the two horizons may be used interchangeably in later work; existing results need no
  horizon caveat.
- FAIL → **all** future VRP work uses the own-bars horizon, and prior figures carry an explicit
  horizon label.

### C3 — does the LEVEL effect survive the better horizon?
Spearman correlation across 10 IV-level deciles on the wide own-bars panel.
**PASS** if ρ ≥ **0.6** (the same bar Study A's H3 used).

- FAIL → Study B's level finding is horizon-dependent. The IV-vs-own-normal perception input is
  **withdrawn** pending re-measurement, and the memory is corrected.

### C4 — the tradable band, at the better horizon
Top IV-level decile restricted to `IV < B4_IV_CEILING = 0.60`, on the wide own-bars panel. This is
the direct analogue of Study B's **+0.0418**.
**PASS** if the bootstrap 95% CI lower bound > 0.

- PASS → the figure survives and may be quoted with the own-bars horizon attached.
- FAIL → **+0.0418 must be labeled vendor-horizon-only** everywhere it appears.

### C6 — survivorship, the bias this design introduces (pre-registered because it flatters us)
Sourcing forward RV from a live bar API means **delisted and bankrupt names silently vanish** —
Alpaca will not serve bars for a ticker that stopped trading. The vendor panel had them; this one
does not. The direction is the dangerous one: a name heading for bankruptcy realizes enormous vol,
which is a large *negative* VRP, so dropping those names **biases mean VRP upward**. Study A's own
example, WOLF realizing 410% into its July-2025 bankruptcy, is exactly the observation at risk.

This is measurable rather than assumable, and the measurement uses a panel where both groups exist.
On the **vendor** panel, split symbols into those Alpaca serves bars for and those it does not, and
compare mean VRP.

**Flag** if the gap exceeds **0.0100** vol pts.

- Within threshold → the own-bars panel is treated as representative.
- Over threshold → **C1 and C4 are reported with an explicit survivorship caveat attached**, and
  the gap is quoted alongside them. The results are not discarded; they are labeled.

### C5 — the deep tier, for the record (descriptive, no threshold)
The same computation restricted to the sampler's **deep** tier — the names we would actually
trade. Reported without a pass/fail.

This exists to keep an honest distinction that the 2026-07-25 breadth work could easily blur:
registering 1,854 corpus names as a **wide collection tier** widened what we *observe*, not what
we *trade*. Study A's "no VRP on our own names" finding is about the deep tier and is **not**
superseded by this study.

## What this study cannot do

- It **does not re-open H1's pre-registered failure**. Study A's verdict on the rank premise was
  accepted and its consequences honored; nothing here revisits that.
- It **cannot open `options-income-lane`**. That lane is blocked on modelling the **capped**
  seller payoff — VRP in vol points is not P&L, a seller's upside stops at the premium while the
  left tail does not. No VRP magnitude, at any horizon or breadth, changes that. A positive mean
  sitting on a p05 below −0.25 is reported as exactly that and opens nothing on its own.
- It does not remove the vendor from the IV side. Only forward realized vol becomes ours.

## Recording rule

Results are appended to this file as a dated **POST-RUN NOTE**. Thresholds above are not edited
after the run under any circumstance; a threshold that turns out to have been badly chosen is
noted, not moved.

---

## POST-RUN NOTE — 2026-07-25 (results; thresholds above unchanged)

Panel: **1,602,950 observations, 2,200 symbols, 118,608 symbol-months, 2020-01-06 → 2026-06-23.**
Bars resolved for 2,285 of 2,311 corpus symbols (98.9%). As predicted, the own-bars arm needs no
Addendum-A1 workaround — it takes its forward window from bars, so it spans the full history the
corpus truncation cost the primary panel.

| | vendor horizon (30 sessions) | own bars (21 sessions) |
|---|---|---|
| **wide** | +0.0205 CI [−0.0015, +0.0373] FAIL | **+0.0259 CI [+0.0047, +0.0428] PASS** |
| **deep (37)** | −0.0105 | −0.0012 CI [−0.0198, +0.0154] |

- **C1 PASS** (both arms). Full sample +0.0259 CI [+0.0047, +0.0428]; excluding Feb–Apr 2020
  +0.0363 CI [+0.0251, +0.0462].
  **Read this carefully rather than as a rescue of Study A's H1.** Addendum A1's 7-year vendor panel
  was a comparable size (1.70M obs / 2,272 symbols) and gave +0.0202 CI [−0.0018, +0.0371] — a
  FAIL. C1's interval is essentially the same *width* (0.0381 vs 0.0389), shifted up by ~+0.0057.
  That shift matches C2's independently measured horizon effect of +0.0051 almost exactly. So the
  full-sample premise **flips from just-failing to just-passing on a measurement convention**, and
  a verdict that moves on 0.005 vol pts is not a robust verdict. The ex-2020 result is the one that
  is not knife-edge, and it holds at both horizons.
- **C2 PASS** — gap **0.0051** ≤ 0.0100 on 634,118 paired (symbol, date) rows. The horizon is
  immaterial to the *magnitude*, which confirms Study A's "universe, not horizon" conclusion at
  breadth. Note the sign: the own-bars horizon reads consistently **higher**, and by exactly the
  amount that decides C1's full-sample call. Immaterial for magnitude, decisive for a knife-edge
  threshold.
- **C3 PASS** — Spearman **+1.000**, monotone across all ten level deciles, from −0.0067 (mean IV
  0.192) to +0.0909 (mean IV 0.833). The level effect is fully robust to the horizon correction.
- **C4 PASS** — **+0.0445 CI [+0.0178, +0.0661]** on 2,083 symbols under the 60% ceiling, against
  Study B's +0.0418 CI [+0.0168, +0.0630] at the vendor horizon. The tradable-band figure survives.
- **C5 (descriptive) — unchanged, and this is still the number that matters.** On our 37 deep-tier
  names: −0.0012 CI [−0.0198, +0.0154], identical to Study A's H4. In the band: −0.0035
  CI [−0.0318, +0.0231].

### C6: the pre-registered survivorship concern was empirically WRONG

The threshold fired (gap **0.0379** > 0.0100), so per the recording rule C1 and C4 carry the
caveat. But the reasoning behind C6 does not survive contact with the data, and saying so is the
point of having measured it:

1. **The premise was false.** C6 assumed "a live bar API cannot serve a delisted ticker." Alpaca
   serves them. Checked directly: **BBBY 1,718 bars, WOLF 1,718, FRC 907, SIVB 872** — Bed Bath &
   Beyond, Wolfspeed, First Republic and Silicon Valley Bank are all in the corpus *and* all have
   full bar history. The blowups are in the panel, not missing from it.
2. **The flag rests on 5 symbols.** Only 5 vendor-panel symbols lacked bars, against 2,200 that
   had them.
3. **The direction is the opposite of the concern.** Those 5 had *higher* VRP (+0.0583) than the
   covered names (+0.0204), so their absence biases the result **down**, not up.

The caveat is recorded because the prereg says thresholds are not moved after the fact. Its
practical weight is nil. What C6 **cannot** see is the corpus's own curation — DoltHub chose these
2,311 symbols, and that selection is upstream of anything measured here.

### What this does not resolve, and what it newly raises

`options-income-lane` stays shut. Nothing here touches the capped-payoff blocker, and C5 says the
premium is absent on the names we actually hold.

**A new gate, raised by C4 against C5.** The wide band (+0.0445) and our own band (−0.0035) sit at
nearly identical implied vol — **mean IV 0.554 vs 0.547**. Same vol level, opposite sign. So the
discriminator is *which names*, not *what IV*. Their intervals do overlap slightly
([+0.0178, +0.0661] vs [−0.0318, +0.0231]), so this is suggestive, not decisive.

The natural hypothesis is competition: our watchlist is megacap and index ETFs, the most heavily
option-sold instruments in the world, while the corpus tail is not. **If that is the mechanism, it
is bad news, not good** — options are rich where few people sell them, which tends to be where the
bid-ask is widest. Every number in Studies A, B and C is derived from vendor mid-market IV and
therefore **ignores the spread entirely**. Before any capture claim, the spread in those names has
to be measured and netted. That is now a second gate standing beside the capped payoff, and it is
untested.
