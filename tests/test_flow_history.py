"""flow_history perception resource — corpus marginals + rarity rank."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from db.flow_corpus import (
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    connect_ro,
    history_query,
    init_db,
    order_grain_variance,
    record_session,
    upsert_rollup,
)

# The 17GB corpus lives on the main tree; worktrees don't copy gitignored data/.
_MAIN_CORPUS = Path("/home/mbelli/dev/help_me_trade/data/flow_corpus.db")
LIVE_CORPUS = DEFAULT_DB_PATH if DEFAULT_DB_PATH.is_file() else _MAIN_CORPUS


def _row(
    date: str,
    *,
    right: str = "C",
    dte: str = "0-7",
    mny: str = "atm",
    side: str = "buy",
    prints: int = 1,
    orders: int = 1,
    premium: float = 1_000.0,
    premium_sq: float | None = None,
    premium_sq_orders: float | None = None,
    premium_max: float | None = None,
    multileg: int = 0,
) -> tuple:
    sq = premium_sq if premium_sq is not None else premium * premium
    sq_o = premium_sq_orders if premium_sq_orders is not None else premium * premium
    mx = premium_max if premium_max is not None else premium
    return (
        date,
        "NVDA",
        right,
        dte,
        mny,
        side,
        side,
        prints,
        orders,
        10,
        premium,
        sq,
        sq_o,
        mx,
        premium,
        premium,
        multileg,
        "I",
        SCHEMA_VERSION,
    )


def _corpus(tmp_path: Path) -> Path:
    db = tmp_path / "flow_corpus.db"
    init_db(db)
    for d in ("2026-01-02", "2026-01-05", "2026-01-06"):
        record_session(
            d,
            prints=10,
            rows=4,
            underlyings=1,
            unresolved_prints=0,
            no_spot_prints=0,
            ingested_at=f"{d}T21:00:00+00:00",
            db_path=db,
        )
    upsert_rollup(
        [
            _row("2026-01-02", right="C", dte="0-7", mny="atm", premium=1_000),
            _row("2026-01-05", right="P", dte="90+", mny="otm10p", premium=50_000),
            _row(
                "2026-01-06",
                right="P",
                dte="90+",
                mny="otm10p",
                prints=2,
                orders=1,
                premium=200.0,
                premium_sq=20_000.0,
                premium_sq_orders=40_000.0,
                premium_max=200.0,
            ),
        ],
        db_path=db,
    )
    return db


def test_symbol_required_names_flow_perception(tmp_path: Path) -> None:
    db = _corpus(tmp_path)
    row = history_query({}, db_path=db)[0]
    assert "error" in row
    assert "flow_perception" in row["error"]
    assert "full-table" in row["error"] or "full table" in row["error"]


def test_missing_corpus_is_unavailable_not_empty(tmp_path: Path) -> None:
    row = history_query({"symbol": "NVDA"}, db_path=tmp_path / "nope.db")[0]
    assert row["unavailable"] is True
    assert "not 'nothing unusual'" in row["note"] or "not nothing unusual" in row["note"]


def test_default_marginals(tmp_path: Path) -> None:
    db = _corpus(tmp_path)
    row = history_query({"symbol": "nvda"}, db_path=db)[0]
    assert row["symbol"] == "NVDA"
    assert row["corpus_through"] == "2026-01-06"
    assert "stale_days" in row
    assert row["side_is_not_sentiment"] is True
    assert row["premium_usd"] == 51_200.0
    buckets = {d["bucket"]: d["premium_usd"] for d in row["right"]}
    assert buckets["C"] == 1_000.0
    assert buckets["P"] == 50_200.0
    assert [d["date"] for d in row["daily"]] == [
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
    ]
    assert "score" not in row
    assert "probability" not in row
    blob = json.dumps(row).lower()
    assert "bullish" not in blob
    assert "bearish" not in blob
    assert "smart money" not in blob


def test_invalid_group_by_and_dte_return_valid_set(tmp_path: Path) -> None:
    db = _corpus(tmp_path)
    g = history_query({"symbol": "NVDA", "group_by": "flavour"}, db_path=db)[0]
    assert "right" in g["available"]
    d = history_query({"symbol": "NVDA", "dte_bucket": "0DTE"}, db_path=db)[0]
    assert d["available"] == ["0-7", "8-30", "31-90", "90+"]
    listed = history_query(
        {"symbol": "NVDA", "group_by": ["right", "side"]}, db_path=db
    )[0]
    assert "ONE of" in listed["error"]
    # a float multileg (JSON 1.0) must filter, not raise
    ml = history_query({"symbol": "NVDA", "multileg": 1.0}, db_path=db)[0]
    assert "error" not in ml
    assert ml["premium_usd"] == 0.0
    bad = history_query({"symbol": "NVDA", "multileg": "2"}, db_path=db)[0]
    assert bad["available"] == ["0", "1"]


def test_rarity_uses_order_grain_and_cell_max(tmp_path: Path) -> None:
    db = _corpus(tmp_path)
    row = history_query(
        {
            "symbol": "NVDA",
            "premium_usd": 10_000,
            "right": "P",
            "dte_bucket": "90+",
            "mny_bucket": "otm10p",
        },
        db_path=db,
    )[0]
    assert row["rank_grain"] == "orders"
    assert row["rank_method"] == "cell_max_bound"
    assert row["orders_smaller"] == 1
    assert row["reference_n"] == 2
    assert row["order_variance_nonneg"] is True
    assert "score" not in row
    mixed = order_grain_variance(200.0, 20_000.0, 1)
    paired = order_grain_variance(200.0, 40_000.0, 1)
    assert mixed is not None and mixed < 0
    assert paired is not None and paired >= -1e-9


@pytest.mark.asyncio
async def test_query_aliases_and_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai.perception.resources import list_resources, query

    db = _corpus(tmp_path)
    monkeypatch.setattr("db.flow_corpus.DEFAULT_DB_PATH", db)
    assert "flow_history" in list_resources()
    result = await query("flow_corpus", {"symbol": "NVDA"})
    assert result["resource"] == "flow_history"
    assert result["rows"][0]["symbol"] == "NVDA"


@pytest.mark.skipif(not LIVE_CORPUS.is_file(), reason="live flow corpus not on this tree")
def test_live_nvda_default_is_small_and_fast() -> None:
    import time

    history_query({"symbol": "NVDA", "until": "2026-07-31"}, db_path=LIVE_CORPUS)
    t0 = time.perf_counter()
    rows = history_query({"symbol": "NVDA"}, db_path=LIVE_CORPUS)
    elapsed = time.perf_counter() - t0
    blob = json.dumps(rows)
    assert elapsed < 2.0
    assert len(blob) < 20_000
    assert rows[0].get("unavailable") is not True
    assert rows[0]["symbol"] == "NVDA"
    var_ok = rows[0]
    conn = connect_ro(LIVE_CORPUS)
    try:
        since, until = var_ok["since"], var_ok["until"]
        agg = conn.execute(
            "SELECT COALESCE(SUM(premium_usd),0), "
            "COALESCE(SUM(premium_sq_orders),0), COALESCE(SUM(orders),0) "
            "FROM flow_rollup WHERE underlying='NVDA' AND date>=? AND date<=?",
            (since, until),
        ).fetchone()
        v = order_grain_variance(agg[0], agg[1], agg[2])
        assert v is None or v >= -1e-6
    finally:
        conn.close()
