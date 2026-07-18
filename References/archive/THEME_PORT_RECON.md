# Theme Port Recon: Mobile → Web

*Generated 2026-06-30. Source files: `mobile/src/theme/themes.ts` (branch `origin/feat/mobile-app`),
`frontend/app/globals.css`, `frontend/lib/workspace/themes.ts`, `frontend/components/theme-provider.tsx`,
`frontend/components/workspace/theme-switcher.tsx`.*

---

## 1. Mobile palette definitions (`mobile/src/theme/themes.ts`)

The mobile system defines four concrete palettes (all in hex) plus a `system` pseudo-mode.
`makeColors()` (in `make-colors.ts`) derives washes and computed tokens from the 18 raw palette fields.

### 1a. Token structure (`make-colors.ts` — RawPalette interface)
```
bg, bgInset, surface, surfaceRaised, scrim
text, textMuted, textFaint, onAccent
border, borderStrong
accent, warm (secondary accent)
positive, negative, warning, info
chart[5]
```
Derived by `makeColors()`: `surfaceHover = surfaceRaised`, `accentWash = rgba(accent, 0.13)`,
`warmWash = rgba(warm, 0.22)`, `positiveWash = rgba(positive, 0.14)`, `negativeWash = rgba(negative, 0.14)`,
`focus = accent`, `danger = negative`.

### 1b. Carbon (dark, teal-led)
```
bg:            #131519
bgInset:       #0e1013
surface:       #181b21
surfaceRaised: #1e2228
text:          #e9e7e3
textMuted:     #9aa1ab
textFaint:     #616873
onAccent:      #0b0d10
border:        #262b33
borderStrong:  #333a44
accent:        #5ad1be  (teal primary)
warm:          #e0975b  (amber secondary)
positive:      #5bc98a
negative:      #ef6f6c
warning:       #e3b341
info:          #6cb0ff
chart:         [#5ad1be, #6cb0ff, #b08cff, #e3b341, #e0975b]
```

### 1c. Warm / warmCarbon (dark, amber-led)
```
bg:            #141311
bgInset:       #0f0e0c
surface:       #1c1a16
surfaceRaised: #232019
text:          #e9e6e0
textMuted:     #a09a8d
textFaint:     #6b6457
onAccent:      #0d0b08
border:        #2b2820
borderStrong:  #3a352a
accent:        #e0975b  (amber PRIMARY — flipped vs carbon)
warm:          #5ad1be  (teal secondary)
positive:      #5bc98a
negative:      #ef6f6c
warning:       #e3b341
info:          #6cb0ff
chart:         [#e0975b, #5ad1be, #b08cff, #e3b341, #6cb0ff]
```

### 1d. OLED (dark, true-black)
```
bg:            #000000
bgInset:       #000000
surface:       #0c0e11
surfaceRaised: #14171c
text:          #f4f3f0
textMuted:     #868d98
textFaint:     #555c66
onAccent:      #000000
border:        #1e232b
borderStrong:  #2b323c
accent:        #5ad1be  (teal, same as carbon)
warm:          #e0975b
positive:      #5bc98a
negative:      #ef6f6c
warning:       #e3b341
info:          #6cb0ff
chart:         [#5ad1be, #6cb0ff, #b08cff, #e3b341, #e0975b]
```

### 1e. Light
```
bg:            #f4f2ec
bgInset:       #eae6dd
surface:       #ffffff
surfaceRaised: #faf8f3
text:          #1b1e23
textMuted:     #5f6873
textFaint:     #8a8f99
onAccent:      #ffffff
border:        #e4ded3
borderStrong:  #d3ccbe
accent:        #2f9e8c  (teal, darkened for light bg)
warm:          #bd7f43  (amber, darkened)
positive:      #2f9e63
negative:      #d4544f
warning:       #b8862a
info:          #3a7fd0
chart:         [#2f9e8c, #3a7fd0, #7a5cc0, #b8862a, #bd7f43]
```

### 1f. Auto (system) behavior
`system` mode (`mobile/src/theme/theme-context.tsx`): resolves to `light` if `colorScheme === 'light'`, else
`carbon`. The picker shows: **Auto | Carbon | Warm | OLED | Light** (in that order).
Mobile key `warmCarbon` → displayed label "Warm".

---

## 2. Web theme system

