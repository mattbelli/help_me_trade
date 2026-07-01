"""Route-level smoke for the scan screener endpoints (wiring + validation).

Confirms the scan_router is mounted on the app and its validation paths work
without touching broker data (the 400 branches short-circuit before the
universe/bars lookup).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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
