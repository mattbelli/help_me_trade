# UI_AUDIT.md — desktop web cockpit diagnostic (2026-07-05)

Method: drove the running production build (localhost:3001) headless via playwright-core
(chromium-1228) at 1720×970 and 1280×800, across all 4 built-in themes + the custom-theme
editor, plus a broad source read of `frontend/components/**`, `app/globals.css`, and
`lib/workspace/**`. Screenshots (read + judged visually) live in
`/home/mbelli/.claude/jobs/2404af04/tmp/ui-audit/`. Every visual claim below is backed by a
named PNG; source claims by `file:line`. Items I could not confirm are marked UNVERIFIED.

Scope note: this is a **desktop-web** audit. Phone (native app) is explicitly out of scope.

---

## Executive summary — the 5 most worth fixing

1. **Two entire UIs are live at once.** The terminal workspace *and* a complete old sidebar app
   (`/agents /performance /automations /trading /rules /memories /charts`) both render, in two
   different design languages — the legacy one even ships a manual Buy/Sell `/trading` terminal
   that contradicts the "cockpit, not a trading terminal" mission.
2. **Empty states are unowned.** Empty pages (Page 4/5) are a blank dotted canvas with zero
   call-to-action, and ~20 widgets each hand-roll a divergent empty/loading state (padding, size,
   alignment, copy tone all differ). One shared `WidgetEmpty`/`WidgetLoading` sweep fixes all.
3. **Ticker-link house rule is broken in 2–3 widgets.** The orders blotter and price-alerts
   render tickers as plain text (not click-to-chart), violating "every rendered ticker is
   click-to-focus-the-chart."
4. **The research feature has two different looks** — the `/research` page (sans-serif generic
   form) vs the research widget (mono terminal). Same feature, two visual languages; pick one.
5. **The widget picker lists two entries both titled "performance"** (and sector-radar's polar
   labels collide + its P&L column clips) — small but user-facing confusion in daily surfaces.

Finding counts: **BROKEN 1 · JANKY 5 · INCONSISTENT 6 · POLISH 6**

---

## BROKEN

### B1 — Legacy `/trading` chart renders blank
- Where: `/trading` (old shell), chart pane. Evidence: `legacy-trading.png` — chart area is
  empty ("Hover chart for OHLCV" placeholder, no candles), and an "Order ticket — order placement
  not built yet" Buy/Sell box sits beside it.
- Why it matters: it's a live, linkable URL that shows a broken chart + a manual order ticket the
  mission says shouldn't exist. Reachable via the still-live left sidebar and any deep link that
  still points here.
- Fix: fold into the retire-legacy-UI sweep (S1) — redirect `/trading` → `/` like `/trades`
  already does (`app/trades/page.tsx` redirects; `app/trading/page.tsx` does not). **S**

---

## JANKY

### J1 — Whole old sidebar UI is still reachable (parallel app)
- Where: `/agents /performance /automations /trading /rules /memories /charts`. Evidence:
  `legacy-automations.png`, `legacy-agents.png`, `legacy-performance.png`, `legacy-trading.png` —
  all render the old shell (left "help me trade" sidebar nav + right "Side panel" watchlist),
  a sans-serif/rounded-card language distinct from the mono terminal workspace.
- Only `/`, `/board`, `/research` are exposed in the Pages menu (`layout/pages-menu.tsx:9`), but
  the other 7 routes still mount full pages (`app/*/page.tsx`). Per the workspace-first direction
  these should die once parity + deep-link repointing is done.
- Fix: audit inbound links, then redirect the 7 legacy routes to their workspace equivalents (or
  404). **M**

### J2 — Empty workspace pages have no empty state
- Where: Page 4 & Page 5 (default layout ships 5 pages, 2 empty). Evidence: `page4__carbon.png`,
  `page5__carbon.png` — a fully blank dot-grid canvas, no "Add a widget to get started" CTA. The
  only add affordance (`command-console.tsx:355` Plus) lives in the console, not on the canvas.
- Fix: render a centered empty-canvas hint with an "Add widget" button when a page's layout is
  empty. **S**

### J3 — Sector-radar polar labels collide + P&L column clips
- Where: `sector-radar` widget. Evidence: `page3__carbon.png` — 27 sector labels overprint each
  other around the small radar (top cluster "Automobiles / Unclassified / Financial Services" is
  unreadable); the right-hand P&L values are cut off at the widget edge (`+$95,75…`, `+$77,61…`).
- Fix: cap/rotate radar labels (top-N + "others"), and let the P&L column shrink/format compactly
  so it doesn't overflow. **M**

