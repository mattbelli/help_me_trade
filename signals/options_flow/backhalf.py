"""Back-half sidecar for the Rust spine (`collectors/flow_rs`, stage 3).

The Rust collector writes `options_flow_events` rows exactly as the Python
`sweep_loop` would — minus the three things that need a broker or the rarity
lookup: sweep/block `detail` (Alpaca snapshots + poll OI), `rarity_*` (the
per-name empirical lookup) and the convergence pass. This sidecar does those
off the rows, on an `ingest_seq` cursor, ~1s behind the insert. In the Python
collector the same work happens BEFORE the insert (`runner.sweep_loop`), so the
only visible difference after the swap is `detail`/`rarity_p` landing 1-2s
after the row (checked 2026-08-18: fusion intake reads neither for options
rows; the UI poller and the daily report re-read).

Cursor semantics: start at the newest row, except rows the previous incarnation
left un-enriched in the last `RESTART_LOOKBACK_S` (a deploy restart) are picked
up. Bursts/clusters are skipped — Rust writes their `detail` itself and Python
never enriched or ranked them either.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from db.base import DEFAULT_DB_PATH
from db.base import connect as _connect
from db.flow_rarity import stamp_event
from signals.options_flow.convergence import process_events
from signals.options_flow.runner import enrich_sweep_events
from trading.market_hours import _ET, _SESSION_OPEN

logger = logging.getLogger(__name__)

STAMP_TYPES = ("sweep", "block")
POLL_S = 1.0
BATCH_LIMIT = 500
RESTART_LOOKBACK_S = 600

_ROW_COLS = (
    "id, ingest_seq, ts, underlying, occ_symbol, right, strike, expiry, dte, "
    "otm_pct, event_type, contracts, premium_usd, direction, direction_basis, "
    "spread_flag, n_prints, reason, caveat, detail, coverage, mid_side"
)


class BackHalf:
    """enrich → rarity → convergence, off the rows the spine wrote."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        get_broker: Callable[[], Any] | None = None,
        enrich: Callable[[list[dict], Any], Awaitable[None]] = enrich_sweep_events,
        converge: Callable[..., Awaitable[dict[str, int]]] = process_events,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db_path if db_path is not None else DEFAULT_DB_PATH
        self._get_broker = get_broker or _alpaca_broker
        self._enrich = enrich
        self._converge = converge
        self._clock = clock
        self._cursor: int | None = None
        self._stopping = False
        self.processed = 0

    # --- cursor -----------------------------------------------------------------

    def _initial_cursor(self) -> int:
        since = (
            datetime.fromtimestamp(self._clock(), UTC)
            - timedelta(seconds=RESTART_LOOKBACK_S)
        ).isoformat()
        with _connect(self._db) as conn:
            row = conn.execute(
                """
                SELECT COALESCE(
                    (SELECT MIN(ingest_seq) - 1 FROM options_flow_events
                     WHERE event_type IN ('sweep', 'block') AND detail IS NULL
                       AND rarity_p IS NULL AND created_at >= ?),
                    (SELECT COALESCE(MAX(ingest_seq), 0) FROM options_flow_events)
                )
                """,
                (since,),
            ).fetchone()
        return int(row[0] or 0)

    def _fetch(self) -> list[dict]:
        if self._cursor is None:
            self._cursor = self._initial_cursor()
        with _connect(self._db) as conn:
            rows = conn.execute(
                f"SELECT {_ROW_COLS} FROM options_flow_events "
                "WHERE ingest_seq > ? ORDER BY ingest_seq ASC LIMIT ?",
                (self._cursor, BATCH_LIMIT),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            ev = dict(r)
            self._cursor = max(self._cursor, int(ev["ingest_seq"]))
            if ev.get("event_type") not in STAMP_TYPES:
                continue
            raw = ev.get("detail")
            ev["detail"] = None
            if isinstance(raw, str) and raw:
                try:
                    ev["detail"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
            out.append(ev)
        return out

    # --- one pass -----------------------------------------------------------------

    async def once(self) -> dict[str, int]:
        events = self._fetch()
        counters = {"events": len(events), "convergence": 0, "solo": 0, "pushes": 0}
        if not events:
            return counters

        broker = None
        try:
            broker = self._get_broker()
        except Exception:
            logger.warning("options_flow backhalf: Alpaca broker unavailable")
        if broker is not None:
            await self._enrich(events, broker)

        # Rarity reads the enriched detail (spot → moneyness); insert_event does
        # the same stamp before its write, so the row ends up identical.
        for ev in events:
            try:
                stamp_event(ev, db_path=self._db)
            except Exception:
                logger.debug("options_flow backhalf: rarity stamp failed for %s", ev.get("id"), exc_info=True)

        with _connect(self._db) as conn:
            for ev in events:
                detail = ev.get("detail")
                conn.execute(
                    """
                    UPDATE options_flow_events
                    SET detail = ?,
                        otm_pct = COALESCE(otm_pct, ?),
                        rarity_p = COALESCE(?, rarity_p),
                        rarity_n = COALESCE(?, rarity_n),
                        rarity_cell = COALESCE(?, rarity_cell),
                        rarity_asof = COALESCE(?, rarity_asof)
                    WHERE id = ?
                    """,
                    (
                        json.dumps(detail) if isinstance(detail, dict) else None,
                        ev.get("otm_pct"),
                        ev.get("rarity_p"),
                        ev.get("rarity_n"),
                        ev.get("rarity_cell"),
                        ev.get("rarity_asof"),
                        ev["id"],
                    ),
                )
            conn.commit()
        self.processed += len(events)

        if broker is None:
            return counters
        now_et = datetime.fromtimestamp(self._clock(), _ET)
        open_min = _SESSION_OPEN[0] * 60 + _SESSION_OPEN[1]
        conv = await self._converge(
            broker,
            events,
            today_et=now_et.date().isoformat(),
            # Real minutes (negative pre-market) — same contract as sweep_loop.
            minutes_since_open=now_et.hour * 60 + now_et.minute - open_min,
            db_path=self._db,
        )
        counters.update({k: int(conv.get(k, 0)) for k in ("convergence", "solo", "pushes")})
        logger.info(
            "options_flow backhalf: events=%s convergence=%s solo=%s pushes=%s",
            len(events), counters["convergence"], counters["solo"], counters["pushes"],
        )
        return counters

    async def run(self, poll_s: float = POLL_S) -> None:
        while not self._stopping:
            try:
                await self.once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("options_flow backhalf: pass failed")
            await asyncio.sleep(poll_s)

    def stop(self) -> None:
        self._stopping = True


def _alpaca_broker() -> Any:
    from brokers.factory import get_broker

    return get_broker("alpaca")
