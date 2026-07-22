"""The HMT_TEST_LIFESPAN=minimal gate (set for the whole suite in conftest).

Under TestClient the app must do schema init + job REGISTRATION only — the
full production startup runs a real network poll inline and spawns loops
whose shutdown cancel cannot interrupt asyncio.to_thread work (the
suite-hang class). If either spy below fires, the gate has regressed and
the suite is back to hanging on CI.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as api_main
from api import jobs


def test_minimal_lifespan_registers_jobs_but_never_starts_loops(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise AssertionError("full lifespan ran under HMT_TEST_LIFESPAN=minimal")

    monkeypatch.setattr(api_main.market_hub, "start", _boom)
    monkeypatch.setattr(api_main, "_poll_once", _boom)

    with TestClient(api_main.app) as client:
        assert client.get("/health").status_code == 200

    # Registration is kept (the /health v2 registry tests depend on it).
    assert any(job.name == "poll" for job in jobs.JOBS)
