# PERF_SPLIT_RECON.md — Performance Page Widget Split Recon
*Generated 2026-06-30. Read-only recon; no code was changed.*

---

## 1. PerformancePage section inventory

**File**: `frontend/components/performance/performance-page.tsx` (758 lines)
**Current widget**: `frontend/components/workspace/widgets/performance-widget.tsx` — wraps the entire page in `h-full overflow-auto px-1.5 py-1`.

The page renders inside a single monolithic component (`PerformancePage`) that owns ALL state. There is **no custom hook / context abstraction** — `fetchPerformanceDashboard()` is called inline, and all derived state (`cumulativeSeries`, `symbolBars`, `assetPie`, `dowBars`, `filteredRoundTrips`) is computed with `useMemo` inside the same component.

### 1a. Page-level style problems (the "wrong in a tile" issues)

| Problem | Location |
|---|---|
| Outer wrapper `div.mx-auto.max-w-6xl.px-6.py-8` | line 338 |
| `PageHeader` renders `<header className="mb-8">` with an `<h1>` + broker badges | line 343 |
| Responsive breakpoints (`sm:grid-cols-2 lg:grid-cols-4`, `lg:grid-cols-2`, `lg:grid-cols-3`) tied to viewport, not tile width | lines 469, 517, 585 |
| `Card` components use Shadcn `border-border/80` / `bg-card/50` — different token style from native workspace widgets that use `var(--border)` / `var(--bg-inset)` | throughout |

---

### 1b. Section-by-section map

#### Section A — Page header + action buttons
**Lines**: 339–362
**Shows**: `PageHeader` (h1 "Performance" + broker badge + subtitle), `RetroReportButton` ("Run Trade Review"), "Sync brokers" button.
**Sub-components**: `PageHeader` (`frontend/components/layout/page-header.tsx`), `RetroReportButton` (`frontend/components/performance/retro-report-button.tsx`)
**Data**: `RetroReportButton` → POST `/api/performance/retrospective` (takes `period` + `accountId` from parent state). Sync → POST `/api/performance/sync`.
**Self-contained?**: No — `RetroReportButton` reads `period` and `accountId` from the parent; can't live in a separate widget without prop plumbing or shared store.
**Tile styling**: `PageHeader` is a full-page header (h1 + large margin-bottom); entirely wrong for a tile. The actions (Sync + Retro) are the only widget-worthy parts.

---

#### Section B — Filters (account, period, custom dates, P&L mode)
**Lines**: 370–465
**Shows**: Account `<Select>`, period pill buttons (Day/Week/Month/Quarter/Year/All/Custom), custom date inputs, Realized vs Cash Flow toggle.
**Data**: `fetchTradeAccounts()` → `accounts[]` (separate API call: `GET /api/trades/accounts`). Filter state: `accountId`, `period`, `customFrom`, `customTo`, `pnlMode` — all drive the main `fetchPerformanceDashboard()` call.
**Self-contained?**: No — these filters parameterize every other section. They must either be centralized or duplicated per widget.
**Tile styling**: The filter bar is reasonable in size but is styled as a page-level row (`mb-6 flex flex-wrap items-end gap-3`). Needs to be compacted into a widget toolbar.

---

#### Section C — Summary KPI cards
**Lines**: 485–515
**Shows**: 4 `StatCard` tiles: Realized P&L / Net Cash Flow, Win Rate (stocks), Trade Count, Fees.
**Sub-component**: `StatCard` (local, lines 100–132) — self-contained display component.
**Data hook**: `dashboard.summary`, `dashboard.stock_win_rate`, `dashboard.realized_pnl`, `dashboard.cash_flow_pnl` — all from `fetchPerformanceDashboard()`. Also depends on `pnlMode` to choose which P&L number to show.
**Self-contained?**: YES, with own filter state or shared store. No cross-section dependencies.
**Tile styling**: Grid `sm:grid-cols-2 lg:grid-cols-4` is viewport-relative; needs `grid-cols-2` or `grid-cols-4` based on tile width not viewport. `StatCard` uses Shadcn Card tokens; minor divergence from workspace style but livable.

---

#### Section D — Cumulative P&L line chart
**Lines**: 517–558 (inside the `lg:grid-cols-2` grid at line 517)
**Shows**: Recharts `LineChart` of cumulative P&L over time (realized or cash-flow per `pnlMode`).
**Data**: `cumulativeSeries` (useMemo, lines 263–274) — running sum over `calendarDays` which is `dashboard.daily_realized` or `dashboard.daily_cash_flow` depending on `pnlMode`. All from `fetchPerformanceDashboard()`.
**Self-contained?**: YES, with own filter + pnlMode state. No cross-section state dependencies.
**Tile styling**: `ChartContainer` with `h-[220px] w-full` — the fixed 220px height is livable in a tile (use `h-full` instead). Chart is inside a `Card` with `CardHeader` + `CardTitle`.

