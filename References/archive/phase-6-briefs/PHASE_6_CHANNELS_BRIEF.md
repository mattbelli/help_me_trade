# Phase 6 — Channels, Multi-Instance Widgets & Live Charts (Batch 2)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.

This batch turns the workspace from "one widget per type, static charts" into a
ThinkorSwim-style linked workspace: multiple chart/watchlist instances, color
"channels" that link a watchlist's selection to charts, a finer grid, and charts
that move with the live WS feed.

---

## 0. Current state (read these first)

- `frontend/lib/workspace/layout-persistence.ts` — `WorkspaceLayoutItem` (`i,x,y,w,h,minW,minH`), `DEFAULT_WORKSPACE_LAYOUT`, localStorage persistence (`STORAGE_KEY = "hmt-workspace-layout"`). **`i` is currently the widget *type* id.**
- `frontend/lib/workspace/use-workspace-store.ts` — zustand store. `addWidget(id)` blocks duplicates via `layout.some(i===id)`. `findPlacement` packs the grid.
- `frontend/lib/workspace/widget-registry.tsx` — `WIDGET_REGISTRY` keyed by type id; `component: ComponentType` (no props today); `defaultSize {w,h,minW,minH}`.
- `frontend/components/workspace/workspace-grid.tsx` — `ReactGridLayout cols={12}`, renders `getWidgetDefinition(item.i)`, `WidgetHeader` (drag handle, title, meta, remove X).
- `frontend/components/workspace/widgets/watchlist-widget.tsx` — uses multi-list API (`fetchWatchlists` → `active_id`/`lists`); now has add/remove (Batch 1).
- `frontend/components/workspace/widgets/chart-widget.tsx` — local `symbol` state, renders `<TvLightChart symbol compact />`.
- `frontend/components/trading/tv-light-chart.tsx` — already has per-component `timeframe` (`CHART_TIMEFRAMES` from `@/lib/chart-time`, default index 4), `style` (candle/line), price-alert lines, `barsRef`/`mainRef`. In `compact` mode it HIDES the timeframe/style toolbar.
- `frontend/lib/use-watchlist-market.ts` — WS client pattern: opens `marketWsUrl()`, sends `{action:"subscribe", symbols}`, handles `{type:"quotes"|"quote", ...}` frames with a `live` flag. Reuse this pattern for a single-symbol live quote hook.
- Backend `brokers/market_stream.py` — shared Alpaca WS hub (do NOT modify in this batch). It streams trade ticks; subscribing more symbols is free (cap `MAX_WS_SYMBOLS=30`).

---

## 1. Layout model: instance IDs + per-instance config (+ 24-col grid)

Update `WorkspaceLayoutItem` in `layout-persistence.ts`:

```ts
export type ChannelId = "none" | "1" | "2" | "3" | "4" | "5" | "6";

export type WidgetInstanceConfig = {
  channel?: ChannelId;        // channel-aware widgets only
  symbol?: string;            // chart: manual symbol when channel === "none"
  timeframe?: string;         // chart: ChartTimeframe.value
  style?: "candle" | "line";  // chart
  watchlistId?: string;       // watchlist: which list this instance shows
};

export type WorkspaceLayoutItem = {
  i: string;       // INSTANCE id, e.g. "chart-1", "watchlist-2", "positions"
  type: string;    // registry type id, e.g. "chart"
  x: number; y: number; w: number; h: number;
  minW?: number; minH?: number;
  config?: WidgetInstanceConfig;
};
```

- **Bump `STORAGE_KEY` to `"hmt-workspace-layout-v2"`** (old 12-col layouts are abandoned; the new default re-applies once — acceptable).
- Rewrite `DEFAULT_WORKSPACE_LAYOUT` for a **24-column** grid (double the old x/w). Give each item an `i` (instance), `type`, and `config` where relevant. Suggested default board (24 cols):
  - `{ i:"account-summary", type:"account-summary", x:0,y:0,w:24,h:3 }`
  - `{ i:"open-orders", type:"open-orders", x:0,y:3,w:12,h:5 }`
  - `{ i:"positions", type:"positions", x:12,y:3,w:12,h:5 }`
  - `{ i:"watchlist-1", type:"watchlist", x:0,y:8,w:8,h:6, config:{channel:"1"} }`
  - `{ i:"chart-1", type:"chart", x:8,y:8,w:16,h:6, config:{channel:"1", timeframe:"...", style:"candle"} }`
  - `{ i:"equity-curve", type:"equity-curve", x:0,y:14,w:12,h:5 }`
- Keep load/save shape but validate items have `type` (drop malformed).

In `workspace-grid.tsx`: set `cols={24}`. Adjust `minW`/`minH` defaults so widgets can shrink: see registry `minW` below (a watchlist should be able to reach ~1/5–1/6 of the screen). Keep `rowHeight={48}`, `margin={[12,12]}`, `useCSSTransforms={false}`.

---

## 2. Channel store + channel UI

New file `frontend/lib/workspace/channels.ts`:

```ts
export type ChannelId = "none" | "1" | "2" | "3" | "4" | "5" | "6";

export const CHANNELS: { id: ChannelId; label: string; color: string }[] = [
  { id: "none", label: "No link", color: "var(--fg-faint)" },
  { id: "1", label: "Channel 1", color: "var(--c-red)" },
  { id: "2", label: "Channel 2", color: "var(--c-amber)" },
  { id: "3", label: "Channel 3", color: "var(--c-yellow)" },
  { id: "4", label: "Channel 4", color: "var(--c-green)" },
  { id: "5", label: "Channel 5", color: "var(--c-blue)" },
  { id: "6", label: "Channel 6", color: "var(--c-violet)" },
];
```

