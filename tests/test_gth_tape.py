"""GTH tape — window/session mapping, Saturday-test fence, persist round-trip.

The tape is the Phase B acquisition layer: the Alpaca sidecar tees overnight
prints here (shadow_pairing), and gth_morning reads the files back. These pin
the session-date algebra (an evening print belongs to the NEXT morning's
session) and the calendar fence (OPRA Saturday industry tests must never mint
a tape file).
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from signals.options_flow.gth_tape import GthTape

ET = ZoneInfo("America/New_York")


def _ns(y, m, d, hh, mm) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp() * 1e9)


def _tape(tmp_path) -> GthTape:
    return GthTape(tape_dir=tmp_path / "gth")


def test_morning_print_lands_in_same_day_session(tmp_path) -> None:
    tape = _tape(tmp_path)
    # Tuesday 2026-08-11 05:30 ET — Europe-morning GTH slice.
    assert tape.offer("SPXW260811P06300000", _ns(2026, 8, 11, 5, 30), 250, 10, "I", "C")
    tape.flush()
    path = tmp_path / "gth" / "2026-08-11.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert rows == [
        {"t": _ns(2026, 8, 11, 5, 30), "s": "SPXW260811P06300000",
         "p": 250, "z": 10, "c": "I", "x": "C"}
    ]
    assert tape.stats()["written"] == 1


def test_evening_print_lands_in_next_morning_session(tmp_path) -> None:
    tape = _tape(tmp_path)
    # Sunday 2026-08-09 22:00 ET — belongs to Monday 08-10's night.
    assert tape.offer("SPX260918C06500000", _ns(2026, 8, 9, 22, 0), 12000, 5, "S", "C")
    tape.flush()
    assert (tmp_path / "gth" / "2026-08-10.jsonl").exists()


def test_rth_print_rejected_and_fast_path_armed(tmp_path) -> None:
    tape = _tape(tmp_path)
    assert not tape.offer("AAPL260814C00230000", _ns(2026, 8, 11, 13, 0), 500, 1, "I", "C")
    # The slow path cached the day's RTH window: the firehose now rejects on
    # one integer compare.
    assert tape._rth_lo <= _ns(2026, 8, 11, 13, 0) < tape._rth_hi
    assert not tape.offer("AAPL260814C00230000", _ns(2026, 8, 11, 15, 59), 500, 1, "I", "C")
    tape.flush()
    assert not list((tmp_path / "gth").glob("*.jsonl"))


def test_saturday_test_session_prints_dropped(tmp_path) -> None:
    tape = _tape(tmp_path)
    # OPRA Saturday industry test: 2026-08-08 was a real one (421k messages).
    # An overnight-window print dated Saturday must never persist.
    assert not tape.offer("SPXW260810C06300000", _ns(2026, 8, 8, 5, 0), 100, 1, "I", "C")
    # Friday-evening print maps to Saturday — equally not a session.
    assert not tape.offer("SPXW260810C06300000", _ns(2026, 8, 7, 21, 0), 100, 1, "I", "C")
    assert tape.stats()["dropped_non_session"] == 2
    tape.flush()
    assert not list((tmp_path / "gth").glob("*.jsonl"))


def test_offer_never_raises_on_garbage(tmp_path) -> None:
    tape = _tape(tmp_path)
    assert not tape.offer("X", "not-a-timestamp", 1, 1, "", "")  # type: ignore[arg-type]
    assert tape.stats()["errors"] == 1


def test_flush_groups_sessions_and_appends(tmp_path) -> None:
    tape = _tape(tmp_path)
    tape.offer("SPXW260811P06300000", _ns(2026, 8, 11, 4, 5), 100, 1, "I", "C")
    tape.offer("SPXW260811P06300000", _ns(2026, 8, 11, 8, 31), 200, 2, "I", "C")
    tape.flush()
    tape.offer("SPXW260811P06300000", _ns(2026, 8, 11, 9, 10), 300, 3, "f", "C")
    tape.flush()  # append, not truncate
    path = tmp_path / "gth" / "2026-08-11.jsonl"
    assert len(path.read_text().splitlines()) == 3
    assert tape.stats()["written"] == 3
