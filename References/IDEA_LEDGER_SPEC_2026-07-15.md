# Idea Ledger & Live Calibration — spec (2026-07-15)

> **SUPERSEDED BY `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md` + `IDEA_ENGINE_PHASE1_SPEC_2026-07-17.md`** (Paths A+B unified) — kept for the record; do not build from this.

**Goal.** One mechanical ledger where EVERY suggested trade idea — from any source, taken or not —
is registered at birth, graded by price, and turned into *calibrated* per-source reliability. The
founder's 2026-07-15 direction: **generation frequency is the calibration flywheel.** LLM sources
fire on event triggers, spin out many ideas, and *every one* is logged and graded. More logging (not
more trading) = faster learning of what actually works. Win% display stays SUPPRESSED until a source
clears a sample gate against THIS ledger — a number is only shown when it is earned.

**This spec extends what already ships — it does not fork a parallel system.** It is the concrete
build of Phase B/C of `IDEA_LEDGER_UNIFICATION_SPEC_2026-07-11.md` (the generalized `register_idea`
surface, short-side, regime, benchmark) *plus* the missing live-calibration compute, the REASON v1/v2
sources, the dossier builder, and the UI. Design law already codified there and honored here: **an
idea is a plan; an event is a fact.** Plans live in `idea_outcomes`; facts (flow bursts, RVOL spikes)
keep their `options_flow_events.ret_*` store, never forced into the idea shape.

---

## 0. What already exists (reuse map — build ON these, do not duplicate)

| Piece | Path | Role in this spec |
|---|---|---|
| `idea_outcomes` table + `register_ideas`/`update_outcome`/`get_summary` | `db/idea_outcomes.py` | THE idea ledger. Add columns + `register_idea`. |
| Mechanical grader `sweep_open_ideas` (daily-bar, tri-state, split-safe) | `research/idea_tracker.py` | Extend with benchmark alpha + ret_pct. |
| Grading job (`idea_tracker`, ~30 min in market) | `api/main.py` + `api/jobs.py` `Job(...)` | Reuse; the sweep IS the scheduler. |
| Convergence event trigger + push budget | `signals/options_flow/convergence.py` | Natural v1/v2 emit trigger. |
| LLM idea generator + fabrication guard `_has_fabricated_probability`, `validate_ideas` | `ai/research/ideas.py`, `ai/research/setup_scan.py` | REASON **v1**. |
| Research Engine v2: planner + critic + specialist roster, `_fallback_plan` | `ai/research/engine_v2.py`, `ai/research/specialists.py` | REASON **v2**. |
| Perception facts builder `get_context(scope="symbol")` | `ai/perception/` + `signals/features.py` | Dossier claim source. |
| Offline Platt/OOF/reliability | `research/calibration.py` | **Different concern** — keep. Live calib is a NEW module (§6, name it apart). |
| Phone proposal `create_phone_proposal` (status `pending_phone`) + `entry_mode` propose/auto | `trading/proposals.py`, `signals/regime_sleeves.py` | Delivery seam (§7). |
| Notify → push (`notify` → `send_alert_push`, Expo/APNs) | `signals/notify.py`, `signals/push.py` | Delivery transport. |
| Widgets `idea-outcomes`, `journal`; `/api/ideas/outcomes`; `usePublishSymbol`/`ClickableSymbol` | `frontend/…/widgets/`, `frontend/lib/api.ts` | Generalize into §8 UI. |

Existing `origin` values in use: `structure`, `llm_conviction`, `structure_vetoed`. New sources add
their own tags (`convergence`, `reason_v1`, `reason_v2`, `dip_buy`, `rvol`, `chat`, …).

DB conventions (all new code follows): `db.base.connect` (WAL, `busy_timeout`), `now_iso()` UTC-ISO,
lazy `init_*_tables()` at top of every reader/writer, migrations via
`db.schema_helpers._add_column_if_missing`. Money/prices as TEXT/Decimal.

---

## 1. Ingestion — one narrow API

