"""Index dealer exposure — the aggregation, the flip solve, and the honesty rails.

Every greek is checked against a finite difference rather than against another
formula, so a sign or scaling slip has nowhere to hide: gamma against a second
difference of the PRICE, vanna and charm against a first difference of the
analytic DELTA. Everything above that is checked on synthetic boards with known
answers: a board built only of calls MUST net positive, a board with no crossing
MUST report no flip rather than name one, and the drill-down rows MUST NOT be able
to overwrite the context they travel with.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from db.gamma_exposure import (
    MIN_OBSERVATIONS,
    history,
    init_gex_table,
    percentile,
    percentiles,
    record,
)
from signals.black76 import black76_price, greeks
from signals.gamma_exposure import (
    CONTRACT_MULTIPLIER,
    Board,
    Leg,
    expiry_rows,
    gamma_flip,
    leg_exposures_usd,
    leg_gamma_usd,
    net_gamma_at,
    profile_rows,
    strike_rows,
    summarize,
)


def _leg(cp: str, strike: float, *, oi: int = 100, dte: int = 30, sigma: float = 0.15,
         forward: float = 7700.0, root: str = "SPXW") -> Leg:
    return Leg(
        root=root, expiry="2026-09-18", cp=cp, strike=strike, dte=dte, oi=oi,
        t_years=dte / 365.0, discount=0.998, forward=forward, sigma=sigma,
    )


def _board(legs: tuple[Leg, ...], spot: float = 7700.0) -> Board:
    return Board(
        legs=legs, spot=spot, quote_as_of="2026-08-11T19:59:59Z",
        oi_as_of="2026-08-11", oi_as_of_age_days=0,
        coverage={"contracts_priced": len(legs), "open_interest_covered_pct": 100.0},
    )


# --- the gamma itself ---------------------------------------------------------
def test_leg_gamma_matches_a_finite_difference_of_the_pricer() -> None:
    """The one number everything else is built on, checked against the pricer.

    Bump spot, reprice with the forward scaled the same way ``leg_gamma_usd`` does,
    and the second difference of the option value must equal the analytic gamma.
    """
    leg = _leg("C", 7700.0, oi=1)
    spot, h = 7700.0, 1.0

    def price(s: float) -> float:
        return black76_price(
            leg.forward * s / spot, leg.strike, leg.t_years, leg.discount, leg.sigma, True
        )

    finite = (price(spot + h) - 2 * price(spot) + price(spot - h)) / (h * h)
    analytic = leg_gamma_usd(leg, spot, spot) / (CONTRACT_MULTIPLIER * spot * spot * 0.01)
    assert abs(analytic / finite - 1) < 1e-4


def test_dollar_gamma_scales_linearly_with_open_interest() -> None:
    one = leg_gamma_usd(_leg("C", 7700.0, oi=1), 7700.0, 7700.0)
    many = leg_gamma_usd(_leg("C", 7700.0, oi=250), 7700.0, 7700.0)
    assert abs(many / one - 250) < 1e-9


def test_call_and_put_at_one_strike_carry_identical_gamma() -> None:
    """Parity's consequence: gamma is right-blind. Only the CONVENTION signs it."""
    call = leg_gamma_usd(_leg("C", 7700.0), 7700.0, 7700.0)
    put = leg_gamma_usd(_leg("P", 7700.0), 7700.0, 7700.0)
    assert abs(call - put) < 1e-9


# --- vanna and charm ----------------------------------------------------------
# Spot-delta cases spanning ITM/ATM/OTM, 1 day to 2 years, dividend above and below
# the rate. Each is (spot, strike, t_years, rate, yield, sigma).
_GREEK_CASES = (
    (7725.0, 7700.0, 30 / 365, 0.043, 0.013, 0.13),
    (7725.0, 7000.0, 30 / 365, 0.043, 0.013, 0.22),
    (7725.0, 8500.0, 180 / 365, 0.043, 0.013, 0.12),
    (7725.0, 7725.0, 1 / 365, 0.043, 0.013, 0.09),
    (100.0, 95.0, 2.0, 0.02, 0.04, 0.45),
)


def _spot_greeks(spot, strike, t, rate, div, sigma, is_call) -> dict[str, float]:
    """Greeks with the forward and discount rebuilt from S, r, q and T.

    Both must be rebuilt, not held fixed, or the finite differences below would be
    testing a different question than the one charm actually answers.
    """
    forward = spot * math.exp((rate - div) * t)
    return greeks(forward, strike, t, math.exp(-rate * t), sigma, is_call, spot=spot)


