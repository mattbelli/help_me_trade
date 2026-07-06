# EXECUTION SPEC — Decision Engine Phases 1–2, reconciled with post-07-02 reality
*Fable 5 · principal-architect spec · 2026-07-06 · supersedes the Phase 1–2 rows of
`2026-07-02_decision-engine-roadmap.md` §7; everything else in the roadmap stands. Code refs verified today.*

## Why a reconciliation
The roadmap predates Phase 5a. Since 07-02 the repo shipped: `setup_scan` (deterministic
`structures.py` detection + LLM veto-and-grade), validated `ideas[]` with server-stamped `origin`,
the `idea_outcomes` shadow-tracking table + mechanical sweep, the conviction lane, memory P0/P1
(judge/tools/trust + FTS5 for the judge), and the mobile Python SSE chat endpoint. Five roadmap
assumptions are now stale:

1. **Native tool-calling is not in the Python `LLMClient`** — it exists only in the TS AI-SDK web
   route (`frontend/app/api/chat/route.ts`). Python surfaces run text + JSON-extraction
   (`ai/llm/structured.py`).
2. **Three chat loops still diverge:** web = AI-SDK iterative multi-step tools; mobile
   (`ai/chat/mobile_agent.py`) = single-pass plan→execute→answer, not iterative; legacy
   `/api/chat` (`ai/chat/agent.py:169`) = text-only, zero tool execution, zero journal/memory
   injection.
3. **FTS5 shipped but only for the judge's neighbor lookup** (`db/ai_tables.py:253`); per-turn
   retrieval is still token-overlap (`ai/memories/retrieval.py:17`).
4. **No trace table, no golden decision set** — only the memory-specific eval exists.
5. **Phase 2's core loop half-exists under another name.** `setup_scan` already IS candidate
   generation → deterministic numbers → LLM veto → validated ideas → journal-everything. What's
   missing is only the card contract + the proposal bridge. And the conviction-lane hard-block is
   a comment (`setup_scan.py:993`), enforced by the absence of any idea→proposal path — vacuous
   today, load-bearing the moment Phase 2 wires one.

## Ruling 1 — the brain lives in Python; TS becomes a proxy (confirms the roadmap, kills the drift)
The founder-agreed mobile architecture already made this call (chat brain = Python; mobile SSE
shipped). Do not host the loop in TS: every tool, the perception layer, the journal, and the
proposal seam are Python; a TS loop pays a network hop per tool call and forks the prompt assembly
forever. The AI-SDK web loop stays as a **fallback flag** until the Python loop reaches parity,
then dies.

**Phase 1a — native tool-calling in `ai/llm/` (3–4 days).**
Add to the `LLMClient` protocol (`ai/llm/base.py:17`): `complete_tools(messages, tools, tool_choice)
→ ToolCallResult` (text | tool_calls[]), streaming variant included. Implement for
`openai_compat.py` (function-calling — DeepSeek, the default provider, supports it) and
`anthropic.py` (tool-use blocks). Keep `parse_structured` as the fallback for providers without
support (`ollama`). The existing `TOOL_DEFINITIONS` registry (`ai/chat/tools.py:37`) already has
names/descriptions/schemas — emit them as provider tool specs; do NOT fork the registry.

**Phase 1b — ONE iterative loop, three thin surfaces (~1 week).**
New `ai/chat/loop.py`: iterative act→observe→act (cap ~6 steps, mirroring the web route's
`stepCountIs(6)`), journal + memory blocks injected once at assembly (web parity:
`injectJournal=true`), SSE event contract = the one mobile already speaks
(`ai/chat/mobile_stream.py`). Then: mobile endpoint swaps `run_mobile_chat_stream`'s single-pass
planner for the loop (its `propose_order`→PENDING interception stays); web route becomes a thin
proxy behind a flag; **delete** the legacy no-tools `/api/chat` path (`agent.py:169`) — it is a
degraded liar (advertises tools in prose it cannot run). Acceptance: the golden set (1c) passes
identically through web-proxy and mobile surfaces.

