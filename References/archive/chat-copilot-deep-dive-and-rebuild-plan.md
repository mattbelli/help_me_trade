# Chat / Copilot — Deep Dive & Rebuild Plan

> **Superseded (2026-06-16).** Canonical plan: [`../BUILD.md`](../BUILD.md) §2 (chat + assistant-ui decisions) and §5 Phase 3–4. This file is kept for historical root-cause notes only.

> Companion to `ai-feature-deep-dive-and-vision.md` and `platform-hardening-master-plan.md`. This doc is specifically about the **`/agents` chat page**: why it feels broken, what it should actually be, whether to adopt open-source, and a concrete build plan. No sugarcoating.

---

## 0. Your vision, stated plainly

> A chat where you **brainstorm strategies and ideas, talk through investing/trading decisions, and the AI learns from those conversations and recommends back to you over time.** A real feedback loop — not a stateless Q&A box.

That is a **strategy-development copilot**, not a chatbot. The current page is a chatbot skeleton with three confusing modes, a brittle custom stream, and no learning loop. Everything below serves that vision.

---

## 1. The bugs you noticed — exact root causes

I traced each symptom to specific code. None of this is vague.

### 1.1 "It says `snapshot.` — I can click it, but there's no actual answer"

**What's happening:** When your message contains a keyword like *portfolio / position / account / orders* (`ai/chat/tools.py::plan_tools`), the backend runs the `get_portfolio_snapshot` tool, streams a **tool chip** to the UI, then calls the LLM for the actual answer. The tool chip renders (that's the `snapshot.` thing you can expand), but the **text answer never appears.**

**Why the text is missing — two stacked causes:**

1. **Errors silently kill the message.** In `ai/chat/agent.py::run_chat_stream`:
   ```122:130:ai/chat/agent.py
   response = llm.complete(final_messages, max_tokens=1200)
   except Exception as exc:
       yield stream.error(str(exc))          # ← emits {"type":"error"} into the stream
       response = f"Unable to reach the configured LLM ..."
   ...
   yield from stream.text_chunks(response)    # ← emitted AFTER the error part
   ```
   The frontend uses the Vercel AI SDK `useChat`. When it receives an `error` part, it fires `onError` and **discards the in-progress assistant message** — so the fallback text streamed *after* the error never renders. And `onError` only does `console.error` (`agent-chat.tsx`), so you don't even see the error. Result: **tool chip, no text, no visible error.** This is almost certainly what you hit — it happens whenever the configured model errors, times out, or (for a local/Ollama model) returns nothing.

2. **Empty completions render an empty bubble.** `text_chunks("")` emits `text-start` then `text-end` with no delta. If the model returns empty/whitespace (common when a weak model is handed the giant raw-JSON tool dump), you get a blank assistant message under the tool chip.

**Contributing design flaw:** tool results are dumped as pretty-printed JSON into the prompt (`_build_final_messages`), which bloats context and makes small models choke — increasing the odds of empty/failed responses.

> On the **mock** provider (the default) you'd see text, because `MockLLM` always returns something. So this bug shows up specifically once you switch to a **real** provider that errors or a **local** model that's slow/empty — exactly when you started "asking it to output certain things."

### 1.2 "My chats don't seem to save"

Persistence only happens in `finalize_chat_turn → _persist_turn`, gated by:
```167:168:ai/chat/agent.py
if not session_id or mode not in PERSISTED_MODES:
    return
```
So a turn is saved **only** if (a) there's a `session_id` and (b) mode is `standard` or `private`. Two ways you end up with nothing saved:

- **You're in `quick` mode.** The bootstrap in `agent-chat.tsx` *falls back to `quick` with `sessionId = null`* whenever the initial session fetch throws (`catch { setChatMode("quick"); setSessionId(null) }`). If the backend was slow/erroring at load (see 1.3), you silently land in ephemeral mode and nothing persists — by design, but invisibly.
- **The turn errored.** Persistence also requires `run.assistant_text` to be truthy in the `finally` of the SSE generator (`api/ai_routes.py`). If the client aborts on the error part (1.1), the save may not capture a clean turn.

Net: saving *works* for standard/private sessions with a successful turn — but the failure modes are silent, so it *feels* like it doesn't save.

