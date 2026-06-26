# Help Me Trade — Build Plan

Agent onboarding: see root [`AGENTS.md`](../AGENTS.md). Update both when phases complete.

**Single source of truth** for current state, architecture, and what to build next.  
Supersedes the six docs in [`archive/`](archive/) (2026-06-16).

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

**Loop scorecard** (full codebase inventory; deep tech in [`AGENT_LOOP_RESEARCH.md`](AGENT_LOOP_RESEARCH.md)):

| Stage | Built | Critical gap |
|---|---|---|
| PERCEIVE | ~85% | fundamentals, news/sentiment, options |
| REASON | ~75% | durable multi-step agent loop, real RAG, guardrails |
| DECIDE | ~60% | position sizing, stops, risk model, backtest framework |
| ACT | ~30% | **risk engine + execution — the bottleneck** |
| DELIVER | ~50% | phone push / mobile |
| LEARN | ~70% | decision journal, closed feedback loop, eval |

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
- **R2 — ACT: the safety spine** *(unblocks the agent acting).* `Decimal` money correctness; **RiskEngine** = single fail-closed chokepoint + guardrails (per-trade risk, heat, daily-loss, drawdown, concentration) + **kill switch**; **ExecutionService** (sole order path) → Alpaca behind the gate; proposal object (TTL + idempotency key = `proposal_id` + payload hash + **TOCTOU re-validation at execution**). Staged: **DRY_RUN → paper → live ramp (25/50/100%) → bounded auto inside a code envelope**. First job: **position-risk-guardian**.
- **R3 — DELIVER: phone.** One Next.js codebase → **Capacitor** shell; **self-hosted APNs** (`aioapns`); interactive **Approve/Deny/Modify** notification categories (foreground actions; Modify deep-links to the order form); **SSE** dashboard + idempotent REST actions; biometric tiered confirm. (Web push can't do lock-screen buttons; Live Activities deferred.)
- **R4 — LEARN: close the loop.** Append-only **decision journal** (reasoning recorded *before* outcome; dual human+structured); approve/reject/modify → **user-editable rules** (promote/demote, ExpeL-style GC); **sqlite-vec** memory + `recall_memory` tool; **Langfuse** self-host eval + golden suite.
- **R5 — deepen PERCEIVE/REASON** *(as the agent's job demands).* Fundamentals/news/calendars (Finnhub/FRED/SEC, own-LLM sentiment); **Pydantic AI + DBOS** durable agent loop; sqlite-vec RAG + rerank; `talipp` live incremental indicators.

**Cross-cutting (honored from R2 on):** `Decimal` everywhere money is computed (SQLite TEXT, not REAL); broker behind a thin `BrokerAdapter`; all orders through the execution gate; broker = source of truth (reconcile on reconnect).

---

## ★ The Decision Engine — the core value (PERCEIVE → REASON → DECIDE) — READ THIS

**This is the grand slam. Delivery (phone push) and execution (the chokepoint) are the strong *foundation* that
makes the app usable — but DECISION QUALITY is what makes it worth using. If the agent's trade ideas aren't
genuinely good, the app has little value.** Founder's explicit direction (2026-06-21): this is the #1 priority
once R3's proposal pipe exists, and a dedicated founder+orchestrator **deep-research effort** will design it.
(Reinforces CLAUDE.md: *"the hard part is decision quality + risk + trust, not automation plumbing."*) **All
future agents: treat this as the headline value-driver, not a someday-R5 footnote.**

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

**Forward-test status (2026-06-21):** the validated dip-buy strategy's **ENTRY** path is wired live as an
isolated, **disabled-by-default** module (`signals/strategy_scan.py`, flag `dip_strategy_enabled`) that reuses
the backtest predicate verbatim and proposes through the existing `propose_trade` chokepoint (long-only,
held-position skip, adversarially reviewed). **Strategies are isolated, independently toggleable, FROZEN
modules**; the learning layer observes outcomes but never mutates a strategy. The **EXIT module below (#1) is
the next increment** — it completes the round-trip and is required before any live EV-vs-backtest comparison.

**Update (2026-06-22) — Strategies UI + refined-dip + mobile Settings.** The registry now has a **cockpit
panel** (Settings → Strategies): per-strategy enable / account-bind / notional / exit-mode / `far_support_min`,
save-per-card, with a no-fabrication honesty caption on the refined variant. Settings is now reachable on
**mobile** (phone-header gear opens the reused dialog; nav stacks responsively). A second strategy
**`dip_refined`** ("Dip-Buy + S/R filter") is seeded **disabled + unbound**: the dip edge + a swing-pivot
"≥5% above nearest support" filter (`research/levels.py` ported from `research/decision-engine-data-layer`).
⚠️ Its "+1.6%→+3%/trade" backtest gain is **recent-excess, NOT block-bootstrap-validated** like the base dip —
a forward-test candidate only. 510 tests green; UI typecheck/lint clean; **UI pending on-phone visual check.**

**Update (2026-06-26) — Per-strategy paper AUTO-ENTRY (`feat/strategy-auto-entry`, PR-pending).** The entry-side
mirror of auto-exit: a strategy can be toggled `entry_mode="auto"` to auto-execute its entries through the
existing chokepoint (`ExecutionService.submit` → `RiskEngine.validate` — unchanged), **paper-fenced** (honored
only in `dry_run`/`paper`; reverts to `propose` under `live`; `session_node_spy` blocked), with a "Trade
executed" phone push on every fill (entry AND auto-exit). `record_strategy_fill` recovers the strategy from a
synthetic `auto_entry|<id>|<ts>` proposal_id so the auto-entered position is tracked + managed/exited normally.
Sizing is a shared helper refactored out of `propose_trade` (identical buying-power cap + risk path). Adversarial
review (standing rule) caught + fixed a double-submit window — new `recent_open_buy_exists` in-flight guard
(15-min) is the entry-side analog of the exit engine's pending marker; `auto_execute_entry` is also self-fencing
(re-asserts paper-only + refuses non-`strategy:` sources). Default `entry_mode='propose'` → **zero behavior
change until a founder flips a toggle.** 879 tests; ruff + typecheck clean. PR for the founder to merge; then
flip `dip_buy`/`dip_refined`/`earnings_drift` to paper-auto.
⚠️ **Alert design (founder/PC, 2026-06-26):** per-trade pushes are correct for these tactical sleeves (rare,
each fill worth knowing), but the future **momentum/rebalance book** (Sharpe-1.42, separate later promotion) can
fire 40+ fills/hour → alert fatigue → user ignores pushes → misses the one that matters. Build a per-strategy
`alert_style` (`per_trade` | `summary`, mirroring `entry_mode`) and emit ONE summary push for bulk-rebalance
strategies AT THAT PROMOTION (not now — no bulk strategy is live). Today's mitigation: `max_concurrent_positions`
caps bound any burst.

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

```
Brokers (Alpaca, Schwab) ──► db/ (SQLite) ──► api/ (FastAPI)
                              │                    │
                              ▼                    ▼
                         trade ledger          Next.js UI
                         snapshots             /agents chat
                         memories              /performance
                              │
                              ▼
                         ai/ (LLM, research, rules, reflection)
```

**Layers that work — extend, don’t replace:**

| Layer | Role |
|-------|------|
| `brokers/` | `BrokerAdapter` protocol; frozen dataclasses; multi-broker |
| `db/` | Snapshots, trade ledger, chat, memories, rules, alerts |
| `ai/llm/` | Provider factory (mock, OpenAI, Anthropic, Ollama, …) |
| `ai/research/` | LangGraph plan → collect → analyze → synthesize |
| `trading/` | Broker sync today; **RiskEngine + ExecutionService** later |
| `api/` | REST + SSE + WebSocket market hub |
| `frontend/` | App shell, trading, performance, agents, settings |

**Chat architecture decision (locked):** Move the **conversation host** to a Next.js route (`app/api/chat/route.ts`) using Vercel AI SDK `streamText`. Python remains the **brain** — portfolio, memory, research, rules, execution. Node never places orders; it only calls authenticated Python endpoints.

**assistant-ui + the two reference examples (locked, 2026-06-16):** Adopt **assistant-ui** for the chat UI via the **AI-SDK runtime adapter** (`@assistant-ui/react-ai-sdk`), *not* the LangGraph adapter — it replaces the current `useChat` + `ai-elements` layer. The [mem0](https://www.assistant-ui.com/examples/mem0) and [stockbroker](https://www.assistant-ui.com/examples/stockbroker) demos are taken as **patterns, not dependencies**:
- **Memory (mem0 pattern):** mirror the extract → store → semantic-retrieve → inject-every-turn loop against **Python** (`/api/context`, `/api/memory/extract`). Reject the hosted Mem0 cloud SDK in Node (3rd store + 2nd secret home + data exfiltration). Self-hosted `mem0ai` Python is the only acceptable Mem0 path.
- **Human-in-the-loop (stockbroker pattern):** build the generic Approve/Reject tool card (AI SDK `requires-action` flow) as **UI groundwork in Phase 3**, but **live trade execution is deferred to Phase 4** (RiskEngine gate). Reject the LangGraph *chat* runtime; revisit only if durable, resumable multi-step workflows become a real need.

---

## 3. Current state (as of 2026-06-16)

### ✅ Shipped and working

| Area | What exists |
|------|-------------|
| **Brokers** | Alpaca + Schwab adapters; settings-driven enable; paper primary |
| **Polling** | Multi-broker poll; resilient loops + `poll_health`; WAL SQLite |
| **Auth** | `API_TOKEN` on API/WS; Host-header check; frontend `X-API-Token` |
| **Market** | Shared WebSocket hub; price alerts; chart grid |
| **Trade ledger** | Schwab JSON import; Alpaca/Schwab API sync on connect + hourly; dedup by `external_key` |
| **Performance** | `/performance` — periods, FIFO round-trips, calendar w/ $ labels, realized vs cash-flow toggle, charts |
| **AI tools** | `get_portfolio_snapshot`, `search_memories`, `start_research` (async job), `get_trade_history`, `get_performance_insights` |
| **LLM hygiene** | Shared `parse_json_object`; JSON planner for tools (not keyword-only); research job queue |
| **Chat** | Next.js `app/api/chat/route.ts` + AI SDK `streamText`; **assistant-ui** Thread (`useTradingChatRuntime`); Python brain (`/api/chat/context`, tools, finalize); Saved + Temporary modes; `propose_order` Approve/Reject UI |
| **Memory loop** | `POST /api/memory/extract`; client `onFinish` is the single extraction trigger (server finalize + legacy `/api/chat` use `extract=False`); inline **“Learned from this chat”** chips (Edit/Discard) under assistant turns |
| **Tests** | pytest: 74 tests — auth, db/WAL, poll health, tools, portfolio + intelligence P1 + chat brain + memory extract |

### ◐ Partial / known gaps

| Area | Status |
|------|--------|
| **Chat** | ✅ Native tool-calling works — fixed the `enabledTools: []` default that left the model with no tools (it fabricated); all tools on by default. Anthropic routed through the native path. **Learned chips rehydrate** on reopening a saved thread (C4) + Copilot **thread-history** switcher (pin / rename / delete). Legacy Python SSE proxy now only for `mock`. |
| **Research** | Mock web clearly labeled + UI banner when `SEARXNG_URL` unset; article `content` fed to findings; SearXNG failures no longer silently mock |
| **Insights** | `portfolio/analytics.py` on poll + relevant memories injected each refresh |
| **Memories** | Token-ranked retrieval on chat/insight/research turns; visible turn curation UI shipped; Mem0 when available; dual store drift remains; chip state not rehydrated on reload |
| **Rules** | Rich facts; `trigger_on: transition` + cooldown; WS `alert` events on fire |
| **Money** | Still `float` in broker types and ledger (Decimal planned) |
| **Secrets** | Masked on API; **plaintext on disk** (encryption not done) |
| **Execution** | Read-only — no `RiskEngine`, no `place_order` |

---

## 4. Guiding principles

1. **Safety before speed** — No live `place_order` until `RiskEngine` + audit log + auth exist.
2. **Ground before generate** — LLM sees analytics + memories + trade performance, not raw JSON dumps.
3. **One ledger** — Import (full history) + API sync (incremental) → deduped `trade_events`.
4. **Realized ≠ cash flow** — Performance UI distinguishes closed-trade P&L from buy/sell cash movement.
5. **Incremental phases** — Each phase leaves the app runnable.
6. **Tests ride along** — pytest per phase, not a final bolt-on.

---

## 5. Roadmap

Phases are ordered by dependency. Effort: **XS** &lt;1d · **S** 1–2d · **M** 3–5d · **L** 1–2w.

### Phase 0 — Platform hardening (mostly done)

| Item | Status | Notes |
|------|--------|-------|
| Poll/alert loops never die silently | ✅ | `try/except` in `_poll_loop`, `_alert_symbol_loop`, `_trade_sync_loop` |
| SQLite WAL + busy timeout | ✅ | `db/base.py` |
| `/health` poll staleness | ✅ | `poll_health` |
| API token + Host + CORS | ✅ | `api/auth.py`, `SecurityMiddleware` |
| Centralized JSON parsing | ✅ | `ai/llm/structured.py` |
| LLM tool planner (not keywords) | ✅ | `plan_tools()` in `ai/chat/tools.py` |
| `start_research` async job | ✅ | `ai/research/jobs.py` |
| Core pytest | ✅ | 8 test modules |

**Remaining from Phase 0–1:**

| Item | Status | Effort | Files |
|------|--------|--------|-------|
| Atomic settings writes + lock | ✅ | S | `config/settings.py` |
| Settings + broker adapter cache | ✅ | S | `config/settings.py`, `brokers/factory.py` |
| Persistent `httpx` client on Alpaca | ✅ | S | `brokers/alpaca.py` |
| GitHub Actions CI (ruff + pytest) | ✅ | S | `.github/workflows/ci.yml` |
| Offload remaining sync broker I/O (`to_thread` / async) | ✅ | S | `api/main.py`, `brokers/market_stream.py` — poll-loop DB writes + market-stream alert check wrapped in `asyncio.to_thread` |
| Encrypt secrets at rest | ◐ | M | `config/secrets.py`, `brokers/schwab_auth.py` |

---

### Phase 1 — Trade history & performance (mostly done)

| Item | Status |
|------|--------|
| `trade_events` ledger + import + dedup | ✅ |
| Schwab JSON parser | ✅ |
| Alpaca/Schwab sync | ✅ |
| FIFO round-trips, daily stats | ✅ |
| `/performance` UI + API | ✅ |
| Realized vs cash-flow toggle | ✅ |
| `get_trade_history` + `get_performance_insights` tools | ✅ |

**Remaining:**

| Item | Effort | Notes |
|------|--------|-------|
| Mark-to-market unrealized (open lots vs snapshot) | M | ✅ shipped — `compute_open_lots` / `compute_unrealized` in the AI bundle |
| Profit factor, hold time, behavioral flags for AI | M | ✅ shipped — `db/analytics.py` enrichment (profit factor, hold time, win/loss streaks, behavioral flags) in the AI bundle |
| Decimal in ledger (align w/ Phase 2) | M | See Phase 2 |
| Multi-account merge UX | S | "All accounts" vs per-broker |

---

### Phase 2 — Intelligence layer (P0 for “feels real”)

The gap across insights, research, chat, and rules: **raw or fake data in, nothing feeds back.**

```
Data adapters → Intelligence layer → Insights · Chat · Research · Rules
                      │
                      ├─ portfolio analytics (exposure, day P&L, flags)
                      ├─ trade performance bundle (done — extend)
                      ├─ semantic memory retrieval
                      └─ rich fact catalog for rules
```

| Priority | Item | Status | Effort |
|----------|------|--------|--------|
| **P0** | `portfolio/analytics.py` — compute on poll; feed insights + chat context | ✅ core | M |
| **P0** | Stop silent mock research; loud “no real source” UI; wire SearXNG or news API | ✅ labeled + banner; SearXNG wiring unchanged | S |
| **P0** | Pass fetched `content` into findings (not just snippets) | ✅ | S–M |
| **P0** | Semantic memory top-k on **every** chat/insight/research turn | ✅ token-ranked; embeddings later | M |
| **P1** | Snapshot diff / time-series (“exposure up vs last week”) | ✅ 1d/7d drift + equity series | M |
| **P1** | Programmatic citations from findings→source map | ✅ | S |
| **P1** | Expand rule facts (per-symbol, day P&L, quotes) | ✅ | M |
| **P1** | Rule `trigger_on: transition` + cooldown; unify alert bus → WS | ✅ | M |

---

### Phase 3 — Chat copilot rebuild

**Decision:** Next.js `app/api/chat/route.ts` + AI SDK `streamText` + **assistant-ui** (AI-SDK runtime adapter). See §2 for the assistant-ui / mem0 / stockbroker decision.

| Step | Item | Status | Effort |
|------|------|--------|--------|
| A | Error visibility + don’t silently fall back to ephemeral mode | ✅ | XS |
| B1 | Route handler + `toUIMessageStreamResponse` | ✅ | M |
| B2 | Provider/key fetched from Python per request (single secret store) | ✅ | S |
| B3 | Tools call Python HTTP; legacy `UIMessageStream` kept for mock/anthropic fallback only | ◐ | M |
| B4 | Modes → **Saved** + **Temporary** (saved turns extract via client `onFinish`) | ✅ | S |
| B5 | **assistant-ui** Thread UI; Approve/Reject for `propose_order` (no execution); legacy `agent-chat.tsx` removed | ✅ | M |
| C1 | `POST /api/memory/extract`; client-only extraction (`extract=False` on finalize + legacy `/api/chat`) | ✅ | S |
| C2 | Visible **“Learned from this chat”** chips under assistant turns (Edit/Discard) | ✅ | M |
| C3 | Python-backed context on turns (`/api/chat/context`, finalize) | ✅ | M |
| C4 | Rehydrate learned chips when reopening a saved thread | ✅ | S |

**Feedback loop (Phase 3–4):**

- `strategy` memory type from brainstorming
- Visible “Learned from this chat” curation after turns (in-session; reload persistence in C4)
- Agent proposes rules (confirm-to-save)
- Reflection ties outcomes back into chat

---

### Phase 4 — Money & safety (before live trading)

| Item | Effort | Notes |
|------|--------|-------|
| `Decimal` on broker types + parse boundary | M | `brokers/base.py`, adapters |
| Store money as TEXT in SQLite | M | snapshots + ledger |
| JSON API serializes decimals as strings | S | `api/serialize.py` |
| **`RiskEngine` + `OrderIntent` + audit log** | L | `trading/risk.py`, `trading/audit.py` |
| **`ExecutionService.submit()`** — sole order path | L | `trading/service.py` |
| `place_order` on Alpaca only, behind gate | M | `brokers/alpaca.py` |
| `trading_mode` paper/live + UI banner | S | settings + frontend |
| Agent `place_order` tool → ExecutionService only | M | never broker directly |
| **Light up Phase 3 Approve/Reject card** → `propose_order` renders card; approve → gated `ExecutionService` (stockbroker pattern, fully realized) | M | depends on RiskEngine above |

```
UI/Agent → OrderIntent → RiskEngine.validate() → approve | reject | needs_confirmation
                              → BrokerAdapter.place_order() (if approved)
                              → AuditLog
```

**Do not** wire a buy button until this phase is complete.

---

### Phase 5 — Product polish & scale

| Item | Effort |
|------|--------|
| Split `api/main.py` into routers | M |
| Pydantic request models on all routes | M |
| Versioned DB migrations (`schema_version`) | M |
| Market-hours-aware polling | S |
| Remove legacy HTML dashboard at `/` | XS |
| SSRF guard on `fetch_url` ✅ | S |
| Mem0/SQLite → one memory store + embeddings | M |
| Backtesting harness (bars + metrics) | L |
| True `astream` for insights/reflection | S–M |

---

### Phase 6 — Shell & dashboard reimagining (web-first)

The big UX pivot toward the "Hermes" vision. No desktop shell required yet. Detail + rationale: [`IDEA_BANK.md`](IDEA_BANK.md) §1, §3, §9 Tier 1.

| Item | Effort | Notes |
|------|--------|-------|
| Bottom command console v1 | M | Port `References/hermes-console-draft.html` into Next; `/` commands + natural-language ask; pages reachable as console views |
| Theme system + config themes | M | Design tokens + shipped themes + user theme **config file** (Warp/Ghostty matte) |
| Widget dashboard v1 | L | Widget catalog, drag/move/resize grid, default template, **per-user JSON layout file** |
| **Mobile shell (bottom-tab app)** | M | ✅ **v1 SHIPPED** — `/workspace` on phones (`<768px`) renders a dedicated `MobileWorkspace` (bottom floating **pill** tab bar: Watch / Portfolio / Agent / Activity) instead of the desktop grid, reusing existing widgets + the copilot thread + notification store. Desktop keeps the drag grid. Founder reaction: "good first pass." **Deferred refinements:** (1) richer per-tab layouts / curated content; (2) brief desktop-layout flash before the mobile breakpoint resolves (SSR `useIsMobile` starts false); (3) Activity tab → R3 phone-approval home. |
| First-run onboarding + agent charter | M | Name the agent; write charter `.md`; seed memory (§2.1) |

**Noted future ideas (Phase 6/7):**
- **Tabbed widgets (browser-style).** A `+` on a widget (esp. **chart**) to add multiple instances (e.g. 4–5 tickers) and tab between them in one tile — like browser tabs. Founder request 2026-06-20.
- **No-overlap is a hard rule.** Widgets must never sit on top of each other. (A 2026-06-20 regression — lowering the grid column floor to fit narrow desktops clamped widgets into collisions — was reverted: the floor stays at `GRID_DEFAULT_COLS` since phones now use the mobile shell, so the desktop grid never narrows enough to clamp.)

### Phase 7 — Proactive agent, tasks, notifications & gateway

The agent comes alive and becomes reachable. Detail: [`IDEA_BANK.md`](IDEA_BANK.md) §2, §4, §7.6, §9 Tier 2.

| Item | Effort | Notes |
|------|--------|-------|
| Headless core + gateway + auth | L | Deployable service; gateway auth (tokens/TLS/device pairing); assume remote clients |
| Secrets-at-rest encryption | M | Graduates from Phase 0 — required once reachable beyond localhost |
| Proactive engine + Tasks scheduler | L | Background watch loop; scheduled prompts (7AM/4PM); event triggers |
| Notifications (in-app + Telegram/Discord interactive) | M | ✅ **in-app SHIPPED** — fired rule/scan `{type:"alert"}` frames over `/ws/market` now surface as a top-right slide-in **banner** + a top-bar **bell** with unread counter + dropdown history (backend ring-buffer `/api/alerts/recent` so a fresh tab shows recent fires). OS notification when tab unfocused. Telegram/Discord interactive + per-channel config still pending. |
| Chart-level monitoring | M | Draw-on-chart + plain-language → watchable conditions |
| Source scripts (X/Reddit via LLM search) | M | Crafted-search ingestion; prototype reliability first (no paid APIs to start) |

**Signal & Automation Engine (the substrate — build first within Phase 7).** Detail: [`IDEA_BANK.md`](IDEA_BANK.md) §7.10–7.12. Most of this is backend / **pytest-verifiable** (remote-friendly); node-UI + chart drawing need visual review.

| Item | Effort | Notes |
|------|--------|-------|
| Indicator/feature layer | M | ✅ **SHIPPED** (#13 `signals/indicators.py` + #14 `signals/features.py` facts-builder). The vocabulary; extends the existing rules engine's facts. |
| Declarative node-graph rule model + validator | M | ✅ **SHIPPED** (#16 `signals/rule_schema.py` — validated structured rules that **compile to the existing engine's condition dict**, never `exec`'d code). |
| Evaluator: streaming + scheduled scan | M | ✅ **scheduled scan SHIPPED** — `signals/scan.py` + `_signal_scan_loop` (watchlist ∪ positions → daily bars → indicator+portfolio facts merged → `evaluate_rules` → `broadcast_alert`; **verified firing live** off real Alpaca data). Per-tick **streaming** evaluation still pending. |
| Backtest runner | M | ✅ **SHIPPED** (#15 `signals/backtest.py` — replay a rule over history → fire events). |
| AI confirm-to-save authoring (`propose_rule`) | M | ✅ **SHIPPED** — copilot turns NL ("alert me when NVDA crosses its 200-day") into a validated `RuleSpec` → Approve/Reject card → on approve, armed via `create_rule`. Mirrors `propose_order`; registered in both the agents page + workspace copilot. A rule is **not** a memory — alert-intent turns skip learned-memory extraction. |
| Node-based visual builder | L | Langflow/n8n-style canvas — visual editor over the same graph (UI; needs eyes) |
| Default presets (clearable) + AI playbook | S | Seed rules/scans as data; power user clears |
| Chart overlay model + Lightweight-Charts expansion | M–L | Decoupled overlay model (drawings/alert-lines/studies/markers); LWC renders; bidirectional draw↔rule (§7.11) |

Declarative-not-code is the locked safety call (no AI-generated `exec`); sandboxed scripting only as a far-future, gated escalation.

### Phase 8 — Desktop shell (Tauri) & multi-window

Detail: [`IDEA_BANK.md`](IDEA_BANK.md) §7.1, §7.6, §9 Tier 3.

| Item | Effort | Notes |
|------|--------|-------|
| Tauri shell as thin client of the core | L | Bundle Next + Python sidecars; window → gateway |
| Multi-window pop-outs | M | Chat / watchlist / chart in separate windows |
| OS integration | S–M | Native notifications, sound, tray, autostart |

---

## 6. What to build next (recommended order)

**Superseded by the Realignment sequence (R1–R5) near the top of this doc** — the loop-centric plan is now the source of truth for ordering. **Start with R1 (import → retrospective LEARN):** zero execution risk, immediately valuable, and it bootstraps the agent's model of the user from years of real Schwab data.

Mapping the legacy phases onto the loop: R2 = old **Phase 4** (Money & safety / RiskEngine + ExecutionService); R3 = old **Phase 7** notifications, extended to phone; R4 = the feedback half of Phases 3/7; R5 = data depth (Phase 2) + the durable agent loop. The Tier 0 "door-open seams" (route all orders through the execution gate; backend as headless core; storage behind `db/`; feed-agnostic strategy boundary; design-token themes) are still honored continuously. The legacy Tier/Phase detail below is retained for historical context and the shipped-state record.

---

## 7. Key modules (quick reference)

| Concern | Path |
|---------|------|
| App entry | `api/main.py` |
| Trade ledger | `db/trades.py`, `imports/`, `trading/sync.py` |
| Performance analytics | `db/analytics.py`, `api/performance_routes.py` |
| Portfolio analytics | `portfolio/analytics.py`, `db/sqlite.py` (`snapshot_analytics`) |
| Chat | `frontend/app/api/chat/route.ts`, `frontend/lib/use-trading-chat-runtime.ts`, `frontend/components/assistant-ui/thread.tsx`, `ai/chat/brain.py` |
| Memory extract + curation | `api/ai_routes.py` (`POST /api/memory/extract`), `frontend/lib/memories.ts`, `frontend/lib/learned-memories-context.tsx`, `frontend/components/agents/turn-learned-memories.tsx` |
| Tools | `ai/chat/tools.py` |
| Research | `ai/research/citations.py`, `ai/research/deep_graph.py`, `ai/research/sources.py` |
| Rules | `ai/rules/engine.py`, `brokers/market_stream.py`, `frontend/lib/rules.ts` |
| Settings / brokers | `config/settings.py`, `brokers/factory.py` |
| Frontend API client | `frontend/lib/api.ts`, `frontend/lib/performance-api.ts` |

---

## 8. Success criteria (release gates)

### Platform

- [ ] SSE/WS responsive during broker polls
- [ ] Poll loop survives injected failures; `/health` reports stale data
- [ ] All API routes reject unauthenticated requests when `API_TOKEN` set
- [x] `pytest` in CI on every push

### Copilot

- [x] Chat streams tokens from AI SDK route (legacy Python SSE proxy remains for some providers)
- [ ] Every turn gets analytics + top-k memories (no keyword triggers)
- [ ] Research never presents mock web as real; articles are read, not skimmed
- [ ] Agent answers “how am I doing?” via `get_performance_insights`, not guessing

### Trading safety

- [ ] No `float` in `brokers/base.py` money fields
- [ ] `POST /api/orders` cannot bypass `RiskEngine`
- [ ] Append-only audit log for every order intent

---

## 9. Explicitly out of scope (for now)

- Schwab order execution (Alpaca paper first)
- LibreChat / Open WebUI adoption (UX inspiration only)
- assistant-ui **LangGraph** runtime (`@assistant-ui/react-langgraph`) + hosted **Mem0 cloud** SDK — patterns adopted, dependencies rejected (see §2)
- Frontend `decimal.js`
- Full Alembic (lightweight `schema_version` is enough)
- **Un-guarded** autonomous trading. Bounded auto-execution IS the end goal — but only inside the RiskEngine's hard limits + kill switch, reached by climbing the autonomy ladder (alert → propose/approve-on-phone → bounded auto). Nothing auto-executes until R2's fail-closed gate exists.

---

## 10. Local dev

```powershell
# API
cd help_me_trade
.\.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend
bun run dev   # port 3001
```

Match `API_TOKEN` in `.env` and `NEXT_PUBLIC_API_TOKEN` in `frontend/.env.local`.  
If port 8000 is in use: `netstat -ano | findstr ":8000"` then `taskkill /PID <pid> /F`.

---

## 11. Document history

| Date | Change |
|------|--------|
| 2026-06-26 | **Mobile chat copilot — RN client built + verified on-device (the Chat tab is real now).** Replaced the "coming soon" placeholder with a full streaming copilot on `feat/mobile-app`: `mobile/src/api/chat-stream.ts` (`streamChat()` async-gen — SSE over **`expo/fetch`**, which on Expo 56 exposes a real `ReadableStream` body, de-risking the RN-streaming concern the brief flagged; parses one typed JSON event per `data:` line), `mobile/src/features/chat/{use-chat.ts,chat-bubbles.tsx,chat-screen.tsx}` (growing assistant bubble, typing/tool-hint affordances, empty-state suggestions, keyboard-avoiding composer lifted clear of the floating tab pill, send↔stop). **Mission rule honored:** chat NEVER approves — a `proposal` event renders a hand-off card → `useNav().openInbox()` → the existing Face-ID Approvals flow (`proposal-card.tsx`); the pending proposal is already in `GET /api/proposals/pending`. Pure JS, no new native deps → Metro Fast-Refresh, no rebuild. tsc + eslint clean (new files). Founder verified on device ("it works, I love the UI"). **Runs on a local stub** (`mobile/src/config.ts` → `CHAT_STUB`) because serv's `POST /api/chat/stream` is **404 — not built**; go-live is the one-line `EXPO_PUBLIC_CHAT_STUB=0` flip once it ships. **Handed to serv:** `References/SERV_BRIEF_chat_stream.md` — build the route emitting the SIMPLE typed events (reuse `run_chat_stream`/`finalize_chat_turn`); ⚠️ safety-critical: the existing `propose_order` tool (`ai/chat/tools.py:~675`) records the proposal AND immediately calls `ExecutionService.submit()` — the mobile endpoint must create a PENDING proposal and STOP (execution only on the phone's Face-ID approve), with adversarial Sonnet review. Contract = `References/MOBILE_CHAT_BRIEF.md`. |
| 2026-06-25 | **★ Agent perception layer (phases 1–3) — the copilot can now see ALL our data + act on a few safe things.** Prompted by the copilot web-searching when asked to "set alerts at our volume-profile levels" — it was blind to our own signals. Founder direction: don't add a specialty tool per question; build a GENERAL read layer + a NARROW curated write surface (`References/PERCEPTION_LAYER.md`, memory `agent-perception-layer`). Built in `ai/perception/`: **(1) `get_context(symbol)`** — one normalized facts object from existing sources (quote, RVOL, session-node levels, technicals incl. vs-200-DMA, fundamentals, earnings, the calibrated `decision_scorer` win%/EV, active alerts); **(2) `query(resource, filters)`** over a one-entry-per-resource registry (watchlists/strategies/price_alerts/proposals/reports) + a `list_capabilities()` manifest; **(3) curated WRITE tools** — `create/update/delete_price_alert` (notifications, not orders → execute directly, no approval card; chokepoint untouched). Each tool registered in BOTH surfaces (Python `ai/chat/tools.py` + live-chat frontend `chat-tools.ts`/`chat-toolbar-config.ts` — a phase-1 gotcha now documented). **Maintenance contract** (code+doc+memory): new per-symbol signal → add to `get_context`; new entity → one `RESOURCES` entry; never a specialty tool. Verified live: copilot answered NVDA's session-node levels from us (not web search); `query` returned the enabled strategies; create→query→delete alert round-trip through `/api/chat/tools/execute`. Same facts object is the Decision Engine's PERCEIVE half — build once, two consumers. 866 tests, ruff+typecheck+build clean; backend+frontend redeployed. |
| 2026-06-25 | **Price alerts: manage in the widget + set them by clicking the chart (founder items 2 + 4).** Alert plumbing already existed (the `price_alerts` table has `direction` above/below/cross + a `repeat` persistence flag; the chart already DREW alert lines; chart/widget/phone all read one shared table) — what was missing was the create/edit/manage surface. Added: (1) **backend edit** — `update_price_alert` (`db/chart_features.py`, editing re-arms `triggered=0`) + `PUT /api/price-alerts/{id}` (validates direction; 404 on missing); (2) **price-alerts widget CRUD** — a **＋ Alert** button → inline create form (symbol·price·direction-segmented·persistent·note), per-row **edit/delete** on hover, refreshes immediately; new `updatePriceAlert` API client; (3) **chart click-to-create** (`tv-light-chart.tsx`) — a floating **＋ Alert** button arms a mode; clicking a price level opens a clamped popover (price prefilled via `coordinateToPrice`, direction defaulted by clicked-vs-current price, persistent toggle) → `createPriceAlert` → the dashed line draws immediately; stale-closure-safe via refs. **Cross-surface sync:** the chart's alert fetch now keys on a refresh counter bumped on create + a 20s poll, so alerts made in the widget/phone appear on the chart and vice-versa (all via the one shared table); the phone still gets the APNs push when an alert fires. Verified live end-to-end (create→list→edit→delete via the API; PUT round-trip). Frontend typecheck+build clean, 850 backend tests, ruff clean; backend + frontend redeployed. **Still pending (broader, HANDOFF #1):** the persistent notification STORE + clickable bell + phone alert *inbox*. |
| 2026-06-25 | **Forward earnings calendar → Alpha Vantage (fixes blank mega-cap next-earnings, e.g. AAPL).** Finnhub's free tier silently omits mega-caps' forward earnings (AAPL/MSFT/GOOGL/AMZN/META returned nothing — verified across a 582-row uncapped window), so `next_earnings` was blank for them in the watchlist/company-info UI and absent from the `earnings_drift` universe. Schwab has no `nextEarningsDate` field; Yahoo now needs an auth crumb. Added **Alpha Vantage** (`signals/alphavantage.py`, `ALPHAVANTAGE_API_KEY`) whose `EARNINGS_CALENDAR` returns the WHOLE forward calendar in ONE call (~10k rows incl. mega-caps) — more robust AND simpler than Finnhub's chunked ≤30-day-window/1500-row-cap dance. AV is now the **primary forward** source; Finnhub stays for PAST dates (last-earnings) + the EPS-surprise history graph (untouched). `earnings_refresh._fetch_calendar_rows` runs both (fail-soft — one source failing doesn't break the other), filters to universe, upserts into the same `earnings_calendar` table; refresh guard now proceeds if EITHER key is set. Free tier (25/day) is ample — the refresh is ~1 call/day. Verified live: AAPL → 2026-07-30, NVDA → 2026-08-26, etc.; `/api/watchlist/market` serves AAPL `next_earnings: 2026-07-30`. 850 tests, ruff clean; backend redeployed. (TA-indicator overlap was why AV-over-FMP for now; FMP reserved for when the Decision Engine needs its paid fundamental depth — transcripts/analyst/institutional.) |
| 2026-06-25 | **Chart/watchlist UX round: live intraday candle rollover, sortable watchlist columns, fixed symbol box, cold-switch prewarm.** Four founder-reported bugs + a perf fix. (1) **Live candle** — intraday charts never started a NEW candle at an interval boundary (the live-price effect only mutated the last bar's close), so 5m looked frozen until a timeframe switch forced a refetch; now rolls to a new bar when the wall clock crosses the bucket, aligned to the bars' grid via `last.time` (`timeframeSeconds()` helper; gated on `timeframe.intraday`). (2) **Watchlist sorting** — column headers (Sym/Last/Δ%/RVOL) are now click-to-sort (asc→desc→off, ▲/▼); client-side view-only, a manual drag clears the sort so it never clobbers the saved order, nulls sort last. (3) **Symbol box (unlinked chart)** — was fully controlled, committed every keystroke with `|| "SPY"`, so deleting to empty snapped back to SPY → you literally couldn't change the ticker (and each partial keystroke refetched); now a local draft committing on Enter/blur, freely clearable. (4) **Payload trim** — 1Min 10→5d (~9k→~4.6k bars), 5Min 30→20d, to cut the client parse/render freeze. (5) **★ Cold-switch prewarm** — a background loop (`_bars_prewarm_loop`) keeps `_BARS_CACHE` warm for the symbols you're likely to switch to (full watchlist + most-recently-viewed non-watchlist, capped 40) for the timeframes actually in use (captured from `/api/bars` requests, so it self-bounds Schwab cost; default-seeds 1Day/365), refetching ~25s before each entry's TTL so it never goes cold mid-session. Refactored `/api/bars` to share a recency-free fetch core (`_get_bars_cached`) so the loop can't self-refresh recency. Verified live: first-touch on a watchlist symbol = 1–3 ms (warm) vs ~0.4s cold; switching Tesla↔SPCX is now instant. 850 tests, ruff + typecheck + build clean; backend + frontend redeployed. |
| 2026-06-25 | **★ CRITICAL fix: autonomous proposals silently required a browser open (live-quote came only from the stream hub).** Debugging "why didn't my vol-node strat prompt today?" — SPY tagged prior-session VAL/PDL support at 9:54 ET (a real long reversion trigger), the scan detected all 4 of today's triggers and claimed their dedup keys, but created **0 proposals**. Root cause: `trading/risk_engine._live_market_price()` sourced the live quote ONLY from the in-process Schwab stream hub (`market_hub.quote_snapshot`), and that hub only subscribes a symbol while a **browser is actively viewing it** or an **active price alert** exists (the lone `ensure_symbols` caller; 0 price alerts) → no browser on SPY ⇒ hub miss ⇒ `None` ⇒ `RiskEngine.validate` "no live quote" / `_size_proposal_args` bails ⇒ no proposal, **silently** (claim-then-`forget` of the dedup key leaves no log/event row, which is why it looked like the loop never ran). This gated **every** market entry through `RiskEngine.validate` — session-node AND dip/earnings via `proposal_intake.propose_trade` — i.e. the agent only proposed when a browser streamed the symbol, directly defeating "works while you step away." **Fix (mission-correct, single shared point):** `_live_market_price` is now `async` and, on a hub miss, falls back to a fresh REST quote — `provider.aget_quotes` `regular_last`, then a **today-ET-dated** 5-min bar close — covering session-node/dip/earnings at once (3 call sites awaited). **RTH-only** (off-hours → `None`, so the chokepoint stays fail-closed and no stale prior-session close slips through) and **fail-soft** (never raises into `validate`; provider error → `None`). Two-round adversarial Sonnet review (SAFE TO COMMIT): caught + fixed off-hours-stale-price and Alpaca-daily-bar staleness (→ RTH gate + intraday 5-min bars + a today-date guard for the at-the-open window). **850 tests** green, ruff clean; live-verified `_live_market_price("SPY")` returns a price on a hub miss. On deploy, today's already-resolved SPY backlog (4 stale triggers) is dedup-seeded to avoid a stale-proposal flood; new touches fire in ~1 min. |
| 2026-06-25 | **Automations cockpit — new top-level `/automations` page.** One supervision hub for everything the agent does in the background, in the left nav (`Zap` icon). A **"Running now" overview** (`running-now.tsx` + `use-automations-overview.ts`) glances at active counts + last/next fire across categories (strategies enabled, rules + newest trigger, report schedule/next-8am, price alerts + last fire), then four sections that **reuse the existing functional components** so they're real controls, not read-only copies: **Strategies** (`SettingsStrategiesPanel embedded` — arm/configure), **Scans & Rules** (extracted a shared `RulesSection` from the orphaned `/rules` page → now reachable; `/rules` still renders it), **Reports** (schedule toggle + time + Generate now + recent), **Alerts** (price alerts; earnings/RVOL flagged as backend-only, no UI yet = v2). Decoupling was surgical (optional `embedded` prop on the strategies panel; `RulesSection` shared by both callers) — existing settings/`/rules`/widget usages unchanged. Frontend-only; typecheck + build clean. Future tenants the page is built to grow into: auto-execute/guardrails (autonomy ladder), earnings/RVOL alert surfaces, generic scheduled tasks, data-freshness status. In-browser visual check pending. |
| 2026-06-25 | **Fix: company-info "Last Earnings" stale (showed March after MU's June report) — Finnhub calendar row-cap + wrong source.** Two bugs. (1) **Source:** the watchlist/company-info "Last Earns" read **Schwab's `lastEarningsDate`** quote fundamental, which lags weeks (MU still 2026-03-18 after a 2026-06-24 report). Now `/api/watchlist/market` serves `last_earnings` from the **Finnhub earnings calendar** (the real report date) and falls back to Schwab only when the calendar is empty — fixes web AND phone (both read this field). (2) **Calendar row-cap:** Finnhub `/calendar/earnings` caps a response at ~1500 rows and returns a slice from one end, so our 165-day refresh window silently came back **all-August** (dropping every recent report incl. MU). `earnings_refresh` now fetches in **≤30-day chunks** (`_chunk_windows`) and merges, staying under the cap — verified MU now lands as `2026-06-24 amc` and `last_earnings_date("MU")` → 2026-06-24 (was missing entirely). Refresh runs on boot so the prod calendar repopulates on deploy. The EPS-surprise graph was already correct (it uses Finnhub `/stock/earnings`, a different endpoint). 846 tests. |
| 2026-06-25 | **Chart-switch lag fix (bars cache) + session-node on a dedicated 1-min loop.** (1) **Lag:** the Schwab reroute made every chart switch a per-symbol REST round-trip fetching ~8k intraday bars (ext-hours), uncached — flipping tickers felt slow. Added a short-TTL response cache on `/api/bars` keyed by (symbol, timeframe, days) — 1Min 60s / 5Min 180s / 15Min 300s / 1Hour 600s / 1Day 300s — so switching *back* to a symbol is instant; success-only (400/502 never cached); the live price still rides the WS stream so a slightly-stale historical window is invisible. (2) **Cadence:** session-node was bundled in the 5-min signal-scan loop, so a SPY node touch could be proposed up to 5 min late. Pulled it into its own `_session_node_loop` at **60s** (`SESSION_NODE_SCAN_INTERVAL_SECONDS`, min 20) — matches the 1-min bar granularity. Cheap: one small SPY 1-min fetch + trigger detection per cycle; the heavy daily-bar scans stay at 5 min. Still propose-only. 846 tests. |
| 2026-06-25 | **Session-node levels on the chart — "Levels" overlay (feature 1 of 3).** A "Levels" toggle on the price chart draws the prior-session volume-profile reference nodes (POC / VAH / VAL / PDH / PDL) as labeled, color-coded horizontal lines (support=green, resistance=red, POC=amber, prior H/L=muted) via lightweight-charts `createPriceLine` — so you SEE where the strategy will act, on any charted symbol. Shared foundation: extracted `_fetch_rth_sessions` + `compute_session_levels` (`signals/session_node_strategy.py`) reused by the live scan, the new `GET /api/session-node/levels?symbol=` endpoint (empty-200 on no data / BrokerError, never 500), and the upcoming morning report. **Market-aware session selection** (the subtle bit): the live scan references the *prior* session (today's is still forming), but display/report consumers want the latest *completed* session — mid-session that's the prior day, after-close/pre-market it's the session that just finished — keyed off `is_market_open()` so the overlay/report are never a day stale. Scan firing unchanged (behavior-preserving extraction, existing tests green). `tests/test_session_node_levels.py`. In-browser visual check of the lines pending. (Next: 2 = 8am morning levels report; 3 = automations/"what's running" panel.) |
| 2026-06-25 | **Fix: 1/5/15-min charts broke after the Schwab reroute — duplicate candles.** Switching `/api/bars` to Schwab exposed a Schwab parser bug: `pricehistory` returns **duplicate candles for the same timestamp** (notably with extended hours spanning day boundaries — 5Min/2d came back 596 bars / 440 unique = 156 dupes). lightweight-charts requires strictly-ascending **unique** times → `setData` throws → the chart renders nothing; daily had no dupes (different aggregation) so only intraday broke, on web AND phone. Fix: `_parse_candles` (`brokers/schwab_market_data.py`) now collapses to one bar per timestamp (last wins) then sorts. Verified 0 dupes / unique-ascending across 1Min/5Min/15Min/1Hour. **Also fixes a latent RVOL/volume-profile bug** — the dupes would double-count volume in 5Min profiles (the RTH-only path didn't dupe in practice, but the parser is now guaranteed clean for all callers). Regression test added. 839 tests. |
| 2026-06-25 | **Session-Node Reversion promoted from a hardcoded rule → first-class Strategy (cockpit UI).** It was a `rules`-table row armed via the alert-centric rules page; now it's a proper entry in the strategies registry, surfaced in Settings → Strategies with the same StrategyCard as `dip_buy`/`dip_refined` (enable toggle, account binding, notional, exit mode). The live scan (`signals/session_node_strategy.py`) now reads `enabled`/`notional`/`account_id` from the **strategy row** (`get_strategy`) instead of the rule + a constant — all other gates (market-open, agent-proposals-enabled, kill switch, pending cap, SPY-pending, prior-session) unchanged; still **propose-only** (only `create_phone_proposal`, never the chokepoint). Seeded **DISABLED** (`db/strategies.py`) — strategies always land unarmed, nothing runs in the background until the founder arms it. Removed the rule seed + a one-time `delete_rule("session_node_spy")` startup migration so it leaves the rules page (single source of truth = the strategy row); id stays `session_node_spy` everywhere (dedup/fills/proposal source). UI: per-strategy conditionals (hide universe picker → "Symbol: SPY", lock exit mode to propose, shorts-note pointing at the Risk-Limits toggle). **Adversarially reviewed (Sonnet) — SAFE**; fixed the one MINOR it flagged: a server-side guard in `api/strategy_routes.py` blocking a direct API PATCH of `session_node_spy` to `exit_mode="auto"` (propose-only enforced at the API, not just the UI). 831 tests, ruff + frontend typecheck clean. Founder arms it in Settings → Strategies for tomorrow's session (long-only unless Allow-short-selling is on). |
| 2026-06-25 | **Post-market (extended-hours) charts via Schwab — charts now show pre/post-market candles.** `/api/bars` was pinned to Alpaca's free IEX feed (RTH-only), so charts stopped dead at the 16:00 ET close — useless for the after-hours earnings moves the founder cares about (missed MU). Rerouted `/api/bars` through the Schwab provider (already the app's data source) with `extended_hours=True`, **with a graceful Alpaca fallback on any provider failure** so charts never hard-break. Schwab plumbing: threaded a keyword `extended_hours: bool = False` through `aget_stock_bars`/`_range`/`_batch`/`_fetch_bars_sync` (→ `need_extended_hours_data`, was hard-coded False) — **default False so RVOL/volume-profile/watchlist stay RTH-only** (load-bearing: the profile math assumes 78 RTH buckets; verified the default path returns 0 after-hours candles). Added **1Hour** support (Schwab has no 60-min frequency) via `_aggregate_to_hourly` resampling 30Min bars (open=first/high=max/low=min/close=last/vol=sum, hour-floored). Verified live: 5Min now returns to 19:55 ET (156 after-hours candles) vs 15:55 ET before; 1Hour aggregates through post-market; RVOL path unchanged at 15:55 ET / 0 ext candles. `MarketDataProvider` protocol + Alpaca accept the flag (Alpaca ignores — IEX is RTH-only). Read path only, no `trading/` touch; 826 tests (`tests/test_postmarket_charts.py`). **Frontend needs no change** (it plots whatever bars return) — in-browser ext-hours rendering (session gaps / 80%-anchor with the longer day) is a pending visual check. With this, **all OHLCV bars in the app now come from Schwab** (charts were the last Alpaca holdout); Alpaca keeps execution + the paper-account equity curve + the bars fallback. |
| 2026-06-25 | **CPU peg fixed — missing index on a poll-loop analytics query (101% → 2%).** A single core was pinned 24/7. Diagnosis took several wrong turns (the first instinct — "we re-fetch a year of bars every poll" — was wrong; verified daily `days=12` returns 8 bars not 252, and two speculative caches — sub-daily `bar_cache` + a short-TTL endpoint `response_cache` — moved CPU 0%, so both were reverted). A py-spy thread dump caught the real culprit: ONE thread spinning in `compute_and_save_snapshot_analytics` → `list_equity_history` (`db/sqlite.py`). That fires every 20s (poll loop, default account) and runs a 7-day equity JOIN of `poll_runs ⋈ account_snapshots`; `account_snapshots(poll_run_id)` had **no index**, so the JOIN was an O(n·m) nested-loop full-scan that grew with the DB — measured **43,623 ms** at 35k rows, overlapping every 20s into a permanent pool-saturating peg. Fix = one index: `idx_account_snapshots_poll_run ON account_snapshots(poll_run_id)` (+ `idx_position_snapshots_poll_run` for parity on the 86k-row table), added to `init_db` (idempotent, created on startup). Same query **43,623 ms → 18 ms (2,400×)**; live backend CPU **101.7% → 2.1%**. No `trading/` touch (snapshot/analytics read path only); 817 tests. **Lesson: profile before caching — every cache here was solving a non-problem; the bug was an unindexed FK join.** (The frontend's universe-sweep fetch storm the recon flagged is a separate, lower-priority I/O item, not the CPU peg.) |
| 2026-06-25 | **Live Activity (Dynamic Island) — full lifecycle VERIFIED end-to-end + envelope fix.** The phone-approval Lock Screen card now works start→finish, joint-verified with the mobile agent on a real device. **Root-cause fix:** serv was sending the trade fields flat at the top of `content-state`, but expo-widgets' generic `LiveActivityAttributes.ContentState` is `{name: String, props: String}` where **`props` is a JSON-ENCODED STRING (double-encoded), not a nested object** — so iOS Codable silently failed to decode → the activity dropped (`getInstances()=0`) even though APNs returned 200. New `_la_wire_content_state` (`signals/push.py`, `_LA_ACTIVITY_NAME="TradeProposalActivity"`) wraps the agreed flat fields as `{name, props:json.dumps(fields)}`; START + UPDATE/END all send this shape (`_la_content_state` unchanged — still the winProb-omission/statusReason builder, now the inner payload). **Joint verification PASSED all three legs on device:** START→`pending` (card rendered, Reject/Approve), UPDATE→`approved` (card flipped to "approving"), END→`executed` (terminal + ~10min dismissal, per-activity token auto-cleared). Contract LOCKED in `References/SERV_REPLY_live_activity.md`. `failed`-path deferred (same envelope, low-risk — verify on first real RiskEngine block). All wired into `trading/proposals.py` already, so real proposals drive the card automatically. `tests/test_live_activity.py` 17 (+1 wrapper). Commit `3742b91`. **The phone-approval unlock now has its Lock Screen surface working — the life-changing piece from the mission.** |
| 2026-06-24 | **Earnings on the web watchlist — at-a-glance row badge + panel countdown (discoverability).** Founder couldn't find company-info (it was the row's tap-to-expand fundamentals panel). Now the collapsed watchlist row shows an **earnings-soon badge** (`E·today`/`E·1d`/`E·{n}d`) when next earnings is within 14 days, color-banded (≤1d red / ≤3d amber / ≤14d dim, tinted pill), and the expanded panel's **Next Earns** gains a countdown (`{date} · {n}d`) + the same color emphasis. New `daysUntil` helper; frontend-only (`watchlist-widget.tsx`), uses the existing `next_earnings` in the `/api/watchlist/market` payload. Phone gets the same via `References/RN_EARNINGS_RELAY.md` (Mac-agent lane). Pairs with the earnings push notifications (`82a5c2d`). typecheck + eslint clean. |
| 2026-06-24 | **Short selling behind a paper-only toggle (relaxes the no-short invariant, carefully).** New `allow_short_selling` setting (default OFF) + `short_selling_allowed()` helper (`config/settings.py`) — honored ONLY in `dry_run`/`paper`; **LIVE short selling stays hard-blocked** regardless of the toggle. RiskEngine (`trading/risk_engine.py`) now permits a SELL with no held long as a **short-open** when allowed, and — critically — applies the **buy-style exposure caps (buying-power, per-order value, concentration) to short-opens** (sells were previously cap-exempt as position-reducing closes; a short INCREASES exposure). `RiskCheckResult.is_short_open` threads to the chokepoint so `execution_service` sends `position_intent="sell_to_open"`; build-time block in `order_proposal.py` relaxed in lockstep. Selling MORE than a held long is still blocked (no implicit close-and-flip); sell-to-close unchanged. Session-node strat proposes resistance-node **shorts** when enabled (`side="sell"`; `record_session_node_fill` accepts sell fills, still strictly `rule_id`-gated). Settings UI toggle (amber, paper-only caveat). **Adversarial Sonnet review caught a real MAJOR bug** — the equity short toggle also enabled **naked option writing** (sell-to-open an unheld option, violating options-long-only); fixed with an `is_option`/`occ_symbol` guard at both validate + build layers + a regression test. `tests/test_short_selling.py` (8: toggle-off blocks, paper allows, live blocks, value/concentration caps apply, close still works, oversell blocked, naked-option-write blocked). 800 tests, ruff + frontend typecheck clean. **Default OFF — founder enables it in Settings → Execution / Risk Limits.** |
| 2026-06-24 | **Watchlist earnings notifications (2-weeks-out / day-before / day-of).** Founder missed MU's after-market report — now a background loop (`_earnings_alert_loop` in `api/main.py`, env-gated `EARNINGS_ALERTS_ENABLED`/`_INTERVAL_SECONDS` default 6h, registered in lifespan) pushes a phone alert at three milestones for every watchlist symbol with an upcoming earnings date: **t14** (📅 in 2 weeks), **t1** (⏰ reports tomorrow), **t0** (🔔 reports TODAY). Non-overlapping ranges (t14 `1<d≤14`, t1 `0<d≤1`, t0 `d==0`) so a 6h cadence never skips a milestone; **AMC/BMO timing** appended when Finnhub has it (`earnings_hour` reads the stored calendar `hour`). Deduped one-per `(symbol, earnings_date, milestone)` via new `db/earnings_alerts.py` (`INSERT OR IGNORE`); keying on the date auto-re-arms when a new earnings date lands. Push carries `data={type:"earnings",symbol,earnings_date,milestone}` for phone deep-link. Per-symbol + whole-loop fail-soft. Uses existing `next_earnings_date` + `send_alert_push`. `tests/test_earnings_alerts.py` (6); 792 tests, ruff clean. (Web already shows next-earnings in the watchlist row's tap-to-expand fundamentals panel; phone next-earnings + countdown is a Mac-agent follow-up.) |
| 2026-06-24 | **Session-Node Reversion strat WIRED (propose-only/paper/SPY/DISABLED) — the daily app-test vehicle.** Landed the research closure via **PR #21** (cherry-pick: `research/{session_node_signal,intraday_bars,intraday_profile,volume_profile}.py` + tests, all-additions, zero blast radius; the PC agent renamed its `test_volume_profile.py`→`test_research_volume_profile.py` to avoid clobbering serv's RVOL test). Then wired the live half per `References/SESSION_NODE_STRATEGY_HANDOFF.md`: new `signals/session_node_strategy.py` (`run_session_node_scan`, registered isolated in `_signal_scan_loop`) computes SPY reference nodes from the prior RTH session + detects node-touch triggers on today's 1-min, and **proposes each NEW long trigger to the phone** via `create_phone_proposal` — machine on PERCEIVE, **human on DECIDE + EXIT**. Exit prompts (reached-POC trim / runner-extend / invalidation / RTH-close) are pushed as phone notifications (`send_alert_push` with deep-link `data`). New `db/session_node.py` (atomic `claim`/`forget` dedup + entry-context). **Safety (adversarially reviewed, Sonnet — all 10 invariants upheld with line-cited evidence):** propose-only (NO execution path — only `create_phone_proposal`; human Approve → existing chokepoint); arming rule **force-created DISABLED** (`create_rule` hard-forces `enabled=False` for `type:trade`; seeded only if absent, never re-enabled on restart); **no fabricated win%** (added `score=False` opt-out to `create_phone_proposal` → honest low-confidence structure tag, default True so existing callers unchanged); **long-only v1** (resistance-node shorts skipped + logged); `record_session_node_fill` strictly filters by `rule_id` so it no-ops on every non-session-node fill (`fill_tracker` calls it on all reconciliations); exit engine no-ops `session_node_spy` (`unknown_kind`, no auto-sell). Hardened the one MINOR the review flagged: dedup is now an **atomic claim-before-await** (`INSERT OR IGNORE`) so a deterministic re-detected trigger can't double-propose even under future concurrent callers. 786 tests (test_session_node_strategy.py +5), ruff clean. **Seeded DISABLED — founder arms it in the rules UI to run the daily app-test.** (Token-sync handoff item dropped — obsolete, headless auto-reauth already shipped.) |
| 2026-06-24 | **Chart: stable view on ticker/timeframe switch + adaptive time axis (`tv-light-chart.tsx`, `chart-time.ts`).** Changing ticker/candle-time used to zoom all the way out (`fitContent()` on every load). Now: a fresh chart (timeframe/style change) or first load shows a moderate recent window; a **symbol switch preserves the zoom (visible bar count) and re-anchors to the most recent candle** — earlier it restored absolute logical indices, which drift to older bars when the new ticker has a different history length ("goes back a week"). The **latest candle is anchored ~80% across** (20% right margin) on every load/switch, and the **price axis is force-refit** (`priceScale.applyOptions({autoScale:true})`) so a $700→$50 switch rescales vertically. Time-axis labels now use lightweight-charts' `tickMarkType` so they adapt with zoom (times→days→months→years; intraday day-boundaries land at session open) instead of formatting every tick the same. A `freshChartRef` distinguishes a recreate (timeframe/style) from a symbol change. Frontend-only; typecheck + build clean. |
| 2026-06-24 | **Workspace grid: growing/infinite canvas + server-side layout persistence + scroll/resize polish.** Three linked dashboard fixes. (1) **Cross-device/tab persistence** — new `db/workspace.py` (`workspace_state` KV) + `GET`/`PUT /api/workspace/layout`; the zustand store (`use-workspace-store.ts`) hydrates server→localStorage→default and saves debounced (500ms) to the server (localStorage kept as instant cache; seeds server from an existing local layout on first load). Single shared layout, last-write-wins, all fail-soft. `tests/test_workspace_layout.py` (9). (2) **Growing canvas** (`grid-metrics.ts`: `colsForLayout` = max(viewport-fill, rightmost-widget+8-col buffer), cap 240; dropped the window-width-derived col count + `clampLayoutWidth` squashing that caused **widgets to overlap differently per window size**). Widgets now hold absolute coords → identical layout on every device; drag a widget past the edge and the canvas extends with two-axis scroll. (3) **Scroll/resize polish** — four-edge gradient fade overlays (shown only when there's more content that way), hidden scrollbars (`.workspace-scroll`), 4-corner resize (`ne`/`nw` added in `globals.css`). **Fixed vertical scroll**: `WorkspaceGrid` root was `flex-1` under a non-flex (block) parent → it grew to content instead of being height-bounded, so nothing could scroll vertically (width was bounded, so horizontal worked); changed root to `h-full`. Typecheck + eslint (changed files) + build clean; 738 tests. |
| 2026-06-24 | **Price alerts: persistent (repeat) + crossing direction + APNs deep-link keys.** Alerts were one-shot only. Added a **`repeat` flag** (`db/chart_features.py` `repeat`/`last_triggered_at` cols + idempotent migration): persistent alerts re-arm after each crossing and stay until deleted; `mark_alert_triggered(repeat=)` stamps `last_triggered_at` but keeps them active. `api/alert_checker.py` honors it with a 60s cooldown (debounce tick-noise at the line). The **`cross` direction already existed** end-to-end (fires on a crossing either way) — `POST /api/price-alerts` now validates direction ∈ {above,below,cross} and accepts `repeat`. Web UI: a Repeat/One-shot toggle + persistent/`↻` status (`trading-page.tsx`, `price-alerts-widget.tsx`). `signals/push.send_alert_push` gained a `data=` arg → the chart-price-alert push carries flat `type:"price_alert"`/`symbol`/`alert_id` keys so the phone can deep-link on tap (RN relay §5). `tests/test_price_alert_repeat.py` (5). |
| 2026-06-24 | **Live WS streaming actually works (dropped-tick root cause) + price-alert delivery + 10 Hz throttle — the three Mac/RN serv items.** Root cause found by a live-stream probe: the Schwab WS never streamed a single tick to clients — the phone always fell back to the ~6-12s REST crawl. The stream was connected and delivering ~1 tick/sec/symbol, but `SchwabMarketStreamHub._on_equity`/`_on_futures` read each tick's fields by the numeric enum `.value` (`LAST_PRICE.value`=3) while **schwab-py relabels every content dict to the field MEMBER NAME** (`"LAST_PRICE"`) before the handler runs (`_Handler.label_message`). So `item.get(3)` was always `None` → `price is None → continue` → **zero broadcasts, ever** (symbol came through the literal `"key"` field, so it looked "connected"). Fix: read by `.name` (`_EQ`/`_FUT` constants); verified against the LIVE stream (AAPL 297.38/+1.05%, NVDA 201.36, SPY 739.52 populate). **Streaming frame** now carries `ext_price`/`ext_change_pct`/`market_session` (`QuoteState` + `_quote_payload`); `BaseMarketHub.apply_extended()` lets the REST watchlist poll write ext-hours pricing through to the hub so every broadcast rides with it. **Price alerts** now broadcast FLAT (matching the rule-alert shape — was nested under `alert:{}`, which rendered blank on the phone) and ALSO fire an **APNs push** (`send_alert_push` gained a `data=` arg → flat `type:"price_alert"`/`symbol`/`alert_id` custom keys for tap-to-deep-link). **WS broadcast throttle** (`BROADCAST_THROTTLE_MS=100`): coalesces hot ticks to ~10 Hz/symbol/client (first tick immediate, further ticks collapse into one trailing send that re-reads state — latest price wins), alerts unthrottled — keeps the phone battery sane under Schwab's 30-100 ticks/sec. Also hardened the sync handler (deprecated `get_event_loop()` → `get_running_loop()` + `_inflight_ticks` hard-ref set so the GC can't cancel a broadcast mid-run). New tests: `tests/test_schwab_stream_handler.py` (4, labeled-key regression lock) + `tests/test_price_alert_dispatch.py` (5, flat alert + push + apply_extended + throttle). 725 tests, ruff + frontend typecheck clean. Mac/RN relay: `References/RN_STREAMING_ALERTS_RELAY.md`. ⚠️ Schwab allows ONE stream session per login — a local probe transiently kicks prod (reconnects ~2s). Schwab stays READ-ONLY. |
| 2026-06-24 | **Watchlist RVOL column — "vol vs expected for this time of day" on real Schwab volume (LIVE).** New `signals/volume_profile.py` (+ `tests/test_volume_profile.py`, 7): pure `compute_profile` (78 RTH 5-min buckets → fraction-of-day normalize → **median** over a 20-trading-day window → renormalized cumfrac S-curve + median daily total; half-days excluded via a "last populated bucket ≥76" short-session filter) and `rvol_now` = `today_cum / (median_daily_total × cumfrac[t])` with states warming(<15min)/live/settled, plus a JSON profile store (`data/volume_profiles/*.json`, 7-day mtime staleness, `build_profile`). `/api/watchlist/market` rewired from `_require_alpaca()` → **`get_market_data_provider()`** and now returns `rvol:{value,state}` per symbol, computed from the already-fetched 5-min batch (bumped days 2→4 for the overnight fallback) + cached profile; **overnight relabel** done clock-aware in the endpoint. Nightly `_volume_profile_refresh_loop` (boot + 24h) builds profiles for all watchlist symbols. Frontend: `RvolInfo`/`WatchlistQuote.rvol` (`api.ts`), a fixed WS-tick merge bug that had dropped `rvol`, and a new **RVOL column** (`2.3×` + color band <1.5 normal/1.5–2 yellow/2–3 amber/>3 red, muted overnight, `—` warming). `MARKET_DATA_PROVIDER=schwab` flipped in `.env` (data path only; `BROKER` untouched). **Verified live end-to-end through the HTTP endpoint** on all 15 watchlist symbols (SPY 66.8M real consolidated vol; CAT 1.78×, SPY 1.23×, NVDA 0.83×, XLF 0.70× — calibrated, self-consistent). 671 tests, ruff + typecheck clean. Deploy helper `tools/serv-deploy.sh` added (the auto-mode classifier blocks the agent from restarting prod / self-granting — a human runs it). **Deferred (not needed for RVOL):** `/api/bars`+`/api/portfolio/history` → provider, `1Hour` aggregation, `trading-panel.tsx`/mobile-card column. In-browser column polish = pending. Schwab stays READ-ONLY. |
| 2026-06-23 | **Schwab market-data provider (step 1) — real consolidated volume, READ-ONLY.** New `MarketDataProvider` protocol (`brokers/base.py`) + `SchwabMarketData` (`brokers/schwab_market_data.py`, no order methods) mapping Schwab `pricehistory` → `StockBar` (epoch-ms→ISO, RTH-only, batch = bounded concurrent loop); `get_market_data_provider()` factory resolver on `MARKET_DATA_PROVIDER` (**default `alpaca`** → inert until flipped). **Verified live end-to-end: SPY 66.6M / AAPL 51.9M daily vol vs Alpaca IEX ~1.9M/1.8M = ~30-35× more (real consolidated)**; 5Min intraday + `1Hour`-raises confirmed. Two real bugs fixed en route: (1) **`brokers/schwab_auth.py` — schwab-py builds its OAuth2 session without `token_endpoint_auth_method`, so authlib defaults to `client_secret_post` which Schwab rejects (`invalid_client`)**; now build the session with `client_secret_basic` + token-persistence writer (this fixes Schwab auth **app-wide**, incl. the existing account-sync adapter). (2) `data/settings.json` held `.env.example` placeholder Schwab creds that shadowed the real `.env` values (`schwab_config()` reads only settings.json) — corrected (gitignored, not committed). `tests/test_schwab_market_data.py` (14); 664 tests. **Deferred:** consumer rewiring (`/api/bars`, watchlist, scans), `1Hour` aggregation, quotes/`totalVolume` for live RVOL, headless-login fix + 7-day-cliff APNs alert. Schwab stays READ-ONLY (no execution). |
| 2026-06-23 | **Chart fix: follow Alpaca `next_page_token` in bar fetches (stale-candle bug).** `/v2/stocks/bars` is paginated; our fetchers requested `limit=10000, sort=asc` but never followed the token, so they returned only page 1 — the OLDEST bars from `start`. The web chart's **1H/180d** view showed a last candle of **Feb 2** instead of today (daily/1Min looked fine only because those windows fit one page). Fixed all four fetchers in `brokers/alpaca.py` (`get/aget_stock_bars_range`, `get/aget_stock_bars_batch`) via shared `_get_all_bar_pages`/`_aget_all_bar_pages` + `_merge_bar_pages` (list→extend, dict→per-symbol extend); 50-page safety cap with a `logger.warning` so silent truncation can't recur. Verified live: **1H/180d now 216→1069 bars, last candle = today 17:00 ET**; 30d/365d/1Min unchanged. `tests/test_alpaca_data_feed.py` (+4 two-page merge/token tests, sync+async, single+batch); 650 tests. Data-read path — no `trading/` touch. |
| 2026-06-23 | **Watchlist intraday series — real 1-day chart for the phone card.** `/api/watchlist/market` (`api/main.py`) now returns a per-symbol `intraday` array alongside the daily `sparkline`: a second `aget_stock_bars_batch(..., timeframe="5Min", days=2)` call (no broker-adapter change — method already took `timeframe`) filtered by new helper `_session_intraday_closes()` to the **latest ET session** + **RTH only** (09:30 inclusive → 16:00 exclusive, reusing `_ET`/`_SESSION_*` from `trading/market_hours.py`). **Fails soft** — on `BrokerError` the intraday call logs + yields `[]`, daily quotes unaffected. RN client (separate repo) plots point `i` at `x=i/78` (390 min ÷ 5) with `sparkline` fallback when empty. Verified live during market hours: n=46 closes at 13:16 ET (≈226 min/5), last intraday close == live `price` for SPY/QQQ/AAPL. `tests/test_watchlist_intraday.py` (boundary/date filtering); 646 tests. Display-only — no `trading/` touch. |
| 2026-06-20 | **R3-A SHIPPED — phone-approval CORE (delivery-agnostic backend).** The server half of push-approval, reusable by web/Capacitor/**React Native** unchanged. New `trading/proposals.py` (`create_phone_proposal`/`approve_proposal`/`reject_proposal`) + `trading/order_proposal.py` (shared `OrderIntent` builder extracted from `propose_order` — DRY, legacy immediate path unchanged). Proposal lifecycle on `paper_order_proposals` (added `expires_at`/`payload_hash`/`delivery`/`decided_at`/`execution_id`/`last_error`; state machine `pending_phone → approving → executed|blocked|failed | rejected | expired`). **Approve re-validates + executes through the SAME `ExecutionService.submit` chokepoint** (never a new broker path); TOCTOU is automatic (submit runs RiskEngine against live state). REST: `POST /api/proposals`, `GET /api/proposals/pending`, `GET /{id}`, `POST /{id}/approve` (payload_hash **required**), `POST /{id}/reject` — all behind `SecurityMiddleware`. Adversarial Sonnet review → fixed pre-commit: **atomic claim** (status-guard + expiry folded into one UPDATE → double/concurrent approve executes **at most once**, incl. a real `asyncio.gather` test), post-execution **persist-failure never reports a placed order as failed**, **fail-safe expiry** (malformed/missing TTL = expired), **TTL cap** (≤1h), required hash. 322 tests. **Deferred (before paper-autonomous/live):** stuck-`approving` reconcile vs `order_executions` on restart; loud warning when `API_TOKEN` unset. **Note:** the chat `propose_order` stays frontend-gated (AI-SDK `needsApproval`) — the *phone* path is the new server-gated one. Next: the actionable Approve/Reject card in the Activity tab. |
| 2026-06-20 | **Mobile shell v1 + workspace serving/grid fixes.** (1) **Phone white-screen fixed** — was a stale `next start` build (rebuilt while the prod service held the old in-memory module graph → hydration failed); added `tools/rebuild-frontend.sh` (atomic build+restart) + `STARTUP.md` guidance so it can't recur. (2) **Phone access gotcha documented** — the app is wired to the `serv.tail78aab8.ts.net` MagicDNS name, so a 3rd-party DNS (iOS Adblock Plus) overriding MagicDNS made it unreachable even with the tunnel up; fix = Automatic DNS. (3) **Mobile bottom-tab shell SHIPPED** — `MobileWorkspace` (Watch/Portfolio/Agent/Activity, floating pill bar) on `<768px`, reusing existing widgets/thread/notifications; desktop grid untouched. (4) **Theme `color-scheme` + next-themes flash fixed** (iOS native controls now match dark themes). (5) **Desktop no-overlap regression caught + reverted** (grid column floor kept at `GRID_DEFAULT_COLS`). (6) **Chat composer `autoFocus` now desktop-only** (stop the mobile keyboard popping on the Agent tab). Frontend-only; typecheck + build + eslint clean; deployed live. UI = pending broader in-browser polish. |
| 2026-06-20 | **R2 sell-to-close SHIPPED — exit held long positions (equity + options).** The agent can now CLOSE what it opens. New **unconditional** risk guard: a SELL may only reduce a **held LONG** position ≥ qty (net of pending sells) — never open a short / sell-to-open / naked (adversarial review confirmed **no path can open a short**). Per-order value cap made **buys-only** (a >$50k winner can now be closed — was a latent block on exits). Options sell-to-close enabled (limit-only; expiry relaxed to allow closing *on* expiry day). `position_intent` (`sell_to_close`/`buy_to_open`) sent to Alpaca as a broker-level backstop. Proposal card shows held qty. 3 review-flagged bugs fixed pre-commit (negative pending-sell clamp, equity/option no-holding parity, narrowed 400 fallback). 312 tests. |
| 2026-06-20 | **Chat grounding + memory-loop fixes (post-options).** (1) Inject current **ET date + market session** into the chat system prompt — the agent had no date so it reasoned from its ~2025 training prior (built an *expired* option expiry); added Alpaca options-snapshot **`get_option_quote`** tool so it pulls real bid/ask instead of web-searching; fixed a **Windows `ZoneInfo`/`tzdata` import crash** (pinned `tzdata` + defensive UTC-offset fallback so the chat module never fails to import). (2) **Broke the self-reinforcing memory loop**: semantic token-overlap dedup + the extractor now drops echoed/already-retrieved facts (store had been re-accumulating reworded "20% cap" copies — a fabricated preference). Verified live: agent placed a real TSLA paper order (queued for Mon open) and a long-option flow once dates were correct. |
| 2026-06-20 | **R2 options (long-only) SHIPPED — buy-to-open calls/puts on paper.** Extended the chokepoint to long single-leg options: `OrderIntent` gains `asset_class`/`occ_symbol`/`multiplier`; `imports/option_symbol.py` `to_occ_symbol`/`parse_occ_symbol` (OCC, round-half-up strike); `brokers/alpaca.py::option_contract_exists`; `risk_engine` options branch — **chokepoint self-enforces ×100 multiplier** on value/buying-power/concentration (a $50k cap = premium×100×contracts), expiry gate, OCC sanity, and rejects sells/market/non-limit (defined-risk only). `propose_order` accepts option fields → OCC, forces limit + buy-to-open; option-aware approve card (shows contract + total cost). **Limit-only** (premium explicit → no options market-data dependency). Sells/spreads/live all hard-deferred. Adversarial review: all 6 safety checks pass; 2 hardening fixes applied (self-enforced multiplier, strike rounding). Verified end-to-end on Alpaca paper after B2's first real (equity) order. |
| 2026-06-20 | **R2-B2 SHIPPED — real Alpaca PAPER execution.** `aplace_order`/`aget_order`/`acancel_order` on Alpaca (raw httpx POST, `client_order_id`=execution_id idempotency, 422→lookup-don't-resubmit); the chokepoint's `paper` branch now places real paper orders (LIVE still hard-guarded server-side for Increment 3); poll-based `fill_tracker` (submitted→filled reconciliation + orphan recovery of `pending` rows by client_order_id on poll + startup); execution-aware approve banner + a Settings "Execution / Risk Limits" panel (mode, kill switch w/ confirm, $50k caps). 270 tests. Two adversarial-review bugs fixed pre-commit: orphan-order window (placed-but-unaudited on crash) closed via client_order_id reconciliation; broken 422-recovery GET fixed to use query params. **R2 first build complete — flip `execution_mode→paper` in Settings to place agent-executed paper orders from chat.** |
| 2026-06-20 | **R2-B1 SHIPPED — execution chokepoint (DRY_RUN).** New `trading/` module: `models.py` (OrderIntent/RiskCheckResult/ExecutionResult, Decimal), `risk_engine.py` (10-check fail-closed validator), `execution_service.py` (the SOLE broker path; dry_run logs + audits, paper/live guarded for B2). `config/settings.py` `trading` block (execution_mode default dry_run, kill switch, $50k caps), `db/order_executions.py` audit table, `propose_order` wired (execute_tool now async). 256 tests. Adversarial Sonnet review caught + fixed 5 guardrail bugs pre-commit: audit-row-before-validate (no unaudited attempts), thread-safe kill-switch read, limit-order notional uses max(limit,market) (penny-limit bypass closed), daily counter excludes dry_run/rejected/error, removed dead proposal-record path. Invariants held: single chokepoint, kill-switch-first, deny-by-default, audit-before-branch, Decimal-only. Next: B2 (Alpaca paper place_order + fill tracking + execution-aware UI). |
| 2026-06-19 | **R1d SHIPPED — honest analytics foundation.** `db/realized_pnl.py` (net-cash realized + position reconciliation) + `db/behavioral.py` (scalp/cadence/concentration/day-of-week/asset-mix); FIFO P&L demoted to `approximate_fifo`. 194 tests. Real-data check: realized_closed +$12,431.97 (exact, reconciles), per-symbol win 64.7%, PLTR/GME flagged incomplete; behavioral signature = TSLA/PLTR-concentrated, 22% options, Friday-heavy, TSLA-led same-day scalping. R1c split-adjust committed bundled (correct for held-across-split lots; not the P&L headline). **R1b (retrospective) now unblocked** on this honest base; Schwab Realized-G/L import fills the incomplete-position P&L when the user exports it. |
| 2026-06-19 | **Course-correction: FIFO round-trip P&L found unreliable on real data.** Verifying R1c against the live ledger via an independent net-cash check exposed that lot-level FIFO over-reports realized P&L by ~$124k (NVDA engine +$70.6k vs true +$1,369.60 net cash on a fully-flat position). Root cause is structural: date-only Schwab export (no intraday order) + heavy same-day scalping + shorts + pre-window positions (PLTR net −1,530 sh). **Pivot:** realized P&L from **net cash flow** (exact for closed positions) + position reconciliation + **Schwab Realized Gain/Loss / 1099-B** as the authoritative source for incomplete lots; raw transactions used for **behavioral** analysis only. R1c (splits) held uncommitted (correct-in-principle, not the fix). Added **R1d** (reliable analytics + behavioral facts) ahead of R1b. |
| 2026-06-19 | **R1a — trade-import hardening (SHIPPED) + full history imported.** Options-aware FIFO round-trips (new `db/fifo_round_trips.py` + `imports/option_symbol.py` used by `db/trades.py`/`db/analytics.py`), atomic imports with an error manifest, and dedup precedence (`broker_txn_id` else SHA256 hash). 183 pytest pass. Imported the user's full 4-year Schwab history (3 accounts, 451 events, parsed 0-warnings; 281 inserted + 65 deduped into the existing partial import) → 463 total ledger events; options round-trips now compute (264 round-trips all-time). **Found:** unadjusted stock splits corrupt cross-split round-trips (NVDA 10:1 → phantom −$14k) → **R1c (split adjustment) reprioritized as a prerequisite before the R1b retrospective.** |
| 2026-06-19 | **Realignment to autonomous trading agent.** North star reframed (§1) from "personal trading copilot" to an agent that trades in the background so the user can work/parent; dashboard supervises, phone approves. Added the **PERCEIVE→REASON→DECIDE→ACT→DELIVER→LEARN loop** framing + a full codebase scorecard, six load-bearing principles, and a realigned **R1–R5** build sequence (R1 = import→retrospective-LEARN head start, R2 = risk engine + execution, R3 = phone delivery, R4 = close the learn loop, R5 = deepen perceive/reason). Deep per-stage tech research (2026 best-in-class, costs, libraries) saved to **`AGENT_LOOP_RESEARCH.md`**. Recorded north-star memory. |
| 2026-06-19 | **In-app notifications**: fired rule/scan alerts (already broadcast over `/ws/market`, previously consumed by nothing) now surface as a top-right slide-in **banner** + a top-bar **bell** with unread counter + dropdown history. Backend: ring-buffer in `MarketStreamHub` (id/ts enrichment) + `GET /api/alerts/recent` so a fresh tab shows recent fires; frontend `use-notification-store` + `use-alert-stream` (separate socket from quotes) + bell/banner components. OS notification when tab unfocused. Also cleaned out 5 leftover test/demo rules (always-true "Has open positions" etc.) that were re-firing on every backend restart. |
| 2026-06-18 | **AI rule authoring (B2)**: `propose_rule` chat tool — NL → validated `RuleSpec` → Approve/Reject card → armed via `create_rule` (the signal scan then fires it). New `ai/chat/alert_intent.py` + tool UI in both chat surfaces. Verified live (authored "NVDA below 200-day SMA" end to end). Also: **a rule is not a memory** — alert-intent turns now skip learned-memory extraction (was wrongly storing the alert as a "preference"). |
| 2026-06-18 | **Signal engine fires (B1)**: periodic `_signal_scan_loop` (`signals/scan.py`) scans watchlist ∪ positions → fetches daily bars → merges indicator + portfolio facts → `evaluate_rules` → broadcasts alerts. **Verified live** — fired an indicator rule (`SPY_pct_change_1d`) + a portfolio rule off real Alpaca data. Also wired `logging.basicConfig` so app logs (poll/scan/errors) actually print (they were silently dropped), and added `References/STARTUP.md`. |
| 2026-06-18 | Charts: **configurable Alpaca data feed** (`data_feed` / `APCA_DATA_FEED`, default free `iex`, SIP a one-flip switch later) + more **intraday history** (1m/5m/15m/1H → 10/30/60/180d). Chart widget consolidated to **one top bar** — symbol + timeframe/style **dropdowns** in the widget header (frees horizontal space for future pen/alert tools). Note: free IEX feed is regular-hours only; after-hours needs the paid SIP plan. |
| 2026-06-18 | Shipped: tool-calling fix (`enabledTools: []` → all tools on by default; ends fabrication) + Anthropic native path (#10, #17); **learned-chip rehydration C4** (#4) + Copilot **thread-history** switcher with pin/rename/delete (#18, #19, #20). Backend: analytics enrichment (#5), async broker I/O offload (#6), mark-to-market unrealized (#7), SSRF guard (#8). **Signal & Automation Engine foundation**: indicators (#13) → facts (#14) → backtest (#15) → validated rule schema (#16), all pure + tested. Workspace UI: settings menu (#9), command-console cleanup + tools-out-of-chat (#17). |
| 2026-06-18 | Vision fold (IDEA_BANK §7.10–7.12): **Signal & Automation Engine** (one declarative node-graph for rules/alerts/scans/automations; indicator layer; streaming/scan/backtest runners; AI confirm-to-save + node-UI + DSL + clearable presets), **charting over a decoupled overlay model** (expand Lightweight Charts; draw↔rule bidirectional; lib swappable), **grounding-not-training + MCP-not-needed-in-app**. Folded into Phase 7 (engine block) + §6. |
| 2026-06-16 | Phase 3 C1–C3: `POST /api/memory/extract`, client `onFinish` extraction, “Learned from this chat” chips; assistant-ui Thread (B5); server `extract=False` on finalize + legacy chat |
| 2026-06-16 | Chat: locked assistant-ui (AI-SDK adapter); mem0 + stockbroker adopted as patterns not deps (Python-backed memory, approval card UI in P3 / execution in P4) |
| 2026-06-16 | Intelligence P1: 1d/7d snapshot drift, programmatic research citations, rich rule facts, transition+cooldown, WS rule alerts |
| 2026-06-16 | Intelligence P0: research mock labeling, content in findings, memory retrieval on chat/insights |
| 2026-06-16 | Phase 0 CI (ruff + pytest); `portfolio/analytics.py` on poll; insights wired to structured analytics |
| 2026-06-16 | Merged six reference docs into this file; originals moved to `archive/` |

*Update this file when priorities shift or phases complete.*
