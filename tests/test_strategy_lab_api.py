"""Read-only Strategy Lab artifact API contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _sleeve(label: str = "Sleeve one") -> dict:
    return {
        "id": "sleeve_one",
        "label": label,
        "generated": "2026-07-16",
        "config": {"n_trades": 12},
        "caveats": [
            "Backtest, not live performance",
            "Linear-accrual marking — live drawdowns will run deeper",
        ],
        "metrics": {"cagr": 0.12, "max_drawdown": 0.2},
        "equity": [["2025-01-02", 100000.0], ["2025-01-03", 100500.0]],
    }


def _benchmarks() -> dict:
    return {
        "generated": "2026-07-16",
        "series": {
            "SPY": [["2025-01-02", 100000.0]],
            "IWR": [["2025-01-02", 100000.0]],
        },
    }


@pytest.fixture
def strategy_lab_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    artifact_dir = tmp_path / "strategy_lab"
    artifact_dir.mkdir()
    (artifact_dir / "sleeve_one.json").write_text(json.dumps(_sleeve()), encoding="utf-8")
    (artifact_dir / "benchmarks.json").write_text(json.dumps(_benchmarks()), encoding="utf-8")
    monkeypatch.setattr(
        "api.strategy_lab_routes.STRATEGY_LAB_ARTIFACTS_DIR",
        artifact_dir,
    )

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def test_catalog_returns_summaries_and_benchmark_ids(
    strategy_lab_client: TestClient,
) -> None:
    response = strategy_lab_client.get("/api/strategy-lab")

    assert response.status_code == 200
    body = response.json()
    assert body["benchmarks"] == ["SPY", "IWR"]
    assert len(body["sleeves"]) == 1
    assert body["sleeves"][0]["id"] == "sleeve_one"
    assert body["sleeves"][0]["config"] == {"n_trades": 12}
    assert "equity" not in body["sleeves"][0]


def test_benchmarks_route_is_verbatim(strategy_lab_client: TestClient) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/benchmarks")

    assert response.status_code == 200
    assert response.json() == _benchmarks()


def test_sleeve_route_is_full_and_preserves_caveats(
    strategy_lab_client: TestClient,
) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/sleeves/sleeve_one")

    assert response.status_code == 200
    assert response.json() == _sleeve()
    assert response.json()["caveats"] == _sleeve()["caveats"]


def test_unknown_sleeve_is_404(strategy_lab_client: TestClient) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/sleeves/unknown")

    assert response.status_code == 404


def test_absent_artifact_directory_is_capability_not_crash(
    strategy_lab_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.strategy_lab_routes.STRATEGY_LAB_ARTIFACTS_DIR",
        tmp_path / "absent",
    )

    response = strategy_lab_client.get("/api/strategy-lab")

    assert response.status_code == 200
    assert response.json() == {
        "sleeves": [],
        "benchmarks": [],
        "capability": "unavailable",
        "error": "artifact_directory_absent",
    }


def test_mtime_cache_refreshes_changed_artifact(
    strategy_lab_client: TestClient,
    tmp_path: Path,
) -> None:
    first = strategy_lab_client.get("/api/strategy-lab/sleeves/sleeve_one")
    assert first.json()["label"] == "Sleeve one"
    artifact_path = tmp_path / "strategy_lab" / "sleeve_one.json"
    artifact_path.write_text(json.dumps(_sleeve("Sleeve changed")), encoding="utf-8")

    second = strategy_lab_client.get("/api/strategy-lab/sleeves/sleeve_one")

    assert second.status_code == 200
    assert second.json()["label"] == "Sleeve changed"


def test_cohort_catalog_reports_not_built(strategy_lab_client: TestClient) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/cohorts")

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": None,
        "capability": "not_built",
        "lanes": [],
    }


def test_cohort_lane_is_404(strategy_lab_client: TestClient) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/cohorts/not-a-lane")

    assert response.status_code == 404
