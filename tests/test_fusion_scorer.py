from __future__ import annotations

from signals.fusion.scorer import options_direction, score_dots


def _dot(
    dot_id: str,
    group: str,
    direction: str,
    value: str,
    ts: str,
    *,
    points: float = 0.0,
) -> dict:
    return {
        "id": dot_id,
        "actor_group": group,
        "direction": direction,
        "raw_value": value,
        "ts": ts,
        "points": points,
        "detail": {},
    }


def test_four_way_options_direction_mapping() -> None:
    assert options_direction("call", "buy_side") == "bull"
    assert options_direction("put", "buy_side") == "bear"
    assert options_direction("call", "sell_side") == "bear"
    assert options_direction("put", "sell_side") == "bull"
    assert options_direction("call", "undetermined") == "none"


def test_same_direction_collapse_uses_larger_group() -> None:
    state = score_dots(
        [
            _dot("o", "options_actor", "bull", "2000000", "2026-07-17T14:30:00Z"),
            _dot("e", "equity_actor", "bull", "1000000", "2026-07-17T14:34:59Z"),
        ]
    )
    assert state["collapsed_dot_ids"] == ["e"]
    assert state["group_scores"] == {"options_actor": 3.0}


def test_opposing_actor_directions_never_collapse() -> None:
    state = score_dots(
        [
            _dot("o", "options_actor", "bull", "1000000", "2026-07-17T14:30:00Z"),
            _dot("e", "equity_actor", "bear", "4000000", "2026-07-17T14:31:00Z"),
        ]
    )
    assert state["collapsed_dot_ids"] == []
    assert set(state["group_scores"]) == {"options_actor", "equity_actor"}
    assert state["direction_state"] == "mixed"


def test_contested_zeroes_agreement_bonus() -> None:
    state = score_dots(
        [
            _dot("ob", "options_actor", "bull", "1000000", "2026-07-17T14:30:00Z"),
            _dot("os", "options_actor", "bear", "1000000", "2026-07-17T14:40:00Z"),
            _dot("eb", "equity_actor", "bull", "4000000", "2026-07-17T14:50:00Z"),
        ]
    )
    assert state["contested"] is True
    assert state["direction_state"] == "contested"
    assert state["agreement_bonus"] == 0.0


def test_breadth_bonus_caps_at_one_point_five() -> None:
    dots = [
        _dot("o", "options_actor", "bull", "1000000", "2026-07-17T14:00:00Z"),
        _dot("e", "equity_actor", "bull", "4000000", "2026-07-17T15:00:00Z"),
    ]
    for group in ("structure", "attention", "catalyst"):
        dots.append(_dot(group, group, "none", "1", "2026-07-17T15:10:00Z", points=1.0))
    state = score_dots(dots)
    assert state["breadth_bonus"] == 1.5
