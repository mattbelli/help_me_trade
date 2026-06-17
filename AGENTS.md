# Help Me Trade — Agent Brief

Personal trading copilot: FastAPI + Next.js, Alpaca/Schwab, local SQLite, provider-agnostic LLM.

**Full roadmap:** [References/BUILD.md](References/BUILD.md)

## Before large work

1. Read §6 (what to build next) in [References/BUILD.md](References/BUILD.md)
2. Match existing patterns in the module map below
3. Never add order execution without `RiskEngine` + `ExecutionService` (Phase 4)

## Starting a new agent session

You do **not** need a special keyword. Cursor loads this file and [`.cursor/rules/help-me-trade.mdc`](.cursor/rules/help-me-trade.mdc) automatically in Agent mode.

**Minimal prompt** (context already loaded):

> Work on the next item in BUILD.md §6.

**Directed prompt** (pick a specific phase):

> @References/BUILD.md Implement Phase 0 leftovers: settings atomicity and CI.

**After finishing work**, ask the agent to update docs (or it should do this without being asked):

> Mark Phase 0 CI done in BUILD.md and sync AGENTS.md current state.

---

## Vision

A **personal trading copilot** — not a generic chatbot:

1. **See** portfolio, charts, and performance grounded in real trade history.
2. **Research** symbols with cited, data-backed reports (not mock snippets).
3. **Talk** through strategy and decisions; the AI remembers and recommends back over time.
4. **Guard** with programmable rules and (later) a mandatory risk gate before any order.

**North star loop:** conversation → structured strategy memory → proposed rule/backtest → tracked outcome → recommendation surfaced in chat.

---

## Stack

FastAPI (Python, port 8000) + Next.js (port 3001) · Alpaca + Schwab · local SQLite · provider-agnostic LLM.

---

## Architecture (keep this shape)

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

**Layers — extend, don't replace:**

| Layer | Role |
|-------|------|
| `brokers/` | `BrokerAdapter` protocol; frozen dataclasses; multi-broker |
| `db/` | Snapshots, trade ledger, chat, memories, rules, alerts |
| `ai/llm/` | Provider factory (mock, OpenAI, Anthropic, Ollama, …) |
| `ai/research/` | LangGraph plan → collect → analyze → synthesize |
| `trading/` | Broker sync today; **RiskEngine + ExecutionService** later |
| `api/` | REST + SSE + WebSocket market hub |
| `frontend/` | App shell, trading, performance, agents, settings |

**Locked chat decision:** Move the **conversation host** to Next.js `frontend/app/api/chat/route.ts` using Vercel AI SDK `streamText` + **assistant-ui** (AI-SDK adapter, not LangGraph). Python remains the **brain** — portfolio, memory, research, rules, execution. Node never places orders; it only calls authenticated Python endpoints.

**Patterns, not dependencies (see [BUILD.md §2](References/BUILD.md)):**
- **mem0 demo** → extract/store/retrieve/inject loop in **Python** (`/api/context`, `/api/memory/extract`); no hosted Mem0 cloud SDK in Node.
- **stockbroker demo** → Approve/Reject tool card UI in Phase 3; live `propose_order` → `ExecutionService` only after RiskEngine (Phase 4).
- Reject assistant-ui **LangGraph** runtime for chat; research may keep LangGraph.

---

## Current state (as of 2026-06-16)

### Shipped and working

| Area | What exists |
|------|-------------|
| **Brokers** | Alpaca + Schwab adapters; settings-driven enable; paper primary |
| **Polling** | Multi-broker poll; resilient loops + `poll_health`; WAL SQLite |
| **Auth** | `API_TOKEN` on API/WS; Host-header check; frontend `X-API-Token` |
| **Market** | Shared WebSocket hub; price alerts; chart grid |
| **Trade ledger** | Schwab JSON import; Alpaca/Schwab API sync on connect + hourly; dedup by `external_key` |
| **Performance** | `/performance` — periods, FIFO round-trips, calendar w/ $ labels, realized vs cash-flow toggle, charts |
| **AI tools** | `get_portfolio_snapshot`, `search_memories`, `start_research` (async job), `get_trade_history`, `get_performance_insights` |
| **LLM hygiene** | Shared `parse_json_object`; JSON planner for tools; research job queue |
| **Chat** | Next.js `app/api/chat/route.ts` + AI SDK streaming; **assistant-ui** Thread (`useTradingChatRuntime`); Python brain endpoints; Saved/Temporary modes; `propose_order` Approve/Reject card |
| **Memory loop** | Client `onFinish` → `POST /api/memory/extract`; **“Learned from this chat”** chips (Edit/Discard); server finalize + legacy `/api/chat` skip extract (`extract=False`) |
| **Tests** | pytest: 74 tests — auth, db/WAL, poll health, structured JSON, tools, symbols, Schwab import, portfolio analytics, intelligence P1, chat brain, memory extract |

