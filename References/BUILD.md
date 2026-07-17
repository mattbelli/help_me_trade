# Help Me Trade — Build Plan

Agent onboarding: see root [`AGENTS.md`](../AGENTS.md). Update both when phases complete.

**The PLAN** — vision, the ★ Decision Engine, the roadmap arc, debt, principles, release gates.
NOT current state: that lives in `HANDOFF.md` (open threads) + `board.json` (in-flight) + `AGENTS.md`
(verified module map); the dated record is `BUILD_LOG.md`. Update this file when DIRECTION changes.

**Companion doc — [`IDEA_BANK.md`](IDEA_BANK.md):** the founder's committed product vision + design decisions (the *why* and *what*). This roadmap is the *how & when*. Most idea-bank items **are** things we'll build — their **ordering** lives in IDEA_BANK § "Build sequencing" and is reflected in §5–6 below. Keep deep rationale/vision in IDEA_BANK; keep the sequenced plan here.

**Stack:** FastAPI (Python, port 8000) + Next.js (port 3001) · Alpaca + Schwab · local SQLite · provider-agnostic LLM.

---

## 1. Product vision

**An autonomous trading agent that works in the background so the user can work and parent** — not a manual terminal, not a generic chatbot. The user does setup at the desk; the agent runs server-side, watches the market, and reaches the user's **phone** to approve / deny / modify. Trading is the mission; the dashboard is a **cockpit to configure + supervise the agent**, not to click trades.

**Autonomy ladder** (climbed as trust grows): **alert** (tell me) → **propose** (agent sizes a setup, pings my phone, I approve) → **bounded auto-execute** (acts within hard guardrails, reports after). Success metric: *how little the user must watch it and still trust it.* Phone **approval** — not auto-execution — is the life-changing unlock.

**The engine is one loop, run continuously:** PERCEIVE → REASON → DECIDE → ACT → DELIVER → LEARN. How each stage works, the current build-state per stage, and the researched best-in-class tech for each live in **[`AGENT_LOOP_RESEARCH.md`](AGENT_LOOP_RESEARCH.md)**.

---

## Realignment — the autonomous agent loop (2026-06-19)

The product crystallized: from "personal trading copilot" (a manual cockpit, a red ocean) to **an autonomous trading agent** (above). **Nothing built is wasted** — most of it *is* the agent's nervous system. The roadmap re-centers on the **loop**; the legacy Phase 0–8 detail in §5 is preserved as the shipped-state record.

**Loop scorecard** (refreshed 2026-07-17 against the verified AGENTS.md map + BUILD_LOG; ~% are honest
rough cuts, not measurements):

