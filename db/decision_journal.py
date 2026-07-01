"""Decision Journal — one immutable-per-stage row per proposal (the agent's track record).

The substrate that lets the agent reference and learn from its own paper calls. Written in
three write-once stages so the reasoning is recorded BEFORE the outcome is known (the
non-fabrication guarantee): CONTEXT at propose, DECISION at decide, OUTCOME when the trade
resolves. Outcome is computed entirely inside the paper world from entry/exit fill prices —
we do NOT reconcile against the separate Schwab/ledger P&L.

Money/qty/prices are TEXT (Decimal), matching ``order_executions`` + the CLAUDE.md money rule.
See ``References/DECISION_JOURNAL_DESIGN.md``. Every write from the execution/approval hot
path is wrapped fail-soft at the call site — a journal write must NEVER break a trade.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.base import now_iso as _now
from db.schema_helpers import _add_column_if_missing

logger = logging.getLogger(__name__)

# Decisions that terminate without ever trading — recorded with outcome='never_filled'.
_NEVER_FILLED_DECISIONS = frozenset({"rejected", "expired"})


def init_decision_journal_tables(db_path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_journal (
                id TEXT PRIMARY KEY,
                proposal_id TEXT UNIQUE NOT NULL,

                -- CONTEXT (write-once at propose; immutable)
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty TEXT NOT NULL,
                order_type TEXT NOT NULL,
                limit_price TEXT,
                source TEXT,
                strategy_id TEXT,
                rule_id TEXT,
                entry_ref_price TEXT,
                thesis TEXT,
                score_json TEXT,
                plan_json TEXT,
                expires_at TEXT,
                asset_class TEXT,
                multiplier TEXT,

                -- DECISION (write-once at decide)
                decision TEXT,
                decided_at TEXT,
                decided_by TEXT,

                -- OUTCOME (filled as the trade resolves)
                entry_execution_id TEXT,
                entry_fill_price TEXT,
                qty_filled TEXT,
                exit_execution_id TEXT,
                exit_fill_price TEXT,
                realized_pnl TEXT,
                r_multiple REAL,
                outcome TEXT,
                holding_days REAL,
                close_reason TEXT,
                resolved_at TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_dj_symbol ON decision_journal(symbol);
            CREATE INDEX IF NOT EXISTS idx_dj_strategy ON decision_journal(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_dj_outcome ON decision_journal(outcome);
            CREATE INDEX IF NOT EXISTS idx_dj_decision ON decision_journal(decision);
            CREATE INDEX IF NOT EXISTS idx_dj_created ON decision_journal(created_at DESC);
            """
        )
        # Idempotent migration for dbs created before the options-multiplier columns.
        _add_column_if_missing(conn, "decision_journal", "asset_class", "TEXT")
        _add_column_if_missing(conn, "decision_journal", "multiplier", "TEXT")
        conn.commit()


