# Phase 6 — Copilot as a Right Sidebar (Batch 7)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.
**Run AFTER Batch 6 commits** — overlaps `workspace-grid.tsx`, `command-console.tsx`, `use-workspace-store.ts`.

Big change of direction: the chat copilot is NO LONGER a grid widget. It becomes a
dedicated **right sidebar** with its OWN chat input. The bottom command line goes
**commands-only**. One copilot, one conversation.

---

## Decisions (from the user)
- Bottom command line = **commands only** (`/...`). No plain-text→chat routing anymore.
- **One** copilot, one shared conversation.
- Copilot = a **right sidebar that slides in**. When expanded it **squishes the grid** to the left. **Clicking off it collapses it to a thin rail.** (Fallback if in-flow squish causes layout issues: render it as an absolute foreground overlay instead — implement the squish version first.)

## 0. Current state (read first)
- `frontend/app/workspace/page.tsx` — inside `WorkspaceChatProviders`, renders `<WorkspaceGrid/>` + `<CommandConsole/>` in a padded (`paddingBottom: consoleHeight`) `flex flex-col` container.
- `frontend/components/workspace/workspace-chat.tsx` — exports `WorkspaceChatProviders` (runtime/providers) and **`WorkspaceThread`** = full `<Thread/>` WITH composer + `TradingWelcome`/`TradingComposerControls`/`ChatSlashCommands`. **Reuse `WorkspaceThread` for the sidebar.**
- `frontend/components/workspace/widgets/chat-widget.tsx` — the messages-only widget (to be REMOVED).
- `frontend/lib/workspace/widget-registry.tsx` — has a `chat` widget entry (to be REMOVED).
- `frontend/lib/workspace/use-workspace-store.ts` — has `ensureChatWidget`, `highlightInstanceId`, `setHighlight` (added for chat auto-add; REMOVE these — see §4).
- `frontend/components/workspace/command-console.tsx` — terminal; currently routes plain text → `aui.thread().composer().send()` + `ensureChatWidget()` + `sent ›` scrollback (REMOVE that path — see §3).
- `frontend/components/workspace/workspace-grid.tsx` — measures grid width via a window-resize listener on `#workspace-grid-root`; has the chat highlight pulse ring (remove the ring).

## 1. Copilot sidebar component
Create `frontend/components/workspace/copilot-sidebar.tsx`:
- A right-side panel, full height of the grid area (it sits above the command console which stays absolute-bottom).
- **Expanded** (~360–400px wide): renders `WorkspaceThread` (the full chat, with its own input/composer) under a slim header (e.g. "Copilot" + a collapse chevron). On-theme carbon styling, left border.
- **Collapsed**: a **thin rail** (~40–48px) — a clickable vertical strip with a chat icon (lucide `MessageSquare`) and/or vertical "Copilot" label; clicking it expands.
- Animate the width transition with `motion` (`motion/react`), matching the console's spring feel.
- **Click-away collapses to the rail:** a document `pointerdown` listener — if the panel is expanded and the click is outside the sidebar root ref, collapse to the rail. Don't collapse when interacting inside (messages/composer).

## 2. Sidebar state store
Create `frontend/lib/workspace/use-copilot-store.ts` (zustand): `{ open: boolean; setOpen; toggle }`, persisted to its own localStorage key (e.g. `hmt-copilot-open`), hydrated on load. Default **collapsed** (rail).

## 3. Layout: squish the grid (page.tsx) + command line commands-only
- In `page.tsx`, inside `WorkspaceChatProviders`, wrap the grid + sidebar in a **horizontal flex row**: `<div class="flex min-h-0 flex-1"> <WorkspaceGrid (flex-1 min-w-0)/> <CopilotSidebar/> </div>`, and keep `<CommandConsole/>` (absolute bottom) as a sibling. Keep the outer `paddingBottom: consoleHeight`. When the sidebar expands, the grid (flex-1) shrinks — i.e. squish.
- **Grid must re-measure on sidebar toggle:** the window-resize listener won't fire when the sidebar changes width. In `workspace-grid.tsx`, replace/augment the width measurement with a **`ResizeObserver`** on `#workspace-grid-root` (observe its box and update `width`) so RGL reflows when the sidebar opens/closes.
- **Command console (commands-only):** remove the plain-text→chat branch in `submitLine` — delete the `aui.thread().composer()...send()`, the `ensureChatWidget()` call + toast, and the `sent ›` scrollback. Non-`/` input on Enter should just show a subtle one-line scrollback hint (e.g. `chat lives in the Copilot →`) and clear — it must NOT send to chat. Remove now-unused imports (`useAui`, `ensureChatWidget`, the toast if unused). Keep ALL terminal behavior: `/` command routing + scrollback, blinking cursor, `/` autocomplete, and the Batch-6 "open panel only when typing" behavior.

## 4. Remove the chat widget
- Delete `frontend/components/workspace/widgets/chat-widget.tsx`.
- Remove the `chat` entry from `widget-registry.tsx` (and any import).
- Remove `ensureChatWidget` from `use-workspace-store.ts`. If `highlightInstanceId`/`setHighlight` become unused after this, remove them too AND remove the corresponding highlight pulse ring + scroll-into-view effect in `workspace-grid.tsx` (they existed only for chat auto-add). Verify nothing else references them.
- The `messagesOnly` prop on `Thread` (`assistant-ui/thread.tsx`) can stay (harmless; legacy `/trading` uses the default). The sidebar uses `WorkspaceThread` (full composer).

## Constraints & verification
- Installed deps only (`motion`, lucide-react, `@assistant-ui/*`). Carbon `var(--...)` tokens; `font-sans`/`font-mono`.
- The sidebar must be inside `WorkspaceChatProviders` (runtime scope) — it already wraps the whole workspace.
- Don't break: terminal `/` commands (incl. `/clear` new-thread), the grid/channels/watchlist, the toolbar, `/trading` page.
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + ~6 unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed (incl. deletions), how you verified each section (esp. sidebar expand/collapse + grid squish reflow, and that the command line no longer sends chat), decisions/trade-offs, anything incomplete with the blocker.
