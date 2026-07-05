# Copilot Memory — why it's storing junk, and how to make it real

*Founder-requested deep dive, 2026-07-05. Investigation only — no code changed. Companion cards on the board
(`memory-overhaul` in the founder lane). Written to be readable without knowing the tech; jargon gets explained
the first time it appears.*

---

## TL;DR — your instinct is correct

The memory widget looks dumb because **most of what's in it was never "decided" to be a memory by anyone or
anything**. It's not that SQLite is a bad place to keep memories (it's fine — that part is a non-issue). The
problem is there's almost no *judgment* on either side of the database:

- **On the way in**, two of the three writers are automatic loggers: every deep-research run dumps the first
  500 characters of its report in as a "memory," and every saved chat turn runs an extractor that — when its
  LLM call fails or returns nothing — falls back to **keyword regexes** that store your raw message. You asked
  *"how do you think the strat2 momentum strategy is working so far?"* and the word "strategy" tripped a regex,
  so your **question** got stored as a **preference**. That's the exact mechanism behind most of the junk.
- **On the way out**, retrieval is word-overlap matching (does the memory share literal words with your
  question), not meaning-based search. And the path that would feed memories into the copilot's context each
  turn is currently switched off in the web chat.

Right now the table holds **29 rows: 17 "research", 6 "lesson", 6 "preference"** — and of the 17 research rows,
**seven are near-identical SLB summaries** from you re-running research on the same symbol. Nothing deduped
them because the dedup check is word-overlap with a loose threshold, and each rerun words the same facts
differently.

---

## 1. How it actually works today (the 5-minute tech lesson)

**Storage.** One SQLite table (`memories` in `data/trading.db`): id, type (`preference` / `lesson` /
`research`), a free-text `content` field, a metadata blob, timestamps. That's it — no importance score, no
expiry, no embedding. Think of it as a notes shoebox: anything anyone throws in stays forever, newest on top.

**Writers — who throws notes in the shoebox:**

| Writer | When it fires | What it stores | Judgment involved |
|---|---|---|---|
| Chat-turn extractor | after **every** saved chat turn | LLM is asked "extract durable facts the user stated"; **if that call fails/errors, regex fallback stores up to 300 raw chars of your message** | LLM: some. Fallback: none — keyword triggers (`strategy`, `always`, `lesson`, `learned`…) |
| Research engine | after **every** deep-research run | first ~500 chars of the report summary, verbatim | none — unconditional log |
| Setup scan | after every scan | "Setup scan: N ideas from M candidates" | none — unconditional log |
| Reflection loop / retrospective / journal review | scheduled | LLM-written lessons ("title: content") | some — these are the sanest writers |

Notably, **the copilot itself has no "save this" tool** — the one agent that actually exercises judgment in
conversation *can't* choose to remember something. Everything is side-effect logging.

**Readers — who takes notes out:**

- The **memory widget** just lists rows newest-first (polls every 20s). No ranking beyond recency.
- The **chat agent** has a `search_memories` tool it can choose to call — but the search is SQL `LIKE` +
  word-overlap scoring. "What did I say about position sizing?" only finds a memory if it shares literal words.
- The **automatic per-turn injection** ("silently give the copilot its relevant memories every turn") exists in
  the Python backend but the web chat calls it with `injectMemories=false` — it's dead in the live path.

**The mem0 footnote** (you remembered right): we decided against mem0's hosted cloud service, but adopted the
self-hosted `mem0ai` Python library as an optional *mirror* alongside SQLite. It's the one component here that
can do meaning-based (vector) search — except a result-parsing bug means its search branch **never returns
hits**, so everything silently falls through to the word-overlap path. So in practice: vector memory installed,
plugged in, inert.

**One vocabulary item, because it's the crux of the fix.** An *embedding* is a fingerprint of a sentence's
*meaning* — a list of numbers such that "I like to keep positions small" and "prefers modest position sizing"
end up with nearly the same numbers even though they share almost no words. A *vector store* is just a table of
those fingerprints you can search by closeness. That's the entire trick behind memory systems that feel smart
(mem0, ChatGPT memory, the Odysseus-style apps): meaning-based lookup at read time, and meaning-based
comparison at write time ("do I already know this? is this an update to an existing memory?").

