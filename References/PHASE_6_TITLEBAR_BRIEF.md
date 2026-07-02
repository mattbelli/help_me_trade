# Phase 6 — Title-bar Consolidation, Channel Revert, Close-Confirm, Lock, Console Tweak (Batch 6)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.

A set of UI-chrome refinements. No new data; mostly moving/removing chrome and a couple of small interactions.

---

## 1. Revert the Batch-5 channel "accent" design; move the channel control to the right (near ✕)

Batch 5 added an ugly colored accent treatment + a labeled chip. Remove it and go back to the simple swatch, relocated.

In `frontend/components/workspace/workspace-grid.tsx` `WidgetHeader`:
- **Remove** the channel accent styling: the `border-t-2` + `borderTopColor`, the absolute left color stripe `<span ... w-1>`, and the `accentColor` logic in the header.
- In `WorkspaceGrid`'s widget wrapper (the `layout.map` panel `<div>`), **remove** the `accentColor` inset `boxShadow` (`style={accentColor ? {...} : undefined}`); keep the existing `highlightInstanceId` pulse ring untouched.
- **Move the `ChannelSelector`** from the LEFT (next to `⠿`/title) to the RIGHT side of the header, immediately to the LEFT of the ✕ remove button (so the channel dot sits near ✕). Header order becomes: `⠿  title  [watchlist list selector]  [meta]  …spacer…  [ChannelSelector]  [✕]`.

In `frontend/components/workspace/channel-selector.tsx`:
- **Revert to the simple swatch** (the pre-Batch-5 look): a small color square (~`size-2.5`, `rounded-sm`, `border border-[var(--border)]`, `opacity-50` when `none`) as the dropdown trigger — NOT a labeled `CH 1` chip. Keep the dropdown menu list (color dot + label per channel) and `onChange`. Keep `stopPropagation` on the trigger so it doesn't start a grid drag.

## 2. Confirm before closing a widget

Clicking ✕ currently removes immediately (easy to do by accident). Add a confirmation step:
- Clicking ✕ opens a small popover/menu anchored to the button: text like "Remove this widget?" with **Cancel** and **Remove** actions. Only **Remove** calls `removeWidget(item.i)`.
- Use the existing dropdown/popover primitive (`@/components/ui/dropdown-menu` or a small popover) styled on-theme (carbon tokens, `font-mono`). Keep `stopPropagation` so it doesn't start a drag. Esc / clicking Cancel / clicking away dismisses.

## 3. Layout lock toggle

Add a lock so the layout can't be accidentally dragged/resized/closed:
- Add to `frontend/lib/workspace/use-workspace-store.ts`: `locked: boolean` + `toggleLock()` (and read it where needed). Persist to a separate localStorage key (e.g. `hmt-workspace-locked`) so it survives reload; hydrate it alongside layout.
- In `workspace-grid.tsx` `ReactGridLayout`: set `isDraggable={!locked}` and `isResizable={!locked}`. When locked, the drag handle should not drag (RGL handles this) and the ✕ remove button should be hidden or disabled (so locked = no accidental close).
- The lock toggle BUTTON lives in the title bar next to "Workspace / default layout" (see §4) — a small lock/unlock icon (lucide `Lock`/`LockOpen`) reflecting state, with an accessible label and an on-theme active style.

## 4. Move "Workspace", "default layout", lock, and the add button into the title bar (centered)

Currently `workspace-grid.tsx` renders its own header row (the `<div class="flex shrink-0 items-center gap-3 px-5 pt-4 pb-2.5">` containing the `Workspace` `<h1>`, the `default layout` chip, a spacer, and the `+ Add widget · or type /add` button). 

- **Delete that entire header row from `workspace-grid.tsx`** (the grid content area `<div class="min-h-0 flex-1 overflow-auto px-4 pb-6">` stays; just drop the header strip above it). Adjust top padding so the grid still has a little breathing room.
- **Add a CENTER cluster to `frontend/components/workspace/workspace-toolbar.tsx`** containing, in the MIDDLE of the title bar: the `Workspace` label, the `default layout` chip (keep its current pill style), the **lock toggle** (§3), and a simple **`+` icon button** (lucide `Plus`) that calls `useWorkspaceStore().openPicker` (replacing the verbose "+ Add widget · or type /add" text — just a `+`).
- Restructure the toolbar into three zones so the cluster is genuinely centered: left = brand + broker chip + paper badge; center = the Workspace cluster (horizontally centered, e.g. absolutely positioned `left-1/2 -translate-x-1/2` or a 3-section flex); right = portfolio stats + ThemeSwitcher + clock. Keep it responsive — it's fine to hide the center text labels on narrow widths but keep the `+` and lock usable; don't let zones overlap/wrap raggedly.

## 5. Console: don't auto-open on click — only expand when typing

In `frontend/components/workspace/command-console.tsx`:
- The bottom input bar must remain clickable/focusable while COLLAPSED (you can click and start typing) — but focusing/clicking it must NOT auto-expand the panel.
- Remove the `setOpen(true)` from the input's `onFocus` (keep `setFocused(true)`), and remove `setOpen(true)` from `focusInput` (keep the `requestAnimationFrame(...).focus()`).
- Expand the panel (`setOpen(true)`) when the user actually starts typing: in `onChange`, if the next value is non-empty and the panel is closed, open it. (The `/`-command submit path already calls `setOpen(true)` — keep that.)
- Keep: ⌘K toggle (still opens+focuses), Esc collapse, click-away collapse, the blinking block cursor, and the `/` autocomplete.

---

## Constraints & verification
- Installed deps only (lucide-react, motion, dropdown/popover primitives). Carbon `var(--...)` tokens; `font-sans`/`font-mono`.
- Don't break: channel→chart linking (config still flows via `updateWidgetConfig`), the watchlist list selector, chat auto-add/highlight, terminal command routing.
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + ~6 unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed, how you verified each of the 5 items, decisions/trade-offs, anything incomplete with the blocker.
