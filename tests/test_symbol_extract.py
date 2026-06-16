"""Symbol extraction tests."""

from __future__ import annotations

from ai.utils.symbols import extract_symbol


def test_extract_symbol_rejects_common_false_positives() -> None:
  assert extract_symbol("CEO said USA markets are up") is None
  assert extract_symbol("AI is changing everything") is None


def test_extract_symbol_finds_ticker() -> None:
  assert extract_symbol("What is the outlook for NVDA?") == "NVDA"
