# CHART_ENGINE — the native chart engine contract

*Phase 0 deliverable (2026-07-10, founder go). This is the spec every phase is held against.
Companion code: `frontend/lib/chart-engine/`. Board card: `native-chart-engine`.*

## Why this exists

The founder is replacing TradingView lightweight-charts with an engine we own — for look,
performance, and above all **the freedom to paint our own data on the chart** (session nodes,
alerts, dark-pool/sweep markers, confluence badges, volume profile). The phone app already made
this move with React Native Skia and it worked. The web engine follows the same philosophy —
own the pixels — with the browser's canvas 2D as the painter (which **is** Skia under the hood
in Chrome; a CanvasKit/WASM build would duplicate what the browser ships).

## The one architectural rule

**The core is headless.** Everything under `frontend/lib/chart-engine/` except the explicitly
DOM-labeled adapter files is pure TypeScript: no DOM types, no React, no `window`. Pixels happen
only through the `Painter` interface. This is the entire shared-with-phone bet: the same core can
later drive the phone charts through an RN-Skia painter (Phase 4, parked until duplication hurts).

```
frontend/lib/chart-engine/
  types.ts     pure   bar model, colors, options, layout constants
  painter.ts   pure   Painter interface (the only way to draw)
  scales.ts    pure   time scale (bar-index domain) + price scale + viewport
  ticks.ts     pure   nice price ticks (1-2-5) + time tick selection/labels
  series.ts    pure   candle/line/volume geometry → Painter calls
  merge.ts     pure   live-tick merge contract
  canvas2d.ts  DOM    Canvas2DPainter + the two-layer canvas stack + DPR
  engine.ts    DOM    ChartEngine orchestrator: events → core → repaint
frontend/components/trading/native-chart.tsx   React wrapper (data, theme, legend)
```

## Coordinate system

- All Painter coordinates are **logical (CSS) pixels**. The adapter owns DPR: it sizes the
  bitmap `w×dpr`, scales the context, and **pixel-snaps strokes** (a 1px line lands on a
  physical pixel row, not blurred across two). Core code never sees DPR.
- Origin top-left. The pane rect (chart area excluding axes) is computed by the engine and
  passed to core draw functions; core never assumes full-canvas.

## Time scale — bar-index domain

Trading charts space bars evenly and skip non-trading time; wall-clock scales leave holes.

- Domain is **fractional bar index** `i ∈ [-0.5, n-0.5 + rightOffset]`.
- Viewport state = `{ barSpacing: px/bar, rightEdgeIndex: fractional index at pane right edge }`.
- `xOf(i) = paneWidth - (rightEdgeIndex - i) · barSpacing` and its inverse. Bar *centers* at
  integer indices.
- **Zoom is anchored**: wheel at x keeps `indexAt(x)` fixed while `barSpacing` scales
  (clamped `[0.5, 60]`). Pan shifts `rightEdgeIndex` by `dx / barSpacing`.
- Clamps: at least 2 bars remain visible; whitespace right of the last bar allowed up to
  `paneWidth/barSpacing − 2` bars. Default view: `rightEdgeIndex = lastIndex + 5`,
  `barSpacing = 7`.
- Time labels come from bar timestamps (epoch ms): intraday timeframes label session/day
  boundaries and HH:MM at ~90px spacing; daily+ label month/year boundaries. Ticks snap to
  real bars — never interpolated times.

## Price scale

- Linear, per-pane. **Autoscale** fits the visible bars' high/low (plus visible overlays later)
  with 8% pad top and bottom, recomputed on any viewport/data change while in auto mode.
  Manual mode (axis drag, Phase 2) sticks until double-click on the axis resets to auto.
- Ticks: 1-2-5·10ᵏ steps targeting ~1 tick per 50px; label decimals derive from the step.

## Panes & layout (Phase 1 fixed; generalized later)

```
+----------------------------------+------+
|  main pane (candles/line)        | price|
|                                  | axis |
|  volume strip (16% of pane h)    | 64px |
+----------------------------------+------+
|  time axis 22px                         |
+------------------------------------------+
```
Volume shares the time scale, has its own hidden linear scale (0..maxVisibleVolume).
Oscillator panes (RSI etc.) arrive with the generalized pane API in Phase 2/3 — the layout
code must not hard-assume two panes even though Phase 1 renders exactly two.

## Layers & repaint discipline

Two stacked canvases managed by the adapter:

