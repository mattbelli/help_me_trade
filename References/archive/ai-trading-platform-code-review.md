# AI Trading Platform — Comprehensive Code & Design Review

**Reviewer scope:** full Python backend (`brokers/`, `api/`, `ai/`, `db/`, `config/`) and the Next.js frontend integration layer (`frontend/lib`, `components`). Conducted by reading source directly rather than docs. The tone below is deliberately blunt — you asked for it, and money is involved.

---

## 1. Executive Summary

### What's genuinely good

- **Clean layering and separation of concerns.** `brokers/` (adapters) → `db/` (persistence) → `ai/` (intelligence) → `api/` (transport) is a sane, conventional structure. The `BrokerAdapter` Protocol in `brokers/base.py` with frozen dataclasses (`Account`, `Position`, `Order`) is exactly the right abstraction for multi-broker support, and the Schwab adapter slotting in behind it proves the abstraction holds.
- **Provider-agnostic LLM layer.** The `LLMClient` Protocol + factory (`ai/llm/factory.py`) cleanly supports OpenAI/DeepSeek/OpenRouter/Anthropic/Ollama/Mock. The `MockLLM` for offline dev and the mock default are excellent engineering hygiene.
- **Secrets are masked on the API surface** (`mask_settings`), `.env` is gitignored, and there's a thoughtful "preserve secret when masked" merge in `apply_settings_patch`.
- **Tracer-bullet discipline.** The roadmap-driven, incremental build is the right way to grow this, and it shows in how self-contained each module is.
- **Real streaming infrastructure** (SSE + a single shared upstream WebSocket hub in `market_stream.py` that fans out to many browser clients) — that's a non-trivial, well-considered design.

### Most critical issues (read these even if you read nothing else)

| # | Issue | Risk |
|---|--------|------|
| **C1** | Blocking (synchronous) `httpx` calls run inside the async event loop in `_poll_once`, market_stream baselines, and the insight stream | Freezes the entire server (all SSE/WebSocket clients) on every poll. Reliability. |
| **C2** | No order-execution layer and, more importantly, no risk-management/guardrail layer exists. The whole buy/sell + agent-trading goal sits on an empty foundation | When you add trading, there is nothing to stop a runaway loss, fat-finger, or hallucinated order. |
| **C3** | Money is `float` everywhere (`cash`, P&L, prices, quantities) | Rounding/precision errors in financial math. Unacceptable once real money is live. |
| **C4** | The poll loop dies permanently on any non-`BrokerError` exception | Silent loss of all data updates and rule evaluation with no recovery. |
| **C5** | LLM "tool use" is brittle keyword matching, and a chat message containing "analyze" can synchronously trigger a full multi-call deep-research run | Unpredictable latency, cost, and behavior; not real agentic orchestration. |
| **C6** | Secrets (incl. Schwab refresh token) stored unencrypted on disk; the API has zero authentication | Any local process / malicious webpage (DNS-rebinding) can read portfolio and change settings — and later, trade. |
| **C7** | Fragile `find('{')…rfind('}')` JSON parsing for every structured LLM output, with no schema validation or retries | Local/quantized models routinely break this; produces empty findings, bad rules, lost memories silently. |
| **C8** | Near-zero automated test coverage (two smoke scripts, no unit tests) on financial-logic code | Regressions in rules/settings/broker parsing will go unnoticed until they cost money. |

---

## 2. Architecture Review & Recommendations

### 2.1 Sync I/O inside async — the single biggest architectural flaw (C1)

`_poll_once` is async, but it calls broker methods that use synchronous `httpx.get`:

```python
# main.py
async def _poll_once() -> None:
    for broker_name in get_enabled_broker_names():
        ...
            broker = get_broker(broker_name)
            captured_at = save_snapshot(
                account=broker.get_account(),
                positions=broker.get_positions(),
                orders=broker.get_open_orders(),
                broker=broker_name,
            )
```

Each `get_account()` etc. blocks on `httpx.get(..., timeout=30.0)` (`brokers/alpaca.py`). Because this runs on the event loop, every concurrent SSE insight stream, every `/ws/market` client, and every API request stalls for the duration of those network calls. Schwab makes it worse — `get_account()` and `get_positions()` each call `_account_payload()`, so you do redundant blocking round-trips.

