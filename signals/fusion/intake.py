"""Cursor-polled trigger intake for the observe-only Fusion Evidence Bus."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from config.idea_engine import (
    FUSION_SOCIAL_SPIKE_RATIO,
    FUSION_STORM_BATCH_EVENTS,
    FUSION_STORM_KEEP_EVENTS,
)
from db import fusion as fusion_db
from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.social_mentions import SOURCES, get_source_buzz
from signals.fusion import registrar
from signals.fusion.scorer import options_direction, score_dots, tape_direction
from trading.market_hours import _ET

PriceLookup = Callable[[str], Decimal | None]
ShockLookup = Callable[[], bool]

_STORE_TABLES = {
    "options_flow": "options_flow_events",
    "equity_tape": "equity_tape_events",
}
_OPTIONS_COMPONENTS = {
    "sweep": "option_sweep",
    "block": "option_block",
    "unusual_volume": "unusual_option_volume",
    "directional_pressure": "directional_pressure",
}
_EQUITY_COMPONENTS = {
    "equity_block": "equity_block",
    "dark_pool": "dark_pool",
}


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _market_day(ts: str) -> str:
    try:
        value = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=_ET)
        return value.astimezone(_ET).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(_ET).date().isoformat()


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _poll(store: str, cursor: int, limit: int, db_path: Path) -> list[dict]:
    table = _STORE_TABLES[store]
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE ingest_seq > ?
            ORDER BY ingest_seq ASC
            LIMIT ?
            """,
            (int(cursor), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def _event_shape(store: str, event: dict) -> dict[str, Any] | None:
    event_type = str(event.get("event_type") or "")
    if store == "options_flow":
        component = _OPTIONS_COMPONENTS.get(event_type)
        if component is None:
            return None
        return {
            "symbol": str(event.get("underlying") or "").strip().upper(),
            "component": component,
            "actor_group": "options_actor",
            "direction": options_direction(event.get("right"), event.get("direction")),
            "raw_value": _decimal(event.get("premium_usd")),
            "detail": {
                "event_type": event_type,
                "right": event.get("right"),
                "execution_side": event.get("direction"),
                "reason": event.get("reason"),
                "caveat": event.get("caveat"),
            },
        }
    component = _EQUITY_COMPONENTS.get(event_type)
    if component is None:  # equity_sweep is intentionally excluded in v1.
        return None
    detail = event.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            detail = {}
    return {
        "symbol": str(event.get("symbol") or "").strip().upper(),
        "component": component,
        "actor_group": "equity_actor",
        "direction": tape_direction(event.get("direction")),
        "raw_value": _decimal(event.get("notional_usd")),
        "detail": {
            **(detail if isinstance(detail, dict) else {}),
            "event_type": event_type,
            "reason": event.get("reason"),
            "caveat": event.get("caveat"),
        },
    }


def _ensure_episode(
    symbol: str, market_day: str, ts: str, shock: bool, db_path: Path
) -> dict[str, Any]:
    episode_id = _stable_id("episode", symbol, market_day)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, market_shock
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, market_day) DO UPDATE SET
                updated_at = excluded.updated_at,
                market_shock = MAX(fusion_episodes.market_shock, excluded.market_shock)
            """,
            (episode_id, symbol, market_day, ts, now_iso(), int(shock)),
        )
        row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE symbol = ? AND market_day = ?",
            (symbol, market_day),
        ).fetchone()
        conn.commit()
    return dict(row)


def _insert_dot(
    *,
    episode_id: str,
    source_store: str,
    source_event_id: str,
    source_ingest_seq: int,
    symbol: str,
    ts: str,
    component: str,
    actor_group: str,
    dot_kind: str,
    direction: str,
    raw_value: Decimal | None,
    points: float,
    detail: dict[str, Any],
    price: Decimal | None,
    atr14: Decimal | None,
    db_path: Path,
) -> str:
    dot_id = _stable_id("dot", source_store, source_event_id, component)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO evidence_dots(
                id, episode_id, source_store, source_event_id, source_ingest_seq,
                symbol, ts, component, actor_group, dot_kind, direction,
                raw_value, points, detail_json, trigger_price, prior_close_atr14,
                stamped, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_store, source_event_id, component) DO UPDATE SET
                detail_json = excluded.detail_json,
                points = excluded.points
            """,
            (
                dot_id,
                episode_id,
                source_store,
                source_event_id,
                int(source_ingest_seq),
                symbol,
                ts,
                component,
                actor_group,
                dot_kind,
                direction,
                str(raw_value) if raw_value is not None else None,
                float(points),
                fusion_db.serialize_detail(detail),
                str(price) if price is not None else None,
                str(atr14) if atr14 is not None else None,
                int(price is not None and atr14 is not None),
                now_iso(),
            ),
        )
        conn.commit()
    return dot_id


