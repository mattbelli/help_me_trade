# frontend rules — read before editing anything under `frontend/`

Rules, not suggestions. Every path, token and line number below is verified against this tree.

## 1. Exemplars — copy, don't invent
Look in `components/workspace/primitives/` FIRST — a shared `DataTable` primitive is landing there;
if it exists, use it instead of hand-rolling. Otherwise extend one of these three. Never invent a
new table or row layout.
- `components/workspace/widgets/positions-widget.tsx` — table ergonomics: the `COLUMNS` array with
  declared `align` (:40-53), sortable `<th>` buttons (:249-273), `group/row` hover (:127).
- `components/workspace/widgets/filings-widget.tsx` — backend vocabulary → human words
  (`kindLabel` :62-75, `normalizePerson` :101), and the width-absorber idiom
  `w-full max-w-0 truncate` (:253-258 — read the comment). Its `TH` (:29) is drifted to
  `--fg-faint`; do not copy that line.
- `components/workspace/widgets/options-flow-widget.tsx` — CSS-grid row widgets
  (`gridTemplateColumns` :378-379, :412-413), plain lowercase colored words (:477-481), provenance
  in `title=` tooltips (`scoreTitle` :228-231).

## 2. Tokens — the only vocabulary
Every color, size and space comes from a CSS var DEFINED in `app/globals.css`. Never invent a var
name: an agent invented `--text-muted` and `--border-subtle` — 22 uses shipped silently broken and
are still live in `index-pressure-widget.tsx`. Prove the var exists before using it:
`grep -n -- "--your-var:" app/globals.css`.

Type ladder — 6 fixed steps, defined `app/globals.css:48-73`. Write it
`text-[length:var(--fs-body)]`, never `text-[12px]`.
- `--fs-micro` 10px — micro-labels, badges, column headers
- `--fs-table` 11px — table body & secondary text
- `--fs-body` 12px — primary body / rows
- `--fs-title` 13px — widget & section titles; the exemplar tables set this on `<table>`
- `--fs-emph` 15px — toolbar tiles, chart price
- `--fs-hero` 20px — hero numbers
Off-ladder, single-purpose only: `--fs-widget-title` 14px (widget header), `--fs-terminal` 14px
(chat transcript). Nothing else.

Color families — these are the vocabulary:
- `--fg-*` text: `--fg-dim` `--fg-faint` `--fg` `--fg-table-header` `--fg-widget-title`
- `--c-*` accents: `--c-accent` `--c-red` `--c-green` `--c-amber` `--c-accent-soft` `--c-blue`
- `--line-*` rules: `--line-divider` (row/section borders) `--line-border`
- `--bg-*` fills: `--bg-hover` `--bg-inset` `--bg-raised` `--bg-elevated` (canonical: `--surface-*`)

Settled call: table column headers use `--fg-table-header`, NOT `--fg-faint`.

## 3. Banned outright — every one is a real defect found in this codebase
- Pill or boxed chips for categorical values. Render a plain lowercase colored word.
- A second header / title / badge bar inside a widget body. The workspace frame owns the header.
- Floating overlay buttons on charts. Chart controls live in the top toolbar.
- Raw backend enum strings in JSX (`"NEW"`, `"data_gap"`, `"stop_limit"`). All user-visible enum
  text goes through `lib/labels.ts` — that is the contract; add the mapping there, not inline.
- Raw hex colors. Px font sizes off the ladder. Invented CSS vars.
- Data stuffed into another column's cell with no header of its own; a data grid with no header row.
- `cache: "no-store"` on any fetch — it bypasses the preflight cache.
- A new widget-picker entry without a merge proposal. Consolidate into an existing widget first.

## 4. Row mechanics
- Bordered rows need the integral line-height pin `leading-[20px]` — on `<tbody>` for tables, on the
  row element for grid rows. Without it Chromium pixel-snaps alternate 1px borders to zero height
  and the grid lines vanish. See the warning at `app/globals.css:54-60`.
- Row density is `px-2 py-1.5` — except the watchlist, which has its own three-step density system
  (`lib/workspace/watchlist-columns.tsx`, 23/29/42px pitches); follow that there, not this line.
- Every rendered ticker uses `ClickableSymbol` (`components/workspace/clickable-symbol.tsx`), and
  the row containing it sets `group/row`.

## 5. States
Use `WidgetEmpty` / `WidgetLoading` / `WidgetError` from `components/workspace/widget-states.tsx`.
Never hand-roll an equivalent. Give `WidgetLoading` a `cols={[...]}` that matches your real column
layout. Handle all three states — "I can't load your positions" must never look like "you have none".

## 6. Verify before claiming done
```
cd frontend && npm run typecheck && npm run check:tokens
node tools/ui-shot.mjs <widget-id>
```
Then OPEN the PNG, look at it, and critique it against sections 1–5. Shipping UI without looking at
it is how every defect listed above got here.
