"""Hermetic tests for options-flow EOD scan orchestration (fake adapter, tmp db)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from db import options_flow as of_db
from signals.options_flow import constants as C
from signals.options_flow.scan import _int_oi, run_eod_scan

EVENT_DATE = "2026-07-09"
# Expiry → DTE 43 → bucket 31-90
EXPIRY = "2026-08-21"
OCC_HIT = "NVDA260821C00190000"
OCC_MISS = "NVDA260821C00200000"
OCC_NO_OI = "NVDA260821P00180000"
OCC_STR_OI = "AAPL260821C00150000"


def _contract(
    symbol: str,
    *,
    strike: float = 190.0,
    right: str = "call",
    oi: Any = 400,
    expiry: str = EXPIRY,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "strike_price": strike,
        "expiration_date": expiry,
        "type": right,
        "open_interest": oi,
        "open_interest_date": EVENT_DATE,
    }


def _bar(*, v: int, c: float) -> dict[str, Any]:
    return {"t": f"{EVENT_DATE}T00:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": v, "n": 1}


class FakeAdapter:
    """Canned get_option_contracts / get_option_bars; optional per-symbol raise."""

    def __init__(
        self,
        contracts_by_u: dict[str, list[dict]],
        bars_by_occ: dict[str, list[dict]],
        *,
        raise_on: set[str] | None = None,
    ) -> None:
        self.contracts_by_u = contracts_by_u
        self.bars_by_occ = bars_by_occ
        self.raise_on = {s.upper() for s in (raise_on or set())}
        self.bars_calls: list[list[str]] = []

    def get_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        del expiration_date_gte, limit
        u = underlying.strip().upper()
        if u in self.raise_on:
            raise RuntimeError(f"boom:{u}")
        return list(self.contracts_by_u.get(u, []))

    def get_option_bars(
        self,
        occ_symbols: list[str],
        start: str,
        end: str | None = None,
        timeframe: str = "1Day",
    ) -> dict[str, list[dict]]:
        del start, end, timeframe
        self.bars_calls.append(list(occ_symbols))
        out: dict[str, list[dict]] = {}
        for occ in occ_symbols:
            key = occ.strip().upper()
            if key in self.bars_by_occ:
                out[key] = self.bars_by_occ[key]
        return out


# --- helpers ---------------------------------------------------------------------


def test_int_oi_tolerant() -> None:
    assert _int_oi("670") == 670
    assert _int_oi(670) == 670
    assert _int_oi(670.0) == 670
    assert _int_oi(None) is None
    assert _int_oi("") is None
    assert _int_oi("  ") is None
    assert _int_oi("nope") is None


# --- scan ------------------------------------------------------------------------


def test_run_eod_scan_writes_daily_and_uv1_hit(tmp_path: Path) -> None:
    """Contract clearing UV-1 gates → daily row + unusual_volume event."""
    db = tmp_path / "flow.db"
    # volume=500, oi=400, close=0.50 → ratio 1.25, premium 25_000
    adapter = FakeAdapter(
        {
            "NVDA": [
                _contract(OCC_HIT, oi=400, strike=190.0),
                _contract(OCC_MISS, oi=500, strike=200.0),
                _contract(OCC_NO_OI, oi=None),  # skipped — unparseable OI
            ],
        },
        {
            OCC_HIT: [_bar(v=500, c=0.50)],
            OCC_MISS: [_bar(v=100, c=1.0)],  # below MIN_CONTRACTS / ratio
            # OCC_NO_OI has no bars anyway
        },
    )
    summary = run_eod_scan(
        adapter, symbols=["NVDA"], event_date=EVENT_DATE, db_path=db
    )
    assert summary["underlyings"] == 1
    assert summary["daily_rows"] == 2  # HIT + MISS; NO_OI dropped
    assert summary["events"] >= 1

    events = of_db.recent_events(symbol="NVDA", db_path=db)
    uv1 = [e for e in events if e.get("occ_symbol") == OCC_HIT]
    assert len(uv1) == 1
    assert uv1[0]["event_type"] == "unusual_volume"
    assert uv1[0]["vol_oi_ratio"] == 1.25
    assert uv1[0]["contracts"] == 500
    assert uv1[0]["direction"] == "undetermined"

    # MISS should not produce a UV-1 event
    assert not any(e.get("occ_symbol") == OCC_MISS for e in events)


def test_run_eod_scan_no_uv1_when_below_thresholds(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        {"NVDA": [_contract(OCC_HIT, oi=400)]},
        {OCC_HIT: [_bar(v=100, c=0.50)]},  # vol too low
    )
    summary = run_eod_scan(
        adapter, symbols=["NVDA"], event_date=EVENT_DATE, db_path=db
    )
    assert summary["daily_rows"] == 1
    assert summary["events"] == 0
    assert of_db.recent_events(db_path=db) == []


def test_run_eod_scan_respects_contract_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "flow.db"
    monkeypatch.setattr(C, "OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING", 3)
    # Also patch the name imported into scan module
    import signals.options_flow.scan as scan_mod

    monkeypatch.setattr(scan_mod, "OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING", 3)

    contracts = [
        _contract(f"NVDA260821C{i:08d}", oi=1000 - i, strike=100.0 + i)
        for i in range(10)
    ]
    bars = {
        str(c["symbol"]): [_bar(v=10, c=1.0)] for c in contracts
    }
    adapter = FakeAdapter({"NVDA": contracts}, bars)
    summary = run_eod_scan(
        adapter, symbols=["NVDA"], event_date=EVENT_DATE, db_path=db
    )
    assert summary["daily_rows"] == 3
    # Highest OI symbols win (oi 1000, 999, 998)
    requested = [occ for call in adapter.bars_calls for occ in call]
    assert len(requested) == 3
    assert set(requested) == {
        "NVDA260821C00000000",
        "NVDA260821C00000001",
        "NVDA260821C00000002",
    }


def test_run_eod_scan_string_oi_and_skips_none(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        {
            "AAPL": [
                _contract(OCC_STR_OI, oi="550", strike=150.0),
                _contract("AAPL260821P00140000", oi=None),
                _contract("AAPL260821P00130000", oi=""),
            ],
        },
        {
            OCC_STR_OI: [_bar(v=50, c=2.0)],
        },
    )
    summary = run_eod_scan(
        adapter, symbols=["AAPL"], event_date=EVENT_DATE, db_path=db
    )
    assert summary["daily_rows"] == 1
    # close_oi stored as int from string OI
    stats_probe = of_db.daily_baselines(
        "AAPL", "31-90", as_of=EVENT_DATE, db_path=db
    )
    assert stats_probe["n"] == 1
    assert stats_probe["mean"] == pytest.approx(50.0)


def test_run_eod_scan_fail_soft_per_underlying(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        {
            "BOOM": [_contract("BOOM260821C00100000", oi=400)],
            "NVDA": [_contract(OCC_HIT, oi=400)],
        },
        {
            OCC_HIT: [_bar(v=500, c=0.50)],
        },
        raise_on={"BOOM"},
    )
    summary = run_eod_scan(
        adapter,
        symbols=["BOOM", "NVDA"],
        event_date=EVENT_DATE,
        db_path=db,
    )
    # BOOM raised → skipped; NVDA still processed
    assert summary["underlyings"] == 1
    assert summary["daily_rows"] == 1
    events = of_db.recent_events(db_path=db)
    assert any(e.get("occ_symbol") == OCC_HIT for e in events)


def test_run_eod_scan_caps_underlyings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "flow.db"
    import signals.options_flow.scan as scan_mod

    monkeypatch.setattr(scan_mod, "OPTIONS_FLOW_MAX_UNDERLYINGS", 2)
    adapter = FakeAdapter({}, {})
    summary = run_eod_scan(
        adapter,
        symbols=["AAA", "BBB", "CCC"],
        event_date=EVENT_DATE,
        db_path=db,
    )
    assert summary["underlyings"] == 2


def test_run_eod_scan_skips_zero_volume_bars(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        {"NVDA": [_contract(OCC_HIT, oi=400)]},
        {OCC_HIT: [_bar(v=0, c=1.0)]},
    )
    summary = run_eod_scan(
        adapter, symbols=["NVDA"], event_date=EVENT_DATE, db_path=db
    )
    assert summary["daily_rows"] == 0
    assert summary["events"] == 0
