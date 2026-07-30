"""The Addendum E label join (frozen mechanics) and the v6 schema switch.

`match_prints` is the one join implementation both the floor-check tool and the
corpus rollup consume; a silent mistake here would mislabel the multileg grain
across all 250 sessions. The v6 tests reload `db.flow_corpus` under the env
override to prove the migration switch actually produces the cond-grain schema
— and that the v5 default remains byte-identical until it is flipped.
"""

from __future__ import annotations

import importlib

import numpy as np
import pytest

import db.flow_corpus
from tools.condition_labels import (
    MULTILEG_CODES,
    PACKAGE_CODES,
    TIED_CODES,
    TOL_NS,
    match_prints,
)

I64 = np.int64


def _m(d, a):
    """match_prints on (cid, cents, size, ts) tuples of plain lists."""
    d = [np.asarray(x, dtype=I64) for x in d]
    a = [np.asarray(x, dtype=I64) for x in a]
    return match_prints(*d, *a)


# --- the frozen join core ----------------------------------------------------

def test_exact_pair_matches():
    d_rows, a_rows = _m(([5], [100], [10], [1_000]),
                        ([5], [100], [10], [1_000]))
    assert list(d_rows) == [0] and list(a_rows) == [0]


def test_kth_occurrence_pairs_in_time_order():
    """Two identical (contract, price, size) prints must pair 1st-to-1st and
    2nd-to-2nd by time — the k discipline. A greedy nearest-timestamp join
    would cross them whenever the tapes interleave."""
    d_rows, a_rows = _m(([5, 5], [100, 100], [10, 10], [1_000, 2_000]),
                        ([5, 5], [100, 100], [10, 10], [2_100, 1_100]))
    got = dict(zip(d_rows.tolist(), a_rows.tolist()))
    assert got == {0: 1, 1: 0}  # first dbn print ↔ earlier alpaca trade


def test_beyond_tolerance_does_not_match():
    d_rows, _ = _m(([5], [100], [10], [0]),
                   ([5], [100], [10], [TOL_NS + 1]))
    assert len(d_rows) == 0


def test_at_tolerance_matches():
    d_rows, _ = _m(([5], [100], [10], [0]),
                   ([5], [100], [10], [TOL_NS]))
    assert len(d_rows) == 1


def test_different_key_never_matches():
    for a_key in (([6], [100], [10]), ([5], [101], [10]), ([5], [100], [11])):
        d_rows, _ = _m(([5], [100], [10], [1_000]), (*a_key, [1_000]))
        assert len(d_rows) == 0


def test_unresolvable_cid_is_excluded_both_sides():
    """cid -1 marks a contract that failed symbol/defs resolution; it must never
    match, and must not perturb k for the resolvable prints around it."""
    d_rows, a_rows = _m(([-1, 5], [100, 100], [10, 10], [1_000, 1_000]),
                        ([5, -1], [100, 100], [10, 10], [1_000, 1_000]))
    assert list(d_rows) == [1] and list(a_rows) == [0]


def test_surplus_alpaca_trade_leaves_dbn_matched():
    """An extra k on one tape (a cancel, a capture dupe) must strand only
    itself: k-th-to-k-th still pairs the prefix."""
    d_rows, a_rows = _m(([5], [100], [10], [1_000]),
                        ([5, 5], [100, 100], [10, 10], [1_000, 5_000]))
    assert list(d_rows) == [0] and list(a_rows) == [0]


def test_input_order_does_not_matter():
    """Both tapes arrive in file order, not key order; the join lexsorts and
    scatters back, and a scatter mistake would relabel prints wholesale."""
    d_rows, a_rows = _m(([9, 5, 9], [50, 100, 50], [1, 10, 1], [3_000, 1_000, 2_000]),
                        ([5, 9, 9], [100, 50, 50], [10, 1, 1], [1_000, 2_000, 3_000]))
    got = dict(zip(d_rows.tolist(), a_rows.tolist()))
    # 9/50/1: dbn ts 2000 (k=0) ↔ alpaca ts 2000 (k=0); ts 3000 (k=1) ↔ 3000
    assert got == {1: 0, 2: 1, 0: 2}


