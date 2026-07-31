"""Hermetic tests for GET /api/options-chain."""

from __future__ import annotations

from datetime import datetime as real_datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from brokers.alpaca import AlpacaAdapter


@pytest.fixture(autouse=True)
def _clear_chain_cache() -> None:
    from api.main import _OPTIONS_CHAIN_CACHE

    _OPTIONS_CHAIN_CACHE.clear()
    yield
    _OPTIONS_CHAIN_CACHE.clear()


def _chain_snapshots() -> dict:
    return {
        "NVDA260717C00200000": {
            "latestQuote": {"bp": 2.1, "ap": 2.2},
            "latestTrade": {"p": 2.15},
            "impliedVolatility": 0.45,
            "greeks": {"delta": 0.55, "gamma": 0.02, "theta": -0.1, "vega": 0.08},
            "dailyBar": {"v": 500},
        },
        "NVDA260717P00200000": {
            "latestQuote": {"bp": 1.8, "ap": 1.9},
            "impliedVolatility": 0.42,
            "greeks": {"delta": -0.45},
        },
        "NVDA260821C00190000": {
            "latestQuote": {"bp": 4.2, "ap": 4.35},
            "latestTrade": {"p": 4.28},
            "impliedVolatility": 0.499,
            "greeks": {
                "delta": 0.44,
                "gamma": 0.031,
                "theta": -0.352,
                "vega": 0.118,
            },
            "dailyBar": {"v": 1234},
        },
        "NVDA260821P00190000": {
            "latestQuote": {"bp": 3.1, "ap": 3.2},
            "impliedVolatility": 0.48,
            "greeks": {"delta": -0.4},
        },
    }


def _ten_strike_chain() -> dict:
    """~10 strikes around 200 for 2026-08-21 — ATM windowing tests."""
    strikes = [180, 185, 190, 195, 200, 205, 210, 215, 220, 225]
    chain: dict = {}
    for strike in strikes:
        strike_key = f"{int(strike * 1000):08d}"
        chain[f"NVDA260821C{strike_key}"] = {
            "latestQuote": {"bp": 1.0, "ap": 1.1, "t": "2026-07-09T20:00:00Z"},
        }
        chain[f"NVDA260821P{strike_key}"] = {
            "latestQuote": {"bp": 0.9, "ap": 1.0, "t": "2026-07-09T19:00:00Z"},
        }
    # Fresher quote on the 200 strike call — should dominate as_of when windowed in.
    chain["NVDA260821C00200000"]["latestQuote"]["t"] = "2026-07-09T21:30:00Z"
    return chain


def _contracts_from_chain(chain: dict) -> list[dict]:
    """Expirations now come from contracts metadata — derive a plain list."""
    from imports.option_symbol import parse_occ_symbol

    out: list[dict] = []
    for occ in chain:
        parsed = parse_occ_symbol(occ)
        if parsed is not None:
            out.append(
                {"symbol": occ, "expiration_date": parsed.expiration.isoformat()}
            )
    return out


def _mock_alpaca(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chain: dict | None = None,
    contracts: list[dict] | None = None,
    underlying_price: float | None = 201.84,
    chain_raises: bool = False,
) -> MagicMock:
    broker = MagicMock(spec=AlpacaAdapter)
    if contracts is None:
        contracts = _contracts_from_chain(chain or {})

    if chain_raises:

        def _boom_chain(_sym: str, feed: str = "opra", **_kw: object) -> dict:
            raise RuntimeError("chain boom")

        broker.get_option_chain_snapshot = _boom_chain
    else:
        broker.get_option_chain_snapshot = MagicMock(
            return_value={} if chain is None else chain
        )

    broker.get_stock_snapshot_fields = MagicMock(
        return_value={"last_price": underlying_price}
        if underlying_price is not None
        else None
    )
    broker.get_option_contracts = MagicMock(return_value=contracts)

    monkeypatch.setattr("api.main.broker_is_configured", lambda name: name == "alpaca")
    monkeypatch.setattr("api.main.get_broker", lambda _name: broker)
    return broker


