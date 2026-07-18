# Phase 6 — Implementation Brief (for coding agents)

Decision-complete brief for the **first vertical slice** of the shell pivot. Scope is deliberately narrow: prove the architecture seam end-to-end against live data before building breadth.

**Context to read first (do not skip):**
- Vision + decisions: [`IDEA_BANK.md`](IDEA_BANK.md) (esp. §1 UI, §3 theming, §7.6 deployment, §9 sequencing)
- Roadmap: [`BUILD.md`](BUILD.md) §5 Phase 6
- Visual spec (reference only — do **not** port vanilla JS): [`hermes-ui-v2.html`](hermes-ui-v2.html)
- Existing code patterns to match:
  - `frontend/app/globals.css` — Tailwind v4 `@theme inline` + OKLCH token system (`:root` / `.dark`)
  - `frontend/lib/api.ts` — API client (`fetchSnapshot`, `fetchBroker`, `Snapshot`, `BrokerInfo` types)
  - `frontend/components/providers/snapshot-provider.tsx` — `useSnapshot()` live data
  - `frontend/lib/use-trading-chat-runtime.ts` — `useTradingChatRuntime()` (assistant-ui)
  - `frontend/components/assistant-ui/thread.tsx` — existing Thread UI
  - `frontend/components/layout/app-shell.tsx` — current shell (do not modify yet)

---

## Locked decisions (do not re-litigate)

1. **Build behind a new route, not by replacing the shell.** New route **`/workspace`**. The existing pages and `AppShell` keep working untouched. We flip the default to the new shell only after the slice is solid.
2. **Theme = extend the existing token system.** Add new themes as `[data-theme="carbon|ember|paper"]` blocks in `globals.css` reusing the **same token variable names** (so all shadcn components inherit). Drive switching with **`next-themes`** (`themes: ['carbon','ember','paper']`, plus existing light/dark). **Do not invent new token names**; map the matte palette onto the existing ones. Carbon = default.
3. **Widget grid = `react-grid-layout`** (already installed, CSS already imported). Draggable + resizable.
4. **Layout persistence = behind an interface.** Implement a `zustand` store (`useWorkspaceStore`) that persists the layout to **`localStorage`** for now, behind a small `LayoutPersistence` interface so a backend/file store can replace it later (Tier 0 seam — do not hardcode localStorage calls in components).
5. **Console "ask" path REUSES `useTradingChatRuntime` + the existing Thread.** Do **not** build a second chat. Slash (`/`) commands are handled client-side (use `cmdk`) and only fall through to the chat runtime for natural language.
6. **Console state = `zustand`** (`useConsoleStore`: `open`, `height`, `mode: 'ask'|'cmd'`). Animate with `motion`.
7. **All data via the existing API client / `useSnapshot()`.** No new fetching patterns; treat the backend as the headless core (§7.6).

## Dependencies — already installed (DO NOT add new ones)
`react-grid-layout`, `react-resizable`, `cmdk`, `zustand`, `motion`, `next-themes`, `@assistant-ui/*`, `lightweight-charts`, `recharts`, `lucide-react`. Tailwind v4.

---

## First slice — deliverables & acceptance criteria

### S1. Theme tokens + switcher
- Add `carbon` (matte dark, default), `ember` (warm dark), `paper` (light) token blocks in `globals.css` reusing existing var names. Aim for the Warp/Ghostty matte feel.
- Wire `next-themes` to switch among them; add a theme control in the workspace toolbar.
- **Accept:** switching themes recolors the whole `/workspace` (and existing shadcn components) with no hardcoded colors; refresh persists the choice.

### S2. `/workspace` route + top toolbar
- New `app/workspace/page.tsx` (client) rendering a top toolbar: brand, broker/connection chip + `paper` pill (from `useSnapshot()`/`brokerInfo`), account summary (portfolio, BP, day P&L), ET clock.
- **Accept:** toolbar shows **live** values from the snapshot provider; updates on the 20s refresh.

### S3. Bottom command console (the core seam)
- Persistent bottom bar; expand/collapse + drag-resize; `ASK`/`CMD` toggle; `Cmd/Ctrl+K` toggles, `/` summons with `cmdk` palette.
- **ASK** → renders the existing assistant-ui Thread via `useTradingChatRuntime` inside the drawer.
- **CMD** → `cmdk` palette with a few stub commands (`/positions`, `/add`, `/theme`, `/clear`); `/theme` and `/clear` work, others can no-op with a toast for now.
- **Accept:** you can ask a question and get a streamed answer from the real `/api/chat`; the palette opens on `/` and filters; console state survives route re-render.

### S4. Two live widgets in a `react-grid-layout` grid
- Widgets: **Account summary tiles** + **Positions table**, both bound to `useSnapshot()`.
- Grid supports drag-reorder + resize; layout persists via `useWorkspaceStore` → localStorage (behind the interface from decision #4).
- **Accept:** both widgets show live snapshot data; rearranging/resizing persists across reload.

---

## Explicitly OUT of scope for this slice (defer)
- Full widget catalog, add-widget picker breadth (stub `/add` only)
- Backend layout/theme persistence (localStorage is fine now; keep the interface)
- Onboarding/charter flow, notifications (top-down), pop-out/multi-window
- Tauri shell, gateway/auth changes, Alpaca MCP wiring
- Replacing the default home route / removing the old shell

## Tier 0 seams to honor (cheap, do them now)
- Layout/theme persistence behind an interface (swap-able for a file/backend later).
- Console talks to the backend only through the existing API client (headless-core posture).
- No new order/execution code of any kind (read-only).

---

## Suggested branch
`feat/phase6-workspace-shell` off the current main. Keep the slice reviewable; open a PR when S1–S4 meet acceptance.