The same pattern hides in `market_stream._ensure_baselines` (calls `broker.get_stock_bars_batch` synchronously inside the async upstream loop) and `_stream_insight`.

**Recommendation:** Make brokers async-first, or offload to a thread.

Minimal fix:

```python
# api/main.py
captured_at = await asyncio.to_thread(
    save_snapshot,
    account=broker.get_account(),  # still sync — wrap the whole unit
    ...
)
```

Better: push the whole broker interaction into a thread:

```python
async def _poll_broker(broker_name: str) -> None:
    def _work() -> str:
        broker = get_broker(broker_name)
        return save_snapshot(
            account=broker.get_account(),
            positions=broker.get_positions(),
            orders=broker.get_open_orders(),
            broker=broker_name,
        )
    captured_at = await asyncio.to_thread(_work)
```

Long-term: give `AlpacaAdapter` an `httpx.AsyncClient` and `async def` methods (the data fetches especially), and poll brokers concurrently with `asyncio.gather`. This matters more once you have two brokers + market hours.

### 2.2 Brokers are reconstructed (and settings re-read from disk) on every call

`get_broker()` → `AlpacaAdapter.from_settings()` → `alpaca_config()` → `load_settings()` reads and merges `data/settings.json` from disk on every single request and every poll, and builds a new adapter with no connection pooling (`_request` opens a fresh TCP/TLS connection per call). This is wasteful and adds latency to hot paths (`/api/bars`, `/api/watchlist/market`, symbol search).

**Recommendation:**

- Cache settings in memory with explicit invalidation (you already call `clear_llm_cache()` on settings PUT — add a `clear_settings_cache()` companion). Reading and deep-merging JSON on every `broker_is_configured()` call (which is called in loops) is pure overhead.
- Hold a persistent `httpx.Client`/`AsyncClient` on the adapter instead of per-request clients.

### 2.3 `float` for money (C3)

Everywhere — `Account.cash: float`, `Position.unrealized_pl: float`, `StockBar.close: float`, rule facts summed with `sum(float(...))`. Floating point cannot represent `0.1` exactly; summing P&L across positions accumulates error, and once you place orders, `qty * price` rounding will disagree with the broker's books.

**Recommendation:** Use `decimal.Decimal` for monetary and quantity fields end-to-end (parse broker strings directly into `Decimal`, store as TEXT or scaled integers in SQLite, serialize to JSON as strings). At minimum, do this before live trading.

Example:

```python
from decimal import Decimal

@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    ...
```

### 2.4 The legacy HTML dashboard at `/`

`api/main.py` serves a full hand-rolled HTML/JS dashboard (lines 527–611) that duplicates the Next.js app. It's dead weight, a second source of truth, and a maintenance trap. Delete it or replace with a redirect/health page.

### 2.5 Migrations-as-init

`init_db()` / `init_ai_tables()` / `init_chat_tables()` are called on every DB operation, each running `CREATE TABLE IF NOT EXISTS` plus `ALTER TABLE … (except OperationalError: pass)`. This is both a performance drag and a schema-management anti-pattern — you can't tell schema version, and failed migrations are silently swallowed.

**Recommendation:** Run init once at startup (you already do in `lifespan`). Remove the `init_*()` calls scattered through every `db/` function. Introduce a tiny `schema_version` table (or adopt Alembic-lite / yoyo-migrations) so migrations are ordered, versioned, and auditable.

---

## 3. Code Quality & Structure Issues

### 3.1 Resilience of background loops (C4)

```python
# main.py
async def _poll_loop() -> None:
    while True:
        await _poll_once()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

`_poll_once` only catches `BrokerError`. If `save_snapshot` raises `sqlite3.OperationalError` ("database is locked"), or `evaluate_on_snapshot` throws, the exception propagates out of `_poll_loop`, the task dies, and polling stops forever with no restart and only a swallowed traceback. The data you trade on goes stale silently.

**Before/After:**

```python
async def _poll_loop() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Poll cycle failed; continuing")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

Apply the same guard to `_alert_symbol_loop`. Also consider a watchdog/health metric that flags "last successful poll > N seconds ago" so the UI can warn the user that data is stale (critical for a trading screen).

### 3.2 SQLite concurrency & atomicity

`_connect()` uses defaults: no `PRAGMA journal_mode=WAL`, no busy timeout. With the poll loop, reflection thread, research runs, and request threads all writing, "database is locked" is a matter of when, not if.

