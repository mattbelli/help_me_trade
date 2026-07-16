"""Tests for research.earnings and the /api/fundamentals/earnings-history endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from db.base import connect
from research.earnings import (
    _ensure_schema,
    _parse_earnings_from_submissions,
    _resolve_cik,
    compute_reaction_pct,
    earnings_dates,
    earnings_history_reactions,
)


@dataclass
class _Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def test_parse_earnings_from_submissions_filters_8k_item_202() -> None:
    recent = {
        "form": ["8-K", "10-Q", "8-K", "8-K", "8-K"],
        "filingDate": [
            "2024-01-25",
            "2024-02-01",
            "2024-04-30",
            "2024-07-31",
            "2024-10-30",
        ],
        "items": ["2.02", "2.02", "5.02", "2.02,9.01", "2.02"],
        "acceptanceDateTime": [
            "2024-01-25T21:30:00.000Z",
            "2024-02-01T12:00:00.000Z",
            "2024-04-30T11:05:00.000Z",
            "2024-07-31T20:31:00.000Z",
            None,
        ],
    }
    assert _parse_earnings_from_submissions(recent) == [
        ("2024-01-25", "2024-01-25T21:30:00.000Z"),
        ("2024-07-31", "2024-07-31T20:31:00.000Z"),
        ("2024-10-30", None),
    ]


def test_parse_earnings_excludes_non_8k_and_missing_item_202() -> None:
    recent = {
        "form": ["10-Q", "8-K"],
        "filingDate": ["2024-02-01", "2024-03-15"],
        "items": ["2.02", "5.02"],
    }
    assert _parse_earnings_from_submissions(recent) == []


def test_parse_earnings_handles_empty_and_missing_items() -> None:
    assert _parse_earnings_from_submissions({}) == []
    assert _parse_earnings_from_submissions(
        {"form": ["8-K"], "filingDate": ["2024-01-01"]}
    ) == []
    assert _parse_earnings_from_submissions(
        {"form": ["8-K"], "filingDate": ["2024-01-01"], "items": [None]}
    ) == []


def test_resolve_cik_strips_delisted_suffix() -> None:
    ticker_cik = {"FOO": "1234567"}
    assert _resolve_cik("FOO-202110", ticker_cik) == "1234567"
    assert _resolve_cik("FOO", ticker_cik) == "1234567"
    assert _resolve_cik("MISSING-202110", ticker_cik) is None


def test_earnings_dates_from_cache(tmp_path: Path) -> None:
    cache_db = tmp_path / "earnings.db"
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO earnings (symbol, cik, date) VALUES (?, ?, ?)",
            [
                ("AAPL", "0000320193", "2024-01-25"),
                ("AAPL", "0000320193", "2024-04-30"),
                ("AAPL", "0000320193", "2024-07-31"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    assert earnings_dates("AAPL", cache_db=cache_db) == [
        "2024-01-25",
        "2024-04-30",
        "2024-07-31",
    ]
    assert earnings_dates("MISSING", cache_db=cache_db) == []


# ---------------------------------------------------------------------------
# compute_reaction_pct
# ---------------------------------------------------------------------------


def _make_bars(*date_close_pairs: tuple[str, float]) -> list[_Bar]:
    return [
        _Bar(time=d, open=c, high=c, low=c, close=c, volume=1_000_000)
        for d, c in date_close_pairs
    ]


def test_compute_reaction_pct_basic() -> None:
    """Earnings on 2024-01-25; reaction = bar on 2024-01-25 vs 2024-01-24."""
    bars = _make_bars(
        ("2024-01-23", 100.0),
        ("2024-01-24", 102.0),  # bar before earnings date
        ("2024-01-25", 108.0),  # first bar on/after earnings date
        ("2024-01-26", 107.0),
    )
    result = compute_reaction_pct("2024-01-25", bars)
    # (108 - 102) / 102 * 100 = 5.882...  → rounded to 5.88
    assert result == round((108.0 - 102.0) / 102.0 * 100, 2)


def test_compute_reaction_pct_negative() -> None:
    bars = _make_bars(
        ("2024-04-29", 200.0),
        ("2024-04-30", 180.0),  # first bar on/after earnings date
    )
    result = compute_reaction_pct("2024-04-30", bars)
    assert result == round((180.0 - 200.0) / 200.0 * 100, 2)  # -10.0


def test_compute_reaction_pct_no_prior_bar() -> None:
    """Earnings date matches the very first bar — no prior bar available."""
    bars = _make_bars(("2024-01-25", 100.0), ("2024-01-26", 105.0))
    assert compute_reaction_pct("2024-01-25", bars) is None


def test_compute_reaction_pct_no_bar_on_or_after() -> None:
    """Earnings date is after all available bars."""
    bars = _make_bars(("2024-01-23", 100.0), ("2024-01-24", 102.0))
    assert compute_reaction_pct("2025-01-25", bars) is None


def test_compute_reaction_pct_uses_first_bar_on_or_after() -> None:
    """Earnings on a weekend — next trading day is Monday 2024-01-29."""
    bars = _make_bars(
        ("2024-01-25", 100.0),  # Friday before earnings
        ("2024-01-29", 110.0),  # Monday — first trading day on/after 2024-01-27
        ("2024-01-30", 109.0),
    )
    result = compute_reaction_pct("2024-01-27", bars)  # Saturday earnings date
    assert result == round((110.0 - 100.0) / 100.0 * 100, 2)  # 10.0


def test_earnings_quarter_label_uses_fiscal_quarter_with_calendar_year() -> None:
    """Regression (NVDA web): keep the company's fiscal quarter NUMBER but use the period's
    CALENDAR year — so NVDA's "Q1 fiscal 2027" (period 2026-06-30) reads as Q1'26, not the
    confusing forward "Q1'27" (raw fiscal) nor "Q2'26" (calendar quarter of the report)."""
    from api.main import _earnings_quarter_label

    assert _earnings_quarter_label(1, "2026-06-30") == "Q1'26"
    assert _earnings_quarter_label(4, "2026-03-31") == "Q4'26"
    assert _earnings_quarter_label(3, "2025-12-31") == "Q3'25"
    assert _earnings_quarter_label(2, "2025-09-30T00:00:00Z") == "Q2'25"  # tolerant of datetime


def test_earnings_history_reactions_order_and_limit(tmp_path: Path) -> None:
    """earnings_history_reactions returns most-recent-first, up to n."""
    cache_db = tmp_path / "earnings.db"
    conn = connect(cache_db)
    try:
        _ensure_schema(conn)
        conn.executemany(
            "INSERT INTO earnings (symbol, cik, date) VALUES (?, ?, ?)",
            [
                ("TSLA", "0001318605", "2024-01-24"),
                ("TSLA", "0001318605", "2024-04-23"),
                ("TSLA", "0001318605", "2024-07-23"),
                ("TSLA", "0001318605", "2024-10-23"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    bars = _make_bars(
        ("2024-01-23", 200.0),
        ("2024-01-24", 210.0),
        ("2024-04-22", 210.0),
        ("2024-04-23", 189.0),
        ("2024-07-22", 189.0),
        ("2024-07-23", 200.0),
        ("2024-10-22", 200.0),
        ("2024-10-23", 220.0),
    )
    results = earnings_history_reactions("TSLA", bars, n=3, cache_db=cache_db)
    # n=3 → last 3 dates: 2024-04-23, 2024-07-23, 2024-10-23; most-recent first
    assert len(results) == 3
    assert results[0]["date"] == "2024-10-23"
    assert results[1]["date"] == "2024-07-23"
    assert results[2]["date"] == "2024-04-23"
    assert results[0]["reaction_pct"] == round((220.0 - 200.0) / 200.0 * 100, 2)  # 10.0
    assert results[2]["reaction_pct"] == round((189.0 - 210.0) / 210.0 * 100, 2)  # -10.0


# ---------------------------------------------------------------------------
# afetch_past_earnings_dates — Finnhub-backed, network-free via mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afetch_past_earnings_dates_returns_past_most_recent_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns past dates for the queried symbol, most-recent-first."""
    from signals import finnhub

    fake_rows = [
        {"symbol": "AAPL", "date": "2025-01-30", "hour": "amc"},
        {"symbol": "AAPL", "date": "2024-10-31", "hour": "amc"},
        {"symbol": "AAPL", "date": "2024-08-01", "hour": "amc"},
        {"symbol": "MSFT", "date": "2025-01-29", "hour": "amc"},  # different symbol — excluded
    ]

    from unittest.mock import MagicMock

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")

    # Patch the async HTTP call to return our fake rows via _parse_calendar.
    # raise_for_status is sync on httpx responses, so use MagicMock (not AsyncMock).
    with patch("signals.finnhub._parse_calendar", return_value=fake_rows):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock(return_value=None)
            mock_resp.json = MagicMock(return_value={"earningsCalendar": []})
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await finnhub.afetch_past_earnings_dates("AAPL")

    assert result == ["2025-01-30", "2024-10-31", "2024-08-01"]


