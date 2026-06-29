# CONSOLIDATION_RECON.md
*Recon pass: 2026-06-29. Read-only. No code was changed.*

---

## 1. PHASE_6 Brief Status

Each brief in alphabetical/batch order, status check against the code.

| Brief | Proposes | Status |
|---|---|---|
| PHASE_6_BRIEF.md (Batch 1) | `/workspace` route, toolbar, bottom console, react-grid-layout, 2 live widgets, theme switcher | **IMPLEMENTED** — `/workspace/page.tsx`, `workspace-toolbar.tsx`, `workspace-grid.tsx`, `command-console.tsx`, themes in `globals.css` all exist |
| PHASE_6_CHANNELS_BRIEF.md (Batch 2) | Multi-instance widgets, color-channel symbol-linking (watchlist→chart), 24-col grid, per-instance WidgetInstanceConfig | **IMPLEMENTED** — `channels.ts`, `use-channel-store.ts`, `WorkspaceLayoutItem.type/config`, `multiInstance`/`channelAware` flags on chart+watchlist, 24-col `DEFAULT_WORKSPACE_LAYOUT` |
| PHASE_6_WIDGETS_BRIEF.md (Batch 3) | Widget registry + `/add` picker + open-orders, watchlist, equity-curve, chart, price-alerts widgets | **IMPLEMENTED** — `widget-registry.tsx` has 12 widgets; `workspace-widget-picker.tsx` exists; all listed widgets built |
| PHASE_6_CONSOLE_BRIEF.md (Batch 4) | Terminal-style command line (blinking cursor, `/` commands, scrollback) + chat widget on canvas | **PARTIALLY** — terminal command line exists; chat-widget was created then **removed** per Batch 7. Console is commands-only now |
| PHASE_6_WATCHLIST2_BRIEF.md (Batch 5) | Dense watchlist, smooth motion reorder, channel link visible, list selector top, blank add row | **IMPLEMENTED** — `watchlist-widget.tsx` uses motion Reorder, WatchlistListSelector in header, channel-selector present |
| PHASE_6_TITLEBAR_BRIEF.md (Batch 6) | Move workspace cluster (Workspace label, layout chip, lock toggle, + button) to toolbar center; lock toggle; confirm-before-close; console expand-on-type only | **PARTIALLY** — confirm-before-close (`WidgetRemoveButton`) and `locked`/`toggleLock` in store **are implemented**; the workspace cluster (label, default-layout chip, lock, + button) in the center toolbar zone is **NOT in workspace-toolbar.tsx** (center has portfolio stats instead); console expand-on-type is likely implemented |
| PHASE_6_COPILOT_SIDEBAR_BRIEF.md (Batch 7) | Copilot as right sidebar (rail collapsed / 360px expanded), squishes grid, commands-only bottom bar, remove chat widget | **IMPLEMENTED** — `copilot-sidebar.tsx`, `use-copilot-store.ts` (open/pinned/width), `WorkspaceChatProviders` wraps both grid+console, chat-widget removed from registry |
| PHASE_6_GRID_POLISH_BRIEF.md (Batch 8) | Channel bug fix (draggableCancel), min-width clamp, themed drag placeholder, SW resize handle, green overnight dot, copilot float/pin | **IMPLEMENTED** — `draggableCancel=".workspace-no-drag"` present, `resizeHandles=["se","sw","ne","nw"]`, copilot has `pinned` flag + overlay/docked modes |
| PHASE_6_GRID_MODEL_BRIEF.md (Batch 9) | Resolution-independent cols (COL_TARGET_PX fixed width), free-flowing placement (compactType=null), fix SW handle CSS, even margins | **IMPLEMENTED** — `grid-metrics.ts` has `GRID_COL_TARGET_PX`, `colsForLayout()`, `compactType={null}`, `preventCollision={true}` in ReactGridLayout; dot-grid background visible when unlocked |
| PHASE_6_COPILOT_DOCK_BRIEF.md (Batch 11) | Move copilot from sidebar to bottom-right dock, expand upward, pin toggle, adjustable width, watchlist add-row dark input | **IMPLEMENTED** — copilot stored with `open`/`pinned`/`width`, workspace page applies `paddingRight: dockedPadding` when open, watchlist has dark add-row |

