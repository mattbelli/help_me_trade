"""The confirmatory abstention driver, on a synthetic corpus with known answers.

These pin what the run is not allowed to get wrong: the examined sessions must never
enter the confirmatory set (they are in-sample and would confirm the rule with the
data that chose it), the rollup's finer primary key must collapse to the registered
bucket grain before the rule is applied, the pooled number must be premium-weighted
rather than a mean of session rates, and the state split must reconcile to the
headline the band is declared against.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from signals.options_flow.corpus_direction import (
    CONTESTED,
    NO_OPINION,
    WEIGHTING_UNSTABLE,
)
from tools.confirmatory_abstention import (
    BAND_HIGH,
    BAND_LOW,
    CORPUS_OF_RECORD,
    EXAMINED_SESSIONS,
    confirmatory_sessions,
    connect,
    run,
    session_reads,
    session_stats,
)

ROOT = Path(__file__).resolve().parent.parent

_SCHEMA = """
CREATE TABLE corpus_sessions (
    date TEXT PRIMARY KEY,
    prints INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    underlyings INTEGER NOT NULL,
    unresolved_prints INTEGER NOT NULL,
    no_spot_prints INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE flow_rollup (
    date TEXT NOT NULL,
    underlying TEXT NOT NULL,
    right TEXT NOT NULL,
    dte_bucket TEXT NOT NULL,
    mny_bucket TEXT NOT NULL,
    side TEXT NOT NULL,
    strict_side TEXT NOT NULL,
    prints INTEGER NOT NULL,
    orders INTEGER NOT NULL,
    contracts INTEGER NOT NULL,
    premium_usd REAL NOT NULL,
    premium_sq REAL NOT NULL,
    premium_sq_orders REAL NOT NULL,
    premium_max REAL NOT NULL,
    u_premium REAL NOT NULL,
    u_premium_clipped REAL NOT NULL,
    multileg INTEGER NOT NULL,
    cond TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    PRIMARY KEY (date, underlying, right, dte_bucket, mny_bucket,
                 side, strict_side, multileg, cond)
);
"""


def _row(date, und, side, premium, clipped, raw, *, right="C", mny="atm",
         multileg=0, cond="a", version=CORPUS_OF_RECORD):
    return (date, und, right, "0-7", mny, side, side, 1, 1, 1,
            premium, 0.0, 0.0, premium, raw, clipped, multileg, cond, version)


def _corpus(tmp_path: Path, sessions: list[tuple[str, int]], rows: list[tuple]) -> Path:
    db = tmp_path / "flow_corpus.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO corpus_sessions VALUES (?,0,0,0,0,0,?,'t')",
        sessions,
    )
    conn.executemany("INSERT INTO flow_rollup VALUES " + "(" + ",".join("?" * 19) + ")", rows)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def examined_dates() -> list[str]:
    return sorted(EXAMINED_SESSIONS)


def test_examined_sessions_are_the_two_the_band_was_set_on(examined_dates):
    """Addendum C:49 (2025-07-28, the rule's audit session) and Addendum A:10 /
    B:4 (2026-07-23, the validation day) — 260 planned minus these two is C's
    '~258 sessions not yet examined'."""
    assert examined_dates == ["2025-07-28", "2026-07-23"]


def test_examined_sessions_never_enter_the_confirmatory_set(tmp_path, examined_dates):
    dates = [*examined_dates, "2026-01-05", "2026-01-06"]
    db = _corpus(
        tmp_path,
        [(d, CORPUS_OF_RECORD) for d in dates],
        [_row(d, "AAA", "buy", 100.0, 10.0, 10.0) for d in dates],
    )
    with connect(db) as conn:
        split = confirmatory_sessions(conn)
    assert split["sessions"] == ["2026-01-05", "2026-01-06"]
    assert split["excluded_examined"] == examined_dates
    assert split["sessions_in_corpus"] == 4


def test_off_vintage_sessions_are_listed_not_pooled(tmp_path):
    db = _corpus(
        tmp_path,
        [("2026-01-05", CORPUS_OF_RECORD), ("2026-01-06", CORPUS_OF_RECORD - 1)],
        [_row("2026-01-05", "AAA", "buy", 100.0, 10.0, 10.0)],
    )
    with connect(db) as conn:
        split = confirmatory_sessions(conn)
    assert split["sessions"] == ["2026-01-05"]
    assert split["excluded_off_vintage"] == [
        {"date": "2026-01-06", "schema_version": CORPUS_OF_RECORD - 1}
    ]


def test_buckets_collapse_the_finer_primary_key(tmp_path):
    """net_premium is buy minus sell ACROSS the side key, and multileg/cond are
    grains inside a bucket — so these four rows are ONE bucket, not four. Summed:
    net = 300 - 100 = +200, clipped = +5, raw = -9 -> weighting_unstable, and the
    whole bucket's $400 abstains."""
    rows = [
        _row("2026-01-05", "AAA", "buy", 200.0, 4.0, 3.0),
        _row("2026-01-05", "AAA", "buy", 100.0, 3.0, -8.0, multileg=1),
        _row("2026-01-05", "AAA", "sell", 100.0, -2.0, -4.0, cond="b"),
        _row("2026-01-05", "AAA", "unclassified", 0.0, 0.0, 0.0, cond="c"),
    ]
    db = _corpus(tmp_path, [("2026-01-05", CORPUS_OF_RECORD)], rows)
    with connect(db) as conn:
        reads = session_reads(conn, "2026-01-05")
    assert len(reads) == 1
    read, premium = reads[0]
    assert premium == pytest.approx(400.0)
    assert read.abstain_state == WEIGHTING_UNSTABLE
    assert read.by_measure == {
        "net_premium": "long",
        "u_premium_clipped": "long",
        "u_premium": "short",
    }


def test_distinct_buckets_stay_distinct(tmp_path):
    rows = [
        _row("2026-01-05", "AAA", "buy", 100.0, 1.0, 1.0),
        _row("2026-01-05", "AAA", "buy", 100.0, 1.0, 1.0, right="P"),
        _row("2026-01-05", "AAA", "buy", 100.0, 1.0, 1.0, mny="otm2_10"),
        _row("2026-01-05", "BBB", "buy", 100.0, 1.0, 1.0),
    ]
    db = _corpus(tmp_path, [("2026-01-05", CORPUS_OF_RECORD)], rows)
    with connect(db) as conn:
        assert len(session_reads(conn, "2026-01-05")) == 4


def test_state_split_sums_to_the_session_abstention(tmp_path):
    """One bucket per state, plus a directional one. Premium-weighted: 100 directional,
    100 no_opinion, 200 weighting_unstable, 100 contested out of 500."""
    rows = [
        _row("2026-01-05", "DIR", "buy", 100.0, 5.0, 5.0),
        _row("2026-01-05", "NOOP", "unclassified", 100.0, 0.0, 0.0),
        _row("2026-01-05", "UNST", "buy", 200.0, 5.0, -5.0),
        _row("2026-01-05", "CONT", "sell", 100.0, 5.0, 5.0),
    ]
    db = _corpus(tmp_path, [("2026-01-05", CORPUS_OF_RECORD)], rows)
    with connect(db) as conn:
        stats = session_stats("2026-01-05", session_reads(conn, "2026-01-05"))
    assert stats["abstention"] == pytest.approx(0.8)
    assert stats["by_state"] == {
        NO_OPINION: pytest.approx(0.2),
        WEIGHTING_UNSTABLE: pytest.approx(0.4),
        CONTESTED: pytest.approx(0.2),
    }
    assert stats["state_split_gap"] == pytest.approx(0.0, abs=1e-12)
    assert stats["buckets"] == 4
    assert stats["premium_usd"] == pytest.approx(500.0)
    assert not stats["in_band"]


def test_band_comparison_and_pooling_are_premium_weighted(tmp_path):
    """Two sessions: a tiny one that abstains on everything, a large one that
    abstains on 10%. The mean of the session rates is 55%; the registered
    premium-weighted pooled rate is 10.9% — and only the second is the band's
    number. The pooled value must land BELOW the band here, and be reported so."""
    rows = [
        _row("2026-01-05", "AAA", "buy", 100.0, 5.0, -5.0),
        _row("2026-01-06", "AAA", "buy", 900.0, 5.0, 5.0),
        _row("2026-01-06", "BBB", "buy", 100.0, 5.0, -5.0),
    ]
    db = _corpus(
        tmp_path,
        [("2026-01-05", CORPUS_OF_RECORD), ("2026-01-06", CORPUS_OF_RECORD)],
        rows,
    )
    result = run(db)

    assert [s["abstention"] for s in result["sessions"]] == [
        pytest.approx(1.0),
        pytest.approx(0.1),
    ]
    assert result["pooled"]["abstention"] == pytest.approx(200.0 / 1100.0)
    assert result["pooled"]["by_state"] == {WEIGHTING_UNSTABLE: pytest.approx(200.0 / 1100.0)}
    assert not result["pooled"]["in_band"]
    assert result["sessions_outside_band"] == {
        "count": 2,
        "below": ["2026-01-06"],
        "above": ["2026-01-05"],
    }
    assert BAND_LOW == 0.20 and BAND_HIGH == 0.35


def test_in_band_session_reports_in_band(tmp_path):
    rows = [
        _row("2026-01-05", "AAA", "buy", 750.0, 5.0, 5.0),
        _row("2026-01-05", "BBB", "buy", 250.0, 5.0, -5.0),
    ]
    db = _corpus(tmp_path, [("2026-01-05", CORPUS_OF_RECORD)], rows)
    result = run(db)
    assert result["pooled"]["abstention"] == pytest.approx(0.25)
    assert result["pooled"]["in_band"]
    assert result["sessions_outside_band"]["count"] == 0
    assert result["per_session_distribution"] == {"n": 1, "min": 0.25, "max": 0.25}


def test_distribution_reports_the_registered_quantiles(tmp_path):
    """Four sessions at 0%, 25%, 50%, 100% abstention by premium."""
    sessions, rows = [], []
    for date, abstaining in (("2026-01-05", 0.0), ("2026-01-06", 250.0),
                             ("2026-01-07", 500.0), ("2026-01-08", 1000.0)):
        sessions.append((date, CORPUS_OF_RECORD))
        if abstaining:
            rows.append(_row(date, "AAA", "buy", abstaining, 5.0, -5.0))
        if abstaining < 1000.0:
            rows.append(_row(date, "BBB", "buy", 1000.0 - abstaining, 5.0, 5.0))
    db = _corpus(tmp_path, sessions, rows)
    dist = run(db)["per_session_distribution"]
    assert dist["n"] == 4
    assert (dist["min"], dist["max"]) == (0.0, 1.0)
    assert dist["p25"] == pytest.approx(0.1875)
    assert dist["median"] == pytest.approx(0.375)
    assert dist["p75"] == pytest.approx(0.625)


def test_missing_examined_session_is_surfaced_not_silent(tmp_path):
    db = _corpus(
        tmp_path,
        [("2026-01-05", CORPUS_OF_RECORD)],
        [_row("2026-01-05", "AAA", "buy", 100.0, 5.0, 5.0)],
    )
    result = run(db)
    assert result["examined_sessions_not_found_in_corpus"] == sorted(EXAMINED_SESSIONS)


def test_sessions_after_the_prereg_window_are_flagged_not_dropped(tmp_path):
    db = _corpus(
        tmp_path,
        [("2026-07-24", CORPUS_OF_RECORD), ("2026-07-29", CORPUS_OF_RECORD)],
        [_row(d, "AAA", "buy", 100.0, 5.0, 5.0) for d in ("2026-07-24", "2026-07-29")],
    )
    result = run(db)
    assert result["sessions_measured"] == 2
    assert result["sessions_after_prereg_window"] == ["2026-07-29"]


def test_driver_opens_the_corpus_read_only(tmp_path):
    db = _corpus(tmp_path, [("2026-01-05", CORPUS_OF_RECORD)], [])
    with connect(db) as conn, pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM corpus_sessions")


def test_cli_writes_the_json_artifact_and_prints_the_table(tmp_path):
    db = _corpus(
        tmp_path,
        [("2026-01-05", CORPUS_OF_RECORD)],
        [
            _row("2026-01-05", "AAA", "buy", 750.0, 5.0, 5.0),
            _row("2026-01-05", "BBB", "buy", 250.0, 5.0, -5.0),
        ],
    )
    out = tmp_path / "artifact" / "confirmatory_abstention.json"
    proc = subprocess.run(
        [sys.executable, "tools/confirmatory_abstention.py", "--db", str(db), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert "INSIDE the band" in proc.stdout
    assert "band is binding on this sum" in proc.stdout
    payload = json.loads(out.read_text())
    assert payload["pooled"]["abstention"] == pytest.approx(0.25)
    assert payload["band"]["low"] == BAND_LOW
