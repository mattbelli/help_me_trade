# COPILOT_TERMINAL_DESIGN.md — the terminal IS the copilot

*Design doc for the founder directive (2026-07-06, board card `copilot-terminal-redesign`):
"the cmd line is just not going to end up existing the way it is now… i love using claude code in
the terminal… could we make a claude code terminal like copilot. commands could work straight out
of the chat." Authored by the Fable design agent after full recon of the live code. No code was
modified — this doc is the contract for the build rounds.*

---

## Vision

One surface, one input, at the bottom of the cockpit: type `/` and it executes instantly; type
words and the agent answers in the same scrollback — streaming, with every tool call visible as it
runs. The grid above stays the supervision surface; the terminal below becomes the command surface.
It should feel like Claude Code moved into the cockpit: always ready, never modal, fast enough that
you never wonder if it heard you.

---

## 0. What exists today (recon map — what this design builds on / kills)

| Piece | Where | Fate |
|---|---|---|
| Command console strip (40px, bottom, always-on) | `frontend/components/workspace/command-console.tsx:244` | **Becomes the terminal's collapsed state** (keeps PageSwitcher/lock/+) |
| Floating scrollback popover above the strip | `command-console.tsx:247-279` | **Dies** — replaced by the drawer scrollback |
| "chat lives in the Copilot →" stub for plain text | `command-console.tsx:163-167` | **Dies** — plain text routes to the chat brain |
| 7 deterministic commands | `frontend/lib/workspace/use-workspace-commands.ts:36-44` | **Survives + grows** — becomes the single command registry |
| Copilot dock (floating panel / pinned right sidebar) | `frontend/components/workspace/copilot-sidebar.tsx` | **Dies at parity** (Phase T5) |
| Grid squish for pinned dock | `frontend/components/workspace/workspace-page.tsx:55,85-90` | **Dies** — terminal overlays, never reflows the grid |
| Copilot toggle in toolbar | `frontend/components/workspace/workspace-toolbar.tsx:536-552` | **Repointed** to summon the terminal |
| Chat runtime (assistant-ui + AI SDK over `/api/chat`) | `frontend/lib/use-trading-chat-runtime.ts` | **Reused unchanged** — the terminal is a new *renderer*, not a new brain |
| Chat route (tool loop, approvals, streaming) | `frontend/app/api/chat/route.ts` | **Reused** + speed fixes (§3) |
| Thread UI (bubbles) | `frontend/components/assistant-ui/thread.tsx` | **Reused via component overrides** (`ThreadComponents` slots, thread.tsx:72-84); bubble components survive for mobile |
| In-chat "slash commands" (prompt-template picker) | `frontend/components/agents/chat-slash-commands.tsx:40-54` | **Dies as UI**; its `CHAT_COMMANDS` data survives as *prompt macros* in the registry |
| Proposal cards (Approve/Reject inline) | `frontend/components/agents/propose-order-tool-ui.tsx:139-214` | **Reused as-is** in the terminal scrollback |
| Channel bus (focus a chart on a symbol) | `frontend/lib/workspace/use-publish-symbol.ts:9-22`, `use-channel-store.ts:24-31` | **Reused** — `/focus` + clickable tickers publish here |
| Thread persistence (Python sessions) | `frontend/lib/python-thread-adapter.tsx:101-170` | **Reused** + small perf fix (O(n) `fetch()`, §3) |
| Mobile Agent tab (bubble Thread) | `frontend/components/workspace/mobile-workspace.tsx:225` | **Untouched** — phone keeps its native chat + biometric approvals |
| Legacy `/agents` chat page | `frontend/app/agents/page.tsx` | **Dies at T5** (redirect to `/`) — aligns with `retire-old-ui` card |

History note: Batch 4 (`References/PHASE_6_CONSOLE_BRIEF.md`) originally specified exactly this —
one input, commands + chat in one scrollback. Batch 7 reversed it into "commands-only console +
separate copilot panel." The founder's directive re-reverses that call with a better home for the
conversation (the drawer, not a grid widget). The Batch-7 lesson that *does* stand: the chat is
never a grid widget.

---

## 1. Where the terminal lives — DECIDED: Quake drawer growing out of the existing strip