**Brief→Vision alignment:**
- CHANNELS (Batch 2) = symbol-linking channels, **not** multi-page workspace tabs (name collision — see §6)
- GRID_MODEL + WIDGETS = the widget canvas system the founder wants
- TITLEBAR = partially toward the toolbar consolidation goal (lock/+ cluster still missing from toolbar center)
- COPILOT_SIDEBAR + COPILOT_DOCK = the AI surface existing as a persistent panel

---

## 2. Workspace Save + Multi-Page Capability

**Can a layout be SAVED today?** YES — dual-write: immediate localStorage (`hmt-workspace-layout-v2`) + debounced server `PUT /api/workspace/layout` (500ms debounce). On load: server is authoritative; localStorage is the offline cache fallback.
- Server persistence: `db/workspace.py` — SQLite `workspace_state` table, key=`'layout'`, value=JSON array. Single-key upsert. Last-write-wins.
- API: `GET /api/workspace/layout` → `{ layout: [...] }`, `PUT /api/workspace/layout` → body `{ layout }`.
- Frontend: `use-workspace-store.ts` `hydrate()` → tries server first, falls back to localStorage, then DEFAULT.

**Is there ANY multi-page / multi-layout / "channels-as-pages" concept?** NO. There is exactly **one** layout slot (`key = 'layout'` in the DB, `hmt-workspace-layout-v2` in localStorage). The store has one `layout: WorkspaceLayoutItem[]`. No concept of named pages, channel-tabs, or saved layout sets exists anywhere in the codebase today.

**How are widgets registered + added?**
- Registry: `frontend/lib/workspace/widget-registry.tsx` → `WIDGET_REGISTRY` (array of `WidgetDefinition`). Keyed by `id` via `WIDGET_MAP`. `getWidgetDefinition(id)`.
- Adding: `useWorkspaceStore().addWidget(type)` — looks up def by type; if `multiInstance`, generates instance id `${type}-N`; finds open placement via `findPlacement()`; seeds default `config`; appends to layout + persists.
- Picker: `WorkspaceWidgetPicker` opened via `pickerOpen` state. Triggered from `/add` command or `openPicker()` from toolbar (but the toolbar `+` button is currently missing — the picker can be opened via the command console `/add`).
- Singleton widgets: blocked from adding a second instance if one exists (type-level uniqueness).
- `multiInstance: true`: chart, watchlist (unlimited copies allowed).
- `channelAware: true`: chart, watchlist, rvol-scanner.

**Full widget inventory (all 12 registered):**

| Widget id | Title | Multi? | Channel? | Description |
|---|---|---|---|---|
| `account-summary` | account summary | no | no | Portfolio, buying power, cash, unrealized P/L |
| `open-orders` | open orders | no | no | Working and recent orders |
| `positions` | positions | no | no | Open positions table |
| `equity-curve` | equity curve | no | no | Portfolio equity over time (recharts line) |
| `watchlist` | watchlist | **yes** | **yes** | Live quotes + sparkline, motion reorder, channel-linked |
| `chart` | chart | **yes** | **yes** | Candlestick/line chart (lightweight-charts), per-instance timeframe/style/studies |
| `price-alerts` | price alerts | no | no | Configured price alerts list |
| `reports` | reports | no | no | Morning levels + generated reports |
| `scans` | scans | no | no | Armed signal rules + recent fires |
| `memory` | memory | no | no | Long-term agent memories / lessons |
| `agent-proposals` | agent proposals | no | no | Pending AI order proposals (Approve / Reject) |
| `rvol-scanner` | Volume Scanner | no | **yes** | Market-wide unusual volume ranking (RVOL ≥ 1.5×) |

**Is there keyboard-shortcut / hotkey infra?** Minimal and ad-hoc — no central hotkey library. What exists:
- `command-console.tsx` line 173: `⌘K / Ctrl+K` toggles console; `Esc` closes; `/` opens with autocomplete.
- `copilot-sidebar.tsx` line 43: `Esc` closes copilot.
- No installed hotkey library (`useHotkeys`, `react-hotkeys-hook`, etc.). Hot-swap-by-keybind for workspace pages would need to be built.

**Grid model:** `react-grid-layout/legacy`, `cols` derived dynamically from container width via `colsForLayout()` (target ~56px per column, `MIN_COLS=12`, `MAX_COLS=48`). `compactType={null}` (free placement, no snap-together). `preventCollision={true}`. `rowHeight=48`. `margin=[12,12]`. `useCSSTransforms={false}`.

