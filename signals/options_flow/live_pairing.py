"""Streaming condition-code matcher — the frozen Addendum E join, live.

Pairs Databento tcbbo prints (direction, no condition code) with Alpaca
wildcard-trade prints (condition code, no quote) as they arrive, using the
SAME frozen semantics as the batch join in `tools.condition_labels`: per
(contract, price_cents, size) key, the k-th occurrence on one tape pairs with
the k-th on the other, accepted when |Δt| ≤ TOL_NS on the shared OPRA
participant timestamp (`ts_event` on the DBN side, `t` on the Alpaca side —
measured offset exactly 0ns). The batch module stays the corpus authority;
this class exists for the intraday lane only and NEVER writes corpus tables
(LIVE_FLOW_PAIRING_SPEC_2026-07-28, "the corpus firewall").

Streaming reproduces the batch pairing exactly when both tapes carry the same
copies of a key (pinned by tests against `match_prints`). Two deliberate,
bounded deviations exist because a live process cannot hold a whole session:

  * A print whose twin has not arrived within `horizon_ns` of WALL-CLOCK time
    resolves to '' (heuristic fallback, same meaning as an unjoined corpus
    print). Its k-slot is tombstoned, not freed, so a late twin cannot shift
    the alignment of every print behind it.
  * A key idle past the horizon is garbage-collected, which restarts its
    k-count from zero on BOTH sides at once. Batch ranks over the whole
    session instead — the pairings only diverge where one tape is missing
    copies inside a GC'd gap, which the nightly live-vs-batch audit measures.

Pure and clock-injected: participant timestamps drive the tolerance check,
the caller's `now_ns` (wall clock live, simulated clock in replay) drives
expiry. No IO, no threads — the caller owns both.
"""

from __future__ import annotations

from collections import deque

from tools.condition_labels import TOL_NS

# 10 minutes — generously above Alpaca's measured open-burst queue lag, far
# above the 100ms match tolerance. A twin later than this was never going to
# pass the tolerance check anyway; holding the slot open longer only costs
# memory.
HORIZON_NS = 600 * 1_000_000_000
# Hard entry cap on prints awaiting a twin. Worst case sized from the spec's
# open-burst budget (~6M); overflow evicts oldest WITH a counter, never
# silently.
MAX_PENDING = 8_000_000

Resolution = tuple[int, str, str]  # (print_id, cond, reason)


class _Key:
    __slots__ = ("d_next", "a_next", "pair_next", "d_pend", "a_pend", "last_ns")

    def __init__(self, now_ns: int) -> None:
        self.d_next = 0  # next k to assign on the DBN side
        self.a_next = 0  # next k to assign on the Alpaca side
        self.pair_next = 0  # next k to pair once both sides have it
        self.d_pend: dict[int, tuple[int, int] | None] = {}  # k -> (print_id, ts) | tombstone
        self.a_pend: dict[int, tuple[str, int]] = {}  # k -> (cond, ts)
        self.last_ns = now_ns