### 1.3 "...and when they do, they take forever to load"

This is **C1** in your hardening plan (blocking sync I/O on the async event loop). `_poll_once` calls **synchronous** `httpx` against the broker on the event loop every 20s, and `llm.complete()` in chat also runs **on the loop**. While either is running, every other async request — including `/api/chat` streaming and the insight SSE — stalls behind it. Session list/detail are sync endpoints (threadpool) so they're less affected, but the chat stream itself competes with polling. Add `init_chat_tables()` running on *every* DB call and a sequential bootstrap (list sessions → then fetch latest detail), and you get the lag.

**Already owned by the plan:** Phase 1 (C1, `to_thread` offload) + P2 (true `astream` streaming) fix the bulk of this.

### 1.4 "The modes make no sense"

Current model (`db/chat_sessions.py`, `lib/chat-sessions.ts`):

| Mode | Saved to sidebar? | Goes to memory? | Reality |
|------|-------------------|-----------------|---------|
| **standard** | yes | yes | the "real" chat |
| **private** | yes | **no** | a confusing middle ground |
| **quick** | no | no | incognito/temporary |

The problem is conceptual, not cosmetic. You have **three** modes for what is really **two** user intents:
1. "A normal conversation I want kept, and I want the AI to learn from it."
2. "A throwaway question I don't want kept or learned from."

`private` ("saved but not learned") is a niche that muddies the model. You literally described wanting #2 ("a simple private chat that wouldn't bring that data into your memories… ask a question that has nothing to do with your training") — that's just a **temporary chat**.

---

## 2. The mode redesign (do this — it's small and high-impact)

Collapse to **two modes + one toggle**:

- **Saved chat** (default): persisted in the sidebar. A per-chat **"Learn from this chat" toggle** (default ON) controls whether memory extraction runs. This replaces both `standard` and `private` with something obvious — you can keep a chat *and* decide whether it teaches the AI.
- **Temporary chat** (the incognito button): not saved, not learned, no sidebar entry. This is your "private question" use case. Mirror ChatGPT's "Temporary chat" mental model — users already understand it.

```
[ + New chat ]           ← creates a Saved chat
[ 🕶 Temporary chat ]     ← ephemeral, nothing persisted

Within a saved chat header:  [✎ title]   [ Learn from this chat ●ON ]
```

Backend: keep `quick` (rename UI to "Temporary"), drop `private` as a separate mode, add a `learn: bool` flag on saved sessions that gates `extract_memories_from_turn`. Migration is trivial (treat existing `private` as `standard` with `learn=false`).

---

## 3. Build vs. adopt open source — my honest recommendation

You asked directly. Here it is, no hedging.

### The core problem
Your backend **hand-rolls the Vercel AI SDK UI-message protocol** in `ai/chat/stream.py` (`UIMessageStream`). That's the source of the jank: the protocol is an internal contract that evolves, and small mismatches (like emitting an `error` part that nukes the message) produce exactly the "tool chip, no text" bug. Maintaining a hand-written encoder of someone else's streaming protocol is a losing game.

### What to adopt (and what not to)

| Option | Verdict | Why |
|--------|---------|-----|
| **Vercel AI SDK** (already in use) | ✅ **Lean in fully** | You're already on `@ai-sdk/react`. Use its **server** side too (`streamText` + `toUIMessageStreamResponse`) so the protocol is generated by the library, not by you. Native tool-calling + streaming, zero hand-rolled SSE. |
| **assistant-ui** (assistant-ui.com) | ✅ **Strongly consider for the UI** | Production-grade React chat primitives built on the AI SDK: streaming, tool UIs, message branching, attachments, auto-scroll, composer. Skips months of UI polish. Drop-in with your shadcn theme. |
| **LangGraph** (Python) | ◐ Optional, later | Good if the agent becomes genuinely multi-step (plan→tool→reflect loops). Overkill for now; you already use it for research. |
| **LibreChat / Open WebUI** | ❌ Don't | These are *complete standalone apps*. Adopting them means replacing your product, not embedding a copilot. Great for **inspiration** (mode UX, prompt library, presets) — not as a dependency. |
| Custom Python `UIMessageStream` | ❌ Stop extending it | It's the bug surface. Either generate the protocol with the AI SDK server, or treat Python purely as a tool/data API (below). |