**What adding named savable pages requires structurally:**
1. Store: replace `layout: WorkspaceLayoutItem[]` with `pages: Record<pageId, WorkspaceLayoutItem[]>` + `activePageId: string`.
2. Backend: `workspace_state` table needs to store multiple keys (e.g. `key = 'layout:home'`, `layout:setup`) OR a separate `pages` table. Today it stores a single `'layout'` key — schema can extend without a migration breaking anything.
3. Toolbar: page-switcher UI (tabs or labeled buttons, cap ~5).
4. `addWidget`, `removeWidget`, `updateWidgetConfig`, `updateLayout` all need to scope to `activePageId`.
5. `DEFAULT_WORKSPACE_LAYOUT` becomes per-page defaults. Ship two named defaults: trading canvas + setup surface.

---

## 3. Old-Route → Widget Worklist

The sidebar nav (`sidebar-nav.tsx`) has 9 routes. The old AppShell (left sidebar + max-w-6xl centered content) is used for all routes EXCEPT `/workspace` (which is full-bleed).

| Route | What it shows | Widget coverage | Componentize effort |
|---|---|---|---|
| `/` (Overview) | Account cards, positions table, orders table, P&L equity chart | **Covered**: account-summary, positions, open-orders, equity-curve widgets all exist | Low — redirect `/` to `/workspace` once default layout is set |
| `/trading` | Single chart (TvLightChart), ticker search, price alert CREATE form, watchlist sidebar | **Partially**: chart + watchlist widgets exist. Price alert creation form is page-only | Medium — alert creation form needs a widget shell or modal |
| `/charts` | Multi-chart react-grid-layout grid (its own `fetchChartLayout`/`saveChartLayout`) | **Partially**: chart widget is multi-instance. But `/charts` has its own separate grid/layout state | Low-Medium — `/charts` page is essentially a primitive workspace; can be deprecated in favor of dropping multiple chart widgets onto the main grid |
| `/performance` | P&L analytics, trade calendar, win rate, charts, drawdown, account-filter | NOT a widget | Heavy/complex — lots of data, filter controls. Best as a full-page widget (large `defaultSize`) OR a multi-tab widget. Not trivially widgetizable |
| `/research` | Deep-dive research runner: topic input, depth/type select, streaming output, artifact list | NOT a widget | Medium — could be a large Research widget (input + output scroll). Feasible |
| `/memories` | Agent memories list (view/edit) | **Exists**: `memory` widget in registry | Low — memory widget already ships. Page redundant |
| `/agents` | Full copilot chat (`TradingChat`) | Handled by CopilotSidebar in `/workspace` | Low — this page is the legacy path; workspace users use the sidebar |
| `/rules` | Portfolio alert rules (list, armed/disarmed, trigger log) | NOT a widget — but `scans` widget covers signal rule fires | Medium — could become an "alert rules" widget or remain a settings-panel-style modal |
| `/automations` | Strategies panel, rules section, reports section, price alerts, running-now | **Partially**: scans, reports, price-alerts, agent-proposals widgets exist | Medium — the automations page aggregates 4–5 sub-sections; each is partially a widget already. "Automations overview" widget could be an entry point |

**Summary of widget gaps (page-only capabilities that need new widgets):**
- Performance analytics (complex, form-heavy — consider large performance widget or keep as a page popped from a widget)
- Research runner (medium — widgetizable)
- Rules editor/list (medium)
- Price alert CREATE form (small — could be a modal or add to price-alerts widget)
- Automations/Strategies overview (medium)

**Heavy/form-like pages (likely stay as modal or dedicated-tab surfaces rather than drag tiles):**
- Settings (Brokers, Execution/Risk, AI, Reports, Import) — modal dialog already, works fine
- Performance (too data-dense for a standard widget; consider a "fullscreen widget" or popup-panel pattern)

---

## 4. "Configure Your AI" Setup Surface

**What exists:**
- `SettingsDialog` (modal, accessible from toolbar bell+gear and sidebar bottom): 7 tabs — **Brokers** (Alpaca+Schwab API keys, connection), **Execution/Risk** (trading risk params), **Strategies** (strategy list, enable/disable, entry mode), **Reports** (scheduled reports), **AI** (`AiProviderSettings` — model provider, API keys, temperature), **Research** (SearxNG URL), **Import** (`SettingsTradesPanel` — trade import flow).
  - File: `frontend/components/settings/settings-dialog.tsx`
  - AI tab: `frontend/components/settings/ai-provider-settings.tsx` — lets user pick LLM provider, enter API key, configure model.
  - Import tab: `frontend/components/settings/settings-trades-panel.tsx` — trade import flow.
  - Brokers tab: Alpaca + Schwab credential entry.

