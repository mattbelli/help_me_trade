# Architecture — a guided tour

*A plain-English walkthrough of how `help_me_trade` is built, for reading top-to-bottom (incl. on mobile).
Companion to `AGENTS.md` (the terse map) and `BUILD.md` (the plan/history). Generated from a read-only audit
2026-06-20.*

The app is an **autonomous trading agent** built around a loop —
**PERCEIVE → REASON → DECIDE → ACT → DELIVER → LEARN**. Backend is Python/FastAPI on a single SQLite file
(`data/trading.db`); frontend is Next.js. Below, each subsystem in the order data flows through it.

---

## 1. Brokers + market data (`brokers/`, `signals/`) — PERCEIVE

How the app sees the market and the account.

- **`brokers/base.py`** — the broker-agnostic data model (`Account`, `Position`, `Order`,
  `PlaceOrderRequest`, `PlaceOrderResponse`) and the `BrokerAdapter` Protocol. Any adapter that duck-types
  the interface works. *(Note: these dataclasses hold money as `float` — fine for display, but the
  authoritative ledger/execution paths use Decimal — see §4.)*
- **`brokers/alpaca.py`** — the primary live adapter (httpx, one sync + one async client). Hits Alpaca REST
  for account/positions/orders (snapshot fired concurrently via `asyncio.gather`), order placement
  (`POST /v2/orders` with `client_order_id` idempotency + 422 recovery), bars/quotes, options snapshots, and
  the tradable-asset list (24h class-level cache).
- **`brokers/schwab.py` + `schwab_auth.py`** — read-only Schwab adapter (account/positions/orders) over the
  `schwab-py` lib, plus its OAuth token lifecycle. Order placement raises `NotImplementedError` — Schwab is
  data-in only.
- **`brokers/factory.py`** — `get_broker(name)` with a singleton cache; the one place adapters are built.
- **`brokers/market_stream.py`** — the **WebSocket hub** (`market_hub` singleton). Holds one upstream Alpaca
  data socket and fans quotes out to N browser clients. New subscriptions get cached quotes immediately, then
  back-fill missing symbols from the bars API. Reconnects with exponential backoff. On each trade tick it
  also runs price-alert checks. `quote_snapshot()` is how the risk engine reads live prices with no API call.
- **`signals/`** — the declarative rules/indicators engine (`scan.py`, `indicators.py`, `features.py`,
  `rule_schema.py`, `trade_context.py`). Periodically fetches daily bars for watchlist + position symbols,
  merges with portfolio facts, and evaluates rules; also builds per-trade market context for retrospectives.

**Flow:** `market_hub.start()` runs a background upstream loop; browser subscribes → cached quotes + bar
back-fill → live ticks update in-memory `QuoteState` + broadcast. Separately, the poll loop snapshots the
account every 20s, and the signal scan evaluates rules on a timer.

---

## 2. Persistence (`db/`) — the single source of truth

Everything lives in one SQLite file (`data/trading.db`, path via `TRADING_DB_PATH`).

- **`db/base.py`** — the shared connection factory (WAL mode, busy timeout, `Row` factory) + `now_iso()`.
- **`db/sqlite.py`** — live **broker snapshots** (`poll_runs`, `account/position/order_snapshots`,
  `snapshot_analytics`). Written by the poll worker, read by the agent + dashboard. *(Money here is `REAL`/
  float — see audit.)*
- **`db/trades.py`** — the core **trade ledger** (`trade_events`, `trade_accounts`, import batches). All money
  as **TEXT/Decimal**. Dedupes on import by external key. The base everything analytic is computed from.
- **`db/order_executions.py`** — the **audit ledger** for every order the agent touches (dry_run/paper/live),
  qty/price as TEXT. Written by the execution engine, read by risk + the fill reconciler.
- **`db/ai_tables.py`** — AI state: `memories` (Jaccard-dedup on write), `research_artifacts`,
  `reflection_runs`, `rules`, `rule_events`.
