"""Trade setup idea schema and pure validation (no I/O)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["validate_ideas"]

_CONFIDENCE_VALUES = frozenset({"low", "medium", "high"})
# Reject only NUMERIC probability claims (calibration rule). Benign percentage
# prose ("price 1.0% above support", "RSI at 53") must pass — a bare \d+% check
# made idea survival depend on the LLM's wording (caught live 2026-07-04).
_PROBABILITY_PATTERN = re.compile(
    r"(\d{1,3}\s*%\s*(?:win|success|chance|probabilit|odds|hit|accura))"
    r"|((?:win\s*rate|win\s*%|probabilit\w*|likelihood|odds)(?:\W+\w+){0,3}\W*\d{1,3}\s*%?(?!\.\d))",
    re.IGNORECASE,
)


def _parse_price(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if dec <= 0:
        return None
    return dec


def _normalize_price_str(dec: Decimal) -> str:
    normalized = dec.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _has_fabricated_probability(*texts: str) -> bool:
    for text in texts:
        if not text:
            continue
        if _PROBABILITY_PATTERN.search(text):
            return True
    return False


def _reject(symbol: str, reason: str) -> dict[str, str]:
    return {"symbol": symbol, "reason": reason}


def validate_ideas(
    raw_ideas: list[Any],
    last_prices: dict[str, Decimal],
    *,
    allowed_symbols: set[str] | frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return (valid, rejected) idea dicts. Valid ideas are normalized in place."""
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    seen_symbols: set[str] = set()
    as_of = datetime.now(UTC).isoformat()

    if not isinstance(raw_ideas, list):
        return valid, rejected

    for raw in raw_ideas:
        if not isinstance(raw, dict):
            continue

        symbol = str(raw.get("symbol", "")).strip().upper()
        if not symbol:
            rejected.append(_reject("?", "missing symbol"))
            continue

        if symbol in seen_symbols:
            rejected.append(_reject(symbol, "duplicate symbol"))
            continue

        if symbol not in allowed_symbols:
            rejected.append(_reject(symbol, "symbol not in scan candidates"))
            continue

        direction = str(raw.get("direction", "")).strip().lower()
        if direction != "long":
            rejected.append(_reject(symbol, "only long setups supported"))
            continue

        thesis = str(raw.get("thesis", "")).strip()
        if not thesis:
            rejected.append(_reject(symbol, "missing thesis"))
            continue

        confidence_reason = str(raw.get("confidence_reason", "")).strip()
        if _has_fabricated_probability(thesis, confidence_reason):
            rejected.append(_reject(symbol, "fabricated probability"))
            continue

        for forbidden in ("win", "probability", "prob"):
            if forbidden in raw and raw[forbidden] is not None:
                rejected.append(_reject(symbol, "fabricated probability"))
                break
        else:
            confidence = str(raw.get("confidence", "")).strip().lower()
            if confidence not in _CONFIDENCE_VALUES:
                rejected.append(_reject(symbol, "invalid confidence"))
                continue

            entry_zone_raw = raw.get("entry_zone")
            if not isinstance(entry_zone_raw, dict):
                rejected.append(_reject(symbol, "invalid entry_zone"))
                continue

            entry_low = _parse_price(entry_zone_raw.get("low"))
            entry_high = _parse_price(entry_zone_raw.get("high"))
            if entry_low is None or entry_high is None:
                rejected.append(_reject(symbol, "invalid entry zone prices"))
                continue
            if entry_low > entry_high:
                rejected.append(_reject(symbol, "entry zone low exceeds high"))
                continue

            stop = _parse_price(raw.get("stop"))
            if stop is None:
                rejected.append(_reject(symbol, "invalid stop"))
                continue
            if stop >= entry_low:
                rejected.append(_reject(symbol, "stop must be below entry zone low"))
                continue

            targets_raw = raw.get("targets")
            if not isinstance(targets_raw, list) or not (1 <= len(targets_raw) <= 3):
                rejected.append(_reject(symbol, "targets must be 1-3 items"))
                continue

            targets: list[Decimal] = []
            target_invalid = False
            for t in targets_raw:
                parsed = _parse_price(t)
                if parsed is None:
                    rejected.append(_reject(symbol, "invalid target price"))
                    target_invalid = True
                    break
                targets.append(parsed)
            if target_invalid:
                continue

            for target in targets:
                if target <= entry_high:
                    rejected.append(_reject(symbol, "target must exceed entry zone high"))
                    target_invalid = True
                    break
            if target_invalid:
                continue

            for i in range(1, len(targets)):
                if targets[i] <= targets[i - 1]:
                    rejected.append(_reject(symbol, "targets must be strictly ascending"))
                    target_invalid = True
                    break
            if target_invalid:
                continue

            last_price = last_prices.get(symbol)
            if last_price is None:
                rejected.append(_reject(symbol, "no last price for symbol"))
                continue

            midpoint = (entry_low + entry_high) / 2
            entry_deviation = abs(midpoint - last_price) / last_price
            if entry_deviation > Decimal("0.25"):
                rejected.append(
                    _reject(
                        symbol,
                        f"entry zone implausible vs last price {_normalize_price_str(last_price)}",
                    )
                )
                continue

            stop_deviation = abs(stop - last_price) / last_price
            if stop_deviation > Decimal("0.40"):
                rejected.append(
                    _reject(
                        symbol,
                        f"stop implausible vs last price {_normalize_price_str(last_price)}",
                    )
                )
                continue

            risks_raw = raw.get("risks")
            if not isinstance(risks_raw, list) or not (1 <= len(risks_raw) <= 4):
                rejected.append(_reject(symbol, "risks must be 1-4 items"))
                continue
            risks = [str(r).strip() for r in risks_raw if str(r).strip()]
            if not (1 <= len(risks) <= 4):
                rejected.append(_reject(symbol, "risks must be 1-4 items"))
                continue

            evidence_raw = raw.get("evidence")
            evidence: list[str] = []
            if isinstance(evidence_raw, list):
                evidence = [str(u).strip() for u in evidence_raw if str(u).strip()]

            entry_low_str = _normalize_price_str(entry_low)
            entry_high_str = _normalize_price_str(entry_high)

            entry_state_raw = raw.get("entry_state")
            if entry_state_raw is not None:
                entry_state = str(entry_state_raw).strip().lower()
                if entry_state not in ("ready", "forming"):
                    rejected.append(_reject(symbol, "invalid entry_state"))
                    continue
            else:
                entry_state = None

            idea: dict[str, Any] = {
                "symbol": symbol,
                "direction": "long",
                "thesis": thesis,
                "entry_zone": {"low": entry_low_str, "high": entry_high_str},
                "stop": _normalize_price_str(stop),
                "targets": [_normalize_price_str(t) for t in targets],
                "confidence": confidence,
                "confidence_reason": confidence_reason,
                "entry_condition": {
                    "type": "price_in_zone",
                    "low": entry_low_str,
                    "high": entry_high_str,
                },
                "status": "candidate",
                "risks": risks,
                "evidence": evidence,
                "as_of": as_of,
            }
            if entry_state is not None:
                idea["entry_state"] = entry_state
            if raw.get("structure_type") is not None:
                idea["structure_type"] = str(raw["structure_type"])
            if raw.get("why") is not None:
                idea["why"] = str(raw["why"])
            valid.append(idea)
            seen_symbols.add(symbol)

    return valid, rejected
