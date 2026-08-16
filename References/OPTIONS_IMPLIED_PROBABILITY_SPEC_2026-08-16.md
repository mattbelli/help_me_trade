# Options-Implied Probability — build spec (2026-08-16)

*A new perception source: fit each name's option smile, read out the market's own probabilities
("options price a 28% chance NVDA clears $190 by Friday"), decompose the chain into three dials
(level / tilt / curvature), baseline the dials against their own history, and surface abnormal
repricings as perception fires. Lineage: Bloch, "A Practical Guide to Quantitative Volatility
Trading" (SSRN 2715517, https://ssrn.com/abstract=2715517 — local copy `References/ssrn-2715517.pdf` is gitignored), assessed 2026-08-16 — the
simplified-mixture fitter (his App. B.3.2), the OTM de-Americanization rule (§5.2), and the
smile-fit-before-differentiating requirement (§1.5.2.2: skipping it mispriced a digital 45%→55%).*

## 0. Mission fit + honesty posture

Connect-the-dots perception, not a strategy and not a verdict. The options market is the one
place where someone else continuously publishes a probability for the events our ideas are about;
this source extracts it and puts it next to everything else. Three uses, in value order:
(1) **benchmark** — any idea's calibrated estimate can sit beside the market's priced odds, and
the gap is the content; (2) **repricing fires** — an abnormal move in the dials vs their own
baseline is a "someone is paying up for this outcome" tell, same species as the abnormality
scanner; (3) **strike/EV arithmetic** for the options-strategies ambition, later.

Presentation: **the number alone is the honest artifact** — it's the market's baked-in
probability, a market observable like a price, and it needs no label machinery (founder call,
2026-08-16). What keeps this honest is engineering, not wording (§5): fail-closed fits, no
extrapolation, and the market's odds never written into win% fields — not because they're
suspect, but because they're a *different quantity* than our calibrated estimate, and the whole
point is comparing the two. Level of any dial = structure, not signal; only *changes vs own
baseline* fire. Directional predictiveness of tilt changes is academically plausible but
unvalidated here → fires are annotate-only in fusion (points=0) until a prereg measures it.

## 1. Decisions

**Locked by prior work:**
- OTM-only inputs (calls above forward, puts below) — the §5.2 American-options rule; recorded in
  memory `bloch-vol-guide-assessed`. ITM quotes are never inputs.
- IV-rank is NOT an entry gate (settled: `IV_RANK_BASELINE_PREREG_2026-07-25.md`, level effect).
  Nothing here re-litigates that; dials are perception, not gates.
- Watchlists are runtime filters, never design-time constants — universe reads
  `flow_watch_symbols_fresh()` at run time.

