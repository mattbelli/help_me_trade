# CONSOLIDATION_S3_RECON.md
*Stage 3 recon — 2026-06-30. Read-only. No code was changed.*
*Companion: `CONSOLIDATION_PLAN.md` (Stage 3 spec), `CONSOLIDATION_RECON.md` (overall current state),
`CONSOLIDATION_S2_RECON.md` (Stage 2 store/persistence map).*

---

## 1. Per-page analysis

### 1a. Performance (`/performance`)

**What it shows (full page):**
- Account + period filter controls (account selector, period buttons Day/Week/Month/Quarter/Year/All/Custom, custom date range, P&L mode toggle Realized vs Cash flow)
- 4 stat cards: Realized P&L or Net cash flow, Win rate (stocks), Trades, Fees
- 2 charts: Cumulative P&L line chart, Calendar heatmap (day tiles, click-to-filter)
- 3 more charts: P&L by symbol bar chart (top 10), Trade mix pie chart (stocks vs options), Day-of-week bar chart
- Closed trades table (filtered by selected calendar day)
- Action buttons: "Sync brokers" (calls `syncTrades()`), "Run Trade Review" (`RetroReportButton`)

**Key implementation details:**
- Main component: `frontend/components/performance/performance-page.tsx` — 758 lines, single "use client" component
- Local state: 9+ `useState` hooks (accounts, period, customFrom/To, dashboard, pnlMode, selectedDate, loading, syncing, error)
- Data sources:
  - `fetchPerformanceDashboard()` → `GET /api/performance/dashboard` (period, account_id, from_date, to_date)
  - `fetchTradeAccounts()` → `GET /api/trades/accounts`
  - `syncTrades()` → broker sync action
  - `runTradeRetrospective()` → creates a research artifact, then `router.push('/research?id=...')`
- `RetroReportButton`: `frontend/components/performance/retro-report-button.tsx:27` — uses `useRouter()` to navigate to `/research?id=...`; this breaks in a pure widget context (no router navigation from within a tile)

**Widget coverage gaps:**
- Zero coverage. The `equity-curve` widget shows portfolio equity over time but not trade analytics. None of the 13 existing widgets cover P&L analytics, win rate, calendar, round-trips, or the period filter.

**Recommended componentization shape:**

Option A: **Full-page widget** — wrap `PerformancePage` (minus `PageHeader`) directly in a large widget shell. defaultSize `{ w: 24, h: 14, minW: 12, minH: 8 }`. Simplest path — zero logic change. The page already has its own filter controls so it's self-contained. The RetroReportButton navigation becomes a broken page-nav in a full-workspace world, but works today (until Stage 5).

Option B (recommended): **Launcher widget + analytics modal** — A `performance-widget.tsx` shows the 4 headline stat cards from the most recent load (last period P&L, win rate, trade count, fees) with a "Open analytics" button that opens the full `PerformancePage` in a large Dialog (`h-[min(88vh,820px)] w-[min(96vw,80rem)]` — the same size as SettingsDialog). The widget itself is compact, ~8 wide × 4 tall. The modal holds the complete analytics page component.

Recommendation: **start with Option A** (wrap as full widget, ship it, keep the page route alive), then promote to B as a polish step. Option A is ~2h; Option B adds the modal shell and stat-only widget state, ~4-6h total.

**Coupling concerns:**
- `RetroReportButton` calls `router.push('/research?id=...')` — this works as-is while the `/research` route exists, but in a fully-widgetized world it would need to open the research viewer differently (e.g., programmatically select the artifact in the research widget). Defer to Stage 5.
- The filter controls (period/account/custom dates) are deeply embedded in `PerformancePage`. For the launcher widget (Option B), the modal must manage its own filter state.

**Effort estimate:** Option A: 2h (thin wrapper + registry entry). Option B: 4-6h (stat-only widget state + modal shell).

---

### 1b. Research (`/research`)

**What it shows:**
- Two-column layout (`xl:grid-cols-[340px_1fr]`): left sidebar + right report viewer
- Left sidebar:
  - "New research" card: topic input, symbol input (optional), depth selector (quick/standard/deep), Run button, streaming phase indicator
  - "Recent reports" card: `ScrollArea h-[420px]`, scrollable artifact list with topic, depth badge, timestamp, symbol, delete button
- Right panel: large card (`min-h-[640px]`), report viewer with `h-[560px]` iframe (`src={researchHtmlUrl(id)}`), or markdown fallback, status strip, "Open full report" link

