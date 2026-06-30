# THEME OVERHAUL RECON
*Generated 2026-06-30 — read-only audit, no code changed.*

---

## 1. The CSS-var system (`frontend/app/globals.css`)

### Theme application mechanism (see §4 for full details)
`next-themes` sets `data-theme="<name>"` on `<html>`. CSS selectors `[data-theme="carbon"]` etc. cascade all vars down to every child. The custom Tailwind dark variant (line 9) is set to match `.dark *` OR `[data-theme="carbon|warm|oled"] *` — all three dark themes trigger the `dark:` variant.

### Blocks defined
| Selector | Notes |
|---|---|
| `:root` | Same values as carbon — the live default before JS hydrates |
| `[data-theme="carbon"]` | Explicit duplicate of `:root` (intentional — ensures theme attr always wins) |
| `[data-theme="warm"]` | Warm amber accent, brownish surfaces |
| `[data-theme="oled"]` | Pure black `#000000` bg, tighter borders |
| `[data-theme="light"]` | Light mode; completely different palette |

### Complete var inventory — what each controls

#### Page background ramp
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--background` | Page/canvas base bg (body, workspace outer wrapper) | YES |
| `--bg-raised` | Slightly elevated surface (toolbar `bg-raised`) | YES |
| `--bg-inset` | Sunken/inset surface (sidebar, input insets, add-row bg) | YES |
| `--bg-hover` | Row/button hover bg | YES |

#### Surfaces / panels
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--card` | shadcn `<Card>` background | YES |
| `--card-foreground` | Card text | YES |
| `--popover` | shadcn `<Popover>` / `<DropdownMenu>` background | YES |
| `--popover-foreground` | Popover text | YES |
| `--panel` | Widget `WidgetCard` bg, dropdown panels in workspace | YES |
| `--sidebar` | Sidebar background | YES |
| `--sidebar-foreground` | Sidebar text | YES |
| `--sidebar-accent` | Sidebar hover/active item bg | YES |
| `--sidebar-accent-foreground` | Sidebar active item text | YES |
| `--sidebar-primary` | Sidebar active icon/primary element | YES |
| `--sidebar-primary-foreground` | Sidebar primary fg | YES |
| `--sidebar-border` | Sidebar border | YES |
| `--sidebar-ring` | Sidebar focus ring | YES |

#### Foreground ramp
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--foreground` | Default text color | YES |
| `--fg-dim` | Secondary/muted text (labels, metadata) | YES |
| `--fg-faint` | Tertiary/ghost text (drag handles, placeholders) | YES |
| `--muted-foreground` | shadcn muted text (same visual role as fg-dim) | YES |
| `--primary` | shadcn primary text | YES |
| `--primary-foreground` | Text on primary bg | YES |
| `--secondary-foreground` | Text on secondary bg | YES |
| `--accent-foreground` | Text on accent bg | YES |

#### Borders
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--border` | Default border (widget frames, table rows, inputs) | YES |
| `--border-strong` | Hover-state border upgrade (widget hover) | YES |
| `--input` | Input element border (same as `--border` in all themes) | YES |
| `--ring` | shadcn focus ring color | YES |
| `--sidebar-ring` | Sidebar focus ring (same as sidebar-primary) | YES |

#### Accent
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--c-accent` | Primary accent (carbon/oled: teal `#5ad1be`; warm: amber `#e0975b`; light: teal `#2f9e8c`) | YES |
| `--c-accent-soft` | Translucent accent for active-state backgrounds | YES |
| `--c-on-accent` | Text color ON the accent bg | YES |
| `--c-glow` | Box-shadow glow ring (used sparingly) | YES |
| `--primary` | shadcn alias for the accent (same value as `--c-accent` in practice) | YES |
| `--ring` | Focus ring (same as `--c-accent` in dark themes) | YES |
| `--chart-accent` | LightweightCharts up-candle + area stroke; also shared by area chart series | YES |

