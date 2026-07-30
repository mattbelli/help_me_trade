"""Alpaca SIP 5-minute RTH equity bars for the flow corpus universe — 1.14f's outcome store.

Registered in `References/FLOW_ROADMAP.md` §1.14f ("the intraday screen"): the
store is REGISTERED-then-fetched, so this tool implements the spec literally and
does not get to improvise. What the registration fixes: SIP feed, 5-minute bars,
RTH only (09:30-16:00 ET), split-adjusted, the full corpus span in one pull, and
**NA accounting that is disclosed rather than silently dropped**.

Universe = `SELECT DISTINCT underlying FROM flow_rollup`. That is a slight
SUPERSET of the registered "every underlying with >=1 RTH print" (the rollup also
carries names whose only prints were non-RTH, and roots that appear across the
251 sessions rather than in one defs file) — 6,998 rollup roots vs the 07-24
measurement of 4,503 active names of 6,294 defs. A superset is the safe error
direction: no registered name can go missing, and the extras are accounted for as
skips or as never-resolved NAs.

Roots that cannot resolve on Alpaca's *stock* endpoints are recorded in the skip
manifest with a reason rather than requested:
  - cash-index roots (SPX, VIX, NDX, ...) — no equity of that name exists;
  - digit-suffixed adjusted roots (ABC1, AIV2, ...) — non-standard deliverables.
Anything else that never returns a bar lands in the manifest's `never_resolved`
list, so a wrong entry in the curated index set cannot hide a name: the empty
accounting catches it either way.

Atomicity/resume doctrine inherited from `tools/alpaca_condition_backfill.py`
(learned the hard way there): write `<date>.npz.part` then rename, and write the
per-session stats JSON LAST — its existence is the ONLY completion marker, so a
half-written session is always retryable and never looks complete.

    python tools/equity_bars_5min_backfill.py            # full span, one pull
    python tools/equity_bars_5min_backfill.py --limit 5 --start 2025-07-28 --end 2025-07-30

Sequential and self-paced under 200 req/min. Never run two copies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
SYMBOL_BATCH = 100
PAGE_LIMIT = 10_000
RETRIES = 5
MAX_RPM = 180  # the registration's "comfortably under 200/min"
ET = ZoneInfo("America/New_York")
RTH_OPEN, RTH_CLOSE = dtime(9, 30), dtime(16, 0)

# Cash-index roots present in this corpus's `flow_rollup` (verified against the
# 6,998-root universe, 2026-07-30 — not a guessed list). Missing entries are not
# a correctness problem: they cost one wasted slot in a batch and then appear in
# the manifest's `never_resolved` list.
INDEX_ROOTS = frozenset(
    """BKX DJIA DJX HGX MRUT MXEA MXEF NANOS NDX NDXP OEX OSX RUT RUTW SPX SPXW
    UTY VIX VIXW XAU XEO XND XSP""".split()
)
ADJUSTED_ROOT_RE = re.compile(r"\d$")

_last_request = 0.0


class AuthError(RuntimeError):
    """401/403 — bad keys. Fatal: the alternative is a silently empty store."""


def _get(url: str, headers: dict[str, str]) -> dict:
    """One paced GET with exponential backoff on 429/5xx. 401/403 is fatal."""
    global _last_request
    last: Exception | None = None
    for attempt in range(RETRIES):
        gap = 60.0 / MAX_RPM - (time.monotonic() - _last_request)
        if gap > 0:
            time.sleep(gap)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            code = getattr(e, "code", None)
            if code in (401, 403):
                raise AuthError(f"Alpaca rejected the credentials ({code})") from e
            if code is not None and 400 <= code < 500 and code != 429:
                raise  # a real request error; retrying cannot fix it
            last = e
            if attempt < RETRIES - 1:
                time.sleep(2.0**attempt)
    raise RuntimeError(f"gave up after {RETRIES} attempts: {last}")


def classify_universe(symbols: list[str]) -> tuple[list[str], dict[str, str]]:
    """(symbols to request, {skipped symbol: reason}) — the NA accounting starts here."""
    fetch: list[str] = []
    skipped: dict[str, str] = {}
    for s in symbols:
        if s in INDEX_ROOTS:
            skipped[s] = "index_root"
        elif ADJUSTED_ROOT_RE.search(s):
            skipped[s] = "adjusted_root"
        else:
            fetch.append(s)
    return fetch, skipped


def _rth_window(session: date) -> tuple[datetime, datetime]:
    """RTH bounds as tz-aware ET datetimes — zoneinfo so the corpus's DST change is right."""
    return (
        datetime.combine(session, RTH_OPEN, ET),
        datetime.combine(session, RTH_CLOSE, ET),
    )


