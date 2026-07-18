# Rank-1 Nightly Research Worker — design (2026-07-18)

*Status: DESIGN — awaiting founder approval; no implementation yet. Inputs: the composition audit
(`BACKEND_COMPOSITION_AUDIT_2026-07-18.md` Rank-1 section), an Opus recon sweep of the five jobs +
collector seam with every load-bearing claim re-verified against code first-hand, and the loop-lag
probe (verdict Mon 07-20 night — informs emphasis, not direction; see §7). Adversarial review: DONE
(Opus, 2026-07-18) — 2 HIGH (liveness illusion; double-run windows), 3 MED, 3 LOW, all folded in
below; the review also cleared the scary dependency questions (no `api.main` import pull-in, worker
write paths are schema-self-sufficient via lazy `CREATE TABLE IF NOT EXISTS`, no lifespan-global
dependencies in any of the five job bodies, §7's WAL honesty accurate per `db/base.py:15-24`).*

## 1. Goal and non-goals

**Goal:** move the nightly/bulk research jobs out of the hmt-backend process into a standalone
`hmt-research-worker` systemd unit, generalizing the proven collector seam — so heavy research work
can never starve the loop that runs exits, fills, and phone approvals, and so research-code deploys
stop sharing a restart blast radius with the trading path.

**Non-goals:** no new transport (the SQLite/WAL spine is the seam, per the evidence-bus ADR); no
change to any job's *semantics* (same gates, intervals, dedup, outputs); no Rank-2 (exit-engine
cadence) or Rank-3 (perception cron) work; no fix for WAL single-writer contention (§7 — honesty).

## 2. What moves, what stays

**Moves (5 jobs):**

| Job | Today | Writes | Cross-process safety |
|---|---|---|---|
| `setup_scan_nightly` | supervised fn `api/main.py:1746`, 900s, gates: weekday<5, ≥17:30 ET, artifact-topic dedup | research/artifact tables + `idea_outcomes` + `lane_stats` | dedup DB-backed (`artifact_exists_by_topic`) — safe |
| `options_flow` (EOD + fill_outcomes) | supervised fn `api/main.py:1175`, 1800s, session gate post/closed/overnight, once/ET-day | `options_flow_events`/`_daily` (to_thread) | dedup **in-memory only** (`main.py:1159`) — must be persisted (§4c) |
| `volume_profile_refresh` | dedicated loop `api/main.py:746`, 24h, per-symbol file-mtime freshness (20h) | JSON files `data/volume_profiles/` | freshness is filesystem — safe |
| `momentum_bars_refresh` | dedicated loop `api/main.py:676`, 24h, gate: any `momentum*` strategy enabled | `momentum_daily_bars` — **bare sync upsert on the loop** (`signals/momentum.py:131`) | idempotent upsert — safe; sync-write fixed in passing (§4d) |
| `reflection` | supervised fn `ai/reflection/scheduler.py:20`, 24h, no gate/dedup | `reflection_runs` (to_thread) | already an importable module — safe |

**Stays in hmt-backend (explicit):** `pipeline_audit` (the pager must live in the surviving
process), `db_retention` (01–05 ET window, not implicated in observed stalls; revisit after),
`fusion_atr_refresh` (RTH cadence, off-loop since step 1), `reports_scheduler` (morning rhythm +
canary coupling), filings/social/earnings/regime/schwab jobs (Rank-3 tier), all market-hours scans,
and everything behind the fault line. The audit's do-not-split table stands.

## 3. Architecture — generalize the collector seam, invent nothing

```
ALPACA / data REST ──► hmt-research-worker ──► data/trading.db (WAL) ◄── hmt-backend
                       (tools/research_worker.py,      ▲                    reads results;
                        own systemd unit,              └── profile JSONs    no longer runs
                        Restart=always)                                     the night shift
```

