"""GTH morning line — z-scored overnight index-option activity, ready by 9:16 ET.

Phase B of the GTH census (BUILD_LOG 2026-08-09). The census measured 260
nights of the 4:00-9:15 ET slice from the npz archive: every night real
(median $130M notional), put-tilted, SPXW+SPX = 98.7%, and 10 nights ≥5σ of
which 9 preceded a |SPY gap| ≥0.9% or an outsized day — CO-OCCURRENCE, never
prediction. This module turns that into a standing morning read: census the
live tape (`gth_tape`), z-score tonight against the trailing history, and hand
the line to the report/perception/widget surfaces.

Honesty invariants (presentation, per connect-the-dots):
  * ACTIVITY only, never direction — no overnight quotes exist on any lane we
    hold (Alpaca option quotes freeze at 16:00 ET; Databento OPRA has no GTH).
  * The z is "how unusual is tonight's total vs the last ~120 nights", robust
    (median/MAD) so one monster night can't inflate its own yardstick. It is
    context for the open, never a signal, a win%, or a prediction.
  * Single-source Alpaca WS: sidecar reconnect gaps undercount. The nightly
    npz REST capture stays ground truth for the 4:00-9:15 slice — re-running
    tools/gth_overnight_census.py rebuilds history from it and supersedes
    live-tape rows with REST truth.

The z window is EXACTLY the census window (4:00-9:15 ET): the archive's floor
is 08:00 UTC, so baselines exist only for that slice. The evening slice
(20:15-4:00) the tape newly captures is reported as `full_night` context but
never z-scored against a history that couldn't see it.
"""

from __future__ import annotations

import collections
import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from signals.options_flow.gth_tape import TAPE_DIR

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "gth_census"
HISTORY_PATH = OUT_DIR / "nightly.json"
MORNING_LINE_PATH = OUT_DIR / "morning_line.json"

WINDOW_START = time(4, 0)   # census floor (npz capture starts 08:00 UTC)
WINDOW_END = time(9, 15)    # GTH close
MACRO_START = time(8, 30)   # 8:30 ET release window → close
TOP_PRINTS = 25

# Trailing baseline: enough nights for a stable median/MAD, short enough to
# track regime drift. Below the floor the z is withheld, never guessed.
BASELINE_WINDOW = 120
BASELINE_MIN = 60
# Phone push threshold — matches the census claim exactly (10/260 nights ≥5σ;
# 9 of 10 preceded a big gap or outsized day). Rare by design: precision over
# recall (founder law). The line itself shows on every surface every morning.
ALERT_Z = 5.0

CAVEATS: tuple[str, ...] = (
    "Activity only, never direction — no overnight quotes exist on any lane "
    "we hold, so there is no buy/sell read and none is implied.",
    "Single-source Alpaca websocket; sidecar reconnect gaps undercount. The "
    "nightly REST capture remains ground truth for the 4:00-9:15 ET slice.",
    "Outlier context is measured co-occurrence, not prediction: 10 of 260 "
    "census nights were >=5 sigma and 9 preceded a |SPY gap| >=0.9% or an "
    "outsized day. Overnight futures moves drive hedging flow drives the gap.",
)


def _ns(session: date, t: time) -> int:
    return int(datetime.combine(session, t, tzinfo=_ET).timestamp() * 1e9)


def _pctile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(int(len(sorted_vals) * q), len(sorted_vals) - 1)]


def load_tape(session: date, *, tape_dir: Path = TAPE_DIR) -> list[dict[str, Any]]:
    """Rows for one night's file; malformed lines (torn writes) are skipped."""
    path = tape_dir / f"{session.isoformat()}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("t"), int):
                rows.append(row)
        except (json.JSONDecodeError, ValueError):
            continue
    return rows


