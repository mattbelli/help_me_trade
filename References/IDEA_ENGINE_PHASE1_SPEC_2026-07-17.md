# Idea Engine — Phase 1 implementation spec (2026-07-17)

*Builds Phase 1 of `IDEA_ENGINE_UNIFIED_DESIGN_2026-07-16.md`: **make silence illegal + ledger
foundation + the known honesty fix** — on lanes that already run. No new LLM capability in this phase.
Every section names exact files, signatures, and tests. Decisions a reviewer might contest are marked
**[ADR-n]** with rationale; the founder can veto any of them before build.*

**Phase 1 delivers, in one sentence:** every existing idea lane (nightly scan, convergence, Path A
proposals) registers into ONE ledger with counted dispositions and rate alarms, the phone transport
stops stripping honesty flags, and the three diagnosed nightly-scan bugs die — so when Phase 2-3 add
event-driven LLM generation, they land in plumbing that already cannot fail silently.

---

## 0. Scope fence

IN: ledger schema + `register_idea`; Path A registration + `user_action` stamping; SPY-benchmark
grading leg; Grading Law v1 (constants + tie-breaks + `no_fill` semantics, daily-bar only); lane_stats
counters + rate alarms + canary + external dead-man; `_la_content_state` fix; the 3 nightly-scan bugs.

OUT (explicitly deferred): trigger bus/episodes (Phase 2), dossier/T1/REASON (Phase 3), calibration
math + widgets + digests (Phase 4), push arming + serializer (Phase 5), **minute-bar grading** — see
[ADR-3] — and the auditor-as-own-systemd-unit split (Phase 1 runs it as an `api/jobs.py` Job; the
external dead-man cron provides the outside-the-failure-domain guarantee that motivated the split).

---

## 1. Ledger schema — `db/idea_outcomes.py`

Extend `init_idea_outcomes_tables` with `_add_column_if_missing` (existing migration idiom, verified
lines 142-152):

```sql
-- identity of the sample cell
signal_family      TEXT NOT NULL DEFAULT 'unknown'   -- trigger/strategy family
direction          TEXT NOT NULL DEFAULT 'long'
confidence_label   TEXT                              -- 'low'|'medium'|'high' (categorical STRING —
                                                     --  [ADR-1]; numeric map applies only inside
                                                     --  future Brier computation, never persisted)
regime_at_entry    TEXT                              -- '{bull,bear}_{calm,stressed}' + version suffix
plan_horizon       TEXT                              -- '5d', 'earnings_exit', ...
-- provenance + audit
user_action        TEXT NOT NULL DEFAULT 'skipped'   -- 'skipped'|'taken'|'auto'
source_ref         TEXT                              -- JSON {"kind": "...", "id": ...}
dossier_ref        TEXT                              -- NULL until Phase 3
bear_case          TEXT NOT NULL DEFAULT ''
dedup_key          TEXT                              -- origin:symbol:direction:bucket
episode_id         TEXT                              -- NULL until Phase 2
cluster_key        TEXT                              -- (direction, week-bucket, macro-day) — stamped
                                                     --  at registration so Phase 4 math has history
synthetic          INTEGER NOT NULL DEFAULT 0        -- canary namespace; every calibration/summary
                                                     --  read path adds WHERE synthetic = 0
user_id            TEXT                              -- single-user today; NULL = founder
-- grading (NULL until ripe — NEVER zero-filled)
grading_basis      TEXT NOT NULL DEFAULT 'plan'      -- 'plan'|'horizon_return'  [ADR-2]
grading_resolution TEXT NOT NULL DEFAULT 'daily'     -- 'daily' only in Phase 1  [ADR-3]
fill_status        TEXT                              -- NULL|'filled'|'no_fill'
ret_pct            REAL
spy_ret_pct        REAL
alpha_pct          REAL
exit_reason        TEXT                              -- 'target'|'stop'|'time'
```

Indexes: `idx_idea_cell ON idea_outcomes(origin, signal_family, status)`;
`CREATE UNIQUE INDEX idx_idea_dedup_open ON idea_outcomes(dedup_key) WHERE dedup_key IS NOT NULL AND
status IN ('pending','triggered')` (partial unique — the open-row dedup wall from spec §1).

