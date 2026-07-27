"""DatabentoFlowStream: shape parity with the Alpaca lane, and provenance."""
from __future__ import annotations

import types

from signals.options_flow.dbn_stream import QUOTE_BASIS, DatabentoFlowStream

META = {"occ_symbol": "TSLA260821C00400000", "underlying": "TSLA", "right": "C",
        "strike": 400.0, "expiry": "2026-08-21", "dte": 26}


def _rec(price_1e9: int, bid: int, ask: int, ts_ns: int = 1_784_813_400_000_000_000):
    lvl = types.SimpleNamespace(bid_px=bid, ask_px=ask)
    return types.SimpleNamespace(instrument_id=42, ts_recv=ts_ns, price=price_1e9,
                                 size=7, publisher_id=35, levels=[lvl])


def _stream(sent):
    s = DatabentoFlowStream(key="k", on_prints=sent.extend,
                            get_subscription_set=lambda: ["TSLA"],
                            meta_for=lambda occ: dict(META))
    s._client = types.SimpleNamespace(symbology_map={42: META["occ_symbol"]})
    return s


def test_print_carries_provider_provenance():
    sent: list = []
    s = _stream(sent)
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    batch = s._drain()
    assert len(batch) == 1
    # Provenance must NOT read as the Alpaca lane's stream_quote, or the two
    # classification regimes pool silently in options_flow_events.
    assert batch[0]["quote_basis"] == QUOTE_BASIS == "dbn_nbbo"
    assert batch[0]["occ_symbol"] == META["occ_symbol"]
    assert batch[0]["price"] == 7.7
    assert batch[0]["size"] == 7


def test_the_print_carries_its_own_nbbo():
    """The whole reason this lane exists. The triple is exactly the shape
    `quote_at` returns, because sweep_loop consumes them through one `or`."""
    s = _stream([])
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    bid, ask, q_ts = s._drain()[0]["nbbo"]
    assert (round(bid, 2), round(ask, 2)) == (7.40, 8.10)
    assert q_ts == 1_784_813_400_000  # the print's own ts: zero staleness


def test_undefined_quote_is_not_stored_as_a_price():
    UNDEF = 9223372036854775807
    s = _stream([])
    s._on_record(_rec(7_700_000_000, UNDEF, UNDEF))
    batch = s._drain()
    assert len(batch) == 1  # the print still counts; only the quote is absent
    assert "nbbo" not in batch[0]


def test_two_prints_in_one_millisecond_keep_their_own_books():
    """Why the book rides the print instead of a store keyed by (occ, ts_ms).

    ts_ms truncates nanoseconds, so these two prints are the SAME key. The ring
    this replaced returned 7.40 to BOTH of them (verified against the old
    implementation, 2026-07-27) while stamping quote_basis="dbn_nbbo", a label
    that asserts the print's own NBBO. That is a false provenance label, not
    merely an imprecise one, and no keyed store can fix it — the key cannot
    tell the two prints apart. Same-millisecond repeats on one contract are
    precisely the sweeps this lane exists to classify.
    """
    s = _stream([])
    base = 1_784_813_400_000_000_000
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000, ts_ns=base))
    s._on_record(_rec(9_000_000_000, 8_900_000_000, 9_200_000_000,
                      ts_ns=base + 400_000))  # +0.4ms: same ts_ms
    first, second = s._drain()
    assert first["ts_ms"] == second["ts_ms"]
    assert round(first["nbbo"][0], 2) == 7.40
    assert round(second["nbbo"][0], 2) == 8.90


def test_quote_at_is_inert_because_there_is_no_quote_store():
    """Kept for surface parity and deliberately empty. It must never grow a
    store again: databento.Live dispatches this callback from its OWN thread
    (client.py:78-80), so anything shared with sweep_loop's read is a race —
    the deque that used to live here raised "deque mutated during iteration",
    and sweep_loop's `except` catches that AFTER popleft, discarding the whole
    cycle's prints. It also had no eviction, so it grew all session."""
    s = _stream([])
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    assert s.quote_at(META["occ_symbol"], 1_784_813_400_000) is None
    assert not hasattr(s, "_quote_ring")


def test_ingest_from_another_thread_never_disturbs_a_reader():
    """The failure mode in miniature: producer on databento's thread, consumer
    draining and reading books on ours. Nothing may raise and nothing may be
    lost — with the book on the print, `_pending_lock` is the only shared state
    left and it already covers the handoff."""
    import threading

    s = _stream([])
    errors: list[BaseException] = []
    N = 4000

    def produce():
        try:
            for i in range(N):
                s._on_record(_rec(7_700_000_000, 7_400_000_000 + i, 8_100_000_000))
        except BaseException as e:  # noqa: BLE001 - surfaced via `errors`
            errors.append(e)

    def drained() -> int:
        # quote_at is inert today; calling it here is the regression fence. If a
        # store ever comes back, this read runs against a live writer and the
        # test fails instead of 0.1% of production cycles silently vanishing.
        s.quote_at(META["occ_symbol"], 1_784_813_400_000)
        # counted only when the book reads back intact, so a torn or missing
        # one shows up as a shortfall rather than passing quietly
        return sum(1 for p in s._drain() if p.get("nbbo", (0, 0, 0))[1] == 8.1)

    t = threading.Thread(target=produce)
    t.start()
    seen = 0
    while t.is_alive():
        seen += drained()
    t.join()
    seen += drained()
    assert not errors
    assert seen == N


def test_unresolved_instrument_is_counted_not_crashed():
    s = _stream([])
    s._client = types.SimpleNamespace(symbology_map={})
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    assert s.unresolved_symbols == 1
    assert s._drain() == []


def test_surface_matches_what_sweep_loop_consumes():
    s = _stream([])
    for attr in ("collected_prints", "dropped_prints", "acked_market_wide",
                 "quote_at", "stop", "run"):
        assert hasattr(s, attr), attr
