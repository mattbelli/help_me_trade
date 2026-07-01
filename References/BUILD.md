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

## 3. Current state — snapshot (2026-07-01)

> **Regenerate this table, don't patch it.** The prior version drifted badly (dated 2026-06-16, hand-patched
> inline) — most of June's shipped work lived only in the §11 history log. This is the first thing a returning
> agent reads for status; if it's stale it's worse than absent. The shipped-state *record* is §11; this is the
> *current* rollup. Grounded in a full-codebase survey (2026-07-01).
> Maturity: **stub** (placeholder) · **partial** (happy-path works, real gaps) · **working** (does its job) ·
> **polished** (does its job well) · **mixed** (parts of each).

### The autonomous-agent spine (the mission)

| Subsystem | Maturity | Reality |
|---|---|---|
| **Execution chokepoint** — `trading/execution_service.py`, `risk_engine.py` | **working** | Single fail-closed path: 10 numbered checks, Decimal money, audit-before-validate, kill switch. Modes: `dry_run` (default) · `paper` (real Alpaca paper orders) · `live` **hard-blocked** (Increment 3). Options long-only/limit-only; short-selling paper-only toggle (default off). |
| **Phone approval** — `trading/proposals.py`, `signals/push.py` | **working** | `create_phone_proposal → APNs push + Live Activity → approve → same chokepoint` (atomic claim, TTL, TOCTOU re-validation). **Verified on a real device** (START→approved→executed). The mission's headline unlock. |
| **Strategies** — `db/strategies.py`, `signals/*_strategy.py` | **working** | 7 registered, **all seeded DISABLED** + mostly unbound: `dip_buy`, `dip_refined`, `steepyc_dip`, `vix_spike`, `earnings_drift`, `session_node_spy`, `momentum_sp500`. Per-strategy `entry_mode` (propose/auto, paper-fenced) + `exit_mode`; auto-entry/auto-exit engines are real and self-defending. |
| **Decision engine** — `signals/decision_scorer.py` | **partial** | Calibrated scorer (Platt + Wilson-CI EV buckets + honesty metadata) is **code-real and wired** into perception/chat/proposals — but **edgeless** by its own metadata (n=447, Brier≈base-rate). Real conviction rides per-strategy *validated* backtest stats, not this generic scorer. Multi-signal fusion + LLM-reasoning layer + S/R scale-out ladder = **still the frontier** (see ★ Decision Engine). |
| **Data / PERCEIVE** | **working** | All OHLCV from Schwab (consolidated volume, extended-hours); Alpaca = execution + paper equity + bars fallback. RVOL (vol-vs-expected-for-time-of-day), session-node levels, earnings calendar (AlphaVantage forward + Finnhub past), technicals vs 50/200-DMA. Gaps: fundamentals depth, news/sentiment, options data. |
| **LEARN loop** — `ai/retrospective.py`, `ai/reflection/` | **partial** | Retrospective: realized P&L → LLM narrative → auto-saved "lesson" memories + backtest-gated **disabled** rule proposals; 24h reflection loop. **NOT built:** decision journal (proposal outcome → durable record → scoring feedback) and vector/`sqlite-vec` memory + `recall_memory`. Retrieval is regex token-overlap only. |

### The cockpit (web workspace)