`save_settings()` does `SETTINGS_PATH.write_text(...)` — not atomic. A crash mid-write corrupts settings (and the corrupt-file handler silently falls back to defaults, so a user could lose broker config without warning). Watchlist mutations are read-modify-write on the same file with no locking → lost updates under concurrent requests.

**Recommendation:**

```python
def _connect(db_path=DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
```

For settings, write to a temp file and `os.replace()` (atomic on Windows and POSIX), and guard with a `threading.Lock`.

### 3.3 Duplicated code

- `_extract_symbol`/`extract_symbol` is defined twice (`ai/chat/tools.py` and `ai/research/sources.py`) with slightly different stopword lists. Consolidate into one util.
- The JSON-island parser (`raw.find("{") … rfind("}")`) is copy-pasted in `deep_graph._plan_queries`, `_extract_findings`, `_evaluate_gaps`, `memory_extract._parse_llm_memories`, and `reflection._parse_reflection`. Extract one well-tested `parse_json_object(raw)` helper (see §5.2).
- `DEFAULT_DB_PATH` / `ROOT` / `_connect` / `_now` are redefined in three `db/*` modules. Centralize in a `db/base.py`.

### 3.4 Endpoint style is inconsistent and untyped

API handlers take `body: dict = Body(...)` and hand-validate with `JSONResponse({"error": ...}, 400)`. This is verbose, error-prone, and produces no OpenAPI schema. You already depend on FastAPI — use Pydantic models for request bodies. You get validation, docs, and editor support for free:

```python
class CreateAlert(BaseModel):
    symbol: str
    price: float
    direction: Literal["above", "below", "cross"] = "cross"
    note: str = ""

@app.post("/api/price-alerts")
def api_create_alert(body: CreateAlert):
    ...
```

### 3.5 `api/main.py` is a 600-line god module

It mixes app lifecycle, three background loops, broker endpoints, watchlist CRUD, chart layout, bars, alerts, and an HTML page. Split into routers (`routers/watchlists.py`, `routers/market.py`, `routers/alerts.py`) the way you already did with `ai_routes.py`.

---

## 4. Risk Management & Safety Concerns

This is the section that matters most for a trading product, and it's where the codebase is thinnest.

### 4.1 There is no execution layer — and therefore no place where guardrails will live (C2)

`BrokerAdapter` only exposes `get_account`, `get_positions`, `get_open_orders`. There is no `place_order`, no `cancel_order`, no `replace_order`. That's fine for today's read-only diary, but your stated goal is buy/sell in-app and eventually autonomous agent trading. **Do not add `place_order` to the adapter and wire it to a button.** Design the safety layer first, because retrofitting risk controls onto a working "fire order" path is how people blow up accounts.

**Recommended architecture — a mandatory pre-trade risk gate:**

```
Agent/UI → OrderIntent → RiskEngine.validate() → [approve/reject/needs-confirmation]
                              │
                              ├─ max order notional / % of equity
                              ├─ max position concentration per symbol
                              ├─ daily loss limit / max drawdown kill-switch
                              ├─ max orders per minute (runaway-loop guard)
                              ├─ symbol allowlist / no-trade list
                              ├─ market-hours / halt check
                              └─ paper-vs-live explicit gating
                          → BrokerAdapter.place_order()  (only if approved)
                          → AuditLog.record(intent, decision, result)
```

Every order — human or AI — must pass through the same `RiskEngine`. No bypass path.

### 4.2 Nothing prevents accidental live trading (C6-adjacent)

The only thing separating paper from live is the `api_base_url` string. A single settings edit points you at `api.alpaca.markets` with real money. There's no explicit, loud "LIVE TRADING" mode flag, no confirmation, no visual distinction.

**Recommendation:** Add an explicit `trading_mode: "paper" | "live"` setting, require a typed confirmation to enable live, surface it prominently in the UI (red banner), and have the `RiskEngine` refuse live orders unless the mode is explicitly live. Default everything to paper.

### 4.3 The rules engine fires every poll with no debounce (alert fatigue + DB bloat)

```python
# engine.py
def evaluate_rules(facts, *, event_type="poll"):
    for rule in list_rules():
        ...
        if not _evaluate_node(conditions, facts):
            continue
        event = record_rule_event(...)   # fires EVERY 20s while condition holds
```

