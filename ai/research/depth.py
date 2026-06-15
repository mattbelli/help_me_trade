"""Research depth presets (Odysseus-style iteration budgets)."""

from __future__ import annotations

RESEARCH_DEPTHS = frozenset({"quick", "standard", "deep"})

DEPTH_CONFIG: dict[str, dict[str, int]] = {
    "quick": {"max_iterations": 1, "max_queries": 2},
    "standard": {"max_iterations": 2, "max_queries": 4},
    "deep": {"max_iterations": 3, "max_queries": 6},
}