| Subsystem | Maturity | Reality |
|---|---|---|
| **Workspace grid** — `use-workspace-store.ts`, `workspace-grid.tsx` | **polished** | react-grid-layout; multi-page (cap 5, `Alt+1–5`/`Alt+[ ]`); drag + 4-corner resize; lock; `preventCollision`; growing canvas; **12px gutters**; row height 48px; server + localStorage persistence. **16 registered widgets.** |
| **Notifications** — `signals/notify.py`, `notifications-widget.tsx` | **mixed** | Unified `notify()` emit (11 call sites) → persist → WS → APNs; server-side read sync; inbox widget (expand/filter/dismiss); bell + banner (their row-click *does* deep-link). **Broken:** the inbox-widget **row-click only expands** — deep-link is a hidden hover icon (= the "click does nothing" complaint); `notification-target.ts` mis-resolves the real price-alert symbol and maps only 6 of 11 kinds. See §5. |
| **Ticker linking / channels** — `clickable-symbol.tsx`, `usePublishSymbol` | **partial** | Channel infra solid; **Positions + Open-Orders + Watchlist + RVOL** publish; **Chart** consumes. **Not linked:** agent-proposals (renders symbol as dead text), scans/research/reports/memory widgets, chat output. |
| **Watchlist** — `watchlist-widget.tsx` | **working** | Sortable Sym/Last/Δ%/RVOL + sparkline, drag-reorder, channel publish. Add-symbol is a **bottom-pinned input**; the founder wants the phone's top-toolbar `+` pattern (**already built in `mobile/`** — port it). |
| **Charts** — `tv-light-chart.tsx` | **working** | lightweight-charts v5; header **price+Δ%**; volume + **RSI/MACD in their own panes** + **SMA/EMA overlays** (client-side `indicators.ts`, mirrors the scanner); **8 timeframes** 1m–1M (Schwab-aggregated 4H/1W/1M); extended-hours; session-node Levels; **right-click alerts** (no-overlay rule); **drawings** — levels/trendlines/channels (`chart-trendline.ts` primitives, logical-coord, **click-to-edit handles**, `/api/drawings` persistence); live candle rollover; theme-reactive. Tabbed/multi-symbol tiles unbuilt. **Overhaul shipped 2026-07-01.** |
| **Copilot** — `copilot-sidebar.tsx` | **partial** | One canonical sidebar mount; persisted open/pin/width; resize handle; terminal-mono restyle (partial — `copilot-thread-history.tsx` still `font-sans`). **Broken:** the floating box still reserves the **full docked width** (`dockedPadding = open ? width : 0` ignores `pinned`); the only opener is a tiny corner pill; `command-console` text input dead-ends instead of routing to chat. |
| **Reports** — `signals/reports.py`, `db/reports.py`, `reports-widget.tsx` | **partial** | One **deterministic template**: "Morning Levels" (SPY only, ~8am, deduped/day) → `reports` table → `report_ready` notification → focuses the widget. **No LLM-authored content, no scheduler beyond one kind, no in-app rich viewer.** Widget still old-styled (shadcn). |
| **Scans / research / rules** | **mixed** | Rules engine works end-to-end; **RVOL scanner is a real working screener**; deep-research pipeline works. **The `scans` widget is a redundant re-render of the rules data** (same two API calls) — this is the "sounds like an alert" confusion. Research page/widget still old-styled. |
| **Performance / Schwab / calendar** | **mixed** | Schwab sync is **read-only, double-guarded** (adapter `NotImplementedError` + `execution_capable=0`); pulls balances/positions/orders + transactions into the shared ledger; account-scoped analytics (net-cash realized is truth, FIFO demoted/unreliable). A P&L **calendar exists but is a flat day-chip list trapped in `performance-page.tsx`** (no month grid, not a widget). Performance is one monolithic embedded page, not small widgets. |
| **Mobile app** — `mobile/` | **working** | Native Expo RN (merged to main): watchlist + candlestick charts + alerts inbox + Live Activity approve/reject (verified) + live copilot on `/api/chat/stream`. Separate 4-tab UI, not the desktop grid. |

---

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
- **[M] Split performance into widgets (P8).** `performance-page.tsx` is one monolith; decompose into ~5–6
  registry widgets (cumulative P&L, per-symbol P&L, day-of-week, win/loss, stat cards). No backend change — the
  grid gives the margins for free. Mirrors `account-summary-widget.tsx`.
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
- **[M] P&L calendar widget (P7).** The calendar is a flat day-chip list trapped in `performance-page.tsx`. Rebuild
  it as a standalone month-grid widget (day-of-week columns, month nav, week/month rollups) registered in
  `widget-registry.tsx`. Backend (`daily_realized`/`daily_cash_flow`) already supports it.

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

