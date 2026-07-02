"""Decision Journal J2 — the read/surface layer.

Two separate stores unified only at the read layer: the AI side (decision_journal) and the
user side (my_trades / real broker fills). These tests defend the shapes the copilot,
the endpoint, and the widget all consume, plus the honesty guarantees (net-cash truth,
FIFO flagged, "insufficient data" on thin buckets, empty AI side until proposals resolve).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai.chat.context import (
    format_journal_context_block,
    turn_journal_context,
)
from ai.perception.resources import list_resources, query
from db import base as db_base
from db.base import connect, now_iso
from db.decision_journal import (
    init_decision_journal_tables,
    record_decision,
    resolve_outcome,
    set_decision,
    set_entry_fill,
)
from db.trades import init_trade_tables

# --- Seeding helpers ---------------------------------------------------------------------


def _seed_decision(
    db: Path,
    *,
    symbol: str,
    strategy_id: str,
    win_prob: float,
    outcome: str | None,
) -> str:
    """A journal row through its real CONTEXT→DECISION→OUTCOME write path."""
    proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    record_decision(
        proposal_id=proposal_id,
        symbol=symbol,
        side="buy",
        qty="10",
        order_type="limit",
        source=f"strategy:{strategy_id}",
        strategy_id=strategy_id,
        entry_ref_price="100",
        thesis=f"{symbol} dip into rising 50DMA — reclaim setup",
        score_json=f'{{"win_prob": {win_prob}, "ev_pct": 0.8}}',
        plan_json='{"stop_pct": -3.0}',
        db_path=db,
    )
    set_decision(proposal_id, decision="approved", decided_by="phone", db_path=db)
    if outcome in ("win", "loss", "scratch"):
        set_entry_fill(
            proposal_id,
            entry_execution_id="ex_entry",
            entry_fill_price="100",
            qty_filled="10",
            db_path=db,
        )
        exit_price = "110" if outcome == "win" else "95" if outcome == "loss" else "100"
        resolve_outcome(
            proposal_id,
            exit_execution_id="ex_exit",
            exit_fill_price=exit_price,
            close_reason="target" if outcome == "win" else "stop",
            entry_time="2026-06-01T14:30:00+00:00",
            close_time="2026-06-03T14:30:00+00:00",
            db_path=db,
        )
    return proposal_id


def _seed_trade_event(db: Path, **cols: object) -> None:
    """Insert a raw trade_event directly (bypasses the import validation harness)."""
    init_trade_tables(db)
    row = {
        "id": f"evt_{uuid.uuid4().hex[:10]}",
        "account_id": "acc1",
        "source": "test",
        "event_type": "trade",
        "fees": "0",
        "description": "",
        "external_key": f"ext_{uuid.uuid4().hex}",
        "broker_txn_id": None,
        "raw_json": "{}",
        "imported_at": now_iso(),
    }
    row.update(cols)
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO trade_events (
                id, account_id, source, event_type, trade_date, symbol, side,
                quantity, price, fees, amount, description, external_key,
                broker_txn_id, raw_json, imported_at
            ) VALUES (
                :id, :account_id, :source, :event_type, :trade_date, :symbol, :side,
                :quantity, :price, :fees, :amount, :description, :external_key,
                :broker_txn_id, :raw_json, :imported_at
            )
            """,
            row,
        )
        conn.commit()


@pytest.fixture
def journal_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "journal.db"
    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    init_decision_journal_tables(db)
    init_trade_tables(db)
    return db


# --- Perception resources (AI side) ------------------------------------------------------


def test_catalog_includes_both_resources() -> None:
    catalog = list_resources()
    assert "decision_journal" in catalog
    assert "my_trades" in catalog
    # summarize is an internal wiring detail — the catalog exposes description + filters only.
    assert set(catalog["decision_journal"]) == {"description", "filters"}


@pytest.mark.asyncio
async def test_decision_journal_resource_shape(journal_db: Path) -> None:
    _seed_decision(
        journal_db, symbol="NVDA", strategy_id="dip_buy", win_prob=0.62, outcome="win"
    )
    _seed_decision(
        journal_db, symbol="NVDA", strategy_id="dip_buy", win_prob=0.55, outcome="loss"
    )
    result = await query("decision_journal", {"symbol": "NVDA"}, db_path=journal_db)
    assert result["count"] == 2
    row = result["rows"][0]
    for key in ("symbol", "decision", "outcome", "win_prob", "r_multiple", "thesis"):
        assert key in row
    # The summary block rides alongside the rows (opt-in summarize wiring).
    summary = result["summary"]
    assert summary["overall"]["wins"] == 1
    assert summary["overall"]["losses"] == 1
    # Two decided < 5 → honest "insufficient data" flag, never a fabricated win rate.
    assert summary["overall"]["note"] == "insufficient data"


@pytest.mark.asyncio
async def test_decision_journal_empty_until_proposals(journal_db: Path) -> None:
    result = await query("decision_journal", db_path=journal_db)
    assert result["count"] == 0
    assert result["rows"] == []
    assert result["summary"]["overall"]["n"] == 0