### Recommended target architecture

The cleanest long-term shape for a single-user local copilot:

```
┌─────────────────────────────────────────────────────────────┐
│  Next.js (frontend)                                          │
│  • assistant-ui / @ai-sdk/react chat UI                      │
│  • app/api/chat/route.ts  ← AI SDK server: streamText()      │
│        - owns streaming + tool-calling (no hand-rolled SSE)  │
│        - defines tools whose execute() calls Python API      │
└───────────────┬─────────────────────────────────────────────┘
                │ HTTP (tool calls + context fetch)
                ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI (Python) — the brain & data layer                   │
│  • /api/context        → portfolio analytics + memories      │
│  • /api/tools/snapshot  /api/tools/search_memories           │
│  • /api/research/jobs   (async, from hardening plan C5)      │
│  • /api/memory/extract  (feedback loop)                      │
└─────────────────────────────────────────────────────────────┘
```

Python stays the source of truth for portfolio, memory, research, and rules. The **AI SDK route owns the conversation** (streaming, tool-call loop, model routing) so you stop fighting the protocol. This also gives you real, model-driven tool-calling for free — directly solving **C5** (no more keyword `plan_tools`).

**If you'd rather not move streaming into Next.js yet** (bigger change), the interim is: make the Python stream *correct* — never emit a terminal `error` part before text, always emit a non-empty text part, and adopt the AI SDK's documented event sequence exactly. But understand this is patching a hand-rolled protocol; the route-handler approach is where you want to end up.

**My bottom line:** Adopt the AI SDK end-to-end + assistant-ui for the UI; keep Python as the tool/data/brain API. Don't adopt a whole chat app. This removes the jank class of bugs entirely instead of patching them one by one.

---

### ✅ DECISION (2026-06-16): move chat to a Next.js route handler

Committed. The chat **conversation host** moves into a Next.js App Router route (`app/api/chat/route.ts`) using the AI SDK server (`streamText` + `toUIMessageStreamResponse`). Rationale: the value is **eliminating the hand-rolled protocol bug class** and getting native model-driven tool-calling (solves C5) — not scaling. Three constraints make this decision safe:

**1. BFF boundary (non-negotiable).** This is a *backend-for-frontend* split, not a logic migration:
- **Next route = conversation host only** — owns streaming, the tool-call loop, and model routing.
- **Python (FastAPI) = the brain** — remains the *sole* owner of portfolio analytics, memory, research (LangGraph), rules, broker adapters, and **all execution**. Every chat tool is just an authenticated HTTP call into Python.
- **Hard line for C2:** order execution and the `RiskEngine` must **never** live in the Node layer. The route may only *call* the gated Python `ExecutionService`; it can never place an order itself. Keep this line bright and the architecture stays sane.

**2. Secrets: Python stays the single source of truth (Option 1).** Provider keys do **not** get a second home in Node env. The Next route fetches the active provider + key from Python (over the C6-authenticated localhost API) at request time and uses it transiently. This keeps C6 (encrypt-secrets-at-rest, one store) intact. *(Option 2 — keys in Node env — was rejected: splitting secret management across two runtimes is a smell for a money app.)*

**3. Accept two LLM stacks, deliberately.** The AI SDK drives **chat**; the Python `ai/llm/*` layer continues to drive **insights, research, reflection, and memory extraction**. This is a conscious trade (slightly more surface area) in exchange for a correct, library-maintained chat protocol — not an accident to clean up later.

```
┌───────────────────────────────────────────────┐
│ Next.js  app/api/chat/route.ts                 │
│   streamText() + tool-call loop  (CHAT ONLY)   │
│   provider+key fetched per-request from Python │
└───────────────┬───────────────────────────────┘
                │ authenticated HTTP (tools, context, provider/key)
                ▼
┌───────────────────────────────────────────────┐
│ FastAPI — the brain (sole owner)               │
│   analytics · memory · research · rules        │
│   ExecutionService + RiskEngine  ← C2 lives    │
│   ONLY here; Node can call, never bypass       │
└───────────────────────────────────────────────┘
```

---