**Backend — the spine**

| Concern | Path |
|---|---|
| App entry / background loops | `api/main.py` |
| Execution chokepoint | `trading/execution_service.py`, `trading/risk_engine.py`, `trading/models.py` |
| Phone proposals / approval | `trading/proposals.py`, `trading/order_proposal.py`, `signals/push.py`, `db/live_activities.py` |
| Auto entry / exit / fills | `trading/proposal_intake.py`, `trading/exit_engine.py`, `trading/fill_tracker.py` |
| Strategies | `db/strategies.py`, `signals/strategy_scan.py`, `signals/session_node_strategy.py`, `signals/momentum.py` |
| Decision scorer (calibrated) | `signals/decision_scorer.py`, `research/artifacts/` (weights · calibration · EV tables) |
| Signals / PERCEIVE | `signals/indicators.py`, `signals/features.py`, `signals/volume_profile.py`, `signals/regime.py`, `signals/earnings_refresh.py` |
| Rules engine | `ai/rules/engine.py`, `signals/rule_schema.py`, `signals/scan.py`, `brokers/market_stream.py` |
| Notifications | `signals/notify.py`, `db/notifications.py` |
| Reports | `signals/reports.py`, `db/reports.py`, `ai/research/report.py` |
| Ledger / performance | `db/trades.py`, `db/realized_pnl.py`, `db/behavioral.py`, `db/analytics.py`, `trading/sync.py`, `imports/` |
| Multi-account | `db/multi_account.py`, `db/accounts.py` (`execution_capable` guard) |
| Memory / LEARN | `ai/memories/retrieval.py`, `ai/chat/memory_extract.py`, `db/ai_tables.py`, `ai/retrospective.py`, `ai/reflection/` |
| Perception layer | `ai/perception/context.py` (`get_context`), `ai/perception/resources.py` (`query`) |
| Chat brain / tools | `ai/chat/brain.py`, `ai/chat/context.py`, `ai/chat/tools.py`, `ai/chat/mobile_stream.py` |
| Brokers | `brokers/alpaca.py` (execution + bars), `brokers/schwab.py` + `brokers/schwab_market_data.py` (data, read-only), `brokers/factory.py` |

**Frontend — the cockpit**

| Concern | Path |
|---|---|
| Workspace store / grid | `frontend/lib/workspace/use-workspace-store.ts`, `components/workspace/workspace-grid.tsx`, `lib/workspace/grid-metrics.ts` |
| Widget registry (16) | `frontend/lib/workspace/widget-registry.tsx`, `components/workspace/widgets/*` |
| Notifications | `frontend/lib/workspace/{use-notification-store,use-notification-stream,use-notification-activate,notification-target}.ts`, `components/workspace/{notification-bell,notification-banner}.tsx` |
| Channels / ticker linking | `frontend/components/workspace/{clickable-symbol,channel-selector}.tsx` (`usePublishSymbol` / `useChannelSymbol`) |
| Copilot | `frontend/components/workspace/{copilot-sidebar,workspace-chat,copilot-thread-history,command-console}.tsx`, `lib/use-trading-chat-runtime.ts`, `components/assistant-ui/thread.tsx`, `app/api/chat/route.ts` |
| Charts | `frontend/components/trading/tv-light-chart.tsx`, `components/workspace/widgets/chart-widget.tsx`, `lib/chart-time.ts` |
| Performance | `frontend/components/performance/performance-page.tsx`, `lib/performance-api.ts` |
| API client | `frontend/lib/api.ts` |

**Mobile** — `mobile/` (Expo RN; feature code under `mobile/src/features/*`).

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

## 10. Local dev & deploy (Linux `serv`)

The app runs on the always-on home Linux box **`serv`** (Ubuntu, user `mbelli`, repo `~/dev/help_me_trade`).
Windows is backup-only. Full setup: `References/SERVER_SETUP.md` + `References/STARTUP.md`; the non-obvious runtime
bits live in the `serv-runtime` project memory.

