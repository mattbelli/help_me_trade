"""Phase 0 memory migration — archive legacy rows (idempotent, dry-run by default)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "trading.db"
sys.path.insert(0, str(ROOT))

from db.ai_tables import init_ai_tables  # noqa: E402


def _preview_rows(conn: sqlite3.Connection) -> list[tuple[int, str, str, str]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, memory_type, COALESCE(status, 'active') AS status, content
        FROM memories
        ORDER BY id
        """
    ).fetchall()
    return [
        (
            int(row["id"]),
            str(row["memory_type"]),
            str(row["status"]),
            str(row["content"]),
        )
        for row in rows
    ]


def _print_table(label: str, rows: list[tuple[int, str, str, str]]) -> None:
    print(f"\n{label} ({len(rows)} rows)")
    print(f"{'id':>5}  {'type':<12}  {'status':<12}  content")
    print("-" * 80)
    for mem_id, mem_type, status, content in rows:
        snippet = content.replace("\n", " ")[:60]
        print(f"{mem_id:5d}  {mem_type:<12}  {status:<12}  {snippet}")


def migrate(*, db_path: Path, apply: bool) -> int:
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    # Ensure the Phase-0 schema (status column, FTS index + triggers) exists —
    # the script must be runnable before the app has ever booted on new code.
    init_ai_tables(db_path)

    conn = sqlite3.connect(db_path)
    try:
        before = _preview_rows(conn)
        _print_table("Before", before)

        active_before = sum(1 for _id, _t, status, _c in before if status == "active")
        to_archive = [
            row
            for row in before
            if row[2] != "archived"
        ]

        if apply:
            conn.execute(
                """
                UPDATE memories
                SET status = 'archived'
                WHERE COALESCE(status, 'active') != 'archived'
                """
            )
            conn.commit()
            after = _preview_rows(conn)
        else:
            after = [
                (mem_id, mem_type, "archived", content)
                for mem_id, mem_type, _status, content in to_archive
            ] + [
                row for row in before if row[2] == "archived"
            ]
            after.sort(key=lambda row: row[0])

        _print_table("After (projected)" if not apply else "After", after)

        active_after = sum(1 for _id, _t, status, _c in after if status == "active")
        print(
            f"\nActive rows: {active_before} → {active_after}"
            f" ({'APPLIED' if apply else 'DRY RUN — pass --apply to execute'})"
        )
    finally:
        conn.close()

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 0 memory migration: archive all legacy memory rows."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute migration (default is dry-run preview only).",
    )
    args = parser.parse_args()
    raise SystemExit(migrate(db_path=args.db, apply=args.apply))


if __name__ == "__main__":
    main()
