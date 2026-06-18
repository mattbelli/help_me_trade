# Help Me Trade — Idea Bank

A living capture of the founder's **product vision, design decisions, and the *why*** behind the build. **Most of this is committed direction — things we *will* build** — not a speculative wishlist. The open question for each piece is usually *when & in what order*, which is what **§9 Build sequencing** answers.

- **[`BUILD.md`](BUILD.md)** = the *how & when* — the sequenced execution plan / source of truth for what's built and next.
- **This file** = the *what & why* — vision, decisions, rationale, and the door-open seams to honor now. **§9** maps each piece to where it folds into BUILD.md.
- Treat this as the agent's long-term memory of the founder's intent. Append, don't overwrite. Date significant additions.

> The vision will evolve as the product develops — expected. Decisions captured here are current intent, not immutable.

---

## 0. North star — who this is for

**Build it for me first. That makes it the best product.**

The founder is a busy parent who:
- **Cannot and should not watch charts all day** — it's unhealthy and *leads to worse trading*.
- Wants to **define what they're looking for**, then step away.
- Wants an agent that **runs in the background, watches, and alerts when something matters**.
- Wants the agent to **learn their style over time** and become **semi-autonomous** — develop its own cadence, surface proposals, and ask for permission before acting.

The product is the bridge between a **proactive "Hermes" agent** and a **dashboard** — not a SaaS dashboard you babysit, and not a bare chatbot. The agent does the watching; the human makes the decisions.

---

## 1. Shell & UI vision

### 1.1 Bottom command console (keep — from the `hermes-console-draft.html`)
- Persistent **command line at the bottom**, expands upward, minimizes back to the dashboard.
- On expand, **dim or blur** the background. ✅ (decided)
- One input, two verbs: plain text → ask the agent; `/` → command. *(Refined: input bar = commands; conversational chat lives in the Copilot — §1.5.)*
- **Refinement (2026-06-18):** the **input bar spans the full bottom width**, but the **popup menu (autocomplete/scrollback) is only as wide as its content** — not a full-width panel. Open **only on typing**, not click/focus. Translucent, content-sized height (no fixed height / drag-resize).
- The **right side of the full-width bottom bar** can carry compact **status info** (connection, clock, agent state, etc.).

### 1.2 Customizable, widget-based dashboard ← **the real power**
- Ships with a **default dashboard template** so it's useful out of the box.
- But the point is **the user builds their own**: different traders rely on different things.
- `/` command → **select a widget** → it **populates into the empty space** above the console.
- Widgets are **draggable, movable, resizable/expandable**.
- Think: a canvas/grid of widgets the user composes, not a fixed layout.
- Layouts should be **saveable** (and likely config-driven — see §3).

**Widget catalog (initial brainstorm):** chart, watchlist, positions, day P&L / account summary, agent insights feed, order ticket, performance stats, news/RSS, research output, rule/alert status, scheduled tasks.

### 1.3 Top toolbar
- **Keep it.** Likes the overall feeling — broker/connection status, account summary, mode pill, clock.

### 1.4 Pop-out windows & multi-window
- "Sit down at my workstation, open full screen" — power-user mode.
- Ability to **pop things out into separate windows**: e.g. command to open a **second window as a watchlist**, a **small watchlist window**, or a **second window of charts**.
- Make pop-out **an option**, not forced — you can fit things on the dashboard *or* pop them out.
- Initial candidates to support pop-out: **chat, watchlist, chart**.
- ⚠️ **Architectural note:** real multi-window implies a **desktop shell (leaning Tauri — §7.1)** acting as a **client to the headless core (§7.6)**, not a browser tab. Phased: web first, desktop shell later.
- **Hierarchy (decided 2026-06-18):** **Workspace → Windows → Widget layout.** A *workspace* owns a **layout of windows**; each *window* has **its own configuration** (its own widget grid, theme, channels). Windows are **savable within a workspace**, so the whole multi-window arrangement reloads as a unit. Lands with the Tauri shell (Tier 3); until then the single browser window is "window 0" of the default workspace. Persist as nested config (workspace file → window configs → per-window layout JSON).

### 1.4a Direct-manipulation of widgets (decided 2026-06-18)
- **Alt+drag** a widget → **drag out a new instance** of it (duplicate-on-drag, for `multiInstance` widgets like charts/watchlists).
- **Shift+click** widgets → **multi-select**, then **move the selection as a group**.
- Optionally **create a named group** of widgets that move/manage together.
- These are workspace-grid interactions; the group concept may later map onto the window hierarchy (§1.4).