**Key implementation details:**
- Main component: `frontend/components/research/research-page.tsx` — 398 lines, `"use client"`
- Uses `useSearchParams()` at line 54 for `?id=` deep-link (auto-selects artifact on load). This deep-link is page-URL-specific and does not translate to a widget.
- Data sources:
  - `listResearchArtifacts()` → `GET /api/research`
  - `getResearchArtifact(id)` → `GET /api/research/{id}`
  - `streamResearch()` → `POST /api/research/stream` (SSE streaming, phase events)
  - `getResearchConfig()` → `GET /api/ai/research-config`
  - `deleteResearchArtifact(id)` → `DELETE /api/research/{id}`
  - `researchHtmlUrl(id)` → `${API_BASE}/api/research/${id}/html` (iframe src)
- `ResearchPage` is a single-file monolith: all form state, streaming state, artifact list, and viewer in one component

**Widget coverage gaps:**
- Zero coverage. No existing widget shows research artifacts or runs research.

**IDEA_BANK direction (§5.1):** Research should live as `/research NVDA` from the command line, not as a dedicated nav page. The widget surface aligns with this — research output on the canvas, trigger from the console.

**Recommended componentization shape:**

Option A: **Large widget** — full `ResearchPage` content (minus `PageHeader`) in one wide widget, defaultSize `{ w: 24, h: 12, minW: 12, minH: 8 }`. The 340px sidebar + iframe viewer would be cramped in most widget sizes. Replace `useSearchParams` with local `useState` for `selectedId`.

Option B (recommended): **Compact launcher widget + modal viewer** — `research-widget.tsx` shows: topic input, symbol, depth selector, Run button, streaming phase status, and last 3 artifact chips. Full artifact list and iframe viewer open in a Dialog on "View report" or after run completes. The widget is small (~10 wide × 6 tall). The modal is sized similarly to SettingsDialog (`w-[min(96vw,80rem)]`).

Option C: **Two widgets** — `research-input-widget` (form + artifact list, ~8 wide) + `research-viewer-widget` (iframe, ~16 wide), channel-linked by artifact ID. More complex but best canvas layout for power users.

Recommendation: **Option B** for Stage 3 (compact + modal), with Option C as a later enhancement. The existing `ResearchPage` component can be used directly in the modal — just strip `PageHeader` and wire `selectedId` through props rather than `useSearchParams`.

**Key coupling concern:**
- `RetroReportButton` in `PerformancePage` calls `router.push('/research?id=...')`. In the all-widget world, this would need to open the research modal/widget with the given artifact selected. Defer to Stage 5 — the `/research` route will remain until Stage 5.
- `useSearchParams()` at line 54 of research-page.tsx — must become local state when the component is used in a widget/modal. Wrap with a `selectedId: number | null` prop to control from outside.

**Effort estimate:** Option B: 3-4h (small launcher widget + strip-and-wrap the existing ResearchPage in a modal, prop-threading selectedId).

---

### 1c. Rules (`/rules`)

**What it shows:**
- `RulesSection` component with two cards:
  - "Active rules" card: list of rules (name, condition summary, armed toggle, delete button), "Add rule" button
  - "Recent triggers" card: `ScrollArea h-[320px]` log of rule events (rule_id, event_type, message, timestamp)
  - "Add rule from preset" Dialog: preset selector, optional threshold field, Create/Cancel

**Key implementation details:**
- `frontend/components/rules/rules-page.tsx` — 16 lines, wraps `RulesSection`
- `frontend/components/rules/rules-section.tsx` — 342 lines, `"use client"`, already has an embedding mode:
  - `showCards` prop (line 39): when `false`, renders without outer Card wrappers (used in `AutomationsPage`)
  - `onCountsChange` prop: callback to report enabled/total count to parent (used in `AutomationsPage`)
- Data sources:
  - `listRules()` → `GET /api/rules`
  - `listRuleEvents(40)` → `GET /api/rule-events?limit=40`
  - `createRule()` → `POST /api/rules`
  - `updateRule()` → `PATCH /api/rules/{id}`
  - `deleteRule()` → `DELETE /api/rules/{id}`

**Widget coverage gaps:**
- The `scans` widget (`frontend/components/workspace/widgets/scans-widget.tsx`) covers:
  - Rule list: name, condition summary, armed toggle, delete ✓
  - "Last fired" column per rule ✓
  - 15-second polling ✓