### J4 — Research page vs research widget diverge
- Where: `/research` page vs the `research` widget. Evidence: `research__carbon__wide.png`
  (sans-serif "New research / Recipe / Topic", generic inputs) vs `page2__carbon.png` (mono
  "RESEARCH / TOPIC / SYMBOL / EFFORT", terminal styling). Same feature, two languages; the page
  also uses "Recipe" while the widget uses no recipe field.
- Fix: converge on the mono terminal language (workspace-first) and share the form component. **M**

### J5 — Widget picker is not discoverable for its full set
- Where: `workspace-widget-picker.tsx:95` (`max-h-72 overflow-y-auto`). Evidence:
  `widget-picker__carbon.png` — the list shows ~5 of 25 widgets with no visible scrollbar/fade
  affordance, so the other 20 look nonexistent. Popover is also pinned high (`pt-[12vh]`, line 57)
  rather than centered.
- Fix: add a scroll fade/indicator (the app already uses bottom-fade elsewhere) and center the
  dialog. **S**

---

## INCONSISTENT

### I1 — Empty-state styling varies widget-to-widget (systemic)
- Where: ~20 widgets, each hand-rolls its empty `<p>`. Evidence (source):
  `positions-widget.tsx:102` (`p-3 text-xs`), `agent-proposals-widget.tsx:31` (no padding),
  `memory-widget.tsx:134` (`p-2 text-xs`), `reports-widget.tsx:213` (`p-3 text-[11px]`),
  `sector-radar-widget.tsx:256` (centered `flex h-full items-center justify-center`).
  Color is consistent (`text-[var(--fg-dim)]`) but padding (`p-2`/`p-3`/none), size (`text-xs`
  vs `text-[11px]`), alignment (top-left vs centered), and tone (terse "No notifications" vs
  helpful "No tracked ideas yet — run a setup scan.") all differ.
- Fix: one `<WidgetEmpty hint?>` component, adopt across widgets. **M** (see S2)

### I2 — Loading-state styling varies + 4 widgets have none
- Where: most widgets show a bare "Loading…" at differing sizes; `chart-widget.tsx`,
  `performance-widget.tsx`, `rules-widget.tsx`, `watchlist-list-selector.tsx` have no
  loading token at all (grep). Evidence: `reports-widget.tsx:213` `text-[11px]` vs others `text-xs`.
- Fix: shared `<WidgetLoading>` (or skeleton) alongside the empty-state sweep. **S**

### I3 — Error states are absent in 8 widgets (silent-empty on fetch fail)
- Where (grep, no error/catch branch): account-summary, agent-proposals, chart, notifications,
  open-orders, positions, performance, rules. On a failed fetch these fall through to the empty
  state, indistinguishable from "genuinely nothing here." UNVERIFIED whether any surface a toast
  instead.
- Fix: standard inline error row in the shared widget shell. **M**

### I4 — Two widgets share the title "performance"
- Where: `widget-registry.tsx:154` (`id: performance`, big analytics) and `:199`
  (`id: performance-stats`, compact bar) both `title: "performance"`. Evidence: grep
  `title: "performance"` returns a duplicate; picker shows two identical rows.
- Fix: rename one (e.g. "performance analytics" vs "performance stats"). **S**

### I5 — Same metric rounds differently across surfaces
- Where: win rate shows `63%` in the performance-stats widget (`page3__carbon.png`) but `62.5%`
  on the legacy `/performance` page (`legacy-performance.png`) for the same 5W/2L sample.
- Fix: one shared formatter / rounding rule. **S**

### I6 — Two design languages (mono terminal vs sans cards)
- Where: workspace + dialogs are mono/terminal; `/research` page and the entire legacy shell are
  sans-serif rounded-card. Evidence: `settings__carbon.png` (mono) vs `research__carbon__wide.png`
  & `legacy-performance.png` (sans). Rolls up into J1/J4 but worth naming as the root pattern.
- Fix: commit to the mono terminal system for all first-class surfaces. **L**

---

## POLISH

### P1 — Ticker-link gaps (house rule)
- Where: `orders-widget.tsx:108/194/278` render `{o.symbol}` as plain text (no ClickableSymbol,
  no row `publish`; only onClick is the tab switcher at `:398`); `price-alerts-widget.tsx:326`
  renders `{alert.symbol}` plain (no publish); `pnl-scatter-widget.tsx:69/204` legend/tooltip
  symbols plain. Evidence: grep + `page3__carbon.png` (orders blotter TSLA rows are colored but
  not links). Note: `rvol-scanner-widget.tsx:151` DOES publish at row level (compliant), and
  positions/watchlist/scans/idea-outcomes/journal/pending-proposal-card all use ClickableSymbol.
