"""`symbol_exists` must reject Alpaca's server-side undotted aliases."""

from __future__ import annotations

import pytest

from brokers.alpaca import AlpacaAdapter, AlpacaError

# Alpaca resolves an undotted request to the canonical dotted asset, so asking
# for BRKB returns the BRK.B record with `tradable: true`.
_ASSETS = {
    "AAPL": {"symbol": "AAPL", "tradable": True},
    "BRK.B": {"symbol": "BRK.B", "tradable": True},
    "BRKB": {"symbol": "BRK.B", "tradable": True},
    "DELISTED": {"symbol": "DELISTED", "tradable": False},
}


def _adapter() -> AlpacaAdapter:
    adapter = AlpacaAdapter(
        base_url="https://paper-api.alpaca.markets",
        key_id="test-key",
        secret="test-secret",
    )

    def _get(path: str) -> dict:
        sym = path.rsplit("/", 1)[-1]
        if sym not in _ASSETS:
            raise AlpacaError(f"asset not found: {sym}")
        return _ASSETS[sym]

    adapter._get = _get  # type: ignore[method-assign]
    return adapter


@pytest.mark.parametrize("symbol", ["AAPL", "BRK.B", "brk.b"])
def test_symbol_exists_accepts_canonical_names(symbol: str) -> None:
    assert _adapter().symbol_exists(symbol) is True


@pytest.mark.parametrize("symbol", ["BRKB", "DELISTED", "NOPE", ""])
def test_symbol_exists_rejects_aliases_and_untradable(symbol: str) -> None:
    """An alias would book the position under a symbol the broker does not hold."""
    assert _adapter().symbol_exists(symbol) is False