A rule like "drawdown > 5%" will record a new `rule_event` every single poll (every 20s) for as long as it's true — flooding the events table and (once notifications exist) spamming the user until they ignore alerts entirely. There is also no delivery mechanism: portfolio rules only `logger.info` + insert a row; nothing reaches the frontend (unlike price alerts, which push over WebSocket).

**Recommendations:**

- Add state-transition / cooldown semantics: only fire when a rule transitions false → true, and/or enforce a per-rule cooldown (`min_seconds_between_triggers`). Track last-fired state per rule.
- Wire portfolio rule triggers to the same WebSocket/SSE push path price alerts use, so "flag when rule broken" actually flags.
- Unknown operators currently return `False` and just log — a typo'd rule silently never fires. Surface rule validation errors at creation time.

### 4.4 `_crossed` price-alert logic has equality edge cases

`api/alert_checker.py` uses `prev < target <= current`. If `prev == target` exactly (price sitting on the line), an "above" cross won't register. Minor, but worth a unit test with boundary values — these are the bugs that erode user trust in alerting.

### 4.5 Prompt injection is an unmitigated path to bad decisions

`ai/research/sources.py::fetch_url` pulls arbitrary web page text and feeds it into the LLM that generates findings, which feed the report, which is stored as a memory, which is later retrieved into chat context. Today that's "just" analysis. Once an agent can trade, a crafted web page or news headline could steer the model's recommendations or (worse) tool calls. Treat all external content as untrusted: keep it in clearly delimited "DATA" sections, never in system/instruction position, and never let fetched content directly authorize an action.

---

## 5. Agent & LLM Workflow Improvements

### 5.1 Keyword "planning" is not agentic, and it's dangerous in a chat (C5)

```python
# tools.py
def plan_tools(user_text: str) -> list[tuple[str, dict]]:
    ...
    if any(word in lower for word in ("research", "deep dive", "analyze", "report on")):
        symbol = extract_symbol(user_text)
        planned.append(("run_research", {"topic": user_text, "symbol": symbol}))
```

**Problems:**

- Typing "analyze my risk" triggers a full deep-research LangGraph run — multiple LLM calls + live web fetches — synchronously inside the chat turn. The user waits, with no way to cancel, and it can cost real tokens/minutes.
- `extract_symbol` regex matches any 1–5 uppercase letters, so "USA", "CEO", "ETF", "AI" become "symbols." It then runs research/bars on garbage tickers.
- The model never decides which tools to use — so it can't not run a tool, can't chain them, and can't ask for clarification.

**Recommendation:** Move to real tool/function calling. Your OpenAI-compatible and Anthropic clients can pass a `tools=[...]` schema and let the model emit tool calls; Ollama supports tools for capable models too. Define tools with JSON schemas, run a proper tool-execution loop (model → tool call → result → model), and make `run_research` an async, cancellable, rate-limited background job that streams progress (you already have `astream_deep_research` and progress callbacks — reuse them) rather than blocking a chat turn.

### 5.2 Chat does not actually stream from the model

```python
# agent.py
response = llm.complete(final_messages, max_tokens=1200)
...
yield from stream.text_chunks(response)
```

The agent calls the blocking `complete()`, waits for the entire response, then fake-streams it 12 chars at a time. The user stares at a spinner for the whole generation, then sees a fast typewriter. You built `astream` on every provider — use it. (Also: `run_chat_stream` is a sync generator driven from an async endpoint, so even the fake streaming is being pumped through `await asyncio.sleep(0)` rather than truly async.) Convert the chat path to async + `astream` for genuine token streaming.

### 5.3 Context management / token budget (C7 + local-LLM relevance)

`_build_final_messages` dumps the entire `tool_results` list as pretty-printed JSON into the user message. If `run_research` ran, that includes the full report + sources; if a snapshot was fetched, up to 20 positions + 10 orders. There's no token budgeting. On a local 8B model with a 2–4k context this silently truncates the actual question.

The fragile JSON parser appears 5×. Centralize and harden it:

```python
import json, re

def parse_json_object(raw: str) -> dict | None:
    # strip ```json fences, then take the first balanced object
    raw = re.sub(r"```(?:json)?", "", raw)
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            try:
                return json.loads(raw[start:i+1])
            except json.JSONDecodeError:
                return None
    return None
