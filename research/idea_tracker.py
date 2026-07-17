"""Mechanical outcome sweep for setup_scan ideas — no LLM, no trading."""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ai.research.data_sources import _bar_date, fetch_daily_bars_since
from config.idea_engine import GRADING_TIEBREAK
from db.idea_outcomes import list_open, list_sweepable, update_outcome
from db.lane_stats import bump, gauge
from trading.market_hours import _ET, is_after_close

logger = logging.getLogger(__name__)

IDEA_TTL_DAYS = 10  # trading days after as_of before pre-trigger expiry
RESOLVE_TTL_DAYS = 20  # trading days after trigger before post-trigger expiry
# Consecutive bar-fetch FAILURES (None, not empty) before an idea is parked as
# data_gap. At the 30-min market-hours sweep this is ~one full trading day of
# outage — a Schwab WAF episode (hours) no longer trips it, and data_gap is now
# RECOVERABLE (a later successful fetch restores the idea), so nothing is lost.
DATA_GAP_FAIL_STREAK = 13

# The benchmark leg (ret/spy/alpha + fill_status) is stamped only on rows that
# reach a graded terminal state. invalidated is NOT graded (the setup never
# existed) and triggered is still open, so neither gets a benchmark leg.
_GRADED_STATUSES = frozenset({"won", "lost", "expired"})

# Path-A horizon parsing (grading_basis='horizon_return'). Only a whole-number
# "Nd" trading-day count is honored ("9d" -> 9, "21d" -> 21); named templates
# ('exit_lead', 'earnings_exit') and anything else parse to None → the row is
# left OPEN, never resolved on a guessed date. Intraday forms ('30m', '2h') are
# already rejected at registration ([ADR-3]); None here is the belt-and-braces.
_HORIZON_DAYS_RE = re.compile(r"^\s*(\d+)\s*d\s*$", re.IGNORECASE)

# The benchmark symbol — one adjusted daily series per sweep (in-sweep cache).
_SPY_SYMBOL = "SPY"

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


def _first_evaluable_date(as_of: str) -> date:
    """The first session that can legitimately grade this idea.

    An idea minted after the close describes a session that is already history,
    so grading it against that day's bar resolves it on price action that
    PREDATES it. NTSK (2026-07-14): scanned at 17:44 ET on a +11% breakout day
    — open 12.48, low 12.40, close 14.27 — with entry zone 14.23-14.37 and stop
    13.59. The sweep replayed that same bar four minutes later, saw the 12.40
    morning low under the stop, and marked the idea LOST. That low happened
    hours before the breakout the idea was built on. The earliest session the
    idea could actually be traded in is the next trading day.
    """
    raw = str(as_of or "").strip()
    try:
        dt: datetime | None = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt is None or dt.tzinfo is None:
        # Date-only or unparseable — no clock to place against the close, so
        # keep the legacy same-day basis rather than invent a session boundary.
        return _parse_as_of_date(as_of)
    et_date = dt.astimezone(_ET).date()
    return _add_trading_days(et_date, 1) if is_after_close(dt) else et_date


def _is_trading_day(d: date) -> bool:
    # Real NYSE calendar (holidays included) — weekday()<5 alone let holidays
    # count as trading days, silently shortening every graded horizon
    # (GPT-5.6 audit finding 13).
    from trading.market_calendar import is_trading_day

    return is_trading_day(d)


def _add_trading_days(start: date, n: int) -> date:
    current = start
    added = 0
    while added < n:
        current += timedelta(days=1)
        if _is_trading_day(current):
            added += 1
    return current


def _trading_days_between(start: date, end: date) -> int:
    """Count trading days strictly after ``start`` up to and including ``end``.

    Used for the ``max_open_age_days`` gauge — how long a still-open idea has
    been alive, measured on the same trading-day clock the TTLs use.
    """
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if _is_trading_day(current):
            count += 1
    return count


def _horizon_trading_days(plan_horizon: Any) -> int | None:
    """Whole trading-day horizon parsed from an 'Nd' string, else None.

    None means 'do not resolve' — a named or unparseable horizon leaves the row
    open rather than guessing a resolution date (spec §4 item 5)."""
    if not plan_horizon:
        return None
    match = _HORIZON_DAYS_RE.match(str(plan_horizon))
    if match is None:
        return None
    n = int(match.group(1))
    return n if n >= 1 else None


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


