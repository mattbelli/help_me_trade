"""Convergence watch + budgeted phone pushes (design §6b / §5).

Pure detection helpers are hermetic (no I/O). ``process_events`` orchestrates
read-only market data, persist-first ``notify``, and deny-by-default push budget.
Every failure path degrades to no phone push — never raises.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.chart_features import list_price_alerts
from signals.indicators import atr
from signals.notify import notify
from signals.options_flow.constants import (
    CONV_LEVEL_ATR,
    CONV_RVOL_MIN,
    OPTIONS_FLOW_MAX_PUSH_PER_DAY,
    SOLO_PUSH_PREMIUM_USD,
)
from signals.options_flow.detect import _fmt_premium
from signals.session_node_strategy import compute_session_levels
from signals.volume_profile import load_profile

logger = logging.getLogger(__name__)

_FLOW_EVENT_TYPES = frozenset({"unusual_volume", "block", "directional_pressure"})


def rvol_from_profile(
    *,
    median_daily_total: float,
    cumfrac: list[float],
    total_volume: float,
    minutes_since_open: int,
) -> float | None:
    """RVOL parity with ``api/main.py:_compute_rvol_from_total_volume``."""
    if minutes_since_open < 0:
        return None
    bucket = min(minutes_since_open // 5, 77)
    if bucket < 0 or bucket > 77:
        return None
    if not cumfrac or len(cumfrac) < 78:
        return None
    try:
        expected = float(median_daily_total) * float(cumfrac[bucket])
    except (TypeError, ValueError, IndexError):
        return None
    if expected <= 0:
        return None
    try:
        vol = float(total_volume)
    except (TypeError, ValueError):
        return None
    if vol <= 0:
        return None
    return round(vol / expected, 2)


def nearest_level(
    *,
    price: float,
    levels: list[dict],
    alert_prices: list[float],
    atr_value: float | None,
) -> dict | None:
    """Closest session node or price alert within ``CONV_LEVEL_ATR × ATR``."""
    if atr_value is None or atr_value <= 0:
        return None
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None

    threshold = CONV_LEVEL_ATR * float(atr_value)
    best: dict | None = None
    best_dist = threshold + 1.0

    for lvl in levels:
        if not isinstance(lvl, dict):
            continue
        try:
            lvl_px = float(lvl.get("price"))
        except (TypeError, ValueError):
            continue
        dist = abs(px - lvl_px)
        if dist <= threshold and dist < best_dist:
            best_dist = dist
            best = {
                "name": str(lvl.get("name") or "level"),
                "price": lvl_px,
            }

    for raw in alert_prices:
        try:
            alert_px = float(raw)
        except (TypeError, ValueError):
            continue
        dist = abs(px - alert_px)
        if dist <= threshold and dist < best_dist:
            best_dist = dist
            best = {"name": "alert", "price": alert_px}

    return best


def _flow_component_text(event: dict) -> str:
    prem = float(event.get("premium_usd") or 0)
    direction = str(event.get("direction") or "undetermined")
    if direction == "buy_side":
        side = "ask-side"
    elif direction == "sell_side":
        side = "bid-side"
    else:
        side = ""
    right = str(event.get("right") or "").strip().lower()
    if right.startswith("c"):
        opt = "call"
    elif right.startswith("p"):
        opt = "put"
    else:
        opt = "options"
    prem_s = _fmt_premium(prem)
    if side:
        return f"{prem_s} {side} {opt} flow"
    return f"{prem_s} {opt} flow"


def _level_component_text(level: dict) -> str:
    name = str(level.get("name") or "level")
    try:
        px = float(level.get("price"))
    except (TypeError, ValueError):
        return f"at {name}"
    if name.lower() == "alert":
        return f"at alert ${px:.2f}"
    return f"at {name} ${px:.2f}"


def build_convergence(
    *,
    event: dict,
    rvol: float | None,
    level: dict | None,
    event_date: str,
) -> dict | None:
    """Fire when flow plus at least one corroborating component (≥2 total)."""
    del event_date  # reserved for stable keys; event carries its own date
    underlying = str(event.get("underlying") or "").strip().upper()
    if not underlying:
        return None

    components: list[str] = ["flow"]
    body_parts: list[str] = [_flow_component_text(event)]

    if rvol is not None and rvol >= CONV_RVOL_MIN:
        components.append("rvol")
        body_parts.append(f"RVOL {rvol}x")

    if level is not None:
        components.append("level")
        body_parts.append(_level_component_text(level))

    if len(components) < 2:
        return None

    event_id = str(event.get("id") or "")
    return {
        "underlying": underlying,
        "title": f"{underlying}: options flow convergence",
        "body": " + ".join(body_parts),
        "components": components,
        "event_id": event_id,
    }


def is_solo_push(event: dict) -> bool:
    event_type = str(event.get("event_type") or "")
    if event_type not in {"unusual_volume", "block"}:
        return False
    try:
        premium = float(event.get("premium_usd") or 0)
    except (TypeError, ValueError):
        return False
    return premium >= SOLO_PUSH_PREMIUM_USD


def _conv_dedup_key(underlying: str, today_et: str) -> str:
    return f"of_conv:{underlying.strip().upper()}:{today_et}"


def _solo_dedup_key(underlying: str, event_type: str, today_et: str) -> str:
    # Per (underlying, event_type, day) — design §5 dedup contract. Raw event ids
    # (which embed ts_ms for blocks) would let one busy name drain the daily budget.
    return f"of_solo:{underlying.strip().upper()}:{event_type}:{today_et}"


def _peek_push_slot(today_et: str, *, db_path: Path) -> str | None:
    """First free daily-budget slot key, or None. Deny-by-default: any error → None."""
    try:
        from db.earnings_alerts import was_sent

        for n in range(1, OPTIONS_FLOW_MAX_PUSH_PER_DAY + 1):
            key = f"of_push:{today_et}:{n}"
            if not was_sent(key, db_path=db_path):
                return key
        return None
    except Exception:
        logger.debug(
            "options_flow convergence: push budget peek failed",
            exc_info=True,
        )
        return None


def _mark_push_slot(key: str, *, db_path: Path) -> None:
    try:
        from db.earnings_alerts import mark_sent

        mark_sent(key, db_path=db_path)
    except Exception:
        logger.debug(
            "options_flow convergence: push budget mark failed for %s",
            key,
            exc_info=True,
        )


def _daily_bars_to_ohlc(bars: list[Any]) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for bar in bars:
        try:
            highs.append(float(bar.high))
            lows.append(float(bar.low))
            closes.append(float(bar.close))
        except AttributeError:
            if not isinstance(bar, dict):
                continue
            try:
                highs.append(float(bar["high"]))
                lows.append(float(bar["low"]))
                closes.append(float(bar["close"]))
            except (KeyError, TypeError, ValueError):
                continue
    return highs, lows, closes


def _gather_sync_context(
    adapter: Any,
    underlying: str,
    *,
    minutes_since_open: int,
    session_levels: list[dict],
) -> dict[str, Any]:
    """Sync read-only component gather — fail-soft per piece."""
    u = underlying.strip().upper()
    ctx: dict[str, Any] = {
        "rvol": None,
        "level": None,
        "last_price": None,
    }

    try:
        profile = load_profile(u)
        if profile is not None:
            snap_fn = getattr(adapter, "get_stock_snapshot_fields", None)
            if callable(snap_fn):
                snap = snap_fn(u)
                if isinstance(snap, dict):
                    day_vol = snap.get("day_volume")
                    last_px = snap.get("last_price")
                    if last_px is not None:
                        ctx["last_price"] = float(last_px)
                    if day_vol is not None:
                        ctx["rvol"] = rvol_from_profile(
                            median_daily_total=profile.median_daily_total,
                            cumfrac=profile.cumfrac,
                            total_volume=float(day_vol),
                            minutes_since_open=minutes_since_open,
                        )
    except Exception:
        logger.debug(
            "options_flow convergence: RVOL gather failed for %s", u, exc_info=True
        )

    atr_value: float | None = None
    try:
        bars_fn = getattr(adapter, "get_stock_bars", None)
        if callable(bars_fn):
            bars = bars_fn(u, timeframe="1Day", days=30)
            highs, lows, closes = _daily_bars_to_ohlc(bars or [])
            atr_value = atr(highs, lows, closes, n=14)
    except Exception:
        logger.debug(
            "options_flow convergence: ATR gather failed for %s", u, exc_info=True
        )

    last_price = ctx.get("last_price")
    if last_price is not None and atr_value is not None:
        alert_prices: list[float] = []
        try:
            for row in list_price_alerts(symbol=u, active_only=True):
                if isinstance(row, dict) and row.get("price") is not None:
                    alert_prices.append(float(row["price"]))
        except Exception:
            logger.debug(
                "options_flow convergence: price alerts failed for %s", u, exc_info=True
            )
        try:
            ctx["level"] = nearest_level(
                price=float(last_price),
                levels=session_levels,
                alert_prices=alert_prices,
                atr_value=atr_value,
            )
        except Exception:
            logger.debug(
                "options_flow convergence: nearest_level failed for %s", u, exc_info=True
            )

    return ctx


async def process_events(
    adapter: Any,
    events: list[dict],
    *,
    today_et: str,
    minutes_since_open: int,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Evaluate convergence + solo pushes for one poll cycle's new events."""
    resolved_db = db_path if db_path is not None else DEFAULT_DB_PATH
    counters = {"convergence": 0, "solo": 0, "pushes": 0}

    if not events:
        return counters

    try:
        from db.earnings_alerts import mark_sent, was_sent
    except Exception:
        logger.exception("options_flow convergence: dedup store unavailable")
        return counters

    by_u: dict[str, list[dict]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("event_type") or "")
        if et not in _FLOW_EVENT_TYPES:
            continue
        u = str(ev.get("underlying") or "").strip().upper()
        if not u:
            continue
        by_u.setdefault(u, []).append(ev)

    convergence_covered: set[str] = set()

    for u, u_events in by_u.items():
        conv_key = _conv_dedup_key(u, today_et)
        try:
            if was_sent(conv_key, db_path=resolved_db):
                continue
        except Exception:
            logger.debug(
                "options_flow convergence: was_sent failed for %s", conv_key, exc_info=True
            )
            continue

        try:
            session = await compute_session_levels(symbol=u, db_path=resolved_db)
        except Exception:
            logger.debug(
                "options_flow convergence: session levels failed for %s", u, exc_info=True
            )
            session = None

        session_levels = (
            list(session.get("levels") or []) if isinstance(session, dict) else []
        )

        try:
            ctx = await asyncio.to_thread(
                _gather_sync_context,
                adapter,
                u,
                minutes_since_open=minutes_since_open,
                session_levels=session_levels,
            )
        except Exception:
            logger.debug(
                "options_flow convergence: context gather failed for %s", u, exc_info=True
            )
            ctx = {"rvol": None, "level": None}

        lead = max(
            u_events,
            key=lambda e: float(e.get("premium_usd") or 0),
        )
        built = build_convergence(
            event=lead,
            rvol=ctx.get("rvol"),
            level=ctx.get("level"),
            event_date=today_et,
        )
        if built is None:
            continue

        slot = _peek_push_slot(today_et, db_path=resolved_db)
        allow_push = slot is not None
        try:
            row = await notify(
                kind="convergence",
                title=built["title"],
                body=built["body"],
                severity="info",
                ref_type="options_flow",
                ref_id=built["event_id"],
                group_key=f"options_flow:{u}",
                data={
                    "underlying": u,
                    "components": list(built["components"]),
                    "reason": lead.get("reason"),
                    "caveat": lead.get("caveat"),
                },
                push=allow_push,
                db_path=resolved_db,
            )
        except Exception:
            logger.exception(
                "options_flow convergence: notify failed for %s", u
            )
            continue

        if row:
            if allow_push and slot is not None:
                _mark_push_slot(slot, db_path=resolved_db)
            try:
                mark_sent(conv_key, db_path=resolved_db)
            except Exception:
                logger.debug(
                    "options_flow convergence: mark_sent failed for %s",
                    conv_key,
                    exc_info=True,
                )
            counters["convergence"] += 1
            convergence_covered.add(u)
            if allow_push:
                counters["pushes"] += 1

    for ev in events:
        if not isinstance(ev, dict):
            continue
        u = str(ev.get("underlying") or "").strip().upper()
        if u in convergence_covered:
            continue
        if not is_solo_push(ev):
            continue

        event_id = str(ev.get("id") or "")
        if not event_id:
            continue

        solo_key = _solo_dedup_key(u, str(ev.get("event_type") or ""), today_et)
        try:
            if was_sent(solo_key, db_path=resolved_db):
                continue
        except Exception:
            logger.debug(
                "options_flow convergence: solo was_sent failed for %s",
                solo_key,
                exc_info=True,
            )
            continue

        slot = _peek_push_slot(today_et, db_path=resolved_db)
        allow_push = slot is not None
        title = f"{u}: large options flow"
        body = str(ev.get("reason") or title)
        try:
            row = await notify(
                kind="options_flow_solo",
                title=title,
                body=body,
                severity="info",
                ref_type="options_flow",
                ref_id=event_id,
                group_key=f"options_flow:{u}",
                data={
                    "underlying": u,
                    "components": ["flow"],
                    "reason": ev.get("reason"),
                    "caveat": ev.get("caveat"),
                },
                push=allow_push,
                db_path=resolved_db,
            )
        except Exception:
            logger.exception(
                "options_flow convergence: solo notify failed for %s", event_id
            )
            continue

        if row:
            if allow_push and slot is not None:
                _mark_push_slot(slot, db_path=resolved_db)
            try:
                mark_sent(solo_key, db_path=resolved_db)
            except Exception:
                logger.debug(
                    "options_flow convergence: solo mark_sent failed for %s",
                    solo_key,
                    exc_info=True,
                )
            counters["solo"] += 1
            if allow_push:
                counters["pushes"] += 1

    return counters