```

…and prefer provider-native structured output (`response_format={"type":"json_object"}` for OpenAI-compat, `format: "json"` for Ollama) so you stop praying the model returns clean JSON.

### 5.4 Memory system: dual-write without reconciliation

`MemoryService` writes to SQLite always and mirrors to Mem0 when available, but search prefers Mem0 and falls back to SQLite keyword `LIKE`. IDs differ between stores, update does a Mem0 `add` (not update) creating duplicates, and there's no way to keep them consistent. For a "learns from the user" product this drift will produce contradictory recalled preferences. Decide on one source of truth (I'd suggest local SQLite + a real embedding index like sqlite-vec/FAISS for semantic search) rather than a half-synced pair. Keyword `LIKE` search is also not "semantic memory" — it'll miss paraphrases.

### 5.5 Determinism for financial reasoning

No temperature is set on any provider. For portfolio insights and rule/finding extraction you want low, fixed temperature (e.g. 0–0.3) for reproducibility and fewer hallucinations. Expose it per-task (creative research vs. deterministic extraction).

---

## 6. Performance, Scalability & Local Deployment

### 6.1 Polling is unconditional and market-hours-unaware

The poll loop hits the broker every 20s, 24/7, even when markets are closed, no positions exist, and no UI clients are connected. For Alpaca that's mostly fine; for Schwab (weekly-reauth OAuth, stricter limits) it's wasteful and the redundant double `_account_payload()` call doubles it. Add market-hours awareness and back off when idle/closed.

### 6.2 Research runs are unbounded and uncancellable

`/api/research/run` is a sync endpoint running a multi-iteration LangGraph (deep = 3 iterations × 6 queries, each doing portfolio+bars+memory+web with `fetch_url` network calls and several LLM calls). Running in FastAPI's bounded threadpool, a few concurrent deep runs can exhaust the threadpool and stall the whole app. No timeout, no per-user concurrency cap, no cancellation.

**Recommendation:** Make research a managed background task (even a simple `asyncio.Task` registry, or a job queue) with: max concurrency, per-run timeout, cancellation, and progress already available via your stream. The UI should poll/stream status, not hold a request open for minutes.

### 6.3 Symbol search loads the full asset universe into memory

`_tradable_symbols()` fetches all active US equities and caches them in a class variable for 24h, then does Python `startswith`/`in` scans per keystroke. It's cached, so OK-ish, but the first call is heavy and it's per-process. Fine for local single-user; revisit if you ever multi-process.

### 6.4 Local LLM (Ollama) specifics

- **Context window:** You never set `num_ctx`. Ollama defaults are small; your large JSON prompts will be silently truncated. Set `options.num_ctx` based on the model and the prompt budget, and measure prompt token counts.
- **JSON mode:** Pass `format: "json"` for extraction tasks — dramatically improves parse reliability on quantized models.
- **Keep-alive / warmup:** Cold model loads add many seconds. Use `keep_alive` and a startup warmup ping so the first insight/chat isn't painfully slow.
- **Model capability awareness:** A 3B quantized model will fail your JSON-extraction and tool-planning prompts far more than a 70B. Consider a capability tier in config and degrade gracefully (e.g., simpler prompts / heuristic fallback) for small local models — you already have heuristic fallbacks in `memory_extract`, generalize that pattern.
- **Streaming path:** `OllamaLLM.astream` spawns a thread that runs the blocking generator and pushes to a queue — acceptable, but it creates a fresh `httpx.Client` per call. Reuse a client.

---

## 7. Security & Compliance

### 7.1 No authentication on the API (C6)

The FastAPI app has no auth at all. It binds to localhost, but:

- Any other process/user on the machine can read your full portfolio and overwrite broker/AI credentials via `PUT /api/settings`.
- DNS-rebinding: a malicious website you visit can, in many setups, make your browser issue requests to `127.0.0.1:8000`. Your CORS uses `allow_origin_regex` for localhost with `allow_credentials=True`, which is permissive. Today the damage is data exfiltration; once trading exists, it's unauthorized trades.

**Recommendation:** Even for a local-first app, add a shared-secret/token check (a header the frontend includes), bind explicitly to `127.0.0.1`, validate the `Host` header against localhost/127.0.0.1 to defeat DNS-rebinding, and tighten CORS to the exact frontend origin. Make this non-optional before any execution endpoint ships.

### 7.2 Secrets at rest are plaintext

`data/settings.json` stores Alpaca/Schwab/LLM API secrets in cleartext; `data/schwab_token.json` holds an OAuth refresh token in cleartext. Masking only applies to API responses, not the files. On a shared or backed-up machine this is a real exposure.

**Recommendation:** Encrypt secrets at rest (OS keychain via `keyring`, or an app-level key from an env var / OS DPAPI on Windows). At minimum, document the risk and set restrictive file permissions. Never log secrets (currently OK — verify the Alpaca 401/error path doesn't echo headers; `_request` includes `response.text` in errors which is fine, but be careful never to log `self._headers`).

### 7.3 SSRF via `fetch_url`

`fetch_url` does `follow_redirects=True` and fetches any URL returned by web search. With a self-hosted SearXNG, results are external/uncontrolled. A malicious result could redirect to `http://127.0.0.1:8000/...`, `http://169.254.169.254/...` (cloud metadata if ever deployed), or internal hosts. Add an allow/deny policy: block private IP ranges and loopback, cap redirects, enforce `http(s)` scheme, and set a response-size limit (you cap text to 4000 chars but download the whole body first).

