"""DatabentoFlowStream: shape parity with the Alpaca lane, and provenance."""
from __future__ import annotations

import importlib.util
import types

import pytest

from signals.options_flow.dbn_stream import QUOTE_BASIS, DatabentoFlowStream

META = {"occ_symbol": "TSLA260821C00400000", "underlying": "TSLA", "right": "C",
        "strike": 400.0, "expiry": "2026-08-21", "dte": 26}


def _rec(price_1e9: int, bid: int, ask: int, ts_ns: int = 1_784_813_400_000_000_000):
    lvl = types.SimpleNamespace(bid_px=bid, ask_px=ask)
    return types.SimpleNamespace(instrument_id=42, ts_recv=ts_ns, price=price_1e9,
                                 size=7, publisher_id=35, levels=[lvl])


def _stream(sent):
    s = DatabentoFlowStream(key="k", on_prints=sent.extend,
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


def test_osi_padding_is_stripped_or_every_print_is_discarded():
    """The outage of 2026-07-27 13:30, in one assertion.

    databento's OPRA symbology is the 21-char OSI form — a 6-char root
    LEFT-JUSTIFIED AND SPACE-PADDED. `_on_record` did `str(occ).strip()`, which
    leaves the interior padding, so `_OCC_RE` never matched, `meta_for` returned
    None, and every print was dropped as `skipped_no_meta`. Live at the open:
    data_msgs=161,899, prints=0. Measured across 2,043,111 real symbols: 0.00%
    parse as-is, 98.49% parse once the padding is gone."""
    from imports.option_symbol import parse_occ_symbol
    from signals.options_flow.dbn_stream import occ_from_osi

    padded = "IPI   261218P00050000"
    assert parse_occ_symbol(padded.strip().upper()) is None, "the old path"
    assert occ_from_osi(padded) == "IPI261218P00050000"
    assert parse_occ_symbol(occ_from_osi(padded)) is not None


def test_a_padded_symbol_produces_a_print_end_to_end():
    """Driven through `_on_record` with a padded symbol, because the unit above
    would still pass if the call site forgot to use the helper — which is
    exactly the shape of the original bug."""
    sent: list = []
    s = DatabentoFlowStream(key="k", on_prints=sent.extend,
                            meta_for=lambda occ: dict(META, occ_symbol=occ))
    s._client = types.SimpleNamespace(
        symbology_map={42: "TSLA  260821C00400000"}
    )
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    batch = s._drain()
    assert len(batch) == 1, f"print discarded; skipped_no_meta={s.skipped_no_meta}"
    assert batch[0]["occ_symbol"] == "TSLA260821C00400000"
    assert s.skipped_no_meta == 0


def test_unresolved_instrument_is_counted_not_crashed():
    s = _stream([])
    s._client = types.SimpleNamespace(symbology_map={})
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    assert s.unresolved_symbols == 1
    assert s._drain() == []


# --- control plane: conditions that make the tape wrong without stopping it --
#
# ⚠️ These need the real record types, and `databento` is deliberately NOT in
# requirements.txt — `dbn_stream` and `flow_corpus_rollup` both import it lazily
# at their use sites so everything else stays testable without it. CI installs
# only requirements.txt, so CI SKIPS this block; it is covered on any box with
# the package (serv, and anywhere the lane could actually run). Using stubs
# instead would defeat the purpose: `_on_control` dispatches on
# `type(rec).__name__` plus an enum comparison, so a hand-rolled double can
# agree with the code while the wire type quietly disagrees.

requires_dbn = pytest.mark.skipif(
    importlib.util.find_spec("databento_dbn") is None,
    reason="databento is an optional dep, deliberately absent from requirements.txt",
)


def _sys(code):
    """A REAL SystemMsg, not a stub. The dispatch keys off `type(rec).__name__`
    and an enum comparison, so a hand-rolled double could agree with the code
    while the wire type disagrees — which is the whole failure being fenced."""
    import databento_dbn as d
    return d.SystemMsg(ts_event=0, code=code, msg="x")


def _err(code):
    import databento_dbn as d
    return d.ErrorMsg(ts_event=0, code=code, err="bad symbol")


@requires_dbn
def test_slow_reader_warning_is_counted():
    """The gateway's only signal that we are behind real time. Under the "warn"
    behaviour it keeps sending everything, so no clock in FeedLiveness can see
    this — records flow, prints flow, they are just increasingly stale."""
    from databento_dbn import SystemCode
    s = _stream([])
    s._on_record(_sys(SystemCode.SLOW_READER_WARNING))
    assert s.slow_reader_warnings == 1
    assert s.data_msgs == 0  # control records are not market data


@requires_dbn
def test_gateway_dropped_records_is_counted():
    """Unrecoverable loss. Only reachable if slow_reader_behavior is ever set to
    "skip" — which is why run() passes "warn" explicitly instead of inheriting a
    vendor default that already moved once (2026-04-26)."""
    from databento_dbn import ErrorCode
    s = _stream([])
    s._on_record(_err(ErrorCode.SKIPPED_RECORDS_AFTER_SLOW_READING))
    assert s.skipped_by_gateway == 1


@requires_dbn
def test_symbol_resolution_failure_is_counted():
    """Since 2026-04-26 a bad symbol no longer terminates the session, so with a
    dynamic watchlist a name can simply never stream. This counter is the trace."""
    from databento_dbn import ErrorCode
    s = _stream([])
    s._on_record(_err(ErrorCode.SYMBOL_RESOLUTION_FAILED))
    assert s.symbol_resolution_failures == 1


@requires_dbn
def test_subscription_ack_is_counted_but_not_yet_acted_on():
    """The gateway's own ack, versus inferring one from the first symbol mapping.
    Instrumented only: whether it arrives outside RTH is the open premise the
    live-socket check answers, and the ack clock must not move on a guess."""
    from databento_dbn import SystemCode
    s = _stream([])
    s._on_record(_sys(SystemCode.SUBSCRIPTION_ACK))
    assert s.subscription_acks == 1


@requires_dbn
def test_a_heartbeat_touches_no_counter():
    """The 07-27 regression, restated for the control path: heartbeats reach this
    callback and must remain invisible to every liveness-bearing counter."""
    from databento_dbn import SystemCode
    s = _stream([])
    for _ in range(120):
        s._on_record(_sys(SystemCode.HEARTBEAT))
    assert (s.data_msgs, s.prints, s.subscription_acks,
            s.slow_reader_warnings, s.skipped_by_gateway) == (0, 0, 0, 0, 0)
    assert s.msgs == 120  # the raw diagnostic still moves


def test_a_reconnect_is_counted_and_its_gap_accumulated():
    """Self-healing is how a chronically flapping session hides. The gap seconds
    are prints we never saw, so they accumulate rather than reporting the last
    one — one 40s outage and eighty 0.5s ones are very different diagnoses."""
    import datetime as dt
    s = _stream([])
    t0 = dt.datetime(2026, 7, 27, 14, 30, tzinfo=dt.UTC)
    s._on_reconnect(t0, t0 + dt.timedelta(seconds=4.5))
    s._on_reconnect(t0, t0 + dt.timedelta(seconds=1.5))
    assert s.reconnects == 2
    assert s.reconnect_gap_s == pytest.approx(6.0)


def test_a_reconnect_with_unusable_timestamps_still_counts():
    """The count is the alarm; the gap is detail. Never let a bad timestamp from
    the vendor swallow the fact that the session dropped — and never let it
    raise, because this runs on databento's thread inside their dispatch."""
    s = _stream([])
    s._on_reconnect("not-a-timestamp", None)
    assert s.reconnects == 1
    assert s.reconnect_gap_s == 0.0


def test_the_print_buffer_is_sized_for_the_whole_market():
    """This lane takes ALL_SYMBOLS, so it is a different order of magnitude from
    the Alpaca lane the 5,000 cap was written for.

    Measured 2026-03-20: the whole OPRA tape is 10,246,184 prints, mean 438/s,
    PEAK 1s of 6,642/s. Against 5,000 with a ~1s drain that overflows outright —
    and `sweep_loop` sleeps 1.0s PLUS its own processing time, so the real
    backlog is longer than a second. Overflow is counted in `dropped_prints`,
    but the prints are gone, and it lands on exactly the open/close bursts the
    pressure gauge exists to measure. Do not re-unify with the Alpaca constant."""
    from signals.options_flow.dbn_stream import COLLECTED_PRINTS_MAX
    WHOLE_MARKET_PEAK_PER_S = 6_642
    assert COLLECTED_PRINTS_MAX >= 5 * WHOLE_MARKET_PEAK_PER_S


def test_production_sets_no_replay_window():
    """replay_from is an arming-check affordance. If it ever defaulted to a
    value, production would silently stream yesterday."""
    assert _stream([])._replay_from is None


def test_a_reconnect_during_replay_says_the_replay_is_over(caplog):
    """databento re-subscribes with start=None hardcoded (session.py:762-769),
    so a dropped replay silently returns as a LIVE session. Observed 07-27:
    asking for an out-of-window Friday got the connection closed, reconnect
    fired 15ms later with start='now', and the run then looked healthy — ack
    received, zero data — with reconnects=1 as the only trace. run() now
    disables the policy whenever replay_from is set; this is the second line of
    defence, because the failure is a wrong measurement, not an error."""
    import logging
    s = DatabentoFlowStream(key="k", on_prints=lambda b: None,
                            meta_for=lambda occ: dict(META),
                            replay_from="2026-07-27T13:30:00Z")
    with caplog.at_level(logging.ERROR):
        s._on_reconnect(None, None)
    assert "RECONNECT DURING REPLAY" in caplog.text
    assert s.reconnects == 1


def test_callback_errors_are_counted_not_just_logged():
    """databento catches an exception out of _on_record, logs it, and keeps the
    session alive — so a systematic failure reads exactly like a dead pipeline.
    The counter is what tells the two apart."""
    s = _stream([])
    s._on_callback_error(ValueError("boom"))
    assert s.callback_errors == 1


def test_surface_matches_what_sweep_loop_consumes():
    s = _stream([])
    for attr in ("collected_prints", "dropped_prints", "acked_market_wide",
                 "quote_at", "stop", "run"):
        assert hasattr(s, attr), attr
