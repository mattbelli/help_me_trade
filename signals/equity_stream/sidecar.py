"""Python legs beside the Rust equity collector (`collectors/equity_rs`).

The Rust binary owns the socket, the detectors and the writes; two legs of the
old `EquityTapeRunner` need Python and run here instead, off what Rust writes:

* **convergence** — `options_flow.convergence.process_events` over new
  `equity_tape_events` rows on an `ingest_seq` cursor (~1 s behind the insert;
  the flow `backhalf.py` pattern).
* **abnormality** — the judgment half of `AbnormalityDetector` (baselines,
  fire rules, cooldown). The hot half (prints → 5-min bars) lives in Rust
  (`bars.rs`) and lands in `data/equity_bars_5min.db` at every 5-min boundary;
  at each boundary this side waits for the collector's "through_bucket"
  marker, loads the session grid, primes the detector with it and runs the
  same `_run_boundary` the in-process runner ran. Restarts are no longer blind:
  the grid IS the state, no REST prime needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.equity_bars_5min import load_session_grid, through_bucket
from signals.abnormality.detector import AbnormalityDetector
from signals.abnormality.store import SessionGrid
from signals.options_flow.convergence import process_events
from trading.market_calendar import is_trading_day
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)

POLL_S = 1.0
BATCH_LIMIT = 500
_ROW_COLS = (
    "id, ingest_seq, ts, symbol, event_type, exchange, price, size, notional_usd, "
    "direction, direction_basis, conditions, reason, caveat, detail"
)


class EquitySidecar:
    def __init__(
        self,
        *,
        db_path: Path | None = None,
        bars_path: Path | None = None,
        get_broker: Callable[[], Any] | None = None,
        converge: Callable[..., Awaitable[dict[str, int]]] = process_events,
        clock: Callable[[], float] = time.time,
        alpaca_creds: tuple[str, str] | None = None,
    ) -> None:
        self._db = db_path if db_path is not None else DEFAULT_DB_PATH
        self._bars = bars_path
        self._get_broker = get_broker or _alpaca_broker
        self._converge = converge
        self._clock = clock
        self._cursor: int | None = None
        self._stopping = False
        self.processed = 0
        self.abnormality = AbnormalityDetector()
        self._abn_reloaded_session = None
        self._waiting_bucket: int | None = None
        self._creds = alpaca_creds
        self._prime: SessionGrid | None = None  # REST bars for the buckets before the collector started

    def stop(self) -> None:
        self._stopping = True

    # --- convergence -----------------------------------------------------------------

    def _fetch(self) -> list[dict]:
        with _connect(self._db) as conn:
            if self._cursor is None:
                row = conn.execute("SELECT COALESCE(MAX(ingest_seq), 0) FROM equity_tape_events").fetchone()
                self._cursor = int(row[0] or 0)
            rows = conn.execute(
                f"SELECT {_ROW_COLS} FROM equity_tape_events WHERE ingest_seq > ? "
                "ORDER BY ingest_seq ASC LIMIT ?",
                (self._cursor, BATCH_LIMIT),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            ev = dict(r)
            self._cursor = max(self._cursor, int(ev["ingest_seq"]))
            for col in ("conditions", "detail"):
                raw = ev.get(col)
                if isinstance(raw, str) and raw:
                    try:
                        ev[col] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        pass
            out.append(ev)
        return out

    async def once_convergence(self) -> dict[str, int]:
        events = self._fetch()
        counters = {"events": len(events), "convergence": 0, "solo": 0, "pushes": 0}
        if not events:
            return counters
        self.processed += len(events)
        try:
            broker = self._get_broker()
        except Exception:
            logger.warning("equity sidecar: Alpaca broker unavailable")
            return counters
        if broker is None:
            return counters
        now_et = datetime.fromtimestamp(self._clock(), _ET)
        open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
        conv = await self._converge(
            broker,
            events,
            today_et=now_et.date().isoformat(),
            minutes_since_open=now_et.hour * 60 + now_et.minute - open_min,
            db_path=self._db,
        )
        counters.update({k: int(conv.get(k, 0)) for k in ("convergence", "solo", "pushes")})
        if counters["convergence"] or counters["pushes"]:
            logger.info(
                "equity tape: events=%s convergence=%s pushes=%s",
                len(events), counters["convergence"], counters["pushes"],
            )
        return counters

    async def convergence_loop(self, poll_s: float = POLL_S) -> None:
        while not self._stopping:
            try:
                await self.once_convergence()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("equity sidecar: convergence pass failed")
            await asyncio.sleep(poll_s)

    # --- abnormality -----------------------------------------------------------------

    def _load_baselines(self, today) -> None:
        from signals.abnormality.baselines import load_baselines

        try:
            bl = load_baselines()
        except Exception:
            logger.exception("abnormality: failed to load baselines")
            bl = None
        self.abnormality.set_baselines(bl, today)
        self._abn_reloaded_session = today

    def _seed_prior_from_db(self, today) -> None:
        """Rehydrate today's per-symbol fire cooldown (first-fire-per-day is the
        study's object; a restart must not re-fire names that already fired)."""
        from db.abnormality import events_today
        from signals.abnormality.engine import PriorFire

        try:
            rows = events_today(today.isoformat(), db_path=self._db)
        except Exception:
            logger.exception("abnormality: cooldown seed read failed")
            return
        fires: dict[str, PriorFire] = {}
        for r in rows:
            name = str(r["symbol"])
            fire = PriorFire(bucket=int(r["bucket"]), composite=float(r["composite"] or 0.0))
            if name not in fires or fire.bucket > fires[name].bucket:
                fires[name] = fire
        self.abnormality.seed_prior(today, fires)
        if fires:
            logger.info("abnormality: cooldown seeded for %d symbols from today's fires", len(fires))

    def _prime_today(self, today) -> None:
        """Mid-session (re)start: the collector's bar store only has buckets from
        its own start; fetch the morning from Alpaca REST once and use it to fill
        the cells the store lacks (same prime the in-process runner did)."""
        from signals.abnormality.prime import fetch_today_grid

        bl = self.abnormality._baselines
        if bl is None or self._creds is None:
            return
        symbols = [str(s) for s in bl.symbols]
        try:
            self._prime = fetch_today_grid(today, symbols, *self._creds)
        except Exception:
            logger.exception("abnormality: prime fetch failed")
            return
        logger.info("abnormality: primed %d symbols for %s (fills buckets the collector missed)", len(symbols), today)

    def _merged(self, grid: SessionGrid) -> SessionGrid:
        p = self._prime
        if p is None or p.session != grid.session:
            return grid
        symbols = sorted(set(grid.symbols) | set(p.symbols))
        idx = {s: i for i, s in enumerate(symbols)}
        n = len(symbols)
        out = {k: np.full((n, grid.high.shape[1]), np.nan) for k in ("high", "low", "close", "volume")}
        gi = np.array([idx[s] for s in grid.symbols], dtype=np.int64)
        pi = np.array([idx[s] for s in p.symbols], dtype=np.int64)
        for k in out:
            out[k][pi] = getattr(p, k)  # REST first ...
            live = getattr(grid, k)
            cur = out[k][gi]
            out[k][gi] = np.where(np.isnan(live), cur, live)  # ... the collector's own bars win
        return SessionGrid(session=grid.session, symbols=np.array(symbols, dtype="U8"), **out)

    def _persist_abnormality(self) -> tuple[int, int]:
        from db.abnormality import insert_events, upsert_state

        events, state = self.abnormality.take_pending()
        if events:
            insert_events(events, db_path=self._db)
        if state:
            upsert_state(state, db_path=self._db)
        return len(events), len(state)

    def once_abnormality(self, now_et: datetime) -> None:
        """One 1-s tick of the boundary loop (sync; the caller threads it)."""
        today = now_et.date()
        # Reload once pre-open (09:20–09:30) so the new artifact is in before
        # the first boundary; weekends/holidays just reload harmlessly.
        if now_et.hour == 9 and 20 <= now_et.minute < 30 and self._abn_reloaded_session != today:
            self._load_baselines(today)
            self.abnormality.reset_session(today)
        open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
        now_min = now_et.hour * 60 + now_et.minute
        if not (open_min <= now_min < 16 * 60) or not is_trading_day(today):
            return
        if self.abnormality._session != today:
            self.abnormality.reset_session(today)
            self._seed_prior_from_db(today)
            if self._prime is None or self._prime.session != today:
                self._prime_today(today)
        bucket = self.abnormality.boundary_due(now_et)
        if bucket is None:
            return
        # Wait for the collector's boundary write (rows first, marker last).
        done = through_bucket(today, self._bars)
        if done is None or done < bucket:
            if self._waiting_bucket != bucket:
                self._waiting_bucket = bucket
                logger.info("abnormality: bucket %d — waiting for the collector's bars (through=%s)", bucket, done)
            return
        grid = load_session_grid(today, self._bars)
        if grid is None:
            return
        grid = self._merged(grid)
        self.abnormality.prime_from_grid(grid.symbols, grid.high, grid.low, grid.close, grid.volume)
        result = self.abnormality.maybe_boundary(now_et)
        n_ev, n_state = self._persist_abnormality()
        logger.info(
            "abnormality: bucket %d symbols=%d fires=%d state_rows=%d%s",
            bucket, len(grid.symbols), n_ev, n_state,
            "" if result is not None else " (stood down / no baseline overlap)",
        )

    async def abnormality_loop(self) -> None:
        today = datetime.fromtimestamp(self._clock(), _ET).date()
        await asyncio.to_thread(self._load_baselines, today)
        while not self._stopping:
            await asyncio.sleep(1)
            try:
                now_et = datetime.fromtimestamp(self._clock(), _ET)
                await asyncio.to_thread(self.once_abnormality, now_et)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("abnormality: boundary loop failed")


def _alpaca_broker() -> Any:
    from brokers.factory import get_broker

    return get_broker("alpaca")