- The `scans` widget does NOT cover:
  - Rule **creation** — no "Add rule" button or dialog
  - The full **trigger event log** (ScrollArea of recent `RuleEvent` objects with timestamp + event_type + message)
- `RulesSection` is ALREADY designed for embedding (`showCards={false}` prop). The `AutomationsPage` uses it this way today.

**Recommended componentization shape:**

Option A (recommended): **New `rules-widget.tsx`** that renders `<RulesSection showCards={false} />` in the widget shell. Zero new logic — the entire RulesSection (armed rules + add-rule dialog + trigger log) comes for free. Add to registry as `"rules"` singleton, defaultSize `{ w: 12, h: 10, minW: 6, minH: 6 }`.

Option B: **Extend the `scans` widget** with rule creation (add button + preset dialog). Would duplicate the add-rule dialog logic from `RulesSection` into `ScansWidget`. More work and no benefit over Option A.

Recommendation: **Option A** — this is the lowest-effort high-coverage win in Stage 3. `RulesSection` was built for embedding.

**Coupling concerns:** Minimal. `RulesSection` is already self-contained with its own data fetching and the Dialog component. No page-router or URL dependencies.

**Effort estimate:** Low — 1-2h (new thin widget file + registry entry + defaultSize). `RulesSection` does all the heavy lifting.

---

## 2. Routes to redirect

### Already-covered routes (can redirect to `/workspace` in Stage 3)

| Route | Covering widget(s) | Completeness | Redirect approach |
|---|---|---|---|
| `/` (Overview) | account-summary, positions, open-orders, equity-curve | Complete — all content covered | `redirect('/workspace')` in `app/page.tsx` (3-line change) |
| `/memories` | memory widget | Complete — MemoriesWidget shows all memory content | `redirect('/workspace')` in `app/memories/page.tsx` |
| `/agents` | CopilotSidebar in workspace | Complete — `/agents` is just `<TradingChat />`, the same as the copilot sidebar | `redirect('/workspace')` in `app/agents/page.tsx` |

**Redirect implementation:** Next.js static redirect in `page.tsx`:
```ts
import { redirect } from "next/navigation";
export default function Page() { redirect("/workspace"); }
```
This is a server-side redirect (no JS needed). The sidebar nav entries can be removed or updated to point to `/workspace` in Stage 5.

### Partially-covered routes (keep for now, revisit in Stage 5)

| Route | Partial coverage | Remaining gap | Stage 3 action |
|---|---|---|---|
| `/charts` | chart widget (multi-instance) | `/charts` has its own separate `fetchChartLayout`/`saveChartLayout` grid state (not workspace state) | Keep route; deprecated in Stage 5 once multi-chart workspace is established |
| `/trading` | chart + watchlist widgets + price-alerts-widget (create/edit/delete) | None significant — price alert creation is fully in price-alerts-widget | Keep route for now; it also has the single-chart + ticker search UX |
| `/automations` | scans + reports + price-alerts + agent-proposals widgets | `SettingsStrategiesPanel` not yet a widget; no "RunningNow" widget | Keep route; consider strategies widget for Stage 4 (config surface) |
| `/performance` | NONE → adding widget in Stage 3 | RetroReportButton router coupling | Keep route as modal fallback through Stage 5 |
| `/research` | NONE → adding widget in Stage 3 | Deep-link `?id=` URL param | Keep route as fallback |
| `/rules` | Partial (scans) → full coverage with new rules widget | Trigger log coverage in scans widget is weaker | Keep route through Stage 5 |

---

## 3. Prior design / reuse

### IDEA_BANK direction (what the vision says)

**Research (§5.1):** "Lives as a command from the command line (`/research NVDA`). Not a dedicated nav page." — directly validates the launcher widget approach and console command integration. The research widget is essentially the visual companion to a `/research` console command.

**Rules (§5.2):** "Created via command from the command line (or by the agent in chat). Settings page to see all your rules, with activate/deactivate, rule profiles, and a text box to create rules in plain language." — the rules widget maps to the "settings page to see all your rules" surface. Profile switching is not built yet; the preset-dialog is a placeholder until the NL rule creation from §7.10 lands.

**Memories (§5.3):** Already covered by `memory` widget. Done.

**Performance** is not explicitly addressed in IDEA_BANK for widget form; it's implied as a "performance stats" widget in the catalog at §1.2: *"performance stats"* appears in the initial widget brainstorm list. The analytics depth suggests a launcher+modal pattern.

