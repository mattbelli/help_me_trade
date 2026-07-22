"""Confluence watch (design §6b / §5, generalized) — detection + bookkeeping only.

Fires when ≥2 independently watched things line up on one underlying the same
day: options flow, equity tape (blocks / dark-pool prints), underlying RVOL,
and proximity to a level (session volume node or user price alert). Flow and
tape components accumulate across the day via the event stores, so a morning
sweep plus an afternoon dark-pool print still converge.

Pure detection helpers are hermetic (no I/O). ``process_events`` orchestrates
read-only market data and dedup bookkeeping. Since 07-21 (founder order) it
emits NO notification rows and NO pushes — detections are counted and deduped
for pipeline health; the notifications stream carries investigated ideas only.
Every failure path degrades silently — never raises.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.chart_features import list_price_alerts
from signals.indicators import atr
from signals.options_flow.constants import (
    CONV_LEVEL_ATR,
    CONV_RVOL_MIN,
    SOLO_PUSH_PREMIUM_USD,
)
from signals.options_flow.detect import _fmt_premium
from signals.session_node_strategy import compute_session_levels
from signals.volume_profile import load_profile

logger = logging.getLogger(__name__)

_FLOW_EVENT_TYPES = frozenset({"unusual_volume", "block", "sweep", "directional_pressure"})
_TAPE_EVENT_TYPES = frozenset({"equity_block", "dark_pool"})


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

    if best is not None:
        # Distance in ATRs — the importance score rewards tighter proximity.
        best["dist_atr"] = round(best_dist / float(atr_value), 3)
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


def _tape_component_text(event: dict) -> str:
    notional = float(event.get("notional_usd") or 0)
    kind = str(event.get("event_type") or "")
    label = "dark-pool print" if kind == "dark_pool" else "block print"
    return f"{_fmt_premium(notional)} {label}"


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
    event: dict | None = None,
    tape_event: dict | None = None,
    rvol: float | None,
    level: dict | None,
    event_date: str,
    interest: dict | None = None,
    extended_hours: bool = False,
) -> dict | None:
    """Fire when ≥2 watched components line up (flow, tape, rvol, level)."""
    del event_date  # reserved for stable keys; events carry their own date
    underlying = ""
    for src in (event, tape_event):
        if isinstance(src, dict):
            underlying = (
                str(src.get("underlying") or src.get("symbol") or "").strip().upper()
            )
            if underlying:
                break
    if not underlying:
        return None

    components: list[str] = []
    body_parts: list[str] = []

    if event is not None:
        components.append("flow")
        body_parts.append(_flow_component_text(event))

    if tape_event is not None:
        components.append("tape")
        body_parts.append(_tape_component_text(tape_event))

    if rvol is not None and rvol >= CONV_RVOL_MIN:
        components.append("rvol")
        body_parts.append(f"RVOL {rvol}x")

    if level is not None:
        components.append("level")
        body_parts.append(_level_component_text(level))

    if len(components) < 2:
        return None

    from signals.confluence_score import score_confluence

    scored = score_confluence(
        flow_event=event,
        tape_event=tape_event,
        rvol=rvol,
        level=level,
        interest=interest,
        extended_hours=extended_hours,
    )

    lead = event if event is not None else tape_event
    event_id = str((lead or {}).get("id") or "")
    return {
        "underlying": underlying,
        "title": f"{underlying}: signal confluence",
        "body": " + ".join(body_parts) + f" · score {scored['score']}",
        "components": components,
        "event_id": event_id,
        "score": scored["score"],
        "drivers": scored["drivers"],
    }


def _today_flow_lead(
    underlying: str, today_et: str, db_path: Path
) -> dict | None:
    """Largest-premium flow event already stored for *underlying* today."""
    try:
        from db.options_flow import recent_events

        rows = recent_events(
            symbol=underlying,
            since=f"{today_et}T00:00:00",
            limit=50,
            db_path=db_path,
        )
        rows = [
            r for r in rows if str(r.get("event_type") or "") in _FLOW_EVENT_TYPES
        ]
        if not rows:
            return None
        return max(rows, key=lambda r: float(r.get("premium_usd") or 0))
    except Exception:
        logger.debug(
            "confluence: flow day-lookup failed for %s", underlying, exc_info=True
        )
        return None


def _today_tape_lead(
    underlying: str, today_et: str, db_path: Path
) -> dict | None:
    """Largest-notional tape event already stored for *underlying* today."""
    try:
        from db.equity_tape import recent_events

        rows = recent_events(
            symbol=underlying,
            since=f"{today_et}T00:00:00",
            limit=50,
            db_path=db_path,
        )
        if not rows:
            return None
        return max(rows, key=lambda r: float(r.get("notional_usd") or 0))
    except Exception:
        logger.debug(
            "confluence: tape day-lookup failed for %s", underlying, exc_info=True
        )
        return None


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
        "interest": {"held": False, "alerted": False, "active_watchlist": False},
    }

    # Revealed interest (importance score) — every read fail-soft.
    try:
        from db.sqlite import get_all_latest_snapshots

        for snap in get_all_latest_snapshots():
            for pos in snap.get("positions") or []:
                if str(pos.get("symbol", "")).upper() == u:
                    ctx["interest"]["held"] = True
                    break
            if ctx["interest"]["held"]:
                break
    except Exception:
        logger.debug("confluence: holdings check failed for %s", u, exc_info=True)
    try:
        from config.settings import get_watchlists_state

        state = get_watchlists_state()
        active = next(
            (
                wl
                for wl in state.get("lists") or []
                if wl.get("id") == state.get("active_id")
            ),
            None,
        )
        if active and u in {
            str(s).upper() for s in (active.get("symbols") or [])
        }:
            ctx["interest"]["active_watchlist"] = True
    except Exception:
        logger.debug("confluence: watchlist check failed for %s", u, exc_info=True)

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

    alert_prices: list[float] = []
    try:
        for row in list_price_alerts(symbol=u, active_only=True):
            if isinstance(row, dict) and row.get("price") is not None:
                alert_prices.append(float(row["price"]))
    except Exception:
        logger.debug(
            "options_flow convergence: price alerts failed for %s", u, exc_info=True
        )
    if alert_prices:
        ctx["interest"]["alerted"] = True

    last_price = ctx.get("last_price")
    if last_price is not None and atr_value is not None:
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

    by_u: dict[str, dict[str, list[dict]]] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("event_type") or "")
        if et in _FLOW_EVENT_TYPES:
            source = "flow"
        elif et in _TAPE_EVENT_TYPES:
            source = "tape"
        else:
            continue
        u = str(ev.get("underlying") or ev.get("symbol") or "").strip().upper()
        if not u:
            continue
        by_u.setdefault(u, {"flow": [], "tape": []})[source].append(ev)

    convergence_covered: set[str] = set()

    for u, group in by_u.items():
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

        # Leads: this batch first, else the day's stored events — a morning
        # sweep + afternoon dark-pool print converge across cycles.
        flow_lead = (
            max(group["flow"], key=lambda e: float(e.get("premium_usd") or 0))
            if group["flow"]
            else _today_flow_lead(u, today_et, resolved_db)
        )
        tape_lead = (
            max(group["tape"], key=lambda e: float(e.get("notional_usd") or 0))
            if group["tape"]
            else _today_tape_lead(u, today_et, resolved_db)
        )
        built = build_convergence(
            event=flow_lead,
            tape_event=tape_lead,
            rvol=ctx.get("rvol"),
            level=ctx.get("level"),
            event_date=today_et,
            interest=ctx.get("interest"),
            extended_hours=minutes_since_open < 0 or minutes_since_open > 390,
        )
        if built is None:
            continue

        # Founder order 07-21: convergence detections mint NO notification rows
        # (and therefore no pushes) — the raw events already feed the fusion bus
        # and the evidence-stack view; the notifications stream carries ideas
        # only. Detection + dedup bookkeeping stay so counters remain comparable.
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

        # Same founder order 07-21: solo large-flow detections are bookkept,
        # never surfaced as notification rows.
        try:
            mark_sent(solo_key, db_path=resolved_db)
        except Exception:
            logger.debug(
                "options_flow convergence: solo mark_sent failed for %s",
                solo_key,
                exc_info=True,
            )
        counters["solo"] += 1

    return counters