- **`db/realized_pnl.py`, `behavioral.py`, `fifo_round_trips.py`, `corporate_actions.py`** — pure in-memory
  computation (no tables). Net-cash realized P&L, behavioral patterns, FIFO cost-basis (split-adjusted), and
  the split table. **Computed at query time from the ledger, not materialized.**
- **`db/analytics.py`** — orchestrator: pulls events → runs FIFO/realized/behavioral → `performance_dashboard`
  (for the UI) and `ai_performance_context` (compact LLM bundle).
- **`db/paper_orders.py`, `chat_sessions.py`, `chart_features.py`** — recorded proposals, chat history, and
  chart-layout/price-alert UI state.

**Persisted:** broker snapshots, the trade ledger, order-execution audit, AI memory/rules, chat. **Computed
on demand:** realized P&L, FIFO round-trips, behavioral analysis, split adjustments.

---

## 3. The agent brain (`ai/`) — REASON / DELIVER / LEARN

- **`ai/chat/intent.py`, `alert_intent.py`, `review_intent.py`** — regex classifiers that detect order /
  alert / performance-review intent and conditionally inject extra system-prompt instructions.
- **`ai/chat/agent.py`** — the system prompt (now with injected **current ET date** + market session),
  the legacy `/chat` streaming runner, and turn persistence.
- **`ai/chat/brain.py`** — the adapter between the Next.js AI SDK and the Python brain: assembles turn context
  (base prompt + intent blocks + retrieved memories), resolves the LLM runtime config, runs tools, finalizes.
- **`ai/chat/tools.py`** — the tool catalog + dispatcher. Read-only tools (snapshot, memories, trade history,
  performance insights, web search, **`get_option_quote`**) and the write tools (**`propose_order`**,
  `create_rule`). `propose_order` is where an approved proposal becomes an `OrderIntent` and enters the
  trading chokepoint; it handles equity + option, buy + sell-to-close (with a held-position pre-check).
- **`ai/chat/memory_extract.py` + `ai/memories/`** — post-turn learning. Extracts `{preference, lesson}` pairs
  (LLM + heuristic fallback), dedupes (semantic token-overlap), and persists. mem0 is optional; SQLite is the
  source of truth. Gated to skip alert/review turns (avoids learning fabricated preferences).
- **`ai/retrospective.py`** — the offline "analyze my history → propose strategy" report (train/OOS split,
  rule backtest on real bars, Markdown + HTML artifacts).
- **`ai/perf_context.py`** — sanitizes performance data before the LLM sees it (leads with reliable net-cash
  realized P&L, flags unreliable FIFO).
- **`ai/insights.py`, `ai/llm/factory.py`** — portfolio insight paragraphs; the provider router (Anthropic/
  OpenAI-compatible/Ollama/Mock, cached, per-request override).

**Chat turn flow** (ONE Python brain since phase 1b; the TS route is a pass-through proxy): user msg →
`POST /api/chat` → `assemble_system_prompt` (`ai/chat/loop.py` — base prompt + order/alert intent blocks +
memories + journal) → `run_chat_loop` with native tool-calling → `execute_tool` (approval tools stop and
surface a card instead) → `finalize_turn` (persist + extract memories).
The retired TS host's `build_turn_context` / `run_tool` / `llm_runtime_config` and their endpoints were
deleted 2026-07-30 (card `chat-dead-ts-surface`).

---

## 4. The execution spine (`trading/`) — ACT  *(the safety-critical core)*

The fail-closed chokepoint. **One un-bypassable path; deny by default; paper before live; Decimal-only.**

- **`trading/models.py`** — `OrderIntent` (the proposal), `RiskCheckResult` (verdict + per-check audit dict),
  `ExecutionResult`, `RiskRejectedError`. Pure value objects.
- **`trading/risk_engine.py`** — `validate(intent)`: the ordered gauntlet — kill switch → option guards
  (limit-only, OCC, expiry) / equity sanity + tradable → qty bounds → snapshot freshness (≤5min, TOCTOU
  guard) → **sell-to-close held-position check** → buying power (buys) → value cap (buys) → concentration
  (buys) → open-orders cap → daily cap. Options self-enforce the ×100 multiplier. First failure denies.