### Existing patterns and reuse opportunities

- **`RulesSection` embedding pattern** (`frontend/components/rules/rules-section.tsx:39`): `showCards={false}` prop already designed for embedding. Already used in `AutomationsPage`. The rules widget is trivially: `<RulesSection showCards={false} />`.

- **`PerformancePage` as a component**: The page is already structured as `export function PerformancePage()` (not a default export page.tsx). It's straightforward to drop into a Dialog or widget shell.

- **`ResearchPage` as a component**: Similarly `export function ResearchPage()`. Needs `selectedId` prop-threading to replace `useSearchParams`.

- **No PHASE_6 brief covers performance/research/rules as widgets.** PHASE_6_WIDGETS_BRIEF.md (Batch 3) listed the initial 5 widgets (open-orders, watchlist, equity-curve, chart, price-alerts). Performance, research, and rules were explicitly deferred as they require heavier componentization.

---

## 4. The modal/overlay primitive

**File:** `frontend/components/ui/dialog.tsx`

**Implementation:** NOT Radix UI — this app uses `@base-ui/react/dialog` (base-ui, line 4). The Dialog wrapper matches the Radix API (`DialogPrimitive.Root`, `.Trigger`, `.Portal`, `.Backdrop`, `.Popup`, `.Close`, `.Title`, `.Description`) but uses base-ui internally.

**`DialogContent` default shape (lines 42-81):**
- `fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2`
- Default max-w: `sm:max-w-sm` — small by default
- Animations: `data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95`, `data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95`
- `showCloseButton` prop (line 44): renders an `×` button, default `true`
- Override size via `className` prop

**Large dialog pattern (SettingsDialog, `frontend/components/settings/settings-dialog.tsx:187`):**
```tsx
<DialogContent className="flex h-[min(88vh,820px)] max-h-[88vh] w-[min(96vw,80rem)] flex-col gap-0 overflow-hidden bg-popover/95 p-0 backdrop-blur-md sm:!max-w-[min(96vw,80rem)]">
```
This is the exact pattern to reuse for the performance analytics modal and research viewer modal.

**Used in the workspace today:**
- `frontend/components/settings/settings-dialog.tsx` — main settings modal (7 tabs)
- `frontend/components/rules/rules-section.tsx:223` — "Add rule from preset" dialog (`sm:max-w-md`)
- `frontend/components/workspace/copilot-thread-history.tsx:218` — rename chat dialog (`sm:max-w-md`)

**Conclusion:** The dialog primitive is complete, working, and production-proven. A "launcher widget → full view modal" pattern needs zero new infrastructure — just `const [open, setOpen] = useState(false)` in the launcher widget + `<Dialog open={open} onOpenChange={setOpen}><DialogContent>...</DialogContent></Dialog>`.

---

## 5. Recommended build sequence

**Sequenced easiest/highest-value first:**

### Step 3a — Rules widget (effort: LOW ~1-2h)
**Why first:** `RulesSection` is already designed for embedding. This is a 30-line wrapper widget. Highest coverage-per-effort ratio in Stage 3.

What to build:
1. `frontend/components/workspace/widgets/rules-widget.tsx` — renders `<RulesSection showCards={false} />` inside the standard `h-full overflow-auto` widget container
2. Add to `WIDGET_REGISTRY` in `widget-registry.tsx`: id `"rules"`, singleton, defaultSize `{ w: 14, h: 10, minW: 6, minH: 6 }`
3. No backend changes. No routing changes. No state threading needed.

Note: The `scans` widget and the new `rules` widget will share the same API data (`listRules`, `listRuleEvents`). Both will poll independently. This is acceptable for Stage 3 — singleton enforcement means a user can't add both to the same page (but per-page singletons in Stage 2 mean a user could have rules on page 1 and scans on page 2). Consider whether `scans` and `rules` should become one widget or stay separate (flag for founder, not a blocker).

### Step 3b — Redirect already-covered routes (effort: LOW ~30min)
**Why second:** Zero-risk, no new components, immediately removes confusion.

What to build:
1. `app/page.tsx` — replace OverviewPage with `redirect('/workspace')` (server component, no "use client")
2. `app/memories/page.tsx` — replace MemoriesPage with `redirect('/workspace')`
3. `app/agents/page.tsx` — replace TradingChat with `redirect('/workspace')`
4. Optionally remove these nav entries from `sidebar-nav.tsx` NAV_ITEMS, or mark them as redirecting (Stage 5 cleanup)

