# Phase 6 — Grid Polish, Channel Fix, Copilot Float/Pin (Batch 8)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.

A mix of one functional bug fix (channels) and several polish items. No other subagent is running; you have the whole workspace to yourself.

---

## 1. FIX: channels won't change (stuck on channel 1) — PRIORITY

Symptom: opening the channel dropdown works, but selecting a different channel does nothing — it stays on channel 1, on both the watchlist and the chart.

Likely root cause: the widget header is the react-grid-layout drag handle (`draggableHandle=".workspace-drag-handle"`). react-draggable binds `mousedown` on the handle and calls `preventDefault()`, which fights the dropdown's selection / focus for interactive controls inside the header (channel selector, list selector, remove). The `stopPropagation` on `pointerdown` in `channel-selector.tsx` does NOT stop react-draggable's `mousedown`.

Fix (robust): tell react-grid-layout to ignore drags that start on interactive header controls.
- In `workspace-grid.tsx`, add `draggableCancel=".workspace-no-drag"` to `<ReactGridLayout>`.
- Add `className="workspace-no-drag"` (merged) to the interactive header controls: the `ChannelSelector` trigger, the `WatchlistListSelector` trigger, and the `WidgetRemoveButton` trigger (and their pop/dropdown content if needed). Keep the existing `stopPropagation` too.
- Verify after the fix: selecting Channel 2–6 / None updates the swatch color AND the chart linked to that channel actually follows the watchlist's clicked symbol. Confirm `updateWidgetConfig(item.i,{channel})` persists (it should once drag no longer eats the event). If the selection still doesn't fire, debug `onSelect`→`onChange`→`updateWidgetConfig` and fix the real cause.

## 2. Limit how small widgets get when the window shrinks

When the window is ~half width, widgets shrink to unreadable. Clamp it:
- In `workspace-grid.tsx`, clamp the measured grid width to a minimum (e.g. `const MIN_GRID_WIDTH = 1100;` then `setWidth(Math.max(el.clientWidth, MIN_GRID_WIDTH))` in the `ResizeObserver` callback). The grid container already has `overflow-auto`, so below the threshold it will scroll horizontally instead of crushing widgets. Tune the constant for readability.

## 3. Pretty, themeable drag placeholder (replace the red box) + nicer resize handles

- The RGL drag placeholder is currently the default red box (`.react-grid-placeholder`). Restyle it in `frontend/app/globals.css` using a NEW themeable token `--grid-placeholder` (define it per theme block — `:root`/carbon, ember, paper, dark, light — defaulting to an accent tint, e.g. `color-mix(in srgb, var(--c-accent) 18%, transparent)`). Style: rounded (`border-radius: 0.5rem`), subtle dashed or solid border in `var(--c-accent)`, no red. Use `!important` if RGL's injected styles win.
- Style the resize handles (`.react-resizable-handle`, incl. `-se` and `-sw`) to be subtle and on-theme (small corner grip, `var(--fg-faint)`/accent on hover), instead of the default arrow PNG. Make them visible on widget hover.

## 4. Resize from bottom-LEFT and bottom-RIGHT corners

- Add `resizeHandles={["se", "sw"]}` to `<ReactGridLayout>` so widgets can be resized from both bottom corners (today only `se`). Verify dragging the SW handle widens a widget to the left (RGL adjusts `x` + `w`). Ensure the SW handle is styled (item 3) and positioned bottom-left. Persist via the existing `onResizeStop`.

## 5. Watchlist 24h indicator: green dot, not blue Moon

- In `watchlist-widget.tsx`, replace the blue `Moon` icon with a small **green dot to the LEFT of the ticker symbol** (e.g. a `size-1.5 rounded-full bg-[var(--c-green)]` with a `title`/aria-label "Tradable 24/5 overnight"). Keep the `fetchOvernightTradable` data wiring; only the visual changes. Non-overnight symbols get no dot (keep column alignment stable — reserve the slot so symbols still line up).

## 6. Copilot: float-over by default + a pin toggle (Zen-browser style)

Change the sidebar from in-flow squish to **floating overlay by default**, with a pin button that makes it permanent/docked (push the grid).
- `use-copilot-store.ts`: add `pinned: boolean` + `setPinned`/`togglePinned`, persisted (e.g. `hmt-copilot-pinned`), default `false` (floating).
- `copilot-sidebar.tsx`: add a **pin toggle button** in the sidebar header (lucide `Pin`/`PinOff`), reflecting state.
  - **Floating (unpinned):** the expanded sidebar is an absolute overlay on the RIGHT, on top of the grid (does NOT reflow the grid); keep the click-away→collapse-to-rail behavior.
  - **Pinned:** the sidebar is in-flow and pushes/squishes the grid (the current behavior); it stays put and does NOT collapse on click-away.
- `page.tsx`: arrange so that when `pinned`, the sidebar participates in the horizontal flex (squish, as today); when floating, it's positioned absolutely over the grid area and the grid takes full width. The collapsed thin rail can remain visible in both modes.
- Keep the `ResizeObserver` grid reflow working for the pinned case.

---

## Constraints & verification
- Installed deps only (`react-grid-layout/legacy`, `motion`, lucide-react). Carbon `var(--...)` tokens; `font-sans`/`font-mono`.
- Don't break: terminal commands, watchlist add/reorder/overnight data, channel→chart linking (item 1), lock/close-confirm, the `/trading` page.
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + ~6 unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed, how you verified each of the 6 items (ESPECIALLY that changing channels now works end-to-end), decisions/trade-offs, anything incomplete with the blocker.
