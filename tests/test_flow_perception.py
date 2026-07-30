"""Daily unusual-vs-peers flow perception scan.

Everything runs on a synthetic corpus in tmp_path — never the real data/ tree. The
constants are chosen so all four components have EXACT hand-computable values on the
spike name, which pins both the arithmetic and the window convention (windows end ON
the scanned session), and proves the registered fence is doing its job: each name
also carries a huge multileg row that must not reach any component.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from db.earnings_calendar import (
    STATE_DATED,
    STATE_NON_FILER,
    STATE_UNKNOWN,
    set_estimated_next_earnings,
    upsert_coverage,
    upsert_earnings,
)
from db.flow_corpus import SCHEMA_VERSION, init_db, upsert_bars, upsert_rollup
from db.flow_perception import rows_for_session, upsert_rows
from signals.flow_perception import (
    DISCLAIMER,
    REGIME_EARNINGS,
    REGIME_NO_CATALYST,
    _rank_within_cells,
    earnings_phrase,
    emit_alerts,
    run_scan,
    scan_session,
)

N_NAMES = 20
N_SESSIONS = 120
SCAN_DAY = "2026-06-19"  # the weekday after the fixture's last session

# Per-session fenced (side=buy, multileg=0) premium for every name: short-dated
# 100k + long-dated 900k = 1M classified, so short_dte_share is 0.1 while quiet.
QUIET_SHORT = 100_000.0
QUIET_LONG = 900_000.0
QUIET_PRINT_MAX = 200_000.0
# The spike name's final session: 4M of short-dated premium, no long-dated, and a
# 2M largest print.
SPIKE_SHORT = 4_000_000.0
SPIKE_PRINT_MAX = 2_000_000.0
SPIKE_VOL_MULT = 8
SPIKE = "UNU"


def _sessions(n: int) -> list[str]:
    out: list[str] = []
    day = date(2026, 1, 2)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _rollup_row(
    day: str,
    sym: str,
    dte_bucket: str,
    premium: float,
    premium_max: float,
    *,
    side: str = "buy",
    multileg: int = 0,
) -> tuple:
    return (
        day, sym, "C", dte_bucket, "otm2_10", side, side, 3, 3, 30,
        premium, 1.0, 1.0, premium_max, 1.0, 1.0, multileg, "", SCHEMA_VERSION,
    )


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, list[str], list[str]]:
    """Synthetic corpus: 20 names x 120 sessions, one name unusual on the last."""
    path = tmp_path / "flow_corpus.db"
    init_db(path)
    sessions = _sessions(N_SESSIONS)
    names = [SPIKE] + [f"N{i:02d}" for i in range(N_NAMES - 1)]
    bars: list[tuple] = []
    rollup: list[tuple] = []
    for day in sessions:
        bars.append(("SPY", day, 500.0, 505.0, 495.0, 500.0, 1_000_000, "split", "t"))
    for k, sym in enumerate(names):
        price = 100.0
        # distinct dollar volumes so the liquidity quintiles spread across names
        shares = 100_000 * (k + 1)
        for day in sessions:
            spike = sym == SPIKE and day == sessions[-1]
            vol = shares * (SPIKE_VOL_MULT if spike else 1)
            bars.append((sym, day, price, price, price, price, vol, "split", "t"))
            if spike:
                rollup.append(_rollup_row(day, sym, "0-7", SPIKE_SHORT, SPIKE_PRINT_MAX))
            else:
                # mild per-name variation keeps peer ranks non-degenerate
                bump = 1.0 + k / 1000.0
                rollup.append(
                    _rollup_row(day, sym, "0-7", QUIET_SHORT * bump, QUIET_PRINT_MAX)
                )
                rollup.append(
                    _rollup_row(day, sym, "31-90", QUIET_LONG, QUIET_PRINT_MAX)
                )
            # fenced OUT: a package leg with premium that would dwarf every
            # component if the (side, multileg) fence ever stopped applying
            rollup.append(
                _rollup_row(
                    day, sym, "0-7", 90_000_000.0, 90_000_000.0, multileg=1
                )
            )
    upsert_bars(bars, path)
    upsert_rollup(rollup, path)
    return path, names, sessions


def test_rank_within_cells_fires_the_top_decile() -> None:
    """p90 inside the peer cell, and only inside it: 11 ranked values per cell."""
    import numpy as np

    values = np.array([float(v) for v in range(11)] + [999.0, np.nan])
    quints = np.array([0] * 11 + [1, 0], dtype=np.int8)
    eligible = np.ones(13, bool)
    rank, fired = _rank_within_cells(values, quints, eligible)
    # cell 0 holds 0..10 -> p90 = 9.0
    assert list(np.flatnonzero(fired[:11])) == [9, 10]
    assert rank[10] == pytest.approx(100.0)
    assert rank[5] == pytest.approx(100.0 * 6 / 11)
    # a one-name cell is its own top decile; an undefined value never fires
    assert fired[11]
    assert not fired[12]
    assert np.isnan(rank[12])


def test_components_match_hand_computed_values(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    path, _names, sessions = corpus
    rows = scan_session(
        corpus_db=path, db_path=tmp_path / "app.db", today=SCAN_DAY
    )
    assert rows, "the spike name should clear its peer cell"
    row = next(r for r in rows if r["symbol"] == SPIKE and r["window"] == 5)
    assert row["session"] == sessions[-1]
    # window = the 5 sessions ENDING on the scanned session: 4 quiet + the spike.
    # classified = 4 x 1M + 4M = 8M; short-dated = 4 x 100k + 4M = 4.4M
    assert row["short_dte_share"] == pytest.approx(0.55)
    # concentration = biggest single day / window classified = 4M / 8M
    assert row["concentration"] == pytest.approx(0.5)
    # largest print vs the trailing-60d median print: 2M / 200k
    assert row["largest_print_ratio"] == pytest.approx(10.0)
    # dollar volume vs its own 60d pre-window median: 8x by construction
    assert row["vol_max_ratio"] == pytest.approx(float(SPIKE_VOL_MULT))
    assert row["n_fired"] >= 2
    assert set(row["fired_components"].split(",")) <= {
        "short_dte_share",
        "concentration",
        "largest_print_ratio",
        "vol_max_ratio",
    }


def test_every_row_is_labelled_perception(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    """The disclaimer is the point of the surface — no row may ship without it."""
    rows = scan_session(corpus_db=corpus[0], db_path=tmp_path / "app.db", today=SCAN_DAY)
    assert rows
    for row in rows:
        assert row["disclaimer"] == DISCLAIMER
        assert "never a probability" in row["disclaimer"]
        assert row["session_age_days"] == 1  # scanned the weekday after the session
        assert row["window"] in (5, 10, 20)


def test_missing_earnings_is_flagged_not_assumed_quiet(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    rows = scan_session(corpus_db=corpus[0], db_path=tmp_path / "app.db", today=SCAN_DAY)
    row = next(r for r in rows if r["symbol"] == SPIKE)
    assert row["days_to_earnings"] is None
    assert row["earnings_coverage"] == 0  # no data != no catalyst
    assert row["earnings_state"] == STATE_UNKNOWN  # ...and neither is "never looked"
    assert row["regime"] == REGIME_NO_CATALYST


def test_a_non_filer_is_recorded_as_a_fact_not_a_hole(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    """An ETF has no earnings — that is the right answer, not missing data."""
    app_db = tmp_path / "app.db"
    upsert_coverage([{"symbol": SPIKE, "state": STATE_NON_FILER}], db_path=app_db)
    rows = scan_session(corpus_db=corpus[0], db_path=app_db, today=SCAN_DAY)
    row = next(r for r in rows if r["symbol"] == SPIKE)
    assert row["earnings_state"] == STATE_NON_FILER
    assert row["days_to_earnings"] is None


def test_an_estimated_date_drives_the_regime_but_stays_labelled(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    """The coverage widening's whole point: a projected date is good enough to tag
    the row earnings-adjacent, and never good enough to present as confirmed."""
    path, _names, sessions = corpus
    app_db = tmp_path / "app.db"
    when = date.fromisoformat(sessions[-1]) + timedelta(days=4)
    set_estimated_next_earnings(SPIKE, when.isoformat(), db_path=app_db)
    rows = scan_session(corpus_db=path, db_path=app_db, today=SCAN_DAY)
    row = next(r for r in rows if r["symbol"] == SPIKE)
    assert row["days_to_earnings"] == 4
    assert row["regime"] == REGIME_EARNINGS
    assert row["earnings_estimated"] == 1
    assert row["earnings_state"] == STATE_DATED
    assert "ESTIMATED" in earnings_phrase(row)


@pytest.mark.parametrize(
    ("offset_days", "regime"),
    [(3, REGIME_EARNINGS), (40, REGIME_NO_CATALYST)],
)
def test_regime_uses_the_ten_session_earnings_gate(
    corpus: tuple[Path, list[str], list[str]],
    tmp_path: Path,
    offset_days: int,
    regime: str,
) -> None:
    path, _names, sessions = corpus
    app_db = tmp_path / "app.db"
    when = date.fromisoformat(sessions[-1]) + timedelta(days=offset_days)
    upsert_earnings(
        [{"symbol": SPIKE, "date": when.isoformat(), "source": "test"}], db_path=app_db
    )
    rows = scan_session(corpus_db=path, db_path=app_db, today=SCAN_DAY)
    row = next(r for r in rows if r["symbol"] == SPIKE)
    assert row["days_to_earnings"] == offset_days
    assert row["earnings_coverage"] == 1
    assert row["regime"] == regime


def test_run_scan_persists_and_is_idempotent(
    corpus: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    path, _names, sessions = corpus
    app_db = tmp_path / "app.db"
    first = run_scan(corpus_db=path, db_path=app_db, today=SCAN_DAY)
    assert first["written"] == first["rows"] > 0
    second = run_scan(corpus_db=path, db_path=app_db, today=SCAN_DAY)
    assert second["rows"] == first["rows"]
    stored = rows_for_session(sessions[-1], limit=500, db_path=app_db)
    assert len(stored) == first["rows"]
    assert stored[0]["n_fired"] >= stored[-1]["n_fired"]  # most unusual first


def test_alerts_are_off_by_default_and_gated_on_the_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.settings as settings_mod

    app_db = tmp_path / "app.db"
    rows = [
        {
            "session": "2026-05-29",
            "symbol": SPIKE,
            "window": 5,
            "n_fired": 3,
            "fired_components": "short_dte_share,concentration,vol_max_ratio",
            "days_to_earnings": None,
            "earnings_coverage": 0,
            "regime": REGIME_NO_CATALYST,
            "session_age_days": 1,
        },
        {  # one fired component: never an alert, whatever the toggle says
            "session": "2026-05-29",
            "symbol": "N01",
            "window": 5,
            "n_fired": 1,
            "fired_components": "concentration",
            "days_to_earnings": 2,
            "earnings_coverage": 1,
            "regime": REGIME_EARNINGS,
            "session_age_days": 1,
        },
    ]
    assert emit_alerts(rows, db_path=app_db) == []

    monkeypatch.setattr(
        settings_mod,
        "alerts_config",
        lambda: {"max_push_per_day": 5, "flow_perception_alerts_enabled": True},
    )
    sent = emit_alerts(rows, db_path=app_db)
    assert len(sent) == 1
    assert sent[0]["kind"] == "flow_perception"
    assert SPIKE in sent[0]["title"]
    assert DISCLAIMER in sent[0]["body"]
    # a re-run of the same session updates that card, it does not file a second one
    again = emit_alerts(rows, db_path=app_db)
    assert [c["id"] for c in again] == [c["id"] for c in sent]


def test_perception_resource_reads_the_rows(tmp_path: Path) -> None:
    from ai.perception.resources import RESOURCES

    app_db = tmp_path / "app.db"
    read = RESOURCES["flow_perception"]["read"]
    assert read({}, db_path=app_db)[0]["unavailable"] is True
    upsert_rows(
        [
            {
                "session": "2026-05-29",
                "symbol": SPIKE,
                "window": 5,
                "quintile": 4,
                "n_fired": 2,
                "fired_components": "short_dte_share,vol_max_ratio",
                "regime": REGIME_NO_CATALYST,
                "earnings_coverage": 0,
                "session_age_days": 1,
                "disclaimer": DISCLAIMER,
            }
        ],
        db_path=app_db,
    )
    rows = read({"symbol": SPIKE.lower()}, db_path=app_db)
    assert rows[0]["symbol"] == SPIKE
    assert rows[0]["disclaimer"] == DISCLAIMER
    assert read({"window": "bogus"}, db_path=app_db)[0]["error"]
