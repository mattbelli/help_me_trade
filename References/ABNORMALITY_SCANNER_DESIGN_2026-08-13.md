# Vol/Volume Abnormality Scanner — Design Brief

*2026-08-13 · card #296 (`vol-abnormality-scanner`) · status: founder review, then build.
Founder direction 07-30 (replaces the parked flow-perception surface); four design decisions
locked with the founder 08-13 (§1). Machinery provenance: 1.14f/g studies
(`References/FLOW_ROADMAP.md`, `tools/mover_intraday_114g.py`).*

## 0. Mission fit + honesty posture

A **perception source** under the connect-the-dots doctrine: it supplies an angle — "this name is
behaving abnormally right now, and here's how abnormal" — never a verdict, never a win%. No edge
gate applies to building it; honesty rules constrain **presentation** (§5).

Measured context we are allowed to state (in-sample, recorded in the 1.14f/g rows): names fired by
the pure equity-RV screen moved **32–43% more than their own time-of-day-matched norm over the
next 2h**, and the direction split was a **coin flip** (all four direction tests dead null). So
this scanner is a **magnitude tell**: it says *something is happening*, not *which way*.

The founder's accumulation thesis is the second half: multi-week bases with escalating volume =
large players building / hands changing; fine-grain abnormality is the *earlier* tell that the
base is waking up. The pairing of the two detectors is the headline event.

## 1. Founder decisions (locked 2026-08-13)

1. **Symmetric both sides.** Base-at-low (accumulation) AND base-at-high (distribution/loading)
   from day one — never long-only.
2. **Grains: 5m + daily at launch.** The 1-minute accumulator is built into the collector (it
   feeds the 5m windows anyway) but 1m *firing* is a fast-follow, not launch scope.
3. **Idea ledger NOT wired.** Founder: "the idea ledger is probably dead as it stands."
   (Measured 08-13: mechanically alive — 17,481 rows, registrations through 08-12, sweeper ran
   08-13 00:47Z — but ~8.4k of those are mechanical `fusion_mech` rows; treated as founder
   distrust of the surface. Ledger revival is its own thread.) The scanner keeps its **own
   outcome table** with the right estimand for a direction-less signal (§6).
4. **Compute over every name the tape sees** (~11k active US equities incl. microcaps); any junk
   filtering happens at display time, never in the math. **Surfaces-only launch** — no inbox
   cards, no phone, until fire rates are measured live.

## 2. What it computes

### 2a. Intraday abnormality (5m grain, RTH)

Per symbol at each 5-minute boundary `b` (bucket 0–77):

- **range_ratio** = Parkinson RV over the trailing 60-min window `[b−12, b)` ÷ the name's own
  trailing-20-session median RV at this same bucket. The `rv_park` machinery from 1.14g
  (per-bin averaged, zero-range bars present-and-zero, ≥80% bins present else **absent** — never
  fabricated). *Ungapped*: the study's one-bar gap was outcome-measurement hygiene (collider),
  irrelevant for live firing.
- **vol_ratio** = trailing-60-min volume ÷ own median window volume at this bucket.
- **rvol_session** = cumulative session volume ÷ own median cumulative at this bucket (classic
  RVOL, from the same baseline artifact — no dependency on `signals/volume_profile.py` JSONs).
- **peer percentiles** of range_ratio and vol_ratio within the cell
  (bucket × trailing-20-session $-vol quintile) — the 1.14d cells: a name is compared only to
  names of similar dollar-volume at the same time of day, so a market-wide wild day doesn't
  light everything up.

**Fire rule (draft — replay-tuned before launch, §8 Inc 2):** fire when range_ratio ≥ p99 of its
cell AND vol_ratio ≥ p95 of its cell AND absolute floors hold (range_ratio ≥ 2.0,
vol_ratio ≥ 3.0). One fire per (symbol, session); re-fire only after 60 min if the composite
exceeds the prior fire by ≥50%. Market-wide budget: top-N per boundary by composite (draft
N=10). Target expected volume: **tens of fires/day market-wide** — rare-great-ideas discipline;
the replay harness over the 251-session store picks the actual thresholds by measured fire rate,
not by argument.

Everything computed is also published as **state** (not fires): latest ratios + percentiles per
symbol per boundary → the snapshot table feeding the watchlist column and scan surface.

### 2b. Base-state detector (daily grain, nightly)

From market-wide daily bars, per symbol, both sides (draft params — tuned by founder eyeball over
known charts + counts, §8 Inc 3):

