"""GTH tape — persist overnight (Cboe Global Trading Hours) index-option prints.

Phase B of the GTH census (BUILD_LOG 2026-08-09): Databento OPRA carries NO
overnight session (memory `databento-opra-no-gth`), so the Alpaca wildcard
sidecar — already connected 24/7 for condition-code pairing — is the only live
lane that sees the ~60k prints/night of SPX/SPXW/XSP/VIX GTH activity. Until
now those prints fed the matcher, aged out as `alpaca_orphans`, and were gone.
This class tees them to disk so the 9:16 ET morning line
(`signals/options_flow/gth_morning.py`) has a night to measure.

Scope discipline:
  * The matcher path is UNTOUCHED — overnight prints still flow through it and
    expire exactly as before. This is a tee, not a reroute.
  * `offer()` is called for EVERY sidecar print, including the whole-market RTH
    firehose (~6k/s peaks), so the common case must be one integer compare:
    a cached [rth_lo, rth_hi) epoch-ns window rejects in-session prints without
    any datetime work.
  * OPRA runs Saturday industry tests over the PRODUCTION feed (memory
    `opra-saturday-test-sessions`) — a print whose session date is not a
    trading day is dropped and counted, never persisted.
  * Failure is counted and swallowed: the tape must never cost the sidecar a
    print or the matcher an epoch.

Files: data/gth_tape/YYYY-MM-DD.jsonl, keyed by the MORNING session the night
precedes (an evening print at 22:00 ET Sunday lands in Monday's file). Rows:
{"t": epoch_ns, "s": occ, "p": price_cents, "z": size, "c": cond, "x": exch}.
The nightly npz condition capture remains the REST ground truth for the
4:00-9:15 ET slice; this tape adds the 20:15-4:00 evening slice npz never held.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# GTH is 20:15 ET -> 9:15 ET; the margins make the boundary tests forgiving
# (nothing trades 9:15-9:25 or 20:00-20:15, and the census clamps at compute
# time anyway). Everything inside [MORNING_END, EVENING_START) is the RTH world
# this tape ignores.
MORNING_END = time(9, 25)
EVENING_START = time(20, 0)
# Bounds the buffer if the flusher dies (disk full): drop-oldest, counted.
BUFFER_MAX = 50_000

TAPE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "gth_tape"


class GthTape:
    """Buffered JSONL persist of GTH-window prints. Thread-safe: `offer` runs on
    the collector's asyncio loop, `flush` on the sidecar's 1s sweep executor."""

    def __init__(self, tape_dir: Path = TAPE_DIR) -> None:
        self._dir = tape_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._buf: list[tuple[str, str]] = []  # (session_iso, json_line)
        # Cached in-session interval: prints inside it are RTH, rejected on one
        # compare. Starts empty so the first print takes the slow path.
        self._rth_lo = 0
        self._rth_hi = 0
        self._trading_day_memo: dict[date, bool] = {}
        self.written = 0
        self.dropped_non_session = 0
        self.dropped_overflow = 0
        self.errors = 0

    # --- ingest -------------------------------------------------------------

    def offer(self, sym: str, t_ns: int, cents: int, size: int,
              cond: str, exch: str) -> bool:
        """Buffer the print if it falls in the GTH window; never raises."""
        try:
            if self._rth_lo <= t_ns < self._rth_hi:
                return False
            dt = datetime.fromtimestamp(t_ns / 1e9, _ET)
            tod = dt.time()
            if MORNING_END <= tod < EVENING_START:
                # RTH world: refresh the fast-reject window for this ET day.
                lo = datetime.combine(dt.date(), MORNING_END, tzinfo=_ET)
                hi = datetime.combine(dt.date(), EVENING_START, tzinfo=_ET)
                self._rth_lo = int(lo.timestamp() * 1e9)
                self._rth_hi = int(hi.timestamp() * 1e9)
                return False
            session = dt.date() if tod < MORNING_END else dt.date() + timedelta(days=1)
            if not self._is_trading_day(session):
                self.dropped_non_session += 1
                return False
            line = json.dumps(
                {"t": t_ns, "s": sym, "p": cents, "z": size, "c": cond, "x": exch},
                separators=(",", ":"),
            )
            with self._lock:
                if len(self._buf) >= BUFFER_MAX:
                    self._buf.pop(0)
                    self.dropped_overflow += 1
                self._buf.append((session.isoformat(), line))
            return True
        except Exception:
            self.errors += 1
            if self.errors == 1:
                logger.exception("gth tape: offer raised — counted, sidecar fine")
            return False

    def _is_trading_day(self, session: date) -> bool:
        cached = self._trading_day_memo.get(session)
        if cached is None:
            from trading.market_calendar import is_trading_day

            cached = bool(is_trading_day(session))
            self._trading_day_memo.clear()  # one night in flight at a time
            self._trading_day_memo[session] = cached
        return cached

    # --- persist ------------------------------------------------------------

    def flush(self) -> None:
        """Append buffered lines to their session files; never raises."""
        with self._lock:
            if not self._buf:
                return
            buf, self._buf = self._buf, []
        try:
            by_session: dict[str, list[str]] = {}
            for session_iso, line in buf:
                by_session.setdefault(session_iso, []).append(line)
            for session_iso, lines in by_session.items():
                path = self._dir / f"{session_iso}.jsonl"
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            self.written += len(buf)
        except Exception:
            self.errors += 1
            if self.errors == 1:
                logger.exception("gth tape: flush failed — prints lost, counted")

    def stats(self) -> dict[str, int]:
        return {
            "written": self.written,
            "dropped_non_session": self.dropped_non_session,
            "dropped_overflow": self.dropped_overflow,
            "errors": self.errors,
        }
