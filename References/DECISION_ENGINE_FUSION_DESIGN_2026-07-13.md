# Decision Engine — v1 Fusion/Scoring Layer Design (2026-07-13)

*The design that connects the built substrate (scorer, journal, calibration ledger, S/R levels,
perception layer) into the REASON layer: a transparent weighted-composite scorecard → calibrated
win%/EV → deterministic trade plan → `propose_trade`. Implementation rounds brief from this doc.*

**Evidence base:** `References/DECISION_ENGINE_RESEARCH.md` (3 adversarially-verified rounds).
**Honesty contract:** `References/DECISION_ENGINE_HONESTY_REDTEAM_2026-07-08.md` — founder chose
**SUPPRESS** (no win%/EV renders until validated; applies to both scorers). This design is built
to that contract.
**Locked architecture (BUILD.md ★):** LLM proposes thesis/weighting-flags; **deterministic code
computes every number** — score, prices, probability, sizing, targets.

---

## 0. What exists vs what this adds (extend, don't duplicate)

| Substrate | File | What v1 does with it |
|---|---|---|
| Generic calibrated scorer | `signals/decision_scorer.py` | **Kept as the live scoring contract.** `score_features()` + the 4-artifact format (`model.json`/`calibrator.json`/`ev_table.json`/`meta.json`) already implement linear-score → Platt → EV-bucket → honesty flags. The v1 scorecard **is a linear model in this exact contract** — hand-set/IC weights instead of max-likelihood coefficients, plus new features. No parallel scorer. |
| Feature builder | `research/features.py` + `research/dataset_io.py` | Extended with the new factor columns (§2). Same PIT discipline (`bars[:idx+1]` only), same offline/online shared-code guarantee. |
| Offline pipeline | `research/dataset.py` (triple-barrier), `research/validation.py` (PurgedKFold+embargo), `research/train.py` (Platt + honesty), `research/ev.py` (Wilson-CI buckets), `research/overfit.py` (DSR/PBO) | **Reused unchanged** as the factor-admission gate. This doc does not redesign the backtest harness; it names what must pass through it. |
| Intake seam | `trading/proposal_intake.py` `propose_trade(TradeCandidate)` | The fusion layer is a **producer into** this seam. Untouched except: `TradeCandidate.notional_usd` gets computed by the sizing rules in §6 (still a `Decimal`, still capped by the existing buying-power/RiskEngine path). |
| 3-stage journal | `db/decision_journal.py` (`score_json`, `plan_json` at CONTEXT stage) | v1 writes the **full factor vector + plan** into the existing columns at propose time — the non-fabrication guarantee (context recorded before outcome) is inherited, not rebuilt. |
| Idea ledger | `db/idea_outcomes.py` + `research/idea_tracker.py` + `References/IDEA_LEDGER_UNIFICATION_SPEC_2026-07-11.md` | Every scored idea registers with `origin="decision_engine"` (unification Phase B). This ledger — all ideas, taken or not — is the **online calibration/IC training set** (memory rule: never win% from taken-trades-only). |
| S/R levels | `signals/session_node_strategy.compute_session_levels` (POC/VAH/VAL/PDH/PDL) + `research/levels.py` (daily swing pivots) | Feed the stop/ladder computation (§6) and the `structure_room` factor (§2.F10). |
| Per-strategy validated stats | `signals/strategy_stats.py` (tiers: validated / strong-unverified / exploratory / regime-local) | **Unchanged and senior.** When a proposal comes from a validated strategy sleeve, the sleeve's backtested stats own the displayed number (`trading/proposals.py:636-654` order preserved). The scorecard is the conviction source for *general ranked ideas* and the ranker across sleeves — it never overrides a validated sleeve stat. |

**Relationship to in-flight work:**
- The **honesty red-team remediation** (compactor flags, tier gate, freshness gate, `rule_id` rewire
  LAST) is a **prerequisite for Phase v2** of this design (§8). v1 shadow mode is safe before it.
- The **strategy-validation queue** (`residual_momentum`, `turn_of_month`, `pead_confirmed`) is
  orthogonal: those are *sleeves* validated through the same PC gate. If validated, they publish to
  `strategy_stats.json` as today; their entry signals may *also* be admitted later as scorecard
  factors, but nothing here blocks or depends on them.

---

## 1. Shape of the whole pipeline (one paragraph)

