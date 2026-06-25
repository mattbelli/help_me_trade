# PC Agent Brief — Decision Engine: full survivorship-free training run (no shortcuts)

*You are a coding agent on the founder's **Windows PC**, where **Norgate Data (NDU)** runs. Your job is to
take the Decision Engine from a 447-sample, 5-symbol Alpaca **toy** to a **production, survivorship-free,
broad-feature, honestly-calibrated** model — in ONE thorough pass, so we never redo this. The serv (Linux)
side loads what you commit. Work on `main`, commit your changes + the model artifacts + a written run report,
and push so serv can pull.*

## Read first (in this order)
1. `CLAUDE.md` (mission + safety + working agreements) and `AGENTS.md` (codebase map).
2. `References/DECISION_ENGINE_RESEARCH.md` — the evidence base. **The locked rules below come from it.**
3. `References/BUILD.md` → the "★ The Decision Engine" section (vision + the train-offline/serve-online split).
4. `References/PERCEPTION_LAYER.md` — the live perception layer (the model's win%/EV is surfaced through it).
5. The pipeline modules: `research/{norgate_loader,bars,features,labeling,dataset,validation,calibration,ev,train,overfit,combined}.py`
   and the live consumer `signals/decision_scorer.py` + `research/features.py`. **Read these before changing anything.**

## Prime directives (do not violate)
1. **Calibrated, NEVER fabricated.** Every win%/EV must come from the backtested + Platt-calibrated model with
   honest confidence intervals. If the data doesn't support a number, emit `low_confidence`/`sufficient=false`,
   not a made-up figure. This is the headline honesty rule.
2. **TRAIN/SERVE PARITY is sacred.** `research/features.py::compute_features` runs BOTH offline (your Norgate
   training) AND live (`signals/decision_scorer.score_symbol` on Alpaca bars). **Every feature you add must
   compute identically in both environments.** The live scorer currently provides only: the symbol's daily
   bars + the SPY benchmark bars. If a new feature needs any other input (VIX, sector ETF, universe breadth),
   you MUST also extend the live `score_symbol` fetch path to provide it AND add a parity test — otherwise the
   live scorer will crash or silently drift. **If you cannot guarantee parity for a feature, DEFER it** (note
   it in the run report). A model that uses features the live scorer can't compute is worse than useless.
3. **Transparent model, not a black box.** Keep the logistic-regression composite (readable coefficients).
   The research doc REFUTED "ensemble beats simple" — do not switch to boosted trees / NN.
4. **No curve-fitting.** Use the purged+embargo CV that already exists; enforce the multiple-testing gates
   (DSR/PBO) — see Phase 2. If the broad model doesn't beat the simple one out-of-sample, SAY SO and ship the
   honest result, don't tune until it looks good.
5. **Reproducibility.** Everything you run must be re-runnable from committed code + a documented command list.
   Write the run report (Phase 5) so the next person never has to rediscover anything.
6. **License/data hygiene.** **NEVER commit raw Norgate bars** (`data/research/*.db` is gitignored — keep it
   that way; Norgate data is licensed and Windows-local). Commit only the KB-scale JSON **artifacts** + code +
   the report. Don't use a Claude subscription/OAuth for any app LLM (provider-agnostic keys only).

## Pre-flight
1. `git pull origin main` (get the latest — perception layer, AV earnings, etc.).
2. Set up the Python env the repo uses (`uv` / `.venv`); `python -m pytest -q` should pass before you start.
3. **Verify Norgate works:** NDU running; `python -c "import norgatedata as nd; print(nd.watchlists()[:20])"` lists
   watchlists. Read `research/norgate_loader.py` for the exact CLI (it has `--watchlist`/`--symbols` argparse) and
   confirm a `"... Current & Past"` survivorship-free watchlist exists (e.g. "Russell 3000 Current & Past", or the
   broadest survivorship-free US equity list available). Read `research/dataset.py` and `research/train.py`
   `--help` / `_parse_args` for their real CLIs — use the actual flags, don't assume.

---

## Phase 1 — Broaden the feature set (shared `research/features.py`, parity-safe)
The live model uses ONLY price/vol/technicals (22 features). Add the high-value signals below. **Priority order
= parity-safety.** Add unit tests; keep `signals/features.py` (the live indicator-facts builder) in sync if it
shares code. Use the existing `signals/indicators.py` + `research/levels.py` helpers — don't reimplement math.

**Tier 1 — clean parity (compute from the symbol's daily bars + SPY bars only; DO THESE):**
- **Support/Resistance distance** (we compute S/R for `dip_refined` but the scorer ignores it). Use
  `research/levels.py`: `dist_to_nearest_support_pct`, `dist_to_nearest_resistance_pct`, `room_to_next_resistance_pct`
  (the last is also useful later for the take-profit ladder). Swing-pivot / prior-high-low based — daily, dual-env.
- **Market regime from SPY** (the live scorer already fetches SPY): `spy_vs_sma200_pct`, `spy_trend`
  (SPY 50/200 cross state), `spy_realized_vol_pct` (e.g. 20-day realized vol, percentile-ranked over a trailing
  window) as a VIX-proxy that needs NO new data source. Regime is a known edge-stability driver (research doc).
- **More relative-strength horizons:** `rel_strength_63`, `rel_strength_126` (you already have `rel_strength_21`).

**Tier 2 — only if you ALSO wire the live path + a parity test (else DEFER and note why):**
- **VIX level/percentile** (real VIX, not the proxy) — needs a live VIX source in `score_symbol`. Norgate has
  `$VIX`; serv would need VIX live (uncertain). If you can't guarantee the live fetch, use the Tier-1 realized-vol
  proxy and DEFER real VIX.
- **Sector relative strength** — needs a committed symbol→sector map (Norgate has GICS sector) + the sector ETF's
  bars live. Only if you wire both sides.
- **Universe breadth** (% of universe > 200-DMA) — easy offline, needs a live universe-scan path on serv. Defer
  unless you wire it.

**For every feature added: a test asserting it's deterministic, look-ahead-free, and identical offline vs the
live compute path.** If Tier-2 stays deferred, that's fine — Tier-1 alone is a big, honest upgrade.

## Phase 2 — Enforce the overfitting gates (DSR / PBO)
`research/overfit.py` has Deflated-Sharpe and PBO/CSCV implemented but they are NOT enforced in `research/train.py`.
Wire them in: after training the candidate, compute DSR and PBO on the OOF/backtest returns and **refuse to export
artifacts** (or loudly stamp `low_confidence` + a rejection reason in `meta.json` + the report) if PBO > 0.5 or DSR
fails its threshold. The pipeline must be unable to silently ship an overfit model. Add a test.

## Phase 3 — The full survivorship-free run (no sampling, max history)
Run the WHOLE pipeline at full scale (read each module's CLI; these are the canonical steps, use the real flags):
1. **Bars:** `python -m research.norgate_loader --watchlist "<broadest Current & Past US-equity list>"` → builds
   `data/research/bars.db` with **TOTALRETURN-adjusted, survivorship-free daily bars**, **≥15 years (prefer the
   max available, ~20y)**. Include the benchmark (SPY) + any Tier-2 inputs you wired (sector ETFs, $VIX). This is
   the long step — let it complete; do NOT truncate the universe.
2. **Label:** `python -m research.dataset ...` → triple-barrier labels for EVERY symbol (don't cap symbols; use the
   default/!aggressive stride only if memory forces it — note any stride in the report). Target **thousands–tens
   of thousands** of labeled setups (vs today's 447).
3. **Train + calibrate + validate:** `python -m research.train ...` → logistic model → Platt calibration on
   purged+embargo OOF predictions → EV table per bin → `meta.json`. With the DSR/PBO gate from Phase 2 active.
4. **Export artifacts** to `research/artifacts/`: `model.json`, `calibrator.json`, `ev_table.json`, `meta.json`.
   `meta.json` MUST record: data_source="norgate", survivorship_free=true, universe name, date_range, n_samples,
   n_features (+ the new feature names), base_rate, brier, reliability, DSR/PBO results, and `low_confidence`
   set honestly from the *actual* sample count + per-bucket sufficiency.

## Phase 4 — Portfolio / combiner validation (as much as we can)
Using the survivorship-free data, run `research/combined.py` (+ `research/overfit.py` PBO) for the live strategies
(dip / dip_refined / earnings_drift, and any others present) **individually and combined on one account**: report
CAGR, Sharpe, max drawdown, and PBO vs SPY across multiple time windows — the honest "is the combined sleeve real
and not overfit" check. Reproduce/extend the founder's earlier combined run (BUILD.md notes dip+earnings PBO 0.18).
Put the numbers in the run report. Do NOT change live strategy logic (strategies are FROZEN) — this is measurement.

## Acceptance criteria (all must hold before you commit)
- Survivorship-free universe, ≥15y, thousands+ labeled setups (state the exact counts).
- New Tier-1 features in and **parity-tested**; any Tier-2 feature is EITHER fully wired live + tested OR deferred
  with a written reason (no half-wired features in the shipped model).
- DSR/PBO gate enforced; the shipped model PASSES it (or, if it doesn't, you ship the honest `low_confidence`
  result + rejection note rather than tuning to pass).
- Reliability/Brier reported; win% are calibrated with CIs; `low_confidence` reflects reality.
- Pre-push gate green: `ruff check .` + `python -m pytest -q` (+ `cd frontend && npm run typecheck` only if you
  touched frontend — you shouldn't need to).

## Deliverables — what to commit & push
1. **Feature/code changes** (`research/features.py`, parity helpers, `research/train.py` DSR/PBO gate, tests).
2. **The 4 production artifacts** in `research/artifacts/`. **They are currently gitignored** (`research/artifacts/*.json`,
   marked "dev/survivor-biased"). For the PRODUCTION run you MUST get them into git so serv can load them —
   `git add -f research/artifacts/model.json research/artifacts/calibrator.json research/artifacts/ev_table.json research/artifacts/meta.json`
   (force past the ignore). Update `research/artifacts/README.md` to say these are the production survivorship-free
   artifacts. (Serv will swap its local dev artifacts for these on its side — flag it in your report.)
3. **A run report** `References/DECISION_ENGINE_RUN_REPORT.md`: the exact commands run, universe + date range +
   sample counts, the full feature list (old + new), the model coefficients summary, calibration/reliability/Brier,
   DSR/PBO results, the Phase-4 portfolio numbers, every decision/caveat, and what was DEFERRED and why. This is the
   "never redo discovery" artifact.
4. **Do NOT commit** `data/research/*.db` (raw Norgate bars/labels — licensed, gitignored; keep it that way).

## Do NOT
- Take shortcuts on the universe or history to "go faster" (the whole point is breadth + survivorship-free).
- Add a feature the live scorer can't compute (parity), switch off the honesty flags, or fabricate a number.
- Curve-fit to beat the simple model; if the broad model loses OOS, report that honestly.
- Change frozen strategy logic, the chokepoint, or anything in `trading/`. This is research + artifacts only.
- Commit raw/licensed Norgate data, or use a Claude subscription for app LLM calls.

When done: push, and leave the run report so serv can wire in the new artifacts and we compare live scores
against the new model. Ask the founder (or leave a note) if Norgate access, a watchlist name, or a data field
blocks any step — don't silently skip it.
