"""The spread probe — the refusal rule, the vol-point conversion, and the cohort order.

This measurement decides whether a measured VRP edge is reachable at all, so the ways it
could quietly lie get tests: a spread converted with the wrong vega scale is off by 100x,
and a cohort built by filtering in the wrong order silently probes the wrong names (the
latter is not hypothetical — it came back empty on the first dry run).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from tools.option_spread_probe import (
    CHEAP_MAX,
    EDGE_VOL_PTS,
    ERODED_MAX,
    MIN_COHORT,
    _measure,
    _pick_atm,
    band_cohort,
    summarise,
)


def _occ(root: str, exp: date, kind: str, strike: float) -> str:
    return f"{root}{exp:%y%m%d}{'C' if kind == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def _snap(bid: float, ask: float, vega: float | None = 0.30) -> dict:
    s: dict = {"latestQuote": {"bp": bid, "ap": ask, "t": "2026-07-24T19:59:59Z"}}
    if vega is not None:
        s["greeks"] = {"vega": vega}
    return s


class _C:
    def __init__(self, strike: float, expiration: date) -> None:
        self.strike, self.expiration = strike, expiration


def test_measure_expresses_the_spread_in_vol_points_on_a_per_one_vol_vega() -> None:
    """Vendor vega is per 1% of vol; a 100x scale error here would flip every verdict."""
    m = _measure(_C(100.0, date(2026, 8, 21)), _snap(4.80, 5.20, vega=0.20), 30, 100.0)
    # half spread 0.20 in premium; vega 0.20 per 1% => 20.0 per 1.00 of vol => 0.01 vol pts
    assert m is not None
    assert abs(m["vega_per_vol"] - 20.0) < 1e-9
    assert abs(m["half_spread_vol_pts"] - 0.01) < 1e-9
    assert abs(m["rel_spread"] - 0.08) < 1e-9


def test_measure_refuses_rather_than_imputes() -> None:
    c, spot = _C(100.0, date(2026, 8, 21)), 100.0
    assert _measure(c, _snap(4.80, 5.20, vega=None), 30, spot) is None   # no greeks
    assert _measure(c, _snap(4.80, 5.20, vega=0.0), 30, spot) is None    # zero vega
    assert _measure(c, _snap(0.0, 5.20), 30, spot) is None               # no bid
    assert _measure(c, _snap(5.20, 4.80), 30, spot) is None              # crossed
    assert _measure(c, {}, 30, spot) is None                             # no quote at all


def test_pick_atm_takes_the_nearest_30dte_expiry_and_the_nearest_strike() -> None:
    today = date(2026, 7, 24)
    near, target, far = today + timedelta(days=20), today + timedelta(days=31), today + timedelta(days=44)
    snaps = {}
    for exp in (near, target, far):
        for strike in (90.0, 100.0, 115.0):
            for kind in ("call", "put"):
                snaps[_occ("X", exp, kind, strike)] = _snap(1.0, 1.2)
    picked = _pick_atm(snaps, spot=101.0, today=today)
    assert set(picked) == {"call", "put"}
    for kind in ("call", "put"):
        contract, _snapshot, dte = picked[kind]
        assert contract.expiration == target      # 31 DTE beats 20 and 44
        assert contract.strike == 100.0           # nearest 101.0
        assert dte == 31


def test_pick_atm_drops_a_chain_that_never_reaches_the_window() -> None:
    today = date(2026, 7, 24)
    soon = today + timedelta(days=3)
    snaps = {_occ("X", soon, "call", 100.0): _snap(1.0, 1.2)}
    assert _pick_atm(snaps, spot=100.0, today=today) == {}


def test_band_cohort_applies_the_ceiling_before_the_decile(tmp_path: Path, monkeypatch) -> None:
    """The other order is empty in production — the true top decile sits above the ceiling."""
    import tools.option_spread_probe as m

    db = tmp_path / "corpus.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE volatility_history (date TEXT, act_symbol TEXT, iv_current TEXT)")
    # 90 quiet names under the ceiling, 20 very hot ones above it. A decile taken first
    # would select only the hot names and leave nothing under 0.60.
    def tag(prefix: str, i: int) -> str:  # letters only — is_optionable_symbol rejects digits
        return f"{prefix}{chr(65 + i // 26)}{chr(65 + i % 26)}"

    rows = [("2026-07-24", tag("L", i), f"{0.10 + i * 0.004:.4f}") for i in range(90)]
    rows += [("2026-07-24", tag("H", i), f"{0.90 + i * 0.01:.4f}") for i in range(20)]
    conn.executemany("INSERT INTO volatility_history VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(m, "CORPUS", db)

    cohort, meta = band_cohort(None)
    assert meta["under_ceiling"] == 90
    assert cohort, "ceiling-then-decile must not come back empty"
    assert all(s.startswith("L") for s in cohort)
    assert meta["top_decile_cutoff"] < m.IV_CEILING
    assert meta["mean_iv_in_band"] < m.IV_CEILING


def test_summarise_refuses_a_thin_cohort() -> None:
    rows = [{"half_spread_vol_pts": 0.01, "legs": {}} for _ in range(MIN_COHORT - 1)]
    s = summarise(rows, "thin")
    assert s["status"] == "UNMEASURED" and "median_half_spread_vol_pts" not in s


def _cohort(value: float) -> list[dict]:
    return [{"half_spread_vol_pts": value,
             "legs": {"call": {"rel_spread": 0.1}}} for _ in range(MIN_COHORT)]


def test_verdicts_follow_the_pre_registered_bands() -> None:
    assert summarise(_cohort(CHEAP_MAX - 0.001), "a")["verdict"] == "CHEAP"
    assert summarise(_cohort(ERODED_MAX - 0.001), "b")["verdict"] == "ERODED"
    assert summarise(_cohort(ERODED_MAX + 0.001), "c")["verdict"] == "PROHIBITIVE"
    # A round trip is two crossings; half the edge one-way is the break-even.
    assert summarise(_cohort(EDGE_VOL_PTS / 2 + 0.001), "d")["round_trip_exceeds_edge"] is True
    assert summarise(_cohort(EDGE_VOL_PTS / 2 - 0.001), "e")["round_trip_exceeds_edge"] is False
