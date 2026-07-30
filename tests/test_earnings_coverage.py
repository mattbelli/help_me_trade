"""Forward-earnings coverage: cadence estimates, the three coverage states, honesty.

The load-bearing property under test is that an ESTIMATE never launders itself
into a confirmed date — not in the store, not for the strategies that time money
off `next_earnings_date`, and not in the sentence a human reads.
"""

from __future__ import annotations

from pathlib import Path

from db.earnings_calendar import (
    STATE_DATED,
    STATE_NON_FILER,
    STATE_UNKNOWN,
    coverage_summary,
    earnings_dates_for,
    last_earnings_date,
    next_earnings,
    next_earnings_date,
    next_earnings_with_state,
    prune_withdrawn_estimates,
    set_estimated_next_earnings,
    symbols_needing_refresh,
    upsert_coverage,
    upsert_earnings,
)
from signals.earnings_edgar import (
    FALLBACK_GAP_DAYS,
    classify_coverage,
    estimate_next_earnings,
    median_gap_days,
)
from signals.flow_perception import earnings_phrase

# A textbook quarterly filer: four 8-K Item 2.02 filings ~91 days apart.
QUARTERLY = ["2025-08-01", "2025-10-30", "2026-01-29", "2026-04-30"]


# --- cadence math ---------------------------------------------------------


def test_median_gap_is_the_names_own_cadence() -> None:
    assert median_gap_days(QUARTERLY) == 91


def test_same_event_duplicates_do_not_crush_the_median() -> None:
    """A release plus a supplemental 8-K days later is ONE event, not a 4-day quarter."""
    noisy = [*QUARTERLY, "2026-05-02", "2026-01-31"]
    assert median_gap_days(noisy) == 91


def test_single_filing_has_no_median_and_falls_back() -> None:
    assert median_gap_days(["2026-04-30"]) is None
    date, gap = estimate_next_earnings(["2026-04-30"], today="2026-05-10")
    assert gap == FALLBACK_GAP_DAYS
    assert date == "2026-07-30"  # 04-30 + 91


def test_estimate_projects_one_cadence_step_past_the_last_filing() -> None:
    date, gap = estimate_next_earnings(QUARTERLY, today="2026-05-10")
    assert (date, gap) == ("2026-07-30", 91)


def test_estimate_rolls_forward_until_the_date_is_actually_future() -> None:
    """History a full cycle old must project to the NEXT cycle, never to the past."""
    date, _gap = estimate_next_earnings(QUARTERLY, today="2026-08-05")
    assert date == "2026-10-29"  # 04-30 + 2*91, not the elapsed 07-30


def test_estimate_refuses_when_the_filer_has_gone_quiet() -> None:
    """Past ~2 missed cycles the company plausibly stopped reporting — say so."""
    assert estimate_next_earnings(QUARTERLY, today="2027-01-01") is None


def test_estimate_of_nothing_is_nothing() -> None:
    assert estimate_next_earnings([], today="2026-05-10") is None
    assert estimate_next_earnings(["garbage"], today="2026-05-10") is None


# --- the three coverage states -------------------------------------------


def test_filer_with_history_is_dated() -> None:
    got = classify_coverage("320193", QUARTERLY, today="2026-05-10")
    assert got["state"] == STATE_DATED
    assert got["next_estimated_date"] == "2026-07-30"
    assert got["n_filings"] == 4
    assert got["last_filing_date"] == "2026-04-30"


def test_resolved_company_with_no_earnings_filings_is_a_non_filer() -> None:
    """The ARKG/FBT case: EDGAR knows the trust, it just never files Item 2.02.

    'No earnings' is the CORRECT answer for these — recording it is what stops
    the refresh re-fetching them every night forever.
    """
    got = classify_coverage("1579982", [], today="2026-05-10")
    assert got["state"] == STATE_NON_FILER
    assert got["next_estimated_date"] is None


def test_unmapped_ticker_is_unknown_not_a_non_filer() -> None:
    """We failed to resolve it — that is an admission about us, not a fact about it."""
    assert classify_coverage(None, [], today="2026-05-10")["state"] == STATE_UNKNOWN


def test_stale_history_is_unknown_and_keeps_what_it_learned() -> None:
    got = classify_coverage("320193", QUARTERLY, today="2027-01-01")
    assert got["state"] == STATE_UNKNOWN
    assert got["next_estimated_date"] is None
    assert got["last_filing_date"] == "2026-04-30"  # the check is still recorded


# --- store: estimates are quarantined from confirmed dates ----------------


def test_estimates_are_invisible_to_the_confirmed_readers(tmp_path: Path) -> None:
    """Every money-timing caller (earnings_drift entry, the exit engine blackout)
    reads these three — an estimate must never reach them."""
    db = tmp_path / "app.db"
    upsert_earnings([{"symbol": "ACME", "date": "2026-04-30"}], db_path=db)
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)

    assert earnings_dates_for("ACME", db_path=db) == ["2026-04-30"]
    assert next_earnings_date("ACME", "2026-05-10", db_path=db) is None
    assert last_earnings_date("ACME", "2026-08-01", db_path=db) == "2026-04-30"


