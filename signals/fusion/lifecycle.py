"""Consolidated periodic lifecycle sweep for the Fusion Evidence Bus.

The intake loop (``fusion_intake``) is event-driven — it only touches an episode
when a fresh dot arrives, so a silent or post-restart episode is never
re-examined. This sweep is the authoritative periodic evaluator (Fusion Horizon
Redesign §2.4/§2.5): for every OPEN episode it recomputes the decayed masses and
closes it on the first close rule that fires; for every ARMED idea it fires the
trigger (before the TTL check), expires it into a counterfactual grade, or
cascades an episode-invalidation.

Pure DB-driven and idempotent: it reloads open episodes + armed ideas from the
DB every pass and recomputes masses from the append-only ``evidence_dots`` log —
no memory state, fully rehydration-safe. A close/transition is a single guarded
UPDATE (``WHERE ... = <expected>``), so a crash mid-pass simply re-evaluates next
pass. Because all fusion writes are single-process, ordering is deterministic and
there is no cross-process race.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from config.idea_engine import (
    FUSION_EPISODE_DECAY_FLOOR,
    FUSION_EPISODE_DECAY_SESSIONS,
    FUSION_EPISODE_INVAL_ATR,
    FUSION_EPISODE_TTL_SESSIONS,
    FUSION_TRIGGER_TTL_IMPULSE_HOURS,
    FUSION_TRIGGER_TTL_THESIS_SESSIONS,
)
from db import fusion as fusion_db
from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.idea_outcomes import init_idea_outcomes_tables, update_outcome
from research.idea_tracker import grade_counterfactual
from signals.fusion.scorer import score_decayed
from trading.market_calendar import is_trading_day
from trading.market_hours import _ET

logger = logging.getLogger(__name__)

PriceLookup = Callable[[str], Any]

# lifecycle_state values written on the arming axis (spec §3). `trigger_fired` is
# the renamed `triggered` state (avoids the collision with the grader's mechanical
# status='triggered' = entry-zone touched).
_LC_ARMED = "armed"
_LC_BORN = "born"

# The mechanical grader advances `status` on the SAME rows this sweep selects by
# `lifecycle_state`, in separate transactions — so every outcome-bearing write
# here is compare-and-swap on the statuses the grader still considers active
# (Round-3 review finding 1: without the guard, the cascade can clobber a row
# the grader resolved between our read and our write).
_MECH_ACTIVE = ("pending", "data_gap")
_MECH_TERMINAL = frozenset({"won", "lost", "expired", "invalidated"})


# --------------------------------------------------------------------------- #
# Parsing + trading-day helpers
# --------------------------------------------------------------------------- #
def _dec(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_dt(ts: Any) -> datetime | None:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _add_trading_days(start, n: int):
    current = start
    added = 0
    while added < n:
        current += timedelta(days=1)
        if is_trading_day(current):
            added += 1
    return current


def _trading_days_between(start, end) -> int:
    """Trading days strictly after ``start`` up to and including ``end`` (ET dates)."""
    if end <= start:
        return 0
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_trading_day(current):
            count += 1
    return count


def _recent_session_opens(now_dt: datetime, n: int) -> list[datetime]:
    """The ``n`` most recent trading-day 09:30-ET session opens at/before ``now``."""
    et = now_dt.astimezone(_ET)
    opens: list[datetime] = []
    day = et.date()
    guard = 0
    while len(opens) < n and guard < 90:
        guard += 1
        if is_trading_day(day):
            so = datetime(day.year, day.month, day.day, 9, 30, tzinfo=_ET)
            if so <= et:
                opens.append(so)
        day -= timedelta(days=1)
    return opens


def _latest_price(symbol: str, price_lookup: PriceLookup | None, conn: Any) -> Decimal | None:
    """Latest price for *symbol*: the injected intake price cache first (prod:
    ``_fusion_price_from_cache``), else the most recent stamped dot's
    ``trigger_price`` from the DB — the same source dots are stamped from, so the
    sweep stays rehydration-safe when no live cache is wired."""
    if price_lookup is not None:
        try:
            cached = price_lookup(symbol)
        except Exception:  # a raising lookup must never kill the sweep
            cached = None
        parsed = _dec(cached)
        if parsed is not None:
            return parsed
    row = conn.execute(
        """
        SELECT trigger_price FROM evidence_dots
        WHERE symbol = ? AND trigger_price IS NOT NULL
        ORDER BY ts DESC, id DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return _dec(row["trigger_price"]) if row is not None else None


