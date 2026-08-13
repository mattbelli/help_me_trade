"""Per-name empirical rarity for live sweep/block events.

Nightly job reads ``flow_corpus`` (the 16 GB research DB — never on the poll
path) and writes a compact lookup into the app DB. ``insert_event`` stamps
``rarity_p`` / ``rarity_n`` from that lookup.

The object is the fusion-audit rank, at parent-order grain:

    p = (#{session peaks >= this premium} + 1) / (n + 1)

inside ``(underlying, right, dte_bucket, mny_bucket)``, trailing 120 corpus
sessions. ITM is its own bucket so an OTM print is never ranked against
dividend-arb. Names with ``n < 100`` stay **unranked** (NULL), not fake-rare.

The rollup does not retain per-order premiums — only ``premium_max`` per
cell-day — so the reference class is **session peaks**, not individual orders.
Zeros (name traded that day, this cell did not) stay in the denominator so a
quiet cell is honestly rare. No z-score, no pooling across names, no direction,
no win%.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.flow_corpus import DEFAULT_DB_PATH as CORPUS_DB_PATH
from db.flow_corpus import DTE_BUCKETS, INDEX_ROOTS, MNY_BUCKETS, SCHEMA_VERSION

logger = logging.getLogger(__name__)

WINDOW = 120
MIN_N = 100
STAMP_TYPES = frozenset({"sweep", "block"})
ATM_BAND = 0.02
OTM_10 = 0.10

_MNY_LABEL = {
    "itm": "ITM",
    "atm": "ATM",
    "otm2_10": "2-10% OTM",
    "otm10p": ">=10% OTM",
    "no_spot_index": "unbucketed-index",
    "no_spot_missing": "unbucketed",
}
_RIGHT_LABEL = {"C": "call", "P": "put"}


def _db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DEFAULT_DB_PATH


def init_tables(db_path: Path | None = None) -> None:
    resolved = _db_path(db_path)
    with _connect(resolved) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS flow_rarity_names (
                underlying TEXT PRIMARY KEY,
                n INTEGER NOT NULL,
                asof_date TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS flow_rarity_cells (
                underlying TEXT NOT NULL,
                right TEXT NOT NULL,
                dte_bucket TEXT NOT NULL,
                mny_bucket TEXT NOT NULL,
                n INTEGER NOT NULL,
                n_active INTEGER NOT NULL,
                max_usd INTEGER NOT NULL,
                peaks TEXT NOT NULL,
                asof_date TEXT NOT NULL,
                PRIMARY KEY (underlying, right, dte_bucket, mny_bucket)
            );
            CREATE TABLE IF NOT EXISTS flow_rarity_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.commit()


# --- bucket mapping (must match tools/flow_corpus_rollup.py) -----------------


def canon_right(right: Any) -> str | None:
    s = str(right or "").strip().upper()
    if s in ("C", "CALL"):
        return "C"
    if s in ("P", "PUT"):
        return "P"
    return None


def dte_bucket(dte: Any) -> str | None:
    """Corpus DTE buckets: 0-7 / 8-30 / 31-90 / 90+.

    Live detector buckets differ (2-7, 91-10000, DTE<=1 → 'out'). The join is
    against the corpus, so this mapping wins.
    """
    if dte is None:
        return None
    try:
        n = int(dte)
    except (TypeError, ValueError):
        return None
    if n <= 7:
        return "0-7"
    if n <= 30:
        return "8-30"
    if n <= 90:
        return "31-90"
    return "90+"


def mny_bucket(
    *,
    strike: Any,
    spot: Any,
    right: str | None,
    underlying: str,
) -> str | None:
    """Corpus moneyness. ATM band straddles both sides at |K/S-1| < 2%."""
    und = str(underlying or "").strip().upper()
    try:
        k = float(strike)
        s = float(spot) if spot is not None else float("nan")
    except (TypeError, ValueError):
        k, s = 0.0, float("nan")
    if s != s or s <= 0:  # NaN or non-positive
        return "no_spot_index" if und in INDEX_ROOTS else "no_spot_missing"
    if k <= 0 or right not in ("C", "P"):
        return None
    m = k / s - 1.0
    am = abs(m)
    if am < ATM_BAND:
        return "atm"
    otm = m > 0 if right == "C" else m < 0
    if otm and am >= OTM_10:
        return "otm10p"
    if otm:
        return "otm2_10"
    return "itm"


def signed_otm_pct(strike: Any, spot: Any, right: str | None) -> float | None:
    """Percent OTM (positive) / ITM (negative). None if uncomputable."""
    try:
        k = float(strike)
        s = float(spot)
    except (TypeError, ValueError):
        return None
    if k <= 0 or s <= 0 or right not in ("C", "P"):
        return None
    m = k / s - 1.0
    signed = m if right == "C" else -m
    return round(signed * 100.0, 4)


def _spot_from_event(event: dict) -> float | None:
    detail = event.get("detail")
    if not isinstance(detail, dict):
        return None
    raw = detail.get("underlying_price")
    if raw is None:
        return None
    try:
        spot = float(raw)
    except (TypeError, ValueError):
        return None
    return spot if spot > 0 else None


def event_cell(event: dict) -> tuple[str, str, str, str] | None:
    """(underlying, right, dte_bucket, mny_bucket) or None if unmappable."""
    und = str(event.get("underlying") or "").strip().upper()
    right = canon_right(event.get("right"))
    dte = dte_bucket(event.get("dte"))
    if not und or right is None or dte is None:
        return None
    mny = mny_bucket(
        strike=event.get("strike"),
        spot=_spot_from_event(event),
        right=right,
        underlying=und,
    )
    if mny is None:
        return None
    return und, right, dte, mny


# --- rank --------------------------------------------------------------------


def laplace_p(premium: float, peaks: list[int] | tuple[int, ...], n: int) -> float:
    """Empirical rank of ``premium`` among ``n`` session peaks (zeros omitted
    from ``peaks`` but counted in ``n``). Smaller p = rarer. Floor 1/(n+1)."""
    if n < 1 or premium <= 0:
        return 1.0
    # peaks sorted ascending; count of values >= premium, zeros excluded
    # (a zero is not >= a positive premium).
    n_ge = len(peaks) - bisect_left(peaks, int(round(premium)))
    return (n_ge + 1) / (n + 1)


def format_sentence(
    *,
    underlying: str,
    right: str,
    dte_bucket_label: str,
    mny: str,
    p: float,
    n: int,
) -> str:
    pct = max(0.0, (1.0 - p) * 100.0)
    cp = _RIGHT_LABEL.get(right, right)
    mny_s = _MNY_LABEL.get(mny, mny)
    return (
        f"larger than {pct:.0f}% of {underlying} {dte_bucket_label} {mny_s} "
        f"{cp} session peaks, n={n}"
    )


# --- lookup cache (app DB only — never the corpus) ---------------------------

_name_cache: dict[str, dict[str, Any] | None] = {}
_meta_asof: str | None = None
_meta_checked_mono: float = 0.0
_cache_db_key: str | None = None


def clear_cache() -> None:
    global _meta_asof, _meta_checked_mono, _cache_db_key
    _name_cache.clear()
    _meta_asof = None
    _meta_checked_mono = 0.0
    _cache_db_key = None


def _meta(db_path: Path) -> dict[str, str]:
    try:
        with _connect(db_path) as conn:
            rows = conn.execute("SELECT key, value FROM flow_rarity_meta").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r["key"]): str(r["value"]) for r in rows}


def _refresh_cache_if_stale(db_path: Path) -> None:
    global _meta_asof, _meta_checked_mono, _cache_db_key
    key = str(db_path)
    now = time.monotonic()
    if key == _cache_db_key and now - _meta_checked_mono < 30.0:
        return
    asof = _meta(db_path).get("asof_date")
    if key != _cache_db_key or asof != _meta_asof:
        _name_cache.clear()
        _meta_asof = asof
        _cache_db_key = key
    _meta_checked_mono = now


def _load_name(underlying: str, db_path: Path) -> dict[str, Any] | None:
    _refresh_cache_if_stale(db_path)
    cached = _name_cache.get(underlying)
    if underlying in _name_cache:
        return cached
    try:
        return _load_name_unlocked(underlying, db_path)
    except sqlite3.OperationalError:
        _name_cache[underlying] = None
        return None


def _load_name_unlocked(underlying: str, db_path: Path) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT n, asof_date FROM flow_rarity_names WHERE underlying = ?",
            (underlying,),
        ).fetchone()
        if row is None:
            _name_cache[underlying] = None
            return None
        cells: dict[tuple[str, str, str], tuple[int, ...]] = {}
        for c in conn.execute(
            """
            SELECT right, dte_bucket, mny_bucket, peaks
            FROM flow_rarity_cells WHERE underlying = ?
            """,
            (underlying,),
        ):
            try:
                peaks = tuple(int(x) for x in json.loads(c["peaks"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                peaks = ()
            cells[(c["right"], c["dte_bucket"], c["mny_bucket"])] = peaks
    rec = {"n": int(row["n"]), "asof": str(row["asof_date"]), "cells": cells}
    _name_cache[underlying] = rec
    return rec


def lookup_cell(
    underlying: str,
    right: str,
    dte: str,
    mny: str,
    *,
    db_path: Path | None = None,
) -> tuple[int, tuple[int, ...], str] | None:
    """Return ``(n, peaks, asof)`` or None if the name is unranked."""
    rec = _load_name(underlying, _db_path(db_path))
    if rec is None:
        return None
    peaks = rec["cells"].get((right, dte, mny), ())
    return rec["n"], peaks, rec["asof"]


# --- stamp -------------------------------------------------------------------


def stamp_event(event: dict, *, db_path: Path | None = None) -> dict:
    """Mutate ``event`` with rarity fields. No-op for non-parent types.

    Never opens the corpus. Missing lookup → fields stay unset (NULL on insert).
    """
    if str(event.get("event_type") or "") not in STAMP_TYPES:
        return event
    cell = event_cell(event)
    if cell is None:
        return event
    und, right, dte, mny = cell
    spot = _spot_from_event(event)
    if event.get("otm_pct") is None and spot is not None:
        filled = signed_otm_pct(event.get("strike"), spot, right)
        if filled is not None:
            event["otm_pct"] = filled
    found = lookup_cell(und, right, dte, mny, db_path=db_path)
    if found is None:
        return event
    n, peaks, asof = found
    if n < MIN_N:
        return event
    try:
        prem = float(event.get("premium_usd") or 0)
    except (TypeError, ValueError):
        return event
    if prem <= 0:
        return event
    p = laplace_p(prem, peaks, n)
    event["rarity_p"] = round(p, 6)
    event["rarity_n"] = n
    event["rarity_cell"] = f"{right}/{dte}/{mny}"
    event["rarity_asof"] = asof
    event["rarity_sentence"] = format_sentence(
        underlying=und,
        right=right,
        dte_bucket_label=dte,
        mny=mny,
        p=p,
        n=n,
    )
    return event


def attach_sentence(event: dict) -> dict:
    """Derive ``rarity_sentence`` on a row that already has stamped columns."""
    p = event.get("rarity_p")
    n = event.get("rarity_n")
    cell = event.get("rarity_cell")
    und = str(event.get("underlying") or "").strip().upper()
    if p is None or not n or not cell or not und:
        return event
    parts = str(cell).split("/")
    if len(parts) != 3:
        return event
    right, dte, mny = parts
    event["rarity_sentence"] = format_sentence(
        underlying=und,
        right=right,
        dte_bucket_label=dte,
        mny=mny,
        p=float(p),
        n=int(n),
    )
    return event


def stamp_existing(
    *,
    since: str,
    db_path: Path | None = None,
    until: str | None = None,
) -> int:
    """Backfill rarity on already-written sweep/block rows. Returns rows stamped."""
    resolved = _db_path(db_path)
    init_tables(resolved)
    clear_cache()
    clauses = ["event_type IN ('sweep', 'block')", "ts >= ?"]
    params: list[Any] = [since]
    if until is not None:
        clauses.append("ts < ?")
        params.append(until)
    where = " AND ".join(clauses)
    changed = 0
    with _connect(resolved) as conn:
        rows = conn.execute(
            f"""
            SELECT id, underlying, right, strike, dte, otm_pct, event_type,
                   premium_usd, detail
            FROM options_flow_events WHERE {where}
            """,
            params,
        ).fetchall()
        for row in rows:
            ev = {
                "id": row["id"],
                "underlying": row["underlying"],
                "right": row["right"],
                "strike": row["strike"],
                "dte": row["dte"],
                "otm_pct": row["otm_pct"],
                "event_type": row["event_type"],
                "premium_usd": row["premium_usd"],
                "detail": None,
            }
            raw = row["detail"]
            if isinstance(raw, str) and raw:
                try:
                    ev["detail"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    ev["detail"] = None
            stamp_event(ev, db_path=resolved)
            if ev.get("rarity_p") is None:
                continue
            conn.execute(
                """
                UPDATE options_flow_events
                SET rarity_p = ?, rarity_n = ?, rarity_cell = ?, rarity_asof = ?,
                    otm_pct = COALESCE(otm_pct, ?)
                WHERE id = ?
                """,
                (
                    ev["rarity_p"],
                    ev["rarity_n"],
                    ev["rarity_cell"],
                    ev["rarity_asof"],
                    ev.get("otm_pct"),
                    ev["id"],
                ),
            )
            changed += 1
            if changed % 500 == 0:
                conn.commit()
        conn.commit()
    return changed


# --- nightly build -----------------------------------------------------------


def _corpus_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA cache_size = -256000")
    return conn


def window_dates(corpus_path: Path = CORPUS_DB_PATH, window: int = WINDOW) -> list[str]:
    """Oldest → newest, length ``window`` (or fewer if the corpus is short)."""
    with _corpus_ro(corpus_path) as conn:
        rows = conn.execute(
            """
            SELECT date FROM corpus_sessions
            WHERE schema_version = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (SCHEMA_VERSION, window),
        ).fetchall()
    return [str(r["date"]) for r in reversed(rows)]