def test_next_earnings_hands_back_the_estimated_flag(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    assert next_earnings("ACME", "2026-05-10", db_path=db) == ("2026-07-30", True)
    assert next_earnings(
        "ACME", "2026-05-10", include_estimated=False, db_path=db
    ) == (None, False)


def test_a_confirmed_date_beats_the_estimate(tmp_path: Path) -> None:
    """Even when the estimate lands EARLIER: somebody asserted the real one."""
    db = tmp_path / "app.db"
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    upsert_earnings([{"symbol": "ACME", "date": "2026-08-06"}], db_path=db)
    assert next_earnings("ACME", "2026-05-10", db_path=db) == ("2026-08-06", False)


def test_one_estimate_per_symbol(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    set_estimated_next_earnings("ACME", "2026-08-02", db_path=db)
    assert next_earnings("ACME", "2026-05-10", db_path=db) == ("2026-08-02", True)
    set_estimated_next_earnings("ACME", None, db_path=db)
    assert next_earnings("ACME", "2026-05-10", db_path=db) == (None, False)


def test_an_estimate_never_downgrades_a_confirmed_row(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upsert_earnings([{"symbol": "ACME", "date": "2026-07-30", "hour": "amc"}], db_path=db)
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    assert next_earnings("ACME", "2026-05-10", db_path=db) == ("2026-07-30", False)


def test_vendor_prune_leaves_our_projections_alone(tmp_path: Path) -> None:
    """The prune withdraws dates no vendor asserts anymore — it must not reach
    estimates, which no vendor ever asserted in the first place."""
    db = tmp_path / "app.db"
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    pruned = prune_withdrawn_estimates(
        ["ACME"], set(), start="2026-05-01", end="2026-12-31",
        today="2026-05-10", db_path=db,
    )
    assert pruned == 0
    assert next_earnings("ACME", "2026-05-10", db_path=db) == ("2026-07-30", True)


def test_state_lookup_separates_non_filer_from_never_looked(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upsert_coverage([{"symbol": "ARKG", "state": STATE_NON_FILER}], db_path=db)
    assert next_earnings_with_state("ARKG", "2026-05-10", db_path=db) == (
        None, False, STATE_NON_FILER,
    )
    assert next_earnings_with_state("NEVER", "2026-05-10", db_path=db) == (
        None, False, STATE_UNKNOWN,
    )
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    assert next_earnings_with_state("ACME", "2026-05-10", db_path=db) == (
        "2026-07-30", True, STATE_DATED,
    )


# --- incremental refresh selection ----------------------------------------


def test_never_checked_symbols_come_first(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upsert_coverage([{"symbol": "OLD", "state": STATE_DATED}], db_path=db)
    due = symbols_needing_refresh(
        ["OLD", "NEW"], today="2026-05-10", max_age_days=7, db_path=db
    )
    assert due[0] == "NEW"


def test_a_fresh_check_is_not_re_fetched(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upsert_coverage([{"symbol": "ACME", "state": STATE_DATED}], db_path=db)
    assert symbols_needing_refresh(
        ["ACME"], today="2026-05-10", max_age_days=7, db_path=db
    ) == []


def test_an_elapsed_estimate_is_always_re_fetched(tmp_path: Path) -> None:
    """The projected date passing is exactly when the real 8-K landed."""
    db = tmp_path / "app.db"
    upsert_coverage(
        [{"symbol": "ACME", "state": STATE_DATED, "next_estimated_date": "2026-05-09"}],
        db_path=db,
    )
    assert symbols_needing_refresh(
        ["ACME"], today="2026-05-10", max_age_days=7, db_path=db
    ) == ["ACME"]


def test_non_filers_back_off_hard(tmp_path: Path) -> None:
    """An ETF does not start filing earnings next week; don't spend a request on it."""
    db = tmp_path / "app.db"
    upsert_coverage([{"symbol": "ARKG", "state": STATE_NON_FILER}], db_path=db)
    due = symbols_needing_refresh(
        ["ARKG"], today="2026-06-10", max_age_days=7, non_filer_max_age_days=90,
        db_path=db,
    )
    assert due == []


def test_coverage_summary_counts_states_and_estimates(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    upsert_coverage(
        [
            {"symbol": "ACME", "state": STATE_DATED},
            {"symbol": "ARKG", "state": STATE_NON_FILER},
        ],
        db_path=db,
    )
    set_estimated_next_earnings("ACME", "2026-07-30", db_path=db)
    summary = coverage_summary(db_path=db)
    assert summary[STATE_DATED] == 1
    assert summary[STATE_NON_FILER] == 1
    assert summary["estimated_rows"] == 1


# --- honesty of the rendered sentence -------------------------------------


def test_an_estimated_date_is_labelled_wherever_a_human_reads_it() -> None:
    text = earnings_phrase({"days_to_earnings": 6, "earnings_estimated": 1})
    assert "ESTIMATED" in text
    assert "not a confirmed date" in text


def test_a_confirmed_date_is_stated_plainly() -> None:
    assert earnings_phrase({"days_to_earnings": 6, "earnings_estimated": 0}) == (
        "earnings in 6d"
    )


def test_a_non_filer_reads_as_a_fact_not_a_gap() -> None:
    text = earnings_phrase(
        {"days_to_earnings": None, "earnings_state": STATE_NON_FILER}
    )
    assert "files no earnings" in text


def test_an_unchecked_name_still_reads_as_missing_data() -> None:
    text = earnings_phrase(
        {"days_to_earnings": None, "earnings_state": STATE_UNKNOWN,
         "earnings_coverage": 0}
    )
    assert text == "no earnings data for this name"
