"""Hermetic tests for options-flow historical backfill (fake adapter, tmp db)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from db import options_flow as of_db
from signals.options_flow import backfill as bf
from signals.options_flow import constants as C

START = "2024-02-12"
# Expiry 2024-03-15 → DTE from 2024-02-12 = 32 → bucket 31-90
EXPIRY = "2024-03-15"
OCC_A = "AAPL240315C00150000"
OCC_B = "AAPL240315P00140000"
OCC_SPY = "SPY240315C00500000"


def _contract(
    symbol: str,
    *,
    strike: float = 150.0,
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
        "open_interest_date": "2026-07-09",
    }


def _bar(day: str, *, v: int, c: float) -> dict[str, Any]:
    return {
        "t": f"{day}T00:00:00Z",
        "o": c,
        "h": c,
        "l": c,
        "c": c,
        "v": v,
        "n": 1,
    }


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
        self.contracts_calls: list[str] = []

    def get_option_contracts(
        self,
        underlying: str,
        *,
        expiration_date_gte: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        del expiration_date_gte, limit
        u = underlying.strip().upper()
        self.contracts_calls.append(u)
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


# --- distribution helpers --------------------------------------------------------


def test_percentile_known_distribution() -> None:
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    stats = bf.distribution_stats(vals)
    assert stats["n"] == 10
    assert stats["mean"] == pytest.approx(5.5)
    assert stats["p50"] == pytest.approx(5.5)
    assert stats["p90"] == pytest.approx(9.1)
    assert stats["p95"] == pytest.approx(9.55)
    assert stats["p99"] == pytest.approx(9.91)
    assert stats["std"] == pytest.approx(3.02765, rel=1e-3)


def test_recommend_zscore_std_floor_is_fraction_of_median_std() -> None:
    stats = {
        ("AAA", "8-30"): {"n": 20, "mean": 100.0, "std": 10.0, "p50": 0, "p90": 0, "p95": 0, "p99": 0},
        ("BBB", "8-30"): {"n": 20, "mean": 200.0, "std": 30.0, "p50": 0, "p90": 0, "p95": 0, "p99": 0},
        ("CCC", "8-30"): {"n": 20, "mean": 50.0, "std": 50.0, "p50": 0, "p90": 0, "p95": 0, "p99": 0},
    }
    # median std = 30; fraction 0.1 → 3.0
    assert bf.recommend_zscore_std_floor(stats) == pytest.approx(3.0)


def test_compute_distributions_sums_per_day_bucket() -> None:
    rows = [
        {"underlying": "AAPL", "dte_bucket": "8-30", "date": "2024-02-12", "volume": 10},
        {"underlying": "AAPL", "dte_bucket": "8-30", "date": "2024-02-12", "volume": 20},
        {"underlying": "AAPL", "dte_bucket": "8-30", "date": "2024-02-13", "volume": 5},
        {"underlying": "AAPL", "dte_bucket": "out", "date": "2024-02-12", "volume": 999},
    ]
    dist = bf.compute_distributions(rows)
    assert ("AAPL", "8-30") in dist
    assert dist[("AAPL", "8-30")]["n"] == 2
    # daily totals: 30 and 5 → mean 17.5
    assert dist[("AAPL", "8-30")]["mean"] == pytest.approx(17.5)
    assert ("AAPL", "out") not in dist


# --- backfill --------------------------------------------------------------------


def test_backfill_builds_rows_dte_premium_skips_bad(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"

    # Bar after expiry → skip; volume 0 → skip; good bar → keep
    adapter = FakeAdapter(
        {
            "AAPL": [
                _contract(OCC_A, oi=500, right="call", strike=150.0),
                _contract(OCC_B, oi=300, right="put", strike=140.0),
            ],
        },
        {
            OCC_A: [
                _bar("2024-02-12", v=100, c=1.50),  # dte=32, bucket 31-90
                _bar("2024-02-13", v=0, c=1.50),  # skip vol 0
                _bar("2024-03-16", v=50, c=1.0),  # after expiry → skip
            ],
            OCC_B: [
                _bar("2024-02-12", v=40, c=2.0),  # premium = 40*2*100 = 8000
            ],
        },
    )
    summary = bf.run_backfill(
        adapter,
        symbols=["AAPL"],
        start=START,
        db_path=db,
        write_doc=False,
    )
    assert summary["symbols_ok"] == 1
    assert summary["daily_rows"] == 2

    rows = summary["rows"]
    by_occ = {r["occ_symbol"]: r for r in rows}
    assert OCC_A in by_occ
    assert OCC_B in by_occ

    a = by_occ[OCC_A]
    assert a["date"] == "2024-02-12"
    assert a["dte"] == 32
    assert a["dte_bucket"] == "31-90"
    assert a["right"] == "call"
    assert a["volume"] == 100
    assert a["premium_usd"] == pytest.approx(100 * 1.50 * 100)
    assert a["close_oi"] is None

    b = by_occ[OCC_B]
    assert b["right"] == "put"
    assert b["premium_usd"] == pytest.approx(8000.0)
    assert b["close_oi"] is None

    # Persisted
    bl = of_db.daily_baselines("AAPL", "31-90", as_of="2024-02-12", db_path=db)
    assert bl["n"] == 1
    assert bl["mean"] == pytest.approx(140.0)  # 100 + 40


def test_backfill_respects_contract_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "flow.db"
    monkeypatch.setattr(bf, "OPTIONS_FLOW_MAX_CONTRACTS_PER_UNDERLYING", 3)

    contracts = [
        _contract(f"AAPL240315C{i:08d}", oi=1000 - i, strike=100.0 + i)
        for i in range(10)
    ]
    bars = {
        str(c["symbol"]): [_bar("2024-02-12", v=10, c=1.0)] for c in contracts
    }
    adapter = FakeAdapter({"AAPL": contracts}, bars)
    summary = bf.run_backfill(
        adapter,
        symbols=["AAPL"],
        start=START,
        db_path=db,
        write_doc=False,
        contract_cap=3,
    )
    assert summary["daily_rows"] == 3
    requested = [occ for call in adapter.bars_calls for occ in call]
    assert len(requested) == 3
    assert set(requested) == {
        "AAPL240315C00000000",
        "AAPL240315C00000001",
        "AAPL240315C00000002",
    }


def test_backfill_fail_soft_per_symbol(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    adapter = FakeAdapter(
        {
            "BOOM": [_contract("BOOM240315C00100000", oi=400)],
            "AAPL": [_contract(OCC_A, oi=400)],
        },
        {
            OCC_A: [_bar("2024-02-12", v=25, c=1.0)],
        },
        raise_on={"BOOM"},
    )
    summary = bf.run_backfill(
        adapter,
        symbols=["BOOM", "AAPL"],
        start=START,
        db_path=db,
        write_doc=False,
    )
    assert summary["symbols_ok"] == 1
    assert summary["symbols_failed"] == 1
    assert summary["daily_rows"] == 1
    assert summary["rows"][0]["underlying"] == "AAPL"


def test_backfill_writes_doc_to_tmp_path(tmp_path: Path) -> None:
    db = tmp_path / "flow.db"
    doc = tmp_path / "research" / "options_flow" / "thresholds.md"
    adapter = FakeAdapter(
        {
            "AAPL": [_contract(OCC_A, oi=400)],
            "SPY": [_contract(OCC_SPY, oi=800, strike=500.0)],
        },
        {
            OCC_A: [
                _bar("2024-02-12", v=10, c=1.0),
                _bar("2024-02-13", v=20, c=1.0),
            ],
            OCC_SPY: [
                _bar("2024-02-12", v=100, c=2.0),
                _bar("2024-02-13", v=200, c=2.0),
            ],
        },
    )
    summary = bf.run_backfill(
        adapter,
        symbols=["AAPL", "SPY"],
        start=START,
        db_path=db,
        write_doc=True,
        doc_path=doc,
    )
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "Honesty caveats" in text
    assert "ZSCORE_STD_FLOOR" in text
    assert "survivorship" in text.lower() or "currently-listed" in text
    assert "close_oi" in text.lower() or "NULL" in text
    assert "AAPL" in text and "SPY" in text
    assert "31-90" in text
    assert summary["zscore_std_floor"] >= 0.0


def test_main_requires_symbols(capsys: pytest.CaptureFixture[str]) -> None:
    rc = bf.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--symbols" in err


def test_main_exits_without_option_bars(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class NoBars:
        pass

    import brokers.factory as factory

    monkeypatch.setattr(factory, "get_broker", lambda *a, **k: NoBars())
    rc = bf.main(["--symbols", "SPY"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "get_option_bars" in err


def test_main_runs_with_fake_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = tmp_path / "flow.db"
    doc = tmp_path / "thresholds.md"
    adapter = FakeAdapter(
        {"AAPL": [_contract(OCC_A, oi=400)]},
        {OCC_A: [_bar("2024-02-12", v=10, c=1.0)]},
    )
    import brokers.factory as factory

    monkeypatch.setattr(factory, "get_broker", lambda *a, **k: adapter)
    rc = bf.main(
        [
            "--symbols",
            "AAPL",
            "--start",
            START,
            "--db-path",
            str(db),
            "--doc-path",
            str(doc),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "backfill done" in out
    assert "ZSCORE_STD_FLOOR" in out
    assert doc.is_file()
    bl = of_db.daily_baselines("AAPL", "31-90", as_of="2024-02-12", db_path=db)
    assert bl["n"] == 1


def test_rank_prefers_nearer_expiry_when_oi_tied() -> None:
    contracts = [
        _contract("FAR", oi=100, expiry="2024-06-01"),
        _contract("NEAR", oi=100, expiry="2024-03-01"),
        _contract("MID", oi=100, expiry="2024-04-01"),
    ]
    ranked = bf._rank_contracts(contracts, cap=2)
    syms = [str(c["symbol"]) for c in ranked]
    assert syms == ["NEAR", "MID"]


def test_dte_bucket_constant_still_imported() -> None:
    # Sanity: backfill module sees the same buckets as constants.
    assert C.dte_bucket(32) == "31-90"
    assert bf.dte_bucket(32) == "31-90"
