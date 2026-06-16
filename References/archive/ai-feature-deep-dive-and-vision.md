# AI Trading Platform — Feature Deep Dive: Why Research, Memories, Agents & Rules Feel Like Shells (and How to Make Them Worth It)

> Companion to `ai-trading-platform-code-review.md`. That doc was about *correctness and safety*. This one is about *value*: why these four surfaces feel "barely developed" after first use, what each is *trying* to be, and the concrete work that turns them from demos into a real copilot. Written as your big-picture/reasoning brain — opinionated on purpose.

---

## 0. TL;DR — the one-paragraph diagnosis

Every one of these features has the **right skeleton and the wrong amount of substance**. The architecture (LangGraph research loop, Mem0 memory, json-rules engine, tool-streaming chat) looks like a serious copilot, but each surface is starved of the *inputs and feedback loops* that would make it intelligent. Research runs on **fake/mock web data by default and ignores the real article text it downloads**. The agent doesn't actually decide anything — tools are picked by keyword matching, and chat doesn't truly stream. Memories are **written but almost never read back** into prompts, so the AI doesn't visibly "learn." Rules expose 4 threshold presets over an engine that can do far more, fire every 20 seconds with no notification delivery, and give a power user no way to author their own logic. Snapshots are a **raw mirror of the broker with zero analytics layered on top**, so the insight paragraph is generically vague. The fix is not a rewrite — it's adding the missing **intelligence layer**: real data sources, server-side analytics, proactive memory retrieval, grounded/cited synthesis, and a richer rule/fact model with a power-user editor.

---

## 1. How the AI actually works today (the real data flow)

A map of what's wired to what, so we're diagnosing reality, not the README.

```
                       ┌─────────────────────────────────────────────┐
                       │  LLM factory (ai/llm/factory.py)              │
                       │  mock | openai | anthropic | deepseek |       │
                       │  openrouter | ollama  — one cached client     │
                       └───────────────┬─────────────────────────────┘
                                       │ (every feature calls get_llm())
        ┌──────────────┬───────────────┼────────────────┬──────────────────┐
        │              │               │                │                  │
   INSIGHTS        CHAT AGENT       RESEARCH         MEMORIES          REFLECTION
 ai/insights.py   ai/chat/agent   ai/research/*    ai/memories     ai/reflection
        │              │               │                │                  │
        │   plan_tools (keyword)   plan→collect→     Mem0 OR SQLite    24h loop →
        │   → execute_tool         analyze→eval→     keyword LIKE      lessons →
        │                          synthesize        (dual-write)      memory
        │              │               │                │
        ▼              ▼               ▼                ▼
   snapshot JSON   snapshot +      sources:          stored, but
   (account +      memories +      portfolio +       retrieved into
   positions)      research        bars + memory +   prompts ONLY on
   → 1 paragraph   dumped as       WEB (mock by      keyword trigger
                   JSON blob       default!)
```

Key truths to internalize:

- **One thin "snapshot" is the entire portfolio brain.** `db/sqlite.py::get_latest_snapshot` returns the latest poll's account + positions + orders, verbatim from the broker. `ai/insights.py::snapshot_to_context` serializes account + up to 20 positions + 10 orders to JSON and asks for "one concise paragraph." There is **no derived analytics** anywhere.
- **Research's "web" is fake unless you set up SearXNG.** `ai/research/sources.py::web_search` returns `_mock_web_search(...)` when `SEARXNG_URL` is empty (the default). That function returns three hardcoded template sentences with `research.local/mock/N` URLs.
- **The agent has no agency.** `ai/chat/tools.py::plan_tools` is `if "portfolio" in text ... if "analyze" in text ...`. The model never chooses tools. And `ai/chat/agent.py` calls **blocking** `llm.complete()` then fake-streams the result 12 chars at a time via `UIMessageStream.text_chunks`.
- **Memory is write-heavy, read-light.** `extract_memories_from_turn` runs after each standard turn and stores facts. But chat only *retrieves* memories if the user types "remember/memory/preference/recall" (the `search_memories` keyword trigger), and **insights/research barely use stored preferences at all.**
- **Rules are a 4-preset UI over a capable engine.** `ai/rules/engine.py` supports `all/any/not` + 8 operators, but `frontend/lib/rules.ts::RULE_PRESETS` exposes only `has-positions`, `position-count`, `portfolio-value`, `unrealized-loss`. Triggers `logger.info` + insert a row; **nothing notifies the UI.**

