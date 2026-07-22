# Round-3 Implementation Spec — Episode & Idea Lifecycle (2026-07-22)

**Status: BUILT 07-22 — with the post-review amendments below.** Drafted by a read-only Opus
pass against live code; orchestrator spot-verified the cited code anchors 07-22 (all held,
incl. exact line numbers). Built same day; two split-mandate Opus adversarial reviews ran
before commit (ledger/Grading-Law + state-machine/migration mandates) and their confirmed
findings were fixed pre-commit.

## ⚠ Post-review amendments (the as-built deltas — these override the body below)

1. **`trigger_fired`, not `triggered`** (pre-build amendment): the lifecycle state is named
   `trigger_fired` everywhere — `triggered` collides with the grader's mechanical
   `status='triggered'` (= entry-zone touched), a different axis and meaning.
2. **Two-axis reconciliation is explicit** (review finding 1/2 — the sweep selects rows by
   `lifecycle_state` while the mechanical grader advances `status` on the SAME rows,
   concurrently): `sweep_lifecycle` mirrors first — mechanically-terminal rows
   (`won|lost|expired|invalidated`) retire to `lifecycle_state='closed'` with every grade
   field untouched; `status='triggered'` promotes to `'live'` (no longer cascade/TTL-eligible).
   Every outcome-bearing sweep write is compare-and-swap via `update_outcome(...,
   expect_status=…)` (`WHERE status IN (…)`) so a mechanical outcome resolved between the
   sweep's read and write always wins. `grade_counterfactual` returns `applied` and its write
   is guarded the same way.
3. **The invalidation cascade fires ONLY on `close_reason='price_invalidation'`** (§3 as
   written — the build initially generalized to any close, which would censor counted rows):
   an aged-out close (`decay_to_noise`/`hard_ttl`/`migrated`) is a dead-but-UNBROKEN thesis —
   born rows ride on to their mechanical grade, armed rows run out their trigger TTL into a
   COUNTED counterfactual. No new trigger fires against a closed episode.
4. **`born` is stamped at reason-lane registration** (`_register_v1_idea` →
   `register_idea(lifecycle_state="born")`; registration validates born-only). The synthetic
   class-template shadow deliberately does NOT enter the lifecycle — the cascade must never
   censor the measurement-control cohort.
5. **The rehydration seed is persisted separately** (`seed_bull_mass`/`seed_bear_mass`) and
   `_rescore` folds it forward-decayed from `opened_at` — as first built the first rescore
   overwrote the seed and §2.4 was a dead feature. Close rules still recompute from
   `evidence_dots` only (an all-seed episode with no fresh dots decays to noise).
6. **Closed episodes are immutable**: `_rescore` and `_ensure_episode`'s open-reuse bump carry
   `AND state='open'` — the replay path hands `_rescore` a closed row on purpose, and the
   sweep runs in a genuinely CONCURRENT thread (the body's "single-writer, no race" claim is
   false: intake and lifecycle are separate `asyncio.to_thread` jobs).
7. **The migration rebuild is wrapped in `BEGIN IMMEDIATE … COMMIT`** — `executescript`
   autocommits per statement otherwise; a crash between DROP and RENAME would let the next
   boot re-create an empty table with the data stranded in `fusion_episodes_new`.
8. **Pre-anchor price-invalidation is dropped** (accepted deviation): before the anchor
   latches there is no thesis direction to invalidate against; decay/TTL still bound the
   un-anchored window (it lasts until the first stamped scored dot).
9. `sweep_lifecycle(now, db_path, *, price_lookup=None)` — the price cache is injected like
   `intake_once`; absent, it falls back to the latest stamped dot's `trigger_price`.

**Build order:** lands AFTER Round 1–2 of `FUSION_HORIZON_REDESIGN_2026-07-21.md` (decayed-mass
scorer + founder T pick). Adversarial review required before the build round commits — this
touches the idea ledger + Grading Law. [Both done 07-22.]

**Scope:** Horizon Redesign §2.4 (episode lifecycle), §2.5 (idea lifecycle), §6 step 3.
Shadow-first: nothing user-facing, phone stays dark, no ARMING CHECKLIST rewrite (Round 5).

**Anchor facts confirmed in code:**
- `db/fusion.py` `fusion_episodes` carries `UNIQUE(symbol, market_day)` (line 43); episode id is
  deterministic `_stable_id("episode", symbol, market_day)` (`signals/fusion/intake.py:135`).
  `_ensure_episode` upserts `ON CONFLICT(symbol, market_day)`.
