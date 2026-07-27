"""Whole-market Alpaca OPRA condition-code capture, one raw file per session.

Addendum E's label source (design doc §6b). For each corpus session: enumerate
the contracts that actually traded from the tcbbo file, pull their trades from
Alpaca's historical REST endpoint (`c` = OPRA condition code), and write a raw
per-session `.npz` beside the DBN archive. Raw capture is the SOURCE OF TRUTH —
the join and the v6 re-roll consume these files; everything derived is
regenerable.

Hard lessons inherited from the tcbbo backfill, deliberately:
  - RETRIES (3, backoff): the tcbbo pass had none and one blip lost a session
    permanently behind a green-looking log.
  - Atomic completion: write `<date>.npz.part`, rename, then write the stats
    JSON last — a file's existence alone never marks a session done (the
    `size > 0` skip-guard trap, bitten twice).
  - Resumable: a session is skipped iff its stats JSON exists.

    python tools/alpaca_condition_backfill.py --data-dir ~/dev/help_me_trade/data/databento \
        --out-dir ~/dev/help_me_trade/data/alpaca_conditions --env-file ~/dev/help_me_trade/.env
    # one session: add --date 2026-07-24

Sequential, niced by the caller. Never run two copies (same reason as the
ALL_SYMBOLS rule: two concurrent whole-market pulls swamped the box 07-26).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ALPACA_BATCH = 100
PAGE_LIMIT = 10_000
RETRIES = 3
# Full-day window: capture everything Alpaca has for the session, including
# late/extended prints — clipping to RTH is the CONSUMER's job (the probe
# clips; the re-roll matches whatever the tcbbo file holds).
SESSION_START, SESSION_END = "T04:00:00-04:00", "T20:00:00-04:00"


def _defs_symbols(defs_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(sorted instrument_ids, alpaca symbols) for C/P contracts, vectorized."""
    import databento as dbn

    arr = dbn.DBNStore.from_file(defs_path).to_ndarray()
    cls = arr["instrument_class"]
    arr = arr[(cls == b"C") | (cls == b"P")]
    ids = arr["instrument_id"].astype(np.int64)
    order = np.argsort(ids, kind="stable")
    syms = np.char.replace(arr["raw_symbol"].astype("U24"), " ", "")
    return ids[order], syms[order]


def _traded_symbols(tcbbo_path: Path, d_ids: np.ndarray, d_syms: np.ndarray) -> list[str]:
    import databento as dbn

    arr = dbn.DBNStore.from_file(tcbbo_path).to_ndarray()
    pid = np.unique(arr["instrument_id"].astype(np.int64))
    del arr
    idx = np.clip(np.searchsorted(d_ids, pid), 0, len(d_ids) - 1)
    hit = d_ids[idx] == pid
    return sorted(d_syms[idx[hit]].tolist())


def _get(url: str, headers: dict[str, str]) -> dict:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            code = getattr(e, "code", None)
            if code is not None and 400 <= code < 500 and code != 429:
                raise  # a real request error; retrying cannot fix it
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"gave up after {RETRIES} attempts: {last}")


