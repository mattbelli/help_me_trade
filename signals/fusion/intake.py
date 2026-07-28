"""Cursor-polled trigger intake for the observe-only Fusion Evidence Bus."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from config.idea_engine import (
    FUSION_CONTEXT_REFRESH_S,
    FUSION_EARNINGS_AHEAD_TD,
    FUSION_EARNINGS_BEHIND_TD,
    FUSION_REGIME_STALE_DAYS,
    FUSION_REHYDRATE_SESSIONS,
    FUSION_SOCIAL_SPIKE_RATIO,
    FUSION_SPOT_MAX_AGE_S,
    FUSION_STORM_BATCH_EVENTS,
    FUSION_STORM_KEEP_EVENTS,
)
from db import fusion as fusion_db
from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect
from db.chart_features import init_chart_features
from db.lane_stats import bump
from db.social_mentions import SOURCES, get_source_buzz
from signals.fusion import registrar
from signals.fusion.scorer import (
    CONVICTION_PRIOR_MASS,
    SESSION_SECONDS,
    THESIS_HALF_LIFE_TD,
    options_direction,
    score_decayed,
    score_dots,
    session_seconds_between,
    tape_direction,
)
from signals.options_flow.constants import CONV_LEVEL_ATR
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
            # Stores emit UTC ISO; a missing offset means UTC, never ET — a
            # naive evening-UTC stamp read as ET would bucket into the wrong
            # session and split one day's evidence across two episodes.
            value = value.replace(tzinfo=timezone.utc)
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
                # Persist DTE so the v2 scorer routes this dot's role (thesis vs
                # trigger) live — evidence_dots have no DTE column, so scorer._dte
                # reads it from detail (the replay recovers historical dots via
                # the options_flow_events join instead).
                "dte": event.get("dte"),
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


def _parse_iso(ts: str) -> datetime:
    """UTC-aware datetime from an ISO string (naive = UTC, house convention)."""
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _rehydrate_seed(conn: Any, symbol: str, opened_ts: str) -> dict[str, Any]:
    """Cold-storage rehydration seed for a NEW episode (§2.4).

    Seed the new row's mass summary from the most recent CLOSED episode for this
    symbol within FUSION_REHYDRATE_SESSIONS trading days, **decayed forward** from
    its ``last_evidence_at`` to now by the thesis half-life — so a returning name
    resumes with attenuated prior mass, not zero. Beyond the window (or with no
    prior) → empty."""
    empty = {"bull_mass": 0.0, "bear_mass": 0.0, "conviction": 0.0, "key_levels_json": "[]"}
    prior = conn.execute(
        """
        SELECT bull_mass, bear_mass, conviction, key_levels_json,
               last_evidence_at, closed_at
        FROM fusion_episodes
        WHERE symbol = ? AND state = 'closed'
        ORDER BY COALESCE(last_evidence_at, closed_at, updated_at) DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if prior is None:
        return empty
    last_ev = prior["last_evidence_at"] or prior["closed_at"]
    if not last_ev:
        return empty
    try:
        elapsed = session_seconds_between(_parse_iso(last_ev), _parse_iso(opened_ts))
    except (TypeError, ValueError):
        return empty
    if elapsed / SESSION_SECONDS > FUSION_REHYDRATE_SESSIONS:
        return empty  # off-watchlist too long — context has gone cold
    decay = 0.5 ** (elapsed / (THESIS_HALF_LIFE_TD * SESSION_SECONDS))
    bull = round(float(prior["bull_mass"] or 0.0) * decay, 4)
    bear = round(float(prior["bear_mass"] or 0.0) * decay, 4)
    denom = bull + bear + CONVICTION_PRIOR_MASS
    conviction = round(abs(bull - bear) / denom, 4) if denom > 0 else 0.0
    return {
        "bull_mass": bull,
        "bear_mass": bear,
        "conviction": conviction,
        "key_levels_json": prior["key_levels_json"] or "[]",
    }


