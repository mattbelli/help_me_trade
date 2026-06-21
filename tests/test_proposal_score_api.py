"""GET /api/proposals/score — live decision score lookup."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_proposal_score_returns_null_without_artifacts(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "signals.decision_scorer.load_artifacts",
        lambda *_args, **_kwargs: None,
    )

    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/proposals/score",
            params={"symbol": "AAPL", "side": "buy"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"score": None}
