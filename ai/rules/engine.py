"""JSON rules engine — evaluate conditions against snapshot/event facts."""

from __future__ import annotations

import logging
from typing import Any

from db.ai_tables import list_rules, record_rule_event

logger = logging.getLogger(__name__)

OPERATORS = {
    "equal": lambda a, b: a == b,
    "notEqual": lambda a, b: a != b,
    "lessThan": lambda a, b: a < b,
    "lessThanInclusive": lambda a, b: a <= b,
    "greaterThan": lambda a, b: a > b,
    "greaterThanInclusive": lambda a, b: a >= b,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else False,
    "contains": lambda a, b: b in a if hasattr(a, "__contains__") else False,
}


def build_facts_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    account = snapshot.get("account", {})
    positions = snapshot.get("positions", [])
    total_unrealized = sum(float(p.get("unrealized_pl", 0)) for p in positions)
    symbols = [str(p.get("symbol", "")).upper() for p in positions]
    return {
        "captured_at": snapshot.get("captured_at"),
        "broker": snapshot.get("broker"),
        "portfolio_value": float(account.get("portfolio_value", 0)),
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "position_count": len(positions),
        "open_order_count": len(snapshot.get("open_orders", [])),
        "total_unrealized_pl": total_unrealized,
        "symbols": symbols,
        "has_positions": len(positions) > 0,
    }


def build_facts_from_event(event: dict[str, Any]) -> dict[str, Any]:
    facts = dict(event.get("facts", {}))
    facts["event_type"] = event.get("type", "custom")
    return facts


def _evaluate_condition(condition: dict[str, Any], facts: dict[str, Any]) -> bool:
    fact_name = condition.get("fact")
    operator = condition.get("operator", "equal")
    expected = condition.get("value")
    actual = facts.get(fact_name)
    fn = OPERATORS.get(str(operator))
    if fn is None:
        logger.warning("Unknown operator %s", operator)
        return False
    try:
        return bool(fn(actual, expected))
    except TypeError:
        return False


def _evaluate_node(node: dict[str, Any], facts: dict[str, Any]) -> bool:
    if "all" in node:
        return all(_evaluate_node(child, facts) for child in node["all"])
    if "any" in node:
        return any(_evaluate_node(child, facts) for child in node["any"])
    if "not" in node:
        return not _evaluate_node(node["not"], facts)
    if "fact" in node:
        return _evaluate_condition(node, facts)
    return False


def evaluate_rules(
    facts: dict[str, Any],
    *,
    event_type: str = "poll",
) -> list[dict[str, Any]]:
    """Evaluate enabled rules; record and return triggered actions."""
    triggered: list[dict[str, Any]] = []
    for rule in list_rules():
        if not rule.get("enabled"):
            continue
        conditions = rule.get("conditions", {})
        if not _evaluate_node(conditions, facts):
            continue
        action = rule.get("action", {})
        event = record_rule_event(
            rule_id=rule["id"],
            event_type=event_type,
            payload={"facts": facts, "action": action},
        )
        triggered.append(
            {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "action": action,
                "event": event,
            }
        )
        logger.info("Rule triggered: %s (%s)", rule["name"], rule["id"])
    return triggered


def evaluate_on_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    facts = build_facts_from_snapshot(snapshot)
    return evaluate_rules(facts, event_type="poll")


def evaluate_on_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    facts = build_facts_from_event(event)
    return evaluate_rules(facts, event_type=str(event.get("type", "event")))