Candidate symbols (watchlist + S&P-500 store + strategy-signal fires + flow events) → per-symbol
**point-in-time feature vector** (extends `research/features.py`) → **transparent weighted composite
score** with regime/earnings **gates** (§3) → **Platt calibration + EV buckets** from committed
offline artifacts (§5) → **deterministic plan**: structure/ATR stop, S/R scale-out ladder,
fractional-Kelly-capped `Decimal` sizing (§6) → **LLM writes the plain-language "why"** from the
deterministic outputs, never a number (§4) → one `TradeCandidate` into `propose_trade` +
**every scored idea registered in the idea ledger** whether proposed or not (§7). Offline (founder's
PC, Norgate survivorship-free): factor admission, weights, calibrator, EV tables → committed
KB-scale artifacts. Online (serv): load artifacts, score live Alpaca/Schwab data. Shared feature
code prevents train/serve drift (BUILD.md "train-offline / serve-online", locked 2026-06-21).

---

## 2. v1 factor set — twelve falsifiable hypotheses

Rules of admission (research Round 3, headline finding — **every published edge was refuted 0-3**):
- Every factor is a **hypothesis, never a pre-trusted edge**. A factor's weight is 0 until it passes
  the PC/Norgate gate: triple-barrier labels → PurgedKFold+embargo OOF → per-factor IC significance
  → DSR (`n_trials` = actual grid cells) → PBO < 0.5 → net-of-cost EV CI excludes 0 for the composite.
- Only data the app **already computes** qualifies for v1 (no new ingestion projects hiding inside
  the scorecard).
- **Two admission lanes**, because two factors cannot be backtested on Norgate daily bars:
  - **Lane A (swing, Norgate-backtestable):** F1–F8, F10-daily. Admitted via the offline gate.
  - **Lane B (intraday/live-only):** F9 RVOL, F11 flow, F10-session-nodes. No historical intraday
    data exists → these can **never** enter the weighted composite via backtest. They ride as
    **annotate-only** signals whose predictive value is measured on the live idea ledger
    (`idea_outcomes` + `options_flow_events.ret_*` stamps, both already accruing). Admission gate:
    n ≥ 500 resolved ledger samples per factor AND a significant rank-IC on those samples —
    reviewed by the founder, weights set offline like everything else. Until then they appear in
    the rationale ("RVOL 3.2× — noted, unweighted") but contribute 0 to the score.

Normalization notes: all normalizations are **point-in-time-safe** (trailing windows only, stats
fit on training folds offline — `research/dataset_io.fit_preprocessing` already enforces this).
Default is **time-series standardization per symbol** (trailing 252 trading days) because the live
app scores symbols one-at-a-time; cross-sectional ranks are used only where the hypothesis is
inherently cross-sectional (F4, F5) and are computed against the S&P-500 daily-bar store
(`momentum_daily_bars`, 503 names, zero broker calls). Every normalized value is clipped to
[−3, +3] before weighting so no single input can dominate.