**Phase 1c — eyes: traces + golden set (3–4 days; this IS the `llm-eval-harness` card).**
`chat_traces` table written by the one loop (single instrumentation point — the reason 1c follows
1b): trace_id, surface, model, prompt_hash, tools_called (name+args-hash+ms), result_sizes,
final_hash, tokens, error. Golden set: 30–50 YAML cases, three assertion tiers, all deterministic
(no LLM-judge in v1): **routing** ("3 best setups" must call screener+regime+calibration;
"how am I doing" must call the journal, scoped real-only), **honesty** (unrated symbol → answer
contains no numeric win%; the `_PROBABILITY_PATTERN` check reused), **grounding** (every number in
the answer appears in some tool result — mechanical substring/tolerance check). Nightly on serv on
the cheap tier; report = pass-rate + diff vs yesterday, pushed on regression. *Not* gated on 5b —
this measures the conversational brain that already exists.

**Explicitly deferred out of Phase 1:** switching per-turn memory retrieval to FTS5/embeddings —
that is memory-overhaul P2, already a founder-gated card; don't duplicate it here. mem0 removal
rides along with that card (`llm-mem0-inert`).

## Ruling 2 — Phase 2 wraps 5a; it does not rebuild it
The roadmap's Slate Pass §3 described candidate→ground→stamp→fuse→validate→emit. 5a built steps
1–5 for the setup lane, better than the sketch (pure stdlib detectors, entry_state, R/R floor,
origin stamped server-side, LLM origin stripped). **DecisionCard is therefore not a new engine —
it is the proposal-bound view of a validated idea.**

- **`trading/decision_card.py`** (Pydantic): numeric fields **copied** from a validated idea
  (entry_zone/stop/targets from `structures.py` via `validate_ideas`) + conviction stamped by the
  existing precedence in `create_phone_proposal` (strategy_stats → scorer-if-sufficient → unrated,
  `win_prob=None`); LLM text fields (thesis/risks/rank) carried over from the idea. Validator:
  numbers must byte-match the source idea; `origin` must equal `"structure"`; direction long-only.
  **This turns the conviction-lane hard-block from a comment into a type** — a conviction idea
  cannot construct a DecisionCard, period. Add the same guard as a runtime assert at the bridge.
- **The bridge** (the only genuinely new moving part): after a slate scan, take top-K
  `entry_state=ready` structure ideas → DecisionCard → `create_phone_proposal(source="slate_pass",
  thesis=idea.thesis, ...)` (`trading/proposals.py:614` — already origin-agnostic, already scores,
  already journals CONTEXT immutably). Respect `max_pending_agent_proposals=3`. Non-proposed slate
  members need **no new journaling** — `register_ideas` already shadow-tracks every idea in
  `idea_outcomes`; the roadmap's `slate_considered` concept is *already live* under a different
  name. Proposed cards get their outcome row linked to the proposal id (one new column).
- **The 08:45 Slate Pass** = a scheduled `setup_scan` over the S&P-500 universe shortlist (the
  screener + cached-bars path — never bursts Schwab) + the bridge. One new scheduled job, not a
  new engine.
- **Mode field:** stamp `mode="swing"` on all 5a-lane cards now (matches the detectors' horizon);
  the full trade-vs-invest build stays Phase 4.

**⛔ Gate unchanged:** the bridge builds only after the founder's ~1–2-week idea-quality verdict
(5b, judging starts 2026-07-06) says the ideas are worth arming, and it ships with full adversarial
ceremony (it is money-adjacent: it feeds the phone). Phase 1 has no such gate — build now.

## Sequence + effort (net: Phase 1–2 shrink from ~4 wks to ~2.5)
| Step | What | Effort | Gate |
|---|---|---|---|
| 1a | tool-calling in `ai/llm/` | 3–4 d | none — build now |
| 1b | one Python loop; web→proxy; delete legacy | ~1 wk | none |
| 1c | traces + golden set (closes `llm-eval-harness`) | 3–4 d | none |
| 2 | DecisionCard + bridge + 08:45 slate job | ~1 wk incl. adversarial review | **5b verdict** |

## Appendix — the 5b judging rubric (founder, starting today)
Grade each resolved idea 1–5 on: (a) **would you have taken it?** (b) was the entry zone honest
(price actually offered the zone)? (c) was the stop survivable (not wicked out before the move)?
(d) thesis quality — did the stated reason play out, even if the trade lost? Log the grade in the
`founder_verdict` column (`idea_outcomes` — it exists, unused, waiting for exactly this). The 5b
gate passes when: ≥10 resolved ideas, mean (a) ≥ 3, and zero "the numbers were wrong" fails
(b/c-type). Mechanical outcomes (`idea_tracker`) accumulate regardless — verdict grades judgment,
the tracker grades results.
