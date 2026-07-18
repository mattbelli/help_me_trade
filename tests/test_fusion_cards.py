from __future__ import annotations

from pathlib import Path

from db.fusion import init_fusion_tables
from db.notifications import init_notifications_tables, list_notifications
from signals.fusion.cards import update_stack_card


def _episode(symbol: str, score: float) -> tuple[dict, dict]:
    episode = {
        "id": f"episode-{symbol}",
        "symbol": symbol,
        "market_day": "2026-07-17",
        "signature": "option_sweep",
        "market_shock": 0,
    }
    state = {
        "score": score,
        "direction_state": "aligned(bull)",
        "contested": False,
    }
    return episode, state


def test_cap_evicts_weakest_card_and_marks_it(tmp_path: Path) -> None:
    db = tmp_path / "cards.db"
    init_fusion_tables(db)
    init_notifications_tables(db)
    for symbol, score in (("WEAK", 1.0), ("MID", 2.0), ("STRONG", 5.0)):
        episode, state = _episode(symbol, score)
        update_stack_card(
            episode,
            state,
            db_path=db,
            enabled=True,
            daily_cap=2,
        )

    visible = list_notifications(db_path=db)
    assert {row["data"]["symbol"] for row in visible} == {"MID", "STRONG"}
    all_rows = list_notifications(include_dismissed=True, db_path=db)
    weak = next(row for row in all_rows if row["data"]["symbol"] == "WEAK")
    assert weak["dismissed"] is True
    assert weak["data"]["evicted"] is True
    assert weak["data"]["eviction_reason"] == "score_ranked_cap"


def test_evicted_card_reenters_only_through_cap_competition(tmp_path: Path) -> None:
    db = tmp_path / "cards.db"
    init_fusion_tables(db)
    init_notifications_tables(db)
    for symbol, score in (("WEAK", 1.0), ("MID", 2.0), ("STRONG", 5.0)):
        episode, state = _episode(symbol, score)
        update_stack_card(episode, state, db_path=db, enabled=True, daily_cap=2)
    # WEAK is evicted. A later dot that leaves it weaker than the current
    # weakest card must NOT resurrect it past the cap.
    episode, state = _episode("WEAK", 1.5)
    assert update_stack_card(episode, state, db_path=db, enabled=True, daily_cap=2) is None
    assert {row["data"]["symbol"] for row in list_notifications(db_path=db)} == {"MID", "STRONG"}
    # But if WEAK strengthens past the weakest live card, it re-enters and
    # the new weakest is evicted — the cap holds at 2 throughout.
    episode, state = _episode("WEAK", 3.0)
    assert update_stack_card(episode, state, db_path=db, enabled=True, daily_cap=2) is not None
    assert {row["data"]["symbol"] for row in list_notifications(db_path=db)} == {"WEAK", "STRONG"}


def test_founder_dismissed_card_never_resurrects(tmp_path: Path) -> None:
    from db.notifications import dismiss

    db = tmp_path / "cards.db"
    init_fusion_tables(db)
    init_notifications_tables(db)
    episode, state = _episode("TSLA", 2.0)
    card = update_stack_card(episode, state, db_path=db, enabled=True)
    assert card is not None
    dismiss(card["id"], db_path=db)
    state["score"] = 99.0
    assert update_stack_card(episode, state, db_path=db, enabled=True) is None
    assert list_notifications(db_path=db) == []


def test_group_key_updates_in_place(tmp_path: Path) -> None:
    db = tmp_path / "cards.db"
    init_fusion_tables(db)
    init_notifications_tables(db)
    episode, state = _episode("TSLA", 2.0)
    first = update_stack_card(episode, state, db_path=db, enabled=True)
    state["score"] = 4.0
    second = update_stack_card(episode, state, db_path=db, enabled=True)
    assert first is not None and second is not None
    assert first["id"] == second["id"]
    assert len(list_notifications(db_path=db)) == 1
    assert second["data"]["score"] == 4.0
