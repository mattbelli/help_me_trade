# Help Me Trade — Agent Brief

**★ THE BEST IDEAS WIN.** On architecture decisions, problem solving, and high-importance edits, run the
loop from the top of `CLAUDE.md`: **reason → check → reason → check** against something real, until checks
stop changing the answer — and when you think there isn't a solution, that's evidence about your METHOD,
not the problem: switch thinking methods (invert the question, turn walls into measurable variables,
inventory adjacent ingredients, zero-context pass, work backwards from the ideal) and go again.

**What this is:** An autonomous trading agent (not a generic chatbot) whose engine is one continuous loop: PERCEIVE → REASON → DECIDE → ACT → DELIVER → LEARN. FastAPI backend (Python, port 8000) + Next.js frontend (port 3001). Brokers: Alpaca (paper primary) + Schwab. Storage: local SQLite (WAL). LLM: provider-agnostic factory (Anthropic, OpenAI, DeepSeek, Ollama, mock). The dashboard is a cockpit to configure and supervise the agent; the loop runs server-side. **Roadmap (what to build next):** `References/BUILD.md`. **Loop architecture + tech research:** `References/AGENT_LOOP_RESEARCH.md`.

---

## Before large work

1. Read `References/BUILD.md §6` (current priorities)
2. Match existing patterns in the module map below
3. Never wire order execution without `RiskEngine` + `ExecutionService` (Phase 4)

---

## Codebase map

### Backend

| Subsystem | Key files | Purpose |
|---|---|---|
| **App entry** | `api/main.py` | FastAPI app, mounts all routers, starts background loops (`_poll_loop`, `_signal_scan_loop`, reflection scheduler) |
| **API routes** | `api/ai_routes.py`, `api/performance_routes.py`, `api/trade_routes.py`, `api/alert_checker.py` | REST + SSE endpoints for chat, memory, performance, trades, alerts |
| **Auth/middleware** | `api/auth.py`, `api/middleware.py`, `api/deps.py` | `API_TOKEN` bearer check, host-header guard, dependency injection |
| **Brokers** | `brokers/alpaca.py`, `brokers/schwab.py`, `brokers/base.py`, `brokers/factory.py`, `brokers/market_stream.py`, `brokers/errors.py` | `BrokerAdapter` protocol; frozen dataclasses; Alpaca WS quote hub + ring buffer; multi-broker factory |
| **Config** | `config/settings.py`, `config/ai_providers.py`, `config/research.py` | Pydantic settings loaded from `.env`; broker enable flags; LLM provider config |
| **DB — ledger** | `db/trades.py`, `db/sqlite.py`, `db/base.py` | `trade_events` table, WAL mode, schema versioning |
| **DB — analytics** | `db/analytics.py`, `db/realized_pnl.py`, `db/fifo_round_trips.py`, `db/chart_features.py` | Period P&L, cash-flow vs realized toggle, FIFO pairing (unreliable — see gotchas) |
| **DB — AI/behavioral** | `db/ai_tables.py`, `db/behavioral.py`, `db/chat_sessions.py` | Rules, alerts, memories, insights, reflection, paper orders, chat sessions |
| **DB — corporate actions** | `db/corporate_actions.py` | Splits/dividends affecting cost basis |
| **Imports** | `imports/schwab_transactions.py`, `imports/alpaca_activities.py`, `imports/base.py`, `imports/money.py`, `imports/option_symbol.py` | Full-history JSON import; `Decimal` parsing; dedup by `external_key` |
| **Trading** | `trading/sync.py` | Broker sync on connect + hourly; read-only today |
| **Portfolio** | `portfolio/analytics.py` | Exposure, concentration, drift flags used by DECIDE stage |
| **Signals** | `signals/indicators.py`, `signals/features.py` | SMA/EMA/RSI/ATR/crosses; `build_indicator_facts` → flat fact dict for LLM |
| **Signals — rules** | `signals/rule_schema.py`, `signals/scan.py` | `RuleSpec`, `METRIC_REGISTRY` (~40 metrics), condition evaluation, transition triggers + cooldown |
| **Signals — backtest** | `signals/backtest.py` | Rule backtesting against historical bars |
| **Signals — context** | `signals/trade_context.py` | Per-trade signal context capture |
| **AI — LLM** | `ai/llm/factory.py`, `ai/llm/base.py`, `ai/llm/structured.py`, `ai/llm/config.py` | Provider factory (anthropic/openai/deepseek/openrouter/ollama/mock); `parse_json_object` shared util |
| **AI — chat** | `ai/chat/brain.py`, `ai/chat/agent.py`, `ai/chat/tools.py`, `ai/chat/context.py`, `ai/chat/stream.py` | Python "brain": tool dispatch, streaming, portfolio/memory/research/rule tools; **`trading` toolset never exposed to chat** |
| **AI — memory** | `ai/memories/retrieval.py`, `ai/chat/memory_extract.py` | Token-overlap retrieval (RAG placeholder); extraction from conversation turns |
| **AI — research** | `ai/research/deep_graph.py`, `ai/research/jobs.py`, `ai/research/citations.py`, `ai/research/sources.py` | Async research job queue; LangGraph-ish plan→collect→analyze→synthesize |
| **AI — rules** | `ai/rules/engine.py` | `evaluate_rules` against snapshot + event facts; fires WS `alert` events |
| **AI — reflection** | `ai/reflection/service.py`, `ai/reflection/scheduler.py` | Periodic background reflection over trade history |
| **AI — insights** | `ai/insights.py`, `ai/retrospective.py`, `ai/perf_context.py` | Analytics-grounded insight generation; retrospective summaries |