@pytest.mark.parametrize("case", _GREEK_CASES)
@pytest.mark.parametrize("is_call", [True, False])
def test_vanna_matches_a_finite_difference_of_delta(case, is_call: bool) -> None:
    """Vanna IS d(delta)/d(vol), so difference the delta and the two must agree."""
    spot, strike, t, rate, div, sigma = case
    h = 1e-5
    up = _spot_greeks(spot, strike, t, rate, div, sigma + h, is_call)["delta"]
    down = _spot_greeks(spot, strike, t, rate, div, sigma - h, is_call)["delta"]
    # /100 because the reported vanna is per 1 volatility POINT, not per 1.0 of vol.
    finite = (up - down) / (2 * h) / 100.0
    assert abs(_spot_greeks(spot, strike, t, rate, div, sigma, is_call)["vanna"] / finite - 1) < 1e-5


@pytest.mark.parametrize("case", _GREEK_CASES)
@pytest.mark.parametrize("is_call", [True, False])
def test_charm_matches_a_finite_difference_of_delta(case, is_call: bool) -> None:
    """Charm is delta drift as time PASSES, so the difference is negated: shrinking
    time to expiry is the clock running forward."""
    spot, strike, t, rate, div, sigma = case
    h = 1e-6
    later = _spot_greeks(spot, strike, t + h, rate, div, sigma, is_call)["delta"]
    sooner = _spot_greeks(spot, strike, t - h, rate, div, sigma, is_call)["delta"]
    finite = -(later - sooner) / (2 * h) / 365.0  # per calendar day
    assert abs(_spot_greeks(spot, strike, t, rate, div, sigma, is_call)["charm"] / finite - 1) < 1e-4


def test_call_and_put_at_one_strike_carry_identical_vanna() -> None:
    """Same parity consequence as gamma — which is exactly why a sign-based
    call/put split of vanna would be meaningless."""
    call = leg_exposures_usd(_leg("C", 7900.0), 7700.0, 7700.0)
    put = leg_exposures_usd(_leg("P", 7900.0), 7700.0, 7700.0)
    assert abs(call.vanna - put.vanna) < 1e-9
    assert abs(call.charm - put.charm) > 1e-6  # charm, unlike vanna, is not


def test_exposures_scale_linearly_with_open_interest() -> None:
    one = leg_exposures_usd(_leg("C", 7900.0, oi=1), 7700.0, 7700.0)
    many = leg_exposures_usd(_leg("C", 7900.0, oi=250), 7700.0, 7700.0)
    for field in ("gamma", "vanna", "charm"):
        assert abs(getattr(many, field) / getattr(one, field) - 250) < 1e-9


def test_call_vanna_is_split_by_right_not_by_sign() -> None:
    """The bug this guards: gamma is positive for both rights, so the sign of a
    signed value happens to identify the side. Vanna is negative below the forward
    for calls too — split on the sign and half the call book files under puts."""
    board = _board((_leg("C", 7000.0, oi=5000), _leg("C", 7100.0, oi=5000)))
    row = summarize(board)
    assert row["call_vanna_usd_per_volpt_b"] < 0  # below the forward, so negative
    assert row["put_vanna_usd_per_volpt_b"] == 0.0  # there are no puts on this board
    assert row["net_vanna_usd_per_volpt_b"] == row["call_vanna_usd_per_volpt_b"]


def test_charm_reports_how_much_of_itself_expires_within_the_day() -> None:
    """26% of the real board's charm sat in contracts expiring that same day. Those
    resolve rather than drift, so the headline must not be extrapolated past them.

    Reported as a level rather than a share because the front of the board and the
    rest of it can point opposite ways — this very board does, and the share form
    of this number printed 129%.
    """
    # Index-scale open interest: at a few hundred contracts every figure here
    # rounds to 0.0 billions and the test would pass without measuring anything.
    board = _board((
        _leg("C", 7700.0, dte=1, oi=2_000_000), _leg("C", 7700.0, dte=200, oi=2_000_000)
    ))
    row = summarize(board)
    front, back = (leg_exposures_usd(leg, board.spot, board.spot) for leg in board.legs)
    assert row["charm_from_expiring_b"] == pytest.approx(front.charm / 1e9, abs=1e-3)
    # The offsetting case the level form survives and a percentage does not: the
    # front alone is bigger than the whole board, so the share form printed 129%.
    assert (front.charm > 0) != (back.charm > 0)
    assert abs(row["charm_from_expiring_b"]) > abs(row["net_charm_usd_per_day_b"]) > 0