### 2a. Current theme list
- **`frontend/lib/workspace/themes.ts` line 1**: `WORKSPACE_THEMES = ["carbon", "ember", "paper"]` — these 3 appear in the ThemeSwitcher UI.
- **`frontend/components/theme-provider.tsx` line 14**: `themes={["carbon", "ember", "paper", "light", "dark"]}` — next-themes knows 5 themes but only 3 are surfaced in the UI.
- **`globals.css` line 9**: `@custom-variant dark` covers `.dark *, [data-theme="carbon"] *, [data-theme="ember"] *, [data-theme="dark"] *`
- **`globals.css` line 395**: `.dark` class — identical to `[data-theme="dark"]`, a legacy shadcn block not wired in the ThemeProvider.
- The `D` hotkey (`theme-provider.tsx` line 62) toggles between paper/light ↔ carbon.
- `enableSystem={false}` — no Auto/OS-tracking currently.

### 2b. CSS var structure (the set every theme block should define)
From `:root` / `[data-theme="carbon"]` (lines 58–173), the full token list is:

**shadcn tokens (all themes MUST define these):**
`--background, --foreground, --card, --card-foreground, --popover, --popover-foreground,
--primary, --primary-foreground, --secondary, --secondary-foreground,
--muted, --muted-foreground, --accent, --accent-foreground, --destructive,
--border, --input, --ring,
--chart-1, --chart-2, --chart-3, --chart-4, --chart-5,
--radius,
--sidebar, --sidebar-foreground, --sidebar-primary, --sidebar-primary-foreground,
--sidebar-accent, --sidebar-accent-foreground, --sidebar-border, --sidebar-ring`

**App custom tokens (all themes MUST define these):**
`--chart-accent` — ⚠️ drives chart up-candle colour
`--pnl-positive, --pnl-negative` — ⚠️ badge green/red
`--panel, --bg-raised, --bg-inset, --bg-hover`
`--border-strong`
`--fg-dim, --fg-faint`
`--c-accent` — brand teal or amber (interactive elements, focus rings)
`--c-accent-soft` — rgba wash of accent
`--c-violet, --c-blue, --c-green, --c-red, --c-yellow, --c-amber`
`--c-on-accent` — text on filled accent button
`--c-command` — command badge colour (dark: `#ff8c42`; light: `#c2410c`)
`--panel-shadow` — box-shadow for raised panels
`--c-glow` — focus glow box-shadow
`--grid-placeholder` — computed via `color-mix`

### 2c. Gaps in current light and dark blocks
`[data-theme="light"]` (lines 292–339) and `[data-theme="dark"]` (lines 341–388) are thin shadcn-template blocks.
They define only `--bg-raised, --bg-hover, --fg-dim, --fg-faint, --c-accent, --c-accent-soft, --c-on-accent, --c-green, --c-red` — missing:
`--bg-inset, --border-strong, --c-violet, --c-blue, --c-yellow, --c-amber, --c-command, --panel, --panel-shadow, --c-glow, --grid-placeholder`.

⚠️ **Stray hardcode**: `[data-theme="light"]` line 335 and `[data-theme="dark"]` line 384 both have
`--c-accent-soft: rgba(90, 209, 190, 0.12)` — that is carbon's teal wash, wrong for both themes.

`.dark` class (lines 395–432) is a dead block: same values as `[data-theme="dark"]`, not referenced by ThemeProvider, not in WORKSPACE_THEMES. Can be removed when porting.

---

## 3. Port map — carbon is already aligned; three themes need changes

### Mapping table (mobile token → web CSS var)
```
mobile bg            → --background, --primary-foreground
mobile bgInset       → --bg-inset, --sidebar
mobile surface       → --card, --popover, --panel, --bg-raised
mobile surfaceRaised → --secondary, --muted, --accent (shadcn hover bg), --bg-hover,
                        --sidebar-accent
mobile text          → --foreground, --card-foreground, --popover-foreground, --primary,
                        --secondary-foreground, --muted-foreground* (→ textMuted instead),
                        --accent-foreground, --sidebar-foreground,
                        --sidebar-accent-foreground, --sidebar-primary-foreground* (bgInset)
mobile textMuted     → --muted-foreground, --fg-dim
mobile textFaint     → --fg-faint
mobile onAccent      → --c-on-accent, --sidebar-primary-foreground
mobile border        → --border, --input, --sidebar-border
mobile borderStrong  → --border-strong
mobile accent        → --ring, --chart-1, --chart-accent, --c-accent,
                        --sidebar-primary, --sidebar-ring
mobile accentWash    → --c-accent-soft
mobile warm          → --c-amber
mobile positive      → --pnl-positive, --c-green
mobile negative      → --destructive, --pnl-negative, --c-red
mobile warning       → --c-yellow
mobile info          → --c-blue
mobile chart[1]      → --chart-2
mobile chart[2]      → --chart-3  (#b08cff → --c-violet)
mobile chart[3]      → --chart-4
mobile chart[4]      → --chart-5

No mobile equivalent → --c-command, --panel-shadow, --c-glow (keep as computed/hardcoded per theme)
No web equivalent    → mobile scrim, surfaceHover, warmWash, accentWash, positiveWash, negativeWash
```

