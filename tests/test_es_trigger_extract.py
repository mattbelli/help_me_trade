"""Tests for research.es_trigger_extract — synthetic tweets, no network."""

from __future__ import annotations

from datetime import datetime

from research.es_trigger_extract import classify_entry_type, extract_es_triggers


def _tweet(post_id: str, created_at: str, text: str) -> dict:
    return {"id": post_id, "created_at": created_at, "text": text}


SYNTHETIC = [
    _tweet("1", "2025-06-02T14:00:00+00:00", "Long $ES : 5440"),
    _tweet("2", "2026-02-26T12:00:00+00:00", "Sold $ES : 6910"),
    _tweet("3", "2025-05-08T15:44:26+00:00", "Limit to sell : $ES : 5750"),
    _tweet("4", "2026-06-12T14:15:24+00:00", "Long from $ES : 7280 : Now 7400 (+120p)"),
    _tweet("5", "2025-06-02T14:00:00+00:00", "Long $NQ : 21000"),
    _tweet("6", "2026-06-02T14:00:00+00:00", "$ES : 7300 : Pivotal"),
]

RTH = datetime(2025, 6, 2, 14, 0, 0)
PREMARKET = datetime(2026, 2, 26, 12, 0, 0)


def test_classify_entry_type_realtime_long():
    text = "Long $ES : 5440. Here I am."
    assert classify_entry_type(text, "long", RTH) == "realtime_entry"


def test_classify_entry_type_realtime_short_sold():
    text = "$ES : 6535 : Sold"
    assert classify_entry_type(text, "short", RTH) == "realtime_entry"


def test_classify_entry_type_realtime_short_bullets():
    text = "Two Bullets Short : $ES : 6406"
    assert classify_entry_type(text, "short", RTH) == "realtime_entry"


def test_classify_entry_type_recap_long_from():
    text = "Long from $ES : 7280 : Now : 7400 (+120p)"
    assert classify_entry_type(text, "long", RTH) == "recap"


def test_classify_entry_type_recap_trimmed():
    text = "Trimmed $ES : 6720, locked +40p"
    assert classify_entry_type(text, "long", RTH) == "recap"


def test_classify_entry_type_anticipatory_limit():
    text = "Limit to sell : $ES : 5750"
    assert classify_entry_type(text, "short", RTH) == "anticipatory"


def test_classify_entry_type_anticipatory_expecting():
    text = "Expecting $ES : 6910 to begin the flush"
    assert classify_entry_type(text, "short", RTH) == "anticipatory"


def test_classify_entry_type_pivot_marker():
    text = "$ES : 7300 : Pivotal"
    assert classify_entry_type(text, "pivot", RTH) == "pivot_marker"


def test_classify_entry_type_premarket_sold_is_realtime():
    text = "Sold $ES : 6910"
    assert classify_entry_type(text, "short", PREMARKET) == "realtime_entry"


def test_extract_es_triggers_synthetic_cases():
    records = extract_es_triggers(SYNTHETIC)
    by_id = {row["post_id"]: row for row in records}

    assert "5" not in by_id
    assert len(records) == 5

    long_rt = by_id["1"]
    assert long_rt["direction"] == "long"
    assert long_rt["entry_type"] == "realtime_entry"
    assert long_rt["forward"] is True
    assert long_rt["level"] == 5440.0

    short_rt = by_id["2"]
    assert short_rt["direction"] == "short"
    assert short_rt["entry_type"] == "realtime_entry"
    assert short_rt["forward"] is True
    assert short_rt["level"] == 6910.0

    anticipatory = by_id["3"]
    assert anticipatory["direction"] == "short"
    assert anticipatory["entry_type"] == "anticipatory"
    assert anticipatory["forward"] is True
    assert anticipatory["level"] == 5750.0

    recap = by_id["4"]
    assert recap["direction"] == "long"
    assert recap["entry_type"] == "recap"
    assert recap["forward"] is False
    assert recap["level"] == 7280.0

    pivot = by_id["6"]
    assert pivot["direction"] == "pivot"
    assert pivot["entry_type"] == "pivot_marker"
    assert pivot["level"] == 7300.0


def test_extract_es_triggers_sorted_by_post_ts():
    records = extract_es_triggers(SYNTHETIC)
    timestamps = [row["post_ts"] for row in records]
    assert timestamps == sorted(timestamps)
