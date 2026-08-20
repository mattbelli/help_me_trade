"""Background job registry, supervisor, and staleness watchdog."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from trading.market_hours import _ET, is_market_open

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("HMT_BACKUP_DIR", "/home/mbelli/backups"))
WATCHDOG_INTERVAL_S = 600.0

_watchdog_stale_last_date: dict[str, date] = {}
_watchdog_backup_last_date: date | None = None
_watchdog_off_box_last_date: date | None = None
# Last watchdog pass that saw the market CLOSED. Staleness for a
# market_hours_only job is measured from here, never across a closed session:
# its last success is yesterday's tape (or a boot baseline after an overnight
# restart), so an unclamped lag pages CRITICAL at the first tick after 9:30 —
# seconds before the job's first eligible run of the day (fired 07-20
# fusion_intake 26,997s / 07-23 fusion_lifecycle 35,007s; which mornings page
# was a race between the 600s watchdog tick and the job's first success).
_watchdog_last_closed_mono: float | None = None


@dataclass
class Job:
    name: str
    fn: Callable[[], Awaitable[None]]
    interval_s: float
    enabled: Callable[[], bool] | None = None
    market_hours_only: bool = False
    heartbeat_only: bool = False
    # Run the fn once at startup before entering the sleep loop. For
    # long-interval jobs (24h) on a box that restarts more often than their
    # interval — without this, the sleep-first ordering below means they
    # never run at all.
    run_on_start: bool = False
    last_success: float | None = field(default=None, repr=False)
    last_success_at: str | None = field(default=None, repr=False)
    consecutive_failures: int = field(default=0, repr=False)
    last_error: str | None = field(default=None, repr=False)
    # Boot baseline: lets the watchdog page on a job that has NEVER succeeded
    # ("loop alive, failing every iteration" — the audit's core failure mode).
    started: float | None = field(default=None, repr=False)


JOBS: list[Job] = []
_name_index: dict[str, Job] = {}


def register_job(job: Job) -> Job:
    JOBS.append(job)
    _name_index[job.name] = job
    return job


def get_job(name: str) -> Job | None:
    return _name_index.get(name)


def mark_alive(name: str) -> None:
    job = _name_index.get(name)
    if job is None:
        return
    job.last_success = time.monotonic()
    job.last_success_at = datetime.now(UTC).isoformat()
    job.consecutive_failures = 0
    job.last_error = None


def _job_enabled(job: Job) -> bool:
    if job.enabled is None:
        return True
    return job.enabled()


def _sleep_seconds(job: Job) -> float:
    multiplier = min(4.0, 1.0 + 0.5 * job.consecutive_failures)
    return job.interval_s * multiplier * random.uniform(0.95, 1.05)


async def run_job(job: Job) -> None:
    first_pass = job.run_on_start
    while True:
        if first_pass:
            first_pass = False
        else:
            try:
                await asyncio.sleep(_sleep_seconds(job))
            except asyncio.CancelledError:
                raise
        try:
            # Gate checks inside the guard: a raising enabled() callable must
            # skip the pass, never kill the supervisor task.
            if not _job_enabled(job):
                continue
            if job.market_hours_only and not is_market_open():
                continue
        except Exception:
            logger.exception("Job %s gate check failed — skipping pass", job.name)
            continue
        if job.heartbeat_only:
            continue
        try:
            await job.fn()
            mark_alive(job.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.consecutive_failures += 1
            job.last_error = str(exc)
            logger.exception(
                "Job %s failed (consecutive=%d)", job.name, job.consecutive_failures
            )


def newest_backup_age_s() -> float | None:
    if not BACKUP_DIR.is_dir():
        return None
    newest_mtime: float | None = None
    for path in BACKUP_DIR.glob("trading-*.db"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest_mtime is None or mtime > newest_mtime:
            newest_mtime = mtime
    if newest_mtime is None:
        return None
    return time.time() - newest_mtime


def off_site_push_age_s() -> float | None:
    """Seconds since serv last confirmed an off-site push.

    Was `off_box_pull_age_s` reading `last_pull` — mattpc PULLING the backup dir.
    Retired 2026-08-20: dual-booting that box into Omarchy stopped the pull for
    three days and only this watchdog noticed. serv now PUSHES an encrypted
    ledger core to Drive (tools/ledger_core_backup.sh) and stamps `last_offsite`.

    None means the marker is absent — never pushed, or the leg is gone. That is
    a real staleness signal, NOT "nothing to check": callers fail CLOSED on it.
    """
    try:
        return time.time() - (BACKUP_DIR / "last_offsite").stat().st_mtime
    except OSError:
        return None


async def _watchdog_once() -> None:
    from signals.notify import notify

    global _watchdog_last_closed_mono

    today_et = datetime.now(_ET).date()
    now_mono = time.monotonic()
    market_open = is_market_open()
    if not market_open:
        _watchdog_last_closed_mono = now_mono

    for job in JOBS:
        if job.name == "watchdog":
            continue
        if not _job_enabled(job):
            continue
        # A market-hours-only job is legitimately idle overnight/weekends —
        # never page it as stale while the market is closed.
        if job.market_hours_only and not market_open:
            continue
        baseline = job.last_success if job.last_success is not None else job.started
        if baseline is None:
            continue
        if job.market_hours_only and _watchdog_last_closed_mono is not None:
            # Closed-market time never counts as lag (see the clock's comment).
            # The clamp is at most one watchdog interval before the open, and
            # threshold > interval, so the first open tick can't false-page; a
            # job still dead ~11 min into the session pages legitimately.
            baseline = max(baseline, _watchdog_last_closed_mono)
        lag_s = now_mono - baseline
        threshold = max(3 * job.interval_s, job.interval_s + 600)
        if lag_s <= threshold:
            continue
        if _watchdog_stale_last_date.get(job.name) == today_et:
            continue
        await notify(
            severity="critical",
            kind="job_watchdog",
            title=f"Background job stale: {job.name}",
            body=(
                f"Job {job.name} has not succeeded in {lag_s:.0f}s "
                f"(threshold {threshold:.0f}s)."
            ),
            group_key=f"job_stale_{job.name}",
        )
        _watchdog_stale_last_date[job.name] = today_et

    # Fail CLOSED on None. None means the backup dir is missing/unreadable or
    # holds no trading-*.db at all — strictly WORSE than a stale backup, and the
    # old `is not None and ...` guard stayed silent for exactly that case.
    backup_age = newest_backup_age_s()
    if backup_age is None or backup_age > 26 * 3600:
        global _watchdog_backup_last_date
        if _watchdog_backup_last_date != today_et:
            if backup_age is None:
                title = "Nightly ledger backup cannot be read"
                body = (
                    f"No readable trading-*.db under {BACKUP_DIR}. The backup "
                    "may never have run, or the directory is gone."
                )
            else:
                title = "Nightly ledger backup is stale"
                body = f"Newest backup is {backup_age / 3600:.1f}h old."
            await notify(
                severity="critical",
                kind="job_watchdog",
                title=title,
                body=body,
                group_key="backup_stale",
            )
            _watchdog_backup_last_date = today_et

    # The off-site leg: serv pushes an encrypted ledger core to Drive nightly
    # and stamps last_offsite. Fail CLOSED — a missing marker is the dead-leg
    # case we are watching for, not "nothing to check". warning, not critical:
    # local + USB copies still exist and pass integrity_check, so this is a
    # degraded DR guarantee rather than an emergency.
    push_age = off_site_push_age_s()
    if push_age is None or push_age > 48 * 3600:
        global _watchdog_off_box_last_date
        if _watchdog_off_box_last_date != today_et:
            if push_age is None:
                title = "Off-site backup push has not reported"
                body = (
                    "No last_offsite marker on serv: the encrypted push has "
                    "never succeeded, or the timer is gone. Backups may not be "
                    "leaving the box. Local + USB copies are unaffected."
                )
            else:
                title = "Off-site backup push is stale"
                body = (
                    f"Off-site push last confirmed {push_age / 3600:.1f}h ago "
                    "(threshold 48h — two nights missed). Local + USB copies "
                    "are unaffected."
                )
            await notify(
                severity="warning",
                kind="job_watchdog",
                title=title,
                body=body,
                group_key="off_site_push_stale",
            )
            _watchdog_off_box_last_date = today_et


def jobs_health() -> list[dict[str, float | int | str | bool | None]]:
    now = time.monotonic()
    rows: list[dict[str, float | int | str | bool | None]] = []
    for job in JOBS:
        lag_s = None if job.last_success is None else now - job.last_success
        rows.append(
            {
                "name": job.name,
                "interval_s": job.interval_s,
                "last_success_at": job.last_success_at,
                "lag_s": lag_s,
                "consecutive_failures": job.consecutive_failures,
                "last_error": job.last_error,
                "enabled": _job_enabled(job),
            }
        )
    return rows


def start_all() -> list[asyncio.Task[None]]:
    now = time.monotonic()
    for job in JOBS:
        job.started = now
    return [
        asyncio.create_task(run_job(job))
        for job in JOBS
        if not job.heartbeat_only
    ]


async def _heartbeat_noop() -> None:
    return None


def register_watchdog_job() -> None:
    register_job(
        Job(
            name="watchdog",
            fn=_watchdog_once,
            interval_s=WATCHDOG_INTERVAL_S,
        )
    )
