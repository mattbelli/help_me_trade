# Phase 6 — Widget catalog brief (for coding agents)

Builds on the shipped workspace slice. Goal: fill the board like `hermes-ui-v2.html` by adding a **widget registry + `/add` picker + more widgets**, every widget bound to **REAL data** (no mocks/fabricated values). Match the existing carbon styling exactly.

**Branch:** continue on `feat/phase6-workspace-shell` (already checked out). Commit when done; do not push, do not open a PR.

## Read first
- `References/PHASE_6_BRIEF.md` — original slice brief + locked decisions (still apply).
- `References/hermes-ui-v2.html` — VISUAL reference for each widget (open orders, watchlist w/ sparkline, equity curve, insights). Reproduce the look; do NOT copy its fake data.
- Existing workspace code (match these patterns precisely — fonts, tokens, table/tile styling):
  - `frontend/components/workspace/workspace-grid.tsx` (widget chrome, drag handle, grid)
  - `frontend/components/workspace/widgets/account-summary-widget.tsx`, `positions-widget.tsx`
  - `frontend/components/workspace/workspace-cmd-palette.tsx` (the `/add` console command hook)
  - `frontend/lib/workspace/use-workspace-store.ts`, `layout-persistence.ts`
  - `frontend/lib/api.ts` — the data layer (types + fetchers listed below)
- Theme tokens live in `frontend/app/globals.css` (`:root` = carbon default; `--panel`, `--bg-inset`, `--border`, `--fg-dim`, `--fg-faint`, `--c-accent`, `--c-green`, `--c-red`, `--c-blue`, `--c-violet`, `--c-amber`, `--panel-shadow`). Use these via `bg-[var(--panel)]`, `text-[var(--c-green)]`, etc. Fonts: `font-sans` (Inter), `font-mono` (JetBrains Mono).

## Locked decisions
1. **Widget registry.** Replace the hardcoded `if/else` + `WIDGET_META` in `workspace-grid.tsx` with a registry: `frontend/lib/workspace/widget-registry.tsx` exporting an array/map of `{ id, title, meta?, component, defaultSize: { w, h, minW, minH } }`. The grid renders from the registry; the picker reads from it.
2. **Real data only.** Each widget binds to the endpoints below. If a field doesn't exist in the data (e.g. RSI, "signal", "% win rate"), **do not fabricate it** — omit it or show an explicit empty/loading state. This is a hard project principle.
3. **Add/remove flow.** Toolbar `+ widget` button and the console `/add` command both open the **widget picker** (use `cmdk` or a simple panel) listing registry widgets not already on the board; selecting one appends it to the layout at a sensible slot via `useWorkspaceStore`. Each widget header gets a close (`×`) control that removes it from the layout. All changes persist through the existing `LayoutPersistence` interface.
4. **Default board.** Update `DEFAULT_WORKSPACE_LAYOUT` to a full, mockup-like board: account-summary (wide), open-orders + positions, equity-curve + watchlist. A fresh load (or after `/clear-layout`) should look populated like the mock.
5. **Styling.** Reuse the established panel/table/tile classes and tokens. Keep `useCSSTransforms={false}` on the grid (crisp text). No dotted background.
6. **Deps:** only what's installed — `recharts` and `lightweight-charts` are both available for charts. Do NOT add packages.

## Widgets to build (all real data)
| Widget id | Source (lib/api.ts) | Content |
|-----------|---------------------|---------|
| `open-orders` | `snapshot.open_orders` (from `useSnapshot()`) | id, symbol, side (buy/sell colored), qty/filled, type, status (colored by state) |
| `watchlist` | `fetchWatchlists()` + `fetchWatchlistMarket(symbols)` | symbol, last price, change_pct (green/red), the real `sparkline` number[] rendered as a tiny inline bar/line. Active-list aware. |
| `equity-curve` | `fetchPortfolioHistory(range)` | line/area chart of `points[].equity`; small range toggle (1D/1W/1M/1Y); poll ~20s |
| `chart` (optional) | `fetchBars(symbol, {timeframe})` | `lightweight-charts` candlestick; symbol input; reuse existing chart patterns if any |
| `price-alerts` (optional) | `fetchPriceAlerts()` | symbol, price, direction, triggered state |

Keep `account-summary` and `positions` as-is (already shipped). **Do NOT build a fake "Hermes insights" widget** — there's no endpoint; skip it (a registry stub with an explicit "insights coming soon" empty state is acceptable but optional).

## Acceptance criteria
- Registry drives the grid; adding `open-orders`, `watchlist`, `equity-curve` works from both the `+ widget` button and `/add` console command; removing via the header `×` works; all persist across reload.
- Every widget shows **live** data (run API on :8000 + frontend on :3001) with proper loading/empty states; no placeholder/fake numbers anywhere.
- Default board renders full and matches the carbon aesthetic of the existing widgets.
- `npm run typecheck` and `npx eslint` clean on all new/changed files (pre-existing errors elsewhere are not your concern).
- Commit to the branch with a clear message; report files changed, which widgets are live vs stubbed, any data fields intentionally omitted, and anything not completed.