**Defaults I'm making (overridable, not blocking):**
- v1 fitter = per-expiry 2-component shifted-lognormal mixture (§2a), full MixVol surface = v2-if-ever.
- Universe: this feature has **no list of its own**. Daily lane = whatever tonight's
  `chain_snapshots` contain (the sampler's append-only universe). Intraday lane = resolved at
  runtime by the same call the flow collector uses (`flow_watch_symbols_fresh()` — the dedicated
  "flow" list is gone, so this is effectively the union of all watchlists — merged with the
  fusion hot lane). SPY always fitted as the market-relative anchor. SPX/SPXW deferred (§9).
- Fires are inbox + surfaces only, no push (abnormality precedent).

**Founder lane (genuinely yours):** greenlight Inc 0 (offline validation) and, after reading its
findings doc, greenlight Inc 1–6. Nothing below builds until then.

## 2. What it computes

### 2a. The fitter — per (symbol, expiry) smile
Inputs, per expiry with DTE ∈ [2, 60]: OTM mids from the chain snapshot (`bid > 0`,
spread/mid ≤ 25% liquid names / ≤ 40% thin, ≥ 6 usable strikes, spot inside quoted strike range).
Forward `F` per expiry: strike of min |C−P|, then `F = K + (C−P)/DF` (reuse `_discount_factor`
in `brokers/options_chain.py`). IVs via `signals/black76.implied_vol` where vendor IV is absent.

Model (Bloch App. B.3.2 restricted to one expiry): undiscounted normalized call price
`C(K) = a·BS(F(1+μ₁), K, σ₁) + (1−a)·BS(F(1+μ₂), K, σ₂)` with `μ₂ = −a·μ₁/(1−a)`
(martingale constraint auto-enforced), bounds `a ∈ [0.05, 0.95]`, `μ₁ ∈ [−0.5, 0.5]`,
`σᵢ ∈ [0.01, 3.0]`. Four free parameters. Fit: `scipy.optimize.least_squares` with bounds,
residuals spread-weighted, initial guess from ATM IV. **The mixture is a valid probability
density by construction** — negative-density bugs are impossible, which is why it beats
polynomial/SVI slices for probability extraction (Bloch ch. 6.1.4: those admit arbitrage).

Quality gate (all must pass or the slice yields nothing): converged; RMSE ≤ 50bp vol
(≤ 100bp thin names); fitted ATM IV within 20% of the strike-interpolated `atm_iv()` reading.
Store params + diagnostics either way (`fit_ok` flag) — failures are data too.

### 2b. Probabilities (digitals)
Analytic from the fitted mixture: `P(S_T > K) = a·N(d₂(F(1+μ₁),K,σ₁)) + (1−a)·N(d₂(...))`.
Stored per (symbol, expiry): prob above/below spot ±2% and ±5%, the implied move (central 68%
band width), and prob-above for the two round strikes bracketing spot. Per-expiry only —
no cross-expiry probability interpolation (would be a fabricated number).

### 2c. The dials
Per (symbol, snapshot), from fitted slices, at constant-maturity tenors 7d and 30d
(total-variance interpolation, reuse the `constant_maturity_iv` convention — bracketing
required, never extrapolated):
- **Level** = fitted ATM IV (complements the existing `iv_daily.atm_iv_30d`).
- **Tilt** = RR25: fitted IV at 25Δ call − fitted IV at 25Δ put (deltas from fitted slice).
- **Curvature** = FLY25: (IV25Δc + IV25Δp)/2 − ATM.
Each dial gets: raw value, z vs own 60-session baseline, and market-relative z
(dial − SPY's same dial, z-scored) — the VIX-spike separator. Earnings flag: expiry window
contains a confirmed/estimated earnings date (`db/earnings_calendar.next_earnings`) → tagged,
and fires are suppressed for that name (v1 answer to event baselines).

## 3. Data + baselines (nightly lane)

No new capture. The post-close sampler (`tools/options_chain_sampler.py`, cron 20:50 UTC)
already writes per-contract `chain_snapshots` (bid/ask/spot/IV/greeks/OI, banded ±25%
moneyness, DTE 7–75) to `data/options_chain_history.db`. Nightly step (new tool
`tools/implied_prob_nightly.py`, chained after the sampler with the `cmd || curl ntfy`
dead-man pattern; ⚠️ exec bit + `chmod +x` git trap; `if __name__ == "__main__"` deny-by-default):
1. Fit all (symbol, expiry) slices for the session from `chain_snapshots`.
2. Compute digitals + dials; append to `data/implied_prob.db`.
3. Refresh 60-session rolling baselines (mean/std per symbol/tenor/dial).
4. Grade matured digitals (§6).
One-time backfill script runs the same fit over every session already on disk (sampler corpus +
usable parts of the frozen DoltHub `options_iv_history.db`; ⚠️ QQQ/IWM/GLD/SLV/TLT absent there)
so baselines are warm on day one.

Storage: own db `data/implied_prob.db`, module `db/implied_prob.py` owning DDL
(provenance doctrine per `db/gamma_exposure.py` docstring: model output, never mixed into
vendor-series stores; ⚠️ resolve `db_path` at call time, not import — the `oi_walls` trap).
Tables: `smile_fits`, `dials`, `digitals`, `digital_outcomes`, `repricing_fires`.

## 4. Intraday lane (fires)

Registered job (`api/jobs.register_job`, `market_hours_only=True`, `interval_s=900`) — REST
only, no socket, so it lives in the backend, not the collectors. Each pass, for the
runtime-resolved active set (§1) + SPY: `get_option_chain_snapshot(sym, DTE ≤ 35, strike band
±15%, max_pages ≤ 8)`, refit front slices, recompute 7d dials. Quotes are RTH-only
(freeze 16:00 ET) — the job's `enabled` respects that; last pass of the day is the 15:45 mark.

Cadence: 900s is a **default with a rationale, not a measured number** — fetch cost is trivial
next to the flow poll (30s cadence on the same API), and the tightest downstream consumer, the
fusion trigger lane, decays on a 60-min half-life, so 15 min ≈ 4 samples per half-life. The
right cadence follows from how fast the dials actually decorrelate intraday, which nobody has
measured — Inc 0 measures it (tcbbo-reconstructed intraday smiles for a few liquid names) and
the interval gets set from that. If the active set grows past what one interval can fetch, the
pass rotates through it rather than capping it.

**Fire rule** `options_repricing`: |intraday z-move| ≥ 2.5 on tilt or level (7d tenor) vs the
60-session baseline AND market-relative z ≥ 1.5 (kills VIX-spike false fires) AND fit quality
passed AND no earnings flag. Debounce: one fire per (symbol, dial) per session unless sign flips.
Delivery: `signals/notify.notify(kind="options_repricing", push=False)` (add to
`INBOX_ONLY_KINDS`), row in `repricing_fires`, and an annotate-only fusion dot — copy
`_mint_abn_dot` (`signals/fusion/intake.py:1011`) verbatim: `actor_group="background_crowd"`,
`direction="none"`, `points=0.0`, detail carries the signed tilt + the measurements.

## 5. Honest by construction (engineering, not labels)

No label/caveat strings, no stamped disclaimers — an implied probability is a market observable
like a price, and the number alone is the honest artifact (founder call, 2026-08-16). What
actually keeps it honest is mechanical:

- Fail-closed: quality-gated fits (§2a); a failing slice yields nothing; `fit_ok=0` rows are
  visible to no consumer. No extrapolation past quoted strikes or expiries, ever.
- Provenance (fit RMSE, n_strikes, quote age) stored on every row, readable in drawers/detail
  for anyone who digs — never headline text.
- The market's odds never write into `win_pct`/calibration-pack fields — a different quantity
  than our calibrated estimate; the product surface puts the two side by side.
- Fusion dots stay annotate-only (points=0) until tilt-change predictiveness is measured under
  its own prereg.

## 6. Outcome tracking (own table)

`digital_outcomes`: every stored **daily** digital graded at expiry (settle close vs level) —
implied p vs realized hit, binned monthly into a calibration table readable via the resource's
`summarize`. Purpose: **pipeline bug detector, not edge measurement** — implied 30% events
should realize near 30% (modulo the known risk-premium tilt, which the caveat names). Drift
beyond the tilt = the fitter is broken. Grader runs inside the nightly tool.

## 7. Surfaces — definition of done (wire-it-everywhere)

1. **Perception resource** `options_implied` in `ai/perception/resources.py` (`RESOURCES` entry +
   reader + `summarize`; aliases in `_RESOURCE_ALIASES`: `implied_probability`, `digitals`,
   `smile`, `risk_reversal`, `market_odds`). Description TIGHT — `list_capabilities()` is already
   ~22.7k vs its 20k cap (HANDOFF:67); no new `_SYMBOL_FIELD_GROUPS` entry until that's fixed.
2. **Chat agent**: routing bullet in `ai/chat/agent.py` (~:95-140) — the `oi_walls` precedent;
   `query` description picks the name up automatically; guard test
   `tests/test_perception_resources.py::test_every_resource_is_named_to_the_agent` must pass.
3. **Books**: reachable with zero edits (in-process via `query`; headless via the generic
   `perception_query.py` allowlist line) — verify, don't assume.
4. **Research engine**: `fetch_implied_source(symbol)` in `ai/research/data_sources.py`
   (the three-surfaces-wired-separately trap is real and recorded).
5. **Fusion**: the annotate-only dot lane (§4).
6. **UI** (no new widget — `frontend/CLAUDE.md` §3): a block in `scans-widget.tsx` for today's
   fires (abnormality precedent) + a probability ladder section in `company-info-sections.tsx`
   for the focused symbol. Tickers click-to-focus. Verify: `npm run typecheck`,
   `check:tokens`, `node tools/ui-shot.mjs`, founder in-browser.
7. **Docs**: this spec in `References/INDEX.md` (same-commit hook), a §4 roadmap line in
   `BUILD.md`, board card, BUILD_LOG row per landed increment.

## 8. Build increments (each committable, verify step each)

- **Inc 0 — offline validation, no product code.** Script under `research/` fits the existing
  corpus (AAPL, NVDA, SPY + two thin names, all sessions on disk). Findings doc:
  RMSE distribution, % slices passing the gate, fitted-vs-naive digital comparison (the 45→55
  demo on our own data), implied-vs-realized calibration on matured expiries, and intraday dial
  decorrelation from tcbbo-reconstructed smiles (sets the §4 cadence from measurement).
  **Founder reads this before Inc 1 exists.** Verify: the doc + plots.
- **Inc 1 — spine.** `db/implied_prob.py` + fitter module (`signals/implied_prob.py`) +
  nightly tool + backfill. Verify: targeted pytest module (⚠️ name it in the brief — full suite
  hangs); row counts; one AAPL smile eyeballed against vendor IVs.
- **Inc 2 — dials + baselines + earnings flag.** Verify: z-history replay over a known event
  day shows the fire that should have fired and silence elsewhere.
- **Inc 3 — perception + chat + research wiring (§7.1–4).** Verify:
  `uv run python tools/perception_query.py query options_implied --filter symbol=AAPL`
  + guard test green.
- **Inc 4 — intraday lane + fires + fusion dots.** Verify: one live RTH session observed;
  debounce unit test; inbox row renders.
- **Inc 5 — UI blocks.** Verify: §7.6 loop.
- **Inc 6 — grader + calibration summary.** Verify: graded rows exist after first expiry passes;
  calibration table readable via `summarize`.

Safety notes for every brief: no `trading/` surface is touched anywhere in this build; nothing
here proposes or sizes trades; commit staging by explicit path (parallel-agent hygiene).

## 9. Out of scope / future

- SPX/SPXW: chain quirks are mapped (AM/PM roots, no vendor greeks, parity spot — see
  `signals/gamma_exposure.py`) but deferred until the equity lane is proven.
- Full MixVol term-structure surface (calendar-consistent scenarios) — only if a consumer
  needs cross-expiry consistency.
- Any directional use of tilt changes → requires its own prereg + measurement first.
- Push alerts for fires; win%/EV integration into idea planning (needs the calibration
  benchmark loop live first); variance-swap/dispersion anything.
