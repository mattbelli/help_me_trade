"""OCC symbol encoding and round-trip parsing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from imports.option_symbol import (
    OptionSymbol,
    parse_occ_symbol,
    parse_option_symbol,
    to_occ_symbol,
)


def test_to_occ_symbol_tsla_call() -> None:
    opt = OptionSymbol(
        underlying="TSLA",
        expiration=date(2026, 2, 20),
        strike=Decimal("410"),
        right="C",
    )
    assert to_occ_symbol(opt) == "TSLA260220C00410000"


def test_parse_occ_symbol_tsla_call() -> None:
    parsed = parse_occ_symbol("TSLA260220C00410000")
    assert parsed is not None
    assert parsed.underlying == "TSLA"
    assert parsed.expiration == date(2026, 2, 20)
    assert parsed.strike == Decimal("410")
    assert parsed.right == "C"


def test_schwab_to_occ_round_trip() -> None:
    schwab = "TSLA 02/20/2026 410.00 C"
    parsed = parse_option_symbol(schwab)
    assert parsed is not None
    occ = to_occ_symbol(parsed)
    assert occ == "TSLA260220C00410000"
    back = parse_occ_symbol(occ)
    assert back == parsed


def test_parse_occ_symbol_put() -> None:
    opt = OptionSymbol(
        underlying="SPXW",
        expiration=date(2026, 2, 13),
        strike=Decimal("6950"),
        right="P",
    )
    occ = to_occ_symbol(opt)
    assert occ == "SPXW260213P06950000"
    parsed = parse_occ_symbol(occ)
    assert parsed == opt


def test_parse_occ_symbol_invalid() -> None:
    assert parse_occ_symbol("NOTANOPTION") is None
    assert parse_occ_symbol("") is None