def test_options_chain_happy_path_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    contracts = [
        {
            "symbol": "NVDA260821C00190000",
            "expiration_date": "2026-08-21",
            "open_interest": "5678",
        },
        {
            "symbol": "NVDA260821P00190000",
            "expiration_date": "2026-08-21",
            "open_interest": 2100,
        },
        {"symbol": "NVDA260717C00200000", "expiration_date": "2026-07-17"},
        {"symbol": "NVDA260717P00200000", "expiration_date": "2026-07-17"},
    ]
    _mock_alpaca(monkeypatch, chain=_chain_snapshots(), contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NVDA"
    assert data["underlying_price"] == 201.84
    assert data["expiry"] == "2026-08-21"
    assert "2026-07-17" in data["expirations"]
    assert "2026-08-21" in data["expirations"]
    assert data["expirations"] == sorted(data["expirations"])
    assert "as_of" in data

    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["strike"] == 190.0
    assert row["call"]["bid"] == 4.2
    assert row["call"]["ask"] == 4.35
    assert row["call"]["iv"] == 0.499
    assert row["call"]["delta"] == 0.44
    assert row["call"]["gamma"] == 0.031
    assert row["call"]["theta"] == -0.352
    assert row["call"]["vega"] == 0.118
    assert row["call"]["volume"] == 1234
    assert row["call"]["oi"] == 5678
    assert row["call"]["last"] == 4.28
    assert row["put"]["bid"] == 3.1
    assert row["put"]["ask"] == 3.2
    assert row["put"]["oi"] == 2100


def test_options_chain_default_nearest_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app
    from trading.market_hours import _ET

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 9, 15, 30, 0, tzinfo=tz or _ET)

    monkeypatch.setattr("brokers.options_chain.datetime", FrozenDatetime)
    _mock_alpaca(monkeypatch, chain=_chain_snapshots())

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 200
    data = resp.json()
    assert data["expiry"] == "2026-07-17"
    strikes = [r["strike"] for r in data["rows"]]
    assert strikes == [200.0]


def test_options_chain_strikes_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    chain = _ten_strike_chain()
    contracts = _contracts_from_chain(chain)
    _mock_alpaca(monkeypatch, chain=chain, contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/options-chain?symbol=NVDA&expiry=2026-08-21&strikes=4"
        )

    assert resp.status_code == 200
    strikes = [r["strike"] for r in resp.json()["rows"]]
    assert strikes == [195.0, 200.0, 205.0, 210.0]


def test_options_chain_strikes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    chain = _ten_strike_chain()
    contracts = _contracts_from_chain(chain)
    _mock_alpaca(monkeypatch, chain=chain, contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/options-chain?symbol=NVDA&expiry=2026-08-21&strikes=all"
        )

    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 10


def test_options_chain_strikes_in_cache_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    chain = _ten_strike_chain()
    contracts = _contracts_from_chain(chain)
    broker = _mock_alpaca(monkeypatch, chain=chain, contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21&strikes=4")
        client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21&strikes=10")

    assert broker.get_option_chain_snapshot.call_count == 2


def test_pick_default_expiry_before_close_on_expiry_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brokers.options_chain import _pick_default_expiry
    from trading.market_hours import _ET

    now = real_datetime(2026, 7, 17, 15, 30, 0, tzinfo=_ET)
    expirations = ["2026-07-17", "2026-08-21"]
    assert _pick_default_expiry(expirations, now) == "2026-07-17"


def test_pick_default_expiry_after_close_on_expiry_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brokers.options_chain import _pick_default_expiry
    from trading.market_hours import _ET

    now = real_datetime(2026, 7, 17, 16, 30, 0, tzinfo=_ET)
    expirations = ["2026-07-17", "2026-08-21"]
    assert _pick_default_expiry(expirations, now) == "2026-08-21"


def test_pick_default_expiry_skips_stale_past_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brokers.options_chain import _pick_default_expiry
    from trading.market_hours import _ET

    now = real_datetime(2026, 7, 20, 10, 0, 0, tzinfo=_ET)
    expirations = ["2026-07-17", "2026-08-21"]
    assert _pick_default_expiry(expirations, now) == "2026-08-21"


def test_options_chain_default_expiry_after_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app
    from trading.market_hours import _ET

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 17, 16, 30, 0, tzinfo=tz or _ET)

    monkeypatch.setattr("brokers.options_chain.datetime", FrozenDatetime)
    _mock_alpaca(monkeypatch, chain=_chain_snapshots())

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 200
    assert resp.json()["expiry"] == "2026-08-21"