- **Ownership flag + lease guard:** `RESEARCH_WORKER_EXTERNAL=1` in the shared `.env`. Worker
  **refuses to start without it** (clone `flow_collector.py:72-77`); backend **skips registering**
  the five jobs when it's set. But the flag alone is weaker than the collector's interlock — the
  collector has a physical mutex (Alpaca 406s a second OPRA socket); SQLite happily accepts two
  writers, and the backend reads the flag **once at import**, so a stale-flag backend restart would
  silently re-own all five jobs while the worker runs (review H2). So the backend side is
  double-guarded: each backend-registered research job's `enabled()` also requires **no fresh worker
  lease** — the worker touches `data/research_worker.lease` (mtime heartbeat each supervisor pass;
  same existence-authoritative style as the rung-3 kill-switch sentinel), and the backend treats a
  lease fresher than ~15 min as "a worker owns the night shift", logging a warning if its flag
  disagrees. Worker-dead → lease goes stale → but the backend does NOT auto-take-over (its flag
  still says external); ownership changes only by explicit flag + restart. Deny-double-run by
  default, in both misconfig directions.
- **Worker shell** (`tools/research_worker.py`, ~100 lines, clone of the collector shell):
  `sys.path` insert → `config.settings` import (loads `.env`) → interlock check → register the five
  jobs via the shared module (§4a) → `api.jobs.register_watchdog_job()` → arm `api/loop_probe.py`
  (reuse, free observability) → `start_all()` → SIGTERM/SIGINT handlers →
  `asyncio.wait(FIRST_COMPLETED)` die-together semantics for systemd `Restart=always`.
- **`api/jobs.py` is reused, not cloned** — verified importable without FastAPI (only imports
  `trading.market_hours` + lazy `signals.notify`). The worker gets the same supervisor, backoff,
  gate handling, and watchdog the backend uses. Registries are per-process by construction.

## 4. The four real changes

**a) Job bodies move to a neutral module — `api/research_jobs.py`.** The five wrapper fns + their
private helpers/constants leave `api/main.py` (which must never be imported by the worker — it
boots the whole app). Shared helpers used by *both* worlds relocate once, no copies:
`_all_watchlist_symbols`/`_liquid_core_symbols`/`_profile_universe` → a small `signals/universe.py`
(with a `reload_settings()`-first variant for out-of-process callers, mirroring
`flow_collector.watchlist_symbols` — the API owns settings writes; the worker re-reads per pass).
`_market_session` needs **no relocation** (review M1 corrected the draft): it is already an import
alias — `from brokers.market_clock import market_session` (`api/main.py:67`) — so
`api/research_jobs.py` imports it from `brokers.market_clock` directly; no `trading/` touch, no
second definition. `api/main.py` imports back from the new modules; `register_research_jobs()` is
called by the backend (flag off) or the worker (flag on) — **one source of gating truth, drift
impossible.** Lazy heavy imports (`run_setup_scan`, `run_eod_scan`) stay lazy so the idle worker
stays small.

**b) Pattern unification + the first-pass trap.** momentum and volume_profile lose their hand-rolled
`while True` loops and become ordinary supervised jobs — `run_job` already provides exactly their
loop (sleep → gate → run → mark_alive → backoff). This is a **three-site** change (review M2), and
all three land inside `register_research_jobs()` / the flag gate so they can't half-apply: the two
`heartbeat_only` registry entries (`api/main.py:1997-2006`) are removed, the two `create_task` lines
(`api/main.py:2225-2226`) are removed, and the jobs are registered as regular `Job`s in exactly one
place — with a parity test asserting **one Job per name** in both flag states. AND `run_job` sleeps
*before* the first pass (`api/jobs.py:81`), so a 24h job on a box that restarts daily would never
run. Fix: add `run_on_start: bool = False` to `Job`; when set, `run_job` executes the fn once before
entering the sleep loop. Worker sets it for volume_profile (per-symbol 20h freshness gate makes a
boot pass nearly free) and momentum — but momentum's boot pass first checks its own persisted
freshness (`momentum_daily_bars` max bar-date already current → skip; review L2 — otherwise every
deploy re-fetches the full S&P 500 batch, ironic for a decoupling design). NOT for reflection (an
LLM run per restart is real money). Reflection instead gains a `lane_stats` **per-ET-day dedup**
(review H2 — it currently has none at all, so any ownership overlap double-charges the LLM daily);
at most one reflection per ET day, still interval-driven. Backend behavior unchanged (field defaults
False).

