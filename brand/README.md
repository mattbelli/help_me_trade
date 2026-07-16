# brand/ — the `stand_in` mark

Chosen 2026-07-05 after five exploration rounds (full record + rationale: `logo-exploration.html`
— open in a browser). Replaces the three-bar candlestick mark currently in
`mobile/src/components/ui/logo.tsx`.

## The mark

Two vertical bars, identical outer footprint, offset upward left→right. The **hollow** bar is the
user — paused, stepped away. The **solid** bar is the agent — standing in, one step higher because
it kept working. Reads as a pause symbol at a glance (the deliberate, contrarian claim: this
product lets you *stop* watching), and as a proxy/handoff on the second look.

## Geometry (canonical, viewBox 0 0 120 120)

| element | spec |
|---|---|
| hollow bar (left) | outer 20×54 at (34, 38); hole carved inward, wall **5.5** (evenodd path, never an outer stroke) |
| solid bar (right) | 20×54 at (66, 26) |
| vertical offset | 12 · horizontal gap 12 (offset = gap, keep this rhythm) |
| wall ratio | 5.5 / 20 = **27.5%** of bar width — preserve in any redrawn cut |

Canonical paths:

```svg
<path d="M34 38 h20 v54 h-20 Z M39.5 43.5 h9 v43 h-9 Z" fill-rule="evenodd"/>  <!-- hollow -->
<rect x="66" y="26" width="20" height="54"/>                                    <!-- solid -->
```

## Color

Greyscale only for now — color is a later decision, applied deliberately, not inherited from the
old teal/amber mark.

| context | hollow | solid |
|---|---|---|
| dark themes (carbon / warmCarbon / oled) | `#9aa3af` | `#e9ebee` |
| light theme | `#5f6773` | `#1f2226` |
| single-color contexts | `currentColor` both (hierarchy comes from hollow vs solid) |

## Size rules

- **≥22px**: use the true hollow cut. At 22pt on a 3× phone screen the wall is ~2.7 physical px — holds fine.
- **≤16px (favicons)**: use `stand_in-small.svg` — flat two-grey fallback (`#525a66` + `#e9ebee`).
  The hole closes at this size; the quiet grey *is* the hollow, at lower resolution.
- App icon: not yet cut. Derive from the canonical geometry; revisit padding/scale in its own pass.

## Files

- `logo-exploration.html` — the full five-round exploration doc (concepts, kill strip, variants, verdicts)
- `stand_in-dark.svg` / `stand_in-light.svg` — themed masters
- `stand_in-mono.svg` — currentColor cut for web
- `stand_in-small.svg` — flat fallback for tiny sizes

## Next steps (not yet done)

1. Rewrite `mobile/src/components/ui/logo.tsx` to this mark (two paths, `fill-rule="evenodd"`,
   react-native-svg; tint from `useColors()` — greys above map to theme neutrals, not accent/warm).
2. Live with it in the PageHeading for a day on device before committing to it.
3. Web dashboard favicon + header swap (use mono/small cuts).
4. App icon pass, then the color question.
