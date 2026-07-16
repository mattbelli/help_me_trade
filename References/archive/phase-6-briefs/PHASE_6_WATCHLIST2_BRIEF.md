# Phase 6 — Watchlist Reconfigure + Channel UX + Smooth Reorder (Batch 5)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.
**Run AFTER Batch 4 (console/chat) commits** — both touch `workspace-grid.tsx` and `use-workspace-store.ts`.

Goal: tighten the watchlist into a dense, readable, spreadsheet-like panel, fix the
channel link so selecting a channel is obvious and actually works, and replace the
buggy native drag-reorder with smooth sliding animation.

---

## 0. Current state (read first)

- `frontend/components/workspace/widgets/watchlist-widget.tsx` — has: an internal top banner showing the list name (dropdown only when >1 list) + "click a row to link…" hint; a `WatchlistAddRow` ("+ add symbol") as the LAST row; rows in a `<table>` with a `⠿` handle using NATIVE HTML5 drag; row click → `publishSymbol` (channel); `×` remove; Moon overnight badge.
- `frontend/components/workspace/channel-selector.tsx` — `ChannelSelector` renders a tiny (size-2.5) color swatch dropdown; `onChange(channelId)`.
- `frontend/lib/workspace/channels.ts` — `CHANNELS` (none + 6 colors mapped to `--c-*` tokens), `channelColor(id)`.
- `frontend/lib/workspace/use-channel-store.ts` — session store; `setChannelSymbol`, `useChannelSymbol`.
- `frontend/components/workspace/workspace-grid.tsx` — `WidgetHeader` renders `⠿`, the `ChannelSelector` (for `channelAware` widgets), the title, meta, remove `×`. The grid wires `onChannelChange={(next)=>updateWidgetConfig(item.i,{channel:next})}` and passes `config`/`onConfigChange` to each widget.
- `frontend/lib/workspace/use-workspace-store.ts` — `updateWidgetConfig(instanceId, patch)` merges config + persists.
- `motion` (`motion/react`) is already a dependency (used in `command-console.tsx`) and exports `Reorder` + `useDragControls`.

---

## 1. Channel: make it work AND make it obvious

- **Verify propagation:** confirm selecting a channel in `ChannelSelector` actually updates `config.channel` (via `updateWidgetConfig`) and re-renders. If broken, fix it.
- **Make selection obvious** (the current tiny swatch gives no visible feedback):
  - Enlarge/clarify the channel control so it reads as clickable: a small rounded chip with the channel color fill + a caret (e.g. a colored dot + `CH 1` label, or `link` when none), with hover/focus affordance. Keep it in the widget header.
  - Give the WIDGET a clear channel cue: when a channel is set, show the channel color prominently — e.g. a colored left accent bar on the header or a colored top hairline border using `channelColor(channel)`. Two widgets on the same channel should visibly share the same color so the link is self-evident. `none` = neutral/no accent.

## 2. Watchlist banner: list selector up top, drop the hint

- Move the **list selector** to the top of the watchlist (a compact dropdown in a slim top banner, left-aligned so it reads right beside the "watchlist" title). Always show it as a dropdown (even with one list, so the user can switch/again create later); on select → `onConfigChange({ watchlistId })`.
- **Remove** the "Click a row to link symbol to channel" hint entirely, and the redundant separate list-name line. Maximize readable data; keep chrome minimal. (Note for future: we want to push as much readable data onto this panel as possible — keep headers/padding tight.)

## 3. Add symbol = blank typable row (no "+ add symbol" label)

- Replace the labeled "+ add symbol" row with an **always-present blank input row directly below the last ticker** — it should look like an empty next cell (very faint or no placeholder), not a button. Clicking/typing it shows the debounced `searchSymbols` suggestions; Enter (or picking a suggestion) adds via `updateWatchlist(list.id,{symbols:[...,SYM]})`, uppercased + de-duped; after adding it resets and stays ready for the next entry. Keep it visually consistent with the data rows (same column alignment).

## 4. Smooth drag-to-reorder (replace native HTML5 DnD)

- Replace the buggy native drag with **`motion`'s `Reorder`** (`import { Reorder, useDragControls } from "motion/react"`):
  - Convert the symbol rows from `<table><tr>` to a `Reorder.Group` (vertical) of `Reorder.Item`s (divs laid out with a CSS grid so columns Sym / Last / Δ% / 7d stay aligned; keep a matching header row above). Layout animation will slide tiles to show the drop target.
  - Add a clear **grab handle on the LEFT of each ticker** (a few dots, e.g. `⋮⋮` or `⠿`) and gate dragging to the handle only: `Reorder.Item` with `dragListener={false}` + `dragControls={controls}`, and `onPointerDown={(e)=>controls.start(e)}` on the handle. This keeps a plain row click → `publishSymbol(channel)` working and the `×` remove working.
  - Persist the new order when reordering settles (`onReorder` updates local state; persist via `updateWatchlist(list.id,{symbols:reordered})`, optimistic with rollback on error).
  - Keep the Moon overnight badge and price/Δ%/sparkline columns intact.

## Constraints & verification
- Installed deps only (`motion`, lucide-react, existing dropdown/command primitives). Carbon `var(--...)` tokens; `font-mono`.
- Don't break channel publishing to charts, the overnight badge, or remove.
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed, how you verified each section (esp. channel selection now visibly works, and reorder animates smoothly), decisions/trade-offs, anything incomplete with the blocker.
