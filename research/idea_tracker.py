"""Mechanical outcome sweep for setup_scan ideas — no LLM, no trading."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ai.research.data_sources import _bar_date, fetch_daily_bars_since
from db.idea_outcomes import list_sweepable, update_outcome

logger = logging.getLogger(__name__)

IDEA_TTL_DAYS = 10  # trading days after as_of before pre-trigger expiry
RESOLVE_TTL_DAYS = 20  # trading days after trigger before post-trigger expiry
# Consecutive bar-fetch FAILURES (None, not empty) before an idea is parked as
# data_gap. At the 30-min market-hours sweep this is ~one full trading day of
# outage — a Schwab WAF episode (hours) no longer trips it, and data_gap is now
# RECOVERABLE (a later successful fetch restores the idea), so nothing is lost.
DATA_GAP_FAIL_STREAK = 13

__all__ = ["sweep_open_ideas"]


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_as_of_date(as_of: str) -> date:
    raw = str(as_of or "").strip()
    if not raw:
        return datetime.now(UTC).date()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return datetime.now(UTC).date()
    return dt.date()


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def _add_trading_days(start: date, n: int) -> date:
    current = start
    added = 0
    while added < n:
        current += timedelta(days=1)
        if _is_trading_day(current):
            added += 1
    return current


def _entry_mid(entry_low: Decimal, entry_high: Decimal) -> Decimal:
    return (entry_low + entry_high) / Decimal(2)


def _pct_move(entry: Decimal, price: Decimal, *, favorable: bool) -> float:
    if entry <= 0:
        return 0.0
    if favorable:
        move = (price - entry) / entry
    else:
        move = (entry - price) / entry
    return float(move * Decimal(100))


def _update_excursions(
    *,
    entry: Decimal,
    bar_low: Decimal,
    bar_high: Decimal,
    mfe_pct: float | None,
    mae_pct: float | None,
) -> tuple[float, float]:
    fav = _pct_move(entry, bar_high, favorable=True)
    adv = _pct_move(entry, bar_low, favorable=False)
    new_mfe = fav if mfe_pct is None else max(mfe_pct, fav)
    new_mae = adv if mae_pct is None else max(mae_pct, adv)
    return new_mfe, new_mae


def _zone_touched(bar_low: Decimal, bar_high: Decimal, entry_low: Decimal, entry_high: Decimal) -> bool:
    return bar_low <= entry_high and bar_high >= entry_low


def _stop_hit(bar_low: Decimal, stop: Decimal) -> bool:
    return bar_low <= stop


def _target_hit(bar_high: Decimal, target: Decimal) -> bool:
    return bar_high >= target


def _process_pending(
    row: dict[str, Any],
    bars: list[Any],
    *,
    now_date: date,
) -> dict[str, Any] | None:
    entry_low = _parse_decimal(row["entry_low"])
    entry_high = _parse_decimal(row["entry_high"])
    stop = _parse_decimal(row["stop"])
    if entry_low is None or entry_high is None or stop is None:
        return {
            "status": "invalidated",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
            "notes": "invalid snapshot prices",
        }

    as_of_date = _parse_as_of_date(str(row["as_of"]))
    expiry_date = _add_trading_days(as_of_date, IDEA_TTL_DAYS)

    for bar in bars:
        bar_date = _bar_date(bar)
        if bar_date is None or bar_date < as_of_date:
            continue

        bar_low = _parse_decimal(getattr(bar, "low", None))
        bar_high = _parse_decimal(getattr(bar, "high", None))
        if bar_low is None or bar_high is None:
            continue

        if _zone_touched(bar_low, bar_high, entry_low, entry_high):
            entry = _entry_mid(entry_low, entry_high)
            mfe_pct, mae_pct = _update_excursions(
                entry=entry,
                bar_low=bar_low,
                bar_high=bar_high,
                mfe_pct=None,
                mae_pct=None,
            )
            # CONSERVATIVE same-bar rules (win% must never be flattered):
            # - zone + stop in one bar = most likely a real entry that stopped
            #   out -> LOST (counting it as invalidated would inflate win%).
            # - zone + target in one bar = the run to target may have happened
            #   BEFORE the dip into our zone -> only TRIGGERED; a win needs a
            #   later bar to prove it.
            if _stop_hit(bar_low, stop):
                return {
                    "status": "lost",
                    "triggered_at": bar_date.isoformat(),
                    "resolved_at": bar_date.isoformat(),
                    "resolution_price": str(stop),
                    "mfe_pct": mfe_pct,
                    "mae_pct": mae_pct,
                }
            return {
                "status": "triggered",
                "triggered_at": bar_date.isoformat(),
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        if _stop_hit(bar_low, stop):
            # Stop breached without ever touching the zone: price arrived by
            # crashing through, the planned entry never existed -> invalidated.
            return {
                "status": "invalidated",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(bar_low),
            }

        if bar_date >= expiry_date:
            return {
                "status": "expired",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": None,
            }

    if now_date >= expiry_date:
        return {
            "status": "expired",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
        }
    return None


def _process_triggered(
    row: dict[str, Any],
    bars: list[Any],
    *,
    now_date: date,
) -> dict[str, Any] | None:
    entry_low = _parse_decimal(row["entry_low"])
    entry_high = _parse_decimal(row["entry_high"])
    stop = _parse_decimal(row["stop"])
    targets = row.get("targets") or []
    target0 = _parse_decimal(targets[0]) if targets else None
    if entry_low is None or entry_high is None or stop is None:
        return {
            "status": "invalidated",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
            "notes": "invalid snapshot prices",
        }

    trigger_date = _parse_as_of_date(str(row.get("triggered_at") or row["as_of"]))
    expiry_date = _add_trading_days(trigger_date, RESOLVE_TTL_DAYS)
    entry = _entry_mid(entry_low, entry_high)
    mfe_pct = row.get("mfe_pct")
    mae_pct = row.get("mae_pct")

    started = False
    for bar in bars:
        bar_date = _bar_date(bar)
        if bar_date is None or bar_date < trigger_date:
            continue

        bar_low = _parse_decimal(getattr(bar, "low", None))
        bar_high = _parse_decimal(getattr(bar, "high", None))
        if bar_low is None or bar_high is None:
            continue

        started = True
        mfe_pct, mae_pct = _update_excursions(
            entry=entry,
            bar_low=bar_low,
            bar_high=bar_high,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
        )

        if _stop_hit(bar_low, stop):
            return {
                "status": "lost",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(bar_low),
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        # The trigger bar itself can never prove a win: on an ambiguous bar
        # (zone + target touched together) the run to target may have happened
        # BEFORE the dip into our zone — the same rule _process_pending applies.
        # Without this guard the next sweep re-reads the trigger bar and
        # converts every ambiguous bar into a "won" (win% inflation).
        if target0 is not None and bar_date > trigger_date and _target_hit(bar_high, target0):
            return {
                "status": "won",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(target0),
                "first_target_hit": 0,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        if bar_date >= expiry_date:
            return {
                "status": "expired",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": None,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

    if started or row.get("mfe_pct") is not None:
        pending_update: dict[str, Any] = {"mfe_pct": mfe_pct, "mae_pct": mae_pct}
        if now_date >= expiry_date:
            pending_update.update(
                {
                    "status": "expired",
                    "resolved_at": now_date.isoformat(),
                    "resolution_price": None,
                }
            )
            return pending_update
        if mfe_pct != row.get("mfe_pct") or mae_pct != row.get("mae_pct"):
            return pending_update
    elif now_date >= expiry_date:
        return {
            "status": "expired",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
        }
    return None


def _sweep_row(row: dict[str, Any], *, now_date: date, db_path: Path | None = None) -> str | None:
    as_of_date = _parse_as_of_date(str(row["as_of"]))
    bars = fetch_daily_bars_since(str(row["symbol"]), as_of_date)
    outcome_id = int(row["id"])
    status = str(row["status"])
    db_kwargs = {} if db_path is None else {"db_path": db_path}

    if bars is None:
        if status == "data_gap":
            # Already parked and the fetch is still failing — leave it parked.
            return None
        streak = int(row.get("fetch_fail_streak") or 0) + 1
        if streak >= DATA_GAP_FAIL_STREAK:
            update_outcome(
                outcome_id,
                status="data_gap",
                resolved_at=now_date.isoformat(),
                fetch_fail_streak=streak,
                notes="bars unavailable after repeated fetch failures",
                **db_kwargs,
            )
            return "data_gap"
        update_outcome(outcome_id, fetch_fail_streak=streak, **db_kwargs)
        return None

    if status == "data_gap":
        # Bars are back (fetch succeeded — empty or not): the outage is over, so
        # RESTORE the idea to its prior open status and let it resume tracking.
        # Daily bars mean the missed window is fully recoverable. Prior status is
        # derivable: triggered if it had already fired, else pending.
        status = "triggered" if row.get("triggered_at") else "pending"
        row = {**row, "status": status}
        update_outcome(
            outcome_id, status=status, fetch_fail_streak=0, **db_kwargs
        )

    if not bars:
        # Fetch succeeded but no trading session on/after as_of yet (weekend/
        # holiday). Benign — nothing to evaluate and NOT a data gap; clear any
        # failure streak so a closed market can never kill an idea.
        if int(row.get("fetch_fail_streak") or 0) > 0:
            update_outcome(outcome_id, fetch_fail_streak=0, **db_kwargs)
        return None

    _stamp_last_price(row, bars, outcome_id=outcome_id, db_kwargs=db_kwargs)

    patch: dict[str, Any] | None
    if status == "pending":
        patch = _process_pending(row, bars, now_date=now_date)
    else:
        patch = _process_triggered(row, bars, now_date=now_date)

    if not patch:
        if int(row.get("fetch_fail_streak") or 0) > 0:
            update_outcome(outcome_id, fetch_fail_streak=0, **db_kwargs)
        return None

    return _apply_update(outcome_id, status, patch, db_kwargs=db_kwargs)


def _stamp_last_price(
    row: dict[str, Any],
    bars: list[Any],
    *,
    outcome_id: int,
    db_kwargs: dict[str, Any],
) -> None:
    """Record the newest daily close so the widget can show how far price sits
    from the entry zone. At most one write per row per trading day (only when
    the value actually changes)."""
    last_bar = bars[-1]
    close = _parse_decimal(getattr(last_bar, "close", None))
    bar_date = _bar_date(last_bar)
    if close is None or bar_date is None:
        return
    if str(row.get("last_price") or "") == str(close):
        return
    update_outcome(
        outcome_id,
        last_price=str(close),
        last_price_at=bar_date.isoformat(),
        **db_kwargs,
    )


def _apply_update(
    outcome_id: int,
    current_status: str,
    patch: dict[str, Any],
    *,
    db_kwargs: dict[str, Any],
) -> str | None:
    new_status = str(patch.get("status", current_status))
    if new_status == current_status:
        update_outcome(
            outcome_id,
            mfe_pct=patch.get("mfe_pct"),
            mae_pct=patch.get("mae_pct"),
            fetch_fail_streak=0,
            **db_kwargs,
        )
        return "excursion_updates"

    update_outcome(
        outcome_id,
        status=new_status,
        triggered_at=patch.get("triggered_at"),
        resolved_at=patch.get("resolved_at"),
        resolution_price=patch.get("resolution_price"),
        first_target_hit=patch.get("first_target_hit"),
        mfe_pct=patch.get("mfe_pct"),
        mae_pct=patch.get("mae_pct"),
        notes=patch.get("notes"),
        fetch_fail_streak=0,
        **db_kwargs,
    )
    return new_status


def sweep_open_ideas(
    now: datetime | None = None,
    *,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Advance open idea outcomes against daily bars. Fail-soft per row."""
    now_dt = now or datetime.now(UTC)
    now_date = now_dt.date()
    db_kwargs = {} if db_path is None else {"db_path": db_path}
    counts = {
        "examined": 0,
        "triggered": 0,
        "won": 0,
        "lost": 0,
        "expired": 0,
        "invalidated": 0,
        "data_gap": 0,
        "excursion_updates": 0,
        "errors": 0,
    }

    for row in list_sweepable(**db_kwargs):
        counts["examined"] += 1
        try:
            result = _sweep_row(row, now_date=now_date, db_path=db_path)
        except Exception:
            counts["errors"] += 1
            logger.warning(
                "Idea sweep failed for outcome id=%s symbol=%s",
                row.get("id"),
                row.get("symbol"),
                exc_info=True,
            )
            continue

        if result is None:
            continue
        if result == "excursion_updates":
            counts["excursion_updates"] += 1
        elif result in counts:
            counts[result] += 1

    return counts
