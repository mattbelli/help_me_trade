"""Retention job — downsamples old poll history without touching ledger tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from db import sqlite as sqlite_mod
from db.base import connect, now_iso
from db.decision_journal import init_decision_journal_tables, record_decision
from db.retention import (
    ALLOWLIST,
    ANALYTICS_KEEP_DAYS,
    DELETE_KEEP_DAYS,
    KEEPER_KEEP_DAYS,
    RAW_KEEP_DAYS,
    SNAPSHOT_TABLES,
    prune_poll_history,
)
from db.trades import init_trade_tables


def _seed_poll_run(
    conn: sqlite3.Connection,
    *,
    captured_at: str,
    broker: str = "alpaca",
    account_id: str | None = "acct_a",
    with_children: bool = True,
) -> int:
    cur = conn.execute(
        "INSERT INTO poll_runs (captured_at, broker, account_id) VALUES (?, ?, ?)",
        (captured_at, broker, account_id),
    )
    run_id = int(cur.lastrowid)
    if with_children:
        conn.execute(
            """
            INSERT INTO account_snapshots (
                poll_run_id, account_id, status, cash, buying_power, portfolio_value
            ) VALUES (?, ?, 'ACTIVE', 10000, 10000, 20000)
            """,
            (run_id, account_id or "unknown"),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots (
                poll_run_id, symbol, qty, side, market_value, unrealized_pl,
                avg_entry_price, current_price
            ) VALUES (?, 'AAPL', 10, 'long', 1500, 50, 145, 150)
            """,
            (run_id,),
        )
        conn.execute(
            """
            INSERT INTO order_snapshots (
                poll_run_id, order_id, symbol, qty, side, order_type,
                status, filled_qty, limit_price
            ) VALUES (?, ?, 'AAPL', 5, 'buy', 'limit', 'open', 0, 145)
            """,
            (run_id, f"ord_{run_id}"),
        )
        conn.execute(
            """
            INSERT INTO snapshot_analytics (poll_run_id, analytics_json)
            VALUES (?, ?)
            """,
            (run_id, json.dumps({"run_id": run_id})),
        )
    return run_id


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

@pytest.fixture
def retention_db(tmp_path: Path) -> Path:
    db = tmp_path / "retention.db"
    sqlite_mod.init_db(db)
    init_trade_tables(db)
    init_decision_journal_tables(db)
    return db


def _seed_retention_fixture(db: Path) -> dict:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=RAW_KEEP_DAYS)

    young_runs: list[int] = []
    old_kept_runs: list[int] = []
    old_pruned_runs: list[int] = []
    orphan_analytics_id = 999_999

    with connect(db) as conn:
        # Recent history (inside keep window): 3 days at 20s spacing, two accounts.
        young_start = now - timedelta(days=2)
        for i in range(6):
            ts = (young_start + timedelta(seconds=20 * i)).isoformat()
            for acct in ("acct_a", "acct_b"):
                run_id = _seed_poll_run(
                    conn, captured_at=ts, broker="alpaca", account_id=acct
                )
                young_runs.append(run_id)

        # Old history: two hours × two accounts × three runs per hour (keep first only).
        old_base = cutoff - timedelta(days=5)
        for hour in (10, 11):
            for minute in (0, 20, 40):
                ts = old_base.replace(hour=hour, minute=minute, second=0).isoformat()
                for acct in ("acct_a", "acct_b"):
                    run_id = _seed_poll_run(
                        conn,
                        captured_at=ts,
                        broker="alpaca",
                        account_id=acct,
                    )
                    if minute == 0:
                        old_kept_runs.append(run_id)
                    else:
                        old_pruned_runs.append(run_id)

        # Schwab account on a separate old hour bucket.
        schwab_ts = (old_base.replace(hour=14, minute=0)).isoformat()
        kept_schwab = _seed_poll_run(
            conn, captured_at=schwab_ts, broker="schwab", account_id="schwab_1"
        )
        pruned_schwab = _seed_poll_run(
            conn,
            captured_at=(old_base.replace(hour=14, minute=30)).isoformat(),
            broker="schwab",
            account_id="schwab_1",
        )
        old_kept_runs.append(kept_schwab)
        old_pruned_runs.append(pruned_schwab)

        # Pre-existing orphaned analytics row.
        conn.execute(
            """
            INSERT INTO snapshot_analytics (poll_run_id, analytics_json)
            VALUES (?, ?)
            """,
            (orphan_analytics_id, json.dumps({"orphan": True})),
        )

        # Latest run for get_latest_snapshot / equity history checks.
        latest_ts = (now - timedelta(minutes=5)).isoformat()
        latest_run = _seed_poll_run(
            conn,
            captured_at=latest_ts,
            broker="alpaca",
            account_id="acct_a",
        )
        conn.execute(
            """
            UPDATE account_snapshots
            SET portfolio_value = 55555
            WHERE poll_run_id = ?
            """,
            (latest_run,),
        )

        conn.commit()

    return {
        "young_runs": young_runs,
        "old_kept_runs": old_kept_runs,
        "old_pruned_runs": old_pruned_runs,
        "latest_run": latest_run,
        "orphan_analytics_id": orphan_analytics_id,
    }


