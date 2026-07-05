"""Tests for social mention accumulator + baseline math."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from db.social_mentions import (
    get_buzz,
    init_social_mentions_tables,
    record_mentions,
)
from signals.social_buzz import (
    extract_symbols,
    fetch_reddit_mentions,
    reset_dedupe_state_for_tests,
    run_accumulator_cycle,
)
from trading.market_hours import _ET

_KNOWN = frozenset({"TSLA", "AAPL", "NVDA", "ALL", "IT", "GO"})


@pytest.fixture
def social_db(tmp_path):
    db = tmp_path / "social.db"
    init_social_mentions_tables(db)
    return db


def test_extract_cashtags_always_count():
    found = extract_symbols("Long $TSLA and $ALL moon", known=_KNOWN)
    assert found == {"TSLA", "ALL"}


def test_extract_bare_words_only_when_known_and_not_blacklisted():
    found = extract_symbols("TSLA ripping but ALL and IT are words", known=_KNOWN)
    assert found == {"TSLA"}


def test_extract_blacklisted_common_tickers_as_bare_words():
    found = extract_symbols("ALL IN on GO rhymes with SO but NVDA runs", known=_KNOWN)
    assert found == {"NVDA"}


def test_record_mentions_additive_upsert(social_db):
    record_mentions({"TSLA": {"reddit": 2}}, bucket="2026-07-01T10", db_path=social_db)
    record_mentions({"TSLA": {"reddit": 3}}, bucket="2026-07-01T10", db_path=social_db)

    buzz = get_buzz(
        "TSLA",
        db_path=social_db,
        now=datetime(2026, 7, 1, 10, 30, tzinfo=_ET),
    )
    assert buzz["last_hour"] == 5


def test_get_buzz_warming_when_insufficient_baseline_days(social_db):
    now = datetime(2026, 7, 15, 14, 0, tzinfo=_ET)
    for offset in (1, 2):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        record_mentions(
            {"NVDA": {"reddit": 10}},
            bucket=f"{day}T14",
            db_path=social_db,
        )
    record_mentions({"NVDA": {"reddit": 40}}, bucket="2026-07-15T14", db_path=social_db)

    buzz = get_buzz("NVDA", db_path=social_db, now=now)
    assert buzz["state"] == "warming"
    assert buzz["baseline_hour"] is None
    assert buzz["anomaly_ratio"] is None
    assert buzz["last_hour"] == 40


def test_get_buzz_baseline_mean_and_ratio(social_db):
    now = datetime(2026, 7, 15, 14, 0, tzinfo=_ET)
    for offset in range(1, 15):
        day = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        record_mentions(
            {"TSLA": {"reddit": 10, "stocktwits": 10}},
            bucket=f"{day}T14",
            db_path=social_db,
        )
    record_mentions({"TSLA": {"reddit": 100}}, bucket="2026-07-15T14", db_path=social_db)

    buzz = get_buzz("TSLA", db_path=social_db, now=now)
    assert buzz["state"] == "ready"
    assert buzz["baseline_hour"] == pytest.approx(20.0)
    assert buzz["anomaly_ratio"] == pytest.approx(5.0)
    assert buzz["today_total"] == 100


def test_run_accumulator_cycle_records_both_sources(social_db):
    reset_dedupe_state_for_tests()

    def fake_reddit() -> dict[str, int]:
        return {"AAPL": 2}

    def fake_stocktwits() -> dict[str, int]:
        return {"AAPL": 1, "MSFT": 1}

    fixed_now = datetime(2026, 7, 5, 9, 0, tzinfo=_ET)
    with (
        patch("signals.social_buzz.fetch_reddit_mentions", fake_reddit),
        patch("signals.social_buzz.fetch_stocktwits_trending", fake_stocktwits),
        patch("signals.social_buzz.et_hour_bucket", return_value="2026-07-05T09"),
    ):
        summary = run_accumulator_cycle(db_path=social_db)

    assert summary["symbols"] == 2
    buzz_aapl = get_buzz("AAPL", db_path=social_db, now=fixed_now)
    assert buzz_aapl["last_hour"] == 3
    buzz_msft = get_buzz("MSFT", db_path=social_db, now=fixed_now)
    assert buzz_msft["last_hour"] == 1


def test_run_accumulator_cycle_fail_soft_on_fetcher_error(social_db):
    reset_dedupe_state_for_tests()

    def broken_reddit() -> dict[str, int]:
        raise RuntimeError("reddit down")

    def ok_stocktwits() -> dict[str, int]:
        return {"TSLA": 4}

    fixed_now = datetime(2026, 7, 5, 11, 0, tzinfo=_ET)
    with (
        patch("signals.social_buzz.fetch_reddit_mentions", broken_reddit),
        patch("signals.social_buzz.fetch_stocktwits_trending", ok_stocktwits),
        patch("signals.social_buzz.et_hour_bucket", return_value="2026-07-05T11"),
    ):
        summary = run_accumulator_cycle(db_path=social_db)

    assert summary["stocktwits_mentions"] == 4
    buzz = get_buzz("TSLA", db_path=social_db, now=fixed_now)
    assert buzz["last_hour"] == 4


def test_dedupe_same_post_ids_across_fetch_calls():
    reset_dedupe_state_for_tests()
    atom = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><id>t3_abc123</id><title>$TSLA breakout</title></entry>"
        "</feed>"
    )

    class FakeResponse:
        status_code = 200
        text = atom

        def raise_for_status(self) -> None:
            return None

    with (
        patch("signals.social_buzz.httpx.get", return_value=FakeResponse()),
        patch("signals.social_buzz.time.sleep", lambda _s: None),
    ):
        first = fetch_reddit_mentions()
        second = fetch_reddit_mentions()

    assert first == {"TSLA": 1}
    assert second == {}


def test_dedupe_across_two_accumulator_cycles(social_db):
    reset_dedupe_state_for_tests()
    atom = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><id>t3_dedupe1</id><title>$TSLA to the moon</title></entry>"
        "</feed>"
    )

    class FakeResponse:
        status_code = 200
        text = atom

        def raise_for_status(self) -> None:
            return None

    fixed_now = datetime(2026, 7, 5, 12, 0, tzinfo=_ET)
    with (
        patch("signals.social_buzz.httpx.get", return_value=FakeResponse()),
        patch("signals.social_buzz.time.sleep", lambda _s: None),
        patch("signals.social_buzz.fetch_stocktwits_trending", lambda: {}),
        patch("signals.social_buzz.et_hour_bucket", return_value="2026-07-05T12"),
    ):
        run_accumulator_cycle(db_path=social_db)
        run_accumulator_cycle(db_path=social_db)

    buzz = get_buzz("TSLA", db_path=social_db, now=fixed_now)
    assert buzz["last_hour"] == 1


def test_parse_reddit_atom_extracts_ids_and_text() -> None:
    """RSS path (Reddit 403-blocks .json): Atom entries -> (id, tag-stripped text)."""
    from signals.social_buzz import _parse_reddit_atom

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><id>t3_abc</id><title>$TSLA to the moon</title>"
        "<content>&lt;p&gt;Buying more NVDA today&lt;/p&gt;</content></entry>"
        "<entry><id>t3_def</id><title>Market thread</title></entry>"
        "<entry><title>no id, skipped</title></entry>"
        "</feed>"
    )
    parsed = _parse_reddit_atom(xml)
    assert [p[0] for p in parsed] == ["t3_abc", "t3_def"]
    assert "$TSLA" in parsed[0][1] and "NVDA" in parsed[0][1]
    assert "<p>" not in parsed[0][1]
    assert _parse_reddit_atom("not xml at all") == []
