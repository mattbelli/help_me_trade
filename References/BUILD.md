# Help Me Trade — Build Plan

Agent onboarding: see root [`AGENTS.md`](../AGENTS.md). Update both when phases complete.

**Single source of truth** for current state, architecture, and what to build next.  
Supersedes the six docs in [`archive/`](archive/) (2026-06-16).

**Companion doc — [`IDEA_BANK.md`](IDEA_BANK.md):** the founder's committed product vision + design decisions (the *why* and *what*). This roadmap is the *how & when*. Most idea-bank items **are** things we'll build — their **ordering** lives in IDEA_BANK § "Build sequencing" and is reflected in §5–6 below. Keep deep rationale/vision in IDEA_BANK; keep the sequenced plan here.

**Stack:** FastAPI (Python, port 8000) + Next.js (port 3001) · Alpaca + Schwab · local SQLite · provider-agnostic LLM.

---

## 1. Product vision

A **personal trading copilot** — not a generic chatbot:

1. **See** portfolio, charts, and performance grounded in real trade history.
2. **Research** symbols with cited, data-backed reports (not mock snippets).
3. **Talk** through strategy and decisions; the AI remembers and recommends back over time.
4. **Guard** with programmable rules and (later) a mandatory risk gate before any order.

**North star loop:** conversation → structured strategy memory → proposed rule/backtest → tracked outcome → recommendation surfaced in chat.

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
| First-run onboarding + agent charter | M | Name the agent; write charter `.md`; seed memory (§2.1) |

### Phase 7 — Proactive agent, tasks, notifications & gateway

The agent comes alive and becomes reachable. Detail: [`IDEA_BANK.md`](IDEA_BANK.md) §2, §4, §7.6, §9 Tier 2.

| Item | Effort | Notes |
|------|--------|-------|
| Headless core + gateway + auth | L | Deployable service; gateway auth (tokens/TLS/device pairing); assume remote clients |
| Secrets-at-rest encryption | M | Graduates from Phase 0 — required once reachable beyond localhost |
| Proactive engine + Tasks scheduler | L | Background watch loop; scheduled prompts (7AM/4PM); event triggers |
| Notifications (in-app + Telegram/Discord interactive) | M | Top-down notification widget; **Accept/Decline from phone**; per-channel config; optional desktop sound |
| Chart-level monitoring | M | Draw-on-chart + plain-language → watchable conditions |
| Source scripts (X/Reddit via LLM search) | M | Crafted-search ingestion; prototype reliability first (no paid APIs to start) |

**Signal & Automation Engine (the substrate — build first within Phase 7).** Detail: [`IDEA_BANK.md`](IDEA_BANK.md) §7.10–7.12. Most of this is backend / **pytest-verifiable** (remote-friendly); node-UI + chart drawing need visual review.

| Item | Effort | Notes |
|------|--------|-------|
| Indicator/feature layer | M | ✅ **SHIPPED** (#13 `signals/indicators.py` + #14 `signals/features.py` facts-builder). The vocabulary; extends the existing rules engine's facts. |
| Declarative node-graph rule model + validator | M | ✅ **SHIPPED** (#16 `signals/rule_schema.py` — validated structured rules that **compile to the existing engine's condition dict**, never `exec`'d code). |
| Evaluator: streaming + scheduled scan | M | ◐ pure `evaluate_condition` exposed (#15); the **streaming + scheduled-scan runtime wiring** (fetch bars → merge indicator facts → fire alerts) is pending — needs live data to verify. |
| Backtest runner | M | ✅ **SHIPPED** (#15 `signals/backtest.py` — replay a rule over history → fire events). |
| AI confirm-to-save authoring (`propose_rule`) | M | NL → validated graph → approval card (the `propose_order` pattern) |
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

Full vision-level ordering (Tiers 0–5) lives in [`IDEA_BANK.md`](IDEA_BANK.md) §9. **Tier 0 "door-open seams" are honored continuously** (route all orders through the future execution gate; treat backend as a headless core w/ remote-ready auth; keep storage behind `db/`; feed-agnostic strategy boundary; design-token themes; files-vs-DB split + precise history). Read-only UX/agent work (Phases 6–8) can ship **before** the live-trading gate (Phase 4).

For a solo developer, the highest-leverage near-term sequence:

1. **Chat Phase 3 (finish)** — rehydrate learned chips on thread reload (C4); optional: drop legacy Python SSE host when all providers route through Next.
2. **Phase 0 leftovers** — async broker I/O offload (secrets encryption can defer to Phase 7 when the gateway needs it).
3. **Phase 6 — Shell pivot** — bottom command console v1, theme/token system, widget dashboard v1, onboarding + charter.
4. **Phase 7 — Agent alive** — the **Signal & Automation Engine** (indicator layer → declarative node-graph rule model → evaluator → backtest; the substrate for alerts/scans/chart studies/AI-proposals, mostly remote/pytest-verifiable), headless core + gateway, proactive engine + tasks, interactive notifications, chart-level monitoring.
5. **Phase 8 — Tauri desktop shell** — once the web UX + core/gateway are solid.
6. **Phase 4 — Money & safety** — `Decimal` + `RiskEngine` + `ExecutionService`, **before any live execution UI** (do whenever live trading is the goal; non-execution work above doesn't depend on it).

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
- Autonomous trading without human confirmation on agent orders

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
