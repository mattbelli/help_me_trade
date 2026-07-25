# Kronos NIGHTLY probe — pre-registration (1-hour bars, next-session horizon)

**Status: unproven experiment, nothing wired into the app.** This is a second,
independent pre-registered pass of the harness in `README.md`, at a different
frequency. It shares one resolver, one bootstrap and one scorer with the 5-minute
probe — that shared code is what makes the two runs comparable — and differs only in
the parameters listed below (`config._NIGHTLY`).

**The 5-minute result does not transfer, and neither does its configuration.** Two of
the choices below *invert* at this frequency. Both inversions were found before any
outcome was scored, and each is recorded with the measurement that forced it.

## The question

> Does Kronos's 3-way outcome distribution beat a vol-matched circular block
> bootstrap's, on 1-hour US-equity bars, over the next trading session?

Beating a coin flip proves nothing. Beating the bootstrap — which already knows recent
volatility, drift and autocorrelation, and knows nothing about the *shape* of the
recent path — is the whole question.

Absolute scores from this harness are **not publishable** and must never reach a
user-facing surface. Only the paired difference is meaningful: survivorship, regime
and drift land on both arms and cancel in the difference.

## PRE-REGISTRATION — fixed before the first scoring run

| Parameter | Value |
| --- | --- |
| Universe | SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL |
| Primary window | 2025-09-01 → 2026-07-01 (`nightly_primary`, post-cutoff) |
| Leakage window | 2023-01-01 → 2023-12-31 (`nightly_leakage`, pre-cutoff) |
| Bars | **1-hour, Alpaca SIP**, RTH only, split-adjusted |
| Context | **360 bars** (~51 sessions), `max_context` 512 |
| Horizon `H` | **7 bars = exactly one RTH session** |
| Barriers | ±1.0 · σ̂ · √H, symmetric |
| σ̂ | EWMA(λ=0.94) over context log returns, causal, **gap-INCLUSIVE** |
| Sampler | **T = 0.7**, top_p = 1.0 |
| Resolution | close-only first touch, identical for all arms |
| Outcome | 3-way: up / down / timeout |
| Paths per arm | 200 |
| Evaluation point | **last RTH bar of each session, one per session** |
| Bootstrap block | **7 bars = one session** |
| Probability | Laplace-smoothed, `(count+1)/(n+3)`, both arms |
| Primary metric | multiclass log-loss |
| Secondary metric | multiclass Brier |
| Uncertainty | paired per-point differences, block-bootstrapped over **session dates** (95%) |
| Eval points | **2,040** (204 per symbol × 10) |

### Why H = 7 is one session, measured not assumed

Alpaca's 1-hour bars are ET hour-aligned and stamped at the bar's **start**, so an RTH
session is exactly `09:00, 10:00, …, 15:00` — **7 bars**. Verified across all 10
symbols: 291 of 291 sessions have exactly 7 RTH bars. The 09:00 bar spans 09:00–10:00
and so carries 30 minutes of pre-market in its OHLV; its *close* is a 10:00 RTH print,
and resolution is close-only, so the outcome is unaffected.

H = 7 is chosen over the literal 24-bar reading because it is the product-natural
horizon for a nightly job. If it passes, multi-session is a separate follow-on
question, not a second bite at this one.

### Inversion 1 — the overnight gap: σ̂ is gap-INCLUSIVE here

The 5-minute probe **drops** cross-session returns; this one **keeps** them. The
governing principle is the same in both cases: *estimate σ̂ on the return process the
horizon actually traverses.*

- 5-minute, H=12 at 10:00/12:00/14:00 — entirely intraday, crosses no gap. Gaps are
  contamination (they inflated σ̂ up to 2.46×).
- **Nightly, H=7 from the session close — crosses exactly one gap.** Gaps are signal.

The arithmetic lines up exactly at this frequency: the forward window is 1 gap + 6
intraday steps, and the return series is periodic with 7 steps per session, so *any* 7
consecutive returns contain exactly one gap. A 7-bar circular block therefore
reproduces the horizon's composition by construction rather than by assumption.

σ̂, the bootstrap's resampling pool, and the realized outcome all use this one
convention. Had the bootstrap drawn from a gap-free pool while Kronos was scored on
gap-inclusive outcomes, the bootstrap *could not produce a gap-sized move* and Kronos
would win on unconditional scale rather than conditional shape — the one thing this
probe claims not to measure.

`barriers.context_log_returns` takes `keep_gaps` as a **keyword with no default**, so
neither profile can inherit the other's convention silently.

### Inversion 2 — the sampler: T = 0.7, not T = 1.0

`calibrate.py` re-run at this frequency (AAPL, 20 points spread across the window),
comparing Kronos's terminal-log-return dispersion to EWMA σ̂·√H. **This measures scale
only — it never touches outcomes, barriers or scoring.**

```
T=1.0/p=0.80 -> 0.97x      T=0.85/p=1.00 -> 1.21x
T=1.0/p=0.90 -> 1.12x      T=0.80/p=1.00 -> 1.13x
T=1.0/p=1.00 -> 1.38x      T=0.75/p=1.00 -> 1.07x
                           T=0.70/p=1.00 -> 1.00x   <- chosen
                           T=0.60/p=1.00 -> 0.86x
```

