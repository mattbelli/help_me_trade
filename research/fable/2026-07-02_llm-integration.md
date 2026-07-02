# LLM-Layer Audit — help_me_trade
*Fable 5 audit · read-only · 2026-07-02 · Question: does the current AI layer move toward a real, good trading partner?*

---

## 1. Architecture: one brain's organs, three different bodies
The pieces are individually well-built and unusually safety-literate, but there is **no single agent loop** — there are three, with different capabilities, and the best one lives in TypeScript.

**Loop A — Web (the only real agent).** `frontend/app/api/chat/route.ts` runs Vercel AI SDK `streamText` with `stopWhen: stepCountIs(6)` — the only *iterative* tool loop in the app. To its credit it's a TS *head* on a Python *body*: system prompt from Python (`fetchChatContext` → `ai/chat/brain.build_turn_context`), tools execute in Python (`ai/chat/tools.execute_tool`), persistence via Python (`/api/chat/finalize`).

**Loop B — Python legacy (`ai/chat/agent.run_chat_stream`).** Now a **degenerate, tool-less path**: `tool_results` initialized empty, passed straight to `_build_final_messages` — the LLM answers with zero data under a prompt that says "only discuss portfolio after calling the matching tool." Reachable via `proxyLegacyPythonChat`. Delete or quarantine; today it's a hallucination generator wearing the honesty prompt.

**Loop C — Mobile (`ai/chat/mobile_agent.run_mobile_chat_stream`).** Plan-then-execute: one LLM call to `plan_tools`, execute the batch, one final completion. **No step can depend on a prior step** — the chain the tool docs prescribe ("to set an alert at VAL, first call get_context") is impossible on mobile in one turn. The planner prompt is a hand-maintained **duplicate** of `TOOL_DEFINITIONS`, itself duplicated a third time as zod in `frontend/lib/chat-tools.ts`. Drift is real: `ai/chat/intent.py` `_ORDER_OPTION` regex has no TS twin.

**Root cause:** the Python `LLMClient` protocol (`ai/llm/base.py`) is text-in/text-out only — no native tool-calling. Everything agentic in Python is prompt-engineered JSON scraping (`ai/llm/structured.py`). That's why the real loop had to live in TS. Coherent *design*, fragmented *runtime*.

## 2. Grounding & honesty: best-in-class where it's code, brittle where it's prose
Anti-fabrication is enforced **structurally**, which is rare and right:
- `ai/perf_context.py` strips unreliable FIFO dollar fields before the LLM sees them (`_strip_unreliable_pnl_fields`); labels incomplete positions "proceeds only — NOT P&L." The model can't quote a number it was never given.
- `signals/decision_scorer.score_symbol` returns `None` not a guess (missing artifacts, <252 bars); carries `sufficient`/`ci`/`brier`/`low_confidence`.
- `ai/journal_review.py` refuses win-rate below `MIN_SAMPLES=10`; Wilson-CI grounded.
- Write surface matches `PERCEPTION_LAYER.md`: broad reads, narrow curated writes, `propose_order` behind approval + RiskEngine + read-only-account rejection; mobile intercepts into `create_phone_proposal`.

**Where it can still fabricate:** (1) Loop B answers with no data; mobile planner JSON-parse failure returns `[]` silently → answer from vibes. (2) Fail-soft = silently absent: `build_symbol_context` drops failed blocks with only a debug log — no "quote unavailable" marker, so the model fills gaps from training data. (3) No output verification that numbers in the answer appeared in a tool result. (4) Honesty rules live only in `agent.py`; retrospective/briefs/insights each carry hand-written variants → one drifted edit = one dishonest surface.

## 3. Partner or Q&A bot? Today: a well-grounded Q&A bot with a proposal pipeline bolted on
**Proactivity — half-built, disconnected.** The propose loop is the crown jewel (scan → `create_phone_proposal` with score+thesis → push → biometric approve → chokepoint). Briefs exist (`signals/reports.py`). But **chat never initiates**; the partner you talk to has no idea it proposed something an hour ago unless you make it call a tool.

**Continuity — the saddest finding.** The infra to remember exists and is *switched off on every live surface*:
- The decision-journal context block (`ai/chat/context.turn_journal_context` — "your track record: 3W/2L, avg +0.4R") is **never injected**: `route.ts` never sends `injectJournal`; mobile builds the prompt with no blocks. Tested, reviewed, dead in prod.
- Memory injection is opt-in (`autoInjectMemories`, default false).
- Automatic memory extraction is disabled on all three loops (`extract=False`).

