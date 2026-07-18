# Phase 6 — Terminal Command Line + Chat-as-Widget (Batch 4)

Branch: `feat/phase6-workspace-shell`. Commit once at the end; do NOT push.

Goal: make the bottom console a true **terminal-style command line** (the single
place you type, with a blinking block cursor and no chat-UI chrome / no help text),
and move the conversation into a **chat widget** on the canvas. The bottom line and
the chat widget share ONE assistant-ui conversation: `/commands` run in the terminal
scrollback; plain text is sent to the conversation and renders in the chat widget.

This is decision-complete; verify the exact assistant-ui APIs against the installed
`@assistant-ui/*` packages (do NOT guess method names — inspect the files below).

---

## 0. Current architecture (read first)

- `frontend/app/workspace/page.tsx` — mounts `<WorkspaceToolbar/>`, `<WorkspaceGrid/>`, and wraps ONLY `<CommandConsole/>` in `<WorkspaceChatProviders>`. The grid has `paddingBottom: consoleHeight`.
- `frontend/components/workspace/workspace-chat.tsx` — `WorkspaceChatProviders` = `LearnedMemoriesProvider > ChatSettingsProvider > AssistantRuntimeProvider(runtime=useTradingChatRuntime())`. Also exports `WorkspaceThread` (renders `<Thread .../>`).
- `frontend/lib/use-trading-chat-runtime.ts` — builds the AI-SDK runtime (`/api/chat`), remote thread-list adapter. `useAui()` / `useAuiState()` from `@assistant-ui/store` expose the runtime; the palette already calls `aui.threads().switchToNewThread()`.
- `frontend/components/assistant-ui/thread.tsx` — the `Thread` component (message list + composer + welcome + suggestions). INSPECT it to learn how to render a **messages-only** view (composer hidden) and whether it exposes `ThreadPrimitive` sub-parts.
- `frontend/components/workspace/command-console.tsx` — bottom bar. Has ASK/CMD `mode` toggle; when open shows `WorkspaceThread` (ask) or `WorkspaceCmdPalette` (cmd) or a "/" hint. Click-away collapse + ⌘K toggle already exist (keep them).
- `frontend/components/workspace/workspace-cmd-palette.tsx` — cmdk palette; command handlers: `positions` (toast stub), `add` (openPicker), `theme` (cycle), `clear` (new thread), `/watchlist add|remove SYM` (regex on Enter). Reuse this command logic.
- `frontend/lib/workspace/use-console-store.ts` — `open,height,mode,paletteOpen,collapsedHeight,toggle,setOpen,setHeight,toggleMode,openPalette,...`.
- `frontend/lib/workspace/use-workspace-store.ts` — `addWidget(type)` (multi-instance aware, singletons block dup), `updateWidgetConfig`, `findPlacement`, `removeWidget`, layout persistence.
- `frontend/lib/workspace/widget-registry.tsx` — `WIDGET_REGISTRY`, `WidgetProps`, `multiInstance`/`channelAware` flags.

Do NOT touch the legacy `/trading` chat under `frontend/components/agents/*` or the `/trading` page.

---

## 1. Wrap the whole workspace in the chat providers

In `workspace/page.tsx`, move `<WorkspaceChatProviders>` so it wraps BOTH `<WorkspaceGrid/>` and `<CommandConsole/>` (it currently wraps only the console). Keep the `paddingBottom: consoleHeight` layout. This gives the chat widget (inside the grid) access to the same runtime/`useAui()` as the bottom console.

---

## 2. Chat widget (new) — shares the conversation, no composer

`frontend/components/workspace/widgets/chat-widget.tsx` (accepts `WidgetProps`, can ignore them):
- Render the assistant-ui conversation **message list only** — the built-in composer must be HIDDEN, because input is unified at the bottom console. Inspect `assistant-ui/thread.tsx`: prefer composing `ThreadPrimitive.Messages` (+ welcome/empty state) directly, or render `<Thread>` with the composer hidden (a prop or wrapping + CSS). Keep tool UIs (`ProposeOrderToolUI`, `StartResearchToolUI`) working — they're already mounted by the provider.
- On-theme carbon styling, scrollable, fills the panel.

Register it in `widget-registry.tsx`:
- `{ id:"chat", title:"copilot", component: ChatWidget, multiInstance:false, channelAware:false, defaultSize:{ w:8, h:8, minW:5, minH:5 } }` (24-col grid).

