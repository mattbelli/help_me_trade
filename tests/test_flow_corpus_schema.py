"""Schema/migration guards for the flow corpus.

The corpus is written once per session across a rolling free window, so a schema
mistake is expensive to discover late. These cover the parts that are easy to
break silently: the ALTER path on a table that predates a column, and the
version fence that stops incomparable vintages from pooling.
"""

from __future__ import annotations

import sqlite3

import pytest

from db.flow_corpus import (
    SCHEMA_VERSION,
    connect,
    delete_session_rows,
    ingested_dates,
    init_db,
    record_session,
    upsert_rollup,
)

ROW = ("2025-07-28", "AAPL", "C", "0-7", "atm", "buy", "buy", 3, 30,
       1000.0, 10.0, 500.0, 25.0, 12.0, 0, SCHEMA_VERSION)


@pytest.fixture
def dbp(tmp_path):
    return tmp_path / "flow_corpus.db"


def test_fresh_db_has_clipped_column(dbp):
    init_db(dbp)
    with connect(dbp) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(flow_rollup)")}
    assert "u_premium_clipped" in cols


def test_fresh_db_has_multileg_in_the_primary_key(dbp):
    """multileg is a GRAIN, not a column: both arms of a bucket must be able to
    coexist. If it were outside the key the clean and contaminated rows would
    collide and one would silently overwrite the other."""
    init_db(dbp)
    with connect(dbp) as conn:
        pk = {r["name"] for r in conn.execute("PRAGMA table_info(flow_rollup)")
              if r["pk"]}
    assert "multileg" in pk


def test_both_multileg_arms_coexist(dbp):
    """The whole point of the Addendum D grain: on 2025-07-28 excluding multi-leg
    prints flips the session sign of all three measures, so a reader must be able
    to compute both arms without a re-roll."""
    init_db(dbp)
    clean, dirty = list(ROW), list(ROW)
    dirty[14] = 1
    dirty[9] = 4000.0
    assert upsert_rollup([tuple(clean), tuple(dirty)], db_path=dbp) == 2
    with connect(dbp) as conn:
        rows = {r["multileg"]: r["premium_usd"] for r in conn.execute(
            "SELECT multileg, premium_usd FROM flow_rollup")}
    assert rows == {0: 1000.0, 1: 4000.0}


def test_legacy_table_is_rebuilt(dbp):
    """A pre-D table has no multileg, and multileg is in the PRIMARY KEY — which
    SQLite cannot ALTER. init_db must drop and rebuild rather than leave a table
    that upsert_rollup will fail against. Safe because every such row is already
    fenced out by the schema_version filter and would be re-rolled."""
    conn = sqlite3.connect(dbp)
    conn.execute(
        """CREATE TABLE flow_rollup (
            date TEXT NOT NULL, underlying TEXT NOT NULL, right TEXT NOT NULL,
            dte_bucket TEXT NOT NULL, mny_bucket TEXT NOT NULL, side TEXT NOT NULL,
            strict_side TEXT NOT NULL, prints INTEGER NOT NULL,
            contracts INTEGER NOT NULL, premium_usd REAL NOT NULL,
            premium_sq REAL NOT NULL, premium_max REAL NOT NULL,
            u_premium REAL NOT NULL, schema_version INTEGER NOT NULL,
            PRIMARY KEY (date, underlying, right, dte_bucket, mny_bucket,
                         side, strict_side))"""
    )
    conn.commit()
    conn.close()

    init_db(dbp)

    with connect(dbp) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(flow_rollup)")}
    assert {"u_premium_clipped", "multileg"} <= cols
    assert upsert_rollup([ROW], db_path=dbp) == 1


def test_init_db_is_idempotent(dbp):
    """Re-running init_db on a current table must not drop it — the rebuild is
    keyed on the missing column, and getting that condition backwards would wipe
    the corpus on every process start."""
    init_db(dbp)
    upsert_rollup([ROW], db_path=dbp)
    init_db(dbp)
    with connect(dbp) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM flow_rollup").fetchone()["c"] == 1


def test_clipped_value_round_trips(dbp):
    init_db(dbp)
    upsert_rollup([ROW], db_path=dbp)
    with connect(dbp) as conn:
        r = conn.execute(
            "SELECT u_premium, u_premium_clipped FROM flow_rollup"
        ).fetchone()
    # Stored side by side and distinct: the registered metric and its labelled
    # robustness counterpart must never collapse into one number.
    assert r["u_premium"] == 25.0
    assert r["u_premium_clipped"] == 12.0


def test_old_vintage_is_not_reported_as_ingested(dbp):
    """A session recorded under an older SCHEMA_VERSION must read as absent so it
    is re-rolled, rather than pooling with current-vintage rows."""
    init_db(dbp)
    with connect(dbp) as conn:
        conn.execute(
            "INSERT INTO corpus_sessions (date, prints, rows, underlyings, "
            "unresolved_prints, no_spot_prints, schema_version, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("2026-07-23", 1, 1, 1, 0, 0, SCHEMA_VERSION - 1, "x"),
        )
    assert ingested_dates(dbp) == set()

    record_session("2026-07-24", prints=1, rows=1, underlyings=1,
                   unresolved_prints=0, no_spot_prints=0, ingested_at="x",
                   db_path=dbp)
    assert ingested_dates(dbp) == {"2026-07-24"}


def test_reroll_across_buckets_does_not_double_count(dbp):
    """mny_bucket is in the PRIMARY KEY, so a re-roll that moves a row to a
    different bucket would leave the old row alive under its old key and count
    the session twice. Session-scoped delete is the only idempotent unit."""
    init_db(dbp)
    unknown = list(ROW)
    unknown[4] = "unknown"
    upsert_rollup([tuple(unknown)], db_path=dbp)

    # spot arrives on the re-roll and the bucket resolves
    delete_session_rows("2025-07-28", db_path=dbp)
    upsert_rollup([ROW], db_path=dbp)

    with connect(dbp) as conn:
        rows = conn.execute(
            "SELECT mny_bucket, SUM(premium_usd) p FROM flow_rollup "
            "WHERE date='2025-07-28' GROUP BY mny_bucket"
        ).fetchall()
    assert len(rows) == 1, "old bucket survived the re-roll — session double-counted"
    assert rows[0]["mny_bucket"] == "atm"
    assert rows[0]["p"] == 1000.0