**What does NOT exist (net-new for the "setup surface"):**
- "Tell it about yourself / goals" onboarding — no user profile, agent charter, or first-run flow exists anywhere. IDEA_BANK describes a `profile/<agent-name>.md` charter written with the user on first run. Nothing in the codebase implements this.
- Agent name / persona — no concept of naming the AI exists; the AI is called "Copilot" generically.
- "Goals / trading style" input — no free-text preference or goal-setting beyond the system prompt (which is hardcoded in `use-trading-chat-runtime.ts`).
- Memory settings view — the `memory` widget shows memories, but there's no settings panel to see timestamps, edit, or set the memory cap (as envisioned in IDEA_BANK §5).
- Account connection wizard / onboarding flow — Brokers tab exists but is inside a modal with no onboarding sequencing or completion state.
- Report/alert subscription setup — Reports tab exists in settings but there's no wizard or "subscribe to this report" UX.
- Notifications preferences (phone/Discord channel config) — not implemented.

**The "setup surface" vision requires:**
1. A dedicated **Setup workspace page** (the second named page/channel in the multi-page concept) containing: account connection status, AI configuration, user profile/goals entry, notification routing.
2. A **first-run detection** flow (check if `profile/<name>.md` exists or if a DB flag is set).
3. A **UserProfile** widget or setup panel that exposes the agent charter / goals.
4. Surfacing the existing SettingsDialog tabs as explicit setup steps or embedded panels (rather than buried in a modal).

---

## 5. Notifications Detail Gap

**Current state confirmed:**
- `notification-bell.tsx`: Bell icon in toolbar → Dropdown → scrollable list (max-h-80, truncated). Each row: dot (read/unread), **truncated title** (`truncate` CSS), **truncated body** (`notificationLabel(n)` with `truncate`), relative timestamp. Click = `markRead(n.id)`. **No expand, no detail view, no navigation on click.**
- `notification-banner.tsx`: Toast-style banners that slide in for NEW notifications, auto-dismiss after 6s. Shows title + truncated body. Dismiss X button. Max 3 simultaneous.
- `use-notification-store.ts`: Zustand store, `GET /api/notifications` polls on hydrate. `markRead`, `markAllRead` call `PATCH /api/notifications/{id}/read` and `POST /api/notifications/mark-all-read`.
- No notifications inbox widget exists. No full-body view anywhere.

**What a notifications-inbox widget needs:**
- Data: `GET /api/notifications` already returns the full payload (the store already has all notifications). `notificationLabel(n)` in `use-notification-store.ts` builds the display body from the notification fields. Full body is already in the data.
- The widget needs: list with full (non-truncated) body display, filter (all/unread), mark-read per row, mark-all-read, severity badge/color, timestamp. Optionally: a "detail" expand (for notifications that link to a symbol or alert).
- This is a straightforward new widget — the data plumbing is complete. The gap is purely UI.

---

## 6. Architecture Forks (for the plan)

Flag these — do not decide here.

### Fork A: "Channels" naming collision (HIGH)
PHASE_6 implemented color-coded **symbol-linking channels** (watchlist click → linked chart updates). `channels.ts`, `use-channel-store.ts`, `ChannelSelector` in widget headers — all built and working. The founder now wants **"workspace channels"** = named, savable workspace pages (like browser tabs). These are **completely different concepts** with the same name. The plan needs to:
- Rename one or both. Option 1: keep "channels" for symbol-linking (existing infra), call workspace pages something else ("layouts", "pages", "boards", "desks"). Option 2: rename symbol-linking to "link groups" or "sync groups", free up "channels" for workspace pages. Either requires a terminology decision.

