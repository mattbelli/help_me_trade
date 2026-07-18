# Fusion Evidence Bus — spec (2026-07-17)

*Grilled with the founder 2026-07-17 (research + forks in session; base data below), then hardened
by two independent audits (one zero-context). This is the fusion layer of the ★ Decision Engine —
consolidate every perception source into per-symbol evidence stacks that feed the Idea Engine
ledger — specified as **the Phase 2 delta set on `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md`**
(see §1b ADR: one architecture, one writer into the ledger). Shipped Phase 1 (Grading Law, lanes,
alarms, `register_idea`) is a CONSUMER of this — nothing in it changes.*

**Status: v3.1 — SIGNED OFF by the founder 2026-07-18; Phase A in build.** v1 = grill session;
v2 = actor-group re-review + two Claude audits; v3 = the GPT-5.6 technical audit's fixes folded
in (findings 1–17, `AUDIT_GPT56_IDEA_ENGINE_FUSION_2026-07-17.md`) + the direction audit's
product deltas (four-lane alerting, skip reasons, one-archetype Phase C); v3.1 = founder
sign-off + the cohort-scorecard addendum (§2.4b) and the Idea Ledger table widget (§2.8). The audit's shipped-code findings
were fixed separately in **Round 0** (commits `9a15594`/`46d3d98`/`7f44ccc`, deployed
2026-07-18): phone win% suppressed, Path A counter honesty, direction-consistent alpha,
canary ledger-leg probe, NYSE calendar, median+never-worked alarms, web approval confirm.

---

## 1. Problem (measured, not assumed)

- Today's "fusion" is `signals/options_flow/convergence.py`: exactly 4 components (flow, tape,
  RVOL, level), fires on ≥2, one notification per symbol-day. Volume: ~110 cards/day 07-10→15,
  then **879 / 842 on 07-16/17** after the equity-tape coverage expansion — `tape+level` alone is
  1,097 of the last two days' cards. Measured archetype breakdown: it's essentially ONE archetype
  (tape-at-level); `flow+level` fired once since 07-13, all flow combos ~25 in two days. **Options
  flow almost never converges under the current gate** — the flagship signal is the rarest output.
- `score_confluence` **compounds correlated components**: a sweep that itself spikes tape + RVOL
  scores 3 components + a breadth bonus (founder flagged this during the Idea Engine build too).
- Convergences **never register in the idea ledger** — the alert type the founder acts on most is
  invisible to the Grading Law. Violates "calibration counts everything."
- Perception sources already built but unwired: `social_buzz` (running since 07-04 — 20,628 hourly
  buckets, 1,445 symbols, Reddit + StockTwits), `regime`, `sector_breakdown`, EDGAR earnings
  proximity, FINRA short data, index-pressure gauge.

## 1b. ADR: one architecture — this spec is the Phase 2 DELTA SET on the Unified Design

*(Rewritten after a zero-context audit 2026-07-17 caught the collision the first draft missed.)*

**Governing doc: `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md`.** Its Phase 2 (trigger-intake job
cursor-polling the WAL stores on integer-id cursors across the real three-process topology,
episode coalescing, dispositions + conservation law, storm shedding, lane meter) **is the
transport and substrate for everything in this spec.** "Stack" here = the Unified Design's
**episode** + its attached evidence dots; one vocabulary, one writer into `register_idea` —
never two racing architectures. In-process hooks are forbidden as transport: `process_events`
runs today in THREE processes (`hmt-flow-collector`, `hmt-equity-collector`, `hmt-backend`
api/main.py) and an in-backend hook never sees collector-side events — the 07-15 lesson the
intake job exists to encode.

**What this spec contributes as deltas** (grill session + audits, 2026-07-17): the group/actor
correlation model (§2.2) refining Unified's family definition; the founder's register-everything
call (§2.4), superseding Unified's below-gate-becomes-counters for stack-level registration;
RVOL-ex-prints; the market-shock rider + news roadmap (§2.9); day-part social pooling (§2.5);
the Evidence Stack widget (§2.8); horizon-named LLM lanes. **Phase C here IS Unified's
investigation tiers** (dossier → T1 null hypothesis → REASON v1/v2) — the earlier draft's thin
"LLM investigation" sketch is superseded by that design, keeping only horizon lane-naming.

