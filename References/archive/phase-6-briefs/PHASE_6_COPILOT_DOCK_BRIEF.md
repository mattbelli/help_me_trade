# Phase 6 — Batch 11: Copilot bottom-right dock + dark input affordances

Two focused, frontend-only UI changes. No backend, no API, no new deps. Hot-reloads (no restart needed).

## Context

- Theme tokens (carbon default) live in `frontend/app/globals.css`. Use CSS vars, never raw hex:
  - `--bg-inset: #0e1013` → **the near-black "dark area"** the user wants. Use this for the copilot message window and the watchlist add input.
  - `--panel` / `--bg-raised: #181b21`, `--bg-hover: #1e2228`, `--border: #262b33`, `--border-strong`, `--fg-dim`, `--fg-faint`, `--c-accent`, `--panel-shadow`.
- Chat UI component to reuse as-is: `WorkspaceThread` from `frontend/components/workspace/workspace-chat.tsx`.
- Layout host: `frontend/app/workspace/page.tsx`. The command console (`command-console.tsx`) is `absolute bottom-3 left-3` — the copilot collapsed dock goes **bottom-right**, so they must not overlap.

---

## Task A — Move the Copilot from a right-edge sidebar to a bottom-right dock

Current: `frontend/components/workspace/copilot-sidebar.tsx` is a full-height right sidebar (44px rail collapsed / 380px expanded), toggled via `useCopilotStore` (`open`, `pinned`).

Rebuild the visual behavior to this spec:

1. **Collapsed (default):** a **compact pill anchored to the bottom-right corner** (e.g. `absolute bottom-3 right-3 z-40`), just wide enough for a chat icon + "Copilot" label. Clicking it opens the panel. It should look like a small floating button/bar, consistent with the console's translucent rounded style.

2. **Expanded:** the panel **grows upward along the right side of the screen**, anchored at the bottom-right. Footprint similar to the old sidebar: width ~`380px` (adjustable, see #5), height up to most of the viewport (e.g. `max-h-[calc(100svh-6rem)]`, with the thread scrolling inside). Rounded, bordered, `--panel-shadow`.
   - A small header row (label "Copilot", pin button, collapse/close button).
   - **The message/thread window is only a portion of the panel and uses the near-black `--bg-inset` background.** Wrap `<WorkspaceThread />` in a container with `bg-[var(--bg-inset)]`.

3. **Floats by default:** expanded panel is an **overlay** (absolute, `z-40+`) that sits over the widgets and does **not** reflow the grid. Clicking outside collapses it back to the pill (keep the existing outside-pointerdown logic, but only when not pinned).

4. **Pin toggle:** a pin button in the header. When **pinned**, the panel becomes **docked/permanent** and the grid is squished to make room (reserve right space equal to the current copilot width). Implement docking by having `page.tsx` apply right padding to the grid container when `pinned && open` (e.g. `paddingRight: copilotWidth`). When unpinned it returns to floating overlay. Use `PinIcon` / `PinOffIcon` (lucide), already imported.

5. **Adjustable width:** add a **left-edge drag handle** on the expanded panel to resize its width. Clamp to a sensible range (e.g. `320`–`640px`). Persist width in `useCopilotStore` + localStorage (add `width`, `setWidth`, key `hmt-copilot-width`), alongside existing `open`/`pinned` persistence in `frontend/lib/workspace/use-copilot-store.ts`. Hydrate it in `hydrate()`.

6. Keep all chat functionality via `WorkspaceThread`. Do not change chat logic.

**Files:** `frontend/components/workspace/copilot-sidebar.tsx` (rewrite layout/anim; consider renaming the exported component is NOT required — keep `CopilotSidebar` export so `page.tsx` import stays valid), `frontend/lib/workspace/use-copilot-store.ts` (add width), `frontend/app/workspace/page.tsx` (docked padding driven by `pinned && open` + width instead of the current flex placement).

**page.tsx note:** today the copilot is a sibling of `WorkspaceGrid` inside `relative flex`. Since it's now an absolute bottom-right overlay in both collapsed and floating states, place `<CopilotSidebar />` as an absolute overlay in the same `relative` container, and apply `paddingRight` to the grid wrapper only when docked (`pinned && open`). Animate the padding so docking feels smooth.

---

## Task B — Make the watchlist add-row read as an obviously-typable dark input

File: `frontend/components/workspace/widgets/watchlist-widget.tsx`, component `WatchlistAddRow` (~lines 117–177).

Current problem: the add input is transparent with a hidden placeholder, so users don't realize they can type a ticker there.

Change the input to look like a real, dark input affordance:
- Give the input (or its row) a **near-black `--bg-inset` background** with a subtle border (`--border`), small rounded corners, so it visually reads as an input field distinct from symbol rows.
- **Always-visible placeholder** like `+ Add symbol` (use `placeholder:text-[var(--fg-faint)]`, remove the `placeholder:text-transparent` trick).
- Add a faint leading `+` glyph inside the field (in the gutter `<span/>` slot or as a prefix) to signal "add".
- Keep all existing behavior: debounced `searchSymbols` suggestions dropdown, Enter to add, Escape to reset, uppercase, the `addSymbol`/`updateWatchlist` flow. Do not change data logic.
- Keep it inside the scroll area below the last symbol row (as it is now).

---

## Out of scope (do NOT touch this batch)
- Channel selector bug, SW resize-handle bug (separate pending items).
- Command console (leave as-is).
- Alt-drag duplicate / shift-select groups / multi-window (future).

## Acceptance criteria
- `bun run lint` (in `frontend/`) passes for changed files; no new TypeScript errors.
- Copilot: collapsed pill bottom-right → click → panel expands upward on the right, floating over widgets; outside-click collapses; pin docks it and squishes the grid; left-edge drag resizes width; width/open/pinned persist across reload; thread window is near-black.
- Watchlist: the add row clearly looks like a typable dark input with a visible `+ Add symbol` placeholder; adding/searching still works.
- Use CSS theme vars throughout (no raw hex).