#### Semantic / data colors
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--c-green` | Positive P&L, gainers, connected-dot | YES |
| `--c-red` | Negative P&L, losers, alerts | YES |
| `--c-yellow` | Warning/moderate signals (RVOL mid tier) | YES |
| `--c-amber` | Amber accent; warm theme accent; RVOL high tier | YES |
| `--c-blue` | Informational / secondary chart series | YES |
| `--c-violet` | Tertiary accent (gradient badge in toolbar) | YES |
| `--c-command` | Command/action highlight (orange) | **MISSING from `carbon` block** (inherits `:root` value `#ff8c42` — functionally identical, just inconsistent) |
| `--pnl-positive` | P&L positive text (distinct from `--c-green` in light theme) | YES |
| `--pnl-negative` | P&L negative text | YES |
| `--destructive` | Destructive action color | YES |

#### Chart series palette
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--chart-1` through `--chart-5` | Named chart series colors (recharts/shadcn chart.tsx) | YES |
| `--chart-accent` | LightweightCharts accent (bridged from `--c-accent` effectively) | YES |

#### Shadows / glow
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--panel-shadow` | Widget card box-shadow; dropdown menus | YES |
| `--c-glow` | Focus/hover glow ring (`0 0 0 1px accent/12%`) | YES |

#### Grid / layout
| Var | Controls | In all 4 themes? |
|---|---|---|
| `--grid-placeholder` | RGL drag placeholder bg | YES |
| `--radius` | Base border-radius (same `0.625rem` in all themes) | YES |

### Which vars are NOT bridged to Tailwind utility classes
The `@theme inline` block maps a subset to `--color-*` tokens, enabling standard Tailwind utilities like `bg-background`, `text-foreground`, `border-border`, `bg-card`, etc.

**NOT bridged** (must be used as `bg-[var(--X)]` arbitrary values):
`--panel`, `--bg-raised`, `--bg-inset`, `--bg-hover`, `--border-strong`, `--fg-dim`, `--fg-faint`, `--c-accent`, `--c-accent-soft`, `--c-violet`, `--c-blue`, `--c-green`, `--c-red`, `--c-yellow`, `--c-amber`, `--c-command`, `--c-on-accent`, `--panel-shadow`, `--c-glow`, `--grid-placeholder`, `--c-danger` (undefined — see worklist)

---

## 2. Make-theme-aware worklist — every offender

### 🔴 CRITICAL: Chart — no theme-change re-init (`tv-light-chart.tsx`)

**Root cause**: `createChart()` runs in a `useEffect` with deps `[timeframe.value, style]`. A theme switch changes `data-theme` on `<html>` (a DOM attribute change), which does NOT trigger any React re-render in the chart component, because the chart never observes `resolvedTheme`. Colors are read once via `resolveCssColor()` at mount and baked into the LightweightCharts Canvas — the Canvas element is not part of CSS cascade.

**Individual offenders:**

| Location | Hardcoded value | Should become |
|---|---|---|
| `tv-light-chart.tsx:271` | `const down = "#9f2f2d"` (down-candle, used in chart init) | `resolveCssColor("--c-red", "#9f2f2d")` — or introduce `--chart-down` |
| `tv-light-chart.tsx:395` | `const down = "#9f2f2d"` (duplicate in bars-fetch callback) | same |
| `tv-light-chart.tsx:473` | `color: alert.direction === "below" ? "#9f2f2d" : "#3d8f7a"` (price alert lines) | `resolveCssColor("--c-red", "#9f2f2d")` / `resolveCssColor("--c-green", "#3d8f7a")` |
| `tv-light-chart.tsx:79` | `return "#3d8f7a"` (support level color) | `resolveCssColor("--c-green", "#3d8f7a")` |
| `tv-light-chart.tsx:81` | `return "#9f2f2d"` (resistance level color) | `resolveCssColor("--c-red", "#9f2f2d")` |
| `tv-light-chart.tsx:83` | `return "#c9922a"` (poc level color) | `resolveCssColor("--c-amber", "#c9922a")` |
| `tv-light-chart.tsx:86-88` | `return "#7a7a78"` (prior high/low/default) | `resolveCssColor("--fg-faint", "#7a7a78")` |

**Fix for theme-change re-init** (chart does not observe theme changes at all):

