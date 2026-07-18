"""Bar-gated, score-ranked Fusion stack inbox cards."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from config.idea_engine import (
    FUSION_CARD_DAILY_CAP,
    FUSION_CARD_TRAILING_PERCENTILE,
    FUSION_CARDS_ENABLED,
)
from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.notifications import evict_notification, upsert_group_notification


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return ordered[index]


def _card_score(row: Any) -> float:
    try:
        payload = json.loads(row["data_json"] or "{}")
        return float(payload.get("score") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def update_stack_card(
    episode: dict[str, Any],
    score_state: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    enabled: bool | None = None,
    daily_cap: int = FUSION_CARD_DAILY_CAP,
    percentile: float = FUSION_CARD_TRAILING_PERCENTILE,
) -> dict[str, Any] | None:
    """Create/update one digest card, evicting the weakest when the cap is full."""
    if enabled is None:
        enabled = FUSION_CARDS_ENABLED
    if not enabled:
        return None
    score = float(score_state["score"])
    day = str(episode["market_day"])
    start = (date.fromisoformat(day) - timedelta(days=7)).isoformat()
    group_key = f"fusion:{episode['symbol']}:{day}"
    with _connect(db_path) as conn:
        history = [
            float(row[0])
            for row in conn.execute(
                """
                SELECT score FROM fusion_episodes
                WHERE market_day >= ? AND market_day < ?
                """,
                (start, day),
            ).fetchall()
        ]
        existing = conn.execute(
            """
            SELECT id, dismissed_at, data_json FROM notifications
            WHERE kind = 'fusion_stack' AND group_key = ?
            """,
            (group_key,),
        ).fetchone()
        cards = conn.execute(
            """
            SELECT id, data_json FROM notifications
            WHERE kind = 'fusion_stack'
              AND group_key LIKE ?
              AND dismissed_at IS NULL
            """,
            (f"fusion:%:{day}",),
        ).fetchall()

    # A live card updates in place unconditionally. A dismissed card only ever
    # returns if it was EVICTED (cap competition) — and then it competes like a
    # newcomer; a founder-dismissed card stays dismissed no matter what lands.
    if existing is not None and existing["dismissed_at"] is not None:
        try:
            was_evicted = bool(json.loads(existing["data_json"] or "{}").get("evicted"))
        except (TypeError, json.JSONDecodeError):
            was_evicted = False
        if not was_evicted:
            return None
        existing = None

    bar = _percentile(history, percentile)
    if existing is None and bar is not None and score < bar:
        return None
    if existing is None and len(cards) >= daily_cap:
        weakest = min(cards, key=_card_score)
        weakest_score = _card_score(weakest)
        if score <= weakest_score:
            return None
        evict_notification(str(weakest["id"]), db_path=db_path)

    payload = {
        "symbol": episode["symbol"],
        "market_day": day,
        "episode_id": episode["id"],
        "score": score,
        "direction_state": score_state["direction_state"],
        "contested": bool(score_state["contested"]),
        "signature": episode.get("signature") or "",
        "market_shock": bool(episode.get("market_shock")),
        "evicted": False,
    }
    return upsert_group_notification(
        group_key=group_key,
        kind="fusion_stack",
        title=f"{episode['symbol']}: evidence stack",
        body=(
            f"score {score:.1f} · {score_state['direction_state']} · "
            f"{episode.get('signature') or 'actor evidence'}"
        ),
        ref_type="fusion_episode",
        ref_id=str(episode["id"]),
        data=payload,
        db_path=db_path,
    )
