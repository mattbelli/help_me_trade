# Flow intelligence design — capture, schema, display (post-wildcard-probe)

*2026-07-11 · Fable deep-dive, founder-requested. Answers three questions for the tape we're about
to have (OPRA trades `*` ACKed; data-rate confirmation Monday open): what to DETECT, what to
STAMP, how the founder SEES it. Companions: `FLOW_CAPACITY_RESEARCH_2026-07-11.md`,
`FLOW_DTE_DIVERSITY_SPEC_2026-07-11.md`, `research/fable/2026-07-09_options-flow-screener-design.md`.
Mode A discipline holds everywhere: facts about the tape, reason+caveat on every event, no
fabricated win% — empirical hit-rates only, always with n, always labeled history-not-prediction.*

## TL;DR

1. **Capture**: build rolling 30-min **directional burst** and **small-print cluster** detectors
   (the founder's hypothesis (c), instrumented); defer per-contract TWAP/iceberg as its own event —
   instead stamp footprint metrics (modal-size share, inter-arrival CV) on cluster events NOW so
   slicing becomes testable from data. Breadth = thin derivative of bursts, week 2.
2. **The scarce resource is quotes-CPU, not the 1,000 cap** (measured: 150 quote subs ≈ 52% of a
   core). Architecture: wildcard trades = attention, a ~60-sub quote budget = confirmation,
   allocated by **quotes-follow-heat** (standing watchlist-hot + escalation pool on any candidate).
3. **Degradation is a governor, not a redesign**: detectors are transport-agnostic (pure, print-fed);
   a lag governor sheds wildcard → capped active-set → 30s poll automatically; every event stamps
   `coverage` so outcome analysis survives the transition.
4. **Schema**: stamp exactly two new columns — `coverage`, `minutes_into_session`. Weekday, size
   bucket, 0DTE, regime: all derivable, no columns. Honest aggregation unit = **symbol-day**
   (dominant side, ≥60% dominance else "mixed"); report gates cells at n≥20 symbol-days.
5. **Display**: new **flow pressure** widget (per-name rows: 30m net bar, burst/cluster words) =
   "where is pressure NOW"; new **flow patterns** widget (signal×bucket × horizon grid) = "is big
   bullish flow contrarian this month?" in one glance. Phone: watchlist bursts/clusters join the
   existing convergence/push budget; off-watchlist market pushes ship OFF (founder arms a floor).

---

## Q1 — CAPTURE: the detector set

### Architecture the detectors sit on (branch W = wildcard flows; branch C = capped set)

**Two-lane ingest (branch W).** Full-tape rates (~50M trades/day avg ≈ 2–3k msg/s, open bursts
10–20×) exceed what per-print Python processing tolerates, but not what counter increments tolerate:

- **Aggregation lane** (every print, O(1), no per-print storage): ring of 1-min buckets per
  `(underlying, right)` — gross premium, n_prints, small-print premium, and per-contract clip
  tallies only for keys already hot. Feeds burst + cluster detectors. Prints < `INGEST_MIN_PREMIUM`
  ($2k, dust floor) skip even this.
- **Event lane** (prints ≥ $25k premium only): full `normalize_print` → F-1/F-2 → block/sweep
  pipeline, exactly today's code, now market-wide. Sweep cluster state only for contracts with a
  ≥$25k print in the last few seconds — bounded.

**Quotes-follow-heat.** The 1,000-quote cap is not the binding constraint; measured CPU is
(HOT_QUOTE_MAX=60 exists for this reason). Allocate ~60 concurrent quote subs as: ~40 **standing**
slots on watchlist-hot contracts (selected by the DTE-diversity quotas, which under branch W govern
*this* budget, not a snapshot rotation) + ~20 **escalation** slots: any candidate event (block,
sweep, unsigned burst, unsigned cluster) on any name subscribes that name's hottest contracts for
`HOT_QUOTE_TTL_SECONDS`, so subsequent prints in the episode get DIR-1 sides. Honest consequence:
off-watchlist episodes are side-classified only *after* escalation — events carry `classified_frac`
and the caveat says so.

**Lag governor (the branch-W kill switch).** Each second, p95 of (wall clock − print ts). Sustained
> 2s for 30s → raise `INGEST_MIN_PREMIUM`; still lagging → unsubscribe `*`, subscribe the capped
active set (branch C), set a health flag, retry `*` next session. The 30s poll stays the final
fallback (permanent, per the multi-user direction). No detector changes hands in any degradation —
they consume normalized prints/buckets and don't know the transport.

### Detector table

| # | name | what it catches | window / trigger | floors (named constants, re-justify from week-1 distribution) | branch C (no wildcard) | verdict |
|---|---|---|---|---|---|---|
| D-1 | **directional burst** (`flow_burst`) | pressure arriving NOW — today's whole-day `directional_pressure` dilutes a 2pm $2M burst into a day-net that fires late or never; the founder acts fast (latency memory) | rolling 30 min, 1-min step, per underlying; signed where quotes exist, else gross-unsigned → escalate quotes → may upgrade to sided | watchlist sided: net ≥ **$1M**/30m, classified_frac ≥ 0.5; market unsigned: gross ≥ **$3M**/30m ex-index; index/megacap: sided-only, net ≥ **$5M** (gross baselines needed before unsigned index bursts — v1 excludes) | identical on active-set prints, watchlist-only; still the single biggest upgrade over day-net pressure | **BUILD (first)** |
| D-2 | **small-print cluster** (`flow_cluster`) — founder's (c) | institutions slicing to minimize footprint; the $25k floor is exactly where a slicer hides; also his (b): sub-$100k prints teased with-flow | rolling 30 min per (underlying, right); prints each < **$100k** and ≥ **$2k**; 60-min re-arm per key unless aggregate doubles | aggregate ≥ **$500k** AND ≥ **25 prints** (single name); index/megacap ≥ **$2M** | identical — active-set prints already include every print on watched contracts | **BUILD (first)** |
| D-3 | **iceberg/TWAP footprint** (per-contract same-size clips, regular cadence) | a single order worked on ONE contract | would be: ≥8 prints, modal size ±20%, spanning ≥10 min | — | — | **DEFER as event type; instrument NOW**: D-2 stamps `modal_size_share`, `n_unique_sizes`, `interarrival_cv`, `top_contract_share` in `detail`. FP sources are exactly why: MM requoting fills and complex-leg reprints produce uniform clips; modern TWAPs randomize cadence (regularity can corroborate, never gate). Revisit when cluster outcomes show whether uniform-clip clusters outperform crowd clusters — then it's one query, not a hunch |
| D-4 | **cross-name breadth** (`flow_breadth`) | macro moment — same-direction bursts across many names inside 15 min | ≥ **5** non-index names with sided D-1 events, same direction, within 15 min → one event listing the names | derivative of D-1; ~20 lines | works (watchlist breadth is still breadth) | **BUILD (thin), week 2** — after D-1 has run live a week. Sector-mapped version (semis sweep) DEFERRED: needs a sector map we don't have; revisit only if breadth events prove interesting |
| D-5 | existing SWP-1 / BLK-1 / day-net pressure | unchanged | unchanged | unchanged | unchanged | **KEEP** — branch W widens block+sweep to market-wide via the event lane (side undetermined until escalation); day-net pressure survives as the EOD summary row, demoted from primary surface (D-1 takes over intraday) |

**Honesty fields on the new events** (schema shape unchanged, §6 contract): `reason` templates
("NVDA: $1.8M net buy-side call premium in 30 min (23 prints, 71% classified)"; "TSLA: $640k of
puts in 31 prints < $100k over 28 min, 84% at one size"), `caveat` templates ("aggregate of many
small prints; cannot distinguish coordinated slicing from a coincident retail crowd; side classified
on 71% of premium"). Direction never guessed; unsigned events say `undetermined`. The retail-crowd
false positive on D-2 is *named in the caveat*, not filtered away — the footprint metrics + outcomes
decide later whether it matters.

Code targets: `signals/options_flow/rolling.py` (new pure module — bucket rings + D-1/D-2, tested
hermetically like `intraday.py`); consumption in the existing `api/main.py` 1s stream loop; governor
in `stream.py`. Home-server constraint respected: counters not per-print objects, one process, no
new services.

---

## Q2 — SCHEMA: stamp now vs derive later

| dimension | decision | justification |
|---|---|---|
| `minutes_into_session` INTEGER | **STAMP** (new column) | the ONE slice not derivable at query time: ts is UTC, session bucketing is ET, and SQLite can't do DST-aware conversion — compute once in Python at write time. One integer; open/morning/midday/close buckets derive from it |
| `coverage` TEXT (`active_set` \| `market_wide`) | **STAMP** (new column) | the denominator changes when wildcard turns on; without it, pre/post outcome comparisons are silently apples-to-oranges and unreconstructable later |
| weekday | derive | `strftime('%w', ts)` — pure ts function, no DST issue at day granularity for a 21:00Z-max stamp; not worth a column |
| 0DTE vs longer | derive | `dte` column already stamped at event time (correctly — dte is event-relative) |
| size bucket | derive | pure function of stored `premium_usd`; bucket boundaries will be retuned — freezing them into a column would be wrong |
| session regime (trend/vol) | derive later | a property of the *date*, not the event — reconstructable forever from SPY/VIX history as a date-join when a regime table exists. No speculative column |
| detector metrics (window_s, classified_frac, modal_size_share, interarrival_cv, top_contract_share, agg components) | `detail` JSON (existing column) | queryable via `json_extract`; keeps the row schema stable while detectors iterate |

Nothing else. Explicitly rejected: `spot_at_event` (outcomes filler already derives base price from
bars), per-event regime, weekday, bucket columns.

### The correlated-sample problem — honest aggregation unit

57 sided prints in one NVDA afternoon share one forward path; event-level n overstates evidence by
an order of magnitude. Decision (query-time in `tools/flow_outcomes.py` — no schema change):

- **Daily horizons (eod/1d/5d): unit = symbol-day.** All sided events of a (signal, size-bucket)
  cell on one symbol-day collapse to ONE observation. Symbol-day side = dominant side by classified
  premium; dominance < 60% → the day lands in a "mixed" cell, excluded from directional hit-rates
  (counted, shown).
- **Intraday horizons (15m/1h): unit = symbol-hour** (events in the same clock hour collapse,
  premium-weighted return of the first event's horizon).
- Report prints **n_events AND n_units** in every cell; percentages render only at
  **n_units ≥ 20**, else the cell shows `n<20` — the founder's two-day teases stay labeled teases
  until ~3–4 weeks of accrual.

---

## Q3 — DISPLAY: how the founder watches this

Grammar is locked (FLOW_VIZ_DESIGN + shipped widget): side = the only saturated color (green
▲ buy / red ▼ sell / grey ◇), types = plain lowercase muted words, no pills, no internal widget
headers, tickers publish-to-chart, honesty flags visible not tooltipped.

### Surface 1 — tape widget (exists): absorb the new events, no redesign

`flow_burst` / `flow_cluster` rows appear as type words **burst** / **cluster** (muted, like
block/sweep); side cell + magnitude bar follow the existing grammar; strike/exp/dte render `—`
(underlying-level events, like pressure today). Drawer adds: window ("30m, 1:42–2:12p"), aggregate
composition ("23 prints · 71% classified"), and for clusters the footprint line ("84% at one size ·
9 unique sizes · cadence cv 0.4"). `coverage` shows in the drawer only when `market_wide` (word
**market**, muted — tells him this name isn't on his watchlist).

### Surface 2 — NEW **flow pressure** widget: "where is pressure NOW" (position taken)

Not a gauge buried in the tape drawer (pressure is a standing question, not a per-event detail) and
not chart marks (overlay is parked, stays parked). A dedicated grid widget, one row per underlying
with activity today, default sort |30m net| desc:

```
ticker | 30m net              | day net | burst      | cluster    | last
NVDA   | ▮▮▮▮▮▮▮▮  +$1.8M     | +$4.1M  | burst 4m   |            | 2:12p
TSLA   |      ▮▮▮  −$620k     | −$2.9M  |            | cluster 11m| 2:05p
```

- **30m net** = diverging horizontal bar centered at zero, green right / red left (side-encoding —
  saturated is allowed), signed number tabular beside it. Unsigned-only names show a grey bar +
  gross number + faint `unsigned`.
- **burst / cluster** = plain lowercase words tinted by side, with age ("burst 4m"); empty when
  quiet. **day net** and **last** muted.
- Frame toolbar (⚙, like the tape): scope **watchlist / market** (market = off-watchlist names that
  produced events, branch W only), min-net filter.
- Ticker → channel publish; row click → drawer: session sparkline of 5-min signed premium buckets,
  top 3 prints, classified %, coverage.
- Mobile: no grid port — watchlist burst/cluster pushes land in the phone's alerts feed via the
  existing 3-tier notification format; the pressure grid is a cockpit surface.

### Surface 3 — NEW **flow patterns** widget: outcomes at a glance (position taken)

Not a report page (workspace-first; a page he must remember to visit answers nothing at a glance)
and not the notifications digest (redesign pending — it can quote this endpoint later). A compact
grid widget backed by a new API endpoint running the Q2 symbol-day aggregation (same endpoint
exposed through the `options_flow` perception resource so chat can answer "is big bullish flow
contrarian?" from the same numbers):

```
                 1h        eod        1d        5d
bullish $1M+     −0.2 41%  −0.5 35%   −0.3 40%  n<20     n=23d
bullish 100k-1M  +0.1 55%  +0.2 58%   n<20      n<20     n=31d
bullish <100k    +0.3 62%  +0.4 65%   +0.2 57%  n<20     n=44d
bearish $1M+     …
```

- Rows = signal × size bucket (6); columns = horizon; cell = median ret + up%, faint n. Cells under
  n≥20 symbol-days render `n<20` faint — the gate IS the honesty. Outcome sign uses muted tint or
  nothing — side stays the only saturated color; these are historical facts, not live aggression.
- Frame toolbar: range (2w / 1m / 3m / all), scope (all / watchlist).
- Footer, one faint line: `unit: symbol-day · correlated prints collapsed · history, not prediction`.
- Fast-follow (after the widget, gated n≥20): ONE line in the tape drawer per event — "similar
  (bullish $1M+, this month): eod median −0.5%, 35% up, n=23 symbol-days" — same endpoint, same
  caveat, never a prediction sentence. This is where the founder's contrarian tease becomes a
  glance instead of a CLI run.

### Phone / pushes

- Alert-worthy (joins the existing convergence pipeline + `alerts.max_push_per_day` budget,
  deny-by-default): sided **burst ≥ $1M net on a watchlist name**; **cluster ≥ floor on a watchlist
  name**; existing solo ≥$1M sweep/block unchanged. Headline via the shared `format_alert` 3-tier
  grammar: "NVDA burst: +$1.8M buy-side calls in 30m".
- **Off-watchlist market events never push by default.** A settings floor ("market burst push",
  default off) exists for the founder to arm — strategy-off-by-default culture applied to alerts.
  The wildcard's off-watchlist value surfaces in the pressure widget's market scope, not his pocket.
- Chart flow-overlay: stays parked; burst/cluster marks join its spec when unparked.

### Display build order

1. Tape widget type words + drawer fields (ships with the detectors — trivial).
2. Flow pressure widget (needs D-1/D-2 live; the new daily surface).
3. Patterns endpoint + widget + perception-resource field (symbol-day unit; cells self-gate).
4. Tape-drawer history line (after n≥20 cells exist).
5. Notifications digest integration — rides `flow-notif-redesign` surface 4, not this track.

---

## Build sequence

**Monday (probe day):** run the wildcard data-rate probe at open (msg/s, p95 lag, CPU). Branch
decision by ~11:00 ET. Either branch, the same detector code ships.

**Week 1 — both branches (transport-agnostic core):**
- `rolling.py`: D-1 burst + D-2 cluster (pure, hermetic tests), footprint metrics in `detail`.
- Schema: `coverage` + `minutes_into_session` via `_add_column_if_missing`; stamp on all new events.
- `tools/flow_outcomes.py`: symbol-day/symbol-hour units, n_units gating, mixed cell.
- Tape widget: burst/cluster rows + drawer fields.
- Watchlist scope first even under branch W — identical code, floors verified on known names.

**Week 1–2 — branch W only:** two-lane ingest + `INGEST_MIN_PREMIUM` + lag governor in `stream.py`;
quotes-follow-heat manager (standing 40 / escalation 20, DTE quotas govern standing selection);
market-wide floors for block/sweep/burst/cluster. **Branch C only:** DTE-diversity quotas on the
poll active set exactly per the approved spec (under W that spec shrinks to the quote budget).

**Week 2:** flow pressure widget; D-4 breadth event; watchlist burst/cluster pushes through the
existing budget.

**Week 3–4+ (data accrual, not code):** patterns widget cells ungate at n≥20 symbol-days; founder's
three hypotheses (big-print contrarian, small-print with-flow, cluster-as-signal) become queries;
D-3 TWAP verdict revisited from cluster footprint metrics; off-watchlist push floor offered only if
market-scope pressure proves itself in the cockpit first.