### 3.5 Adopting the two assistant-ui patterns ([mem0](https://www.assistant-ui.com/examples/mem0) + [stockbroker](https://www.assistant-ui.com/examples/stockbroker))

Both example apps are built on **assistant-ui**, but they use **two different runtimes** — and only one matches the §3 decision. The rule we're following: **take the pattern, not the dependency.**

| Example | Runtime it uses | Fits §3 decision? | What we take | What we reject |
|---------|-----------------|-------------------|--------------|----------------|
| **Mem0 memory chat** | AI SDK route (`streamText` + `toUIMessageStreamResponse`) | ✅ yes — this *is* our transport | The loop: extract → store → semantic-retrieve → **inject every turn** → visualize/edit. Their memory-viz + privacy-control UI ideas. | The **Mem0 *cloud* SDK** (`MEM0_API_KEY`, `Mem0Client`) called from Node. |
| **LangGraph stockbroker** | `@assistant-ui/react-langgraph` + a LangGraph **server** runtime | ❌ no — different transport | The **human-in-the-loop approval pattern** + the `requires-action` Approve/Reject **tool card**. | The **LangGraph chat runtime** (interrupts/checkpointer server). |

**✅ DECISION (2026-06-16): adopt both as patterns on the AI-SDK runtime.**

1. **assistant-ui — adopt, via the AI SDK runtime adapter (`@assistant-ui/react-ai-sdk`), in Phase B5.** This is the same adapter the mem0 example uses, so it sits cleanly on top of our `app/api/chat/route.ts`. **Cost accepted:** it *replaces* the current `@ai-sdk/react useChat` + custom `ai-elements` UI layer. We do **not** use `@assistant-ui/react-langgraph`.

2. **Memory stays in Python (single source of truth).** We mirror the mem0 *loop* against our own endpoints — retrieve via `/api/context`, store via `/api/memory/extract` — backed by `ai/memories` + a local embedding index. *Rejected: the hosted Mem0 cloud SDK in Node* — it would add a third memory store, a second secret home (`MEM0_API_KEY` in Node, undoing C6/§3), and ship trading conversations to a third party. If we ever want Mem0's extraction quality specifically, run the open-source **`mem0ai` Python package self-hosted** behind the existing HTTP boundary — adopt the library, not the cloud.

3. **Human-in-the-loop: build the groundwork now, defer live execution to C2.** Phase B5 builds a **generic approval/confirmation tool card** (the stockbroker `requires-action` → Approve/Reject UX) using the **AI SDK's native tool-approval flow** (tool isn't auto-executed → render the card → on approve, submit the tool result). The **actual trade approve→execute path is deferred to C2 (hardening Phase 5)**: the card is only UI; every real order still flows through Python's gated `RiskEngine` + `ExecutionService` + audit log. **No order-execution surface exists in the chat layer until the RiskEngine does.** *(Rejected: adopting the LangGraph chat runtime now — it would reverse the §3 transport decision and stand up checkpoint/thread server infra for a benefit the AI-SDK tool-approval flow already covers. Revisit only if durable, resumable, multi-step agent workflows become a real need.)*

---

## 4. The feedback loop — how chat actually makes the AI "learn and recommend back"

This is the part that turns it from a chatbot into your copilot. Today it barely exists: memories are written after standard turns but **almost never read back**, and the chat only injects context when you say magic words.

### 4.1 Always inject context (every turn, no keywords)
Before each turn, the server should assemble and prepend:
- **Portfolio analytics digest** (from the `analytics` layer proposed in the feature deep dive — exposure, day P&L, concentration, flags). Compact, not raw JSON.
- **Top-k relevant memories** by **semantic** similarity (preferences, lessons, prior strategy notes) — not keyword `LIKE`.
- **Active rules** summary, so the copilot knows your guardrails.

This alone makes it feel like it knows you.

### 4.2 Capture strategy thinking as structured memory
Brainstorming is the highest-value content. Extract not just "preferences/lessons" but **strategy hypotheses**:
```json
{
  "type": "strategy",
  "title": "Trim NVDA into strength above 20% weight",
  "thesis": "Concentration risk; lock gains, redeploy to laggards",
  "conditions": ["weight_pct > 20", "rsi > 70"],
  "status": "idea",          // idea → testing → active → retired
  "confidence": 0.5,
  "source_session": "chat_..."
}
```
Now a brainstorm becomes a durable, queryable object the copilot can revisit, refine, and reference.