---

## 2. The vision that's actually there (worth protecting)

You've scaffolded a genuinely good product shape. Before criticizing, name the vision each surface is reaching for — because the fixes should *realize* it, not replace it:

| Surface | The vision it's reaching for |
|---------|------------------------------|
| **Snapshots** | A living, analyzed view of the portfolio — exposure, concentration, drift, risk — not just "what I hold." |
| **Research** | Odysseus-style: plan queries → search → *read* → reflect on gaps → iterate → synthesize a **cited** report grounded in real data. |
| **Agents** | A proactive copilot that knows your portfolio, recalls your preferences, runs tools, and helps you build strategy over time. |
| **Memories** | Long-term learning: the system gets more "yours" every session and changes its future behavior. |
| **Rules** | A programmable guardrail/alert layer — eventually *your* trading logic, authored by you, enforced by the app. |

The skeleton supports all of this. The gap is substance and feedback loops.

---

## 3. The "shell" pattern: one root cause, five symptoms

Every surface fails the same way — **it produces output without enough grounded input, and its output doesn't feed back into anything.** Concretely:

| Symptom you feel | Root technical cause | Where |
|------------------|----------------------|-------|
| Research = "useless junk" | Web search is **mock by default**; real fetched page text is **discarded** (only snippets reach the LLM); **no fundamentals/news** | `ai/research/sources.py`, `ai/research/report.py` |
| Insight feels generic | LLM sees a **raw position list with no analytics, no market context, no memory** | `ai/insights.py` |
| Agent feels janky | **Keyword tool routing**, **fake streaming**, raw-JSON context dumps | `ai/chat/tools.py`, `ai/chat/agent.py` |
| "It doesn't learn" | Memories are **stored but rarely retrieved** into prompts; keyword search only | `ai/chat/memory_extract.py`, `ai/memories/__init__.py` |
| Rules feel weak/weird | **4 presets**, **no notification delivery**, **fires every poll**, **no power-user authoring** | `frontend/lib/rules.ts`, `ai/rules/engine.py`, `api/main.py` |

The unifying fix is an **Intelligence Layer** (Section 9) that sits between raw data and the LLM, and a **retrieval + feedback discipline** so outputs improve inputs.

---

## 4. Snapshots — a raw mirror with no brain

### What it does now
`_poll_once` (every 20s) writes account/positions/orders to SQLite. `get_latest_snapshot` reads back **only the latest run**. The frontend `SnapshotProvider` polls `/api/snapshot` on a 20s timer; `InsightsPanel` re-streams an LLM paragraph **every time `captured_at` changes** (i.e., constantly).

### Why it feels shallow
1. **Zero derived analytics.** No day P&L, no per-position weight (% of portfolio), no sector/asset-class breakdown, no concentration metric, no cost basis vs current, no comparison to *yesterday's* snapshot, no benchmark (vs SPY). The model is handed `[{symbol, qty, market_value, unrealized_pl, ...}]` and asked to be insightful — it can only restate the obvious.
2. **You're storing time series and throwing it away.** `poll_runs` accumulates history, but nothing reads more than the latest row. The single most valuable thing — *change over time* ("your NVDA exposure went 18%→31% this week", "drawdown is 3 days running") — is sitting in the DB unused.
3. **No market context.** The snapshot doesn't know if positions are up or down *today*, or how they move vs the market. You already have a live quote hub (`brokers/market_stream.py`) and bars — none of it joins the snapshot.
4. **Insight has no continuity or memory.** Each refresh is a cold one-paragraph generation. It never references your stated risk preferences (which are in Memories!) or prior insights.

### What "good" looks like
A server-computed `PortfolioAnalytics` object persisted alongside each snapshot, e.g.:

```python
@dataclass(frozen=True)
class PortfolioAnalytics:
    total_equity: Decimal
    day_pl: Decimal              # vs prior close
    day_pl_pct: Decimal
    cash_pct: Decimal
    largest_position_pct: Decimal      # concentration
    top_holdings: list[tuple[str, Decimal]]  # symbol, weight%
    sector_weights: dict[str, Decimal]
    exposure_vs_prior_snapshot: dict[str, Decimal]  # drift
    winners: list[str]; losers: list[str]
    unrealized_pl: Decimal
    flags: list[str]   # "Concentrated in 1 name", "Cash heavy", ...
```

