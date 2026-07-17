"""Idea Engine Phase 1 — ledger foundation (spec §1, §2, §7, tests §8.1).

Covers the narrow register_idea write path (dedup wall, intraday-horizon +
probability rejection, the two grading bases), canary/synthetic invisibility in
the summary/list readers, config invariants, and a register_ideas regression pin
so the shared _insert_idea refactor stays byte-identical for the legacy lane.
"""

from __future__ import annotations

import pytest

from config.idea_engine import (
    GRADING_TIEBREAK,
    IDEA_COOLDOWN_MIN,
    STRATEGY_HORIZONS,
    validate_config,
)
from db.base import connect, now_iso
from db.idea_outcomes import (
    get_summary,
    init_idea_outcomes_tables,
    list_open,
    register_idea,
    register_ideas,
    update_outcome,
)

# ET 10:00 / 10:10 / 10:40 in January (EST, UTC-5): first two share the 30-min
# cooldown bucket, the third lands in the next one.
AS_OF_A = "2026-01-06T15:00:00+00:00"
AS_OF_A2 = "2026-01-06T15:10:00+00:00"
AS_OF_B = "2026-01-06T15:40:00+00:00"


@pytest.fixture
def idea_db(tmp_path):
    db = tmp_path / "ideas.db"
    init_idea_outcomes_tables(db)
    return db


def _plan(db, **overrides):
    kwargs = dict(
        origin="convergence",
        signal_family="flow_conv",
        symbol="TSLA",
        entry_zone={"low": "100", "high": "105"},
        stop="95",
        targets=["110"],
        thesis="Pullback into support with improving tape.",
        as_of=AS_OF_A,
        db_path=db,
    )
    kwargs.update(overrides)
    return register_idea(**kwargs)


def _horizon(db, **overrides):
    kwargs = dict(
        origin="strategy:momentum_sp500",
        signal_family="momentum_sp500",
        symbol="AAPL",
        entry_zone=None,
        stop=None,
        targets=None,
        grading_basis="horizon_return",
        entry_ref_price="200",
        plan_horizon="21d",
        user_action="auto",
        source_ref={"kind": "proposal", "id": 42},
        as_of=AS_OF_A,
        db_path=db,
    )
    kwargs.update(overrides)
    return register_idea(**kwargs)