def test_every_drilldown_row_carries_all_three_axes() -> None:
    """A view that reports gamma and quietly drops vanna would read as a board with
    no vanna rather than a view that does not show it."""
    board = _board((_leg("C", 7900.0, dte=10), _leg("P", 7500.0, dte=45)))
    for rows in (strike_rows(board), expiry_rows(board), summarize(board)["by_horizon"]):
        for row in rows:
            assert "net_vanna_usd_per_volpt_b" in row
            assert "net_charm_usd_per_day_b" in row


# --- the convention -----------------------------------------------------------
def test_calls_net_positive_and_puts_net_negative() -> None:
    calls = _board((_leg("C", 7700.0), _leg("C", 7750.0)))
    puts = _board((_leg("P", 7700.0), _leg("P", 7750.0)))
    assert net_gamma_at(calls.legs, 7700.0, 7700.0) > 0
    assert net_gamma_at(puts.legs, 7700.0, 7700.0) < 0


def test_matched_call_and_put_open_interest_nets_to_zero() -> None:
    legs = (_leg("C", 7700.0, oi=500), _leg("P", 7700.0, oi=500))
    assert abs(net_gamma_at(legs, 7700.0, 7700.0)) < 1e-6


# --- the flip -----------------------------------------------------------------
def test_flip_is_found_between_a_put_wall_below_and_a_call_wall_above() -> None:
    """Puts stacked low, calls stacked high: net must cross zero between them."""
    legs = (_leg("P", 7300.0, oi=5000), _leg("C", 8100.0, oi=5000))
    flip, crossings = gamma_flip(legs, 7700.0)
    assert crossings == 1
    assert flip is not None
    assert 7300.0 < flip < 8100.0
    # And it really is a crossing, not just a number in the right range.
    assert net_gamma_at(legs, flip - 50, 7700.0) < 0 < net_gamma_at(legs, flip + 50, 7700.0)


def test_a_board_that_never_crosses_reports_no_flip_rather_than_naming_one() -> None:
    legs = (_leg("C", 7700.0, oi=1000), _leg("C", 7800.0, oi=1000))
    flip, crossings = gamma_flip(legs, 7700.0)
    assert flip is None
    assert crossings == 0


def test_multiple_crossings_are_counted_and_the_nearest_is_returned() -> None:
    """An ambiguous board must SAY it is ambiguous — one level would be false
    confidence when the sign flips more than once inside the search range."""
    legs = (
        _leg("P", 6200.0, oi=9000, sigma=0.10),
        _leg("C", 7000.0, oi=9000, sigma=0.10),
        _leg("P", 8300.0, oi=9000, sigma=0.10),
    )
    flip, crossings = gamma_flip(legs, 7700.0)
    assert crossings >= 2
    assert flip is not None
    others = [7000.0, 8300.0]
    assert abs(flip - 7700.0) <= min(abs(o - 7700.0) for o in others) + 400


# --- the views ----------------------------------------------------------------
def test_strikes_rank_by_gross_not_net() -> None:
    """The 8000 case: a huge two-sided strike nets to almost nothing. Ranking by
    net drops the single biggest concentration on the board off the list."""
    board = _board((
        _leg("C", 8000.0, oi=100_000), _leg("P", 8000.0, oi=99_000),  # gross huge, net ~0
        _leg("C", 7750.0, oi=3_000),                                   # small but net-only
    ))
    top = strike_rows(board, limit=1)
    assert [r["strike"] for r in top] == [8000.0]


def test_strike_rows_come_back_in_strike_order() -> None:
    board = _board((_leg("C", 7900.0, oi=50), _leg("C", 7600.0, oi=99), _leg("C", 7750.0, oi=70)))
    strikes = [r["strike"] for r in strike_rows(board, limit=3)]
    assert strikes == sorted(strikes)


def test_expiry_rows_are_nearest_first() -> None:
    board = _board((
        _leg("C", 7700.0, dte=45), _leg("C", 7700.0, dte=2), _leg("C", 7700.0, dte=20),
    ))
    rows = expiry_rows(board)
    assert [r["dte"] for r in rows] == sorted(r["dte"] for r in rows)