**Python (uv-managed, not system).** The `.venv` is a uv-fetched **CPython 3.12** (matches CI; Ubuntu 26.04 has no
apt `python3.12`). Run tools as `.venv/bin/{ruff,pytest,uvicorn}`. Rebuild:
`uv venv --seed --python 3.12 .venv && uv pip install -r requirements.txt ruff`.

**Node (split).** Interactive/SSH shells get **Node 22** via nvm (the Composer bridge needs `engines >=22.13`); the
systemd frontend runs system **Node 20** (`next start`). Don't assume a non-login shell's `node -v` is 22.

**Services (systemd, both enabled).** `hmt-backend` (uvicorn `0.0.0.0:8000`) + `hmt-frontend` (`npm run start`, 3001).
Deploy **without sudo**: `tools/serv-deploy.sh {backend|frontend|both}` (kills MainPID; `Restart=always` relaunches).
⚠️ **`serv-deploy.sh` does NOT rebuild the frontend** — for any frontend change you MUST `cd frontend && npm run build`
FIRST, then `serv-deploy.sh frontend`, or it restarts into a stale `.next` (verify with
`grep -rl "<new-UI-string>" frontend/.next/`). Backend changes are picked up by the restart alone.

**Networking.** Tailscale-only (name `serv.tail78aab8.ts.net`). **Never port-forward** — the real ledger + broker
keys live here. Secrets in gitignored `.env` (+ `frontend/.env.local`); `data/` (real ledger + financial exports)
is gitignored — never commit it.

**Before pushing:** `.venv/bin/ruff check .` (whole repo) + `.venv/bin/python -m pytest -q` +
`cd frontend && npm run typecheck`.

**Coding cadence (CLAUDE.md).** Delegate code to Composer: precise brief → `node tools/composer-bridge/dispatch.mjs
--brief tools/composer-bridge/briefs/<x>.md` (backgrounded, **one run at a time**) → review the real `git diff` +
lint/tests yourself before committing. **Adversarially (Sonnet) review any change to `trading/` or
`db/order_executions` before committing.**

---

## 11. Document history

