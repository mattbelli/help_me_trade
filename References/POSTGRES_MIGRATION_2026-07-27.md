# Postgres Migration — governing design (2026-07-27)

*Status: DESIGN — founder decided 2026-07-27 to migrate; this doc is the plan of record.
Built from three parallel research passes (store inventory, dialect audit, runtime/ops), then
hardened by a two-reviewer adversarial round (technical mandate: every §5 claim checked against the
code and the live DB; safety/ops mandate: cutover/rollback/CI failure modes). All confirmed
findings are folded in below. Companion evidence: BUILD_LOG 2026-07-27 rows ("write-lock convoy
hunted to extinction") and the `postgres-migration-decision` board card.*

## 0. The decision and why

**We are migrating `trading.db` to PostgreSQL, deliberately and in phases — not as a firefight.**
The 07-27 write-lock convoy was root-caused and fixed on SQLite (init-per-call write transactions +
an unindexed 591k-row scan holding the lock; verified clean during RTH after PRs #115–#121), so we
migrate **from a healthy system on our own schedule**.

Why migrate anyway — the arguments that carried:

1. **Migration cost only grows.** Schema count grows, data grows, and the moment there are real
   users a botched cutover has consequences it doesn't have today. Waiting has a compounding price;
   a trigger-based "wait and see" treats the cost as constant, and it isn't.
2. **The convoy *form* is structural to SQLite.** One file = one write lock, and three OS processes
   (`hmt-backend`, `hmt-flow-collector`, `hmt-equity-collector`) plus 8 cron jobs all write the same
   `trading.db`. Today's specific lock holders are dead, but any future slow writer starves every
   other subsystem — on 2026-07-27 the flow collector **dropped sweep events 121 times in 30 min**
   at market open behind another process's lock. Postgres (MVCC, row-level locks) shrinks the blast
   radius to the guilty rows.
3. **Multi-user / shared-plane direction wants Postgres anyway.** Doing it pre-users is the
   cheapest it will ever be.
4. Independent zero-context review reached the same conclusion — fresh-eyes convergence is signal.

What Postgres does **not** fix, so nobody expects it to: missing indexes (slow there too, just
localized), sync DB calls on the async event loop (**worse** over a socket than over a local file),
and non-DB bugs like the settings.json stale-cache clobber. The off-loop discipline built in the
convoy round carries over unchanged.

**The success criterion, fixed now and made falsifiable:** at the first Monday open after cutover —
dropped sweep cycles ≤ a handful **with cause attributed** (`db_error` vs `socket_reconnect` vs
`upstream_lag` — the collectors must tag drops before the verdict, or a PG wobble is
indistinguishable from an OPRA reconnect storm), p99 loop-probe latency no worse than the SQLite
baseline, zero units in `failed`, zero fill-attribution orphans. Baseline to beat: 121 dropped
sweep cycles/30 min on 2026-07-27. "Zero `database is locked`" is *not* the criterion — that error
class ceases to exist by construction, so it proves nothing. Plus: full suite green on Postgres;
cutover reconciliation exact (§9); memory-retrieval hit@4 re-measured ≥ its SQLite baseline (§7).
**Go/no-go is the founder's call, held before Monday's open and again at Monday's close** (§10 for
why the deadline structure matters).

## 1. Goals and non-goals

**Goals**
- `trading.db` (67 live tables, ~1.8 GB, ~9.03M rows, 3 daemon writers + cron writers) moves to one
  Postgres 18 cluster on serv, with pooling, backups, and the same operational discipline the
  SQLite stack has today (nightly verified backups pulled off-box).
- Money columns become `NUMERIC` (today: TEXT + Python `Decimal`) — a correctness upgrade, with the
  Decimal seam in Python preserved **and the JSON wire format preserved** (§5 row 5: money
  serializes as strings at the API edge, exactly as today — never through float).
- The trading chokepoint invariants survive byte-for-byte: `execution_service.submit()` remains the
  only broker path, `RiskEngine.validate()` gates every order, deny-by-default, kill-switch-first.
- Zero data loss on cutover, verified by reconciliation (§9), with a frozen SQLite fallback.

**Non-goals (v1) — each is a deliberate scope cut, not an oversight**
- **The research/corpus stores stay on SQLite indefinitely**: `flow_corpus.db` (9.1 GB / 48.9M-row
  rollup), `oi_walls.db` (3.7 GB), `options_iv_history.db`, `options_chain_history.db`, and all
  `data/research/*.db`. They are single-writer batch stores driven by hand-run tools — no
  contention, and ~90% of the data volume for 0% of the benefit. Re-open only if a store gains a
  live multi-process writer.
- **Timestamps stay ISO-8601 TEXT in v1.** Every value comes from one function
  (`db/base.now_iso()`, always UTC with explicit offset, uniform format), so lexicographic order is
  correct, and the database is created with collation `C` — verified byte-identical to SQLite's
  BINARY collation on this data. Converting to `timestamptz` changes the Python-side type
  (str → datetime) at hundreds of read sites and is a per-table follow-on (§11), not a cutover
  risk. The copy-time validator asserts every timestamp value carries an explicit offset (§9), so
  the two SQL sites that cast (`::timestamptz`) can never be shifted by the server `TimeZone` GUC —
  and those casts are written `AT TIME ZONE 'UTC'` regardless (§5 row 14).
- **Booleans stay 0/1 integers.** Zero code change; the codebase is consistently 0/1.
- **JSON columns stay TEXT in v1.** The 11 SQL-side `json_extract` sites are rewritten with
  `::jsonb` casts (cast verified IMMUTABLE, so the expression index is legal; live data verified:
  0 invalid JSON across `source_ref`/`payload_json`/`raw_json`); Python-side `json.loads(row[...])`
  keeps working untouched. `jsonb` column conversion is a per-column follow-on (§11) — psycopg
  returns parsed objects for `jsonb`, which would silently break every `json.loads` site if done
  wholesale.
- **No ORM.** Plain SQL today, plain SQL after (psycopg3). No SQLAlchemy.
- **No async-driver rewrite.** Sync psycopg3 under the existing threadpool/`to_thread` discipline
  (125 non-test `to_thread` sites; ~136 of 171 routes are already sync-on-threadpool). asyncpg is
  rejected: a second driver and a third placeholder dialect for microseconds of benefit against
  1–3 ms local queries. psycopg3 keeps a per-function async upgrade path open.
- **No TimescaleDB in v1** (§11 — and note the churn problem it would solve is solved cheaper with
  native partitioning if needed). The raw tick corpus stays OUT of the database (DBN files +
  rollups on disk) — deliberate, unchanged.
- **No dual-write, ever** (§10).

## 2. Safety invariants (non-negotiable, restated for this project)

- **Paper before live** at every phase; the LIVE hard-block is untouched by this project.
- Any change touching `trading/`, the ledger, or `db/order_executions` ships only after **TWO
  parallel Opus adversarial reviewers with split mandates**. The dialect port of the money modules,
  the money **serialization boundary** (§5 row 5), and the cutover tooling all qualify.
- **Reconciliation before reads flip** (§9) — exact, by the *computable* procedure specified there
  (the naive "SQL `SUM()` on both sides" is provably wrong on TEXT money: SQLite coerces TEXT to
  C doubles, measured `51165.20999999994` vs the true `51165.21`, and coerces malformed values to
  `0.0` — blind to exactly the corruption that matters). Net-cash realized P&L identical to the
  cent. "Close enough" is a failed cutover.
- Secrets: the DSN and any password live outside the repo. `chmod 600 .env` (it is 0775 today —
  fix in Round 0); passwords via `~/.pgpass` (0600), never inside the DSN string and **never on a
  command line** (`ps` is world-readable and this box runs other people's services). The cluster
  listens on localhost + unix socket only — never the tailnet interface. `data/` remains
  gitignored; backups stay on serv + the existing mattpc pull. **Postgres logs will contain
  statement text with account ids and money values** — `/var/log/postgresql` is user financial
  data and gets the same treatment: mode-restricted, size-capped rotation, never committed,
  disk-free alarmed (§4).
- **Worktree/DSN hazard** (new class — today the path-based `TRADING_DB_PATH` gave accidental
  isolation between trees; a DSN is location-independent): the app refuses a production DSN unless
  `HMT_ENV=prod` is set and the process runs from the deployed tree; the worktree `.env` template
  carries broker keys but never `TRADING_DB_URL`.
- The idempotency walls are load-bearing and must port intact: `trade_events UNIQUE(account_id,
  external_key)` (ledger dedup), `decision_journal.proposal_id UNIQUE`, and the partial unique
  indexes (`idx_fusion_episode_open`, `idx_idea_dedup_open`, `idx_notifications_fusion_group`,
  `uq_fill_attribution` family).

## 3. Scope: what migrates, what stays

| Store | Size / rows | Live writers | Disposition |
|---|---|---|---|
| `data/trading.db` | 1.8 GB / 9.03M rows / 67 tables | 3 daemons + cron | **MIGRATES** |
| `data/flow_corpus.db` | 9.1 GB / 51.8M | batch tools only | stays SQLite |
| `data/oi_walls.db` | 3.7 GB / 27.2M | batch tool only | stays SQLite |
| `data/options_iv_history.db` | 344 MB / 1.88M | frozen (bridge retired) | stays SQLite; **COPY-tool rehearsal target** |
| `data/options_chain_history.db` | ~empty | nightly cron | stays SQLite |
| `data/research/*.db` (6 files) | ~260 MB | offline CLI | stays SQLite |
| Legacy strays (`app.db`, `hmt.db`, `trading.db.bak`, pre-repair snapshots…) | — | none referenced by code | excluded; not migrated |

Within `trading.db`, everything copies (including written-never-read audit tables — cheap, keeps
every writer working), with these flagged exceptions:

- **`daily_bars` inside trading.db is a schema leak** — `trading/proposals.py:704` routes
  `score_symbol` at the trading DB path, so `research/bars.py`'s cache table (50k rows, complete
  with its `INSERT OR REPLACE` writer) lives in the wrong file. **Fix the leak before Rehearsal B**
  (own card), and add the verify step: assert `daily_bars` is absent from the trading store before
  the copy runs. §5 row 6's "REPLACE sites mostly stay SQLite" claim *depends on this ordering*.
- **`rule_events` has 10,831 measured orphan rows** against its declared `rules(id)` FK — the copy
  hard-fails on FK enforcement unless decided first. Decide (drop the FK, or repair/delete orphans)
  **before Rehearsal B**, not at reconciliation. (§5 row 17.)
- Orphan tables (`social_mentions` — writer deleted; `fusion_investigation_baseline` — no writer)
  copy as-is; dropping them is later board-discipline cleanup, not a migration decision.
- FTS5 shadow tables (`memories_fts_*`) do **not** copy — the FTS index is rebuilt natively (§7).

**A free win worth recording:** `earnings_alerts_sent` — the cross-process dedup/push-budget
interlock deliberately built on the shared WAL file (written by all three daemons) — needs no
redesign. All three processes become clients of one server; the interlock gets *more* correct.
Likewise the cron jobs stop being transient WAL writers and become ordinary clients.

**The seam has bypasses — they get dispositions, not hope.** "All production sites funnel through
`db/base.connect()`" is true of `db/` and **false of `tools/`**. Raw `sqlite3.connect` consumers of
`trading.db`, each with its required treatment, all landing **before** cutover (the §12 grep gate
moves pre-cutover accordingly):

| Tool | What it does | Disposition |
|---|---|---|
| `tools/db_maintenance.py:46` | VACUUM/checkpoint/integrity on trading.db | port to PG (`VACUUM`/`pg_stat` equivalents); it becomes the PG maintenance tool §12 needs anyway |
| `tools/repair_event_quantities.py:54,146` | **money-path writer** (repairs trade_events) | port via `db/base.connect()`; two-Opus review with the money round |
| `tools/fire_test_entry.py:94` | live auto-entry harness reads managed_positions | port (it must work day 1 — it's the smoke tool) |
| `tools/flow_outcomes.py:96`, `tools/fusion_z_baseline.py:219` (reads raw, **writes via `_connect`** — would straddle two stores), `tools/fusion_rescore_replay.py:118`, `tools/memory_migrate_p0.py:55`, `research/vol_bakeoff.py:57` | readers/one-shots | port or explicitly retire; none may survive pointing at the file |
| `deploy/backup-db.sh:19`, `tools/idea_engine_deadman.sh:82` | shell `sqlite3` CLI | §4 backups / §4 dead-man — both have designed ports |

## 4. Target architecture

**Hosting: native apt PostgreSQL 18 (`18+290ubuntu1` on Ubuntu 26.04).** Not Docker. The deciding
argument: this becomes the datastore for an app that trades real money and must come back clean
from an unattended reboot — fewest layers, and `postgresql.service` is a system unit in the same
class as the `hmt-*` units, so the ordering edge is real. dockerd as a hard dependency of the
trading datastore is a new failure surface. ⚠️ **Host port 5432 is already taken** (`nhl-gm-db`
container) — unix socket is the primary path for the *daemons*, TCP on **127.0.0.1:5433** for
role-separated clients (below). `listen_addresses = 'localhost'`. (Noted for the memory budget:
this is the box's *third* Postgres — two small ones already run in containers for other projects —
and the SQLite corpora still want page cache.)

**Auth and roles — access control, not just quotas.** The CI runner, cron, agents, and all three
daemons run as the same OS user (`mbelli`), so unix-socket **peer auth cannot separate anything**.
Therefore:

- Roles: **`hmt_app`** (daemons + cron writers; password in `~/.pgpass`), **`hmt_ro`**
  (dead-man + read-only tooling; `SELECT` only), **`hmt_test`** (CI; credentials scoped to the
  runner env, never in `.env`; `CONNECTION LIMIT 10` as a quota *on top of* access control).
- **`REVOKE CONNECT ON DATABASE hmt FROM PUBLIC;`** — explicitly; PUBLIC has CONNECT by default
  and this revoke is the actual wall between CI and production.
- The dead-man (`tools/idea_engine_deadman.sh`) ports to `psql -X -A -t` as `hmt_ro` via
  `~/.pgpass`, preserving its "no app modules, no HTTP to the app" constraint. Left unported it
  false-pages at 16:45 ET **every weekday** after cutover until the founder mutes the topic — which
  destroys the one alarm that survives a wedged backend. Ported in the cutover round, verified with
  `--check-only` as a deploy step. (Bash scripts have no dotenv — and note `load_dotenv` in Python
  resolves `.env` from the *module path*, not cwd, so "cron cds into the repo" is not what makes
  env work; `.pgpass` is the credential path for shell tooling.)

**Memory: measure first, throttle don't kill.** Measured at review time: 15 GiB RAM, ~6.9 GiB
available, **swap 98% full (3.9/4.0 GiB)** — the box has already spilled and has nowhere left to
spill. Top consumers are the app itself (`uvicorn` 1.9 GB RSS, collectors 0.8+0.4 GB) and
concurrent agent sessions (~2 GB), *not* the 17 containers (~1.1 GB combined). Round 0 therefore:
record `free`/`smem` before and 48h after install; profile the 1.9 GB backend as its own follow-up;
no heavy agent fan-outs during cutover week. Tuning:

```
port = 5433                      listen_addresses = 'localhost'
shared_buffers = 1GB             effective_cache_size = 4GB
work_mem = 32MB                  maintenance_work_mem = 256MB
autovacuum_work_mem = 64MB       autovacuum_max_workers = 2
max_connections = 100            huge_pages = off
log_min_duration_statement = 1000
log_lock_waits = on
log_rotation_size = 64MB         # + logrotate retention cap; disk-free alarm
```

Unit drop-in: **`MemoryHigh` (reclaim-throttle), not `MemoryMax`** — a cgroup OOM kill on any
backend sends the whole cluster into crash recovery, killing every in-flight transaction for all
three daemons; "one greedy query" must degrade, not detonate. Plus `OOMScoreAdjust=-900` on the
postmaster and `MemorySwapMax` to keep PG out of the exhausted swap.

**Units and deploy — named changes, not assumptions:**
- All three daemons get `After=postgresql.service` (+`Wants=`), and **`StartLimitBurst=5` /
  `StartLimitIntervalSec=60`**. Today they are `Restart=always`/`RestartSec=3` with no start limit:
  if PG is unreachable (bad password, cluster down), they'd crash-loop invisibly forever —
  `is-active` says `activating`, nothing pages. With the limit, a PG-down condition ends in
  `failed`, which is alertable; add that alert.
- The two collector units exist only in `/etc/systemd/system/` — **commit them to
  `deploy/systemd/`** so the migration's unit edits are tracked.
- `tools/serv-deploy.sh` gains a **schema-apply step (`tools/pg_schema.py --apply`) that runs
  before restarts and aborts the deploy on failure** — today the script has no build/migrate step
  and cannot even stop a unit (it kills MainPIDs, which systemd respawns in 3 s; see §9).
- Collectors become **fail-soft on DB errors**: a write failure counts-and-drops, never exits the
  process. Today an uncaught DB exception exits → systemd respawns in 3 s → re-subscribes OPRA
  while the old socket lingers → Alpaca 406 storms → dropped sweeps *caused by the restart loop
  itself*, corrupting exactly the §0 metric. (This is also just correct behavior post-convoy.)
- `hmt-frontend` stays up during cutover (verified: zero DB access) but serves a 502-ing cockpit
  for the window — expected, and **no frontend build may run during the cutover window** (standing
  rule: never `npm run build` under live `next start`).

**Driver + pool: psycopg3 (sync) + `psycopg_pool.ConnectionPool`, module-level in `db/base.py`. No
pgbouncer** (second daemon for a single-host app; revisit at multiple app hosts). The seam:

```python
# db/base.py sketch — the dual-driver seam
def connect(db_path: Path = DEFAULT_DB_PATH):
    if TRADING_DB_URL and _is_trading_store(db_path):
        return _POOL.connection()        # psycopg3 pool; commits AND returns conn on exit
    return _sqlite_connect(db_path)      # research/corpus stores + rollback path (keeps its
                                         # WAL/busy_timeout PRAGMAs fenced on this branch)
```

- Pool sizing: backend `max_size=16`, collectors `max_size=4`, cron tools `max_size=2` → peak ≈ 30
  of 100. FastAPI's threadpool (default 40) bounds concurrency from above; watch the anyio limiter
  post-cutover and raise to 64 only if `loop_probe` shows saturation.
- Context-manager semantics land in our favor: `with sqlite3_conn` commits but leaves the
  connection open; `with pool.connection()` commits and returns it to the pool (verified). The
  call-site *shape* is correct — but every module still gets a pass in the dialect port (§5 row 1):
  191 non-test in-block `commit()` calls become transaction boundaries, and no cursor may be used
  after its block.
- The `_TimedConnection` slow-transaction logger is rebuilt on psycopg (same `HMT_SLOW_TXN_MS`
  knob), complemented by `log_lock_waits` + `log_min_duration_statement`. MVCC deletes the convoy,
  not the value of knowing who's slow.

**Schema management: schema-in-code stays; lazy init goes.** Today: 39 lazy `init_*()` functions,
91 `CREATE TABLE IF NOT EXISTS`, 41 try/except `ALTER TABLE`, applied via `executescript` at **43
non-test call sites across ~35 modules** — including all five money modules. Under Postgres,
concurrent `CREATE TABLE IF NOT EXISTS` from daemons racing at boot can error, and DDL takes real
locks. The port:

- DDL strings stay in the `db/*.py` modules (ported to PG dialect) — the schema the code expects is
  the schema that exists, by construction.
- `executescript(sql)` → `conn.execute(sql)`: psycopg3 executes multi-statement strings **only when
  no parameters are bound**, and any `%` inside such a blob is then literal. `tools/pg_schema.py`
  states and enforces both conditions (DDL blobs are parameterless by construction here).
- `tools/pg_schema.py --apply` runs the init functions **once, at deploy**, serialized under
  `pg_advisory_lock`. The try/except-`OperationalError` ALTER idiom becomes
  `ADD COLUMN IF NOT EXISTS` (the `"duplicate column"` string-match in `db/paper_orders.py:65`
  breaks under PG error text — it dies).
- In-process lazy init on the PG branch is a no-op (`_INIT_DONE` short-circuits); the SQLite branch
  keeps today's behavior for the research stores.
- SQLite-only migration scaffolding (`_migrate_fusion_episodes` rebuild, `live_activities_new`
  rename, the ALTER ladders) is **not ported** — the PG schema is created in final form.

**Backups — and the watchdog must not fail open.** `pg_dump -Fc` nightly on the existing 03:00
cron, keeping `deploy/backup-db.sh`'s discipline verbatim (loud failure, retention, config tarball,
mattpc 03:30 off-box pull), plus a **weekly full `pg_restore` into a scratch database** as the real
integrity check (`--list` only proves the TOC parses). Two traps found in review, both fixed in
Round 0 *in the same change*:

- `api/jobs.py:126-139` globs `trading-*.db` for backup freshness and **treats `None` as healthy**
  (`api/jobs.py:189-190` only pages when age > 26h *and is not None*). Post-cutover that's either a
  perpetually-green stale signal (script unchanged, happily backing up the frozen file) or a
  permanently-silenced page (glob matches nothing → `None` → quiet). Rewrite to glob the pg_dump
  artifacts **and invert the null case to page** ("cannot determine backup freshness" = critical).
  Add `backup_kind: "pg_dump"|"sqlite"` to `/health`. Verify in rehearsal by deleting the backup
  dir and confirming the page fires.
- The cutover artifact must be named **outside the prune glob** — `PRECUTOVER-trading-<date>.db`,
  not `trading-PRECUTOVER.db`: the pruner runs `find -name 'trading-*.db' -mtime +14 -delete`,
  which would delete the rollback artifact at almost exactly the moment §10's two-week window still
  depends on it. Confirm the mattpc pull carries it off-box.

**Not WAL archiving/PITR**: a failed `archive_command` on an unattended home server accumulates WAL
until the disk fills and the primary panics, and the RPO isn't needed — `order_executions`/`trades`
are mirrored at the broker (the authoritative record), high-volume tables are re-derivable. 24 h
RPO is honest. **Re-open trigger: Rung-3 bounded auto-execution arming** — the moment the local
ledger becomes genuinely authoritative, turn on archiving. (Recorded: the corpus SQLite files have
never been backed up — defensible, rebuildable from vendor data, now written down.)

**Monitoring before cutover, not after:** `/health` today reports `db_size_bytes` via
`os.stat(DEFAULT_DB_PATH)` — post-cutover that's a frozen constant. Before cutover, add PG-native
fields with thresholds: pool in-use/waiting (the anyio-saturation concern has no instrument
otherwise), cluster reachability, oldest-transaction age, dead-tuple ratio on the 5 churn tables,
DB size, `backup_kind`. The Monday go/no-go is made on these numbers, not on logs and vibes.

**Churn/bloat on the snapshot tables is a real story:** `position_snapshots` (6.4M rows, 20 s
inserts) + retention DELETEs would wait for autovacuum's default 20% scale factor (~1.28M dead
rows) while index scans degrade. Per-table settings at creation:
`autovacuum_vacuum_scale_factor=0.02, autovacuum_analyze_scale_factor=0.02` on the four snapshot
tables. If that proves insufficient, native **range partitioning by day** turns retention into
`DROP PARTITION` — O(1), zero bloat, no Timescale needed (§11).