---

## 2. Why each kind of junk exists — receipts from your actual data

| What you see | Row | Root cause |
|---|---|---|
| Your question stored as a "preference": *"From chat: how do you think the strat2 momentum strategy is working so far?"* | id 148 | regex fallback keyword `strategy` — no judgment ever touched it |
| Your request stored as a "lesson": *"i was hoping you could take a look outside of my watchlist…"* | id 149 | same fallback path (`lesson`-family keywords) |
| Seven SLB research blurbs in a row | ids 152–159 | every research run auto-logs its summary; word-overlap dedup can't tell they're the same thesis reworded |
| "Setup scan: 0 ideas from 12 candidates" as a *memory* | id 164 | unconditional post-scan log — this is a *log line*, not knowledge |
| AMD/NVDA/GLW TL;DRs | ids 160–166 | research reports are **artifacts** that already live in research history; re-logging their first 500 chars into memory is pure pollution |

The unifying theme: **the system confuses "things that happened" with "things worth remembering."** A memory
system's whole job is that filter, and the filter is the part that was never built. (BUILD.md even knows this:
the success-gate checklist still has an unchecked *"no keyword triggers"* item, and vector memory is roadmap
item R4.)

---

## 3. What good looks like (the mem0 pattern, which we already chose)

Every credible implementation converges on the same loop:

1. **Extract** — after a conversation/event, an LLM proposes candidate facts *worth keeping* (small, durable,
   in the user's or agent's voice: "prefers X", "SLB thesis: fiber capex supercycle, entered at $45").
2. **Judge** — for each candidate, compare (by meaning, via embeddings) against existing memories and decide
   **ADD / UPDATE / DELETE / NOOP**. This one step kills duplicates, keeps facts current, and rejects noise.
3. **Store** — the fact plus provenance (where it came from), type, and timestamps. SQLite is a perfectly good
   home; we'd add `sqlite-vec` so the same file can hold the meaning-fingerprints.
4. **Retrieve** — at question time, embed the query and pull the top-k closest memories (weighted by recency
   and type), and inject them into the copilot's context automatically — the user never asks "check your
   memory."

Steps 2 and 4 are what we're missing entirely; step 1 exists but with the junk-producing fallback; step 3 is
solid already. **SQLite was never the problem.**

---

## 4. Proposed fix, in three phases (no code yet — your call on go)

**Phase 1 — stop the bleeding (small, ~an evening).** Delete the regex fallback (LLM-or-nothing extraction);
stop auto-logging research summaries and scan results as memories (research already has its own history — if a
run produced a durable *thesis*, the extractor can propose it as a proper lesson instead); add a write-time LLM
judge with a hard bar ("is this a durable fact about the user, a strategy, or a market view — or just chatter?").
One-time cleanup of the current 29 rows (I'd curate: keep ~6-8, delete the rest — with your sign-off).
Also: give the copilot an explicit `save_memory` tool so deliberate "remember this" moments work.

**Phase 2 — retrieval that works (the R4 roadmap item, ~a day).** `sqlite-vec` embeddings on write; a
`recall_memory` that searches by meaning; fix (or drop) the inert mem0 mirror; turn per-turn injection ON for
the copilot with top-k + recency weighting. This is when memory starts silently improving answers.

**Phase 3 — lifecycle (later).** UPDATE/merge instead of add-only (the judge from Phase 1 enables this),
importance + time-decay so stale market facts fade, and provenance shown in the widget ("saved because you said
X on July 3") so you can audit why anything exists.

This sequencing matches the existing roadmap (BUILD.md R4: sqlite-vec + recall tool) and the perception-layer
rule (RAG only over unstructured text, never numbers). Memory quality is squarely on the critical path for the
Decision Engine — the LEARN half is only as good as what gets remembered.

---

*Verification notes: all mechanisms above traced to code (`ai/chat/memory_extract.py`, `ai/research/engine_v2.py:489`,
`ai/reflection/service.py`, `db/ai_tables.py`, `ai/memories/retrieval.py`) and confirmed against the live DB
read-only (29 rows as of tonight). Nothing was modified.*