Then `build_insight_messages` feeds **analytics + today's market move + relevant memories** to the LLM. *That's* the difference between "You hold 6 positions worth $75k" and "Your book is 31% NVDA — concentration is up from 18% last week and you told me you cap single names at 20%. Tech is +1.2% today but you're flat because energy dragged."

### Concrete improvements
- Build a `portfolio/analytics.py` module; compute on each poll, store a `snapshot_analytics` row.
- Add a `prior_snapshot` diff so insights can talk about *change*.
- Join live quotes (`market_hub`) for day P&L.
- Feed top relevant memories (risk rules, preferences) into the insight prompt.
- Stop regenerating the paragraph every 20s — only on meaningful change or manual refresh (cost + continuity).

---

## 5. Research — the biggest disappointment, and the most fixable

This is where "useless junk" comes from. There are **six** stacked causes, in priority order.

### Cause 1 (decisive): web search is mock by default
```103:140:ai/research/sources.py
def web_search(query, *, limit=5):
    searxng = get_searxng_url()
    if searxng:
        return _searxng_search(searxng, query, limit=limit)
    return _mock_web_search(query, limit=limit)   # ← default path
```
`_mock_web_search` returns three canned sentences ("Volume has been elevated versus the 20-day average") with fake `research.local/mock/N` URLs. **If you didn't configure SearXNG, your Apple deep-dive was an LLM hallucinating a report from three fake snippets + price bars.** That alone is the headline bug.

### Cause 2: real fetched content is downloaded then ignored
`collect_for_query` *does* call `fetch_url` and store up to 4000 chars of real page text in `source["content"]`. But the analysis stages only pass `snippet` (200–400 chars) to the model:
```103:111:ai/research/report.py
source_catalog = [
    {"id": s.get("id"), "type": s.get("type"),
     "title": s.get("title"), "snippet": s.get("snippet")}  # ← no "content"
    for s in sources
]
```
Same in `deep_graph._extract_findings`. So findings are extracted from **search blurbs, not articles**. You pay to fetch and store the real text, then throw it away.

### Cause 3: no real financial data
"Research Apple" has access to: Alpaca daily bars (→ one "% change over 30 days" string), the portfolio snapshot, prior memories, and web snippets. There is **no fundamentals (P/E, revenue, margins, guidance), no earnings calendar, no analyst estimates, no news API, no filings.** A finance research report with none of that is structurally generic.

### Cause 4: snippet-only analysis → generic prose, shallow loop
`standard` depth = 2 iterations. Gap evaluation (`_evaluate_gaps`) is an LLM guessing over *counts* of findings, not actual coverage. Synthesis is one LLM call over thin findings → vague sections.

### Cause 5: citations silently vanish
`build_report_from_findings` asks the model to cite `source_id`s, but `render_report_html` only renders a citation when `c in source_map`. Local/quantized models rarely echo exact ids, so citations disappear → the report looks unsourced even when sources exist.

### Cause 6: fragile JSON parsing degrades to raw dumps
Plan/extract/synthesize each do `raw.find("{") … rfind("}")`. A model that wraps JSON in prose or fences falls back to `_fallback_report` — a single "Report" section of raw text. Looks broken.

### Plus: the UI lies about it
The report header says "Deep research" and lists "web" sources pointing at `research.local/mock/...`. The user can't tell it was synthetic. `research-page.tsx` even shows the Odysseus tagline.

### What "good" looks like
1. **Real data adapters first.** Add a `research/providers/` with: a news source (e.g. an RSS/news API), a fundamentals source, and *keep SearXNG* — but **make "no real source configured" a loud UI state**, not a silent mock. Never present mock data as research.
2. **Read, don't skim.** Pass fetched `content` (chunked/summarized) into findings extraction, with per-chunk citation tracking. This is the single biggest quality lever after turning off mock.
3. **Ground findings in data.** Each finding should carry `source_id` + a verbatim supporting quote, and the synthesizer must only use findings that have support. Reject uncited claims.
4. **Enforce citations.** Generate citations programmatically from the findings→source map instead of trusting the model to echo ids.
5. **Make depth mean depth.** "Deep" should fetch+read more sources and actually re-query on identified gaps, not just loop the same snippet pass.
6. **Surface provenance** in the report: data freshness, which sources were real vs unavailable, confidence per section.

This keeps your LangGraph (`plan→collect→analyze→evaluate→synthesize`) — it's the right shape — and fills it with substance.

---

## 6. Agents — a Q&A box pretending to be a copilot

