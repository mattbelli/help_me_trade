# Help Me Trade — Build Plan

**Single source of truth** for vision, current state, architecture, and what to build next.  
Supersedes the six docs in [`archive/`](archive/) (2026-06-16).

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
| **Chat** | Sessions (standard/private/quick); tool UI; controlled collapsibles |
| **Tests** | pytest: auth, db/WAL, poll health, structured JSON, tools, symbols, Schwab import |

### ◐ Partial / known gaps

| Area | Status |
|------|--------|
| **Chat** | Still on Python SSE + hand-rolled `UIMessageStream`; blocking `complete()` + fake typewriter; 3 confusing modes |
| **Research** | Mock web search when `SEARXNG_URL` unset; fetched article `content` not fed to findings |
| **Insights** | Raw snapshot JSON → one paragraph; no `PortfolioAnalytics`; no memory injection |
| **Memories** | Write-heavy; keyword `LIKE` search; dual Mem0/SQLite drift |
| **Rules** | 4 presets in UI; fires every poll; no WS delivery for portfolio rules |
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

| Item | Effort | Files |
|------|--------|-------|
| Atomic settings writes + lock | S | `config/settings.py` |
| Settings + broker adapter cache | S | `config/settings.py`, `brokers/factory.py` |
| Persistent `httpx` client on Alpaca | S | `brokers/alpaca.py` |
| Offload remaining sync broker I/O (`to_thread` / async) | S | `api/main.py`, `brokers/market_stream.py` |
| Encrypt secrets at rest | M | `config/secrets.py`, `brokers/schwab_auth.py` |
| GitHub Actions CI (ruff + pytest) | S | `.github/workflows/ci.yml` |

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
| Mark-to-market unrealized (open lots vs snapshot) | M | Optional calendar mode |
| Profit factor, hold time, behavioral flags for AI | M | `db/analytics.py` enrichment |
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

| Priority | Item | Effort |
|----------|------|--------|
| **P0** | `portfolio/analytics.py` — compute on poll; feed insights + chat context | M |
| **P0** | Stop silent mock research; loud “no real source” UI; wire SearXNG or news API | S |
| **P0** | Pass fetched `content` into findings (not just snippets) | S–M |
| **P0** | Semantic memory top-k on **every** chat/insight/research turn | M |
| **P1** | Snapshot diff / time-series (“exposure up vs last week”) | M |
| **P1** | Programmatic citations from findings→source map | S |
| **P1** | Expand rule facts (per-symbol, day P&L, quotes) | M |
| **P1** | Rule `trigger_on: transition` + cooldown; unify alert bus → WS | M |

---

### Phase 3 — Chat copilot rebuild

**Decision:** Next.js `app/api/chat/route.ts` + AI SDK `streamText` + optional **assistant-ui**.

| Step | Item | Effort |
|------|------|--------|
| A | Error visibility + don’t silently fall back to ephemeral mode | XS |
| B1 | Route handler + `toUIMessageStreamResponse` | M |
| B2 | Provider/key fetched from Python per request (single secret store) | S |
| B3 | Delete hand-rolled `UIMessageStream`; tools call Python HTTP | M |
| B4 | Modes → **Saved** (+ learn toggle) + **Temporary** | S |
| B5 | Rich tool cards (positions table, research progress) | M |
| C | Inject analytics + memories + rules every turn via `/api/context` | M |

**Feedback loop (Phase 3–4):**

- `strategy` memory type from brainstorming
- Visible “Learned: …” curation after turns
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
| SSRF guard on `fetch_url` | S |
| Mem0/SQLite → one memory store + embeddings | M |
| Backtesting harness (bars + metrics) | L |
| True `astream` for insights/reflection | S–M |

---

## 6. What to build next (recommended order)

For a solo developer, the highest leverage sequence:

1. **Finish Phase 0 leftovers** — settings atomicity, adapter cache, CI (1–2 days).
2. **Intelligence P0** — portfolio analytics + real research sources + memory retrieval (1 week). *Biggest “oh, it’s real” moment.*
3. **Chat Phase B** — migrate to Next route handler; fix modes (1 week).
4. **Decimal + RiskEngine design** — before any execution UI (2 weeks).
5. **Rules transition + alert bus** — make guardrails actually notify (3–4 days).

---

## 7. Key modules (quick reference)

| Concern | Path |
|---------|------|
| App entry | `api/main.py` |
| Trade ledger | `db/trades.py`, `imports/`, `trading/sync.py` |
| Performance analytics | `db/analytics.py`, `api/performance_routes.py` |
| Chat | `ai/chat/agent.py`, `frontend/components/agents/` |
| Tools | `ai/chat/tools.py` |
| Research | `ai/research/deep_graph.py`, `ai/research/sources.py` |
| Rules | `ai/rules/engine.py`, `frontend/lib/rules.ts` |
| Settings / brokers | `config/settings.py`, `brokers/factory.py` |
| Frontend API client | `frontend/lib/api.ts`, `frontend/lib/performance-api.ts` |

---

## 8. Success criteria (release gates)

### Platform

- [ ] SSE/WS responsive during broker polls
- [ ] Poll loop survives injected failures; `/health` reports stale data
- [ ] All API routes reject unauthenticated requests when `API_TOKEN` set
- [ ] `pytest` in CI on every push

### Copilot

- [ ] Chat streams tokens from AI SDK route (no hand-rolled protocol)
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
| 2026-06-16 | Merged six reference docs into this file; originals moved to `archive/` |

*Update this file when priorities shift or phases complete.*