**Collapsed** = today's 40px strip, same footprint, same layout reservation
(`workspace-page.tsx:81`), same right-side controls (PageSwitcher, lock, + widget). The prompt is
always there, always one keystroke away. Nothing about the cockpit's resting state changes.

**Summoned** = the strip expands upward into a full-width drawer overlaying the grid:

- Default height **46svh**, drag-resizable via the top edge (**25–85svh**), persisted
  (localStorage, same pattern as `use-copilot-store.ts:70-76`).
- **Overlay, never reflow.** The grid does not squish (kills the `paddingRight` animation at
  `workspace-page.tsx:85-90` and its RGL-race bug class — see done card `copilot-grid-shift`).
  Widgets above the drawer line stay fully live.
- The drawer + strip component is **always mounted**; collapsed just clips it
  (`height: 40px; overflow: hidden`, `content-visibility: auto` on the transcript). Summoning is a
  height transition (≤120ms ease-out, transform/height only) — **no remount**, which is the dock's
  single worst latency sin today (`copilot-sidebar.tsx:102-120` unmounts the whole Thread when
  closed).
- Click-away does **not** collapse (unlike today's popover, `command-console.tsx:219-231` — that
  behavior dies). The terminal is a place you stay; Esc or ⌘K dismisses it.

**Why bottom drawer, not right rail or full page:** streaming prose wants width, charts want the
width more permanently — a right rail steals chart columns all day for a surface you use in bursts.
Full page breaks "supervise above, command below." Bottom is also where the input already lives;
zero muscle-memory cost. The chart no-overlay rule is untouched — the drawer is a horizontal band,
not floating buttons on a canvas.

**What happens to the two old surfaces:** the console strip *is* the terminal (collapsed state);
the copilot dock keeps working untouched through T1–T4 and is deleted at T5 (§6).

---

## 2. One input, two execution paths — DECIDED: `/` = deterministic, else chat; one registry, callable both ways

### Routing (exact rules)

1. Input starts with `/` → parse against the **command registry**. Known command → execute
   deterministically, echo `› /cmd` + output lines into the scrollback. Unknown `/foo` → error
   line with nearest-match suggestion (current behavior, `use-workspace-commands.ts:203-207`).
   Slash input **never** reaches the LLM.
2. Anything else → the chat brain: appended to the current assistant-ui thread via the existing
   runtime (`use-trading-chat-runtime.ts`), streamed into the same scrollback.
3. Registry entries have a `kind`:
   - `action` — today's deterministic commands + new ones (below). Run locally/via REST, <150ms.
   - `prompt` — the existing `CHAT_COMMANDS` templates (`lib/chat-commands.ts`: `/portfolio`,
     `/research`, `/buy`…). Selecting one inserts the canned prompt (+ enables its tools, as
     `chat-slash-commands.tsx:44-50` does today) and sends it to the LLM. Autocomplete labels these
     `→ agent` so the two kinds are visually distinct. This resolves the name collision between the
     two existing slash systems by merging them into one registry.

### Command set (registry v1 = today's 7 + these)