### Frontend

| Subsystem | Key files | Purpose |
|---|---|---|
| **Chat runtime** | `frontend/app/api/chat/route.ts`, `frontend/lib/use-trading-chat-runtime.ts` | Next.js route using Vercel AI SDK `streamText`; calls Python brain endpoints |
| **Chat UI** | `frontend/components/assistant-ui/thread.tsx`, `frontend/components/agents/` | assistant-ui Thread; Approve/Reject order card; turn-learned-memories chips |
| **Memory** | `frontend/lib/memories.ts`, `frontend/lib/learned-memories-context.tsx`, `frontend/components/agents/turn-learned-memories.tsx` | Client-side extract trigger `onFinish`; Edit/Discard chips |
| **Workspace shell** | `frontend/components/workspace/workspace-grid.tsx`, `frontend/components/workspace/workspace-toolbar.tsx`, `frontend/components/workspace/command-console.tsx`, `frontend/components/workspace/copilot-sidebar.tsx` | Widget-grid cockpit; bottom command console; right-rail copilot dock |
| **Workspace state** | `frontend/lib/workspace/use-workspace-store.ts`, `frontend/lib/workspace/widget-registry.tsx`, `frontend/lib/workspace/grid-metrics.ts`, `frontend/lib/workspace/channels.ts`, `frontend/lib/workspace/use-channel-store.ts` | Grid layout, widget registry, channel system, grid dimension math |
| **Performance** | `frontend/app/performance/`, `frontend/components/performance/`, `frontend/lib/performance-api.ts` | Period P&L, calendar, FIFO charts, realized vs cash-flow toggle |
| **API client** | `frontend/lib/api.ts`, `frontend/lib/trades-api.ts`, `frontend/lib/research.ts`, `frontend/lib/rules.ts` | Typed fetch wrappers calling FastAPI |
| **Settings** | `frontend/components/settings/`, `frontend/lib/ai-settings.ts` | Provider/model config, broker settings |

---

## Agent loop → file map

