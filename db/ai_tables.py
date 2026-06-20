"""SQLite tables for AI memories, research artifacts, rules, and reflections."""

from __future__ import annotations

import json
import re
import sqlite3
import string
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now

MEMORY_TYPES = frozenset({"preference", "lesson", "research"})


def init_ai_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS research_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                symbol TEXT,
                markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reflection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                lessons_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                conditions_json TEXT NOT NULL,
                action_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rule_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (rule_id) REFERENCES rules(id)
            );

            CREATE INDEX IF NOT EXISTS idx_memories_type_created
                ON memories(memory_type, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_research_created
                ON research_artifacts(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_reflection_created
                ON reflection_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_rule_events_created
                ON rule_events(created_at DESC);
            """
        )
        for ddl in (
            "ALTER TABLE research_artifacts ADD COLUMN report_json TEXT",
            "ALTER TABLE research_artifacts ADD COLUMN html TEXT",
            "ALTER TABLE research_artifacts ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE research_artifacts ADD COLUMN depth TEXT NOT NULL DEFAULT 'standard'",
            "ALTER TABLE research_artifacts ADD COLUMN updated_at TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            UPDATE research_artifacts
            SET updated_at = created_at
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )


def _row_to_memory(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "type": row["memory_type"],
        "content": row["content"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _normalize_memory_content(text: str) -> str:
    """Conservative normalization for dedup: lowercase, collapse whitespace, trim
    surrounding punctuation. Keeps all tokens so distinct facts stay distinct."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized.strip(string.punctuation + " ")


def create_memory(
    *,
    memory_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"memory_type must be one of: {', '.join(sorted(MEMORY_TYPES))}")
    text = content.strip()
    if not text:
        raise ValueError("content is required")

    init_ai_tables(db_path)
    now = _now()
    meta = metadata or {}
    norm = _normalize_memory_content(text)
    with _connect(db_path) as conn:
        # Dedup-on-write: if a same-type memory already holds (a normalized form of)
        # this content, refresh it instead of inserting a near-identical duplicate.
        for row in conn.execute(
            "SELECT * FROM memories WHERE memory_type = ?", (memory_type,)
        ).fetchall():
            if _normalize_memory_content(row["content"]) == norm:
                conn.execute(
                    "UPDATE memories SET updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                refreshed = conn.execute(
                    "SELECT * FROM memories WHERE id = ?", (row["id"],)
                ).fetchone()
                conn.commit()
                return _row_to_memory(refreshed)
        cursor = conn.execute(
            """
            INSERT INTO memories (memory_type, content, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (memory_type, text, json.dumps(meta), now, now),
        )
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
    return _row_to_memory(row)


def list_memories(
    *,
    memory_type: str | None = None,
    limit: int = 50,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    init_ai_tables(db_path)
    limit = max(1, min(limit, 200))
    with _connect(db_path) as conn:
        if memory_type:
            if memory_type not in MEMORY_TYPES:
                raise ValueError(
                    f"memory_type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
                )
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE memory_type = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (memory_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM memories
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_memory(r) for r in rows]


def search_memories(
    query: str,
    *,
    memory_type: str | None = None,
    limit: int = 10,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Simple keyword search for local memory store."""
    init_ai_tables(db_path)
    q = query.strip().lower()
    if not q:
        return list_memories(memory_type=memory_type, limit=limit, db_path=db_path)

    with _connect(db_path) as conn:
        if memory_type:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE memory_type = ? AND LOWER(content) LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (memory_type, f"%{q}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE LOWER(content) LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (f"%{q}%", limit),
            ).fetchall()
    return [_row_to_memory(r) for r in rows]


def get_memory(
    memory_id: int, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
    return _row_to_memory(row) if row else None


def update_memory(
    memory_id: int,
    *,
    content: str | None = None,
    memory_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row:
            return None
        current = _row_to_memory(row)
        new_type = memory_type if memory_type is not None else current["type"]
        if new_type not in MEMORY_TYPES:
            raise ValueError(
                f"memory_type must be one of: {', '.join(sorted(MEMORY_TYPES))}"
            )
        new_content = content.strip() if content is not None else current["content"]
        if not new_content:
            raise ValueError("content is required")
        new_meta = metadata if metadata is not None else current["metadata"]
        now = _now()
        conn.execute(
            """
            UPDATE memories
            SET memory_type = ?, content = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_type, new_content, json.dumps(new_meta), now, memory_id),
        )
        updated = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        conn.commit()
    return _row_to_memory(updated)


def delete_memory(memory_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return cursor.rowcount > 0


def create_research_run(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_ai_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO research_artifacts (
                topic, symbol, markdown, status, created_at,
                report_json, html, sources_json, depth, updated_at
            ) VALUES (?, ?, '', 'running', ?, NULL, NULL, '[]', ?, ?)
            """,
            (topic.strip(), (symbol or "").strip() or None, now, depth, now),
        )
        row = conn.execute(
            "SELECT * FROM research_artifacts WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
    return _row_to_research(row)


def save_research_artifact(
    *,
    topic: str,
    markdown: str,
    symbol: str | None = None,
    status: str = "completed",
    report_json: dict[str, Any] | None = None,
    html: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    depth: str = "standard",
    artifact_id: int | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_ai_tables(db_path)
    now = _now()
    payload = (
        json.dumps(report_json, ensure_ascii=False)
        if report_json is not None
        else None
    )
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    with _connect(db_path) as conn:
        if artifact_id is not None:
            conn.execute(
                """
                UPDATE research_artifacts
                SET topic = ?, symbol = ?, markdown = ?, status = ?,
                    report_json = ?, html = ?, sources_json = ?, depth = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    topic.strip(),
                    (symbol or "").strip() or None,
                    markdown,
                    status,
                    payload,
                    html,
                    sources_json,
                    depth,
                    now,
                    artifact_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM research_artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        else:
            cursor = conn.execute(
                """
                INSERT INTO research_artifacts (
                    topic, symbol, markdown, status, created_at,
                    report_json, html, sources_json, depth, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic.strip(),
                    (symbol or "").strip() or None,
                    markdown,
                    status,
                    now,
                    payload,
                    html,
                    sources_json,
                    depth,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM research_artifacts WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        conn.commit()
    return _row_to_research(row)


def update_research_status(
    artifact_id: int,
    *,
    status: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE research_artifacts
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, _now(), artifact_id),
        )
        conn.commit()


def get_research_artifact(
    artifact_id: int, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM research_artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    return _row_to_research(row) if row else None


def delete_research_artifact(
    artifact_id: int, db_path: Path = DEFAULT_DB_PATH
) -> bool:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM research_artifacts WHERE id = ?", (artifact_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


def list_research_artifacts(
    *, limit: int = 20, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM research_artifacts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [_row_to_research(r) for r in rows]


def _row_to_research(row: sqlite3.Row) -> dict[str, Any]:
    report_raw = row["report_json"] if "report_json" in row.keys() else None
    html = row["html"] if "html" in row.keys() else None
    sources_raw = row["sources_json"] if "sources_json" in row.keys() else "[]"
    depth = row["depth"] if "depth" in row.keys() else "standard"
    updated = row["updated_at"] if "updated_at" in row.keys() else row["created_at"]
    report = json.loads(report_raw) if report_raw else None
    return {
        "id": int(row["id"]),
        "topic": row["topic"],
        "symbol": row["symbol"],
        "markdown": row["markdown"],
        "status": row["status"],
        "depth": depth,
        "report": report,
        "html": html,
        "sources": json.loads(sources_raw or "[]"),
        "created_at": row["created_at"],
        "updated_at": updated,
    }


def save_reflection_run(
    *,
    summary: str,
    lessons: list[dict[str, Any]],
    status: str = "completed",
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_ai_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO reflection_runs (summary, lessons_json, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (summary, json.dumps(lessons), status, now),
        )
        row = conn.execute(
            "SELECT * FROM reflection_runs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
    return _row_to_reflection(row)


def list_reflection_runs(
    *, limit: int = 20, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reflection_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [_row_to_reflection(r) for r in rows]


def _row_to_reflection(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "summary": row["summary"],
        "lessons": json.loads(row["lessons_json"] or "[]"),
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _row_to_rule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "conditions": json.loads(row["conditions_json"]),
        "action": json.loads(row["action_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_rule(
    *,
    rule_id: str,
    name: str,
    conditions: dict[str, Any],
    action: dict[str, Any],
    enabled: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_ai_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO rules (id, name, enabled, conditions_json, action_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_id,
                name.strip(),
                1 if enabled else 0,
                json.dumps(conditions),
                json.dumps(action),
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        conn.commit()
    return _row_to_rule(row)


def update_rule(
    rule_id: str,
    *,
    name: str | None = None,
    conditions: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    enabled: bool | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        if not row:
            return None
        current = _row_to_rule(row)
        updated = {
            "name": name if name is not None else current["name"],
            "conditions": conditions if conditions is not None else current["conditions"],
            "action": action if action is not None else current["action"],
            "enabled": enabled if enabled is not None else current["enabled"],
            "updated_at": _now(),
        }
        conn.execute(
            """
            UPDATE rules
            SET name = ?, enabled = ?, conditions_json = ?, action_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updated["name"],
                1 if updated["enabled"] else 0,
                json.dumps(updated["conditions"]),
                json.dumps(updated["action"]),
                updated["updated_at"],
                rule_id,
            ),
        )
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        conn.commit()
    return _row_to_rule(row)


def delete_rule(rule_id: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_rule(rule_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    return _row_to_rule(row) if row else None


def list_rules(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM rules ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_rule(r) for r in rows]


def record_rule_event(
    *,
    rule_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    init_ai_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO rule_events (rule_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (rule_id, event_type, json.dumps(payload or {}), now),
        )
        row = conn.execute(
            "SELECT * FROM rule_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        conn.commit()
    return {
        "id": int(row["id"]),
        "rule_id": row["rule_id"],
        "event_type": row["event_type"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": row["created_at"],
    }


def get_last_rule_event(
    rule_id: str, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM rule_events
            WHERE rule_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (rule_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "rule_id": row["rule_id"],
        "event_type": row["event_type"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": row["created_at"],
    }


def list_rule_events(
    *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_ai_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM rule_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "rule_id": r["rule_id"],
            "event_type": r["event_type"],
            "payload": json.loads(r["payload_json"] or "{}"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
