<!-- Source: multi-agent research workflow (flow-viz-design-research), 7 Sonnet agents, 2026-07-11.
     5 external-research lenses (Bookmap/databento/Sierra/Exocharts; Unusual Whales/Cheddar/FlowAlgo/Tradytics;
     thinkorswim/TradingView/TrendSpider; alert/notification UX; Tufte/Bloomberg dense-data infodesign)
     + an audit of our current options-flow widget & notification bodies. Founder asked: study the best terminals,
     design ours to match our terminal UI. This is the RESEARCH doc; mockups (Path 1) come after founder review. -->

# Order-Flow & Alerts Design Reference — steal-list for help_me_trade

*Scope: chart flow-overlay, options-flow widget, notifications/alerts. Hard rules in force everywhere: no pill/rounded chips (plain lowercase colored words only), no internal widget headers (grid WidgetHeader owns chrome), chart controls in toolbar only (in-chart actions = right-click), every ticker click-to-focuses the chart, monospace/dense/Bloomberg-not-consumer-app, mobile bottom-tab with no overlapping widgets.*

---

## 1. Big ideas (the 5-8 patterns worth stealing everywhere)

1. **Fixed-slot templates, not free-text sentences.** Every row/alert goes through the same field order every time (who → what → how big → why) so the eye stops reading and starts pattern-matching positions. This is the single biggest fix — it kills both current pain surfaces (`·`-joined hand-built sentences in options-flow rows and notification bodies).
2. **Color is the *second* channel, never the only one.** Pair every color with a glyph/sign (▲/▼, +/−) so meaning survives a sunlit phone screen and colorblind users, and so color stays scarce enough to mean something.
3. **One canonical color law across all three surfaces: aggressor side.** Not instrument type, not event category, not P&L. We already fixed this for the options-flow side chip (`204abed`) — extend the *same* mapping to chart markers and notifications so a color learned in one surface transfers instantly to the others.
4. **Magnitude as visual weight, not just a printed number.** Row/glyph size, font-weight, or a bullet-graph fill communicates "how big" pre-attentively; the digit is still there for precision but isn't the first thing read.
5. **Progressive disclosure: dumb glyph → tap for detail → full reasoning only in chat/timeline.** Three tiers, three surfaces. Nothing on the resting view should require reading a sentence.
6. **Honesty/confidence must be a first-class glyph in the glance, not a hover tooltip.** This is a correctness requirement here specifically (per the honesty red-team finding) — an unvalidated signal must look visually different from a validated one at a glance, not just in a `title=` attribute.
7. **De-dupe and batch correlated fires into one push.** Repeat/simultaneous alerts are the #1 driver of "just mute it" — already founder-mandated for momentum fills; apply the same suppression layer to every alert class.
8. **Sort by importance, not just time — position is itself a signal.** A composite "unusualness" score (we already have the ingredients: `confluence_score`, `vol_zscore`, RVOL) should drive default ordering, with time/ticker sort as an explicit power-user toggle.

---

## 2. Surface A — Chart flow-overlay (big buys/sweeps/option flow on the chart)

**Feasibility ceiling up front:** no L2/MBO depth feed → no liquidity heatmap, no strict book-depth sweep/absorption detection, no DOM ladder. Everything below is buildable from SIP trades+quotes (equities) and the existing OPRA options-flow classification (side, size, sweep proxy) we already compute.

