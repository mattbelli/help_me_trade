"""FRED regime signals — market_regime, freshness guard, fetch parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.regime import upsert_regime
from signals import regime as regime_mod
from signals.regime import fetch_fred, market_regime, regime_is_fresh


def _seed_regime(db: Path) -> None:
    upsert_regime(
        [
            ("DGS10", "2026-06-24", 4.41),
            ("DGS2", "2026-06-24", 4.10),
            ("VIXCLS", "2026-06-24", 18.92),
            ("DGS10", "2026-06-23", 4.40),
            ("DGS2", "2026-06-23", 4.09),
            ("VIXCLS", "2026-06-23", 18.50),
        ],
        db_path=db,
    )


def test_market_regime_computes_spread_and_vix(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    _seed_regime(db)
    out = market_regime(db_path=db)
    assert out is not None
    assert out["as_of"] == "2026-06-24"
    assert out["dgs10"] == pytest.approx(4.41)
    assert out["dgs2"] == pytest.approx(4.10)
    assert out["term_spread"] == pytest.approx(0.31)
    assert out["vix"] == pytest.approx(18.92)


def test_market_regime_respects_as_of(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    _seed_regime(db)
    out = market_regime(as_of="2026-06-23", db_path=db)
    assert out is not None
    assert out["as_of"] == "2026-06-23"
    assert out["dgs10"] == pytest.approx(4.40)
    assert out["term_spread"] == pytest.approx(0.31)


def test_market_regime_none_when_series_missing(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    upsert_regime([("DGS10", "2026-06-24", 4.4)], db_path=db)
    assert market_regime(db_path=db) is None


def test_regime_is_fresh_false_when_stale(tmp_path: Path) -> None:
    db = tmp_path / "regime.db"
    _seed_regime(db)
    assert regime_is_fresh(max_age_days=5, db_path=db, now=date(2026, 6, 24))
    assert not regime_is_fresh(max_age_days=5, db_path=db, now=date(2026, 6, 30))
    assert not regime_is_fresh(db_path=tmp_path / "empty.db")


def test_fetch_fred_skips_dot_values(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "observations": [
            {"date": "2026-06-25", "value": "4.4"},
            {"date": "2026-06-24", "value": "."},
            {"date": "2026-06-23", "value": ""},
            {"date": "2026-06-22", "value": "4.41"},
        ]
    }
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    monkeypatch.setattr(regime_mod.httpx, "get", lambda *a, **k: resp)

    rows = fetch_fred("DGS10", api_key="test-key")
    assert rows == [("2026-06-25", 4.4), ("2026-06-22", 4.41)]


@pytest.mark.asyncio
async def test_refresh_regime_macro_without_key_skips_keyed_series(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No FRED key ⇒ the keyed series are skipped, but vix3m (keyless CSV) still
    refreshes — the term_flip trigger's data dependency must not need an API key."""
    keyed_calls: list[str] = []
    monkeypatch.setattr(regime_mod, "fred_is_configured", lambda: False)
    monkeypatch.setattr(
        regime_mod,
        "fetch_fred",
        lambda series_id, **kw: keyed_calls.append(series_id) or [],
    )
    monkeypatch.setattr(
        regime_mod, "fetch_fred_csv", lambda series_id, **kw: [("2026-06-25", 24.5)]
    )
    db = tmp_path / "regime.db"
    n = await regime_mod.refresh_regime_macro(db_path=db)
    assert n == 1
    assert keyed_calls == []
    from db.regime import latest_observation

    assert latest_observation("vix3m", db_path=db) == ("2026-06-25", 24.5)