## 5. The dialect port — danger register first, then the mechanical bulk

Scale of the port (production-only counts; staying-SQLite modules excluded from budgeting):
**688 non-test execute call sites across 83 files**; 624 `= ?` placeholders; 191 in-block commits;
150 of 268 test files touch the DB. **Every row below is a named checklist entry for the phase-1
review; the two-Opus money-path review re-verifies the ⚠️ rows.**

| # | Construct | Sites | Treatment |
|---|---|---|---|
| 1 | `with _connect(...)` semantics + in-block `commit()` | ~350 sites (incl. staying-SQLite modules) | Pool makes the shape correct (§4), but each migrating module gets a pass: no cursor use after the block, no connection-scoped temp state, commits become transaction boundaries. |
| 2 | `BEGIN IMMEDIATE` read-modify-write guards ⚠️ | `db/notifications.py:186,253` · `db/options_flow.py:215` · `db/equity_tape.py:137` · `db/news.py:115` · `db/fusion.py:66` | **No blanket translation.** Bus-cursor upserts → single-statement `ON CONFLICT DO UPDATE ... GREATEST(...)` (atomic under row locks). `notifications.py:186` is an update-or-insert with content refresh + un-dismiss — it becomes `INSERT ... ON CONFLICT (group_key, kind) DO UPDATE SET ...`, **which requires creating that unique index (does not exist today; the partial fusion index cannot arbitrate non-fusion kinds)**. `notifications.py:253` (`evict_notification`) is a read-modify-write of a JSON blob with no conflict to arbitrate — `SELECT ... FOR UPDATE`. The fusion executescript rebuild is scaffolding — dropped. Deleting a `BEGIN IMMEDIATE` without a designed replacement silently removes a mutual-exclusion guard. |
| 3 | **Implicit reliance on SQLite's global write lock** (no `BEGIN IMMEDIATE` anywhere near it) ⚠️ | `db/idea_outcomes.py:657-670` + `:307` (`MAX(idea_index)+1` subquery, comment says "race-safe under SQLite's single writer") · `signals/fusion/lifecycle.py:486` · `signals/strategy_scan.py:448` · `db/ai_tables.py:415` · sweep: `grep -rni "write lock\|single writer\|race-safe\|serializ" db/ trading/ signals/` | The nastier sibling of row 2 — code that never took an explicit lock because the global one made it unnecessary. Under READ COMMITTED, two concurrent `register_idea` calls both compute `MAX+1`; the loser's unique violation is then **silently swallowed** by the row-7 `ON CONFLICT DO NOTHING` translation → the idea is never registered, no error anywhere — silent loss in the ledger that "calibration counts everything" depends on. Fix: `idea_index` allocation becomes a sequence/identity, not a `MAX+1` subquery; each swept site gets an individual design. General rule: **`ON CONFLICT DO NOTHING` turns loud unique-violations into silent no-ops** — audit every converted site whose caller checks only `rowcount`. |
| 4 | Scalar 2-arg `MAX()/MIN()` monotonicity invariants ⚠️ | `db/options_flow.py:127` · `db/equity_tape.py:96` · `db/fusion.py:390` · `db/index_pressure.py:133-136` · `signals/fusion/intake.py:222` | `GREATEST()`/`LEAST()` — **never plain assignment**. Each protects a never-rewind cursor / never-shrink counter / sticky shock flag; PG hard-errors on the SQLite form (aggregates are also illegal in `DO UPDATE SET`, so no quiet mistranslation exists), but a careless "fix" loses the invariant with no test — add a regression test per site. NULL divergence (SQLite `MAX(NULL,3)`→NULL vs `GREATEST(NULL,3)`→3) verified harmless here: all five columns are `NOT NULL DEFAULT 0` with `int()` binds. |
| 5 | Money TEXT → NUMERIC, **including the serialization boundary** ⚠️ | storage: `db/order_executions.py`, `db/trades.py`, `db/managed_positions.py`, `db/risk_breaker.py`, `db/schwab_history.py`, `db/fill_attribution.py` · wire: `api/orders_routes.py:23-36` blotter fields + every route emitting money columns | psycopg3 maps `numeric` ↔ `Decimal` natively; `str(qty)` writes bind as unknown-OID text and cast (verified for INSERT and WHERE); `Decimal(str(Decimal))` round-trips. Reads change runtime type (str → Decimal): (a) string-op sites — exactly one exists, the `TRIM(filled_qty) != ''` screen at `db/order_executions.py:422`, becomes `IS NOT NULL`; (b) **the JSON boundary — today money crosses the wire as JSON *strings*; a Decimal through FastAPI's encoder becomes a float** (measured: `Decimal('123.456789012345678')` → `123.45678901234568`) and plain `json.dumps` raises. Policy: **`str()` at the API edge, preserving today's wire format exactly**; enumerate emitting routes in the money round. Copy-time `::numeric` casting is preceded by the §9 parse-scan (currently 0 unparseable values — keep it that way). Two-Opus review, both mandates. |
| 6 | `INSERT OR REPLACE` = DELETE-then-INSERT | 16 non-test statements; trading-store sites after the `daily_bars` leak fix (§3): `db/session_node.py:87` | For migrating sites: `ON CONFLICT ... DO UPDATE` must enumerate every column; audit each table for columns the code expects REPLACE to *reset to defaults*. Ordering dependency: the leak fix must land first or `research/bars.py:74` is also a trading-store site. |
| 7 | `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING` | 14 non-test sites (`db/news.py`, `db/fusion.py`, `db/risk_breaker.py`, `db/idea_outcomes.py:315`, …) | Two semantic gaps: SQLite `OR IGNORE` swallows **NOT NULL/CHECK/FK** violations too — `DO NOTHING` absorbs only unique/exclusion conflicts, so with row 17 turning FKs on, some silently-dropped writes become hard errors (decide per site); and see row 3's silent-no-op warning. `DO UPDATE` against a partial unique index must restate the index predicate in the conflict target. |
| 8 | `cursor.lastrowid` | 11 prod sites (`db/ai_tables.py` ×5, `db/chart_features.py` ×3, `db/idea_outcomes.py:673`, `db/sqlite.py:146`, `db/llm_spend.py:95`) | `RETURNING id`. Fails loudly if missed (`lastrowid` is None under psycopg); the ai_tables sites' follow-up `SELECT * WHERE id=?` collapses into `RETURNING *`. |
| 9 | `rowid` in SQL | `db/live_activities.py:140-146` (single-row claim under concurrency; its `ORDER BY started_at DESC` also hits row 12's NULL flip — nullable, 0 NULLs today, latent) | Rewrite on the table's own PK with an explicit claim design (`FOR UPDATE SKIP LOCKED`-style). FTS rowid linkage dissolves in §7. |
| 10 | FTS5 `memories_fts` ⚠️ | `db/ai_tables.py:141-313` + `ai/memories/retrieval.py` | Own phase — §7. Re-validation job, not translation. |
| 11 | Boolean arithmetic `writers + (a != b)` | `db/index_pressure.py:140-142` | Wrap the boolean to int (`(...)::int` / CASE). Both operands are already `COALESCE`d to −1, so no NULL subtlety — just the operator arity. Hard error if missed (fine). |
| 12 | NULL sort order flips (SQLite: NULLs first ASC; PG: last) | every `ORDER BY` on a nullable column; known-risky: `db/live_activities.py:145` | Sweep all ORDER BYs on nullable columns; add `NULLS FIRST/LAST` or COALESCE where order is load-bearing. Silent result-order change, no error. |
| 13 | Unaliased derived tables | `db/equity_history.py:113-123` (`db/flow_corpus.py:335` stays SQLite) | Add `AS t`. Guaranteed runtime error if missed. |
| 14 | `strftime` in SQL | `db/retention.py:55,60,74,96` · `db/equity_history.py:116` | On TEXT timestamps: hour/day bucketing becomes `substr()` (exact on ISO format). The epoch bucket must be **timezone-pinned**: `floor(extract(epoch from (x::timestamptz AT TIME ZONE 'UTC')) / n)` — a bare `::timestamptz` interprets offset-less values in the server `TimeZone` GUC (live rows all carry `+00:00`, enforced by the §9 validator, but the SQL must not depend on it). SQLite's `TEXT / int` implicit coercion dies — rewrite, don't translate. |
| 15 | `GROUP_CONCAT` | `db/index_pressure.py:189` | `string_agg(DISTINCT coverage, ',')` — separator mandatory. |
| 16 | JSON expression index | `db/idea_outcomes.py:263-267` + consumer `trading/proposals.py:79-83` | `((source_ref::jsonb ->> 'kind'), (source_ref::jsonb ->> 'id'))` — cast verified IMMUTABLE; live values verified: `->>'id'` is JSON-text in all non-null rows, matching the `str(proposal_id)` bind. Keeps the proposal lookup O(log n). |
| 17 | Declared-but-unenforced FKs start enforcing ⚠️ | **four** modules: `db/sqlite.py:43,55,68,77` (clean) · `db/trades.py:51,67` (clean) · `db/ai_tables.py:82` (**10,831 orphan `rule_events` — §3, decide pre-Rehearsal-B**) · `db/chat_sessions.py:38` (**`ON DELETE CASCADE` — starts *deleting* message rows on session delete; enumerate every cascade clause, this is a behavior change in the deleting direction, not just rejected inserts**) | Per-FK decision: enforce, or drop deliberately. And `db/retention.py:157-162` deletes **parent-first** (`poll_runs` then children) — determinate failure under enforced FKs, not a "verify": swap to child-first. |
| 18 | `PRAGMA` surfaces | `PRAGMA table_info` via `db/schema_helpers.py` (+7 sites; mostly dies with lazy init) · **`PRAGMA freelist_count` at `db/retention.py:258` is part of `prune_poll_history`'s return contract** | table_info → `information_schema.columns` behind the same helper. freelist_count has no PG analogue — replace in the contract (`pg_relation_size` delta or drop the key + fix consumers). WAL/busy_timeout PRAGMAs fence onto the SQLite branch of `connect()` (§4 sketch). |
| 19 | Paramstyle `?` → `%s`; `%` literals; dynamic `IN` builders | all sites; **literal-`%` sites: exactly two** — `trading/risk_engine.py:925`, `db/paper_orders.py:337` (`LIKE 'book_%'`); the LIKE-with-bound-parameter sites need nothing (psycopg never inspects parameter values) | **No `?`→`%s` translation shim** (hides errors). The **28** dynamic `IN ({placeholders})` builders convert to `ANY`-style with two traps: **`NOT IN (...)` must become `<> ALL(%s)`** — `<> ANY` is true for nearly every row, and the one `NOT IN` site is the daily-order-count query feeding the risk caps (`db/order_executions.py:234`), a silent money-adjacent inversion; and a Python **list** binds as a concretely-typed array (`text[]` etc.), so `= ANY(%s)` is *not* type-transparent — check each builder's element type against the column. |
| 20 | `sqlite3.Row` / `sqlite3.Connection` types | 28 non-test `sqlite3.Row` refs · **37 non-test `sqlite3.Connection` annotations** (incl. `db/schema_helpers.py:8,14` and `db/accounts.py:46-94`, both already touched by other rows) · positional `row[0]` at ~12 sites | `dict_row` on the pool; positional sites rewritten to named columns (or `tuple_row` locally). Annotations become the seam's union type or a protocol. (No `except IndexError` exists anywhere — that hazard has zero call sites.) |
| 21 | **Reserved-word identifiers** | `index_pressure_minutes.window` + `index_pressure_days.window` — `WINDOW` is fully reserved in PG; every unquoted reference in `db/index_pressure.py` (~8 sites incl. `ON CONFLICT(date, window)`) is a syntax error | **Rename the column during the copy** (the cheapest moment there will ever be) rather than quote it forever. Mechanical sweep for the whole class: every DDL identifier × `SELECT word FROM pg_get_keywords() WHERE catcode='R'`. |
| 22 | `database is locked` retry/fail-soft handlers ⚠️ | ~15 sites incl. `trading/position_ledger.py:256`, `db/risk_breaker.py:54,86`, `signals/pipeline_audit.py:607`, `db/accounts.py:52-88` | PG's error taxonomy is different (`SerializationFailure`, `DeadlockDetected`, connection errors) — each handler is a **policy decision, not a rename**. Risk-breaker + position-ledger handlers are money-path: two-Opus review. `db/accounts.py`'s blanket swallow-`OperationalError`-return-`[]` narrows to `UndefinedTable`. Collector-level policy is §4's fail-soft rule. |
| 23 | `executescript` | **43 non-test sites, ~35 modules** (all five money modules; `db/base.py` ×3) | §4's schema-management design: `conn.execute(multi_statement)` is legal only parameterless, `%` is then literal; all schema application moves to `tools/pg_schema.py` at deploy. |

**Free passes — verified absent or swept clean, do not re-audit:** `ATTACH` (0), `WITHOUT ROWID`
(0), `LIMIT` in DELETE/UPDATE (0 — retention batches in Python), `datetime('now')`/`julianday`/
`CURRENT_TIMESTAMP` in SQL (0 — all "now" comes from `now_iso()`), `IFNULL` (0), `INSTR`/`GLOB`/
`RANDOM()`/`IIF`/`printf` (0), `COLLATE NOCASE` (0), `except IndexError` (0), **`LIKE`
case-sensitivity divergence (swept: every prod LIKE lowercases both sides or uses mechanical
lowercase keys)**, **double-quoted string literals in SQL (swept: none — SQLite's DQS misfeature
unused)**. Window functions and CTEs in use are fully portable.

## 6. Type mapping (the v1 ruleset)

| SQLite today | Postgres v1 | Note |
|---|---|---|
| Money `TEXT` (Decimal via Python) | **`NUMERIC`** | Never `double precision` for money. §5 row 5, incl. the wire format. |
| `REAL` (floats: snapshots, flow premiums, paper proposals) | `double precision` | Pre-chokepoint floats stay floats in v1. |
| ISO-TEXT timestamps | `TEXT`, database collation `C` | §1 non-goal; `timestamptz` per-table later. |
| 0/1 INTEGER booleans | `integer`/`smallint` | No `boolean` conversion in v1. |
| `*_json TEXT` | `TEXT` (+ `::jsonb` casts at the 11 SQL sites) | `jsonb` per-column later. |
| `INTEGER PRIMARY KEY [AUTOINCREMENT]` | **`GENERATED BY DEFAULT AS IDENTITY`** | *BY DEFAULT*, not ALWAYS — the COPY carries existing ids and restores must stay simple; sequences never reuse ids either way. **Sequences are `setval`'d to MAX(id)+1 as a mandatory §9 step** — miss it and every auto-PK insert fails at Monday's open while read-only smoke stays green. |
| `BLOB` (`memories.embedding`) | `bytea` | Python cosine leg unchanged (§7). |

## 7. The memory retriever (FTS5 → Postgres FTS) — a re-validation phase, not a translation

The one subsystem with a live measured benchmark: hybrid retrieval (FTS5 bm25 + model2vec
embeddings, RRF-fused) scored **hit@4 12/15 vs 9/15 lexical-only**. `bm25()` and `ts_rank_cd` are
*different ranking functions* — the benchmark does not carry over unexamined.

Plan: generated `tsvector` column + GIN index (**deletes all three FTS sync triggers** and the
external-content rowid linkage — a real simplification); query via `websearch_to_tsquery`/
`to_tsquery` replacing the hand-built FTS5 OR-string; rank via `ts_rank_cd`. The embedding leg
(`bytea`, `np.frombuffer`, Python cosine over ≤200 rows) and the RRF fusion stay untouched.
**Gate: re-run the hit@4 benchmark before cutover; ship only at ≥ the SQLite baseline.** If ranking
regresses, `pg_trgm` is the fallback lexical leg, and `MEMORY_EMBEDDINGS_ENABLED` remains the kill
switch. The fail-soft-to-recency path stays (exception type changes with §5 row 22).

## 8. Tests and CI

Today's isolation mechanism **is the filesystem** — `conftest.py` forces `TRADING_DB_PATH` to a
tempfile before any import (a hole that once let tests write the real DB — twice), and 634 fixtures
build `tmp_path / "*.db"` files threaded through 2,388 `db_path=` arguments. This is the
migration's largest hidden cost, and the isolation design must be **fail-closed against the
production cluster**:

- **CI runs against a dedicated second cluster** (`pg_createcluster 18 citest`, own port, ~64 MB
  `shared_buffers`) — **as the default, not an escalation**. The single-cluster idea died in
  review twice over: (a) Postgres backend memory is charged to `postgresql.service`, not the
  runner's cgroup, so the runner's hard-won `MemoryMax=6G` cap (born of a real 07-12 outage)
  doesn't contain CI's DB load *either way* — but on a shared cluster, cgroup pressure that kills
  any one backend sends the **whole cluster into crash recovery, taking the live trading datastore
  down with it**; (b) "escalate if CI disturbs the app" is unusable when the disturbance mode is
  an instantaneous crash rather than a slowdown. A tiny second cluster is one more systemd unit
  and buys a hard blast wall.
- Role/auth separation per §4: `hmt_test` credentials live in the runner's env, never `.env`;
  `REVOKE CONNECT` on the production DB from PUBLIC and `hmt_test` (belt on top of the
  separate-cluster braces).
- Per-run **database** cloned from a template database (near-instant; note `CREATE DATABASE ...
  TEMPLATE x` fails if any connection holds the template — dedicate a template owner and retry),
  dropped at teardown; per-test isolation via transaction rollback. **Per-test *schemas* inside any
  shared database are forbidden** — a `search_path` bug away from the historic incident.
- **The conftest assertion is three-pronged and fail-closed**: DSN env var present (missing →
  abort, never fall back), `dbname` matches `^hmt_test`, and `SELECT current_user` = `hmt_test` —
  the role check is the teeth; a wrong dbname under a powerless role is still harmless.
- The `db_path=` parameter survives as an opaque store handle (research stores need it);
  trading-store tests migrate module-by-module alongside their dialect port. Lock-simulation tests
  (`database is locked` injections) convert to the §5 row 22 failure modes or retire.
- **Dual-dialect CI during the transition** (SQLite branch + PG branch) so the rollback path stays
  continuously exercised until deleted. Accepted cost, stated: the ~7–14 min suite roughly doubles
  serially on the single runner (there is one `Runner.Listener`; no parallel-job assumption);
  revisit the per-test `timeout = 300` against a measured PG-suite p99.

## 9. Cutover plan

**Sequencing principle: one atomic cutover of trading.db, heavily rehearsed — not per-table
phasing.** The driver switch is global (`TRADING_DB_URL`), the stores are interdependent (fusion
reads the tape; scans read snapshots), and a long dual-store window creates cross-store consistency
questions strictly worse than a rehearsed atomic switch with a frozen fallback. "Trading last" is
honored in *review order and rehearsal depth*: the money modules get the two-Opus review before
anything cuts over, and the cutover happens with markets closed against a paper-only execution
surface (Schwab read-only by standing rule; Alpaca paper; no live rail armed).

**Pre-cutover gates (all must be green before scheduling the Saturday):** §3 bypass-tool
dispositions done and the no-`sqlite3.connect`-on-trading-paths grep passes (moved here from the
retire round — as a post-hoc check it verifies an invariant already false); `daily_bars` leak fixed
+ absence asserted; `rule_events` FK decision executed; dead-man ported and `--check-only` green;
backup watchdog rewritten (fails closed); `/health` PG fields live; units updated (ordering +
StartLimit + committed to `deploy/systemd/`); `.env` chmod 600; money-round two-Opus review merged.

1. **Rehearsal A — COPY tooling** on `options_iv_history.db` (1 table, 1.88M rows, zero writers):
   proves `tools/sqlite_to_pg.py` — per-table batched SELECT → psycopg3 `cursor.copy()` with an
   explicit per-column converter table. No pgloader: SQLite has no types to infer; every mapping
   that matters is semantic and lives in our converter table, and the schema must come from the
   ported code, not inference.
2. **Rehearsal B — full dress rehearsal**: restore Friday's nightly SQLite backup into a scratch PG
   database, run the ported app against it on a dev port, run the suite, smoke health + loop_probe.
   **Measure and record the wall-clock for copy + index build + `VACUUM ANALYZE`** on the real box
   (index builds on 67 tables on a swap-tight machine are the long pole, not the COPY) — the
   downtime claim must be measured, not asserted. **Rehearse the abort path too** (step 5) — it is
   the only path guaranteed to be exercised under stress.
3. **Cutover (a Saturday, markets closed), scripted as `tools/pg-cutover.sh` with the literal
   commands — no muscle memory.** In order:
   - `sudo systemctl stop` the three daemons (NOT MainPID kills — `Restart=always` respawns those
     in 3 s; note `tools/serv-deploy.sh` cannot stop units and even refuses stopped ones — hence
     the dedicated script). Confirm the 06:00 UTC schwab_reauth cron cannot fire mid-window or is
     disarmed for the weekend.
   - `sqlite3 .backup` → `PRAGMA integrity_check` → artifact named **`PRECUTOVER-trading-<date>.db`**
     (outside the prune glob, §4) → **then `mv data/trading.db data/trading.RETIRED-PENDING.db &&
     chmod 400`** — the freeze is *physical*: any straggler writer (missed tool, stray cron, agent
     shell) now fails loudly with "unable to open database file" instead of writing into a void
     that reconciliation is structurally blind to. Record `stat` + `PRAGMA data_version` of the
     artifact; re-check unchanged after the copy.
   - Copy from the frozen artifact via the rehearsed tool.
   - **Reconcile — the computable procedure:** (a) per-table row counts; (b) per money column, a
     **Python-side `Decimal` sum** streamed from both stores, compared as Decimal (never SQL
     `SUM()` on the SQLite side — TEXT coerces to double and malformed values to 0.0); (c) a
     per-row order-independent hash over `pk|normalized-value` per money table, catching offsetting
     errors sums cannot; (d) the **pre-copy parse scan**: every money TEXT value parses as
     `Decimal` (baseline today: 0 unparseable — enforced, never coerced) and every timestamp
     carries an explicit offset; (e) net-cash realized P&L identical to the cent; (f) spot-check
     `order_executions` against broker records.
   - **`setval` every identity sequence to MAX(id)+1** (§6), then **`VACUUM ANALYZE` every table**
     (a freshly-copied cluster has no statistics — skipping this sends Monday's planner in blind
     and makes "PG feels slower" a false no-go), then the **write-smoke**: insert-and-rollback one
     row per identity table (read-only smoke cannot catch a dead sequence).
   - Flip `TRADING_DB_URL` in `.env` → `sudo systemctl start` → smoke health + loop_probe.
   - **Abort branch, pre-committed:** if reconciliation is not exact by **T+3h**, `mv` the file
     back, restart on SQLite, retry next Saturday. The rename makes abort a two-command operation;
     it is rehearsed in Rehearsal B; the founder is the decision owner.
4. **Monday = the falsifier session** (§0 criteria, instrumented and attributed). **Go/no-go
   checkpoints: before Monday's open, and at Monday's close** — with the §10 deadline rule.

## 10. Rollback

- **Dual-write is rejected**, permanently: it doubles the write path through the sacred chokepoint,
  poses an unanswerable "which store is truth?" question, and its characteristic failure — a silent
  partial ledger write — is worse than the outage it prevents. Dual-*read* fallback rejected for
  the same reason: it hides failure instead of surfacing it.
- Rollback = remove `TRADING_DB_URL` from `.env`, `mv` the frozen file back, restart the three
  units. One branch in `db/base.connect()` — kept alive and **CI-tested for ~2 weeks post-cutover,
  then deleted**. A rollback path never exercised isn't one.
- **The rollback deadline is before Monday's first fill — not Monday's close.** Review killed the
  "bounded loss, broker-recoverable" framing: broker resync (`trading/sync.py`,
  `trading/fill_tracker.py`) rebuilds `trades`/`order_executions` but **not** the derived control
  state. A Monday-close rollback to Saturday's file would: reset `managed_positions.high_water_mark`
  (the exit engine forgets where the trailing stop is) and `first_target_hit` (**scale-outs fire a
  second time**); orphan any position opened Monday (**live broker position with no exit
  management at all**); revert Monday-closed positions to `open`; **un-trip a tripped daily-loss
  breaker and re-baseline it at post-loss equity**; and wipe Monday's push-dedup keys (every alert
  re-fires). None of that is broker-recoverable. Therefore: rollback is a *pre-open* decision;
  once Monday trades, the session is **committed — forward-fix only**. (A post-rollback
  reconstruction script was considered and rejected by the plan's own standard: it would be
  untested at the moment of need.)
