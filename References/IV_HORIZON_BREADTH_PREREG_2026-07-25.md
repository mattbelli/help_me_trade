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
