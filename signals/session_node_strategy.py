"""Live session-node reversion scan — propose-only SPY node touches (human-approved)."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import floor
from pathlib import Path
from typing import Any

from brokers.alpaca import StockBar
from brokers.factory import get_market_data_provider
from config.settings import (
    is_kill_switch_active,
    short_selling_allowed,
    trading_config,
)
from db.accounts import default_execution_account, get_account
from db.base import DEFAULT_DB_PATH
from db.managed_positions import list_open_positions
from db.paper_orders import list_pending_proposals
from db.session_node import claim, forget, get_payload, mark
from db.sqlite import get_latest_snapshot
from db.strategies import get_strategy
from research.bars import RESEARCH_DATA_DIR
from research.intraday_bars import IntradayBar
from research.intraday_profile import segment_globex_sessions, segment_rth_sessions, to_ny
from research.session_node_signal import (
    OpenPosition,
    ReferenceNodes,
    compute_reference_nodes,
    detect_exit_prompts,
    detect_node_triggers,
)
from trading.market_hours import is_market_open
from trading.order_proposal import build_order_proposal_from_args, order_intent_from_fields
from trading.proposals import create_phone_proposal
from trading.risk_engine import RiskEngine, _live_market_price

logger = logging.getLogger(__name__)

RULE_ID = "session_node_spy"
SYMBOL = "SPY"
SESSION_NODE_NOTIONAL_USD = 500.0
LOG_PATH = RESEARCH_DATA_DIR / "session_node_log.csv"
_BAR_DAYS = 5
_FUTURES_BAR_DAYS = 10  # ~3 Globex sessions in 5 calendar days; 10 gives a comfortable buffer
_BUYING_POWER_BUFFER = Decimal("0.95")
_STRUCTURE_TAG = "structure-reaction · low-confidence · human-discretion"

_LEVEL_KINDS: dict[str, str] = {
    "POC": "poc",
    "VAH": "resistance",
    "VAL": "support",
    "PDH": "prior_high",
    "PDL": "prior_low",
}


@dataclass(frozen=True)
class SessionNodeContext:
    nodes: ReferenceNodes
    today_bars: list[IntradayBar]
    all_bars: list[IntradayBar]
    session_date: date


_LOG_COLUMNS = (
    "ts",
    "kind",
    "session_date",
    "node_name",
    "direction",
    "node_price",
    "touch_target",
    "proposal_id",
    "position_id",
    "context",
)


def _stockbar_to_intraday(bar: StockBar) -> IntradayBar:
    time_str = bar.time.strip()
    if time_str.endswith("Z"):
        time_str = time_str[:-1] + "+00:00"
    return {
        "t": time_str,
        "o": float(bar.open),
        "h": float(bar.high),
        "l": float(bar.low),
        "c": float(bar.close),
        "v": float(bar.volume),
    }


def _nodes_to_dict(nodes: ReferenceNodes) -> dict[str, Any]:
    return {
        "session_date": nodes.session_date.isoformat(),
        "poc": nodes.poc,
        "vah": nodes.vah,
        "val": nodes.val,
        "pdh": nodes.pdh,
        "pdl": nodes.pdl,
    }


def _nodes_from_dict(data: dict[str, Any]) -> ReferenceNodes | None:
    try:
        return ReferenceNodes(
            session_date=date.fromisoformat(str(data["session_date"])),
            poc=float(data["poc"]),
            vah=float(data["vah"]),
            val=float(data["val"]),
            pdh=float(data["pdh"]),
            pdl=float(data["pdl"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _prop_dedup_key(session_date: date, node_name: str, trigger_ts: str) -> str:
    return f"prop:{session_date.isoformat()}:{node_name}:{trigger_ts}"


def _exit_dedup_key(position_id: str, kind: str) -> str:
    return f"exit:{position_id}:{kind}"


def _append_log_row(row: dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_header = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
        with LOG_PATH.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=_LOG_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow({col: row.get(col, "") for col in _LOG_COLUMNS})
    except Exception:
        logger.exception("Failed to append session-node log row")


def _open_position_from_managed(row: dict[str, Any]) -> OpenPosition | None:
    params = row.get("params") or {}
    nodes = _nodes_from_dict(params.get("nodes") or {})
    if nodes is None:
        return None
    try:
        return OpenPosition(
            entry_ts=str(params["entry_ts"]),
            entry_price=float(params["entry_price"]),
            direction=str(params.get("direction") or "long"),
            entry_node_name=str(params["entry_node_name"]),
            entry_node_price=float(params["entry_node_price"]),
            nodes=nodes,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _size_proposal_args(
    *,
    side: str = "buy",
    notional_usd: float,
    account_id: str,
    broker: str,
    db_path: Path,
) -> dict[str, Any] | None:
    price = await _live_market_price(SYMBOL)
    if price is None or price <= 0:
        return None
    price_dec = Decimal(str(price))

    qty = floor(Decimal(str(notional_usd)) / price_dec)
    snapshot = get_latest_snapshot(broker, account_id=account_id, db_path=db_path)
    if snapshot is not None:
        # Both longs and shorts consume buying power — cap the size either way.
        buying_power = Decimal(str(snapshot["account"]["buying_power"]))
        cap_qty = floor(buying_power * _BUYING_POWER_BUFFER / price_dec)
        qty = min(qty, cap_qty)
    if qty < 1:
        return None

    args: dict[str, Any] = {
        "symbol": SYMBOL,
        "side": side,
        "qty": int(qty),
        "order_type": "market",
        "account_id": account_id,
    }
    built = build_order_proposal_from_args(args, db_path=db_path)
    if "error" in built:
        return None

    intent = order_intent_from_fields(
        built["intent_fields"],
        proposal_id="precheck",
        session_id=None,
        db_path=db_path,
    )
    risk = await RiskEngine.validate(intent, db_path=db_path)
    if not risk.passed:
        return None
    return args


async def _propose_trigger(
    trigger,
    *,
    nodes: ReferenceNodes,
    notional_usd: float,
    account_id: str,
    broker: str,
    db_path: Path,
) -> dict[str, Any] | None:
    # Support-node reversion → buy (long); resistance-node reversion → sell
    # (short). Shorts only reach here when shorting is enabled (gated upstream).
    side = "sell" if trigger.direction == "short" else "buy"
    dedup_key = _prop_dedup_key(nodes.session_date, trigger.node_name, trigger.ts)
    # Claim the key ATOMICALLY before the await, so this deterministic trigger
    # can never be double-proposed. Release it (forget) on any path that does NOT
    # create a proposal, so the next scan can re-evaluate.
    if not claim(dedup_key, "propose", db_path=db_path):
        return None

    args = await _size_proposal_args(
        side=side,
        notional_usd=notional_usd,
        account_id=account_id,
        broker=broker,
        db_path=db_path,
    )
    if args is None:
        forget(dedup_key, db_path=db_path)
        return None

    context = f"{trigger.context} · {_STRUCTURE_TAG}"
    row = await create_phone_proposal(
        args,
        source="session_node",
        rule_id=RULE_ID,
        score=False,
        db_path=db_path,
    )
    if "error" in row:
        forget(dedup_key, db_path=db_path)
        return None

    entry_ctx = {
        "entry_ts": trigger.ts,
        "entry_price": trigger.touch_price,
        "direction": trigger.direction,
        "entry_node_name": trigger.node_name,
        "entry_node_price": trigger.node_price,
        "nodes": _nodes_to_dict(nodes),
        "target_price": trigger.target_price,
    }
    mark(
        dedup_key,
        "propose",
        payload={"proposal_id": row["id"], "entry_context": entry_ctx},
        db_path=db_path,
    )
    mark(
        f"ctx:{row['id']}",
        "entry_context",
        payload=entry_ctx,
        db_path=db_path,
    )

    _append_log_row(
        {
            "ts": trigger.ts,
            "kind": "propose",
            "session_date": nodes.session_date.isoformat(),
            "node_name": trigger.node_name,
            "direction": trigger.direction,
            "node_price": trigger.node_price,
            "touch_target": f"{trigger.touch_price}->{trigger.target_price}",
            "proposal_id": row["id"],
            "context": context,
        }
    )
    return {
        "kind": "propose",
        "proposal_id": row["id"],
        "node_name": trigger.node_name,
        "trigger_ts": trigger.ts,
    }


async def _push_exit_prompts(
    *,
    all_bars: list[IntradayBar],
    db_path: Path,
) -> list[dict[str, Any]]:
    from signals.push import send_alert_push

    acted: list[dict[str, Any]] = []
    open_rows = [
        row
        for row in list_open_positions(db_path=db_path)
        if row.get("strategy_id") == RULE_ID and row.get("symbol") == SYMBOL
    ]
    for row in open_rows:
        position = _open_position_from_managed(row)
        if position is None:
            continue
        bars_since_entry = [bar for bar in all_bars if bar["t"] > position.entry_ts]
        try:
            prompts = detect_exit_prompts(position, bars_since_entry)
        except Exception:
            logger.exception(
                "Exit prompt detection failed for managed position %s", row["id"]
            )
            continue

        for prompt in prompts:
            dedup_key = _exit_dedup_key(str(row["id"]), prompt.kind)
            # Atomic claim before the await — never push the same prompt twice.
            if not claim(dedup_key, "exit_prompt", db_path=db_path):
                continue
            try:
                await send_alert_push(
                    title=f"SPY exit prompt ({prompt.kind})",
                    body=prompt.message,
                    data={
                        "type": "exit_prompt",
                        "symbol": SYMBOL,
                        "position_id": row["id"],
                        "kind": prompt.kind,
                    },
                    db_path=db_path,
                )
            except Exception:
                logger.exception(
                    "Failed to push exit prompt %s for position %s",
                    prompt.kind,
                    row["id"],
                )
                forget(dedup_key, db_path=db_path)
                continue

            _append_log_row(
                {
                    "ts": prompt.ts,
                    "kind": "exit_prompt",
                    "session_date": position.nodes.session_date.isoformat(),
                    "node_name": position.entry_node_name,
                    "direction": position.direction,
                    "node_price": position.entry_node_price,
                    "touch_target": prompt.price,
                    "position_id": row["id"],
                    "context": prompt.message,
                }
            )
            acted.append(
                {
                    "kind": "exit_prompt",
                    "position_id": row["id"],
                    "prompt_kind": prompt.kind,
                }
            )
    return acted


async def _fetch_rth_sessions(
    symbol: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[list[IntradayBar], dict[date, list[IntradayBar]]] | None:
    """Fetch 1-min bars and segment into sessions.  Returns (all_bars, sessions).

    For futures symbols (starting with '/') uses the full CME Globex session (~23h)
    via ``segment_globex_sessions``; for equities keeps the RTH window (09:30–16:00 ET)
    via ``segment_rth_sessions`` — behaviour is byte-identical to before for equities.
    Futures look back 10 calendar days (vs 5) so there is always a complete prior session
    even accounting for the Sunday 18:00 ET open and weekend gaps.
    """
    del db_path  # reserved for future DB-backed bar sources
    sym = symbol.strip().upper()
    if not sym:
        return None

    is_futures = sym.startswith("/")
    bar_days = _FUTURES_BAR_DAYS if is_futures else _BAR_DAYS

    provider = get_market_data_provider()
    bars_by_symbol = await provider.aget_stock_bars_batch(
        [sym], timeframe="1Min", days=bar_days
    )
    raw = bars_by_symbol.get(sym, [])
    if not raw:
        return None

    all_bars = [_stockbar_to_intraday(bar) for bar in raw]
    sessions = segment_globex_sessions(all_bars) if is_futures else segment_rth_sessions(all_bars)
    if not sessions:
        return None
    return all_bars, sessions


async def load_session_node_context(
    symbol: str = SYMBOL,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> SessionNodeContext | None:
    """Fetch 1-min bars, segment RTH sessions, and compute prior-session reference nodes."""
    fetched = await _fetch_rth_sessions(symbol, db_path=db_path)
    if fetched is None:
        return None
    all_bars, sessions = fetched

    today = to_ny(all_bars[-1]["t"]).date()
    prior_dates = sorted(session_date for session_date in sessions if session_date < today)
    if not prior_dates:
        return None

    prior_bars = sessions[prior_dates[-1]]
    today_bars = sessions.get(today, [])
    nodes = compute_reference_nodes(prior_bars)
    if nodes is None:
        return None

    return SessionNodeContext(
        nodes=nodes,
        today_bars=today_bars,
        all_bars=all_bars,
        session_date=nodes.session_date,
    )


async def compute_session_levels(
    symbol: str = SYMBOL,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    """Return the most-recent COMPLETED session's node levels for chart/report consumers.

    The live scan references the *prior* session because the current one is still
    forming. Display/report consumers want the latest FINISHED session: mid-session
    that's the prior day; after the close / pre-market / weekend it's the session that
    just completed. Keyed off is_market_open() so the levels are never a day stale.
    """
    fetched = await _fetch_rth_sessions(symbol, db_path=db_path)
    if fetched is None:
        return None
    _all_bars, sessions = fetched

    dates = sorted(sessions)
    if is_market_open():
        # Current session is still forming — reference the prior COMPLETED one.
        if len(dates) < 2:
            return None
        ref_date = dates[-2]
    else:
        # Market closed (after-hours / pre-market / weekend): the latest session is done.
        ref_date = dates[-1]

    nodes = compute_reference_nodes(sessions[ref_date])
    if nodes is None:
        return None

    sym = symbol.strip().upper()
    levels = [
        {"name": name, "price": price, "kind": _LEVEL_KINDS[name]}
        for name, price in (
            ("POC", nodes.poc),
            ("VAH", nodes.vah),
            ("VAL", nodes.val),
            ("PDH", nodes.pdh),
            ("PDL", nodes.pdl),
        )
    ]
    return {
        "symbol": sym,
        "session_date": nodes.session_date.isoformat(),
        "levels": levels,
    }


async def run_session_node_scan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Propose-only session-node scan. Never submits orders — phone approval only."""
    acted: list[dict[str, Any]] = []
    try:
        strat = get_strategy(RULE_ID, db_path=db_path)
        if strat is None or not strat.get("enabled"):
            return []

        if not is_market_open():
            return []

        cfg = trading_config()
        if not cfg.get("agent_proposals_enabled", True):
            return []
        if is_kill_switch_active():
            return []

        strat_account_id = strat.get("account_id")
        if strat_account_id:
            account = get_account(str(strat_account_id), db_path=db_path)
        else:
            account = default_execution_account(db_path=db_path)
        if account is None or not account.get("execution_capable"):
            return []
        account_id = str(account["id"])
        broker = str(account["broker"]).strip().lower()

        pending = list_pending_proposals(account_id=account_id, db_path=db_path)
        max_pending = cfg.get("max_pending_agent_proposals", 3)
        if len(pending) >= max_pending:
            return []
        if any(str(p.get("symbol", "")).upper() == SYMBOL for p in pending):
            return []

        notional_raw = strat.get("notional_usd", SESSION_NODE_NOTIONAL_USD)
        try:
            notional_usd = float(notional_raw)
        except (TypeError, ValueError):
            notional_usd = SESSION_NODE_NOTIONAL_USD
        if notional_usd <= 0:
            return []

        ctx = await load_session_node_context(SYMBOL, db_path=db_path)
        if ctx is None or not ctx.today_bars:
            return []

        nodes = ctx.nodes
        today_bars = ctx.today_bars
        all_bars = ctx.all_bars

        # Short triggers are only proposed when shorting is explicitly enabled
        # (paper/dry_run); otherwise they're skipped + logged (the no-short
        # default). Longs always propose.
        shorts_on = short_selling_allowed()

        triggers = detect_node_triggers(today_bars, nodes)
        for trigger in triggers:
            if trigger.direction == "short" and not shorts_on:
                logger.info(
                    "Session-node skip short at %s (%s) — shorting disabled",
                    trigger.node_name,
                    trigger.ts,
                )
                _append_log_row(
                    {
                        "ts": trigger.ts,
                        "kind": "skip_short",
                        "session_date": nodes.session_date.isoformat(),
                        "node_name": trigger.node_name,
                        "direction": trigger.direction,
                        "node_price": trigger.node_price,
                        "touch_target": trigger.target_price,
                        "context": trigger.context,
                    }
                )
                acted.append(
                    {
                        "kind": "skip_short",
                        "node_name": trigger.node_name,
                        "trigger_ts": trigger.ts,
                    }
                )
                continue

            if trigger.direction not in ("long", "short"):
                continue

            try:
                result = await _propose_trigger(
                    trigger,
                    nodes=nodes,
                    notional_usd=notional_usd,
                    account_id=account_id,
                    broker=broker,
                    db_path=db_path,
                )
                if result is not None:
                    acted.append(result)
            except Exception:
                logger.exception(
                    "Session-node proposal failed for %s at %s",
                    trigger.node_name,
                    trigger.ts,
                )

        try:
            acted.extend(await _push_exit_prompts(all_bars=all_bars, db_path=db_path))
        except Exception:
            logger.exception("Session-node exit prompt pass failed")
    except Exception:
        logger.exception("Session-node scan failed")
    return acted