- The intake loop (`fusion_intake`, 60 s, `market_hours_only`) is **event-driven** — it only
  touches an episode when a dot arrives. A silent episode is never re-examined. This is why
  closes need a separate periodic sweep.
- The ledger is one table, `idea_outcomes` (`db/idea_outcomes.py`), with
  `status ∈ {pending, triggered, won, lost, expired, invalidated, data_gap}`, `episode_id`,
  `source_ref`, the `idx_idea_dedup_open` open-row wall, and `find_ideas_by_episode` (:640) for
  register-crash recovery. `JOURNAL_ORIGINS = {structure_vetoed, fusion_mech}` (:58).
- The grader (`research/idea_tracker.py`) already emits `expired` (horizon,
  `fill_status=no_fill` pre-trigger / `filled` post-trigger) and `invalidated` (stop breached
  with zone never touched). `_GRADED_STATUSES = {won, lost, expired}` (:36) get the SPY
  benchmark leg; `invalidated` never does. `fusion_mech` grades daily bars from the next
  session, batch-fetched.
- Fusion writes are single-process (all in `hmt-backend`); collectors write only their own
  stores. So intra-fusion ordering is single-writer — no cross-process race on episode/idea rows.
- Migration convention: `db/schema_helpers._add_column_if_missing` (used throughout
  `idea_outcomes`); fusion tables today use only `CREATE TABLE IF NOT EXISTS` in
  `init_fusion_tables`.

Naming conventions matched below: `snake_case`; TEXT for prices/timestamps (ISO); `_at` suffix
for instants; `_json` for blobs; booleans as `INTEGER NOT NULL DEFAULT 0`; REAL for
scores/masses.

---

## 1. Episode schema delta

New-install `CREATE TABLE` shape (also the target the migration rebuilds to). New columns vs
today are marked `-- NEW`:

```sql
CREATE TABLE fusion_episodes (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market_day TEXT NOT NULL,        -- now = OPENING session only, not a key
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',      -- NEW: open | closed
    closed_at TEXT,                          -- NEW
    close_reason TEXT,                       -- NEW: decay_to_noise|price_invalidation|hard_ttl|migrated
    last_evidence_at TEXT,                   -- NEW: ts of most recent SCORED actor dot
    anchor_dot_id TEXT,
    anchor_price TEXT,
    anchor_atr14 TEXT,
    anchor_direction TEXT,
    registration_called INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    bull_mass REAL NOT NULL DEFAULT 0,       -- NEW: decayed thesis-class mass (cold-storage summary)
    bear_mass REAL NOT NULL DEFAULT 0,       -- NEW
    conviction REAL NOT NULL DEFAULT 0,      -- NEW: |bull-bear|/(bull+bear), 0 when gross 0
    key_levels_json TEXT NOT NULL DEFAULT '[]', -- NEW: structure levels snapshot for rehydrate + price-invalidation
    direction_state TEXT NOT NULL DEFAULT 'mixed',
    contested INTEGER NOT NULL DEFAULT 0,
    signature TEXT NOT NULL DEFAULT '',
    market_shock INTEGER NOT NULL DEFAULT 0
    -- UNIQUE(symbol, market_day) DROPPED
);
CREATE UNIQUE INDEX idx_fusion_episode_open
    ON fusion_episodes(symbol) WHERE state = 'open';   -- one OPEN episode per symbol = the new dedup key
CREATE INDEX idx_fusion_episode_state ON fusion_episodes(state, symbol);
```

The four mass/level columns are exactly the "persisted mass/conviction summary for cold-storage
rehydration" §2.4 demands — no speculative columns beyond them. `bull_mass/bear_mass/conviction`
are produced by the Round-1 decayed-mass scorer and persisted at each `_rescore` (see §2).
Price-invalidation is *derived* from `anchor_price/anchor_atr14/anchor_direction` at check time
(no extra column).

