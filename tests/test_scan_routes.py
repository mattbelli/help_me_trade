"""Route-level smoke for the scan screener endpoints (wiring + validation).

Confirms the scan_router is mounted on the app and its validation paths work
without touching broker data (the 400 branches short-circuit before the
universe/bars lookup).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.scan_routes as scan_routes
from api.main import app
from signals.screener import PRESETS


def test_scan_presets_route_mounted() -> None:
    resp = TestClient(app).get("/api/scan/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert "presets" in data
    assert len(data["presets"]) == len(PRESETS)


def test_scan_run_unknown_preset_400() -> None:
    resp = TestClient(app).get("/api/scan/run", params={"preset": "does-not-exist"})
    assert resp.status_code == 400


def test_scan_run_bad_limit_400() -> None:
    resp = TestClient(app).get("/api/scan/run", params={"preset": "oversold", "limit": 9999})
    assert resp.status_code == 400


def test_scan_run_unknown_universe_400() -> None:
    resp = TestClient(app).get(
        "/api/scan/run", params={"preset": "oversold", "universe": "bogus"}
    )
    assert resp.status_code == 400


# --- sp500 universe: reads the momentum_daily_bars store, NEVER the broker -----


def _store_series(base: float, step: float, n: int = 260):
    """A (dates, closes, opens) tuple in the shape load_daily_bars() returns."""
    closes = [base + step * i for i in range(n)]
    opens = closes  # store approximation is fine for the trend it encodes
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    return (dates, closes, opens)


def _mock_sp500_store(monkeypatch, store: dict) -> list[str]:
    """Point the sp500 path at ``store`` and forbid any broker call.

    Returns the recorded broker-call list so a test can assert it stayed empty.
    """
    broker_calls: list[tuple] = []

    async def _fail_broker(*args, **kwargs):  # would be the LIVE watchlist path
        broker_calls.append(args)
        return None

    monkeypatch.setattr("api.main._get_bars_cached", _fail_broker)
    monkeypatch.setattr(scan_routes, "_sp500_universe", lambda: list(store))
    monkeypatch.setattr(
        "db.momentum_bars.load_daily_bars",
        lambda symbols, **_: {s: store[s] for s in symbols if s in store},
    )
    return broker_calls


def test_scan_run_sp500_reads_store_no_broker(monkeypatch) -> None:
    store = {
        "UP": _store_series(100.0, 0.5),  # rising → overbought (RSI≈100)
        "DOWN": _store_series(230.0, -0.5),  # falling → oversold (RSI≈0)
    }
    broker_calls = _mock_sp500_store(monkeypatch, store)

    resp = TestClient(app).get(
        "/api/scan/run", params={"preset": "oversold", "universe": "sp500"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["universe"] == "sp500"
    assert data["universe_size"] == 2
    assert [r["symbol"] for r in data["results"]] == ["DOWN"]
    assert broker_calls == []  # sp500 path issued ZERO broker calls


def test_scan_run_sp500_volume_preset_honestly_empty(monkeypatch) -> None:
    store = {"UP": _store_series(100.0, 0.5), "DOWN": _store_series(230.0, -0.5)}
    _mock_sp500_store(monkeypatch, store)

    resp = TestClient(app).get(
        "/api/scan/run", params={"preset": "unusual_volume", "universe": "sp500"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0  # no volume in the store → no matches
    assert data["results"] == []
    assert "note" in data and "Watchlist" in data["note"]  # honest, not silent
