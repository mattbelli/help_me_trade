"""Nightly deep brief — Stage 1 is the deterministic candidate board (no LLM)."""

from research.brief.board import build_board, load_board, render_board_md

__all__ = ["build_board", "load_board", "render_board_md"]
