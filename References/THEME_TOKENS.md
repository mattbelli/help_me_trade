# Theme tokens — the knob model + core-theme snapshot

*The theme editor was rebuilt (2026-07-12) around ~27 clearly-named "knobs" in 4 sections
(Surface · Lines · Chart · Text). This doc is the map + the snapshot to rebuild the 4 core
themes if we ever need to. The **knob vars are canonical** — each `[data-theme=…]` block in
`globals.css` sets only knobs; `:root` derives the legacy/shadcn var names from them so the
~1,500 existing consumers keep working untouched.*

## The knobs (section → var → what it paints)

### SURFACE (back → front)
| Knob | Var | Paints |
|---|---|---|
| Canvas | `--surface-canvas` | deepest app background |
| Grid dots | `--surface-grid-dot` | workspace dot grid (rendered ~5% opacity) |
| Placement preview | `--surface-drop` | drag/drop drop-zone fill |
| Toolbars | `--surface-toolbar` | top toolbar + bottom bar |
| Header | `--surface-header` | each widget's title bar |
| Widget body | `--surface-widget` | widget / card / popover surface |
| Hover | `--surface-hover` | row & control hover fill |
| Inset | `--surface-inset` | sunken wells, toolbar tiles, sidebar rails |

### LINES
| Knob | Var | Paints |
|---|---|---|
| Border | `--line-border` | element outlines / outer borders |
| Divider | `--line-divider` | inner hairlines (row separators, in-widget dividers) |

### CHART
| Knob | Var | Paints |
|---|---|---|
| Chart background | `--chart-bg` | candle chart canvas |
| Chart grid | `--chart-grid` | gridlines inside charts |
| Candle up | `--chart-up` | up candles / up volume |
| Candle down | `--chart-down` | down candles / down volume |
| Chart line | `--chart-line` | sparklines, equity/pnl curves |

### TEXT
| Knob | Var | Paints |
|---|---|---|
| Primary | `--text-primary` | main body text |
| Title | `--text-title` | widget / section titles |
| Column header | `--text-header` | SYM/LAST/Δ% table label rows |
| Secondary | `--text-secondary` | secondary values, timestamps |
| Faint | `--text-faint` | placeholders, disabled, hairline labels, resize handles |
| Accent | `--text-accent` | tickers, links, active controls; drives focus ring + accent-soft |
| On accent | `--text-on-accent` | text on accent-filled buttons |
| Positive | `--pos` | gains, buy side, up tick-flash, positive fills |
| Negative | `--neg` | losses, sell side, errors, destructive |
| Warning | `--warn` | warnings, earnings, console `›` prompt |
| Info | `--info` | info, open-order status |

## Legacy var → knob (`:root` aliases; consumers unchanged)
`--background,--chart-background`→canvas · `--panel,--card,--popover`→widget ·
`--bg-raised`→toolbar · `--bg-hover,--muted,--secondary,--accent`→hover · `--bg-inset,--sidebar`→inset ·
`--border,--input`→line-border · `--foreground,--card-foreground,--popover-foreground,--primary,--secondary-foreground,--accent-foreground`→text-primary ·
`--fg-widget-title`→text-title · `--fg-table-header`→text-header · `--fg-dim,--muted-foreground`→text-secondary · `--fg-faint`→text-faint ·
`--c-accent,--ring`→text-accent · `--c-on-accent,--primary-foreground`→text-on-accent ·
`--c-green,--c-green-bg,--pnl-positive`→pos · `--c-red,--c-danger,--destructive,--pnl-negative`→neg ·
`--c-amber,--c-yellow,--c-command`→warn · `--c-blue,--c-cyan,--c-violet`→info · `--chart-accent`→chart-up · `--grid-placeholder`→drop

**Removed (were dead):** `--c-glow`, `--chart-1..5`, `--sidebar-foreground/-primary/-primary-foreground/-accent/-accent-foreground/-border/-ring`.
**Kept per-theme, non-knob:** `--border-strong`, `--panel-shadow`, `--widget-hover-shadow`, `--scrim`, `--grain`/`--grain-opacity` (Ember), `--tick-flash-mix`/`--md-paper-bg`/`--scroll-shadow` (Light).

## Core theme snapshot (knob values — the rebuild source)

| Knob | carbon | Ember (warm) | oled | light |
|---|---|---|---|---|
| surface-canvas | `#131519` | `#202020` | `#000000` | `#f5f6f8` |
| surface-grid-dot | `#e9e7e3` | `#d3ccbe` | `#e9e7e3` | `#1b1f24` |
| surface-drop | *mix(accent 18%)* | *mix* | *mix* | *mix* |
| surface-toolbar | `#181b21` | `#262626` | `#0d0d0f` | `#ffffff` |
| surface-header | `#181b21` | `#262626` | `#0d0d0f` | `#ffffff` |
| surface-widget | `#181b21` | `#262626` | `#0d0d0f` | `#ffffff` |
| surface-hover | `#1e2228` | `#2d2d2c` | `#17171a` | `#eef0f3` |
| surface-inset | `#0e1013` | `#161617` | `#000000` | `#eceef1` |
| line-border | `#262b33` | `#353533` | `#26262a` | `#e2e5ea` |
| line-divider | `#262b33` | `#353533` | `#26262a` | `#e2e5ea` |
| border-strong | `#333a44` | `#454440` | `#34343a` | `#cdd2da` |
| chart-bg | `#131519` | `#202020` | `#000000` | `#f5f6f8` |
| chart-grid | `#262b33` | `#353533` | `#26262a` | `#e2e5ea` |
| chart-up | `#5ad1be` | `#97a06e` | `#5bc98a` | `#1f9d57` |
| chart-down | `#ef6f6c` | `#c47767` | `#ef6f6c` | `#d23b34` |
| chart-line | `#5ad1be` | `#97a06e` | `#5bc98a` | `#1f9d57` |
| text-primary | `#e9e7e3` | `#d3ccbe` | `#e9e7e3` | `#1b1f24` |
| text-title | `#e9e7e3` | `#d3ccbe` | `#e9e7e3` | `#1b1f24` |
| text-header | `#616873` | `#7f766a` | `#5c5c63` | `#878e98` |
| text-secondary | `#9aa1ab` | `#b5ac9e` | `#9b9ba3` | `#59616c` |
| text-faint | `#616873` | `#7f766a` | `#5c5c63` | `#878e98` |
| text-accent | `#5ad1be` | `#d99a52` | `#9b6bff` | `#2563eb` |
| text-on-accent | `#0b0d10` | `#161617` | `#000000` | `#ffffff` |
| pos | `#5bc98a` | `#a3b565` | `#5bc98a` | `#1f9d57` |
| neg | `#ef6f6c` | `#d9705c` | `#ef6f6c` | `#d23b34` |
| warn | `#e0975b` | `#e8a24a` | `#e0975b` | `#c77b3a` |
| info | `#6cb0ff` | `#6f97a8` | `#6cb0ff` | `#3b82c4` |

*Ember opts into film grain (`--grain-opacity: 0.08`). Light overrides `--tick-flash-mix: 16%`,
`--md-paper-bg: #f6f3ec`, softer `--scrim`/`--scroll-shadow`. oled uses a hairline
`--panel-shadow` ring + `--c-accent-soft` at 16%.*