@pytest.mark.asyncio
async def test_afetch_past_earnings_dates_returns_empty_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: returns [] when API key is absent."""
    from signals import finnhub

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    result = await finnhub.afetch_past_earnings_dates("AAPL")
    assert result == []


@pytest.mark.asyncio
async def test_afetch_past_earnings_dates_returns_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: returns [] on network error."""
    import httpx

    from signals import finnhub

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_client_cls.return_value = mock_client

        result = await finnhub.afetch_past_earnings_dates("AAPL")

    assert result == []


# ---------------------------------------------------------------------------
# /api/fundamentals/earnings-history endpoint — EPS surprise (Finnhub /stock/earnings)
# ---------------------------------------------------------------------------

_FAKE_EPS_SURPRISES = [
    {
        "period": "2025-01-30",
        "year": 2025,
        "quarter": 1,
        "eps_estimate": 2.35,
        "eps_actual": 2.40,
        "surprise_pct": 2.13,
    },
    {
        "period": "2024-10-31",
        "year": 2024,
        "quarter": 4,
        "eps_estimate": 1.60,
        "eps_actual": 1.46,
        "surprise_pct": -8.75,
    },
    {
        "period": "2024-07-30",
        "year": 2024,
        "quarter": 3,
        "eps_estimate": 1.35,
        "eps_actual": 1.40,
        "surprise_pct": 3.70,
    },
]