| # | Factor | Horizon | Hypothesis (falsifiable) | Feature + normalization (reasoned call) | Lane |
|---|---|---|---|---|---|
| F1 | `flush_reversion` | swing | A sharp 3-day flush in an otherwise healthy name mean-reverts over the next 5–20 td (the founder's own validated dip mechanism, generalized to a continuous factor). Falsified if IC ≤ 0 on OOF triple-barrier returns. | `prior_return_3d / (atr_14/close)` — volatility-scaled 3d return (raw % is regime-blind), then trailing-252d z, sign −1 (more negative flush → higher score). | A |
| F2 | `trend_context` | swing | Long setups above a long-term uptrend resolve better than counter-trend ones (the R1e retrospective showed the founder's untested habit is the *opposite* — buying below the 200-day — so this is genuinely informative either way). | `dist_from_sma200_pct` trailing-252d z, sign +1, **plus** the existing `vs_sma200_below` one-hot (already in `dataset_io.py`). | A |
| F3 | `oversold_rsi` | swing | RSI14 < ~30 within an uptrend marks exhaustion, adding bounce odds beyond F1 (tests whether RSI is redundant with the flush — if collinear, it dies at admission). | RSI is already bounded 0–100: linear map `(50 − rsi_14)/20`, clipped, sign +1. No z needed. | A |
| F4 | `rel_strength` | swing | 21-day relative strength vs SPY predicts continuation; for dip-family entries it may invert (interaction to be measured, not assumed). | `rel_strength_21` cross-sectional percentile vs the S&P-500 store that day, mapped to [−1.5, +1.5]; sign resolved by backtest (pre-registered both-ways test, one d.o.f. spent). | A |
| F5 | `high_252_proximity` | swing | Nearness to the 52-week high carries drift (George–Hwang anchor under-reaction) — the classic claim, treated as unproven. | `dist_from_high_252` cross-sectional percentile, sign +1 (closer to high = higher). | A |
| F6 | `volume_surprise` | swing | Setup-day volume ≥ ~2× the 20-day average signals institutional participation and improves follow-through vs quiet tape. | `log2(vol_vs_avg_20)` clipped to [−2, +2], sign +1. Log because volume ratios are heavy-tailed. | A |
| F7 | `gap_context` | swing | A large adverse overnight gap into the setup (gap-down) worsens next-day fills and mean-reversion odds (news-driven flushes behave differently from tape-driven ones). | `gap_prior / (atr_14/close)` trailing-252d z, sign resolved by backtest. Weakest prior in the set — expected to die at admission; kept because it's free and tests a real confound of F1. | A |
| F8 | `market_tailwind` | swing | Broad-market condition shifts the base rate of *all* long setups (dip_buy's own backtest: the market-down filter ~doubled EV). | Two sub-features already computed: SPY `vs_sma200` (cached SPY bars) and `vix_percentile` (`ai/perception/resources._read_market`, 252d). Enters BOTH as a weighted factor (z of −vix_percentile) AND as the hard gate in §3 — the backtest decides if the additive part survives once the gate exists. | A |
| F9 | `rvol_tod` | **intraday** | RVOL vs time-of-day expectation ≥ 2 at scoring time marks unusual participation and improves 1–5d follow-through. | `log2(rvol.value)` clipped [0, +2]. **Lane B**: no historical intraday profile data — live-ledger admission only. | B |
| F10 | `structure_room` | both | Entries with ≥ 2R of unobstructed room to the nearest overhead resistance have higher EV than entries directly under a lid (also feeds the ladder, §6). | `(nearest_resistance − entry) / atr_14`, clipped [0, +3], sign +1. Daily-level component (swing pivots via `research/levels.py`) is Lane A — backtestable on Norgate; session-node component (VAH/PDH) is Lane B. Ship the daily component first. | A/B |
| F11 | `flow_event` | **intraday** | A high-confluence options-flow event (existing `signals/confluence_score` — "honest unusualness, never a win%") within the session precedes short-horizon drift. | Confluence score of the most recent same-day event, 0 if none, clipped [0, +2]. **Lane B**: validated only against the live `options_flow_events.ret_15m/1h/eod/1d/5d` stamps already being collected. | B |
| F12 | `earnings_proximity` | both | An earnings print inside the trade's expected holding window is a binary-event risk that dominates any technical edge. | **Gate, not a weighted factor** (§3): `days_to_next_earnings` from the existing earnings-calendar table. Historical earnings dates exist in `research/earnings.py`, so the gate's *effect* is backtestable (does excluding pre-print entries improve EV?). | A |

**Parsimony:** 10 weighted candidates + 2 gates is the ceiling, not the target. The admission gate
will kill some (expected: F7, possibly F3 as collinear with F1). Research Round 3 open question —
"how few factors given multiple-testing power" — is answered empirically: every factor tested is an
`n_trials` increment in the DSR, so the grid stays small and pre-registered. **Do not add factors
mid-round.**

---

## 3. The composite scorecard — the math

Transparent weighted composite (research verdict: scorecard FIRST; "IC-weighted ML ensemble beats
simple" was **refuted 0-3** — no gradient-boosted anything in v1..v3).

**Score.** For symbol *s* at time *t*, with normalized clipped features `x_i` (§2), fixed
hypothesis signs `d_i ∈ {+1, −1}`, and weights `w_i ≥ 0, Σw_i = 1` over the *admitted* factor set:

```
raw = Σ_i  w_i · d_i · x_i                          # in roughly [−3, +3]
score = G_regime · G_earnings · raw                  # gates, below
```

**Missing data — renormalize, never impute silently.** If factor *i* is unavailable for this
symbol (e.g., no 252d history for the z-window, no profile for RVOL), drop it and renormalize the
remaining weights. Record `coverage = Σ_available w_i` in the score payload; if
`coverage < 0.6`, **do not score** (return `None`, exactly like today's scorer when
`len(bars) < MIN_BARS_FOR_CONTEXT`). This deliberately diverges from the logistic scorer's
median-impute: the scorecard's whole point is that the founder can read why a score is what it is,
and "we pretended RSI was the median" breaks that. (The offline pipeline still uses
`fit_preprocessing`'s fold-safe medians for *training* — the coverage rule is a live-scoring
honesty rule.)

**Gates (regime + earnings) — multiplicative, not additive.** Research open question #4 (does
regime gate or add?) — design call: **gate**, because a risk-off tape changes the base rate of all
long setups at once, and an additive term lets nine bullish factors outvote the one that says "the
market is falling apart" (the exact failure the dip_buy market-down filter proved matters).
- `G_regime ∈ {0, 1}`: 0 when `regime_label` (existing `_read_market` computation: term spread +
  VIX) is risk-off **and** SPY < SMA200; 1 otherwise. Whether risk-off means *block* (G=0) or
  *half-size* (G=1 with sizing multiplier 0.5) is a **FOUNDER DECISION** (FD-3, §9) — default in
  this doc: block new entries, matching the residual_momentum spec's `SPY<SMA200 → no new entries`.
- `G_earnings ∈ {0, 1}`: 0 when `next_earnings_date − today < expected_holding_days` (from the
  factor's horizon; triple-barrier vertical barrier offline). Earnings-drift-family sleeves are
  exempt (their edge *is* the print) — the gate applies to general scorecard ideas only.
- Gates fire visibly: a gated candidate is still **registered in the idea ledger** with
  `gated_by` recorded, so the LEARN loop can measure whether the gates actually help (a gate is a
  hypothesis too).

**Initial weights: equal.** `w_i = 1/k` over the admitted set. Research explicitly left
equal-vs-prior open; equal weights are the defensible zero-information start, they make the first
backtest round interpretable (any lift is the factors, not the weighting), and 1/N is famously hard
to beat at small samples. A founder-prior override (e.g., double weight on F1 because dip-buying is
his validated edge) is **FD-2** — supported by config, not silently chosen.

**Rolling-IC upgrade path (v3).** Grinold & Kahn: per-factor
`IC_i(t)` = Spearman rank correlation of `d_i·x_i` vs realized triple-barrier label returns over a
rolling window; `ICRatio_i = mean(IC_i) / std(IC_i)`; candidate weights
`w_i ∝ max(ICRatio_i, 0)` (floor at 0 — a factor that goes negative gets benched, not shorted),
renormalized. Two IC estimates, used differently:
- **Offline** (authoritative): walk-forward monthly IC on the Norgate dataset → produces the
  candidate weight vector as an artifact.
- **Online** (monitoring only): rolling IC on resolved idea-ledger rows → rendered in a report so
  drift is visible; **never mutates live weights**.

**When IC-weighting earns its keep (the criterion):** switch from equal to IC weights only when
ALL of: (a) ≥ 500 resolved decision-engine ledger ideas (the live-relevance check), (b) the
IC-weighted composite beats the equal-weighted composite on the held-out OOS tail of the offline
backtest on **both** Brier score and net EV, (c) PBO of the weight-selection grid < 0.5. Fails any
→ stay equal-weighted. This is the "add rolling-IC later **if it earns its keep**" verdict made
mechanical.

**Artifact contract — unchanged.** The scorecard ships in the existing 4-file format:
`model.json` carries `feature_names`, `coefficients` (= `w_i·d_i` over standardized features),
`intercept` (= 0), `preprocessing` (the fold-safe z stats); `calibrator.json`/`ev_table.json`/
`meta.json` exactly as produced by `research/train.py` + `research/ev.py` today.
`signals/decision_scorer.score_features()` runs it **without modification** — the honesty flags
(`low_confidence`, `sufficient`, `n_samples`), Wilson-CI buckets, and `top_factors` attribution
all come for free. `meta.json` gains `scorecard_version` and `factor_set` fields so surfaces can
say *which* scorecard produced a number. The one live-code change: `score_features` grows the
coverage rule above (a ~10-line guard).

---

## 4. The LLM layer — narrative on top, numbers never

Evidence (research §4, high confidence): LLMs are weak at financial numerics, run-to-run volatile,
leakage-prone; LLM = thesis/text, deterministic code = every number. The chat brain and report
writer already follow this; the fusion layer formalizes the interface.

**What the LLM does (all three, and nothing else):**
1. **Reads unstructured text**: the candidate's `news` headlines (already in `get_context`),
   `social_buzz` output when present, prior journal theses for the symbol.
2. **Writes the plain-language "why"**: a thesis composed FROM the deterministic packet — the
   fired factors (`top_factors` names + directions), gates, plan levels, and headline context.
3. **Proposes factor-relevance flags for edge cases**: a fixed enum vocabulary (below). Flags are
   the LLM's one structured output — qualitative observations code can't make ("this headline says
   the CEO resigned an hour ago; the flush factor is reading stale news").

**Interface (deterministic scorer → LLM → proposal):**

```
INPUT  (built by code):  { symbol, side, score_packet: {score, top_factors[], gates_fired[],
                           coverage}, plan: {entry, stop, ladder[]}, headlines[], buzz_summary?,
                           prior_theses[] }
OUTPUT (LLM, JSON):      { thesis: str,                       # ≤ 480 chars, plain language
                           cited_factors: [str],              # MUST ⊆ input top_factors names
                           flags: [enum],                     # ⊆ {news_contradicts, binary_event_risk,
                                                              #    stale_story, crowded_hype, data_suspect}
                           abstain: bool }                    # "I can't write an honest thesis"
```

**Forbidden — enforced in code, not by prompt:**
- The LLM output contains **no numeric field**. The validator strips/rejects any thesis containing
  a `%` token, the words "win", "probability", "EV", "expected value", or a price not present
  verbatim in the input packet (prices may be *quoted* from the plan, never invented). A rejected
  thesis falls back to the deterministic rationale string (factor list + gates), and the pipeline
  proceeds — **LLM unavailability or misbehavior never blocks or alters a proposal.**
- `cited_factors` not a subset of the packet's factors → dropped. Flags outside the enum → dropped.
- The LLM output **cannot change the score, the plan, the size, or the ranking.** In v1/v2, flags
  are **annotate-only**: recorded in `score_json` and shown as text on analytical surfaces. Each
  flag is itself a hypothesis — e.g., "do `news_contradicts`-flagged ideas underperform their
  score bucket?" is answerable from the ledger after enough samples. Only after such an analysis
  (and a founder decision, FD-6) may a flag be promoted to a deterministic gate with a
  pre-registered effect — at which point it is code, not LLM output, that applies it.

**Leakage note for any future flag-backtest:** never backtest LLM sentiment/flags over text inside
the model's training window (Glasserman & Lin) — flag analyses run on the *live* ledger only,
which is post-cutoff by construction.

---

## 5. Calibration + honesty gates — score → win%/EV, or silence

**Machinery (all existing, reused):**
- Offline: composite `raw` scored over the Norgate dataset → OOF predictions from
  `research/validation.purged_kfold` (+embargo) → Platt fit on OOF only (`research/train.py`) →
  `ev_table.json` per score-decile bucket with Wilson CIs and `sufficient = n ≥ min_bucket`
  (`research/ev.py`) → `meta.json` honesty block (`low_confidence = n_samples < 1000`, Brier vs
  base rate).
- Platt over isotonic is settled for our sample sizes (research: Platt wins under ~1000 labels);
  revisit only when the labeled set is 5k+.
- Out-of-range score → **no bucket → EV fields null** (`_find_ev_bucket` already refuses to
  inherit a neighbor's numbers).

**The training set is the calibration ledger, not taken trades.** Two labeled sources, kept honest:
1. **Offline (authoritative for the published number):** Norgate triple-barrier samples — the
   thousands-of-instances requirement (BUILD.md: ~1,000–10,000; the founder's ~260 round-trips are
   far too few).
2. **Online (drift monitor + Lane-B admission):** `idea_outcomes` rows with
   `origin="decision_engine"` — every scored idea, proposed or not, approved or not (memory rule:
   calibration counts everything). The mechanical sweep (`research/idea_tracker.py`) already
   resolves these with conservative same-bar rules. Requirement on the tracker: red-team finding
   #4 (creation-day bar look-ahead) **must be resolved before online stats are trusted** — still an
   open **FOUNDER DECISION** (FD-7: is same-day entry a legitimate trigger?).

**Display policy — SUPPRESS (the founder's decision, applied verbatim).** A win%/EV renders on ANY
user surface only when **all** hold:

```
validated                    # meta.json honesty: NOT low_confidence (n ≥ 1000) AND Brier
                             # meaningfully beats base rate on OOS (the "edgeless" test)
AND bucket.sufficient        # this score's EV bucket cleared min_bucket
AND fresh                    # artifact as_of ≤ MAX_AGE (FD-5, default 120 days) — the red-team
                             # freshness gate, applied to scorecard artifacts too
AND config-match             # live factor_set/universe == the backtested config (param-drift guard)
```

Anything less → the number is **absent**, not badged: phone card and glanceable widgets show the
trade + "unvalidated setup"; analytical surfaces (DecisionCard, chat) show the factor breakdown
and plan with an explicit "no validated edge — provisional score, journal-only" line, following the
one already-honest surface (`proposal-score.tsx`) as the template. The honesty flags ride the
payload end-to-end (the red-team Fix Groups A/B are assumed shipped before Phase v2 — §8).
The `ci_low/ci_high` Wilson band is the *bucket's empirical-win* interval and is only ever
captioned as such (red-team #7).

**Ranking is not probability.** The engine may *rank* candidates by score internally in all phases
(ranking is how it picks what to propose). But a rank presented as "conviction 8/10" implies edge,
so the 0-10 conviction label surfaces **only when the win% behind it is renderable** under the
gate above. Before that, surfaces may show which factors fired — facts, not forecasts.

---

## 6. The plan — entry/stop/ladder/size, deterministic from real levels

All plan math runs in deterministic code. Feature/score math stays float in `signals/`+`research/`
(existing seam rule); everything money-shaped crosses to `Decimal` at the `propose_trade` boundary
(`TradeCandidate.notional_usd`, limit prices), matching the float→Decimal seam.

**Level inventory (per symbol, computed at plan time):**
`levels = session_nodes(POC, VAH, VAL, PDH, PDL)` (from `compute_session_levels`) `∪
daily swing pivots` (`research/levels.py`) — deduplicated within 0.5·ATR14 of each other
(keep the level with more touches). No round-number or invented levels, ever.

**Entry.** For scan-produced candidates: an entry *zone* (limit at structure or market on next
check), emitted in the `idea_outcomes` shape (`entry_low/entry_high`) so the ledger and the
proposal describe the same plan. `entry_ref_price` stays the live price at propose (the journal's
R basis, unchanged).

**Stop — structure-first, ATR-fallback, bounded:**
```
structure_stop = nearest support BELOW entry (VAL, PDL, daily pivot, POC-if-below) − 0.25·ATR14
atr_stop       = entry − k·ATR14                          # k backtest-selected; start 1.5 (FD-4)
stop           = max(structure_stop, atr_stop)            # the tighter of the two, but…
bounded:         0.75·ATR14 ≤ (entry − stop) ≤ 3·ATR14    # never microscopic, never a barn door
```
If no support exists within 3·ATR, use the pure ATR stop. The stop is recorded as `stop_pct` in
`plan_json` — the field `resolve_outcome` already reads for R-multiples.

**Take-profit ladder — from detected resistance, validated before it replaces anything:**
```
targets = overhead levels above entry, ascending, keeping only those ≥ 1R from entry and
          ≥ 0.5·ATR14 apart; take the first 3.
ladder  = 25% at T1, 25% at T2, 25% at T3, 25% runner (trail = structure_stop ratchet)
```
Degenerate cases: fewer than 2 usable levels → **no ladder**; the plan falls back to the sleeve's
validated exit rule (`strategy_plan` / exit engine) or, for general ideas, the triple-barrier-style
fixed exit the backtest was labeled with. **The ladder is itself a hypothesis** (BUILD.md
engine-roadmap #1: "does scaling out at resistance actually beat the fixed barrier on EV? Backtest
the exit rule; don't assume") — it ships as *display + journal plan* first, and replaces validated
exits only after the offline exit-rule backtest says it wins (Phase v2 exit criterion, §8).

**Sizing — risk-capped always, Kelly only on validated edge:**
```
risk_budget   = equity × RISK_CAP_PCT                     # hard per-trade cap — FD-1 (suggest 0.5%)
risk_qty      = risk_budget / (entry − stop)              # Decimal
IF the win% is renderable under §5's gate:                # validated + sufficient + fresh
    b   = bucket.avg_win / bucket.avg_loss                # from ev_table (add the two fields to
                                                          # research/ev.py bucket output)
    f*  = p − (1−p)/b                                     # full Kelly from OUR calibrated edge
    f   = λ · max(f*, 0)   with λ = 0.25                  # fractional — λ is FD-1b, never magic
    kelly_qty = (equity × f) / entry
    qty = min(risk_qty, kelly_qty)
ELSE:
    qty = min(risk_qty, strategy/default notional ÷ entry)   # current fixed-notional behavior
```
Research Round 3: all published Kelly constants/risk-of-ruin formulas were refuted — the sizing
derives from **our own** calibrated p and b, and λ=0.25 is an explicitly-labeled conservative
starting judgment, reviewed after live data, not a claimed optimum. The result flows into
`TradeCandidate.notional_usd`; `_size_candidate`'s buying-power cap and `RiskEngine.validate`
still gate downstream — **this design changes nothing at or past the chokepoint.**

---

## 7. The LEARN loop — what flows back, what never moves by itself

**Write path (every scored candidate, no exceptions):**
1. `register_idea(origin="decision_engine", …)` into `idea_outcomes` — including gated,
   below-threshold, and never-proposed ideas. Requires unification Phase B (the generalized
   `register_idea` helper + short-side support) plus **one schema addition: a nullable
   `features_json` TEXT column** storing the normalized factor vector + coverage + gates at
   registration. That snapshot is the online IC substrate; without it, per-factor attribution from
   the ledger is impossible.
2. Proposed ideas additionally journal via the existing seam (`score_json` = full score packet +
   LLM flags; `plan_json` = entry/stop/ladder/size math inputs), and carry `idea_id` once the
   unification B3 bridge lands — giving the founder's taken-vs-passed comparison mechanically.

**Read-back (nightly job, report-only):**
- **Per-factor rolling IC** on resolved ledger rows (needs `features_json`): rendered in a weekly
  calibration digest per the unification spec's reporting phase; cells gate at n ≥ 20 resolved
  ("accruing (n)" below that), factor-admission decisions gate at n ≥ 500 (§2 Lane B, §3 IC
  criterion).
- **Per-strategy attribution:** `strategy_id`/`origin` slice win-rate, avg R, net P&L via the
  existing `stats_by_strategy` + `get_summary().by_origin`. Known gap, flagged not solved here:
  exposure caps are account-wide while multiple strategies share an account (memory:
  per-strategy attribution) — a RiskEngine-adjacent change that is explicitly out of this
  design's scope.
- **Calibration drift monitor:** live reliability (predicted win% vs realized, per bucket) vs the
  offline artifact — divergence beyond the bucket's Wilson CI → flag in the digest → founder
  decides whether to force a PC re-run.

**When weights update:** ONLY via an offline PC run producing new committed artifacts, reviewed
before commit. Cadence: monthly/quarterly re-run (per the locked train-offline architecture), or
sooner on a drift flag.

**What NEVER auto-updates (hard list):**
- Factor weights, calibrator coefficients, EV tables — artifacts only, no online mutation path
  exists by design (the live scorer literally has no write access to them).
- Factor admission/removal — offline gate + founder review.
- Gate thresholds (regime, earnings, coverage) — config committed with the artifacts.
- Strategy/scorecard **arming** — everything ships `enabled=False` + `entry_mode="propose"`
  (memory rule: the founder arms manually, never auto-armed). The scorecard's proposal-producing
  scan is a strategy-registry entry like any other, subject to the same toggle.
- Anything at or past `propose_trade` — the chokepoint, RiskEngine, and execution stay untouched.

---

## 8. Phasing — shadow → surfaced → IC-weighted

**Phase v1 — scorecard live in SHADOW (journal-only).**
Build: factor columns in `research/features.py` (Lane A set); scorecard artifact builder
(`research/scorecard.py` — assembles `model.json` from the hypothesis config, reusing
`train.py`/`ev.py` for calibration/buckets); coverage guard in `score_features`; plan builder
(`signals/plan_builder.py` — levels, stop, ladder, sizing); the fusion producer
(`signals/decision_engine_scan.py` — candidate universe → score → plan → register_idea; **proposes
nothing**); `features_json` column + `origin="decision_engine"` registration (rides unification
Phase B); LLM thesis interface with validator (annotate-only).
Surfaces: **nothing new renders.** Scores/plans/theses exist only in `score_json`/`plan_json`/
the idea ledger.
**Verify/exit criteria:** (a) ≥ ~200 decision-engine ideas registered with full factor vectors and
the sweep resolving them; (b) a grep/test-level audit confirming no new number reaches any
user surface (the red-team's test harness pattern); (c) first offline scorecard backtest run
completes on the PC — factor admission results in hand; (d) founder reviews the factor-admission
report.

**Phase v2 — calibrated numbers surface behind the honesty gates.**
Preconditions (hard): the consolidated red-team remediation is shipped in its stated order
(compactors/tier-gate/caveats/freshness FIRST, `rule_id` rewire LAST); the offline scorecard run
produced artifacts that clear `low_confidence=False` + DSR/PBO + net-of-cost EV CI > 0; FD-7
(idea-tracker same-day semantics) resolved so online monitoring is trustworthy.
Build: proposal path armed (scorecard scan registered as a disabled-by-default strategy; founder
flips the toggle); win%/EV render under the §5 gate on the surfaces the remediation fixed; ladder
backtest run — ladder replaces fixed exits only where it won.
**Verify/exit criteria:** (a) OOS reliability diagram + Brier beat base rate and match the live
drift monitor within CI for one full month of shadow-vs-live comparison; (b) founder sees and
approves the first ten live proposal cards (thesis quality + number presence/absence correct);
(c) suppression verified end-to-end on phone + chat + widgets for a below-gate idea.

**Phase v3 — IC-weighted refinement.**
Preconditions: §3's earn-its-keep criterion met (≥ 500 resolved ledger ideas; IC-weights beat
equal on OOS Brier AND net EV; PBO < 0.5).
Build: IC weight vector as a new artifact version; Lane-B factor admissions as their ledger
samples mature; conditional calibration (per-strategy × regime/RVOL slices, n-gated — the
red-team's "Decision Engine v2 forward" note) enters design here, not before.
**Verify/exit criteria:** same artifact gates as v2 re-run on the new weights; equal-weight
scorecard kept as the comparison baseline in every subsequent digest (if IC stops beating it,
revert is a one-artifact rollback).

---

## 9. FOUNDER DECISION items (explicit — nothing here is silently picked)

| # | Decision | Default proposed in this doc |
|---|---|---|
| FD-1 | Hard per-trade risk cap (`RISK_CAP_PCT` of equity) | 0.5% |
| FD-1b | Kelly fraction λ | 0.25 |
| FD-2 | Initial weights: equal vs founder-prior (e.g., overweight F1 dip-family) | Equal (1/N) |
| FD-3 | Risk-off regime gate behavior: block new longs vs half-size | Block |
| FD-4 | ATR stop multiplier k search range for the backtest (and the shipped value) | Sweep 1.0–2.5, ship the OOS winner; 1.5 until then |
| FD-5 | Artifact freshness limit before a scorecard number is suppressed as stale | 120 days |
| FD-6 | Whether/when an LLM flag (e.g., `binary_event_risk`) may be promoted to a deterministic gate | Never in v1/v2; per-flag ledger analysis + explicit sign-off |
| FD-7 | (Carried from red-team #4, still open) Idea-tracker same-day-entry semantics: is a creation-day zone touch a legitimate trigger? Blocks trusting online stats. | Recommend `<= as_of_date` (skip creation-day bar) unless same-day entry is intended |
| FD-8 | Scorecard scan's candidate universe breadth (watchlist-only vs +S&P-500 store) and cadence | Watchlist + S&P-500 store, nightly + intraday-on-event |

---

## 10. What this design does NOT do (traps named, refuted paths declined)

- **No ML ensemble, no gradient-boosted trees, no neural anything** — "IC-weighted ML ensemble
  beats simple" was refuted 0-3. v1..v3 stay a linear, founder-readable scorecard.
- **No pre-trusted factors** — every published edge failed adversarial verification; every factor
  here is a hypothesis with weight 0 until OUR backtest admits it.
- **No survivorship-biased validation** — admission runs on Norgate Current & Past only; yfinance/
  survivor-only data never produces a published number.
- **No fixed-horizon labels** — triple-barrier only (volatility-aware), already implemented.
- **No leakage** — PurgedKFold + embargo for all OOF predictions; preprocessing fit per training
  fold; LLM flag analyses restricted to post-cutoff live data; Lane-B intraday factors never
  pseudo-backtested on data that doesn't exist.
- **No fabricated numbers, anywhere** — the LLM cannot emit or alter a number (enforced by
  validator, not prompt); unvalidated/insufficient/stale/config-drifted scores are SUPPRESSED, not
  badged, per the founder's decision; Wilson CIs are captioned as what they are.
- **No magic Kelly constants or risk-of-ruin formulas** — sizing derives from our calibrated p and
  b; λ and the risk cap are labeled judgments under founder control.
- **No curve-fit auto-arm** — weights/gates/admission move only via offline artifacts + review;
  every proposal-producing component ships disabled + propose-mode.
- **No touching the sacred path** — `propose_trade` is designed *into*, not modified;
  `ExecutionService.submit`, `RiskEngine.validate`, and the kill switch are unreachable from this
  layer except as the gates they already are.
- **No backtest-harness redesign and no new data purchases** — the PC/Norgate pipeline is reused
  as-is; live data stays the free/current stack (research Round 2: prove the free-data baseline
  before spending a dollar).
