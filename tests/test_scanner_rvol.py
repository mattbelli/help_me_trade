"""Unit tests for the RVOL scanner backend.

Covers:
- _rvol_bucket_now(): bucket index from ET wall clock
- _compute_rvol_from_total_volume(): pure math with a fake profile + total_volume
- GET /api/scanner/rvol: endpoint behaviour (non-Schwab provider, off-hours, happy path)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.main import _compute_rvol_from_total_volume, _rvol_bucket_now
from signals.volume_profile import N_BUCKETS, Profile
from trading.market_hours import _ET

# ---------------------------------------------------------------------------
# _rvol_bucket_now
# ---------------------------------------------------------------------------


def _mock_now(h: int, m: int) -> datetime:
    return datetime(2026, 6, 24, h, m, 0, tzinfo=_ET)


def test_rvol_bucket_now_pre_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before 09:30 ET → None (pre-market, don't scan)."""
    monkeypatch.setattr("api.main.datetime", type("_DT", (), {"now": staticmethod(lambda tz=None: _mock_now(9, 0))})())
    result = _rvol_bucket_now()
    assert result is None


def test_rvol_bucket_now_open_first_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly 09:30 ET → bucket 0."""
    monkeypatch.setattr("api.main.datetime", type("_DT", (), {"now": staticmethod(lambda tz=None: _mock_now(9, 30))})())
    result = _rvol_bucket_now()
    assert result == 0


def test_rvol_bucket_now_midday(monkeypatch: pytest.MonkeyPatch) -> None:
    """12:30 ET = 180 minutes after open → bucket 36."""
    monkeypatch.setattr("api.main.datetime", type("_DT", (), {"now": staticmethod(lambda tz=None: _mock_now(12, 30))})())
    result = _rvol_bucket_now()
    assert result == 36  # (12*60+30 - (9*60+30)) // 5 = 180 // 5


def test_rvol_bucket_now_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """After 16:00 ET → clamped to 77."""
    monkeypatch.setattr("api.main.datetime", type("_DT", (), {"now": staticmethod(lambda tz=None: _mock_now(17, 0))})())
    result = _rvol_bucket_now()
    assert result == 77


# ---------------------------------------------------------------------------
# _compute_rvol_from_total_volume
# ---------------------------------------------------------------------------


def _uniform_profile(median_daily: float = 100_000.0) -> Profile:
    """Profile with uniform per-bucket volume (cumfrac[i] = (i+1)/78)."""
    return Profile(
        symbol="TEST",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=median_daily,
        n_days=20,
    )


def test_compute_rvol_at_expected_pace() -> None:
    """Symbol running at exactly expected pace → RVOL ≈ 1.0."""
    profile = _uniform_profile(median_daily=78_000.0)
    bucket = 38  # ~13:40 ET (bucket 38 → expected cumfrac = 39/78 = 0.5)
    expected_vol = profile.median_daily_total * profile.cumfrac[bucket]
    result = _compute_rvol_from_total_volume(profile, expected_vol, bucket)
    assert result == pytest.approx(1.0, rel=0.01)


def test_compute_rvol_double_pace() -> None:
    """Symbol running at 2x expected pace → RVOL ≈ 2.0."""
    profile = _uniform_profile(median_daily=78_000.0)
    bucket = 38
    expected_vol = profile.median_daily_total * profile.cumfrac[bucket]
    result = _compute_rvol_from_total_volume(profile, expected_vol * 2, bucket)
    assert result == pytest.approx(2.0, rel=0.01)


def test_compute_rvol_none_profile() -> None:
    """None profile → None."""
    assert _compute_rvol_from_total_volume(None, 50_000.0, 38) is None


def test_compute_rvol_bucket_out_of_range() -> None:
    """Bucket > 77 → None."""
    profile = _uniform_profile()
    assert _compute_rvol_from_total_volume(profile, 50_000.0, 78) is None
    assert _compute_rvol_from_total_volume(profile, 50_000.0, -1) is None


def test_compute_rvol_zero_expected() -> None:
    """Profile with cumfrac[0] = 0 (impossible in practice but guarded) → None."""
    profile = Profile(
        symbol="FLAT",
        cumfrac=[0.0] + [(i + 1) / (N_BUCKETS - 1) for i in range(N_BUCKETS - 1)],
        median_daily_total=100_000.0,
        n_days=10,
    )
    # cumfrac[0] = 0 → expected_vol = 0 → guard triggers
    assert _compute_rvol_from_total_volume(profile, 10_000.0, 0) is None


# ---------------------------------------------------------------------------
# /api/scanner/rvol endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client():
    """FastAPI TestClient with the app's lifespan bypassed."""
    from fastapi.testclient import TestClient

    from api.main import app

    # Bypass lifespan (DB init, background tasks) for unit tests.
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _make_schwab_provider() -> MagicMock:
    from brokers.schwab_market_data import SchwabMarketData

    provider = MagicMock(spec=SchwabMarketData)
    provider.aget_quotes = AsyncMock(return_value={})
    return provider


