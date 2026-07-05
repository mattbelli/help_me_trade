# Copilot Memory Overhaul — elite design (2026-07-05)

*Adversarial review + implementation-grade upgrade of `References/MEMORY_SYSTEM_REPORT.md`. Every claim below
was re-verified against the live code and the live DB (read-only). This doc supersedes the report's Phase 1–3
sketch and the BUILD.md R4 line "sqlite-vec + recall_memory" (References/BUILD.md:60) — see §9 for why.*

---

## 0. Verdict on the original report

**What it got right (verified):**
- The root-cause diagnosis is accurate and honest. The regex fallback (`ai/chat/memory_extract.py:20-56`,
  invoked at `:130-131`) really is the mechanism behind rows 148/149; the research auto-log
  (`ai/research/engine_v2.py:489-497`) and scan auto-log (`ai/research/setup_scan.py:1107-1116`) really are
  unconditional; per-turn injection really is dead in the live web path
  (`frontend/lib/python-server.ts:44` defaults `injectMemories=false`); retrieval really is token overlap
  (`ai/memories/retrieval.py:17-31`); the mem0 semantic branch really is inert
  (`ai/memories/__init__.py:73-100` iterates mem0 2.0.7's `{"results": [...]}` dict as if it were a list —
  the keys fail `isinstance(item, dict)`, `out` stays empty, everything falls through; BUILD.md:511-514
  documents the same).