### What it does now
`frontend/components/agents/agent-chat.tsx` is a solid Vercel-AI-SDK chat UI (sessions, modes, model picker, tool rendering). The backend (`ai/chat/agent.py`) does: keyword `plan_tools` → run tools synchronously → one blocking `llm.complete()` → fake-stream.

### Why it feels janky
1. **No real tool-calling.** `plan_tools` matches words. Consequences:
   - The "Research AAPL momentum" suggestion fires a **full deep-research run inside the chat turn** — multiple LLM calls + (real or mock) web fetches — synchronously. You stare at "Thinking…" for a long time, then everything appears at once.
   - The model can't choose *not* to run a tool, can't chain (snapshot → then research the worst position), can't ask a clarifying question.
   - `extract_symbol` regex grabs any 1–5 uppercase letters → "CEO", "USA", "ETF" become tickers.
2. **Chat doesn't actually stream.** `llm.complete()` blocks for the whole response; `text_chunks` then types it out 12 chars at a time. That's the "janky" feel — latency then a burst.
3. **Context bloat.** Tool results are `json.dumps`'d wholesale into the final user message (`_build_final_messages`). A research result or full snapshot becomes a giant blob the model wades through — bad for small/local models especially.
4. **No proactivity, no situational awareness.** The agent doesn't know your rules, doesn't see alerts, doesn't reference analytics, has no goals or scheduled tasks. It only reacts to the current message. The "proactive copilot" in the system prompt is aspirational.
5. **Memory barely reaches it.** It only searches memories when the user uses trigger words, and even then via keyword `LIKE`. So it rarely "remembers" unprompted.

### What "good" looks like
- **Native function/tool calling** with JSON schemas (OpenAI/Anthropic/Ollama all support it). A real loop: model → tool call → result → model, streamed token-by-token via your existing `astream`.
- **Make `run_research` a background job**, not a chat blocker — return a job id and stream/poll progress (you already have `astream_deep_research`).
- **Always inject context the model needs**: a compact portfolio analytics summary + top-k semantically-retrieved memories at the *start* of every turn, regardless of keywords.
- **Tool results summarized, not dumped** — pass a compact, model-friendly digest with ids the model can reference.
- **Proactivity hooks** (later): let the agent surface "3 things to look at" on load, driven by analytics + rules + memories.

---

## 7. Memories — written diligently, read almost never

### What it does now
- Three types: `preference | lesson | research`.
- After each **standard** chat turn, `extract_memories_from_turn` makes an LLM call to pull 0–2 durable facts (falls back to regex heuristics).
- Storage: SQLite always; Mem0 mirrored *if installed* — but search prefers Mem0 and falls back to SQLite **keyword `LIKE`**, IDs differ between stores, and `update` does a Mem0 `add` (creating duplicates).
- Retrieval into prompts: chat only when keyword-triggered; insights never; research adds a summary but doesn't pull preferences.

### Why it feels shallow
1. **Write-mostly.** A memory only has value if it changes a *future* output. Right now stored preferences almost never reach the prompt, so the system can't visibly "learn."
2. **Keyword search ≠ semantic memory.** "I cap single names at 20%" won't surface for a query about "concentration risk" via `LIKE`.
3. **Dual-store drift.** Mem0 + SQLite aren't reconciled — contradictory or duplicate recall.
4. **No structure or lifecycle.** No symbol/position linkage, no confidence, no decay, no de-duplication, no "confirm this is right?" review. No provenance shown to build trust.

### What "good" looks like
- **One source of truth.** Pick local SQLite + a real embedding index (`sqlite-vec`, FAISS, or Chroma) for semantic recall. Treat Mem0 as optional, not authoritative, or drop it until it earns its place.
- **Retrieve everywhere, proactively.** Before *every* insight, research, and chat turn, fetch top-k relevant memories by embedding similarity and inject them. This is what makes it feel like it knows you.
- **Structured memory.** `{type, content, symbol?, confidence, source, created_at, last_used_at, embedding}`. Decay/boost by usage. De-dup on add.
- **A review loop.** Surface "I learned: you cap single names at 20% — keep?" so the user curates. Trust comes from visibility + control.
- **Close the loop with reflection.** Reflection already writes lessons; make sure those lessons are retrieved into future insights/research, and tie them to outcomes ("this lesson fired before a good/bad trade").

---

## 8. Rules — a powerful engine hidden behind 4 toggles (your instinct is correct)

You said it best: rules feel weird, there are only a few options, and a power user should be able to write their own. Agreed on all counts — and the engine already supports far more than the UI shows.