### Patterns to adopt
- **Marker lane below the price pane, not glyphs pinned to candles.** An options sweep doesn't have a natural equity price to anchor to (pinning to open/close/mid is misleading); a dedicated thin lane under price (like the volume pane) sidesteps that and solves overlap — same-candle events stack horizontally in the lane instead of colliding on the candle.
- **Color = side, shape = event type, size = log(notional).** Two colors (buy-side aggressor / sell-side aggressor, matching the already-fixed side-chip law) crossed with 2-3 shapes (triangle = directional sweep, diamond = block/large print, dot = repeat cluster). No free text on the glyph. Radius scales `base + k*log(notional)`, clamped to a max px so one $10M print doesn't dwarf the lane.
- **Zero text by default; hover/tap reveals detail.** Contract, strike/exp, size, price, aggressor side, timestamp live in a tooltip/hover-card — reuse the exact hover-card pattern already used for watchlist/positions (ticker-links-everywhere convention), don't invent a new one.
- **Zoom-aware clustering.** At wide zoom, bucket events per visible pixel-column into one aggregate glyph ("+12" or a darker dot) instead of a smear of 200 overlapping marks; unfold to individuals on zoom-in. Skip for v1 if options-flow volume per symbol/day is still low tens — build when it's not.
- **Aggressor trade bubbles for equity tape specifically** are feasible today off the same `_on_trade` tick-rule classification the SIP-hygiene fix already does (Lee-Ready style: at/above ask = buy aggressor, at/below bid = sell aggressor). This is a straightforward viz layer on data we already classify, separate from the options-flow lane.
- **Delta/CVD as a lightweight optional sub-pane** (not v1): one number per bar, cheapest of the order-flow patterns, divergence (price new high, CVD doesn't) is a classic tell — flag as a fast-follow once the marker lane ships, not a blocker.

### How to render it in our terminal look
- Toolbar (not floating): a toggle group in the existing top toolbar — "flow: off / equity / options / both" — never a button drawn over the canvas, per the chart-ui rule.
- The lane itself is a new sub-pane, same visual treatment as the existing volume histogram (thin, axis-minimal, monochrome background) — markers are the only saturated color in it.
- Right-click on a marker (not left-click, which is reserved for click-to-focus elsewhere) opens the same context-menu affordance already used for "set alert" at a level, extended with "view flow detail" / "jump to options-flow row".
- Explicit non-goal for now: no heatmap, no DOM ladder, no book-depth sweep/absorption — label any sweep/absorption marker we do ship as a **proxy** (burst + quote-through-tick heuristic), never implied depth-verified, matching the honesty-labeling discipline already established for the Decision Engine.

### Before → after (ascii sketch)
```
Before: nothing on the chart — flow only visible in a separate widget, disconnected from price context.

After (marker lane under candles):
  price pane:  ─────╱‾‾╲___╱‾‾‾‾╲──
  flow lane:      ▲       ◆    ▲▲(+3)
                 buy      block  clustered buys (zoomed out)
  hover on ▲ → "AAPL 211.34 · CALL sweep $215 8/16 · $1.24M · 6.1x avg · 14:32:07 ET"
```

### Open questions for the founder
- Equity-tape markers and options-flow markers in the **same lane** (visually distinguished by shape) or **two stacked lanes**? Same lane is denser; two lanes is clearer but costs vertical real estate.
- Default lane on/off per chart, or opt-in per session? (Given "step away and trust it," probably default-on but auto-thins via clustering.)
- Is a CVD sub-pane wanted at all, or is that manual-trader polish we should deprioritize per the mission (autonomy ladder over DOM-style tools)?

---

## 3. Surface B — Options-flow widget redesign

*This is the founder's stated #1 pain point — build first.*

### What's broken today (from the audit)
- Line 1 packs 5 independently-colored fields (time, ticker, side, type, premium) with three different hues in one row and no legend; line 2's field count/order changes **per event type** (UV-1 has 4 segments, sweep has 5), so nothing lines up into a column — the eye re-parses every row's shape.
- Expand reveals a *second* wall-of-text (`formatExpandedDetail`, 7 abbreviated Greek symbols joined by `·`) — not a real drill-down, just a longer string.
- No visible unusualness/rank signal beyond premium; a $2M sweep and a $2M chain-surge look equally salient.
- The honesty caveat (`caveat` field, e.g. "side inferred from tick test — weak") only shows as a native `title` tooltip — invisible unless hovered. This is exactly the red-team failure mode: low-confidence data rendered identically to high-confidence data.

### Patterns to adopt
- **Fixed-width columns, monospace tabular numerals, right-aligned.** Every event type renders through the *same* column set — `TIME | TICKER | SIDE | TYPE | STRIKE/EXP·DTE | SIZE | PREMIUM | FLAG`. Fields that don't apply to a given event type render blank/dash, not omitted — column position must never shift row-to-row. This alone fixes the "re-parse every row" problem.
- **Type/flags as a narrow plain-word column, not five competing hues.** Collapse `block/sweep/unusual-volume/chain-surge/pressure` into one small learnable vocabulary rendered as **plain lowercase colored words** (per house rule — never a bordered badge): e.g. `sweep` in one reserved accent, `block` in another, with side (call/put, buy/sell) carrying the green/red channel instead. Don't spend 5 hues on event-type *and* 2 more on side — reserve saturated color for the side/aggressor fact only (canonical color law), let type be a plain word in a muted/neutral tint.
- **Row weight = magnitude, not just a right-aligned number.** A subtle left-edge fill (bullet-graph style, div/SVG, no axis/legend) proportional to size-vs-this-ticker's-own-baseline (`vol_zscore` or premium vs trailing avg — data already exists) — the row's *weight* is read before the digit.
- **Default sort by unusualness, not pure time.** We already compute the closest thing to this (`confluence_score.py`'s `score` + `drivers`) but it's currently trapped inside the confluence push payload — surface a per-row z-score/ratio-based composite as the default sort key, with an explicit time-sort toggle for power users. Don't fabricate a black-box score (Tradytics anti-pattern) — the components (`vol_zscore`, `vol_oi_ratio`, exchange count) must be inspectable on hover, same honesty discipline as the Decision Engine.
- **Honesty flag as a visible glyph, not a hover-only tooltip.** Give `caveat`/`direction_basis=weak` its own always-visible marker (a muted word like `unconfirmed` or a dimmed side color) in the fixed column set — never rely on `title=` alone.
- **Progressive disclosure done properly:** default row shows the 6 decision fields; expand reveals Greeks/quote detail in an actual aligned mini-grid (label:value pairs, not a 7-field `·` string), still no prose.
- **Persistent filter rail, not a modal** — side/premium-floor/DTE/event-type/sweep-only toggles live in the toolbar/settings rail (already how this app's toolbar-owns-chrome rule works), saved as presets.
- **Ship a sane default floor.** Per the anti-pattern warning (FlowAlgo/BlackBoxStocks firehose), default to sweep/block-first ordering + a reasonable premium floor rather than showing every sub-$10k print at equal visual weight.

### Concrete before → after
```
BEFORE (current, two different shapes for two event types):

14:32:07 ET  NVDA ⓘ  ▲ call   sweep        $1.24M
             Aug 130C · 4d · 420 ctr · 3 prints · NYSE/ARCA
             [chevron] und $131.02 · iv 62% · δ .48 · γ .02 · θ -.11 · ν .09 · oi 1,200 · 129.80×130.10

14:32:41 ET  TSLA ⓘ  ▼ put    chain surge  $340K
             $250-260 bucket · 3.1σ · 180 ctr

AFTER (one fixed column set, weight-fill, honesty glyph, plain-word type):

 TIME     TICKER  SIDE  TYPE     STRIKE/EXP    DTE  SIZE     PREMIUM   FLAG
 ▓▓▓▓░░ 14:32:07  NVDA  ▲ call   sweep    Aug 130C    4d  420 ctr  $1.24M   —
 ▓░░░░░ 14:32:41  TSLA  ▼ put    chain surge  250-260    —  180 ctr  $340K    —
 ▓▓░░░░ 14:33:02  AAPL  ▲ call   unusual vol  Sep 215C   38d 300 ctr  $610K   unconfirmed

 (left bar = magnitude weight vs. this ticker's own baseline; blank cells for
 inapplicable fields, e.g. chain-surge has no single strike; "unconfirmed" is
 the always-visible honesty glyph, not a hover-only tooltip)
```

### Open questions for the founder
- Collapse `unusual_volume` / `block` / `sweep` / `chain_surge` / `directional_pressure` into fewer visual buckets, or keep all 5 as distinct words? (Recommend: keep 5 words, but strip 4 of the 5 current hues — only side keeps saturated color.)
- Is a composite unusualness score (built from existing `vol_zscore`/`confluence_score` ingredients) something you want surfaced per-row now, or should that wait for calibration work broadly?
- Default premium floor value — pick one, or make it a per-user setting from day one?

---

## 4. Surface C — Notifications / alerts redesign

*Founder's stated #2 pain point — build after the options-flow widget.*

### What's broken today (from the audit)
- Every push/notification body is a hand-built Python string concatenating 3-5 differently-formatted clauses with inconsistent separators across sources (`convergence.py` uses `" + "` and `· score X`; `detect.py`'s UV-1 reason uses `:` and `,`; `equity_tape.py` uses `·` throughout) — the eye can't pattern-match across notification *kinds* because each kind invents its own sentence shape.
- Full detail and the one-line summary are the same string — collapsed view is `line-clamp-2` truncation of a paragraph, not a designed headline tier; expanded view is `whitespace-pre-wrap` of the same paragraph. There's no real tiering.
- The toast banner always shows the same amber `AlertTriangle` icon regardless of actual severity — severity only exists as a 2px left border color, easy to miss.
- No de-dupe/batching — this is the exact "40+/hr fill" alert-fatigue risk already flagged for auto-entry, but the underlying wall-of-text problem is present for *every* alert source (confluence pushes, options-flow solo pushes, equity-tape prints), not just fills.

### Patterns to adopt
- **Enforce one `format_alert(symbol, action, size, reason)` template at the phone-push chokepoint.** No free-text bodies bypass it. This is a backend change (Python, in `signals/push.py` / wherever bodies are assembled) as much as a frontend one — the fix belongs at the formatting layer that all three current ad-hoc string-builders (`convergence.py`, `detect.py`, `equity_tape.py`) currently duplicate independently.
- **Three tiers, three surfaces, never blended:**
  1. *Headline* (push title / lock screen): `SYMBOL · glyph+side · magnitude-vs-baseline · one-clause why`. Capped, never truncates mid-word because it's built from fixed slots, not a sentence.
  2. *Detail card* (in-app notification card / approval card): a handful of short **separate lines**, each one fact — never woven into prose. This is where entry/stop/targets/win% or the flow specifics (strike/exp/premium/ratio) live.
  3. *Full reasoning* (chat/timeline only): the current prose style is fine *here* — comparable historical prints, full decision-engine chain, calibration history.
- **Magnitude-first framing:** lead with the ratio-vs-baseline ("6.1x avg" / "3.2x normal size"), not the raw absolute number — this is a formatting change on data we already have (`vol_oi_ratio`, RVOL, `confluence_score` drivers), not new plumbing.
- **Actionable verb, not descriptive noun:** "AAPL call sweep — $1.24M" not "AAPL unusual options activity alert."
- **Color+glyph, never color alone, and no pill chrome:** replace the current bordered kind-chip (`kindChipClass`) with a plain colored word + directional glyph (`▲ call` in green) — same fix as the options-flow widget, same canonical side-color law.
- **Confidence/honesty flag as a visible headline element, not buried.** Directly actionable given the existing red-team finding: the headline template's fixed slots must include the validation flag (e.g., a distinct rendering for "unvalidated edge") as a first-class glyph that survives transport to the phone card — not something stripped before it reaches the widget, and not merely inferable from the caveat text.
- **De-dupe/cooldown + batched digest as one shared layer**, applied to every alert class (options-flow spikes, equity-tape prints, decision-engine proposals), not just the momentum-fills case it's currently earmarked for: collapse "5 sweeps on AAPL in 3 min" into one push with a count, and suppress identical re-fires within a cooldown window instead of re-pushing.
- **Toast severity should actually vary the icon/color**, not always render the same amber triangle — reserve animation/pulse strictly for items crossing an actionability threshold (reuse whatever significance score already gates phone delivery, don't invent a second one).

### Concrete before → after
```
BEFORE (current confluence push body, one wall-of-text paragraph):
Title: "AAPL: signal confluence"
Body:  "$1.2M ask-side call flow + $3.4M dark-pool print + RVOL 2.1x · score 2.7"

BEFORE (current UV-1 solo push):
Body: "NVDA Aug $130C: 4,200 contracts, 6.3x OI, $2.1M premium (EOD)"

AFTER — headline tier (lock screen / push title):
"AAPL ▲ call sweep — $1.24M (6x avg) — into earnings"        [flag: validated components only]
"NVDA ▲ call — $2.1M (6.3x OI) — chain confirms"             [flag: unconfirmed — no direction basis]

AFTER — detail-card tier (in-app notification/approval card):
 AAPL   211.34   +1.8%
 ▲ call sweep · $215 strike · Aug 16
 $1.24M premium · 6.1x 20d avg
 why: aggressive buy-side, near-the-money, pre-earnings
 [confidence: unvalidated edge]

AFTER — chat/timeline tier: full prose, comparable historical sweeps,
decision-engine reasoning chain, calibration history — unchanged from today's
prose style, just relocated out of the push/card.
```

### Open questions for the founder
- Cooldown window length and batching threshold (e.g., "≥3 same-symbol fires in 5 min → batch") — pick defaults or make configurable per alert class?
- Should the headline-tier honesty flag be a word (`unconfirmed`) or a distinct glyph shape — given "no pill chips," leaning toward a plain word, but confirm it shouldn't be color-only (accessibility rule from pattern 6).
- Does the toast banner need per-severity icons at all, or is severity-in-border-color sufficient once the headline template itself carries the honesty/magnitude signal? (Leaning: yes, still worth it — motion/icon should be reserved for "needs a decision now," per the pre-attentive-motion pattern.)

---

## 5. Feasibility & sequencing

### Buildable now, from data we already have
| Item | Data already exists? | Notes |
|---|---|---|
| Fixed-column options-flow widget layout | Yes | `db/options_flow.py` schema already has every field needed (side, type, size, premium, dte, strike, exchanges, direction_basis) |
| Row magnitude weight (bullet-graph fill) | Yes | `vol_zscore`, `vol_oi_ratio` already computed in `detect.py` |
| Honesty glyph on options-flow rows + notifications | Yes | `caveat`/`direction_basis` fields already exist — this is purely a rendering-visibility fix, not new data |
| Default sort by unusualness | Partial | `confluence_score.py` score exists but only for ≥2-component hits; solo-event rows would need a lighter per-row z-score composite (cheap: reuse `vol_zscore` directly) |
| Notification headline template + 3-tier structure | Yes | Requires refactoring the string-building in `convergence.py`/`detect.py`/`equity_tape.py`/`push.py` into one shared formatter — no new data, moderate backend refactor |
| De-dupe/cooldown/batch layer | Yes | Same data, needs a new shared suppression component (currently only planned for momentum fills — generalize it) |
| Equity aggressor trade bubbles (chart) | Yes | Same tick-rule classification as the SIP-hygiene fix already in `_on_trade` |
| Options-flow marker lane on chart | Yes | Event payload already carries side/size/timestamp; new lane sub-pane, not new data |
| Sweep/absorption proxy markers (chart) | Partial | Proxy-only (burst + quote-through-tick heuristic); must be labeled as approximation, same honesty discipline |
| Delta/CVD sub-pane | Yes but lower priority | Cheapest order-flow addition if ever wanted; not required for v1 |

### Needs new data (out of reach until a depth feed exists)
- Liquidity heatmap (Bookmap-style resting-size ladder) — needs L2/MBP-10.
- DOM/order-book ladder visualization — needs L2/MBO.
- Strict book-depth sweep detection ("consumed N levels of resting size") — needs L2; our version is a trades+quotes proxy only.
- Strict absorption ("resting size refilling as it's hit") — needs L2; our version is an outcome-inference proxy only.
- Footprint/Numbers-Bars ladder chart — feasible from trades+quotes in principle, but a heavier build (per-price-level volume bucketing across the whole chart); flag as a *future* full order-flow view, not part of this three-surface pass.

### Recommended build order (matches founder's stated pain order)
1. **Options-flow widget redesign** — fixed columns, collapsed color law, magnitude weight, visible honesty flag, real drill-down for Greeks. Highest daily pain, zero new data needed, backend changes are additive (expose existing fields, don't need new detection logic).
2. **Notifications/alerts redesign** — shared `format_alert` template + 3-tier structure + de-dupe/batch layer. Touches more backend call sites (`convergence.py`, `detect.py`, `equity_tape.py`, `push.py`) than the widget, so budget more review time; this is exactly the kind of change that should get the adversarial-review treatment given it touches the phone-delivery chokepoint and the honesty-flag correctness requirement.
3. **Chart flow-overlay** — new marker lane + equity aggressor bubbles + options-flow markers, reusing color/glyph conventions already locked in by steps 1-2 (so the same visual grammar transfers across all three surfaces per the cross-surface-consistency principle). Do this last both because it's the founder's stated third priority and because it benefits from the color/weight/honesty-glyph vocabulary being settled by the other two surfaces first — building it first would risk inventing a fourth grammar instead of reusing the first two.