def fetch_session(
    session: date, symbols: list[str], headers: dict[str, str]
) -> tuple[dict[str, list[tuple[np.ndarray, ...]]], int]:
    """All RTH 5-min bars for `symbols` on `session`, batched + paginated.

    The request window is already RTH, but bars are ALSO filtered client-side to
    [09:30, 16:00) ET — `end` inclusivity would otherwise admit the 16:00 bar,
    which covers after-hours.
    """
    lo, hi = _rth_window(session)
    lo_ns, hi_ns = int(lo.timestamp()) * 1_000_000_000, int(hi.timestamp()) * 1_000_000_000
    per_sym: dict[str, list[tuple[np.ndarray, ...]]] = {}
    n_req = 0

    for i in range(0, len(symbols), SYMBOL_BATCH):
        batch = ",".join(symbols[i : i + SYMBOL_BATCH])
        token = None
        while True:
            q = {
                "symbols": batch,
                "timeframe": "5Min",
                "start": lo.isoformat(),
                "end": hi.isoformat(),
                "limit": PAGE_LIMIT,
                "feed": "sip",
                "adjustment": "split",
            }
            if token:
                q["page_token"] = token
            d = _get(f"{BARS_URL}?{urllib.parse.urlencode(q)}", headers)
            n_req += 1
            for sym, bars in (d.get("bars") or {}).items():
                if not bars:
                    continue
                t = np.array(
                    [np.datetime64(b["t"].replace("Z", ""), "ns") for b in bars],
                    dtype="datetime64[ns]",
                ).astype(np.int64)
                keep = (t >= lo_ns) & (t < hi_ns)
                if not keep.any():
                    continue
                per_sym.setdefault(sym, []).append(
                    (
                        t[keep],
                        np.array([b["o"] for b in bars], np.float64)[keep],
                        np.array([b["h"] for b in bars], np.float64)[keep],
                        np.array([b["l"] for b in bars], np.float64)[keep],
                        np.array([b["c"] for b in bars], np.float64)[keep],
                        np.array([b["v"] for b in bars], np.int64)[keep],
                    )
                )
            token = d.get("next_page_token")
            if not token:
                break
    return per_sym, n_req


