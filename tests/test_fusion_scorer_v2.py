"""v2 decayed-mass scorer (Fusion Horizon Redesign §2.1/§2.2).

Covers the two bugs the redesign kills: the no-decay all-day sum and the
binary contested-if-ever bit. score_dots and the live shadow gate are untouched
and out of scope here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from db.fusion import init_fusion_tables
from db.options_flow import init_db as init_options
from signals.fusion.scorer import (
    CONVICTION_PRIOR_MASS,
    NOISE_FLOOR,
    ROLE_THESIS,
    ROLE_TRIGGER,
    SESSION_SECONDS,
    THESIS_HALF_LIFE_TD,
    evidence_role,
    score_decayed,
    session_seconds_between,
)

_UTC = timezone.utc


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_UTC)


def _dot(
    dot_id: str,
    ts: datetime,
    *,
    direction: str = "bull",
    group: str = "options_actor",
    component: str = "option_block",
    raw: float = 500_000.0,  # log2(500k / 250k base) = 1.0 point per dot
    dte: int | None = None,
    stamped: int = 1,
) -> dict:
    dot: dict = {
        "id": dot_id,
        "actor_group": group,
        "component": component,
        "direction": direction,
        "ts": ts.isoformat(),
        "raw_value": str(raw),
        "stamped": stamped,
        "detail": {},
    }
    if dte is not None:
        dot["dte"] = dte
    return dot


# --------------------------------------------------------------------- decay


def test_thesis_mass_halves_after_one_session_half_life():
    # Half-life dial cut to 0.5 td (11,700 session-seconds = 3h15m) so it fits
    # inside one RTH session — no boundary crossing to obscure the arithmetic.
    ts = _utc(2026, 7, 21, 14, 0)  # Tue 10:00 ET
    now = _utc(2026, 7, 21, 17, 15)  # Tue 13:15 ET → exactly 11,700 session-s
    dot = _dot("thesis", ts, component="option_block", dte=30)
    assert session_seconds_between(ts, now) == 0.5 * SESSION_SECONDS
    m0 = score_decayed([dot], ts, thesis_half_life_td=0.5)
    m1 = score_decayed([dot], now, thesis_half_life_td=0.5)
    assert m0["thesis"]["bull_mass"] == 1.0
    assert abs(m1["thesis"]["bull_mass"] - 0.5) < 1e-6


def test_thesis_decay_ignores_overnight_and_weekend():
    # A pure weekend elapses zero session time.
    assert session_seconds_between(_utc(2026, 7, 18, 12, 0), _utc(2026, 7, 19, 12, 0)) == 0.0
    # Fri 15:00 ET → Mon 11:00 ET: only the 1h Friday tail + 1.5h Monday head
    # count; the ~64h overnight/weekend gap is invisible to the thesis clock.
    fri = _utc(2026, 7, 17, 19, 0)  # 15:00 ET Fri
    mon = _utc(2026, 7, 20, 15, 0)  # 11:00 ET Mon
    assert session_seconds_between(fri, mon) == 3600 + 5400  # 1h + 1.5h
    dot = _dot("thesis", fri, component="option_block", dte=30)
    # Thesis barely decays across the weekend; a same-span TRIGGER dot (wall
    # clock, overnight counts) is stone dead by Monday.
    thesis = score_decayed([dot], mon)["thesis"]["bull_mass"]
    trig_dot = _dot("trig", fri, component="option_sweep", dte=0)
    trigger = score_decayed([trig_dot], mon)["trigger"]["bull_mass"]
    assert thesis > 0.8  # 0.5 ** (9000 / (2.5*23400)) ≈ 0.898
    assert trigger < 1e-3  # ~64h wall clock at a 60-min half-life → ~0


def test_trigger_mass_halves_after_60_wall_minutes_and_counts_overnight():
    ts = _utc(2026, 7, 21, 14, 0)
    dot = _dot("trig", ts, component="option_sweep", dte=0)
    m0 = score_decayed([dot], ts)
    m1 = score_decayed([dot], ts + timedelta(minutes=60))
    assert m0["trigger"]["bull_mass"] == 1.0
    assert abs(m1["trigger"]["bull_mass"] - 0.5) < 1e-6
    # Overnight is real time for a trigger: 12h later it is gone.
    overnight = score_decayed([dot], ts + timedelta(hours=12))["trigger"]["bull_mass"]
    assert overnight < 1e-3


# ---------------------------------------------------------------- conviction


def test_conviction_shrinks_with_prior_mass_small_below_one_large_approaches_one():
    """The INTC-artifact fix: a thin one-sided read no longer posts full
    conviction. With the prior in the denominator, a lone dot is well below 1 and
    conviction only approaches 1 as real one-sided mass accumulates — asymptotic,
    never exactly 1. Balanced masses stay exactly 0."""
    ts = _utc(2026, 7, 21, 14, 0)

    # Small one-sided mass (one point-1.0 dot): 1 / (1 + prior) — well below 1.
    small = score_decayed([_dot("a", ts, direction="bull", dte=30)], ts)
    assert small["thesis"]["conviction"] < 0.5
    # scorer rounds conviction to 4dp, so compare with a 4dp-scale tolerance.
    assert abs(small["thesis"]["conviction"] - 1.0 / (1.0 + CONVICTION_PRIOR_MASS)) < 1e-3

    # Large one-sided mass (20 aligned point-2.0 dots → gross 40): approaches 1.
    big_dots = [
        _dot(f"b{i}", ts, direction="bull", raw=1_000_000, dte=30) for i in range(20)
    ]
    big = score_decayed(big_dots, ts)
    assert big["thesis"]["conviction"] > 0.9
    assert big["thesis"]["conviction"] < 1.0  # asymptotic — the prior never lets it hit 1

    # Balanced two-sided → exactly 0 (the prior only touches the denominator).
    balanced = score_decayed(
        [
            _dot("a", ts, direction="bull", dte=30),
            _dot("b", ts, direction="bear", dte=30),
        ],
        ts,
    )
    assert balanced["thesis"]["conviction"] == 0.0
    assert balanced["maturity"] == 0.0  # gross × 0 conviction


def test_conviction_prior_mass_zero_recovers_pure_ratio():
    """The dial is threaded through score_decayed: prior_mass=0 restores the old
    |bull−bear|/gross, so a lone one-sided dot reads exactly 1.0."""
    ts = _utc(2026, 7, 21, 14, 0)
    pure = score_decayed(
        [_dot("a", ts, direction="bull", dte=30)], ts, conviction_prior_mass=0.0
    )
    assert pure["thesis"]["conviction"] == 1.0


# ------------------------------------------------- z ranking statistic (norm)


def test_z_math_and_cancellation():
    """z = |bull − bear| / √(gross + prior) — the ranking statistic
    (FUSION_SCORING_NORMALIZATION_2026-07-22). Four aligned point-1.0 dots:
    z = 4/√(4+2); balanced masses net to 0 → z exactly 0 (cancellation is
    EXPOSED by z, not hidden); no dots → z 0."""
    ts = _utc(2026, 7, 21, 14, 0)
    aligned = [_dot(f"a{i}", ts, direction="bull", dte=30) for i in range(4)]
    state = score_decayed(aligned, ts)["thesis"]
    assert abs(state["z"] - 4.0 / (4.0 + CONVICTION_PRIOR_MASS) ** 0.5) < 1e-3

    balanced = score_decayed(
        [
            _dot("a", ts, direction="bull", dte=30),
            _dot("b", ts, direction="bear", dte=30),
        ],
        ts,
    )
    assert balanced["thesis"]["z"] == 0.0
    assert score_decayed([], ts)["thesis"]["z"] == 0.0


def test_z_prior_mass_tames_thin_names():
    """A lone point-1.0 dot: with the prior z = 1/√3 ≈ 0.577; with prior=0 it
    would read 1/√1 = 1.0 — the shrinkage prior is what stops three thin prints
    spiking the board (symptom 3 of the disease)."""
    ts = _utc(2026, 7, 21, 14, 0)
    thin = [_dot("a", ts, direction="bull", dte=30)]
    with_prior = score_decayed(thin, ts)["thesis"]["z"]
    no_prior = score_decayed(thin, ts, conviction_prior_mass=0.0)["thesis"]["z"]
    assert abs(with_prior - 1.0 / 3.0**0.5) < 1e-3
    assert abs(no_prior - 1.0) < 1e-3
    assert with_prior < no_prior


def test_z_high_on_ocean_lean_but_contested_for_dual_gate():
    """The Grok catch the dual gate exists for: z ≈ lean·√gross, so a modest
    proportional lean on ocean flow still posts a big z (ranked, visible) while
    conviction sits under the bar (contested → NOT armable). 60 bull / 40 bear
    point-1.0 dots: z = 20/√102 ≈ 1.98, conviction = 20/102 ≈ 0.196 < 0.4."""
    ts = _utc(2026, 7, 21, 14, 0)
    ocean = [_dot(f"b{i}", ts, direction="bull", dte=30) for i in range(60)] + [
        _dot(f"s{i}", ts, direction="bear", dte=30) for i in range(40)
    ]
    state = score_decayed(ocean, ts)["thesis"]
    assert state["z"] > 1.9
    assert state["conviction"] < 0.4
    assert state["contested"] is True


# ------------------------------------------------------------ contested heals


def test_contested_heals_as_thesis_evidence_ages_below_floor():
    """The core disease fix. A near-balanced two-sided symbol is contested while
    its evidence is fresh, and — with NO new dots — heals as the day's mass
    decays below the noise floor. Healing comes from the CONTEMPORANEOUS gross
    floor, not the old absorbing-till-midnight bit. (Under the conviction prior
    the ratio is no longer perfectly time-invariant: uniform decay shrinks gross
    while the prior stays fixed, so conviction drifts DOWN as evidence ages — it
    never spuriously rises, which is all the heal needs.)"""
    ts = _utc(2026, 7, 21, 14, 0)  # Tue 10:00 ET, both sides fresh
    dots = [
        _dot("bull", ts, direction="bull", raw=500_000, dte=30),  # point 1.0
        _dot("bear", ts, direction="bear", raw=570_000, dte=30),  # point ~1.19
    ]
    fresh = score_decayed(dots, ts)["thesis"]
    assert fresh["gross"] >= NOISE_FLOOR
    assert fresh["conviction"] < 0.4
    assert fresh["contested"] is True

    later = _utc(2026, 7, 24, 14, 0)  # Fri 10:00 ET → > 1 half-life of sessions
    assert session_seconds_between(ts, later) > THESIS_HALF_LIFE_TD * SESSION_SECONDS
    healed = score_decayed(dots, later)["thesis"]
    assert healed["gross"] < NOISE_FLOOR  # aged into noise
    assert healed["contested"] is False  # heals via the gross floor
    assert healed["conviction"] < fresh["conviction"]  # prior shrinks it further as mass ages


# --------------------------------------------------------------- role routing


def test_role_routing_and_maturity_ignores_trigger_dots():
    ts = _utc(2026, 7, 21, 14, 0)
    sweep_0dte = _dot("s", ts, component="option_sweep", dte=0)
    flow_30dte = _dot("f", ts, component="option_block", dte=30)
    assert evidence_role(sweep_0dte) == ROLE_TRIGGER
    assert evidence_role(flow_30dte) == ROLE_THESIS

    trig_only = score_decayed([sweep_0dte], ts)
    assert trig_only["trigger"]["bull_mass"] > 0
    assert trig_only["thesis"]["bull_mass"] == 0.0
    assert trig_only["maturity"] == 0.0  # a trigger dot never builds maturity

    thesis_only = score_decayed([flow_30dte], ts)
    assert thesis_only["thesis"]["bull_mass"] > 0
    assert thesis_only["trigger"]["bull_mass"] == 0.0

    # Adding the trigger dot to the thesis dot leaves maturity byte-identical:
    # no cross-role mixing (§2.2/§5).
    both = score_decayed([flow_30dte, sweep_0dte], ts)
    assert both["maturity"] == thesis_only["maturity"]
    assert both["trigger"]["bull_mass"] == trig_only["trigger"]["bull_mass"]


# -------------------------------------------------------------- replay smoke


def test_replay_smoke_on_tmp_db(tmp_path):
    from tools.fusion_rescore_replay import rescore_range

    db_path = tmp_path / "t.db"
    init_fusion_tables(db_path)
    init_options(db_path)

    episode_id = "ep_smoke"
    day = "2026-07-21"
    conn = sqlite3.connect(db_path)
    # One 30-DTE bullish block, sourced from options_flow_events so the replay's
    # DTE-recovery join is exercised (evidence_dots don't persist DTE).
    conn.execute(
        """INSERT INTO options_flow_events(id, ts, underlying, occ_symbol,
               event_type, dte, premium_usd, created_at)
           VALUES ('ev1','2026-07-21T14:00:00+00:00','ZZZ','ZZZ260821C1',
               'block', 30, 500000, '2026-07-21T14:00:00+00:00')"""
    )
    conn.execute(
        """INSERT INTO evidence_dots(id, episode_id, source_store,
               source_event_id, source_ingest_seq, symbol, ts, component,
               actor_group, dot_kind, direction, raw_value, points, stamped,
               created_at)
           VALUES ('d1', ?, 'options_flow', 'ev1', 1, 'ZZZ',
               '2026-07-21T14:00:00+00:00', 'option_block', 'options_actor',
               'event', 'bull', '500000', 0, 1, '2026-07-21T14:00:00+00:00')""",
        (episode_id,),
    )
    conn.execute(
        """INSERT INTO fusion_investigations(episode_id, market_day, symbol,
               decided_at, decision, score, threshold)
           VALUES (?, ?, 'ZZZ', '2026-07-21T14:05:00+00:00',
               'shadow_investigate', 1.8, 1.0)""",
        (episode_id, day),
    )
    conn.commit()
    conn.close()

    report = rescore_range(db_path)
    assert len(report["records"]) == 1
    rec = report["records"][0]
    assert rec["symbol"] == "ZZZ"
    assert rec["decision"] == "shadow_investigate"
    assert rec["old_score"] == 1.8
    # 30-DTE block → thesis; one-sided point-1.0 mass → conviction shrunk by the
    # prior to 1/(1+2) and maturity = gross × that > 0.
    assert rec["thesis_bull"] > 0
    assert abs(rec["conviction"] - 1.0 / (1.0 + CONVICTION_PRIOR_MASS)) < 1e-3
    assert rec["maturity"] > 0
    # z = 1/√(1+2) ≈ 0.577 at decision; the peak instant is the dot's own ts.
    assert abs(rec["z"] - 1.0 / 3.0**0.5) < 1e-2
    assert rec["peak_z"] >= rec["z"]
    assert rec["peak_z_ts"] is not None
    # conviction 1/3 < CONVICTION_BAR → never armable → peak_armable_z stays 0.
    assert rec["peak_armable_z"] == 0.0