# --------------------------------------------------------------------------- #
# Episode close rules (spec §2 — tested in order, first that fires wins)
# --------------------------------------------------------------------------- #
def _decayed_to_noise(episode: dict, dots: list[dict], now_dt: datetime) -> bool:
    """Thesis gross decayed mass below the floor NOW and at each of the last
    N_DECAY session opens (consecutive, session-scoped so a mid-session lull can't
    false-close). The current reading blocks the close when fresh intraday
    evidence has arrived; the per-session-open readings enforce persistence."""
    floor = FUSION_EPISODE_DECAY_FLOOR
    if score_decayed(dots, now_dt)["thesis"]["gross"] >= floor:
        return False
    opened = _parse_dt(episode.get("opened_at"))
    if opened is None:
        return False
    opens = _recent_session_opens(now_dt, FUSION_EPISODE_DECAY_SESSIONS)
    if len(opens) < FUSION_EPISODE_DECAY_SESSIONS:
        return False
    for so in opens:
        if so < opened:
            return False  # episode too young to have decayed across N_DECAY sessions
        as_of = [d for d in dots if (_parse_dt(d.get("ts")) or now_dt) <= so]
        if score_decayed(as_of, so)["thesis"]["gross"] >= floor:
            return False
    return True


def _episode_close_reason(
    episode: dict, dots: list[dict], now_dt: datetime, latest_price: Decimal | None
) -> str | None:
    # 1. price_invalidation — anchor-latched thesis against a K×ATR adverse move.
    anchor_price = _dec(episode.get("anchor_price"))
    anchor_atr = _dec(episode.get("anchor_atr14"))
    anchor_dir = str(episode.get("anchor_direction") or "")
    if (
        anchor_price is not None
        and anchor_atr is not None
        and anchor_atr > 0
        and anchor_dir in ("bull", "bear")
        and latest_price is not None
    ):
        band = Decimal(str(FUSION_EPISODE_INVAL_ATR)) * anchor_atr
        if anchor_dir == "bull" and latest_price <= anchor_price - band:
            return "price_invalidation"
        if anchor_dir == "bear" and latest_price >= anchor_price + band:
            return "price_invalidation"

    # 2. decay_to_noise — thesis mass gone for N_DECAY consecutive sessions.
    if _decayed_to_noise(episode, dots, now_dt):
        return "decay_to_noise"

    # 3. hard_ttl — the swing ceiling in trading days.
    opened = _parse_dt(episode.get("opened_at"))
    if opened is not None:
        age = _trading_days_between(
            opened.astimezone(_ET).date(), now_dt.astimezone(_ET).date()
        )
        if age >= FUSION_EPISODE_TTL_SESSIONS:
            return "hard_ttl"
    return None


# --------------------------------------------------------------------------- #
# Trigger conditions (spec §3 — evaluated from persisted values, no live fetch)
# --------------------------------------------------------------------------- #
def compute_trigger_expiry(armed_at: Any, trigger: dict[str, Any]) -> str:
    """``trigger_expires_at`` from ``armed_at`` + the trigger's TTL: wall-clock for
    the hours (impulse) class, the trading-day calendar (expiring at 16:00 ET) for
    the sessions (thesis-confirmation) class. Computed once at arm time."""
    armed_dt = _parse_dt(armed_at) or datetime.now(timezone.utc)
    ttl = (trigger or {}).get("ttl") or {}
    unit = str(ttl.get("unit") or "")
    value = ttl.get("value")
    cls = str((trigger or {}).get("class") or "")
    if unit == "hours" or (unit == "" and cls == "impulse"):
        hours = float(value) if value is not None else float(FUSION_TRIGGER_TTL_IMPULSE_HOURS)
        return (armed_dt + timedelta(hours=hours)).isoformat()
    sessions = int(value) if value is not None else int(FUSION_TRIGGER_TTL_THESIS_SESSIONS)
    expiry_date = _add_trading_days(armed_dt.astimezone(_ET).date(), sessions)
    return datetime(
        expiry_date.year, expiry_date.month, expiry_date.day, 16, 0, tzinfo=_ET
    ).isoformat()


def _latest_rvol(dots: list[dict]) -> float | None:
    """The most recent RVOL reading from the episode's rvol_ex_prints dots."""
    best_ts = ""
    best: float | None = None
    for dot in dots:
        if str(dot.get("component") or "") != "rvol_ex_prints":
            continue
        value = _dec(dot.get("raw_value"))
        ts = str(dot.get("ts") or "")
        if value is not None and ts >= best_ts:
            best_ts = ts
            best = float(value)
    return best