def _signed_ret_pct(entry: Decimal | None, price: Decimal | None, direction: str) -> float | None:
    """Return the position return in %, SIGNED BY DIRECTION: a short whose price
    FALLS earns a positive return. None (never a fabricated 0) when the entry
    reference is missing or non-positive."""
    if entry is None or price is None or entry <= 0:
        return None
    if direction == "short":
        move = (entry - price) / entry
    else:
        move = (price - entry) / entry
    return float(move * Decimal(100))


def _spy_return(
    spy_by_date: dict[date, Decimal], start_date: date, end_date: date
) -> float | None:
    """SPY (market-long) return over [start_date, end_date] from ONE cached
    series: entry = first SPY close on/after start, exit = last close on/before
    end. None when either leg is missing — the benchmark stays NULL, never 0."""
    if not spy_by_date:
        return None
    entry: Decimal | None = None
    exit_close: Decimal | None = None
    for bar_date in sorted(spy_by_date):
        if entry is None and bar_date >= start_date:
            entry = spy_by_date[bar_date]
        if bar_date <= end_date:
            exit_close = spy_by_date[bar_date]
    if entry is None or exit_close is None or entry <= 0:
        return None
    return float((exit_close - entry) / entry * Decimal(100))


def _resolution_close(bars: list[Any], resolved_date: date) -> Decimal | None:
    """The last daily close on/before ``resolved_date`` — the exit reference for
    a time-expired row whose resolution_price is NULL (bars are ascending)."""
    chosen: Decimal | None = None
    for bar in bars:
        bar_date = _bar_date(bar)
        if bar_date is None:
            continue
        if bar_date <= resolved_date:
            close = _parse_decimal(getattr(bar, "close", None))
            if close is not None:
                chosen = close
        else:
            break
    return chosen


def _update_excursions(
    *,
    entry: Decimal,
    bar_low: Decimal,
    bar_high: Decimal,
    mfe_pct: float | None,
    mae_pct: float | None,
    direction: str,
) -> tuple[float, float]:
    """MFE/MAE as positive magnitudes, signed FROM the position's perspective:
    for a long, favorable = price up (bar_high), adverse = price down (bar_low);
    for a short, favorable = price down (bar_low), adverse = price up (bar_high).
    """
    if direction == "short":
        fav = _pct_move(entry, bar_low, favorable=False)
        adv = _pct_move(entry, bar_high, favorable=True)
    else:
        fav = _pct_move(entry, bar_high, favorable=True)
        adv = _pct_move(entry, bar_low, favorable=False)
    new_mfe = fav if mfe_pct is None else max(mfe_pct, fav)
    new_mae = adv if mae_pct is None else max(mae_pct, adv)
    return new_mfe, new_mae


def _zone_touched(bar_low: Decimal, bar_high: Decimal, entry_low: Decimal, entry_high: Decimal) -> bool:
    return bar_low <= entry_high and bar_high >= entry_low


def _stop_hit(bar_low: Decimal, bar_high: Decimal, stop: Decimal, direction: str) -> bool:
    # Long: stop sits BELOW the zone (bar_low breaches it). Short: stop sits
    # ABOVE the zone ([ADR-6] inverted geometry), so bar_high breaches it.
    if direction == "short":
        return bar_high >= stop
    return bar_low <= stop


def _target_hit(bar_low: Decimal, bar_high: Decimal, target: Decimal, direction: str) -> bool:
    # Long: target ABOVE the zone (bar_high reaches it). Short: target BELOW
    # ([ADR-6]), so bar_low reaches it.
    if direction == "short":
        return bar_low <= target
    return bar_high >= target


