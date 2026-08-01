"""Daily top-up of the standing open-interest store — both feeds behind data/oi_walls.db.

Both halves were one-shots that fossilized: the SME purchase (2026-07-20) and the OPRA
SPX/SPXW statistics purchase (2026-07-19) each stopped at 2026-07-17, because nothing
refreshed them and a store that stops updating looks exactly like a store with nothing
new. This is the refresh for both.

⚠️ THE TWO FEEDS DO NOT COST THE SAME, and the difference is not a Databento-wide rule —
it is per dataset (measured 2026-07-31/08-01 with `metadata.get_cost`):

  opra  OPRA.PILLAR statistics — **$0.00**, zeroed by the Standard subscription's L0 tier
        (whole market is $0.00 too; scoped to SPX/SPXW it is 31 MB/session billable)
  sme   GLBX.MDP3 statistics  — **metered**: $0.0033/session, whole-era re-pull $0.175,
        and ALL_SYMBOLS 7 days quotes $6.28, which is why the scope stays pinned

So every run quotes its window first and aborts above the feed's --max-cost, and both
stream `get_range`, which pre-checks funds, rather than batch submit, which does not
(2026-07-19: ~$43 billed past credit).

Window: [last stored date for this feed, dataset available end). Starting AT the last
stored date rather than after it re-reads one session per run, which picks up the
exchange's final-settlement revision of a preliminary OI value; rows are INSERT OR
REPLACE on (date, source, root, expiry, cp, strike), so the overlap is free.

Cadence, from measured publish times rather than assumption: SME OI ts_recv clusters at
10:00 ET (the final settlement pass) and 21:00-23:00 ET (the preliminary); OPRA OI lands
06:30-08:44 ET pre-open. A 17:00 UTC run has both for the prior session in either DST
regime. Daily rather than weekly because the index collar re-establishes at the quarterly
roll, and a weekly job can straddle the one session that defines the next quarter's legs.

`oi_daily.date` is the UTC date of ts_recv — for SME's ET-evening preliminary that is the
NEXT date, so read the column as a publish date, never as the session it describes. OPRA
OI is T+1 by nature: day D's record states D-1's closing positions.

⚠️ SCOPE CONTINUITY: the opra feed stays pinned to SPX/SPXW, the roots the purchased
2013-2026 history contains. Widening it (NDX/RUT/VIX/SPY/QQQ/IWM are all free and one
constant away) would silently change the universe mid-series, so any study spanning the
change must be told — widen deliberately, with a dated note, not as a convenience.

Raw DBN is KEPT: the condensed table holds open interest only, while settlement prices
for later study stages exist nowhere else.

  python tools/oi_refresh.py                       # top up every feed
  python tools/oi_refresh.py --feed sme            # one feed
  python tools/oi_refresh.py --start 2026-01-01    # re-read a window (cost is quoted)
  python tools/oi_refresh.py --dry-run             # quote the cost, fetch nothing
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import databento as db
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.oi_condense import condense_file, open_db, parse_opra, parse_sme  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "statistics"
FEEDS = {
    "sme": {
        "dataset": "GLBX.MDP3",
        "symbols": "SME.OPT",
        "parser": parse_sme,
        "out_dir": ROOT / "data" / "databento" / "sme_oi",
        # The whole 10-month era quotes $0.175, so this passes any ordinary top-up and
        # any plausible catch-up, while a full-era re-read has to be asked for.
        "max_cost": 0.05,
        "first_date": date(2025, 11, 18),
        # One request for the whole window: the feed is ~280 KB/session.
        "per_day": False,
    },
    "opra": {
        "dataset": "OPRA.PILLAR",
        # Pinned to the purchased history's roots — see SCOPE CONTINUITY above.
        "symbols": "SPX.OPT,SPXW.OPT",
        "parser": parse_opra,
        "out_dir": ROOT / "data" / "databento" / "opra_index_oi",
        # L0 on this dataset is free; anything above zero means the entitlement changed
        # and a human should look before we spend.
        "max_cost": 0.0,
        "first_date": date(2026, 7, 17),
        # Per-day requests: a 14-day window 504'd at the gateway (measured 2026-08-01)
        # where the same span day-by-day streams fine at ~3.4 MB/session.
        "per_day": True,
    },
}
STYPE = "parent"
DL_ATTEMPTS = 3
DL_BACKOFF_S = 15

logger = logging.getLogger("oi_refresh")


def _last_stored_date(conn, source: str) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM oi_daily WHERE source=?", (source,)).fetchone()
    return row[0] if row and row[0] else None


def _instant(value: str) -> datetime:
    """ISO string -> aware UTC datetime. Dates and timestamps must be compared as
    instants, never as strings: '2026-08-01' sorts BEFORE '2026-08-01T00:00:00Z' while
    naming the same moment, which is how an empty window reached the API as a 422."""
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text if len(text) > 10 else f"{text}T00:00:00+00:00")
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _windows(start: date, end_ts: str, per_day: bool) -> list[tuple[str, str, str]]:
    """(start, end, label) requests covering [start, end_ts]. The final window ends at
    the dataset's exact end so today's records are not left for tomorrow. Empty windows
    are dropped — a dataset whose end is exactly midnight has no data on that date yet."""
    end_day = end_ts[:10]
    if not per_day:
        spans = [(start.isoformat(), end_ts, f"{start.isoformat()}_{end_day}")]
    else:
        spans, d = [], start
        last = date.fromisoformat(end_day)
        while d <= last:
            nxt = d + timedelta(days=1)
            spans.append((d.isoformat(), end_ts if d == last else nxt.isoformat(), d.isoformat()))
            d = nxt
    return [s for s in spans if _instant(s[0]) < _instant(s[1])]


def _download(client: db.Historical, spec: dict, start: str, end: str, part: Path) -> None:
    """Databento streams end prematurely often enough to matter — a 14-day window 504'd
    on the first live run. A truncated DBN is worthless, never resumable, so each attempt
    clears the partial and starts over."""
    for attempt in range(1, DL_ATTEMPTS + 1):
        try:
            client.timeseries.get_range(
                dataset=spec["dataset"], schema=SCHEMA, symbols=spec["symbols"],
                stype_in=STYPE, start=start, end=end, path=str(part))
            return
        except Exception as exc:
            part.unlink(missing_ok=True)
            if attempt == DL_ATTEMPTS:
                raise
            wait = DL_BACKOFF_S * attempt
            logger.warning("download %s->%s attempt %d/%d failed (%s) — retrying in %ds",
                           start, end, attempt, DL_ATTEMPTS, exc, wait)
            time.sleep(wait)


def refresh(
    client: db.Historical, conn, feed: str, *, start_override: date | None,
    max_cost: float | None, dry_run: bool,
) -> None:
    spec = FEEDS[feed]
    last = _last_stored_date(conn, feed)
    start = start_override or (date.fromisoformat(last) if last else spec["first_date"])
    # The dataset's own end, not "today": the API rejects an end past its available range
    # (422 data_end_after_available_end) and that boundary moves intraday. Kept at full
    # timestamp precision — truncating it to a date would drop everything published today
    # and leave the store a permanent day further behind than the T+1 data requires.
    end_ts = str(client.metadata.get_dataset_range(dataset=spec["dataset"])["end"])
    ceiling = spec["max_cost"] if max_cost is None else max_cost
    windows = _windows(start, end_ts, spec["per_day"])
    if not windows:
        logger.info("[%s] nothing new: stored through %s, dataset ends %s", feed, last, end_ts)
        return
    quoted = 0.0
    for w_start, w_end, _ in windows:
        cost = client.metadata.get_cost(
            dataset=spec["dataset"], schema=SCHEMA, symbols=spec["symbols"], stype_in=STYPE,
            start=w_start, end=w_end)
        quoted += cost
        if cost > ceiling:
            raise RuntimeError(
                f"[{feed}] {w_start}: quoted ${cost:.4f} > max-cost ${ceiling:.4f}; aborting run")
    logger.info("[%s] %d window(s) %s -> %s quote $%.4f total (stored through %s)",
                feed, len(windows), start, end_ts, quoted, last)
    if dry_run:
        return

    out_dir = spec["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for w_start, w_end, label in windows:
        # Named by the WINDOW, not the run date: two runs in one UTC day fetch different
        # windows, and an end-date-only name would let the second overwrite the first —
        # silently dropping a session's settlement prices, which live only in the raw.
        raw = out_dir / f"{label}.dbn.zst"
        # A completed past day is re-condensed but never re-fetched; the last window
        # always is, because it ends mid-session and grows. Condensing regardless is what
        # repairs a run that died between download and write.
        if not raw.exists() or (w_start, w_end, label) == windows[-1]:
            part = raw.with_name(raw.name + ".part")
            part.unlink(missing_ok=True)
            _download(client, spec, w_start, w_end, part)
            part.replace(raw)
        total += condense_file(conn, raw, feed, spec["parser"], day=None)
    new_last = _last_stored_date(conn, feed)
    logger.info("[%s] condensed %d rows from %d file(s); stored through %s (was %s)",
                feed, total, len(windows), new_last, last)
    for r in conn.execute(
            "SELECT root, expiry, cp, strike, oi FROM oi_daily"
            " WHERE source=? AND date=? ORDER BY oi DESC LIMIT 5", (feed, new_last)):
        logger.info("  [%s] wall %s %s %s %g: %s", feed, *r[:4], f"{r[4]:,}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--feed", choices=[*FEEDS, "all"], default="all")
    ap.add_argument("--start", type=date.fromisoformat,
                    help="override the resume point (default: the feed's last stored date)")
    ap.add_argument("--max-cost", type=float,
                    help="override the feed's spend ceiling for this run")
    ap.add_argument("--dry-run", action="store_true", help="quote the cost, fetch nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv(ROOT / ".env")
    client = db.Historical(os.environ["DATABENTO_API_KEY"])
    conn = open_db()
    feeds = list(FEEDS) if args.feed == "all" else [args.feed]
    failed = []
    for feed in feeds:
        try:
            refresh(client, conn, feed, start_override=args.start,
                    max_cost=args.max_cost, dry_run=args.dry_run)
        except Exception as exc:
            # One dead feed must not strand the other: a GLBX billing stop should never
            # be the reason the free OPRA half also goes stale.
            logger.error("[%s] FAILED: %s", feed, exc)
            failed.append(feed)
    if failed:
        raise SystemExit(f"feeds failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