def _trigger_fires(
    trigger: dict[str, Any],
    dots: list[dict],
    now_dt: datetime,
    latest_price: Decimal | None,
    atr: Decimal | None,
) -> bool:
    """Whether an armed idea's trigger condition evaluates true at ``now``.

    Reads only persisted/cached state (intake price cache, fusion_atr_cache, the
    episode's recomputed masses) — storm-cheap and injectable in tests."""
    condition = trigger.get("condition") or {}
    ctype = str(condition.get("type") or "")
    direction = str(condition.get("direction") or "")
    level = _dec(condition.get("level"))
    threshold_atr = condition.get("threshold_atr")
    tol = (
        Decimal(str(threshold_atr if threshold_atr is not None else 0.1)) * atr
        if atr is not None and atr > 0
        else None
    )

    if ctype == "level_touch":
        if latest_price is None or level is None or tol is None:
            return False
        return abs(latest_price - level) <= tol

    if ctype == "level_reclaim":
        if latest_price is None or level is None:
            return False
        if direction == "bull":
            return latest_price >= level
        if direction == "bear":
            return latest_price <= level
        return False

    if ctype == "impulse_cluster":
        if latest_price is None or level is None or tol is None:
            return False
        if abs(latest_price - level) > tol:
            return False
        threshold = float(condition.get("threshold") or 0.0)
        trig = score_decayed(dots, now_dt)["trigger"]
        side = float(trig["bull_mass" if direction == "bull" else "bear_mass"])
        return side >= threshold

    if ctype == "rvol_confirm":
        threshold = float(condition.get("threshold") or 0.0)
        rvol = _latest_rvol(dots)
        if rvol is None or rvol < threshold:
            return False
        thesis = score_decayed(dots, now_dt)["thesis"]
        net = float(thesis["bull_mass"]) - float(thesis["bear_mass"])
        if direction == "bull":
            return net > 0
        if direction == "bear":
            return net < 0
        return False

    return False  # unknown condition type never fires (conservative)


def _parse_trigger(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def sweep_lifecycle(
    now: Any,
    db_path: Path = DEFAULT_DB_PATH,
    *,
    price_lookup: PriceLookup | None = None,
) -> dict[str, int]:
    """One idempotent lifecycle pass at instant ``now`` (see module docstring).

    ``price_lookup`` mirrors ``intake_once``: prod passes the intake price cache
    (``_fusion_price_from_cache``); tests inject a synthetic lookup. When absent,
    price rules fall back to the latest stamped dot's price from the DB.
    """
    fusion_db.init_fusion_tables(db_path)
    init_idea_outcomes_tables(db_path)
    now_dt = _parse_dt(now) or datetime.now(timezone.utc)
    counters = {
        "episodes_closed": 0,
        "ideas_trigger_fired": 0,
        "ideas_expired": 0,
        "ideas_invalidated": 0,
    }

    # 1. Episode close sweep (evaluated BEFORE dependent-idea evaluation so an
    #    invalidation cascades to armed ideas in the SAME pass).
    with _connect(db_path) as conn:
        open_eps = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM fusion_episodes WHERE state = 'open'"
            ).fetchall()
        ]
    for ep in open_eps:
        dots = fusion_db.episode_dots(ep["id"], db_path=db_path)
        with _connect(db_path) as conn:
            latest = _latest_price(str(ep["symbol"]), price_lookup, conn)
        reason = _episode_close_reason(ep, dots, now_dt, latest)
        if reason is None:
            continue
        with _connect(db_path) as conn:
            cur = conn.execute(
                """
                UPDATE fusion_episodes
                SET state = 'closed', closed_at = ?, close_reason = ?, updated_at = ?
                WHERE id = ? AND state = 'open'
                """,
                (now_dt.isoformat(), reason, now_iso(), ep["id"]),
            )
            conn.commit()
        if cur.rowcount:
            counters["episodes_closed"] += 1

    # 2. Armed/born idea evaluation.
    with _connect(db_path) as conn:
        ideas = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM idea_outcomes WHERE lifecycle_state IN (?, ?)",
                (_LC_ARMED, _LC_BORN),
            ).fetchall()
        ]

    for idea in ideas:
        try:
            _evaluate_idea(idea, now_dt, db_path, price_lookup, counters)
        except Exception:
            logger.warning(
                "fusion lifecycle: idea %s evaluation failed", idea.get("id"),
                exc_info=True,
            )

    # 3. Flow hot lane reconciliation (Round 4) — same cadence, fail-soft: a
    #    broken lane sweep must never block episode closes or idea grading.
    try:
        from signals.fusion.attention import sweep_hot_lane

        counters.update(sweep_hot_lane(now_dt, db_path))
    except Exception:
        logger.warning("fusion lifecycle: hot-lane sweep failed", exc_info=True)
    return counters