| Stage | Built | Frontier / gap |
|---|---|---|
| PERCEIVE | ~90% | options flow (own OPRA collector) + equity tape (own collector) + filings + EDGAR earnings + FINRA SI + regime + social + index-pressure gauge all live; gaps: fundamentals depth, news |
| REASON | ~80% | ONE Python chat brain (`ai/chat/loop.py`, all surfaces) + research engine v2 + nightly setup scan w/ conviction lane; gaps: durable multi-step loop, vector memory (parked, founder lane) |
| DECIDE | ~70% | **Idea Engine Phase 1 LIVE 07-16** (unified ledger + Grading Law + lane alarms); calibrated scorer wired; the EDGE is the frontier — fusion layer designed (`DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md`), not built |
| ACT | ~90% | chokepoint + phone approval + paper auto-entry/exit LIVE (exit engine revived 07-16 #91); LIVE hard-gated (Increment 3); bounded-auto = rung-3 design intel staged |
| DELIVER | ~85% | native RN app + Live Activities + push budget + notifications inbox/routing + workspace cockpit + own chart engine (TV deleted) |
| LEARN | ~70% | decision journal J0-J3 + calibration review + **idea-outcome grading of EVERY idea, taken or not** + reflection; vector memory + ExpeL rule promotion still parked |

**Six load-bearing principles** (research-derived — non-negotiable):
1. **LLM proposes; deterministic code disposes** — the LLM never holds account state and can only make a trade *smaller or absent* within rails, never larger or outside limits.
2. **Function-calls/SQL for live numbers; RAG only for unstructured text + memory** (vector RAG over prices hallucinates).
3. **One fail-closed risk chokepoint** — a single, un-bypassable path to the broker.
4. **Approve/reject/modify is the learning ground truth; P&L is noisy + gated** (≥30–100 trades).
5. **Provider-agnostic but tiered** — Opus 4.8 (heavy) + Haiku 4.5 (routine), behind a swap layer.
6. **Every learned artifact stays a human-editable rule/example** — inference-time, no model training.

**Realigned build sequence (R1–R5) — this is now the source of truth for ordering:**

- **R1 — Head start: import → retrospective LEARN** *(zero execution risk; IN PROGRESS).* The agent deep-researches years of imported trades → behavioral patterns (what went right / wrong) → surfaces in dashboards + a report → proposes rules/strategies. **Every proposed rule must pass the backtest→paper gate (no curve-fit auto-arm).** Bootstraps the agent's model of the user from real data — the cold-start fix, and the first trust-building "it knows me" moment. Lights up PERCEIVE/dashboards + REASON (research over the ledger) + LEARN (extract habits) + DECIDE (backtest proposals), all with **no ACT risk**. Sub-steps:
  - **R1a ✅ SHIPPED (import plumbing)** — option-symbol parsing (`imports/option_symbol.py`), atomic imports + error manifest, dedup precedence (`broker_txn_id` → hash). Full 4-year Schwab history imported (463 events across Individual/Roth/Joint + Alpaca paper; clean dedup of the prior partial import). The parsing/atomicity/dedup are the keepers.
  - **⚠️ FIFO round-trip P&L is structurally UNRELIABLE on this data — do NOT derive realized P&L from raw transactions.** Date-only export (no intraday timestamps) + heavy same-day scalping + short sales + positions opened *before* the export window (PLTR net −1,530 sh) make lot-level FIFO wrong. Measured: engine overstated total realized P&L by **~$124k** ($136k vs net-cash truth ~$12.4k); NVDA engine +$70.6k vs true **+$1,369.60** (position fully flat → realized = net cash, order-independent). R1c's split adjustment only flipped NVDA's phantom −$14k into a phantom +$56k — same class of bug.
  - **R1d ✅ SHIPPED — reliable analytics + behavioral facts.** `db/realized_pnl.py` (net-cash realized, exact for closed positions, + reconciliation: closed / open-long-MTM / incomplete-flagged) + `db/behavioral.py` (cadence, sizing, concentration, same-day-scalp, day-of-week, asset mix); FIFO P&L demoted to `approximate_fifo` in `ai_performance_context`. 194 tests pass. **Verified on real data:** realized_closed **+$12,431.97** (matches independent net-cash check; NVDA correctly +$1,369.60), per-symbol win-rate 64.7%, PLTR (−1,530 sh) + GME flagged incomplete. Behavioral truths surfaced: TSLA 24.6% / PLTR 22.2% of trades, 22% options, 26 same-day scalp-days (TSLA-led), heaviest on Fri/Tue.
  - **Authoritative P&L source = Schwab Realized Gain/Loss report / 1099-B** (handles cost basis, splits, wash sales, pre-window lots) — fills the open/incomplete positions net-cash can't. Same source as futures (PDF/CSV). User export pending.
  - **R1c (split adjustment) — held/uncommitted.** Correct-in-principle for genuinely-held-across-split lots; folds into FIFO *hold-time* use, not the P&L headline. Will commit bundled with R1d.
  - **R1b ✅ SHIPPED — retrospective analysis pass.** `ai/retrospective.py` → reads the honest facts (`ai_performance_context`, train-window-scoped) → provider-agnostic structured LLM → narrative (what went right/wrong + behavioral observations) + candidate rules + ≤3 lessons. **Guardrails (Sonnet-adversarially-reviewed):** genuine **train/test split** — rules proposed from the train window only, backtested on a held-out OOS tail the model never saw (insufficient-history → "validate manually", never faked); proposed rules saved **`enabled=False`** (never auto-armed), invalid specs dropped; unreliable `approximate_fifo` $ figures stripped from the LLM's view; honest disclaimer. `POST /api/performance/retrospective` → renders in the existing `/research` viewer via a "Run Trade Review" button on `/performance`. 198 tests. **Narrative quality is model-dependent** (reliable on a strong LLM; the deterministic analytics + entry-style are always solid) — fix is a stronger model / native JSON-schema mode, not more parsing. Still needs the Schwab Realized-G/L import to complete P&L on incomplete positions (PLTR etc.).
  - **R1e ✅ SHIPPED — deterministic entry-style profile.** `signals/trade_context.py` — per-trade market context from bars (prior 1/3/5d move, vs SMA50/200, dist-from-52w-high, gap) + an `entry_style` profile (pullback/dip/breakout/momentum split, mean-reversion-vs-momentum lean, fights-trend %, avg prior move). **No LLM → reliable regardless of model.** Wired into `ai_performance_context(include_trade_context=True)`. 221 tests. **Verified on real data: confirmed mean-reversion dip-buyer** — 64.5% of contextualized buys below the 200-day, avg entry after −2.7%. Caveat: only **31.6% bar coverage** (warmup/symbol gaps) → improve fetch later. News/earnings context = a later scoped add (Finnhub, salient-trades-only); options deferred.
  - **R1f — onboarding capstone (NEXT).** First-run flow: import → deterministic-first **edge-revealing feedback** (summary + entry-style + patterns, always reliable) + LLM narrative on top → proposed strategy. "Analyze later" entry point (the existing Run Trade Review button) covers users who import after onboarding. The Phase-6 first-run-onboarding item, realized as R1's capstone. Then **R2** (risk engine / ACT — the forward agent).
- **R2 — ACT: the safety spine ✅ SHIPPED** *(chokepoint live since late June — `execution_service.submit()` + `RiskEngine.validate()` + kill switch + daily-loss breaker; DRY_RUN→paper done; live ramp still hard-gated behind Increment 3's typed double-gate. Original spec kept below for the record.)* `Decimal` money correctness; **RiskEngine** = single fail-closed chokepoint + guardrails (per-trade risk, heat, daily-loss, drawdown, concentration) + **kill switch**; **ExecutionService** (sole order path) → Alpaca behind the gate; proposal object (TTL + idempotency key = `proposal_id` + payload hash + **TOCTOU re-validation at execution**). Staged: **DRY_RUN → paper → live ramp (25/50/100%) → bounded auto inside a code envelope**. First job: **position-risk-guardian**.
- **R3 — DELIVER: phone. ✅ SHIPPED** (the `feat/mobile-app` app, merged 2026-06-30; Mac/RN agent owns `mobile/`, serv owns the backend slice). A **native Expo React Native app** (`mobile/` — Expo SDK 56 / RN 0.85, `expo-router`, `expo-widgets`, FlashList) — a deliberate pivot from the originally-planned Capacitor-wrap-the-web. **Working phone client:** watchlist (live WS pricing + extended-hours `sessionView` + RVOL), **candlestick charts** (vendored TradingView lightweight-charts in a WebView; inline + full-screen with interval selector + crosshair), ticker detail + company/EPS disclosure, alerts **inbox** (push deep-links, persisted clear-read), Instagram-style tab bar, runtime **theme switcher** matching the web. **★ Live Activity / Dynamic Island Approve/Reject — the phone-approval unlock — VERIFIED END-TO-END on a real device (`3742b91`):** START→`pending` → UPDATE→`approved` → END→`executed` all passed; serv's `_la_wire_content_state` (`signals/push.py`) sends the double-encoded `{name, props}` envelope expo-widgets needs; **wired into `trading/proposals.py` so real proposals drive the card automatically**; `tests/test_live_activity.py` (17). **Mobile copilot LIVE** on serv's `POST /api/chat/stream` (SSE over `expo/fetch`); per the mission, **chat never approves** — a `proposal` event hands off to the existing Face-ID Approvals inbox. **Since closed:** mobile chat was rebuilt onto the unified `loop.py` brain (2026-07-07/08, six-event SSE via `ai/chat/mobile_agent.py`), superseding the gap-doc era. Still open: LA `failed`-path unverified (check on first RiskEngine block). *(The original plan — self-hosted APNs `aioapns`, interactive Approve/Deny/Modify categories, biometric tiered confirm — is substantially realized; the chokepoint stays the sole executor, fired only by the phone's Approve.)* **NB:** distinct from the Phase-6 web "mobile shell" (the responsive `/workspace` breakpoint) — this is the dedicated native app.
- **R4 — LEARN: close the loop. ◐ PARTIAL** — decision journal J0-J3 + calibration review shipped 07-01; the idea-outcome ledger + Grading Law (07-16) is the calibration flywheel; vector memory + ExpeL rule promotion PARKED (memory-overhaul, founder lane). Original spec: Append-only **decision journal** (reasoning recorded *before* outcome; dual human+structured); approve/reject/modify → **user-editable rules** (promote/demote, ExpeL-style GC); **sqlite-vec** memory + `recall_memory` tool; **Langfuse** self-host eval + golden suite.
- **R5 — deepen PERCEIVE/REASON. ◐ THE CURRENT ARC** — July landed the perception breadth (flow, tape, filings, EDGAR, index pressure); the REASON/DECIDE deepening is now the Idea Engine + ★ Decision Engine fusion work (§4). Pydantic AI / DBOS not adopted. Original spec: Fundamentals/news/calendars (Finnhub/FRED/SEC, own-LLM sentiment); **Pydantic AI + DBOS** durable agent loop; sqlite-vec RAG + rerank; `talipp` live incremental indicators.

**Cross-cutting (honored from R2 on):** `Decimal` everywhere money is computed (SQLite TEXT, not REAL); broker behind a thin `BrokerAdapter`; all orders through the execution gate; broker = source of truth (reconcile on reconnect).

---

## ★ The Decision Engine — the core value (PERCEIVE → REASON → DECIDE) — READ THIS

**This is the grand slam. Delivery (phone push) and execution (the chokepoint) are the strong *foundation* that
makes the app usable — but DECISION QUALITY is what makes it worth using. If the agent's trade ideas aren't
genuinely good, the app has little value.** Founder's explicit direction (2026-06-21): this is the #1 priority
once R3's proposal pipe exists, and a dedicated founder+orchestrator **deep-research effort** will design it.
(Reinforces CLAUDE.md: *"the hard part is decision quality + risk + trust, not automation plumbing."*) **All
future agents: treat this as the headline value-driver, not a someday-R5 footnote.**

**Where it actually stands (2026-07-17) — build honestly splits the *infra* from the *edge*:**
- **Code-real today:** the `propose_trade` intake seam → phone proposal; the calibrated scorer wired into
  perception/chat/proposals; per-strategy validated backtests; S/R levels + volume profile; **and the Idea
  Engine Phase 1 (LIVE 07-16)** — unified idea ledger, EVERY idea registered + graded vs its own plan +
  SPY alpha (Grading Law), lane meters + silence alarms, `user_action` stamping. The calibration flywheel
  the engine needs is now running; the displayed win% is the Wilson-bounded calibrated number
  (`calibration-pack` spec) or "unproven" — never fabricated.
- **Still the frontier (the value):** the FUSION layer — multi-signal scoring + LLM-reasoning-on-top +
  S/R scale-out ladders as plan outputs. Designed (`DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md`), not
  built; Idea Engine Phase 2+ is the vehicle. Each factor stays a hypothesis gated by backtest→paper.

The agent must produce **ranked, explained, fully-planned trade ideas** by fusing many signals the way a sharp
discretionary trader does — but tireless and data-complete.

**The vision (founder's example), made concrete:**
> "hype is picking up on Reddit/X · stock just crossed below its 200-day · it's oversold · overall market
> health is good · insiders made big buys recently · the sector is beaten-up but looks like a rebound" →
> **this idea ranks 10/10.**

Every proposal the agent pushes should carry: **(a)** a clear plain-language **"why"** (the thesis + which
signals fired); **(b)** a **conviction rank / estimated win %** — *calibrated, never fabricated* (guardrail
below); **(c)** a **full trade plan** — entry, stop, and a **scale-out take-profit ladder** at *real* levels
(e.g. 25% @ $310 / 25% @ $315 / 25% @ $320 / 25% @ $325), with targets from **prior support/resistance**, not
round guesses.

### Two halves to build
**1. The best data (PERCEIVE) — feed the LLM the best inputs (garbage in → garbage out; the edge is breadth +
quality of grounded signals):**
- **Social / hype:** Reddit, X, StockTwits — crafted-search ingestion, LLM-read for emerging hype/sentiment
  shifts (prototype reliability first, no paid APIs to start — IDEA_BANK §7.6).
- **Technicals:** price vs 50/200-DMA, RSI/oversold, ATR, **support/resistance levels**, RVOL/volume, trend.
- **Market health / regime:** breadth, index trend, VIX, risk-on/off.
- **Fundamentals / catalysts:** insider transactions (SEC Form 4), earnings calendar + surprises, valuation,
  analyst actions (Finnhub/FRED/SEC).
- **Sector / relative strength:** sector beaten-up-but-turning, RS vs market.

**2. Signal fusion + ranking (REASON → DECIDE) — turn signals into a scored, planned idea:**
- Normalize every source into **features/facts** (extends `signals/features.py`).
- **Fuse** weak signals into a conviction score — *deterministic feature scoring* + **LLM reasoning on top** for
  narrative + edge cases. Locked split: **LLM proposes the thesis/weighting; deterministic code computes the
  live numbers, the score, the sizing, and the targets** (function-calls/SQL for numbers; RAG only for
  unstructured text — vector RAG over prices hallucinates).
- **Compute the plan deterministically:** risk-based sizing, stop, and the take-profit ladder from detected S/R.
- Emit one rich **`TradeCandidate`** (rationale, score, win%, entry/stop/targets, source signals) into the single
  **`propose_trade` intake seam** → phone proposal. Every data source is a pluggable *producer* behind that one
  seam (see R3-C) so **adding sources is additive, not rewiring.**

### Non-negotiable guardrail — calibrated, NOT fabricated
The founder wants a **rank / win %** on every idea — but a made-up "70% win" is the single most dangerous thing
this app could show (false confidence on real money). Any score/win% MUST be **derived from a backtested,
calibrated model or labeled explicitly as heuristic conviction** — never invented by the LLM. Every proposed
rule/strategy still passes the **backtest → paper gate** before it's trusted (no curve-fit auto-arm). This is
the CLAUDE.md honesty rule applied to the headline feature.

**[Founder directive 2026-07-17 — calibration is a FILTER, not the product.]** Markets are non-stationary
and a fused multi-factor win% ("you'll win 68% if you take this") needs sample sizes that never converge
inside one regime — **chasing that number is explicitly NOT the priority.** What IS required: log EVERY
idea + outcome, grade mechanically (the idea ledger), and use the grades ORDINALLY — rank the lanes,
weed out bad idea-types, tighten the filter so only a handful of good ideas reach the phone (idea count
is budgeted; never a thousand pings). The displayed number stays honest per the guardrail above
(Wilson-bounded or "unproven") — but the app's forefront is IDEA GENERATION + event-driven investigation
(social pop → research it in-app; RVOL 4× normal → look; two giant put sweeps co-occur at 10am → connect
the dots), not probability precision. Cardinal calibration ("68%") doesn't converge; ordinal calibration
("this lane beats that lane; kill the junk lane") converges in weeks and survives regime drift.

### Sequencing
Maps onto **R5 (deepen PERCEIVE/REASON)** but is elevated to the app's headline value-driver. Order: R3-C ships
the proposal *pipe* (one source, fixed sizing) → then iterate the decision engine deep — data sources →
fusion/scoring → calibrated win% → S/R-based target planning — each preceded by **dedicated deep research**
(best sources/APIs, fusion methods, win-rate calibration, scale-out/target methodology, social-hype reliability).

### Decision engine NOW vs trading bot LATER (same brain) + the two data needs
**Same brain, different trigger finger.** A decision-support engine (agent scores an idea → *human approves* →
chokepoint executes) and a future **trading bot** (agent scores → *auto-executes within guardrails* → reports)
share the SAME scored edge — they differ only in who pulls the trigger (the autonomy ladder: propose →
bounded auto-execute). The `propose_trade` seam already supports both, so **edge/factor research feeds both** —
nothing is wasted. Build the human-approval engine now; the bot is the same pipe with a guardrailed
auto-approve later. NOW factors: mean-reversion + momentum (buildable as scorecard factors). LATER/bot:
statistical arbitrage (pairs, options skew — needs continuous monitoring + paired execution).

**Headline metric = Expected Value, not just win%.** `EV = win% × avg_win − loss% × avg_loss`. A 40%-win
setup with 3:1 winners beats a 70%-win setup with fat losers — show EV per idea, with win% + confidence band.

**TWO data needs — do NOT conflate (or you buy the wrong data):**
- **Live / operational** (agent decides *now*): quotes, current sentiment/insider/fundamentals → the
  **data-sources research** (queued).
- **Historical / backtest** (validate edges + calibrate honest EV/win% over *thousands* of instances): years
  of **survivorship-free, split/dividend-adjusted daily bars** + **point-in-time** fundamentals across many
  symbols. Daily bars are cheap (yfinance/Stooq free to start; Tiingo/Norgate/Sharadar cheap + rigorous);
  the hard/expensive part is point-in-time fundamentals + historical sentiment (Tier 2, deferred). Do NOT buy
  a real-time feed for backtesting. Significance bar is real: **~1,000–10,000 instances** — the user's own
  ~260 round-trips are far too few, so derive edge stats from broad **backtests**, not personal history, and
  always show the uncertainty band.
- **Backtest pipeline steps:** define survivorship-free universe → ingest adjusted bars → compute
  point-in-time features → **triple-barrier label** outcomes (this generates the thousands of samples) →
  walk-forward backtest → Platt-calibrate score→prob → EV per bucket → multiple-testing checks (DSR/PBO).
  Groundwork exists: `signals/backtest.py` + Alpaca bar fetch. Gaps: survivorship-free universe, labeling,
  calibration pipeline.

**Research DONE (Rounds 1-3 in `References/DECISION_ENGINE_RESEARCH.md`).** Net: build the honest pipeline,
treat every factor as a hypothesis (no published edge survived verification), run almost entirely on free
*live* data, QuiverQuant $75/mo optional.

### Train-offline / serve-online — the build architecture (locked 2026-06-21)
Backtesting/training is **offline + periodic**; live scoring is **online + continuous**. They split cleanly,
which also dissolves the Norgate-is-Windows-only problem:
- **OFFLINE (founder's Windows PC, where Norgate's data app runs) — re-run monthly/quarterly:** Norgate
  **survivorship-free** history → point-in-time features → triple-barrier labels → PurgedKFold+embargo backtest
  → Platt calibration → produces small **ARTIFACTS**: factor weights (config), the calibration model
  (coefficients), per-bucket EV/win% tables.
- **Commit the ARTIFACTS to the repo — NOT the raw licensed Norgate data** (artifacts are KB-scale + license-safe).
- **ONLINE (serv, Linux) — continuous:** the live agent **loads the artifacts** and scores today's setups using
  **free Alpaca live data** → EV + win% + confidence band on each proposal. Norgate never touches serv.
- **Shared feature code** (`signals/`) runs on BOTH history (offline) and live data (online) → no train/serve
  drift. Repo grows an offline `research/`-style backtest pipeline + the live `signals/` scorer reusing it.
- **Backtest data source = Norgate** (best survivorship-free; Windows-only is fine since backtest is offline).
  Sharadar (Linux/API) is the fallback if offline-on-Windows becomes painful. Backtest-data is a *modest paid
  sub* (separate from the ~free live-data answer); confirm current pricing on the vendor site.

### Research evidence base — `References/DECISION_ENGINE_RESEARCH.md`
Round-1 deep research (2026-06-21, adversarially verified) — **read it before building the engine.** Headlines:
**(a)** the signals → composite **score** → **calibration layer** → win% pipeline, with the **LLM only on
narrative/text and deterministic code on every number**, is evidence-backed (and maps onto our agent-proposes →
human-approves → chokepoint model); **(b) start with a transparent weighted-composite scorecard, NOT an ML
ensemble** ("ensemble beats simple" was refuted); **(c)** win% = **Platt scaling** (beats isotonic at the small
sample sizes a solo trader has) + out-of-sample reliability/Brier + multiple-testing controls (Deflated Sharpe,
PBO/CSCV) — until samples suffice, show a **confidence interval / "insufficient data," never a fabricated single
win-rate.** Still open (next research): concrete fusion weights, highest-ROI **paid data sources**, min sample
size for honest calibration, and how regime/breadth gates the score.

### Engine-deepening roadmap — captured ideas (founder, 2026-06-21)
The breadth that makes the engine *effective* (beyond the v1 technical scorecard). **Cross-cutting honesty rule
(applies to ALL of these):** every signal is a HYPOTHESIS validated by backtest — and macro/event/crowd edges
are *especially* prone to being already priced in, so their value is usually in **RISK (avoiding landmines,
sizing) more than prediction.** Build order: prove the Norgate base model first → add these as tested
hypotheses → portfolio layer on top. None require rewiring — they're features, plan-outputs, or context layers
behind the same pipeline + `propose_trade` seam.

**Strategy substrate (settled June, detail in BUILD_LOG rows 2026-06-21/22/26):** strategies are isolated,
independently-toggleable, FROZEN modules in a registry (`signals/strategy_scan.py` + Settings cockpit
panel), proposing through the one `propose_trade` seam; per-strategy paper **auto-entry/auto-exit** runs
through the unchanged chokepoint, paper-fenced. **Every new strategy ships disabled + `entry_mode=propose`
— the founder arms it manually.** ⚠️ Standing alert-design constraint: bulk/rebalance books (momentum) must
batch fills into ONE summary push at their promotion — per-trade pushes are for rare tactical sleeves only.

**1. Support/Resistance + trade-PLAN outputs (completes the "fully-planned idea").**
- *Features:* distance to nearest support/resistance, room-to-run to next level, level-test counts (from bars:
  swing pivots, volume-by-price, prior highs/lows, round numbers).
- *Outputs (attach to proposal like the score):* **TP scale-out ladder** at detected resistance (the 25%@$310…
  vision); **hold length** — nearly free, the triple-barrier labels already record `holding_days`.
- *Validate:* does scaling out at resistance actually beat the fixed barrier on EV? Backtest the exit rule; don't assume.

**2. World-context layer (events · macro · cross-asset · prediction markets).**
- *Economic-event awareness (FOMC/CPI/earnings):* a feature ("days to next event", "event inside holding
  window") AND a **hard risk rule** — *don't hold long option premium through a known IV-crush event* (critical
  for the founder's long-options style; "sell the day before"). Calendar data is free.
- *Macro/sector/index/regime:* sector-ETF RS, SPY vs 200-DMA, VIX percentile, breadth (% of universe > 200-DMA),
  FRED macro. Roles: features **and** a **regime GATE/modifier** (Round-1 open question — regime can dominate
  edge robustness; regime change needs human review). Mostly free data.
- *Prediction markets (Polymarket):* cleaner macro-event *odds* feed (free public API) for the event/regime
  layer. **Skeptical:** prediction markets are efficient + already priced into stocks; indirect for equity
  edges, coverage-limited. Treat as an experiment, not a foundation.
- *Social hype via X API (VERIFIED on docs.x.com 2026-06):* X is now **pay-per-use** (no subscription, no
  minimum) — **Posts: Read = $0.005/post**, and crucially **24h deduplication** (charged once per unique tweet
  per UTC day no matter how often you re-poll → poll-frequency barely affects cost, no clever `since_id`
  needed). Reading a handful of curated trader accounts every minute during market hours ≈ **$30–100/mo**
  (≈ unique-tweets/day × $0.005); set a **hard spending limit** as the safety rail; monitor via the Usage API.
  This is the **first real social-data option** (Round 2 found none) — still a hypothesis (must prove the
  tweet signal adds EV). NB: "Owned Reads" $0.001 apply only to your *own* data; xAI-credit kickback needs
  $200+/mo (irrelevant at this scale).
- *Crypto/BTC (free data):* **(a)** BTC as a risk-on/off **regime feature for equities** — easy, cheap, do it;
  **(b)** *trading* crypto as its own universe — a separate model + product-scope decision (pipeline
  generalizes, but 24/7, higher vol, no fundamentals). Bonus: crypto is great free data for *dev-testing* the harness.

**3. Portfolio management + per-account risk profiles.** Elevates agent from trade-picker to portfolio MANAGER
(the founder manages aggressive trading accounts AND conservative accounts for others — concrete multi-account
need). Two layers:
- *Layer A — risk profiles per account (conservative/moderate/aggressive): near-term, high ROI.* A parameter set
  the agent obeys per account — conviction threshold, sizing (¼-Kelly → fuller Kelly), max position %/heat/net
  exposure, allowed instruments, drawdown limits. Mostly **config + per-account RiskEngine caps** (extend what
  exists). One engine serves every account at its risk tolerance.
- *Layer B — portfolio exposure / allocator: deeper.* Net delta (long/short), correlation-aware sizing (vs
  existing holdings, not just per-trade Kelly), hedging — the classical risk-budget/allocator the per-trade
  proposals fit within (Round-1: LLM signals → classical allocator w/ exposure/turnover controls). **Data gap:**
  options Greeks/delta need an options-data source or Black-Scholes-with-IV (no free options data yet — Round 2
  didn't crack it); equity net-exposure is easy. This is where regime-gate + event-awareness + sizing converge.

**4. Agent-in-the-loop SIMULATION harness (the "value test" — arguably a prerequisite to bounded auto-execute).**
A "wind tunnel" that drives the WHOLE loop (perceive→score→propose→auto-approve→execute→learn) over years of
data in minutes, to observe **system behavior** (NOT P&L): did rule count explode or stabilize? proposal volume
runaway or sane? did dedup/caps/kill-switch hold across 10k iterations? did the learn loop converge (self-improve)
or oscillate/diverge (haywire)? **Distinct from the model backtest** (that tests the score; this tests the
organism). Needs: a **sim clock** (point-in-time), a **sim broker** (fills+slippage, extends dry-run), an
**auto-decision policy** (proxy for the human approver — its choice shapes results), an **observability layer**.
Catches: human-proxy approval; "self-improve" only works as well as the learn loop is *wired* (R4, partial — sim
reveals gaps); **cost** (run the deterministic loop at scale, mock/sample the LLM); point-in-time discipline is
mandatory. Doubles as the best integration/regression test + safety stress-test + trust-building demo. R4/R5 work.

---

## 2. Architecture (keep this shape)

Moved (2026-07-17): the live, verified module map is **`AGENTS.md`** (root); the plain-English tour is
`References/ARCHITECTURE.md`; editable diagrams: `ARCHITECTURE_DIAGRAM/MODULES.excalidraw`.
The 06-16 "locked" chat-architecture notes that lived here were superseded by Phase 1b (one Python
chat brain, `ai/chat/loop.py`) — see BUILD_LOG.md rows 2026-07-07.

## 3. Current state

Removed (2026-07-17) — this snapshot table kept going stale, and its own header said stale-is-worse-
than-absent. Current state now lives where it stays current: **`HANDOFF.md`** (open threads),
**`board.json`** (in-flight work), **`AGENTS.md`** (what exists, verified), `BUILD_LOG.md` (what shipped, when).

## 4. Active roadmap — the arc (rewritten 2026-07-17)

*In-flight truth = `board.json` + `HANDOFF.md`; this section is the STRATEGIC arc only — update it when
the arc changes, not per round. The 07-01 cockpit-hardening backlog (P1–P12) that lived here shipped
across the July rounds (BUILD_LOG rows 2026-07-01 → 07-16) and is retired.*

*Order founder-confirmed 2026-07-17 (grill session): **#1–2 are the forefront** — "get the fusion layer
and idea engine running, populate ideas, start learning from them" — then the AI-chat surface upgraded to
work with all of it, closing the self-learning loop end-to-end. The app runs two parallel avenues:
**(a) event-driven ideas → phone approve** (the away-from-desk product) and **(b) frozen strategies on
paper autopilot → watch results → promote to real only on observed evidence.** Memory overhaul stays
parked until the idea engine gives it something to recall (mem0 removal excepted — that goes anytime).*

1. **Idea Engine** — Phase 1 LIVE (07-16); verify gate, then Phase 2 spec *informed by the first live
   week of lane meters* (`IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md` is governing). This is the vehicle
   for the ★ Decision Engine's event-gated idea throughput + calibration flywheel.
2. **★ Decision Engine fusion layer** — build to `DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md`:
   multi-signal fusion + LLM-on-top + S/R ladders, honest calibrated EV/win% as samples accrue.
3. **Autonomy rung 3 — bounded auto-execute** — Increment 3 typed double-gate for LIVE; rung-3 design
   intel staged (fs kill-switch sentinel, notional reconciliation, consent-hash audit chain).
4. **Perception: verify + extend** — index-pressure OpEx verify + classification lab (pre-registered
   metrics only); deferred collector findings F3/F4/F8; earnings/EDGAR hardening.
5. **Strategy pipeline** — PC/Norgate validation queue (3 candidates, `STRATEGY_VALIDATION_QUEUE`);
   earnings_drift experiments; `chokepoint-short-support` (greenlit in principle, carded).
6. **Product surface** — `WATCHLIST_DEFAULTS_SPEC` (ready to build); workspace-first convergence (legacy
   sidebar pages die after parity); mobile = native bottom-tab app, never the desktop grid.

**Open product decisions live in the board's `founder` lane** — not in this file.

## 5. Known debt & cross-cutting issues (pruned 2026-07-17)

*Full cleanup backlogs: `CODE_AUDIT.md` (06-20) + `REFACTOR_BACKLOG_2026-07-08.md`. The 07-01 findings
that lived here (notification deep-links, inert row-click, copilot dock width, scans≈rules) were fixed or
superseded by the July rounds — see BUILD_LOG. Still genuinely open:*

- **mem0 semantic branch is inert.** `MemoryService.search()` mishandles mem0's `{"results": [...]}`
  shape → retrieval silently falls through to local token-overlap. Fix belongs to the parked
  memory-overhaul (founder lane) — don't patch piecemeal.
- **FIFO is unreliable** on the real Schwab export — net-cash realized is the source of truth; label FIFO
  wherever it surfaces (standing honesty rule, not a fixable bug).
- **Exit-engine sibling fallbacks.** #91 fixed the guard; unguarded broker-id fallbacks remain in
  `proposal_intake`/`RiskEngine` paths (recorded on `data-correctness-m-tail`).
- **Symbol-search triplication** — three parallel implementations; consolidate when touched.
- **Mobile parity unaudited** for the July web fixes — check `PLATFORM_PARITY.md` before assuming web-only.

## 6. Guiding principles

1. **Safety before speed** — the chokepoint + RiskEngine + audit trail exist; LIVE stays hard-blocked until Increment 3's typed double-gate, and nothing ever bypasses `RiskEngine.validate()`.
2. **Ground before generate** — LLM sees analytics + memories + trade performance, not raw JSON dumps.
3. **One ledger** — Import (full history) + API sync (incremental) → deduped `trade_events`.
4. **Realized ≠ cash flow** — Performance UI distinguishes closed-trade P&L from buy/sell cash movement.
5. **Incremental phases** — Each phase leaves the app runnable.
6. **Tests ride along** — pytest per phase, not a final bolt-on.

---

## 7. Key modules (quick reference)

Moved (2026-07-17): **`AGENTS.md`** is the verified module map — one table, kept current by the
same-round rule + weekly freshness audit. Don't maintain a second copy here.

## 8. Success criteria (release gates)

### Platform

- [ ] SSE/WS responsive during broker polls
- [ ] Poll loop survives injected failures; `/health` reports stale data
- [x] All API routes reject unauthenticated requests when `API_TOKEN` set (test-covered)
- [x] `pytest` in CI on every push

### Copilot

- [x] Chat streams tokens from AI SDK route (legacy Python SSE proxy remains for some providers)
- [x] Every turn gets analytics + top-k memories (`ai/chat/context.py`; retrieval is token-overlap until the memory overhaul)
- [ ] Research never presents mock web as real; articles are read, not skimmed
- [ ] Agent answers “how am I doing?” via `get_performance_insights`, not guessing

### Trading safety

- [x] Decimal-only money in `trading/` — float→Decimal seam at the propose boundary (adopted design; broker adapters stay float by choice)
- [ ] `POST /api/orders` cannot bypass `RiskEngine`
- [x] Audited execution record for every order intent (`db/order_executions`, audit-before-validate)

---

## 9. Explicitly out of scope (for now)

- Schwab order execution — **permanently**: Schwab = real funded accounts, read-only data/ledger forever; Alpaca is the execution broker
- LibreChat / Open WebUI adoption (UX inspiration only)
- assistant-ui **LangGraph** runtime (`@assistant-ui/react-langgraph`) + hosted **Mem0 cloud** SDK — patterns adopted, dependencies rejected (see §2)
- Frontend `decimal.js`
- Full Alembic (lightweight `schema_version` is enough)
- **Un-guarded** autonomous trading. Bounded auto-execution IS the end goal — but only inside the RiskEngine's hard limits + kill switch. The fail-closed gate exists and paper auto-entry/exit already runs inside it; LIVE bounded-auto waits for Increment 3's typed double-gate + the rung-3 hardening.

---

## 10. Local dev & deploy (Linux `serv`)

Moved (2026-07-17): quick start + deploy in the root **`README.md`**; full server detail in
`References/SERVER_SETUP.md` + `References/STARTUP.md`; non-obvious runtime bits in the `serv-runtime`
project memory. Pre-push checks: `ruff check .` + targeted pytest + `npm run typecheck` (see CLAUDE.md).

## 11. Document history

Moved (2026-07-17) to **`BUILD_LOG.md`** — one row per round, newest first, grep-only.