**[ADR-1] `confidence_label` TEXT, not `stated_confidence REAL`.** The design doc superseded the
07-15 spec here: a persisted 0.70 float is reachable by the perception layer and chat — the exact
transport shape of the 07-08 leak. The string is unlaunderable. Future model-scored sources get a new
`calibrated_prob` column when one exists; never reuse this one.

**[ADR-2] `grading_basis` distinguishes two estimands.** Plan-shaped ideas (zone/stop/targets — all of
Path B and future REASON) grade by the Grading Law: T1-before-stop. Path A proposals are NOT
plan-shaped (`TradeCandidate` carries symbol/side/notional/rationale — verified `proposal_intake.py:45`
— no stop, no targets); forcing fake zones onto them would corrupt the estimand. They grade
`horizon_return`: entry ref = price at proposal creation, horizon = per-strategy template (below),
won = `ret_pct > 0` at horizon, `exit_reason='time'`, `fill_status='filled'` for taken/auto rows and
for skipped rows too (a market order at ref would have filled — the counterfactual is honest).
Path A cells are therefore **never pooled with plan cells** — `grading_basis` is part of the cell key
in Phase 4, stamped correctly from birth now.

**[ADR-3] Phase 1 grades daily-bar only.** No intraday-horizon origin exists yet: the two enabled
strategies (`earnings_drift`, `momentum_sp500`) are multi-day; session_node is disabled; REASON lands
Phase 3 with the minute-bar grader (design Grading Law). Guard: `register_idea` REJECTS (logs +
counts `rejected_intraday_horizon`) any registration whose `plan_horizon` parses to <1 trading day,
so a prematurely-armed intraday source cannot silently mis-grade on daily bars. The column ships now
so no backfill relabeling is ever needed.

## 2. `register_idea` — the narrow write path

New function beside `register_ideas` (which stays as-is for the artifact-keyed setup_scan path —
**[ADR-4]**: sharing a private `_insert_idea` core instead of rewriting the legacy caller keeps the
nightly lane's regression surface at zero this phase; the unification spec's "thin adapter" happens
when setup_scan itself migrates in Phase 3):

```python
def register_idea(
    *, origin: str, signal_family: str, symbol: str, direction: str = "long",
    entry_zone: dict | None,          # {"low","high"} strings; None only for horizon_return basis
    stop: str | None, targets: list[str] | None,
    thesis: str = "", bear_case: str = "",
    confidence_label: str | None = None,     # validated against {'low','medium','high'}
    as_of: str | None = None, plan_horizon: str | None = None,
    grading_basis: str = "plan",
    source_ref: dict | None = None, user_action: str = "skipped",
    regime_at_entry: str | None = None,
    entry_ref_price: str | None = None,      # horizon_return basis: the graded entry
    db_path: Path = DEFAULT_DB_PATH,
) -> int | None:                              # idea id | None if deduped/invalid
```

Rules (from spec §1, all testable): `dedup_key = f"{origin}:{symbol}:{direction}:{bucket}"` where
bucket = ET date + `IDEA_COOLDOWN_MIN[origin]` window (default 30 min; config §7); insert relies on the
partial unique index (race-safe, `INSERT OR IGNORE` + rowcount) — never a SELECT-then-INSERT.
`artifact_id=0` sentinel for non-artifact sources; `idea_index` = 0 (uniqueness carried by dedup_key).
Plan basis validates zone/stop side-correctness (long-only Phase 1, reuse the `validate_ideas` checks);
`horizon_return` basis requires `entry_ref_price` + `plan_horizon` and forbids stop/targets. NO
conviction filter, ever. Rejects any kwargs carrying a numeric probability (defense mirrors
`_has_fabricated_probability` on thesis/bear_case text).

## 3. Path A registration + user_action — `trading/` (ADVERSARIAL REVIEW REQUIRED)

Three surgical touches, all **fail-soft** (registration failure logs + increments a lane_stats counter
and NEVER blocks the trading path — the ledger is downstream of trading):