def _expire_if_ttl_lapsed(
    idea: dict, now_dt: datetime, db_path: Path, counters: dict[str, int]
) -> None:
    """Armed-never-triggered whose TTL lapsed → counterfactual grade (§4).
    ``applied`` is False when the CAS guard inside grade_counterfactual missed
    (the mechanical grader resolved the row first) — count only real expiries."""
    expires = _parse_dt(idea.get("trigger_expires_at"))
    if expires is not None and now_dt >= expires:
        if grade_counterfactual(idea, now_dt, db_path=db_path).get("applied"):
            counters["ideas_expired"] += 1


def _evaluate_idea(
    idea: dict,
    now_dt: datetime,
    db_path: Path,
    price_lookup: PriceLookup | None,
    counters: dict[str, int],
) -> None:
    outcome_id = int(idea["id"])
    lifecycle_state = str(idea.get("lifecycle_state") or "")
    status = str(idea.get("status") or "")
    episode_id = idea.get("episode_id")

    # (a) §3 mirror transitions — the mechanical grader advances `status` but
    #     never writes the arming axis, so the sweep reconciles: a resolved row
    #     retires to lifecycle 'closed' (grades untouched — the arming machine
    #     must never rewrite a mechanical outcome), an entry-zone touch promotes
    #     to 'live' (a live idea is no longer cascade/TTL-eligible, §3/§5).
    if status in _MECH_TERMINAL:
        update_outcome(
            outcome_id,
            lifecycle_state="closed",
            expect_status=tuple(_MECH_TERMINAL),
            db_path=db_path,
        )
        return
    if status == "triggered":
        update_outcome(
            outcome_id,
            lifecycle_state="live",
            expect_status=("triggered",),
            db_path=db_path,
        )
        return

    # (b) episode close handling. Only a PRICE-BROKEN thesis invalidates
    #     (censors: no benchmark leg) a not-yet-live idea — §3 restricts the
    #     cascade to close_reason='price_invalidation' on purpose, because
    #     calibration counts everything it can: an aged-out close (decay_to_noise
    #     / hard_ttl / migrated) is a dead-but-unbroken thesis, so a born row
    #     rides on to its mechanical grade and an armed row runs out its trigger
    #     TTL into a COUNTED counterfactual instead of vanishing.
    if episode_id:
        with _connect(db_path) as conn:
            ep = conn.execute(
                "SELECT state, close_reason FROM fusion_episodes WHERE id = ?",
                (episode_id,),
            ).fetchone()
        if ep is not None and str(ep["state"]) == "closed":
            if str(ep["close_reason"] or "") == "price_invalidation":
                if update_outcome(
                    outcome_id,
                    status="invalidated",
                    lifecycle_state="invalidated",
                    resolved_at=now_dt.date().isoformat(),
                    expect_status=_MECH_ACTIVE,
                    db_path=db_path,
                ):
                    counters["ideas_invalidated"] += 1
                return
            if lifecycle_state == _LC_ARMED:
                _expire_if_ttl_lapsed(idea, now_dt, db_path, counters)
            return  # dead-but-unbroken thesis: no NEW trigger fires against it

    if lifecycle_state != _LC_ARMED:
        return  # a born idea with a live episode simply waits for arming

    # (c) trigger fire — evaluated BEFORE the TTL check so an idea whose trigger
    #     fires on the same pass its TTL lapses transitions to trigger_fired, not
    #     expired (§3 fire-before-expire race, single writer).
    trigger = _parse_trigger(idea.get("trigger_json"))
    if trigger is not None:
        symbol = str(idea["symbol"])
        with _connect(db_path) as conn:
            latest = _latest_price(symbol, price_lookup, conn)
        # Explicit as_of: get_atr's staleness cutoff is relative to the
        # caller's session (07-28); the sweep's own clock is that session.
        atr = fusion_db.get_atr(
            symbol,
            as_of=now_dt.astimezone(_ET).date().isoformat(),
            db_path=db_path,
        )
        dots = (
            fusion_db.episode_dots(str(episode_id), db_path=db_path) if episode_id else []
        )
        if _trigger_fires(trigger, dots, now_dt, latest, atr):
            if update_outcome(
                outcome_id,
                lifecycle_state="trigger_fired",
                expect_status=_MECH_ACTIVE,
                db_path=db_path,
            ):
                counters["ideas_trigger_fired"] += 1
            return

    # (d) TTL expiry (§4).
    _expire_if_ttl_lapsed(idea, now_dt, db_path, counters)
