"""Parse Schwab-style and OCC option contract symbols."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from imports.money import parse_dollar, parse_mmddyyyy

# UNDERLYING MM/DD/YYYY STRIKE C|P  (spacing tolerant)
_OPTION_RE = re.compile(
    r"^([A-Z][A-Z0-9.]*)\s+"
    r"(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"([\d,]+\.?\d*)\s+"
    r"([CP])$",
    re.IGNORECASE,
)

# OCC: {underlying}{YYMMDD}{C|P}{strike*1000:08d}
_OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


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


def to_occ_symbol(opt: OptionSymbol) -> str:
    """Encode an option contract as an OCC symbol (e.g. TSLA260220C00410000)."""
    yymmdd = opt.expiration.strftime("%y%m%d")
    strike_int = int(
        (opt.strike * Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return f"{opt.underlying}{yymmdd}{opt.right}{strike_int:08d}"


def parse_occ_symbol(occ: str | None) -> OptionSymbol | None:
    """Parse an OCC symbol into contract fields, or None if malformed."""
    if not occ or not occ.strip():
        return None
    text = occ.strip().upper()
    match = _OCC_RE.match(text)
    if not match:
        return None
    underlying, yymmdd, right, strike_raw = match.groups()
    try:
        yy = int(yymmdd[0:2])
        mm = int(yymmdd[2:4])
        dd = int(yymmdd[4:6])
        expiration = date(2000 + yy, mm, dd)
    except ValueError:
        return None
    strike = Decimal(strike_raw) / Decimal("1000")
    if strike <= 0:
        return None
    return OptionSymbol(
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        right=right,
    )


def is_option_symbol(sym: str | None) -> bool:
    return parse_option_symbol(sym) is not None or parse_occ_symbol(sym) is not None
