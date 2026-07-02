"""Tests for the Finnhub company-info fetchers (profile · metrics · news) + cache.

All three are fail-soft: a missing key or a failed request returns a safe empty
value and never raises, so the company-info modal degrades gracefully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signals import finnhub


def _mock_client(payload):
    """A patched httpx.AsyncClient whose .get returns a response yielding ``payload``."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(return_value=None)
    mock_resp.json = MagicMock(return_value=payload)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.fixture(autouse=True)
def _clear_cache():
    finnhub._company_cache.clear()
    yield
    finnhub._company_cache.clear()


# --- profile -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afetch_company_profile_trims_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    payload = {
        "name": "Apple Inc",
        "ticker": "AAPL",
        "exchange": "NASDAQ",
        "finnhubIndustry": "Technology",
        "marketCapitalization": 3_000_000.0,
        "logo": "https://logo",
        "weburl": "https://apple.com",
        "ipo": "1980-12-12",
        "shareOutstanding": 15_000.0,
        "country": "US",
        "currency": "USD",
        "phone": "1-408-996-1010",  # not in the curated set → dropped
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)) as cls:
        result = await finnhub.afetch_company_profile("aapl")
        # Second call is served from cache — no second HTTP client constructed.
        again = await finnhub.afetch_company_profile("AAPL")

    assert result["name"] == "Apple Inc"
    assert result["weburl"] == "https://apple.com"
    assert "phone" not in result  # trimmed to the curated key set
    assert again == result
    assert cls.call_count == 1  # cached on the second call


@pytest.mark.asyncio
async def test_afetch_company_profile_empty_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    assert await finnhub.afetch_company_profile("AAPL") == {}


@pytest.mark.asyncio
async def test_afetch_company_profile_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert await finnhub.afetch_company_profile("AAPL") == {}


# --- metrics -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afetch_company_metrics_curates_flat_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    payload = {
        "metric": {
            "peTTM": 28.5,
            "52WeekHigh": 199.6,
            "52WeekLow": 124.1,
            "beta": 1.24,
            "dividendYieldIndicatedAnnual": 0.55,
            "grossMarginTTM": 44.1,
            "netProfitMarginTTM": 25.3,
            "marketCapitalization": 3_000_000.0,
            "epsTTM": 6.4,
            "irrelevant": "ignored",
        }
    }
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)):
        result = await finnhub.afetch_company_metrics("AAPL")

    assert result["pe_ttm"] == 28.5
    assert result["week_52_high"] == 199.6
    assert result["week_52_low"] == 124.1
    assert result["beta"] == 1.24
    assert result["div_yield"] == 0.55
    assert result["net_margin"] == 25.3
    assert result["market_cap"] == 3_000_000.0
    assert "irrelevant" not in result


@pytest.mark.asyncio
async def test_afetch_company_metrics_first_non_null_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """peTTM absent → falls back to peBasicExclExtraTTM."""
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    payload = {"metric": {"peBasicExclExtraTTM": 31.2}}
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)):
        result = await finnhub.afetch_company_metrics("AAPL")
    assert result["pe_ttm"] == 31.2


@pytest.mark.asyncio
async def test_afetch_company_metrics_empty_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    assert await finnhub.afetch_company_metrics("AAPL") == {}


# --- news ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_afetch_company_news_parses_sorts_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    payload = [
        {
            "headline": "Older story",
            "source": "Reuters",
            "url": "https://r/1",
            "datetime": 1_700_000_000,
            "summary": "s1",
            "image": "https://img/1",
        },
        {
            "headline": "Newer story",
            "source": "Bloomberg",
            "url": "https://b/2",
            "datetime": 1_700_100_000,
            "summary": "s2",
            "image": "",
        },
        {"headline": "", "url": "https://x/3", "datetime": 1_700_200_000},  # dropped
    ]
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)):
        result = await finnhub.afetch_company_news("AAPL", limit=5)

    assert [n["headline"] for n in result] == ["Newer story", "Older story"]
    assert result[0]["datetime"].startswith("20")  # epoch → ISO
    assert result[0]["source"] == "Bloomberg"


@pytest.mark.asyncio
async def test_afetch_company_news_respects_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    payload = [
        {"headline": f"h{i}", "url": f"https://u/{i}", "datetime": 1_700_000_000 + i}
        for i in range(30)
    ]
    with patch("httpx.AsyncClient", return_value=_mock_client(payload)):
        result = await finnhub.afetch_company_news("AAPL", limit=10)
    assert len(result) == 10


@pytest.mark.asyncio
async def test_afetch_company_news_empty_on_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "")
    assert await finnhub.afetch_company_news("AAPL") == []


@pytest.mark.asyncio
async def test_afetch_company_news_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    monkeypatch.setattr(finnhub, "finnhub_api_key", lambda: "fake-key")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with patch("httpx.AsyncClient", return_value=mock_client):
        assert await finnhub.afetch_company_news("AAPL") == []
