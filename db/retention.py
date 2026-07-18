"""Batched retention for poll snapshot tables — downsamples old poll history."""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

RAW_KEEP_DAYS = 14  # raw runs downsample to one-per-hour past this age
KEEPER_KEEP_DAYS = 90  # hourly keepers downsample to one-per-day past this age
# Hard ceiling: runs older than this are deleted outright (skeleton + children), not
# just downsampled — without it the daily keepers accrue forever and the tables have no
# upper bound. The longest lookback any reader needs is 7 days (list_equity_history /
# get_snapshot_days_ago cap at 7d; every other reader takes the latest run), so a full
# year of daily keepers is deep headroom while still giving the tables a firm bound.
DELETE_KEEP_DAYS = 365
# snapshot_analytics JSON trimmed past this age (skeleton stays). The sole
# reader (_load_analytics) only ever fetches the LATEST run per account, so
# even 7 days is pure headroom; at ~10MB/day of blobs this constant IS the
# table's steady-state size — raise it only if a historical reader appears.
ANALYTICS_KEEP_DAYS = 7
SNAPSHOT_TABLES = (
    "account_snapshots",
    "position_snapshots",
    "order_snapshots",
    "snapshot_analytics",
)
ALLOWLIST = ("poll_runs", *SNAPSHOT_TABLES)


def _assert_allowlisted(table: str) -> None:
    if table not in ALLOWLIST:
        raise ValueError(f"retention refuse: table {table!r} not in ALLOWLIST")


def _prunable_run_ids(conn, cutoff: str) -> tuple[list[int], int]:
    """Old runs to delete: all but the earliest per (broker, account_id, hour)."""
    rows = conn.execute(
        """
        WITH old_runs AS (
            SELECT
                id,
                strftime('%Y-%m-%dT%H', captured_at) AS hour_bucket,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        broker,
                        COALESCE(account_id, ''),
                        strftime('%Y-%m-%dT%H', captured_at)
                    ORDER BY captured_at ASC, id ASC
                ) AS rn
            FROM poll_runs
            WHERE captured_at < ?
        )
        SELECT id FROM old_runs WHERE rn > 1
        """,
        (cutoff,),
    ).fetchall()
    kept_hours = conn.execute(
        """
        SELECT COUNT(DISTINCT
            broker || '|' || COALESCE(account_id, '') || '|' ||
            strftime('%Y-%m-%dT%H', captured_at)
        )
        FROM poll_runs
        WHERE captured_at < ?
        """,
        (cutoff,),
    ).fetchone()[0]
    return [int(row["id"]) for row in rows], int(kept_hours or 0)


