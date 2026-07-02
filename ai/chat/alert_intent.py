"""Detect user intent for alert/signal rule proposals."""

from __future__ import annotations

import re

from signals.rule_schema import METRIC_REGISTRY, Operator

_ALERT_KEYWORDS = re.compile(
    r"\b(alert|notify|notification|watch|signal|rules?)\b",
    re.IGNORECASE,
)
_CONDITION_KEYWORDS = re.compile(
    r"\b(when|if)\b|\b(cross(es|ed|ing)?|exceed(s|ed)?|drop(s|ped|ping)?|"
    r"rise(s|n|s)?|fall(s|ing)?|below|above)\b",
    re.IGNORECASE,
)


def looks_like_alert_intent(user_text: str) -> bool:
    text = (user_text or "").strip()
    if not text:
        return False
    return bool(_ALERT_KEYWORDS.search(text) and _CONDITION_KEYWORDS.search(text))


def _metric_vocabulary_block() -> str:
    metrics = ", ".join(sorted(METRIC_REGISTRY.keys()))
    operators = ", ".join(sorted(op.value for op in Operator))
    return (
        f"Valid metrics: {metrics}\n"
        f"Valid operators: {operators}\n"
        "- Bool metrics (e.g. cross_below_sma_200): use operator equal with value true/false.\n"
        "- Numeric metrics (e.g. rsi_14): use lessThan, greaterThan, etc. with a number.\n"
        "- Symbol-scoped metrics require a symbol field; portfolio metrics must omit symbol."
    )


def alert_intent_prompt_block(user_text: str) -> str:
    if not looks_like_alert_intent(user_text):
        return ""
    return (
        "\n\nALERT / SIGNAL REQUEST (this turn):\n"
        "- Call create_rule immediately with a validated spec (confirm parameters "
        "first — it arms immediately): "
        "{name, condition, action?}.\n"
        "- Wrap the rule in spec: {spec: {name, condition, action?}}.\n"
        "- Do NOT narrate the tool or write fake calls like "
        "[Calling create_rule...] or \"Let me set up that alert\".\n"
        "- The approval card is the UI — minimal text before the tool call.\n"
        f"{_metric_vocabulary_block()}\n"
    )