---

#### Section E — Calendar heatmap
**Lines**: 559–583 (right column of the same `lg:grid-cols-2` grid)
**Shows**: `CalendarHeatmap` (local component, lines 134–196) — colored day tiles with click-to-filter.
**Data**: `calendarDays` (same derived state as cumulative chart). Clicking a day sets `selectedDate` which filters the round-trips table (Section G).
**Sub-component**: `CalendarHeatmap` (lines 134–196) — fully local, no imports, good extraction candidate.
**Self-contained?**: PARTIALLY — the calendar itself is self-contained, but `selectedDate` bridges to the round-trips table. If split, the calendar→table link is broken unless shared state is used.
**Tile styling**: `CalendarHeatmap` uses `flex-wrap gap-2` with `min-w-[4.75rem]` tiles — works in a tile, but "wraps" to fill available width naturally.
**Cross-section dependency**: `selectedDate` → `filteredRoundTrips` (Section G). This is the main coupling concern.

---

#### Section F — P&L by symbol bar chart + Trade mix pie chart
**Lines**: 585–668 (a `lg:grid-cols-3` grid: symbol bars span `col-span-2`, pie takes 1 col)
**Shows**: Horizontal Recharts `BarChart` of top-10 symbols by absolute P&L; `PieChart` of Stocks vs Options fills.
**Data**:
  - `symbolBars` (useMemo, lines 276–288) — computed from `dashboard.round_trips` (iterates round_trips, groups by symbol, sums P&L, top 10 by abs).
  - `assetPie` (useMemo, lines 290–302) — from `dashboard.asset_classes`.
  Both from `fetchPerformanceDashboard()`.
**Self-contained?**: YES (if they own their fetch). No cross-section dependencies.
**Tile styling**: `lg:col-span-2` / single column works only in a page context. Each chart has `h-[220px]` fixed height — use `h-full` in a tile.

---

#### Section G — Day of week bar chart
**Lines**: 669–691
**Shows**: Recharts `BarChart` of average P&L by Mon–Fri.
**Data**: `dowBars` (useMemo, lines 303–314) — from `dashboard.day_of_week_realized` or `dashboard.day_of_week` depending on `pnlMode`. From `fetchPerformanceDashboard()`.
**Self-contained?**: YES, no cross-section dependencies beyond `pnlMode`.
**Tile styling**: Full-width card with `h-[180px]` — works fine as a standalone tile. Use `h-full`.

---

#### Section H — Closed trades table (round trips)
**Lines**: 692–752
**Shows**: Table of `filteredRoundTrips` — Symbol, Open date, Close date, Qty, P&L. Filtered by `selectedDate` (Calendar click) or shows all.
**Data**: `filteredRoundTrips` (useMemo, lines 325–335) — from `dashboard.round_trips`, filtered by `selectedDate` + `pnlMode`.
**Self-contained?**: PARTIALLY — the table data comes from the same `fetchPerformanceDashboard()` call, but `filteredRoundTrips` depends on `selectedDate` from Section E. Without the calendar, it shows all round trips (a useful default).
**Tile styling**: `CardContent className="overflow-x-auto"` — the table is naturally scrollable and works in a tile. No hard size constraints.

---

## 2. Proposed widget split

### Widget 1: `perf-summary`
**What it wraps**: Section C (KPI stat cards) + Section A actions (Sync + RetroReportButton, NOT PageHeader) + compact filter bar (Section B).
**Data**: `fetchTradeAccounts()` + `fetchPerformanceDashboard()` — same single call.
**Self-contained**: YES. This widget owns `accountId`, `period`, `pnlMode` either locally or via shared store.
**Default size**: `w: 24, h: 4, minW: 12, minH: 3`
**Trim needed**: Remove `PageHeader`; compact the filter row into a tight toolbar (`flex items-center gap-2 px-3 py-2 border-b`); replace Shadcn `Card` shells with native workspace `div.rounded-md.border.bg-[var(--bg-inset)]` tiles matching `AccountSummaryWidget` style.
**Effort**: LOW — mostly trim + re-skin.

---

### Widget 2: `perf-pnl-chart`
**What it wraps**: Section D (cumulative line chart).
**Data**: `fetchPerformanceDashboard()` → `daily_realized` / `daily_cash_flow`.
**Self-contained**: YES.
**Default size**: `w: 16, h: 5, minW: 8, minH: 3`
**Trim needed**: Replace `h-[220px]` with `h-full`; remove `Card`/`CardHeader` wrapper (or keep as minimal widget chrome); add compact period + pnlMode selectors in a toolbar strip (or read from shared store).
**Effort**: LOW.