def _seed_ledger_rows(db: Path) -> dict[str, str]:
    trade_id = f"evt_{uuid.uuid4().hex[:10]}"
    proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_events (
                id, account_id, source, event_type, trade_date, symbol, side,
                quantity, price, fees, amount, description, external_key,
                raw_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                "acc1",
                "test",
                "trade",
                "2026-01-01",
                "AAPL",
                "buy",
                "10",
                "150",
                "0",
                "-1500",
                "",
                f"ext_{uuid.uuid4().hex}",
                "{}",
                now_iso(),
            ),
        )
        conn.commit()

    record_decision(
        proposal_id=proposal_id,
        symbol="AAPL",
        side="buy",
        qty="10",
        order_type="limit",
        thesis="retention belt-and-braces",
        db_path=db,
    )

    with connect(db) as conn:
        trade_row = conn.execute(
            "SELECT * FROM trade_events WHERE id = ?", (trade_id,)
        ).fetchone()
        journal_row = conn.execute(
            "SELECT * FROM decision_journal WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()

    return {
        "trade_id": trade_id,
        "proposal_id": proposal_id,
        "trade_row": tuple(trade_row),
        "journal_row": tuple(journal_row),
    }


def test_allowlist_is_exactly_five_snapshot_tables() -> None:
    assert set(ALLOWLIST) == {
        "poll_runs",
        *SNAPSHOT_TABLES,
    }
    assert len(ALLOWLIST) == 5


def test_prune_poll_history_downsamples_and_preserves_ledger(
    retention_db: Path,
) -> None:
    meta = _seed_retention_fixture(retention_db)
    ledger = _seed_ledger_rows(retention_db)

    with connect(retention_db) as conn:
        young_before = _count(conn, "poll_runs")
        pruned_ids = set(meta["old_pruned_runs"])

    first = prune_poll_history(db_path=retention_db, raw_keep_days=RAW_KEEP_DAYS)

    with connect(retention_db) as conn:
        assert _count(conn, "poll_runs") == young_before - len(pruned_ids)
        assert first["poll_runs"] == len(pruned_ids)
        assert first["orphaned_analytics_deleted"] >= 1

        for run_id in meta["young_runs"]:
            assert conn.execute(
                "SELECT 1 FROM poll_runs WHERE id = ?", (run_id,)
            ).fetchone()

        for run_id in meta["old_kept_runs"]:
            assert conn.execute(
                "SELECT 1 FROM poll_runs WHERE id = ?", (run_id,)
            ).fetchone()
            for table in SNAPSHOT_TABLES:
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE poll_run_id = ?",  # noqa: S608
                    (run_id,),
                ).fetchone()
                if table == "snapshot_analytics":
                    # Fixture's old runs sit past ANALYTICS_KEEP_DAYS: the keeper
                    # skeleton survives but its fat JSON is deliberately trimmed.
                    assert row is None, (
                        f"kept run {run_id} should have trimmed analytics"
                    )
                else:
                    assert row is not None, f"kept run {run_id} missing {table} child"

        for run_id in meta["old_pruned_runs"]:
            assert conn.execute(
                "SELECT 1 FROM poll_runs WHERE id = ?", (run_id,)
            ).fetchone() is None
            for table in SNAPSHOT_TABLES:
                if table == "snapshot_analytics":
                    n = conn.execute(
                        "SELECT COUNT(*) FROM snapshot_analytics WHERE poll_run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                else:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE poll_run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                assert n == 0

        assert conn.execute(
            "SELECT 1 FROM snapshot_analytics WHERE poll_run_id = ?",
            (meta["orphan_analytics_id"],),
        ).fetchone() is None

        # One run per (broker, account_id, hour) among old rows.
        old_rows = conn.execute(
            """
            SELECT broker, COALESCE(account_id, ''), strftime('%Y-%m-%dT%H', captured_at)
            FROM poll_runs
            WHERE captured_at < ?
            """,
            ((datetime.now(UTC) - timedelta(days=RAW_KEEP_DAYS)).isoformat(),),
        ).fetchall()
        buckets: dict[tuple[str, str, str], int] = {}
        for row in old_rows:
            key = (row[0], row[1], row[2])
            buckets[key] = buckets.get(key, 0) + 1
        assert all(n == 1 for n in buckets.values())

        trade_after = conn.execute(
            "SELECT * FROM trade_events WHERE id = ?", (ledger["trade_id"],)
        ).fetchone()
        journal_after = conn.execute(
            "SELECT * FROM decision_journal WHERE proposal_id = ?",
            (ledger["proposal_id"],),
        ).fetchone()
        assert tuple(trade_after) == ledger["trade_row"]
        assert tuple(journal_after) == ledger["journal_row"]

    latest = sqlite_mod.get_latest_snapshot("alpaca", account_id="acct_a", db_path=retention_db)
    assert latest is not None
    assert latest["account"]["portfolio_value"] == 55555

    equity = sqlite_mod.list_equity_history("alpaca", days=7, db_path=retention_db)
    assert equity
    assert any(row["equity"] == 55555 for row in equity)

    second = prune_poll_history(db_path=retention_db, raw_keep_days=RAW_KEEP_DAYS)
    assert second["poll_runs"] == 0
    assert second["orphaned_analytics_deleted"] == 0
    for table in SNAPSHOT_TABLES:
        assert second[table] == 0


def test_keeper_aging_keeps_earliest_per_day_past_90d(retention_db: Path) -> None:
    now = datetime.now(UTC)
    survivors: list[int] = []
    prunable: list[int] = []
    with connect(retention_db) as conn:
        # Two days, each ~100 days old, three runs per day at distinct hours.
        for day_offset in (100, 101):
            base = (now - timedelta(days=day_offset)).replace(
                minute=0, second=0, microsecond=0
            )
            earliest = None
            for hour in (9, 10, 11):
                ts = base.replace(hour=hour).isoformat()
                run_id = _seed_poll_run(
                    conn, captured_at=ts, broker="alpaca", account_id="acct_a"
                )
                if hour == 9:
                    earliest = run_id
                else:
                    prunable.append(run_id)
            survivors.append(earliest)
        conn.commit()

    result = prune_poll_history(db_path=retention_db)

    with connect(retention_db) as conn:
        for run_id in survivors:
            assert conn.execute(
                "SELECT 1 FROM poll_runs WHERE id = ?", (run_id,)
            ).fetchone(), f"earliest-per-day keeper {run_id} deleted"
        for run_id in prunable:
            assert (
                conn.execute(
                    "SELECT 1 FROM poll_runs WHERE id = ?", (run_id,)
                ).fetchone()
                is None
            ), f"non-earliest run {run_id} survived daily aging"
        # Exactly one run per day among the >90d rows.
        rows = conn.execute(
            "SELECT strftime('%Y-%m-%d', captured_at) AS d, COUNT(*) n "
            "FROM poll_runs GROUP BY d"
        ).fetchall()
        assert all(int(r["n"]) == 1 for r in rows)

    assert result["poll_runs"] == len(prunable)

    second = prune_poll_history(db_path=retention_db)
    assert second["poll_runs"] == 0


def test_analytics_trim_respects_cutoff_but_keeps_skeleton(
    retention_db: Path,
) -> None:
    now = datetime.now(UTC)
    with connect(retention_db) as conn:
        # Past the analytics cutoff (survives as a lone keeper): trimmed.
        old_ts = (now - timedelta(days=ANALYTICS_KEEP_DAYS + 10)).replace(hour=3).isoformat()
        old_run = _seed_poll_run(
            conn, captured_at=old_ts, broker="alpaca", account_id="acct_a"
        )
        # Inside the analytics window: analytics must be kept.
        recent_ts = (now - timedelta(days=max(1, ANALYTICS_KEEP_DAYS - 4))).replace(hour=3).isoformat()
        recent_run = _seed_poll_run(
            conn, captured_at=recent_ts, broker="schwab", account_id="acct_b"
        )
        conn.commit()

    result = prune_poll_history(db_path=retention_db)

    with connect(retention_db) as conn:
        # Old run skeleton survives, but its fat JSON is gone.
        assert conn.execute(
            "SELECT 1 FROM poll_runs WHERE id = ?", (old_run,)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT 1 FROM snapshot_analytics WHERE poll_run_id = ?", (old_run,)
            ).fetchone()
            is None
        )
        for table in ("account_snapshots", "position_snapshots", "order_snapshots"):
            assert conn.execute(
                f"SELECT 1 FROM {table} WHERE poll_run_id = ?", (old_run,)
            ).fetchone(), f"{table} skeleton for old run was deleted"
        # Recent run keeps its analytics.
        assert conn.execute(
            "SELECT 1 FROM snapshot_analytics WHERE poll_run_id = ?", (recent_run,)
        ).fetchone()

    assert result["analytics_trimmed"] >= 1

    second = prune_poll_history(db_path=retention_db)
    assert second["analytics_trimmed"] == 0