**Dedup-key replacement:** the partial unique index `idx_fusion_episode_open` enforces the §2.4
rule "one OPEN episode per symbol." `_ensure_episode` (`signals/fusion/intake.py:132`) is
rewritten: (a) `SELECT id FROM fusion_episodes WHERE symbol=? AND state='open'`; if found,
reuse it and bump `updated_at`; (b) else INSERT a NEW episode with
`id = _stable_id("episode", symbol, opened_ts)` (opened_ts = the triggering dot's ts;
one-open-at-a-time makes it collision-free), seeding masses from cold storage per §2
rehydration. The `ON CONFLICT(symbol, market_day)` upsert is removed. `evidence_dots.ON
CONFLICT` already preserves a dot's original `episode_id`, so replays stay stably attached.

**Migration (shadow data — pragmatic rebuild).** SQLite cannot `ALTER TABLE ... DROP` a
table-level UNIQUE, so a one-time rebuild in `init_fusion_tables`, guarded by "does column
`state` exist?" (via `_table_columns`):
1. `CREATE TABLE fusion_episodes_new (…new shape…)`.
2. `INSERT INTO fusion_episodes_new SELECT …, 'closed' AS state, updated_at AS closed_at,
   'migrated' AS close_reason, updated_at AS last_evidence_at, 0,0,0,'[]', … FROM
   fusion_episodes`. **Existing ids are preserved**, so every child row (`evidence_dots`,
   `fusion_investigations`, `reason_runs`, `fusion_score_history`, `idea_outcomes.episode_id`)
   stays linked.
3. `DROP TABLE fusion_episodes; ALTER TABLE fusion_episodes_new RENAME TO fusion_episodes;`
   then recreate indexes.

Backfilling all legacy rows to `state='closed'` is correct: they were symbol-day buckets that
are, by definition, over. It also satisfies `idx_fusion_episode_open` trivially (no open rows)
so the index builds clean. Fresh installs skip the rebuild and get the new `CREATE TABLE`
directly.

---

## 2. Episode close rules (exact, testable)

Evaluated by a **new consolidated sweep** `signals/fusion/lifecycle.py::sweep_lifecycle(now,
db_path)`, wired as job `fusion_lifecycle` (`interval_s=60.0`, `market_hours_only=True`) plus
one unconditional run on the after-close path so hard-TTL/decay stamp at EOD. It reads only the
DB + the ATR/levels/price caches — **no memory state, fully rehydration-safe** (see restart
below). Evidence-driven closes may ALSO fire early inside intake's `_rescore` when a fresh dot
invalidates, but the sweep is the authoritative periodic evaluator so a silent or post-restart
episode still closes.

Each pass, for every `state='open'` episode, recompute current-time masses by replaying
`evidence_dots` through the Round-1 decayed scorer at `now` (masses are recomputable from the
append-only log per §2.1), then test in this order and stamp the first that fires
(`state='closed'`, `closed_at=now`, `close_reason=…`):

1. **price_invalidation** — with `anchor_direction` latched: bull closes when
   `latest_price ≤ anchor_price − K_INVAL × anchor_atr14`; bear when
   `latest_price ≥ anchor_price + K_INVAL × anchor_atr14`. `latest_price` = the intake price
   cache (`_fusion_price_from_cache`), same source dots are stamped from. Before the anchor
   latches, use the opening reference price and mass-direction. **`K_INVAL = 1.5` — FOUNDER
   DIAL** (`FUSION_EPISODE_INVAL_ATR`). Wider than the idea's 1× stop on purpose: the episode
   is the thesis, not the trade. (Founder-tunable variant: invalidation against a stored
   `key_levels_json` structural level instead of anchor-ATR.)
2. **decay_to_noise** — thesis gross mass `bull_mass + bear_mass < DECAY_FLOOR` on the **first
   sweep of each session**, for `N_DECAY` consecutive sessions. **`DECAY_FLOOR` — FOUNDER
   DIAL** (`FUSION_EPISODE_DECAY_FLOOR`), starting value set so gross mass yielding a group
   score below ~0.5 counts as noise (i.e. one stale small print cannot hold an episode open).
   **`N_DECAY = 2` sessions — FOUNDER DIAL**. Session-scoped (not per-60 s-pass) so a
   mid-session lull can't false-close.
3. **hard_ttl** — `trading_days_between(opened_at, now) ≥ TTL_SESSIONS`. **`TTL_SESSIONS = 15`
   — FOUNDER DIAL** (`FUSION_EPISODE_TTL_SESSIONS`); matches the swing ceiling (2–15 sessions,
   §2.3) and the thesis half-life (2–3 td, §4) leaves an episode long dead before 15.