def test_profile_never_calls_its_hypothetical_level_spot() -> None:
    """The clobber bug: these rows ride alongside a context block holding the REAL
    spot, and a same-named key would silently overwrite it — a reader would then
    take a -8% scenario level for the current index."""
    rows = profile_rows(_board((_leg("C", 7700.0),)))
    assert all("spot" not in r for r in rows)
    assert all("hypothetical_spot" in r for r in rows)
    assert rows[0]["hypothetical_spot"] < rows[-1]["hypothetical_spot"]


def test_drilldown_rows_cannot_overwrite_the_readers_context() -> None:
    """Generic guard for the whole class of bug the profile hit."""
    from ai.perception.resources import _GEX_CONTEXT_KEYS

    context_keys = set(_GEX_CONTEXT_KEYS)
    board = _board((_leg("C", 7700.0), _leg("P", 7600.0)))
    for rows in (strike_rows(board), expiry_rows(board), profile_rows(board)):
        for row in rows:
            assert not context_keys & set(row), f"row would clobber context: {row}"


# --- the honesty rails --------------------------------------------------------
def test_summary_always_carries_the_convention_the_source_and_the_disclaimer() -> None:
    row = summarize(_board((_leg("C", 7700.0), _leg("P", 7650.0))))
    assert "ASSUMES" in row["convention"]
    assert row["greeks_source"] == "black76"
    assert "no edge" in row["not_a_signal"]
    # The unconditional facts must be present beside the conditional net.
    assert row["call_gamma_usd_per_1pct_b"] > 0
    assert row["put_gamma_usd_per_1pct_b"] > 0
    # Three axes in one unit is a trap unless the denominators are named.
    for denominator in ("1% move", "volatility point", "calendar day"):
        assert denominator in row["units"]
    assert "tested against forward returns" in row["vanna_charm_mean"]


def test_summary_signs_the_distance_to_the_flip() -> None:
    """Unsigned, the two regimes would read identically.

    Each case builds its OWN board: a leg's forward and its board's spot come from
    one read of one chain, and reusing legs under a different spot would assert a
    carry that chain never quoted (a synthetic-only state, but it silently moves
    the flip — which is how this test first failed).
    """
    def board_at(spot: float) -> Board:
        legs = (
            _leg("P", 7300.0, oi=5000, forward=spot * 1.0005),
            _leg("C", 8100.0, oi=5000, forward=spot * 1.0005),
        )
        return _board(legs, spot=spot)

    above = summarize(board_at(7900.0))
    below = summarize(board_at(7500.0))
    assert above["spot_above_flip_pct"] > 0
    assert below["spot_above_flip_pct"] < 0
    # The identity itself, independent of which side we happen to be on.
    for row in (above, below):
        assert (row["spot"] > row["gamma_flip_spot"]) == (row["spot_above_flip_pct"] > 0)


def test_horizon_table_ends_with_the_whole_board() -> None:
    """The DTE cut moves the net by ~2x on the real board, so the total has to be
    in the table next to the slices, not implied by them."""
    board = _board((_leg("C", 7700.0, dte=3), _leg("C", 7700.0, dte=200)))
    rows = summarize(board)["by_horizon"]
    assert rows[-1]["dte_max"] is None
    assert rows[-1]["open_interest"] == sum(leg.oi for leg in board.legs)


# --- the store ----------------------------------------------------------------
def _summary_for(net: float, as_of: str = "2026-08-10T20:00:00Z") -> dict:
    return {
        "quote_as_of": as_of,
        "oi_as_of": "2026-08-11",
        "spot": 7725.35,
        "net_gamma_usd_per_1pct_b": net,
        "call_gamma_usd_per_1pct_b": 285.0,
        "put_gamma_usd_per_1pct_b": 285.0 - net,
        "gamma_flip_spot": 7600.0,
        "spot_above_flip_pct": 1.6,
        "net_vanna_usd_per_volpt_b": net / 2,
        "net_charm_usd_per_day_b": -net / 4,
        "by_horizon": [{"dte_max": None, "gross_usd_per_1pct_b": 500.0}],
        "coverage": {"open_interest_covered_pct": 92.5},
    }


def test_record_round_trips_and_is_idempotent_on_one_quote_timestamp(tmp_path: Path) -> None:
    """The same frozen close read twice is ONE observation, not two — otherwise a
    re-run after the close silently doubles that day's weight in every percentile."""
    db = tmp_path / "gex.db"
    assert record(_summary_for(65.8), db_path=db) == "2026-08-10T20:00:00Z"
    record(_summary_for(65.8), db_path=db)
    rows = history(db_path=db)
    assert len(rows) == 1
    assert rows[0]["net_b"] == 65.8
    assert rows[0]["net_vanna_b"] == 32.9
    assert rows[0]["net_charm_b"] == -16.45
    assert rows[0]["session"] == "2026-08-10"