- **No reverse `pg_to_sqlite` ETL** — same reasoning.
- Decommission explicitly: when the SQLite branch is deleted, the frozen file is renamed
  `trading.RETIRED-<date>.db` permanently; the §9 freeze already guarantees no stray tool can
  silently write a shadow database in the interim.

## 11. Deferred follow-ons (each its own card, none block cutover)

- `timestamptz` conversion, per table, starting with new tables.
- `jsonb` column conversion where SQL-side querying earns it (`source_ref`, `payload_json`,
  `raw_json` first).
- **TimescaleDB: deferred with a trigger.** The hot in-PG time series is retention-pruned and, if
  churn bites, day-partitioned natively (§4). Re-open when the multi-user shared plane needs
  time-series queries at scale, or any PG table passes ~50M rows with measured time-range pain.
- **Multi-user seam**: `tenant_id`/`owner_id` reserved columns on NEW tables per standing
  direction — cheap for rewritten DDL; do not retrofit old tables yet.
- Promote `paper_order_proposals` floats to NUMERIC; dead-table cleanup (`social_mentions`,
  `fusion_investigation_baseline`, `reason_runs`, never-created `idea_dossiers`/`llm_spend`).
- Backend RSS profile (the 1.9 GB `uvicorn` — the box's true memory hog, §4).
- Slow-suite work if the PG suite measurably drags (template DBs are the main mitigation).

## 12. Work plan (rounds, each with a verify step)

| Round | Deliverable | Verify |
|---|---|---|
| 0 | Cluster install + tuning (§4 incl. `MemoryHigh`/OOMScoreAdjust, log rotation); **citest cluster**; roles + REVOKEs; `.env` chmod; backup script + **watchdog fail-closed rewrite** + weekly restore check; `/health` PG fields; unit ordering + StartLimit + collector units committed; memory before/after measurement | restore cycle green; deleted-backup-dir page fires; conftest aborts without env; units end in `failed` (not loop) when PG is down |
| 1 | `db/base.py` dual-driver seam + pool + ported slow-txn logger + `HMT_ENV` prod-DSN guard; `tools/pg_schema.py --apply` (advisory lock); deploy-script step (abort-on-failure) | both branches pass existing suite untouched; schema applies idempotently twice; worktree without HMT_ENV refuses a prod DSN |
| 2..n | Dialect port, module-by-module — danger-register rows 2, 3, 4, 9, 17 (FK decisions + retention child-first), 21 (`window` rename) first; each module's tests migrate to the PG fixture in the same PR; collectors fail-soft | dual-dialect CI green per module; regression test per monotonicity invariant; §3 bypass tools dispositioned |
| money | `db/order_executions.py`, `db/trades.py`, `db/managed_positions.py`, `db/risk_breaker.py`, `db/fill_attribution.py`, `trading/position_ledger.py` error policy, `tools/repair_event_quantities.py`, **the API serialization boundary** | **Two parallel Opus adversarial reviewers, split mandates** — standing rule |
| FTS | §7 memory retriever port | hit@4 ≥ SQLite baseline |
| copy | `tools/sqlite_to_pg.py` + converter table + reconciliation suite (§9) + `tools/pg-cutover.sh`; Rehearsal A | options_iv rehearsal reconciles exactly |
| dress | Rehearsal B incl. **abort rehearsal** + measured downtime | suite + smoke + loop_probe clean on scratch DB; §9 pre-cutover gates all green |
| cutover | §9 step 3 | reconciliation exact; sequences set; ANALYZE done; write-smoke green; units up |
| verdict | Monday watch, go/no-go **pre-open and at close** (§0 attributed criteria) | criteria met vs the 121-failure baseline |
| retire | Delete SQLite branch after ~2 weeks dual-CI; permanent retire rename | grep gate re-run (now trivially green — it was a pre-cutover gate) |
