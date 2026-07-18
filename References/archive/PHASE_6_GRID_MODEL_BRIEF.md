# Phase 6 — Free-Flowing Grid Model + Resize Fix + Margins (Batch 9)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.

NOTE: there are already-applied UNCOMMITTED working-tree fixes you should INCLUDE in your commit:
- `channel-selector.tsx` and `watchlist-list-selector.tsx`: `onSelect`→`onClick` (base-ui Menu.Item fires onClick; this fixed the channel-switch bug).
- `use-workspace-store.ts`: `hydrate` drops unknown widget types from saved layouts.
Leave those intact and commit them along with your work.

Goal: make the grid resolution-independent and free-flowing, fix the bottom-left resize handle, and even out the page margins.

## 0. Current grid (read first)
`frontend/components/workspace/workspace-grid.tsx` uses `react-grid-layout/legacy`:
- `cols={24}`, `rowHeight={48}`, `margin={[12,12]}`, `width={Math.max(clientWidth, MIN_GRID_WIDTH=1100)}` (a `ResizeObserver` on `#workspace-grid-root`), `useCSSTransforms={false}`, `resizeHandles={["se","sw"]}`, `draggableHandle=".workspace-drag-handle"`, `draggableCancel=".workspace-no-drag"`.
- Default vertical compaction (widgets snap up/together).
- Container: `<div class="min-h-0 flex-1 overflow-auto px-4 pt-3 pb-6">`.
- Layout coords are in 24-col units (`layout-persistence.ts` `DEFAULT_WORKSPACE_LAYOUT`).

## 1. Resolution-independent sizing (consistent widget pixel size; wide screens add SPACE, not width)
The problem: today each column = containerWidth/24, so widgets get wider on big monitors and tiny on small ones. Desired: a widget keeps ~constant pixel size across resolutions; a wider screen yields MORE columns (extra empty space to place more widgets), a narrower screen FEWER.

Implement a **fixed target column width**:
- Add constants e.g. `COL_TARGET_PX = 56`, `MARGIN = 12`, `MIN_COLS = 12`, `MAX_COLS = 48`.
- Derive cols from the measured container width: `cols = clamp(Math.floor((width + MARGIN) / (COL_TARGET_PX + MARGIN)), MIN_COLS, MAX_COLS)`.
- Keep `width = clientWidth` (drop the `MIN_GRID_WIDTH` clamp). Because cols scales with width, RGL's computed column pixel width stays ≈ `COL_TARGET_PX`, so a widget spanning `w` columns keeps a near-constant pixel width regardless of screen size; extra width becomes extra columns (open space on the right).
- Pass the derived `cols` to `<ReactGridLayout>` and to the store's placement math (the store's `GRID_COLS=24` `findPlacement` should use the same dynamic column count — export/accept cols, or move `findPlacement` to accept a `cols` arg; keep it simple and consistent so newly-added widgets place correctly). Clamp any widget `w` to `<= cols`.
- Re-derive cols in the `ResizeObserver` callback so it updates live as the window resizes. Keep `useCSSTransforms={false}`.
- Sanity-check the default layout still looks reasonable at ~24–28 cols (a 1920px screen ≈ 28 cols, a 1366px laptop ≈ 20 cols). It's OK if very wide widgets need a slightly smaller default `w`.

## 2. Free-flowing placement (allow gaps; don't snap back)
Today widgets compact together and a dragged widget snaps to touch others. Make placement free:
- Set `compactType={null}` and `preventCollision={true}` on `<ReactGridLayout>` so widgets stay exactly where dropped (gaps allowed) and don't overlap.
- Verify: dragging a widget into open space leaves it there; it does not jump back to the bottom row or snap against a neighbor.
- Keep `onDragStop`/`onResizeStop` persistence working.

## 3. Fix the bottom-left (SW) resize handle
Currently only SE works; the SW handle's arrow points the wrong way (up) and it can't be dragged.
- Fix the CSS for `.react-resizable-handle-sw` in `globals.css`: position it at the **bottom-left** corner, `cursor: nesw-resize`, and orient the visual grip correctly (the Batch-8 styling got it wrong). Ensure it sits on top (z-index) and is pointer-interactive (not covered by widget content/overflow). Both `-se` and `-sw` should be subtle, themed (`var(--fg-faint)`, accent on hover), and appear on widget hover.
- Verify dragging the SW handle resizes the widget from the left (RGL adjusts `x`+`w`) and persists.

## 4. Even out margins
The gap between the toolbar and the top of the widgets looks smaller than the left/right gap to the screen edge. Make the grid's top padding match the horizontal padding (e.g. align `pt-*` with `px-*` on the grid container, accounting for the toolbar border) so the gutter is visually consistent on all sides.

## Constraints & verification
- Installed deps only (`react-grid-layout/legacy`). Carbon `var(--...)` tokens.
- Don't break: drag/resize persistence, the lock toggle (`isDraggable/isResizable={!locked}`), channel/list selectors, copilot, console.
- Frontend-only (no backend) — note in your report that no server restart is needed (Next hot-reloads).
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + ~6 unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed, how you verified each item (esp. widgets keeping size across widths + free placement + SW resize working), decisions/trade-offs (incl. chosen COL_TARGET_PX), anything incomplete with the blocker.