def build_lookup(
    *,
    corpus_path: Path = CORPUS_DB_PATH,
    db_path: Path | None = None,
    window: int = WINDOW,
    min_n: int = MIN_N,
) -> dict[str, Any]:
    """Scan the corpus per session, replace the app-DB lookup. Returns stats.

    Per-session PK equality (date is first in the rollup key) — never a
    market-wide range scan. The hot path never calls this.
    """
    dates = window_dates(corpus_path, window)
    if not dates:
        raise RuntimeError("flow corpus has no schema_version="
                           f"{SCHEMA_VERSION} sessions")
    asof = dates[-1]
    name_days: dict[str, set[str]] = defaultdict(set)
    cell_day: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(dict)

    with _corpus_ro(corpus_path) as conn:
        for i, date in enumerate(dates, start=1):
            rows = conn.execute(
                """
                SELECT underlying, right, dte_bucket, mny_bucket,
                       MAX(premium_max) AS px
                FROM flow_rollup
                WHERE date = ?
                GROUP BY 1, 2, 3, 4
                """,
                (date,),
            )
            for r in rows:
                und = str(r["underlying"] or "").strip().upper()
                right = str(r["right"] or "")
                dte = str(r["dte_bucket"] or "")
                mny = str(r["mny_bucket"] or "")
                if not und or right not in ("C", "P"):
                    continue
                if dte not in DTE_BUCKETS or mny not in MNY_BUCKETS:
                    continue
                px = r["px"]
                if px is None:
                    continue
                name_days[und].add(date)
                cell_day[(und, right, dte, mny)][date] = int(round(float(px)))
            if i == 1 or i == len(dates) or i % 20 == 0:
                logger.info("flow_rarity build %s/%s %s", i, len(dates), date)

    name_rows: list[tuple[str, int, str]] = []
    skipped = 0
    for und, days in name_days.items():
        n = len(days)
        if n < min_n:
            skipped += 1
            continue
        name_rows.append((und, n, asof))

    by_und: dict[str, list[tuple[str, str, str, dict[str, int]]]] = defaultdict(list)
    for (und, right, dte, mny), daymap in cell_day.items():
        by_und[und].append((right, dte, mny, daymap))
    cell_rows: list[tuple] = []
    for und, n, _asof in name_rows:
        for right, dte, mny, daymap in by_und.get(und, []):
            peaks = sorted(v for v in daymap.values() if v > 0)
            if not peaks:
                continue
            cell_rows.append(
                (
                    und, right, dte, mny, n, len(peaks), peaks[-1],
                    json.dumps(peaks), asof,
                )
            )

    resolved = _db_path(db_path)
    init_tables(resolved)
    with _connect(resolved) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM flow_rarity_names")
        conn.execute("DELETE FROM flow_rarity_cells")
        conn.execute("DELETE FROM flow_rarity_meta")
        conn.executemany(
            "INSERT INTO flow_rarity_names (underlying, n, asof_date) VALUES (?,?,?)",
            name_rows,
        )
        conn.executemany(
            """
            INSERT INTO flow_rarity_cells (
                underlying, right, dte_bucket, mny_bucket, n, n_active,
                max_usd, peaks, asof_date
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            cell_rows,
        )
        meta = {
            "asof_date": asof,
            "window": str(len(dates)),
            "min_n": str(min_n),
            "n_names": str(len(name_rows)),
            "n_cells": str(len(cell_rows)),
            "n_skipped": str(skipped),
            "first_date": dates[0],
        }
        conn.executemany(
            "INSERT INTO flow_rarity_meta (key, value) VALUES (?,?)",
            list(meta.items()),
        )
        conn.commit()
    clear_cache()

    calib = _walk_forward_sample(by_und, name_days, dates, min_n=min_n)
    return {
        "asof_date": asof,
        "window": len(dates),
        "first_date": dates[0],
        "n_names": len(name_rows),
        "n_cells": len(cell_rows),
        "n_skipped": skipped,
        "calibration": calib,
    }


def _walk_forward_sample(
    by_und: dict[str, list[tuple[str, str, str, dict[str, int]]]],
    name_days: dict[str, set[str]],
    dates: list[str],
    *,
    min_n: int,
    sample_cells: int = 400,
    alphas: tuple[float, ...] = (0.20, 0.10, 0.05, 0.01),
) -> dict[str, Any]:
    """Walk-forward Laplace rank on a sample of cells. Realised/claimed at α."""
    hits = {a: 0 for a in alphas}
    total = 0
    used = 0
    for und, n_days in name_days.items():
        if len(n_days) < min_n:
            continue
        for right, dte, mny, daymap in by_und.get(und, []):
            if used >= sample_cells:
                break
            # Dense series over this name's active dates, in window order.
            series = [float(daymap.get(d, 0) or 0) for d in dates if d in n_days]
            if len(series) < min_n + 10:
                continue
            used += 1
            hist: list[int] = []
            for x in series:
                if len(hist) >= min_n:
                    peaks = sorted(v for v in hist if v > 0)
                    p = laplace_p(x, peaks, len(hist))
                    total += 1
                    for a in alphas:
                        if p <= a:
                            hits[a] += 1
                hist.append(int(round(x)))
                if len(hist) > WINDOW:
                    hist.pop(0)
        if used >= sample_cells:
            break
    out: dict[str, Any] = {"n_obs": total, "n_cells": used}
    for a in alphas:
        claimed = a
        realised = (hits[a] / total) if total else None
        ratio = (realised / claimed) if realised is not None and claimed else None
        out[f"a{a:.2f}"] = {
            "realised": None if realised is None else round(realised, 4),
            "claimed": claimed,
            "ratio": None if ratio is None else round(ratio, 3),
        }
    return out


def put_fixture(
    *,
    underlying: str,
    right: str,
    dte: str,
    mny: str,
    n: int,
    peaks: list[int],
    asof: str = "2026-08-11",
    db_path: Path | None = None,
) -> None:
    """Test helper: write one name/cell into the lookup."""
    resolved = _db_path(db_path)
    init_tables(resolved)
    peaks_s = sorted(int(x) for x in peaks if x > 0)
    with _connect(resolved) as conn:
        conn.execute(
            """
            INSERT INTO flow_rarity_names (underlying, n, asof_date)
            VALUES (?,?,?)
            ON CONFLICT(underlying) DO UPDATE SET n=excluded.n, asof_date=excluded.asof_date
            """,
            (underlying, n, asof),
        )
        conn.execute(
            """
            INSERT INTO flow_rarity_cells (
                underlying, right, dte_bucket, mny_bucket, n, n_active,
                max_usd, peaks, asof_date
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(underlying, right, dte_bucket, mny_bucket) DO UPDATE SET
                n=excluded.n, n_active=excluded.n_active, max_usd=excluded.max_usd,
                peaks=excluded.peaks, asof_date=excluded.asof_date
            """,
            (
                underlying, right, dte, mny, n, len(peaks_s),
                peaks_s[-1] if peaks_s else 0, json.dumps(peaks_s), asof,
            ),
        )
        conn.execute(
            """
            INSERT INTO flow_rarity_meta (key, value) VALUES ('asof_date', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (asof,),
        )
        conn.commit()
    clear_cache()
