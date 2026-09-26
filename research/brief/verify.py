"""Nightly brief checks, all code: every printed number traced to our data, no win% claims.

Flag, don't block: a number the brief prints that appears nowhere in the board, the
fact digests or the session's own tool results is listed in a footer, and counted
per memo for the A/B scorecard. A derived number (a distance, a ratio the writer
worked out) will land in the footer too; that is the point, the reader sees it
wasn't read off our data.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from pathlib import Path
from typing import Any

from ai.research.ideas import _PROBABILITY_PATTERN

# Spans that are not quantities: dates, times, horizon counts, index names, list
# ordinals. Blanked out before numbers are extracted.
_IGNORE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?"
    r"|\b\d+(?:\.\d+)?\s*-?\s*(?:trading\s+days?|sessions?|days?|weeks?|months?|years?|"
    r"quarters?|hours?|minutes?|mins?|yrs?|[dwmyh])\b"
    r"|S&P\s*500|Russell\s*[123]000|Nasdaq[- ]?100|\bDow\s*30"
    r"|^\s*(?:[-*]\s*)?#?\d{1,2}[.)]\s"
    r"|#\d+|\brank\s+\d+",
    re.IGNORECASE | re.MULTILINE,
)
_NUM = re.compile(
    r"(?<![\w.$])(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>%|bps?\b|x\b|[kmbt]n?\b|thousand\b|million\b|billion\b|trillion\b|percent\b))?",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}


def parse_numbers(text: str) -> list[dict[str, Any]]:
    """Every quantity in `text`: {token, value, decimals, scale, pct, bp}.

    Bare integers <= 20 (counts, horizons, ranks) and bare years 1900-2099 are
    skipped: they say nothing about whether a price or level was read off our data.
    """
    text = _IGNORE.sub(lambda m: " " * len(m.group(0)), text or "")
    out = []
    for m in _NUM.finditer(text):
        raw = m.group("num").replace(",", "")
        suf = (m.group("suf") or "").lower()
        value = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        bare = not suf and not m.group("cur") and decimals == 0
        if bare and (value <= 20 or 1900 <= value <= 2099):
            continue
        out.append({
            "token": m.group(0).strip(),
            "value": value,
            "decimals": decimals,
            "scale": _SCALE.get(suf, 1.0),
            "pct": suf in ("%", "percent"),
            "bp": suf.startswith("bp"),
        })
    return out


class NumberIndex:
    """Sorted absolute values of every number in a corpus (JSON leaves + text)."""

    def __init__(self) -> None:
        self._vals: list[float] = []

    def add_value(self, v: float) -> None:
        self._vals.append(abs(float(v)))

    def add_text(self, text: str) -> None:
        for n in parse_numbers(text):
            self._vals.append(n["value"] * n["scale"])
            if n["scale"] != 1.0:
                self._vals.append(n["value"])

    def add_json(self, node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            self.add_value(node)
        elif isinstance(node, str):
            self.add_text(node)
        elif isinstance(node, dict):
            for v in node.values():
                self.add_json(v)
        elif isinstance(node, list):
            for v in node:
                self.add_json(v)

    def freeze(self) -> NumberIndex:
        self._vals.sort()
        return self

    def _near(self, target: float, tol: float) -> bool:
        i = bisect_left(self._vals, target - tol)
        return i < len(self._vals) and self._vals[i] <= target + tol

    def matches(self, n: dict[str, Any]) -> bool:
        """Equal at the printed precision; sign-blind; 62% may be a stored 0.62, 25bp a 0.25."""
        tol = 0.5 * 10 ** -n["decimals"] + 1e-9
        m = n["value"]
        if n["bp"]:
            return self._near(m, tol) or self._near(m / 100, tol / 100)
        if n["pct"]:
            return self._near(m, tol) or self._near(m / 100, tol / 100)
        s = n["scale"]
        # The mantissa alone too: sources print "$222.3 million3" (a footnote glued to
        # the unit) or report "in millions" tables, where the scale never reaches the digits.
        return self._near(m * s, tol * s) or (s != 1.0 and self._near(m, tol))


def build_index(*, jsons: list[Any] = (), texts: list[str] = ()) -> NumberIndex:
    idx = NumberIndex()
    for j in jsons:
        idx.add_json(j)
    for t in texts:
        idx.add_text(t)
    return idx.freeze()


def unmatched_numbers(text: str, index: NumberIndex) -> list[str]:
    """Printed tokens not found in the corpus, first occurrence order, deduped."""
    seen: dict[str, None] = {}
    for n in parse_numbers(text):
        if not index.matches(n):
            seen.setdefault(n["token"], None)
    return list(seen)


def number_in_source(number: str, source_text: str) -> bool:
    """A digest fact's `number` (e.g. "$1.2B", "12%", "3,400") appears in its cited source."""
    wanted = parse_numbers(number)
    if not wanted:
        # A bare small integer ("3") is skipped by the parser; check it literally.
        return bool(re.search(rf"(?<![\d.]){re.escape(number.strip())}(?![\d])", source_text))
    return all(build_index(texts=[source_text]).matches(n) for n in wanted)


# --- probability claims ---------------------------------------------------------------------

_PROB_PHRASE = re.compile(r"\b(?:probability|odds|likelihood)\s+(?:of|that)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def strip_probability_claims(text: str) -> tuple[str, int]:
    """Drop every sentence carrying a win%-style claim; returns (text, sentences removed)."""
    removed = 0
    lines = []
    for line in (text or "").split("\n"):
        parts = _SENTENCE.split(line)
        kept = [p for p in parts if not (_PROBABILITY_PATTERN.search(p) or _PROB_PHRASE.search(p))]
        removed += len(parts) - len(kept)
        if len(kept) == len(parts):
            lines.append(line)
        elif "".join(kept).strip(" -*>"):
            lines.append(" ".join(kept))
    return "\n".join(lines), removed


# --- the night's check ----------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def footer(unmatched: list[str]) -> str:
    if not unmatched:
        return ""
    return (
        "\n\n---\n### Numbers not found in our data\n\n"
        "_Printed above but not in tonight's board, fact digests or the sessions' tool "
        "results. Usually a figure the writer derived; check before relying on it._\n\n"
        + ", ".join(f"`{t}`" for t in unmatched) + "\n"
    )


def check_night(brief_dir: Path, production_arm: str) -> dict[str, Any]:
    """Count unmatched numbers per memo (own tool results) and in brief.md (all
    production sessions' tool results + the judge's). Writes numbers.json."""
    base = [_load_json(brief_dir / "board.json"), _load_json(brief_dir / "facts.json")]
    out: dict[str, Any] = {"memos": {}, "brief": None}
    for memo in sorted(brief_dir.glob("memo_*.md")):
        key = memo.stem.removeprefix("memo_")
        idx = build_index(jsons=base, texts=[_read(brief_dir / f"tools_{key}.txt")])
        text = _read(memo)
        un = unmatched_numbers(text, idx)
        out["memos"][key] = {"numbers": len(parse_numbers(text)), "unmatched": un}
    brief = brief_dir / "brief.md"
    if brief.exists():
        texts = [_read(p) for p in sorted(brief_dir.glob(f"tools_*_{production_arm}.txt"))]
        texts.append(_read(brief_dir / "tools_judge.txt"))
        idx = build_index(jsons=base, texts=texts)
        text = _read(brief)
        out["brief"] = {"numbers": len(parse_numbers(text)),
                        "unmatched": unmatched_numbers(text, idx)}
    (brief_dir / "numbers.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