| Command | Effect | Plumbing (exists today) |
|---|---|---|
| `/focus SYM [1-6]` | Publish symbol to channel (default: terminal's channel chip, default Ch 1) | `useChannelStore.setChannelSymbol` (`use-channel-store.ts:24-31`) |
| `/alert SYM PRICE` | Create price alert | existing price-alerts REST API |
| `/watch SYM` / `/unwatch SYM` | Alias for `/watchlist add|remove` | `use-workspace-commands.ts:63-117` |
| `/scan [preset]` | Focus/spawn the scans widget on a preset — deep-link, don't dump | `useWorkspaceStore.focusWidget` (`use-workspace-store.ts:54`) |
| `/page N\|name` | Switch workspace page | `setActivePage` |
| `/new`, `/threads` | New chat thread / thread picker | `aui.threads()`, `CopilotThreadHistory` reused |
| `/model [name]`, `/tools` | Chat settings inline | `ChatSettingsProvider` |
| `/help` | Registry listing with kinds + keys | registry itself |

Autocomplete: the strip's popover (pattern exists, `command-console.tsx:84-113`) grows arg
completion — widgets from `WIDGET_REGISTRY` (already done for `/add`), symbols via the existing
symbol search, themes, pages. ↑↓ navigate, Tab completes, Enter runs. The assistant-ui
`Unstable_TriggerPopover` composer version dies with the dock.

### Commands straight out of the chat — the other direction

Register **one** frontend-executed tool, `workspace_command({ command: string })`:

- Declared in the route's tool set with **no `execute`** → AI SDK forwards the call to the client;
  the terminal executes it through the *same* `runCommand` registry and returns the output string
  as the tool result. One registry, both directions — the model can `/focus GS`, `/add chart`,
  `/alert NVDA 120` mid-answer, and the user sees it as a normal tool block in the scrollback.
- **Whitelist enforced twice:** the tool schema enumerates allowed verbs, and the client executor
  rejects anything not `kind: action`. The registry contains only UI/config/alert actions — no
  order path exists in it. `propose_order` remains its own server tool behind
  `needsApproval` (`lib/chat-tools.ts:134`) and the untouched chokepoint. Registered in both
  surfaces per the two-surface gotcha (`lib/chat-tools.ts` + `lib/chat-toolbar-config.ts`) noted in
  `References/PERCEPTION_LAYER.md`.

---

## 3. Speed, engineered — what's slow today, the fix, and the budget

### Found causes of "the copilot window is not it" (recon, file:line)

1. **Mount cliff.** Closed dock unmounts the entire Thread tree; every open remounts markdown +
   Shiki + tool UIs from scratch (`copilot-sidebar.tsx:102-120,194`). Runtime state survives
   (`workspace-page.tsx:82`), the DOM doesn't.
2. **Spring-gated interactivity.** Panel width + grid `paddingRight` animate on `motionStd`
   (spring 420/36, `copilot-sidebar.tsx:133-134`, `workspace-page.tsx:88`) — a few hundred ms of
   settle before the composer feels usable, plus a full grid reflow when pinned.
3. **Two sequential Python round-trips before the LLM call.** `fetchLlmRuntime` (route.ts:121)
   then `fetchChatContext` (route.ts:149) — serial, though independent. Only then does
   `streamText` open the provider stream.
4. **Thread-switch tax.** `python-thread-adapter.tsx:132-146` re-fetches the *entire session list*
   to resolve one thread; history load is O(n) messages with an encode/decode round-trip each
   (`:40-71`), synchronous on switch.
5. **380px default width** (`use-copilot-store.ts:11`) — streaming markdown in a phone-width
   column reads slow even when it isn't.

### Fixes

- **F1 — always-mounted transcript** (kills #1): drawer clips, never unmounts. Summon = one height
  transition ≤120ms; content painted from the first frame.
- **F2 — no springs on the critical path** (kills #2): summon uses a fixed ease-out; zero grid
  reflow (overlay). `motionStd` stays for decorative motion only.
- **F3 — parallelize + cache the route preamble** (kills #3):
  `Promise.all([fetchLlmRuntime(model), fetchChatContext(...)])`, and cache the llm-runtime
  response module-scope with a 5-min TTL (it's config, not data) — steady-state cost drops to one
  Python RTT for context assembly only.
- **F4 — instrument before optimizing further:** `Server-Timing` header from the route (runtime /
  context / convert / provider-TTFB spans) + a dev-visible `⏱ first token: 640ms` line in the
  terminal (debug flag). The `llm-eval-harness` board card wants tracing anyway; this is its
  first slice.
- **F5 — optimistic echo:** the `› you` line renders synchronously on Enter (local append), before
  any network. Slash commands that hit the network render a pending `…` line immediately and
  resolve in place. A "thinking" glyph appears within one frame of send.
- **F6 — thread-switch fix:** give `fetch(threadId)` a session-list cache (or dedicated endpoint);
  lazy-render history (newest page first).
- **F7 — prewarm:** on first input focus per session, ping a lightweight route that triggers the
  llm-runtime cache fill so the first real send pays only context + provider TTFB.

### Perceived-latency budget (verify gates for T3)

| Moment | Budget | Met by |
|---|---|---|
| Keystroke → glyph | < 16ms | local input state (already true) |
| ⌘K → drawer visibly opening w/ content | < 100ms | F1 + F2 |
| Enter → own line + thinking glyph in scrollback | < 16ms | F5 |
| Local slash command → output line | < 50ms | in-process registry |
| Networked slash command → pending line | < 16ms (resolve when done) | F5 |
| Send → first streamed token | p50 < 800ms, p95 < 1.5s | F3 + F7 (F4 proves it) |
| Tool call visible after model emits it | < 100ms | AI SDK part streaming (already true) |
| Layout shift during stream | 0 CLS | fixed-height collapsed tool rows, bottom-pinned autoscroll |

---

## 4. Scrollback as a first-class surface

**Flat mono transcript. No bubbles.** Implemented by overriding `UserMessage` / `AssistantMessage`
via the existing `ThreadComponents` slots (`thread.tsx:72-84`) — the assistant-ui runtime,
streaming, grouping, and approval mechanics are all reused; only the message skin changes. The
bubble components survive untouched for the mobile Agent tab.

```
› what's my biggest risk right now                          ← user: --fg-dim, prompt glyph
⚙ get_portfolio_snapshot · 320ms ✓                          ← tool block, collapsed, 1 line
⚙ get_context NVDA · 412ms ✓
  Concentration: NVDA is 31% of paper book [mem:140] …      ← streamed markdown, full width
```

- **Tool blocks** — the perceived-speed core: one collapsed line per call, `⚙ name arg-summary ·
  duration · status`, spinner while running, click/Space expands args/result (reuse
  `tool-fallback.tsx` collapsible + status/elapsed logic; restyle to a flat row; consecutive calls
  group via the existing `GroupedParts` accordion, `thread.tsx:419-442`). You watch it work —
  that's what makes Claude Code feel fast at identical wall-clock.
- **Proposal cards** — `ProposeOrderToolUI`/`ProposeRuleToolUI` render inline exactly as today
  (`propose-order-tool-ui.tsx:151-214`), Approve/Reject via the AI SDK approval round-trip.
  Web keeps in-chat approval (no biometrics on web — decided in
  `References/WEB_CHAT_DESIGN_BRIEF.md`); the chokepoint and phone flow are untouched.
- **`[mem:ID]` chips** — NOT yet implemented anywhere in the frontend (recon-confirmed); the
  terminal implements the contract from `References/MEMORY_OVERHAUL_DESIGN.md` §7: token → chip;
  click → provenance popover (source badge, date, verbatim quote, session deep-link); authority
  visually distinct (`agent_inferred` renders `(unconfirmed)` muted); superseded chains link
  "→ replaced by [mem:N]". The existing post-turn "Learned:" chips (`turn-learned-memories.tsx`)
  move into the transcript as a dim line with one-tap **Undo**. (Gated on memory-overhaul Phase 1
  turning standing injection on — the renderer ships ready, dormant until then.)
- **Tickers clickable everywhere** (durable rule `ticker-links-everywhere`): a markdown component
  override renders `$SYM` tokens and known symbols (watchlist ∪ positions ∪ recent context) as
  `ClickableSymbol` (`clickable-symbol.tsx:8-53`); activation publishes to the terminal's channel
  chip via `usePublishSymbol`. Command outputs get the same treatment.
- **Command lines interleave with chat.** Console entries carry an anchor
  `{threadId, afterMessageId}` set at execution time; the message-component override renders its
  anchored command lines beneath each message (pre-thread entries render at top). Command history
  persists in sessionStorage (last 200); chat history is already durable in Python sessions.
- **Session model:** the scrollback shows the active thread; `/threads` (or the header dropdown,
  reusing `CopilotThreadHistory`) switches; state survives page switches because the terminal
  mounts outside the grid (`workspace-page.tsx:95`) and never unmounts.

---

## 5. Coexistence with the grid

- **Grid = supervision, terminal = command.** The terminal never becomes a widget (Batch-7 lesson
  stands) and results that belong in widgets **deep-link instead of dumping**: `/scan momentum`
  prints the top 3 lines + focuses the scans widget (`focusWidget`, `use-workspace-store.ts:54`);
  the agent's answer about a symbol ends with the ticker clickable → chart focus via channel.
- **Keyboard model:**

| Key | Context | Action |
|---|---|---|
| ⌘K / Ctrl+K | anywhere | summon ⇄ dismiss terminal (existing binding, `command-console.tsx:196-201`) |
| ⌘J | anywhere | alias of ⌘K during migration (dock muscle memory, `workspace-page.tsx:43`); retired at T5+1 |
| `/` | non-typing target | focus input, begin command (existing, `command-console.tsx:203-212`) |
| Esc | streaming | **interrupt the run** (runtime cancel — same semantics as Claude Code) |
| Esc | idle, drawer open | collapse drawer, blur input |
| ↑ / ↓ | input empty | input history (commands + prompts, deduped, persisted) |
| ↑ / ↓ / Tab / Enter | autocomplete open | navigate / complete / run |
| Shift+PgUp / PgDn | drawer open | scrollback paging |
| Alt+1-5, Alt+[ ] | unchanged | page hotkeys (`use-page-hotkeys.ts`) — no collisions |

- **Focus rules:** summon focuses the input; dismiss returns focus to the workspace; a running
  stream continues when collapsed — the strip shows a subtle streaming glyph, and the drawer
  reopens to the live stream.

---

## 6. ASCII mockups

**Collapsed (resting state — today's strip, plus channel chip + activity glyph):**

```
┌─────────────────────────────────────────── grid (untouched) ───┐
│  [chart GS ▁▂▅▃▇]   [watchlist]   [positions]   [scans]        │
├─────────────────────────────────────────────────────────────────┤
│ › _                                    ·Ch1·  ⟐  pages ⌁ 🔒 [+] │  40px
└─────────────────────────────────────────────────────────────────┘
```

**Summoned, streaming, tool blocks + proposal (⌘K):**

```
┌──────────────────────── grid (top 54svh, still live) ───────────┐
│  [chart NVDA]                    [watchlist]                    │
╞══ ▲ drag ═══════════════════════════════════════════════════════╡
│ main thread ▾                                   Ch1 ▾ · claude ▾ │
│ ─────────────────────────────────────────────────────────────── │
│ › /focus NVDA                                                    │
│   → NVDA · Ch 1                                                  │
│ › should i trim it before earnings?                              │
│ ⚙ get_context NVDA · 412ms ✓                                     │
│ ⚙ get_earnings_dates NVDA · 96ms ✓                                │
│   NVDA reports in 6 days. Position is 31% of the paper book —    │
│   over your 20% cap [mem:140]. IV is rich; two ways to respect   │
│   the cap without exiting the thesis [mem:167 unconfirmed]: ▌    │
│                                                                   │
│ ┌ PROPOSAL · SELL 15 NVDA · limit 118.40 · paper ──────────────┐ │
│ │ trims to 19.8% of book · score 71                            │ │
│ │              [ Approve ]   [ Reject ]                        │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ─────────────────────────────────────────────────────────────── │
│ › _                                        esc interrupt · ⌘K ▾ │
└──────────────────────────────────────────────────────────────────┘
```

**Tool block expanded (click / Space):**

```
│ ⚙ get_context NVDA · 412ms ✓                                     │
│   ├ in  { "scope": "symbol", "symbol": "NVDA" }                  │
│   └ out price 121.02 · rvol 1.8 · earnings 2026-07-12 · …        │
```

---

## 7. Architecture — new, reused, killed

**New (all frontend; zero backend changes except route speed fixes):**

- `components/workspace/terminal/terminal.tsx` — drawer shell (strip + scrollback + input),
  replaces `command-console.tsx`'s popover half; absorbs its input/strip verbatim.
- `components/workspace/terminal/terminal-transcript.tsx` — Thread with overridden
  `UserMessage`/`AssistantMessage`/tool-row components + command-line interleave.
- `components/workspace/terminal/terminal-tool-row.tsx` — flat collapsed tool line (derived from
  `tool-fallback.tsx`).
- `components/workspace/terminal/mem-chip.tsx` — `[mem:ID]` chip + provenance popover (§4).
- `lib/workspace/use-terminal-store.ts` — open/height/input-history (succeeds
  `use-console-store.ts` + the open/width halves of `use-copilot-store.ts`).
- `lib/workspace/command-registry.ts` — unified registry (`action` + `prompt`), consumed by the
  input, the autocomplete, `/help`, and the `workspace_command` client tool.

**Reused unchanged:** runtime + transport (`use-trading-chat-runtime.ts`), route tool loop +
approvals (`route.ts`), proposal/rule tool UIs, `markdown-text.tsx` (deferred streaming parse),
`CopilotThreadHistory`, `ClickableSymbol`, channel bus, toasts.

**One brain, honored:** the terminal is a *renderer* on the existing web runtime → `/api/chat`.
Mobile keeps its Python SSE renderer (`References/MOBILE_CHAT_BRIEF.md`). When the roadmap's
one-Python-brain rewrite lands, the transport under the runtime swaps and the terminal doesn't
change — nothing in this design forks the loop.

**Killed at T5:** `copilot-sidebar.tsx`, dock button + toolbar repoint, grid `paddingRight`
squish, console popover + chat stub, `ChatSlashCommands` composer popover, `/agents` page
(redirect), `use-copilot-store.ts` (folded into terminal store).

---

## 8. Build plan (each phase independently shippable; dock keeps working until T5)

**T0 — Instrument (S, ½ round).** `Server-Timing` spans in `route.ts` + debug first-token line.
*Verify:* send 5 messages, read spans; record baseline p50 (this number gates T3's claims).

**T1 — Terminal shell (M, 1 round).** Drawer expand/resize/persist from the strip; unified
scrollback renders command lines; input history (↑); Esc/⌘K semantics; autocomplete moves into the
drawer. Commands only — plain text still prints the copilot stub. Dock untouched.
*Verify:* ⌘K < 100ms summon (devtools perf trace); `/theme`, `/add chart`, `/watchlist add GS`
round-trip in scrollback; history recall across reload.

**T2 — Chat in the terminal (L, 1–2 rounds).** Transcript component over the shared runtime:
flat message skins, streaming markdown, flat tool rows + grouping, proposal cards inline, ticker
links, thread switcher, command/chat interleave. Plain text now routes to chat. Dock still works —
both render the same runtime, so threads stay in sync by construction.
*Verify:* same thread visible in dock and terminal simultaneously; propose→approve in terminal
executes paper order via chokepoint (fire `tools/fire_test_entry.py` path untouched); Esc
interrupts a stream; zero CLS during stream (perf trace).

**T3 — Speed pass (M, 1 round).** F3 parallel+cache preamble, F6 thread-switch fix, F7 prewarm.
*Verify:* first-token p50 < 800ms over 20 sends (compare T0 baseline); budget table §3 all green.

**T4 — Commands both ways (M, 1 round).** `command-registry.ts` consolidation (+`/focus`,
`/alert`, `/scan`, `/page`, `/new`, `/model`, `/help`), `workspace_command` client tool registered
in both tool surfaces.
*Verify:* `/focus GS 2` retunes Ch-2 widgets; ask the agent "put NVDA on chart 1 and alert me at
120" → two visible `workspace_command` tool rows + alert exists; whitelist test: model cannot
invoke a non-registry verb.

**T5 — Parity audit + demolition (S–M, 1 round).** Checklist: thread history, model/tools
switching, attachments (or explicit cut), learned-memory chips, mobile untouched. Then delete the
dock + stub + `/agents`, repoint ⌘J + toolbar button, slim stores. Update BUILD/HANDOFF/board.
*Verify:* `npm run typecheck`; grep zero references to `copilot-sidebar`; mobile Agent tab
unchanged in browser; deep-links that targeted `/agents` land on `/`.

`[mem:ID]` chips ship in T2 as renderer-ready (feature-flagged) and light up when memory-overhaul
Phase 1 enables standing injection — no dependency inversion.

---

## 9. Founder decisions required

1. **Does the dock survive as an option?** Recommendation: **no** — one home, delete at T5. (A
   "pop out to floating window" can come back later cheaply via the `theme-editor-floating`
   pattern if you miss it.)
2. **Agent-initiated output.** When background work lands (alerts, finished research): does the
   terminal auto-summon, or does the strip just show an activity glyph + bell as today?
   Recommendation: **glyph only** — the cockpit should never move without your hand on it.
3. **Iosevka trial** (board card `iosevka-font-trial`): the terminal is the natural first surface
   for your wezterm font. Cheap to include in T1 behind `--font-mono`. Yes/no?
4. **Attachments in the terminal:** the dock's composer supports attachments; keep (terminal gets
   a paperclip glyph) or cut on web? Recommendation: keep, low cost (reuse dropzone).
