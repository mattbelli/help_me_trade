# Session Handoff — continue Phase 6 in a fresh Claude Code session

> **SUPERSEDED BY `HANDOFF.md`** (this was the one-off Phase-6 bootstrap, 2026-06-18).

Read this first, then `References/BUILD.md` (roadmap) and `References/IDEA_BANK.md`
(vision + every locked decision). This file captures the working context that only
lived in the previous chat session.

## Your role / workflow
- **You are the orchestrator/brain**: plan, write briefs, review, make architecture
  calls. You do NOT do most of the typing.
- **Composer 2.5 is the coder**, dispatched via the bridge in `tools/composer-bridge/`.
  Default to Composer for coding; hand-roll yourself only for genuinely hard /
  algorithmic / cross-cutting work, or when a quick surgical edit is faster.
- **Review before commit.** The user reviews changes live in the browser (their dev
  server is always running). Do NOT commit until they confirm the feel. After a
  Composer run, review the actual `git diff` (never trust the self-report), lint,
  then commit only with their OK.
- Ask the user (AskUserQuestion) sparingly — only for genuine decisions/forks, not
  execution choices.

## How to dispatch Composer
```
node tools/composer-bridge/dispatch.mjs --brief tools/composer-bridge/briefs/<x>.md
# runs Composer 2.5 against the repo working tree, streams to stdout
```
- Auth: `CURSOR_API_KEY` is in `tools/composer-bridge/.env` (gitignored, already set).
- Runs edit the working tree IN PLACE with no isolation → dispatch SEQUENTIALLY,
  never two at once (they'd collide). Review + commit between.
- Run it backgrounded and tail `tools/composer-bridge/last-run.log` so the user can
  watch. On error it prints a clean message (doesn't dump the SDK bundle).
- The bridge is host-agnostic: works the same in terminal Claude Code, and later
  server-side over SSH (per IDEA_BANK §7.6/§7.9).

## Where we are
- Branch: **`feat/phase6-workspace-shell`** (Phase 6 = the workspace shell pivot:
  `/workspace` route, carbon/ember/paper themes, command console, draggable widget
  grid, channels, copilot dock). Many commits already landed (see `git log`).
- The bridge itself is committed (`tools/composer-bridge/`).

## UNCOMMITTED work in the tree (pending the user's review)
All of these are done + lint/typecheck-clean, awaiting the user's browser sign-off
before commit. Suggested grouping when they approve:
- **Grid scroll + drag** (`workspace-grid.tsx`, `grid-metrics.ts`): horizontal scroll
  instead of overlap on narrow widths (fixed col px width, floor 24 cols, measure the
  SCROLL VIEWPORT `#workspace-grid-scroll` not the inner root); and **swap→push-clear
  drag** (`allowOverlap` + `findFreeSlot`: dragged stays where dropped, overlapped
  widgets relocate fully clear; drop on empty = stays).
- **Console polish** (`command-console.tsx`, `use-console-store.ts`): full-width
  bottom toolbar, content-width black input (16–32rem, 14px), squared (`rounded-none`),
  56px bar height.
- `tools/composer-bridge/.gitignore` (added `briefs/`, `*.log`).
- Untracked: `References/*` briefs + `IDEA_BANK.md` (these are committed on `main`;
  pulled into this branch's tree only as reference — do not re-commit them here).

## Pending tasks (user's queue, in order)
1. **Scroll with NO scrollbars + a blurred edge cue** signaling off-screen widgets
   (hide native scrollbars, fade/mask the grid edges when content overflows).
2. **Alt-drag to duplicate** a widget instance (good Composer dispatch).
3. **Shift-click multi-select + group move** — HARD in react-grid-layout (one item at
   a time); needs a custom multi-drag layer. Do last, likely hand-rolled.
4. Later: chart click → price level → alert (above/below/crossing/recurring) + inline
   add in price-alerts widget (needs backend: alert model only has above/below today).
5. Deferred by user: widget-group "lock to a window"; multi-window (Tauri phase).

## Gotchas learned (don't re-discover these)
- This project's dropdown menus are **base-ui** (`@base-ui/react/menu`): `Menu.Item`
  fires **`onClick`**, NOT `onSelect` (silent no-op). Cost two failed batches.
- `react-grid-layout/legacy` v2.2.3 `EventCallback` types `oldItem`/`newItem` as
  `LayoutItem | null` — type drag handlers `Layout[number] | null` and guard.
- Themes: carbon is the genuine default, defined in `:root` in `globals.css` (accents
  live there so they exist before any `data-theme` is set). Inter + JetBrains Mono.
- Grid free-placement uses `allowOverlap` + resolve-on-drop, NOT
  `preventCollision={false}` (that flings widgets off-screen).

## Key pointers
- `References/BUILD.md` — roadmap/source of truth. `References/IDEA_BANK.md` — vision +
  decisions. `References/current orchestrator convo.md` — full prior-session export
  (skim only if needed). `tools/composer-bridge/README.md` — bridge details.