def test_package_family_is_the_registered_set():
    """Addendum E's PACKAGE_CODES, pinned: all multi-leg packages plus all
    stock-option combos, and nothing else — a drive-by 'cleanup' of this set is
    a direction-rule change and must fail loudly."""
    assert MULTILEG_CODES == frozenset("fghijlmtu")
    assert TIED_CODES == frozenset("knopqrs")
    assert PACKAGE_CODES == MULTILEG_CODES | TIED_CODES
    assert not (PACKAGE_CODES & frozenset("abcde"))   # single-leg stays out
    assert not (PACKAGE_CODES & frozenset("ACEGS"))   # cancels/ISO stay out


# --- the v6 migration switch -------------------------------------------------

@pytest.fixture
def v6(monkeypatch, tmp_path):
    """db.flow_corpus reloaded at schema_version 6, restored to the default
    after — module-level constants (LABELED, _ROLLUP_COLUMNS, DDL) are computed
    at import, so the reload IS the switch being tested."""
    monkeypatch.setenv("FLOW_CORPUS_SCHEMA_VERSION", "6")
    fc = importlib.reload(db.flow_corpus)
    yield fc, tmp_path / "v6.db"
    monkeypatch.delenv("FLOW_CORPUS_SCHEMA_VERSION")
    importlib.reload(db.flow_corpus)


V6_ROW = ("2026-07-24", "AAPL", "C", "0-7", "atm", "buy", "buy", 3, 2, 30,
          1000.0, 10.0, 700.0, 500.0, 25.0, 12.0, 1, "f", 6)


def test_v6_puts_cond_in_the_primary_key(v6):
    fc, dbp = v6
    assert fc.SCHEMA_VERSION == 6 and fc.LABELED
    fc.init_db(dbp)
    with fc.connect(dbp) as conn:
        pk = {r["name"] for r in conn.execute("PRAGMA table_info(flow_rollup)")
              if r["pk"]}
    assert "cond" in pk and "multileg" in pk


def test_v6_rows_split_by_condition_code(v6):
    """cond is a grain: the same bucket under two codes must coexist, or the
    'finer splits without a schema change' promise of Addendum E is false."""
    fc, dbp = v6
    fc.init_db(dbp)
    other = list(V6_ROW)
    other[17] = "t"
    other[10] = 4000.0
    assert fc.upsert_rollup([V6_ROW, tuple(other)], db_path=dbp) == 2
    with fc.connect(dbp) as conn:
        rows = {r["cond"]: r["premium_usd"] for r in conn.execute(
            "SELECT cond, premium_usd FROM flow_rollup")}
    assert rows == {"f": 1000.0, "t": 4000.0}


def test_v6_session_ledger_records_the_label_provenance(v6):
    fc, dbp = v6
    fc.init_db(dbp)
    fc.record_session("2026-07-24", prints=1, rows=1, underlyings=1,
                      unresolved_prints=0, no_spot_prints=0, ingested_at="x",
                      multileg_source="codes", label_join_rate=0.9823,
                      label_coverage=0.981, db_path=dbp)
    with fc.connect(dbp) as conn:
        r = conn.execute("SELECT * FROM corpus_sessions").fetchone()
    assert r["multileg_source"] == "codes"
    assert r["label_join_rate"] == pytest.approx(0.9823)
    assert r["schema_version"] == 6


def test_default_vintage_is_v7():
    """Migration FIRED 2026-07-28 (Addendum E executed under Addendum F); v7 is
    the 1.25 clause-1 spread fence (c9698b1, registered disposition 2026-07-29).
    The default import must be the current labeled vintage — regressing this
    silently would re-roll new sessions at a stale vintage into the corpus."""
    fc = importlib.reload(db.flow_corpus)
    assert fc.SCHEMA_VERSION == 7 and fc.LABELED
    assert "cond" in fc._ROLLUP_COLUMNS