### 1.5 Chat placement → the Copilot (refined 2026-06-18)
- Chat is **not its own page**. Conversational chat lives in the **Copilot**, separate from the command bar.
- **Copilot anchored bottom-right.** Collapsed = a **compact bar just wide enough for the Copilot label + a text box**. On click it **expands upward along the right side of the screen**, taking a similar footprint to the old right sidebar.
- The **message/text window is only a portion** of the expanded panel and is a **dark, near-black area**.
- **Floats over the widgets by default** (doesn't reflow them); a **pin button** docks it permanently (then it squishes the grid, Zen-browser style).
- **Width should be adjustable.**
- (Earlier it could also pop out to its own window once the desktop shell exists — still optional.)

---

## 2. Agent vision (the heart of it)

### 2.1 First-run onboarding & agent identity ← **the founding moment**
- On **first load**, the program runs a short onboarding conversation:
  1. **"What should I call your agent?"** — the user names the agent.
  2. **"How do you want me to help you trade / invest?"** — the user describes intent.
- This produces an **opening session** — a `Sol.md`-style / "Hermes"-style **agent charter** that the user and agent co-write to get to know each other.
- The charter is **not static**: the agent is **molded as the user grows** and figures out what they want. The user usually has *some* idea at the start — onboarding gets them there faster.
- Both sides learn: the agent learns the user, the user learns the agent — and **this gets written straight into memory** as the **North Star** for that user.
- Think of it as a per-user `AGENTS.md` the human authors with help, that seeds long-term memory.

### 2.2 Proactive & background — **key aspect**
- Agent **runs in the background**, monitors, and **alerts when things happen**.
- Proactive **flags are a primary feature**, not a nice-to-have.
- Over time the agent **learns from the user** and develops **its own cycle** → **semi-autonomous**: it can prompt the user with proposals, and the user grants permission.

### 2.3 Chart-aware monitoring
- User can **tell the agent what they're watching**: "make levels in charts," define trends/patterns/setups.
- **Two input modes (decided): both** — **draw directly on a chart widget** *and* **describe in plain language**. Either should produce a watchable condition.
- Agent **monitors those conditions in the background** and alerts when triggered.
- This is the "I don't have to stare at charts" payoff.

### 2.4 Tasks / scheduled prompts ← **new pillar**
- A **Tasks** capability: **auto-running prompts to agents on a schedule** (e.g. 7:00 AM pre-market brief, 4:00 PM wrap-up).
- Also event-driven: **design a notification** — "when X happens, alert me."
- Tasks can be created in **settings** or **by the agent through chat**.

### 2.5 Autonomy & permission model (non-trade actions) ← **decided**
Trading is always gated (§7.5). For everything else, how much the agent does on its own:

| Action | Autonomy |
|--------|----------|
| **Memories** | **Autonomous** — agent creates/curates on its own (hard-capped, editable in settings — §5.3) |
| **Research** | **User-initiated** — only when the user asks, *or* when triggered by an assigned **task** |
| **Tasks** | **User-created** — the user defines scheduled/triggered tasks |
| **Rules** | **Agent proposes** (create / edit / remove) → user approves; **user can also create/edit directly** |

- The learning loop is the point: the agent **must be able to recommend adding, removing, or editing rules** as it learns the user — it should make the user better, not just react. Proposals keep the human in control while still letting the agent show insight.

### 2.6 External insight ingestion ("source scripts")
- Follow a **handful of specific people on X/Twitter** — *not* trade signals, but **insights** that inform decisions.
- Pull from **certain subreddits**.
- User defines this almost like a **script / declarative spec**: "watch @handleA, @handleB, r/subreddit → bring it down to me / let the agent factor it in."
- **Start without paid APIs.** ✅ (decided) Don't pay for X/Reddit APIs at the beginning.
- Instead, let the user **craft a search** — e.g. the way searching a Twitter handle on the open web surfaces recent posts, see whether an **LLM (with web search) can pull down what that user posted recently** and summarize it.
- Evaluate **how each option affects the program** (cost, reliability, freshness, ToS) before committing to anything paid. Paid official APIs are a *later* upgrade path, not the v1.

---

## 3. Theming & config-driven philosophy

### 3.1 Aesthetic direction
- Current palette (GitHub-dark) is **okay** but wants more of a **Warp / Ghostty** feel: **matte**, refined, with **some color to pop**. (answers prior Q4)

### 3.2 User themes via config file ← **requirement**
- Ship a **few standard themes**.
- Users must be able to **define their own themes in a config file**.
- Implies a clean **design-token system** (CSS variables) that a theme file maps onto — colors, maybe fonts/spacing.

### 3.3 Config-driven everywhere
- Themes → config.
- Dashboard layouts → saved/config.
- Source "scripts" (§2.4) → config/declarative.
- General principle: **the user can express preferences in files**, not just clicking UI.

---

## 4. Notifications — push everywhere

- Proactive flags get **pushed to phone and Discord** (more channels later). (answers prior Q3)
- **Customizable per channel** (what goes where).
- If the **desktop app is open**, optional **audible sound**.
- **In-app behavior (decided):** flags **pop out like an iPhone home-screen notification** — a small notification **widget that slides down from the top** (overlay, not docked into the console).
- The notification is **clickable** → clicking it **opens the command console** focused on that item. (This replaces the earlier "glow + chip" idea; details TBD — "we'll get there.")
- **Must be interactive, not just one-way.** The real benefit: get a flag/proposal on the phone when away from the computer and **Accept / Decline / reply** right there. This is *why* an always-on core matters (§7.6).
- **Channels:**
  - **Long term:** the **full mobile app** is the first-class interactive client (native Accept/Decline + chat) — see §7.6.
  - **Short term (before the app):** a **Telegram or Discord bot** gives interactive **inline Accept/Decline buttons + two-way replies** fast. Prefer these over one-way push (e.g. Pushover) precisely because of the two-way need. Button taps route back through the gateway to the brain.
- Setup work required later (push infra, bot integration, mobile app).

---

## 5. Research, Memories & Rules — placement & behavior

How the three "intelligence" surfaces show up to the user (answers prior Q "where do these live?").

### 5.1 Research → command
- Lives as a **command from the command line** (e.g. `/research NVDA`). Not a dedicated nav page.

### 5.2 Rules → command + settings management
- **Created via command** from the command line (or by the agent in chat).
- **Settings page** to **see all your rules**, with:
  - ability to **activate / deactivate** individual rules,
  - **rule profiles** — save a set of "which rules are active" and switch between them,
  - a **text box to create rules** in plain language.

### 5.3 Memories → pervasive auto-capture, with a hard cap
- Memories are **created all over the place**, from **all user inputs**: trades, orders placed/canceled/executed, **all chat and commands**.
- The **agent decides and prioritizes** what's worth saving — it identifies what's important rather than logging everything blindly.
- **Hard limit on the memory file size** ← **important design choice.** Like "Hermes," cap it. Avoid the OpenClaw-style failure where the memory file just **grows and grows until performance deteriorates**. Bounded, curated memory > unbounded log.
- **Settings page** to **view memories** — each entry with **when it happened**, and the ability to **edit** them.

---

## 6. Decisions captured (from prior Q&A)

| # | Question | Decision |
|---|----------|----------|
| 1 | Console expand behavior | **Dim or blur** the dashboard behind it |
| 2 | Kill chat page? | No — chat can **pop out to its own window**; make dashboard-vs-popout **optional** (also applies to watchlist, chart) |
| 3 | Proactive flag aggressiveness | **Key feature** — push to phone/Discord/everywhere (customizable) + **optional desktop sound**; auto-expand TBD |
| 4 | Aesthetic | **Warp / Ghostty matte** + pops of color; **themeable via config** |
| 5 | Agent name? | **User names the agent during first-run onboarding** (no fixed default; "Hermes" was just our working placeholder) — see §2.1 |
| 6 | Flag in-app behavior | **iPhone-style notification widget sliding down from the top**, clickable → opens the console (§4) |
| 7 | X/Reddit ingestion | **No paid APIs to start** — user crafts a search; LLM + web search pulls recent public posts (§2.5) |
| 8 | Research / Memories / Rules placement | Research = command; Rules = command + settings (profiles/activate/textbox); Memories = pervasive auto-capture, **hard-capped**, viewable/editable in settings (§5) |
| 9 | Desktop shell | **Leaning Tauri v2** — snappy/light, Windows WebView2 = Chromium; brain stays Python, Rust = thin glue; **web-first now, wrap later** (§7) |
| 10 | Config vs data persistence | **Files** for human-authored config (themes, layouts, charter, source scripts); **DB** for machine data (memories, trades, snapshots, rule state). Widget layout = **per-user JSON file** (§7) |
| 11 | Profiles | **Single self-learning profile now**; charter = on-disk `.md`; structured so multi-profile is possible later (§7) |
| 12 | Deployment model | **Headless core + authenticated gateway + thin clients** (desktop shell, mobile, web). Brain location (desktop / home server / VPS) = a config choice. **Tailscale** for private phone↔home-server access (§7.6) |
| 13 | Non-trade autonomy | Memory = **autonomous**; Research = **user/task-initiated**; Tasks = **user-created**; Rules = **agent proposes + user can edit directly** (§2.5) |
| 14 | Chart-level input | **Both** draw-on-chart and plain-language (§2.3) |
| 15 | Mobile role | **Full companion app** as a first-class gateway client (interactive Accept/Decline + chat); Telegram/Discord bot as the interim interactive channel (§4, §7.6) |
| 16 | Brokers | **Alpaca-first** (founder will fund Alpaca + test-run); keep **unified "All accounts"** view since architecture supports it; Schwab execution still out of scope |
| 17 | Alpaca live account | **Exists, unfunded.** Doesn't change the build — **paper stays default**; both keys in single store; live gated behind `trading_mode` + RiskEngine (§7.7) |
| 18 | Alpaca MCP server | **Adopt read-side only** — enable data/research toolsets as a maintained agent tool source; **never expose `trading`** (execution stays behind ExecutionService/RiskEngine). Scope via `ALPACA_TOOLSETS` (§7.7) |

---

## 7. Architecture leanings (not locked)

### 7.1 Desktop shell → leaning **Tauri v2**
- The shell is **just a window wrapper + OS integration** — trading logic always stays in **Python (FastAPI)**.
- **Why Tauri:** uses the OS's built-in webview (not a bundled Chromium), so tiny bundle + low RAM + fast cold start — matches the **"snappy, light, always-open background app"** priority, especially with multiple pop-out windows.
- On **Windows** (primary OS), Tauri's webview is **WebView2 = Chromium**, so rendering matches Chrome — no loss vs Electron there.
- **Rust worry addressed:** no trading logic in Rust. Tauri v2 plugins cover **multi-window, tray, OS notifications, global shortcuts, autostart, sound** → little/no Rust to write. Satisfies the Rust interest without complexity on the hard parts.
- **Electron tradeoff:** most mature, deepest ecosystem, and the founder has shipped it before (NHL sim) → faster *today*; but its heavy footprint conflicts with the #1 snappiness preference.
- ⚠️ **Wrinkle:** frontend is **Next.js with server routes** (chat `route.ts`) → not a static export. Packaging = desktop window → **localhost**, with **Next server + Python both bundled as sidecar processes**. Doable in both shells; slightly more setup in Tauri. Prototype when packaging.
- **Timing:** non-blocking. Browser ↔ localhost-FastAPI architecture is identical wrapped or not. **Build web-first; wrap in Tauri when ready to package.**

### 7.2 Persistence — files vs database
- **Per-user file** = human-readable JSON/MD on disk in the profile folder; easy to read, hand-edit, back up, share.
- **Database** = rows in the existing SQLite; best for high-volume, queryable, machine-generated data.
- **Split by author:**
  - **Files** → **themes, dashboard/widget layouts, agent charter, source scripts** (matches the config-file philosophy).
  - **Database** → **memories, trades, snapshots, rule state.**
- Widget layout = **per-user JSON file.** ✅

### 7.3 Where Rust fits (and where it doesn't)
- **Reality check:** at personal/single-user scale this app is **I/O-bound** (network + LLM + tiny SQLite), not CPU-bound. Rust's CPU-speed edge buys ~nothing you'd feel. "Snappy" comes from a light frontend + Tauri webview + non-blocking calls — **not** the brain's language.
- **Don't rewrite the brain in Rust.** Python async FastAPI is the right tool for an I/O orchestrator.
- **Rust shows up anyway, mostly for free:**
  - **Tauri shell core** — Rust by definition, but thin (plugins do window/tray/notifications/shortcuts/sidecar). ✅ unavoidable + minimal.
  - **Libraries** — Pydantic v2 core is Rust; **Polars** (Rust under the hood) gives fast dataframes/backtesting *while writing Python*. Use these instead of hand-rolling Rust.
- 🎯 **The one real future candidate:** a dedicated **real-time market-data / indicator / alert engine** — *if* background monitoring scales to **many symbols × streaming ticks × sub-second level/trend/indicator evaluation**. Rust would give low, predictable latency + tiny footprint for an all-day workload. Until then Python handles a handful of symbols fine.
  - **If/when built, [`apca`](https://github.com/d-e-s-o/apca)** (async Rust Alpaca crate — account/orders/data + websocket streaming) is the natural connectivity layer for that engine. ⚠️ **GPL-3.0** (copyleft — fine for personal use; a real constraint if the desktop app is *distributed*); last release Dec 2024, single maintainer — check liveliness first. Not adopted now (would force Rust↔Python bridging, contradicting "Python is the brain").
- **Rule of thumb:** Rust = the **shell and the libraries**, not the **brain**. Only add a hand-written Rust service when profiling proves a concrete bottleneck (most likely the monitoring engine at scale).

**"Should we build entirely in Rust?" → No (settled 2026-06-17).** Possible, but wrong for this product:
- **AI lives in Python.** This is an **AI copilot** — its core is LLM orchestration, **LangGraph** research, provider SDKs, embeddings, mem0-style memory. That ecosystem is Python-first and Rust-poor; an all-Rust build means reimplementing the most important, fastest-moving part from scratch, alone.
- **I/O-bound, not CPU-bound** → Rust's speed win is ~invisible for the brain (the "snappy" feel comes from Tauri + frontend, not the backend language).
- **Iteration speed** — Rust punishes churn (borrow checker, compiles); a solo, busy founder on an evolving design needs Python's fast change cycle.
- **Throws away working code**; `alpaca-py` (Apache-2.0) already does what's needed; **Schwab has no mature Rust SDK**; the frontend would lose Next.js + assistant-ui (free streaming chat).
- **Correct model = polyglot, each language where strongest:** **Python = brain** · **TS/React = clients** · **Rust = thin Tauri shell + optional future real-time engine.** All-Rust would break this for zero real gain. (All-Rust would only make sense for a latency-critical, low-AI HFT system — the opposite of this profile.)

### 7.4 Where the GPU fits
- GPUs win at **massively parallel math / neural nets** — not plumbing. Most of this app (polling, APIs, SQLite) is I/O-bound, so GPU is irrelevant there.
- 🎯 **The real win: run AI locally on the GPU via Ollama** (already supported in `ai/llm/factory.py`). Benefits map directly to founder goals:
  - **Privacy** — trade data/memories never leave the machine (consistent with the BUILD.md rejection of cloud data exfiltration).
  - **Cost** — no per-token fees for an **always-on background agent** (monitoring, scheduled 7AM/4PM tasks, continuous flags).
  - **Embeddings** — local embedding model on GPU for the (hard-capped) semantic memory retrieval — fast + private.
- **Tradeoff:** strong financial reasoning needs a capable GPU (VRAM); small local models may trail GPT/Claude on hard judgment. → **Hybrid pattern:** local for high-frequency/private/cheap work; cloud on-demand for heavy reasoning. Provider-agnostic factory makes the switch trivial.
- **Lower-priority GPU uses (only at scale):** GPU backtesting / Monte Carlo sweeps (RAPIDS/CuPy — start CPU+Polars); training ML signal/forecast models (speculative). UI/chart rendering already uses the GPU automatically.
- **Rule of thumb:** GPU = the **AI + heavy math**, not the plumbing.

### 7.5 Future-proofing: ML, backtesting, real-time bot (don't lock these out)
**Intent:** not shipping ML / backtesting / a real-time trading bot yet, but **must not architect ourselves out of them.** Keep *seams*, don't build the features. The cheap, do-now habits:

1. **One execution gate.** A real-time bot is **just another `OrderIntent` producer** — no special privileges; routes through `RiskEngine` + `ExecutionService` + audit log like any order (Phase 4). Hold this line = add a bot later with **no safety rearchitecture**. (Single most important seam.)
2. **Strategy logic = data-source-agnostic.** Strategy consumes an **event stream of bars/ticks** and emits signals, indifferent to whether events come from history (backtest) or a live feed (real-time). → **backtester and live bot become the same engine** with the feed swapped. Avoids weeks of rework.
3. **Abstract storage; plan a separate store for high-volume data.** SQLite stays for snapshots/ledger/memories. It is **not** for high-frequency tick/bar streams — keep `db/` access abstracted so a **columnar/time-series store (DuckDB + Parquet)** can be added for ticks/bars later without touching business logic. "Pull lots of data fast" is a storage-shape problem too.
4. **Preserve raw history + precision.** Keep granular data; land **`Decimal` on money** (Phase 4). ML training + honest backtests both need clean, complete, precise inputs.
5. **Real-time ingest/indicator engine = the Rust candidate (§7.3).** Low, predictable-latency data pull/processing feeding events to Python, which orchestrates strategy + the execution gate.

**HFT bot → standalone Rust program (if ever built).** Textbook case for isolation: its own Rust binary (connectivity via `apca`), running independently so a bug can't take down the copilot; plugs in as **another gateway client** (events in → orders out). Latency nuance: can't round-trip to the Python `RiskEngine` per order, so it carries **embedded Rust risk guardrails** mirroring policy + respects a **global kill-switch** + writes the **central audit log**; the Python RiskEngine governs its *config/limits*, not every tick. Paper-first, opt-in, isolated.

**Autonomy safeguards (decided in principle):** autonomy is **explicit opt-in** — a button, then a **second confirmation**, with a clear **risk acknowledgment** so the user understands what enabling it means. Paper-first regardless. *Still to detail:* once on, **confirm-every-order vs pre-authorized "envelopes"** (trade up to X shares of these symbols within these risk limits without asking). (See §8.)

### 7.6 Deployment: headless core + gateway + thin clients ← **the unifying architecture**
**The model (decided in principle).** Build the **brain as a headless service** (FastAPI + agent + scheduler + data) that holds all truth and does all work. Expose **one authenticated gateway** (REST + WebSocket/SSE + push). Every UI is a **thin client** of that gateway: the **Tauri desktop shell**, the **mobile app**, a browser. Clients render + call; they contain no logic. (This is the "Hermes Desktop" pattern the founder likes.)

**Why it resolves the always-on tension:** "run it on my desktop and leave it on" vs "run it on a home server/VPS and reach it from my phone" become **the same code deployed in a different location** — a *config choice*, not a rewrite. So:
- **Casual user** runs the core inside the desktop app; closing it = inactive. No server, no setup. Not alienated.
- **Power user** runs the core 24/7 on a **home server or VPS**; desktop + phone connect from anywhere; 7AM briefs and phone Accept/Decline work because the brain never sleeps.
- Ship **one architecture**, let the user choose where the brain lives.

**Reaching a home-server core from the phone — privately:**
- ✅ **Recommended: Tailscale** (or similar mesh VPN). Phone + desktop + home server on one private encrypted network; **no public ports, no third-party server holding data**; free for personal use. Best privacy + "reach it anywhere."
- **VPS** = simplest "always reachable," but trade data lives off-machine.
- Avoid exposing the raw core port to the open internet.

**Security must scale with exposure (important):** localhost is forgiving; the moment the gateway is reachable beyond localhost, the existing gaps matter — solid **auth tokens / device pairing, TLS, and the deferred secrets-at-rest encryption**. Design the gateway auth to assume remote clients from day one (don't bake in "localhost only").

**Already partly here:** today's FastAPI backend + Next frontend over localhost is the embryo of this. The evolution = formalize the backend as a deployable headless service with a real gateway + multi-client auth.

### 7.5a Storage tiers — SQLite vs DuckDB/Parquet vs Postgres
Two different axes people conflate: **transactional (OLTP, row-oriented) vs analytical (OLAP, column-oriented)**, and **embedded (file + library, zero ops) vs server (separate process)**.

| Tool | Type | Best job |
|------|------|----------|
| **SQLite** | embedded · OLTP | App's transactional state — ledger, snapshots, memories, rules, settings *(current)* |
| **Postgres** | **server** · OLTP | Same role as SQLite but for **many concurrent writers / remote / multi-user** |
| **DuckDB** | embedded · **OLAP** | **Analytics** — fast scans/aggregations over big datasets ("SQLite for analytics") |
| **Parquet** | **file format** (not a DB) | Compressed columnar storage of big tables (years of bars); DuckDB reads it directly |

**Decisions / recommendation (for a single-user, local, privacy-first, snappy desktop app):**
- ✅ **Keep SQLite** for transactional app state. Handles a solo user's data effectively forever.
- ❌ **Do NOT move to Postgres** — the "add Postgres" advice targets multi-user web SaaS; it means running a server + ops burden, which fights the embedded/snappy/local ethos. **Only reconsider if a hosted/multi-device/multi-user version ever happens** (not on roadmap).
- ✅ **Add DuckDB + Parquet *later*, for the analytical/time-series side only** — tick/bar history, backtesting, ML features. It's **embedded like SQLite** (no server) and can query SQLite + Parquet together, so the two coexist cleanly.
- **Net:** SQLite (now) and DuckDB/Parquet (later) are **complementary, not competing**; Postgres is a *replacement* path we're declining. Both chosen tools are embedded → no servers → stays self-contained.



### 7.7 Alpaca MCP server & live account
**Live account (no funds):** doesn't change the build. **Paper stays the default**; live creds just *exist*. Keep both keys in the single secret store, default paper, gate live behind `trading_mode` switch + live banner + RiskEngine (Phase 4). Wire for it now, don't activate it.

**Alpaca MCP server (official, v2):** FastMCP/OpenAPI, **60+ tools** — account, positions, portfolio history, stock/crypto/options market data (Greeks, IV), screeners, corporate actions, news, watchlists, *and* order placement. Toolsets are filterable via **`ALPACA_TOOLSETS`** (the key safety lever).

**Decision — adopt it, READ-SIDE ONLY:**
- ✅ **Harness read/data/research toolsets** (`account`, `stock-data`, `crypto-data`, `options`, `watchlists`, `corporate-actions`, `news`) as a **maintained MCP tool source for the agent** — 60+ tools we don't have to hand-build, kept current with Alpaca's API. Slots into the **headless-core + tools** model as a sidecar (§7.6).
- ❌ **Never expose the `trading`/order-placement toolset to the chat agent** — that bypasses RiskEngine, violating the single-order-path / safety principle. **Execution stays behind `ExecutionService` → `RiskEngine`** (Phase 4), which may call `alpaca-py` directly. Disable `trading` in `ALPACA_TOOLSETS`.

**Caveats:**
- **Preserve "ground before generate"** — primary portfolio/performance context still flows through *our* analytics layer; MCP tools are the agent's **extended reach** (research/discovery), not a replacement for the grounded snapshot.
- **Second key-holder** — runs locally with our keys via env (acceptable, not third-party exfiltration), but scope toolsets tightly + keep keys in the single store.
- **Alpaca-only** — layers *alongside* the `BrokerAdapter` abstraction (Schwab still exists); does not replace the adapter/ExecutionService shape.
- Also useful as a **spec/reference** for which tools are worth building ourselves, and as a **dev/ops** accelerator.

**Community Rust SDKs evaluated — not adopted (2026-06-17):** [`apca`](https://github.com/d-e-s-o/apca) (crate) + [`apcacli`](https://github.com/d-e-s-o/apcacli) (CLI). Reasons: (1) **Rust** → would force a Rust↔Python bridge, contradicting "Python is the brain" (§7.3); (2) we already have a working Python adapter + official `alpaca-py` (Apache-2.0); (3) **GPL-3.0** copyleft matters if the app is distributed. **`apca` is bookmarked** as connectivity for the *future Rust real-time engine* only (§7.3). `apcacli` = optional personal dev/ops tool (official MCP already covers CLI/agent access). **Right "don't reinvent" audit instead:** prefer `alpaca-py` methods over any hand-rolled `httpx` in `brokers/alpaca.py`.

### 7.9 Dev workflow — Claude Code as planner, Composer as coder (2026-06-18)
**Question:** can Claude Code (Anthropic Mac sub + VS Code ext) spawn Composer 2.5 subagents, or must that stay in Cursor?

**Findings:**
- **Composer 2.5 is Cursor-only.** Claude Code's own subagents run **Claude models** on the Anthropic sub — they cannot *natively* dispatch Composer.
- **But a real bridge exists: the Cursor SDK** (`@cursor/sdk` TS / `cursor-sdk` Python). It runs Cursor agents programmatically with `model: { id: "composer-2.5" }`, auth via `CURSOR_API_KEY`, `local: { cwd }` to run against this repo. So **Claude Code (the brain) can shell out to a tiny Cursor-SDK script to spawn a Composer 2.5 coder** — `Agent.prompt(brief, { model: { id: "composer-2.5" }, local: { cwd }})` for one-shots, `Agent.create` + `agent.send` for multi-turn. Needs a `CURSOR_API_KEY` (Cursor Dashboard → Integrations).
- **Three workable setups:** (1) **stay in Cursor** — Cursor agent plans + spawns Composer (cheapest, current); (2) **full Claude Code** — Claude plans + Claude subagents code (no Composer, uses the Claude sub); (3) **bridge** — Claude Code plans + invokes the Cursor SDK to dispatch Composer coders (best-of-both; one credential + a small wrapper script to maintain).
- **Decision (current):** keep the **Composer-subagent method** for coding; **investigate the SDK bridge** as the path to let the Claude plugin orchestrate Composer. The cloud-vs-local note: use **`local`** runtime so coders run against the working tree, not a cloned repo.

### 7.8 Profiles & charter
- **Single self-learning profile now** — one user, one agent charter that grows with them.
- Charter = a real **Markdown file** (e.g. `profile/<agent-name>.md`) the agent reads + updates; memory DB complements it.
- **Build for one profile but keep it in a profile folder** so multi-profile is possible later without rework.

---

## 8. Open questions / to revisit

- **Desktop shell — confirm at packaging time.** Validate the **Next-server-as-sidecar** approach in Tauri before committing.
- **Memory cap mechanics** — how big is the cap, and how does the agent decide what to evict/summarize when full?
- **X/Reddit via LLM web search** — how reliable/fresh is it in practice? Prototype before depending on it. Paid official APIs remain a later upgrade path.
- **Bot autonomy detail** (gating UX decided: opt-in + double-confirm + risk ack) — still to pick **confirm-every-order vs pre-authorized "envelopes"** (symbol/size/risk limits the bot may act within without asking). Paper-first regardless. (§7.5)
- **Gateway auth design** — token + device pairing + TLS scheme for remote clients; finalize the **secrets-at-rest encryption** (existing gap) before the core is reachable beyond localhost. (§7.6)
- **Where the core runs by default** — ship it embedded in the desktop app (casual) with home-server/VPS as an opt-in deployment? Confirm the default + provide a Tailscale setup guide. (§7.6)

---

## 9. Build sequencing — how & when it folds into BUILD.md

The *how & when* for the vision above. Tiers are roughly time-ordered, but **Tier 0 is honored continuously** (cheap principles, not features) so we never lock ourselves out. Read-only UX/agent work (Tiers 1–3) can all ship **before** the live-trading safety gate (Tier 4). This is a **living draft — reorder freely.** Maps to BUILD.md phases in brackets.

### Tier 0 — Door-open seams (honor NOW, in everything we build)
*Not features — abstractions/principles that keep the future cheap. Apply them whenever touching the relevant code.*
- **All order paths route through the (future) execution gate** — never call `broker.place_order` directly. [→ BUILD P4]
- **Treat the FastAPI backend as a headless core**; keep client⇄brain separation clean; **design auth assuming remote clients** (no localhost-only assumptions). [§7.6]
- **Keep storage behind the `db/` layer** so DuckDB/Parquet/TSDB can slot in later. [§7.5a]
- **Define a feed-agnostic strategy/signal boundary** the first time signals are touched (same code backtests + trades live). [§7.5]
- **Introduce a design-token/theme system** the first time the frontend is reworked, so config-file themes are possible. [§3]
- **Files-vs-DB split** (config in files, machine data in DB) + **preserve granular history**; plan `Decimal` at the money boundary. [§7.2, §7.5]

### Tier 1 — Near-term: the UX pivot (web-first, no desktop shell needed)
1. **Finish current roadmap** — Chat P3 (rehydrate learned chips) + P0 leftovers (async broker I/O, secrets encryption). [BUILD §6]
2. **Bottom command console v1** — port `hermes-console-draft.html` into the Next app; `/` commands + natural-language ask; existing pages reachable as console commands/views. [new BUILD P6]
3. **Theme system + config themes** — design tokens + a couple shipped themes + a user theme config file (Warp/Ghostty matte). [P6]
4. **Widget dashboard v1** — widget catalog, drag/move/resize grid, default template, **per-user JSON layout file**. [P6]
5. **First-run onboarding + agent charter** — name the agent, write the charter `.md`, seed memory. [P6]

### Tier 2 — Mid-term: the agent comes alive + becomes reachable
6. **Headless core + gateway formalization** — deployable service, real gateway auth (tokens/TLS/device pairing), **finish secrets-at-rest encryption** (graduates here — needed once reachable beyond localhost). [new BUILD P7]
7. **Proactive engine + Tasks scheduler** — background watch loop, scheduled prompts (7AM/4PM), event triggers. [P7]
8. **Notifications** — in-app top-down notification widget; **Telegram/Discord interactive Accept/Decline**; per-channel config; optional desktop sound. [P7]
9. **Chart-level monitoring** — draw-on-chart + plain-language → watchable conditions feeding the proactive engine/rules. [P7]
10. **Source scripts** — X handles / subreddits via crafted LLM web search (prototype reliability first). [P7]

### Tier 3 — Desktop shell
11. **Tauri desktop shell** wrapping the web UI as a **thin client of the core**; **multi-window pop-outs** (chat/watchlist/chart); OS notifications + sound + tray + autostart. [new BUILD P8]

### Tier 4 — Money & safety GATE (required before any live trading)
12. **Phase 4 as written** — `Decimal`, `RiskEngine`, `OrderIntent`, audit log, `ExecutionService`; light up Approve/Reject execution; paper/live `trading_mode` + banner. [BUILD P4]
13. **Autonomy opt-in** — double-confirm + risk acknowledgment; then confirm-each vs pre-authorized envelopes; paper-first. [§7.5]

### Tier 5 — Scale & ambition
14. **Mobile companion app** — full interactive gateway client. [§4, §7.6]
15. **Data/ML/bot** — DuckDB+Parquet, backtesting harness, ML signals, real-time ingest/indicator engine (the Rust candidate). [BUILD P5 + §7.3/§7.5]

### Anytime / opportunistic
- **Local GPU models via Ollama** — already supported in the provider factory; low effort, big privacy + cost win for an always-on agent. Enable whenever convenient. [§7.4]

---

## 10. Document history

| Date | Change |
|------|--------|
| 2026-06-17 | Created. Captured: widget-based customizable dashboard, pop-out/multi-window, Warp/Ghostty matte + config-file themes, background/semi-autonomous agent, scheduled Tasks, chart-level monitoring, X/Reddit "source scripts," push-everywhere notifications, and prior Q&A decisions. |
| 2026-06-17 | Resolved open questions: first-run onboarding & user-named agent charter (§2.1, North Star into memory); top-down iPhone-style clickable notification widget (§4); no paid APIs to start — crafted-search/LLM ingestion (§2.5); Research=command, Rules=command+settings profiles, Memories=pervasive auto-capture **hard-capped** + editable in settings (§5). |
| 2026-06-17 | Architecture leanings (§7): desktop shell → **Tauri v2** (snappy/light, brain stays Python, web-first/wrap-later); persistence split → **files** for config (themes, layouts, charter, source scripts) + **DB** for machine data; widget layout = per-user JSON file; **single self-learning profile** with on-disk charter `.md`, multi-profile later. |
| 2026-06-17 | Rust scope (§7.3): app is I/O-bound → **don't rewrite the brain in Rust**; Rust appears via Tauri shell (thin) + libraries (Pydantic, Polars); only real future candidate is a **real-time market-data/alert engine at scale**. |
| 2026-06-17 | GPU scope (§7.4): real win = **local AI via Ollama on GPU** (privacy + no API cost for always-on agent + local embeddings); hybrid local/cloud; GPU backtesting/ML only at scale. |
| 2026-06-17 | Future-proofing (§7.5): keep doors open for ML/backtesting/real-time bot via **seams not features** — one execution gate, feed-agnostic strategies, abstracted storage (DuckDB/Parquet later), preserve precise history; deferred decision = **bot autonomy policy** (confirm-each vs pre-authorized envelopes). |
| 2026-06-17 | Autonomy gating decided (§7.5): **opt-in button → second confirmation + risk acknowledgment**, paper-first. Storage tiers (§7.5a): **keep SQLite** (OLTP), **add DuckDB+Parquet later** (OLAP/ticks/backtest, embedded), **decline Postgres** unless multi-user/hosted. |
| 2026-06-17 | Deployment architecture (§7.6): **headless core + authenticated gateway + thin clients** (Hermes-style); brain location = config choice (desktop / home server / VPS); **Tailscale** for private phone↔home-server; gateway auth must assume remote clients. Non-trade autonomy matrix (§2.5); chart levels = both draw + words (§2.3); interactive mobile via full app + Telegram/Discord interim (§4); **Alpaca-first** + unified accounts (§6). |
| 2026-06-17 | Reframed IDEA_BANK as **committed vision (what & why)**, BUILD.md as **execution (how & when)**. Added **§9 Build sequencing** (Tier 0 door-open seams → Tier 5 ambition) and mirrored it into BUILD.md as **Phases 6 (shell pivot), 7 (proactive agent + gateway), 8 (Tauri shell)** + reordered §6. Pointers added in AGENTS.md/BUILD.md. |
| 2026-06-17 | UI draft v2 (`hermes-ui-v2.html`): matte Warp/Ghostty theme system (Carbon/Ember/Paper + config), widget-grid dashboard (add/drag/resize), top-down interactive notifications, onboarding charter. Decided **build-for-real next (branch + `/workspace`, not big-bang into main)**; HTML drafts become reference spec. Alpaca (§7.7): live account unfunded — paper stays default; **adopt Alpaca MCP server read-side only**, never expose `trading`. |
| 2026-06-17 | Community Rust SDKs (§7.7, §7.3): **not adopting** `apca`/`apcacli` now (Rust↔Python bridge contradicts "Python is the brain"; working Python adapter exists; GPL-3.0 matters if distributed). **`apca` bookmarked** for the future Rust real-time engine only. |
| 2026-06-17 | **Settled: NOT building entirely in Rust** (§7.3). AI ecosystem is Python-first (LangGraph/SDKs/embeddings); app is I/O-bound; Rust punishes fast iteration; would discard working code + Next.js/assistant-ui. Confirmed polyglot model: **Python brain · TS clients · Rust shell + optional future engine**. |
| 2026-06-18 | UI refinements: command bar **full-width, content-sized popup, status on the right** (§1.1); **Copilot → bottom-right, expands upward, dark near-black message area, floats-by-default + pin, adjustable width** (§1.5); **direct-manipulation** alt-drag-duplicate + shift-select group move + widget groups (§1.4a); **Workspace → Windows → layout hierarchy** with savable per-window configs (§1.4). Watchlist add-row to read as an obviously-typable dark input. |
| 2026-06-18 | Dev workflow (§7.9): **Composer 2.5 is Cursor-only**, but the **Cursor SDK** (`@cursor/sdk` / `cursor-sdk`, `model: composer-2.5`, `local` runtime, `CURSOR_API_KEY`) is a real **bridge** so **Claude Code can spawn Composer coders** via a small SDK script. Keeping the Composer-subagent method; bridge flagged for investigation. |
