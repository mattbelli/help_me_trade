# Ops / Reliability Audit — help_me_trade on `serv`
*Fable 5 audit · read-only · 2026-07-02 · verified against live serv state (systemd units, crontab, journald, backup dir, token file, DB row counts).*

## 1. Reliability map
**What genuinely keeps it alive:** systemd `Restart=always`/`RestartSec=3` on both units (survives reboots, ordered after `tailscaled`); **the 16 loops are well-guarded** — every loop body is `while True: try: … except Exception: logger.exception(): sleep` (verified across `api/main.py` + `ai/reflection/scheduler.py`), the feared "one exception kills the loop for the day" is essentially absent (one narrow exception, #7); per-symbol inner try/excepts stop one bad ticker poisoning a pass; **SQLite discipline is decent** (WAL + `busy_timeout=5000` + 30s connect timeout, connection-per-call, poll-path DB work off-loop via `asyncio.to_thread`); fail-soft externals (`notify()` never raises, Schwab reads serve stale cache on error, exit engine isolated from poll); **nightly backup uses the right primitive** (`sqlite3 .backup`, 14-day retention, 03:00 cron). **The theme is not "loops crash" — it's silent external death with a blind watchdog, a health signal nothing consumes, backups that never leave the box, and a DB growing without bound.**

## 2. Ranked findings
### CRITICAL-1 — The Schwab token lifecycle is a three-way split brain, and the watchdog is structurally blind
*The single most likely way the system goes dark for days while the founder is away.*
**(a) The health monitor can never warn.** `_client_from_token_basic`'s `_write` (`brokers/schwab_auth.py:169-172`) rewrites `creation_timestamp = int(time.time())` on **every access-token refresh** (~30 min), but `auth_status()` uses `creation_timestamp` as the 7-day *refresh-token* clock (`schwab_auth.py:89-95`). **Confirmed live:** file says created 07-02 12:44 UTC; real refresh mint was 07-01 06:00 (reauth.log). So `estimated_days_until_reauth` is pinned ~7.0 forever → the `_schwab_health_loop` "expiring" alert (fires ≤1.0 days) **can never trigger while the app runs.** A dead refresh token leaves the file intact → `authenticated: True` → no "offline" alert either. First warning arrives ~6 days *after* the token dies.
**(b) The re-auth cron has a 1-day margin + a 50% historical failure rate.** `0 6 */6 * *` is a day-of-month *stride* (1,7,13,19,25,31) → up to 6-day gaps vs a 7-day token. Flow is headless Playwright + OTP (`scripts/schwab_reauth.py`, 563 lines — fragile). `data/reauth.log` shows it **already failed once** (`ModuleNotFoundError: No module named 'scripts'`, the pre-`cd` crontab) — 1 of 2 runs on record failed.
**(c) A successful re-auth may not save a running app.** The market-data adapter is cached forever with its `OAuth2Client` built from the token *at construction* (`brokers/factory.py:101-107`, no TTL/mtime check). After the in-memory refresh token dies, every quote/bars call fails and **stays failing even after the cron mints a fresh token**, until a restart; the cached client's `_write` can even clobber the cron's fresh file. Masked only because active dev restarts the backend near-daily — the masking disappears exactly in the step-away scenario.
**Blast:** all Schwab market data / RVOL / session-node / price alerts / EOD snapshots / watchlist silently dead up to 6 days; loops spin logging exceptions nobody reads.
**Fix (ordered):** 1) preserve the original `creation_timestamp` in `_write` (only a real OAuth mint sets it); 2) make `_schwab_health_loop` probe reality (one `get_quotes(["SPY"])`; `critical` push after N consecutive failures) — makes the watchdog honest regardless of bookkeeping; 3) invalidate the cached client on token-file mtime change so a cron re-mint heals a running app; 4) run reauth **daily** with a "skip if token <5 days old" guard + push-on-failure; 5) atomic token writes (tmp + `os.replace`, like `settings.py:223`).

### CRITICAL-2 — Backups never leave the box, and restore has never been tested
`deploy/backup-db.sh:15-20`: the off-box rsync is a **commented-out placeholder**. All 13 backups live on the **same LVM volume** as the live DB, one home server → disk death/fire/theft/corruption loses the real-money ledger AND every backup at once. No restore ever performed; no `PRAGMA integrity_check` on copies; backup covers **only `trading.db`** — not `settings.json` (strategy arming!), `schwab_account_labels.json`, `volume_profiles/`, `imports/`, or secrets. A stale `data/trading.db.bak` (Jun 19, a `cp` of a live WAL DB → possibly inconsistent) is a trap — delete it.

### HIGH-3 — The database grows without bound (~60 MB/day, accelerating); nothing prunes
`trading.db` 96 MB (Jun 20) → **502 MB (Jul 2)**. Live: 130,669 `poll_runs`, **1,466,321 `position_snapshots`**, 55,008 `snapshot_analytics` JSON blobs. 20s poll × 4 accounts appends forever; **no DELETE/prune/retention/VACUUM anywhere.** Already bit once (`db/sqlite.py:89-93`: a JOIN "grew to ~45s as tables passed 35k rows, saturating the poll thread" — indexed around, growth remains). Trajectory: multi-GB → queries past the 5s `busy_timeout` → "database is locked" inside trading loops. **Fix:** nightly retention (keep raw 20s snapshots 7–14 days, downsample older to hourly, delete orphaned `snapshot_analytics`) + one supervised `VACUUM`.

