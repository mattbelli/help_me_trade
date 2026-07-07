"""Truth table for the shared per-asset-class router used by both hybrid seams."""

from __future__ import annotations

import pytest

from brokers.symbol_class import classify_symbol, rest_provider_for


@pytest.mark.parametrize(
    ("sym", "expected"),
    [
        # future — leading slash, checked BEFORE the crypto "contains /" rule.
        ("/ES", "future"),
        ("/ESU25", "future"),
        ("/CL", "future"),
        ("/nqz25", "future"),  # lower-case normalizes
        # crypto — internal slash, no leading slash.
        ("BTC/USD", "crypto"),
        ("ETH/USD", "crypto"),
        # option — full OSI/OCC contract.
        ("AAPL240119C00190000", "option"),
        ("SPY251219P00450000", "option"),
        # equity — everything else, incl. ETFs and dotted tickers.
        ("SPY", "equity"),
        ("AAPL", "equity"),
        ("BRK.B", "equity"),
    ],
)
def test_classify_truth_table(sym: str, expected: str) -> None:
    assert classify_symbol(sym) == expected


def test_future_and_crypto_do_not_collide() -> None:
    # /ES has a slash but it's LEADING → future wins (rule 1 before rule 2).
    assert classify_symbol("/ES") == "future"
    # BTC/USD has a slash that is NOT leading → crypto.
    assert classify_symbol("BTC/USD") == "crypto"


@pytest.mark.parametrize(
    ("sym", "provider"),
    [
        ("/ESU25", "schwab"),
        ("SPY", "alpaca"),
        ("BTC/USD", "alpaca"),
        ("AAPL240119C00190000", "alpaca"),
    ],
)
def test_rest_provider_routing(sym: str, provider: str) -> None:
    assert rest_provider_for(sym) == provider