---

### Widget 3: `perf-calendar`
**What it wraps**: Section E (calendar heatmap) + Section H (round trips table inline below).
**Rationale for bundling**: The calendar→round-trip date filter is a single interactive flow. Bundling them preserves the UX without needing shared state across widgets.
**Data**: `fetchPerformanceDashboard()` → `daily_realized` / `daily_cash_flow` + `round_trips`.
**Self-contained**: YES if bundled.
**Default size**: `w: 16, h: 8, minW: 10, minH: 5`
**Trim needed**: Extract `CalendarHeatmap` (lines 134–196) into `frontend/components/performance/calendar-heatmap.tsx`; mount inside a `flex h-full flex-col` layout; top section = heatmap (scrollable), bottom = compact round-trips table.
**Effort**: MEDIUM — extraction + layout restructure, but no logic changes.
**Fork**: if round-trips table is wanted as its own widget (e.g. for a large data-dense layout), split them with shared `selectedDate` in a Zustand store. Flag for founder to decide.

---

### Widget 4: `perf-symbol-chart`
**What it wraps**: Section F (P&L by symbol horizontal bar chart). The pie chart can be bundled here (small) or broken out.
**Data**: `fetchPerformanceDashboard()` → `round_trips` (client-side aggregated) + `asset_classes`.
**Self-contained**: YES.
**Default size**: `w: 12, h: 5, minW: 6, minH: 3`
**Trim needed**: Replace `h-[220px]` with `h-full`; remove viewport-relative `col-span-2`. Consider toggling between "by symbol" and "stocks vs options" within one tile (avoids a tiny pie widget).
**Effort**: LOW.

---

### Widget 5: `perf-dow-chart`
**What it wraps**: Section G (day of week bar chart).
**Data**: `fetchPerformanceDashboard()` → `day_of_week` / `day_of_week_realized`.
**Self-contained**: YES.
**Default size**: `w: 10, h: 4, minW: 6, minH: 3`
**Trim needed**: Replace `h-[180px]` with `h-full`; remove wrapping `Card`; compact.
**Effort**: LOW.

---

### Summary table

| Widget | Sections | Data call | Effort | Notes |
|---|---|---|---|---|
| `perf-summary` | C + A + B | `fetchPerformanceDashboard` | LOW | Flagship tile; remove PageHeader |
| `perf-pnl-chart` | D | `fetchPerformanceDashboard` | LOW | Use `h-full` chart |
| `perf-calendar` | E + H | `fetchPerformanceDashboard` | MEDIUM | Bundle calendar+table to keep date-filter UX |
| `perf-symbol-chart` | F | `fetchPerformanceDashboard` | LOW | Bundle pie into chart with toggle |
| `perf-dow-chart` | G | `fetchPerformanceDashboard` | LOW | Small standalone tile |

**Total API endpoint**: All 5 widgets call `GET /api/performance/dashboard` — this creates up to 5 parallel fetches with the same params. Acceptable but worth noting; a shared data store (see §3) eliminates duplicates.

---

## 3. Shared-state concern

### The problem
All sections consume filters: `accountId`, `period`, `customFrom`, `customTo`, `pnlMode`. If each of 5 widgets owns its own filter state:
- User must set "Month / All accounts / Realized" five times.
- Widgets desync (one shows Month, another still shows Week).
- 5 parallel API requests with identical params if filters match.

The calendar→round-trips `selectedDate` link is an additional cross-widget interaction.

### Options

**Option A — Each widget owns its own filters**
- Simplest to implement. Each widget has a compact period + account picker in its toolbar.
- UX is clunky: 5 separate pickers. Acceptable only for a "quick fix" v1.
- Keeps widgets fully independent.

