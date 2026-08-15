"""An over-cap perception result must stay parseable and say what it lost.

Both agent surfaces (research/book_tools.py in-process, tools/perception_query.py
for headless sessions) cap one tool result at 20,000 chars, and both used to do it
by slicing the rendered JSON at a character index — handing the model a fragment
that ends mid-object. Measured 2026-08-11: three live resources were over the cap
(oi_walls 22,388, options_flow 19,230, flow_perception 57,781) and none of them
said so.

Two halves, and both are needed: hoisting the per-row constants got two of the
three under the cap outright, and dropping whole rows keeps the third honest.
"""

from __future__ import annotations

import json

import pytest

from ai.perception.resources import _hoist_shared, fit_to_cap


def _rows(n: int, *, note: str = "x" * 400) -> list[dict]:
    return [{"symbol": f"S{i}", "rank": i, "note": note} for i in range(n)]


def test_a_constant_field_is_stated_once_and_the_rows_keep_the_rest() -> None:
    shared, rows = _hoist_shared(_rows(5))
    assert shared == {"note": "x" * 400}
    assert rows == [{"symbol": f"S{i}", "rank": i} for i in range(5)]


def test_hoisting_never_loses_a_field_that_varies() -> None:
    """The hazard of a shared block is quietly swallowing a real per-row fact."""
    rows = _rows(5)
    rows[2]["note"] = "different"
    shared, slimmed = _hoist_shared(rows)
    assert "note" not in shared
    assert [r["note"] for r in slimmed] == [r["note"] for r in rows]


def test_a_field_missing_from_one_row_does_not_hoist() -> None:
    """`all(r[k] == v)` over a key absent elsewhere would invent the field for rows
    that never carried it — a fabricated fact, which is worse than a fat payload."""
    rows = _rows(4)
    del rows[3]["note"]
    shared, _ = _hoist_shared(rows)
    assert shared == {}


def test_two_rows_are_left_alone() -> None:
    """At two rows, equality is as likely coincidence as structure and the saving is
    noise — so the shape stays predictable for small results."""
    assert _hoist_shared(_rows(2)) == ({}, _rows(2))


def test_non_dict_rows_pass_through_untouched() -> None:
    assert _hoist_shared(["a", "b", "c"]) == ({}, ["a", "b", "c"])


def test_an_over_cap_result_still_parses_as_json() -> None:
    """The whole point. A character slice produced invalid JSON; this must not."""
    payload = {"resource": "flow_perception", "count": 50, "rows": _rows(50)}
    fitted = fit_to_cap(payload, cap=20_000)
    rendered = json.dumps(fitted)
    assert len(rendered) <= 20_000
    json.loads(rendered)  # raises if we cut mid-object


def test_dropped_rows_are_declared_not_silent() -> None:
    payload = {"resource": "flow_perception", "count": 50, "rows": _rows(50)}
    fitted = fit_to_cap(payload, cap=20_000)
    assert len(fitted["rows"]) < 50
    assert "of 50 rows dropped" in fitted["truncated"]


def test_the_rows_kept_are_the_head_not_an_arbitrary_slice() -> None:
    """Every reader orders rows most-significant-first — largest OI, top-decile flow.
    Dropping from the front would throw away exactly what was asked for."""
    payload = {"resource": "x", "count": 50, "rows": _rows(50)}
    kept = fit_to_cap(payload, cap=20_000)["rows"]
    assert [r["rank"] for r in kept] == list(range(len(kept)))


def test_a_result_under_cap_is_returned_unchanged() -> None:
    payload = {"resource": "x", "count": 3, "rows": _rows(3, note="short")}
    assert fit_to_cap(payload, cap=20_000) is payload


def test_indent_is_measured_the_way_the_caller_will_print_it() -> None:
    """tools/perception_query.py prints indent=2, ~40% more characters than compact.
    Measuring compact there would overrun the cap it advertises."""
    payload = {"resource": "x", "count": 50, "rows": _rows(50)}
    fitted = fit_to_cap(payload, cap=20_000, indent=2)
    assert len(json.dumps(fitted, indent=2)) <= 20_000


def test_a_long_string_result_is_sliced() -> None:
    """Web search and report text are unstructured — a slice loses the tail and
    nothing else, which is the honest trade."""
    fitted = fit_to_cap("y" * 30_000, cap=1_000)
    assert isinstance(fitted, str) and len(fitted) < 1_100 and "truncated" in fitted


def test_a_structured_payload_with_no_rows_is_returned_WHOLE() -> None:
    """Only what can be cut without corrupting it is cut. `list_capabilities` is
    20,364 chars of nested dicts with no `rows` to drop; slicing it at 20,000 would
    hand the model a broken manifest — a worse outcome than a long one, and the very
    bug this function replaces. Oversized-but-valid beats undersized-but-invalid."""
    payload = {"resource": "x", "blob": "y" * 30_000}
    assert fit_to_cap(payload, cap=1_000) is payload


@pytest.mark.asyncio
async def test_query_hoists_on_the_real_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wire: hoisting has to happen inside query(), the one call every agent
    surface goes through, or each reader would have to remember to do it."""
    from ai.perception import resources

    monkeypatch.setitem(
        resources.RESOURCES, "market", {**resources.RESOURCES["market"],
                                        "read": lambda f, *, db_path: _rows(4)}
    )
    result = await resources.query("market")
    assert result["shared"] == {"note": "x" * 400}
    assert result["count"] == 4
    assert "note" not in result["rows"][0]


def test_segments_reader_emits_one_row_per_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-08-15 GOOGL chat bug: segments returned ONE giant row, the cap
    dropped it whole, and the model read `rows: []` as "we have no data". One
    row per series lets the trimmer cost the tail axes, never the answer."""
    from pathlib import Path

    import research.fundamentals as rf
    from ai.perception.resources import _read_segments

    canned = {
        "symbol": "TEST", "cik": "1", "concept": "revenue", "as_of": None,
        "axes": ["BusinessSegments", "Geographical"],
        "series": [
            {"axis": "Geographical", "member": "US", "unit": "USD",
             "annual": [{"period_end": "2025-12-31"}], "quarterly": []},
            {"axis": "BusinessSegments", "member": "Old", "unit": "USD",
             "annual": [{"period_end": "2020-12-31"}], "quarterly": []},
            {"axis": "BusinessSegments", "member": "Cloud", "unit": "USD",
             "annual": [{"period_end": "2025-12-31"}],
             "quarterly": [{"period_end": "2026-06-30"}]},
        ],
        "source": "src",
    }
    monkeypatch.setattr(rf, "segment_series", lambda *a, **k: canned)
    rows = _read_segments({"symbol": "TEST"}, db_path=Path("unused"))
    assert [(r["axis"], r["member"]) for r in rows] == [
        ("BusinessSegments", "Cloud"),  # current business segments lead
        ("BusinessSegments", "Old"),    # stale members sort behind fresh ones
        ("Geographical", "US"),         # other axes are the droppable tail
    ]
    assert all(r["symbol"] == "TEST" and r["note"] for r in rows)
