"""Trade retrospective — honest analytics → narrative + backtest-gated rule proposals."""

from __future__ import annotations

import copy
import html
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

import db.ai_tables as ai_tables
from ai.llm.base import LLMMessage
from ai.llm.factory import get_llm
from ai.llm.structured import complete_for_extraction, parse_with_retry
from ai.memories import get_memory_service
from brokers.factory import get_broker
from db.analytics import ai_performance_context, resolve_period
from signals.backtest import backtest_condition
from signals.indicators import Bar
from signals.rule_schema import METRIC_REGISTRY, Group, Leaf, RuleSpec

logger = logging.getLogger(__name__)

MAX_LESSONS = 3
OOS_FRACTION = 0.2
WARMUP_DAYS = 252
MIN_SPLIT_TRADING_DAYS = 30
_HOLD_TIME_FIELDS = frozenset(
    {"symbol", "open_date", "close_date", "is_option", "asset_class"}
)
INSUFFICIENT_OOS_NOTE = (
    "insufficient history for a held-out test — validate manually"
)

RETRY_PROMPT = (
    "Your previous response was not valid JSON matching the schema. "
    "Return only a JSON object with keys: narrative, what_went_right, "
    "what_went_wrong, behavioral_observations, proposed_rules, proposed_lessons."
)