**Option B — Shared Zustand filter store (`usePerformanceFilterStore`)**
- Pattern already exists in the codebase: `useDashboardPrefs` (account selector in `AccountSummaryWidget`).
- One store: `{ accountId, period, customFrom, customTo, pnlMode }` — all perf widgets read from it.
- A compact `perf-summary` widget (or a dedicated tiny `perf-filters` tile) renders the controls; other widgets are display-only tiles.
- Also handles `selectedDate` (calendar click shared to round-trips table if they're separate widgets).
- Deduplication: can add a `dashboard` cache into the store so widgets share one fetch result.
- **Recommended for v1.** Effort: MEDIUM (one new Zustand store + wiring), but gives the cleanest multi-tile UX.

**Option C — Drop filters in widgets for v1**
- All widgets default to "month / all accounts / realized" with no per-tile customization.
- Users go to the `/performance` full page to adjust filters and get detail.
- Widgets are "glanceable tiles" only.
- Least effort; punts on the UX problem.
- Acceptable as an interim state while the split is being built.

**Recommendation**: Ship with **Option C** for the initial split (fastest, keeps widgets minimal and self-contained), plan **Option B** for the follow-up sprint. The filter store is a 1-day build once the widget shells exist. Do NOT do Option A — it creates a terrible UX that will need to be undone.

---

## 4. Research widget assessment

**File**: `frontend/components/research/research-page.tsx` (397 lines)
**Widget**: `frontend/components/workspace/widgets/research-widget.tsx` (wraps page in `h-full overflow-auto`)

### What's wrong in a tile

| Problem | Location |
|---|---|
| `PageHeader` with h1 + broker badges | line 164 |
| Root `div.space-y-8` — 32px gaps designed for scroll | line 163 |
| `xl:grid-cols-[340px_1fr]` — hardcoded 340px sidebar; collapses to one column only at `xl` breakpoint | line 192 |
| `min-h-[640px]` on the report viewer card | line 321 |
| `h-[420px]` ScrollArea for the artifact list | line 269 |
| `h-[560px]` for the iframe and markdown viewer | lines 368, 375 |

In a typical workspace tile (e.g., `w:12, h:10`), the 340px sidebar leaves almost no room for the viewer. The fixed 560px iframe forces the tile to be extremely tall or clips badly. The `PageHeader` is a hard problem: it renders an h1, broker badges, and a health-poll for its subtitle — entirely wrong inside a grid tile.

### Options

**Option 1 — Trim styling only**
Remove `PageHeader`, replace `xl:grid-cols-[340px_1fr]` with a responsive split (`md:grid-cols-[260px_1fr]` or a flex layout), replace fixed heights with `flex-1 min-h-0`. The two-panel layout survives but is cramped in anything narrower than a wide tile. Quick win; still fundamentally a page.
Effort: LOW. Good as an immediate fix to unblock the widget.

**Option 2 — Split into two widgets** (`research-launcher` + `research-viewer`)
- `research-launcher`: compact card with topic/symbol/depth form + artifact list (compact scrollable list, no fixed heights).
- `research-viewer`: iframe + report content, channel-aware (receives artifact ID from the launcher or a store).
- Requires a small shared store (selectedArtifactId) or a channel broadcast.
Effort: MEDIUM.

**Option 3 — Launcher tile + modal/drawer for viewer** (recommended)
- The widget is a compact form + artifact list.
- Clicking a report opens a full-screen modal/drawer (or navigates to `/research?id=X`).
- The modal has no size constraints — the existing iframe works perfectly there.
- The tile stays compact and useful in any reasonable tile size.
- Aligns with mobile direction (modal-based detail views).
- Note: `RetroReportButton` already navigates to `/research?id=X` — the existing deep-link mechanism via `useSearchParams` in `ResearchPage` supports this already.
Effort: MEDIUM (need a Drawer/Sheet component for the viewer; the launcher form is straightforward).

**Recommendation**: **Option 1 now** (trim the immediate styling breakage — 30 min), **Option 3 in a follow-up** (proper tile UX). Option 2 adds complexity without the UX benefits of Option 3.

---

## Key forks to decide (don't decide here — flag for founder)

1. **Calendar → Round Trips link**: Bundle both into `perf-calendar` widget (self-contained, recommended) OR split with shared Zustand `selectedDate`? Bundled is simpler; split is more composable.

2. **perf-symbol-chart vs perf-breakdown**: Combine "P&L by symbol" + "Trade Mix pie" + "Day of Week" into one `perf-breakdown` widget with tabs? Or keep them as 3 separate small tiles the user can arrange? Separate tiles = more workspace flexibility but more clutter in the add-widget menu.

3. **Shared filter store timing**: Ship Option C (hardcoded defaults, no filters in tiles) initially and add the Zustand store in the next sprint? Or block the split on building Option B first?

4. **Research widget**: Trim-only now (Option 1) or full launcher+modal rewrite (Option 3)? Trim is fast; modal is the right end state.

---

*End of recon. Files referenced: `frontend/components/performance/performance-page.tsx`, `frontend/components/performance/retro-report-button.tsx`, `frontend/components/workspace/widgets/performance-widget.tsx`, `frontend/components/workspace/widgets/research-widget.tsx`, `frontend/components/research/research-page.tsx`, `frontend/lib/performance-api.ts`, `frontend/lib/trades-api.ts`, `frontend/lib/workspace/widget-registry.tsx`, `frontend/components/layout/page-header.tsx`.*
