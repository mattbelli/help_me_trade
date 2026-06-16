"""Parse broker money strings into Decimal."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def parse_dollar(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    text = str(value).strip()
    if not text:
        return Decimal("0")
    negative = text.startswith("-") or text.startswith("(")
    text = text.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()
    if not text:
        return Decimal("0")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return Decimal("0")
    return -amount if negative else amount


def parse_quantity(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def decimal_to_api(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")


def parse_mmddyyyy(value: str) -> str:
    """Return ISO date YYYY-MM-DD from Schwab date strings."""
    text = str(value).strip()
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        raise ValueError(f"Invalid date: {value}")
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"
