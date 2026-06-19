"""Parse Schwab-style option contract symbols."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from imports.money import parse_dollar, parse_mmddyyyy

# UNDERLYING MM/DD/YYYY STRIKE C|P  (spacing tolerant)
_OPTION_RE = re.compile(
    r"^([A-Z][A-Z0-9.]*)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"([\d,]+\.?\d*)\s+"
    r"([CP])$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OptionSymbol:
    underlying: str
    expiration: date
    strike: Decimal
    right: str  # "C" or "P"
    multiplier: int = 100


def parse_option_symbol(sym: str | None) -> OptionSymbol | None:
    """Return parsed option contract fields, or None for plain equity symbols."""
    if not sym or not sym.strip():
        return None
    text = " ".join(sym.strip().upper().split())
    match = _OPTION_RE.match(text)
    if not match:
        return None
    underlying, exp_raw, strike_raw, right = match.groups()
    try:
        expiration = date.fromisoformat(parse_mmddyyyy(exp_raw))
    except ValueError:
        return None
    strike = parse_dollar(strike_raw)
    if strike <= 0:
        return None
    option_right = right.upper()
    if option_right not in {"C", "P"}:
        return None
    return OptionSymbol(
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        right=option_right,
    )


def is_option_symbol(sym: str | None) -> bool:
    return parse_option_symbol(sym) is not None