def census_from_rows(rows: list[dict[str, Any]], session: date) -> dict[str, Any]:
    """The census-tool metrics over live-tape rows — SAME shape as a
    nightly.json row (tools/gth_overnight_census.py), so the trailing baseline
    reads npz-derived and live-tape nights interchangeably."""
    lo, hi = _ns(session, WINDOW_START), _ns(session, WINDOW_END)
    on = [r for r in rows if lo <= r["t"] < hi]
    base: dict[str, Any] = {"session": session.isoformat(), "source": "live_tape"}
    full = _totals(rows)
    base["full_night"] = {**full, "evening_notional": round(
        full["notional"] - _totals(on)["notional"], 2)}
    if not on:
        base["prints"] = 0
        return base

    yymmdd = session.strftime("%y%m%d")
    macro_lo = _ns(session, MACRO_START)
    notional = put_usd = zd_usd = macro_usd = 0.0
    sizes: list[float] = []
    per_print: list[tuple[float, dict[str, Any]]] = []
    by_root: dict[str, dict[str, float]] = {}
    codes: collections.Counter[str] = collections.Counter()
    for r in on:
        s = str(r.get("s") or "")
        px = int(r.get("p") or 0) / 100.0
        sz = int(r.get("z") or 0)
        usd = px * sz * 100.0
        notional += usd
        sizes.append(sz)
        per_print.append((usd, r))
        if len(s) > 15:
            root = s[:-15].rstrip("0123456789")
            if s[-9] == "P":
                put_usd += usd
            if s[-15:-9] == yymmdd:
                zd_usd += usd
        else:
            root = s
        if r["t"] >= macro_lo:
            macro_usd += usd
        codes[str(r.get("c") or "")] += 1
        agg = by_root.setdefault(root, {"prints": 0, "contracts": 0, "notional": 0.0})
        agg["prints"] += 1
        agg["contracts"] += sz
        agg["notional"] += usd
    sizes.sort()
    per_print.sort(key=lambda x: -x[0])
    base.update(
        prints=len(on),
        contracts=int(sum(sizes)),
        notional=round(notional, 2),
        put_share_notional=round(put_usd / notional, 4) if notional else 0.0,
        zero_dte_share_notional=round(zd_usd / notional, 4) if notional else 0.0,
        macro_window_share=round(macro_usd / notional, 4) if notional else 0.0,
        size_p50=_pctile(sizes, 0.5),
        size_p99=_pctile(sizes, 0.99),
        size_max=sizes[-1],
        notional_p99=round(_pctile(sorted(u for u, _ in per_print), 0.99), 2),
        by_root={
            k: {"prints": int(v["prints"]), "contracts": int(v["contracts"]),
                "notional": round(v["notional"], 2)}
            for k, v in by_root.items()
        },
        codes=dict(codes.most_common(8)),
        top_prints=[
            {"sym": str(r.get("s") or ""), "size": int(r.get("z") or 0),
             "price": round(int(r.get("p") or 0) / 100.0, 2),
             "notional": round(usd, 0), "cond": str(r.get("c") or ""),
             "et": datetime.fromtimestamp(r["t"] / 1e9, _ET).strftime("%H:%M:%S")}
            for usd, r in per_print[:TOP_PRINTS]
        ],
    )
    return base


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = c = 0
    usd = 0.0
    for r in rows:
        n += 1
        sz = int(r.get("z") or 0)
        c += sz
        usd += int(r.get("p") or 0) / 100.0 * sz * 100.0
    return {"prints": n, "contracts": c, "notional": round(usd, 2)}


def load_history(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def baseline_block(
    tonight: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Robust z for tonight's slice notional vs the trailing baseline.

    median/MAD (sigma = 1.4826*MAD, the normal-consistent scaling) so the
    yardstick ignores its own outliers; withheld (z=None) below BASELINE_MIN
    nights rather than guessed. put_share_pctile is descriptive context: the
    fraction of baseline nights with a put share at or below tonight's.
    """
    session = str(tonight.get("session") or "")
    prior = sorted(
        (
            r for r in history
            if r.get("prints", 0) > 0 and str(r.get("session")) < session
        ),
        key=lambda r: str(r.get("session")),
    )[-BASELINE_WINDOW:]
    vals = sorted(float(r["notional"]) for r in prior)
    n = len(vals)
    out: dict[str, Any] = {"nights": n, "z": None, "median": None, "sigma": None}
    if n < BASELINE_MIN:
        out["note"] = f"baseline building ({n}/{BASELINE_MIN} nights)"
        return out
    med = _pctile(vals, 0.5)
    mad = _pctile(sorted(abs(v - med) for v in vals), 0.5)
    sigma = 1.4826 * mad
    out["median"] = round(med, 2)
    out["sigma"] = round(sigma, 2)
    if sigma > 0 and tonight.get("prints", 0) > 0:
        out["z"] = round((float(tonight["notional"]) - med) / sigma, 2)
    shares = [float(r.get("put_share_notional") or 0.0) for r in prior]
    tonight_share = float(tonight.get("put_share_notional") or 0.0)
    if shares and tonight.get("prints", 0) > 0:
        out["put_share_pctile"] = round(
            sum(1 for s in shares if s <= tonight_share) / len(shares), 2
        )
    return out


def append_history(row: dict[str, Any], path: Path = HISTORY_PATH) -> None:
    """Insert/replace this session's row. Empty nights (prints=0) are NOT
    appended: the census proved every real night has activity, so an empty
    tape is a feed failure and must not drag the baseline."""
    if row.get("prints", 0) <= 0:
        return
    rows = [r for r in load_history(path) if r.get("session") != row.get("session")]
    rows.append(row)
    rows.sort(key=lambda r: str(r.get("session") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tmp.replace(path)


def compute_morning_line(
    session: date,
    *,
    tape_dir: Path = TAPE_DIR,
    history_path: Path = HISTORY_PATH,
) -> dict[str, Any] | None:
    """Census tonight's tape, z it against history, return the line (or None
    when no tape file exists — e.g. the collector was down all night)."""
    rows = load_tape(session, tape_dir=tape_dir)
    if not rows:
        return None
    tonight = census_from_rows(rows, session)
    line: dict[str, Any] = {
        "session": session.isoformat(),
        "generated_at": datetime.now(_ET).isoformat(timespec="seconds"),
        "window_et": "04:00-09:15",
        "slice": tonight,
        "baseline": baseline_block(tonight, load_history(history_path)),
        "caveats": list(CAVEATS),
    }
    return line


def write_morning_line(line: dict[str, Any], path: Path = MORNING_LINE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(line, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_morning_line(path: Path = MORNING_LINE_PATH) -> dict[str, Any] | None:
    try:
        line = json.loads(path.read_text(encoding="utf-8"))
        return line if isinstance(line, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None