**Cold-storage rehydration (§2.4).** When `_ensure_episode` opens a NEW episode for a symbol,
it looks up the most recent `state='closed'` episode for that symbol within
`REHYDRATE_SESSIONS` (**FOUNDER DIAL**, default 10 td) and seeds the new row's
`bull_mass/bear_mass/conviction/key_levels_json` from that summary, **decayed forward** from
the old `last_evidence_at` to `now` by the thesis half-life. Off-watchlist rotation therefore
stops destroying context; a returning name resumes with attenuated prior mass, not zero.
Beyond the window, seed empty.

**Restart / process death.** Nothing lives in memory: `sweep_lifecycle` reloads open episodes
from the DB every pass and recomputes masses from `evidence_dots`. Close is a single atomic
`UPDATE` (open or closed, never half). A crash mid-pass simply re-evaluates next pass —
idempotent. This is the §2.4 "rehydrate from the DB, never from memory" guarantee.

---

## 3. Idea state machine

The redesign lifecycle is a NEW arming/delivery axis layered on the existing mechanical
`status` axis without a second ledger. Add to `idea_outcomes` (via `_add_column_if_missing`,
matching the existing block at `db/idea_outcomes.py:173-228`):

```
lifecycle_state    TEXT           -- born|armed|triggered|live|closed|expired|invalidated; NULL for non-arming lanes
armed_at           TEXT
trigger_json       TEXT           -- {class, ttl, condition:{type, level, direction, threshold}}
trigger_expires_at TEXT           -- denormalized from trigger_json for the sweep's index
```
```sql
CREATE INDEX idx_idea_armed
    ON idea_outcomes(trigger_expires_at)
    WHERE lifecycle_state = 'armed';
```

`lifecycle_state` is the authoritative arming axis; the grader's `status` is the mechanical
projection. `trigger_class` stays inside `trigger_json` (sliceable later via `json_extract`) —
minimum schema. Non-arming lanes (`fusion_mech` journal, `setup_scan`, Path A) leave all four
NULL and behave exactly as today.

**States, transitions, persistence, and who writes:**

| From → To | Condition | Writer | Persist |
|---|---|---|---|
| — → **born** | reason-lane dossier registers hypothesis | `research/reason.py` → `register_idea` (exists) | `status=pending, lifecycle_state=born` |
| born → **armed** | investigator attaches plan + `trigger_json` + `trigger_expires_at` | reason lane (ARM round writes it live; Round 3 plumbs the fields dark) | `lifecycle_state=armed, armed_at, trigger_json, trigger_expires_at`; `status` stays `pending` |
| armed → **triggered** | trigger condition evaluates true before `trigger_expires_at` | `sweep_lifecycle` | `lifecycle_state=triggered` (proposal happens in ARM round) |
| triggered → **live** | entry zone touched | existing grader (`_process_pending` → `status=triggered`) | `lifecycle_state=live` mirrored from `status=triggered` |
| live → **closed** | plan hits target/stop | existing grader | `status=won\|lost, lifecycle_state=closed` |
| armed → **expired** | `now ≥ trigger_expires_at`, trigger never fired | `sweep_lifecycle` | counterfactual grade — §4 |
| armed\|born → **invalidated** | underlying episode closes `price_invalidation` (or decays hard against) while idea not yet live | `sweep_lifecycle` | §4 |

Naming note: lifecycle `triggered` = trigger *condition* fired (ready to propose); the grader's
`status=triggered` = entry-*zone* touched (position on) = lifecycle `live`. Kept distinct in
the two columns; prose disambiguates.

**Trigger condition representation & mechanical evaluation.** `trigger_json`:
```json
{ "class": "impulse", "ttl": {"unit": "hours", "value": 4},
  "condition": {"type": "level_touch", "level": "212.40", "direction": "bull", "threshold_atr": 0.1} }
```
Two classes, differing only in TTL scale and condition type — both evaluated identically from
persisted values (no live fetch):
- **impulse** (hours TTL, **default 4 h — FOUNDER DIAL** `FUSION_TRIGGER_TTL_IMPULSE_HOURS`):
  condition types `level_touch` (latest cached price within `threshold_atr` of `level`) and
  `impulse_cluster` (episode one-sided fast-class mass ≥ threshold AND a level touch). Sharp,
  session-scoped.
- **thesis_confirmation** (days TTL, **default 3 td — FOUNDER DIAL**
  `FUSION_TRIGGER_TTL_THESIS_SESSIONS`): types `level_reclaim` (price closes back beyond
  `level` in `direction`) and `rvol_confirm` (RVOL ≥ threshold with mass direction intact).

