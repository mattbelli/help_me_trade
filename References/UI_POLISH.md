# UI_POLISH.md — visual-quality & motion review of the terminal workspace

Scope: the workspace UI only — `/` (widget grid, toolbar, console, copilot, dialogs, picker,
theme menu), `/board`, `/research`. Legacy sidebar routes are out of scope (mid-retirement).
Method: drove localhost:3001 across all 4 themes + custom-theme editor, read `app/globals.css`,
`components/workspace/**`, `components/ui/terminal/**`. This file is the *look-and-feel* companion
to `References/UI_AUDIT.md` (which owns state-handling / migration / empty-state findings — not
repeated here). Screenshots: `/home/mbelli/.claude/jobs/2404af04/tmp/ui-polish/`.

---

## Design POV

This is a Bloomberg-terminal-done-right: a dense, mono, var-themed cockpit where the *data* is the
decoration and chrome gets out of the way. Its personality is already unusually disciplined — zero
hardcoded hex in the widget layer, four genuinely distinct themes (carbon teal, warm sand, OLED
violet, clean light), lowercase mono widget titles, a `⠿` drag glyph, a blinking `▋` command cursor,
`[x]/[ ]` ASCII toggles, a `›` command prompt. It reads like a tool built by someone who respects the
user's screen. What it is *not* yet is *alive*: prices repaint silently, pages hard-cut, widgets pop
in without ceremony, and the one spring it owns (`stiffness:420, damping:36`) lives in only five
files. "Exceptional" here is not more color or more animation — it is **making the terminal feel
instrument-grade**: numbers that tick like a real quote board, panels that switch with the crispness
of a hardware readout, one coherent type scale and one elevation system so nothing looks hand-rolled,
and motion so restrained it registers as *responsiveness*, never as decoration. The bar is Linear's
precision and a Bloomberg terminal's calm — every micro-interaction earns its place or is cut. Get the
tick-flash, the type scale, and the page-swap right and this jumps from "very good dense UI" to
"instrument you trust with real money."

---

## 1 — Motion design

Current state: `motion/react` is used in exactly 5 files — `copilot-sidebar` (width spring + panel
slide), `workspace-page` (docked padding spring), `command-console` (container spring + blink cursor),
`notification-banner` (`AnimatePresence` slide-in), and `watchlist-widget` (`Reorder`). The house
spring is `{type:"spring", stiffness:420, damping:36}` (notification uses damping 32). Everything else
is either `transition-colors`/`transition-[border-color]` at 150ms or completely static. **There is no
`prefers-reduced-motion` guard anywhere in the codebase** (grep: 0 hits) — this must be fixed before
adding any of the below.

**Establish a motion scale first** (new `lib/workspace/motion.ts`) so every addition is coherent:

```ts
// The 4 roles. Never free-hand a spring/duration outside these.
export const motionStd    = { type: "spring", stiffness: 420, damping: 36 } as const; // panels, popovers, docking — the existing house spring
export const motionSnappy = { type: "spring", stiffness: 700, damping: 34 } as const; // press, toggles, small transforms
export const motionFast   = { duration: 0.13, ease: [0.2, 0, 0, 1] } as const;        // color/opacity hovers (matches current ~150ms)
export const motionExit   = { duration: 0.10, ease: [0.4, 0, 1, 1] } as const;        // leave-fast
export const flashDecay   = { duration: 0.5,  ease: [0.4, 0, 0.2, 1] } as const;      // tick-flash fade-back
// Wrap all of the above in a useReducedMotion() check that collapses to {duration:0}.
```