### 4.3 Close the loop: idea → rule → backtest → outcome → recommendation
The copilot should be able to:
1. **Propose a rule** from a strategy idea (emit the rule JSON from the feature-deep-dive Tier-3 schema) → you confirm → it's saved to the rules engine.
2. **Propose a backtest** (when the backtesting harness exists — product roadmap item) and report metrics.
3. **Reflection ties outcomes back** (the existing 24h reflection loop): "Your 'trim into strength' idea would have fired 3× this month; 2 were favorable." That recommendation flows back into chat and memory.

That is the feedback loop, concretely: **conversation → structured strategy memory → proposed rule/backtest → tracked outcome → recommendation surfaced back in chat.**

### 4.4 Make learning visible and user-controlled
After a turn the copilot learns from, show a small "Learned: *you cap single names at 20%* — keep / edit / discard" affordance. Trust comes from seeing and curating what it remembers. This also fixes the "it doesn't seem to learn" feeling by making the loop visible.

---

## 5. What "professional chat interface" means here (feature checklist)

| Capability | Today | Target |
|------------|-------|--------|
| True token streaming | ❌ blocking `complete()` then fake typewriter | ✅ real `astream` / AI SDK stream |
| Model-driven tool calls | ❌ keyword `plan_tools` | ✅ native function-calling loop (C5) |
| Tool results rendered usefully | ◐ raw JSON chip | ✅ rich tool cards (snapshot table, research link, memory list) |
| Errors visible & recoverable | ❌ silent `console.error` | ✅ inline error bubble + retry, never a blank turn |
| Mode model | ❌ 3 confusing modes | ✅ Saved (+learn toggle) / Temporary |
| Context awareness | ◐ keyword-triggered | ✅ portfolio + memory + rules injected every turn |
| Persistence | ◐ works but silent failures | ✅ reliable autosave, optimistic sidebar |
| Stop / regenerate / edit / branch | ◐ stop+regenerate only | ✅ edit-and-resend, branch (assistant-ui gives this) |
| Markdown / tables / code | ◐ basic | ✅ full rich rendering, copy, charts inline |
| Streaming research without blocking chat | ❌ sync research blocks the turn | ✅ async job + progress card (C5) |
| Per-chat model picker | ✅ exists | ✅ keep |

---

## 6. Phased build plan (aligned to the hardening master plan)

Order matters, and it's now shaped by the §3 decision: **don't over-invest in the Python `UIMessageStream` — you're deleting it.** Do the minimum to make the app non-broken in the meantime, finish the runtime-agnostic infra (C1/C6), then migrate chat to the route handler and build the loop.

### Phase A — Stop the bleeding (this week; minimal, throwaway-aware)
- **A1. Minimal error visibility only.** `onError` in `agent-chat.tsx` sets an error state + retry instead of silent `console.error`. *(Do NOT polish the Python `stream.error()` path — that code is slated for deletion in Phase B3.)*
- **A2. Make persistence loud.** Don't silently fall back to ephemeral `quick` when bootstrap fails; show the active mode in the header so "is this even saving?" is never ambiguous.
- **A3. (Runtime-agnostic, lands regardless) C1 offload + C6 auth.** Move blocking broker/LLM I/O off the event loop, and stand up the authenticated localhost API. **The route handler depends on both** — C6 auth is what the Next route uses to call Python and to fetch the provider/key (Option 1).

> Note: true `astream` streaming for the *Python* chat path is intentionally **dropped** from Phase A — once chat moves to the route handler, the AI SDK owns streaming. Insights/research keep their Python streaming.

