"""Evaluate JSON rules with rich facts, transition triggers, cooldown, and WS alerts."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.ai_tables import get_last_rule_event, list_rules, record_rule_event

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

# Last poll match state for transition triggers (rule_id → matched).
_prior_match: dict[str, bool] = {}


def build_facts_from_snapshot(
    snapshot: dict[str, Any],
    *,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    account = snapshot.get("account", {})
    positions = snapshot.get("positions") or []
    analytics = snapshot.get("analytics") or {}
    total_equity = float(account.get("portfolio_value", 0))
    total_unrealized = sum(float(p.get("unrealized_pl", 0)) for p in positions)
    symbols = [str(p.get("symbol", "")).upper() for p in positions if p.get("symbol")]

    facts: dict[str, Any] = {
        "captured_at": snapshot.get("captured_at"),
        "broker": snapshot.get("broker"),
        "portfolio_value": total_equity,
        "cash": float(account.get("cash", 0)),
        "buying_power": float(account.get("buying_power", 0)),
        "position_count": len(positions),
        "open_order_count": len(snapshot.get("open_orders", [])),
        "total_unrealized_pl": total_unrealized,
        "symbols": symbols,
        "has_positions": len(positions) > 0,
        "cash_pct": analytics.get("cash_pct"),
        "largest_position_pct": analytics.get("largest_position_pct"),
        "equity_change_since_prior": analytics.get("equity_change_since_prior"),
        "equity_change_pct_since_prior": analytics.get("equity_change_pct_since_prior"),
        "equity_change_since_day_ago": analytics.get("equity_change_since_day_ago"),
        "equity_change_pct_since_day_ago": analytics.get(
            "equity_change_pct_since_day_ago"
        ),
        "equity_change_since_week_ago": analytics.get("equity_change_since_week_ago"),
        "equity_change_pct_since_week_ago": analytics.get(
            "equity_change_pct_since_week_ago"
        ),
    }

    if analytics.get("flags"):
        facts["analytics_flags"] = list(analytics["flags"])

    for pos in positions:
        sym = str(pos.get("symbol", "")).upper()
        if not sym:
            continue
        weight_pct = 0.0
        if total_equity > 0:
            weight_pct = round(
                (float(pos.get("market_value", 0)) / total_equity) * 100, 2
            )
        facts[f"position_{sym}_weight_pct"] = weight_pct
        facts[f"position_{sym}_unrealized_pl"] = round(
            float(pos.get("unrealized_pl", 0)), 2
        )
        facts[f"position_{sym}_market_value"] = round(
            float(pos.get("market_value", 0)), 2
        )
        facts[f"position_{sym}_price"] = round(float(pos.get("current_price", 0)), 4)
        if quotes and sym in quotes:
            q = quotes[sym]
            facts[f"position_{sym}_quote_change_pct"] = round(
                float(q.get("change_pct", 0)), 4
            )
            if q.get("price"):
                facts[f"position_{sym}_quote_price"] = round(float(q["price"]), 4)

    drift = analytics.get("exposure_drift_pct") or {}
    if isinstance(drift, dict):
        for sym, delta in drift.items():
            facts[f"exposure_drift_{str(sym).upper()}_pct"] = delta

    week_drift = analytics.get("exposure_drift_vs_week_ago_pct") or {}
    if isinstance(week_drift, dict):
        for sym, delta in week_drift.items():
            facts[f"exposure_drift_week_{str(sym).upper()}_pct"] = delta

    return facts


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


def evaluate_condition(condition: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Pure evaluation of a rule condition tree against a facts dict."""
    return _evaluate_node(condition, facts)


def _parse_rule_options(action: dict[str, Any]) -> tuple[str, int]:
    trigger_on = str(action.get("trigger_on", "always")).strip().lower()
    if trigger_on not in {"always", "transition"}:
        trigger_on = "always"
    try:
        cooldown = int(action.get("cooldown_seconds", 0) or 0)
    except (TypeError, ValueError):
        cooldown = 0
    return trigger_on, max(0, cooldown)


def _cooldown_active(rule_id: str, cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False
    last = get_last_rule_event(rule_id)
    if not last:
        return False
    try:
        last_at = datetime.fromisoformat(str(last["created_at"]))
    except ValueError:
        return False
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - last_at).total_seconds()
    return age < cooldown_seconds


def evaluate_rules(
    facts: dict[str, Any],
    *,
    event_type: str = "poll",
) -> list[dict[str, Any]]:
    """Evaluate enabled rules; record and return triggered actions."""
    global _prior_match
    triggered: list[dict[str, Any]] = []
    for rule in list_rules():
        if not rule.get("enabled"):
            continue
        rule_id = str(rule["id"])
        conditions = rule.get("conditions", {})
        matched = _evaluate_node(conditions, facts)
        action = rule.get("action", {})
        trigger_on, cooldown_seconds = _parse_rule_options(action)

        prior = _prior_match.get(rule_id, False)
        _prior_match[rule_id] = matched

        if trigger_on == "transition":
            should_fire = matched and not prior
        else:
            should_fire = matched

        if not should_fire:
            continue
        if _cooldown_active(rule_id, cooldown_seconds):
            logger.debug("Rule %s skipped — cooldown active", rule_id)
            continue

        event = record_rule_event(
            rule_id=rule_id,
            event_type=event_type,
            payload={"facts": facts, "action": action, "trigger_on": trigger_on},
        )
        triggered.append(
            {
                "rule_id": rule_id,
                "rule_name": rule["name"],
                "action": action,
                "event": event,
            }
        )
        logger.info("Rule triggered: %s (%s)", rule["name"], rule_id)
    return triggered


def evaluate_on_snapshot(
    snapshot: dict[str, Any],
    *,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    facts = build_facts_from_snapshot(snapshot, quotes=quotes)
    return evaluate_rules(facts, event_type="poll")


def evaluate_on_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    facts = build_facts_from_event(event)
    return evaluate_rules(facts, event_type=str(event.get("type", "event")))


def reset_rule_transition_state() -> None:
    """Clear in-memory transition state (tests)."""
    global _prior_match
    _prior_match = {}