def _prunable_daily_run_ids(conn, cutoff: str) -> list[int]:
    """Keeper aging: among runs older than ``cutoff``, all but the earliest per
    (broker, account_id, DAY) — downsamples the hourly keepers to daily."""
    rows = conn.execute(
        """
        WITH old_runs AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        broker,
                        COALESCE(account_id, ''),
                        strftime('%Y-%m-%d', captured_at)
                    ORDER BY captured_at ASC, id ASC
                ) AS rn
            FROM poll_runs
            WHERE captured_at < ?
        )
        SELECT id FROM old_runs WHERE rn > 1
        """,
        (cutoff,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _deletable_run_ids(conn, cutoff: str) -> list[int]:
    """All poll runs older than the hard-delete horizon — removed entirely (skeleton +
    children). Downsampling only thins old history; this is what bounds it."""
    rows = conn.execute(
        "SELECT id FROM poll_runs WHERE captured_at < ?",
        (cutoff,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _trimmable_analytics_run_ids(conn, cutoff: str) -> list[int]:
    """Analytics blobs for surviving runs older than ``cutoff`` — trimmed even
    when the poll_run keeper stays (the run skeleton is history; the fat JSON goes)."""
    rows = conn.execute(
        """
        SELECT sa.poll_run_id
        FROM snapshot_analytics sa
        JOIN poll_runs pr ON pr.id = sa.poll_run_id
        WHERE pr.captured_at < ?
        """,
        (cutoff,),
    ).fetchall()
    return [int(row["poll_run_id"]) for row in rows]


def _orphaned_analytics_ids(conn) -> list[int]:
    rows = conn.execute(
        """
        SELECT sa.poll_run_id
        FROM snapshot_analytics sa
        LEFT JOIN poll_runs pr ON pr.id = sa.poll_run_id
        WHERE pr.id IS NULL
        """
    ).fetchall()
    return [int(row["poll_run_id"]) for row in rows]


def _delete_by_poll_run_ids(
    conn,
    table: str,
    run_ids: list[int],
) -> int:
    _assert_allowlisted(table)
    if not run_ids:
        return 0
    placeholders = ",".join("?" * len(run_ids))
    if table == "poll_runs":
        cur = conn.execute(
            f"DELETE FROM poll_runs WHERE id IN ({placeholders})",
            run_ids,
        )
    else:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE poll_run_id IN ({placeholders})",
            run_ids,
        )
    return int(cur.rowcount)


def _delete_runs_across_tables(
    db_path: Path,
    run_ids: list[int],
    batch: int,
    counts: dict[str, int],
) -> None:
    """Batched, transactional delete of runs + all their snapshot children."""
    for i in range(0, len(run_ids), batch):
        chunk = run_ids[i : i + batch]
        with _connect(db_path) as conn:
            for table in SNAPSHOT_TABLES:
                counts[table] += _delete_by_poll_run_ids(conn, table, chunk)
            counts["poll_runs"] += _delete_by_poll_run_ids(conn, "poll_runs", chunk)
            conn.commit()
        if i + batch < len(run_ids):
            time.sleep(0.2)


def prune_poll_history(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    raw_keep_days: int = RAW_KEEP_DAYS,
    keeper_keep_days: int = KEEPER_KEEP_DAYS,
    analytics_keep_days: int = ANALYTICS_KEEP_DAYS,
    delete_keep_days: int = DELETE_KEEP_DAYS,
    batch: int = 5000,
) -> dict[str, int | dict[str, int]]:
    """Age poll history: raw→hourly past ``raw_keep_days``, hourly keepers→daily
    past ``keeper_keep_days``, hard-delete runs past ``delete_keep_days``, and trim
    analytics JSON past ``analytics_keep_days``."""
    now = datetime.now(UTC)
    cutoff = (now - timedelta(days=raw_keep_days)).isoformat()
    keeper_cutoff = (now - timedelta(days=keeper_keep_days)).isoformat()
    delete_cutoff = (now - timedelta(days=delete_keep_days)).isoformat()
    analytics_cutoff = (now - timedelta(days=analytics_keep_days)).isoformat()
    counts = {table: 0 for table in ALLOWLIST}
    counts["orphaned_analytics_deleted"] = 0
    counts["analytics_trimmed"] = 0
    counts["runs_hard_deleted"] = 0
    hours_kept = 0

    with _connect(db_path) as conn:
        prunable, hours_kept = _prunable_run_ids(conn, cutoff)
        orphan_ids = _orphaned_analytics_ids(conn)

    # Tier 1: raw runs older than raw_keep_days → one run per hour.
    _delete_runs_across_tables(db_path, prunable, batch, counts)

    # Tier 2: hourly keepers older than keeper_keep_days → one run per day.
    with _connect(db_path) as conn:
        daily_prunable = _prunable_daily_run_ids(conn, keeper_cutoff)
    _delete_runs_across_tables(db_path, daily_prunable, batch, counts)

    # Tier 3: hard ceiling — runs older than delete_keep_days go entirely (skeleton +
    # every child). This is the upper bound the downsample tiers alone never impose.
    with _connect(db_path) as conn:
        deletable = _deletable_run_ids(conn, delete_cutoff)
    runs_before = counts["poll_runs"]
    _delete_runs_across_tables(db_path, deletable, batch, counts)
    counts["runs_hard_deleted"] = counts["poll_runs"] - runs_before

    # Orphaned analytics (poll_run already gone).
    for i in range(0, len(orphan_ids), batch):
        chunk = orphan_ids[i : i + batch]
        with _connect(db_path) as conn:
            deleted = _delete_by_poll_run_ids(conn, "snapshot_analytics", chunk)
            counts["snapshot_analytics"] += deleted
            counts["orphaned_analytics_deleted"] += deleted
            conn.commit()
        if i + batch < len(orphan_ids):
            time.sleep(0.2)

    # Analytics blob trim: drop fat JSON for surviving runs older than the cutoff.
    with _connect(db_path) as conn:
        analytics_trim_ids = _trimmable_analytics_run_ids(conn, analytics_cutoff)
    for i in range(0, len(analytics_trim_ids), batch):
        chunk = analytics_trim_ids[i : i + batch]
        with _connect(db_path) as conn:
            deleted = _delete_by_poll_run_ids(conn, "snapshot_analytics", chunk)
            counts["snapshot_analytics"] += deleted
            counts["analytics_trimmed"] += deleted
            conn.commit()
        if i + batch < len(analytics_trim_ids):
            time.sleep(0.2)

    try:
        db_bytes = os.stat(db_path).st_size
    except OSError:
        db_bytes = 0
    with _connect(db_path) as conn:
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])

    result: dict[str, int | dict[str, int]] = {
        **counts,
        "hours_kept": hours_kept,
        "db_bytes": db_bytes,
        "freelist_count": freelist_count,
        "rows_deleted_by_table": {table: counts[table] for table in ALLOWLIST},
    }
    logger.debug("prune_poll_history cutoff=%s result=%s", cutoff, result)
    return result


# Research capture streams under data/research/ that append one <YYYY-MM-DD>.jsonl
# per session and never touch it again after the close window. They are the
# classification lab's pre-registered ground truth (CLASSIFICATION_LAB_PREREG),
# so retention must be LOSSLESS — gzip only (~14x measured on 2026-07-17), never
# downsample or delete.
RESEARCH_CAPTURE_DIRS = ("print_lab", "chain_samples")


def compress_research_captures(
    research_root: Path | None = None, *, today: str | None = None
) -> dict[str, int]:
    """Gzip prior sessions' research capture JSONL in place; today's file is
    still being appended to and is left alone. Idempotent: a crash between the
    atomic .gz replace and the source unlink just re-compresses next run."""
    root = (
        research_root
        if research_root is not None
        else DEFAULT_DB_PATH.parent / "research"
    )
    if today is None:
        today = datetime.now(_ET).date().isoformat()
    counts = {"compressed": 0, "bytes_before": 0, "bytes_after": 0}
    for sub in RESEARCH_CAPTURE_DIRS:
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            if path.stem >= today:
                continue
            gz_path = path.with_suffix(".jsonl.gz")
            tmp_path = path.with_suffix(".jsonl.gz.tmp")
            size_before = path.stat().st_size
            try:
                with path.open("rb") as src, gzip.open(tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                os.replace(tmp_path, gz_path)
                path.unlink()
            except OSError:
                logger.exception("research capture compress failed: %s", path)
                tmp_path.unlink(missing_ok=True)
                continue
            counts["compressed"] += 1
            counts["bytes_before"] += size_before
            counts["bytes_after"] += gz_path.stat().st_size
    return counts