---

### 3a. Carbon → no changes needed
Web `[data-theme="carbon"]` already matches mobile carbon exactly.
The only web-only extras are `--c-command: #ff8c42`, `--panel-shadow`, `--c-glow` (keep as-is).
**Action: none.** Confirm `--c-command` stays in the block.

### 3b. Warm — rename/replace ember
Web's `[data-theme="ember"]` is the warmCarbon concept but with a different amber shade
(`#e89253` vs mobile's `#e0975b`) and slightly different backgrounds.
Recommendation: **rename `[data-theme="ember"]` → `[data-theme="warm"]`** and replace its palette with
mobile warmCarbon values exactly. Update `WORKSPACE_THEMES`, `WORKSPACE_THEME_LABELS`, and
`ThemeProvider themes` array.

Full `[data-theme="warm"]` block:
```css
[data-theme="warm"] {
    color-scheme: dark;
    --background: #141311;
    --foreground: #e9e6e0;
    --card: #1c1a16;
    --card-foreground: #e9e6e0;
    --popover: #1c1a16;
    --popover-foreground: #e9e6e0;
    --primary: #e9e6e0;
    --primary-foreground: #141311;
    --secondary: #232019;
    --secondary-foreground: #e9e6e0;
    --muted: #232019;
    --muted-foreground: #a09a8d;
    --accent: #232019;
    --accent-foreground: #e9e6e0;
    --destructive: #ef6f6c;
    --border: #2b2820;
    --input: #2b2820;
    --ring: #e0975b;
    --chart-1: #e0975b;
    --chart-2: #5ad1be;
    --chart-3: #b08cff;
    --chart-4: #e3b341;
    --chart-5: #6cb0ff;
    --chart-accent: #e0975b;
    --pnl-positive: #5bc98a;
    --pnl-negative: #ef6f6c;
    --radius: 0.625rem;
    --sidebar: #0f0e0c;
    --sidebar-foreground: #e9e6e0;
    --sidebar-primary: #e0975b;
    --sidebar-primary-foreground: #0f0e0c;
    --sidebar-accent: #232019;
    --sidebar-accent-foreground: #e9e6e0;
    --sidebar-border: #2b2820;
    --sidebar-ring: #e0975b;
    --panel: #1c1a16;
    --bg-raised: #1c1a16;
    --bg-inset: #0f0e0c;
    --bg-hover: #232019;
    --border-strong: #3a352a;
    --fg-dim: #a09a8d;
    --fg-faint: #6b6457;
    --c-accent: #e0975b;
    --c-accent-soft: rgba(224, 151, 91, 0.13);
    --c-violet: #b08cff;
    --c-blue: #6cb0ff;
    --c-green: #5bc98a;
    --c-red: #ef6f6c;
    --c-yellow: #e3b341;
    --c-amber: #5ad1be;  /* secondary in warm = teal */
    --c-command: #ff8c42;
    --c-on-accent: #0d0b08;
    --panel-shadow: 0 1px 0 rgba(255, 255, 255, 0.02), 0 8px 30px rgba(0, 0, 0, 0.4);
    --c-glow: 0 0 0 1px rgba(224, 151, 91, 0.13);
    --grid-placeholder: color-mix(in srgb, var(--c-accent) 18%, transparent);
}
```

### 3c. OLED — new block
`[data-theme="oled"]` does not exist in the web. Add it. Note `--sidebar` = `#000000` (no inset contrast
needed on OLED — battery savings) and `--c-on-accent: #000000` (text on teal button).

Full `[data-theme="oled"]` block:
```css
[data-theme="oled"] {
    color-scheme: dark;
    --background: #000000;
    --foreground: #f4f3f0;
    --card: #0c0e11;
    --card-foreground: #f4f3f0;
    --popover: #0c0e11;
    --popover-foreground: #f4f3f0;
    --primary: #f4f3f0;
    --primary-foreground: #000000;
    --secondary: #14171c;
    --secondary-foreground: #f4f3f0;
    --muted: #14171c;
    --muted-foreground: #868d98;
    --accent: #14171c;
    --accent-foreground: #f4f3f0;
    --destructive: #ef6f6c;
    --border: #1e232b;
    --input: #1e232b;
    --ring: #5ad1be;
    --chart-1: #5ad1be;
    --chart-2: #6cb0ff;
    --chart-3: #b08cff;
    --chart-4: #e3b341;
    --chart-5: #e0975b;
    --chart-accent: #5ad1be;
    --pnl-positive: #5bc98a;
    --pnl-negative: #ef6f6c;
    --radius: 0.625rem;
    --sidebar: #000000;
    --sidebar-foreground: #f4f3f0;
    --sidebar-primary: #5ad1be;
    --sidebar-primary-foreground: #000000;
    --sidebar-accent: #14171c;
    --sidebar-accent-foreground: #f4f3f0;
    --sidebar-border: #1e232b;
    --sidebar-ring: #5ad1be;
    --panel: #0c0e11;
    --bg-raised: #0c0e11;
    --bg-inset: #000000;
    --bg-hover: #14171c;
    --border-strong: #2b323c;
    --fg-dim: #868d98;
    --fg-faint: #555c66;
    --c-accent: #5ad1be;
    --c-accent-soft: rgba(90, 209, 190, 0.13);
    --c-violet: #b08cff;
    --c-blue: #6cb0ff;
    --c-green: #5bc98a;
    --c-red: #ef6f6c;
    --c-yellow: #e3b341;
    --c-amber: #e0975b;
    --c-command: #ff8c42;
    --c-on-accent: #000000;
    --panel-shadow: 0 1px 0 rgba(255, 255, 255, 0.01), 0 8px 30px rgba(0, 0, 0, 0.5);
    --c-glow: 0 0 0 1px rgba(90, 209, 190, 0.12);
    --grid-placeholder: color-mix(in srgb, var(--c-accent) 18%, transparent);
}
```

### 3d. Light — replace oklch block with mobile light hex values
Current `[data-theme="light"]` uses oklch colors and is missing many vars. Replace entirely with:

```css
[data-theme="light"] {
    color-scheme: light;
    --background: #f4f2ec;
    --foreground: #1b1e23;
    --card: #ffffff;
    --card-foreground: #1b1e23;
    --popover: #ffffff;
    --popover-foreground: #1b1e23;
    --primary: #1b1e23;
    --primary-foreground: #f4f2ec;
    --secondary: #faf8f3;
    --secondary-foreground: #1b1e23;
    --muted: #faf8f3;
    --muted-foreground: #5f6873;
    --accent: #faf8f3;
    --accent-foreground: #1b1e23;
    --destructive: #d4544f;
    --border: #e4ded3;
    --input: #e4ded3;
    --ring: #2f9e8c;
    --chart-1: #2f9e8c;
    --chart-2: #3a7fd0;
    --chart-3: #7a5cc0;
    --chart-4: #b8862a;
    --chart-5: #bd7f43;
    --chart-accent: #2f9e8c;
    --pnl-positive: #2f9e63;
    --pnl-negative: #d4544f;
    --radius: 0.625rem;
    --sidebar: #eae6dd;
    --sidebar-foreground: #1b1e23;
    --sidebar-primary: #2f9e8c;
    --sidebar-primary-foreground: #ffffff;
    --sidebar-accent: #faf8f3;
    --sidebar-accent-foreground: #1b1e23;
    --sidebar-border: #e4ded3;
    --sidebar-ring: #2f9e8c;
    --panel: #ffffff;
    --bg-raised: #ffffff;
    --bg-inset: #eae6dd;
    --bg-hover: #faf8f3;
    --border-strong: #d3ccbe;
    --fg-dim: #5f6873;
    --fg-faint: #8a8f99;
    --c-accent: #2f9e8c;
    --c-accent-soft: rgba(47, 158, 140, 0.12);
    --c-violet: #7a5cc0;
    --c-blue: #3a7fd0;
    --c-green: #2f9e63;
    --c-red: #d4544f;
    --c-yellow: #b8862a;
    --c-amber: #bd7f43;
    --c-command: #c2410c;
    --c-on-accent: #ffffff;
    --panel-shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 10px 30px rgba(60, 50, 30, 0.08);
    --c-glow: 0 0 0 1px rgba(47, 158, 140, 0.12);
    --grid-placeholder: color-mix(in srgb, var(--c-accent) 18%, transparent);
}
```

---

## 4. What to do with web-only extras

| Web theme | Recommendation | Rationale |
|---|---|---|
| `ember` | Rename → `warm`, replace palette with warmCarbon values | Same concept, different hex; mobile warmCarbon is the canonical version |
| `paper` | Drop | Mobile `light` covers the parchment light theme; `paper` is redundant and close but not canonical |
| `dark` (block) | Drop | Not in mobile set; never surfaced in UI; `carbon` is the canonical dark |
| `.dark` class | Remove | Dead legacy block, duplicate of dropped `[data-theme="dark"]`; shadcn's dark mode class handled by `color-scheme: dark` on each theme |

---

## 5. Switcher list changes

### `frontend/lib/workspace/themes.ts`
```ts
// Change from:
export const WORKSPACE_THEMES = ["carbon", "ember", "paper"] as const;
export const WORKSPACE_THEME_LABELS: Record<WorkspaceTheme, string> = {
  carbon: "Carbon", ember: "Ember", paper: "Paper"
};
// Change to:
export const WORKSPACE_THEMES = ["carbon", "warm", "oled", "light"] as const;
export const WORKSPACE_THEME_LABELS: Record<WorkspaceTheme, string> = {
  carbon: "Carbon", warm: "Warm", oled: "OLED", light: "Light"
};
```

### `frontend/components/theme-provider.tsx`
```tsx
// Change themes list and default; add enableSystem for Auto:
themes={["carbon", "warm", "oled", "light"]}
// Remove 'ember', 'paper', 'dark' from the list
```

### `globals.css` line 9 — update `@custom-variant dark`:
```css
@custom-variant dark (&:is(.dark *, [data-theme="carbon"] *, [data-theme="warm"] *, [data-theme="oled"] *));
```
(Remove `ember`, `dark`; add `warm`, `oled`.)

### `ThemeSwitcher` THEME_SWATCH record
Must be extended to include `warm`, `oled`, `light` keys (currently only `carbon`, `ember`, `paper`).

### D hotkey in `theme-provider.tsx` line 61–64
Currently toggles paper/light ↔ carbon. Update to toggle `light` ↔ `carbon`:
```tsx
setTheme(resolvedTheme === "light" ? "carbon" : "light")
```

### Auto option (optional — not strictly required for V1 port)
Mobile `system` → `light` (OS light) or `carbon` (OS dark). To add on web:
flip `enableSystem={true}` in ThemeProvider and set `defaultTheme="system"` with
`themes={["system", "carbon", "warm", "oled", "light"]}`. Add "Auto" to `WORKSPACE_THEMES`.

---

## 6. Flags / awkward mappings

- **`--c-amber` in warm theme**: In warm, the secondary (teal) is stored as `--c-amber` because the palette is
  inverted. This is semantically odd. Consider whether components use `--c-amber` for amber-specific meaning or as
  "secondary accent" — check usages before assuming this is safe.

- **`scrim` (mobile) has no web CSS var**: Mobile uses `colors.scrim` for modal overlays. Web uses Tailwind's
  backdrop-blur or shadcn dialog styles. No action needed but note the gap.

- **`warmWash` / `positiveWash` / `negativeWash`**: Mobile derives these from hex; web has no equivalent vars.
  No action needed — web uses `--c-accent-soft` for the single wash variant.

- **`--radius`**: Carbon, warm, oled all use `0.625rem`; mobile light uses same; current web light block had
  `0.5rem` — corrected to `0.625rem` in the map above for consistency.

- **`paper` is very close to mobile `light`** (`#f3f1ec` vs `#f4f2ec` bg; `#14a08b` vs `#2f9e8c` accent).
  If backward compat with any `paper`-keyed stored preference is needed, an alias or migration shim may be
  wanted (rename old `paper` pref → `light` on load).

- **Chat-restyle over-reach**: The stale `[data-theme="light"]` and `[data-theme="dark"]` blocks defined
  `--c-accent`, `--c-green`, `--c-red` with incorrect values (wrong colours, wrong wash). The port above
  cleans this up for `light`. Since `dark` is dropped, that block's stray vars disappear with it.

---

## 7. File touch list for the implementation

```
frontend/app/globals.css                                  — replace light, drop dark/.dark, add warm+oled, rename ember→warm
frontend/lib/workspace/themes.ts                          — new WORKSPACE_THEMES + WORKSPACE_THEME_LABELS
frontend/components/theme-provider.tsx                    — update themes list; fix D hotkey
frontend/components/workspace/theme-switcher.tsx          — add warm/oled/light to THEME_SWATCH
```