def test_a_store_written_before_vanna_existed_migrates_instead_of_failing(
    tmp_path: Path,
) -> None:
    """The production store already held snapshots when these two columns landed.

    The old rows read back NULL rather than 0.0 on purpose — a board that was never
    measured for vanna is not a board whose vanna was zero, and a backfilled zero
    would be indistinguishable from the real thing in every percentile after it.
    """
    import sqlite3

    db = tmp_path / "gex.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE gex_snapshots (as_of TEXT PRIMARY KEY, session TEXT NOT NULL,"
            " oi_as_of TEXT NOT NULL, spot REAL NOT NULL, net_b REAL NOT NULL,"
            " call_b REAL NOT NULL, put_b REAL NOT NULL, gross_b REAL NOT NULL,"
            " flip REAL, spot_above_flip_pct REAL, oi_covered_pct REAL)"
        )
        conn.execute(
            "INSERT INTO gex_snapshots VALUES"
            " ('2026-08-11T19:59:59Z','2026-08-11','2026-08-11',7725.35,65.8,"
            "285.6,219.8,505.4,7617.7,1.41,92.5)"
        )
    init_gex_table(db)
    record(_summary_for(70.0, "2026-08-12T19:59:59Z"), db_path=db)

    rows = {r["as_of"]: r for r in history(db_path=db)}
    assert rows["2026-08-11T19:59:59Z"]["net_vanna_b"] is None
    assert rows["2026-08-12T19:59:59Z"]["net_vanna_b"] == 35.0


def test_percentiles_says_the_shared_caveat_once_not_once_per_axis(
    tmp_path: Path,
) -> None:
    """Three copies of one 200-character note reads like three problems."""
    db = tmp_path / "gex.db"
    record(_summary_for(65.8), db_path=db)
    out = percentiles(
        {"net_b": 65.8, "net_vanna_b": 32.9, "net_charm_b": -16.45}, db_path=db
    )
    assert out["observations"] == 1
    assert set(out["percentiles"]) == {"net_b", "net_vanna_b", "net_charm_b"}
    assert all(v is None for v in out["percentiles"].values())
    assert isinstance(out["note"], str)  # exactly one, not one per field


def test_record_refuses_a_summary_with_no_quote_timestamp(tmp_path: Path) -> None:
    summary = _summary_for(65.8)
    summary["quote_as_of"] = None
    assert record(summary, db_path=tmp_path / "gex.db") is None


def test_percentile_withholds_itself_until_there_is_enough_history(tmp_path: Path) -> None:
    db = tmp_path / "gex.db"
    for i in range(5):
        record(_summary_for(float(i), f"2026-08-{10 + i:02d}T20:00:00Z"), db_path=db)
    out = percentile("net_b", 3.0, db_path=db)
    assert out["percentile"] is None
    assert out["observations"] == 5
    assert "cannot be backfilled" in out["note"]


def test_percentile_ranks_once_the_window_is_deep_enough(tmp_path: Path) -> None:
    db = tmp_path / "gex.db"
    for i in range(MIN_OBSERVATIONS):
        record(
            _summary_for(float(i), f"2026-06-01T{i // 60:02d}:{i % 60:02d}:00Z"),
            db_path=db,
        )
    out = percentile("net_b", float(MIN_OBSERVATIONS - 1), db_path=db)
    assert out["observations"] == MIN_OBSERVATIONS
    assert out["percentile"] == 100.0
    assert percentile("net_b", -1.0, db_path=db)["percentile"] == 0.0