### 7.4 Compliance note

Once you trade real money and especially once an AI places orders, you'll want an immutable audit trail (who/what initiated each order, the model + prompt + inputs, the risk decision, broker response). Build the audit log alongside the execution layer (§4.1), not after. This is also invaluable for debugging "why did the agent do that."

---

## 8. Testing & Observability

### 8.1 Tests (C8)

There are no unit tests — only `scripts/test_ai_stack.py` and `scripts/test_ai_provider.py`, which are end-to-end smoke scripts (good to have, but they boot a server and use the mock LLM). The pieces most likely to silently corrupt money/decisions have zero coverage:

- `ai/rules/engine.py` operator logic and nested `all`/`any`/`not` evaluation.
- `config/settings.py` merge/mask/secret-preservation logic (this is genuinely tricky code — `_deep_merge`, `apply_settings_patch`, watchlist normalization).
- Broker response parsing (`alpaca.get_positions`, `schwab.get_positions` qty/side/PL math).
- `alert_checker._crossed` boundary conditions.
- The JSON-island parser.

**Recommendation:** Add pytest. Start with pure-function tests (rules engine, settings merge, alert crossing, broker parsing from canned JSON fixtures). These are fast, need no network, and protect exactly the logic that can lose money. Wire ruff + mypy (the code is already fully type-hinted — you'd get a lot of value cheaply) into CI.

### 8.2 Observability

- `logging` is used throughout but `logging.basicConfig()` is never called, so depending on how uvicorn is launched, your module INFO logs may not appear. Configure logging explicitly at startup (level, format, ideally JSON).
- No request IDs, no metrics, no tracing. For an agentic system you specifically want: per-research-run timing, per-LLM-call latency/token counts/cost, poll success/failure counts, and "time since last successful snapshot."
- Several broad `except Exception:` blocks swallow errors with at most a warning (mem0 init, `fetch_url`, settings load, `_broadcast_quote`). That's defensible at the edges, but make sure failures that affect correctness (e.g., research save failing, memory write failing) are surfaced, not just logged.
- Add a `/health` that actually reflects health (last poll age, broker connectivity, DB writable, LLM reachable) rather than a static `{"status":"ok"}`.

---

## 9. Prioritized Refactoring Roadmap (Effort × Impact)

Ordered by do-this-first value. "Impact" weighs financial-loss / reliability risk heavily.

| Priority | Item | Impact | Effort | Section |
|----------|------|--------|--------|---------|
| **P0** | Wrap background loops in try/except so polling can't die silently | 🔴 High | XS | 4.x / 3.1 |
| **P0** | Move blocking broker/LLM I/O off the event loop (`asyncio.to_thread` now; async clients later) | 🔴 High | S–M | 2.1 |
| **P0** | Enable SQLite WAL + busy timeout; atomic settings writes + lock | 🔴 High | S | 3.2 |
| **P0** | Add minimal API auth + Host-header check + tighten CORS | 🔴 High | S | 7.1 |
| **P1** | Design the RiskEngine + audit log + paper/live gating before any `place_order` | 🔴 High | M–L | 4.1/4.2 |
| **P1** | Rule trigger debounce/state-transition + deliver portfolio alerts to UI | 🟠 Med-High | S–M | 4.3 |
| **P1** | Switch money fields to `Decimal` (start at broker parse boundary) | 🟠 Med-High | M | 2.3 |
| **P1** | Add pytest unit tests for rules, settings merge, alert crossing, broker parsing | 🟠 Med-High | M | 8.1 |
| **P2** | Real LLM tool-calling; make `run_research` an async cancellable job, not a chat side-effect | 🟠 Med | M–L | 5.1 |
| **P2** | True token streaming for chat via `astream` | 🟠 Med | S–M | 5.2 |
| **P2** | Centralize + harden JSON parsing; use provider JSON mode; set temperature | 🟠 Med | S | 5.3/5.5 |
| **P2** | Cache settings in-memory; reuse httpx clients | 🟡 Med-Low | S | 2.2 |
| **P2** | Encrypt secrets at rest; SSRF guard on `fetch_url` | 🟡 Med-Low | S–M | 7.2/7.3 |
| **P3** | Pydantic request models across endpoints; split `api/main.py` into routers | 🟡 Low | M | 3.4/3.5 |
| **P3** | Move schema init out of per-call path; add versioned migrations | 🟡 Low | M | 2.5 |
| **P3** | Market-hours-aware polling; remove legacy HTML dashboard; structured logging | 🟡 Low | S | 6.1/2.4/8.2 |
| **P3** | Reconcile Mem0/SQLite memory into one source of truth + embeddings search | 🟡 Low | M | 5.4 |

---

## 10. Bonus: Suggested Next Development Steps

1. **Build the safety layer before the execution layer.** Implement `RiskEngine`, `OrderIntent`, and an append-only `audit_log` table. Add `place_order`/`cancel_order` to the `BrokerAdapter` Protocol only behind that gate. Default to paper, require explicit live opt-in. This unblocks "buy/sell in app" safely and is the prerequisite for everything autonomous.

2. **Backtesting / paper-replay harness.** Your roadmap mentions backtesting but there's no code for it yet. Before any agent trades autonomously, you need: a historical bar store, a deterministic strategy-evaluation loop, and metrics (P&L, max drawdown, Sharpe, win rate, exposure). This is also how you'll validate that an AI strategy is worth deploying — "learns and trades on its own" is only credible if you can replay it.

3. **Turn rules into the strategy-learning substrate.** You already have a JSON rules engine and a reflection loop that writes lessons. Connect them: let reflection propose rule changes (human-approved), track each rule's hit-rate and realized outcome, and surface "this rule has fired 12× and would have saved/cost $X." That's a concrete, low-risk path toward "AI helps develop strategy."

4. **A unified "agent run" abstraction with cost/latency telemetry.** Insights, research, reflection, and memory-extraction all instantiate the LLM and parse JSON ad hoc. A small `AgentRun` wrapper (inputs, prompt, tokens, cost, duration, parsed output, errors) gives you observability and a foundation for scheduled agent tasks (your "agent task schedule" goal).

5. **Decimal + currency types and a proper P&L module.** Realized vs. unrealized P&L, fees, and cost-basis tracking deserve their own tested module rather than `sum(float(...))` in the rules engine.

6. **Frontend: a prominent data-freshness + mode banner.** Surface "last updated Xs ago / STALE" and "PAPER vs LIVE." On a trading screen, silently stale data is a correctness bug, not a cosmetic one.

---

## Closing assessment

This is a well-structured, thoughtfully layered prototype with genuinely good instincts (clean adapters, provider-agnostic LLM, mock-first dev, shared market stream). The architecture will scale into the product you're describing. The gap is not structure — it's production-grade reliability and safety discipline: blocking I/O on the event loop, fragile background loops, float money, brittle LLM JSON/tool handling, and — most importantly — the complete absence of a risk/guardrail layer at exactly the moment you're about to add real-money execution. Fix the P0 items this week, design the `RiskEngine` before you ship a single order, and add tests around the financial logic. Do that, and you'll have a foundation you can trust an agent to operate on.

---

*Happy to turn any section into concrete PRs — the P0 loop-resilience + WAL + auth changes are small and can be implemented immediately.*