def _put_disposition(
    *,
    store: str,
    event: dict,
    disposition: str,
    episode_id: str | None,
    dot_id: str | None,
    detail: str | None,
    db_path: Path,
) -> None:
    if disposition not in fusion_db.DISPOSITIONS:
        raise ValueError(f"invalid fusion disposition: {disposition}")
    now = now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO fusion_dispositions(
                source_store, source_event_id, source_ingest_seq, episode_id,
                dot_id, disposition, detail, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_store, source_event_id) DO UPDATE SET
                episode_id = excluded.episode_id,
                dot_id = excluded.dot_id,
                disposition = excluded.disposition,
                detail = excluded.detail,
                updated_at = excluded.updated_at
            """,
            (
                store,
                str(event["id"]),
                int(event["ingest_seq"]),
                episode_id,
                dot_id,
                disposition,
                detail,
                now,
                now,
            ),
        )
        conn.commit()


def _attach_attention(episode: dict, ts: str, db_path: Path) -> None:
    for source in sorted(SOURCES):
        buzz = get_source_buzz(episode["symbol"], source, db_path=db_path)
        ratio = buzz.get("anomaly_ratio")
        if buzz.get("state") != "ready" or ratio is None:
            continue
        if float(ratio) < FUSION_SOCIAL_SPIKE_RATIO:
            continue
        source_id = f"{episode['symbol']}:{buzz['bucket']}:{source}"
        _insert_dot(
            episode_id=episode["id"],
            source_store="social_mentions",
            source_event_id=source_id,
            source_ingest_seq=0,
            symbol=episode["symbol"],
            ts=ts,
            component=f"{source}_buzz",
            actor_group="attention",
            dot_kind="state",
            direction="none",
            raw_value=_decimal(ratio),
            points=min(3.0, max(0.0, math.log2(float(ratio)))),
            detail=buzz,
            price=None,
            atr14=None,
            db_path=db_path,
        )


def _attach_shock(episode: dict, ts: str, db_path: Path) -> None:
    if not int(episode.get("market_shock") or 0):
        return
    _insert_dot(
        episode_id=episode["id"],
        source_store="market_context",
        source_event_id=f"shock:{episode['market_day']}",
        source_ingest_seq=0,
        symbol=episode["symbol"],
        ts=ts,
        component="market_shock",
        actor_group="context",
        dot_kind="state",
        direction="none",
        raw_value=None,
        points=0.0,
        detail={"display_only": True},
        price=None,
        atr14=None,
        db_path=db_path,
    )


def _rescore(episode_id: str, db_path: Path) -> dict[str, Any]:
    dots = fusion_db.episode_dots(episode_id, db_path=db_path)
    state = score_dots(dots)
    signature = ",".join(sorted({str(dot["component"]) for dot in dots}))
    collapsed = set(state["collapsed_dot_ids"])
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE fusion_episodes SET
                score = ?, direction_state = ?, contested = ?,
                signature = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                state["score"],
                state["direction_state"],
                int(state["contested"]),
                signature,
                now_iso(),
                episode_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO fusion_score_history(episode_id, score, score_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, state["score"], fusion_db.serialize_detail(state), now_iso()),
        )
        # A later, larger actor can change which observation is collapsed.
        conn.execute(
            """
            UPDATE fusion_dispositions SET disposition = 'scored', updated_at = ?
            WHERE episode_id = ? AND disposition IN ('scored', 'collapsed')
            """,
            (now_iso(), episode_id),
        )
        for dot_id in collapsed:
            conn.execute(
                """
                UPDATE fusion_dispositions SET disposition = 'collapsed', updated_at = ?
                WHERE dot_id = ? AND disposition IN ('scored', 'collapsed')
                """,
                (now_iso(), dot_id),
            )
        conn.commit()
    return state


def _maybe_call_registrar(
    episode_id: str, anchor_dot_id: str, score_state: dict[str, Any], db_path: Path
) -> None:
    with _connect(db_path) as conn:
        episode_row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if episode_row is None or int(episode_row["registration_called"]):
            return
        dot_row = conn.execute(
            "SELECT * FROM evidence_dots WHERE id = ?", (anchor_dot_id,)
        ).fetchone()
        if dot_row is None:
            return
        conn.execute(
            """
            UPDATE fusion_episodes SET
                anchor_dot_id = ?, anchor_price = ?, anchor_atr14 = ?,
                anchor_direction = ?, registration_called = 1
            WHERE id = ? AND registration_called = 0
            """,
            (
                anchor_dot_id,
                dot_row["trigger_price"],
                dot_row["prior_close_atr14"],
                dot_row["direction"],
                episode_id,
            ),
        )
        conn.commit()
    with _connect(db_path) as conn:
        episode = dict(
            conn.execute("SELECT * FROM fusion_episodes WHERE id = ?", (episode_id,)).fetchone()
        )
    registrar.register_stack(
        episode,
        fusion_db.episode_dots(episode_id, db_path=db_path),
        {**score_state, "signature": episode["signature"]},
        db_path=db_path,
    )


def intake_once(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    price_lookup: PriceLookup | None = None,
    shock_lookup: ShockLookup | None = None,
    batch_limit: int = 2000,
    storm_threshold: int = FUSION_STORM_BATCH_EVENTS,
    storm_keep: int = FUSION_STORM_KEEP_EVENTS,
) -> dict[str, int]:
    """Consume both native stores once, with one terminal disposition per row."""
    from db.equity_tape import init_db as init_equity
    from db.options_flow import init_db as init_options

    init_options(db_path)
    init_equity(db_path)
    fusion_db.init_fusion_tables(db_path)
    counters = {key: 0 for key in fusion_db.DISPOSITIONS}
    lookup = price_lookup or (lambda _symbol: None)
    shock = bool(shock_lookup()) if shock_lookup is not None else False

    for store in fusion_db.STORES:
        cursor = fusion_db.get_cursor(store, db_path=db_path)
        events = _poll(store, cursor, batch_limit, db_path)
        shed_ids: set[str] = set()
        if len(events) > storm_threshold:
            ranked = sorted(
                events,
                key=lambda event: float(event.get("premium_usd") or event.get("notional_usd") or 0),
                reverse=True,
            )
            shed_ids = {str(event["id"]) for event in ranked[max(0, storm_keep) :]}

        for event in events:
            if str(event["id"]) in shed_ids:
                _put_disposition(
                    store=store,
                    event=event,
                    disposition="shed",
                    episode_id=None,
                    dot_id=None,
                    detail="storm_watermark",
                    db_path=db_path,
                )
                counters["shed"] += 1
                fusion_db.set_cursor(store, int(event["ingest_seq"]), db_path=db_path)
                continue

            shape = _event_shape(store, event)
            if shape is None or not shape["symbol"]:
                _put_disposition(
                    store=store,
                    event=event,
                    disposition="ignored_by_rule",
                    episode_id=None,
                    dot_id=None,
                    detail="component_excluded",
                    db_path=db_path,
                )
                counters["ignored_by_rule"] += 1
                fusion_db.set_cursor(store, int(event["ingest_seq"]), db_path=db_path)
                continue

            episode = _ensure_episode(
                shape["symbol"],
                _market_day(str(event["ts"])),
                str(event["ts"]),
                shock,
                db_path,
            )
            price = _decimal(lookup(shape["symbol"]))
            atr14 = fusion_db.get_atr(shape["symbol"], db_path=db_path)
            dot_id = _insert_dot(
                episode_id=episode["id"],
                source_store=store,
                source_event_id=str(event["id"]),
                source_ingest_seq=int(event["ingest_seq"]),
                symbol=shape["symbol"],
                ts=str(event["ts"]),
                component=shape["component"],
                actor_group=shape["actor_group"],
                dot_kind="event",
                direction=shape["direction"],
                raw_value=shape["raw_value"],
                points=0.0,
                detail=shape["detail"],
                price=price,
                atr14=atr14,
                db_path=db_path,
            )
            if price is None or atr14 is None:
                disposition = "unstampable"
                detail = "missing_cached_price" if price is None else "missing_prior_close_atr14"
            elif shape["direction"] == "none":
                disposition = "ignored_by_rule"
                detail = "non_directional_actor_dot"
            else:
                disposition = "scored"
                detail = None
            _put_disposition(
                store=store,
                event=event,
                disposition=disposition,
                episode_id=episode["id"],
                dot_id=dot_id,
                detail=detail,
                db_path=db_path,
            )
            _attach_attention(episode, str(event["ts"]), db_path)
            _attach_shock(episode, str(event["ts"]), db_path)
            state = _rescore(episode["id"], db_path)
            if disposition == "scored":
                _maybe_call_registrar(episode["id"], dot_id, state, db_path)
            from signals.fusion.cards import update_stack_card

            with _connect(db_path) as conn:
                current_episode = dict(
                    conn.execute(
                        "SELECT * FROM fusion_episodes WHERE id = ?", (episode["id"],)
                    ).fetchone()
                )
            update_stack_card(current_episode, state, db_path=db_path)
            counters[disposition] += 1
            fusion_db.set_cursor(store, int(event["ingest_seq"]), db_path=db_path)

    return counters
