"""RVOL-spike alert dedup persistence (ops MED-5).

The dedup state moved from a module-level in-memory dict to the persisted
earnings_alerts_sent key store, so a mid-day process restart no longer re-fires
already-sent RVOL alerts. Because the helpers now read the DB (no module state),
a fresh read after mark == the "post-restart" observation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from api.main import _mark_rvol_alerted, _rvol_alert_key, _should_rvol_alert


@pytest.fixture()
def _tmp_alert_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db = tmp_path / "alerts.db"
    monkeypatch.setattr("db.earnings_alerts.DEFAULT_DB_PATH", db)
    monkeypatch.setattr("db.base.DEFAULT_DB_PATH", db)
    return db


def test_rvol_key_is_symbol_and_et_date_scoped() -> None:
    assert _rvol_alert_key("AAPL", date(2026, 7, 6)) == "rvol:AAPL:2026-07-06"


def test_rvol_dedup_persists_across_restart(_tmp_alert_db: Path) -> None:
    today = date(2026, 7, 6)

    # First time this symbol is seen today → alert allowed.
    assert _should_rvol_alert("AAPL", today) is True

    _mark_rvol_alerted("AAPL", today)

    # Same day, after a "restart" (helpers hold no state — this re-reads the DB):
    # the alert is suppressed.
    assert _should_rvol_alert("AAPL", today) is False

    # A different symbol is unaffected.
    assert _should_rvol_alert("MSFT", today) is True

    # The next ET day, the same symbol is allowed again (per-day key).
    assert _should_rvol_alert("AAPL", date(2026, 7, 7)) is True