### Phase B — Migrate chat to the Next.js route handler (the §3 decision)
- **B1.** Build `app/api/chat/route.ts` with `streamText` + `toUIMessageStreamResponse`; wire `useChat`/assistant-ui to it.
- **B2.** Provider/key fetched per-request from Python (Option 1); model routing in the route.
- **B3.** **Delete** `ai/chat/stream.py` (`UIMessageStream`) and the keyword `plan_tools`. Define AI-SDK tools whose `execute()` calls Python tool endpoints (`get_portfolio_snapshot`, `search_memories`, `start_research`).
- **B4.** Collapse modes to **Saved** + **Temporary**; add per-chat **Learn** toggle; migrate `private → standard + learn=false`.
- **B5.** **Adopt assistant-ui via `@assistant-ui/react-ai-sdk`** (replaces `useChat` + `ai-elements`); build rich tool-result components (positions table, memory list, research progress card) — edit/branch/scroll come for free. **Includes the generic approval/confirmation tool card** (stockbroker `requires-action` → Approve/Reject pattern) wired to the AI SDK tool-approval flow — *UI groundwork only, no order execution yet* (see §3.5 decision 3).

### Phase C — Real agent + memory loop (pairs with hardening Phase 3 / C5+C7)
- **C1.** Native tool-calling loop now lives in the route handler — model decides tools, can chain, can ask clarifying questions. (C5 is effectively *delivered by* the B-phase migration; this phase hardens it.)
- **C2.** `start_research` enqueues an async Python job; chat never blocks on research.
- **C3.** **The mem0 loop, Python-backed (§3.5 decision 2):** inject portfolio analytics + **semantic memories** + active rules into every turn via Python `/api/context`; after learn-enabled turns, store new memories via `/api/memory/extract`. Same UX as the mem0 example, zero data exfiltration.

### Phase D — The feedback loop + safe execution (the actual vision)
- **D1.** `strategy` memory type + extraction tuned for brainstorming.
- **D2.** Copilot can propose a rule (confirm-to-save) and, later, a backtest.
- **D3.** Reflection surfaces strategy outcomes back into chat + memory.
- **D4.** Visible "Learned: …" curation affordance.
- **D5. (Gated by hardening C2/Phase 5.)** Light up the **live trade approve→execute** path on top of B5's approval card: the agent's `propose_order` tool renders the Approve/Reject card; on approve, the route calls the gated Python `ExecutionService` (RiskEngine + audit). This is the stockbroker pattern, fully realized — but only once C2 exists.

---

## 7. The no-bullshit summary

- The page isn't fundamentally broken — it's a **chatbot skeleton with silent failure modes and a confusing mode model**, sitting on infrastructure issues you already plan to fix.
- The **"snapshot, no answer"** bug is real and specific: an error/empty completion emits a stream event that makes the client throw away the message. Fix the stream's error handling and you'll stop losing answers. *(Phase A1/A2.)*
- The **"slow / doesn't save"** issues are mostly **C1** (blocking event loop) plus a silent fallback to ephemeral `quick` mode. Both already have owners in the master plan.
- **Modes:** go to **Saved (+learn toggle)** and **Temporary**. Done.
- **Open source:** adopt **libraries, not apps**. **Decided (§3):** chat moves to a **Next.js route handler** on the **Vercel AI SDK** (`streamText`), with **assistant-ui** for the UI (via the **AI-SDK runtime adapter**, not the LangGraph one). Python stays the brain/tool/data API behind a **BFF boundary** — and the `RiskEngine`/execution (C2) lives *only* in Python. Secrets: **one source of truth in Python** (route fetches provider/key per-request; no keys in Node env). Do **not** bolt on LibreChat/Open WebUI — UX inspiration only.
- **The two assistant-ui examples (§3.5):** take both as **patterns, not dependencies**. The [mem0](https://www.assistant-ui.com/examples/mem0) loop runs against **Python memory** (no cloud SDK, no data exfiltration). The [stockbroker](https://www.assistant-ui.com/examples/stockbroker) **human-in-the-loop approval card** is built in B5 as UI groundwork, with **live trade execution deferred to C2/Phase 5** — and we do **not** adopt its LangGraph chat runtime.
- **The vision** — brainstorm → AI learns → recommends back — requires the **feedback loop in §4**: inject context every turn, capture strategy ideas as structured memory, turn ideas into rules/backtests, and surface tracked outcomes back into the chat. That's what makes it *your* copilot instead of a generic chat box.

Start with Phase A — it's a few hours of work and will immediately make the chat feel real (answers stop disappearing, errors become visible, responses stream). Then redesign modes (Phase B) and build the loop (Phases C–D).