**`DECISION_ENGINE_FUSION_DESIGN_2026-07-13.md` (REASON scorecard → calibrated win% →
`propose_trade`)**: stays Current for its own layer, stays gated on factor admission + the 8
founder-lane decisions, is NOT this build. **BUILD.md §4 item 2 currently says "build to" the
07-13 doc — that line is rewritten in the same commit that promotes this spec out of DRAFT**, so
"fusion layer" stops meaning two different things. INDEX rows carry the split.

**Phase 1 gap this absorbs**: Phase 1's spec promised convergence → `register_idea` and it never
shipped (no `origin="convergence"` call site exists). Phase A supersedes that promise with the
fusion lanes; the gap is recorded on the gate card, not silently forgiven.

## 2. Design

### 2.1 Observations and stacks

Every perception source yields a typed **observation** ("dot"): `{symbol, ts, component, group,
direction (bull/bear/none), raw value, points, detail}`. **Transport (per §1b ADR): dots are
DERIVED, not pushed** — the Unified Design's trigger-intake job cursor-polls the existing event
stores from inside `hmt-backend`; collectors keep writing facts to their own stores and never
learn the bus exists.

- **Cursor contract (pinned — GPT-5.6 finding 6):** both stores have `TEXT PRIMARY KEY` ids
  and upsert (`INSERT OR REPLACE`), so neither raw ids nor `rowid` are stable cursors. Phase A
  adds a **monotonic `ingest_seq` INTEGER column** to `options_flow_events` + `equity_tape`
  (`AUTOINCREMENT`-style counter assigned once at first insert, PRESERVED on upsert — an
  updated row keeps its seq; migration backfills existing rows in `created_at` order). Intake
  polls `WHERE ingest_seq > cursor ORDER BY ingest_seq`; cursors are per-store rows in a
  `bus_cursors` table. Never `created_at` arithmetic (ISO-string trap, documented).
- **Trigger context is persisted at dot-mint time (finding 7):** the intake job stamps each
  actor dot with the underlying's last price (from the quote/bars cache the backend already
  holds) and the prior-close ATR(14) (precomputed once daily per active symbol, cached table)
  — so registration builds the frozen plan from PERSISTED values and never fetches externally
  during a burst. A dot that cannot be stamped (no cached price) is stored unstamped and
  counted `unstampable` — visible, never silently skipped; the stack registers when a
  stampable directional dot arrives.