- **`trading/execution_service.py`** — `submit(intent)`: **the only code that POSTs to a broker.** Writes a
  `pending` audit row *before* anything, validates, then branches on `execution_mode`: `dry_run` (log only) /
  `paper` (real Alpaca paper order) / `live` (blocked). Every attempt is audited.
- **`trading/fill_tracker.py`** — poll-based reconciliation: advances `pending`/`submitted` rows toward
  terminal states (filled/canceled) + recovers orphans by `client_order_id`, broadcasting fill alerts.
- **`trading/sync.py`** — historical trade-ledger sync from broker activity APIs (not live-order flow).

**Order flow:** chat `propose_order` → `OrderIntent` → `ExecutionService.submit` → `pending` audit row →
`RiskEngine.validate` (10 checks) → dry_run log / paper POST / live-blocked → `submitted` row + alert →
`fill_tracker` polls to `filled`. **A sell can only reduce a held long — no path opens a short.**

---

## 5. API + config + imports (`api/`, `config/`, `imports/`, `portfolio/`)

- **`api/main.py`** — the FastAPI app. `lifespan` inits all tables, starts `market_hub`, and launches the
  background loops: broker poll (snapshots + rule eval + paper reconciliation), signal scan, alert-symbol
  subscription, reflection journal, hourly trade sync. Mounts the ai/trade/performance routers + inline
  dashboard/settings/watchlist/chart routes.
- **`api/auth.py` + `middleware.py`** — token auth (header/bearer/query), CORS origins, allowed-hosts
  (DNS-rebind defense). `SecurityMiddleware` gates every request globally; only `/health` is exempt.
- **`api/ai_routes.py`, `trade_routes.py`, `performance_routes.py`** — the AI features, trade ledger + Schwab
  import, and performance/retrospective endpoints.
- **`config/settings.py`** — layered settings (defaults → env → `data/settings.json`), thread-safe cache +
  lock, atomic writes, masked secrets. `apply_settings_patch` is the only mutation path. Holds the `trading`
  block (execution_mode, kill switch, risk caps).
- **`config/ai_providers.py`, `research.py`** — provider metadata; SearXNG URL resolution.
- **`imports/`** — `money.py` (Decimal parsing), `option_symbol.py` (Schwab + OCC formats),
  `schwab_transactions.py` / `alpaca_activities.py` (broker → `ParsedTradeEvent` → ledger).
- **`portfolio/analytics.py`** — pure snapshot → weights/concentration/drift/risk-flags; run after each poll.

---

## 6. Frontend (`frontend/`) — the workspace + copilot

- **App shell** (`app/layout.tsx` → `AppShell`): mounts the snapshot poller (20s) + price-alert monitor.
- **Workspace** (`app/workspace/page.tsx`): toolbar (broker status, ET clock, portfolio numbers) +
  notification banner + the grid + copilot sidebar + command console. Layout persisted to `localStorage`
  via a Zustand store.
- **Widget system** (`lib/workspace/widget-registry.tsx` + `react-grid-layout`): account/orders/positions/
  equity-curve/watchlist/chart/price-alerts/scans/memory. Channel-aware widgets (watchlist↔chart) link by
  color channel.
- **Copilot** (`CopilotSidebar` → `@assistant-ui/react` Thread): runtime over the Vercel AI SDK pointed at
  the Next.js `/api/chat` route, which proxies the Python brain for context/tools/finalize. Order proposals
  render as inline approve/reject cards.
- **`lib/api.ts`** — the single client-side REST/WS client (adds `X-API-Token`, 20s timeout). `lib/
  python-server.ts` is the server-side counterpart used in the Next.js route handlers.

**Backend comms:** REST to `NEXT_PUBLIC_API_URL` + one WebSocket (`/ws/market`) for live quotes/alerts; the
`/api/chat` route handler bridges to Python for the LLM loop.

---

*For known issues + cleanup backlog, see `CODE_AUDIT.md`.*
