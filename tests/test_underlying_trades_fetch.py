"""Underlying-tape fetcher — pagination, timestamps and the on-disk schema.

Mocked transport only: this module must never make a live call from a test, and the
schema assertions are the contract tools/classification_lab_scorer.py reads.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from tools.classification_lab_scorer import load_underlying
from tools.underlying_trades_fetch import (
    DEFAULT_SYMBOLS,
    INDEX_ROOTS,
    fetch_day,
    fetch_trades,
    parse_trade_ts_ms,
    window_ranges,
    write_capture,
)

_ET = ZoneInfo("America/New_York")
BASE = "https://data.alpaca.markets"
DAY = date(2026, 7, 20)


def test_windows_cover_both_gauge_windows_plus_their_shoulders():
    ranges = window_ranges(DAY)
    assert [(a.hour, a.minute, b.hour, b.minute) for a, b in ranges] == [
        (9, 25, 10, 5),
        (15, 25, 16, 5),
    ]


def test_nanosecond_timestamps_parse_to_milliseconds():
    # Alpaca stamps trades with 9 fractional digits; fromisoformat caps at 6.
    assert parse_trade_ts_ms("2026-07-20T13:30:00.123456789Z") == int(
        datetime(2026, 7, 20, 9, 30, 0, 123456, tzinfo=_ET).timestamp() * 1000
    )
    assert parse_trade_ts_ms("2026-07-20T13:30:00Z") == int(
        datetime(2026, 7, 20, 9, 30, tzinfo=_ET).timestamp() * 1000
    )
    assert parse_trade_ts_ms("not-a-time") is None


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pagination_follows_next_page_token_and_keeps_every_page():
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("page_token")
        seen.append(token)
        if token is None:
            return httpx.Response(
                200,
                json={
                    "trades": [{"t": "2026-07-20T13:30:00Z", "p": 600.0}],
                    "next_page_token": "PAGE2",
                },
            )
        return httpx.Response(
            200,
            json={"trades": [{"t": "2026-07-20T13:30:01Z", "p": 600.5}], "next_page_token": None},
        )

    with _client(handler) as client:
        rows = fetch_trades(client, BASE, "SPY", *window_ranges(DAY)[0])
    # page 1 only would have kept a single trade — the 2026-06-23 stale-candle bug
    assert seen == [None, "PAGE2"]
    assert [r["price"] for r in rows] == [600.0, 600.5]
    assert all(r["sym"] == "SPY" for r in rows)


def test_request_carries_the_window_bounds_ascending_and_the_page_limit():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        assert request.url.path == "/v2/stocks/SPY/trades"
        return httpx.Response(200, json={"trades": []})

    start, end = window_ranges(DAY)[0]
    with _client(handler) as client:
        fetch_trades(client, BASE, "SPY", start, end, "sip")
    assert captured["start"] == "2026-07-20T13:25:00Z"
    assert captured["end"] == "2026-07-20T14:05:00Z"
    assert captured["sort"] == "asc"
    assert captured["feed"] == "sip"
    assert int(captured["limit"]) == 10_000


def test_zero_and_malformed_trades_are_dropped_never_imputed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "trades": [
                    {"t": "2026-07-20T13:30:00Z", "p": 600.0},
                    {"t": "2026-07-20T13:30:01Z", "p": 0},
                    {"t": "bogus", "p": 601.0},
                    {"p": 602.0},
                ]
            },
        )

    with _client(handler) as client:
        rows = fetch_trades(client, BASE, "SPY", *window_ranges(DAY)[0])
    assert [r["price"] for r in rows] == [600.0]


def test_a_non_200_raises_rather_than_writing_a_short_tape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    with _client(handler) as client, pytest.raises(RuntimeError, match="429"):
        fetch_trades(client, BASE, "SPY", *window_ranges(DAY)[0])


def test_index_roots_are_not_in_the_default_symbol_list():
    # SPX/SPXW have no equity tape; the scorer applies the registered SPY x10 proxy.
    assert not INDEX_ROOTS & set(DEFAULT_SYMBOLS)
    assert "SPY" in DEFAULT_SYMBOLS
    assert "MSFT" in DEFAULT_SYMBOLS


def test_capture_round_trips_into_the_schema_the_scorer_reads(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.split("/")[3]
        stamp = (
            "2026-07-20T13:30:00Z"
            if "13:25" in str(request.url.params["start"])
            else ("2026-07-20T19:30:00Z")
        )
        return httpx.Response(
            200, json={"trades": [{"t": stamp, "p": 600.0 if symbol == "SPY" else 500.0}]}
        )

    with _client(handler) as client:
        rows, counts = fetch_day(client, BASE, DAY, ["SPY", "QQQ"])
    assert counts == {"SPY": 2, "QQQ": 2}  # one trade per window per symbol
    assert rows == sorted(rows, key=lambda r: (r["ts_ms"], r["sym"]))

    path = write_capture(rows, DAY, tmp_path)
    assert path.name == "2026-07-20.jsonl.gz"
    with gzip.open(path, "rt") as fh:
        written = [json.loads(line) for line in fh]
    assert sorted(written[0]) == ["price", "sym", "ts_ms"]

    tape = load_underlying(tmp_path, "2026-07-20")
    assert set(tape) == {"SPY", "QQQ"}
    ts_list, prices = tape["SPY"]
    assert ts_list == sorted(ts_list)
    assert prices == [600.0, 600.0]
