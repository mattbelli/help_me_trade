"""Per-cycle exit engine — evaluates open managed positions and dispatches sells.

Safety-critical: this is the first auto-sell path. Fail-closed on ambiguity; never bypass
the execution chokepoint; authoritative qty always comes from the live broker snapshot.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from brokers.market_stream import market_hub
from db.accounts import get_account
from db.base import DEFAULT_DB_PATH, now_iso
from db.earnings_calendar import earnings_dates_for
from db.managed_positions import close_position, list_open_positions, update_exit_state
from db.order_executions import get_execution
from db.paper_orders import get_proposal
from db.sqlite import get_latest_snapshot
from db.strategies import get_strategy
from research.strategy_features import _trading_days_until
from signals.exit_evaluators import ExitDecision, evaluate_dip_exit, evaluate_earnings_exit
from trading.execution_service import ExecutionService
from trading.models import OrderIntent, RiskRejectedError
from trading.proposals import create_phone_proposal

logger = logging.getLogger(__name__)

_EXIT_RETRY_THROTTLE_SECONDS = 300


def _parse_ts(iso_ts: str | None) -> datetime | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _age_seconds(iso_ts: str) -> float:
    dt = _parse_ts(iso_ts)
    if dt is None:
        return 1e9
    return (datetime.now(UTC) - dt).total_seconds()


def _snapshot_after(captured_at: str | None, other: str | None) -> bool:
    """True iff `captured_at` parses AND is strictly after `other`.

    Fail-closed: if either timestamp is missing/unparseable, returns False (the
    caller treats "can't prove the snapshot is newer" as not-newer).
    """
    cap = _parse_ts(captured_at)
    ref = _parse_ts(other)
    if cap is None or ref is None:
        return False
    return cap > ref


def _close_reason(row: dict[str, Any]) -> str:
    marker = (row.get("exit_context") or {}).get("pending_exit")
    if marker and marker.get("reason"):
        return str(marker["reason"])
    return "closed_externally"


def _exit_in_flight(
    row: dict[str, Any],
    *,
    snapshot_captured_at: str | None,
    db_path: Path,
) -> bool:
    """Double-sell defense — True while a prior exit dispatch is still active or throttled."""
    marker = (row.get("exit_context") or {}).get("pending_exit")
    if not marker:
        return False

    kind = marker.get("kind")
    ref = marker.get("ref")
    at = marker.get("at")

    # 1) Status check — promptly clears once the order/proposal resolves.
    if kind == "auto" and ref:
        ex = get_execution(ref, db_path=db_path)
        if ex and ex.get("status") in {"pending", "submitted"}:
            return True
    if kind == "propose" and ref:
        pr = get_proposal(ref, db_path=db_path)
        if pr and pr.get("status") in {"pending_phone", "approving"}:
            return True

    # 2) Snapshot-lag guard — NEVER re-fire until a snapshot captured strictly AFTER
    #    the last attempt exists. Until then this snapshot can't reflect the prior
    #    order (a reduce/close), so re-evaluating it could double-sell. Fail-closed:
    #    a missing/unparseable captured_at keeps us blocked.
    if at and not _snapshot_after(snapshot_captured_at, at):
        return True

    # 3) Throttle — covers dry_run / rejected / lost-ref where the snapshot advances
    #    but the position never changes (so guard 2 alone would let it re-fire).
    if at and _age_seconds(at) < _EXIT_RETRY_THROTTLE_SECONDS:
        return True

    return False


def _set_pending_marker(
    row: dict[str, Any],
    *,
    kind: str,
    ref: str,
    reason: str,
    action: str,
    qty: int,
    db_path: Path,
) -> None:
    ctx = {
        **(row.get("exit_context") or {}),
        "pending_exit": {
            "kind": kind,
            "ref": ref,
            "reason": reason,
            "action": action,
            "qty": qty,
            "at": now_iso(),
        },
    }
    update_exit_state(row["id"], exit_context=ctx, db_path=db_path)


def _strategy_kind(strategy_id: str) -> str | None:
    sid = strategy_id.lower()
    if "earnings" in sid:
        return "earnings"
    if "dip" in sid:
        return "dip"
    return None


async def _process_managed_position(
    row: dict[str, Any],
    *,
    broker_name: str,
    positions_by_symbol: dict[str, dict[str, Any]],
    snapshot: dict[str, Any] | None,
    db_path: Path,
) -> dict[str, Any]:
    position_id = row["id"]
    symbol = row["symbol"]

    # (A) Closure reconcile — position gone ⇒ close the row.
    if snapshot is None:
        return {"action": "skip", "reason": "no_snapshot", "position_id": position_id, "symbol": symbol}

    pos = positions_by_symbol.get(symbol.upper())
    gone = pos is None or float(pos.get("qty", 0)) <= 0 or pos.get("side") != "long"
    if gone:
        # Only conclude "gone" when the snapshot was captured strictly AFTER this
        # row's entry. Otherwise a BUY that just filled (managed row created) but
        # isn't in this still-pre-fill snapshot yet would be wrongly closed, silently
        # orphaning a live position from all exit management. Fail-closed: skip when
        # we can't prove the snapshot post-dates the entry.
        if not _snapshot_after(snapshot.get("captured_at"), row.get("entry_date")):
            return {
                "action": "skip",
                "reason": "entry_postdates_snapshot",
                "position_id": position_id,
                "symbol": symbol,
            }
        reason = _close_reason(row)
        close_position(position_id, reason=reason, db_path=db_path)
        return {
            "action": "closed",
            "reason": reason,
            "position_id": position_id,
            "symbol": symbol,
        }

    # (B) Evaluate — authoritative qty/price from LIVE snapshot, never row["qty"].
    entry_price = float(pos["avg_entry_price"])
    current_price = float(pos["current_price"])
    live_qty = float(pos["qty"])

    holding_days = _trading_days_until(row["entry_date"], now_iso())
    if holding_days < 0:
        holding_days = 0

    hwm = float(row["high_water_mark"]) if row.get("high_water_mark") else None
    kind = _strategy_kind(row["strategy_id"])
    decision: ExitDecision

    if kind == "earnings":
        decision = evaluate_earnings_exit(
            today=now_iso(),
            earnings_dates=earnings_dates_for(symbol, db_path=db_path),
            holding_days=holding_days,
            params=row["params"],
        )
    elif kind == "dip":
        decision = evaluate_dip_exit(
            entry_price=entry_price,
            current_price=current_price,
            bar_high=None,
            bar_low=None,
            high_water_mark=hwm,
            first_target_hit=row["first_target_hit"],
            holding_days=holding_days,
            params=row["params"],
        )
    else:
        return {
            "action": "skip",
            "reason": "unknown_kind",
            "strategy_id": row["strategy_id"],
            "position_id": position_id,
            "symbol": symbol,
        }

    # (C) Persist exit state ALWAYS (even on hold / in-flight).
    if decision.new_high_water_mark is not None or decision.new_first_target_hit is not None:
        update_exit_state(
            row["id"],
            high_water_mark=(
                str(decision.new_high_water_mark)
                if decision.new_high_water_mark is not None
                else None
            ),
            first_target_hit=decision.new_first_target_hit,
            db_path=db_path,
        )

    # (D) Hold ⇒ done.
    if decision.action == "hold":
        return {
            "action": "hold",
            "reason": decision.reason,
            "position_id": position_id,
            "symbol": symbol,
        }

    # (E) Scale/exit ⇒ whole-share qty from live position.
    if decision.action == "exit":
        exit_qty = int(math.floor(live_qty))
    else:
        exit_qty = int(math.floor(live_qty * decision.exit_fraction))

    if exit_qty < 1:
        return {
            "action": "skip",
            "reason": "qty_below_1",
            "position_id": position_id,
            "symbol": symbol,
            "decision_action": decision.action,
        }

    # (F) In-flight guard — no second sell while one is pending/throttled.
    if _exit_in_flight(
        row, snapshot_captured_at=snapshot.get("captured_at"), db_path=db_path
    ):
        return {
            "action": "skip",
            "reason": "in_flight",
            "position_id": position_id,
            "symbol": symbol,
        }

    # (G) Dispatch per exit_mode — auto goes ONLY through ExecutionService.submit.
    exit_mode = (
        (get_strategy(row["strategy_id"], db_path=db_path) or {}).get("exit_mode")
        or row.get("exit_mode")
        or "propose"
    )

    ref: str | None = None

    if exit_mode == "auto":
        intent = OrderIntent(
            proposal_id=f"exit_{row['id']}_{now_iso()}",
            session_id=None,
            symbol=symbol,
            side="sell",
            qty=Decimal(str(exit_qty)),
            order_type="market",
            limit_price=None,
            broker=broker_name,
            asset_class="us_equity",
            account_id=row["account_id"],
        )
        try:
            result = await ExecutionService.submit(intent, db_path=db_path)
        except RiskRejectedError as exc:
            # Throttle-mark even on rejection so a persistent reject (e.g. off-hours
            # no-quote, daily cap) can't re-submit every cycle; ref="" ⇒ the status
            # check is skipped and the throttle/snapshot-lag guard governs the retry.
            logger.warning("exit rejected for %s: %s", symbol, exc)
            _set_pending_marker(
                row, kind="auto", ref="", reason=f"rejected:{decision.reason}",
                action=decision.action, qty=exit_qty, db_path=db_path,
            )
            return {
                "action": "rejected",
                "reason": decision.reason,
                "error": str(exc),
                "position_id": position_id,
                "symbol": symbol,
            }
        except Exception as exc:
            # Any other failure (broker/network/db) must also back off, never hammer.
            logger.exception("exit submit errored for %s", symbol)
            _set_pending_marker(
                row, kind="auto", ref="", reason=f"error:{decision.reason}",
                action=decision.action, qty=exit_qty, db_path=db_path,
            )
            return {
                "action": "error",
                "reason": decision.reason,
                "error": str(exc),
                "position_id": position_id,
                "symbol": symbol,
            }
        ref = result.execution_id
        _set_pending_marker(
            row,
            kind="auto",
            ref=result.execution_id,
            reason=decision.reason,
            action=decision.action,
            qty=exit_qty,
            db_path=db_path,
        )
    elif exit_mode == "propose":
        res = await create_phone_proposal(
            {
                "symbol": symbol,
                "side": "sell",
                "qty": exit_qty,
                "order_type": "market",
                "account_id": row["account_id"],
            },
            source=f"exit:{row['strategy_id']}",
            db_path=db_path,
        )
        # create_phone_proposal returns the proposal row (status "pending_phone") on
        # success, or {"error": ...} on validation failure — NOT a "proposed" status.
        # Success = it minted an id and there's no error.
        if res.get("id") and not res.get("error"):
            ref = str(res["id"])
            _set_pending_marker(
                row,
                kind="propose",
                ref=ref,
                reason=decision.reason,
                action=decision.action,
                qty=exit_qty,
                db_path=db_path,
            )
        else:
            # Mark to throttle the retry so a failing proposal can't flood every cycle.
            logger.warning("exit proposal failed for %s: %s", symbol, res)
            _set_pending_marker(
                row, kind="propose", ref="", reason=f"failed:{decision.reason}",
                action=decision.action, qty=exit_qty, db_path=db_path,
            )
            return {
                "action": "propose_failed",
                "reason": decision.reason,
                "detail": res,
                "position_id": position_id,
                "symbol": symbol,
            }
    else:
        return {
            "action": "skip",
            "reason": "unknown_exit_mode",
            "exit_mode": exit_mode,
            "position_id": position_id,
            "symbol": symbol,
        }

    # (H) Notify the user — ALWAYS after successful dispatch.
    try:
        await market_hub.broadcast_alert(
            {
                "type": "alert",
                "source": "exit_engine",
                "rule_name": f"Exit: {row['strategy_id']}",
                "action": {
                    "type": "exit",
                    "mode": exit_mode,
                    "decision": decision.action,
                    "symbol": symbol,
                    "reason": decision.reason,
                    "qty": exit_qty,
                    "strategy_id": row["strategy_id"],
                    "detail": decision.detail,
                },
            }
        )
    except Exception:
        logger.exception("failed to broadcast exit alert")

    return {
        "action": decision.action,
        "mode": exit_mode,
        "reason": decision.reason,
        "qty": exit_qty,
        "ref": ref,
        "position_id": position_id,
        "symbol": symbol,
    }


async def run_exit_engine(*, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Walk open managed positions, evaluate exits, dispatch sells, reconcile closures."""
    rows = list_open_positions(db_path=db_path)
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_account[row["account_id"]].append(row)

    outcomes: list[dict[str, Any]] = []

    for account_id, account_rows in by_account.items():
        account = get_account(account_id, db_path=db_path)
        broker_name = (account or {}).get("broker", "").lower()
        if not account or not broker_name:
            logger.warning(
                "exit engine skipping account %s — missing account or broker",
                account_id,
            )
            for row in account_rows:
                outcomes.append(
                    {
                        "action": "skip",
                        "reason": "no_account",
                        "position_id": row["id"],
                        "symbol": row["symbol"],
                    }
                )
            continue

        snapshot = get_latest_snapshot(
            broker_name, account_id=account_id, db_path=db_path
        )
        # get_latest_snapshot falls back to the broker-wide latest run when this
        # account has no snapshot yet — which can be ANOTHER account's. Acting on the
        # wrong portfolio would make every row look "gone" and mass-close them, or
        # size a sell against the wrong position. Reject a mismatch (fail-closed).
        if snapshot and (snapshot.get("account") or {}).get("id") != account_id:
            logger.warning(
                "exit engine: snapshot account mismatch for %s — treating as no snapshot",
                account_id,
            )
            snapshot = None
        positions_by_symbol = {
            p["symbol"].upper(): p
            for p in (snapshot or {}).get("positions", [])
        }

        for row in account_rows:
            try:
                outcome = await _process_managed_position(
                    row,
                    broker_name=broker_name,
                    positions_by_symbol=positions_by_symbol,
                    snapshot=snapshot,
                    db_path=db_path,
                )
            except Exception:
                logger.exception(
                    "exit engine failed for managed position %s (%s)",
                    row.get("id"),
                    row.get("symbol"),
                )
                outcome = {
                    "action": "error",
                    "position_id": row.get("id"),
                    "symbol": row.get("symbol"),
                }
            outcomes.append(outcome)

    return outcomes