Evaluation reads: intake price cache, `fusion_session_levels`, `fusion_atr_cache`, and the
episode's recomputed masses — all already-persisted, storm-cheap, and injectable in tests.
`trigger_expires_at` is computed once at arm time (`armed_at` + TTL over the trading-day
calendar for the days class, wall-clock for hours).

**Cadence.** `sweep_lifecycle` evaluates triggers every 60 s in market hours — fast enough for
impulse's hours-TTL and price-touch responsiveness; the days-class is over-served, which is
fine. Both classes share the one sweep.

**Trigger-vs-TTL race (single pass, single writer):** within a pass, evaluate the fire
condition **before** the TTL check for each armed idea, so an idea whose trigger fires on the
same pass its TTL lapses transitions to `triggered`, not `expired`. Because all fusion writes
are single-process, there is no cross-process race; ordering is deterministic.

**Crash/restart for armed ideas.** `armed` rows persist fully in the ledger (`trigger_json`,
`trigger_expires_at`, `armed_at`); the sweep reloads them each pass — restart mid-armed just
resumes evaluation. The register-then-crash window (row written, `reason_runs` marker not) is
already covered by `find_ideas_by_episode` (`db/idea_outcomes.py:640`), which the arm path must
consult before re-registering. Every transition is an idempotent guarded
`UPDATE … WHERE lifecycle_state = <expected>`.

---

## 4. Grading integration (one ledger, no bypass)

Both terminal arming states resolve **into `idea_outcomes`** using existing columns — no new
table, no new status enum value.

**expired = armed-never-triggered → counterfactual grade.** When `sweep_lifecycle` expires an
armed idea, it does not merely stamp `no_fill`; per the Grading Law it records *what the plan
would have done*. A new helper `research/idea_tracker.py::grade_counterfactual(row, now)` walks
the plan forward from `armed_at` as an **always-filled** counterfactual (mirroring the
`_process_horizon` filled-counterfactual pattern, entry = entry_ref), and writes via existing
`update_outcome`:
- `status='expired'`, `lifecycle_state='expired'`, `exit_reason='trigger_ttl'` (**new string
  value on the existing `exit_reason` column**), `fill_status='counterfactual'` (**new string
  value on the existing `fill_status` column**).
- `ret_pct / spy_ret_pct / alpha_pct` stamped via the existing `_stamp_benchmark`
  (direction-consistent alpha, §Grading Law) — because `expired ∈ _GRADED_STATUSES`, the
  benchmark leg is stamped automatically once `fill_status != 'no_fill'`.

This makes the load-bearing measurement possible: *did the trigger gate withhold losers or
wrongly withhold winners?* Cohorts slice on `fill_status='counterfactual'` to compare
armed-and-fired vs armed-and-expired outcomes. `GRADEABLE_STATUSES` already includes `expired`,
so founder 5b grading is unaffected; `JOURNAL_ORIGINS` filtering is untouched.

**invalidated.** When the episode under an armed/born idea closes `price_invalidation` (or
hard-against decay), the sweep stamps `status='invalidated'`, `lifecycle_state='invalidated'`.
Per existing law `invalidated ∉ _GRADED_STATUSES` → **no benchmark leg** (the setup never
became real; a ret/alpha for it would be phantom). This matches the grader's existing
invalidated semantics exactly.

**One-ledger confirmation:** every terminal write is a `update_outcome` / `register_idea` call
on `idea_outcomes`. `fusion_investigations` / `reason_runs` remain markers, never outcome
stores (the code comment at `db/fusion.py:122-126` already encodes this rule). No new ledger is
introduced; expired/invalidated are causes/reasons on the existing rows, satisfying §2.5's "new
graded outcomes in the ONE existing ledger."

---

## 5. Edge cases + targeted test plan

**Edge cases and required behavior:**
- **Overlapping sequential episodes on one symbol** — `idx_fusion_episode_open` guarantees at
  most one open; the second material dot after a close opens a *new* episode (new id,
  rehydrated masses). Prevents the symbol-day merge the redesign kills.
- **Evidence for a closed episode** — `_ensure_episode` finds no open episode → opens a new one
  (does NOT reopen the closed row). The replayed-dot case keeps its original `episode_id` via
  `evidence_dots.ON CONFLICT`; a genuinely new post-close dot starts the next episode. Closed
  episodes are immutable.