- `proposal_intake.propose_trade` (after `create_phone_proposal` succeeds, ~line 190): `register_idea(
  origin=f"strategy:{candidate.rule_id or candidate.source}", signal_family=<strategy kind from
  candidate.source>, grading_basis="horizon_return", entry_ref_price=<sized ref price already computed
  by _size_candidate>, plan_horizon=<per-strategy template — config map STRATEGY_HORIZONS, §7>,
  user_action="skipped", source_ref={"kind":"proposal","id":proposal_id})`. Returned `idea_id` is
  stored into the proposal payload dict (new key `idea_outcome_id`) so lifecycle transitions can stamp.
- `proposal_intake.auto_execute_entry` (after successful submit): same, `user_action="auto"`.
- `trading/proposals.py`: `approve_proposal` success → `user_action='taken'`; `reject_proposal` and
  both expiry sites (~lines 155/172/318, verified) → remains `'skipped'` (already the default —
  transition writes only `updated_at`; explicit stamp still executed so the write path is exercised
  and testable). New helper `_stamp_idea_user_action(proposal_row, action)` — one place, fail-soft.

Non-negotiables for the reviewer: zero behavior change to sizing/validation/submit; every new call
wrapped so an idea-ledger exception cannot propagate into the chokepoint path; no new imports of
trading modules FROM db/idea_outcomes (dependency points ledger→nothing, trading→ledger).

## 4. Grading Law v1 + SPY benchmark — `research/idea_tracker.py`

Extend `sweep_open_ideas` (signature verified :440, fail-soft per row, tri-state fetch preserved):

1. **Benchmark leg** (spec §3): one SPY adjusted daily series fetched per sweep (cached in-sweep);
   on each resolution stamp `ret_pct` (entry-mid → resolution price, signed by direction),
   `spy_ret_pct` over `[first_evaluable_date, resolved_date]`, `alpha_pct = ret - spy`. SPY fetch
   failure → both NULL, own-series resolution proceeds (never blocks, never zero-fills).
2. **`exit_reason`** stamped from the resolving branch: `target|stop|time`.
3. **Tie-break codified**: same daily bar touches stop AND target → **stop wins** (pessimistic; the
   existing behavior is made explicit as `GRADING_TIEBREAK = "stop"` + test — never left implicit).
4. **`no_fill`**: a `pending` idea whose entry zone was never touched by expiry resolves
   `status='expired', fill_status='no_fill'` (today's `expired` already means this for plan ideas —
   the new column makes it queryable as a first-class rate). Zone-touched-then-expired rows get
   `fill_status='filled'`.
5. **`horizon_return` rows**: new branch — resolve at `_add_trading_days(first_evaluable, horizon)`;
   `ret_pct` from `entry_ref_price` to that close; won/lost by sign; `exit_reason='time'`.
6. Every sweep writes lane_stats counters: `graded`, `data_gap`, `errors`, `max_open_age_days`.

## 5. Silence made illegal — `db/lane_stats.py` (NEW) + `signals/pipeline_audit.py` (NEW)

**`db/lane_stats.py`**: one table `lane_stats(lane TEXT, date TEXT, counter TEXT, value REAL,
updated_at TEXT, PRIMARY KEY(lane,date,counter))` + `bump(lane, counter, n=1)` (UPSERT add) +
`day(lane, date)` / `series(lane, counter, days)` readers. Lanes in Phase 1: `setup_scan`,
`conviction_pass`, `convergence`, `idea_tracker`, `path_a_register`, `canary`. Writers call `bump`
fail-soft. **All alarm rules read these tables — never in-memory counters** (the founding incident).

**`signals/pipeline_audit.py`** — pure functions + one async job (`register_job(Job(name="pipeline_audit",
interval_s=900, market_hours_only=False))`):