@pytest.mark.asyncio
async def test_decision_journal_outcome_filter(journal_db: Path) -> None:
    _seed_decision(
        journal_db, symbol="AAPL", strategy_id="dip_buy", win_prob=0.6, outcome="win"
    )
    _seed_decision(
        journal_db, symbol="MSFT", strategy_id="dip_buy", win_prob=0.6, outcome="loss"
    )
    result = await query("decision_journal", {"outcome": "loss"}, db_path=journal_db)
    assert result["count"] == 1
    assert result["rows"][0]["symbol"] == "MSFT"


# --- Perception resources (Me side) ------------------------------------------------------


@pytest.mark.asyncio
async def test_my_trades_resource_shape(journal_db: Path) -> None:
    # A closed AAPL round-trip: buy 10 @100 (-1000), sell 10 @110 (+1100) → +100 net cash.
    _seed_trade_event(
        journal_db,
        trade_date="2026-06-01",
        symbol="AAPL",
        side="buy",
        quantity="10",
        price="100",
        amount="-1000",
    )
    _seed_trade_event(
        journal_db,
        trade_date="2026-06-05",
        symbol="AAPL",
        side="sell",
        quantity="10",
        price="110",
        amount="1100",
    )
    result = await query("my_trades", {"symbol": "AAPL"}, db_path=journal_db)
    assert result["count"] == 1
    trade = result["rows"][0]
    assert trade["symbol"] == "AAPL"
    assert trade["outcome"] == "win"
    assert trade["hold_days"] == 4
    assert trade["pnl_basis"] == "fifo_approximate"
    # Summary leads with net-cash realized (the source of truth) + FIFO honesty note.
    summary = result["summary"]
    assert summary["net_cash_realized"]["realized_closed"] == "100"
    assert summary["behavioral"]["trade_count"] == 2
    assert "FIFO-approximate" in summary["honesty"]


# --- Endpoint routes (mounted on a fresh app; api.main untouched) ------------------------


@pytest.fixture
def journal_client(journal_db: Path) -> TestClient:
    from api.journal_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_endpoint_decisions_and_stats(
    journal_db: Path, journal_client: TestClient
) -> None:
    _seed_decision(
        journal_db, symbol="NVDA", strategy_id="dip_buy", win_prob=0.62, outcome="win"
    )
    resp = journal_client.get("/api/journal/decisions", params={"symbol": "NVDA"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["decisions"][0]["symbol"] == "NVDA"
    assert "stats" in body

    stats = journal_client.get("/api/journal/stats", params={"strategy": "dip_buy"})
    assert stats.status_code == 200
    assert stats.json()["overall"]["strategy_id"] == "dip_buy"


def test_endpoint_bad_limit_400(journal_client: TestClient) -> None:
    resp = journal_client.get("/api/journal/decisions", params={"limit": 9999})
    assert resp.status_code == 400


def test_endpoint_trades_and_summary(
    journal_db: Path, journal_client: TestClient
) -> None:
    _seed_trade_event(
        journal_db,
        trade_date="2026-06-01",
        symbol="AAPL",
        side="buy",
        quantity="5",
        price="100",
        amount="-500",
    )
    _seed_trade_event(
        journal_db,
        trade_date="2026-06-02",
        symbol="AAPL",
        side="sell",
        quantity="5",
        price="90",
        amount="450",
    )
    trades = journal_client.get("/api/journal/trades", params={"symbol": "AAPL"})
    assert trades.status_code == 200
    assert trades.json()["trades"][0]["outcome"] == "loss"

    summary = journal_client.get("/api/journal/trades/summary", params={"symbol": "AAPL"})
    assert summary.status_code == 200
    assert summary.json()["net_cash_realized"]["realized_closed"] == "-50"


# --- Chat injection ----------------------------------------------------------------------


def test_journal_block_empty_when_nothing() -> None:
    assert format_journal_context_block({}) == ""
    assert format_journal_context_block({"decisions": [], "user_profile": None}) == ""


def test_journal_block_is_bounded_and_labeled() -> None:
    journal = {
        "scope": "NVDA",
        "ai_stats": {"n": 4, "wins": 3, "losses": 1, "avg_r": 1.2},
        "decisions": [
            {
                "symbol": "NVDA",
                "decision": "approved",
                "outcome": "win",
                "r_multiple": 1.4,
                "score": {"win_prob": 0.62},
            }
        ],
        "user_profile": "142 trades, 68% options, top TSLA/NVDA, buy/sell 1.1",
    }
    block = format_journal_context_block(journal)
    assert "agent's own paper calls for NVDA" in block
    assert "user's trading pattern" in block
    # Bounded: header + <=3 decision lines + one profile line.
    assert len(block.strip().splitlines()) <= 6


def test_turn_journal_context_fail_soft(journal_db: Path) -> None:
    # No data seeded — must return a shaped, empty-ish dict, never raise.
    ctx = turn_journal_context("how did my NVDA trades go?")
    assert isinstance(ctx, dict)
    assert ctx.get("ai_stats", {}).get("n", 0) == 0
