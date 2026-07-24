# Kronos probe — is a pretrained K-line model's path distribution better than a bootstrap?

**Status: unproven experiment.** Nothing here is wired into the app. No product code
imports this package. If the probe fails, delete it (see *Removal* below) and nothing
else changes.

## The question

[Kronos](https://github.com/shiyu-coder/Kronos) (AAAI'26, MIT) is a pretrained
autoregressive model over OHLCV candles. Sampling it many times yields a *distribution
of future paths*, conditioned on recent price action.

Classical methods already give us a path distribution — a block bootstrap of recent
returns — but that distribution is **unconditional**: it knows the current volatility
and nothing else. The only thing Kronos can uniquely contribute is *conditional
structure*: "given this specific recent shape, the next hour is skewed."

So the probe asks exactly one thing:

> Does Kronos's 3-way outcome distribution (up-barrier / down-barrier / timeout) score
> better against realized outcomes than a volatility-matched block bootstrap on the
> **same bars**?

Beating a coin flip proves nothing. Beating the bootstrap is the whole question.

## Why the comparison is the measurement

Every bias we cannot remove — survivorship in a fixed present-day symbol list, the
particular regime of the test window, market drift — lands on **both arms equally** and
cancels in the paired difference. That is why the metric is a difference in scores and
never an absolute number. Absolute values from this harness are not publishable and
must never reach a user-facing surface.

## Leakage — the hazard that actually matters here

Kronos was pretrained on ~12B candles from 45 exchanges. US equities are in there. It
may have **memorized** the bars we test on. Neither the repo nor the model card states a
training cutoff; the paper's own test window is 2024-07 → 2025-06, implying a corpus
ending around mid-2024, and it was published 2025-08.

Two consequences, both pre-registered below:

1. The primary window is **post-cutoff only** (2025-09 onward) — clean under any reading.
2. The identical harness also runs a **pre-cutoff window (2023)**. If Kronos looks
   dramatically better on old data than on new, that gap is an estimate of memorization
   rather than skill. Contamination becomes a measured number instead of a worry.

## PRE-REGISTRATION — fixed before the first run, do not tune after seeing results

| Parameter | Value |
| --- | --- |
| Universe | SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL |
| Primary window | 2025-09-01 → 2026-07-01 (post-cutoff) |
| Leakage window | 2023-01-01 → 2023-12-31 (pre-cutoff) |
| Bars | 5-minute, Alpaca SIP, **RTH only** |
| Context | 512 bars |
| Horizon `H` | 12 bars (1 hour) |
| Barriers | ±1.0 · σ̂ · √H, symmetric |
| σ̂ | EWMA(λ=0.94) stdev of **intraday** 5-min log returns over the context — causal, uses only bars ≤ t; cross-session returns dropped |
| Sampler | T=1.0, top_p=1.0 (set by `calibrate.py` on dispersion match, before any scoring) |
| Resolution | **close-only** first touch, identical for all three arms |
| Outcome | 3-way: `up` / `down` / `timeout` |
| Paths per arm | 200 |
| Evaluation points | 10:00, 12:00, 14:00 ET each session (24 bars apart → non-overlapping at H=12) |
| Probability estimate | Laplace-smoothed path counts, `(count+1)/(n+3)`, both arms |
| Primary metric | multiclass log-loss (lower is better) |
| Secondary metric | multiclass Brier |
| Uncertainty | paired per-point differences, block-bootstrapped over session dates (95%) |

**Decision rule.** PASS iff Kronos's mean log-loss is lower than the bootstrap's *and*
the 95% paired CI on the difference excludes 0. Anything else is a FAIL and the package
gets deleted. No re-running with different barriers, horizons, or symbols to find a
win — that is how a null result gets laundered into a false positive.

Symmetric barriers are deliberate: they pin the bootstrap near P(up)≈P(down), so any
Kronos advantage is conditional information rather than a drift term either arm could
have captured.

**Instrument calibration happened first, deliberately.** `calibrate.py` compares
Kronos's path dispersion to realized σ̂ *without touching outcomes*, and set top_p=1.0
(0.97× scale match, vs 0.61× at the 0.9 default — nucleus truncation compounds over a
12-step rollout). It also caught a bug in this harness: 512 RTH 5-minute bars span ~6.6
sessions, so a naive `diff()` swept in 7 overnight gaps and inflated σ̂ by up to **2.46×**,
worst on exactly the high-volatility days that matter most. Both fixes landed before the
first scored run. Choosing sampler settings on scale match is calibrating the instrument;
choosing them on outcome scores would be tuning for a win, and is forbidden.

`close-only` resolution understates real stop behaviour (a real stop can be hit
intrabar). It is used because it applies *identically* to real bars, Kronos rollouts,
and bootstrap paths. A high/low resolver would hand Kronos more touch opportunities than
the return-based bootstrap and manufacture a win.

## Isolation

- **Separate venv** `.venv-kronos/` (gitignored). `torch` is NOT in the project
  `requirements.txt` and must not be added to it.
- Outputs go to `data/research/kronos_probe/` — already gitignored with the rest of `data/`.
- Bars land in the shared research cache (`data/research/intraday_bars.db`) via
  `research.intraday_bars`, tagged `source='alpaca'`. Those bars are useful on their own
  and survive deletion of this package.
- No product module imports this package. The dependency arrow points one way.

## Running it

```bash
# 1. one-time venv + upstream clone (upstream is NOT vendored into this repo)
uv venv --seed --python 3.12 .venv-kronos
.venv-kronos/bin/pip install torch --index-url https://download.pytorch.org/whl/cu124
.venv-kronos/bin/pip install -r research/kronos_probe/requirements.txt
git clone --depth 1 https://github.com/shiyu-coder/Kronos .venv-kronos/src/Kronos

# 2. pull bars — uses the project venv (needs the Alpaca broker config)
.venv/bin/python -m research.kronos_probe.run fetch --window primary

# 3. run the probe — uses the kronos venv (needs torch + GPU)
.venv-kronos/bin/python -m research.kronos_probe.run probe --window primary
.venv-kronos/bin/python -m research.kronos_probe.run probe --window leakage
```

`fetch` and `probe` are separate commands on purpose: only `probe` needs torch, so the
expensive data pull never depends on the experimental venv.

**Runtime: ~13.3s per evaluation point**, so the primary window (~6,300 points) is a
~23-hour job — an overnight run, not a coffee break. Batch size does not help (50/100/200
draws cost the same per path; the card is saturated at 50). The cost is upstream having no
KV cache: it re-encodes all 512 context bars for every generated token. Don't shrink the
pre-registered parameters to make it faster — the GPU is otherwise idle and nothing here
is deadline-bound.

## Removal

```bash
rm -rf research/kronos_probe .venv-kronos data/research/kronos_probe
# then drop the .venv-kronos line from .gitignore
```

The cached Alpaca bars stay. That is the whole cleanup.