---

## 3. Terminal-style bottom console

Rework `command-console.tsx` + `use-console-store.ts`:
- **Remove the ASK/CMD mode toggle** and the `mode` concept (drop `mode`/`toggleMode` usage; clean the store). Remove the help/instruction text ("Press / for commands", "Ask your copilot…").
- The bottom bar becomes a single terminal prompt: a `‹prefix›` (e.g. `›`) + a text `<input>` that fills the width. Implement a **blinking block cursor**: hide the native caret (`caret-color: transparent`) and render a blinking block glyph (e.g. `▋`) right after the typed text while focused (end-of-text position is acceptable). Use `var(--c-command)` for the prompt accent. `font-mono`.
- **Submit (Enter) routing:**
  - Text starting with `/` → **command**: echo the typed line into a terminal **scrollback**, execute via the shared command runner (section 5), append the result/output line to scrollback, and ensure the console is `open` so scrollback is visible.
  - Otherwise → **chat**: send the text to the shared conversation via the assistant-ui runtime (append a user message — verify the API, e.g. `aui.thread().composer.setText(text); aui.thread().composer.send()` OR `runtime.thread.append({ role:"user", content:[{type:"text", text}] })`). Then ensure a chat widget exists (section 4). Clear the input. Do not force-expand the console for chat (the reply shows in the widget); optionally show a brief "sent ›" line in scrollback.
- **Scrollback:** keep a list of entries (in `use-console-store` or local state): `{ id, kind:"input"|"output"|"error", text }`. When `open`, render the scrollback (scrollable, newest at bottom) above the input instead of the old Thread/palette. Keep the resize grip, collapse chevron, click-away collapse (Batch 1), and ⌘K toggle.
- Optional (keep minimal, no big help UI): when the input is exactly `/`, you MAY show a small inline list of available commands (terminal autocomplete), but no persistent instructions.

---

## 4. Auto-add chat widget with VISIBLE placement

When a chat message is sent and no `chat` widget instance exists on the board, auto-add one so the reply is visible — and make sure the user SEES it (never spawn off-screen):
- Add it at the TOP of the board (insert at `y:0`, let vertical compaction push others down) rather than wherever `findPlacement` happens to land.
- Add a transient `highlightInstanceId` to `use-workspace-store` (with a `setHighlight(id)` that auto-clears after ~2.5s). The grid scrolls that widget into view and applies a brief pulse/ring (e.g. `ring-2 ring-[var(--c-command)]` animated) while highlighted.
- Show a workspace toast (existing `useWorkspaceToast`): "Chat added — drag to reposition."
Implement as a store method like `ensureChatWidget()` returning the instance id, used by the console submit path.

---

## 5. Shared command runner

Extract the command logic currently inside `workspace-cmd-palette.tsx`'s `run`/watchlist handlers into a reusable hook/module (e.g. `frontend/lib/workspace/use-workspace-commands.ts`) exposing something like `runCommand(rawInput: string): Promise<{ handled: boolean; output?: string; error?: string }>`. Support the existing commands: `/positions`, `/add`, `/theme [name]`, `/clear`, `/watchlist add SYM`, `/watchlist remove SYM`. The terminal console calls this; unknown commands return an error line like `unknown command: /foo`.
- `/clear`: clears chat thread (`aui.threads().switchToNewThread()`) AND clears the terminal scrollback.
- You may keep the cmdk palette file if convenient, but the primary UX is now typing in the terminal. If you keep it, restyle minimally; otherwise remove it and its references.

---

## Constraints & verification
- Installed deps only (motion, lucide-react, @assistant-ui/*, cmdk). Carbon `var(--...)` tokens; `font-sans`/`font-mono`.
- VERIFY assistant-ui APIs (send/append message, hide composer, new thread) against the installed package versions by reading the source under `frontend/components/assistant-ui/` and `node_modules/@assistant-ui` if needed. Do not invent method names.
- Do NOT break the `/trading` page or legacy agents chat.
- Run in `frontend/`: `npm run typecheck` + `npx eslint <changed files>`; fix everything you introduce (pre-existing `tv-light-chart.tsx:194` + ~6 unrelated pre-existing typecheck errors are NOT yours).

## Report
Branch + commit hash, files changed, how you verified each section (incl. which assistant-ui APIs you used for send/append + hiding the composer), decisions/trade-offs, and anything incomplete with the blocker.