At 5 minutes the failure mode was paths that were too **narrow** (0.61× at p=0.9); at
1 hour T=1.0 is 1.38× too **wide**. `top_p = 0.80` also matches on scale (0.97×), but
nucleus truncation buys that match by deleting support, and the scored outcome is a
barrier touch — a tail event. Temperature re-weights the full distribution instead, so
scale is corrected with T and **top_p stays 1.0**.

Sampled on 5 points the T=1.0 ratio first read 1.73×; on 20 points it reads 1.38×.
Calibrate on enough points that the estimate is stable, or the sampler gets picked by
noise.

### Instrument diagnostics (no model output involved)

Measured over all 2,040 points before the scoring run:

- `std(realized terminal return / EWMA σ̂·√H)` = **1.274**. The EWMA reference
  understates true next-session dispersion by ~27%, so **both arms sit ~22% narrow of
  truth** — the bootstrap by construction, Kronos by calibration to the same
  reference. This is deliberate: equalising the arms' unconditional scale is what
  leaves conditional *shape* as the only thing that can differ. It is recorded here so
  no one later reads either arm's absolute width as a volatility claim.
- Realized outcome base rates: **up 24.9% / down 23.7% / timeout 51.4%.** Symmetric and
  non-degenerate, which is what the symmetric barriers are for.

### Early closes — a half-day is invisible in the bar count

On the SIP tape an early-close session still yields a full 7-bar grid: thin
after-hours prints fill the 14:00 and 15:00 slots. On 2025-11-28 SPY's 15:00 bar
carried **89k shares against a 9.5M session median**, so its "close" is not a price
anyone could trade.

Rule: drop any session whose final RTH bar has **< 10%** of that session's median bar
volume, both as an evaluation point and as a forward window. Volume-based rather than
a hardcoded holiday list so it carries to any window unchanged. Across 10 symbols ×
291 sessions it flags exactly the real NYSE early closes — 2025-07-03, 2025-11-28,
2025-12-24 — and nothing else, at a ~180× margin (0.9% vs 163% of median), so the
threshold is not doing subtle work. Sessions with fewer than 7 bars are dropped too.

## Decision rules — fixed before running

- **Path test PASSES iff** mean(kronos log-loss − bootstrap log-loss) < 0 **AND** the
  95% paired CI excludes 0. CI resamples whole **session dates**, not points.
- **Volatility test PASSES iff** Kronos's QLIKE beats **both** EWMA and GARCH(1,1),
  CIs excluding 0. Beating only the weaker baseline is a FAIL — the cheap baseline is
  the real competitor.
- The two legs are reported **separately**. A path-FAIL + vol-WIN is **not** a pass; it
  demotes to a bake-off input that still owes a win over EWMA's zero-dependency
  simplicity.
- Anything else → the nightly configuration is dead. Record the number and stop.
- The leakage window runs **only if** the path test passes; the gap between windows is
  then the memorization estimate.
- No re-running with different barriers, horizons, symbols or samplers to find a win.

## Environment (deviations from the brief, stated)

- **Native Windows, not WSL2.** WSL is unusable on this box: `networkingMode=mirrored`
  fails with `CreateInstance/CreateVm/ConfigureNetworking/0x8007054f` and falls back to
  `networkingMode=None`, leaving the VM with no DNS (survives `wsl --shutdown`).
  Fixing it means editing a `.wslconfig` shared with docker-desktop. `tzdata` is
  installed into `.venv-kronos` so `zoneinfo` resolves.
- RTX 4090, `torch 2.6.0+cu124`, Python 3.12.13, fresh `.venv-kronos`.
- **TF32 and autocast OFF** — `torch.backends.cuda.matmul.allow_tf32` is False
  (default), keeping results comparable to the 5-minute run. (`cudnn.allow_tf32` is
  True by default but is irrelevant: this model's compute is matmul, not convolution.)
- Upstream Kronos is **cloned, not vendored**, at `.venv-kronos/src/Kronos`.
- `torch` is **not** in the project `requirements.txt` and must not be added.

## Resumability

The full window is a multi-hour job, so records are written **per symbol as that
symbol completes** (`{window}_records/{SYMBOL}.json`); a restart skips symbols that
already have output. Both RNGs are seeded per symbol — numpy for the bootstrap arm and
torch for the Kronos arm — so a resumed run consumes the same stream a clean run
would. This is persistence only; it changes nothing being measured.

## Running it

```bash
# bars (project venv — needs Alpaca creds; imports neither torch nor pandas)
.venv/Scripts/python.exe -m research.kronos_probe.run fetch --window nightly_primary

# sampler calibration (before any scoring, never after)
.venv-kronos/Scripts/python.exe -m research.kronos_probe.calibrate --window nightly_primary

# the probe (kronos venv — needs torch + GPU)
.venv-kronos/Scripts/python.exe -m research.kronos_probe.run probe --window nightly_primary

# re-score saved records without re-running rollouts
.venv-kronos/Scripts/python.exe -m research.kronos_probe.run score --window nightly_primary
```

## Results

_Not yet run. Filled in after the scoring run, against the rules above and nothing else._
