"""Persistent store for Fusion Phase C2 dossiers (deterministic claim matrices).

One store, one module — the fusion house pattern (``db/fusion.py`` is the sibling).
A dossier is the deterministic claim matrix an admitted evidence stack gets BEFORE
any LLM sees it: written by ``signals/dossier.build_and_store_dossier`` (zero LLM,
zero network — store reads only), read by the Phase C3 investigator. Upsert on
``episode_id`` so a re-investigation rebuilds the row in place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH, now_iso
from db.base import connect as _connect


def init_idea_dossiers(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idea_dossiers (
                episode_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market_day TEXT NOT NULL,
                created_at TEXT NOT NULL,
                claims_json TEXT NOT NULL,
                summary TEXT NOT NULL,
                claim_count INTEGER NOT NULL,
                contradiction_count INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def put_dossier(dossier: dict[str, Any], *, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Upsert a built dossier (a re-investigation rebuilds the same episode row).

    ``claim_count`` / ``contradiction_count`` are derived from the claims here so
    the stored counts can never drift from the claims payload. A contradiction is
    an unordered pair of reciprocally-linked claims, so the raw link total is
    halved.
    """
    init_idea_dossiers(db_path)
    claims = list(dossier.get("claims") or [])
    claim_count = len(claims)
    contradiction_count = (
        sum(len(claim.get("contradicts") or []) for claim in claims) // 2
    )
    created_at = str(dossier.get("created_at") or now_iso())
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO idea_dossiers(
                episode_id, symbol, market_day, created_at, claims_json,
                summary, claim_count, contradiction_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                symbol = excluded.symbol,
                market_day = excluded.market_day,
                created_at = excluded.created_at,
                claims_json = excluded.claims_json,
                summary = excluded.summary,
                claim_count = excluded.claim_count,
                contradiction_count = excluded.contradiction_count
            """,
            (
                str(dossier["episode_id"]),
                str(dossier.get("symbol") or ""),
                str(dossier.get("market_day") or ""),
                created_at,
                json.dumps(claims, default=str),
                str(dossier.get("summary") or ""),
                int(claim_count),
                int(contradiction_count),
            ),
        )
        conn.commit()


def get_dossier(
    episode_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    """The stored dossier for ``episode_id`` (claims parsed back), or None."""
    init_idea_dossiers(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM idea_dossiers WHERE episode_id = ?",
            (str(episode_id),),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["claims"] = json.loads(out.get("claims_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["claims"] = []
    return out