def test_options_chain_as_of_max_quote_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app

    chain = _ten_strike_chain()
    contracts = _contracts_from_chain(chain)
    _mock_alpaca(monkeypatch, chain=chain, contracts=contracts)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(
            "/api/options-chain?symbol=NVDA&expiry=2026-08-21&strikes=4"
        )

    assert resp.status_code == 200
    assert resp.json()["as_of"] == "2026-07-09T21:30:00Z"


def test_options_chain_as_of_null_without_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app

    sparse_chain = {
        "NVDA260717C00200000": {
            "latestQuote": {"bp": 1.5},
        },
    }
    _mock_alpaca(monkeypatch, chain=sparse_chain)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA&expiry=2026-07-17")

    assert resp.status_code == 200
    assert resp.json()["as_of"] is None


def test_options_chain_missing_fields_are_null(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    sparse_chain = {
        "NVDA260717C00200000": {
            "latestQuote": {"bp": 1.5},
        },
    }
    _mock_alpaca(monkeypatch, chain=sparse_chain)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA&expiry=2026-07-17")

    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["call"]["bid"] == 1.5
    assert row["call"]["ask"] is None
    assert row["call"]["iv"] is None
    assert row["call"]["delta"] is None
    assert row["call"]["volume"] is None
    assert row["call"]["oi"] is None
    assert row["call"]["last"] is None
    assert row["put"]["bid"] is None


def test_options_chain_empty_chain_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca(monkeypatch, chain={})

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "NVDA"
    assert data["expirations"] == []
    assert data["expiry"] is None
    assert data["rows"] == []
    assert data["as_of"] is None


def test_options_chain_contracts_fetched_once_per_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole reason the chain can be polled: contracts cost 2.15s for SPY
    (14,398 rows, measured live 2026-07-31) and carry only per-day constants —
    the expirations list and open interest. Quotes must re-fetch every poll;
    contracts must not."""
    broker = _mock_alpaca(monkeypatch, chain=_chain_snapshots())
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21")
        # A different expiry so the endpoint's own response cache cannot serve it.
        second = client.get("/api/options-chain?symbol=NVDA&expiry=2026-07-17")

    assert first.status_code == 200
    assert second.status_code == 200
    assert broker.get_option_contracts.call_count == 1
    assert broker.get_option_chain_snapshot.call_count == 2
    # OI still lands on the right expiry's rows, from the cached map.
    assert second.json()["expiry"] == "2026-07-17"


def test_options_chain_contracts_failure_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient vendor failure must not blind the picker until midnight."""
    broker = _mock_alpaca(monkeypatch, chain=_chain_snapshots())
    broker.get_option_contracts = MagicMock(side_effect=RuntimeError("contracts boom"))
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/options-chain?symbol=NVDA&expiry=2026-08-21")
        assert first.json()["expirations"] == []
        broker.get_option_contracts = MagicMock(
            return_value=[
                {
                    "symbol": "NVDA260821C00190000",
                    "expiration_date": "2026-08-21",
                    "open_interest": "77",
                }
            ]
        )
        second = client.get("/api/options-chain?symbol=NVDA&expiry=2026-07-17")

    assert second.json()["expirations"] == ["2026-08-21"]


def test_options_chain_broker_error_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.main import app

    # Contracts must resolve an expiry so the (raising) chain fetch runs.
    _mock_alpaca(
        monkeypatch,
        chain_raises=True,
        contracts=[
            {"symbol": "NVDA260717C00200000", "expiration_date": "2026-07-17"}
        ],
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 500
    assert "error" in resp.json()


def test_options_chain_requires_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    _mock_alpaca(monkeypatch, chain=_chain_snapshots())

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain")

    assert resp.status_code == 400


def test_options_chain_alpaca_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.main import app

    monkeypatch.setattr("api.main.broker_is_configured", lambda _name: False)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get("/api/options-chain?symbol=NVDA")

    assert resp.status_code == 501


# --- Index option roots (SPX/SPXW) — no contracts API, generated picker ---------


def test_index_expirations_spxw_horizons() -> None:
    from datetime import date

    from brokers.options_chain import _index_expirations

    exps = _index_expirations("SPXW", date(2026, 7, 22))
    assert exps == sorted(set(exps))
    # Dailies: next 15 weekdays, starting tomorrow.
    assert "2026-07-23" in exps and "2026-08-11" in exps
    assert "2026-07-22" not in exps  # never today
    assert "2026-07-26" not in exps  # never a weekend (Sunday)
    # Friday weeklies past the daily window, monthlies far out.
    assert "2026-09-04" in exps
    assert "2026-12-18" in exps


def test_index_expirations_spx_is_third_fridays_only() -> None:
    from datetime import date, timedelta

    from brokers.options_chain import _index_expirations

    exps = _index_expirations("SPX", date(2026, 7, 22))
    assert "2026-08-21" in exps and "2027-02-19" in exps
    for e in exps:
        d = date.fromisoformat(e)
        assert d.weekday() == 4
        assert 15 <= d.day <= 21  # third Friday
        assert d - timedelta(days=7) < date(d.year, d.month, 15)


def test_parity_spot_prefers_tightest_call_put_pair() -> None:
    from brokers.options_chain import _parity_spot

    def side(bp: float, ap: float) -> tuple[str, dict]:
        return ("OCC", {"latestQuote": {"bp": bp, "ap": ap}})

    strike_map = {
        # C-P = +40 at 7400 (call rich: spot above strike)
        7400.0: {"call": side(50.0, 52.0), "put": side(10.0, 12.0)},
        # tightest pair: C-P = +2 → S ≈ 7442
        7440.0: {"call": side(20.0, 22.0), "put": side(18.0, 20.0)},
        # one-sided rows never contribute
        7500.0: {"call": side(5.0, 6.0)},
    }
    assert _parity_spot(strike_map) == 7442.0


def test_options_chain_spxw_uses_generated_picker_and_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brokers.options_chain import build_options_chain_sync
    from trading.market_hours import _ET

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 7, 22, 15, 0, 0, tzinfo=tz or _ET)

    monkeypatch.setattr("brokers.options_chain.datetime", FrozenDatetime)
    broker = MagicMock(spec=AlpacaAdapter)
    broker.get_option_chain_snapshot.return_value = {
        "SPXW260723C07440000": {"latestQuote": {"bp": 20.0, "ap": 22.0}},
        "SPXW260723P07440000": {"latestQuote": {"bp": 18.0, "ap": 20.0}},
        "SPXW260723C07450000": {"latestQuote": {"bp": 15.0, "ap": 16.0}},
        "SPXW260723P07450000": {"latestQuote": {"bp": 23.0, "ap": 24.0}},
    }
    result = build_options_chain_sync(broker, "SPXW", "2026-07-23", "all")

    # No trading-API calls for an index root — contracts/stock quote don't exist.
    broker.get_option_contracts.assert_not_called()
    broker.get_stock_snapshot_fields.assert_not_called()
    assert result["expiry"] == "2026-07-23"
    assert len(result["expirations"]) > 10
    assert [r["strike"] for r in result["rows"]] == [7440.0, 7450.0]
    assert result["underlying_price"] == 7442.0
    assert result["underlying_price_source"] == "options_parity"
    # OI has no source for index roots.
    assert result["rows"][0]["call"]["oi"] is None