def capture_session(
    session: date, data_dir: Path, out_dir: Path, headers: dict[str, str]
) -> None:
    stats_path = out_dir / f"{session.isoformat()}.json"
    if stats_path.exists():
        return
    tpath = data_dir / "opra_tcbbo" / f"{session.isoformat()}.dbn.zst"
    dpath = data_dir / "opra_defs" / f"{session.isoformat()}.dbn.zst"
    if not tpath.exists() or not dpath.exists():
        print(f"{session}: missing dbn inputs, skipped", flush=True)
        return

    t0 = time.time()
    d_ids, d_syms = _defs_symbols(dpath)
    symbols = _traded_symbols(tpath, d_ids, d_syms)
    print(f"{session}: {len(symbols):,} traded contracts", flush=True)

    sym_list: list[str] = []
    sym_idx_of: dict[str, int] = {}
    parts_t: list[np.ndarray] = []
    parts_c: list[np.ndarray] = []
    parts_s: list[np.ndarray] = []
    parts_i: list[np.ndarray] = []
    parts_x: list[np.ndarray] = []
    parts_cc: list[np.ndarray] = []
    n_req = 0
    start = f"{session}{SESSION_START}"
    end = f"{session}{SESSION_END}"
    for i in range(0, len(symbols), ALPACA_BATCH):
        batch = ",".join(symbols[i : i + ALPACA_BATCH])
        tok = None
        while True:
            q = {"symbols": batch, "start": start, "end": end, "limit": PAGE_LIMIT}
            if tok:
                q["page_token"] = tok
            d = _get(
                "https://data.alpaca.markets/v1beta1/options/trades?"
                + urllib.parse.urlencode(q),
                headers,
            )
            n_req += 1
            for sym, trades in (d.get("trades") or {}).items():
                si = sym_idx_of.get(sym)
                if si is None:
                    si = sym_idx_of[sym] = len(sym_list)
                    sym_list.append(sym)
                n = len(trades)
                if not n:
                    continue
                parts_t.append(
                    np.array(
                        [np.datetime64(t["t"].replace("Z", ""), "ns") for t in trades],
                        dtype="datetime64[ns]",
                    ).astype(np.int64)
                )
                parts_c.append(
                    np.array(
                        [int(round(float(t["p"]) * 100)) for t in trades], dtype=np.int64
                    )
                )
                parts_s.append(np.array([int(t["s"]) for t in trades], dtype=np.int32))
                parts_i.append(np.full(n, si, dtype=np.int32))
                parts_x.append(
                    np.array([str(t.get("x") or "") for t in trades], dtype="U1")
                )
                parts_cc.append(
                    np.array([str(t.get("c") or "") for t in trades], dtype="U1")
                )
            tok = d.get("next_page_token")
            if not tok:
                break

    n_trades = int(sum(len(a) for a in parts_t))
    out_dir.mkdir(parents=True, exist_ok=True)
    part = out_dir / f"{session.isoformat()}.npz.part"
    final = out_dir / f"{session.isoformat()}.npz"
    with open(part, "wb") as fh:
        np.savez_compressed(
            fh,
            t_ns=np.concatenate(parts_t) if parts_t else np.empty(0, np.int64),
            price_cents=np.concatenate(parts_c) if parts_c else np.empty(0, np.int64),
            size=np.concatenate(parts_s) if parts_s else np.empty(0, np.int32),
            sym_idx=np.concatenate(parts_i) if parts_i else np.empty(0, np.int32),
            exchange=np.concatenate(parts_x) if parts_x else np.empty(0, "U1"),
            cond=np.concatenate(parts_cc) if parts_cc else np.empty(0, "U1"),
            symbols=np.array(sym_list, dtype="U24"),
        )
    os.replace(part, final)
    elapsed = time.time() - t0
    # Stats last: its existence is the ONLY completion marker.
    stats = {
        "session": session.isoformat(),
        "contracts_enumerated": len(symbols),
        "contracts_with_trades": len(sym_list),
        "trades": n_trades,
        "requests": n_req,
        "elapsed_s": round(elapsed, 1),
        "bytes": final.stat().st_size,
    }
    stats_path.write_text(json.dumps(stats, indent=1))
    print(f"{session}: {n_trades:,} trades, {n_req} requests, "
          f"{elapsed / 60:.1f} min, {final.stat().st_size / 1e6:.0f} MB", flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="one session; default = all tcbbo sessions")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--env-file", default=None)
    a = p.parse_args()
    if a.env_file:
        for line in Path(a.env_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        raise SystemExit("APCA_API_KEY_ID / APCA_API_SECRET_KEY not set")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
    data_dir = Path(a.data_dir).expanduser()
    out_dir = Path(a.out_dir).expanduser()

    if a.date:
        sessions = [date.fromisoformat(a.date)]
    else:
        sessions = sorted(
            date.fromisoformat(f.name.split(".")[0])
            for f in (data_dir / "opra_tcbbo").glob("*.dbn.zst")
        )
    for s in sessions:
        try:
            capture_session(s, data_dir, out_dir, headers)
        except Exception as e:  # keep the unattended run alive; stats absent = retryable
            print(f"{s}: FAILED — {e}", flush=True)


if __name__ == "__main__":
    main()