| # | What / where | Why it raises quality | Sketch (real values) | Eff | Imp |
|---|---|---|---|---|---|
| M1 | **Price tick-flash.** No price cell flashes on change anywhere (verified: no `prevPrice`/flash logic). Targets: chart-header price `workspace-grid.tsx:148-171`, watchlist LAST/Δ%, positions value/price, account tiles `workspace-toolbar.tsx:112`. | This is *the* signature move of a real quote board — the single highest "this is a live terminal" signal. Silent repaints are the biggest thing separating this from a Bloomberg feel. | `usePriceFlash(value)` → `"up"|"down"|null` for 500ms. Apply a fading **background wash**, not a jarring text-color swap: cell gets `background: color-mix(in srgb, var(--c-green) 16%, transparent)` then eases to transparent over `flashDecay`. Flash the LAST/price cell **only** (never every column). Skip under reduced-motion. | M | **H** |
| M2 | **Page hot-swap crossfade.** `ReactGridLayout key={activePageId}` (`workspace-grid.tsx:747`) hard-remounts on Alt+1-5 — an abrupt cut. | The static-switch preference is about *not sliding widgets around* — a 120ms opacity fade honors that while removing the jarring flash-of-relayout. | Wrap `#workspace-grid-root` in `motion.div key={activePageId}` with `initial={{opacity:0}} animate={{opacity:1}} transition={{duration:0.12}}` — **fade-in only, no exit** (an exit adds perceived latency). Feels like a channel change on a hardware monitor. | S | **H** |
| M3 | **Widget mount/unmount.** Widgets appear/vanish instantly; RGL just adds/removes DOM. | Add/remove is a deliberate user act — a 150ms acknowledgement makes the grid feel physical, not like a page reload. | Animate an **inner** wrapper (RGL owns the root transform, so don't animate the root): `initial={{opacity:0, scale:0.98}} animate={{opacity:1, scale:1}}` with `motionStd`; removal via `AnimatePresence` exit `{opacity:0, scale:0.98}` `motionExit`. Confirm-remove already exists (`WidgetRemoveButton`), so this only fires on real intent. | M | M |
| M4 | **Focus flash is a hard on/off.** `flashing` toggles `ring-2 ring-[var(--c-accent)]` for exactly 1200ms then snaps off (`workspace-grid.tsx:529, 613`). | The snap-off looks like a bug; a decaying pulse reads as "here it is" and is more legible when a ticker-link scrolls a widget into view. | Replace the boolean ring with a one-shot CSS keyframe: `@keyframes widget-focus { 0%{box-shadow:0 0 0 2px var(--c-accent)} 70%{box-shadow:0 0 0 2px var(--c-accent)} 100%{box-shadow:0 0 0 2px transparent} }` `1200ms ease-out`. Same duration, far classier exit. | S | M |
| M5 | **Notification / bell arrival.** Banner already slides in (`notification-banner.tsx:97`, spring 420/32) — good. But the bell (`notification-bell`) and new list items don't announce themselves. | A new agent proposal is the mission's whole point (phone-approval unlock) — its arrival deserves one calm beat, not silence. | New notification row: `initial={{opacity:0, x:8}}` + a one-shot left-border accent flash (`box-shadow: inset 2px 0 0 var(--c-accent)` fading over 600ms). Bell: scale-pop the unread count `motionSnappy` when it increments. Keep it to *one* pulse — never a looping attention-grabber. | S | M |
| M6 | **Press feedback.** Every interactive control is `transition-colors` only — no `active:` state (checked `terminal-button.tsx`, `terminal-toggle.tsx`, toolbar buttons). | A 60ms scale-down on press is the cheapest possible "the machine felt that" signal; its absence is why buttons feel slightly like web links, not instrument keys. | Add `active:scale-[0.97] transition-transform` (or `whileTap={{scale:0.97}}` where already motion) to `TerminalButton`, `TerminalToggle`, the console lock/add buttons, page pills. ~100ms. Do **not** add to table rows (density). | S | M |
| M7 | **Dialog/popover enter-exit.** Dropdowns, widget-picker, theme menu, remove-confirm appear instantly (Radix default, no motion variant). | The console and copilot animate; popovers don't — an inconsistency you feel. A 100ms scale+fade makes them feel attached to their trigger. | Radix content: `data-[state=open]:animate-in fade-in-0 zoom-in-95` / `data-[state=closed]:animate-out fade-out-0 zoom-out-95`, `duration-100`, origin from `--radix-*-content-transform-origin` (tw-animate-css is already imported in `globals.css:2`). | S | M |

**Where restraint beats motion:** no motion on table-row *content* (positions/orders/watchlist rows —
density is the feature); no parallax; no animated number *count-up* on the account tiles (a tick-flash
yes, a slot-machine roll no — it delays reading the number, which is unforgivable on a money readout);
no chart-crosshair custom motion (TradingView owns it and does it well); no looping/breathing anything
except the existing intentional command cursor and the `research-cursor-pulse`.

---

## 2 — Typography & rhythm

The mono density is a feature and reads beautifully. But the *scale* has drifted: **12 distinct
font-sizes are in use** — `text-[8px]`(5) `[9px]`(40) `9.5px` `[10px]`(124) `[11px]`(112) `[11.5px]`
`[12px]`(37) `[13px]`(11) `[14px]`(4) `[15px]`(2) `[19px]` `[20px]`, plus `text-xs`(53)/`sm`(18). The
oddballs `11.5px` (widget title `workspace-grid.tsx:435`, header font `workspace-toolbar.tsx:265`) and
`9.5px` (broker dropdown `dt`s) buy nothing and cost consistency.

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| T1 | **Collapse to a 6-step scale.** | 12 sizes is ad-hoc drift; a fixed ladder is what makes Linear/terminal type feel *engineered*. | Roles: `9` = micro-labels/badges · `10` = table body & secondary · `11` = primary body/rows · `12` = widget & section titles · `14` = toolbar tiles / chart price emphasis · `15/19` = hero numbers only. **Kill `11.5px`→`12px`, `9.5px`→`10px`, `13px`→`12px`, `8px`→`9px`.** Encode as CSS vars (`--fs-label`…`--fs-hero`) so it can't drift again. | M | M |
| T2 | **Standardize label tracking + casing.** UPPERCASE micro-labels use **three** different tracking values: `tracking-wide` (0.025em), `tracking-[0.08em]` (toolbar tile `:109`), and `tracking-[0.1em]` (account-summary `:26`) — plus bare uppercase. | The uppercase micro-label is *the* recurring motif (SYM/LAST/RVOL, REAL/PAPER, PORT/BP/DAY, section headers) — a single tracking value makes it a designed *system*, not three coincidences. | One rule: **all uppercase micro-labels = `text-[9px] tracking-[0.08em] text-[var(--fg-faint)] uppercase`**. Wrap as a `<MicroLabel>`/util class so the three variants can't recur. Non-uppercase mono titles stay `tracking-normal`. | S | M |
| T3 | **`tabular-nums` — already good; keep the contract.** Confirmed applied consistently to every numeric cell/tile; it's only *redundant* (harmless) on static mono labels. | The real risk is future drift: a new tick-flash cell (M1) or countdown that forgets it will reflow width on every update — jarring on a money readout. | No sweep needed now. When adding M1/tick-flash cells and any new mutating numeric, `tabular-nums` is mandatory. Optionally drop it from static mono labels to cut class noise. Low urgency. | S | L |
| T4 | **Retire the double-title redundancy.** Several widgets render the header title *and* repeat it uppercase in the body (header "sector radar" → body "SECTOR RADAR"; "hold time vs p&l" → "HOLD TIME VS P&L 100 trades"). | Repeating the name wastes a row of precious vertical density and reads amateur. | Drop the in-body title; keep only the *qualifier* ("100 trades", "27 sectors · $61,043") as an uppercase micro-label. Frees a line per analytics widget. | S | M |

---

## 3 — Depth & elevation

Current: flat panels, 1px `--border`, a per-theme `--panel-shadow` (real drop shadow on dark/warm/
light, a `0 0 0 1px rgba(255,255,255,0.04)` *hairline* on OLED — correct instinct for pure black). A
`--c-glow` var exists but is barely used. The WidgetCard declares `transition-[border-color,box-shadow]`
**but never changes box-shadow on hover** (`workspace-grid.tsx:527`) — a dead transition and a missed
affordance. Dropdowns are inconsistent: some use `shadow-[var(--panel-shadow)]`, some raw `shadow-lg`.

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| D1 | **A 3-tier elevation system.** | Right now "panel", "popover", "dialog" all improvise shadows. Three named tiers make the whole app read as one designed surface stack. | `--elev-1` = current `--panel-shadow` (widgets) · `--elev-2` = popovers/dropdowns (slightly tighter+darker) · `--elev-3` = dialogs/modals (the theme-editor's backdrop-blur look). Define per theme; **replace every raw `shadow-lg`** (`page-switcher.tsx:259`, console overlay, notification dropdown) with the token. | M | M |
| D2 | **Make the widget hover-lift real.** The transition is declared but no-ops. | Founder likes flat — so make it *barely* perceptible: a hover that firms the border + a whisper of shadow says "this is a live, grabbable object" without kitsch. | On `.group` widget: `hover:border-[var(--border-strong)]` (kept) **+ `hover:shadow-[0_2px_10px_rgba(0,0,0,0.22)]`** on dark themes (theme-var it; near-zero on OLED to protect the black, none on light beyond the existing subtle shadow). Transition already present. | S | M |
| D3 | **Translucency for transient surfaces only.** The console (`bg-[var(--bg-raised)]/85 backdrop-blur-md`) and copilot already use blur beautifully. | Backdrop-blur is premium *when reserved for things floating over content* — spreading it to widgets would kill legibility and cost GPU on a data-dense grid. | Keep blur on: console, copilot, dropdowns, dialog scrim. **Never** on widget panels or table rows. Codify this as a rule so it isn't over-applied. | — | — |
| D4 | **Promote the "glow" accent to a system, sparingly.** The connected-broker dot uses an isolated `shadow-[0_0_8px_var(--c-green)]` (`workspace-toolbar.tsx:302`); nothing else glows. | One-off glows look accidental. A single rule — *live/active status dots glow, nothing else* — turns it into intentional vocabulary. | Apply the same `0 0 8px` glow to the watchlist live-dot and chart-header live dot (`workspace-grid.tsx:177`) when live; remove glow when stale. Consistent "this data is hot" cue. Don't glow anything non-status. | S | M |

---

## 4 — Color & contrast refinement

The four themes are the strongest thing here — disciplined `--fg-dim`/`--fg-faint` hierarchy, no hex
leakage, good P&L balance (`--c-green #5bc98a` / `--c-red #ef6f6c` on dark; `#1f9d57`/`#d23b34` light).
Accent discipline is mostly excellent (teal reserved for accent, orange `›` for command, per-theme
accent recolors correctly). Refinements, not overhauls:

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| C1 | **Light-theme chart legibility.** In light, the embedded charts turn white and the whole panel reads flatter/more generic than the dark themes (which is where the app's identity lives). | Light is a first-class theme in the menu; it shouldn't feel like the "downgrade." | Give light panels a touch more separation: `--border` on light is `#e2e5ea` (very soft) — the widget hover-lift (D2) and elevation tiers (D1) do most of the work; also verify chart gridlines aren't near-invisible. Mostly handled by D1/D2. | S | M |
| C2 | **`fg-faint` on light is borderline.** `--fg-faint: #878e98` on `#f5f6f8` for micro-labels/dot-grid. | Sub-3:1 on the smallest labels hurts the a11y story and the crispness. | Nudge to `#7c828d` (or reserve `fg-faint` for non-text decoration only and use `fg-dim` for any real label). Verify contrast at 9-10px. | S | L |
| C3 | **Selection/focus is inconsistent.** Some controls have `focus-visible:ring-2 ring-[var(--c-accent)]` (toolbar buttons, broker), others just `outline-none` (per UI_AUDIT P2). | A single, always-accent focus ring is both a11y and polish. | Standardize `focus-visible:ring-2 ring-[var(--c-accent)] ring-offset-1 ring-offset-[var(--panel)]` on every focusable. (Overlaps UI_AUDIT P2 — same fix, wider sweep.) | S | M |
| C4 | **P&L color could carry more info at a glance.** Green/red are well balanced but binary. | Optional: a faint magnitude cue (bolder for big moves) reads faster on a scan. | *Restraint call — probably skip.* If pursued: only bump weight, never hue, for |Δ%|>2. Listed for completeness; low priority. | S | L |

---

## 5 — Micro-interactions & affordances

Strong foundation: resize handles reveal on hover (150ms ease, themed corners), scroll-edge gradient
fades (40px, four-edge, `workspace-grid.tsx:785+`), hidden scrollbars, the `⠿` drag handle, the
blinking command cursor. Gaps:

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| N1 | **Drag pickup has no visual.** RGL placeholder is styled (`globals.css:409`, dashed accent) but the *dragged* card doesn't lift. | Picking up a widget should feel like lifting it off the board — the missing depth cue makes drag feel flat. | On `.react-grid-item.react-grid-item--dragging` (RGL adds it): `box-shadow: var(--elev-3); opacity:0.92; cursor:grabbing;` + a 1° scale. Pure CSS, no JS. | S | M |
| N2 | **Skeletons are the generic shadcn pulse.** ~20 widgets use the shared `<Skeleton>` (a `bg` opacity-pulse); `tw-shimmer` is imported (`globals.css:3`) but **unused**. | A pulse reads "generic web app loading"; a mono row-shimmer that mimics the real table reads "terminal fetching data" — and matches the shape of what's arriving. | Give the loading state a mono row-skeleton (fixed-width cells matching the real columns) with the `tw-shimmer` gradient sweeping ~1.2s, `--bg-hover`→`--bg-raised`. Fold into the UI_AUDIT S2 `<WidgetLoading>` sweep so it's one kit. | S | M |
| N3 | **Toast has zero motion and generic styling.** `workspace-toast.tsx:34-40` renders with a plain conditional (no enter/exit), `bg-popover shadow-lg font-mono text-xs`, auto-dismiss 2800ms. | The approve/reject flow surfaces its result *only* via this toast — the mission-critical confirmation appears and vanishes with a hard cut and non-terminal chrome. This is the weakest-feeling surface on the most important path. | Slide-up + fade in via `AnimatePresence` + `motionStd`, exit `motionExit`; restyle to `bg-[var(--panel)]/90 backdrop-blur` + `--elev-2` + accent left-rule for success / `--c-red` for failure; add a thin 2.8s countdown rule so the dismiss isn't a surprise. | S | **H** |
| N5 | **Watchlist rows snap on hover.** Every other row/control uses `transition-colors` (~150ms); watchlist rows (`watchlist-widget.tsx:291`) omit it, so their `hover:bg` flips instantly — a subtle inconsistency you feel scanning between widgets. | One eased-hover cadence across all rows is exactly the kind of uniformity that reads as "authored". | Add `transition-colors` to the watchlist row. One-line fix. | S | L |
| N4 | **Widget-picker rows lack keyboard-move affordance / hover depth.** (Picker also carded in UI_AUDIT J5 for scroll-fade.) | It's a command surface (also reachable via `/add`) — arrow-key highlight + a hover row-fill make it feel like a launcher, not a list. | Row `hover:bg-[var(--bg-hover)]` + `aria-selected` accent-left-rule on keyboard focus; Enter to add. | S | M |

---

## 6 — Data-viz polish

Confirmed by source pass. Current specs vary per widget: equity-curve `Area strokeWidth:2` with a
gradient fill (`--c-accent` 0.25→0) and `dot r:2`; winrate-trend `Line strokeWidth:2, dot:false`, **no
fill**; sparkline `polyline strokeWidth:1.5`, **no dot / no baseline / no fill**; sector-radar
`fillOpacity:0.35`, default stroke; pnl-scatter default dot radius; calendar heatmap opacity ramp
`0.05 + intensity*0.25`. All 6 recharts widgets set `isAnimationActive={false}` (intentional — a
terminal shouldn't animate charts on every poll; **keep this**). Axis type is uniform (`fontSize:10`,
`fill var(--fg-faint)`, `stroke var(--border)`). Two real gaps: charts don't share one *primitive*
spec, and two of them lack the treatments their siblings already have.

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| V1 | **One chart-primitive spec (`chartTheme.ts`).** Stroke widths (2 / 2 / 1.5), dot radii (2 / none / default), and axis styling are set independently per widget. | Shared constants are what make a dashboard read as *one instrument* rather than six charts from six authors — right now they're 90% aligned by luck, not by contract. | Constants: line `strokeWidth:1.75` · sparkline `1.5` · scatter dot `r:3` (hover `r:4`, currently default) · axis `fontSize:9 fill var(--fg-faint)` · grid `var(--border)`. Point every widget at them; delete the per-widget literals. | M | M |
| V2 | **Extend equity's gradient-fill to winrate-trend.** Equity already does this well (`AreaChart`, `--c-accent` 0.25→0); winrate-trend is a bare `Line` with `dot:false, no fill` (`winrate-trend-widget.tsx:212`). | The two trend widgets should read identically; the bare line looks unfinished next to the filled equity curve. **Copy the pattern that already exists** — no new invention. | Convert winrate-trend to `Area` with the same `<linearGradient>` at 0.20→0. (pnl-scatter and sparkline correctly stay fill-less.) | S | M |
| V3 | **Sparkline baseline + endpoint dot.** `sparkline.tsx:61` is a bare `polyline` — no reference point, so up-vs-down-from-where is ambiguous. | A faint prev-close baseline and a 1.5px endpoint dot make each 1D spark readable in a fast column scan. | Dashed `var(--border)` baseline at prev-close (the flat-state already draws a dashed mid-line — reuse it); `r:1.5` endpoint dot colored by direction; line color by net change (already done). | S | M |
| V4 | **Calendar heatmap ramp is too faint + linear.** `trading-calendar-widget.tsx:146` caps at `opacity 0.05+intensity*0.25` ≈ 0.30 max, scaled linearly off `|net|/maxAbs`. | A ceiling of 0.30 means even the biggest day barely tints; linear scaling lets one outlier flatten everything else — so magnitude is hard to read despite the ramp existing. | Raise the ceiling to ~0.55 and switch to **quantile/log** binning (5 steps) so a single huge day doesn't wash out the rest; keep the number label. See the `dataviz` skill's sequential-palette guidance. | S | M |
| V5 | **Radar label triage.** 27 labels overprint (dup of UI_AUDIT J3, restated for the viz spec). | Illegible labels undercut an otherwise striking chart (`fillOpacity:0.35` teal spider is genuinely nice). | Top-8 sectors labeled + "others" spoke; rotate/anchor labels radially; compact the clipped P&L column. | M | M |

---

## 7 — Personality details

The terminal touches already present (`⠿`, `▋` blink, `[x]/[ ]`, `›` prompt, `▰` gradient logo mark,
lowercase titles, `21:56 ET` clock) are *exactly* right — keep every one. A few more that fit, and a
clear line on what would be too cute:

| # | What / where | Why | Sketch | Eff | Imp |
|---|---|---|---|---|---|
| P1 | **Boot moment.** App mounts cold with no identity beat. | A ~400ms terminal "power-on" (the `▰` mark + a single sweep line, or a one-line `booting workspace…`) sets the instrument tone and covers first-paint. | One-shot on first mount only (session-flagged), skipped under reduced-motion, hard-capped ~400ms so it never taxes a returning user. Optional. | S | L |
| P2 | **Live "ET" clock could pulse its colon.** `use-et-clock` renders `21:56 ET` statically. | A 1s colon blink is a classic quiet terminal signal that the app is live — pairs with the command cursor. | `:` at `opacity:1↔0.4` on a 1s interval (reuse the console blink cadence). Tiny, on-brand. | S | L |
| P3 | **Consistent status-dot vocabulary.** Live/stale dots exist (green fill vs hollow border, `workspace-grid.tsx:177`) — good — but the glow (D4) and the shape language should be identical everywhere a "live" state appears. | A single dot grammar (filled+glow = live, hollow = stale, red = error) is the kind of detail that makes a terminal feel authored. | Codify: `<StatusDot state="live|stale|error">`; reuse in watchlist, chart header, broker, connection. | S | M |

**Too cute — do not add:** typewriter/character-reveal on any data text; a fake CRT scanline or noise
overlay; ASCII-art headers inside widgets; sound; emoji anywhere in the terminal chrome; an animated
number count-up on money readouts (see M-restraint). These all fight density and/or reading speed.

---

## Do first (highest impact per effort)

1. **M1 — Price tick-flash** (M/H). The single biggest "this is a live terminal" upgrade; nothing else
   moves the needle on *feel* as much.
2. **M2 — Page hot-swap crossfade** (S/H). One `motion.div` + a `key`; removes the most jarring
   transition in the app while honoring the static-switch preference.
3. **`prefers-reduced-motion` guard + `motion.ts` scale** (S/H, prerequisite). Ship this *with* M1/M2 —
   it's the seatbelt that lets everything else be added safely and coherently.
4. **N3 — Restyle + animate the result toast** (S/H). Zero motion + generic `bg-popover` chrome on the
   *approve/reject confirmation* — the highest-value user path deserves a native, animated toast; tiny fix.
5. **T1+T2 — Type scale collapse + label-tracking rule** (M/M). Turns 12 drifting sizes and 3 tracking
   values into one engineered ladder; invisible individually, transformative in aggregate. (D2 — the
   already-declared-but-dead widget hover-lift — is the honorable-mention near-freebie right behind these.)

## Don't do

- **No number count-up / slot-roll on money tiles** — it delays reading the figure; a tick-flash is the
  correct live cue, a roll is a toy.
- **No motion on table-row content** (positions/orders/watchlist rows) — density is the product; row
  animation adds latency-feel to the exact place users scan fastest.
- **No backdrop-blur on widget panels or rows** — reserve it for transient floating surfaces (console,
  copilot, dropdowns, dialogs); on a data grid it costs legibility and GPU.
- **No exit animation on page switch** (fade-*in* only) — a leave animation adds perceived latency to a
  keyboard-driven action that must feel instant.
- **No decorative CRT/scanline/noise, no ASCII-art, no sound, no per-character text reveals** — they
  read as costume, not instrument, and every one of them fights the density the founder values.
- **Don't touch the TradingView crosshair/interactions** — it's already correct and not ours to restyle.
- **Don't lower type density or widen padding "for breathing room"** — the small/dense mono is a
  deliberate feature; polish *within* it, never loosen it.
```