def _process_pending(
    row: dict[str, Any],
    bars: list[Any],
    *,
    now_date: date,
    direction: str,
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

    start_date = _first_evaluable_date(str(row["as_of"]))
    expiry_date = _add_trading_days(start_date, IDEA_TTL_DAYS)

    for bar in bars:
        bar_date = _bar_date(bar)
        if bar_date is None or bar_date < start_date:
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
                direction=direction,
            )
            # CONSERVATIVE same-bar rules (win% must never be flattered):
            # - zone + stop in one bar = most likely a real entry that stopped
            #   out -> LOST (counting it as invalidated would inflate win%).
            # - zone + target in one bar = the run to target may have happened
            #   BEFORE the dip into our zone -> only TRIGGERED; a win needs a
            #   later bar to prove it.
            if _stop_hit(bar_low, bar_high, stop, direction):
                return {
                    "status": "lost",
                    "triggered_at": bar_date.isoformat(),
                    "resolved_at": bar_date.isoformat(),
                    "resolution_price": str(stop),
                    "exit_reason": "stop",
                    "fill_status": "filled",
                    "mfe_pct": mfe_pct,
                    "mae_pct": mae_pct,
                }
            return {
                "status": "triggered",
                "triggered_at": bar_date.isoformat(),
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        if _stop_hit(bar_low, bar_high, stop, direction):
            # Stop breached without ever touching the zone: price arrived by
            # crashing through, the planned entry never existed -> invalidated.
            return {
                "status": "invalidated",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(bar_high if direction == "short" else bar_low),
            }

        if bar_date >= expiry_date:
            return {
                "status": "expired",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": None,
                "exit_reason": "time",
                "fill_status": "no_fill",
            }

    if now_date >= expiry_date:
        return {
            "status": "expired",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
            "exit_reason": "time",
            "fill_status": "no_fill",
        }
    return None


def _process_triggered(
    row: dict[str, Any],
    bars: list[Any],
    *,
    now_date: date,
    direction: str,
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
            direction=direction,
        )

        stop_now = _stop_hit(bar_low, bar_high, stop, direction)
        # The trigger bar itself can never prove a win: on an ambiguous bar
        # (zone + target touched together) the run to target may have happened
        # BEFORE the dip into our zone — the same rule _process_pending applies.
        # Without the bar_date > trigger_date guard the next sweep re-reads the
        # trigger bar and converts every ambiguous bar into a "won".
        target_now = (
            target0 is not None
            and bar_date > trigger_date
            and _target_hit(bar_low, bar_high, target0, direction)
        )
        # TIE-BREAK LAW (config.GRADING_TIEBREAK): one bar touching BOTH stop
        # and target resolves pessimistically — stop wins. Stated once here,
        # pinned by a test; never left implicit in the branch order.
        if stop_now and (not target_now or GRADING_TIEBREAK == "stop"):
            return {
                "status": "lost",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(bar_high if direction == "short" else bar_low),
                "exit_reason": "stop",
                "fill_status": "filled",
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        if target_now:
            return {
                "status": "won",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": str(target0),
                "exit_reason": "target",
                "fill_status": "filled",
                "first_target_hit": 0,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }

        if bar_date >= expiry_date:
            return {
                "status": "expired",
                "resolved_at": bar_date.isoformat(),
                "resolution_price": None,
                "exit_reason": "time",
                "fill_status": "filled",
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
                    "exit_reason": "time",
                    "fill_status": "filled",
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
            "exit_reason": "time",
            "fill_status": "filled",
        }
    return None


def _process_horizon(
    row: dict[str, Any],
    bars: list[Any],
    *,
    now_date: date,
    direction: str,
) -> dict[str, Any] | None:
    """Path-A grading_basis='horizon_return' (spec §4 item 5): resolve at the
    close of first_evaluable + N trading days. entry ref == entry_low == the
    registration ref price; won = signed ret_pct > 0; exit_reason=time; always
    a filled counterfactual. Never looks at stop/targets (stored empty)."""
    entry_ref = _parse_decimal(row["entry_low"])
    if entry_ref is None:
        return {
            "status": "invalidated",
            "resolved_at": now_date.isoformat(),
            "resolution_price": None,
            "notes": "invalid entry reference",
        }

    horizon_n = _horizon_trading_days(row.get("plan_horizon"))
    if horizon_n is None:
        # Unparseable/named horizon: leave open (never guess a date). The row
        # stays counted (examined + the max_open_age gauge), so a stuck horizon
        # source surfaces through the dead-grader alarm rather than vanishing.
        return None

    start_date = _first_evaluable_date(str(row["as_of"]))
    resolve_date = _add_trading_days(start_date, horizon_n)

    for bar in bars:
        bar_date = _bar_date(bar)
        if bar_date is None or bar_date < resolve_date:
            continue
        close = _parse_decimal(getattr(bar, "close", None))
        if close is None:
            continue
        ret = _signed_ret_pct(entry_ref, close, direction)
        status = "won" if (ret is not None and ret > 0) else "lost"
        return {
            "status": status,
            "resolved_at": bar_date.isoformat(),
            "resolution_price": str(close),
            "exit_reason": "time",
            "fill_status": "filled",
        }
    return None


def _stamp_benchmark(
    patch: dict[str, Any],
    *,
    entry_ref: Decimal | None,
    direction: str,
    first_evaluable: date,
    bars: list[Any],
    spy_by_date: dict[date, Decimal],
) -> None:
    """Stamp ret_pct / spy_ret_pct / alpha_pct onto a graded terminal patch.

    ret_pct = entry ref → resolution price, signed by direction. spy_ret_pct =
    market-long SPY return over [first_evaluable, resolved] from the one cached
    series (always stored market-long — the raw benchmark record). alpha is
    measured against the DIRECTION-CONSISTENT passive alternative: long → spy,
    short → −spy (i.e. alpha = ret − sign·spy). A direction-signed ret against
    a long-only benchmark let a beta-one short book ret−(−spy) ≈ +2·|spy|
    "alpha" in a crash — doubling the common shock instead of removing it
    (GPT-5.6 audit finding 10, 2026-07-17). Any leg that can't be computed
    stays NULL (never zero): a missing SPY series drops spy+alpha while ret
    still resolves."""
    resolved_raw = patch.get("resolved_at")
    if not resolved_raw:
        return
    try:
        resolved_date = date.fromisoformat(str(resolved_raw)[:10])
    except ValueError:
        return

    exit_price = _parse_decimal(patch.get("resolution_price"))
    if exit_price is None:
        # time-expired rows carry no resolution_price → use the resolved-day
        # close from the idea's OWN series (the same series the grade used).
        exit_price = _resolution_close(bars, resolved_date)

    ret = _signed_ret_pct(entry_ref, exit_price, direction) if exit_price is not None else None
    spy = _spy_return(spy_by_date, first_evaluable, resolved_date)
    patch["ret_pct"] = ret
    patch["spy_ret_pct"] = spy
    benchmark = (-spy if direction == "short" else spy) if spy is not None else None
    patch["alpha_pct"] = (
        (ret - benchmark) if (ret is not None and benchmark is not None) else None
    )


def _sweep_row(
    row: dict[str, Any],
    *,
    now_date: date,
    db_path: Path | None = None,
    spy_by_date: dict[date, Decimal] | None = None,
) -> str | None:
    spy_by_date = spy_by_date or {}
    first_evaluable = _first_evaluable_date(str(row["as_of"]))
    # Fetch from the first EVALUABLE session, not as_of: an idea minted after
    # tonight's close has no bar to read until tomorrow, and the tri-state
    # contract then reports [] ("no session yet") rather than a resolvable bar.
    bars = fetch_daily_bars_since(str(row["symbol"]), first_evaluable)
    outcome_id = int(row["id"])
    status = str(row["status"])
    direction = str(row.get("direction") or "long").strip().lower() or "long"
    grading_basis = str(row.get("grading_basis") or "plan").strip() or "plan"
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
    if grading_basis == "horizon_return":
        patch = _process_horizon(row, bars, now_date=now_date, direction=direction)
    elif status == "pending":
        patch = _process_pending(row, bars, now_date=now_date, direction=direction)
    else:
        patch = _process_triggered(row, bars, now_date=now_date, direction=direction)

    if not patch:
        if int(row.get("fetch_fail_streak") or 0) > 0:
            update_outcome(outcome_id, fetch_fail_streak=0, **db_kwargs)
        return None

    # A no_fill expiry never opened a position — a ret/alpha for it would be
    # counterfactual noise folded into cell alpha by any aggregation that
    # filters on status alone (review finding). NULL, never phantom.
    if (
        str(patch.get("status")) in _GRADED_STATUSES
        and patch.get("fill_status") != "no_fill"
    ):
        entry_low = _parse_decimal(row["entry_low"])
        entry_high = _parse_decimal(row["entry_high"])
        if grading_basis == "horizon_return":
            entry_ref = entry_low
        elif entry_low is not None and entry_high is not None:
            entry_ref = _entry_mid(entry_low, entry_high)
        else:
            entry_ref = None
        _stamp_benchmark(
            patch,
            entry_ref=entry_ref,
            direction=direction,
            first_evaluable=first_evaluable,
            bars=bars,
            spy_by_date=spy_by_date,
        )

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
        ret_pct=patch.get("ret_pct"),
        spy_ret_pct=patch.get("spy_ret_pct"),
        alpha_pct=patch.get("alpha_pct"),
        exit_reason=patch.get("exit_reason"),
        fill_status=patch.get("fill_status"),
        fetch_fail_streak=0,
        **db_kwargs,
    )
    return new_status


def _fetch_spy_series(rows: list[dict[str, Any]]) -> dict[date, Decimal]:
    """One SPY adjusted daily series per sweep, keyed date → close. Fail-soft:
    a fetch failure yields {} so every row still resolves on its own series with
    a NULL benchmark leg (spec §4 item 1)."""
    try:
        starts = [_first_evaluable_date(str(r.get("as_of") or "")) for r in rows]
        if not starts:
            return {}
        bars = fetch_daily_bars_since(_SPY_SYMBOL, min(starts))
        if not bars:
            return {}
        out: dict[date, Decimal] = {}
        for bar in bars:
            bar_date = _bar_date(bar)
            close = _parse_decimal(getattr(bar, "close", None))
            if bar_date is not None and close is not None:
                out[bar_date] = close
        return out
    except Exception:
        logger.warning("SPY benchmark fetch failed for idea sweep", exc_info=True)
        return {}


def _record_lane_meters(
    *,
    now_date: date,
    graded: int,
    data_gap: int,
    errors: int,
    examined: int,
    db_kwargs: dict[str, Any],
) -> None:
    """Fail-soft lane meters (spec §4 item 6, §5). Counters use bump (additive);
    max_open_age_days uses gauge (point-in-time — bump would inflate it).
    Synthetic canary rows are already excluded: the counts passed in count only
    non-synthetic resolutions and list_open filters synthetic=0.

    ``swept`` is the sweep heartbeat — bumped once per completed sweep,
    unconditionally. Grading is legitimately lumpy (ideas mature on 5-21
    trading-day horizons), so liveness rules key on swept, never on graded
    (review HIGH: a healthy-but-quiet grader must not page as dead)."""
    try:
        bump("idea_tracker", "swept", 1, **db_kwargs)
        if examined:
            bump("idea_tracker", "examined", examined, **db_kwargs)
        if graded:
            bump("idea_tracker", "graded", graded, **db_kwargs)
        if data_gap:
            bump("idea_tracker", "data_gap", data_gap, **db_kwargs)
        if errors:
            bump("idea_tracker", "errors", errors, **db_kwargs)
        open_rows = list_open(**db_kwargs)
        max_age = max(
            (
                _trading_days_between(
                    _first_evaluable_date(str(r.get("as_of") or "")), now_date
                )
                for r in open_rows
            ),
            default=0,
        )
        gauge("idea_tracker", "max_open_age_days", float(max_age), **db_kwargs)
    except Exception:
        logger.warning("idea_tracker lane meters failed", exc_info=True)


def sweep_open_ideas(
    now: datetime | None = None,
    *,
    db_path: Path | None = None,
    transitions: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Advance open idea outcomes against daily bars. Fail-soft per row.

    When ``transitions`` is passed, real status changes (triggered/won/lost/
    expired/invalidated — not excursion updates or data_gap parking) are
    appended to it so the caller can notify. Synthetic (canary) rows sweep and
    resolve like any other but are excluded from transitions, the graded/
    data_gap/errors lane counters, and the max_open_age gauge, so the canary can
    never make a dead lane look alive."""
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

    rows = list(list_sweepable(**db_kwargs))
    spy_by_date = _fetch_spy_series(rows) if rows else {}

    graded_nonsynth = 0
    data_gap_nonsynth = 0
    errors_nonsynth = 0

    for row in rows:
        is_synth = int(row.get("synthetic") or 0) == 1
        counts["examined"] += 1
        try:
            result = _sweep_row(
                row, now_date=now_date, db_path=db_path, spy_by_date=spy_by_date
            )
        except Exception:
            counts["errors"] += 1
            if not is_synth:
                errors_nonsynth += 1
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
            if not is_synth:
                if result in _GRADED_STATUSES:
                    graded_nonsynth += 1
                elif result == "data_gap":
                    data_gap_nonsynth += 1
            if transitions is not None and result != "data_gap" and not is_synth:
                transitions.append(
                    {
                        "id": row.get("id"),
                        "symbol": row.get("symbol"),
                        "status": result,
                        "origin": row.get("origin"),
                        "entry_low": row.get("entry_low"),
                        "entry_high": row.get("entry_high"),
                        "stop": row.get("stop"),
                    }
                )

    _record_lane_meters(
        now_date=now_date,
        graded=graded_nonsynth,
        data_gap=data_gap_nonsynth,
        errors=errors_nonsynth,
        examined=int(counts.get("examined", 0)),
        db_kwargs=db_kwargs,
    )

    return counts