Promote a single write path onto `db/idea_outcomes.py` (per the unification spec's B1). Legacy
`register_ideas(artifact_id, ideas)` becomes a thin adapter that loops → `register_idea`.

```python
def register_idea(
    *, origin: str, signal_family: str, symbol: str, direction: str,   # 'long' | 'short'
    entry_zone: dict,              # {low, high}  (price strings)
    stop: str, targets: list[str],
    stated_confidence: float,      # [0,1] — see honesty note below
    thesis: str, bear_case: str,   # bear_case mandatory for LLM sources (§5)
    as_of: str, regime_at_entry: str,
    source_ref: dict,              # {"kind": "artifact"|"convergence"|"chat"|..., "id": ...}
    dossier_ref: str | None = None,
    plan_horizon: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int | None:  # idea_id, or None if deduped/invalid
```

Rules:
- **Cooldown + dedup = sample independence.** Compute `dedup_key = f"{origin}:{symbol}:{direction}:{bucket}"`
  where `bucket` is a per-source time bucket (config `IDEA_COOLDOWN_MIN[origin]`, default 30 min; the
  convergence day already dedups per `(underlying, day)`). A row whose `dedup_key` matches a still-OPEN
  row is skipped. This is what stops one flow burst re-described by two LLM calls from becoming n=2, or
  a 30-print sweep from becoming n=30. `dedup_key TEXT` + partial-unique index over open rows.
- **NO conviction-threshold filter at record time.** Low-confidence ideas ARE logged — they populate
  the bottom reliability bins, which is exactly how the diagram earns its shape. Filtering by
  conviction reintroduces selection bias and is forbidden here (see anti-patterns §10).
- Validation reuses `ai/research/ideas.validate_ideas` shape (zone sane vs last price, stop on the
  correct side per `direction`). Short-side inverts stop/target checks (unification B2).
- **`stated_confidence` is never an LLM-authored probability.** LLM sources emit a *categorical*
  (`low|medium|high`, `ai/research/ideas._CONFIDENCE_VALUES`); the server maps it to a fixed
  representative value (`{low:0.35, medium:0.55, high:0.70}`) — coarse, deterministic, honest.
  `_has_fabricated_probability` already rejects any LLM text that states a numeric win%. Model-scored
  sources (future) may pass a real calibrated probability.

---

## 2. Ledger schema (extend `idea_outcomes`)

Keep the existing table (`id` PK == the "idea_id" of the extraction). Add via `_add_column_if_missing`:

```sql
ALTER TABLE idea_outcomes ADD COLUMN signal_family     TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE idea_outcomes ADD COLUMN direction         TEXT NOT NULL DEFAULT 'long';
ALTER TABLE idea_outcomes ADD COLUMN stated_confidence REAL;          -- [0,1], NULL until known
ALTER TABLE idea_outcomes ADD COLUMN regime_at_entry   TEXT;          -- e.g. 'bull_calm'
ALTER TABLE idea_outcomes ADD COLUMN plan_horizon      TEXT;          -- '5d' etc. (informational)
ALTER TABLE idea_outcomes ADD COLUMN user_action       TEXT NOT NULL DEFAULT 'skipped'; -- taken|skipped|auto
ALTER TABLE idea_outcomes ADD COLUMN source_ref        TEXT;          -- JSON {kind,id}
ALTER TABLE idea_outcomes ADD COLUMN dossier_ref       TEXT;          -- pointer to §4 dossier
ALTER TABLE idea_outcomes ADD COLUMN bear_case         TEXT NOT NULL DEFAULT '';
ALTER TABLE idea_outcomes ADD COLUMN dedup_key         TEXT;          -- §1 cooldown key
-- graded fields (NULL until ripe — NEVER zero-filled):
ALTER TABLE idea_outcomes ADD COLUMN ret_pct           REAL;          -- signed, by-plan
ALTER TABLE idea_outcomes ADD COLUMN spy_ret_pct       REAL;          -- benchmark over same window
ALTER TABLE idea_outcomes ADD COLUMN alpha_pct         REAL;          -- ret_pct - spy_ret_pct
ALTER TABLE idea_outcomes ADD COLUMN exit_reason       TEXT;          -- target|stop|time
```
`(origin, signal_family)` is the calibration cell key. The existing `status`
(`won|lost|expired|invalidated|data_gap`) IS the by-plan outcome; §6 maps `won→1`, `lost→0`,
`expired→neutral` (excluded from win/loss, tracked separately). `entry_low/high` = `plan_entry`,
`stop` = `plan_stop`, `targets_json[0]` = `plan_target`. Migration note: `artifact_id INTEGER NOT
NULL` predates non-artifact sources; `register_idea` writes sentinel `artifact_id=0`, and the
`dedup_key` unique index (not the legacy `UNIQUE(artifact_id, idea_index)`) is the dedup guard for new
rows. Add index `idx_idea_cell ON idea_outcomes(origin, signal_family, status)`.

**Regime tag recipe** (stamped at emit, `signals/regime_tag.py`, new small helper): SPY trend from
50/200 SMA on the daily series already fetched — `bull` if SMA50>SMA200, else `bear`; crossed with a
volatility state — `calm|stressed` from VIX (`db/regime.market_regime()["vix"]`) vs its trailing
median (or 20-day SPY realized vol). Four buckets: `{bull,bear}_{calm,stressed}`. Cache the SPY series
per sweep/emit tick so this is one fetch, not one-per-idea.

---

## 3. Grading scheduler (extend `sweep_open_ideas`)

The existing `research/idea_tracker.sweep_open_ideas` already satisfies most invariants: it is a
**scheduler job** (`idea_tracker`, ~30 min in market — NOT page-load), **time-based ripeness**
(`IDEA_TTL_DAYS`/`RESOLVE_TTL_DAYS` trading-day TTLs), **split-safe** (entry AND exit read from ONE
`fetch_daily_bars_since` adjusted series in the same call), and **missing-data-stays-open**
(`data_gap` is recoverable, never zero-filled; `DATA_GAP_FAIL_STREAK`). Extend it:

1. **Benchmark in the same resolution.** On each row that resolves (`won|lost|expired`), fetch the SPY
   adjusted daily series once per sweep (cache it), compute `spy_ret_pct` over `[first_evaluable_date,
   resolved_at]`, set `ret_pct` from entry-mid→resolution, `alpha_pct = ret_pct - spy_ret_pct`. Both
   sides from the SAME SPY series — no dual-baseline fetch (§10). If SPY bars are missing for the
   window, `spy_ret_pct`/`alpha_pct` stay NULL (win/loss still resolves on the name's own series).
2. **Stamp `exit_reason`** from the resolving branch (`target`/`stop`/`time`).
3. Failure handling unchanged: fail-soft per row (`counts["errors"]`), streak→`data_gap`, recover on
   next successful fetch. No new failure modes introduced.

Cadence: keep the `idea_tracker` job (30 min market / slower off-hours). Calibration (§6) is **read-time
only** — no separate grading-on-read job, and no page-load trigger anywhere.

---

## 4. Dossier builder (claim matrix)

`signals/dossier.py` (new, pure code — no LLM). At trigger time assemble the angles for a symbol into a
scored claim matrix, reusing the perception facts builder (`ai/perception get_context(scope="symbol")`
→ quote, rvol, session_nodes, technicals, fundamentals, earnings, strategy_signals, active_alerts) plus
flow/tape stores:

```
build_dossier(symbol, at) -> {
  claims: [ {angle, statement, value, freshness_s, contradicts:[ids], weight} , … ],
  token_budget_summary: str,   # deterministic, budgeted; fed to REASON
  as_of, symbol,
}
```
Angles: options flow (net premium / largest print), equity tape (block/dark-pool notional, velocity,
spread), RVOL vs time-of-day, volume-profile level distances (`signals/volume_profile.load_profile` +
`compute_session_levels`), earnings proximity, recent headlines/filings, current exposure (held?).
Each claim carries **freshness** (age → decay weight) and **contradiction** links (e.g. bid-side flow
vs bull tape). Persist the assembled dossier (JSON) to a `idea_dossiers(dossier_ref PK, symbol, as_of,
claims_json)` table; `register_idea(dossier_ref=...)` links it so an idea's evidence is inspectable
after the fact (§8 idea detail). Token-budget the summary so REASON prompts stay cheap.

---

## 5. REASON v1 vs v2 — competing sources, the ledger arbitrates

Both fire on the SAME triggers (convergence hit, tape event, RVOL candidate), both consume the §4
dossier, both `register_idea` with distinct `origin`, both use structured outputs with a
deterministic-line fallback (already present as `_fallback_plan`). The ledger decides which wins over
weeks — not a persona debate, not a human hunch.

- **REASON v1** (`research/reason.py::reason_v1`, wrapping `ai/research/setup_scan` + `validate_ideas`):
  ONE structured LLM call, cheap model. Output: thesis, direction, entry_zone, stop, target(s),
  categorical confidence, and a **MANDATORY `bear_case` / invalidation field** (reject the idea if
  empty). `origin="reason_v1"`.
- **REASON v2** (`research/reason.py::reason_v2`, wrapping `ai/research/engine_v2.run_research_v2`):
  bull analyst ∥ bear analyst (the specialist roster) → judge/critic composes the plan + the strongest
  *surviving* counterargument into `bear_case`. `origin="reason_v2"`.

Emit wiring: after `build_convergence(...)` in `signals/options_flow/convergence.py` (and the tape/RVOL
equivalents), a new `signals/idea_emit.py` fans the dossier to v1 and v2 behind an env gate
(`REASON_EMIT_ENABLED`, default off until proven cheap/stable). Generation is decoupled from delivery:
emitting an idea logs it; it does NOT push a phone card (that is §7, gated).

---

## 6. Calibration computation (new: `signals/idea_calibration.py`)

A **live read** over graded `idea_outcomes`, distinct from the offline sklearn `research/calibration.py`
(name them apart to avoid the collision). Pure functions, no I/O beyond the ledger read. Reuse the
existing honesty vocabulary from `signals/decision_scorer.score_symbol` — which already emits
`win_prob`, Wilson `ci_low/ci_high`, `sufficient`, `low_confidence` (fails safe to insufficient),
`brier`, `n_samples` — and align field names with `ai.journal_review.compute_calibration`
(`GET /api/journal/calibration`, the existing realized-vs-predicted surface) so the two don't diverge.

**Cell.** `(origin, signal_family)` optionally × `regime`. Win/loss universe = rows with `status in
{won,lost}`; `won→1`, `lost→0`. `expired`/`invalidated`/`data_gap` excluded from win rate (counted
separately as "unresolved"/"no-plan").

**Reliability bins.** Fixed edges over the full `[0,1]` on `stated_confidence` (deciles; coarser if
sparse). Every bin exists even when empty (surfaces the gap honestly).

**Wilson score interval** (canonical; z=1.96 for 95%), per cell and per bin:
```
p̂ = k/n
center = (p̂ + z²/2n) / (1 + z²/n)
half   = (z / (1 + z²/n)) · sqrt( p̂(1-p̂)/n + z²/4n² )
wilson_lower = center - half     # ← the number the UI displays
wilson_upper = center + half
```
**Brier** = mean((p̂_i − y_i)²) over resolved ideas in the cell; report against the **0.25 baseline**
(Brier of a constant 0.5 forecast) and the base-rate Brier; skill = 1 − brier/0.25.

**Gates.** `MIN_N = 10` resolved per cell/bin. Below it the API returns `{status:"insufficient", n}` —
the UI shows "insufficient data", **NEVER a number**. Per-bin Wilson CIs always accompany the point.

**Regime overlay is gated on a disjoint-CI test.** Only split a cell by regime when the regime
sub-cells' Wilson CIs are **disjoint** (statistically distinguishable) AND each clears MIN_N; otherwise
pool (don't slice n into noise). Return `regime_split: null` when the test fails.

**Optional exponential recency decay** (per-family half-life `RECENCY_HALFLIFE_DAYS[family]`):
weight `w_i = 0.5^(age_days_i / half_life)`. **Floor before decay** — apply the MIN_N gate on the RAW
resolved count first, so decay can never drop a cell below its own gate. Weighted Wilson uses Kish
effective sample size `n_eff = (Σw)² / Σw²` in place of `n`.

---

## 7. Delivery gating (never before it's earned)

- **Every idea source ships DISABLED for phone delivery / propose-only** until the founder arms it —
  the existing product rule (`entry_mode` default `propose`, sources force-created disabled, paper
  only; see `signals/regime_sleeves.py`). Generation (§5) is always allowed; *delivery* is gated.
- **Arming** flips a source to phone-eligible. An armed idea routes through the existing chokepoint-safe
  seam exactly as scan fires do — mirror `trading/proposal_intake.py::propose_from_scan_fire` →
  `propose_trade` (kill-switch / market-open / dedup / `RiskEngine.validate` pre-check) →
  `create_phone_proposal(...)` (status `pending_phone`, Approve/Reject) →
  `send_proposal_push` + `start_proposal_live_activity`. No new push transport. Choose the notify `kind`
  deliberately: `idea_resolved` is `INBOX_ONLY` (no buzz) while a triggered idea pushes; an armed
  Approve/Reject card should push. Rate-limit with the convergence `_peek_push_slot` daily-budget idiom.
- **Displayed win% for an armed source = the Wilson lower bound** of its `(origin, signal_family, bin)`
  cell (§6), placed in the proposal `score` dict. If the cell is below MIN_N → **emit no `win_prob` at
  all** (the card renders "unproven") — follow the existing suppression convention
  (`signals/strategy_stats.py` `REGIME_LOCAL_CONVICTION`: `win_prob=None`, `validated="regime-local"`),
  never a placeholder number.
- **Unvalidated sources are labeled as perception**, never dressed as a scored signal (honesty §9).
- **Future — CI-tier suppression (not silent auto-kill).** A confidence tier whose realized Wilson
  *upper* bound falls below its stated floor is FLAGGED for founder review (a `founder` lane card),
  not silently disabled. Suppress the number, surface the discrepancy.

---

## 8. UI (Next.js workspace widgets)

All widgets obey house law: **no internal header bar** (grid `WidgetHeader` + toolbar own all chrome,
`frontend/components/workspace/workspace-grid.tsx`); controls = toolbar dropdowns
(`*HeaderControls` + `dropdown-menu.tsx`, `workspace-no-drag`/`dragSafeControlProps`); categorical tags
= **plain lowercase colored words, no pill chips**; shared compact timestamps `relativeTime`
(`@/lib/relative-time`); every rendered ticker is a `ClickableSymbol` publishing chart focus via
`usePublishSymbol(channel)` (`channelAware:true` in the registry). Register in `WIDGET_REGISTRY`
(`frontend/lib/workspace/widget-registry.tsx`); poll via `setInterval`+`useState` (no SWR); states via
`WidgetLoading/Empty/Error`. New API calls in `frontend/lib/ideas-api.ts` against `apiFetch`/`API_BASE`.

- **(a) Ideas feed** (`ideas-feed`, generalizes `idea-outcomes-widget` Open tab): live ideas with an
  angles summary (from §4 dossier), the plan (entry/stop/target), and a **calibrated-win%-or-"unproven"**
  label. Toolbar dropdown = source filter (model on `OptionsFlowHeaderControls`). Ticker → chart focus.
- **(b) Per-source scorecard** (`source-scorecard`, generalizes `idea-outcomes` `by_origin` block):
  a **reliability diagram** (predicted vs empirical, **CI error bars per bin**), Wilson win rates +
  sample counts per cell, Brier trend vs 0.25, regime split (only when §6 renders one). Cells below
  MIN_N render "accruing (n)".
- **(c) Idea detail**: the full §4 dossier claim matrix + the graded outcome (ret/alpha/exit_reason)
  once ripe. Reachable from feed row.
- **(d) Phone-card fields**: thesis, `bear_case`, plan, and the calibrated number **or "unproven"**.
  Built from the §7 proposal payload — no win% unless the cell earned it.

Backend routes extend the existing `/api/ideas/*`: `GET /api/ideas/feed`,
`GET /api/ideas/calibration/{origin}[?family=&regime=]`, `GET /api/ideas/{id}` (detail + dossier).

---

## 9. Honesty invariants (testable assertions)

1. **No number below the gate.** For any cell with `n < MIN_N`, every API/UI/phone path returns/renders
   a non-numeric label ("insufficient" / "unproven" / "accruing (n)") — test: assert no float win% in
   the response for a synthetic 9-sample cell.
2. **Displayed win% == Wilson lower bound** of the resolved `(origin, family, bin)` cell — never the
   point estimate, never `stated_confidence`, never an LLM token. Test: response value equals
   `wilson_lower(k, n)` exactly.
3. **No LLM-authored probability survives ingestion.** `register_idea` rejects any idea whose text trips
   `_has_fabricated_probability` or carries a `win/probability/prob` field (reuse `validate_ideas`).
4. **Denominator includes skipped ideas.** Win rate for a source counts `taken` AND `skipped` — test:
   a source with 2 taken + 18 skipped resolved computes over n=20, not n=2.
5. **Missing data is NULL, never 0.** Ungraded/failed-fetch rows have NULL `ret_pct/alpha_pct/outcome`;
   they are excluded from rates, not counted as losses. Test: a `data_gap` row never lowers win rate.
6. **Grading is scheduler-driven.** No calibration or grading executes on an HTTP read/page load —
   test: a `GET` to any ideas route performs zero writes to `idea_outcomes`.
7. **Unvalidated = perception label.** A source with no armed/validated cell is tagged `perception` in
   the payload, never `scored`/`validated`.
8. **Gate label survives every transport.** The honesty flags must not be stripped between the ledger and
   the phone. Today `signals/push.py::_la_content_state` forwards only `win_prob/ev_pct/validated/label`
   and DROPS `sufficient/low_confidence/ci_*/n_samples` (the exact 2026-07-08 red-team finding). Fix:
   the phone/Live-Activity payload must carry the calibration verdict (a proven-vs-unproven flag), and a
   card with `win_prob=None` must render "unproven" rather than a blank — test asserts `_la_content_state`
   emits the gate label for a sub-MIN_N idea and no numeric `winProb`.

## 10. Anti-patterns we're avoiding

- **Conviction-filtered logging** — dropping low-confidence ideas at record time. Reintroduces selection
  bias and empties the bottom bins. Log everything (§1).
- **Page-load grading** — computing outcomes when a widget renders. Non-reproducible, race-prone,
  double-counts. Grading is the scheduler's job only (§3, §9.6).
- **Dual-baseline price fetches** — pulling entry from one series and exit (or SPY) from another; splits
  desync each other and corrupt alpha. ONE adjusted series per resolution, both legs from it (§3).
- **n-inflation** — one flow burst / one debate re-described as many samples. Per-(source,symbol)
  cooldown + `dedup_key` collapse it to one idea (§1).
- **LLM-persona gates** — letting a "more convincing" bull/bear narrative decide which idea ships. The
  ledger arbitrates on realized calibration, not rhetoric (§5).
- **Fabricated win%** — any numeric probability sourced from an LLM or shown below MIN_N (§9).
- **Forking a parallel ledger** — a new `ideas` table beside `idea_outcomes`. Extend the existing one;
  fragmentation is the exact failure the 2026-07-11 unification spec called out.

---

## 11. Phased build plan (verify per phase)

**Phase 1 — ledger + ingestion + grading + one live source (convergence).**
- Add §2 columns + `dedup_key` index; `register_idea` (short-side aware) + legacy adapter; `regime_tag`
  helper. Extend `sweep_open_ideas` with SPY benchmark → `ret/spy_ret/alpha/exit_reason`. Wire
  `signals/options_flow/convergence.py` to `register_idea(origin="convergence")` on each built
  convergence (NO phone push change).
- *Verify:* unit tests — register→dedup (n stays 1 on repeat), short-side stop/target inversion,
  benchmark alpha from a fixture SPY series, NULL-on-missing-SPY. Run a market-hours sweep; confirm
  convergence hits appear as `pending` idea rows and resolve won/lost with `alpha_pct` populated.
  `ruff check .` + targeted `pytest tests/test_idea_tracker.py`.

**Phase 2 — dossier builder + REASON v1.**
- `signals/dossier.py` + `idea_dossiers` table; `research/reason.py::reason_v1` on `ai/research/setup_scan`
  with mandatory `bear_case`; emit v1 on convergence behind `REASON_EMIT_ENABLED`.
- *Verify:* dossier claim matrix on a live symbol has ≥4 angles with freshness/contradiction; v1 emits a
  validated idea (or deterministic fallback) that registers with `dossier_ref` set and NO fabricated
  probability. Cheap-model cost/latency logged.

**Phase 3 — REASON v2 A/B + scorecard UI + live calibration.**
- `reason_v2` on `engine_v2`; both v1/v2 emit on every trigger. `signals/idea_calibration.py`
  (Wilson/Brier/bins/MIN_N/regime-disjoint/decay) + `/api/ideas/calibration/*`. Build `ideas-feed` +
  `source-scorecard` widgets.
- *Verify:* calibration unit tests — Wilson matches canonical formula on known (k,n); MIN_N gate returns
  "insufficient"; regime split suppressed when CIs overlap; Kish n_eff under decay. Scorecard renders
  reliability diagram with CI error bars; sub-MIN_N cells show "accruing". `npm run typecheck`.

**Phase 4 — delivery gating + phone-card integration.**
- Source-arming (disabled by default, `entry_mode` propose→arm) → `create_phone_proposal` for armed
  sources; phone card shows Wilson-LB-or-"unproven"; CI-tier suppression flag → `founder` lane card.
- *Verify:* an unarmed source never creates a proposal; an armed source below MIN_N ships a card with
  "unproven" (assert no number); armed above MIN_N ships the Wilson lower bound. End-to-end via the
  existing test-entry harness in paper. Adversarial (Sonnet) review of the `trading/` proposal touch.

---

*Non-goals (unchanged from unification spec):* no auto-execution from the ledger ever (measurement, not
a signal path — chokepoint/RiskEngine untouched); no merging the flow facts store into `idea_outcomes`;
no backfilled/synthetic outcomes (late registrations start tracking at registration, flagged).
