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


def test_quote_at_returns_the_prints_own_nbbo():
    s = _stream([])
    s._on_record(_rec(7_700_000_000, 7_400_000_000, 8_100_000_000))
    ts_ms = 1_784_813_400_000
    q = s.quote_at(META["occ_symbol"], ts_ms)
    assert q is not None
    bid, ask, q_ts = q
    assert (round(bid, 2), round(ask, 2)) == (7.40, 8.10)
    assert q_ts == ts_ms  # zero staleness by construction


def test_undefined_quote_is_not_stored_as_a_price():
    UNDEF = 9223372036854775807
    s = _stream([])
    s._on_record(_rec(7_700_000_000, UNDEF, UNDEF))
    assert s.quote_at(META["occ_symbol"], 1_784_813_400_000) is None
    assert len(s._drain()) == 1  # the print still counts; only the quote is absent


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
