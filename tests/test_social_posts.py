"""Tests for Reddit social post ingest, published-hour rollup, overlap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ai.perception.resources import RESOURCES, list_resources, query
from db.social_mentions import (
    get_buzz,
    init_social_mentions_tables,
    record_mentions,
    record_reddit_posts,
    user_overlap_neighbors,
)
from signals.social_buzz import (
    _enrich_reddit_post,
    _parse_reddit_atom,
    extract_symbols,
    reset_dedupe_state_for_tests,
    run_accumulator_cycle,
)
from trading.market_hours import _ET

_KNOWN = frozenset(
    {"TSLA", "AAPL", "NVDA", "MEME", "T", "PEG", "C", "BULL", "TECH", "SI", "D", "Q"}
)
# Overlap looks back 30 days from now: fixed dates aged out and broke CI (09-09).
_RECENT_DAY = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
_RECENT_AT = f"{_RECENT_DAY}T12:00:00+00:00"
_RECENT_AT_5 = f"{_RECENT_DAY}T12:05:00+00:00"
_RECENT_BUCKET = f"{_RECENT_DAY}T08"


@pytest.fixture
def social_db(tmp_path: Path) -> Path:
    db = tmp_path / "social.db"
    init_social_mentions_tables(db)
    return db


def _atom_entry(
    *,
    entry_id: str,
    title: str,
    content: str = "",
    author: str = "/u/tester",
    published: str = "2026-08-18T00:00:59+00:00",
    link: str = "https://www.reddit.com/r/wallstreetbets/comments/abc/",
) -> str:
    import html as _html

    # Reddit escapes HTML inside Atom <content>; raw tags would break the XML.
    escaped = _html.escape(content) if content else ""
    return (
        f"<entry>"
        f"<author><name>{author}</name></author>"
        f"<id>{entry_id}</id>"
        f"<published>{published}</published>"
        f'<link href="{link}"/>'
        f"<title>{title}</title>"
        f'<content type="html">{escaped}</content>'
        f"</entry>"
    )


def _feed(*entries: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        + "".join(entries)
        + "</feed>"
    )


def test_parse_reddit_atom_captures_author_published_link() -> None:
    xml = _feed(
        _atom_entry(
            entry_id="t3_1vr9752",
            title="$TSLA breakout",
            content="<p>Buying more</p>",
            author="/u/smellyfingernail",
            published="2026-08-18T00:00:59+00:00",
            link="https://www.reddit.com/r/wallstreetbets/comments/1vr9752/x/",
        )
    )
    parsed = _parse_reddit_atom(xml)
    assert len(parsed) == 1
    row = parsed[0]
    assert row["id"] == "t3_1vr9752"
    assert row["author"] == "/u/smellyfingernail"
    assert row["published_at"] == "2026-08-18T00:00:59+00:00"
    assert row["url"].endswith("/1vr9752/x/")
    assert "$TSLA" in row["title"]
    assert "Buying more" in row["text"]
    assert "<p>" not in row["text"]


def test_mention_lands_in_published_et_hour_not_fetch_now(social_db: Path) -> None:
    """Published UTC 14:30 -> ET 10:30 in August; fetch-now hour is different."""
    reset_dedupe_state_for_tests()
    atom = _feed(
        _atom_entry(
            entry_id="t3_pub_hour",
            title="$TSLA moon",
            published="2026-08-18T14:30:00+00:00",
        )
    )

    class FakeResponse:
        status_code = 200
        text = atom

        def raise_for_status(self) -> None:
            return None

    from db.social_mentions import et_hour_bucket as real_bucket

    def _bucket(when=None):  # noqa: ANN001
        if when is None:
            return "2026-08-18T16"
        return real_bucket(when)

    with (
        patch("signals.social_buzz.httpx.get", return_value=FakeResponse()),
        patch("signals.social_buzz.fetch_stocktwits_trending", lambda: {}),
        patch("signals.social_buzz.et_hour_bucket", side_effect=_bucket),
    ):
        run_accumulator_cycle(db_path=social_db)

    published_hour = datetime(2026, 8, 18, 10, 30, tzinfo=_ET)
    buzz = get_buzz("TSLA", db_path=social_db, now=published_hour)
    assert buzz["last_hour"] == 1
    buzz_fetch = get_buzz(
        "TSLA",
        db_path=social_db,
        now=datetime(2026, 8, 18, 16, 0, tzinfo=_ET),
    )
    assert buzz_fetch["last_hour"] == 0


def test_duplicate_post_id_is_noop_even_without_memory_dedupe(social_db: Path) -> None:
    reset_dedupe_state_for_tests()
    atom = _feed(
        _atom_entry(
            entry_id="t3_restart",
            title="$NVDA DD",
            published="2026-08-18T15:00:00+00:00",
        )
    )

    class FakeResponse:
        status_code = 200
        text = atom

        def raise_for_status(self) -> None:
            return None

    with (
        patch("signals.social_buzz.httpx.get", return_value=FakeResponse()),
        patch("signals.social_buzz.fetch_stocktwits_trending", lambda: {}),
    ):
        run_accumulator_cycle(db_path=social_db)
        reset_dedupe_state_for_tests()
        run_accumulator_cycle(db_path=social_db)

    now = datetime(2026, 8, 18, 11, 0, tzinfo=_ET)
    assert get_buzz("NVDA", db_path=social_db, now=now)["last_hour"] == 1


def test_collision_blacklist_bare_vs_cashtag() -> None:
    bare = extract_symbols("MEME T PEG C BULL TECH SI D Q noise", known=_KNOWN)
    assert bare == set()
    cash = extract_symbols("$MEME $T $PEG $C go", known=_KNOWN)
    assert cash == {"MEME", "T", "PEG", "C"}


def test_multi_ticker_increments_rollup_but_skipped_by_overlap(social_db: Path) -> None:
    posts = [
        {
            "post_id": "t3_multi",
            "author": "/u/a",
            "published_at": "2026-08-01T14:00:00+00:00",
            "bucket": "2026-08-01T10",
            "title": "$TSLA and $NVDA",
            "symbols": {"TSLA", "NVDA"},
            "n_symbols": 2,
            "has_url": 0,
            "word_count": 4,
            "dd_flag": 0,
            "subreddit": "stocks",
        }
    ]
    newly = record_reddit_posts(posts, db_path=social_db)
    assert len(newly) == 1
    assert newly[0]["n_symbols"] == 2
    record_mentions(
        {"TSLA": {"reddit": 1}, "NVDA": {"reddit": 1}},
        bucket="2026-08-01T10",
        db_path=social_db,
    )
    buzz_t = get_buzz(
        "TSLA",
        db_path=social_db,
        now=datetime(2026, 8, 1, 10, 0, tzinfo=_ET),
    )
    buzz_n = get_buzz(
        "NVDA",
        db_path=social_db,
        now=datetime(2026, 8, 1, 10, 0, tzinfo=_ET),
    )
    assert buzz_t["last_hour"] == 1 and buzz_n["last_hour"] == 1
    assert user_overlap_neighbors("TSLA", min_authors=1, db_path=social_db) == []


def test_single_ticker_overlap_frac_one(social_db: Path) -> None:
    rows = []
    for i, (author, sym) in enumerate(
        [
            ("/u/a", "TSLA"),
            ("/u/b", "TSLA"),
            ("/u/c", "TSLA"),
            ("/u/d", "TSLA"),
            ("/u/e", "TSLA"),
            ("/u/a", "NVDA"),
            ("/u/b", "NVDA"),
            ("/u/c", "NVDA"),
            ("/u/d", "NVDA"),
            ("/u/e", "NVDA"),
        ]
    ):
        rows.append(
            {
                "post_id": f"t3_ov_{i}",
                "author": author,
                "published_at": _RECENT_AT,
                "bucket": _RECENT_BUCKET,
                "title": f"${sym}",
                "symbols": {sym},
                "n_symbols": 1,
                "has_url": 0,
                "word_count": 1,
                "dd_flag": 0,
                "subreddit": "stocks",
            }
        )
    assert len(record_reddit_posts(rows, db_path=social_db)) == 10
    neighbors = user_overlap_neighbors("TSLA", min_authors=5, db_path=social_db)
    assert neighbors
    assert neighbors[0]["symbol"] == "NVDA"
    assert neighbors[0]["frac"] == pytest.approx(1.0)
    assert neighbors[0]["shared_authors"] == 5
    assert neighbors[0]["authors_of_query"] == 5


def test_overlap_below_min_authors_returns_empty(social_db: Path) -> None:
    rows = [
        {
            "post_id": "t3_few_a",
            "author": "/u/a",
            "published_at": _RECENT_AT,
            "bucket": _RECENT_BUCKET,
            "title": "$TSLA",
            "symbols": {"TSLA"},
            "n_symbols": 1,
            "has_url": 0,
            "word_count": 1,
            "dd_flag": 0,
            "subreddit": "stocks",
        },
        {
            "post_id": "t3_few_b",
            "author": "/u/a",
            "published_at": _RECENT_AT_5,
            "bucket": _RECENT_BUCKET,
            "title": "$NVDA",
            "symbols": {"NVDA"},
            "n_symbols": 1,
            "has_url": 0,
            "word_count": 1,
            "dd_flag": 0,
            "subreddit": "stocks",
        },
    ]
    record_reddit_posts(rows, db_path=social_db)
    assert user_overlap_neighbors("TSLA", min_authors=5, db_path=social_db) == []


def test_has_url_ignores_reddit_links_counts_external() -> None:
    reddit_only = _enrich_reddit_post(
        {
            "id": "t3_r",
            "author": "/u/x",
            "published_at": "2026-08-18T00:00:00+00:00",
            "url": "https://www.reddit.com/r/stocks/comments/x/",
            "title": "no external",
            "text": "see https://www.reddit.com/r/stocks/comments/x/",
        },
        subreddit="stocks",
        known=_KNOWN,
    )
    assert reddit_only["has_url"] == 0

    with_sec = _enrich_reddit_post(
        {
            "id": "t3_s",
            "author": "/u/x",
            "published_at": "2026-08-18T00:00:00+00:00",
            "url": "https://www.reddit.com/r/stocks/comments/y/",
            "title": "filing",
            "text": "read https://sec.gov/Archives/edgar/data/1/a.htm",
        },
        subreddit="stocks",
        known=_KNOWN,
    )
    assert with_sec["has_url"] == 1


def test_dd_flag_on_title_patterns() -> None:
    bracket = _enrich_reddit_post(
        {
            "id": "t3_dd1",
            "author": "/u/x",
            "published_at": "2026-08-18T00:00:00+00:00",
            "url": "",
            "title": "[DD] $NVDA thesis",
            "text": "[DD] $NVDA thesis",
        },
        subreddit="wallstreetbets",
        known=_KNOWN,
    )
    word = _enrich_reddit_post(
        {
            "id": "t3_dd2",
            "author": "/u/x",
            "published_at": "2026-08-18T00:00:00+00:00",
            "url": "",
            "title": "NVDA DD inside",
            "text": "NVDA DD inside",
        },
        subreddit="wallstreetbets",
        known=_KNOWN,
    )
    plain = _enrich_reddit_post(
        {
            "id": "t3_dd3",
            "author": "/u/x",
            "published_at": "2026-08-18T00:00:00+00:00",
            "url": "",
            "title": "NVDA looks fine",
            "text": "NVDA looks fine",
        },
        subreddit="wallstreetbets",
        known=_KNOWN,
    )
    assert bracket["dd_flag"] == 1
    assert word["dd_flag"] == 1
    assert plain["dd_flag"] == 0


@pytest.mark.asyncio
async def test_perception_social_resource_wired(social_db: Path) -> None:
    assert "social" in RESOURCES
    assert "social" in list_resources()
    desc = RESOURCES["social"]["description"].lower()
    assert "not a forecast" in desc or "not a win" in desc

    rows = [
        {
            "post_id": f"t3_perc_{i}",
            "author": f"/u/u{i}",
            "published_at": _RECENT_AT,
            "bucket": _RECENT_BUCKET,
            "title": "$TSLA",
            "symbols": {"TSLA"},
            "n_symbols": 1,
            "has_url": 0,
            "word_count": 1,
            "dd_flag": 0,
            "subreddit": "stocks",
        }
        for i in range(5)
    ] + [
        {
            "post_id": f"t3_perc_n_{i}",
            "author": f"/u/u{i}",
            "published_at": _RECENT_AT,
            "bucket": _RECENT_BUCKET,
            "title": "$NVDA",
            "symbols": {"NVDA"},
            "n_symbols": 1,
            "has_url": 0,
            "word_count": 1,
            "dd_flag": 0,
            "subreddit": "stocks",
        }
        for i in range(5)
    ]
    record_reddit_posts(rows, db_path=social_db)

    overlap = await query(
        "social",
        {"symbol": "TSLA", "overlap": True},
        db_path=social_db,
    )
    assert overlap["rows"]
    assert overlap["rows"][0]["symbol"] == "NVDA"

    empty = await query(
        "social",
        {"symbol": "AAPL", "overlap": True},
        db_path=social_db,
    )
    assert empty["rows"] == []

    with_posts = await query("social", {"symbol": "TSLA"}, db_path=social_db)
    kinds = {r.get("row_kind") for r in with_posts["rows"]}
    assert "buzz" in kinds and "post" in kinds