- **base_low** ("accumulation candidate"): close within 15% of the trailing 26-week low; in the
  bottom 20% of the 52-week range for ≥20 of the last 30 sessions (a base, not a falling knife);
  **volume escalation** = median(20d volume) ÷ median(preceding 60d volume) ≥ 1.3.
- **base_high**: exact mirror at the top of the range.
- Output per symbol: `base_state ∈ {none, base_low, base_high}` + strength fields (weeks in
  base, volume-trend ratio).

### 2c. The pairing — the headline event

An intraday fire on a name whose `base_state ≠ none` emits kind **`abnormality_paired`** instead
of `abnormality`. Expected rare (a handful/week market-wide). This is the founder-thesis event
and the only candidate for notifications later (§8 Inc 6).

## 3. Data + baselines (nightly)

- **5-min store**: extend `data/equity_bars_5min/` (currently 251 sessions → 2026-07-27) via
  `tools/equity_bars_5min_backfill.py` — first a gap backfill 07-28→today, then nightly
  incremental (~5 MB/session). Universe switches from flow_rollup roots to **all active Alpaca
  US equities** (`/v2/assets?status=active&asset_class=us_equity`, already wired in
  `brokers/alpaca.py:883`) via a `--universe` flag — a superset; same npz layout, same
  part→rename + stats-JSON-last atomicity doctrine.
