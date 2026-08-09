"""tools/board.py — the canonical board writer agents are required to use."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import board as B


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    live, archive = tmp_path / "board.json", tmp_path / "board_archive.json"
    live.write_text(
        json.dumps(
            {
                "updated_at": "2026-08-01",
                "lanes": ["next", "done"],
                "cards": [
                    {"num": 2, "id": "beta", "title": "Beta", "lane": "next", "notes": ""},
                    {"num": 1, "id": "alpha", "title": "Alpha", "lane": "done", "notes": "x"},
                ],
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(B, "LIVE_PATH", live)
    monkeypatch.setattr(B, "ARCHIVE_PATH", archive)
    return live, archive


def _cards(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cards"]


def test_write_is_canonical_and_orders_keys(paths) -> None:
    live, _ = paths
    board = B._read(live)
    board["cards"][0] = {"notes": "n", "legacy": "keep", "title": "T", "id": "z", "num": 9}
    B._write(live, board)

    raw = live.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=1, ensure_ascii=False) + "\n"
    # Canonical order first, unknown/legacy fields preserved after it.
    assert list(_cards(live)[0]) == ["num", "id", "title", "notes", "legacy"]


def test_add_takes_the_next_number_and_lands_on_top(paths) -> None:
    live, _ = paths
    B.cmd_add(
        SimpleNamespace(
            title="Fix the thing",
            id=None,
            lane="next",
            type="bug",
            priority="high",
            owner="agent",
            notes="",
        )
    )
    card = _cards(live)[0]
    assert card["num"] == 3
    assert card["id"] == "fix-the-thing"


def test_cards_resolve_by_number_or_slug(paths) -> None:
    live, _ = paths
    B.cmd_move(SimpleNamespace(ref="#2", lane="done", note=None))
    assert _cards(live)[0]["lane"] == "done"

    B.cmd_note(SimpleNamespace(ref="beta", text="second look"))
    assert _cards(live)[0]["notes"].endswith(f"{date.today().isoformat()}: second look")


def test_archive_sweeps_only_settled_done_cards(paths) -> None:
    live, archive = paths
    board = B._read(live)
    stale = (date.today() - timedelta(days=90)).isoformat()
    board["cards"][1]["updated_at"] = stale
    board["cards"][0]["updated_at"] = date.today().isoformat()
    B._write(live, board)

    B.cmd_archive(SimpleNamespace(days=14))

    assert [c["id"] for c in _cards(live)] == ["beta"]
    assert [c["id"] for c in _cards(archive)] == ["alpha"]


def test_reopening_an_archived_card_returns_it_to_the_live_file(paths) -> None:
    live, archive = paths
    board = B._read(live)
    board["cards"][1]["updated_at"] = (date.today() - timedelta(days=90)).isoformat()
    B._write(live, board)
    B.cmd_archive(SimpleNamespace(days=14))

    B.cmd_move(SimpleNamespace(ref="1", lane="in_progress", note="reopened"))

    assert [c["id"] for c in _cards(live)] == ["alpha", "beta"]
    assert _cards(archive) == []


def test_fmt_repairs_a_reformatted_board(paths) -> None:
    live, _ = paths
    # What an agent's default json.dump does — the whole-file reformat this tool exists to prevent.
    live.write_text(json.dumps(B._read(live), indent=2), encoding="utf-8")

    B.cmd_fmt(SimpleNamespace())

    raw = live.read_text(encoding="utf-8")
    assert raw == json.dumps(json.loads(raw), indent=1, ensure_ascii=False) + "\n"
