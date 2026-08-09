#!/usr/bin/env python3
"""Canonical reader/writer for the project kanban — the only way agents should edit it.

Two files, one board:
  References/board.json          live cards (inbox/founder/in_progress/next/backlog + recent done)
  References/board_archive.json  settled `done` cards, swept here by `archive`

The on-disk format is ``json.dumps(indent=1, ensure_ascii=False) + "\\n"`` with a canonical
key order. Any other dump — indent=2, or ensure_ascii=True — rewrites all ~2,700 lines, and a
whole-file reformat conflicts with every other open branch. api/main.py:_save_board_atomic
writes the identical format; keep the two in sync.

  tools/board.py add   --title "..." [--lane next] [--type feature] [--notes "..."]
  tools/board.py move  <num|id> <lane> [--note "..."]
  tools/board.py note  <num|id> "..."
  tools/board.py show  <num|id>
  tools/board.py list  [--lane next]
  tools/board.py archive [--days 14]
  tools/board.py fmt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REFERENCES = Path(__file__).resolve().parent.parent / "References"
LIVE_PATH = REFERENCES / "board.json"
ARCHIVE_PATH = REFERENCES / "board_archive.json"

# Cards serialize in this key order; anything else a card carries is preserved and
# appended after these (legacy `status`/`updated`/`created` fields live on old cards).
KEY_ORDER = (
    "num",
    "id",
    "title",
    "lane",
    "type",
    "priority",
    "owner",
    "created_at",
    "updated_at",
    "notes",
)
ARCHIVE_AFTER_DAYS = 14


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"cards": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _canon_card(card: dict) -> dict:
    out = {k: card[k] for k in KEY_ORDER if k in card}
    out.update({k: v for k, v in card.items() if k not in out})
    return out


def _write(path: Path, board: dict) -> None:
    board["cards"] = [_canon_card(c) for c in board.get("cards", [])]
    board["updated_at"] = date.today().isoformat()
    text = json.dumps(board, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _next_num(*boards: dict) -> int:
    nums = [c["num"] for b in boards for c in b.get("cards", []) if isinstance(c.get("num"), int)]
    return max(nums, default=0) + 1


def _find(ref: str, *boards: dict) -> tuple[dict, dict]:
    """Resolve `#101`, `101`, or an id slug to (card, the board holding it)."""
    key = ref.lstrip("#")
    for board in boards:
        for card in board.get("cards", []):
            if card.get("id") == ref or (key.isdigit() and card.get("num") == int(key)):
                return card, board
    sys.exit(f"no card matching {ref!r} (try: tools/board.py list)")


def _slug(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")[:48]


def _stamp(card: dict, text: str) -> None:
    """Append a dated line to the card's notes — the log convention on every card."""
    line = f"{date.today().isoformat()}: {text}"
    card["notes"] = f"{card.get('notes', '')}\n{line}".strip()
    card["updated_at"] = date.today().isoformat()


def cmd_add(args: argparse.Namespace) -> None:
    live, archive = _read(LIVE_PATH), _read(ARCHIVE_PATH)
    card_id = args.id or _slug(args.title)
    if any(c.get("id") == card_id for c in live["cards"] + archive["cards"]):
        sys.exit(f"card id {card_id!r} already exists")
    card = {
        "num": _next_num(live, archive),
        "id": card_id,
        "title": args.title,
        "lane": args.lane,
        "type": args.type,
        "priority": args.priority,
        "owner": args.owner,
        "updated_at": date.today().isoformat(),
        "notes": args.notes,
    }
    live["cards"].insert(0, card)
    _write(LIVE_PATH, live)
    print(f"#{card['num']} {card['id']} -> {card['lane']}")


def cmd_move(args: argparse.Namespace) -> None:
    live, archive = _read(LIVE_PATH), _read(ARCHIVE_PATH)
    card, holder = _find(args.ref, live, archive)
    card["lane"] = args.lane
    card["updated_at"] = date.today().isoformat()
    if args.note:
        _stamp(card, args.note)
    if holder is archive and args.lane != "done":
        # Reopened: an archived card belongs back in the live file.
        archive["cards"].remove(card)
        live["cards"].insert(0, card)
        _write(ARCHIVE_PATH, archive)
    _write(LIVE_PATH, live)
    print(f"#{card.get('num')} {card['id']} -> {args.lane}")


def cmd_note(args: argparse.Namespace) -> None:
    live, archive = _read(LIVE_PATH), _read(ARCHIVE_PATH)
    card, holder = _find(args.ref, live, archive)
    _stamp(card, args.text)
    _write(LIVE_PATH if holder is live else ARCHIVE_PATH, holder)
    print(f"#{card.get('num')} {card['id']} noted")


def cmd_show(args: argparse.Namespace) -> None:
    card, _ = _find(args.ref, _read(LIVE_PATH), _read(ARCHIVE_PATH))
    print(json.dumps(_canon_card(card), indent=1, ensure_ascii=False))


def cmd_list(args: argparse.Namespace) -> None:
    live, archive = _read(LIVE_PATH), _read(ARCHIVE_PATH)
    for card in live["cards"] + (archive["cards"] if args.lane == "done" else []):
        if args.lane and card.get("lane") != args.lane:
            continue
        print(f"#{str(card.get('num', '?')):>4}  {card.get('lane', ''):<12} {card['title'][:88]}")


def cmd_archive(args: argparse.Namespace) -> None:
    live, archive = _read(LIVE_PATH), _read(ARCHIVE_PATH)
    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    old = [
        c
        for c in live["cards"]
        if c.get("lane") == "done" and (c.get("updated_at") or "") < cutoff
    ]
    if not old:
        print("nothing to archive")
        return
    live["cards"] = [c for c in live["cards"] if c not in old]
    archive["cards"] = old + archive["cards"]
    _write(LIVE_PATH, live)
    _write(ARCHIVE_PATH, archive)
    print(f"archived {len(old)} done cards older than {cutoff}")


def cmd_fmt(_args: argparse.Namespace) -> None:
    """Restore canonical formatting — the repair after something re-dumped the board."""
    for path in (LIVE_PATH, ARCHIVE_PATH):
        if path.is_file():
            before = path.read_text(encoding="utf-8")
            _write(path, _read(path))
            after = path.read_text(encoding="utf-8")
            print(f"{path.name}: {'rewritten' if before != after else 'already canonical'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="open a new card")
    p.add_argument("--title", required=True)
    p.add_argument("--id", help="slug id (default: derived from the title)")
    p.add_argument("--lane", default="next")
    p.add_argument("--type", default="feature")
    p.add_argument("--priority", default="medium")
    p.add_argument("--owner", default="agent")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("move", help="change a card's lane")
    p.add_argument("ref")
    p.add_argument("lane")
    p.add_argument("--note", help="also append this dated line to the notes")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("note", help="append a dated line to a card's notes")
    p.add_argument("ref")
    p.add_argument("text")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("show", help="print one card")
    p.add_argument("ref")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("list", help="one line per card")
    p.add_argument("--lane")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("archive", help="sweep settled done cards into board_archive.json")
    p.add_argument("--days", type=int, default=ARCHIVE_AFTER_DAYS)
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("fmt", help="restore canonical formatting to both files")
    p.set_defaults(func=cmd_fmt)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