def write_session(
    session: date,
    out_dir: Path,
    requested: list[str],
    per_sym: dict[str, list[tuple[np.ndarray, ...]]],
    n_req: int,
    elapsed: float,
) -> dict:
    """npz (temp -> rename) then the stats JSON last. Layout mirrors data/alpaca_conditions:
    a `symbols` index table plus parallel per-bar arrays addressed by `sym_idx`.
    Rows are grouped by symbol (symbols sorted) and time-ordered within a symbol.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    syms = sorted(per_sym)
    cols: list[list[np.ndarray]] = [[] for _ in range(6)]
    idx: list[np.ndarray] = []
    for si, sym in enumerate(syms):
        for part in per_sym[sym]:
            for c, arr in zip(cols, part):
                c.append(arr)
            idx.append(np.full(len(part[0]), si, dtype=np.int32))

    def _cat(parts: list[np.ndarray], dtype) -> np.ndarray:
        return np.concatenate(parts) if parts else np.empty(0, dtype)

    part_path = out_dir / f"{session.isoformat()}.npz.part"
    final = out_dir / f"{session.isoformat()}.npz"
    with open(part_path, "wb") as fh:
        np.savez_compressed(
            fh,
            t_ns=_cat(cols[0], np.int64),
            open=_cat(cols[1], np.float64),
            high=_cat(cols[2], np.float64),
            low=_cat(cols[3], np.float64),
            close=_cat(cols[4], np.float64),
            volume=_cat(cols[5], np.int64),
            sym_idx=_cat(idx, np.int32),
            symbols=np.array(syms, dtype="U8"),
        )
    os.replace(part_path, final)

    empty = sorted(set(requested) - set(syms))
    stats = {
        "session": session.isoformat(),
        "symbols_requested": len(requested),
        "symbols_with_bars": len(syms),
        "bars": int(sum(len(a) for a in cols[0])),
        "requests": n_req,
        "elapsed_s": round(elapsed, 1),
        "bytes": final.stat().st_size,
        "empty_symbols": empty,
    }
    # Stats last: its existence is the ONLY completion marker.
    (out_dir / f"{session.isoformat()}.json").write_text(json.dumps(stats, indent=1))
    return stats


def backfill_session(
    session: date, symbols: list[str], out_dir: Path, headers: dict[str, str]
) -> None:
    if (out_dir / f"{session.isoformat()}.json").exists():
        return
    t0 = time.time()
    per_sym, n_req = fetch_session(session, symbols, headers)
    st = write_session(session, out_dir, symbols, per_sym, n_req, time.time() - t0)
    print(
        f"{session}: {st['bars']:,} bars, {st['symbols_with_bars']:,}/{st['symbols_requested']:,} "
        f"symbols, {st['requests']} requests, {st['elapsed_s'] / 60:.1f} min, "
        f"{st['bytes'] / 1e6:.1f} MB",
        flush=True,
    )


def build_manifest(
    out_dir: Path, universe: list[str], requested: list[str], skipped: dict[str, str],
    sessions: list[date],
) -> dict:
    """Rebuilt from the per-session stats on every run, so a resumed run still
    produces a complete, consistent manifest."""
    per_session: dict[str, dict] = {}
    never: set[str] | None = None
    basis = 0
    for s in sessions:
        p = out_dir / f"{s.isoformat()}.json"
        if not p.exists():
            continue
        st = json.loads(p.read_text())
        empty = set(st.pop("empty_symbols", []))
        st.pop("session", None)
        st["symbols_empty"] = len(empty)
        per_session[s.isoformat()] = st
        # A session written against a DIFFERENT universe (a leftover --limit smoke
        # in the same directory) would make the intersection understate the NAs,
        # so only same-universe sessions form the never-resolved basis.
        if st["symbols_requested"] == len(requested):
            basis += 1
            never = empty if never is None else (never & empty)
    never = (never or set()) & set(requested)

    manifest = {
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "spec": "FLOW_ROADMAP.md 1.14f outcome store — Alpaca SIP 5Min RTH, split-adjusted",
        "span": {
            "start": sessions[0].isoformat() if sessions else None,
            "end": sessions[-1].isoformat() if sessions else None,
        },
        "universe": {
            "source": "SELECT DISTINCT underlying FROM flow_rollup (superset of the registered set)",
            "total": len(universe),
            "requested": len(requested),
            "skipped": len(skipped),
        },
        "skipped": dict(sorted(skipped.items())),
        "sessions_expected": len(sessions),
        "sessions_complete": len(per_session),
        "sessions": per_session,
        "never_resolved_count": len(never),
        "never_resolved_basis_sessions": basis,
        # Requested on every completed session and empty on all of them: NA, disclosed.
        "never_resolved": sorted(never),
        "empty_symbols_note": "per-session empty lists live in <session>.json",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def load_universe(db: Path) -> list[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in con.execute("SELECT DISTINCT underlying FROM flow_rollup"))
    finally:
        con.close()


def load_sessions(db: Path, start: str | None, end: str | None) -> list[date]:
    sql = "SELECT date FROM corpus_sessions WHERE date >= ? AND date <= ?"
    args = [start or "0000-01-01", end or "9999-12-31"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sorted(date.fromisoformat(r[0]) for r in con.execute(sql + " ORDER BY date", args))
    finally:
        con.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", default=None, help="first corpus session (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="last corpus session (YYYY-MM-DD)")
    p.add_argument("--symbols", default=None, help="comma-separated override for the DB universe")
    p.add_argument("--limit", type=int, default=None, help="first N universe symbols (smoke)")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "equity_bars_5min"))
    p.add_argument("--corpus-db", default=str(REPO_ROOT / "data" / "flow_corpus.db"))
    p.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    a = p.parse_args()

    env_file = Path(a.env_file).expanduser()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        raise SystemExit("APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}

    db = Path(a.corpus_db).expanduser()
    out_dir = Path(a.out_dir).expanduser()
    universe = (
        sorted({s.strip().upper() for s in a.symbols.split(",") if s.strip()})
        if a.symbols
        else load_universe(db)
    )
    if a.limit:
        universe = universe[: a.limit]
    requested, skipped = classify_universe(universe)
    sessions = load_sessions(db, a.start, a.end)
    print(
        f"{len(universe):,} universe roots -> {len(requested):,} requested, "
        f"{len(skipped):,} skipped; {len(sessions)} sessions",
        flush=True,
    )

    failed = 0
    for s in sessions:
        try:
            backfill_session(s, requested, out_dir, headers)
        except AuthError:
            raise  # bad keys: stop, do not build an empty store
        except Exception as e:  # keep the unattended run alive; no stats file = retryable
            failed += 1
            print(f"{s}: FAILED — {e}", flush=True)

    m = build_manifest(out_dir, universe, requested, skipped, sessions)
    print(
        f"manifest: {m['sessions_complete']}/{m['sessions_expected']} sessions, "
        f"{m['never_resolved_count']:,} never-resolved symbols, {failed} failures",
        flush=True,
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