**c) Flow-EOD dedup becomes restart-safe.** Replace the in-memory `_options_flow_ran_dates` set with
the persisted per-ET-day counter store that already exists: bump `lane_stats("options_flow_eod",
"ran", 1)` on completion; the "already ran today?" check reads that day-counter (in-memory set kept
as fast path). Both the bump and the read key on `datetime.now(_ET).date().isoformat()` — never a
UTC date (review L3; `lane_stats` defaults to `et_today()` but the caller must not leak a UTC date;
the EOD window crosses 00:00 UTC nightly). The pre-existing overnight edge (session gate admits
`overnight`, so after ET-midnight the scan can re-fire with a not-yet-traded `event_date`) is
preserved as-is, out of scope. The new lane feeds a **v1** `pipeline_audit` silent-lane rule for
flow-EOD (promoted from follow-up by review H1 — see §5).

**d) Momentum sync-write fix.** Wrap `upsert_daily_bars(rows, ...)` (`signals/momentum.py:131`) in
`asyncio.to_thread` — the last bare sync SQLite write among the five. Benefits both processes;
2-line diff.

## 5. Liveness — stated honestly (review H1 reshaped this section)

The draft's "worker watchdog covers it" story was an illusion for these jobs: all five gate
**inside** their fn bodies, so an early `return` still hits `mark_alive` (`api/jobs.py:97-98`) — a
worker that never actually scans looks "alive" all day to its own watchdog. Worse, flow-EOD
swallows all its exceptions internally (`api/main.py:1178-1230`), so even the failing-every-pass
signal never reaches the supervisor for it. What the watchdog genuinely covers: jobs whose fn
*raises* (setup_scan, momentum, reflection propagate). Coverage as designed, per layer:

1. **Worker watchdog** (same `api.jobs` watchdog, pages via `signals.notify` — cross-process safe):
   catches exception-failing jobs and a wedged supervisor. Does NOT catch silent no-op passes; the
   shared `_watchdog_once` backup-staleness check is **backend-only** (parameterized out in the
   worker — review L1 — the worker doesn't own backups and would double-page).
2. **Silent-lane rules in `pipeline_audit` (backend-side, DB-based)** — the real coverage for
   "process up, lane dead": `rule_setup_scan_silent` already works unchanged (keys on `lane_stats`
   `ran`, 3-trading-day floor), and **v1 adds the flow-EOD twin** on the new
   `options_flow_eod`/`ran` lane. Momentum/volume-profile rules stay deferred (momentum is gated on
   strategy enablement — a naive rule would false-page while `momentum*` strategies are disabled,
   which is their default state).
3. **Worker dead-man receipts — pulled INTO v1** (was deferred; H1's worker-dead scenario would
   otherwise page nothing for ≥3 days): one script in the `idea_engine_deadman.sh` mold + one cron
   line, checking morning-after receipts straight off the spine — yesterday's (trading-day)
   `setup_scan`/`ran` and `options_flow_eod`/`ran` day-counters, and newest volume-profile mtime
   < 26h — escalating via ntfy, fully outside both processes' failure domains.
4. **Process supervision:** systemd `Restart=always` + `StartLimitIntervalSec=0` in the unit
   template (defaults can land a fast crash-loop in `failed` permanently), deploy script verifies
   PID change.

Plus: backend `/health` gains a small read-only `research_worker` block derived from the spine
(latest `setup_scan` lane day-counter, latest `options_flow_daily` event_date, profile-dir newest
mtime, `momentum_daily_bars` max date, latest `reflection_runs` row) — cockpit visibility without
any new heartbeat mechanism.

## 6. Ops

- **Unit:** `hmt-research-worker.service` — copy-paste template added to `SERVER_SETUP.md` next to
  the collectors (`WorkingDirectory` repo root, `ExecStart=.venv/bin/python
  tools/research_worker.py`, `Restart=always`, `RestartSec=3`, `StartLimitIntervalSec=0`).
- **Deploy:** add `hmt-research-worker` to `restart_collector_if_present` in `tools/serv-deploy.sh`
  — workers must run current code (the Friday-night pre-fix-PID lesson). Ordering is already
  correct: backend (migrations/seeds) restarts first; worker assumes schema exists, like the
  collectors. A mid-scan restart is safe: setup-scan's artifact marker writes only on completion, so
  the scan re-runs (duplicate LLM cost, correct outcome); everything else is idempotent.
- **Schwab tokens (R1b deploy-checklist item, not a repo claim):** the reauth cron lives on serv's
  crontab, not in the repo (review M3 — unverifiable here). At R1b time: (1) check whether
  `get_market_data_provider()` actually resolves to Schwab for profile builds — if Alpaca, this
  whole coupling is moot; (2) if Schwab, read the real cron entry on serv and add the worker restart
  to its on-mint branch — otherwise the worker holds a stale in-memory token and the nightly profile
  build dies silently (and per §5, silently is exactly how it would die). Never burst-request
  Schwab: the existing 0.6s/symbol pacing moves with the job unchanged.
- **RAM:** idle worker ≈ one Python + lazy-loaded deps; heavy modules load on first nightly pass.

## 7. Honesty — what this split fixes, and what it does not

**Fixes:** F1 for the trading path (no research work shares the trading loop — the 07-13 daytime
class stays dead, and the surviving *nightly* window moves wholesale out of the process that runs
exits/approvals); F2 (research deploys/crashes no longer restart trading; backend deploys no longer
kill mid-scan research); watchdog blindness during research stalls (each process watches itself,
and the loop probe rides in both).

**Does NOT fix:** WAL single-writer contention (audit F5). The writers still serialize on one file
— from another process, a long research write transaction can still delay the backend's own writes
(`busy_timeout=5000` absorbs, then errors). If Monday's probe convicts long write transactions
(executor-lag warnings / DB-lock stack dumps), the cure is **transaction hygiene inside the job
write helpers** (short txns, batched inserts) — that work lands in the same modules this design
touches, so the verdict shapes emphasis, not architecture. Either Monday outcome, this split
proceeds on its own merits (blast radius + loop isolation).

## 8. Rollout, rollback, verify

- **Phase R1a — mechanical extraction (backend-only, deployable any time):** create
  `api/research_jobs.py` + `signals/universe.py`; relocate `_market_session`; momentum to_thread
  fix; flow-EOD lane_stats dedup; `Job.run_on_start` + tests. Backend still owns all five jobs.
  Zero behavior change (tests assert registration parity before/after).
- **Phase R1b — the split (after founder approves + Monday verdict is read):** worker shell + lease
  guard + unit install + `.env` flag flip + deploy-script line + dead-man receipts + `/health` block
  + interlock/parity tests + the Schwab-cron checklist item. Deploy sequence **disowns before it
  owns** (review H2 — the draft's order opened a both-own window): flip flag → `serv-deploy.sh
  backend` (backend restarts, drops the five jobs) → verify backend PID + journal shows them
  skipped → install/start worker unit → worker journal shows five jobs registered + `loop_probe
  armed` + lease file appearing.
- **Rollback:** stop unit + flag 0 + restart backend — backend re-owns the five (registration code
  is gated, not deleted). Same lever as the collectors.
- **First-night verify (the acceptance test):** worker journal shows setup scan ≥17:30 ET +
  flow EOD post-close with sane counts; backend journal has **zero** EOD/setup lines and
  `loop_probe:` stays silent through the nightly window; `lane_stats` day-counters bumped;
  `/health` research block fresh; lane alarms quiet; next-morning artifacts present (profiles
  fresh, momentum bars current, reflection row when due).

## 9. Deferred / follow-ups (recorded, not scoped)

`db_retention` move (candidate once worker is proven); momentum/volume-profile silent-lane rules
(must be enablement-aware — momentum strategies are disabled by default, a naive rule false-pages);
reflection cadence quirk (§4b — interval-only scheduling means it runs far less than daily on a
frequently-deployed box); the pre-existing flow-EOD overnight/event-date edge (§4c); Rank-3
perception cron worker; Rank-2 exit-engine cadence (own round, max depth + adversarial review,
resolve the audit-vs-rung-3 doctrine conflict there).