| Stage | Files |
|---|---|
| **PERCEIVE** | `brokers/market_stream.py` (WS hub + ring buffer), `brokers/alpaca.py` (snapshot/bars), `signals/indicators.py` + `signals/features.py` (fact dict), `api/main.py` `_poll_loop` + `_signal_scan_loop` |
| **REASON** | `ai/llm/factory.py` (provider), `ai/chat/brain.py` + `ai/chat/agent.py` (stream + tool planner), `ai/chat/context.py` (grounding), `ai/memories/retrieval.py` (retrieval), `ai/insights.py` |
| **DECIDE** | `signals/rule_schema.py` + `signals/scan.py` (rule candidates), `ai/rules/engine.py` (evaluate), `portfolio/analytics.py` (exposure/risk flags), `ai/chat/tools.py` `propose_order` |
| **ACT** | `trading/sync.py` (read-only today) — **RiskEngine + ExecutionService not yet built; this is the bottleneck** |
| **DELIVER** | `api/alert_checker.py`, `brokers/market_stream.py` WS broadcast, `frontend/lib/workspace/use-alert-stream.ts`, `frontend/components/workspace/notification-banner.tsx` |
| **LEARN** | `ai/chat/memory_extract.py`, `ai/memories/retrieval.py`, `ai/reflection/service.py`, `db/behavioral.py`, `db/ai_tables.py` |

---

## Key conventions — an agent must know these

**Money is `Decimal`** (stored as SQLite TEXT, never REAL). `imports/money.py` centralizes parsing. Broker types still use `float` — conversion is a known gap.

**Realized P&L ≠ FIFO round-trips.** Net-cash truth (`db/realized_pnl.py`) is reliable. FIFO round-trips (`db/fifo_round_trips.py`) are best-effort and break on: date-only export timestamps (intraday scalps pair wrong), shorts, pre-import-window positions. The Performance UI lets the user toggle between the two views; don't treat FIFO numbers as authoritative.

**Coding tasks go to Composer, not hand-edits.** Dispatch via:
```
node tools/composer-bridge/dispatch.mjs --brief <path-to-brief-file>
```
One run at a time. Logs to `tools/composer-bridge/last-run.log`. Brief files live in `References/`. Only hand-edit for trivial one-liners or when Composer is inappropriate.

**Never expose the `trading` toolset to the chat agent.** Order execution is a separate toolset gated behind RiskEngine (not built). The chat agent has: portfolio, memories, research, web_search, propose_order (card only), create_rule (arms immediately).

**LLM proposes; deterministic code disposes.** The LLM never holds account state. Hard risk limits live in deterministic code, not prompts.

**Tests:** `pytest` — run targeted subsets and pipe through `| tail` to avoid drowning in output. 230+ tests cover auth, db/WAL, poll health, structured JSON, tools, Schwab import, analytics (realized/behavioral/trade-context), rules/backtest, retrospective + honest-perf guardrails, chat brain, memory extract/dedup.

**Secrets** in gitignored `.env` (repo root) and `frontend/.env.local`. Never commit either. `data/` is gitignored — real ledger SQLite and user trade export files live there.

**Chat host is Next.js** (`frontend/app/api/chat/route.ts`, Vercel AI SDK). Python is the brain — it never hosts the conversation. Node never places orders; it only calls authenticated Python endpoints.

**No LangGraph runtime for chat.** Research may keep LangGraph. Reject assistant-ui LangGraph adapter. No hosted Mem0 cloud SDK in Node.

---

## Run commands

```powershell
# Backend (repo root, activate venv first)
.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend
npm run dev        # port 3001  (or: bun run dev)
```

Full details + env knobs: `References/STARTUP.md`.

If port 8000 is busy: `netstat -ano | findstr ":8000"` → `taskkill /PID <pid> /F`.

---

## Maintenance

When a phase completes or priorities shift, update **both** before closing:
1. `References/BUILD.md` — mark items done, adjust §6 priorities
2. This file — refresh Current state and loop scorecard

BUILD.md is source of truth; this file is the fast brief agents read first.
