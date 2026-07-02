"""Batched retention for poll snapshot tables — downsamples old poll history."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect

logger = logging.getLogger(__name__)

RAW_KEEP_DAYS = 14
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


def prune_poll_history(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    raw_keep_days: int = RAW_KEEP_DAYS,
    batch: int = 5000,
) -> dict[str, int]:
    """Downsample poll history older than ``raw_keep_days`` to one run per hour."""
    cutoff = (datetime.now(UTC) - timedelta(days=raw_keep_days)).isoformat()
    counts = {table: 0 for table in ALLOWLIST}
    counts["orphaned_analytics_deleted"] = 0
    hours_kept = 0

    with _connect(db_path) as conn:
        prunable, hours_kept = _prunable_run_ids(conn, cutoff)
        orphan_ids = _orphaned_analytics_ids(conn)

    for i in range(0, len(prunable), batch):
        chunk = prunable[i : i + batch]
        with _connect(db_path) as conn:
            for table in SNAPSHOT_TABLES:
                counts[table] += _delete_by_poll_run_ids(conn, table, chunk)
            counts["poll_runs"] += _delete_by_poll_run_ids(conn, "poll_runs", chunk)
            conn.commit()
        if i + batch < len(prunable):
            time.sleep(0.2)

    for i in range(0, len(orphan_ids), batch):
        chunk = orphan_ids[i : i + batch]
        with _connect(db_path) as conn:
            deleted = _delete_by_poll_run_ids(conn, "snapshot_analytics", chunk)
            counts["snapshot_analytics"] += deleted
            counts["orphaned_analytics_deleted"] += deleted
            conn.commit()
        if i + batch < len(orphan_ids):
            time.sleep(0.2)

    result = {**counts, "hours_kept": hours_kept}
    logger.debug("prune_poll_history cutoff=%s result=%s", cutoff, result)
    return result
