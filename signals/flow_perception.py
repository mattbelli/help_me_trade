"""Daily unusual-vs-peers flow scan — the 1.11-1.14d fingerprint as PERCEPTION.

Ships the backwards flow fingerprint (short-dated share, single-day burst
concentration, abnormal largest print, equity volume spike) as labelled
perception, per the founder decision on board card ``flow-next-lens``. What the
registered research actually found — and why every row carries a fixed
disclaimer:

* BACKWARDS (FLOW_ROADMAP 1.13 / 1.13b / 1.14b): big-move windows really do carry
  this fingerprint 1.4-1.7x more often than liquidity-matched peers, in TWO
  regimes split by earnings-adjacency — short-dated option flow intensifies near
  scheduled news, while the equity volume spike is a no-catalyst phenomenon.
* FORWARD (1.14c global thresholds, 1.14d peer ranks): pre-registered NULL, twice.
  Day-grain screening is dead at these feature definitions — the profile
  DESCRIBES big-move windows without SELECTING them. The one recurring positive
  (5d short_dte_share, 1.2-1.3x) is one angle for fusion, never a standalone
  alert.

So this job answers exactly one question: "is this name unusual versus its
same-liquidity peers today, worth a look?" It never emits a probability, a win
rate, a score, or a trade signal.

The math is imported, never re-derived: component definitions come from
``tools/mover_controls.py`` (``_window_features``) and ``tools/mover_volume.py``
(``_vol_feats``), and the firing rule is 1.14d's — the component clears the p90 of
its own (session x trailing-20d dollar-volume quintile) peer cell.

Source is the flow corpus, because the registered fence (``side IN ('buy','sell')
AND multileg=0``, with a per-day ``premium_max``) exists ONLY there: production's
``options_flow_events`` is a detector-selected subset with no tick-test side and no
multileg grain, so recomputing the components off it would quietly change the
math. The corpus advances via ``tools/flow_corpus_rollup.py``, so every row stamps
the session it describes and how stale that session is — a corpus that stopped
advancing must read as stale, never as "nothing unusual today".
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from db.base import DEFAULT_DB_PATH
from db.earnings_calendar import last_earnings_date, next_earnings_date
from db.flow_perception import COMPONENT_COLUMNS, upsert_rows
from tools import mover_controls, mover_profile
from tools.mover_controls import (
    WINDOWS,
    _bars,
    _flow_features,
    _tier_quintiles,
    _window_features,
)
from tools.mover_profile import _universe
from tools.mover_volume import _vol_feats

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# 1.14d's firing rule verbatim: top decile of the component inside the peer cell.
# p90 mirrors the frozen backwards thresholds' tail choice — inherited, not tuned.
RANK_PCT = 90.0

# Earnings-adjacency gate, in weekday sessions. 10 is the pre-existing
# earnings_drift td=10 constant, so strategy, research gate and this product
# surface all say "earnings-adjacent" about the same horizon. Fixed, NOT tied to
# the window length: a window-tied gate would tag one name three different ways
# in the same session, which reads as noise.
EARNINGS_ADJACENT_MAX_SESSIONS = 10

REGIME_EARNINGS = "earnings_adjacent"
REGIME_NO_CATALYST = "no_known_catalyst"

# What each regime means, per 1.14b's measured split (used by the alert body).
REGIME_NOTE = {
    REGIME_EARNINGS: (
        "earnings-adjacent — near scheduled news the short-dated flow tilt is the "
        "part that intensifies, and the pre-event tape is usually quiet"
    ),
    REGIME_NO_CATALYST: (
        "no known catalyst — away from earnings the equity volume spike is the part "
        "that carries the pattern (earnings detection is the only catalyst source we "
        "have, so this means no KNOWN catalyst)"
    ),
}

DISCLAIMER = (
    "Perception, not a prediction. This row says only that the name's option flow / "
    "equity volume was unusual versus same-liquidity peers over the trailing window. "
    "Forward day-grain screening on these components was pre-registered and came back "
    "NULL twice (FLOW_ROADMAP 1.14c global thresholds, 1.14d peer ranks) — so this is "
    "context to look at alongside other angles, never a probability, win rate, score "
    "or trade signal."
)

# The registered corpus loaders read a module-level CORPUS_DB constant (research
# scripts, not parameterized) and mover_profile imported it BY VALUE, so both names
# must be swapped or _universe() still queries the default corpus.
_CORPUS_CONST_OWNERS = (mover_controls, mover_profile)


@contextmanager
def _corpus_at(path: Path | None):
    """Point the registered corpus loaders at ``path`` for the duration.

    Swapping the constant beats copying the loaders: the registered SQL fence
    stays the single source of truth for what a "fenced name-day" is.
    """
    if path is None:
        yield
        return
    saved = [(mod, mod.CORPUS_DB) for mod in _CORPUS_CONST_OWNERS]
    for mod, _ in saved:
        mod.CORPUS_DB = path
    try:
        yield
    finally:
        for mod, old in saved:
            mod.CORPUS_DB = old


def _rank_within_cells(
    values: np.ndarray, quintiles: np.ndarray, eligible: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Percentile rank + top-decile flag of one component inside each peer cell.

    Cell = (this session x trailing-20d dollar-volume quintile), exactly as 1.14d.
    A name with an undefined value is not ranked and cannot fire (missing is not
    absent), and it stays out of its peers' denominator.
    """
    rank = np.full(values.shape, np.nan)
    fired = np.zeros(values.shape, bool)
    defined = eligible & np.isfinite(values)
    for q in range(5):
        cell = defined & (quintiles == q)
        n = int(cell.sum())
        if not n:
            continue
        vals = values[cell]
        cut = float(np.percentile(vals, RANK_PCT))
        fired[cell] = vals >= cut
        # share of the cell at or below this value, in percent
        rank[cell] = 100.0 * np.searchsorted(np.sort(vals), vals, side="right") / n
    return rank, fired