1. **scene** — grid, series, volume, axes, last-price line. Dirty on: data, viewport, style,
   colors, resize.
2. **cursor** — crosshair lines + axis chips + hover state. Dirty on: pointer move/leave.

Invalidation sets dirty flags; **one rAF callback paints whichever layers are dirty**. Event
handlers never paint synchronously. Mouse-move therefore repaints only the cursor layer —
that is the 60fps mechanism, and it is a contract, not an optimization.

**Perf budget (measured, not vibes):** cursor-layer repaint ≤ 1ms; full scene repaint ≤ 4ms
with 5,000 visible bars at 1080p on the dev box. A dev-only frame-time overlay
(`?chartPerf=1`) reports both. No per-frame allocations in hot paths.

## Live data contract

- `setBars(bars)` replaces history (sorted ascending, dedup by time — the wrapper guarantees).
- `applyLive(price)` mutates **only the last bar**: `close = price`, `high = max`, `low = min`.
  It never appends, reorders, or touches history. (The TV 1D→4H page-kill came from exactly
  this seam — the merge ran against the wrong series. Here the rule is structural.)
- New-bar rollover in Phase 1 comes from the wrapper's periodic refetch (bars-cache TTLs);
  Phase 2 adds local rollover from the timeframe so the forming bar appears without a fetch.

## Painter interface (the adapter seam)

```ts
interface Painter {
  clear(): void;
  fillRect(x, y, w, h, color): void;
  line(x1, y1, x2, y2, { color, width, dash? }): void;      // adapter pixel-snaps
  polyline(points, { color, width }): void;
  circle(x, y, r, { fill?, stroke?, strokeWidth? }): void;
  text(s, x, y, { color, font, align?, baseline? }): void;
  measure(s, font): number;
  withClip(x, y, w, h, fn): void;
}
```
Adapters: `Canvas2DPainter` (now); `RNSkiaPainter` (Phase 4, phone). Anything the core wants
to draw that this interface can't express means the interface grows — core never reaches
around it.

## React wrapper (`native-chart.tsx`)

- Mirrors `TvLightChart`'s props (`symbol, timeframe, style, livePrice, extPrice,
  marketSession, compact, extended, ...`) so `chart-widget` can swap engines per widget.
- Owns data: `fetchBarsCached` (same cache as TV), loading/error states, live-price effect.
- Owns theme: resolves `EngineColors` from CSS vars (`chart-colors.ts`) and re-resolves on
  theme change → `engine.setColors()`. All four themes + custom themes work from day one.
- Owns the OHLC legend (DOM overlay fed by the crosshair callback) — text UI stays in React;
  the canvas draws only chart.

## Rollout & verification

- **A/B in the real cockpit**: eye menu gains "Native engine (beta)" per chart widget
  (`config.chartEngine: "native"`). TV chart remains the default until the founder kills it.
- Pure modules get **vitest** unit tests (`npm run test:engine`): zoom-anchor invariance,
  pan/clamp behavior, autoscale ranges, tick steps, live-merge immutability of history.
- Visual checks: headless-chromium screenshot loop with zoomed crops (wick alignment, axis
  text crispness at dpr 1 and 2) before founder eyeballs.
- Adversarial Sonnet review on interaction math before the toggle ships.

## Phases

- **0 (this doc)** — contract, types, painter interface, vitest infra. ✅ when committed.
- **1** — scales/ticks/series/merge + canvas2d + engine + wrapper + eye-menu toggle. Founder
  can A/B candles, volume, pan/zoom, crosshair, live ticks, themes.
- **2** — parity overlays, one per round: session-node levels, price alerts (lines, bells,
  drag, right-click → set alert), drawings, ext-hours shading, studies, axis drag.
  **Cross-client contract: `References/RN_SKIA_CHART_RELAY.md` §5** — alerts/drawings render the
  same API rows as the phone's Skia chart (data-space `{time, price}` anchors only; alert visual
  conventions locked in §5.2; new drawings write semantic color names, not hex).
- **3** — polish (tick flash, price tint), perf gate signed off, founder verdict → delete
  lightweight-charts + the TV attribution link.
- **4 (parked)** — RN-Skia painter; port phone charts onto the shared core.

## Non-goals (now)

Touch gestures (phone is native), order-entry from chart, multi-symbol overlays, seconds
timeframes, WebGL. Revisit only after Phase 3.