### HIGH-4 — `/health` covers 1 of 16 loops, and nothing consumes it
`poll_health.py` tracks only the poll loop; the other 15 have no last-success tracking. Because loops are exception-guarded, the real failure is **"loop alive, failing every iteration"** → journald lines nobody reads. The one honest signal (`poll_healthy:false`) has **no consumer** — crontab has only backup + reauth; no uptime monitor, no staleness alert. If signal-scan stopped 3 days ago, the founder finds out by noticing his phone got quiet. **Failure and success modes are indistinguishable.**

### MED-5 — Deploy semantics
`serv-deploy.sh` relaunches "from the working directory" (dirty right now; shared by multiple agents) → the running service can match no commit. Every deploy resets in-memory dedup (`_rvol_alert_last_date:695`, `_schwab_health_last_date:906`, `_schwab_snapshot_last_date:965`, profile cooldowns) → **duplicate phone alerts after a mid-day restart** (alert fatigue = mission risk); earnings/reports got this right (DB-backed) — move the other three over. No `StartLimitBurst` + inline `_poll_once()` at boot → a bad deploy crash-loops, re-issuing broker calls every 3s → risks tripping the Akamai WAF that then keeps Schwab dead. Add `StartLimitIntervalSec/Burst` + an `OnFailure=` alert unit; deploy from a tag, not the working tree.

### MED-6 — Scheduler-in-web-process coupling
One uvicorn worker runs the API + WS hub + all 16 loops on one GIL + one thread pool; a full-S&P volume-profile precompute, an LLM reflection pass, and a dashboard burst all contend with poll/exit latency (the 45s-query incident proves interference is real). The job-runner (§3) is the structural fix; short of it, add a poll-lag heartbeat so degradation is visible.

### LOW-7 — The one loop that *can* die
`_reports_scheduler_loop` calls `_morning_report_enabled()` + `reports_config()` **outside** its try (`main.py:1164,1169`) — the only loop where an exception escapes the guard and kills the task for good. Move inside the try. Also `schwab_snapshot_task` is cancelled but missing from the shutdown await-tuple.

### LOW-8 — Calendar/time
DST handled correctly (`ZoneInfo` throughout; window-based scheduling is restart-tolerant — good). Gap: **no market-holiday calendar** — EOD snapshot + morning reports run on holidays (harmless noise); RVOL on half-days skews. Low priority.

## 3. The job-runner (smallest version that isn't over-engineered)
Not DBOS/Celery — ~80 lines in `api/jobs.py`: a `Job` dataclass (`name, fn` = ONE pass, `interval, last_success, consecutive_failures`) + a supervisor `while True: try: await job.fn(); mark success except: count+log; sleep(interval + jittered backoff)`. The loops already separate "one pass" from "loop" (`_poll_once`, `_earnings_refresh_once`, …) → delete the 16 `while True` wrappers, register the `*_once` fns with intervals, one `create_task(supervisor())`. Then `/health` returns every job's `last_success`/lag/failures; a `watchdog` job (10 min) pushes `critical` once/day per stale job (rides the trusted push path — closes #4); `market_hours_only` flag so weekends don't page. Natural home for the retention job (#3) + a real Schwab probe (#1b).

## 4. Backup / restore / DR
Half-right (correct `.backup`, WAL-aware, retention, logging). Complete it: 1) **off-box copy this week** (uncomment the rsync → old Windows box / any tailnet host, or `rclone` to B2/S3 encrypted; include `settings.json` + labels + `imports/`); 2) **verify every copy** (`PRAGMA integrity_check`, fail loudly + push; alert if newest backup >26h old); 3) **restore drill once now + quarterly** (other machine: clone → copy backup → boot → confirm `/health`/totals/positions match; time it; write into `SERVER_SETUP.md` — until done once, "you have hope, not backups"); 4) delete `trading.db.bak` + empty `trades.db`.

## 5. Deploy & reproducibility
Today "stand it up elsewhere in a day" = SERVER_SETUP.md + uv/nvm hand-steps + 2 hand-written units + 2 crontab lines that exist only on serv (the reauth `ModuleNotFoundError` was exactly config-not-in-git). **No Dockerfile/compose.** Pragmatic: a `docker-compose.yml` (backend python:3.12 + `requirements.txt`; frontend node:22 + built `.next`; `./data` bind-mount; env from `.env`) → DR becomes "install docker+tailscale, restore `data/`, `docker compose up`" and deploys become image-pinned not working-tree-shaped. Until then: commit the crontab lines to `deploy/`; make `serv-deploy.sh` refuse a dirty-tree restart without `--force`.

## 6. Observability minimum
1) **`/health` v2** from the job registry (every loop's last_success + lag + failure streak + DB size + Schwab probe). 2) **One alert for silence:** the in-process watchdog covers loop staleness; add ONE *external* check for whole-process death (a cron on any other tailnet device hitting `/health`, alert on non-200/stale — in-process watchdogs can't report their own death). 3) Logging is fine as-is (journald, quiet, greppable) — skip structured logging; spend effort on 1 & 2.

## Priority order (effort vs. saved weekends)
1. **CRITICAL-1 fixes 1–4** (token clock, real API probe, cache invalidation, daily idempotent reauth) — ~a day, removes the most likely multi-day silent outage.
2. **Off-box backup + one restore drill + integrity check** (CRITICAL-2) — ~½ day.
3. **Job runner + `/health` v2 + watchdog push** (HIGH-4, enables the rest) — ~a day.
4. **Retention job + VACUUM** (HIGH-3) — ~½ day.
5. Deploy hygiene, compose, holiday calendar — as they come up.

**Bottom line:** the loops are in better shape than "16 hand-rolled asyncio loops" suggests — the guard discipline is consistent and verified. What will actually break unattended trading is everything *around* them: **a token watchdog that can't see, a health signal nobody reads, backups that never leave the building, and a database quietly eating the machine.**