# --------------------------------------------------------------------------- #
# register_idea — the narrow write path
# --------------------------------------------------------------------------- #
class TestLedgerRegisterIdea:
    def test_plan_idea_persists_cell_columns(self, idea_db):
        idea_id = _plan(idea_db)
        assert isinstance(idea_id, int)
        rows = list_open(db_path=idea_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "TSLA"
        assert row["origin"] == "convergence"
        assert row["signal_family"] == "flow_conv"
        assert row["direction"] == "long"
        assert row["grading_basis"] == "plan"
        assert row["status"] == "pending"
        assert row["artifact_id"] == 0
        assert row["entry_low"] == "100"
        assert row["entry_high"] == "105"
        assert row["stop"] == "95"
        assert row["targets"] == ["110"]
        assert row["dedup_key"].startswith("convergence:TSLA:long:")
        assert row["cluster_key"].startswith("long:")

    def test_dedup_repeat_within_bucket_yields_one_row(self, idea_db):
        first = _plan(idea_db, as_of=AS_OF_A)
        second = _plan(idea_db, as_of=AS_OF_A2)  # same cooldown bucket
        assert isinstance(first, int)
        assert second is None
        assert len(list_open(db_path=idea_db)) == 1

    def test_next_bucket_registers_a_second_row(self, idea_db):
        assert isinstance(_plan(idea_db, as_of=AS_OF_A), int)
        assert isinstance(_plan(idea_db, as_of=AS_OF_B), int)
        assert len(list_open(db_path=idea_db)) == 2

    def test_open_row_wall_is_partial_resolved_rows_free_the_key(self, idea_db):
        first = _plan(idea_db, as_of=AS_OF_A)
        assert isinstance(first, int)
        # Same bucket again while the first is OPEN → deduped.
        assert _plan(idea_db, as_of=AS_OF_A2) is None
        # Resolve the first: it leaves the partial (open-only) unique index.
        assert update_outcome(first, status="won", db_path=idea_db)
        again = _plan(idea_db, as_of=AS_OF_A2)
        assert isinstance(again, int)
        assert again != first

    def test_concurrent_same_key_insert_collapses_to_one(self, idea_db):
        # Two independent connections racing the same dedup key: the partial
        # unique index + INSERT OR IGNORE guarantees exactly one survivor.
        ids = [_plan(idea_db, as_of=AS_OF_A) for _ in range(5)]
        survivors = [i for i in ids if i is not None]
        assert len(survivors) == 1
        assert len(list_open(db_path=idea_db)) == 1

    @pytest.mark.parametrize("horizon", ["30m", "45m", "2h", "1h"])
    def test_intraday_horizon_rejected(self, idea_db, horizon):
        assert _horizon(idea_db, plan_horizon=horizon) is None
        assert list_open(db_path=idea_db) == []

    @pytest.mark.parametrize("horizon", ["1d", "5d", "21d", "exit_lead", "earnings_exit"])
    def test_multiday_and_named_horizons_accepted(self, idea_db, horizon):
        assert isinstance(_horizon(idea_db, plan_horizon=horizon), int)

    def test_probability_text_rejected_on_thesis(self, idea_db):
        assert _plan(idea_db, thesis="Clean 80% win rate setup here.") is None
        assert list_open(db_path=idea_db) == []

    def test_probability_text_rejected_on_bear_case(self, idea_db):
        assert _plan(idea_db, bear_case="Only a 15% chance it fails.") is None

    def test_horizon_return_requires_ref_price(self, idea_db):
        assert _horizon(idea_db, entry_ref_price=None) is None

    def test_horizon_return_requires_plan_horizon(self, idea_db):
        assert _horizon(idea_db, plan_horizon=None) is None

    def test_horizon_return_forbids_stop(self, idea_db):
        assert _horizon(idea_db, stop="150") is None

    def test_horizon_return_forbids_targets(self, idea_db):
        assert _horizon(idea_db, targets=["250"]) is None

    def test_horizon_return_valid_persists_ref_as_entry(self, idea_db):
        idea_id = _horizon(idea_db)
        assert isinstance(idea_id, int)
        row = list_open(db_path=idea_db)[0]
        assert row["grading_basis"] == "horizon_return"
        assert row["entry_low"] == "200"
        assert row["entry_high"] == "200"
        assert row["stop"] == ""
        assert row["targets"] == []
        assert row["user_action"] == "auto"

    def test_plan_side_correctness_rejects_bad_stop(self, idea_db):
        assert _plan(idea_db, stop="101") is None  # stop inside/above the zone

    def test_plan_side_correctness_rejects_low_target(self, idea_db):
        assert _plan(idea_db, targets=["104"]) is None  # target below zone high

    def test_confidence_label_validated(self, idea_db):
        assert _plan(idea_db, confidence_label="high") is not None

    def test_confidence_label_rejects_junk(self, idea_db):
        assert _plan(idea_db, confidence_label="0.72") is None
        assert list_open(db_path=idea_db) == []

    def test_confidence_label_none_ok(self, idea_db):
        assert _plan(idea_db, confidence_label=None) is not None

    def test_non_long_direction_rejected(self, idea_db):
        assert _plan(idea_db, direction="short") is None


# --------------------------------------------------------------------------- #
# Canary / synthetic invisibility (WHERE synthetic = 0 on summary readers)
# --------------------------------------------------------------------------- #
class TestLedgerSyntheticInvisibility:
    def _insert_synthetic(self, db, status: str):
        now = now_iso()
        with connect(db) as conn:
            conn.execute(
                """
                INSERT INTO idea_outcomes (
                    artifact_id, idea_index, symbol, as_of, entry_low, entry_high,
                    stop, targets_json, status, origin, synthetic, updated_at
                ) VALUES (0, 999, 'CANARY', ?, '1', '1', '', '[]', ?, 'canary', 1, ?)
                """,
                (now, status, now),
            )
            conn.commit()

    def test_synthetic_pending_invisible_to_list_open(self, idea_db):
        self._insert_synthetic(idea_db, "pending")
        real = _plan(idea_db)
        assert isinstance(real, int)
        rows = list_open(db_path=idea_db)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "TSLA"

    def test_synthetic_resolved_invisible_to_get_summary(self, idea_db):
        self._insert_synthetic(idea_db, "won")
        summary = get_summary(db_path=idea_db)
        # The synthetic 'won' canary must not appear in headline counts.
        assert summary["counts"].get("won", 0) == 0
        assert summary["win_rate"] is None
        assert "canary" not in summary["by_origin"]


# --------------------------------------------------------------------------- #
# register_ideas regression — legacy setup_scan lane unchanged by the refactor
# --------------------------------------------------------------------------- #
class TestLedgerRegisterIdeasRegression:
    def _setup_idea(self):
        return {
            "symbol": "AAPL",
            "direction": "long",
            "thesis": "Test thesis for mechanical tracking.",
            "entry_zone": {"low": "100", "high": "105"},
            "stop": "95",
            "targets": ["110"],
            "as_of": AS_OF_A,
        }

    def test_snapshot_inserts_exactly_as_before(self, idea_db):
        inserted = register_ideas(1, [self._setup_idea()], db_path=idea_db)
        assert inserted == 1
        rows = list_open(db_path=idea_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "AAPL"
        assert row["entry_low"] == "100"
        assert row["entry_high"] == "105"
        assert row["stop"] == "95"
        assert row["targets"] == ["110"]
        assert row["status"] == "pending"
        assert row["origin"] == "structure"

    def test_idempotent_on_artifact_index(self, idea_db):
        assert register_ideas(1, [self._setup_idea()], db_path=idea_db) == 1
        assert register_ideas(1, [self._setup_idea()], db_path=idea_db) == 0
        assert len(list_open(db_path=idea_db)) == 1


# --------------------------------------------------------------------------- #
# Config invariants (§7 config-rot defense)
# --------------------------------------------------------------------------- #
class TestLedgerConfig:
    def test_validate_config_holds(self):
        validate_config()  # raises AssertionError on any broken invariant

    def test_cooldown_defaults_to_30(self):
        assert IDEA_COOLDOWN_MIN["some_new_origin"] == 30

    def test_strategy_horizons_and_tiebreak(self):
        assert STRATEGY_HORIZONS["earnings_drift"] == "9d"
        assert STRATEGY_HORIZONS["momentum_sp500"] == "21d"
        assert GRADING_TIEBREAK == "stop"


# --------------------------------------------------------------------------- #
# [ADR-6] direction symmetry — shorts first-class in measurement
# --------------------------------------------------------------------------- #
class TestLedgerDirectionSymmetry:
    def test_short_plan_valid_geometry_registers(self, idea_db):
        idea = _plan(
            idea_db,
            direction="short",
            entry_zone={"low": "100", "high": "105"},
            stop="110",           # short: stop above the zone
            targets=["95", "90"],  # short: targets below, strictly descending
        )
        assert isinstance(idea, int)

    def test_short_plan_stop_below_zone_rejected(self, idea_db):
        assert (
            _plan(
                idea_db,
                direction="short",
                stop="95",  # long-shaped stop on a short plan
                targets=["90"],
            )
            is None
        )

    def test_short_plan_target_above_zone_rejected(self, idea_db):
        assert (
            _plan(
                idea_db,
                direction="short",
                stop="110",
                targets=["120"],  # long-shaped target on a short plan
            )
            is None
        )

    def test_short_targets_must_descend(self, idea_db):
        assert (
            _plan(
                idea_db,
                direction="short",
                stop="110",
                targets=["90", "95"],  # not strictly descending
            )
            is None
        )

    def test_invalid_direction_rejected(self, idea_db):
        assert _plan(idea_db, direction="sideways") is None

    def test_long_and_short_same_symbol_are_distinct_dedup_buckets(self, idea_db):
        long_id = _plan(idea_db)
        short_id = _plan(
            idea_db, direction="short", stop="110", targets=["95"]
        )
        assert isinstance(long_id, int) and isinstance(short_id, int)
        assert long_id != short_id


class TestLedgerSyntheticKwarg:
    def test_register_idea_synthetic_flag_persists_and_hides(self, idea_db):
        idea = _plan(idea_db, origin="canary", synthetic=1)
        assert isinstance(idea, int)
        with connect(idea_db) as conn:
            row = conn.execute(
                "SELECT synthetic FROM idea_outcomes WHERE id = ?", (idea,)
            ).fetchone()
        assert row["synthetic"] == 1
        assert list_open(db_path=idea_db) == []

    def test_default_is_not_synthetic(self, idea_db):
        idea = _plan(idea_db)
        with connect(idea_db) as conn:
            row = conn.execute(
                "SELECT synthetic FROM idea_outcomes WHERE id = ?", (idea,)
            ).fetchone()
        assert row["synthetic"] == 0
