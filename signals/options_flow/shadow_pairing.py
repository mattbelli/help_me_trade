"""Live pairing — the streaming matcher fed by both real tapes, enriching in place.

LIVE_FLOW_PAIRING_SPEC_2026-07-28: replay settled everything deterministic about
`StreamingCondMatcher`; Inc 2 ran this class as a metrics-only shadow. Since
2026-08-03 (Inc 3, first step) it also ENRICHES: the spine registers each
normalized print dict via `attach_print`, and when the matcher resolves that
print's condition code the code is stamped onto the dict in place
(`ref["conditions"] = [code]`) — before the ~1s drain for the ~95% of prints
whose twin lands inside the flush tick (measured 07-31: 72% ≤100ms). Detectors
downstream (F-2 spread exclusion, hygiene, ISO sweeps) read `conditions` and
light up with no changes of their own. This class still never writes any table,
and a print whose code arrives after the drain simply ships unlabeled — the
spec's rule: never hold the spine for a label (`metrics_only=True` restores the
pure Inc-2 shadow behavior).

Two threads feed one matcher, which is why the lock exists and lives HERE (the
matcher is documented single-threaded, "the caller owns both"):
  * `on_dbn` runs on the databento client's own thread (dbn_stream's raw-print
    tee — after symbology resolution, BEFORE the meta gate, so the shadow sees
    the same whole-market population the batch join labels).
  * The Alpaca wildcard-trades client runs on the collector's asyncio loop;
    the sweep runs off-loop via `asyncio.to_thread` (the 07-27 stall round was
    exactly this class of sync-on-loop work — a horizon-boundary sweep was
    measured at up to 333ms).

Two hard rules from the 07-28 adversarial review (both were BLOCKERs):
  * `on_dbn` gates on `_stopping` — the tee is bound into the runner for the
    process lifetime, so after a shadow death the databento thread would
    otherwise keep feeding a matcher whose only garbage collector (`_periodic`)
    is gone: a measured 484 B/print leak → collector OOM, the exact harm the
    exit-wait exclusion exists to prevent.
  * The matcher is RESET on every sidecar reconnect. Any Alpaca gap while the
    DBN tee keeps feeding offsets the k-rank of every key that keeps trading,
    and the offset never heals inside the horizon — measured on the real
    class: a 100-print offset turns a hot key into 100% FALSE matches (mispair
    inside TOL_NS, wrong codes counted as matched). The board records 40-48
    Alpaca keepalive flaps per RTH day, so this is a per-session certainty,
    not an edge case. A reset starts a new measurement epoch (counted, logged
    loudly); mixed-epoch numbers would be silently wrong instead.

The DBN print_id passed to the matcher is its arrival `time.time_ns()` — opaque
to the matcher, and it makes enrichment lag free: resolution time minus
print_id IS the wait. The spec's match-first question ("what fraction of twins
land inside the ~1s flush tick?") is answered directly by the ≤1s buckets.
Bucket counts are logged RAW (not just percentages) so successive 60s lines can
be differenced — open vs midday is recoverable from the journal.

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
from typing import Any

import msgpack
import websockets

from signals.options_flow.intraday import is_multileg
from signals.options_flow.live_pairing import StreamingCondMatcher

logger = logging.getLogger(__name__)

OPRA_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/opra"
PX_SCALE = 1e9  # DBN fixed-point price scale (dbn_stream.PX_SCALE)
RECONNECT_BASE_SEC, RECONNECT_MAX_SEC = 5.0, 120.0
# 1s sweeps keep each expiry batch at arrival-rate size (ms of work) instead of
# letting a horizon boundary land a 500k-slot batch in one call.
SWEEP_EVERY_SEC = 1.0
STATS_EVERY_SEC = 60.0
# Wire-lag sample window, cleared at every stats line so the percentiles
# describe the last window, not a rate-dependent slice of the burst.
LAG_SAMPLE_MAX = 8192
# The keepalive-flap board card records ping timeouts every ~60-90s on this
# account since 07-23; a laxer timeout rides out fanout pauses that would
# otherwise force a matcher reset (each reset = a lost measurement epoch).
PING_TIMEOUT_SEC = 60.0
# Enrichment-lag bucket edges (ns). ≤1s is the match-first boundary: the
# pipeline flushes on a ~1s tick, so anything in the first three buckets ships
# pre-matched at zero added latency.
ENRICH_EDGES_NS = (10_000_000, 100_000_000, 1_000_000_000, 10_000_000_000)
ENRICH_LABELS = ("≤10ms", "≤100ms", "≤1s", "≤10s", ">10s")
# Spec open-item #2: the index roots whose live coverage is the question.
INDEX_ROOTS = ("SPX", "SPXW", "XSP", "VIX", "VIXW", "NDX", "NDXP")
# A resolution that lands between `on_dbn` and `attach_print` (inline match —
# the twin was already waiting) is parked here for the attach to collect;
# entries older than this are dropped on the 1s sweep. print_id IS arrival
# time_ns, so age needs no extra bookkeeping. 10s also bounds refs whose code
# resolved but whose print never attached (meta-gate-skipped prints).
EARLY_COND_TTL_NS = 10_000_000_000
# Attached refs live long enough for the late-enrichment repair, then drop
# (not at the matcher's 10-min expiry — that would pin drained prints for
# ~100MB+ across an open burst). 30s covers the measured open-burst lag
# (08-03 bell: wire p95 = 13.7s; 07-14 probe: per-print peak 6–10s) with
# margin; a code later than this is counted in the >10s bucket but no longer
# repairs the stored event.
ATTACH_TTL_NS = 30_000_000_000
# Late-repair queue bound — event ids awaiting a spread_flag UPDATE. Sized far
# above any real day's block+sweep event count; overflow drops oldest, counted.
REPAIR_QUEUE_MAX = 10_000


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
                 ws_url: str = OPRA_WS_URL, metrics_only: bool = False,
                 gth_tape: Any | None = None) -> None:
        self._key = alpaca_key
        self._secret = alpaca_secret
        self._ws_url = ws_url
        # Overnight persist tee (gth_tape.GthTape) — the sidecar is the only
        # lane that sees the GTH session (databento-opra-no-gth), and without
        # this those prints die as matcher orphans. offer()/flush() swallow
        # their own exceptions; a dead tape never costs the matcher.
        self._gth = gth_tape
        self._m = StreamingCondMatcher()
        self._lock = threading.Lock()
        self._stopping = False
        self._connected_once = False
        self.metrics_only = metrics_only
        # print_id → the spine's normalized print dict, awaiting its code.
        # Entries leave on resolution (matched/expired/evicted — the matcher
        # emits ALL of those through _absorb) or wholesale on a matcher reset,
        # so the population is bounded by the matcher's own pending horizon
        # (measured stress peak 226k entries; these are refs, not copies).
        self._attached: dict[int, dict] = {}
        # print_id → code, for the inline-match race (resolved before attach).
        self._early_cond: dict[int, str] = {}
        self.enriched = 0
        self.early_hits = 0
        # Event ids needing spread_flag=1 because a MULTILEG code landed on a
        # print AFTER its event was already emitted. The runner drains this
        # every ~1s cycle and applies the UPDATE — this class still never
        # writes a table itself.
        self._repairs: deque[str] = deque()
        self.repairs_queued = 0
        self.repairs_dropped = 0

        self.dbn_seen = 0
        self.alp_seen = 0
        self.tee_errors = 0
        self.alp_errors = 0
        self.reconnects = 0
        self.matcher_resets = 0
        self.acked_wide = False
        self.enrich_buckets = [0] * len(ENRICH_LABELS)
        self.d_roots: dict[str, int] = {}
        self.a_roots: dict[str, int] = {}
        self._d_lags_ms: deque[float] = deque(maxlen=LAG_SAMPLE_MAX)
        self._a_lags_ms: deque[float] = deque(maxlen=LAG_SAMPLE_MAX)

    # --- ingest (dbn side: called on the databento client thread) -----------

    def on_dbn(self, occ: str, price_raw: int, size: int,
               ts_event: int) -> int | None:
        """dbn_stream's raw-print tee. MUST NOT raise into the spine's callback.

        Returns the print_id (arrival time_ns) so the caller can register the
        normalized print for enrichment via `attach_print`, or None when the
        pairing is stopped/degraded (the caller then simply doesn't attach).

        The `_stopping` gate is load-bearing, not tidiness: the tee stays bound
        into the runner after a shadow death, and an unswept matcher grows at a
        measured 484 B/print until the OOM killer takes the whole collector.
        """
        if self._stopping:
            return None
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
            return arrival
        except Exception:
            self.tee_errors += 1
            if self.tee_errors == 1:
                logger.exception(
                    "pairing shadow: on_dbn raised — shadow degraded, spine fine"
                )
            return None

    def attach_print(self, print_id: int, ref: dict) -> None:
        """Register the spine's normalized print for in-place code stamping.

        Called on the databento client thread right after `normalize_print`,
        with the id `on_dbn` returned for the same record. The common inline
        case — the twin was already waiting when `on_dbn` fed the matcher —
        resolves before this call, so check the parked codes first. Stamping
        assigns a FRESH list (never mutates one a reader may hold): the drain
        thread sees either the old `[]` or the new `[code]`, both coherent.
        """
        if self._stopping or self.metrics_only:
            return
        try:
            with self._lock:
                cond = self._early_cond.pop(print_id, None)
                if cond is not None:
                    ref["conditions"] = [cond] if cond else []
                    self.early_hits += 1
                    self.enriched += 1
                else:
                    self._attached[print_id] = ref
        except Exception:
            self.tee_errors += 1
            if self.tee_errors == 1:
                logger.exception("pairing: attach_print raised — counted")

    # --- ingest (alpaca side: collector asyncio loop) ------------------------

    def on_alpaca(self, sym: str, t_ns: int, cents: int, size: int,
                  cond: str) -> None:
        try:
            arrival = time.time_ns()
            with self._lock:
                self.alp_seen += 1
                self._a_lags_ms.append((arrival - t_ns) / 1e6)
                r = _root(sym)
                self.a_roots[r] = self.a_roots.get(r, 0) + 1
                out = self._m.on_alpaca_trade((sym, cents, size), t_ns, cond,
                                              now_ns=arrival)
                self._absorb(out, arrival)
        except Exception:
            # A raise would unwind into _alpaca_loop's reconnect handler and
            # cost a matcher reset — a whole measurement epoch for one bad frame.
            self.alp_errors += 1
            if self.alp_errors == 1:
                logger.exception("pairing shadow: on_alpaca raised — counted")

    def _absorb(self, resolutions: list, now_ns: int) -> None:
        # Only matches carry a meaningful wait; expiry/eviction are counted by
        # the matcher itself. print_id IS the print's arrival time_ns. Every
        # resolution — matched or not — releases its attached ref: an expired
        # print keeps its `[]` and the dict must not pin it any longer.
        for print_id, cond, why in resolutions:
            ref = self._attached.pop(print_id, None)
            if why != "matched":
                continue
            if not self.metrics_only:
                if ref is not None:
                    ref["conditions"] = [cond] if cond else []
                    self.enriched += 1
                    # The print's event already exists and this code says
                    # package leg → the stored row is wrong; queue the
                    # id-exact repair (applied by the runner within ~1s).
                    ev_id = ref.get("_event_id")
                    if ev_id and cond and is_multileg([cond]):
                        if len(self._repairs) >= REPAIR_QUEUE_MAX:
                            self._repairs.popleft()
                            self.repairs_dropped += 1
                        self._repairs.append(str(ev_id))
                        self.repairs_queued += 1
                else:
                    # Resolved before the spine attached (inline match) — park
                    # the code for attach_print; the sweep TTLs strays (prints
                    # the meta gate later drops never attach at all).
                    self._early_cond[print_id] = cond
            wait = now_ns - print_id
            for i, edge in enumerate(ENRICH_EDGES_NS):
                if wait <= edge:
                    self.enrich_buckets[i] += 1
                    break
            else:
                self.enrich_buckets[-1] += 1

    def drain_repairs(self) -> list[str]:
        """Take (and clear) the queued late-repair event ids. Runner-called."""
        with self._lock:
            if not self._repairs:
                return []
            out = list(self._repairs)
            self._repairs.clear()
            return out

    def _reset_matcher(self) -> None:
        """New measurement epoch after a sidecar gap. See module docstring —
        without this, one reconnect mis-ranks every hot key for the rest of
        the session and the 'matched' number silently stops meaning matched."""
        with self._lock:
            dropped_d, dropped_a = self._m.pending_dbn, self._m.pending_alpaca
            self._m = StreamingCondMatcher()
            self.matcher_resets += 1
            # A fresh matcher will never resolve the old print_ids — release
            # the refs now or they pin drained prints for the process life.
            self._attached.clear()
            self._early_cond.clear()
        logger.warning(
            "pairing shadow: MATCHER RESET #%s after sidecar gap — new epoch; "
            "discarded pending d=%s a=%s (prints during the gap are unmeasured, "
            "never mismatched)",
            self.matcher_resets, f"{dropped_d:,}", f"{dropped_a:,}",
        )

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
            # Ordering matters: _stopping FIRST, so the tee stops feeding the
            # matcher the moment the sweeper is gone (BLOCKER 1 above).
            self._stopping = True
            for t in children:
                t.cancel()
            for t in children:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if self._gth is not None:
                self._gth.flush()
            self._log_stats(final=True)

    def _sweep_once(self) -> None:
        with self._lock:
            now = time.time_ns()
            self._absorb(self._m.sweep(now), now)
            if self._early_cond:
                cutoff = now - EARLY_COND_TTL_NS
                for pid in [p for p in self._early_cond if p < cutoff]:
                    del self._early_cond[pid]
            if self._attached:
                cutoff = now - ATTACH_TTL_NS
                for pid in [p for p in self._attached if p < cutoff]:
                    del self._attached[pid]
        # Outside the matcher lock (the tape has its own); still on the sweep
        # executor thread, so file I/O never lands on the collector's loop.
        if self._gth is not None:
            self._gth.flush()

    async def _periodic(self) -> None:
        last_stats = time.monotonic()
        while not self._stopping:
            await asyncio.sleep(SWEEP_EVERY_SEC)
            # Off-loop: a horizon-boundary sweep was measured at 18-333ms and
            # would stall the 1s flush tick; the lock is a threading.Lock, so
            # an executor thread serializes with the dbn tee just the same.
            await asyncio.to_thread(self._sweep_once)
            if time.monotonic() - last_stats >= STATS_EVERY_SEC:
                last_stats = time.monotonic()
                self._log_stats()

    def _log_stats(self, final: bool = False) -> None:
        # Snapshot under the lock, format and emit OUTSIDE it — the dbn tee
        # must never queue behind a journald write.
        with self._lock:
            m = self._m
            snap = dict(
                matched=m.matched, tol=m.tolerance_rejects, expired=m.expired,
                evicted=m.evicted, late=m.cond_after_expiry,
                orphans=m.alpaca_orphans, pend_d=m.pending_dbn,
                pend_a=m.pending_alpaca, keys_peak=m.keys_peak,
                dbn_seen=self.dbn_seen, alp_seen=self.alp_seen,
                enriched=self.enriched, att_pend=len(self._attached),
                repairs=self.repairs_queued,
                buckets=list(self.enrich_buckets),
                d_lags=sorted(self._d_lags_ms), a_lags=sorted(self._a_lags_ms),
                d_roots_n=len(self.d_roots), a_roots_n=len(self.a_roots),
                idx=[(r, self.d_roots.get(r, 0), self.a_roots.get(r, 0))
                     for r in INDEX_ROOTS],
                top_a=sorted(self.a_roots.items(), key=lambda kv: -kv[1])[:8],
            )
            self._d_lags_ms.clear()
            self._a_lags_ms.clear()
        gth = self._gth.stats() if self._gth is not None else {}
        resolved = snap["matched"] + snap["tol"] + snap["expired"] + snap["evicted"]
        n_match = max(snap["matched"], 1)
        # Raw counts AND cumulative % — raw so successive lines difference into
        # windows (open vs midday), % for the at-a-glance read.
        buckets = " ".join(
            f"{lab}={cnt:,}({cnt / n_match:.1%})"
            for lab, cnt in zip(ENRICH_LABELS, snap["buckets"])
        )
        idx = " ".join(f"{r}={d}/{a}" for r, d, a in snap["idx"])
        logger.info(
            "pairing shadow%s: dbn=%s alp=%s matched=%s (%.1f%% of %s resolved) "
            "enriched=%s att_pend=%s repairs=%s "
            "tol=%s expired=%s evicted=%s late_twin=%s orphans=%s | enrich %s | "
            "wire_lag_ms d p50=%.0f p95=%.0f a p50=%.0f p95=%.0f | pending d=%s "
            "a=%s keys_peak=%s | roots d=%s a=%s idx_d/a: %s | top_a=%s | "
            "gth_w=%s gth_drop=%s | "
            "acked_wide=%s resets=%s tee_err=%s alp_err=%s reconnects=%s "
            "rss=%.0fMB",
            " FINAL" if final else "",
            f"{snap['dbn_seen']:,}", f"{snap['alp_seen']:,}",
            f"{snap['matched']:,}",
            100.0 * snap["matched"] / max(resolved, 1), f"{resolved:,}",
            f"{snap['enriched']:,}", f"{snap['att_pend']:,}",
            f"{snap['repairs']:,}",
            f"{snap['tol']:,}", f"{snap['expired']:,}", f"{snap['evicted']:,}",
            f"{snap['late']:,}", f"{snap['orphans']:,}",
            buckets,
            _pctl(snap["d_lags"], 0.5), _pctl(snap["d_lags"], 0.95),
            _pctl(snap["a_lags"], 0.5), _pctl(snap["a_lags"], 0.95),
            f"{snap['pend_d']:,}", f"{snap['pend_a']:,}",
            f"{snap['keys_peak']:,}",
            f"{snap['d_roots_n']:,}", f"{snap['a_roots_n']:,}", idx,
            " ".join(f"{r}={n:,}" for r, n in snap["top_a"]),
            gth.get("written", 0), gth.get("dropped_non_session", 0),
            self.acked_wide, self.matcher_resets, self.tee_errors,
            self.alp_errors, self.reconnects, _rss_mb(),
        )

    # --- the wildcard codes sidecar (same protocol the recorder probe ran) ----

    async def _alpaca_loop(self) -> None:
        backoff = RECONNECT_BASE_SEC
        while not self._stopping:
            try:
                async with websockets.connect(
                    self._ws_url, max_size=2**23, ping_timeout=PING_TIMEOUT_SEC
                ) as ws:
                    await self._handshake(ws)
                    await ws.send(msgpack.packb(
                        {"action": "subscribe", "trades": ["*"]}))
                    logger.info("pairing shadow: subscribed trades=['*']")
                    # Every reconnect is a gap in the Alpaca tape while the
                    # DBN tee kept feeding — the epoch boundary lives HERE,
                    # after re-subscribe, so gap-fed dbn prints are discarded
                    # rather than mis-ranked (review BLOCKER 2).
                    if self._connected_once:
                        self._reset_matcher()
                    self._connected_once = True
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
                            elif t == "subscription":
                                trades = item.get("trades") or []
                                self.acked_wide = "*" in [str(s) for s in trades]
                                logger.info(
                                    "pairing shadow: subscription ack trades=%s "
                                    "wide=%s", trades, self.acked_wide,
                                )
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
        cond = str(item.get("c") or "")
        self.on_alpaca(sym, t, cents, size, cond)
        if self._gth is not None:
            self._gth.offer(sym, t, cents, size, cond, str(item.get("x") or ""))

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