`frontend/lib/workspace/use-channel-store.ts` — zustand, **session-only (not persisted)**:
- state `symbols: Record<ChannelId, string | undefined>`
- `setChannelSymbol(channel: ChannelId, symbol: string)` (no-op for `"none"`)
- selector to read the current symbol for a channel.

Channel selector UI: a small component (e.g., `workspace/channel-selector.tsx`) — a tiny colored square button shown in `WidgetHeader` ONLY for `channelAware` widgets. Clicking opens a small menu (reuse the same dropdown primitive Batch 1 used for the broker popover) listing the 7 channels with their color dots; selecting one calls `onConfigChange({ channel })`. The square shows the active channel color (faint when `"none"`).

---

## 3. Registry: type-keyed + props + flags

In `widget-registry.tsx`:
- Add to `WidgetDefinition`: `multiInstance?: boolean`, `channelAware?: boolean`.
- Mark `chart` and `watchlist` as `multiInstance: true, channelAware: true`. All others are singletons, not channel-aware.
- Widget components now receive props. Define and export:

```ts
export type WidgetProps = {
  instanceId: string;
  config: WidgetInstanceConfig;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
};
```

Make `component: ComponentType<WidgetProps>`. Singleton widgets can ignore the props. `getWidgetDefinition(type)` stays but is now looked up by **type**.

Tighten `minW` so widgets shrink on a 24-col grid (examples): account-summary `minW:6`, positions/open-orders/equity-curve `minW:5`, watchlist `minW:4`, chart `minW:6`, price-alerts `minW:5`. (These are floors; tune for readability.)

---

## 4. Workspace store: multi-instance + config writes

In `use-workspace-store.ts`:
- `addWidget(type)`: look up def by type. If `multiInstance`, generate a fresh instance id `${type}-${n}` (n = next free integer for that type) and seed `config` defaults (e.g., chart → `{channel:"none", timeframe: default, style:"candle"}`). If singleton, keep the existing block-duplicate behavior (one instance, `i === type`).
- Add `updateWidgetConfig(instanceId, patch)` that merges into the item's `config` and persists.
- `removeWidget(instanceId)` unchanged (filters by `i`).
- `gridLayout`/`onLayoutChange` in the grid must key by instance `i` and preserve `type`/`config` across layout changes.

The grid render loop uses `getWidgetDefinition(item.type)`, renders `<Widget instanceId={item.i} config={item.config ?? {}} onConfigChange={(p)=>updateWidgetConfig(item.i,p)} />`.

`workspace-cmd-palette.tsx` / picker: adding a chart or watchlist should now create a new instance each time (no "already added" rejection for multiInstance types).

---

## 5. Watchlist widget (instance-aware + publishes to channel)

In `watchlist-widget.tsx`, accept `WidgetProps`:
- Per-instance list: if `config.watchlistId` is set, show that list; else the active list. Add a small list dropdown in the widget so different instances can show different lists; persist choice via `onConfigChange({ watchlistId })`.
- **Row click publishes to the channel:** on clicking a symbol row, call `useChannelStore.setChannelSymbol(config.channel, symbol)` (no-op when channel is `"none"`). Give rows a clickable affordance/hover; keep the add/remove controls from Batch 1 working.
- Keep the add/remove + `/watchlist` behavior intact.

---

## 6. Chart widget + live updates

`chart-widget.tsx` accepts `WidgetProps`:
- **Symbol resolution:** if `config.channel !== "none"`, the chart's symbol is the channel symbol from `useChannelStore` (fall back to `config.symbol` or "SPY" until the channel has one). If channel is `"none"`, use the manual symbol input (persist via `onConfigChange({ symbol })`).
- **Per-instance timeframe/style:** drive from `config.timeframe`/`config.style`; render a compact timeframe + candle/line control row in the widget header area (since `TvLightChart compact` hides its own toolbar). Persist changes via `onConfigChange`.
- **Live price:** add a single-symbol live-quote hook `frontend/lib/use-symbol-quote.ts` (mirror `use-watchlist-market.ts`: open `marketWsUrl()`, subscribe to the one symbol, return `{ price, live }`). Pass the latest price into `TvLightChart` as a new optional prop.

`tv-light-chart.tsx`:
- Add optional prop `livePrice?: number`. Add a controlled-prop path so the widget can drive `timeframe`/`style`: accept optional `timeframe?: ChartTimeframe` and `style?: "candle"|"line"` props that, when provided, override internal state (keep internal state as fallback for the non-compact `/trading` usage so that page is unaffected).
- On `livePrice` change, update the forming (last) bar: mutate the last entry in `barsRef` (set `close = livePrice`, `high = max(high, livePrice)`, `low = min(low, livePrice)`) and call `mainRef.current.update(...)` so the candle/line moves without a full reload. Guard against no bars / loading.
- Do NOT break the existing `/trading` page usage of `TvLightChart` (props are additive/optional).

Note (acceptable for now): each chart/watchlist instance opens its own browser→backend WS connection; the backend hub shares one upstream Alpaca connection, so this is fine. Mind the 30-symbol upstream cap.

---

## Constraints & verification

- Use ONLY installed deps (zustand, react-grid-layout/legacy, lightweight-charts, the existing dropdown primitive). No new packages.
- Carbon token styling only (`var(--...)`), `font-mono`/`font-sans`. No hardcoded colors.
- Run `npm run typecheck` and `npx eslint <changed files>` in `frontend/`; fix everything you introduce. Pre-existing errors elsewhere are out of scope.
- Manual browser smoke isn't required, but the build must typecheck/lint clean.

## Report back
Branch + commit hash, files changed, how you verified each of the 6 sections, key decisions/trade-offs, and anything incomplete with the blocker.