| Date | Change |
|------|--------|
| 2026-07-01 | **Fix: Schwab account DAY P&L missed realized gains (read +0 when flat).** The account view computed `day_pl` as `sum(position.currentDayProfitLoss)`, which is 0 once a day-trader round-trips out — silently dropping the day's REALIZED gain (founder flagged a closed +$868.25 NVDA day-trade reading +0). Now `day_pl = currentBalances.liquidationValue − initialBalances.liquidationValue` (Schwab's own start-of-day baseline), capturing realized-closed + unrealized-open, with the position-sum as fallback. `brokers/schwab_accounts.py`. Verified: Individual ••1025 day_pl 0 → +866.13 (= the founder's +$868.25 gross less $2.12 SEC/TAF fees; matches the synced ledger net-cash). |
| 2026-07-01 | **Agent perception now reads the Schwab data (read-only) + the paper↔real firewall reaches the agent.** After bringing the account/trade data in, the AGENT couldn't use most of it: the live `/api/schwab/accounts` wasn't in the perception layer, and `my_trades` read `trade_events` globally — blending the real Schwab history with the 19 Alpaca **paper** trades. Added a **`schwab_accounts`** perception resource (live balances/cash/positions, tagged `read_only`, via a new sync core `get_schwab_accounts_sync` sharing the 30s cache; the async API route now wraps it) and made **`my_trades` real-only** (`_my_trade_events` excludes non-Schwab accounts by default; optional `account_id`). **Safety re-verified before wiring:** Schwab execution is blocked at 3 independent layers — `SchwabAdapter.aplace_order/cancel/get` all `raise NotImplementedError`, the `propose_order` chat tool rejects non-execution-capable accounts ("read-only … pick a paper account"), and `RiskEngine.validate` fails any order to a `execution_capable=0` account (all 4 Schwab rows). The perception adds are GET-only, zero execution surface. 1 new test (`tests/test_perception_real_only.py`). Live: agent reads 4 accts/$231,817.98 (read-only) + real-only my_trades = 507 trades, net-cash $15,838.05 (paper excluded). |
| 2026-07-01 | **Schwab trade auto-sync — all 4 accounts (P6 Phase 2).** Rewrote the never-run `sync_schwab()` (Transactions API) into a per-hash, per-account, read-only pull: one shared OAuth client → a `SchwabAdapter` per account, each trade attributed to the right `trade_accounts` row by `broker_external_id`. Incremental + rolling cursor (`_schwab_sync_start`, pure/unit-tested): never re-imports the JSON boundary (different dedup key → would double-count), re-fetches a 7-day window so same-day fills aren't dropped. Decoupled from `brokers.schwab.enabled` (runs on token presence). Fixed 2 pre-existing latent bugs (wrong `Client.TransactionType` enum; parser grabbed the cash leg + summed fees — rewrote to key off the security leg, using `netAmount` as fee-inclusive net cash; futures/options net cash now captured). Adversarial Sonnet review caught a CRITICAL same-day-drop cursor bug + 3 MAJORs (exception isolation, backfill-gap, pacing/concurrency) — all fixed; read-only/attribution/gating confirmed. Foundation: created the missing Roth-Personal ••0348 row, stamped all 4 hashes, relabeled Joint/Roth (one-time DB migration). 10 new tests; **1134 suite green. Live-verified: 56 events, 0 dupes, idempotent 2nd run, zero JSON-boundary overlap.** Committed + pushed (`063b2df`); deployed for the hourly loop. |
| 2026-07-01 | **Real Schwab account data into the cockpit + unified account selector w/ a paper↔real firewall (P6, reworked).** Founder ask: "bring our Schwab account/trade data in" + "see all accounts from the toolbar — all/all-Schwab/all-Alpaca/individual — with a paper vs real view; never sum paper+real." Discovery flipped the original P6 plan: account-snapshot polling was **fully disabled** (`brokers.schwab.enabled=false`; only the *market-data* path was live) and `resolve_account_hash` **raises** on this token — it sees **4** linked Schwab accounts (app had 3 rows, all `broker_external_id=None`). Shipped: **(1)** a dedicated **read-only** Schwab source kept out of the Alpaca poll/`trade_accounts` execution machinery — `brokers/schwab_accounts.py` (TTL-cached 30s, GET-only: `get_account_numbers` + per-account `get_account`/`get_orders_for_account`, masked #s, fail-soft, never raises) → `GET /api/schwab/accounts`. **(2)** An explicit **`money_type` (paper|real)** firewall: `_account_money_type` (schwab→real, alpaca→paper) stamped on `/api/snapshot` snaps + a hard guard so `_aggregate_account_snapshots` sums a **single money_type only** (paper+real can never blend). **(3)** A **unified `AccountSelector`** (replaces two duplicated inline selectors) grouped Real·Schwab / Paper·Alpaca, scope model `overview|all_real|all_paper|<id>` in `use-dashboard-prefs` (legacy `"all"`→`overview`), a REAL/PAPER/REAL+PAPER badge, and Account Summary rendering **Overview = two side-by-side totals, never one figure**; flip to Real·Schwab to watch real balances in the same tiles while paper-testing. Backend firewall hand-written; frontend via Composer (1 brief) — **caught + hand-fixed a real bug** (selector was gated on the paper `view`, so picking a real account hid the selector). The **standalone `schwab-activity` widget was built first, then removed** in favor of the selector per founder feedback (grid degrades unknown widgets gracefully). Labels founder-mapped → gitignored `data/schwab_account_labels.json`. **Live-verified: Real·Schwab $231,817.98 (4 accts) vs Paper·Alpaca $300,200.12 (3 accts), separate.** ruff + 1124 pytest + typecheck green; chokepoint untouched. Deployed on serv, armed poll resumed clean. **Next (queued):** Phase 2 auto-sync all 4 Schwab accounts via the Transactions API (multi-account attribution fix + incremental-from-last-known to avoid double-counting the 2023→ JSON import); Phase 3 account/broker filtering on `/performance` + journal; Stage B (positions/orders follow a real-account pick). |
| 2026-07-01 | **Cockpit + scanner + charts polish round (session end).** Watchlist RVOL 500 fix — a futures globex RVOL `IndexError` was 500-ing `/api/watchlist/market`, so all row charts fell back to the flat sparkline (`10cb95d`). Scans → real market screener: cold-cache 20s timeout fixed (bounded-concurrent fetch, `8492742`) + an **S&P 500 universe** toggle reading the `momentum_daily_bars` store (503 names, zero broker calls, `167d871`). **Bad-tick chart filter** — drop ticks >3× / <⅓ of a stable reference at both the chart and the Schwab hub (killed the $52→$750 candle spikes; also protects the watchlist / alerts / RiskEngine live-quote, `f3e1cdf`). **Company-info ⓘ on every ticker** — one shared `CompanyInfoModal` (company profile + fundamentals + our `get_context` facts + Finnhub `/company-news`) via `ClickableSymbol` + a root `useTickerInfo()` provider; the watchlist ticker was unified onto `ClickableSymbol`; `api/ticker_routes.py` + `signals/finnhub.py` profile/metrics/news fetchers (`e49f5b7`→`96a8e9e`). **Chart ~80% right-margin** restored via a persistent time-scale `rightOffset` (the overhaul's panes were clobbering the one-shot range, `766462b`). All deployed on serv + pushed (origin/main = `766462b`); full suite ~1124 green; chokepoint untouched. **Next:** §4.1 leftovers — Schwab activity widget (P6), P&L calendar widget (P7), perf-widget split (P8), tmux-lite (P9); Russell-1000 scans + Globex futures-RVOL profile; ★ Decision Engine edge (R5). |
| 2026-07-01 | **Decision journal J2 (surface) + J3 (calibration) — the Hermes read/learn layer.** J2 (`aa858dc`): two perception resources — `decision_journal` (the agent's own calls) + `my_trades` (the user's real Schwab history: net-cash P&L + behavioral + FIFO-flagged round-trips) — plus bounded fail-soft per-turn chat injection ("my calls" / "your pattern", opt-in), `GET /api/journal/*`, and a **journal widget with an AI⇄Me toggle**. Founder-agreed architecture: **two stores, unified only at the read/view layer** (NOT one table or a user/ai tag) — AI decisions = *calibration*, user trades = *behavior*. J3 (`3115d5b`): a 24h **calibration review** — predicted win% (stored `score_json`) vs realized outcome, per strategy + per bucket, Wilson-CI-grounded verdicts + "insufficient data" honesty; seeds ≤3 **surface-only** lesson-memories on notable drift (never retunes a strategy/scorer); `GET /api/journal/calibration` + a Calibration tab. Full suite **1107** green; deployed on serv. AI side is empty until proposals resolve (Me side shows the real imported history). |
| 2026-07-01 | **Cockpit-hardening round + decision-journal record path (multi-agent).** From the founder's cockpit review: notification inbox row-click now deep-links (+ fixed symbol resolution / added the `rule_triggered` mapping) + clickable proposal tickers (P1/P2, `85dd8bc`); copilot floating dock no longer reserves the full width + toolbar opener + `Ctrl/Cmd+J` (P10); watchlist top-`+` add-row ported from mobile (P12, in `f93f582`); mem0 delete-drift fixed — deletes now mirror to the vector store (P4). **Decision journal J0+J1** (`aa3c67a`): immutable per-proposal record (context→decision→outcome) computed in the paper world; fill-price/exit-link/thesis plumbing; adversarial Sonnet review caught + fixed a `close_position` fail-soft hole + a ×100 options-multiplier bug (both regression-tested). **Reports** (`b0eeef9`): LLM-authored morning/evening/on-request briefs + `levels`, fail-soft, disabled-by-default, terminal-styled in-app HTML viewer (P3, closes P5-reports). **Scans** (`d8f0e25`): real universe screener — 8 presets over watchlist∪positions via the cached-bars path (no Schwab burst), replacing the alert-lookalike (P5). Full suite 1079 green; chokepoint untouched. **Next:** journal J2 (perception/chat surface + widget) + J3 (calibration review). ⚠️ Local commits (origin/main = `f93f582`); deploy + in-browser check pending. |
| 2026-07-01 | **BUILD.md hardened + reformatted (12-agent codebase survey).** Regenerated the Current-state snapshot (§3); retired the legacy Phase 0–8 roadmap into a single **Active roadmap** (§4) mapping the founder's 2026-07-01 cockpit review (P1–P12: notification click/linking, ticker-links-everywhere, real LLM reports + in-app viewer, memory/decision-journal 'Hermes' core, terminal-restyle stragglers, Schwab monitor widget, P&L calendar widget, perf-widget split, tmux-lite split, copilot dock/pin, charts-owned-separately, watchlist top-`+`); added a **Known-debt** registry (§5) of root-caused bugs (notification symbol-resolution + unmapped kinds + inert row-click, copilot `dockedPadding` ignores `pinned`, mem0 delete-drift, scans≈rules); split the ★ Decision Engine into *infra-real vs edge-frontier*; rewrote Local dev for the Linux `serv` runtime. Late-June shipped work (workspace-consolidation S1–S3, theme overhaul, perf-split, notifications foundation, native mobile app) now rolls up in §3 — detail stays in the `CONSOLIDATION_*`/`THEME_*`/`PERF_SPLIT_RECON`/`NOTIFICATIONS_FOUNDATION` recon docs + git log. |
| 2026-06-26 | **Mobile theme switcher (4 palettes + Auto) + chat composer polish.** Turned the single-palette `theme/colors.ts` into a runtime theme system on `feat/mobile-app`: `theme/make-colors.ts` (builder: ~18 raw tokens → full `Colors`, washes derived), `theme/themes.ts` (**carbon** default, **warm carbon** — leads with orange so it's distinct from Carbon, **oled** true-black, **light**), `theme/theme-context.tsx` (`ThemeProvider` swaps the live `colors` object via `applyColors` + remounts only the routed subtree — Auth/react-query sit above it, so switching is instant with no re-login/refetch; persists to AsyncStorage; **Auto** mode follows the OS scheme). Picker in **Settings → appearance**; status bar adapts. Fixed the 3 module-scope color maps (button/text/feedback) that would otherwise snapshot Carbon. Tab pill is now theme-aware (light glass + theme-accent capsule in light/warm; was a hardcoded dark blur). Chat: composer lifted clear of the nav pill + the three founder suggestion prompts. Logo direction LOCKED (flat horizontal candle-bars, scattered/`D`; chunky weight for the favicon) — PageHeader build next. tsc + eslint clean; verified on device (light mode + warm-orange + persisted pick). Known light-mode cosmetics deferred: candle-chart grid lines + a watchlist grip dot (both white). |
| 2026-06-26 | **Mobile chat copilot — LIVE on real data (stub flipped) + one serv gap flagged.** serv shipped `POST /api/chat/stream` (`References/SERV_chat_stream_DONE.md`); flipped the mobile client live: `CHAT_STUB` default → off (`mobile/src/config.ts`), and switched the request to `mode: "temporary"` (saved mode 400s without a sessionId; the history screen + saved mode are the follow-up). Verified live: Q&A streams real portfolio data + inherits the perception tools (`get_context`/`query`) for free. **Gap found (serv):** on a trade request the endpoint creates the PENDING proposal (lands in `/api/proposals/pending`, never executes) but does NOT emit the structured `proposal` SSE event — it only names it in the assistant text — so the in-chat hand-off card can't render. Client is ready; serv just needs to emit the event. Repro + fix in `References/SERV_chat_stream_proposal_event_GAP.md`. |
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