- **Armed idea whose episode closes under it** — sweep transitions the idea to `invalidated` in
  the SAME pass it closes the episode (episode close evaluated before dependent-idea evaluation
  within the pass), so an armed idea never dangles against a dead thesis.
- **TTL sweep racing a trigger fire** — resolved by the fire-before-expire ordering in §3,
  single-writer.
- **Restart mid-armed** — armed row + `trigger_expires_at` reload from DB; sweep resumes.
  Covered with a fresh-connection test (call sweep, simulate restart by re-opening the DB, call
  again).

**Targeted test modules (never full suite):**
- **`tests/test_fusion_episode_lifecycle.py` (NEW)** — open→close for each reason
  (price_invalidation with synthetic price cache, decay_to_noise with `now` advanced past
  thesis half-life, hard_ttl with `now` +15 td); sequential-episode open after close;
  evidence-for-closed opens new; cold-storage rehydration seeds decayed masses; restart
  idempotency (double sweep + fresh connection).
- **`tests/test_idea_trigger_machine.py` (NEW)** — armed→triggered on each condition type
  (`level_touch`, `level_reclaim`) via injected caches; armed→expired on TTL with
  counterfactual grade asserted (`exit_reason='trigger_ttl'`, `fill_status='counterfactual'`,
  non-null `alpha_pct`); impulse (hours) vs thesis (sessions) TTL computation;
  fire-before-expire race ordering; restart mid-armed.
- **`tests/test_fusion_intake.py` (EXTEND)** — `_ensure_episode` keys on
  open-episode-per-symbol; `idx_fusion_episode_open` rejects a 2nd open row; `_rescore`
  persists masses/`last_evidence_at`.
- **`tests/test_idea_tracker.py` (EXTEND)** — `grade_counterfactual` benchmark leg; invalidated
  stays out of `_GRADED_STATUSES`; no new table created.
- **`tests/test_idea_ledger.py` (EXTEND)** — `lifecycle_state` column present; headline
  win-rate / journal filters unchanged by counterfactual-expired rows.
- **Migration test** (in `test_fusion_episode_lifecycle.py`) — build an old-shape
  `fusion_episodes` with `UNIQUE(symbol, market_day)` and rows, run `init_fusion_tables`,
  assert rows become `state='closed', close_reason='migrated'`, ids preserved, UNIQUE gone,
  partial index present.

Every case is driven by synthetic dots, injected `price_lookup`/ATR/levels caches, and a fixed
`now` — **no live market data**, per constraint.

---

## 6. Explicit non-goals (Round 3)

- **No user-facing changes** — shadow-first. No Desk/Ledger widget work, no payload changes.
  Delivery seams untouched.
- **Phone stays dark** — `REASON_EMIT_ENABLED` / `REASON_SHADOW_ONLY` unchanged;
  `alerts.max_push_per_day` stays 0. No push path armed.
- **No attention-tier work** — Tier 1/2/3 mapping is Round 4.
- **No ARMING CHECKLIST rewrite** — the checklist in `config/idea_engine.py` is Round 5.
  Round 3 *plumbs* the arming columns/evaluator and tests them on synthetic rows; the reason
  lane writing real `trigger_json` and the checklist flip stay dark. The
  `assert REASON_SHADOW_ONLY is True` in `validate_config` remains.
- No change to Grading Law semantics, `fusion_mech` plan formula (frozen estimand), scorer
  decay classes (Round 1), or the maturity threshold T (RESET, founder-picked later).

---

## Critical files for implementation
- `db/fusion.py` — episode schema delta, rebuild migration, open-episode lookup + rehydration helpers
- `signals/fusion/lifecycle.py` — NEW: `sweep_lifecycle` (episode close rules + trigger evaluator), wired as the `fusion_lifecycle` job
- `signals/fusion/intake.py` — `_ensure_episode` rewrite (one-open-per-symbol), `_rescore` mass/`last_evidence_at` persistence, evidence-driven early close
- `db/idea_outcomes.py` — `lifecycle_state` + `armed_at`/`trigger_json`/`trigger_expires_at` columns, armed index, one-ledger guarantee
- `research/idea_tracker.py` — `grade_counterfactual` (expired trigger-TTL), invalidated handling
- `config/idea_engine.py` — the FOUNDER DIAL constants
