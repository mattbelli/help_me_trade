# SERV HANDOFF — report generation (from the mobile client, 2026-08-06)

Context: the RN app just replaced its report viewer. Both markdown books and
`flow_decomp` HTML now render through one stylesheet in a WKWebView
(`mobile/src/features/reports/report-document.ts` on `feat/mobile-app`). Doing
that surfaced four things that are **generator-side, not rendering-side**.

Evidence is from 25 live reports pulled off `GET /api/reports?limit=25` on
2026-08-06, plus a read of `research/book_driver.py` and
`research/book_prompts/fable.md` on `origin/main`.

---

## 1. The Deep Seek JSON dump is an ARCHITECTURE bug, not a prompt bug  · HIGH

`book_deepseek` bodies are mostly a fenced ```json block:

| report | total chars | inside ``` fences | share |
|---|---|---|---|
| Deep Seek book · 2026-08-06 | 6,804 | 6,319 | **92%** |
| Deep Seek book · 2026-07-30 | 26,651 | 19,040 | **71%** |
| Deep Seek book · 2026-08-05 | 15,706 | 8,233 | 52% |
| **Fable book (any date)** | — | **0** | **0%** |

**Fable is the control, and the asymmetry explains itself:**

- **Fable** writes TWO FILES — `fable_report.md` and `fable_decision.json`
  (`research/book_prompts/fable.md`, step 3). The decision physically cannot
  land in the report body.
- **Deep Seek** writes ONE STREAM. `_run_analysis` collects the whole model
  output into `raw`; `_extract_decision` parses the decision from `blocks[-1]`
  — but `save_report(kind, "", title, raw, …)` then stores **that same `raw`,
  json block included**, as the report body.

So the prompt is behaving correctly: `_DECISION_CONTRACT` explicitly requires
"After the report, end with EXACTLY ONE fenced ```json block". The model
complies and the save path keeps it. **No prompt change can fix this** — the
block is required output.

**Fix (small, server-side):** strip the block you have already parsed before
saving the body. At the two `save_report(..., raw, ...)` calls in
`run_book_cycle`, pass the report text with the trailing fenced json removed —
`_JSON_BLOCK_RE` already identifies it. Keep the raw stream in `payload` if you
want it for debugging; just don't make it the reader-facing body.

Consequence today: the phone collapses long fenced blocks behind a disclosure,
so the 2026-08-06 report renders as ~485 characters of prose. The founder read
that as "the output is tiny — something's wrong." Nothing is wrong with
delivery; that is genuinely all the prose in it.

## 2. That payload is exactly the structure the client wants  · HIGH (the real win)

The candidate objects already carry everything a rich UI needs:

```
symbol, direction, thesis, bear_case, entry_low, entry_high, stop,
targets[], entry_ref_price, plan_horizon, confidence_label, advanced,
notional_usd, entry_type, limit_price
```

The client currently has to regex meaning back out of prose (it marks up signed
figures, prices and known tickers) — inference over something you know exactly.

**Ask:** expose the candidates as *data* beside the prose. The `payload` column
already exists and is already returned by `GET /api/reports/{id}`, so
`payload.candidates` needs no template work and pairs naturally with the item-1
fix — you're removing the block from the body anyway, so put it where it belongs.

That lets the phone render real entry/stop/target plan cards instead of a
collapsed blob.

## 3. Neither book prompt says anything about report structure  · MEDIUM

This is the root cause of "Deep Seek has no headings" (0 headings in 6,804 chars
on 08-06; 4 in 26,651 on 07-30, vs Fable's 1 h1 / 6 h2 / 2 h3 in 5,207).

The **entire** formatting instruction in each prompt is one clause:

- `research/book_prompts/fable.md`: *"the nightly report in markdown: what you
  see, what you dug into, your hold/trim review of existing positions, your
  conclusions."*
- `_CHARTER` (`research/book_driver.py:113`): *"Then file your nightly report in
  markdown: what you see, what you dug into, your hold/trim review, and your
  conclusions."*

No sections, no ordering, no TL;DR, no length, no mention that it is read on a
phone. Fable writes well anyway; Deep Seek does not. That is a prompt gap, not a
model defect.

**Ask:** add the block in the appendix below to BOTH prompts — verbatim into
`fable.md`, and into `_CHARTER` for Deep Seek.

Also worth fixing while you're in there: `_CHARTER` currently says to end the
report with a `## Desk journal` heading *"inside the json block"* — a markdown
heading inside JSON. The journal is already a plain `journal` string field in the
contract, so the instruction is contradictory.

## 4. `REPORT_KINDS` labels aren't exposed over the API  · LOW

`signals/reports.py:44` maps `flow_decomp` → `Flow Decomposition`, but the API
returns only the raw `kind`, so the phone's masthead reads `flow decomp`. The
only client-side alternative is a second copy of that map, which will drift.

**Ask:** add `kind_label` to the report payload from the existing map.

---

## Contract note: the mobile viewer restyles your HTML template

For `format: "html"` reports the client strips `<head>`, `<style>` and `<script>`
and re-styles the body **by class name**, so reports follow the app's theme
rather than the template's hardcoded dark palette (same idea as
`frontend/lib/report-theme.ts` on web).