class StreamingCondMatcher:
    """Feed both tapes in arrival order; collect (print_id, cond, reason).

    Reasons: ``matched`` (code adopted), ``tolerance`` (k-th pair beyond
    TOL_NS — '' like the batch join), ``expired`` (twin never came inside the
    horizon), ``evicted`` (pending cap overflow). Every DBN print fed in is
    resolved exactly once; Alpaca prints are fuel, not output.
    """

    def __init__(self, *, horizon_ns: int = HORIZON_NS,
                 max_pending: int = MAX_PENDING) -> None:
        self._horizon_ns = horizon_ns
        self._max_pending = max_pending
        self._keys: dict[tuple, _Key] = {}
        # (arrival now_ns, key, k) per DBN print — FIFO, so expiry and cap
        # eviction both pop from the left. Entries whose slot has since paired
        # are stale and skipped on pop.
        self._expiry: deque[tuple[int, tuple, int]] = deque()
        self._last_gc_ns = 0

        self.matched = 0
        self.tolerance_rejects = 0
        self.expired = 0
        self.evicted = 0  # the spec's dropped_pending
        self.cond_after_expiry = 0  # twin arrived after its slot expired
        self.alpaca_orphans = 0  # conds whose DBN twin never existed (GC'd)
        self.pending_dbn = 0
        self.pending_alpaca = 0
        self.pending_dbn_peak = 0
        self.pending_alpaca_peak = 0
        self.keys_peak = 0

    # --- ingest -------------------------------------------------------------

    def on_dbn_print(self, key: tuple, ts_ns: int, print_id: int,
                     now_ns: int) -> list[Resolution]:
        ks = self._key_state(key, now_ns)
        k = ks.d_next
        ks.d_next += 1
        ks.d_pend[k] = (print_id, ts_ns)
        self.pending_dbn += 1
        if self.pending_dbn > self.pending_dbn_peak:
            self.pending_dbn_peak = self.pending_dbn
        self._expiry.append((now_ns, key, k))
        out = self._pair(ks)
        if self.pending_dbn > self._max_pending:
            out.extend(self._evict_over_cap())
        return out

    def on_alpaca_trade(self, key: tuple, ts_ns: int, cond: str,
                        now_ns: int) -> list[Resolution]:
        ks = self._key_state(key, now_ns)
        k = ks.a_next
        ks.a_next += 1
        ks.a_pend[k] = (cond, ts_ns)
        self.pending_alpaca += 1
        if self.pending_alpaca > self.pending_alpaca_peak:
            self.pending_alpaca_peak = self.pending_alpaca
        return self._pair(ks)

    def sweep(self, now_ns: int) -> list[Resolution]:
        """Expire overdue slots and GC idle keys. Call every few seconds."""
        out: list[Resolution] = []
        cutoff = now_ns - self._horizon_ns
        while self._expiry and self._expiry[0][0] <= cutoff:
            _, key, k = self._expiry.popleft()
            ks = self._keys.get(key)
            if ks is None:
                continue
            entry = ks.d_pend.get(k)
            if entry is None:  # already paired, or already tombstoned
                continue
            ks.d_pend[k] = None  # tombstone: the slot stays consumed
            self.pending_dbn -= 1
            self.expired += 1
            out.append((entry[0], "", "expired"))

        # Idle-key GC, amortized to once per horizon. By the time a key is
        # idle this long every real DBN entry in it has already expired via
        # the deque above (last_ns advances on every arrival), so this drops
        # tombstones and orphaned Alpaca conds.
        if now_ns - self._last_gc_ns >= self._horizon_ns:
            self._last_gc_ns = now_ns
            dead = [k for k, ks in self._keys.items() if ks.last_ns <= cutoff]
            for key in dead:
                ks = self._keys.pop(key)
                for entry in ks.d_pend.values():
                    if entry is not None:  # defensive; see above
                        self.pending_dbn -= 1
                        self.expired += 1
                        out.append((entry[0], "", "expired"))
                n_a = len(ks.a_pend)
                self.alpaca_orphans += n_a
                self.pending_alpaca -= n_a
        return out

    # --- internals ----------------------------------------------------------

    def _key_state(self, key: tuple, now_ns: int) -> _Key:
        ks = self._keys.get(key)
        if ks is None:
            ks = self._keys[key] = _Key(now_ns)
            if len(self._keys) > self.keys_peak:
                self.keys_peak = len(self._keys)
        else:
            ks.last_ns = now_ns
        return ks

    def _pair(self, ks: _Key) -> list[Resolution]:
        out: list[Resolution] = []
        while ks.pair_next < ks.d_next and ks.pair_next < ks.a_next:
            k = ks.pair_next
            ks.pair_next += 1
            entry = ks.d_pend.pop(k)
            cond, a_ts = ks.a_pend.pop(k)
            self.pending_alpaca -= 1
            if entry is None:  # slot expired before the twin arrived
                self.cond_after_expiry += 1
                continue
            print_id, d_ts = entry
            self.pending_dbn -= 1
            if abs(a_ts - d_ts) <= TOL_NS:
                self.matched += 1
                out.append((print_id, cond, "matched"))
            else:
                self.tolerance_rejects += 1
                out.append((print_id, "", "tolerance"))
        return out

    def _evict_over_cap(self) -> list[Resolution]:
        out: list[Resolution] = []
        while self.pending_dbn > self._max_pending and self._expiry:
            _, key, k = self._expiry.popleft()
            ks = self._keys.get(key)
            if ks is None:
                continue
            entry = ks.d_pend.get(k)
            if entry is None:
                continue
            ks.d_pend[k] = None
            self.pending_dbn -= 1
            self.evicted += 1
            out.append((entry[0], "", "evicted"))
        return out
