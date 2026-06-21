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