def test_scanner_rvol_non_schwab_provider_returns_400(
    test_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-Schwab provider → 400 with a helpful message."""
    from brokers.alpaca import AlpacaAdapter

    alpaca = MagicMock(spec=AlpacaAdapter)
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: alpaca)

    resp = test_client.get("/api/scanner/rvol")
    assert resp.status_code == 400
    assert "Schwab" in resp.json()["error"]


def test_scanner_rvol_off_hours_returns_empty(
    test_client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Outside RTH (pre-market session) → empty results with a note, not an error."""
    provider = _make_schwab_provider()
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)

    # Force session to 'pre'
    monkeypatch.setattr("api.main._market_session", lambda: "pre")
    # Force bucket to None (pre-open)
    monkeypatch.setattr("api.main._rvol_bucket_now", lambda: None)

    resp = test_client.get("/api/scanner/rvol")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"] == "pre"
    assert body["count"] == 0
    assert body["results"] == []
    assert "note" in body


def test_scanner_rvol_happy_path(
    test_client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """During RTH with profile + totalVolume → returns ranked RVOL results."""
    from signals.volume_profile import Profile, save_profile

    # Write fake profiles to a temp dir.
    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    median = 100_000.0
    cumfrac = [(i + 1) / N_BUCKETS for i in range(N_BUCKETS)]
    for sym in ("AAA", "BBB", "CCC"):
        p = Profile(symbol=sym, cumfrac=cumfrac, median_daily_total=median, n_days=20)
        save_profile(p)

    bucket = 38  # mid-day bucket
    # At bucket 38, cumfrac[38] = 39/78 = 0.5 → expected_vol = 50_000
    # AAA: total_volume = 100_000 → RVOL ≈ 2.0 (high)
    # BBB: total_volume =  60_000 → RVOL ≈ 1.2 (below default 1.5 threshold)
    # CCC: total_volume = 150_000 → RVOL ≈ 3.0 (highest)
    fake_quotes = {
        "AAA": {"regular_last": 50.0, "ext_last": None, "total_volume": 100_000},
        "BBB": {"regular_last": 30.0, "ext_last": None, "total_volume": 60_000},
        "CCC": {"regular_last": 75.0, "ext_last": None, "total_volume": 150_000},
    }

    provider = _make_schwab_provider()
    provider.aget_quotes = AsyncMock(return_value=fake_quotes)
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("api.main._market_session", lambda: "regular")
    monkeypatch.setattr("api.main._rvol_bucket_now", lambda: bucket)

    resp = test_client.get("/api/scanner/rvol?min_rvol=1.5&limit=50")
    assert resp.status_code == 200
    body = resp.json()

    assert body["session"] == "regular"
    # BBB (RVOL≈1.2) should be filtered out; AAA and CCC should be present.
    symbols_returned = [r["symbol"] for r in body["results"]]
    assert "CCC" in symbols_returned
    assert "AAA" in symbols_returned
    assert "BBB" not in symbols_returned

    # Results are sorted descending by RVOL — CCC (3.0) before AAA (2.0).
    assert symbols_returned[0] == "CCC"
    assert symbols_returned[1] == "AAA"

    assert body["results"][0]["rvol"] == pytest.approx(3.0, rel=0.02)
    assert body["results"][1]["rvol"] == pytest.approx(2.0, rel=0.02)
    assert body["count"] == 2


def test_scanner_rvol_limit_param(
    test_client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """limit=1 returns only the top symbol."""
    from signals.volume_profile import Profile, save_profile

    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))

    median = 100_000.0
    cumfrac = [(i + 1) / N_BUCKETS for i in range(N_BUCKETS)]
    for sym in ("AAA", "CCC"):
        p = Profile(symbol=sym, cumfrac=cumfrac, median_daily_total=median, n_days=20)
        save_profile(p)

    fake_quotes = {
        "AAA": {"regular_last": 50.0, "ext_last": None, "total_volume": 100_000},
        "CCC": {"regular_last": 75.0, "ext_last": None, "total_volume": 150_000},
    }
    provider = _make_schwab_provider()
    provider.aget_quotes = AsyncMock(return_value=fake_quotes)
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("api.main._market_session", lambda: "regular")
    monkeypatch.setattr("api.main._rvol_bucket_now", lambda: 38)

    resp = test_client.get("/api/scanner/rvol?min_rvol=1.5&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["symbol"] == "CCC"


def test_scanner_rvol_bad_limit(test_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """limit > 200 → 400."""
    provider = _make_schwab_provider()
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    resp = test_client.get("/api/scanner/rvol?limit=999")
    assert resp.status_code == 400


def test_scanner_rvol_provider_error_returns_502(
    test_client, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Provider BrokerError during quote fetch → 502."""
    from brokers.errors import BrokerError
    from signals.volume_profile import Profile, save_profile

    monkeypatch.setenv("VOLUME_PROFILE_DIR", str(tmp_path))
    p = Profile(
        symbol="AAA",
        cumfrac=[(i + 1) / N_BUCKETS for i in range(N_BUCKETS)],
        median_daily_total=100_000.0,
        n_days=20,
    )
    save_profile(p)

    provider = _make_schwab_provider()
    provider.aget_quotes = AsyncMock(side_effect=BrokerError("timeout"))
    monkeypatch.setattr("api.main.get_market_data_provider", lambda: provider)
    monkeypatch.setattr("api.main._market_session", lambda: "regular")
    monkeypatch.setattr("api.main._rvol_bucket_now", lambda: 38)

    resp = test_client.get("/api/scanner/rvol")
    assert resp.status_code == 502
    assert "timeout" in resp.json()["error"]
