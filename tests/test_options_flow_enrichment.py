"""Hermetic tests for sweep detail enrichment helper."""

from __future__ import annotations

from signals.options_flow.runner import build_sweep_detail as _build_sweep_detail


def test_build_sweep_detail_full_snapshot() -> None:
    snapshot = {
        "impliedVolatility": 0.621,
        "greeks": {
            "delta": 0.44123,
            "gamma": 0.03145,
            "theta": -0.3521,
            "vega": 0.1183,
            "rho": 0.0123,
        },
        "openInterest": 12400,
        "latestQuote": {"bp": 4.2, "ap": 4.35},
    }
    detail = _build_sweep_detail(snapshot, 412.345)
    assert detail == {
        "underlying_price": 412.35,
        "iv": 0.621,
        "delta": 0.4412,
        "gamma": 0.0314,
        "theta": -0.3521,
        "vega": 0.1183,
        "rho": 0.0123,
        "oi": 12400.0,
        "bid": 4.2,
        "ask": 4.35,
    }


def test_build_sweep_detail_omits_missing_fields() -> None:
    detail = _build_sweep_detail({"latestQuote": {"bp": 1.5}}, None)
    assert detail == {"bid": 1.5}


def test_build_sweep_detail_none_when_empty() -> None:
    assert _build_sweep_detail(None, None) is None
    assert _build_sweep_detail({}, None) is None