The chart effect's deps are `[timeframe.value, style]` (line 354). Adding `resolvedTheme` (from `useTheme()`) forces chart destruction+recreation on theme switch. A lighter alternative: keep a separate `useEffect([resolvedTheme])` that calls:
```ts
chartRef.current?.applyOptions({ layout: { textColor: text }, grid: { vertLines: { color: border }, horzLines: { color: border } }, rightPriceScale: { borderColor: border }, timeScale: { borderColor: border } });
mainRef.current?.applyOptions({ upColor: accent, downColor: down, borderUpColor: accent, borderDownColor: down, wickUpColor: accent, wickDownColor: down });
```
This avoids destroying chart state (user's zoom/scroll preserved) while still updating palette on theme switch. Volume series colors would need re-setData to update bar colors.

The simplest correct fix: add `resolvedTheme` to the chart-creation `useEffect` deps so it fully recreates. The bars-fetch effect already re-runs on `[symbol, timeframe, style]` so data will reload.

### 🟡 MEDIUM: Research page iframe — hardcoded dark bg

| Location | Hardcoded value | Should become |
|---|---|---|
| `research-page.tsx:370` | `bg-[#0f1115]` (iframe background for HTML research reports) | `bg-[var(--bg-inset)]` or `bg-[var(--background)]` |

Note: the iframe renders external HTML content — the iframe container bg is just the letterbox color seen when the iframe content doesn't fill the area. It should match the panel bg.

### 🟡 MEDIUM: `--c-danger` undefined in all themes

| Location | Issue | Fix |
|---|---|---|
| `command-console.tsx:67` | `text-[var(--c-danger,#ef4444)]` — `--c-danger` is not defined in any theme block; falls to hardcoded `#ef4444` always | Either rename to `text-[var(--c-red)]`, or define `--c-danger` in all 5 theme blocks (aliasing `--c-red`) |

### 🟢 LOW: Attachment UI — light-mode hardcodes

| Location | Hardcoded value | Notes |
|---|---|---|
| `assistant-ui/attachment.tsx:175` | `bg-white`, `[&_svg]:text-black` on the attachment tile remove button | Looks wrong in dark themes (white blob on dark background). Should use `bg-background` / `text-foreground`. Minor UI corner. |

### ✅ Non-issues (acceptable patterns)

- `bg-black/10` (dialog backdrop), `bg-black/50` (widget picker scrim) — pure alpha overlays, intentionally colorless
- `"#000000"` in `chart-colors.ts:18` — canvas color probe reset, not a visual color
- Recharts' `[stroke='#ccc']` → `stroke-border/50` in `ui/chart.tsx:68` — a smart selector that overrides Recharts' default hardcoded SVG strokes with themed vars
- `bg-muted-foreground/40` on the paper mode dot in sidebar — uses Tailwind token, correctly themed

---

## 3. The complete per-theme var template

This is the full canonical set every theme block MUST define for total coherence. Current values shown.

```css
[data-theme="THEME"] {
  /* ── color-scheme ─────────────────────────────────────────────────── */
  color-scheme: dark;                   /* light for the light theme */

  /* ── Page background ramp ─────────────────────────────────────────── */
  --background:        ;               /* page/canvas base */
  --bg-raised:         ;               /* toolbar, slightly elevated surfaces */
  --bg-inset:          ;               /* sidebar, sunken elements */
  --bg-hover:          ;               /* hover state for rows/buttons */

  /* ── shadcn surface tokens (used by shadcn components) ────────────── */
  --card:              ;
  --card-foreground:   ;
  --popover:           ;               /* should === --panel typically */
  --popover-foreground:;
  --secondary:         ;               /* === --bg-hover typically */
  --secondary-foreground:;
  --muted:             ;               /* === --bg-hover typically */
  --accent:            ;               /* === --bg-hover typically */

  /* ── Widget panel ─────────────────────────────────────────────────── */
  --panel:             ;               /* widget card bg, dropdown panels */
  --panel-shadow:      ;               /* box-shadow for widget cards */

  /* ── Foreground ramp ──────────────────────────────────────────────── */
  --foreground:        ;               /* primary text */
  --muted-foreground:  ;               /* secondary text (shadcn alias for fg-dim) */
  --fg-dim:            ;               /* secondary/metadata text */
  --fg-faint:          ;               /* ghost text, placeholders, drag handles */
  --primary:           ;               /* shadcn primary (=== --foreground) */
  --primary-foreground:;               /* text on primary bg */
  --secondary-foreground:;
  --accent-foreground: ;

  /* ── Borders ──────────────────────────────────────────────────────── */
  --border:            ;               /* default border */
  --border-strong:     ;               /* elevated border (hover state) */
  --input:             ;               /* input border (=== --border) */
  --ring:              ;               /* focus ring (shadcn) */

  /* ── Accent ───────────────────────────────────────────────────────── */
  --c-accent:          ;               /* primary brand accent */
  --c-accent-soft:     ;               /* translucent accent for active bg */
  --c-on-accent:       ;               /* text on --c-accent bg */
  --c-glow:            ;               /* glow ring box-shadow */
  --chart-accent:      ;               /* LightweightCharts up-candle + area */

  /* ── Semantic data colors ─────────────────────────────────────────── */
  --c-green:           ;               /* positive, connected, support */
  --c-red:             ;               /* negative, losers, resistance, alerts */
  --c-yellow:          ;               /* warning / moderate */
  --c-amber:           ;               /* amber accent / high RVOL */
  --c-blue:            ;               /* informational / secondary series */
  --c-violet:          ;               /* tertiary accent */
  --c-command:         ;               /* command/action highlight */
  --pnl-positive:      ;               /* P&L positive (may differ from c-green in light) */
  --pnl-negative:      ;               /* P&L negative */
  --destructive:       ;               /* destructive action */

  /* ── Chart series palette (recharts/shadcn) ───────────────────────── */
  --chart-1:           ;
  --chart-2:           ;
  --chart-3:           ;
  --chart-4:           ;
  --chart-5:           ;

  /* ── Sidebar ──────────────────────────────────────────────────────── */
  --sidebar:           ;
  --sidebar-foreground:;
  --sidebar-primary:   ;
  --sidebar-primary-foreground:;
  --sidebar-accent:    ;
  --sidebar-accent-foreground:;
  --sidebar-border:    ;
  --sidebar-ring:      ;

  /* ── Layout ───────────────────────────────────────────────────────── */
  --radius:            0.625rem;       /* uniform across all themes */
  --grid-placeholder:  color-mix(in srgb, var(--c-accent) 18%, transparent);
}
```

### Current values for all 4 themes

| Var | carbon | warm | oled | light |
|---|---|---|---|---|
| **color-scheme** | dark | dark | dark | light |
| **--background** | #131519 | #141311 | #000000 | #f4f2ec |
| **--bg-raised** | #181b21 | #1c1a16 | #0c0e11 | #ffffff |
| **--bg-inset** | #0e1013 | #0f0e0c | #000000 | #eae6dd |
| **--bg-hover** | #1e2228 | #232019 | #14171c | #faf8f3 |
| **--card** | #181b21 | #1c1a16 | #0c0e11 | #ffffff |
| **--popover** | #181b21 | #1c1a16 | #0c0e11 | #ffffff |
| **--panel** | #181b21 | #1c1a16 | #0c0e11 | #ffffff |
| **--secondary** | #1e2228 | #232019 | #14171c | #faf8f3 |
| **--muted** | #1e2228 | #232019 | #14171c | #faf8f3 |
| **--accent** | #1e2228 | #232019 | #14171c | #faf8f3 |
| **--foreground** | #e9e7e3 | #e9e6e0 | #f4f3f0 | #1b1e23 |
| **--muted-foreground** | #9aa1ab | #a09a8d | #868d98 | #5f6873 |
| **--fg-dim** | #9aa1ab | #a09a8d | #868d98 | #5f6873 |
| **--fg-faint** | #616873 | #6b6457 | #555c66 | #8a8f99 |
| **--primary** | #e9e7e3 | #e9e6e0 | #f4f3f0 | #1b1e23 |
| **--border** | #262b33 | #2b2820 | #1e232b | #e4ded3 |
| **--border-strong** | #333a44 | #3a352a | #2b323c | #d3ccbe |
| **--input** | #262b33 | #2b2820 | #1e232b | #e4ded3 |
| **--ring** | #5ad1be | #e0975b | #5ad1be | #2f9e8c |
| **--c-accent** | #5ad1be | #e0975b | #5ad1be | #2f9e8c |
| **--c-accent-soft** | rgba(90,209,190,0.12) | rgba(224,151,91,0.13) | rgba(90,209,190,0.13) | rgba(47,158,140,0.12) |
| **--c-on-accent** | #0b0d10 | #0d0b08 | #000000 | #ffffff |
| **--c-glow** | 0 0 0 1px rgba(90,209,190,0.12) | 0 0 0 1px rgba(224,151,91,0.13) | 0 0 0 1px rgba(90,209,190,0.12) | 0 0 0 1px rgba(47,158,140,0.12) |
| **--chart-accent** | #5ad1be | #5ad1be | #5ad1be | #2f9e8c |
| **--c-green** | #5bc98a | #5bc98a | #5bc98a | #2f9e63 |
| **--c-red** | #ef6f6c | #ef6f6c | #ef6f6c | #d4544f |
| **--c-yellow** | #e3b341 | #e3b341 | #e3b341 | #b8862a |
| **--c-amber** | #e0975b | #e0975b | #e0975b | #bd7f43 |
| **--c-blue** | #6cb0ff | #6cb0ff | #6cb0ff | #3a7fd0 |
| **--c-violet** | #b08cff | #b08cff | #b08cff | #7a5cc0 |
| **--c-command** | *(missing — inherits #ff8c42 from :root)* | #ff8c42 | #ff8c42 | #c2410c |
| **--pnl-positive** | #5bc98a | #5bc98a | #5bc98a | #2f9e63 |
| **--pnl-negative** | #ef6f6c | #ef6f6c | #ef6f6c | #d4544f |
| **--destructive** | #ef6f6c | #ef6f6c | #ef6f6c | #d4544f |
| **--panel-shadow** | 0 1px 0 rgba(255,255,255,0.02), 0 8px 30px rgba(0,0,0,0.35) | 0 1px 0 rgba(255,255,255,0.02), 0 8px 30px rgba(0,0,0,0.40) | 0 1px 0 rgba(255,255,255,0.01), 0 8px 30px rgba(0,0,0,0.50) | 0 1px 2px rgba(0,0,0,0.04), 0 10px 30px rgba(60,50,30,0.08) |
| **--sidebar** | #0e1013 | #0f0e0c | #000000 | #eae6dd |
| **--sidebar-primary** | #5ad1be | #e0975b | #5ad1be | #2f9e8c |
| **--chart-1..5** | teal/blue/violet/yellow/amber | amber/teal/violet/yellow/blue | teal/blue/violet/yellow/amber | darker counterparts |

**Gaps in the current palette design (important for the redesign):**
1. `carbon` and `warm` both set `--chart-accent: #5ad1be` (teal). In warm, the brand accent is amber, but chart accent stays teal — this means the warm up-candle is teal, not amber. This is likely the intended behavior (chart readability), but should be a conscious decision.
2. All three dark themes share identical semantic colors (`--c-green`, `--c-red`, `--c-yellow`, `--c-amber`, `--c-blue`, `--c-violet`) — no per-theme tuning of semantics yet.
3. `--fg-dim` === `--muted-foreground` in all themes (same values). These could diverge in the redesign.
4. `--panel` === `--card` === `--popover` in all themes. They could be differentiated.

---

## 4. How theme switching works

### Mechanism
`ThemeProvider` (`frontend/components/theme-provider.tsx`) wraps `next-themes`' `NextThemesProvider` with:
- `attribute="data-theme"` — sets `data-theme="<name>"` on `<html>`
- `defaultTheme="carbon"`
- `themes={["carbon", "warm", "oled", "light"]}`
- `storageKey="hmt-theme-carbon"` — localStorage key
- `disableTransitionOnChange` — suppresses CSS transitions during switch (prevents flash)
- `enableSystem={false}` — no OS dark/light following
- `enableColorScheme={false}` — `next-themes` does NOT set `color-scheme`; each theme block sets its own `color-scheme` property instead

**Hotkey**: `ThemeHotkey` (in theme-provider.tsx) toggles `carbon ↔ light` on `D` keypress.

**Theme switcher**: `workspace/theme-switcher.tsx` — a `DropdownMenu` with 4 items; calls `setTheme(id)` from `useTheme()`. Swatches use `data-theme={id}` directly on the swatch span, so each swatch renders in its own theme's colors via CSS cascade. Does NOT import or use `resolvedTheme`; relies on `mounted` guard to avoid hydration mismatch.

### How a JS-configured component (the chart) should observe theme changes

The chart (`tv-light-chart.tsx`) currently does NOT import `useTheme` and does NOT observe theme changes.

**Correct fix pattern:**

```tsx
// In TvLightChart component:
import { useTheme } from "next-themes";

// Inside the component:
const { resolvedTheme } = useTheme();

// Option A — add resolvedTheme to chart-creation effect deps (simpler, destroys+recreates):
useEffect(() => {
  // ... existing createChart() code ...
}, [timeframe.value, style, resolvedTheme]);   // <-- add resolvedTheme

// Option B — separate color-update effect (preserves zoom/scroll, more efficient):
useEffect(() => {
  if (!chartRef.current || !mainRef.current) return;
  const text = resolveCssColor("--foreground", "#c8c6c3");
  const border = resolveCssColor("--border", "rgba(255,255,255,0.09)");
  const accent = resolveCssColor("--chart-accent", "#3d8f7a");
  const down = resolveCssColor("--c-red", "#9f2f2d");      // needs --c-red in palette
  chartRef.current.applyOptions({
    layout: { textColor: text },
    grid: { vertLines: { color: border }, horzLines: { color: border } },
    rightPriceScale: { borderColor: border },
    timeScale: { borderColor: border },
  });
  mainRef.current.applyOptions({
    upColor: accent, downColor: down,
    borderUpColor: accent, borderDownColor: down,
    wickUpColor: accent, wickDownColor: down,
  });
  // Volume bar colors can't be updated in-place without re-setData;
  // re-fetch or recompute from barsRef.current if needed.
}, [resolvedTheme]);
```

Option A is simpler and ensures full coherence (all colors, including volume bars, update). Option B is faster but needs extra work for volume series. **Recommend Option A** given the chart already re-creates on timeframe/style changes.

---

## 5. Key decisions to flag for the redesign

1. **Chart down-candle color** needs a new CSS var (`--chart-down` or just reuse `--c-red`). All themes need it. Currently hardcoded `#9f2f2d` which is carbon's red — lighter themes need a different red.

2. **carbon vs oled visual differentiation**: Both use teal accents. The main differences are `--background` (`#131519` vs `#000000`) and `--panel` (`#181b21` vs `#0c0e11`). If the user perceives widgets as "not changing" between carbon and oled, it may be a contrast perception issue — the surfaces are similar in hue/chroma, differing mainly in lightness.

3. **warm `--chart-accent` anomaly**: warm sets `--chart-accent: #5ad1be` (teal) while `--c-accent: #e0975b` (amber). This means warm's up-candles are teal regardless of the brand accent. Intentional? In the redesign, consider whether `--chart-accent` should follow `--c-accent`.

4. **New vars needed for the overhaul**:
   - `--chart-down` — currently always hardcoded; needs to be a theme var (carbon/oled: `#ef6f6c`; warm: `#ef6f6c`; light: `#d4544f`)
   - `--c-danger` — currently undefined; either alias to `--c-red` or define distinctly
   - Potentially `--chart-bg` if chart background needs to differ from `--background`

5. **Session level colors** in the chart are also hardcoded in `sessionLevelColor()`. These need to reference the theme vars too (support → `--c-green`, resistance → `--c-red`, poc → `--c-amber`, prior hi/lo → `--fg-faint`).