- Fix: wrap those tickers in `ClickableSymbol` / add row `publish`. **S**

### P2 — outline-none without a focus indicator (keyboard a11y)
- Where: `watchlist-widget.tsx:170` (ticker-add input) and `watchlist-list-selector.tsx:48`
  (list button) use `outline-none` with hover-only styling and no `focus-visible` replacement, so
  keyboard focus is invisible. (Note: price-alerts inputs at `:188/203/256` correctly pair
  `outline-none` with `focus:border-[var(--c-accent)]` — not a problem.)
- Fix: add `focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]` to the two offenders. **S**

### P3 — Empty research report viewer is a large void
- Where: `/research` right pane. Evidence: `research__carbon__wide.png` — one centered line
  "Run research or select a report from the list." in a very large empty area.
- Fix: show a compact skeleton or a "recent reports" hint to fill the space. **S**

### P4 — Inconsistent tooltip/hover affordance on icon controls
- Where: only 10 of 27 widgets use `title=` (grep). Icon-only buttons in some widget headers lack
  hover tooltips that others have.
- Fix: pass `title`/`aria-label` on all icon-only controls. **S**

### P5 — Research widget placeholder mismatch
- Where: research widget (`page2__carbon.png`) shows Topic "NVDA earnings setup" but Symbol
  placeholder "AAPL" — mismatched example values. Cosmetic.
- Fix: align the example symbol to the topic (or drop the example). **S**

### P6 — "Copilot" floating button persists behind the open panel
- Where: workspace bottom-right. Evidence: `widget-picker__carbon.png` shows the floating
  "Copilot" pill while state is otherwise idle; when the panel is open it's occluded rather than
  hidden (`copilot__carbon.png`). Minor redundancy with the toolbar copilot toggle.
- Fix: hide the floating launcher when the panel is open. **S** — UNVERIFIED whether this is
  intentional (two entry points).

---

## Systemic patterns (fix once, benefit everywhere)

- **S1 — Retire the legacy sidebar UI.** 7 routes (`/agents /performance /automations /trading
  /rules /memories /charts`) still mount the old shell. Repoint inbound/deep links, then
  redirect-or-404 them. Removes the biggest source of visual inconsistency (J1, B1, I6, J4).
- **S2 — One widget state kit.** A shared `<WidgetEmpty>` / `<WidgetLoading>` / `<WidgetError>`
  (consistent centered layout, one size, optional hint) adopted across ~20 widgets collapses
  I1+I2+I3+J2 into a single sweep. Today every widget reinvents these.
- **S3 — One ticker primitive everywhere.** Route all rendered tickers through `ClickableSymbol`
  (P1); a lint/grep guard ("`.symbol` rendered without ClickableSymbol") prevents regressions —
  the codebase already flags this gap class in memory.
- **S4 — One number/format layer.** Shared formatters for win-rate/P&L/percent rounding (I5) so
  the same metric reads identically on every surface.

## What's already good (don't regress these)

- **Theming is disciplined.** Zero hardcoded hex in the widget layer (grep clean) — everything is
  `var(--…)`. All 4 built-in themes (`workspace__{carbon,warm,oled,light}__wide.png`) and the
  custom-theme editor (`theme-editor__carbon.png`) render with correct contrast and no broken
  colors, including OLED pure-black and the light theme.
- **Chart house rules respected.** Chart controls (`5m / Candles / Studies`) live in each chart's
  top toolbar; no absolute-positioned buttons float over the canvas (`chart-widget.tsx` grep
  clean). Drag/resize handles are themed and reveal on hover (`globals.css`).
- **Grid integrity.** Widgets never overlap (react-grid-layout); scrollbars are intentionally
  hidden with a fade cue (founder preference).
- **Core chrome is coherent.** Toolbar, settings dialog (`settings__carbon.png`), theme menu,
  notifications dropdown, copilot panel, and widget picker all share the mono terminal language
  and read cleanly at both viewports.
- **Analytics pages are strong.** Page 3 (orders + performance + trading calendar + hold-time
  scatter) and the Board (`board__carbon__wide.png`) are dense, legible, and well-composed.
- **ClickableSymbol adoption is already wide** (positions, watchlist, scans, idea-outcomes,
  journal, proposals) — P1 is closing a few remaining gaps, not building from scratch.
- No console/page errors were logged on `/`, `/board`, or `/research` in any theme during driving.

---
Screenshots: `/home/mbelli/.claude/jobs/2404af04/tmp/ui-audit/` (workspace + board + research ×4
themes ×2 viewports; theme-menu/editor; notifications; copilot; widget-picker; settings ×4
sections; pages 2–5; legacy routes ×7).