### What it does now
- Engine (`ai/rules/engine.py`): `all/any/not` trees + 8 operators (`equal`, `lessThan`, `greaterThanInclusive`, `in`, `contains`, …). This is a real json-rules-engine pattern.
- Facts available (`build_facts_from_snapshot`): `portfolio_value`, `cash`, `buying_power`, `position_count`, `open_order_count`, `total_unrealized_pl`, `symbols`, `has_positions`.
- UI (`RULE_PRESETS`): 4 presets, threshold-only.
- Evaluation: on every poll, every matching rule **records an event and `logger.info`s** — and that's it.

### Why it feels weak / weird
1. **The UI exposes ~10% of the engine.** You can express `any/all/not` and per-fact comparisons, but the user can only pick a preset and a number.
2. **The fact catalog is portfolio-aggregate only.** No per-symbol facts, no price, no % drawdown, no per-position P/L%, no day change, no time-of-day, no "position older than N days." So you can't write the rules traders actually want ("alert if any position down >8%", "if SPY < 200DMA", "if AAPL crosses 230").
3. **Fires every poll while true.** A "drawdown > 5%" rule records a new event **every 20 seconds** for as long as it's true → event spam + DB bloat, and (once notifications exist) alert fatigue. There's no `false→true` transition detection or cooldown.
4. **No notification delivery.** Triggers only insert a row. Price alerts (a *separate* system in `db/chart_features.py`) push over WebSocket; portfolio rules don't push anything. Two parallel alerting systems that should be one.
5. **No power-user authoring.** No raw JSON/DSL editor, no "test this rule against my current snapshot," no actions beyond `notify`.

### The redesign: make it THEIR copilot

**8a. Expand the fact catalog** (server-side), including per-symbol facts so rules can target positions:

```python
facts = {
    # account
    "portfolio_value": ..., "cash": ..., "cash_pct": ...,
    "buying_power": ..., "day_pl": ..., "day_pl_pct": ...,
    "total_unrealized_pl": ..., "total_unrealized_pl_pct": ...,
    "position_count": ..., "largest_position_pct": ...,
    # time / market
    "weekday": ..., "hour_et": ..., "market_open": bool,
    # per-position (evaluated per holding)
    "positions": [
      {"symbol": "NVDA", "weight_pct": 31.2, "unrealized_pl_pct": -8.4,
       "day_change_pct": -2.1, "held_days": 12, "price": 118.4}
    ],
    # quotes (live)
    "quotes": {"SPY": {"price": 540.2, "sma200": 521.0, "day_change_pct": 1.2}},
}
```

Add a `forEach` / per-position rule mode so a single rule can mean "**any** position down >8%".

**8b. Give power users a real editor.** Three tiers, same engine underneath:

- **Tier 1 — Presets** (what exists): one-click, threshold.
- **Tier 2 — Visual builder**: pick fact + operator + value, AND/OR groups. The engine already supports the tree.
- **Tier 3 — Raw JSON/DSL editor** (the CLI-config feel you want): a Monaco editor with the rule schema, validation, and a **"Dry-run against current snapshot"** button that shows which facts matched. Example a power user could paste:

```json
{
  "name": "Trim concentrated tech on weakness",
  "when": {
    "all": [
      { "fact": "largest_position_pct", "operator": "greaterThan", "value": 25 },
      { "any": [
        { "fact": "position.unrealized_pl_pct", "scope": "each", "operator": "lessThan", "value": -8 },
        { "fact": "day_pl_pct", "operator": "lessThan", "value": -3 }
      ]}
    ]
  },
  "cooldown_minutes": 60,
  "trigger_on": "transition",          // false→true only
  "actions": [
    { "type": "notify", "channel": "toast", "message": "Concentrated tech under pressure — review {top_holding}" },
    { "type": "memory", "note": "Flagged concentration risk on {date}" }
  ]
}
```

A YAML variant of the same is even friendlier for the "config file" vibe. You could also let users keep rules as files in a `rules/` dir and hot-reload them — very CLI-power-user.

**8c. Add `trigger_on: transition` + `cooldown`** so rules fire once per state change, not every poll. Store last-evaluated state per rule.

**8d. Unify alerting and actually deliver.** One alert bus that both price alerts and portfolio rules publish to; deliver via the existing WebSocket to a toast/notification center in the UI (and later desktop/push/email). Right now `market_stream` already pushes price alerts — extend that channel to rule triggers.