def test_hard_delete_removes_runs_past_delete_keep_days(retention_db: Path) -> None:
    now = datetime.now(UTC)
    with connect(retention_db) as conn:
        # Past the hard-delete horizon: the run and ALL its children must be gone —
        # downsampling alone would have kept this as a lone daily keeper forever.
        ancient_ts = (
            (now - timedelta(days=DELETE_KEEP_DAYS + 30)).replace(hour=3).isoformat()
        )
        ancient_run = _seed_poll_run(
            conn, captured_at=ancient_ts, broker="alpaca", account_id="acct_a"
        )
        # Older than the daily-keeper cutoff but inside the hard-delete horizon: it is
        # downsampled to a lone daily keeper, but its skeleton survives.
        kept_ts = (
            (now - timedelta(days=KEEPER_KEEP_DAYS + 10)).replace(hour=3).isoformat()
        )
        kept_run = _seed_poll_run(
            conn, captured_at=kept_ts, broker="alpaca", account_id="acct_b"
        )
        conn.commit()

    result = prune_poll_history(db_path=retention_db)

    with connect(retention_db) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM poll_runs WHERE id = ?", (ancient_run,)
            ).fetchone()
            is None
        ), "run past the hard-delete horizon survived"
        for table in SNAPSHOT_TABLES:
            assert (
                conn.execute(
                    f"SELECT 1 FROM {table} WHERE poll_run_id = ?",  # noqa: S608
                    (ancient_run,),
                ).fetchone()
                is None
            ), f"{table} child of hard-deleted run survived"
        # The within-horizon keeper survives (its skeleton, at least).
        assert conn.execute(
            "SELECT 1 FROM poll_runs WHERE id = ?", (kept_run,)
        ).fetchone(), "within-horizon daily keeper was wrongly hard-deleted"

    assert result["runs_hard_deleted"] >= 1

    second = prune_poll_history(db_path=retention_db)
    assert second["runs_hard_deleted"] == 0


def test_prune_returns_observability_fields(retention_db: Path) -> None:
    _seed_retention_fixture(retention_db)
    result = prune_poll_history(db_path=retention_db)

    assert isinstance(result["db_bytes"], int) and result["db_bytes"] > 0
    assert isinstance(result["freelist_count"], int)
    assert result["freelist_count"] >= 0
    rows_deleted = result["rows_deleted_by_table"]
    assert isinstance(rows_deleted, dict)
    assert set(rows_deleted) == set(ALLOWLIST)
    assert all(isinstance(v, int) for v in rows_deleted.values())
