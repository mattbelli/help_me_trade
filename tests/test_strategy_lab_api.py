"""Read-only Strategy Lab artifact API contracts."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brokers.base import Account, Position
from db.base import connect
from db.managed_positions import record_open_position
from db.order_executions import create_execution_record, update_execution_status
from db.sqlite import save_snapshot
from db.strategies import init_strategy_tables, seed_default_strategies


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
    db_path = tmp_path / "strategy_lab.db"
    artifact_dir.mkdir()
    (artifact_dir / "sleeve_one.json").write_text(json.dumps(_sleeve()), encoding="utf-8")
    (artifact_dir / "benchmarks.json").write_text(json.dumps(_benchmarks()), encoding="utf-8")
    monkeypatch.setattr(
        "api.strategy_lab_routes.STRATEGY_LAB_ARTIFACTS_DIR",
        artifact_dir,
    )
    monkeypatch.setattr("api.strategy_lab_routes.STRATEGY_LAB_DB_PATH", db_path)

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _seed_paper_position(
    db_path: Path,
    *,
    symbol: str = "AAPL",
    mark: float | None = 105.5,
) -> None:
    init_strategy_tables(db_path)
    seed_default_strategies(db_path=db_path)
    create_execution_record(
        execution_id=f"exec_{symbol}",
        proposal_id=None,
        session_id=None,
        symbol=symbol,
        side="buy",
        qty=Decimal("7"),
        order_type="market",
        limit_price=None,
        execution_mode="paper",
        status="filled",
        broker="alpaca",
        account_id="acct_paper",
        db_path=db_path,
    )
    update_execution_status(
        f"exec_{symbol}",
        "filled",
        avg_fill_price="100.00",
        filled_qty="7",
        db_path=db_path,
    )
    position_id = record_open_position(
        account_id="acct_paper",
        symbol=symbol,
        strategy_id="earnings_drift",
        qty="7",
        exit_mode="propose",
        params={"exit_lead_td": 1, "max_holding_days": 15},
        entry_execution_id=f"exec_{symbol}",
        db_path=db_path,
    )
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE managed_positions SET entry_date = ? WHERE id = ?",
            ("2026-07-15T14:00:00+00:00", position_id),
        )
        conn.commit()
    if mark is not None:
        save_snapshot(
            Account(
                id="broker-account",
                status="ACTIVE",
                cash=1_000,
                buying_power=2_000,
                portfolio_value=3_000,
            ),
            [
                Position(
                    symbol=symbol,
                    qty=7,
                    side="long",
                    market_value=mark * 7,
                    unrealized_pl=(mark - 100) * 7,
                    avg_entry_price=100,
                    current_price=mark,
                )
            ],
            [],
            broker="alpaca",
            account_id="acct_paper",
            db_path=db_path,
        )


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


def test_paper_book_shape_and_unrealized_math(
    strategy_lab_client: TestClient,
    tmp_path: Path,
) -> None:
    _seed_paper_position(tmp_path / "strategy_lab.db")

    response = strategy_lab_client.get("/api/strategy-lab/paper")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["strategy_id"] == "earnings_drift"
    assert book["label"] == "Earnings Drift"
    assert book["n_positions"] == 1
    assert book["oldest_entry"] == "2026-07-15T14:00:00+00:00"
    assert book["mean_unrealized_pct"] == pytest.approx(5.5)
    assert book["planned_exit_note"] == (
        "exit 1 trading day before next earnings · 15d safety cap"
    )
    assert book["positions"][0] == {
        "symbol": "AAPL",
        "qty": "7",
        "entry_date": "2026-07-15T14:00:00+00:00",
        "entry_price": "100.00",
        "mark": 105.5,
        "unrealized_pct": pytest.approx(5.5),
        "days_held": pytest.approx(3, abs=1),
    }


def test_paper_book_missing_mark_is_fail_soft(
    strategy_lab_client: TestClient,
    tmp_path: Path,
) -> None:
    _seed_paper_position(tmp_path / "strategy_lab.db", mark=None)

    response = strategy_lab_client.get("/api/strategy-lab/paper")

    assert response.status_code == 200
    book = response.json()["books"][0]
    assert book["positions"][0]["mark"] is None
    assert book["positions"][0]["unrealized_pct"] is None
    assert book["mean_unrealized_pct"] is None


def test_paper_books_empty_without_open_positions(
    strategy_lab_client: TestClient,
) -> None:
    response = strategy_lab_client.get("/api/strategy-lab/paper")

    assert response.status_code == 200
    assert response.json() == {"books": []}
