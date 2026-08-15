"""Market-wide daily SIP equity bars — abnormality scanner's daily store.

Same atomicity doctrine as the 5-min store (part→rename, stats JSON last).
Universe is all active Alpaca US equities. Fetches date-range chunks so a
~420-session backfill is hundreds of requests, not tens of thousands.

    python tools/equity_bars_daily_backfill.py --lookback 420
    python tools/equity_bars_daily_backfill.py --since-last
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.equity_bars_5min_backfill import (  # noqa: E402
    AuthError,
    build_manifest,
    classify_universe,
    extend_manifest_sessions,
    fetch_range,
    last_complete_session,
    load_alpaca_universe,
    normalize_trading_base,
    write_session,
)
from trading.market_calendar import last_closed_session, trading_days  # noqa: E402

CHUNK_SESSIONS = 20


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--lookback", type=int, default=None, help="N trading days ending at --end")
    p.add_argument("--since-last", action="store_true")
    p.add_argument("--symbols", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "equity_bars_daily"))
    p.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    p.add_argument("--trading-base", default=None)
    p.add_argument("--chunk", type=int, default=CHUNK_SESSIONS)
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
    trading_base = normalize_trading_base(
        a.trading_base or os.environ.get("APCA_API_BASE_URL")
    )
    out_dir = Path(a.out_dir).expanduser()

    if a.symbols:
        universe = sorted({s.strip().upper() for s in a.symbols.split(",") if s.strip()})
    else:
        universe = load_alpaca_universe(headers, trading_base)
    if a.limit:
        universe = universe[: a.limit]
    requested, skipped = classify_universe(universe)

    end = date.fromisoformat(a.end) if a.end else last_closed_session()
    if a.since_last:
        last = last_complete_session(out_dir)
        start = (last + timedelta(days=1)) if last else (end - timedelta(days=600))
    elif a.start:
        start = date.fromisoformat(a.start)
    elif a.lookback:
        # Walk back until we have lookback trading days.
        start = end
        while len(trading_days(start, end)) < a.lookback:
            start -= timedelta(days=7)
        sessions_all = trading_days(start, end)
        start = sessions_all[-a.lookback]
    else:
        start = end - timedelta(days=600)
    sessions = trading_days(start, end)
    print(
        f"{len(universe):,} alpaca symbols -> {len(requested):,} requested, "
        f"{len(skipped):,} skipped; {len(sessions)} sessions {sessions[0] if sessions else '-'}.."
        f"{sessions[-1] if sessions else '-'}",
        flush=True,
    )
    if a.since_last and not sessions:
        print("already current through last closed session", flush=True)
        return

    pending = [s for s in sessions if not (out_dir / f"{s.isoformat()}.json").exists()]
    failed = 0
    for i in range(0, len(pending), a.chunk):
        chunk = pending[i : i + a.chunk]
        t0 = time.time()
        try:
            by_sess, n_req = fetch_range(chunk[0], chunk[-1], requested, headers, timeframe="1Day")
        except AuthError:
            raise
        except Exception as e:
            failed += 1
            print(f"{chunk[0]}..{chunk[-1]}: FAILED — {e}", flush=True)
            continue
        elapsed = time.time() - t0
        per = elapsed / max(len(chunk), 1)
        for s in chunk:
            per_sym = by_sess.get(s, {})
            st = write_session(s, out_dir, requested, per_sym, n_req, per)
            print(
                f"{s}: {st['bars']:,} bars, {st['symbols_with_bars']:,}/"
                f"{st['symbols_requested']:,} symbols, {st['bytes'] / 1e6:.1f} MB",
                flush=True,
            )

    m = build_manifest(
        out_dir,
        universe,
        requested,
        skipped,
        extend_manifest_sessions(out_dir, sessions) if a.since_last else sessions,
        universe_source="alpaca_assets",
    )
    print(
        f"manifest: {m['sessions_complete']}/{m['sessions_expected']} sessions, "
        f"{m['never_resolved_count']:,} never-resolved, {failed} failures",
        flush=True,
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