It styles this vocabulary, from `render_report_html` in `ai/research/report.py`:

```
wrap · hero · badge · meta · summary · metrics · metric-card ·
metric-label · metric-value · report-section · panel · dual · citations
```

**Please keep those class names stable and flag any additions** — a new class
renders unstyled on mobile. Structural HTML changes are otherwise free; the class
names are the load-bearing part.

Previously the client only regex-stripped tags, which left the whole `<style>`
block as body text — every flow report opened with ~800 characters of
`:root { --bg: #0f1115; … }`. Fixed client-side; noted so nobody re-fixes it here.

---

## Appendix — the report-format block to add to both prompts

Design notes, so you can push back on specifics rather than guess at intent:

- **Order is BLUF / inverted-pyramid.** The founder reads this at night on a
  phone to decide whether to approve proposals, so the only section that asks
  him to act comes second, and supporting context sinks to the bottom.
- **"What changed" is new.** These run nightly against the same book; the delta
  since last cycle is the highest-value content and nothing currently asks for it.
- **Falsifier + confidence move into the prose.** The program spec frames this as
  a forward-test study where every idea is ledger-graded whether taken or not;
  recording confidence before the outcome is the thing that makes calibration
  possible, and it should be visible to the reader, not only in the payload.
- **Tables are safe to author.** The mobile renderer stacks each row into a
  labelled card below 560px, so the agent should not hand-flatten data to avoid
  wide tables.
- **Signed figures are load-bearing.** The viewer colors gains green and losses
  red by detecting the sign — `+6.1%` renders green, `6.1%` renders grey.

````markdown
## Report format

Write for a phone: one column, read at night, on iOS. Lead with the answer.

Use these `##` sections, in this order, omitting any that are empty:

1. `## TL;DR` — 3–5 sentences covering the whole night: book P&L, what you're
   advancing, and the single most important thing that changed. This is promoted
   into the report's standfirst, so it must stand alone.
2. `## Tonight's calls` — what you are advancing and what you want done. One
   table: `| symbol | entry | stop | targets | size | confidence |`. If you are
   advancing nothing, say so in one line — that is a fine night.
3. `## What changed` — the delta since your last cycle: fills, stops that moved,
   theses that broke, orders that rotted. Skip anything that is merely still true.
4. `## Positions` — your hold/trim review, one row per position:
   `| symbol | qty @ cost | now | stop | verdict |`, with a sentence beneath each
   naming its falsifier — the level or date that kills it.
5. `## Passed on` — what you seriously weighed and did not advance, with the one
   reason each. This is graded for calibration, so it is not optional.
6. `## Market frame` — regime, tape, and the outside research that informed the
   above. Context goes last, not first.

Style:

- Write every change signed and united: `+6.1%`, `−$417`, `$835.67`. Gains render
  green and losses red automatically, but only when the sign is present.
- State confidence (low/medium/high) inline where you make a call, not only in
  the decision block.
- Bold the symbol that opens a bullet. Keep paragraphs to ~4 sentences.
- Tables are rendered as stacked cards on a phone — use them freely for repeating
  fields, and keep them to 6 columns or fewer.
- Never paste raw JSON, tool output, or your decision block into the prose.
- No `#` title line — the system supplies the title and date.
````

---

## Unrelated, but the book agent keeps asking and it isn't landing

Multiple Fable reports repeat this verbatim:

> Feature gap persists: the pack still hides stop PRICES on open orders. I can
> verify presence, not level. Repeating the request.

The agent can confirm three sell stops exist but not at what price, so it cannot
verify its own risk placement between cycles — which matters more now that a
resting GTC stop is its only protection. Surfacing it because it has been asked
repeatedly inside report prose, where nobody reads it as a ticket.
