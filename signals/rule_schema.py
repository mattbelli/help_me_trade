"""Validated rule-condition schema that compiles to the engine's condition dict."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Self, Union

from pydantic import BaseModel, Field, field_validator, model_validator

# --- Metric registry (fact keys must match signals/features.py + build_facts_from_snapshot) ---

_SYMBOL_NUMBER_METRICS = (
    "price",
    "pct_change_1d",
    "pct_change_5d",
    "pct_change_21d",
    "sma_50",
    "sma_200",
    "ema_20",
    "rsi_14",
    "atr_14",
    "dist_from_high_252",
    "vol_vs_avg_20",
    "rel_strength_21",
)

_SYMBOL_BOOL_METRICS = (
    "cross_above_sma_200",
    "cross_below_sma_200",
)

_POSITION_NUMBER_METRICS = (
    "position_weight_pct",
    "position_unrealized_pl",
    "position_market_value",
    "position_price",
    "position_quote_change_pct",
    "position_quote_price",
)

_PORTFOLIO_NUMBER_METRICS = (
    "portfolio_value",
    "cash",
    "buying_power",
    "position_count",
    "open_order_count",
    "total_unrealized_pl",
    "cash_pct",
    "largest_position_pct",
    "equity_change_since_prior",
    "equity_change_pct_since_prior",
    "equity_change_since_day_ago",
    "equity_change_pct_since_day_ago",
    "equity_change_since_week_ago",
    "equity_change_pct_since_week_ago",
)

_PORTFOLIO_BOOL_METRICS = ("has_positions",)


def _build_metric_registry() -> dict[str, dict[str, str]]:
    registry: dict[str, dict[str, str]] = {}
    for metric in _SYMBOL_NUMBER_METRICS:
        registry[metric] = {"scope": "symbol", "value_type": "number"}
    for metric in _SYMBOL_BOOL_METRICS:
        registry[metric] = {"scope": "symbol", "value_type": "bool"}
    for metric in _POSITION_NUMBER_METRICS:
        registry[metric] = {"scope": "position", "value_type": "number"}
    for metric in _PORTFOLIO_NUMBER_METRICS:
        registry[metric] = {"scope": "portfolio", "value_type": "number"}
    for metric in _PORTFOLIO_BOOL_METRICS:
        registry[metric] = {"scope": "portfolio", "value_type": "bool"}
    return registry


METRIC_REGISTRY: dict[str, dict[str, str]] = _build_metric_registry()

_BOOL_OPERATORS = frozenset({"equal", "notEqual"})
_NUMERIC_COMPARE_OPERATORS = frozenset(
    {
        "lessThan",
        "lessThanInclusive",
        "greaterThan",
        "greaterThanInclusive",
    }
)
_LIST_OPERATORS = frozenset({"in", "contains"})


def fact_key(metric: str, symbol: str | None) -> str:
    """Return the flat fact key the rules engine uses for a registry metric."""
    entry = METRIC_REGISTRY[metric]
    scope = entry["scope"]
    if scope == "symbol":
        if not symbol:
            raise ValueError(f"symbol is required for metric '{metric}'")
        return f"{symbol}_{metric}"
    if scope == "position":
        if not symbol:
            raise ValueError(f"symbol is required for metric '{metric}'")
        suffix = metric.removeprefix("position_")
        return f"position_{symbol}_{suffix}"
    return metric


class Operator(str, Enum):
    equal = "equal"
    notEqual = "notEqual"
    lessThan = "lessThan"
    lessThanInclusive = "lessThanInclusive"
    greaterThan = "greaterThan"
    greaterThanInclusive = "greaterThanInclusive"
    in_ = "in"
    contains = "contains"

    @classmethod
    def _missing_(cls, value: object) -> Operator | None:
        if value == "in":
            return cls.in_
        return None


class Leaf(BaseModel):
    metric: str
    symbol: str | None = None
    operator: Operator
    value: float | int | bool | str | list[Any]

    @field_validator("metric")
    @classmethod
    def metric_must_be_registered(cls, metric: str) -> str:
        if metric not in METRIC_REGISTRY:
            known = ", ".join(sorted(METRIC_REGISTRY))
            raise ValueError(f"unknown metric '{metric}'; known metrics: {known}")
        return metric

    @model_validator(mode="after")
    def validate_scope_value_and_operator(self) -> Self:
        entry = METRIC_REGISTRY[self.metric]
        scope = entry["scope"]
        value_type = entry["value_type"]

        if scope in {"symbol", "position"}:
            if not self.symbol or not str(self.symbol).strip():
                raise ValueError(f"symbol is required for metric '{self.metric}'")
            object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        elif self.symbol is not None:
            raise ValueError(f"symbol must not be set for portfolio metric '{self.metric}'")

        operator_name = self.operator.value

        if value_type == "bool":
            if not isinstance(self.value, bool):
                raise ValueError(
                    f"metric '{self.metric}' expects a bool value, got {type(self.value).__name__}"
                )
            if operator_name not in _BOOL_OPERATORS:
                raise ValueError(
                    f"metric '{self.metric}' only supports operators: "
                    f"{', '.join(sorted(_BOOL_OPERATORS))}"
                )
            return self

        if operator_name in _NUMERIC_COMPARE_OPERATORS:
            if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
                raise ValueError(
                    f"operator '{operator_name}' requires a numeric value for "
                    f"metric '{self.metric}'"
                )
        elif operator_name in _LIST_OPERATORS:
            if not isinstance(self.value, (list, tuple, set)):
                raise ValueError(
                    f"operator '{operator_name}' requires a list value for metric '{self.metric}'"
                )
        elif operator_name in _BOOL_OPERATORS:
            if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
                raise ValueError(
                    f"operator '{operator_name}' requires a numeric value for "
                    f"metric '{self.metric}'"
                )

        return self

    def compile(self) -> dict[str, Any]:
        return {
            "fact": fact_key(self.metric, self.symbol),
            "operator": self.operator.value,
            "value": self.value,
        }


class Group(BaseModel):
    all: list[Condition] | None = None
    any: list[Condition] | None = None
    not_: Condition | None = Field(default=None, alias="not")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_exactly_one_branch(self) -> Self:
        branches = (
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
        )
        if sum(branches) != 1:
            raise ValueError("group must have exactly one of: all, any, not")
        if self.all is not None and len(self.all) == 0:
            raise ValueError("all must be a non-empty list")
        if self.any is not None and len(self.any) == 0:
            raise ValueError("any must be a non-empty list")
        return self

    def compile(self) -> dict[str, Any]:
        if self.all is not None:
            return {"all": [child.compile() for child in self.all]}
        if self.any is not None:
            return {"any": [child.compile() for child in self.any]}
        if self.not_ is not None:
            return {"not": self.not_.compile()}
        raise RuntimeError("invalid group: no branch set")


def _parse_condition(value: Any) -> Leaf | Group:
    if isinstance(value, (Leaf, Group)):
        return value
    if isinstance(value, dict):
        if "metric" in value:
            return Leaf.model_validate(value)
        return Group.model_validate(value)
    raise ValueError("condition must be a leaf (metric) or group (all/any/not)")


Condition = Annotated[Union[Leaf, Group], _parse_condition]

Group.model_rebuild()


class Action(BaseModel):
    type: Literal["alert", "trade"] = "alert"
    trigger_on: Literal["always", "transition"] = "transition"
    cooldown_seconds: int = Field(default=0, ge=0)
    message: str | None = None
    symbol: str | None = None
    side: Literal["buy", "sell"] | None = None
    notional_usd: float | None = None
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None

    @model_validator(mode="after")
    def trade_action_requires_fields(self) -> Self:
        if self.type == "trade":
            if not self.symbol or not self.side or self.notional_usd is None:
                raise ValueError(
                    "trade actions require symbol, side, and notional_usd"
                )
        return self


class Universe(BaseModel):
    kind: Literal["watchlist", "positions", "symbol", "all"]
    value: str | None = None

    @model_validator(mode="after")
    def value_required_for_kind(self) -> Self:
        if self.kind in {"watchlist", "symbol"} and not self.value:
            raise ValueError(f"value is required when universe kind is '{self.kind}'")
        return self


class RuleSpec(BaseModel):
    name: str
    condition: Condition
    action: Action = Field(default_factory=Action)
    universe: Universe | None = None
    enabled: bool = True
    source: Literal["user", "ai", "preset"] = "user"

    def to_engine_rule(self) -> dict[str, Any]:
        action: dict[str, Any] = {
            "type": self.action.type,
            "trigger_on": self.action.trigger_on,
            "cooldown_seconds": self.action.cooldown_seconds,
        }
        if self.action.message is not None:
            action["message"] = self.action.message
        if self.action.symbol is not None:
            action["symbol"] = self.action.symbol
        if self.action.side is not None:
            action["side"] = self.action.side
        if self.action.notional_usd is not None:
            action["notional_usd"] = self.action.notional_usd
        if self.action.order_type != "market":
            action["order_type"] = self.action.order_type
        if self.action.limit_price is not None:
            action["limit_price"] = self.action.limit_price
        return {
            "name": self.name,
            "enabled": self.enabled,
            "conditions": self.condition.compile(),
            "action": action,
        }
