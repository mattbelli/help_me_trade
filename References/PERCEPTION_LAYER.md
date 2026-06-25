# Agent Perception Layer — design (2026-06-25)

*How the AI sees and acts on the app's data. The fix for "the copilot is blind to our own signals" — and
the PERCEIVE half of the Decision Engine. Build once, two consumers.*

## The principle (locked by founder)
- **READS are GENERAL.** The agent can perceive **all** app data sources through a small, general interface
  that **grows automatically** as we add features. **Never add a tool just to expose new data.**
- **WRITES are NARROW + curated.** The agent gets only a small, deliberate set of **"create useful things in
  the app"** actions. It can **NEVER write/change internal code, config, settings, or the database** beyond
  those explicit app-objects. The execution chokepoint stays sacred: propose → human approves → chokepoint.

So we only ever hand-wire a tool for a genuinely new **action** (rare). New **data** is free — the agent
already sees it through the perception layer.

---

## The READ layer (perception)

### 1. Context builder — `get_context(scope, ...)`
ONE tool returns a **normalized facts object** (deterministic numbers — "ground before generate", not raw
JSON dumps). Scopes:
- **symbol** → `{ quote (price, change%, session, ext), rvol, session_nodes (POC/VAH/VAL/PDH/PDL + prior-day
  H/L), technicals (vs 50/200-DMA, ATR, RSI, trend), fundamentals (pe/eps/div/mktcap), earnings (last/next),
  strategy_signals (which strategies fire + why), active_alerts }`
- **portfolio** → `{ accounts, positions, exposure, realized/unrealized P&L, open proposals }`
- **watchlist** → `{ list + per-symbol compact quote }`
- **market / regime** (future) → `{ SPY vs 200-DMA, VIX percentile, breadth }`

Built on the existing `signals/features.py` facts-builder + `signals/indicators.py` + the data the
`/api/watchlist/market`, `/api/session-node/levels`, `/api/bars`, `/api/snapshot` paths already assemble.
**Adding a new signal → it appears in this object automatically** (no new tool).

### 2. Generic resource query — `query(resource, filters?)`
Structured read over a **resource registry** for breadth beyond a symbol snapshot. Initial resources:
`watchlists`, `strategies`, `price_alerts`, `reports`, `proposals`, `positions`, `bars`, `earnings_calendar`,
`rvol_scanner`. Each registers **once** (name → schema → read fn, reusing `db/` + read endpoints). Returns
structured rows. **New resource = one registry line.**

### 3. Manifest / data dictionary — `list_capabilities()`
A self-describing catalog the agent is given so it **discovers** what it can read (scopes, resources, fields)
instead of us hardcoding question-shaped logic. This is what makes the layer general.

### 4. Text retrieval (RAG) — existing `search_memories` (+ research/news later)
RAG only for **unstructured text** (preferences, lessons, research notes, news). **Numbers always come from
1–3 (function-call/structured), NEVER RAG over prices** — vector RAG over numbers hallucinates (locked rule).

### Broad access ≠ everything-in-context
"Ingest as much as possible" = the agent can **pull** anything on demand through these few general tools —
**not** that we shove all data into every prompt. The manifest tells it what exists; it fetches what the
question needs. Keeps tokens sane and reasoning sharp.

---

## The WRITE layer (curated "create useful things")
A small allowlisted set, each one intentional + reviewed:
- `create_price_alert` / `update_price_alert` / `delete_price_alert` — notifications, safe (not orders).
- `propose_order` — routes to the chokepoint; **human approves**; no direct broker, no shorting, paper-gated.
- `propose_rule` — NL → validated RuleSpec → approve → armed.
- *(future, deliberate)* `create_report`, `create_task`.

**Explicitly NOT:** no code/config/settings writes, no raw DB mutation, no direct broker calls, no
auto-execute, no exposing the Alpaca trading toolset. Same safety model as today (CLAUDE.md).

---

## Architecture
- A server-side **agent perception service** (`ai/perception/`) — a **façade** over the existing `db/` +
  `signals/` + read endpoints. It has **no write paths** except by calling the curated write tools. The
  copilot tool layer (`ai/chat/tools.py`) exposes `get_context` / `query` / `list_capabilities` + the write
  tools and routes to this service.
- **It is the Decision Engine's PERCEIVE half** — the same facts object feeds `signals/decision_scorer.py`
  and proposals. Build it for the copilot now; the decision engine reuses it.
- **MCP later:** wrap the same service as an MCP server so external clients (Claude Desktop, etc.) get the
  same access. The façade is MCP-shaped; no rework needed.

---

## Build phases
1. **Context builder (symbol + portfolio scopes) + `list_capabilities` + wire into the copilot.** Immediately
   fixes the blind copilot ("what's RVOL/levels on NVDA?", grounds answers in our data).
2. **Generic resource-query registry** (watchlists / strategies / alerts / reports / proposals / bars / …).
3. **Curated write tools** (`create_price_alert` first) — so "set an alert at SPY's VAL" works end-to-end
   through the agent.
4. **Later:** market/regime context; the Decision Engine consumes the facts object; MCP external interface.

## ⚠️ Tool registration — TWO surfaces (gotcha)
Adding a copilot tool requires registering it in **both** places or the live chat won't see it:
1. **Python executor** — `ai/chat/tools.py` (`TOOL_DEFINITIONS` + `execute_tool`). This is what runs the tool.
2. **Live-chat frontend registry** — `frontend/lib/chat-tools.ts` (`buildChatTools`, an AI-SDK `tool()` whose
   `execute` calls `executePythonTool("<name>", input)`) **and** `frontend/lib/chat-toolbar-config.ts`
   (`CHAT_TOOL_OPTIONS`, which drives `DEFAULT_ENABLED_TOOLS` — so the tool is on by default).
The live chat builds its tool list from #2, not from the Python planner. (We hit this in phase 1: `get_context`
worked in Python but the chat said it "isn't available" until registered in #2.) Phase-3 write tools: same.

## Guardrails
- The perception service is read-only by construction; writes happen only through the explicit allowlisted
  tools. Chokepoint sacred; no shorting; paper-gated; LIVE blocked. Reviewed like any `trading/`-adjacent
  change where it touches the chokepoint.