def _row_to_journal(row: Any) -> dict[str, Any]:
    out = dict(row)
    for raw_key, parsed_key in (("score_json", "score"), ("plan_json", "plan")):
        raw = out.get(raw_key)
        if raw:
            try:
                out[parsed_key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                out[parsed_key] = None
        else:
            out[parsed_key] = None
    return out


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_ts(iso_ts: str | None):
    if not iso_ts:
        return None
    from datetime import UTC, datetime

    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _holding_days(entry_time: str | None, close_time: str | None) -> float | None:
    e = _parse_ts(entry_time)
    c = _parse_ts(close_time)
    if e is None or c is None:
        return None
    return max(0.0, (c - e).total_seconds() / 86400.0)


# --- CONTEXT (write-once at propose) -----------------------------------------------------


def record_decision(
    *,
    proposal_id: str,
    symbol: str,
    side: str,
    qty: str,
    order_type: str,
    limit_price: str | None = None,
    source: str | None = None,
    strategy_id: str | None = None,
    rule_id: str | None = None,
    entry_ref_price: str | None = None,
    thesis: str | None = None,
    score_json: str | None = None,
    plan_json: str | None = None,
    expires_at: str | None = None,
    asset_class: str | None = None,
    multiplier: str | None = None,
    created_at: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Insert the immutable CONTEXT snapshot. Idempotent on proposal_id (no-op if present).

    ``multiplier`` is the contract multiplier (100 for us_option, 1 for equity) — it makes
    the realized P&L options-correct at resolve time. Defaults to "1" when unknown."""
    init_decision_journal_tables(db_path)
    now = created_at or _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO decision_journal (
                id, proposal_id, created_at, symbol, side, qty, order_type, limit_price,
                source, strategy_id, rule_id, entry_ref_price, thesis, score_json,
                plan_json, expires_at, asset_class, multiplier, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proposal_id) DO NOTHING
            """,
            (
                f"dj_{uuid.uuid4().hex[:12]}",
                proposal_id,
                now,
                str(symbol).strip().upper(),
                str(side).strip().lower(),
                str(qty),
                str(order_type),
                limit_price,
                source,
                strategy_id,
                rule_id,
                entry_ref_price,
                thesis,
                score_json,
                plan_json,
                expires_at,
                asset_class,
                str(multiplier) if multiplier is not None else "1",
                now,
            ),
        )
        conn.commit()


# --- DECISION (write-once at decide) -----------------------------------------------------


def set_decision(
    proposal_id: str,
    *,
    decision: str,
    decided_at: str | None = None,
    decided_by: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Stamp the decide-stage columns. Rejected/expired also set outcome='never_filled'."""
    init_decision_journal_tables(db_path)
    now = _now()
    fields = ["decision = ?", "decided_at = ?", "decided_by = ?", "updated_at = ?"]
    values: list[Any] = [decision, decided_at or now, decided_by, now]
    if decision in _NEVER_FILLED_DECISIONS:
        fields.append("outcome = ?")
        values.append("never_filled")
    values.append(proposal_id)
    with _connect(db_path) as conn:
        conn.execute(
            f"UPDATE decision_journal SET {', '.join(fields)} WHERE proposal_id = ?",
            values,
        )
        conn.commit()


# --- OUTCOME (entry fill) ----------------------------------------------------------------


def set_entry_fill(
    proposal_id: str,
    *,
    entry_execution_id: str | None,
    entry_fill_price: str | None,
    qty_filled: str | None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Link the entry execution + fill price when the BUY fills. Marks outcome='open'
    (only while unresolved — never clobbers a terminal outcome)."""
    init_decision_journal_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE decision_journal
            SET entry_execution_id = ?, entry_fill_price = ?, qty_filled = ?,
                outcome = COALESCE(outcome, 'open'), updated_at = ?
            WHERE proposal_id = ?
            """,
            (entry_execution_id, entry_fill_price, qty_filled, now, proposal_id),
        )
        conn.commit()


# --- OUTCOME (final resolution) ----------------------------------------------------------


def resolve_outcome(
    proposal_id: str,
    *,
    exit_execution_id: str | None = None,
    exit_fill_price: str | None = None,
    close_reason: str | None = None,
    entry_time: str | None = None,
    close_time: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    """Compute + persist the final OUTCOME from entry/exit fill prices.

    realized_pnl = (exit − entry) × qty × multiplier for a long (sign-correct for a short
                   entry). ``multiplier`` is the contract multiplier (100 for us_option, 1
                   for equity) so options P&L is not recorded 100× too small.
    r_multiple   = signed (exit − basis)/(basis − stop), stop derived from the plan's
                   ``stop_pct`` off ``entry_ref_price`` (the R basis); null if no stop.
                   Multiplier-independent — it's a per-unit ratio, so it cancels out.
    outcome      = win (pnl>0) / loss (pnl<0) / scratch (pnl==0).
    holding_days = calendar days from entry_time → close_time.

    With NO exit price sourceable (a reconcile-closure — the position vanished with no agent
    sell), outcome='closed_incomplete' and P&L/R stay null. We never fabricate a price."""
    init_decision_journal_tables(db_path)
    row = get_by_proposal(proposal_id, db_path=db_path)
    if row is None:
        return

    side = str(row.get("side") or "").strip().lower()
    entry_ref = _to_decimal(row.get("entry_ref_price"))
    entry_fill = _to_decimal(row.get("entry_fill_price"))
    qty = _to_decimal(row.get("qty_filled")) or _to_decimal(row.get("qty"))
    exitp = _to_decimal(exit_fill_price)
    multiplier = _to_decimal(row.get("multiplier")) or Decimal(1)
    plan = row.get("plan") or {}

    realized_pnl: str | None = None
    r_multiple: float | None = None
    holding = _holding_days(entry_time, close_time)

    # Prefer the actual entry fill for realized P&L; fall back to the reference price.
    entry_for_pnl = entry_fill or entry_ref

    if exitp is None or entry_for_pnl is None or qty is None:
        outcome = "closed_incomplete"
    else:
        if side == "sell":  # short entry (not used today — sells only reduce longs)
            pnl = (entry_for_pnl - exitp) * qty * multiplier
        else:
            pnl = (exitp - entry_for_pnl) * qty * multiplier
        realized_pnl = str(pnl)
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"

        # R-multiple: only for longs with a plan stop and a reference basis.
        basis = entry_ref or entry_fill
        stop_pct = _to_decimal(plan.get("stop_pct")) if isinstance(plan, dict) else None
        if side != "sell" and basis is not None and stop_pct is not None:
            stop_price = basis * (Decimal(1) + stop_pct / Decimal(100))
            risk = basis - stop_price
            if risk != 0:
                r_multiple = float((exitp - basis) / risk)

    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE decision_journal
            SET exit_execution_id = ?, exit_fill_price = ?, realized_pnl = ?,
                r_multiple = ?, outcome = ?, holding_days = ?, close_reason = ?,
                resolved_at = ?, updated_at = ?
            WHERE proposal_id = ?
            """,
            (
                exit_execution_id,
                exit_fill_price,
                realized_pnl,
                r_multiple,
                outcome,
                holding,
                close_reason,
                now,
                now,
                proposal_id,
            ),
        )
        conn.commit()


# --- Queries -----------------------------------------------------------------------------


def get_by_proposal(
    proposal_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    init_decision_journal_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM decision_journal WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
    return _row_to_journal(row) if row else None


def list_recent(
    *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_decision_journal_tables(db_path)
    lim = max(1, min(limit, 500))
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM decision_journal ORDER BY created_at DESC LIMIT ?", (lim,)
        ).fetchall()
    return [_row_to_journal(r) for r in rows]


def by_symbol(
    symbol: str, *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_decision_journal_tables(db_path)
    lim = max(1, min(limit, 500))
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM decision_journal WHERE symbol = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (symbol.strip().upper(), lim),
        ).fetchall()
    return [_row_to_journal(r) for r in rows]


def by_strategy(
    strategy_id: str, *, limit: int = 50, db_path: Path = DEFAULT_DB_PATH
) -> list[dict[str, Any]]:
    init_decision_journal_tables(db_path)
    lim = max(1, min(limit, 500))
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM decision_journal WHERE strategy_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (strategy_id, lim),
        ).fetchall()
    return [_row_to_journal(r) for r in rows]


def stats_by_strategy(
    strategy_id: str, *, db_path: Path = DEFAULT_DB_PATH
) -> dict[str, Any]:
    """Aggregate realized outcomes for a strategy (wins/losses, avg R, net P&L).

    Descriptive only — no fabricated calibration. Callers decide when the sample is
    large enough to trust (the J3 review layer applies the CI/insufficient-data bar)."""
    init_decision_journal_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT outcome, realized_pnl, r_multiple FROM decision_journal "
            "WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchall()

    counts: dict[str, int] = {}
    pnl_total = Decimal(0)
    pnl_n = 0
    r_total = 0.0
    r_n = 0
    for r in rows:
        outcome = r["outcome"] or "unresolved"
        counts[outcome] = counts.get(outcome, 0) + 1
        pnl = _to_decimal(r["realized_pnl"])
        if pnl is not None:
            pnl_total += pnl
            pnl_n += 1
        if r["r_multiple"] is not None:
            r_total += float(r["r_multiple"])
            r_n += 1

    wins = counts.get("win", 0)
    losses = counts.get("loss", 0)
    decided = wins + losses
    return {
        "strategy_id": strategy_id,
        "n": len(rows),
        "counts": counts,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / decided) if decided else None,
        "net_realized_pnl": str(pnl_total) if pnl_n else None,
        "avg_r_multiple": (r_total / r_n) if r_n else None,
    }