### Partial / known gaps

| Area | Status |
|------|--------|
| **Chat** | assistant-ui Thread shipped; legacy Python SSE proxy for mock/anthropic; learned chips in-session only (not rehydrated on reload) |
| **Research** | Mock web labeled + Research page banner; full content in findings; configure `SEARXNG_URL` for live search |
| **Insights** | Analytics on poll + relevant memories each refresh |
| **Memories** | Token-ranked retrieval; turn curation UI (Edit/Discard chips); Mem0 when available; dual store drift; reload persistence TODO |
| **Rules** | Rich facts (per-symbol, drift, quotes); `trigger_on: transition` + cooldown; WS `alert` events on fire |
| **Money** | Still `float` in broker types and ledger (Decimal planned) |
| **Secrets** | Masked on API; plaintext on disk (encryption not done) |
| **Execution** | Read-only — no `RiskEngine`, no `place_order` |

---

## Guiding principles

1. **Safety before speed** — No live `place_order` until `RiskEngine` + audit log + auth exist.
2. **Ground before generate** — LLM sees analytics + memories + trade performance, not raw JSON dumps.
3. **One ledger** — Import (full history) + API sync (incremental) → deduped `trade_events`.
4. **Realized ≠ cash flow** — Performance UI distinguishes closed-trade P&L from buy/sell cash movement.
5. **Incremental phases** — Each phase leaves the app runnable.
6. **Tests ride along** — pytest per phase, not a final bolt-on.

---

## What to build next (recommended order)

1. **Chat Phase 3 (finish)** — rehydrate learned chips on thread reload (BUILD.md C4); optional: drop legacy Python SSE host.
2. **Phase 0 leftovers** — async broker I/O offload, secrets encryption (2–3 days).
3. **Decimal + RiskEngine design** — before any execution UI (2 weeks).

Phase details, effort estimates, and file targets: [References/BUILD.md §5–6](References/BUILD.md).

---

## Key modules

| Concern | Path |
|---------|------|
| App entry | `api/main.py` |
| Trade ledger | `db/trades.py`, `imports/`, `trading/sync.py` |
| Performance analytics | `db/analytics.py`, `api/performance_routes.py` |
| Portfolio analytics | `portfolio/analytics.py`, `db/sqlite.py` |
| Chat | `frontend/app/api/chat/route.ts`, `frontend/lib/use-trading-chat-runtime.ts`, `frontend/components/assistant-ui/thread.tsx`, `ai/chat/brain.py` |
| Memory extract + curation | `api/ai_routes.py`, `frontend/lib/memories.ts`, `frontend/lib/learned-memories-context.tsx`, `frontend/components/agents/turn-learned-memories.tsx` |
| Tools | `ai/chat/tools.py` |
| Memories / retrieval | `ai/memories/retrieval.py`, `ai/chat/context.py` |
| Research | `ai/research/citations.py`, `ai/research/deep_graph.py`, `ai/research/sources.py` |
| Rules | `ai/rules/engine.py`, `brokers/market_stream.py` |
| Settings / brokers | `config/settings.py`, `brokers/factory.py` |
| Frontend API client | `frontend/lib/api.ts`, `frontend/lib/performance-api.ts` |

---

## Safety and out of scope

**Do not wire a buy button or `place_order` until Phase 4 is complete:**

```
UI/Agent → OrderIntent → RiskEngine.validate() → approve | reject | needs_confirmation
                              → BrokerAdapter.place_order() (if approved)
                              → AuditLog
```

**Out of scope for now:**

- Schwab order execution (Alpaca paper first)
- LibreChat / Open WebUI adoption (UX inspiration only)
- assistant-ui **LangGraph** runtime + hosted **Mem0 cloud** SDK in Node (patterns in BUILD.md §2)
- Frontend `decimal.js`
- Full Alembic (lightweight `schema_version` is enough)
- Autonomous trading without human confirmation on agent orders

---

## Local dev

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

## Maintenance

When a phase completes or priorities shift, **the agent should update both files** before considering the task done:

1. **[References/BUILD.md](References/BUILD.md)** — mark items ✅, adjust §6 priorities, add doc history row if significant
2. **This file** — refresh **Current state** and **What to build next** to match BUILD.md

BUILD.md is the source of truth; AGENTS.md is the short briefing agents read first.