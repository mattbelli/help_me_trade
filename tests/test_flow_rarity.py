"""Per-name flow rarity: buckets, Laplace rank, stamp, no corpus on the hot path."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from db import options_flow as of_db
from db.flow_corpus import DTE_BUCKETS, INDEX_ROOTS, MNY_BUCKETS
from db.flow_rarity import (
    MIN_N,
    attach_sentence,
    canon_right,
    clear_cache,
    dte_bucket,
    event_cell,
    laplace_p,
    mny_bucket,
    put_fixture,
    signed_otm_pct,
    stamp_event,
)
from tools.flow_corpus_rollup import _bucket_dte, _bucket_mny


def _sweep(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "of_2026-08-11_TSLA_swp_1",
        "ts": "2026-08-11T18:00:00+00:00",
        "underlying": "TSLA",
        "occ_symbol": "TSLA261120C00350000",
        "right": "call",
        "strike": 350.0,
        "expiry": "2026-11-20",
        "dte": 101,
        "otm_pct": None,
        "event_type": "sweep",
        "premium_usd": 765_000.0,
        "detail": {"underlying_price": 325.0},
    }
    base.update(overrides)
    return base


# --- buckets match the corpus rollup -----------------------------------------


def test_dte_buckets_match_corpus_rollup() -> None:
    for d in range(-2, 400):
        idx = int(_bucket_dte(np.array([d], dtype=np.int32))[0])
        assert dte_bucket(d) == DTE_BUCKETS[idx]


def test_mny_buckets_match_corpus_rollup() -> None:
    strikes = np.array([80.0, 99.0, 100.0, 102.0, 110.0, 120.0, 100.0])
    spots = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, np.nan])
    rights = np.array([0, 0, 0, 0, 0, 0, 0], dtype=np.int8)  # calls
    is_index = np.array([False] * 6 + [True])
    got = _bucket_mny(strikes, spots, rights, is_index)
    for i, (k, s) in enumerate(zip(strikes, spots, strict=True)):
        right = "C"
        und = "SPX" if is_index[i] else "TSLA"
        spot = None if not np.isfinite(s) else float(s)
        label = mny_bucket(strike=float(k), spot=spot, right=right, underlying=und)
        assert label == MNY_BUCKETS[int(got[i])], (i, k, s, label, int(got[i]))


def test_itm_is_its_own_bucket_not_mixed_with_otm() -> None:
    # 12% OTM call
    assert mny_bucket(strike=364.0, spot=325.0, right="C", underlying="TSLA") == "otm10p"
    # 12% ITM call
    assert mny_bucket(strike=286.0, spot=325.0, right="C", underlying="TSLA") == "itm"
    # ATM band straddles
    assert mny_bucket(strike=325.0, spot=325.0, right="C", underlying="TSLA") == "atm"
    # put OTM below spot
    assert mny_bucket(strike=280.0, spot=325.0, right="P", underlying="TSLA") == "otm10p"


def test_no_spot_splits_index_from_equity() -> None:
    assert mny_bucket(strike=5000.0, spot=None, right="C", underlying="SPX") == "no_spot_index"
    assert mny_bucket(strike=100.0, spot=None, right="C", underlying="TSLA") == "no_spot_missing"
    assert "SPX" in INDEX_ROOTS


def test_canon_right_and_signed_otm() -> None:
    assert canon_right("call") == "C"
    assert canon_right("P") == "P"
    assert canon_right(None) is None
    # 350c vs 325 spot = 7.69% OTM
    pct = signed_otm_pct(350.0, 325.0, "C")
    assert pct is not None and 7.0 < pct < 8.0
    # same strike put is ITM
    put = signed_otm_pct(350.0, 325.0, "P")
    assert put is not None and put < 0


def test_event_cell_maps_november_otm_call() -> None:
    cell = event_cell(_sweep())
    assert cell == ("TSLA", "C", "90+", "otm2_10")  # 350/325-1 = 7.7% → otm2_10


def test_event_cell_400_call_is_otm10p() -> None:
    cell = event_cell(_sweep(strike=400.0))
    assert cell == ("TSLA", "C", "90+", "otm10p")


# --- Laplace rank ------------------------------------------------------------


def test_laplace_p_largest_hits_the_floor() -> None:
    peaks = list(range(1000, 10120, 100))  # 100 values, max 11000
    n = 120  # 20 zeros in the denominator
    p = laplace_p(50_000, peaks, n)
    assert p == pytest.approx(1 / 121)
    # a print inside the pack
    p_mid = laplace_p(6000, peaks, n)
    assert 0.3 < p_mid < 0.7
    # tiny print: every stored peak is larger; zeros in n keep p below 1
    p_small = laplace_p(1, peaks, n)
    assert p_small == pytest.approx((len(peaks) + 1) / (n + 1))
    assert p_small > p_mid > p


def test_laplace_p_zeros_in_n_make_a_quiet_cell_rare() -> None:
    # cell printed on 3 of 120 days, all small
    peaks = [10_000, 20_000, 30_000]
    p = laplace_p(765_000, peaks, 120)
    assert p == pytest.approx(1 / 121)


# --- stamp -------------------------------------------------------------------


def test_tesla_nov_otm_ranks_high_same_dollars_0dte_atm_does_not(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    # Nov OTM: quiet cell, peaks well below $765k
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm10p",
        n=120, peaks=[50_000, 80_000, 120_000, 200_000, 400_000], db_path=db,
    )
    # 0-7 ATM: firehose, $765k is ordinary
    put_fixture(
        underlying="TSLA", right="C", dte="0-7", mny="atm",
        n=120,
        peaks=[int(x) for x in np.linspace(200_000, 8_000_000, 80)],
        db_path=db,
    )
    nov = stamp_event(
        _sweep(strike=400.0, dte=101, premium_usd=765_000), db_path=db,
    )
    weekly = stamp_event(
        _sweep(
            id="w",
            strike=325.0,
            dte=3,
            premium_usd=765_000,
            detail={"underlying_price": 325.0},
        ),
        db_path=db,
    )
    assert nov["rarity_p"] < 0.05
    assert weekly["rarity_p"] > 0.4
    assert nov["rarity_n"] == 120
    assert "90+" in nov["rarity_cell"]
    assert "otm10p" in nov["rarity_cell"]
    assert "larger than" in nov["rarity_sentence"]
    assert "session peaks" in nov["rarity_sentence"]
    assert "win" not in nov["rarity_sentence"].lower()


def test_cluster_is_not_stamped(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm2_10",
        n=120, peaks=[1], db_path=db,
    )
    ev = stamp_event(_sweep(event_type="flow_cluster"), db_path=db)
    assert "rarity_p" not in ev


def test_name_below_min_n_stays_unranked(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TINY", right="C", dte="90+", mny="otm10p",
        n=40, peaks=[1_000], db_path=db,
    )
    ev = stamp_event(
        _sweep(underlying="TINY", strike=400.0, detail={"underlying_price": 325.0}),
        db_path=db,
    )
    assert 40 < MIN_N
    assert "rarity_p" not in ev


def test_unknown_name_unranked(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm10p",
        n=120, peaks=[1], db_path=db,
    )
    ev = stamp_event(_sweep(underlying="ZZZZ"), db_path=db)
    assert "rarity_p" not in ev


def test_stamp_fills_otm_pct(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm2_10",
        n=120, peaks=[1], db_path=db,
    )
    ev = stamp_event(_sweep(), db_path=db)
    assert ev["otm_pct"] is not None
    assert ev["otm_pct"] > 0


def test_stamp_never_opens_the_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm2_10",
        n=120, peaks=[1], db_path=db,
    )
    real = sqlite3.connect

    def wrapped(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        uri = str(database)
        assert "flow_corpus" not in uri, uri
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", wrapped)
    clear_cache()
    stamp_event(_sweep(), db_path=db)


def test_insert_event_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm2_10",
        n=120, peaks=[10_000, 20_000, 30_000], db_path=db,
    )
    of_db.init_db(db)
    of_db.insert_event(_sweep(), db_path=db)
    got = of_db.recent_events(db_path=db)[0]
    assert got["rarity_p"] is not None
    assert got["rarity_n"] == 120
    assert got["rarity_sentence"].startswith("larger than")
    assert got["otm_pct"] is not None


def test_reinsert_without_lookup_keeps_stamp(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    put_fixture(
        underlying="TSLA", right="C", dte="90+", mny="otm2_10",
        n=120, peaks=[10_000], db_path=db,
    )
    of_db.init_db(db)
    of_db.insert_event(_sweep(), db_path=db)
    first = of_db.recent_events(db_path=db)[0]["rarity_p"]
    # Drop the lookup so the second insert cannot restamp.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM flow_rarity_names")
        conn.execute("DELETE FROM flow_rarity_cells")
        conn.commit()
    clear_cache()
    of_db.insert_event(_sweep(premium_usd=800_000), db_path=db)
    got = of_db.recent_events(db_path=db)[0]
    assert got["rarity_p"] == first
    assert got["premium_usd"] == 800_000


def test_attach_sentence_is_perception_not_a_signal() -> None:
    ev = {
        "underlying": "TSLA",
        "rarity_p": 0.033,
        "rarity_n": 120,
        "rarity_cell": "C/90+/otm10p",
    }
    attach_sentence(ev)
    s = ev["rarity_sentence"].lower()
    assert "larger than" in s
    for banned in ("win", "smart money", "signal", "will move", "buy"):
        assert banned not in s