- "SQLite was never the problem" — correct, and the right thing to tell the founder.
- The extract → judge → store → retrieve loop is the right skeleton. Phase-1-first ("stop the bleeding
  before adding capability") is the right rollout instinct.
- The framing "the system confuses things-that-happened with things-worth-remembering" is exactly the
  product insight; this doc keeps it as the design's north star.

**What it got wrong or missed (blunt):**
1. **It missed a live correctness bug worse than the junk: the dedup-on-write path swallows corrections.**
   `db/ai_tables.py:250-269` treats any ≥0.5-Jaccard candidate as "the same fact", refreshes the OLD row's
   timestamp, and **discards the new content**. If the founder says "my cap is now 25%" it Jaccard-matches
   the stored "cap at 20%" (shared tokens: cap/individual/stock/positions → 4/6 = 0.67) and the correction
   is silently thrown away while the stale 20% fact gets re-stamped as fresh. The dedup is simultaneously
   too tight (eats contradictions) and too loose (rows 138–142 are five surviving paraphrases of the same
   20%-cap fact). The report only noticed the second failure mode.
2. **Zero safety analysis.** Nothing in the report says which memories are allowed to influence trade
   proposals. This system has *already* fabricated a preference once (BUILD.md:756, the self-reinforcing
   "20% cap" loop) and the live DB shows the loop now crosses subsystems: row 153 (a "research" memory)
   embeds lesson 144's text verbatim, and row 155 cites `[memory_9]` — research grounded on memories that
   were themselves research summaries, re-summarized into memory. For an app whose mission is trust-gated
   autonomous trading, memory→proposal influence needs an explicit authority model (§6). This was the
   report's biggest omission.
3. **No taxonomy work.** It keeps `preference/lesson/research` — but "research" as a memory *type* is the
   bug, not a data-quality accident, and "preference" conflates hard rules ("never short") with soft tastes
   ("I like round-number stops") and stale-able market theses. Types are where staleness, injection policy,
   and trading-authority all attach; leaving them untouched leaves the design unbuildable.
4. **"sqlite-vec + embeddings" was asserted, not decided.** The default LLM is DeepSeek
   (`config/ai_providers.py:75`) which has no embeddings API; the report never says where embeddings come
   from, what they cost, or what happens when they're unavailable. §5 decides this concretely for this box.
5. **No trust surface, no eval, no kill criteria.** "Provenance shown in the widget" is one clause in
   Phase 3; there is no citation design, no verbatim-vs-paraphrase contract for "remember this", no golden
   set, no definition of done. For the founder-facing half of the system, that's most of the work.

**The 5 highest-leverage upgrades this review adds:**
1. **Authority model** — `user_authored` / `user_confirmed` / `agent_inferred`, with a hard rule about which
   may touch trade-proposal parameters (§6).
2. **Taxonomy that carves at the joints** — `directive | preference | fact | lesson | thesis`, each with its
   own staleness, injection tier, and authority ceiling; "research" abolished as a type; episodic events stay
   in their own tables and are perceived via `query()`, never via memory (§2).
3. **SUPERSEDE semantics** — contradictions create a new row linked to the old (`supersedes_id`), old row
   becomes `status='superseded'` (auditable, never injected); fixes the correction-swallowing bug and gives
   the founder a visible history of his own rule changes (§3.3, §7).
4. **Retrieval decided for this box** — local Ollama embeddings (`nomic-embed-text`, already-running daemon
   at :11434) + FTS5 (compiled in, verified) hybrid with ticker exact-boost; brute-force cosine over the
   tiny corpus, **no sqlite-vec dependency, mem0 removed entirely** (§5).
5. **Trust + eval surface** — `[mem:ID]` citations rendered as chips, provenance/quote/pin/confirm/undo in
   the widget, a golden-set eval harness with numeric acceptance bars, and kill criteria with one-flag
   rollback (§7, §8).

---

## 1. Design principles (derived from mission + incidents)

1. **A memory is a decision, never a side effect.** Every row exists because an LLM judge (or the founder)
   explicitly decided ADD/UPDATE/SUPERSEDE. No writer bypasses the judge except the founder's own hands.
2. **Prefer forgetting to fabricating.** Extraction is LLM-or-nothing (a missed memory is recoverable — the
   founder can say "remember this"; a junk memory costs trust, the app's scarcest resource). Expected output
   of most turns is *zero* candidates.
3. **Nothing is ever silently destroyed.** Judge and copilot can archive/supersede; only the founder (widget
   or API) can hard-delete. Every state change keeps provenance.
4. **The model only sees what it can defend.** Injected memories carry id + age + authority; the copilot
   cites them; the founder can audit any memory-driven claim in two clicks.
5. **Memory is not config.** Hard risk limits live in Settings and the RiskEngine; memory never becomes a
   second, unvalidated store of caps and limits (the extractor already excludes risk/config settings —
   `ai/chat/memory_extract.py:108-115` — keep that).
6. **Numbers still come from perception.** Unchanged locked rule (References/PERCEPTION_LAYER.md:44-46):
   memory is RAG over unstructured durable text; prices/positions/PnL come from `get_context`/`query`.

---

## 2. Taxonomy — five types, each with different physics

| Type | What it is | Examples | Staleness | Injection tier | Max authority |
|---|---|---|---|---|---|
| `directive` | A standing rule the founder gave the agent. Imperative voice. | "Never propose shorts." "Cap any single name at 20% of portfolio." "Always state the stop before the target." | None — valid until superseded/revoked. | **Always injected** (tier A) | user_authored |
| `preference` | A soft taste/style fact about the founder. | "Prefers weekly options on indexes, monthlies on single names." "Wants terse answers during market hours." | None — superseded when he changes his mind. | Relevance (tier B) | user_authored |
| `fact` | A durable biographical/structural fact. | "Has 4 real Schwab accounts, read-only." "Trades from his phone during workday." | Slow; superseded on change. | Relevance (tier B) | user_authored |
| `lesson` | A behavioral generalization with evidence, from the founder or the learn loop. | "Tends to cut winners early — 6 of last 8 winners exited below target." "dip_buy win% overconfident by 14pts over 32 trades." | Slow decay of retrieval prior (half-life 180d); never auto-deleted. | Relevance (tier B) | user_confirmed |
| `thesis` | A market view about a symbol/sector, held until invalidated or expired. | "SLB: oversold on oil capex fears; fiber narrative intact; watching $43 support." | **`valid_until` (default +90d)**; expired = excluded from injection, labeled STALE in search. | Relevance (tier B), only while valid | agent_inferred (advisory) |

**What is explicitly NOT a memory:**
- Research reports → `research_artifacts` (already stored, already browsable). Kill the auto-log.
- Scan results ("0 ideas from 12 candidates") → scan artifacts/logs. Kill the auto-log.
- Portfolio-state observations ("cash is only 1.6%") → these are snapshots, not knowledge; they live in
  `reflection_runs` (where rows 143–147's content already also exists) and are perceived via `query`.
- Alert/rule creations → the rule row is the durable record (existing gate at `memory_extract.py:93-95`, keep).
- The agent's own track record → `decision_journal` (References/DECISION_JOURNAL_DESIGN.md), injected via
  the existing journal context block (`ai/chat/context.py:81-152`), not via memory.

Migration of `MEMORY_TYPES` (`db/ai_tables.py:17`): new set `{directive, preference, fact, lesson, thesis}`.
`research` remains *readable* (legacy rows keep their type until archived in migration §9.0) but is rejected
on write. Frontend union + labels + filter chips updated to match (`frontend/lib/memories.ts:3`,
`frontend/components/workspace/widgets/memory-widget.tsx:26-31`).

---

## 3. Schema

Additive `ALTER TABLE`s in `init_ai_tables` following the existing try/except pattern
(`db/ai_tables.py:79-89`). No table rebuild; existing ids stable.

```sql
-- lifecycle
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
    -- active | superseded | archived | expired
ALTER TABLE memories ADD COLUMN supersedes_id INTEGER;      -- new row → row it replaced
ALTER TABLE memories ADD COLUMN valid_until TEXT;           -- theses only (ISO date)

-- provenance + authority
ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy';
    -- user_explicit | chat_extract | agent_save | reflection | retrospective | research_promote | widget_manual
ALTER TABLE memories ADD COLUMN authority TEXT NOT NULL DEFAULT 'agent_inferred';
    -- user_authored | user_confirmed | agent_inferred
ALTER TABLE memories ADD COLUMN quote TEXT;                 -- founder's verbatim words, when applicable
    -- (source_message_id / session_id stay in metadata_json as today — memory_extract.py:134-136)

-- trust surface
ALTER TABLE memories ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_used_at TEXT;          -- last time injected into a turn
ALTER TABLE memories ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0;  -- times cited as [mem:ID]

-- retrieval
ALTER TABLE memories ADD COLUMN symbols TEXT NOT NULL DEFAULT '[]';   -- JSON array, e.g. ["SLB"]
ALTER TABLE memories ADD COLUMN embedding BLOB;             -- float32[768], little-endian
ALTER TABLE memories ADD COLUMN embedding_model TEXT;       -- e.g. 'nomic-embed-text-v1.5'

CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status, memory_type);

-- lexical index (FTS5 verified available in the app venv's sqlite)
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, quote, symbols,
    content='memories', content_rowid='id'
);
-- kept in sync by AFTER INSERT/UPDATE/DELETE triggers on memories (standard external-content pattern);
-- one-time backfill at migration.
```

**Deliberately absent:** no `importance REAL`, no `confidence REAL`. A fabricated float is pseudo-precision
(the calibration rule cuts both ways); the judge's admission bar + `pinned` + type priors do that job with
zero fake numbers.

### 3.1 What replaces the Jaccard dedup
`_is_duplicate` / `_memory_tokens` / the dedup block in `create_memory` (`db/ai_tables.py:185-229, 250-269`)
are **deleted**. `create_memory` becomes a dumb insert; all sameness/contradiction judgment moves to the
judge (§4.2), which sees candidates *with* their nearest existing memories and decides with meaning, not
token sets. The one guard kept at the DB layer: exact-normalized-string match short-circuits to NOOP (free,
can't swallow a correction).

### 3.2 Status semantics
- `active` — retrievable + injectable.
- `superseded` — replaced by a newer row (`supersedes_id` on the new row points here). Never injected;
  visible in widget history view and in `search_memories(include_inactive=true)`.
- `archived` — retired by founder or judge (junk, no longer relevant). Never injected.
- `expired` — thesis past `valid_until` (stamped by the daily sweep, §3.3). Never injected; search shows it
  with a `STALE (from <month>)` label so the copilot can say "my July thesis said X, but it's stale."

### 3.3 Lifecycle jobs
The existing 24h reflection scheduler (`ai/reflection/scheduler.py:14-25`) gains a memory sweep step:
1. Stamp `status='expired'` on theses past `valid_until`.
2. Nothing else. No auto-decay deletions, no auto-merges outside the judge. Boring on purpose.

---

## 4. Write path

### 4.1 Extractor (replaces `ai/chat/memory_extract.py`)

**Triggers:** unchanged — after saved-mode turns only (`ai/chat/brain.py:38-44,107` semantics kept; temporary
chats never write). Existing alert-intent and review-intent gates kept (`memory_extract.py:93-99`).

**The regex fallback (`memory_extract.py:20-56`) is deleted, along with its invocation (`:130-131`).**
LLM-or-nothing: on LLM failure, retry once (empty-content retry per the DeepSeek json_mode lesson), then save
nothing and log. Output is small, so JSON mode is fine here (the markdown-over-JSON lesson applies to long
outputs).

**Prompt (sketch — final wording iterates through the eval set §8):**

```
You extract durable memories for a trading copilot's long-term store. Most turns contain
NOTHING worth remembering — an empty list is the expected, correct output.

A candidate memory must be ALL of:
- stated by the USER (never the assistant; never a restatement of the "Already known" list below),
- durable (still true and useful in 3 months),
- about the user's rules, tastes, situation, or a hard-won lesson — NOT a question, NOT a one-off
  request/action, NOT market commentary, NOT a risk/config setting (those live in Settings).

Types:
- directive: a standing order to you, imperative ("never/always ...").
- preference: a soft taste or style.
- fact: a durable fact about the user's accounts/situation/workflow.
- lesson: a behavioral lesson the user stated about their own trading.
(You may NOT emit type "thesis" — theses are saved deliberately, not extracted.)

Rules:
- Write content in third person, ≤ 200 chars, keep the user's numbers VERBATIM (20% stays 20%).
- Also return "quote": the user's exact words the memory came from (substring of the user message).
- Return "symbols": tickers the memory is about, [] if none.
- Max 2 candidates per turn.

Already known (do NOT re-extract, even reworded): <injected memories + top-10 recent, id + content>

Return JSON: {"memories": [{"type": ..., "content": ..., "quote": ..., "symbols": [...]}]}
```

The **"Already known" block is the anti-echo mechanism**: the exact memories injected into this turn
(§5.4) plus the 10 most recently touched rows are shown to the extractor as negative examples. This is the
structural fix for the 2026-06-20 self-reinforcing loop (BUILD.md:756) — the previous fix (token-overlap
dedup) is what created the correction-swallowing bug in §0.1.

### 4.2 Judge — ADD / UPDATE / SUPERSEDE / NOOP (new, `ai/memories/judge.py`)

Runs on every candidate batch from any writer (extractor, save_memory tool, reflection, research promotion).
One LLM call per batch.

**Input per candidate:** the candidate (type/content/quote/symbols/source) + its **top-8 nearest existing
memories** by the §5 hybrid retriever (id, type, status, content, created_at, authority).

**Output per candidate:** `{action, target_id?, content?, valid_until?, reason}` where:
- `ADD` — genuinely new. Insert as `active`.
- `UPDATE target_id` — compatible refinement of the same fact (adds detail, no conflict). Rewrite that
  row's content (judge supplies the merged text), bump `updated_at`, re-embed. Authority of the row is
  **never raised** by an UPDATE from an agent-side writer.
- `SUPERSEDE target_id` — conflicts with an existing memory (user changed his mind, thesis replaced).
  Insert the new row with `supersedes_id=target_id`; set old row `status='superseded'`. This is the
  correction path the current code destroys.
- `NOOP` — already known, or fails the admission bar ("would a great human assistant write this down?").
  Nothing stored; reason logged (feeds the eval).

**The judge can never DELETE and can never target a `pinned` or `user_authored` row with SUPERSEDE/UPDATE
unless the candidate's source is `user_explicit`** (i.e., only the founder's own words can override the
founder's own rules; a reflection run can't overwrite "never short"). Poisoning containment in one sentence.

Judge failures (both attempts): store nothing, log, drop. Same prefer-forgetting rule.

### 4.3 Each automatic writer, redesigned

| Writer | Today | New behavior |
|---|---|---|
| Chat extractor | regex fallback junk (`memory_extract.py:130-131`) | §4.1 extractor → §4.2 judge. |
| Research engine | unconditional 500-char summary dump (`ai/research/engine_v2.py:489-497`) | **Delete the `get_memory_service().add(...)` call.** Reports live in `research_artifacts` only. Phase 3 adds *promotion*: on completion, one optional thesis candidate ("<SYM> thesis: ... " + `valid_until`) → judge, `source='research_promote'`, advisory authority. The judge is what collapses 7 SLB reruns into 1 updated thesis (UPDATE/SUPERSEDE, not 7 ADDs). |
| Setup scan | "Setup scan: 0 ideas from 12 candidates" (`ai/research/setup_scan.py:1107-1116`) | **Delete the call.** A scan result is a log line; scans already persist artifacts + register idea outcomes. No replacement. |
| Reflection | direct `memory.add` of LLM lessons (`ai/reflection/service.py:52-63`) | Candidates → judge, `source='reflection'`, type `lesson`. Prompt hardened: lessons must be *behavioral generalizations with evidence counts*, not portfolio snapshots ("cash is 1.6%" belongs in `reflection_runs`, where it already also lives). Cap 3/run (as today). |
| Retrospective | direct `memory.add` (`ai/retrospective.py:164-176`) | Same: → judge, `source='retrospective'`, cap kept. |
| Journal review (J3) | seeds calibration-drift lessons via the same service | Same: → judge. Its Wilson-CI honesty rules are already elite — unchanged otherwise. |

### 4.4 Copilot tool contracts

Registered on **both** surfaces per the perception-layer gotcha (`ai/chat/tools.py` + `frontend/lib/chat-tools.ts`
/ `chat-toolbar-config.ts`; PERCEPTION_LAYER.md:98-105). `search_memories` already exists on both
(`ai/chat/tools.py:88-91,526-529`; `frontend/lib/chat-tools.ts:53-60`).

**`save_memory`** *(new — the report was right that its absence is absurd)*
```
args: { content: string, type: "directive"|"preference"|"fact"|"lesson"|"thesis",
        quote?: string, symbols?: string[], valid_until?: string }
```
- When the user said "remember this/that ..." the copilot MUST pass `quote` = the user's exact words;
  `content` is the canonical third-person rewrite. Source `user_explicit`, authority `user_authored`.
  The verbatim contract: **the paraphrase is what's retrieved; the quote is what's displayed and audited** —
  the founder always sees his own words next to the agent's rewrite in the widget.
- When the copilot decides on its own that something is worth keeping (e.g., a thesis after a research
  discussion): source `agent_save`, authority `agent_inferred`.
- Routed through the judge, but with NOOP disabled when source is `user_explicit` (an explicit "remember
  this" is always honored; the judge is only choosing between ADD/UPDATE/SUPERSEDE targets).
- Returns `{id, action, content}`; the copilot confirms in-chat: `Saved [mem:171].`

**`search_memories`** *(upgraded in place)*
```
args: { query: string, limit?: number (≤20), type?: string, include_inactive?: boolean }
```
Backed by the §5 hybrid retriever. Results include id, type, status, age, authority; expired theses carry
`stale: true`. `include_inactive` surfaces superseded/archived rows for "what did I used to think?" audits.

**`forget_memory`** *(new)*
```
args: { id: number, reason: string }
```
Archives (never hard-deletes) — sets `status='archived'`, stores reason in metadata. The copilot may only
call it with an id it retrieved this session, and confirms in-chat. Hard delete remains founder-only
(widget / `DELETE /api/memories/{id}`, `api/ai_routes.py:688-692`).

---

## 5. Retrieval — hybrid, decided for this box

### 5.1 The decision
**FTS5 + local embeddings, brute-force cosine, no sqlite-vec, mem0 removed.** Rationale grounded here:

- **Corpus size:** single user + elite admission bar ⇒ realistically 50–300 active rows after a year.
  Brute-force cosine over ≤ a few thousand 768-dim float32 vectors is sub-millisecond in numpy. An ANN
  index (sqlite-vec) is solving a problem this box will not have for years; revisit only if active rows
  exceed ~5k (put that trigger in CODE_AUDIT, not in this build).
- **Embeddings must be local:** default provider is DeepSeek (no embeddings product); provider-agnostic
  keys rule out assuming OpenAI; financial text never leaves the box. **Ollama is already running on this
  server** (verified live at :11434, currently serving gemma4). One-time `ollama pull nomic-embed-text`
  (137M params, 768-dim, ~30ms/embed on this hardware, free forever). Implementation details that matter:
  nomic requires task prefixes — embed rows as `search_document: <content>` and queries as
  `search_query: <text>`; store `embedding_model` per row so a future model swap knows what to re-embed.
- **FTS5 is non-negotiable alongside vectors:** tickers and exact numbers are where embeddings are weakest
  ("SLB" must exact-match; "20%" matters verbatim). FTS5 verified compiled into the app venv's sqlite.
- **mem0 is removed** (requirements.txt:10, the wrapper branches in `ai/memories/__init__.py:28-36,52-60,
  73-100,124-152`). It is a third store that has already produced two real bugs (delete-drift, fixed
  2026-07-01; the still-live inert-search parsing bug), its 2.x default config wants an OpenAI key, and its
  entire value (semantic search + write-time judgment) is exactly what §4–§5 build natively in ~300 lines we
  fully control. `MemoryService` stays as the seam — same API, one store. This retires board card
  `mem0-inert` and the BUILD.md:322 "self-hosted mem0ai" decision (pattern kept, dependency dropped).

### 5.2 Scoring formula (`ai/memories/retrieval.py`, replacing token overlap wholesale)

```
eligible  = status='active' AND (valid_until IS NULL OR valid_until >= today)

lex(m)    = min-max-normalized FTS5 bm25 over the candidate set          ∈ [0,1]
sem(m)    = max(0, cosine(embed(query), m.embedding))                    ∈ [0,1]
rel(m)    = max(lex, sem) + 0.25 · [query symbols ∩ m.symbols ≠ ∅]

half_life = { lesson: 180d, thesis: 45d, fact: 365d, directive|preference: ∞ }
recency(m)= 0.6 + 0.4 · exp(−age_days / half_life)        (=1.0 where half_life=∞)

score(m)  = rel(m) · recency(m)
```

Top-k = 4, **floor = 0.35** — below the floor, inject nothing (silence beats noise; the floor is what makes
always-on injection safe). Query symbols = tickers regex-matched from the user text (same pattern as
`ai/chat/context.py:94-98`). Degradation ladder: Ollama down → `sem=0`, FTS5-only, log once; FTS5 error →
directives-only injection; retrieval exception → directives-only from a 60s cache. **The turn never fails
because memory failed.**

### 5.3 When retrieval runs
- **Every turn, automatically, in all modes** (temporary mode reads memories fine — it only skips *writing*,
  `ai/chat/brain.py:107`). The user should never have to say "check your memory."
- **Plus on demand** via `search_memories` when the copilot wants more than the injected top-4.

### 5.4 Injection policy (replaces the JSON block at `ai/chat/context.py:26-37`)

Two tiers, one markdown block (markdown, not JSON — the long-output lesson, and models attend to it better):

```
## Standing memory
Directives (always apply):
- [mem:140] Cap any single name at 20% of portfolio. (user, Jun 20)
Relevant now:
- [mem:167] (thesis, Jul 3, unconfirmed) SLB: oversold on oil-capex fears; watching $43. 
- [mem:144] (lesson, Jun 28) Tends to add to losers without stops.
When a memory materially shapes your answer or any trade discussion, cite it inline as [mem:ID].
Never treat an (unconfirmed) memory as the user's own rule — ask before acting on it.
```

- **Tier A** — all `active` directives + all `pinned` rows. Always injected, capped at 8 (if the founder
  ever exceeds 8 directives, the widget nags to consolidate — directives are supposed to be few).
- **Tier B** — top-4 above the floor from §5.2.
- **Budget:** ≤ 700 tokens total (tier A ≈ 250, tier B ≈ 450). Each line ≤ 200 chars by construction (§4.1).
- Flip `injectMemories` default to **true** in `frontend/lib/python-server.ts:44` (web) and default
  `inject_memories=True` in the planned Python `POST /api/chat/stream` (mobile). Both funnel through
  `build_turn_context` (`ai/chat/brain.py:47-66`) — one implementation, both clients, per the
  mobile-chat-architecture note. Injection bumps `last_used_at` on the injected rows.

---

## 6. Safety — memory vs the money path

- **The chokepoint does not read memory.** `RiskEngine.validate()` and `execution_service.submit()` take no
  memory input today and never will; hard limits live in settings. Memory influences only the *conversation
  and proposal-drafting* stage upstream of the human approval gate. (Verified: no `ai/memories` import
  anywhere under `trading/`.)
- **Authority rule (the new hard line):** only `user_authored` or `user_confirmed` `directive`/`preference`
  rows may be treated as the user's constraints when the copilot drafts or discusses proposals.
  `agent_inferred` rows are injected with an explicit `(unconfirmed)` tag and the standing instruction
  (§5.4) to ask before acting on them. A fabricated preference can therefore reach the founder's *eyes*
  (tagged, citable, auditable) but never his *order flow* as if it were his own rule.
- **Promotion is a founder tap:** the widget's Confirm action sets `authority='user_confirmed'`
  (PATCH, endpoint exists at `api/ai_routes.py:656-685`). Nothing else can raise authority (§4.2 judge rule).
- **Theses are advisory forever.** A thesis can justify *researching* or *proposing* — the human approval
  gate (and later the auto-execute guardrails) remain the enforcement layer, unchanged.
- Widget/API manual creates (`api/ai_routes.py:631-653`) get `source='widget_manual'`,
  `authority='user_authored'` — the founder typed it himself.

---

## 7. Trust surface — the founder-facing half

**Widget upgrades** (`frontend/components/workspace/widgets/memory-widget.tsx` — today: list + delete only):
1. **Provenance line on every row:** source badge (`you said` / `extracted` / `reflection` / `research`),
   date, and — when `quote` exists — the founder's verbatim words in a expandable sub-line:
   *"saved from: 'remember I never want to short anything'"*. Chat-sourced rows deep-link to the session
   via `metadata.session_id`/`source_message_id` (already stored, `memory_extract.py:134-136`).
2. **Pin** (star toggle → tier-A injection) and **Confirm** (visible only on `agent_inferred` rows; one tap
   → `user_confirmed`). Both are single PATCHes.
3. **Status filter:** Active (default) / History (superseded + archived + expired). Superseded rows show
   "→ replaced by [mem:ID]" links; the founder can watch his own rule evolution.
4. **New type chips** (directive/pref/fact/lesson/thesis) with directive visually loudest — these steer the
   agent every single turn and deserve the visual weight.
5. Delete stays hard-delete, founder-only, as today (`memory-widget.tsx:93-101`).

**Citations:** the copilot cites `[mem:ID]` when a memory shapes an answer (§5.4 instruction). The chat
renderer converts `[mem:ID]` to a chip; clicking opens that memory's provenance (content + quote + source +
age + authority). This is the audit loop closed: *claim → memory → the founder's own words that created it.*

**Learned chip + undo:** the existing post-turn "Learned: …" chip (via `POST /api/memory/extract`,
`api/ai_routes.py:695+`) stays, now judge-gated so it fires rarely — and gains an inline **Undo** that
archives the row in one tap. Saving a memory in front of the user with an instant undo is the single
cheapest trust behavior in the whole design.

---

## 8. Eval + kill criteria — how we know it's elite

**Golden set** (`tests/fixtures/memory_golden.json`, ~40 cases; assembled from the real junk that motivated
this — rows 148/149/164 verbatim — plus synthetics):
- 12 junk turns (questions, action requests, review asks, market chatter) → expect **zero** candidates.
- 10 legit statements (directives, prefs, facts, incl. "remember that ..." verbatim cases) → expected
  type + content-must-contain assertions (numbers verbatim).
- 8 judge cases: paraphrase of existing → NOOP; refinement → UPDATE; **"cap is now 25%" vs stored 20% →
  SUPERSEDE** (the §0.1 bug, immortalized as a regression test); reflection candidate targeting a
  user_authored directive → rejected.
- 6 echo cases: injected memory restated by assistant/user → NOOP (the 2026-06-20 loop, as a test).
- 15 retrieval queries → expected-hit@4, incl. paraphrase ("keep positions small" → sizing pref) and
  ticker-exact ("what's my SLB view") and a stale-thesis exclusion case.

**Harnesses:** (a) `tests/` with a mock LLM validate plumbing/actions deterministically; (b)
`tools/memory_eval.py` runs the same golden set against the **live** configured LLM + live Ollama and prints
per-case pass/fail — run before each phase flip (the "verify LLM features live, not just green tests"
lesson). Acceptance bars: junk precision **12/12** (zero junk stored — non-negotiable), extraction recall
≥ 8/10, judge action accuracy ≥ 90%, retrieval hit@4 ≥ 13/15, p95 added turn latency from
retrieval < 150ms.

**Kill criteria + rollback:** if in the first two weeks (i) any fabricated preference is found injected as
if user-authored, or (ii) >20% of newly stored memories get founder-deleted/undone, or (iii) injection
measurably degrades answers — flip `injectMemories` back to false (one default, §5.4) and/or disable the
extractor call in `finalize_chat_turn`; the store, widget, and `search_memories` keep working. No phase
below leaves memory worse than the day before it shipped.

---

## 9. Rollout — four phases, each independently shippable

*(Reshaped from the report's three: its Phase 1 mixed "stop writing junk" with "add a judge" — those have
different risk profiles and must ship separately. Its Phase 2/3 order — retrieval before lifecycle — survives
scrutiny and is kept, but lifecycle's SUPERSEDE moves early into the judge because the correction-swallowing
bug makes contradiction handling urgent, not "later".)*

**Phase 0 — stop the bleeding + migrate (one evening; zero new behavior).**
- Delete: regex fallback (`memory_extract.py:20-56,130-131`); research auto-log (`engine_v2.py:489-497`);
  scan auto-log (`setup_scan.py:1107-1116`); Jaccard dedup-on-write (`ai_tables.py:185-229,250-269` →
  exact-match NOOP only).
- Schema migration (§3) + FTS5 table + backfill (embeddings column stays NULL until Phase 2).
- **29-row disposition** (soft — status changes only, nothing destroyed; founder sign-off per §10):
  148, 149 → `archived` (regex junk); 150–166 → `archived` (artifacts live in `research_artifacts`;
  the widget's artifact deep-link (`memory-widget.tsx:50-55`) keeps working from History);
  143–147 → `archived` (portfolio snapshots; content preserved in `reflection_runs`);
  138–142 → collapse to two rows pending §10.1: "Cap any single name at 20% of portfolio" (`directive`)
  + "Risk no more than 5% per trade" (`preference`), others superseded-linked to the survivor.
- New MEMORY_TYPES enforced on write; frontend types/labels updated.
- **Verify:** run a research job + a scan → zero new memory rows; golden junk turns through the extractor →
  zero rows; widget shows the curated Active set; `pytest -q`, `ruff`, typecheck green.

**Phase 1 — judgment + deliberate memory (the trust core).**
- `ai/memories/judge.py` (§4.2) wired behind every writer; reflection/retrospective/journal-review candidates
  rerouted through it (§4.3).
- `save_memory` + `forget_memory` tools, registered on both surfaces (§4.4).
- Widget: provenance, quote display, pin, confirm, status filter (§7). Learned-chip undo.
- **Verify:** `tools/memory_eval.py` extraction+judge bars met live; say "remember that I never trade the
  first 15 minutes" in web chat → directive row with exact quote + `Saved [mem:NNN]` reply; repeat it
  reworded → NOOP; contradict it → SUPERSEDE visible in widget History.

**Phase 2 — retrieval that works + injection ON.**
- `ollama pull nomic-embed-text`; embed backfill for active rows; hybrid retriever (§5.2) replaces
  `retrieval.py` token overlap; `search_memories` upgraded (§4.4).
- mem0 removed (dependency + wrapper branches); `MemoryService` API unchanged for callers.
- Injection ON: markdown block (§5.4) replaces the JSON block (`context.py:26-37`); web default flipped
  (`python-server.ts:44`); mobile `POST /api/chat/stream` (when serv lands it) inherits via
  `build_turn_context`. `[mem:ID]` chip rendering in chat.
- **Verify:** retrieval golden bars met; kill Ollama → chat still answers (FTS5-only, log line proves the
  ladder); latency check p95 < 150ms; live: "what did I say about position sizing?" answers with a
  [mem:ID] citation whose chip opens the right provenance.

**Phase 3 — lifecycle + learn-loop integration.**
- Thesis `valid_until` sweep in the 24h reflection job (§3.3); STALE labeling in search.
- Research thesis *promotion* (one judge-gated candidate per completed run, §4.3) — the replacement for the
  deleted auto-log, shipped last and watched against the kill criteria (it is the closest descendant of the
  behavior that caused the SLB mess).
- Reflection prompt hardening (behavioral-generalizations-only).
- **Verify:** re-run SLB research twice → exactly one thesis row, second run = UPDATE not ADD; a thesis with
  a past `valid_until` disappears from injection and shows STALE in search; two-week kill-criteria review
  logged in BUILD.md.

Per repo discipline: judge/extractor code is not under `trading/`, but Phase 1+3 touch the learn loop that
feeds proposals — run the adversarial Sonnet review on the judge + authority rules before commit anyway.

---

## 10. Founder decisions required (the only genuine product calls)

1. **Are rows 138–142 real?** Their session ids (`sess_echo`, `sess_new`, `chat_abc`) look like test
   fixtures. Confirm "20% single-name cap" and "5% risk per trade" are your actual standing rules → they
   become `user_confirmed` directives (allowed to shape proposals). If not confirmed, they migrate as
   `agent_inferred` (advisory, tagged unconfirmed) until you tap Confirm.
2. **Migration sign-off** on the §9.0 disposition of the 29 rows (all reversible — archived, not deleted).
3. **May the copilot archive on chat instruction?** Design default: yes — "forget that" → `forget_memory`
   (archive-only, in-chat confirmation, founder can restore from History). Veto if you want forgetting to be
   widget-only.
