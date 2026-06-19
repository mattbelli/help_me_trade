"""Tests for robust LLM JSON object extraction."""

from __future__ import annotations

from pydantic import BaseModel

from ai.llm.structured import (
    extract_json_object,
    parse_json_object,
    parse_structured,
)


class SampleModel(BaseModel):
    name: str
    value: int


def test_parse_json_object_from_fenced_block() -> None:
    raw = 'Here you go:\n```json\n{"queries": ["a", "b"]}\n```\nThanks'
    parsed = parse_json_object(raw)
    assert parsed == {"queries": ["a", "b"]}


def test_parse_json_object_with_leading_and_trailing_prose() -> None:
    raw = (
        "Sure — here is the report:\n"
        '{"narrative": "closed P&L was positive", "count": 2}\n'
        "Let me know if you need more detail."
    )
    parsed = parse_json_object(raw)
    assert parsed == {"narrative": "closed P&L was positive", "count": 2}


def test_parse_json_object_bare_object_regression() -> None:
    raw = '{"name": "test", "value": 3}'
    assert parse_json_object(raw) == {"name": "test", "value": 3}


def test_parse_json_object_nested_braces() -> None:
    raw = 'prefix {"outer": {"inner": 1}, "ok": true} suffix'
    parsed = parse_json_object(raw)
    assert parsed == {"outer": {"inner": 1}, "ok": True}


def test_parse_json_object_trailing_commas() -> None:
    raw = '{"items": [1, 2,], "done": true,}'
    parsed = parse_json_object(raw)
    assert parsed == {"items": [1, 2], "done": True}


def test_parse_json_object_prefers_largest_valid_object() -> None:
    raw = (
        '{"hint": "ignore"} '
        '{"narrative": "full report", "nested": {"a": 1, "b": {"c": 2}}}'
    )
    parsed = parse_json_object(raw)
    assert parsed == {
        "narrative": "full report",
        "nested": {"a": 1, "b": {"c": 2}},
    }


def test_parse_json_object_ignores_trailing_prose_after_balanced_object() -> None:
    raw = '{"gaps": "need more sector data"} and some trailing text'
    snippet = extract_json_object(raw)
    assert snippet == '{"gaps": "need more sector data"}'
    assert parse_json_object(raw) == {"gaps": "need more sector data"}


def test_parse_json_object_returns_none_on_invalid() -> None:
    assert parse_json_object("not json at all") is None
    assert parse_json_object('{"broken": ') is None


def test_parse_structured_validates_model() -> None:
    model = parse_structured('{"name": "test", "value": 3}', SampleModel)
    assert model is not None
    assert model.name == "test"
    assert model.value == 3