- **NEW daily store**: `data/equity_bars_daily/<session>.npz`, same doctrine, same fetcher
  pattern (1Day timeframe). Initial backfill ~420 sessions, then nightly append. (Checked:
  `momentum_daily_bars` won't do — 503 symbols, open/close only, no h/l/v.)
- **Baseline artifact**: `data/abnormality/baselines-<session>.npz` + manifest, rebuilt nightly:
  - per symbol × bucket: median-20-session Parkinson range, window volume, cumulative volume;
  - per symbol: median daily volume, median $-vol, **quintile id**, `base_state` + strength;
  - per symbol × bucket: median forward-2h max|move| (the **outcome norm** for grading, §6).
  Collector loads it at startup and reloads pre-open (calendar-gated). **Stale artifact
  (>3 sessions old) → detector stands down** rather than fire against stale norms (the #309
  refuse-stale doctrine).
- **Warmup**: a symbol needs ≥10 qualifying sessions of the trailing 20, else absent. New
  listings stay absent until warm.
- **Corporate-action guard**: baselines are split-adjusted; the live tape is raw. If the session
  opens with a price gap >40% vs the baseline day's close, or the first boundary shows a >50×
  volume ratio riding a large gap, suppress the symbol for the session (`ca_suspect`); the
  nightly rebuild absorbs the split. Renames arrive via the assets refresh (new symbol → warmup).

## 4. Live detector (rides `hmt-equity-collector`)

New package `signals/abnormality/` (baselines, detector, base_state, replay). The detector hangs
off `EquityTapeRunner` beside `TapeDetector`/`VelocityDetector`:

- **Hot path O(1) per print**: update the symbol's current 1-minute bar (high/low/volume). Ring
  of trailing 1m bars per symbol (60 deep — feeds the 5m windows now, the 1m grain later).
  Memory ~11k symbols × 60 bars × 3 floats ≈ a few MB.
- **Boundary work on a 5-min timer task** (never the recv loop): aggregate rings → ratios →
  cell percentiles → fires + state snapshot. ~11k rows × a few ops + a handful of sorts; must
  measure <1% of a core at the close burst (the tape itself peaks at 20.3% of one core).
- **Mid-session restart**: one bounded bulk fetch of today's 5-min bars primes the rings; until
  primed, ratios are absent (never fabricated).
- **Persistence via the flush loop**: fires → new table `abnormality_events` in `trading.db`
  (DEFAULT_DB_PATH → conftest test isolation holds); state → batched single-transaction upsert
  into `abnormality_state` per boundary. NOT into `equity_tape` events (windowed state ≠ a
  print) and convergence's `_TAPE_EVENT_TYPES` is NOT extended at launch.

## 5. Honesty + labels (fixed now, not at build)

Every event and state row carries label + caveat fields, and they survive every transport
(the honesty-redteam rule):

> **abnormality perception — magnitude tell.** Selects unusual range/volume vs this name's own
> time-of-day norm and its peers. Measured in-sample (1.14f/g): fired names moved 32–43% more
> than their time-matched norm over the next 2h; direction was a coin flip. Not a validated
> edge; no direction implied.

Paired events add: *"Base-state pairing encodes a founder thesis (accumulation/distribution) —
labelled thesis, not measurement."*

No win%, no probability-dressed score. The UI shows plain ratios ("6× normal range, 8× normal
volume, top 0.5% of peers"), caveats in drawers per honesty-calibrated-not-loud.

## 6. Outcome tracking (own table — the ledger stays unwired, §1.3)

`abnormality_outcomes`: one row per fired event — forward max|move| over 2h and to close,
divided by the baseline forward norm (**excess ratio**), plus realized direction. A nightly
sweeper grades the prior session's fires straight from the extended 5-min store (no live
polling). Aggregates (fire counts, median excess, direction split) surface on the scan footer
and the perception resource; any *rate* ever displayed is Wilson-bounded. This is
calibration-counts-everything with the correct estimand for a direction-less signal.

## 7. Surfaces — definition of done (wire-it-everywhere)

1. **Watchlist column** `abnorm` — the RVOL-column pattern end-to-end: attach from
   `abnormality_state` in `/api/watchlist/market`, `WatchlistQuote` field, one
   `WATCHLIST_COLUMNS` entry, color ramp, dim-when-stale.
2. **Scan surface** — `GET /api/scanner/abnormality` reading the state table: ranked live list,
   filters (fires today / paired / base_low / base_high). Lands **inside the existing scan UI**
   (widget-consolidation rule: merge proposal, no new widget). The bespoke `/api/scanner/rvol`
   (Schwab-quote-driven, ~986 liquid-core names) is a candidate for later subsumption — noted,
   not touched now.
3. **Fusion dots** — attach lane `_attach_abnormality`, template = `_attach_rvol`
   (`signals/fusion/intake.py:896`): annotate-only group, **points 0.0 pinned**, source_store
   `"abnormality"` deliberately not in `STORES` (rides episodes, never opens them), rolling
   state dot episode-keyed + paired fires as their own dot kind. Unavailable states publish
   `raw_value=None` + a state label, never a frozen reading.
4. **Agent perception — all three surfaces** (the bypassed-perception lesson):
   `ai/perception/resources.py` RESOURCES entry `abnormality` (+ aliases `vol_scanner`,
   `unusual_activity`) reading state/events/outcomes; per-symbol field in
   `ai/perception/context.py` (like rvol); a compact "today's abnormality fires" line in the
   nightly **book pack** market frame (the CPI lesson — packs are what books read first); and
   confirm `research/book_tools.py` + `tools/book_session.sh --allowedTools` reach it.
5. **Ticker links** everywhere a symbol renders.

## 8. Build increments (each committable, verify step each)

1. **Data**: `--universe` flag + 07-28→today gap backfill on the 5-min fetcher; daily fetcher +
   ~420-session backfill; nightly cron chain (flock pattern of `flow_perception_nightly.sh`;
   mind the exec-bit and cron-cd traps). *Verify: manifests complete; spot-check bars vs vendor.*
2. **Baselines + replay**: builder + artifact; replay harness over the full store → fire-rate
   tables at candidate thresholds + example-fire galleries; **founder eyeballs, thresholds
   locked**. *Verify: builder idempotent; replay counts reproducible.*
3. **Base-state detector**: nightly, both sides; galleries for founder eyeball; params locked.
   *Verify: known accumulation charts he names actually flag.*
4. **Live detector**: collector integration + events/state tables + outcome sweeper; runs dark
   for a few sessions. *Verify: live fires == replay on the same session's store (parity test);
   hot-path CPU delta measured at the close burst.*
5. **Surfaces**: column, scanner endpoint + scan-UI merge, fusion attach lane, perception ×3 +
   book pack, ticker links. ui-review agent pass. *Verify: the two-question check — can the AI
   see it? what dead ends are left?*
6. **Founder-gated, later**: notifications for paired fires; 1m-grain firing; convergence
   membership; any fusion scoring promotion. Explicitly not launch scope.

## 9. Out of scope / future

GTH/overnight (baselines are RTH-only), options-flow pairing on abnormal names (future
connect-the-dots angle), ETF exclusion (revisit only with replay evidence of spam), idea-ledger
wiring (revival is its own thread), phone push.

**Accepted edge cases**: halt-resume bars will fire (that's genuinely "something happening";
cooldown bounds it). Thin names go absent under the coverage rule rather than noisy. Early
closes follow the session calendar. Collector restarts prime from the bulk endpoint.