def _earnings_context(
    symbol: str, session: str, *, db_path: Path
) -> tuple[int | None, bool, str]:
    """(calendar days to next known earnings, have any earnings data, regime).

    Coverage is reported separately from the date so "we have no earnings data for
    this name" never reads as "this name has no catalyst": the live
    earnings_calendar covers the symbols the refresh job tracks, which is far
    narrower than the flow corpus.
    """
    nxt = next_earnings_date(symbol, session, db_path=db_path)
    covered = bool(nxt) or bool(last_earnings_date(symbol, session, db_path=db_path))
    if not nxt:
        return None, covered, REGIME_NO_CATALYST
    days = (_date.fromisoformat(nxt) - _date.fromisoformat(session)).days
    sessions = int(np.busday_count(session, nxt))
    regime = (
        REGIME_EARNINGS
        if sessions <= EARNINGS_ADJACENT_MAX_SESSIONS
        else REGIME_NO_CATALYST
    )
    return days, covered, regime


def scan_session(
    *,
    session: str | None = None,
    corpus_db: Path | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    min_fired: int = 1,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """Compute the perception rows for one corpus session (default: the latest).

    Returns rows for every (name, window) with at least ``min_fired`` components in
    the top decile of their peer cell. Non-firing names get no row — their answer
    is "not unusual versus peers this session".
    """
    with _corpus_at(corpus_db):
        universe = _universe()
        if not universe:
            logger.warning("flow perception scan: empty corpus universe")
            return []
        spy_dates, _H, _L, C, V, _spy_c = _bars(universe)
        F, pmax = _flow_features(universe, spy_dates)

    if not spy_dates:
        logger.warning("flow perception scan: corpus has no sessions")
        return []
    target = session or spy_dates[-1]
    if target not in spy_dates:
        raise ValueError(f"session {target} is not in the corpus calendar")
    s = spy_dates.index(target)

    dv = C * V
    quint = _tier_quintiles(C, V)
    cell_q = quint[:, s]
    # A name needs a bar that session and a known liquidity cell — 1.14d counts
    # quintile -1 as ineligible rather than pooling it with real peers.
    eligible = np.isfinite(C[:, s]) & (cell_q >= 0)
    if not eligible.any():
        logger.warning("flow perception scan: no eligible names on %s", target)
        return []

    scan_day = today or datetime.now(_ET).date().isoformat()
    session_age = int(np.busday_count(target, scan_day))
    idx = np.flatnonzero(eligible)
    rows: list[dict[str, Any]] = []
    earnings_cache: dict[str, tuple[int | None, bool, str]] = {}

    for W in WINDOWS:
        values = {c: np.full(len(universe), np.nan) for c in COMPONENT_COLUMNS}
        for i in idx:
            # windows end ON the target session: the registered helpers take the
            # exclusive right edge, so t = s + 1 (the 1.14c/1.14d panel convention).
            wf = _window_features(int(i), s + 1, W, F, pmax)
            vf = _vol_feats(int(i), s + 1, W, dv, F, pmax)
            for comp, val in (
                ("short_dte_share", wf["short_dte_share"]),
                ("concentration", wf["concentration"]),
                ("largest_print_ratio", wf["largest_print_ratio"]),
                ("vol_max_ratio", vf["vol_max_ratio"]),
            ):
                if val is not None:
                    values[comp][i] = val

        ranked = {
            comp: _rank_within_cells(values[comp], cell_q, eligible)
            for comp in COMPONENT_COLUMNS
        }
        n_fired = sum(ranked[comp][1].astype(int) for comp in COMPONENT_COLUMNS)
        for i in np.flatnonzero(n_fired >= max(1, min_fired)):
            symbol = universe[i]
            if symbol not in earnings_cache:
                earnings_cache[symbol] = _earnings_context(
                    symbol, target, db_path=db_path
                )
            days_to_earnings, covered, regime = earnings_cache[symbol]
            fired_names = [c for c in COMPONENT_COLUMNS if ranked[c][1][i]]
            row: dict[str, Any] = {
                "session": target,
                "symbol": symbol,
                "window": W,
                "quintile": int(cell_q[i]),
                "n_fired": int(n_fired[i]),
                "fired_components": ",".join(fired_names),
                "days_to_earnings": days_to_earnings,
                "earnings_coverage": int(covered),
                "regime": regime,
                "session_age_days": session_age,
                "disclaimer": DISCLAIMER,
            }
            for comp in COMPONENT_COLUMNS:
                rank, fired = ranked[comp]
                val = values[comp][i]
                row[comp] = round(float(val), 6) if np.isfinite(val) else None
                row[f"{comp}_rank"] = (
                    round(float(rank[i]), 1) if np.isfinite(rank[i]) else None
                )
                row[f"{comp}_fired"] = int(fired[i])
            rows.append(row)
    return rows


def run_scan(
    *,
    session: str | None = None,
    corpus_db: Path | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    persist: bool = True,
    today: str | None = None,
) -> dict[str, Any]:
    """Scan a session and (by default) persist its rows. Returns a summary."""
    rows = scan_session(
        session=session, corpus_db=corpus_db, db_path=db_path, today=today
    )
    written = upsert_rows(rows, db_path=db_path) if persist and rows else 0
    sessions = sorted({r["session"] for r in rows})
    return {
        "session": sessions[0] if sessions else session,
        "rows": len(rows),
        "written": written,
        "symbols": len({r["symbol"] for r in rows}),
        "alert_candidates": len({r["symbol"] for r in rows if r["n_fired"] >= 2}),
        "session_age_days": rows[0]["session_age_days"] if rows else None,
        "rows_detail": rows,
    }


def _alert_rows(rows: list[dict[str, Any]], min_components: int) -> list[dict[str, Any]]:
    """One row per symbol — the window where the most components fired."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["n_fired"] < min_components:
            continue
        prev = best.get(row["symbol"])
        if prev is None or row["n_fired"] > prev["n_fired"]:
            best[row["symbol"]] = row
    return [best[sym] for sym in sorted(best)]


def emit_alerts(
    rows: list[dict[str, Any]],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    min_components: int = 2,
) -> list[dict[str, Any]]:
    """Inbox cards for names top-decile on >= min_components — OFF by default.

    Gated on ``alerts.flow_perception_alerts_enabled`` (False in DEFAULT_SETTINGS):
    the founder arms this deliberately, like every other new source. Cards go in
    through the grouped upsert (the fusion_stack path), keyed per session+symbol, so
    a re-run of the same session updates its card instead of filing a second one.
    Card feed only — no phone push; the kind is also in notify's INBOX_ONLY_KINDS so
    a future caller routing this through notify() cannot start buzzing the phone by
    accident.
    """
    from config.settings import alerts_config
    from db.notifications import upsert_group_notification

    if not alerts_config().get("flow_perception_alerts_enabled"):
        return []
    sent = []
    for row in _alert_rows(rows, min_components):
        fired = row["fired_components"].replace(",", ", ")
        earnings = (
            f"earnings in {row['days_to_earnings']}d"
            if row["days_to_earnings"] is not None
            else ("no upcoming earnings date known" if row["earnings_coverage"]
                  else "no earnings data for this name")
        )
        body = (
            f"{row['n_fired']} of 4 components in the top decile of its liquidity "
            f"peers over {row['window']} sessions: {fired}. "
            f"{REGIME_NOTE[row['regime']]}; {earnings}. {DISCLAIMER}"
        )
        card = upsert_group_notification(
            group_key=f"flow_perception:{row['session']}:{row['symbol']}",
            kind="flow_perception",
            title=f"{row['symbol']}: unusual flow vs peers ({row['window']}d)",
            body=body,
            ref_type="symbol",
            ref_id=row["symbol"],
            data={
                "symbol": row["symbol"],
                "session": row["session"],
                "window": row["window"],
                "fired_components": row["fired_components"],
                "regime": row["regime"],
                "days_to_earnings": row["days_to_earnings"],
                "session_age_days": row["session_age_days"],
                "disclaimer": DISCLAIMER,
            },
            db_path=db_path,
        )
        if card:
            sent.append(card)
    return sent
