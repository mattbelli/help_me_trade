"""Inc 2 live shadow — the streaming matcher fed by both real tapes, metrics only.

LIVE_FLOW_PAIRING_SPEC_2026-07-28: replay settled everything deterministic about
`StreamingCondMatcher`; this module measures ONLY what replay cannot — real
wire-arrival lag on both sides, live wildcard per-root coverage, enrichment lag
(how long a Databento print waits for its condition code), and RSS through a
real open burst. It changes NOTHING about what the collector emits: the spine's
prints flow exactly as before, and this class never writes any table.

Two threads feed one matcher, which is why the lock exists and lives HERE (the
matcher is documented single-threaded, "the caller owns both"):
  * `on_dbn` runs on the databento client's own thread (dbn_stream's raw-print
    tee — after symbology resolution, BEFORE the meta gate, so the shadow sees
    the same whole-market population the batch join labels).
  * The Alpaca wildcard-trades client and the sweep/stats timer run on the
    collector's asyncio loop.
Per-event work under the lock is the matcher's ~3µs; at the measured 6.6k/s
whole-market peak that is <5% of one core of contention worst-case.

The DBN print_id passed to the matcher is its arrival `time.time_ns()` — opaque
to the matcher, and it makes enrichment lag free: resolution time minus
print_id IS the wait. The spec's match-first question ("what fraction of twins
land inside the ~1s flush tick?") is answered directly by the ≤1s buckets.

Shadow failure must never cost the spine: every entry point swallows its own
exceptions (counted, first one logged), and the collector runs `run()` as a
task EXCLUDED from its FIRST_COMPLETED exit wait.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque

import msgpack
import websockets

from signals.options_flow.live_pairing import StreamingCondMatcher

logger = logging.getLogger(__name__)

OPRA_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
PX_SCALE = 1e9  # DBN fixed-point price scale (dbn_stream.PX_SCALE)
RECONNECT_BASE_SEC, RECONNECT_MAX_SEC = 5.0, 120.0
SWEEP_EVERY_SEC = 5.0
STATS_EVERY_SEC = 60.0
LAG_SAMPLE_MAX = 8192
# Enrichment-lag bucket edges (ns). ≤1s is the match-first boundary: the
# pipeline flushes on a ~1s tick, so anything in the first three buckets ships
# pre-matched at zero added latency.
ENRICH_EDGES_NS = (10_000_000, 100_000_000, 1_000_000_000, 10_000_000_000)
ENRICH_LABELS = ("≤10ms", "≤100ms", "≤1s", "≤10s", ">10s")


def _root(sym: str) -> str:
    # OCC compact = root + YYMMDD + C/P + 8-digit strike: a fixed 15-char tail.
    return sym[:-15] if len(sym) > 15 else sym


def _pctl(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(int(len(sorted_vals) * q), len(sorted_vals) - 1)]


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * 4096 / 1e6
    except Exception:
        return 0.0


class ShadowPairing:
    """Feed both live tapes through the frozen join; log what falls out."""

    def __init__(self, *, alpaca_key: str, alpaca_secret: str,
                 ws_url: str = OPRA_WS_URL) -> None:
        self._key = alpaca_key
        self._secret = alpaca_secret
        self._ws_url = ws_url
        self._m = StreamingCondMatcher()
        self._lock = threading.Lock()
        self._stopping = False

        self.dbn_seen = 0
        self.alp_seen = 0
        self.tee_errors = 0
        self.reconnects = 0
        self.enrich_buckets = [0] * len(ENRICH_LABELS)
        self.d_roots: dict[str, int] = {}
        self.a_roots: dict[str, int] = {}
        self._d_lags_ms: deque[float] = deque(maxlen=LAG_SAMPLE_MAX)
        self._a_lags_ms: deque[float] = deque(maxlen=LAG_SAMPLE_MAX)

    # --- ingest (dbn side: called on the databento client thread) -----------

    def on_dbn(self, occ: str, price_raw: int, size: int, ts_event: int) -> None:
        """dbn_stream's raw-print tee. MUST NOT raise into the spine's callback."""
        try:
            arrival = time.time_ns()
            cents = int(round(price_raw / PX_SCALE * 100))
            key = (occ, cents, int(size))
            with self._lock:
                self.dbn_seen += 1
                self._d_lags_ms.append((arrival - ts_event) / 1e6)
                r = _root(occ)
                self.d_roots[r] = self.d_roots.get(r, 0) + 1
                out = self._m.on_dbn_print(key, ts_event, arrival, now_ns=arrival)
                self._absorb(out, arrival)
        except Exception:
            self.tee_errors += 1
            if self.tee_errors == 1:
                logger.exception(
                    "pairing shadow: on_dbn raised — shadow degraded, spine fine"
                )

    # --- ingest (alpaca side: collector asyncio loop) ------------------------

    def on_alpaca(self, sym: str, t_ns: int, cents: int, size: int,
                  cond: str) -> None:
        arrival = time.time_ns()
        with self._lock:
            self.alp_seen += 1
            self._a_lags_ms.append((arrival - t_ns) / 1e6)
            r = _root(sym)
            self.a_roots[r] = self.a_roots.get(r, 0) + 1
            out = self._m.on_alpaca_trade((sym, cents, size), t_ns, cond,
                                          now_ns=arrival)
            self._absorb(out, arrival)

    def _absorb(self, resolutions: list, now_ns: int) -> None:
        # Only matches carry a meaningful wait; expiry/eviction are counted by
        # the matcher itself. print_id IS the print's arrival time_ns.
        for print_id, _cond, why in resolutions:
            if why != "matched":
                continue
            wait = now_ns - print_id
            for i, edge in enumerate(ENRICH_EDGES_NS):
                if wait <= edge:
                    self.enrich_buckets[i] += 1
                    break
            else:
                self.enrich_buckets[-1] += 1

    # --- lifecycle ------------------------------------------------------------

    def stop(self) -> None:
        self._stopping = True

    async def run(self) -> None:
        logger.info("pairing shadow: starting (matcher + wildcard codes sidecar)")
        # Not gather(): a sibling that raises would leave the other task
        # orphaned — and an orphaned _alpaca_loop keeps HOLDING the OPRA
        # socket after the shadow is reported dead (the next probe 406s with
        # no visible owner). Children die together, whatever happens.
        children = (
            asyncio.create_task(self._alpaca_loop(), name="shadow-alpaca"),
            asyncio.create_task(self._periodic(), name="shadow-periodic"),
        )
        try:
            done, _ = await asyncio.wait(
                children, return_when=asyncio.FIRST_EXCEPTION
            )
            for t in done:
                exc = t.exception()
                if exc is not None:
                    raise exc
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pairing shadow: died — the spine is unaffected")
        finally:
            self._stopping = True
            for t in children:
                t.cancel()
            for t in children:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            self._log_stats(final=True)

    async def _periodic(self) -> None:
        last_stats = time.monotonic()
        while not self._stopping:
            await asyncio.sleep(SWEEP_EVERY_SEC)
            with self._lock:
                self._absorb(self._m.sweep(time.time_ns()), time.time_ns())
            if time.monotonic() - last_stats >= STATS_EVERY_SEC:
                last_stats = time.monotonic()
                self._log_stats()

    def _log_stats(self, final: bool = False) -> None:
        with self._lock:
            m = self._m
            resolved = m.matched + m.tolerance_rejects + m.expired + m.evicted
            n_match = max(m.matched, 1)
            buckets = " ".join(
                f"{lab}={cnt / n_match:.1%}"
                for lab, cnt in zip(ENRICH_LABELS, self.enrich_buckets)
            )
            d_lags = sorted(self._d_lags_ms)
            a_lags = sorted(self._a_lags_ms)
            top_a = sorted(self.a_roots.items(), key=lambda kv: -kv[1])[:8]
            logger.info(
                "pairing shadow%s: dbn=%s alp=%s matched=%s (%.1f%% of %s "
                "resolved) tol=%s expired=%s evicted=%s late_twin=%s orphans=%s "
                "| enrich %s | wire_lag_ms d p50=%.0f p95=%.0f a p50=%.0f "
                "p95=%.0f | pending d=%s a=%s keys_peak=%s | roots d=%s a=%s "
                "top_a=%s | tee_err=%s reconnects=%s rss=%.0fMB",
                " FINAL" if final else "",
                f"{self.dbn_seen:,}", f"{self.alp_seen:,}", f"{m.matched:,}",
                100.0 * m.matched / max(resolved, 1), f"{resolved:,}",
                f"{m.tolerance_rejects:,}", f"{m.expired:,}", f"{m.evicted:,}",
                f"{m.cond_after_expiry:,}", f"{m.alpaca_orphans:,}",
                buckets,
                _pctl(d_lags, 0.5), _pctl(d_lags, 0.95),
                _pctl(a_lags, 0.5), _pctl(a_lags, 0.95),
                f"{m.pending_dbn:,}", f"{m.pending_alpaca:,}",
                f"{m.keys_peak:,}",
                f"{len(self.d_roots):,}", f"{len(self.a_roots):,}",
                " ".join(f"{r}={n:,}" for r, n in top_a),
                self.tee_errors, self.reconnects, _rss_mb(),
            )

    # --- the wildcard codes sidecar (same protocol the recorder probe ran) ----

    async def _alpaca_loop(self) -> None:
        backoff = RECONNECT_BASE_SEC
        while not self._stopping:
            try:
                async with websockets.connect(self._ws_url, max_size=2**23) as ws:
                    await self._handshake(ws)
                    await ws.send(msgpack.packb(
                        {"action": "subscribe", "trades": ["*"]}))
                    logger.info("pairing shadow: subscribed trades=['*']")
                    backoff = RECONNECT_BASE_SEC
                    while not self._stopping:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except TimeoutError:
                            continue
                        for item in self._frames(raw):
                            t = item.get("T")
                            if t == "t":
                                self._on_trade_msg(item)
                            elif t == "error":
                                logger.error("pairing shadow: server error: %s",
                                             item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stopping:
                    break
                self.reconnects += 1
                logger.warning(
                    "pairing shadow: sidecar stream error (%s); reconnect in "
                    "%.0fs — a 406 means another process holds the OPRA socket",
                    exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)

    def _on_trade_msg(self, item: dict) -> None:
        sym = str(item.get("S") or "")
        t = item.get("t")
        if not sym or not isinstance(t, int):  # timestamp=2 → epoch ns
            return
        try:
            cents = int(round(float(item.get("p")) * 100))
            size = int(item.get("s"))
        except (TypeError, ValueError):
            return
        self.on_alpaca(sym, t, cents, size, str(item.get("c") or ""))

    @staticmethod
    def _frames(raw: bytes | str) -> list[dict]:
        if isinstance(raw, str):
            return []
        data = msgpack.unpackb(raw, timestamp=2)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return [data] if isinstance(data, dict) else []

    async def _handshake(self, ws) -> None:
        auth = msgpack.packb(
            {"action": "auth", "key": self._key, "secret": self._secret})
        deadline = time.monotonic() + 12.0
        sent = False
        while time.monotonic() < deadline:
            for item in self._frames(await asyncio.wait_for(ws.recv(),
                                                            timeout=10.0)):
                t, msg = item.get("T"), str(item.get("msg", ""))
                if t == "error":
                    raise RuntimeError(f"code={item.get('code')} {msg}")
                if t == "success" and msg == "authenticated":
                    return
                if t == "success" and msg == "connected" and not sent:
                    await ws.send(auth)
                    sent = True
        raise RuntimeError("no authenticated ack")