@pytest.mark.asyncio
async def test_earnings_history_endpoint_returns_eps_surprises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint returns EPS surprise events, most-recent-first with Q-label."""
    from fastapi.testclient import TestClient

    import signals.finnhub as finnhub_mod
    from api.main import app

    monkeypatch.setattr(
        finnhub_mod,
        "afetch_eps_surprises",
        AsyncMock(return_value=_FAKE_EPS_SURPRISES),
    )

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=True) as client:
        resp = client.get("/api/fundamentals/earnings-history?symbol=AAPL&n=6")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"
    events = data["events"]
    assert len(events) == 3
    # most-recent first
    assert events[0]["period"] == "2025-01-30"
    assert events[0]["label"] == "Q1'25"
    assert events[0]["eps_actual"] == 2.40
    assert events[0]["eps_estimate"] == 2.35
    assert events[0]["surprise_pct"] == 2.13

    assert events[1]["period"] == "2024-10-31"
    assert events[1]["label"] == "Q4'24"
    assert events[1]["surprise_pct"] == -8.75

    assert events[2]["label"] == "Q3'24"
    assert events[2]["surprise_pct"] == 3.70


@pytest.mark.asyncio
async def test_earnings_history_endpoint_missing_symbol() -> None:
    """Returns 400 when symbol is absent."""
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/fundamentals/earnings-history")

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_earnings_history_endpoint_empty_on_finnhub_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns empty events list (not 500) when afetch_eps_surprises returns []."""
    from fastapi.testclient import TestClient

    import signals.finnhub as finnhub_mod
    from api.main import app

    monkeypatch.setattr(
        finnhub_mod,
        "afetch_eps_surprises",
        AsyncMock(return_value=[]),
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/fundamentals/earnings-history?symbol=TSLA")

    assert resp.status_code == 200
    assert resp.json() == {"symbol": "TSLA", "events": []}


# ---------------------------------------------------------------------------
# afetch_eps_surprises — Finnhub /stock/earnings, network-free via mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afetch_eps_surprises_parses_and_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parses Finnhub payload, reverses to most-recent-first, truncates to limit."""
    from unittest.mock import MagicMock

    from signals import finnhub

    # Finnhub returns oldest-first
    fake_payload = [
        {"period": "2024-07-30", "year": 2024, "quarter": 3,
         "estimate": 1.35, "actual": 1.40, "surprisePercent": 3.70},
        {"period": "2024-10-31", "year": 2024, "quarter": 4,
         "estimate": 1.60, "actual": 1.46, "surprisePercent": -8.75},
        {"period": "2025-01-30", "year": 2025, "quarter": 1,
         "estimate": 2.35, "actual": 2.40, "surprisePercent": 2.13},
    ]

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(return_value=fake_payload)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await finnhub.afetch_eps_surprises("AAPL", limit=2)

    # Should be most-recent-first, truncated to 2
    assert len(result) == 2
    assert result[0]["period"] == "2025-01-30"
    assert result[0]["year"] == 2025
    assert result[0]["quarter"] == 1
    assert result[0]["eps_actual"] == 2.40
    assert result[0]["eps_estimate"] == 2.35
    assert result[0]["surprise_pct"] == 2.13
    assert result[1]["period"] == "2024-10-31"


@pytest.mark.asyncio
async def test_afetch_eps_surprises_returns_empty_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: returns [] when API key is absent."""
    from signals import finnhub

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    result = await finnhub.afetch_eps_surprises("AAPL")
    assert result == []


@pytest.mark.asyncio
async def test_afetch_eps_surprises_returns_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-soft: returns [] on network error."""
    import httpx

    from signals import finnhub

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_client_cls.return_value = mock_client

        result = await finnhub.afetch_eps_surprises("AAPL")

    assert result == []


@pytest.mark.asyncio
async def test_afetch_eps_surprises_handles_none_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries with null estimate/actual/surprisePercent are included with None values."""
    from unittest.mock import MagicMock

    from signals import finnhub

    fake_payload = [
        {"period": "2025-01-30", "year": 2025, "quarter": 1,
         "estimate": None, "actual": 2.40, "surprisePercent": None},
    ]

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(return_value=fake_payload)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await finnhub.afetch_eps_surprises("AAPL")

    assert len(result) == 1
    assert result[0]["eps_estimate"] is None
    assert result[0]["eps_actual"] == 2.40
    assert result[0]["surprise_pct"] is None