- **Rate rules, never booleans-at-zero** (design §failure-modes): each rule compares today's counter
  to the trailing 10-trading-day median (calendar-aware via `market_calendar`). Phase 1 rules:
  `conviction parse_fail_rate > 0.5 for 2 consecutive days` (would have caught the dead lane in 48h);
  `setup_scan registered == 0 for 3 consecutive weekdays`; `idea_tracker graded+examined == 0 for 2
  market days`; `path_a_register failures > 0` (any — it's fail-soft, so failures are pure signal);
  `max_open_age_days > IDEA_TTL + RESOLVE_TTL + 3` (dead grader). Each fires
  `notify(kind="ops_alert", severity="warning", push=True)` once per (rule, day) — dedup via
  lane_stats counter `alarm_fired:<rule>`.
- **Canary**: once per trading day ~15:00 ET the job registers a synthetic idea
  (`origin="canary", synthetic=1`), waits for the tracker's next sweep to touch it, then sends a real
  push (`kind="ops_canary"`, to the founder's device) and asserts a `push_deliveries` receipt
  (surface verified: `record_delivery`/`has_sent_delivery`) within 30 min; missing receipt →
  `severity="critical"` notify (which bypasses inbox-only — verified `notify.py:74`). Canary rows are
  invisible to every summary: `get_summary`/`list_open`/API add `WHERE synthetic = 0` (test-pinned).
- **Conservation check v1** (full law arrives with the trigger bus in Phase 2): nightly scan run must
  satisfy `structure + conviction + vetoed + rejected_by_validation == candidates_assessed` — logged
  counters must balance; imbalance = a new uncounted drop path → warning.

**External dead-man — `tools/idea_engine_deadman.sh` + user cron (NEW)**: runs 16:45 ET (20:45/21:45
UTC — DST-safe by running both and gating on ET inside the script, the sampler-cron lesson); queries
the DB directly (sqlite3 CLI, read-only) for today's canary `push_deliveries` receipt; on missing →
sends via a channel that does NOT traverse hmt-backend. **[ADR-5]** Channel = `ntfy.sh` topic or
plain `mail(1)` — decided at build time by what serv already has; the requirement is only
"independent of the backend process and its push pipeline"; document the choice in the script header.

## 6. The honesty fix + the three scan bugs

- **`signals/push.py::_la_content_state`** (verified ~:615-640): forward the calibration verdict —
  when `score.sufficient is False or score.win_prob is None`, emit `"calibration": "unproven"` and NO
  `winProb`/`evPct` keys at all; when present, forward `win_prob` + `ci_low` + `n_samples` together
  (qualifier inseparable from the number). Mobile renders "unproven" for the flag. ~20 lines + tests
  (spec §9.8: sub-gate idea → no numeric winProb end-to-end).
- **Nightly banner** (`api/main.py:1738`): read `result.get("report", {}).get("ideas")`; count
  structure + conviction separately in the log line and the `scan_ready` notify; also `bump` lane_stats
  (`registered`, `vetoed`, `conviction_ok`).
- **Conviction pass** (`ai/research/setup_scan.py:694-780`): raise `max_tokens` 2000→6000; log
  `finish_reason` + raw-tail (200 chars) on every parse failure; one retry on empty-content/truncation
  (the documented DeepSeek idiom); `bump("conviction_pass", "parse_fail"|"ok")`. Root-cause telemetry
  first, tuning second — if the failure survives the token raise, the logged tail tells Phase 3 what
  to fix structurally.
- **Duplicate-run guard** (`api/main.py:1709-1718`): replace the `list_research_artifacts(limit=20)`
  scan with an exact-match existence query (`db/ai_tables.py`: new `artifact_exists_by_topic(topic)
  -> bool`, indexed lookup). Kills the Jul-14 triple-run class regardless of artifact volume.

## 7. Config — `config/idea_engine.py` (NEW, small)

`IDEA_COOLDOWN_MIN: dict[str, int]` (default 30), `STRATEGY_HORIZONS: dict[str, str]`
(`earnings_drift: "exit_lead"` → resolved from strategy params at call time; `momentum_sp500: "21d"`),
`GRADING_TIEBREAK = "stop"`, alarm thresholds, canary times. One `validate_config()` invariant test
imported by the test suite (design's config-rot defense). No env vars for constants that are law
(tie-break, MIN_N later) — env only for deployment toggles.

## 8. Tests (all new/changed behavior pinned; targeted modules, never the full suite)

`tests/test_idea_engine_phase1.py` (new) + extensions to `tests/test_idea_tracker.py`:

1. register_idea: dedup (repeat within bucket → 1 row), open-row wall via partial index under
   concurrent insert, intraday-horizon rejection [ADR-3], probability-text rejection, horizon_return
   requires ref+horizon and forbids stop/targets.
2. Grading: same-bar stop+target → lost (tie-break law); `no_fill` sets fill_status + excluded from
   win/loss; SPY leg — alpha from fixture series, both-legs-one-series (split-safety), NULL on missing
   SPY; horizon_return resolution at exact trading-day offset; data_gap rows never lower win rate.
3. Path A: propose_trade registers fail-soft (ledger exception injected → proposal still created +
   `path_a_register.failures` bumped); approve stamps `taken`; reject/expire leave `skipped`; auto
   stamps `auto`; proposal payload carries `idea_outcome_id`.
4. Silence: synthetic 2-day parse-fail streak → exactly one ops_alert; canary row invisible to
   `get_summary`/`/api/ideas/outcomes` (`synthetic=0` pin); missing canary receipt → critical notify;
   `notify(severity="critical")` bypasses every inbox-only/budget path (design's Phase-1 test).
5. Honesty: `_la_content_state` with `win_prob=None`/`sufficient=False` → no winProb key +
   `calibration="unproven"`; with a valid score → win_prob+ci+n travel together.
6. Scan bugs: banner counts `report.ideas`; `artifact_exists_by_topic` exact-match; conviction retry
   path (mocked truncation → one retry → counted).

Run: `pytest tests/test_idea_engine_phase1.py tests/test_idea_tracker.py tests/test_options_flow_poll.py -q`
(+ the trading-touch modules' existing tests: `tests/test_proposals.py` if present — enumerate at
build time), pytest's own exit code + `N passed` line required. `ruff check .` +
`cd frontend && npm run typecheck` (mobile render of `calibration` flag).

## 9. Build + review + deploy process

1. Brief a coder per CLAUDE.md rule 1 (this spec IS the brief's core; stakes: `trading/` touch =
   frontier coder or orchestrator hand-edit, everything else routable).
2. **Two Opus reviewers, split mandates** (house rule for trading/ + this is the ledger's foundation):
   static adversary on the `trading/`+`db/` diff; dynamic prover runs the Phase-1 verify drill —
   deliberately break the conviction parser in a test env and demonstrate the alarm fires within one
   audit cycle (the founding incident, reproduced then caught).
3. Board: extend `idea-ledger-flywheel` card in the work commit; BUILD.md history row.
4. Deploy via `tools/serv-deploy.sh` (backend only — collectors untouched by Phase 1); off-hours not
   required but preferred. Post-deploy verify (same evening): nightly scan banner shows real counts;
   `lane_stats` rows appearing; next morning: canary push received + receipt row present; dead-man cron
   dry-run (`--check-only`) green.
5. Acceptance = design doc Phase-1 verify gate, restated: a rejected proposal lands
   `user_action='skipped'` and still grades; a broken parser pages within one cycle; a sub-gate idea
   renders "unproven" on the phone with no number.

*Phase 2 spec is written only after this gate passes — informed by what the lane meters actually show
in the first live week.*

## Addendum — founder direction (during build)

**[ADR-6] Direction symmetry supersedes "long-only Phase 1" at the LEDGER layer.** `register_idea`
plan-basis validation must accept `direction="short"` with inverted geometry (stop above zone, targets
below) + tests, applied at wave-1 integration. Grading Law resolution is direction-aware (§4 `ret_pct`
already signed). What stays long-only for now: `ai/research/ideas.validate_ideas` (untouched until
Phase 3) and the execution chokepoint (founder-gated, unchanged). Path A registration remains
buy-entries-only because Path A strategies cannot short at execution — revisit when that unlocks.

**[ADR-7] Multi-surface delivery is a requirement, not a nicety.** All Phase 4/5 UI work renders the
one composed payload on BOTH desktop workspace and phone; acceptance for any idea surface includes the
desktop rendering.