**8e. Richer actions (still safe).** `notify`, `tag/memory`, `run_research(symbol)`, and — gated behind the future RiskEngine — `propose_order` (never auto-execute without the safety layer from the code review). This is the bridge from "alert me" to "help me act."

**8f. Let the agent author rules.** "Add a rule to warn me if any position drops more than 8%" → the chat agent emits the JSON rule (Tier 3 schema) → user confirms → saved. That closes the loop between the copilot and the guardrails, and it's where "make it *mine*" really lands.

---

## 9. The missing piece across all of it: an Intelligence Layer

The recurring theme: the LLM is handed **raw or fake data with no analysis, and its output feeds back into nothing.** Introduce a layer between data and model, used by *every* feature:

```
 Brokers / Market / Web / Fundamentals  →  DATA ADAPTERS
                                              │
                                              ▼
        ┌─────────────────────────────────────────────────────────┐
        │  INTELLIGENCE LAYER                                       │
        │  • portfolio analytics (exposure, drift, day P&L, flags) │
        │  • fact builder (rich, per-symbol)                       │
        │  • semantic memory retrieval (embeddings)                │
        │  • grounded source reading + citation map                │
        └───────────────┬─────────────────────────────────────────┘
                        ▼
        Insights · Chat · Research · Rules · Reflection  (all consume the same layer)
                        │
                        ▼
        OUTPUTS feed back: research → memory, rules → alerts → agent,
        reflection → preferences → future prompts
```

Build this once and all four surfaces get better simultaneously. It's the highest-leverage work in the whole product.

---

## 10. Prioritized roadmap to make these "worth it"

Ordered by *value per unit effort*. P0 = makes the feature stop feeling fake.

| Priority | Change | Surface | Why it matters | Effort |
|----------|--------|---------|----------------|--------|
| **P0** | Stop presenting mock web search as research; show a loud "no real source configured" state; wire at least one real source (SearXNG or news API) | Research | Kills "useless junk" at the root | S |
| **P0** | Feed fetched **content** (not just snippets) into findings extraction | Research | Turns skimming into reading | S–M |
| **P0** | Build `portfolio/analytics.py` and feed analytics + day P&L + memories into the insight prompt | Snapshots/Insights | Generic → specific & personal | M |
| **P0** | Retrieve top-k memories (semantic) into **every** chat/insight/research prompt | Memories | Makes "it learns" actually true | M |
| **P1** | Native tool-calling loop + true token streaming; make research a background job | Agents | Removes the jank, enables chaining | M–L |
| **P1** | Rule `trigger_on: transition` + cooldown; unify alerts onto the WS bus with a UI notification center | Rules | Stops spam; "flag me" finally works | M |
| **P1** | Expand fact catalog with per-symbol/price/time facts | Rules | Enables rules traders actually want | M |
| **P1** | Programmatic citations from findings→source map; reject uncited claims | Research | Reports look (and are) sourced | S–M |
| **P2** | Tier-3 raw JSON/YAML rule editor with schema validation + "dry-run vs snapshot" | Rules | The power-user "make it mine" feature | M |
| **P2** | Add fundamentals + news data adapters | Research | Real financial depth | M–L |
| **P2** | One memory source of truth + embeddings; structured memory + review loop | Memories | Trust + recall quality | M |
| **P2** | Snapshot diff / time-series analytics ("exposure up vs last week") | Snapshots | Insight about *change* | M |
| **P3** | Agent can author rules from chat (with confirm) | Agents+Rules | Closes copilot↔guardrail loop | M |
| **P3** | Harden all LLM JSON parsing (shared parser + provider JSON mode) | All | Reliability on local models | S |

---

## 11. The honest bottom line

These four features aren't half-built UIs — they're **fully-built UIs over half-built intelligence.** The screens, streaming, sessions, and graph scaffolding are genuinely good and worth keeping. What's missing is everything that would make the AI *grounded and personal*: real data instead of mock, analysis instead of raw lists, retrieval instead of write-only memory, model-driven tools instead of keyword matching, and a rule system that trusts the user to program it.

Do the P0 row and the difference will be immediate and visceral — research that cites real articles, insights that mention *your* risk rules and today's move, an agent that remembers you, and rules you can actually write. That's the moment these stop feeling like shells and start feeling like a copilot.

Start with research (turn off mock + read content) and the analytics-fed insight — they're the smallest changes with the most "oh, *now* it's real" payoff.
