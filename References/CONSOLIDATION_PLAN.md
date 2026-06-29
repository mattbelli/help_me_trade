# Workspace Consolidation — the plan (2026-06-29, founder-agreed)

*Companion to `CONSOLIDATION_RECON.md` (current-state map). Goal: collapse the OLD page-nav UI into the
workspace/widget paradigm. Workspace is the UI going forward; everything becomes a widget.*

## The vision (founder)
- **Everything is a widget.** Old routes (Automations, Performance, Research, Rules, Memories, Settings, the
  notifications inbox) get reborn as widgets, not separate pages.
- **Multiple savable, hot-swappable workspace PAGES** (cap ~5), switched by keybind — different pages hold
  different watchlists / charts / widget setups.
- **Two conceptual surfaces, both just pages:** a **"Configure your AI"** setup page (import trades, goals,
  connect accounts, reports/alerts, settings) + the **live canvas** (view + execute).
- **Ship editable defaults** — a default Setup page + default Canvas page; user can start from scratch, delete,
  rebuild.

## Locked decisions
- **Setup surface = config widgets on a default page** (founder pick). ONE paradigm — the setup surface is a
  default workspace page made of config widgets; deep flows (onboarding, connect-account, trade-import) open as
  modals launched from their widget. Fully editable/deletable like any page.
- **Start with the Notifications inbox widget** (founder pick) — fixes the immediate pain (can't read/click rich
  notifications) AND is the first "componentize into a widget" example.
- **Terminology (CONFIRMED 2026-06-29):** **"pages"** = the top-level swappable saved layouts (the multi-page
  concept). **"channels"** = the existing PHASE_6 symbol-linking color groups, which live WITHIN a page — each page
  can have its own channel setup (different watchlist/chart symbol-link groups per page). Pages contain channels.

## Current-state facts that shape the plan (from recon)
- Workspace shell + 12 widgets + console + copilot already built (PHASE_6). Layout SAVES (server `workspace_state`
  key `layout` + localStorage) but there is **ONE layout slot — no multi-page concept**.
- Notifications: bell truncates title+body, click = mark-read only, no detail/inbox. Data fully available
  (`GET /api/notifications` + the 1c store/stream). A `notifications` widget is pure UI — plumbing is done.
- Old routes: Overview/memories/agents/charts ≈ already covered by widgets; trading/automations partially;
  **Performance, Research, Rules NOT widgetized** (the real componentization work).
- Setup: a 7-tab SettingsDialog modal exists (Brokers/account-connect, Execution/Risk, Strategies, Reports, AI,
  Research, Import/trade-import). Net-new for "configure your AI": onboarding/first-run, agent charter/persona/name,
  goals/trading-style free-text, a memory settings view, notification routing.

## The roadmap (5 stages — each usable on its own)
**Stage 1 — Notifications inbox widget (START HERE).** A `notifications` widget: scrollable inbox, each entry
expandable to FULL text, mark-read / mark-all / dismiss, filter (all / unread / by kind), severity styling, and
kind-appropriate inline actions where trivial (view report, etc.). Bell stays as the quick-glance + unread badge;
the widget is the full inbox. Default-place it on the grid (+ available via `/add`). This also delivers the
"clickable with detail" that Phase 1c deferred. **No backend change — uses the existing store + endpoints.**

**Stage 2 — Multi-page foundation (the container).** Migrate the single layout → named pages (cap 5): store
(`layout[]` → `pages: {id → layout[]}` + `activePageId`), backend (additive `workspace_state` keys per page —
non-breaking; wrap the current layout as default page "canvas" on first load), a page-switcher in the toolbar +
hot-swap keybind. Wire the unfinished PHASE_6_TITLEBAR center cluster (label / + / lock) here. Everything else
drops into this.

**Stage 3 — Componentize the old pages → widgets.** Build the NOT-yet-widgetized surfaces as widgets: Performance
(analytics), Research (input + streaming output), Rules (rule editor; scans widget already shows fires), + gaps
(price-alert creation, automations overview entry-point). Redirect the already-covered routes (Overview, memories,
agents, charts) to the workspace.

**Stage 4 — The "Configure your AI" default page (setup surface).** A default page of config widgets: settings
(reuse the 7 SettingsDialog tabs as widgets/modals), account connection, trade import, reports/alerts setup — PLUS
the net-new pieces (first-run onboarding, agent charter/name, goals/trading-style, memory settings, notification
routing). Shipped as an editable default page.

**Stage 5 — Deprecate old routes + polish.** Remove the old page-nav routes / AppShell once everything's
widgetized; finalize the two editable default pages (Setup + Canvas); polish.

## Open decisions (resolve as we reach them — none block Stage 1)
1. "channels" vs "pages" terminology (Stage 2) — proposed above.
2. Old routes: keep as fallback during Stage 3, delete in Stage 5 (proposed).
3. Setup page exact widget set + the net-new onboarding/charter scope (Stage 4).

## Status note
Notifications backend+frontend (Phase 1b+1c) is built, deployed live on serv, and verified (a real `report_ready`
notification fired end-to-end). Still **uncommitted** (per protocol, awaiting founder's "commit" word). The bell
being too cramped for rich content is NOT a 1b/1c defect — it's exactly what Stage 1 (the inbox widget) fixes.
