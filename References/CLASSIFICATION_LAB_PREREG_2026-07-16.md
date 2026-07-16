# Classification Lab — Pre-Registration (2026-07-16, before first data)

Founder-specified methodology, recorded BEFORE the first ground-truth day accrues
(earnings_drift pre-registration discipline: the scoring rules may not be revised
after seeing results; any revision gets a dated addendum and re-scores from day 1).

**Question:** can a cheap classification method (no standing quote subscriptions)
match true at-print DIR-1 classification well enough that the OPRA quote-sub cap
stops constraining the index-pressure gauge — or is the paid trade+quote-paired
feed (Databento TBBO, ~$199/mo) justified with a measured number?

## Ground truth
`data/research/print_lab/<date>.jsonl` — every classified print in the gauge
windows on the subscribed slice, with the exact quote DIR-1 used (bid/ask/q_ts).
Only prints with `basis=stream_quote` score as truth; `undetermined` prints are
excluded from accuracy but counted (a method claiming a side where truth refused
to guess is scored separately as "overreach rate").

## Methods under test
1. **Naive tick test** — sign of price change vs previous trade on the same
   contract (founder Method 1).
2. **Delta-adjusted tick test** — classify the RESIDUAL after removing the
   underlying-driven component (founder's formula):
   `resid = (P_t − P_{t−1}) − [Δ·(S_t − S_{t−1}) + ½Γ·(S_t − S_{t−1})² + V·(σ_t − σ_{t−1})]`
   Δ/Γ/V/σ from the 15s chain sampler (nearest sample ≤ trade ts); S from
   historical equity trades REST (exchange timestamps on both sides — capture-lag
   sync noise does not apply offline; flagged for any future LIVE variant).
   Note: SPX/SPXW have no equity tape — use SPY×10 as the S proxy, scored
   separately, or exclude; decided by data quality, recorded either way.
3. **Sampled-quote back-pairing** — Lee-Ready against the sampler's quote
   timeline, at effective sampling intervals 15s / 30s / 60s (downsampled).
   Lookup variants, each scored separately (founder Method 2 guidance):
   - **strict backward**: closest quote sample with ts ≤ trade ts (never forward —
     forward-looking pairing systematically flips buyer sweeps to sells);
   - **interpolated**: linear interpolation between surrounding samples.
   A 5s bound is estimated from the print-lab's own tick quotes downsampled to a
   5s grid (approximation — quotes observed only at print times; labeled as such).

## Metrics (per method × variant × name × window)
- **Agreement %** vs ground truth (side-classified prints only), overall and
  premium-weighted (a wrong 5-lot ≠ a wrong $2M sweep).
- **Directional bias** — signed error asymmetry: P(truth=buy | method=sell) vs
  P(truth=sell | method=buy), premium-weighted. THE gating metric: symmetric
  errors wash out of a net gauge; asymmetric errors flip its sign exactly at
  breakouts (founder's "danger zone").
- **Spike conditioning** — all metrics recomputed on the top-decile volume
  minutes only. A method is judged on its spike-minute bias, not its average.
- **Time-of-window decay** — first-5-minutes vs rest (open burst = worst case
  for both quotes and underlying sync).
- **Sampling-rate decay** — back-pairing accuracy at 15s vs 30s vs 60s (+5s
  bound); the founder's explicit question: how fast does it degrade?
- **Net-gauge fidelity (the end-to-end test)** — recompute each window's
  per-name net premium using each method's labels; report correlation, sign
  agreement, and max magnitude error vs the ground-truth net.

## Success thresholds (set now, before data)
A method is VIABLE for gauge use if, on spike-conditioned premium-weighted terms:
agreement ≥ 85%, directional bias ≤ 5 percentage points asymmetry, and
window-net sign agreement ≥ 95% with magnitude error ≤ 15%. A method that
passes average-conditioned but fails spike-conditioned is REJECTED (danger zone).
If no method passes after ≥ 5 ordinary sessions (OpEx days scored separately,
never pooled), the paid paired feed is justified by this record.

## Data inputs and their limits
- print_lab (ground truth): subscribed slice only (~1,000 contracts incl. the
  gauge lane) — results extrapolate to unsubscribed contracts only by assumption;
  stated wherever quoted.
- chain sampler 15s: quotes/greeks/IV per contract, gauge windows only; 15s is
  the floor for direct back-pairing tests (5s = bounded estimate).
- Historical equity trades REST: underlying S_t at exchange timestamps.
- 2026-07-17 is monthly OpEx: scored, kept as its own cell, never pooled with
  ordinary days.