def _coerce_str_list(value: Any) -> list[str]:
    """Tolerate LLMs that return a string or a {key: text} dict where a list of
    strings is expected (common with non-strict structured-output models)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        out: list[str] = []
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                val = json.dumps(val, default=str)
            out.append(f"{key}: {val}" if key else str(val))
        return out
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict):
                items.append("; ".join(f"{k}: {v}" for k, v in item.items()))
            else:
                items.append(str(item))
        return items
    return [str(value)]


class ProposedRule(BaseModel):
    name: str
    rationale: str = ""
    spec: dict[str, Any]


class RetroReport(BaseModel):
    narrative: str = ""
    what_went_right: list[str] = Field(default_factory=list)
    what_went_wrong: list[str] = Field(default_factory=list)
    behavioral_observations: list[str] = Field(default_factory=list)
    proposed_rules: list[ProposedRule] = Field(default_factory=list)
    proposed_lessons: list[str] = Field(default_factory=list)

    @field_validator(
        "what_went_right",
        "what_went_wrong",
        "behavioral_observations",
        "proposed_lessons",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, v: Any) -> list[str]:
        return _coerce_str_list(v)

    @field_validator("proposed_rules", mode="before")
    @classmethod
    def _coerce_rules(cls, v: Any) -> Any:
        # Tolerate a single rule object or a dict-of-rules.
        if isinstance(v, dict):
            if {"name", "spec"} & set(v.keys()):
                return [v]
            return list(v.values())
        return v


def generate_retrospective_report(
    *,
    account_id: str | None = None,
    period: str = "all",
) -> dict[str, Any]:
    analysis_from, analysis_to = resolve_period(period)
    split = _compute_train_oos_split(analysis_from, analysis_to)
    if split.get("train"):
        train_ctx = ai_performance_context(
            account_id=account_id,
            period=period,
            custom_from=split["train"]["from"],
            custom_to=split["train"]["to"],
        )
    else:
        train_ctx = ai_performance_context(account_id=account_id, period=period)
    llm_ctx = _sanitize_llm_context(train_ctx)
    report = _call_retrospective_llm(llm_ctx)
    processed_rules = _process_proposed_rules(report, split)

    memory = get_memory_service()
    lessons_seeded = 0
    for lesson in report.proposed_lessons[:MAX_LESSONS]:
        text = lesson.strip()
        if not text:
            continue
        memory.add(
            text,
            memory_type="lesson",
            metadata={"source": "retrospective", "period": period},
        )
        lessons_seeded += 1

    topic = f"Trade Retrospective {period}"
    run = ai_tables.create_research_run(
        topic=topic, depth="retrospective", db_path=ai_tables.DEFAULT_DB_PATH
    )
    markdown = _report_to_markdown(
        report, processed_rules, period=period, split=split
    )
    html_doc = _render_retrospective_html(
        report, processed_rules, period=period, split=split
    )
    report_payload = {
        **report.model_dump(),
        "period": period,
        "account_id": account_id,
        "proposed_rules": processed_rules,
    }
    artifact = ai_tables.save_research_artifact(
        topic=topic,
        markdown=markdown,
        status="completed",
        report_json=report_payload,
        html=html_doc,
        depth="retrospective",
        artifact_id=run["id"],
        db_path=ai_tables.DEFAULT_DB_PATH,
    )

    return {
        "artifact_id": artifact["id"],
        "period": period,
        "n_proposed_rules": len(processed_rules),
        "n_lessons": lessons_seeded,
    }


def _call_retrospective_llm(ctx: dict[str, Any]) -> RetroReport:
    metrics = ", ".join(sorted(METRIC_REGISTRY))
    system = (
        "You are a trading coach writing an honest retrospective from verified analytics. "
        "Ground every claim ONLY in the provided facts — cite specific symbols and numbers. "
        "Use realized.realized_closed as 'realized on closed positions' (net-cash, reliable). "
        "Positions listed under realized.incomplete_positions lack cost basis — do NOT claim "
        "their P&L or include them in win/loss narratives. "
        "Honor realized.reliability flags; if data is partial, say so. "
        "Treat approximate_fifo as hold-time color only, not headline P&L. "
        "Behavioral observations should reflect concentration, scalping, cadence, timing, asset mix. "
        f"Proposed rules must use ONLY these metrics: {metrics}. "
        "Each proposed_rules item needs name, rationale, and spec (RuleSpec-shaped dict with "
        "name, condition, action). Return JSON only."
    )
    user = (
        "Analyze this trading performance context and return the retrospective report JSON:\n"
        f"{json.dumps(ctx, indent=2, default=str)}"
    )
    llm = get_llm()
    messages = [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=user),
    ]
    raw = complete_for_extraction(llm, messages, max_tokens=2500)
    parsed = parse_with_retry(
        raw,
        RetroReport,
        retry_prompt=RETRY_PROMPT,
        llm=llm,
        messages=messages,
        max_tokens=2500,
    )
    if parsed is None:
        logger.warning("Retrospective LLM output failed validation; using fallback")
        return RetroReport(
            narrative=(
                "Unable to parse a structured retrospective from the model. "
                "Review the performance dashboard for reliable realized P&L on closed positions."
            ),
            behavioral_observations=[
                "Check realized.reliability flags before drawing conclusions.",
            ],
        )
    return parsed


def _process_proposed_rules(
    report: RetroReport,
    split: dict[str, Any],
) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    for proposed in report.proposed_rules:
        try:
            spec = RuleSpec.model_validate(proposed.spec)
        except Exception as exc:
            logger.warning(
                "Dropping invalid proposed rule %r: %s",
                proposed.name,
                exc,
            )
            continue

        backtest_metrics = _backtest_proposed_rule(spec, split)
        engine = spec.to_engine_rule()
        rule_id = f"rule-{uuid.uuid4().hex[:8]}"
        ai_tables.create_rule(
            rule_id=rule_id,
            name=engine["name"],
            conditions=engine["conditions"],
            action=engine["action"],
            enabled=False,
            db_path=ai_tables.DEFAULT_DB_PATH,
        )
        ai_tables.record_rule_event(
            rule_id=rule_id,
            event_type="ai_retrospective",
            payload={
                "source": "ai_retrospective",
                "rationale": proposed.rationale,
                "backtest_metrics": backtest_metrics,
            },
            db_path=ai_tables.DEFAULT_DB_PATH,
        )
        processed.append(
            {
                "name": proposed.name,
                "rationale": proposed.rationale,
                "rule_id": rule_id,
                "condition_summary": _summarize_condition(spec.condition),
                "backtest_metrics": backtest_metrics,
                "spec": proposed.spec,
            }
        )
    return processed


def _backtest_proposed_rule(spec: RuleSpec, split: dict[str, Any]) -> dict[str, Any]:
    scopes = _condition_metric_scopes(spec.condition)
    if any(scope != "symbol" for scope in scopes):
        return {
            "note": (
                "not backtestable from bars (portfolio/position metric) — validate manually"
            ),
        }

    symbol = _primary_symbol(spec.condition)
    if not symbol:
        return {"note": "no symbol found for bar backtest"}

    if not split.get("valid"):
        return {"note": INSUFFICIENT_OOS_NOTE, "oos": False}

    oos_window = split["oos"]
    bars, bar_dates = _fetch_symbol_bars(symbol, oos_window)
    oos_start = date.fromisoformat(oos_window["from"])
    oos_end = date.fromisoformat(oos_window["to"])
    oos_bar_dates = [d for d in bar_dates if oos_start <= d <= oos_end]
    if not bars or not oos_bar_dates:
        return {"note": INSUFFICIENT_OOS_NOTE, "oos": False}

    oos_indices = {i for i, d in enumerate(bar_dates) if oos_start <= d <= oos_end}
    engine = spec.to_engine_rule()
    fires = backtest_condition(engine["conditions"], bars, symbol)
    oos_fires = [f for f in fires if f["index"] in oos_indices]

    last_fire: str | None = None
    if oos_fires:
        idx = oos_fires[-1]["index"]
        if 0 <= idx < len(bar_dates):
            last_fire = bar_dates[idx].isoformat()

    return {
        "oos_window": oos_window,
        "train_window": split["train"],
        "fire_count": len(oos_fires),
        "oos_trading_days": _count_trading_days(oos_start, oos_end),
        "last_fire": last_fire,
        "oos": True,
        "note": None,
    }


def _count_trading_days(start: date, end: date) -> int:
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def _compute_oos_window(
    from_date: str | None,
    to_date: str | None,
) -> dict[str, str] | None:
    if not from_date or not to_date:
        return None
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if end <= start:
        return None
    span = (end - start).days
    if span < 1:
        return None
    oos_days = max(1, int(span * OOS_FRACTION))
    oos_start = end - timedelta(days=oos_days - 1)
    if oos_start <= start:
        return None
    return {"from": oos_start.isoformat(), "to": end.isoformat()}


def _compute_train_window(
    from_date: str,
    oos_window: dict[str, str],
) -> dict[str, str] | None:
    start = date.fromisoformat(from_date)
    oos_start = date.fromisoformat(oos_window["from"])
    train_end = oos_start - timedelta(days=1)
    if train_end < start:
        return None
    return {"from": from_date, "to": train_end.isoformat()}


def _split_is_valid(
    train_window: dict[str, str] | None,
    oos_window: dict[str, str] | None,
) -> bool:
    if not train_window or not oos_window:
        return False
    train_days = _count_trading_days(
        date.fromisoformat(train_window["from"]),
        date.fromisoformat(train_window["to"]),
    )
    oos_days = _count_trading_days(
        date.fromisoformat(oos_window["from"]),
        date.fromisoformat(oos_window["to"]),
    )
    return (
        train_days >= MIN_SPLIT_TRADING_DAYS
        and oos_days >= MIN_SPLIT_TRADING_DAYS
    )


def _compute_train_oos_split(
    from_date: str | None,
    to_date: str | None,
) -> dict[str, Any]:
    oos_window = _compute_oos_window(from_date, to_date)
    train_window = (
        _compute_train_window(from_date, oos_window)
        if oos_window and from_date
        else None
    )
    valid = _split_is_valid(train_window, oos_window)
    return {
        "analysis_from": from_date,
        "analysis_to": to_date,
        "train": train_window,
        "oos": oos_window,
        "valid": valid,
    }


def _sanitize_llm_context(ctx: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(ctx)
    fifo = sanitized.get("approximate_fifo")
    if isinstance(fifo, dict):
        for key in ("largest_wins", "largest_losses"):
            trades = fifo.get(key)
            if isinstance(trades, list):
                fifo[key] = [_strip_unreliable_pnl_fields(t) for t in trades]
    return sanitized


def _strip_unreliable_pnl_fields(trade: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(trade, dict):
        return trade
    return {k: v for k, v in trade.items() if k in _HOLD_TIME_FIELDS}


def _split_disclaimer(split: dict[str, Any]) -> str:
    if split.get("valid") and split.get("train") and split.get("oos"):
        train = split["train"]
        oos = split["oos"]
        return (
            f"Rules were proposed from your trades through {train['to']}, then tested "
            f"on the held-out {oos['from']}–{oos['to']} window the model never saw. "
            "Still fit to your history — validate before arming."
        )
    return (
        "Insufficient history for a reliable held-out test on this period — "
        "validate any candidate rules manually before arming."
    )


def _format_fire_display(metrics: dict[str, Any]) -> str:
    if metrics.get("oos") is False:
        return str(metrics.get("note", INSUFFICIENT_OOS_NOTE))
    fire = metrics.get("fire_count")
    if fire is None:
        return str(metrics.get("note", "n/a"))
    days = metrics.get("oos_trading_days")
    if days is not None:
        return f"{fire} fires over {days} OOS trading days"
    return str(fire)


def _fetch_symbol_bars(
    symbol: str,
    oos_window: dict[str, str],
) -> tuple[list[Bar], list[date]]:
    end = date.fromisoformat(oos_window["to"])
    start = date.fromisoformat(oos_window["from"]) - timedelta(days=WARMUP_DAYS)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=UTC)

    try:
        broker = get_broker("alpaca")
        stock_bars = broker.get_stock_bars_range(
            symbol,
            timeframe="1Day",
            start=start_dt,
            end=end_dt,
        )
    except Exception as exc:
        logger.warning("Bar fetch failed for %s: %s", symbol, exc)
        return [], []

    bars: list[Bar] = []
    dates: list[date] = []
    for bar in stock_bars:
        bar_date = _parse_bar_date(bar.time)
        bars.append(
            {
                "o": bar.open,
                "h": bar.high,
                "l": bar.low,
                "c": bar.close,
                "v": bar.volume,
            }
        )
        dates.append(bar_date)
    return bars, dates


def _parse_bar_date(time_str: str) -> date:
    text = time_str.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(text[:10])


def _condition_metric_scopes(condition: Leaf | Group) -> list[str]:
    if isinstance(condition, Leaf):
        return [METRIC_REGISTRY[condition.metric]["scope"]]
    if condition.all is not None:
        scopes: list[str] = []
        for child in condition.all:
            scopes.extend(_condition_metric_scopes(child))
        return scopes
    if condition.any is not None:
        scopes = []
        for child in condition.any:
            scopes.extend(_condition_metric_scopes(child))
        return scopes
    if condition.not_ is not None:
        return _condition_metric_scopes(condition.not_)
    return []


def _primary_symbol(condition: Leaf | Group) -> str | None:
    if isinstance(condition, Leaf) and condition.symbol:
        return condition.symbol
    children: list[Leaf | Group] = []
    if isinstance(condition, Group):
        if condition.all:
            children = condition.all
        elif condition.any:
            children = condition.any
        elif condition.not_:
            children = [condition.not_]
    for child in children:
        sym = _primary_symbol(child)
        if sym:
            return sym
    return None


def _summarize_condition(condition: Leaf | Group) -> str:
    if isinstance(condition, Leaf):
        prefix = f"{condition.symbol} " if condition.symbol else ""
        return f"{prefix}{condition.metric} {condition.operator.value} {condition.value}"
    if condition.all is not None:
        parts = [_summarize_condition(c) for c in condition.all]
        return " AND ".join(parts)
    if condition.any is not None:
        parts = [_summarize_condition(c) for c in condition.any]
        return " OR ".join(f"({p})" for p in parts)
    if condition.not_ is not None:
        return f"NOT ({_summarize_condition(condition.not_)})"
    return "unknown condition"


def _report_to_markdown(
    report: RetroReport,
    processed_rules: list[dict[str, Any]],
    *,
    period: str,
    split: dict[str, Any],
) -> str:
    lines = [f"# Trade Retrospective ({period})", "", report.narrative, ""]

    if report.what_went_right:
        lines.append("## What went right")
        for item in report.what_went_right:
            lines.append(f"- {item}")
        lines.append("")

    if report.what_went_wrong:
        lines.append("## What went wrong")
        for item in report.what_went_wrong:
            lines.append(f"- {item}")
        lines.append("")

    if report.behavioral_observations:
        lines.append("## Behavioral observations")
        for item in report.behavioral_observations:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## Candidate rules (backtested OOS, NOT armed)")
    lines.append(f"*{_split_disclaimer(split)}*")
    lines.append("")
    if processed_rules:
        for rule in processed_rules:
            metrics = rule.get("backtest_metrics") or {}
            fire = _format_fire_display(metrics)
            lines.append(
                f"- **{rule['name']}** ({rule.get('condition_summary', '')}): "
                f"{fire}. {rule.get('rationale', '')}"
            )
    else:
        lines.append("- No valid candidate rules proposed.")
    lines.append("")
    return "\n".join(lines).strip()


def _render_retrospective_html(
    report: RetroReport,
    processed_rules: list[dict[str, Any]],
    *,
    period: str,
    split: dict[str, Any],
) -> str:
    title = html.escape(f"Trade Retrospective ({period})")
    narrative = _markdownish_to_html(report.narrative)

    def bullet_section(heading: str, items: list[str]) -> str:
        if not items:
            return ""
        lis = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return f"<section><h2>{html.escape(heading)}</h2><ul>{lis}</ul></section>"

    rules_rows = ""
    for rule in processed_rules:
        metrics = rule.get("backtest_metrics") or {}
        fire_display = html.escape(_format_fire_display(metrics))
        rules_rows += f"""
        <tr>
          <td>{html.escape(str(rule.get('name', '')))}</td>
          <td><code>{html.escape(str(rule.get('condition_summary', '')))}</code></td>
          <td>{fire_display}</td>
          <td>{html.escape(str(rule.get('rationale', '')))}</td>
        </tr>"""

    disclaimer = _split_disclaimer(split)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0f1115; --card: #171a21; --text: #e8eaed; --muted: #9aa0a6;
      --accent: #8ab4f8; --warn: #f9ab00;
    }}
    body {{ font-family: system-ui, sans-serif; background: var(--bg); color: var(--text);
           margin: 0; padding: 24px; line-height: 1.5; }}
    .card {{ background: var(--card); border-radius: 12px; padding: 24px; max-width: 900px;
             margin: 0 auto; }}
    h1 {{ margin-top: 0; font-size: 1.5rem; }}
    h2 {{ font-size: 1.1rem; color: var(--accent); margin-top: 1.5rem; }}
    .disclaimer {{ background: rgba(249,171,0,0.12); border: 1px solid var(--warn);
                  border-radius: 8px; padding: 12px; margin: 16px 0; font-size: 0.9rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ border-bottom: 1px solid #2a2f3a; padding: 8px; text-align: left;
              vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    code {{ font-size: 0.85em; }}
    ul {{ padding-left: 1.2rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <section><h2>Narrative</h2><div>{narrative}</div></section>
    {bullet_section("What went right", report.what_went_right)}
    {bullet_section("What went wrong", report.what_went_wrong)}
    {bullet_section("Behavioral observations", report.behavioral_observations)}
    <section>
      <h2>Candidate rules (backtested OOS, NOT armed)</h2>
      <div class="disclaimer">{html.escape(disclaimer)}</div>
      <table>
        <thead>
          <tr><th>Name</th><th>Condition</th><th>OOS fires</th><th>Rationale</th></tr>
        </thead>
        <tbody>
          {rules_rows or '<tr><td colspan="4">No valid candidate rules proposed.</td></tr>'}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def _markdownish_to_html(text: str) -> str:
    escaped = html.escape(text)
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br/>")
    return f"<p>{escaped}</p>"