def test_percentile_rejects_a_field_that_is_not_stored(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown field"):
        percentile("skew_b", 1.0, db_path=tmp_path / "gex.db")


def test_history_on_a_missing_store_is_empty_not_an_error(tmp_path: Path) -> None:
    assert history(db_path=tmp_path / "never-created.db") == []


# --- the board build, end to end, with no network -----------------------------
_SIGMA = 0.20
_FORWARD = 7800.0
_STRIKES = (7600.0, 7700.0, 7800.0, 7900.0, 8000.0)


class _StubBroker:
    """A chain whose quotes were PRICED at a known vol, so the inversion has a
    right answer to be checked against rather than merely a plausible one."""

    def __init__(self, expiries: tuple[str, ...]) -> None:
        self.expiries = expiries
        self.calls: list[str] = []

    def get_option_chain_snapshot(self, root: str, **kwargs: object) -> dict:
        self.calls.append(root)
        if root != "SPXW":
            return {}
        out: dict[str, dict] = {}
        for expiry in self.expiries:
            t = _dte(expiry) / 365.0
            for strike in _STRIKES:
                for cp, is_call in (("C", True), ("P", False)):
                    price = black76_price(_FORWARD, strike, t, 1.0, _SIGMA, is_call)
                    occ = (
                        f"SPXW{expiry[2:].replace('-', '')}{cp}{int(strike * 1000):08d}"
                    )
                    out[occ] = {
                        "latestQuote": {
                            "bp": round(price - 0.05, 2),
                            "ap": round(price + 0.05, 2),
                            "t": f"{expiry}T19:59:00Z",
                        }
                    }
        return out


def _dte(expiry: str) -> int:
    from datetime import date as _date

    return (_date.fromisoformat(expiry) - _date(2026, 8, 11)).days


def _build(monkeypatch: pytest.MonkeyPatch, oi_map: dict, expiries: tuple[str, ...]):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import db.oi_walls as oi_walls_mod
    from signals.gamma_exposure import build_board as _build_board

    monkeypatch.setattr(oi_walls_mod, "oi_board", lambda **_kw: ("2026-08-10", oi_map))
    broker = _StubBroker(expiries)
    now = datetime(2026, 8, 11, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    return _build_board(broker=broker, now_et=now), broker


def test_build_board_recovers_the_vol_the_quotes_were_priced_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oi_map = {("SPXW", "2026-09-18", "C", 7800.0): 1_000}
    board, broker = _build(monkeypatch, oi_map, ("2026-09-18",))
    assert board is not None
    assert set(broker.calls) == {"SPX", "SPXW"}  # BOTH index roots, always
    assert len(board.legs) == 1
    leg = board.legs[0]
    assert abs(leg.sigma - _SIGMA) < 0.01
    # Parity recovers the forward the quotes were built around...
    assert abs(leg.forward - _FORWARD) < 1.0
    # ...and spot is that forward discounted. Stated as the identity rather than a
    # number: F*D = S*e^(-qT), so it sits a dividend under the true spot — nothing
    # at the 1-DTE expiry that sets it in production, 0.4% out here at 38 DTE.
    assert abs(board.spot - leg.forward * leg.discount) < 0.02


def test_build_board_prices_only_strikes_that_carry_open_interest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The store holds OI >= 1 only, so a listed strike with no row is an empty
    strike — zero contracts, zero gamma, correctly absent rather than missing."""
    oi_map = {
        ("SPXW", "2026-09-18", "C", 7800.0): 500,
        ("SPXW", "2026-09-18", "P", 7800.0): 400,
    }
    board, _ = _build(monkeypatch, oi_map, ("2026-09-18",))
    assert board is not None
    assert {(leg.cp, leg.strike) for leg in board.legs} == {("C", 7800.0), ("P", 7800.0)}
    # 10 contracts on the chain, 2 with OI: the other 8 are accounted for by name.
    assert board.coverage["skipped_contracts"]["no_open_interest"] == 8
    assert board.coverage["open_interest_priced"] == 900


def test_build_board_reports_open_interest_it_could_not_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counts alone would hide the thing that matters — a thousand unpriceable
    strikes of 30 lots is noise, the same count at 300,000 is a hole."""
    oi_map = {
        ("SPXW", "2026-09-18", "C", 7800.0): 500,
        ("SPXW", "2026-09-18", "C", 99_999.0): 250_000,  # not on the chain at all
    }
    board, _ = _build(monkeypatch, oi_map, ("2026-09-18",))
    assert board is not None
    assert board.coverage["open_interest_standing"] == 250_500
    assert board.coverage["open_interest_covered_pct"] == 0.2


def test_build_board_without_an_oi_store_returns_no_board_not_a_flat_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, _ = _build(monkeypatch, {}, ("2026-09-18",))
    assert board is None


def test_perception_reader_rejects_an_unknown_view() -> None:
    from ai.perception.resources import _read_gex

    rows = _read_gex({"view": "vanna"}, db_path=Path("unused.db"))
    assert rows[0]["error"].startswith("unknown view")
    assert "profile" in rows[0]["views"]