Dots accumulate into a per-**symbol-day evidence stack** (= the episode + its dots; SQLite
tables per Unified Phase 2 schema + `evidence_dots`). Dots are NEVER dropped for scoring
reasons — the stack shows everything that fired, always ("a number of unusual things happened
here" is information even when correlated).

### 2.2 Component groups — group by ACTOR, decorrelate at the measurement
*(v2 after adversarial re-review 2026-07-17 — v1's single "participation" group wrongly lumped
options flow with equity tape, which are usually DIFFERENT actors; it would have scored
"dark-pool print + call sweep at level" — the flagship convergence — same as one of them.)*

| group | components (v1 build) | why it's one group |
|---|---|---|
| options actor | option sweeps, option blocks, unusual option volume, directional pressure | same prints seen different ways |
| equity actor | equity blocks, dark-pool prints **only** — `equity_sweep` is EXCLUDED from v1 (matches today's `_TAPE_EVENT_TYPES`; it alone spans ~1,700+ symbols/day and admitting it is a Later decision with its own scale math) | same institution crossing stock |
| background crowd | **RVOL-ex-prints** (v1: ANNOTATE-ONLY — displayed, unscored; see below) | block volume mechanically inflates raw RVOL; a symmetric residual baseline is future work |
| structure | session levels (PDH/VAL/POC…), founder price alerts | — |
| attention | social-mention spike vs own same-hour baseline (§2.5) | Reddit/StockTwits separate dots |
| catalyst | earnings within N td (from the existing EDGAR-fed earnings store — already persisted) | filings dots = Later (fetcher → store persistence design first) |
| context | regime, index pressure, **market-shock flag** (big SPY move / VIX spike — stamped on every stack + idea open during the window, see §2.9) | **riders — displayed, never scored** |

*(`signals/sector_breakdown.py` was listed as a rider in an earlier draft — wrong: it's
portfolio sector P&L, not market sector state. A true market-sector-state rider is future work.)*

- **Event dots vs state dots — the trigger rule** (defines the stack population): the two ACTOR
  groups (options, equity) emit *event* dots and are the only groups that can CREATE a stack.
  Structure/crowd/attention/catalyst are *state* dots — they attach context to an actor-anchored
  stack but never create one (otherwise every symbol near a level or inside an earnings window
  grows a stack every day and the percentile population is undefined). Attention-as-anchor
  (social spike with zero prints) is a recorded revisit once baselines mature, not v1.
- **Within-group aggregation differs by dot kind**: actor groups **aggregate the day's value per
  direction, then log-score once** (`log2(Σvalue/base)`, capped — the existing scorer's curve).
  Not sum-of-dots (compounds correlated repeats), not max (erases persistence: five $400k sweeps
  ≠ one $400k sweep); both directions large → contested (§2.3). State groups take **latest/best**
  (closest level, current RVOL-ex-prints, today's spike z, nearest catalyst) — summing state
  readings is meaningless.
- **Across groups: sum of group scores** + direction-agreement bonus (direction-carrying groups
  only) + bounded cross-group breadth bonus. Options actor + equity actor lighting up (outside
  a collapse window) scores as two honest actors — never one actor counted twice.
- **No hard group-count gate.** The adaptive percentile bar (§2.6) is the only bar — a lone $8M
  sweep clears it on magnitude; the old solo-push exception dies with the gate that made it
  necessary.
- **Within-window collapse — full semantics (finding 11):** an options-actor dot and an
  equity-actor dot on the same symbol within the same **5-minute window** AND the **same
  direction** collapse for scoring: the pair contributes to ONE group — whichever of the two
  groups carries the larger log-scored aggregate absorbs the window; the other dot's value is
  excluded from its own group's directional sum for that window (a sweep triggers the market
  maker's delta hedge within seconds — one order seen twice). **Opposing directions never
  collapse** — that's two dots of evidence AND a contested signal, both scored in their own
  groups. ACROSS windows the actor split stands (a 10:31 dark pool + a 10:41 sweep are two
  actors). Both dots always display regardless. **Options direction mapping is the four-way
  rule** (`signals/options_flow/opening_pressure.py` convention): call@ask=bull, put@ask=bear,
  call@bid=bear, put@bid=bull; undetermined execution side → direction `none` (dot displays,
  never enters a directional sum).
- **RVOL-ex-prints: ANNOTATE-ONLY in v1 (demoted per finding 15).** Subtracting today's
  detected prints from the numerator while the baseline expectation still contains historical
  prints yields a systematically understated residual — not an independent crowd signal. The
  crowd group ships as a displayed, unscored rider until the baseline is residualized
  symmetrically (own work item, not v1); the spec's earlier "minimal residual" scoring claim
  is withdrawn. The scorer never claims an independence it doesn't have.
- **Known residual bias (recorded, not solved):** participation mechanically ramps near earnings,
  so participation×catalyst co-fire is partially correlated; RVOL-ex-prints only subtracts prints
  above our detection floor. Groups REDUCE correlation; the meters watch what remains. On macro
  days the Unified Design's **cross-sectional discount** applies at scoring time (importance is
  relative to the concurrent episode population — "everything anomalous" reads as "nothing is").
- **Cross-group breadth bonus is pinned**: `+0.5 per scored group beyond the second, cap +1.5`
  (replaces the old `0.75×(n−2)` component-count bonus). Groups are only partially independent
  (above), so the bonus stays small enough that breadth never outweighs one genuinely large actor.
- **Learning: presence-marginals, not signature cells.** Every stack/idea records its full
  component signature (`source_ref.signature`) for drill-in, but lane meters aggregate by
  component PRESENCE (tape present vs absent, social present vs absent…). Even at ~hundreds of
  registrations/day the full signature space is a mini curse-of-dimensionality (rare combos never
  fill); marginals rank in weeks — ordinal, per the calibration-is-a-filter directive. Whether
  co-fire beats solo is measured, never assumed.

### 2.3 Direction state — the back-to-back-sweeps problem

Each dot carries a direction. Stack direction state: `aligned(bull|bear)` / `mixed` /
`contested` (opposing directions within the SAME group same day, e.g. bullish sweep then bearish
sweep). Contested stacks: agreement bonus zeroed, stack flagged visibly (contested is itself
information — someone's arguing), never eligible for the LLM/push tier.

### 2.4 Two idea lanes out of one bus

The bus feeds `db/idea_outcomes.register_idea` (the existing narrow write path; `source_ref =
{kind:"fusion", id:<stack_id>, signature, groups, score, pct}`) via two lanes:

- **`fusion_mech`** — **EVERY actor-anchored stack registers** with a mechanical plan (entry =
  last, stop = 1×ATR(14), target = 2×ATR, horizon = days), as a **journal-class lane**: excluded
  from headline win-rate and the founder 5b grading queue, exactly like the `VETOED_ORIGIN`
  precedent in `db/idea_outcomes.py`. No score bar on registration (founder call 2026-07-17,
  reversing an earlier draft cap): registering only top-scored stacks recreates the
  taken-trades-only bias one level up — we could never learn whether the score ranks outcomes.
  With the full population graded, the score→outcome curve is measured and the attention bar
  (§2.6) becomes data-derived within weeks. **Quality governs attention; quantity governs
  learning.** Direction-symmetric ([ADR-6]): bear stacks register `direction=short` with the
  inverted plan — measurement is never long-only. **Contested stacks (§2.3) still register** —
  learning wants them — tagged `contested` in `source_ref`; they're only barred from LLM/push.
  **The mechanical plan formula is FROZEN v1 (estimand discipline)**: it grades a different game
  than the flow it triggers on, and that's fine *because it's labeled* — anyone "improving" the
  plan mid-flight corrupts the estimand; a changed formula = a NEW lane name, never an edit.
  **Named code change**: `VETOED_ORIGIN` is a single hardcoded string in ~5 filter sites in
  `db/idea_outcomes.py` — this lane needs a `JOURNAL_ORIGINS` frozenset extended into every
  summary/list/grading-queue filter, not a copy of the one-string pattern.
  **Registration instant is PINNED (it defines the frozen estimand)**: one registration per
  symbol-day, fired at the **first directional actor dot** — that dot's price is `entry_ref`,
  ATR(14) from the prior close builds the plan, that dot's direction is the plan direction. Later
  dots update the stack score and card, **never the plan**; a later opposing dot sets the
  `contested` tag only. A stack whose anchor dot is direction-less registers when its first
  directional actor dot lands; a symbol-day that never gets one never registers (counted, per
  the no-silent-caps rule). One row per symbol-day by construction — no intraday
  `dedup_key`-bucket collisions (`idx_idea_dedup_open`) can silently drop rows.
  Scale accepted explicitly with MEASURED numbers (fixed after fresh-eyes audit #3 caught the
  earlier draft reusing convergence-card counts for the bigger stack population): actor-anchored
  symbol-days = **~1,250/day** at 07-16/17 breadth (block+dark_pool distinct symbols alone:
  1,248 / 1,260 measured); rolling ~10-td open window unions toward **2,000–3,000 distinct
  symbols** in the grading sweep ≈ **10–15 Alpaca daily-bar batch requests/day** (200
  symbols/request; arithmetic fixed per finding 14; never Schwab at this breadth — WAF/burst
  rule). **Named Phase A work item (finding 14b): the batch-bar grading path** — today's
  `research/idea_tracker.py` fetches one symbol per row; pointing it at this population
  unmodified would be thousands of calls per sweep. `fusion_mech` rows grade through a new
  batched fetch (`get_stock_bars_batch`-style, grouped per sweep), existing lanes unchanged.
  If sweep cost still bites, the recorded fallback is the Unified Design's
  graded-random-sample of below-bar stacks — same selection-bias-free learning, fraction of
  the rows. `equity_sweep` events stay excluded (§2.2) — admitting them ~doubles this math.
  **Grading resolution — explicit reconciliation with Unified §3 (revised per finding 5)**:
  Unified mandates minute bars for event-driven intraday origins. `fusion_mech` takes daily
  bars (minute bars for ~1,250 symbols/day is the cost wall) — but the earlier draft's "daily
  is conservative" claim was WRONG: grading the registration DAY's full range includes
  pre-trigger movement (a stop touched at 09:40 grades an idea whose dot fired at 14:00 —
  leakage, not conservatism). v3 rule: **`fusion_mech` grading starts at the NEXT session**
  (`first_evaluable` = first trading day after registration; entry stays the pinned dot price)
  + the Grading Law's pessimistic same-bar stop-wins rule from there. Owned cost, labeled:
  day-one continuation is missed — uniformly across every signature, so ordinal comparisons
  stay fair, and the estimand is leakage-free. **LLM-lane ideas grade per Unified's minute-bar
  rule** (their ≤~5/day volume makes it affordable).
- **The LLM lane** — stacks crossing the intraday investigation bar (§2.6) get the
  **Unified Design's investigation tiers**
  (per §1b ADR: dossier claim-matrix → T1 null-hypothesis triage → REASON v1, with the v2 A/B on
  its hash sample — NOT a new thin prompt; the earlier draft's sketch is superseded). **Origins
  stay `reason_v1` / `reason_v2` exactly as Unified specifies** (finding 16 — the earlier
  draft's `fusion_llm_<horizon>` origin would have destroyed the v1-vs-v2 A/B identity). The
  **horizon call** (`days|weeks|months` — the founder's "where is the biggest payoff and on
  what timeframe" ask) is a **cell tag, not the origin**: meters slice origin×horizon, so we
  learn which horizons the engine calls well AND keep the matched model comparison. The
  server-enforced contract keeps having **no probability field**. Grading uses the plan's own
  time_stop (bounded ≥1 td … ≤ ledger max).
  **No scalp horizon in v1**: the ledger's intraday guard (`db/idea_outcomes.py` [ADR-3]) rejects
  sub-1-td horizons and grading is daily-bar-only — grading a scalp on daily bars would be
  fiction. Scalp returns only if/when intraday grading ships.
- **Honesty (presentation, not construction):** LLM output is a labeled hypothesis — "unproven",
  no win%, Wilson-bounded lane stats only once earned. Horizon/payoff estimates are the LLM's
  stated reasoning, never a calibrated number.
- **Phone pushes come ONLY from the LLM lane**, inside the existing daily push budget. Budgeted
  pings, never a thousand ideas.

### 2.4b Cohort scorecards — ideas graded as portfolios (founder ask 2026-07-18)

Per-idea outcomes are noisy; baskets converge. On top of individual grading, the ledger is
rolled up into **cohort portfolios**, each tracked like its own strategy vs SPY:

- **Cohorts (v1)**: per lane — (1) ALL registered ideas, (2) top-5 per registration batch,
  (3) top-25 per registration batch. If top-5 ≥ top-25 ≥ all ≥ SPY, the ranking has ordinal
  value; if top-5 underperforms all, the score is anti-informative — either answer is the point.
- **Rank is PINNED before the grading window opens (hindsight rule — non-negotiable)**:
  cohort membership derives only from information fixed before `first_evaluable`; "top 5"
  is never recomputed from graded knowledge. Per-lane mechanics (settled in A2, 07-18):
  **artifact lanes** (`llm_conviction`, `reason_*`) already pin `idea_index` = emission
  order per artifact — that IS their batch rank; **`fusion_mech`** stamps
  `score_at_registration` in `source_ref` at the anchor instant, AND — because its grading
  starts at the NEXT session — the registration day's *final* stack score is also fixed
  pre-window, so the nightly rollup may rank fusion cohorts by day-final score, stamped
  once, never recomputed. Both satisfy the rule's actual purpose: no outcome knowledge
  enters the rank.
- **Construction is PRE-REGISTERED (estimand discipline — changed rules = new cohort name)**:
  equal weight per idea; entry at the lane's grading basis (fusion_mech: next-session start;
  LLM lanes: per Unified); exit per each idea's own plan grading; cohort period return = mean
  of member idea returns; cumulative equity curve per cohort vs SPY over the same days,
  direction-consistent benchmark (short ideas measured vs −SPY, per Round 0).
- **Mechanics**: a read-side rollup on the existing grading scheduler — no new write path, no
  registration change beyond the rank stamp. Curves + per-cohort stats land in lane_stats-style
  storage for the widget (§2.8).
- **Presentation honesty**: cohort curves are **observational tracking of a forward journal**,
  labeled as such — never "validated strategy", no win%/EV framing, Wilson-bounded stats only
  once earned. (The Norgate backtest visualizer — `STRATEGY_LAB_SPEC.md`, research branch,
  unbuilt — is the separate backtest view; its "live-vs-backtest overlay" future item is where
  these curves eventually meet it.)

### 2.5 Attention component (social buzz)

Baseline code already exists — this is a MIGRATION, not greenfield: `db/social_mentions.py` has
`_baseline_hour_mean` (same-hour, 14-day lookback, `_MIN_BASELINE_DAYS`, warming/ready states in
`get_buzz`). The change: ~13 samples per (symbol, hour) cell is too thin, so v1 pools hours into
**day-parts** (pre-open / open / midday / close / after-hours) inside the existing baseline path
(~40+ samples/cell); per-hour baselines return once ~6 weeks accumulate. `get_buzz`'s
warming/ready gate survives — thin-history symbols stay silent. Reddit + StockTwits stay separate
dots in one group. No LLM.

### 2.6 Bars (adaptive, observe-first)

*Bars govern ATTENTION surfaces only — registration has no bar (§2.4). Population for every
percentile below = actor-anchored stacks (§2.2 trigger rule).*

- **Inbox-card bar**: top ~25% of trailing-7-day stack scores (recomputed nightly), hard-capped
  at 50 cards/day. Percentile handles scorer changes and regime drift; the cap bounds founder
  attention — coverage GROWS (07-16: cards jumped ~110→~860/day when tape breadth expanded; under
  the old scorer ≥5/day went 4 → 243 in a week), so an uncapped percentile silently scales with
  breadth. Stacks past the cap still exist in the widget — the drop is visible, never silent.
- **Cap is score-ranked, not arrival-ranked (finding 12):** when the cap is full and a
  stronger stack crosses the bar, it **evicts the weakest carded stack** (evicted card is
  auto-dismissed with an `evicted` marker, counted in lane_stats — never silently vanished).
  Without eviction, "top 50" degrades to first-50-by-morning. In-place updates key on
  `group_key` with a uniqueness guard in the new update path (`db/notifications.py` today
  always inserts; the update path is new code and must serialize per group_key so concurrent
  dots can't mint duplicate cards).
- **LLM tier — intraday bar + daily budget, NEVER a nightly top-N** (fixed after fresh-eyes
  audit #3: "top 5/day" implied a close-time batch, contradicting Unified's
  investigate-immediately law and killing same-day pushes — the mission's whole point). A stack
  crossing the **investigation bar** is investigated *immediately*, under a **daily budget of ~5
  with Unified's opening reserve** (a fraction held back so the open can't drain the day by
  09:40). The bar's *threshold* is what gets recomputed nightly from the trailing importance
  distribution, aimed so ~budget-many stacks clear it; selection is intraday, arrival-order,
  budget-capped. **Pushes**: existing `max_push_per_day` budget.
- **The bar becomes data-derived**: with the full population registering and grading (§2.4), the
  score-decile → outcome curve is measured; after the first weeks the card bar is set where
  quality actually cliffs, not where a draft guessed.
- First 2–3 sessions after deploy = observe-only (register + meter, no push changes) to sanity-check
  the new distribution before arming the LLM tier.

### 2.7 Notifications consolidation — the four-lane taxonomy (fork 3 + direction audit)

Every user-facing emission belongs to exactly one lane — detectors never decide delivery:
1. **protective** — held-position risk, failed execution, kill switch, ops criticals. Never
   budgeted, always pushes (exists today; unchanged).
2. **actionable proposal** — investigated, current, fully planned, approval-eligible (the LLM
   lane's output; push-eligible inside the budget). Gains **skip reasons** in Phase C: one-tap
   "already exposed / wrong horizon / disliked thesis / no capital / timing" on every
   rejected proposal — binary taken/skipped confuses taste, constraints, and quality
   (direction-audit blind spot 3), and the taste model needs the why.
3. **digest** — useful, non-urgent: the bar-crossing stack cards below, grouped per
   symbol-day. Inbox-only.
4. **silent evidence** — everything else: retained, graded, explorable in the widget, never
   a notification.

- Per-event convergence cards DIE. **A stack gets an inbox card ONLY when it crosses the
  inbox-card bar (§2.6)** (≤50/day by construction) — the full firehose lives in the widget, not
  the inbox. Registration (§2.4) is unrelated to cards: it's silent bookkeeping. At post-07-16 breadth (~1,250 stacks/day) an ungated stack card per symbol-day would be
  *worse* than today, so card creation is bar-gated, then updated **in place** as further dots
  land (`notifications.group_key`; note: in-place update is NEW code — `db/notifications.py`
  today only flips read/dismissed).
- Discrete notifications survive only for: stack crossed the bar (the in-place card, inbox-only),
  LLM idea proposed (push-eligible), executions/fills/risk (unchanged). Inbox: ~1,250 potential
  stack-cards/day → ≤50.
- **No push blackout between phases**: today's budget-gated convergence push path stays alive
  through Phases A–B and retires only when the Phase C LLM lane arms — the phone never goes dark
  mid-migration.
- **Accepted UX cliff (named, founder-visible)**: Phase A replaces the inbox surface (per-event
  cards → ≤50 ranked stack cards) BEFORE Phase C's investigation quality exists — the cards are
  score-ranked, not LLM-vetted, for the A→C window. Accepted because today's surface is a
  measured ~850–1,000-card/day mess; if the interim cards disappoint, the fallback is a config flag
  restoring legacy convergence cards, not a redesign.

### 2.8 Evidence Stack widget (dev-mode view, web first — founder-approved fork 4)

Two tabs, deliberately unpolished ("the glass wall"):
- **Firehose**: today's stacks ranked by live score — symbol, signature, direction state, score,
  registered?/LLM?/pushed? flags.
- **Drill-in** (per symbol): every dot — time, component, group, raw value, points (and why:
  how the group's directional aggregate moved), score math, bar position (percentile), ledger
  link if registered.
All from `evidence_dots` + existing stores; presentation only. Phone surface deferred until web
proves out. Ticker links click-to-chart per house rule.

**Idea Ledger table (founder ask 2026-07-18 — ships in Phase B beside the stack widget):** a
spreadsheet-style read-only view over `idea_outcomes` — every idea as a row: date, symbol,
direction, lane/origin, rank-at-registration, entry/stop/target, status
(pending/triggered/closed), result %, alpha vs SPY, taken/skipped (+ skip reason once Phase C
captures it). Sort/filter by lane and cohort; tickers click-to-chart. This is the ground-truth
"dev mode" surface.

**Cohort curves live in the STRATEGY LAB, not a widget (founder call 2026-07-18).** The §2.4b
scorecards (all/top-5/top-25 per lane vs SPY) are strategies-being-tracked, and the founder's
framing of the Strategy Lab (`STRATEGY_LAB_SPEC.md`, research branch) is exactly that: one page
where strategies prove themselves — backtested sleeves AND live/tracked cohorts, same visual
grammar (our line vs the SPY line), room to breathe that a grid widget lacks. So Phase B builds
the lab page's first live section (cohort curves over the unified ledger) instead of a
cohort widget; the backtest-visualizer half joins when the PC/Norgate artifacts merge.

### 2.9 Shocks and news (founder ask 2026-07-17 — "the Iran headline problem")

An external shock (war headline, macro print) can crush every open idea and make a good idea
grade bad. Three honest layers — and one refusal:
- **Already absorbed**: the Grading Law grades **alpha-vs-SPY**, so the broad-market component of
  a shock hits the benchmark too and washes out of the grade automatically.
- **v1 rider**: the market-shock flag (context group, above) stamps every stack + open idea during
  the window, so every lane can later be sliced with/without shock days. Structural property worth
  relying on: common shocks hit ALL lanes at once, so **lane-vs-lane ordinal rankings — what the
  filter actually uses — largely survive shocks** even when absolute grades suffer.
- **First post-v1 component**: symbol-tagged **news dots** (catalyst group, event-kind — a
  headline happens at a moment). Source candidate: Alpaca news feed (already-held keys, free,
  symbol-tagged), per the free/keyless-first data rule. Enters alone, per the
  one-component-at-a-time rule, so its lane effect is attributable.
- **Refusal (honesty)**: no per-idea counterfactuals ("it would have won without the war") —
  unknowable, and exactly the win%-precision goose chase the calibration directive bans. Slicing
  by shock flag is the honest version.

## 3. Phasing

- **A — Unified Phase 2 substrate + evidence deltas** (backend): the trigger-intake job
  (cursor-poll, episodes, dispositions, storm shedding — build to Unified §Phase 2), with this
  spec's scoring model (§2.2), `fusion_mech` register-everything (§2.4, incl. `JOURNAL_ORIGINS`),
  social day-part component (§2.5), shock rider, notification consolidation (§2.7). Observe-only
  bars. Legacy convergence push path stays.
- **B — Evidence Stack widget + Idea Ledger table** (web, §2.8) + the **Strategy Lab page's
  first live section**: the cohort-scorecard curves (§2.4b; render as ranked batches
  accumulate). Widgets = daily supervision in the grid; the lab = where strategies prove
  themselves (backtest sleeves join later from the research-branch artifacts).
- **C — investigation tiers + pushes, ONE archetype first** (direction-audit sequencing):
  Unified's dossier → T1 → REASON v1 (+v2 hash A/B) with horizon cell-tags (§2.4), proven
  end-to-end on a single high-value archetype — **options-actor + structure co-fire** — before
  any breadth. Skip-reasons capture ships here (§2.7 lane 2). Arm the LLM tier + pushes after
  the observe window; retire the legacy convergence push lane only then.
- Later (own rounds, not v1): **news dots first (§2.9)**, then FINRA short component (persistence
  design first — it's a fetcher, not a store), filings dots, attention-as-anchor revisit,
  market-sector-state rider, phone surface.

Each phase is a lane/meter change the Grading Law can see; new components after v1 enter ONE at a
time so their lanes are attributable.

## 4. Non-goals

- No win% / probability model anywhere in this build (calibration = filter, not goal).
- No change to Grading Law semantics, Path A, setup-scan lanes, or the execution chokepoint.
- No new always-on data feeds (bus consumes stores that already exist).
- No polish pass on the widget until the mechanism earns it.
