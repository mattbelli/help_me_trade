# UI_AUDIT.md — desktop web cockpit diagnostic (2026-07-05, pruned)

Method: drove the running production build (localhost:3001) headless via playwright-core
(chromium-1228) at 1720×970 and 1280×800, across all 4 built-in themes + the custom-theme
editor, plus a broad source read of `frontend/components/**`, `app/globals.css`, and
`lib/workspace/**`. Screenshots in `/home/mbelli/.claude/jobs/2404af04/tmp/ui-audit/`.
Items not visually confirmed are marked UNVERIFIED.

*Pruned 2026-07-05 (founder): the original report spent most of its weight on legacy-UI vs
workspace divergence — that migration is known, deliberate, and already carded
(`retire-old-ui`). Those findings were removed; what remains is the genuinely useful part.
A separate visual-quality/polish review (motion, typography, depth) lives in
`References/UI_POLISH.md`.*

---

## JANKY

### J2 — Empty workspace pages have no empty state
- Where: Page 4 & 5 (default layout ships 5 pages, 2 empty). Evidence: `page4__carbon.png` —
  a fully blank dot-grid canvas, no "Add a widget" CTA. The only add affordance
  (`command-console.tsx:355` Plus) lives in the console, not on the canvas.
- Fix: centered empty-canvas hint + "Add widget" button when a page's layout is empty. **S**

### J3 — Sector-radar polar labels collide + P&L column clips
- Where: `sector-radar` widget. Evidence: `page3__carbon.png` — 27 sector labels overprint
  around the small radar; right-hand P&L values cut off at the widget edge (`+$95,75…`).
- Fix: cap/rotate radar labels (top-N + "others"); compact P&L formatting. **M**

### J5 — Widget picker hides most of its set
- Where: `workspace-widget-picker.tsx:95` (`max-h-72 overflow-y-auto`). Evidence:
  `widget-picker__carbon.png` — ~5 of 25 widgets visible with no scrollbar/fade affordance,
  so the rest look nonexistent. Popover pinned high (`pt-[12vh]`, line 57) not centered.
- Fix: scroll fade/indicator (bottom-fade pattern already exists app-wide) + center. **S**

## INCONSISTENT

### I1 — Empty-state styling varies widget-to-widget (systemic)
- ~20 widgets hand-roll their empty `<p>`: `positions-widget.tsx:102` (`p-3 text-xs`),
  `agent-proposals-widget.tsx:31` (no padding), `memory-widget.tsx:134` (`p-2 text-xs`),
  `reports-widget.tsx:213` (`text-[11px]`), `sector-radar-widget.tsx:256` (centered flex).
  Color is consistent; padding/size/alignment/copy-tone all differ.
- Fix: one `<WidgetEmpty hint?>` adopted across widgets. **M** (see S2)

### I2 — Loading-state styling varies + 4 widgets have none
- Bare "Loading…" at differing sizes; `chart-widget.tsx`, `performance-widget.tsx`,
  `rules-widget.tsx`, `watchlist-list-selector.tsx` have no loading token at all (grep).
- Fix: shared `<WidgetLoading>` / skeleton alongside the empty-state sweep. **S**

### I3 — Error states absent in 8 widgets (silent-empty on fetch fail)
- No error/catch branch (grep): account-summary, agent-proposals, chart, notifications,
  open-orders, positions, performance, rules — a failed fetch is indistinguishable from
  "genuinely nothing here." UNVERIFIED whether any surface a toast instead.
- Fix: standard inline error row in the shared widget shell. **M**

### I4 — Two widgets share the title "performance"
- `widget-registry.tsx:154` (`id: performance`) and `:199` (`id: performance-stats`) both
  `title: "performance"` — the picker shows two identical rows.
- Fix: rename one ("performance analytics" vs "performance stats"). **S**

### I5 — Same metric rounds differently across surfaces
- Win rate renders `63%` in performance-stats but `62.5%` elsewhere for the same 5W/2L
  sample — no shared formatter.
- Fix: one shared number/format layer (win-rate/P&L/percent). **S**

## POLISH

### P1 — Ticker-link gaps (house rule)
- `orders-widget.tsx:108/194/278` render `{o.symbol}` plain; `price-alerts-widget.tsx:326`
  plain; `pnl-scatter-widget.tsx:69/204` legend/tooltip plain. (Compliant already:
  rvol-scanner row-publish; positions/watchlist/scans/idea-outcomes/journal/proposals all
  use ClickableSymbol.)
- Fix: wrap in `ClickableSymbol` / row publish. **S** (carded: `ticker-link-gaps`)

### P2 — outline-none without focus indicator (keyboard a11y)
- `watchlist-widget.tsx:170` (ticker-add input), `watchlist-list-selector.tsx:48` (list
  button): `outline-none`, hover-only styling, no `focus-visible` replacement.
- Fix: `focus-visible:ring-2 ring-[var(--c-accent)]` on the two offenders. **S**

### P3 — Empty research report viewer is a large void
- One centered line in a very large empty pane. Fix: compact skeleton or "recent reports"
  hint. **S**

### P4 — Inconsistent tooltips on icon-only controls
- Only 10 of 27 widgets use `title=`. Fix: `title`/`aria-label` on all icon-only
  controls. **S**

### P5 — Research widget placeholder mismatch
- Topic example "NVDA earnings setup" beside Symbol placeholder "AAPL". Cosmetic. **S**

### P6 — "Copilot" floating pill persists behind the open panel
- Occluded rather than hidden when the panel opens. UNVERIFIED whether intentional (two
  entry points). Fix: hide the launcher while the panel is open. **S**

## Systemic sweeps

- **S2 — One widget state kit.** Shared `<WidgetEmpty>` / `<WidgetLoading>` /
  `<WidgetError>` adopted across ~20 widgets collapses I1+I2+I3+J2 into one sweep.
  (Carded: `ui-empty-states`.)
- **S3 — One ticker primitive everywhere** + a grep/lint guard against `.symbol` rendered
  outside ClickableSymbol. (Carded: `ticker-link-gaps`.)
- **S4 — One number/format layer** so the same metric reads identically everywhere (I5).

## What's already good (don't regress)

- **Theming is disciplined**: zero hardcoded hex in the widget layer; all 4 themes render
  with correct contrast (incl. OLED pure-black and light).
- **Chart house rules respected**: controls in the chart toolbar, nothing floats over the
  canvas; drag/resize handles themed, reveal on hover.
- **Grid integrity**: no widget overlaps; hidden scrollbars with fade cues (intentional).
- **Core chrome is coherent**: toolbar, settings, theme menu, notifications, copilot,
  picker all share the mono terminal language at both viewports.
- **Analytics pages are strong**: Page 3 and the Board are dense, legible, well-composed.
- No console/page errors on `/`, `/board`, `/research` in any theme.

---
Screenshots: `/home/mbelli/.claude/jobs/2404af04/tmp/ui-audit/`.