def record_session_node_fill(
    execution_row: dict[str, Any],
    *,
    filled_qty: float | None,
    status: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Open a managed position for a filled session-node proposal (bookkeeping only)."""
    from db.managed_positions import record_open_position
    from db.paper_orders import get_proposal

    try:
        if status not in {"filled", "partially_filled"}:
            return None
        # Accept buy (long entry) and sell (short entry). The strict rule_id
        # gate below is what keeps this no-op'ing on every non-session-node fill.
        if str(execution_row.get("side", "")).strip().lower() not in ("buy", "sell"):
            return None
        proposal_id = execution_row.get("proposal_id")
        if not proposal_id:
            return None
        proposal = get_proposal(str(proposal_id), db_path=db_path)
        if proposal is None or proposal.get("rule_id") != RULE_ID:
            return None

        ctx = get_payload(f"ctx:{proposal_id}", db_path=db_path)
        if not ctx:
            return None

        account_id = execution_row.get("account_id") or proposal.get("account_id")
        if not account_id:
            account = default_execution_account(db_path=db_path)
            account_id = account["id"] if account else None
        if not account_id:
            return None

        params = dict(ctx)
        params["entry_price"] = float(params.get("entry_price", 0))
        params["entry_ts"] = str(params.get("entry_ts") or execution_row.get("created_at"))

        qty = str(filled_qty) if filled_qty is not None else None
        return record_open_position(
            account_id=str(account_id),
            symbol=SYMBOL,
            strategy_id=RULE_ID,
            qty=qty,
            exit_mode="propose",
            params=params,
            entry_execution_id=str(execution_row.get("id") or "") or None,
            db_path=db_path,
        )
    except Exception:
        logger.exception("Failed to record session-node managed position from fill")
        return None