def _ensure_episode(
    symbol: str, market_day: str, ts: str, shock: bool, db_path: Path
) -> dict[str, Any]:
    """Return the OPEN episode for *symbol* (one per symbol — the new dedup key).

    Reuse the open episode if one exists (bump updated_at + shock, keep its
    OPENING market_day); otherwise open a NEW episode whose id is keyed on the
    opening dot's ts (one-open-at-a-time makes it collision-free), seeding its
    mass summary from cold storage (§2.4). A replayed first dot whose episode has
    since CLOSED finds that closed row by id and reuses it (the dot re-attaches
    via evidence_dots.ON CONFLICT) rather than reopening it."""
    now = now_iso()
    with _connect(db_path) as conn:
        open_row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE symbol = ? AND state = 'open'",
            (symbol,),
        ).fetchone()
        if open_row is not None:
            # AND state='open': the lifecycle sweep runs in a CONCURRENT thread
            # and can close this episode between our SELECT and this UPDATE — a
            # closed row is immutable (its summary is the cold-storage record).
            conn.execute(
                """
                UPDATE fusion_episodes SET
                    updated_at = ?,
                    market_shock = MAX(market_shock, ?)
                WHERE id = ? AND state = 'open'
                """,
                (now, int(shock), open_row["id"]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM fusion_episodes WHERE id = ?", (open_row["id"],)
            ).fetchone()
            return dict(row)

        episode_id = _stable_id("episode", symbol, ts)
        existing = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if existing is not None:
            return dict(existing)  # closed replay — never reopen a closed episode

        seed = _rehydrate_seed(conn, symbol, ts)
        # The seed is stored TWICE: in bull/bear_mass (the live summary, valid
        # until the first _rescore) and in seed_bull/bear_mass (immutable at
        # open) so _rescore can fold it forward-decayed instead of erasing it —
        # without the dedicated columns the first rescore overwrote the seed
        # and §2.4 rehydration was a dead feature (review finding).
        conn.execute(
            """
            INSERT INTO fusion_episodes(
                id, symbol, market_day, opened_at, updated_at, state, market_shock,
                bull_mass, bear_mass, conviction, key_levels_json,
                seed_bull_mass, seed_bear_mass
            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                symbol,
                market_day,
                ts,
                now,
                int(shock),
                seed["bull_mass"],
                seed["bear_mass"],
                seed["conviction"],
                seed["key_levels_json"],
                seed["bull_mass"],
                seed["bear_mass"],
            ),
        )
        row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?", (episode_id,)
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
                points = excluded.points,
                -- Crash-replay restamp: a first pass before the price/ATR caches
                -- warmed leaves NULL stamps; a replay that has them fills the
                -- gaps. COALESCE keeps the ORIGINAL stamp when present (history
                -- is never rewritten), and stamped is recomputed from the
                -- coalesced pair.
                trigger_price = COALESCE(
                    evidence_dots.trigger_price, excluded.trigger_price),
                prior_close_atr14 = COALESCE(
                    evidence_dots.prior_close_atr14, excluded.prior_close_atr14),
                stamped = CASE WHEN
                    COALESCE(evidence_dots.trigger_price,
                             excluded.trigger_price) IS NOT NULL
                    AND COALESCE(evidence_dots.prior_close_atr14,
                                 excluded.prior_close_atr14) IS NOT NULL
                    THEN 1 ELSE 0 END
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
    # Key on (event day, episode): the old key — the EPISODE's opening day
    # alone — was one global row per day whose episode_id/symbol stuck on the
    # first episode to claim it (the upsert's SET list never rewrites those),
    # so every other episode that day got no shock dot at all (07-28 audit).
    _insert_dot(
        episode_id=episode["id"],
        source_store="market_context",
        source_event_id=f"shock:{_market_day(ts)}:{episode['id']}",
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


def _structure_points(dist_atr: float) -> float:
    """Proximity closeness in [0, 1]: 1.0 sitting on the level, → 0 at the
    CONV_LEVEL_ATR×ATR edge. Closer-is-higher makes the scorer's latest/best
    max() pick the closest level, and any genuinely-near level scores > 0."""
    return round(max(0.0, 1.0 - dist_atr / CONV_LEVEL_ATR), 4)


def _active_alert_prices(symbol: str, db_path: Path) -> list[float]:
    """Active founder price-alert levels for *symbol* on this db.

    Reads the ``price_alerts`` store (db/chart_features.py — api/price-alerts
    backs onto it) with the SAME active predicate as ``list_price_alerts``
    (``triggered = 0 OR repeat = 1``), but honoring ``db_path`` so the intake
    stays on its own connection. Store read only — never a network call."""
    prices: list[float] = []
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT price FROM price_alerts
            WHERE symbol = ? AND (triggered = 0 OR repeat = 1)
            """,
            (symbol.strip().upper(),),
        ).fetchall()
    for row in rows:
        try:
            if row["price"] is not None:
                prices.append(float(row["price"]))
        except (TypeError, ValueError):
            continue
    return prices


def _mint_structure_dot(
    episode: dict,
    ts: str,
    component: str,
    best: dict,
    trigger_price: float,
    source: str,
    db_path: Path,
) -> None:
    """Mint/refresh the single (episode, component) structure state dot.

    Latest/best per the state-dot idiom (see ``_attach_attention``): a STABLE
    source_event_id keyed on the episode+component means ``_insert_dot``'s
    ON CONFLICT refreshes the same row in place (points + detail) instead of
    appending — one dot per component, closest level's proximity in ``points``,
    the current level snapshot in ``detail``. Direction is always ``none`` so
    structure never enters a directional sum."""
    dist_atr = float(best.get("dist_atr") or 0.0)
    detail = {
        "component": component,
        "level_name": str(best.get("name") or "level"),
        "level_price": best.get("price"),
        "dist_atr": dist_atr,
        "threshold_atr": CONV_LEVEL_ATR,
        "trigger_price": trigger_price,
        "source": source,
        "display_only": False,
    }
    _insert_dot(
        episode_id=episode["id"],
        source_store="structure",
        source_event_id=f"{episode['id']}:{component}",
        source_ingest_seq=0,
        symbol=episode["symbol"],
        ts=ts,
        component=component,
        actor_group="structure",
        dot_kind="state",
        direction="none",
        raw_value=None,
        points=_structure_points(dist_atr),
        detail=detail,
        price=None,
        atr14=None,
        db_path=db_path,
    )
    bump("fusion_intake", "structure_dots", db_path=db_path)


def _attach_structure(
    episode: dict,
    ts: str,
    price: Decimal | None,
    atr14: Decimal | None,
    db_path: Path,
) -> None:
    """State-group STRUCTURE context for an actor-anchored episode (spec §2.1/§2.2).

    Runs ONLY inside the actor-dot path (the episode already exists) — structure
    is a STATE group and never creates a stack (the trigger rule). Derives, from
    stores only: (a) the nearest cached session level (PDH/PDL/VAL/VAH/POC) within
    CONV_LEVEL_ATR×ATR(14) of the actor dot's stamped price, and (b) any active
    founder price alert within the same proximity — reusing the legacy
    ``nearest_level`` proximity (never a second definition). Mints/refreshes at
    most one dot per component (closest wins). Storm-cheap: zero network. If the
    dot's price or ATR is unstamped, structure is skipped for this pass — counted,
    never raised."""
    if price is None or atr14 is None:
        bump("fusion_intake", "structure_skipped_unstamped", db_path=db_path)
        return
    try:
        price_f = float(price)
        atr_f = float(atr14)
    except (TypeError, ValueError):
        bump("fusion_intake", "structure_skipped_unstamped", db_path=db_path)
        return
    if atr_f <= 0:
        bump("fusion_intake", "structure_skipped_unstamped", db_path=db_path)
        return

    # Legacy level component — same stores, same 0.5×ATR proximity semantics.
    from signals.options_flow.convergence import nearest_level

    symbol = str(episode["symbol"])
    session_best = nearest_level(
        price=price_f,
        levels=fusion_db.get_session_levels(symbol, db_path=db_path),
        alert_prices=[],
        atr_value=atr_f,
    )
    if session_best is not None:
        _mint_structure_dot(
            episode, ts, "session_level", session_best, price_f, "session_levels", db_path
        )

    alert_best = nearest_level(
        price=price_f,
        levels=[],
        alert_prices=_active_alert_prices(symbol, db_path),
        atr_value=atr_f,
    )
    if alert_best is not None:
        _mint_structure_dot(
            episode, ts, "price_alert", alert_best, price_f, "price_alerts", db_path
        )


def _weekdays_after(start: date, end: date) -> int:
    """Weekdays in (start, end] — the cheap trading-day distance.

    Holidays count as trading days here (over-counts by one across a holiday),
    the same accepted trade-off as SESSION_SECONDS ignoring half-days. Spans
    beyond a month return a large sentinel instead of walking the calendar."""
    if end <= start:
        return 0
    if (end - start).days > 31:
        return 99
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def _attach_earnings(episode: dict, ts: str, market_day: str, db_path: Path) -> None:
    """Catalyst state dot: an earnings print inside the adjacency window.

    Scored latest/best like structure, on the same [0, 1] closeness scale —
    1.0 at tomorrow's print (or today's/just-reported), falling linearly to the
    window edge. Sides: ``upcoming`` (strictly future), ``today`` (calendar says
    this market day — the biased bmo/amc hour is deliberately unused), and
    ``reported`` (drift window). ``market_day`` is the EVENT's day, not the
    episode's opening day — proximity must move with time, and a symbol that
    LEAVES the window has its dot zeroed (a frozen last-good reading would lie
    to the dossier for the episode's whole TTL). Store read only, no network."""
    from db.earnings_calendar import last_earnings_date, next_earnings_date

    symbol = str(episode["symbol"])
    anchor = date.fromisoformat(market_day)
    candidates: list[dict[str, Any]] = []
    upcoming = next_earnings_date(symbol, market_day, db_path=db_path)
    if upcoming:
        try:
            distance = _weekdays_after(anchor, date.fromisoformat(upcoming))
        except ValueError:
            distance = 0
        if 1 <= distance <= FUSION_EARNINGS_AHEAD_TD:
            candidates.append(
                {
                    "side": "upcoming",
                    "earnings_date": upcoming,
                    "weekdays": distance,
                    "points": round(
                        (FUSION_EARNINGS_AHEAD_TD + 1 - distance) / FUSION_EARNINGS_AHEAD_TD, 4
                    ),
                }
            )
    reported = last_earnings_date(symbol, market_day, db_path=db_path)
    if reported:
        try:
            distance = _weekdays_after(date.fromisoformat(reported), anchor)
        except ValueError:
            distance = 99
        if distance <= FUSION_EARNINGS_BEHIND_TD:
            candidates.append(
                {
                    "side": "today" if reported == market_day else "reported",
                    "earnings_date": reported,
                    "weekdays": distance,
                    "points": round(
                        (FUSION_EARNINGS_BEHIND_TD + 1 - distance)
                        / (FUSION_EARNINGS_BEHIND_TD + 1),
                        4,
                    ),
                }
            )
    if not candidates:
        # Out of window: zero an existing dot instead of freezing it — read
        # first so the common no-earnings case costs no write transaction.
        stale_detail = fusion_db.serialize_detail(
            {
                "side": "none",
                "as_of": market_day,
                "window_ahead_td": FUSION_EARNINGS_AHEAD_TD,
                "window_behind_td": FUSION_EARNINGS_BEHIND_TD,
            }
        )
        with _connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT id, points FROM evidence_dots
                WHERE episode_id = ? AND component = 'earnings_proximity'
                """,
                (episode["id"],),
            ).fetchone()
            if row is not None and float(row["points"] or 0.0) != 0.0:
                conn.execute(
                    "UPDATE evidence_dots SET points = 0.0, detail_json = ? WHERE id = ?",
                    (stale_detail, row["id"]),
                )
                conn.commit()
        return
    best = max(candidates, key=lambda c: float(c["points"]))
    detail = {
        **best,
        "as_of": market_day,
        "window_ahead_td": FUSION_EARNINGS_AHEAD_TD,
        "window_behind_td": FUSION_EARNINGS_BEHIND_TD,
    }
    _insert_dot(
        episode_id=episode["id"],
        source_store="earnings_calendar",
        source_event_id=f"{episode['id']}:earnings_proximity",
        source_ingest_seq=0,
        symbol=symbol,
        ts=ts,
        component="earnings_proximity",
        actor_group="catalyst",
        dot_kind="state",
        direction="none",
        raw_value=_decimal(best["weekdays"]),
        points=float(best["points"]),
        detail=detail,
        price=None,
        atr14=None,
        db_path=db_path,
    )
    bump("fusion_intake", "earnings_dots", db_path=db_path)


# Market-wide context reads are identical for every episode on a market day —
# memoized so a busy intake pass costs one query burst per TTL, not per event.
_CONTEXT_MEMO: dict[tuple[str, str, str], tuple[float, Any]] = {}


def _context_memo(kind: str, market_day: str, db_path: Path, build: Callable[[], Any]) -> Any:
    key = (kind, market_day, str(db_path))
    hit = _CONTEXT_MEMO.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < FUSION_CONTEXT_REFRESH_S:
        return hit[1]
    value = build()
    if len(_CONTEXT_MEMO) > 64:
        _CONTEXT_MEMO.clear()  # tiny working set; clears itself across day/db rollover
    _CONTEXT_MEMO[key] = (now, value)
    return value


_REGIME_SERIES = ("VIXCLS", "vix3m", "DGS2", "DGS10")


def _regime_payload(market_day: str, db_path: Path) -> dict[str, Any] | None:
    from db.regime import latest_observation

    anchor = date.fromisoformat(market_day)
    readings: dict[str, tuple[str, float]] = {}
    for series in _REGIME_SERIES:
        obs = latest_observation(series, as_of=market_day, db_path=db_path)
        if obs is None:
            continue
        try:
            age = (anchor - date.fromisoformat(obs[0])).days
        except ValueError:
            continue
        if age > FUSION_REGIME_STALE_DAYS:
            continue
        readings[series] = obs
    if not readings:
        return None
    detail: dict[str, Any] = {"stale_days_max": FUSION_REGIME_STALE_DAYS}
    vix = readings.get("VIXCLS")
    vix3m = readings.get("vix3m")
    dgs2 = readings.get("DGS2")
    dgs10 = readings.get("DGS10")
    if vix is not None:
        detail["vix"] = vix[1]
        detail["vix_date"] = vix[0]
    if vix3m is not None:
        detail["vix3m"] = vix3m[1]
        detail["vix3m_date"] = vix3m[0]
    # Derived measures require DATE-ALIGNED observations — the two series come
    # from different feeds with different lags, and a cross-date ratio inverts
    # the term-structure verdict on exactly the spike days it exists to flag
    # (regime_sleeves joins on date for the same reason; never a second
    # definition). Levels above still surface individually when misaligned.
    if vix is not None and vix3m is not None and vix[0] == vix3m[0] and vix3m[1] > 0:
        # > 1 = term-structure backwardation (near-term stress priced above far).
        detail["vix_term_ratio"] = round(vix[1] / vix3m[1], 4)
    if dgs2 is not None and dgs10 is not None and dgs2[0] == dgs10[0]:
        detail["curve_2s10s"] = round(dgs10[1] - dgs2[1], 4)
        detail["curve_dates"] = [dgs2[0], dgs10[0]]
    return detail


def _attach_regime(episode: dict, ts: str, market_day: str, db_path: Path) -> None:
    """Context state dot (ANNOTATE-ONLY — the scorer never scores this group):
    the macro regime read (VIX level + term ratio, 2s10s curve) riding on the
    episode so the card/dossier can show the tape's weather. Series staler than
    FUSION_REGIME_STALE_DAYS mint nothing — counted, never raised."""
    detail = _context_memo(
        "regime", market_day, db_path, lambda: _regime_payload(market_day, db_path)
    )
    if not detail:
        bump("fusion_intake", "regime_skipped_stale", db_path=db_path)
        return
    _insert_dot(
        episode_id=episode["id"],
        source_store="market_context",
        source_event_id=f"{episode['id']}:regime",
        source_ingest_seq=0,
        symbol=episode["symbol"],
        ts=ts,
        component="regime",
        actor_group="context",
        dot_kind="state",
        direction="none",
        raw_value=_decimal(detail.get("vix")),
        points=0.0,
        detail=detail,
        price=None,
        atr14=None,
        db_path=db_path,
    )
    bump("fusion_intake", "regime_dots", db_path=db_path)


def _index_pressure_payload(market_day: str, db_path: Path) -> dict[str, Any] | None:
    from db.index_pressure import day_summary

    for window in ("close", "open"):
        rows = day_summary(market_day, window=window, db_path=db_path)
        if not rows:
            continue
        bull = sum(r["bull_usd"] for r in rows)
        bear = sum(r["bear_usd"] for r in rows)
        gross = bull + bear + sum(r["unclassified_usd"] for r in rows)
        return {
            "date": market_day,
            "window": window,
            "bull_usd": round(bull, 2),
            "bear_usd": round(bear, 2),
            "net_usd": round(bull - bear, 2),
            "classified_frac": round((bull + bear) / gross, 4) if gross > 0 else 0.0,
            "underlyings": len(rows),
            "top": [
                {"underlying": r["underlying"], "net_usd": round(r["net_usd"], 2)}
                for r in rows[:3]
            ],
        }
    return None


def _attach_index_pressure(episode: dict, ts: str, market_day: str, db_path: Path) -> None:
    """Context state dot (ANNOTATE-ONLY): today's index-options pressure gauge
    read, market-wide by construction (#93 — the gauge lane, never a wildcard
    trades sub). Prefers the close window once it has data, else the open; a day
    with no gauge minutes mints nothing — counted, never raised."""
    payload = _context_memo(
        "index_pressure",
        market_day,
        db_path,
        lambda: _index_pressure_payload(market_day, db_path),
    )
    if not payload:
        bump("fusion_intake", "index_pressure_skipped_none", db_path=db_path)
        return
    _insert_dot(
        episode_id=episode["id"],
        source_store="index_pressure",
        source_event_id=f"{episode['id']}:index_pressure",
        source_ingest_seq=0,
        symbol=episode["symbol"],
        ts=ts,
        component="index_pressure",
        actor_group="context",
        dot_kind="state",
        direction="none",
        raw_value=_decimal(payload.get("net_usd")),
        points=0.0,
        detail=payload,
        price=None,
        atr14=None,
        db_path=db_path,
    )
    bump("fusion_intake", "index_pressure_dots", db_path=db_path)


def _rescore(episode_id: str, db_path: Path) -> dict[str, Any]:
    dots = fusion_db.episode_dots(episode_id, db_path=db_path)
    state = score_dots(dots)
    signature = ",".join(sorted({str(dot["component"]) for dot in dots}))
    collapsed = set(state["collapsed_dot_ids"])
    now = now_iso()
    # Persist the v2 decayed thesis mass summary alongside the legacy undecayed
    # score (§2). The sweep re-derives masses from evidence_dots for its close
    # decisions (never from these columns — an all-seed episode with no fresh
    # dots still decays to noise); this summary is what rehydration reads later,
    # so the §2.4 seed is folded in forward-decayed from open rather than erased.
    thesis = score_decayed(dots, now)["thesis"]
    # The z dual-gate (investigate now; ARM at Round 5) reads this SEEDLESS
    # decayed thesis — the exact math the fusion_z_baseline T tables were
    # computed from, so the founder's T pick stays on one scale (seed folding
    # below applies only to the persisted episode summary).
    state["decayed_thesis"] = thesis
    bull, bear, conviction = (
        thesis["bull_mass"],
        thesis["bear_mass"],
        thesis["conviction"],
    )
    with _connect(db_path) as conn:
        ep = conn.execute(
            "SELECT opened_at, seed_bull_mass, seed_bear_mass FROM fusion_episodes WHERE id = ?",
            (episode_id,),
        ).fetchone()
    seed_bull = float(ep["seed_bull_mass"] or 0.0) if ep is not None else 0.0
    seed_bear = float(ep["seed_bear_mass"] or 0.0) if ep is not None else 0.0
    if seed_bull or seed_bear:
        try:
            elapsed = session_seconds_between(_parse_iso(str(ep["opened_at"])), _parse_iso(now))
        except (TypeError, ValueError):
            elapsed = 0.0
        seed_decay = 0.5 ** (elapsed / (THESIS_HALF_LIFE_TD * SESSION_SECONDS))
        bull = round(bull + seed_bull * seed_decay, 4)
        bear = round(bear + seed_bear * seed_decay, 4)
        denom = bull + bear + CONVICTION_PRIOR_MASS
        conviction = round(abs(bull - bear) / denom, 4) if denom > 0 else 0.0
    last_evidence_at = max((str(dot["ts"]) for dot in dots), default=None)
    with _connect(db_path) as conn:
        # AND state='open': a closed episode is immutable — this UPDATE can race
        # the lifecycle sweep's close, and the replay path hands _rescore a
        # closed episode on purpose (review finding: without the guard, replays
        # rewrote closed episodes' cold-storage summaries).
        conn.execute(
            """
            UPDATE fusion_episodes SET
                score = ?, direction_state = ?, contested = ?,
                signature = ?, bull_mass = ?, bear_mass = ?, conviction = ?,
                last_evidence_at = ?, updated_at = ?
            WHERE id = ? AND state = 'open'
            """,
            (
                state["score"],
                state["direction_state"],
                int(state["contested"]),
                signature,
                bull,
                bear,
                conviction,
                last_evidence_at,
                now,
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
    """Register the SYMBOL-DAY on its first stampable directional actor dot.

    2026-07-28 restore (founder-blessed): Round-3 long-lived episodes had turned
    the boolean ``registration_called`` latch into once-per-EPISODE-LIFETIME —
    a silent estimand drift from the spec's pinned "one registration per
    symbol-day" (volume fell 605/day → 120/day as episodes saturated). The latch
    is now ``registered_market_day``, keyed on the DOT's session. The episode's
    own anchor_* columns still latch exactly once (first-ever anchor): the
    lifecycle sweep's price-invalidation measures from them, and rolling that
    reference daily would be a second silent semantics change. Later days'
    registrations carry their day-anchor via an overlay dict instead."""
    with _connect(db_path) as conn:
        episode_row = conn.execute(
            "SELECT * FROM fusion_episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if episode_row is None:
            return
        dot_row = conn.execute(
            "SELECT * FROM evidence_dots WHERE id = ?", (anchor_dot_id,)
        ).fetchone()
        if dot_row is None:
            return
        # Never anchor on an unstamped dot: a crash-replay can present a dot
        # whose persisted stamps predate the caches warming even though THIS
        # pass has live price/ATR. Latching a NULL anchor would burn the whole
        # symbol-day (register_stack refuses it, and the latch blocks every
        # later stampable dot). Leave the day unlatched instead.
        if (
            not int(dot_row["stamped"] or 0)
            or dot_row["trigger_price"] is None
            or dot_row["prior_close_atr14"] is None
            or dot_row["direction"] not in ("bull", "bear")
        ):
            return
        dot_day = _market_day(str(dot_row["ts"]))
        # MONOTONIC latch (review H1/H2): strictly newer days only. An equal
        # day is already registered; an OLDER day (lagging store cursor,
        # multi-session outage catch-up) must never register — the ledger's
        # dedup wall is a PARTIAL index over open rows, so a resolved
        # symbol-day would re-enter the cohort at a different price, and a
        # backward latch write would then let the current day re-register too.
        # You cannot honestly register a past session's plan at today's price.
        if dot_day <= str(episode_row["registered_market_day"] or ""):
            return
        # First-ever anchor still latches onto the episode row (lifecycle
        # reference); the registration_called guard keeps it write-once.
        conn.execute(
            """
            UPDATE fusion_episodes SET
                anchor_dot_id = ?, anchor_price = ?, anchor_atr14 = ?,
                anchor_direction = ?
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
    # The day's registration always anchors on TODAY's dot — for the episode's
    # first day the overlay equals the just-latched episode anchor; for later
    # days it is the day-anchor the episode columns deliberately do not take.
    try:
        dot_detail = json.loads(dot_row["detail_json"] or "{}")
        dot_price_source = str(dot_detail.get("price_source") or "cache")
    except (TypeError, ValueError):
        dot_price_source = "cache"
    day_anchor = {
        "anchor_dot_id": anchor_dot_id,
        "anchor_price": dot_row["trigger_price"],
        "anchor_atr14": dot_row["prior_close_atr14"],
        "anchor_direction": dot_row["direction"],
        # Review S3: the plan's entry may be a spot/print rather than a live
        # cache read — the ledger row must say so (see registrar source_ref).
        "anchor_price_source": dot_price_source,
    }
    registrar.register_stack(
        {**episode, **day_anchor},
        fusion_db.episode_dots(episode_id, db_path=db_path),
        {**score_state, "signature": episode["signature"]},
        db_path=db_path,
    )
    # Latch ONLY after the ledger call returned. A crash (or an exception from
    # register_stack) before this point leaves the day unlatched, so the
    # replayed event retries; register_idea's dedup_key wall absorbs the
    # duplicate call (test_same_symbol_day_registers_once proves it). The old
    # order — latch first, register second — turned any crash in that window
    # into a silent, permanent hole in the measurement ledger.
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE fusion_episodes SET
                registration_called = 1,
                registered_market_day = MAX(COALESCE(registered_market_day, ''), ?)
            WHERE id = ?
            """,
            (dot_day, episode_id),
        )
        conn.commit()


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
    init_chart_features(db_path)  # price_alerts store, read by _attach_structure
    counters = {key: 0 for key in fusion_db.DISPOSITIONS}
    lookup = price_lookup or (lambda _symbol: None)
    shock = bool(shock_lookup()) if shock_lookup is not None else False
    context_done: set[tuple[str, str]] = set()  # (episode_id, market_day) this pass

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
            if price is None and store == "equity_tape":
                # Off-cache tape names (the ASTS case: 64/64 dots unstampable,
                # $70-130M/day of one-sided prints invisible to the live lane)
                # stamp from the print's own execution price. This flips their
                # dots into the MASS (magnitude still comes from notional) and
                # also becomes the only price lifecycle sees for them (anchor
                # latch, 1.5×ATR price_invalidation, level triggers via the
                # _latest_price DB fallback). Dark prints can report late or
                # off-NBBO, so a rogue print CAN falsely invalidate — accepted
                # noise: the lane is observe-only, episodes self-heal on the
                # next dot, and it beats the alternative (price-blind, forever
                # inert). Auditable via close_reason='price_invalidation' on
                # print-stamped symbols; robustify (median-of-recent-prints)
                # only if the ledger shows it. Labeled for the honesty trail.
                fallback = _decimal(event.get("price"))
                if fallback is not None and fallback > 0:
                    price = fallback
                    shape["detail"]["price_source"] = "print"
            if price is None:
                # Whole-market fallback (2026-07-28): options events carry no
                # underlying price and the bars caches are watchlist-scoped, so
                # the 07-27 whole-market flow lane minted 4,848 unpriced dots
                # in one day. fusion_spot_cache is the equity collector's
                # consolidated last trade for every US name — freshness-bounded
                # so an overnight/collector-down spot reads as missing, never
                # as a price. Labeled for the honesty trail.
                spot = fusion_db.get_spot(
                    shape["symbol"],
                    max_age_s=FUSION_SPOT_MAX_AGE_S,
                    as_of=str(event["ts"]),  # fence vs the EVENT, not the wall clock
                    db_path=db_path,
                )
                if spot is not None:
                    price = spot
                    shape["detail"]["price_source"] = "tape_spot"
            atr14 = fusion_db.get_atr(
                shape["symbol"],
                as_of=_market_day(str(event["ts"])),
                db_path=db_path,
            )
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
            _attach_structure(episode, str(event["ts"]), price, atr14, db_path)
            # Context/catalyst facts are per-(episode, market-day), not
            # per-event: run once per pass so a storm burst can't multiply
            # write commits on the single-writer WAL (the 07-27 lock-starve
            # class). The 60s poll cadence is the refresh clock.
            context_key = (str(episode["id"]), _market_day(str(event["ts"])))
            if context_key not in context_done:
                context_done.add(context_key)
                _attach_earnings(episode, str(event["ts"]), context_key[1], db_path)
                _attach_regime(episode, str(event["ts"]), context_key[1], db_path)
                _attach_index_pressure(episode, str(event["ts"]), context_key[1], db_path)
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
            from signals.fusion.investigate import evaluate_investigation

            # The DOT's session, not the episode's opening day: long-lived
            # episodes keep their birth market_day, and budgeting/metering on
            # that filed decisions under sessions long past (07-28 audit — the
            # two 07-27 admissions were booked to 07-22/07-23).
            evaluate_investigation(
                current_episode, state, market_day=context_key[1], db_path=db_path
            )
            counters[disposition] += 1
            fusion_db.set_cursor(store, int(event["ingest_seq"]), db_path=db_path)

    return counters