### Step 3c — Research widget (effort: MEDIUM ~3-4h)
**Why third:** Concrete IDEA_BANK vision alignment (§5.1), good canvas use-case (run research without leaving the workspace).

What to build:
1. `frontend/components/workspace/widgets/research-widget.tsx`:
   - Topic input + symbol input + depth selector + Run button
   - Streaming phase indicator (while loading)
   - Recent 3 artifacts list (topic, depth badge, timestamp, click to view)
   - "View report" opens a Dialog with the full ResearchPage content
2. Modify `ResearchPage` to accept `initialSelectedId?: number` prop (replaces `useSearchParams`); the widget passes this through the Dialog
3. Or: Extract a `ResearchRunner` sub-component from `ResearchPage` for the widget form, and `ResearchViewer` for the modal
4. Add to registry: id `"research"`, singleton, defaultSize `{ w: 12, h: 8, minW: 6, minH: 5 }`
5. No backend changes. The `/research` route stays alive for deep-links.

**Genuine fork:** Should the research widget show the full viewer inline (making it a large widget like 20+ wide) or use a modal? The iframe `h-[560px]` needs space. Inline = better for focused research sessions; modal = better for canvas density. **Recommendation: modal for Stage 3, inline option as a later widget config flag.**

### Step 3d — Performance widget (effort: MEDIUM-HIGH ~4-6h)
**Why fourth (not first):** Higher effort, and performance data is only valuable if you have trade history imported. Rules and research are immediately useful for any user.

What to build:
1. **Stage 3d-i (Option A, ~2h):** `performance-widget.tsx` — full page widget wrapping `PerformancePage` (minus `PageHeader`). Large defaultSize `{ w: 24, h: 14, minW: 16, minH: 10 }`. Zero logic change, just wrap. Ship this first.
2. **Stage 3d-ii (Option B, ~3-4h additional):** Add a compact "performance stats" widget that shows the 4 headline stat cards using a lightweight stat-fetch hook (just the summary stats, not the full dashboard). Stats widget opens the full analytics Dialog on "Open analytics" button. This is a polish step — do it when Option A proves too large for practical use.
3. `RetroReportButton` navigation stays as-is (navigates to `/research` page) — acceptable through Stage 5.

### Step 3e — Automations gaps (effort: MEDIUM, defer to Stage 4)
- The `SettingsStrategiesPanel` is not yet a widget and is needed for the "Configure your AI" setup page (Stage 4). Defer to Stage 4.
- The `RunningNow` component (`automations/running-now.tsx`) could become a small status widget. Low priority.
- Price alert creation is fully covered by the existing `price-alerts-widget`.

---

## Key file references for Stage 3

| File | Role |
|---|---|
| `frontend/components/rules/rules-section.tsx` | RulesSection — ready for embedding (showCards=false) |
| `frontend/components/performance/performance-page.tsx` | PerformancePage — drop into widget/modal shell |
| `frontend/components/performance/retro-report-button.tsx` | Uses router.push('/research?id=...') — coupling to address in Stage 5 |
| `frontend/components/research/research-page.tsx` | ResearchPage — needs selectedId prop-thread, remove useSearchParams |
| `frontend/lib/research.ts` | Research API: listResearchArtifacts, streamResearch, researchHtmlUrl, etc. |
| `frontend/lib/performance-api.ts` | Performance API: fetchPerformanceDashboard, syncTrades, PerformanceDashboard type |
| `frontend/lib/workspace/widget-registry.tsx` | WIDGET_REGISTRY — add rules, research, performance entries here |
| `frontend/components/ui/dialog.tsx` | Dialog primitive (base-ui) — the launcher→modal overlay |
| `frontend/components/settings/settings-dialog.tsx:187` | Large dialog size pattern: `h-[min(88vh,820px)] w-[min(96vw,80rem)]` |
| `frontend/app/page.tsx` | Redirect target: replace OverviewPage with redirect('/workspace') |
| `frontend/app/memories/page.tsx` | Redirect target: replace MemoriesPage with redirect('/workspace') |
| `frontend/app/agents/page.tsx` | Redirect target: replace TradingChat with redirect('/workspace') |
| `frontend/components/workspace/widgets/scans-widget.tsx` | Existing rules view — overlaps with new rules widget (flag: merge or keep separate?) |
