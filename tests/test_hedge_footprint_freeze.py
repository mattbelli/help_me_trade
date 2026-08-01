"""Pinned tests for the 3.3 frozen-label writer (FLOW_ROADMAP.md 3.3 SPEC v2)."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.hedge_footprint_freeze import (
    HIGH_THRESHOLD,
    balance_table,
    canonical_bytes,
    otm_pct,
    stratum_for,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data/research/hedge_footprint/equity"


class TestStratum:
    def test_registered_boundaries(self):
        # HIGH s >= +3, ANTI s <= -3, NULL strictly between — boundaries inclusive
        assert stratum_for(3.0) == "HIGH"
        assert stratum_for(-3.0) == "ANTI"
        assert stratum_for(2.999999) == "NULL"
        assert stratum_for(-2.999999) == "NULL"
        assert stratum_for(0.0) == "NULL"
        assert HIGH_THRESHOLD == 3.0  # registered constant; changing it = amendment

    def test_anti_is_not_pooled_semantics(self):
        # the ANTI stratum exists precisely so score <= -3 never lands in NULL
        assert stratum_for(-9.2) == "ANTI"


class TestOtmPct:
    def test_call_above_spot_is_otm_positive(self):
        assert otm_pct(110.0, 100.0, is_put=False) == pytest.approx(0.10)

    def test_put_below_spot_is_otm_positive(self):
        assert otm_pct(90.0, 100.0, is_put=True) == pytest.approx(0.10)

    def test_itm_is_negative_both_sides(self):
        assert otm_pct(90.0, 100.0, is_put=False) == pytest.approx(-0.10)
        assert otm_pct(110.0, 100.0, is_put=True) == pytest.approx(-0.10)

    def test_bad_spot_is_nan(self):
        import math
        assert math.isnan(otm_pct(100.0, float("nan"), is_put=False))


class TestDeterminism:
    def test_canonical_bytes_key_order_invariant(self):
        a = canonical_bytes({"b": 1, "a": [{"y": 2, "x": 3}]})
        b = canonical_bytes({"a": [{"x": 3, "y": 2}], "b": 1})
        assert a == b
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()


class TestBalanceTable:
    def _row(self, stratum_score, h_sign=1.0, is_put=False, side=1, sess="2026-01-05"):
        return {
            "symbol": "TST", "session": sess, "stratum": stratum_for(stratum_score),
            "premium": 1e6, "contracts": 5000, "d": 100.0, "abs_h": 2e5,
            "spot_measure": 25.0, "otm_pct": 0.05, "dte": 30, "m_bps": 10.0,
            "h_sign": h_sign, "is_put": is_put, "side_sign": side,
            "earnings_dates_45d": ["2026-01-07"],
        }

    def test_strata_counted_and_earnings_windows_split(self):
        rows = [self._row(5.0), self._row(0.0), self._row(-5.0)]
        bal = balance_table(rows)
        assert bal["HIGH"]["n_events"] == 1
        assert bal["NULL"]["n_events"] == 1
        assert bal["ANTI"]["n_events"] == 1
        # 01-07 is within (session, session+3d] of 01-05
        assert bal["HIGH"]["n_earnings_within_3d"] == 1
        assert bal["HIGH"]["n_earnings_within_9d"] == 1


@pytest.mark.skipif(not (DATA_DIR / "hedge_scores_frozen_3p3.json").exists(),
                    reason="frozen artifact not on this machine")
class TestFrozenArtifact:
    def test_frozen_file_is_canonical_and_strata_are_registered(self):
        raw = (DATA_DIR / "hedge_scores_frozen_3p3.json").read_bytes()
        doc = json.loads(raw)
        # the on-disk bytes ARE the canonical serialization (sha-stable)
        assert canonical_bytes(doc) == raw
        assert doc["high_threshold"] == 3.0
        assert doc["n_events"] == len(doc["events"])
        for ev in doc["events"]:
            assert ev["stratum"] == stratum_for(ev["score"])
        # deterministic order
        keys = [(e["symbol"], e["ts_ns"]) for e in doc["events"]]
        assert keys == sorted(keys)