So the "partner" starts every turn amnesiac about its own calls and the founder's preferences — despite both being in the DB.

**Point of view — none by design.** No persona, no risk profile, no instruction to push back. `propose_order` surfaces the score *after* creating the proposal; the agent never says "I scored this 38% — I wouldn't."

## 4. Memory & learning: write-mostly, and the vector store never worked
**mem0 bug confirmed, worse than "a bug."** `ai/memories/__init__.py:76-98`: `Memory.search()` returns `{"results":[...]}`; `for item in results:` iterates dict *keys* (strings), `isinstance(item, dict)` fails for all, falls back to token-overlap. Semantic memory has **never once fired**, invisibly. Dead dependency manufacturing false confidence.

| Loop | Verdict |
|---|---|
| Decision journal (`db/decision_journal.py`) | Excellent substrate — reasoning recorded before outcome. |
| `journal_review` calibration → lesson memories | Correct, honest — but output is a memory row that's never injected. **Loop open.** |
| Daily `reflection_loop` | Weak: snapshot + last 10 lessons → LLM writes more lessons. No promotion/expiry/feedback. Write-only. |
| Retrospective | Best artifact: OOS-backtested rule proposals, ship disabled, honest disclaimers. Human-in-loop. |

**Smallest real learning loop:** journal verdicts → injected into (a) every chat turn (the existing block, on) and (b) the propose-time thesis prompt ("dip_buy ran overconfident by 12 pts over 23 trades — temper the pitch"). Plumbing, not research.

## 5. Reasoning quality: the LLM narrates; it does not yet reason over trades
Scorer-is-deterministic is correct. But the **REASON stage is missing**: nowhere does the LLM take N candidates + regime + news + exposure and produce a ranked, argued slate. Rules fire individually; scorer stamps win%; LLM decorates. No fusion, no veto, no cross-candidate comparison. `get_context` also lacks the planned `strategy_signals`, news/sentiment, and market/regime scopes — so a good reasoning prompt would reason half-blind. JSON-mode only for OpenAI-compat providers; others use regex+retry. One flat default model for everything (no reasoning/routine tiering). **Eval/observability: zero** — no golden set, no tool-routing accuracy, no grounding check, no tracing. A prompt change that misrouted 30% of queries would go unnoticed.

## 6. The plan — priority order
1. **Switch on the partner it already is (days).** Inject the journal block + memories on web (`fetchChatContext` → `inject_journal=True`) + add both to the mobile prompt; re-enable turn memory extraction for saved chats. Highest partner-feel-per-line in the codebase.
2. **Unify on one Python brain (1-2 wks).** Add native tool-calling to `LLMClient` (Anthropic `tools` + OpenAI functions), one streaming agentic loop in `ai/chat/`, TS route becomes a thin SSE proxy, point mobile at it. Delete Loop B; delete `plan_tools` duplicate menu; generate or retire the zod schemas. One prompt site, one tool registry.
3. **Replace mem0 with something boring (2-3 days).** Delete the mem0 mirror; `db/ai_tables` + SQLite FTS5 + a small embeddings column. Boring+observable beats clever+inert.
4. **Close the calibration loop into prompts (2-3 days).** `journal_review` verdicts → propose-time thesis + chat context. First moment the system *acts* on what it learned.
5. **Structured DecisionCard + a real REASON pass (1-2 wks).** Pydantic schema (thesis/entry/stop/targets/risks/win_prob) where **numeric fields are copied from `score_symbol` in code, never model-generated**. Then a daily "rank the candidates" pass over scan + `get_context` + regime → 0-3 cards. Add `strategy_signals` + regime scope to perception first.
6. **Make the proactive loop conversational (1 wk).** Morning brief → push + seeded chat thread; "while-you-were-away" digest on app-open. Generators exist in `signals/reports.py`; delivery wiring.
7. **Eval harness + tracing (1 wk, then continuous).** Log every prompt/tool-call/response (or self-host Langfuse); a ~50-case golden set (tool routing, grounding = "does every number in the answer appear in a tool result?", refusal cases). Without this, items 2 & 5 can't be verified.
8. **Persona + standing preferences (2 days).** Founder-curated profile (risk tolerance, sizing, style, "push back when the score disagrees with me") injected every turn, editable in Settings. Gives the partner a spine.

**Bottom line:** the safety chassis and honesty engineering are ahead of most production LLM products; the *partnership* layer is mostly built but **disconnected** — dead journal injection, disabled extraction, an inert vector store, three divergent loops, and no eval loop to know if any of it is good. Items 1-4 are wiring, not research, and would change how this thing feels within two weeks.