### Fork B: Single-page → multi-page store migration (HIGH)
Today: `store.layout: WorkspaceLayoutItem[]` + one server key. Adding named pages is an additive change to the store and DB schema, but it must be done cleanly or the hydration logic gets messy. Key question: do old single-layout users automatically get their existing layout named as "Trading Canvas" page 1? (Yes — that's the safe migration path: on first load post-migration, wrap the existing layout into `pages.canvas` and create an empty `pages.setup`.)

### Fork C: Old routes — deprecate or keep? (MEDIUM)
The old SaaS sidebar + max-w-6xl shell still works for `/trading`, `/charts`, `/performance`, `/research`, `/memories`, `/rules`, `/automations`. These are parallel to the workspace widgets. Two options:
- Option A: Keep old routes as-is for now, redirect `/` to `/workspace`, and gradually widen widget coverage. Old routes stay available as "full-screen mode" for their capabilities.
- Option B: Remove old AppShell entirely, make every surface a widget or a modal. More disruptive, requires widgetizing performance + research + rules.

### Fork D: Setup surface — page vs widget vs modal (MEDIUM)
The "configure your AI" surface can be: (a) a second named workspace page ("Setup" board) with setup-oriented widgets, (b) a redesigned full-screen modal/wizard triggered on first run, (c) a hybrid (first-run wizard → then settings accessible from a Setup page). The current SettingsDialog is a modal — whether to promote it to a workspace page or replace it with a wizard is a product decision.

### Fork E: Hotkey infra for page hot-swap (LOW)
No hotkey library is installed. Adding `Alt+1` / `Alt+2` page-switching (or similar) requires either installing a library (`react-hotkeys-hook`) or continuing the ad-hoc `keydown` pattern. Low complexity but needs a consistent approach.

### Fork F: /charts page's separate grid vs workspace multi-instance charts (LOW)
The `/charts` page has its own `fetchChartLayout`/`saveChartLayout` (separate from workspace layout). Once multi-instance chart widgets are on the workspace canvas, `/charts` becomes redundant. Decision: keep `/charts` as a legacy power-user path, or deprecate it and document "add multiple chart widgets to your workspace"?

---

## Key File References

| File | Role |
|---|---|
| `frontend/lib/workspace/layout-persistence.ts` | `WorkspaceLayoutItem`, `DEFAULT_WORKSPACE_LAYOUT`, `LayoutPersistence` interface, localStorage impl |
| `frontend/lib/workspace/use-workspace-store.ts` | Zustand store — `layout`, `hydrate`, `addWidget`, `updateWidgetConfig`, `removeWidget`, `toggleLock`, `gridCols` |
| `frontend/lib/workspace/widget-registry.tsx` | `WIDGET_REGISTRY` (12 widgets), `WidgetDefinition`, `WidgetProps` |
| `frontend/lib/workspace/channels.ts` | Symbol-linking channel types and colors (6 color channels + none) |
| `frontend/lib/workspace/use-channel-store.ts` | Session-only channel store (watchlist→chart symbol linking) |
| `frontend/lib/workspace/use-copilot-store.ts` | Copilot sidebar state: `open`, `pinned`, `width` |
| `frontend/lib/workspace/use-notification-store.ts` | Notification store; `GET /api/notifications`, markRead |
| `frontend/components/workspace/workspace-grid.tsx` | ReactGridLayout host, `WidgetCard`, `WidgetHeader`, `ChartHeaderControls` |
| `frontend/components/workspace/workspace-toolbar.tsx` | Top toolbar: brand, broker chip, portfolio stats (PORT/BP/DAY/UPL), bell, settings, theme, clock |
| `frontend/components/workspace/notification-bell.tsx` | Bell dropdown — truncated list, no detail view |
| `frontend/components/workspace/notification-banner.tsx` | Toast banners for new notifications |
| `frontend/components/workspace/copilot-sidebar.tsx` | Right-side AI chat panel (floating or docked) |
| `frontend/components/workspace/command-console.tsx` | Bottom terminal bar (`⌘K`, `/` commands, scrollback) |
| `frontend/components/settings/settings-dialog.tsx` | 7-tab settings modal (Brokers, Execution, Strategies, Reports, AI, Research, Import) |
| `frontend/components/layout/app-shell.tsx` | AppShell: full-bleed for `/workspace`, sidebar+centered for all others |
| `frontend/components/layout/sidebar-nav.tsx` | Old SaaS sidebar nav (9 routes) |
| `frontend/app/workspace/page.tsx` | Workspace page — full-bleed, WorkspaceToolbar + WorkspaceGrid + CopilotSidebar + CommandConsole |
| `db/workspace.py` | Server-side layout persistence (SQLite `workspace_state` key-value table) |
| `api/main.py:2221` | `GET /api/workspace/layout` + `PUT /api/workspace/layout` endpoints |
