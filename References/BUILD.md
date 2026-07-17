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
| DECIDE | ~65% | sizing/stops/kill-switch done; calibrated scorer wired but **edgeless** — validated multi-signal fusion + S/R ladder = the frontier |
| ACT | ~80% | chokepoint + phone-approval **SHIPPED** (paper, verified on device); auto-entry/exit engines; LIVE hard-gated (Increment 3); bounded-auto next |
| DELIVER | ~70% | native RN app **merged to main**; Live Activity approve/reject **verified end-to-end on device** (real proposals drive it); copilot live on `/api/chat/stream`. Open: chat `proposal` SSE event + LA `failed`-path |
| LEARN | ~55% | retrospective→lessons→(disabled) rule proposals live; **decision journal + vector memory NOT built** (R4) |

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
- **R3 — DELIVER: phone. ◐ LANDED on main** (the `feat/mobile-app` app, merged 2026-06-30; Mac/RN agent owns `mobile/`, serv owns the backend slice). A **native Expo React Native app** (`mobile/` — Expo SDK 56 / RN 0.85, `expo-router`, `expo-widgets`, FlashList) — a deliberate pivot from the originally-planned Capacitor-wrap-the-web. **Working phone client:** watchlist (live WS pricing + extended-hours `sessionView` + RVOL), **candlestick charts** (vendored TradingView lightweight-charts in a WebView; inline + full-screen with interval selector + crosshair), ticker detail + company/EPS disclosure, alerts **inbox** (push deep-links, persisted clear-read), Instagram-style tab bar, runtime **theme switcher** matching the web. **★ Live Activity / Dynamic Island Approve/Reject — the phone-approval unlock — VERIFIED END-TO-END on a real device (`3742b91`):** START→`pending` → UPDATE→`approved` → END→`executed` all passed; serv's `_la_wire_content_state` (`signals/push.py`) sends the double-encoded `{name, props}` envelope expo-widgets needs; **wired into `trading/proposals.py` so real proposals drive the card automatically**; `tests/test_live_activity.py` (17). **Mobile copilot LIVE** on serv's `POST /api/chat/stream` (SSE over `expo/fetch`); per the mission, **chat never approves** — a `proposal` event hands off to the existing Face-ID Approvals inbox. **Open items:** the chat endpoint doesn't yet emit the structured `proposal` SSE event, so the in-chat hand-off card can't render (`References/SERV_chat_stream_proposal_event_GAP.md`); LA `failed`-path deferred (same envelope — verify on first RiskEngine block); saved chat-history mode; ongoing native polish. *(The original plan — self-hosted APNs `aioapns`, interactive Approve/Deny/Modify categories, biometric tiered confirm — is substantially realized; the chokepoint stays the sole executor, fired only by the phone's Approve.)* **NB:** distinct from the Phase-6 web "mobile shell" (the responsive `/workspace` breakpoint) — this is the dedicated native app.
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

**Where it actually stands (2026-07-01) — build honestly splits the *infra* from the *edge*:**
- **Code-real today:** the single `propose_trade` intake seam → phone proposal; a **calibrated scorer**
  (`signals/decision_scorer.py` — Platt scaling + Wilson-CI EV buckets + honesty metadata) **wired into** perception
  (`get_context`), the chat tools, and proposals; per-strategy backtest stats; and S/R **levels** detection + the
  prior-session volume profile (`signals/session_node_strategy.py`).
- **Still the frontier (the value):** the generic scorer is **edgeless by its own metadata** (n=447, Brier≈base-rate)
  — the real conviction shown to the user today comes from per-strategy *validated* backtests, not this scorer.
  Multi-signal **fusion**, the **LLM-reasoning-on-top** layer, the **S/R scale-out ladder** as a plan output, and
  honest **calibrated win%/EV** across thousands of backtested instances are **built next** — each factor a
  hypothesis gated by backtest→paper. This is where the app becomes worth using.

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

Moved (2026-07-17): the live, verified module map is **`AGENTS.md`** (root); the plain-English tour is
`References/ARCHITECTURE.md`; editable diagrams: `ARCHITECTURE_DIAGRAM/MODULES.excalidraw`.
The 06-16 "locked" chat-architecture notes that lived here were superseded by Phase 1b (one Python
chat brain, `ai/chat/loop.py`) — see BUILD_LOG.md rows 2026-07-07.

## 3. Current state — snapshot (2026-07-01)

Removed (2026-07-17) — this snapshot table kept going stale, and its own header said stale-is-worse-
than-absent. Current state now lives where it stays current: **`HANDOFF.md`** (open threads),
**`board.json`** (in-flight work), **`AGENTS.md`** (what exists, verified), `BUILD_LOG.md` (what shipped, when).

## 4. Active roadmap

One roadmap now. The **strategic arc is the R1–R5 agent loop** (above) — this section is the **tactical backlog**
that advances it. The legacy *Phase 0–8* plan is **retired**: everything it tracked is either shipped (see §3 +
§11) or folded into the items below. Effort: **XS** <½d · **S** ½–2d · **M** 3–5d · **L** 1–2w. Tags `P1–P12`
trace to the founder's 2026-07-01 cockpit review.

**✅ Shipped 2026-07-01 (this round):** notification row-click deep-links + symbol-resolution fix + clickable
proposal tickers (P1/P2, `85dd8bc`) · copilot floating-dock padding fix + toolbar opener + `Ctrl/Cmd+J` (P10) ·
watchlist top-`+` add-row (P12, in `f93f582`) · mem0 delete-drift fix (P4) · **decision journal J0-J3** — record path → copilot/widget
surface → calibration review (P4, `aa3c67a`/`aa858dc`/`3115d5b`) · **LLM-authored reports + in-app viewer** (P3, `b0eeef9`, closes P5-reports) · **real scans
screener** (P5, `d8f0e25`). Full suite 1079 green; chokepoint untouched. Items below are still open. ⚠️ **Local
commits, not pushed; deploy + in-browser check pending (founder).**

### 4.1 Cockpit hardening — the founder's current focus (July 2026)

The app *works*; daily use surfaced friction. These make it feel finished. Ordered quick-wins-first.

**Notifications & ticker linking (P1, P2)**
- **[XS] Notification row-click should act, not just expand.** `notifications-widget.tsx` `handleRowClick` only
  toggles expand + marks-read; the deep-link (`activate()`) is bound to a *hidden hover icon*. Call `activate()`
  from the row body. **This is the literal "I click it and nothing happens".**
- **[S] Fix notification deep-link resolution.** `notification-target.ts`: (a) prefer `data.symbol` over `ref_id`
  (today the alert's row-id shadows the ticker → the chart jumps to a garbage symbol); (b) add the `rule_triggered`
  case (the rule-engine alert kind isn't matched at all); (c) map the unmapped kinds (execution/fill/rebalance/
  schwab_health) or at least show a toast instead of a silent no-op.
- **[S] Link every ticker (P2).** Wire `ClickableSymbol` + `usePublishSymbol` into `pending-proposal-card.tsx` /
  agent-proposals (the agent's core deliverable currently renders the symbol as dead text), then scans/research/
  reports/memory widgets and chat output. Founder rule: **any ticker on screen opens/links the chart.**

**Watchlist (P12)**
- **[S] Top-toolbar `+` add-symbol.** Replace the bottom-pinned input with a `+` in the widget header (the
  `WidgetHeader` already has a watchlist-only control slot) that inserts an add row at the **top** and pins new
  symbols to the top — the pattern already ships in `mobile/` (`watchlist-screen.tsx`). Port it.

**Copilot window (P10)**
- **[S] Stop the floating copilot reserving full width.** `workspace/page.tsx` `dockedPadding = open ? width : 0`
  ignores `pinned`, so the small floating box still pushes the grid over by the full panel width. Gate on `pinned`.
- **[S] A real opener.** The only way to open the copilot is a tiny corner pill — add a toolbar button + hotkey.
- **[M] Finish the overhaul.** Drag/reposition or snap-zone float; route `command-console`'s plain-text input into
  the copilot (today it dead-ends in a redirect message); unify `copilot-thread-history.tsx` to the mono font.

**Widgets & layout (P5, P8, P9)**
- **[XS–S] Terminal-restyle sweep (P5).** `reports-widget.tsx` (shadcn Button/Skeleton) and `research-page.tsx`
  are the known stragglers; do a completeness pass over all 16 widgets (memory / price-alerts / agent-proposals
  were never checked).
- **[S *or* L — needs a decision] Scans widget (P5).** Today it's a *duplicate render of the rules data* — hence
  "sounds like an alert". Either **relabel/retire it** (S) or **build a real universe screener** generalizing
  `rvol-scanner-widget.tsx` (L). **Open decision — §4.4.**
- **✅ Split performance into widgets (P8) — SHIPPED (2026-07-02).** Decomposed into 7 registry widgets:
  `performance-stats` (realized/unrealized/win/trades/fees), `trade-calendar`, `orders` (blotter), + 4 graphs
  (`sector-radar`, `pnl-scatter`, `trade-distribution`, `winrate-trend`). All account-sliced via the new resolver.
  Old monolithic `performance` widget still registered — retire it in a follow-up.
- **[S] "Split a widget" interaction (tmux-lite, P9).** Shrink the widget + auto-place a new one in the freed slot
  via the existing `findPlacement`/`addWidget`. Defer a true nested pane-tree unless this proves insufficient.

**Reports (P3)**
- **[M–L] Real briefs, in-app viewer.** Generalize `signals/reports.py` beyond the one hardcoded SPY levels
  template: multiple kinds/symbols/times; swap the deterministic body for **LLM-authored** content (reuse
  `ai/research/report.py`'s JSON→HTML machinery); build an **in-app report viewer** route so the `report_ready`
  notification deep-links straight into the rendered brief (morning/evening briefs, on-request levels). The
  founder's core reports vision. Cost/cadence is an open decision (§4.4).

**Schwab monitoring & calendar (P6, P7)**
- **✅ Real Schwab data + unified account selector w/ paper↔real firewall (P6, reworked) — SHIPPED + deployed
  (2026-07-01).** The "data already synced, just a UI task" assumption was wrong: account-snapshot polling was
  **fully off** and single-account (`resolve_account_hash` *raises* — this token sees **4** linked accounts).
  Shipped a **dedicated read-only** Schwab source (`brokers/schwab_accounts.py` → `GET /api/schwab/accounts`,
  TTL-cached, GET-only, independent of the Alpaca execution machinery) + an explicit **`money_type` firewall**
  (schwab→real, alpaca→paper; `_aggregate_account_snapshots` sums one money_type only — paper+real never blend) +
  a **unified `AccountSelector`** (grouped Real·Schwab / Paper·Alpaca; scopes `overview|all_real|all_paper|<id>`;
  REAL/PAPER badge; Account Summary Overview = two side-by-side totals). The founder's use case works: flip to
  Real·Schwab and watch real balances in the same tiles while paper-testing. A standalone `schwab-activity` widget
  was built first then **removed** for the selector (founder feedback). Live: Real $231,817.98 (4) vs Paper
  $300,200.12 (3). **Follow-ups:** Phase 2 auto-sync (below), Phase 3 `/performance`+journal account/broker filter,
  Stage B (positions/orders follow a real pick), equity-curve history storage.
- **✅ Schwab trade auto-sync — all 4 accounts (P6 Phase 2) — SHIPPED + verified (2026-07-01).** Rewrote the
  never-run `sync_schwab()` (Transactions API): per-hash `SchwabAdapter`s off ONE shared OAuth client (works with the
  4 linked accounts `from_settings` can't), attributing each trade to the right row by `broker_external_id` (mapped in
  the foundation step below). Sync is **incremental + rolling per account** (`_schwab_sync_start`, pure + unit-tested):
  never re-imports the JSON boundary (its dedup key differs → would double-count), re-fetches a 7-day rolling window so
  same-day fills aren't dropped. Decoupled from `brokers.schwab.enabled` (runs on token presence, so the broken
  per-broker snapshot poll stays off). **Also fixed two pre-existing latent bugs the sync never hit:**
  `Client.TransactionType.TRADE` (doesn't exist → `Client.Transactions.TransactionType.TRADE`) and the parser (it
  grabbed the cash `CURRENCY_USD` leg + summed fees into amounts — rewrote to key off the security leg: symbol from
  the EQUITY/OPTION/FUTURE leg, signed `amount`=side/qty, `price`, and the transaction `netAmount` as fee-inclusive net
  cash matching the JSON export; futures/options net cash now captured too). **Adversarial Sonnet review** found a
  CRITICAL (the naive `last+1` date cursor silently dropped same-day fills under the hourly loop) + 3 MAJORs (no
  per-account/-item exception isolation; a failed backfill chunk left a permanent gap; no pacing / concurrency guard)
  — all fixed (rolling cursor, per-account try/except, don't-advance-on-failure, chunk pacing + a non-blocking lock);
  read-only/attribution/gating confirmed clean. 10 new tests (`tests/test_schwab_sync.py`); 1134 suite green.
  **Verified live: 56 events across the 4 accounts, 0 dupes; a 2nd run = 0 inserts / all-duplicates (idempotent); zero
  API rows overlap the JSON boundary.** The foundation (create the missing Roth-Personal ••0348 row, stamp all 4
  `broker_external_id`s to their hashes, relabel Joint/Roth) was a one-time DB migration. **Follow-ups:** the
  `_poll_broker("schwab")` snapshot poll is still single-account (out of scope; equity balances come from the separate
  read-only `/api/schwab/accounts`); Phase 3 = `/performance`+journal account/broker filtering.
- **✅ P&L calendar widget (P7) — SHIPPED (2026-07-02).** `trade-calendar` widget: Monday-start month grid, month
  nav + month total in the toolbar, per-week P&L column on the right, and a day-of-week rollup strip along the
  bottom (folds in the old day-of-week chart). Reads `daily_realized` per displayed month; account-sliced.

### 4.2 The Decision Engine + LEARN — the headline value (P4)

*The app's #1 value-driver (CLAUDE.md, ★ Decision Engine below). The plumbing is built; the **edge** and the
**memory that makes it feel like a co-pilot** are the work.*
- **[XS] Fix mem0 delete-drift.** `MemoryService.delete()` never mirrors to `self._mem0.delete()`, so a deleted
  memory/preference silently resurfaces via `mem0.search()` — an active correctness bug squarely under the
  founder's "how does it store/act on what I tell it" trust concern. Patch first.
- **[L — design pass first] Decision journal.** The "acts on what it learns" half is *undesigned*, not just unbuilt:
  proposal approve/reject/modify outcome → a durable, queryable record → feeds future scoring/sizing. Scope the
  schema + retrieval as a founder-facing design pass before a Composer brief. **The single most important item.**
- **[M] Vector memory.** `sqlite-vec` + a `recall_memory` tool to replace regex token-overlap retrieval (R4).
- **[deep-research → build] The edge.** The calibrated scorer is wired but edgeless (n=447). Multi-signal fusion,
  LLM-reasoning-on-top, S/R scale-out ladders, per-strategy validated backtests → honest EV/win%. See ★ Decision
  Engine + the engine-deepening roadmap; each factor is a hypothesis gated by backtest→paper (never fabricated).

### 4.3 Workspace consolidation — finish it (`References/CONSOLIDATION_PLAN.md`)

Stages 1–3 shipped (notifications inbox widget → multi-page → rules/research/performance widgets). Remaining:
- **[L] Stage 4 — "Configure-your-AI" default page:** a page of config widgets (the 7 Settings tabs, account
  connect, trade import, reports/alerts setup) **plus** net-new first-run onboarding, **agent charter/name/persona**,
  goals/style free-text, memory settings, notification routing.
- **[M] Stage 5 — deprecate the old page-nav routes** once everything's widgetized; finalize the two editable
  default pages (Setup + Canvas).

### 4.4 Open product decisions (resolve before briefing)
- **Scans widget:** retire/relabel (S) vs. build a real universe screener (L)? ~10× apart in effort.
- **"Hermes" agent (P4):** purely the backend memory/decision-journal work, or also a **product-identity** call —
  a named persona + a distinct co-pilot surface/mode? Scope the product question before engineering.
- **Reports (P3):** LLM-authored scheduled briefs carry a per-run token/latency/retry cost — decide cadence + budget.
- **Charts (P11):** the overhaul is **owned by a separate agent/thread** — the main loop must not re-scope or rebuild it.

---

## 5. Known debt & cross-cutting issues

A durable registry so survey findings don't evaporate between sessions. Fix opportunistically; add a regression
test with each fix. (No reader checked CI pass-state after the June churn — run `ruff` + `pytest` + frontend
`typecheck` before leaning on green.)

**Correctness bugs (found 2026-07-01, root-caused)**
- **Notification deep-link mis-resolves the symbol.** `notification-target.ts` does `ref_id ?? data.symbol`; the
  chart price-alert path sets `ref_id` to the alert's row-id, so the chart jumps to a bogus ticker. Prefer `data.symbol`.
- **Notification kinds unmapped.** ~5 of 11 `notify()` kinds (execution / fill / rebalance / `rule_triggered` /
  schwab_health) have no `notification-target.ts` case → clicking the deep-link icon is a silent no-op.
  `rule_triggered` (the rule-engine alert) not matching the `price_alert` case is the sharpest instance.
- **Inbox row-click is inert.** The deep-link lives on a hover-only icon; the row body only expands (= founder P1).
- **Copilot float reserves full width.** `dockedPadding = open ? width : 0` ignores `pinned` (`app/workspace/page.tsx`).
- **mem0 delete-drift — FIXED 2026-07-01** (`ai/memories/__init__.py`): `delete()` now mirrors to mem0 and
  `add()` tags `local_id` so entries are findable. **BUT adjacent + still-open:** `MemoryService.search()` treats
  mem0's `{"results": [...]}` return as a plain list, so the **mem0 semantic branch never returns hits — retrieval
  silently falls through to local token-overlap.** Net: mem0's vector search is currently *inert*; fixing it is part
  of the P4 vector-memory work (§4.2).

**Conflations / taxonomy**
- **`scans` ≈ `rules`.** The scans widget re-renders the rules data (same two API calls); BUILD.md's own historical
  use of "scan" to mean rule-evaluation seeded the confusion. Resolve the product decision (§4.4), then dedup.
- **Notification `kind` taxonomy** is not reconciled backend↔frontend (raw strings like `schwab_health` surface in
  the filter chips).

**Style / consistency**
- **Terminal-restyle is incomplete.** Confirmed old-style: `research-page.tsx`, `reports-widget.tsx`,
  `copilot-thread-history.tsx` (`font-sans` seam inside a mono panel). Unaudited: memory / price-alerts /
  agent-proposals widgets.
- **Three parallel symbol-search implementations**; `ClickableSymbol` is not literally reused by watchlist/rvol
  (behaviourally fine, drift risk). Consolidate when touched.

**Data honesty**
- **FIFO is unreliable** on the real Schwab export — net-cash realized is the source of truth (CLAUDE.md). Wherever
  FIFO round-trips appear in Performance, say so.

**Mobile parity (unaudited)**
- Whether the phone app shares the notification-click / ticker-linking / copilot-dock issues was **not surveyed** —
  check before assuming the fixes are web-only.

---

## 6. Guiding principles

1. **Safety before speed** — No live `place_order` until `RiskEngine` + audit log + auth exist.
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

## 10. Local dev & deploy (Linux `serv`)

Moved (2026-07-17): quick start + deploy in the root **`README.md`**; full server detail in
`References/SERVER_SETUP.md` + `References/STARTUP.md`; non-obvious runtime bits in the `serv-runtime`
project memory. Pre-push checks: `ruff check .` + targeted pytest + `npm run typecheck` (see CLAUDE.md).

## 11. Document history

Moved (2026-07-17) to **`BUILD_LOG.md`** — one row per round, newest first, grep-only.
