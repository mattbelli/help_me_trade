# Research Engine v2 — specialist fan-out, model-agnostic, effort-scaled

*Design locked with the founder 2026-07-03. The current deep-research pipeline produces
reports thinner than a 2-second chat answer; this replaces it. Headline Decision-Engine
value. Build in phases; adversarially review the synthesis/decision paths.*

## Why (measured weaknesses of v1 — `ai/research/`)
Single cheap model does every step; `web_search(limit=3)` hardcoded; `_CONTENT_LIMIT=1500`
char/source; analysis capped at `sources[-12:]`; final report = ONE `max_tokens=2000`
summary over 12k truncated chars; "depth" only changes iteration/query counts. No finance
specialization; our own Schwab fundamentals/technicals/news are not first-class inputs.

## Reference (founder's bar): Odysseus (`/home/mbelli/odysseus`, ex-Tongyi DeepResearch)
NOT multi-agent — a single-model iterative loop whose quality comes from: (1) explicit
LLM **plan** up front (sub-questions + success criteria); (2) parallel search rounds;
(3) ONE **rolling evolving report** re-synthesized each round; (4) LLM **self-stop**;
(5) big **≥1500-word cited final write** that auto-expands if thin; (6) effort = rounds +
time budget. Also: untrusted-web-content wrapped as `user`-role (prompt-injection guard).

## Design principle: SUPERSET of Odysseus
v2 ports ALL six Odysseus strengths, then adds finance specialists + a critic + our
proprietary data. Floor = Odysseus's loop; ceiling higher. Keep a single-model mode so the
two are A/B-comparable on the same question.

## Architecture — planner → parallel specialists → critic → editor
```
PLAN (planner role) — decompose into an angle-plan + success criteria + report format
  │
  ├─ Fundamentals analyst  ┐  run in PARALLEL (asyncio, bounded semaphore)
  ├─ Technicals analyst     │  each specialist:
  ├─ News / catalyst analyst│    • pulls ITS OWN targeted data (see Sources) + web rounds
  ├─ Bear-case / risk analyst    • runs a mini rolling-synthesis loop (Odysseus-style)
  └─ Macro / sector analyst ┘    • returns a distilled, cited section (dense, not raw snippets)
  │
CRITIC (critic role) — real adversarial gap/coverage red-team over the specialist sections;
  names missing angles/unverified claims → optionally triggers targeted follow-up rounds
  │
CHIEF EDITOR (editor role) — synthesizes the distilled sections (NOT raw snippets) into a
  long, cited, structured paper (exec summary → per-angle sections → risks → bottom line);
  auto-expand if under target length; inline [text](url) citations; calibration-aware
  (never fabricate win%/estimates — cite the calibration resource where relevant).
```
Fan-out reuses the `ThreadPoolExecutor`/asyncio pattern already in `jobs.py`. Specialists
are role-PROMPTS (distinct system messages + scoped tools/data), not separate processes.

## Model roles — user-configurable, AUTO-delegate default (founder: model-agnostic)
NO hardcoded vendor/model. New config `research.model_roles`:
`{ planner, specialists, extraction, critic, editor }` → each a model NAME ("" = default).
Resolution: role model → else `research.strong_model`/`fast_model` hints → else app default
(`get_llm(model=<name>)`, one active provider). **Auto-delegate** (default): strong→
planner/critic/editor, fast→specialists/extraction. With DeepSeek connected today that's
v4-pro (strong) + v4-flash (fast); reassign in Settings when other providers are added.
Anthropic Max/OAuth is NOT usable as the app LLM (ToS) — provider API keys only.
UI: Settings → Research gets a role→model table (dropdowns of the provider's models) + an
"Auto" toggle + optional strong/fast hints. Falls back safely to the single default model.

## Effort — named presets (founder choice)
`research.effort` preset bundles specialist count, rounds/specialist, per-source depth,
self-stop cap, synthesis target length:
| preset | specialists | rounds/spec | self-stop | notes |
|--------|-------------|-------------|-----------|-------|
| quick | 2 (fund+tech) | 1 | off | fast skim |
| standard | 4 | 2 | off | default |
| deep | 5 | 3 | off | full roster |
| exhaustive | 5 | self-stop ≤20 | on | critic-driven follow-ups |
Presets are the UI (dropdown on research start); raw knobs live behind an optional
advanced toggle later. Remove v1's fixed truncation caps — scale content/source limits with
effort instead of the `1500`/`-12`/`limit=3`/`12000` constants.

## Sources — make OUR data first-class (our edge over generic Odysseus)
Per specialist, targeted inputs via the perception layer (`ai/perception/`):
- Fundamentals: Schwab/AlphaVantage/Finnhub fundamentals + earnings (forward + past EPS).
- Technicals: real bars + indicators + levels (`research/levels.py`), RVOL.
- News/catalyst: `fetch_watchlist_news`/Finnhub + web; dated, deduped.
- Bear-case: adversarial web + our calibration resource (predicted-vs-realized).
- Macro/sector: term-spread/VIX/regime (`market` resource) + sector comps.
Web = SearXNG on :8585 (see [[searxng-port]]). Wrap fetched web text as untrusted
`user`-role content (Odysseus's prompt-injection guard).

## UI (separate track — founder ask)
- **Workspace viewer**: research must render IN the workspace (old `/research` page is
  pre-widget). A popup/modal Markdown viewer (the artifact `markdown`) + **Download** (.md).
  Reachable from the memory card, the completion notification, and a research widget.
- **Settings archive**: keep the list of past researches + per-report **download** in
  Settings → Research (as today), plus delete.
- Keep the themed HTML view too (already themed), but MD viewer + download is the ask.

## Phases (tasks tracked separately)
1. **Config + effort foundation** — `research.model_roles` + auto-delegate resolver;
   `research.effort` presets → a resolved `ResearchEffort` config; Settings UI. Low-risk.
2. **Specialist engine** — new orchestrator (planner→parallel specialists→critic→editor),
   model-agnostic per role, Odysseus-superset loop; replace v1 analyze/synthesize; remove
   truncation caps. The big one; adversarial review of synthesis + self-stop.
3. **Source enrichment** — wire per-specialist proprietary data via perception; untrusted-web
   guard.
4. **UI** — effort preset selector on start; workspace MD viewer + download widget; settings
   archive download.
5. **Follow-ups** (task #24) — `web_search_status()` health probe; chat-path notify fix.

## Quality gate
A/B the v2 paper vs the Odysseus single-model loop on the same ticker; v2 must read clearly
richer (more sourced facts, explicit risks, calibrated confidence) — else iterate before
shipping the preset as default. Never fabricate numbers (calibration rule).
