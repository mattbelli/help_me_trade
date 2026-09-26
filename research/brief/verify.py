"""Nightly brief checks, all code: every printed number traced to our data, no win% claims.

Flag, don't block: a number the brief prints that appears nowhere in the board, the
fact digests or the session's own tool results is listed in a footer, and counted
per memo for the A/B scorecard. A derived number (a distance, a ratio the writer
worked out) will land in the footer too; that is the point, the reader sees it
wasn't read off our data.

Scoped: a number in a sentence that names a ticker (a board candidate or an
index/ETF symbol) must be found in THAT symbol's data: its card, its facts, and the
tool-result records about it; an index/ETF symbol's scope is the index, macro,
sector and events frames. A sentence with no ticker inherits one from its bullet
parent or its heading, and only a number with no ticker anywhere above it falls back
to the whole corpus. Measured on the 2026-09-25 brief: against the whole corpus
(~26k values) about half of all numbers still "matched" after a x1.07 fake.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any

from ai.research.ideas import _PROBABILITY_PATTERN

# Spans that are not quantities: dates (2026-09-25, 9/25, Oct-16, 09-29), times, horizon
# counts, index names, list ordinals. Blanked out before numbers are extracted.
_IGNORE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[- ]\d{1,2}\b"
    r"|\b[01]\d-[0-3]\d\b"
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
    """Sorted absolute values of numbers."""

    def __init__(self) -> None:
        self._vals: list[float] = []

    def add_value(self, v: float) -> None:
        self._vals.append(abs(float(v)))

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


# --- scope: which symbols a sentence is about -------------------------------------------

# Index symbols (and the broad-index ETFs) share one scope: the index, macro and events
# frames plus records about them. Sector ETFs are names in their own right. A number
# with no ticker around it is checked against the MARKET scope: the frames plus every
# tool record no board name owns (a macro query, a web page about rates).
INDEX_SCOPE = "(index)"
MARKET_SCOPE = "(market)"
MACRO_SCOPE = "(macro)"  # the board's macro frame alone
INDEX_SYMBOLS = frozenset({"SPX", "SPXW", "NDX", "NDXP", "RUT", "ES", "NQ",
                           "SPY", "QQQ", "IWM", "DIA"})
ETF_SYMBOLS = frozenset({"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB",
                         "XLRE", "XLC"})
INDEX_SCOPE_SYMBOL = "SPX"  # any index symbol names the index scope
_OWNER_KEYS = ("symbol", "underlying", "root", "ticker", "index")
_TICKER = re.compile(r"(?<![\w$/&.-])[$/]?([A-Z][A-Z0-9]{0,4})(?![\w&])")
_TIME_ET = re.compile(r"\d(?:\s?[AaPp][Mm])?\s?ET\b")  # "16:00 ET" is a time zone, not Energy Transfer
_READ_PREFIX = re.compile(r"^\s*\d+(?:\t|→)")  # the Read tool's line numbers
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_OWNER_LINE = re.compile(r'^\s*"(?:symbol|underlying|root|ticker|index)":\s*"')
_BULLET = re.compile(r"^(?:[-*+]|\d{1,2}[.)])\s")


def tickers_in(text: str, symbols: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in _TICKER.findall(_TIME_ET.sub(" ", text)) if t in symbols)


def numbers_in_context(
    text: str, symbols: frozenset[str], base: frozenset[str] = frozenset(), *,
    blank_resets: bool = False,
) -> list[tuple[dict[str, Any], frozenset[str]]]:
    """(number, the symbols its sentence is about) for every quantity in `text`.

    A sentence's own tickers win; else its line's first sentence's; else the nearest
    bullet parent's;
    else the block's (the last heading, or bold title line naming a ticker); else `base`.
    `blank_resets` ends a block at a blank line: tool results are stored joined by
    blank lines, so one result's heading must not claim the next result's numbers.
    """
    out = []
    head, stack = base, []  # stack: (indent, tickers) of the open bullets
    for raw in (text or "").split("\n"):
        line = _READ_PREFIX.sub("", raw, count=1)
        body = line.lstrip()
        if not body:
            if blank_resets:
                head, stack = base, []
            continue
        own = tickers_in(line, symbols)
        indent = len(line) - len(body)
        if body.startswith("#"):
            head, stack = own or base, []
            inherited = head
        elif body.startswith("**") and not _BULLET.match(body):
            # A bold title line ("**1. MSFT — long**") opens a name's block; a bold
            # label with no ticker ("**Bear case:** ...") stays inside the current one.
            head, stack = own or head, []
            inherited = head
        elif _BULLET.match(body):
            while stack and stack[-1][0] >= indent:
                stack.pop()
            inherited = next((t for _, t in reversed(stack) if t), head)
            stack.append((indent, own or inherited))
        else:
            stack = []
            inherited = head
        if blank_resets and _OWNER_LINE.search(line):
            head = own  # an undecodable JSON fragment's `"symbol": "XLK"` owns what follows
        sentences = _SENTENCE.split(line)
        lead = tickers_in(sentences[0], symbols)  # "- **VKTX (flow):** ... 30.5C ..."
        for sentence in sentences:
            scope = tickers_in(sentence, symbols) or lead or inherited
            out += [(n, scope) for n in parse_numbers(sentence)]
    return out


class Corpus:
    """Every number we hold, globally and per symbol scope (see the module doc)."""

    def __init__(self, symbols: frozenset[str] | None = None) -> None:
        # None: no scoping, every number checked against the whole corpus.
        self.symbols = (frozenset() if symbols is None
                        else frozenset(symbols) | INDEX_SYMBOLS | ETF_SYMBOLS)
        self.all = NumberIndex()
        self.scopes: dict[str, NumberIndex] = defaultdict(NumberIndex)

    @staticmethod
    def _key(symbol: str) -> str:
        return INDEX_SCOPE if symbol in INDEX_SYMBOLS else symbol

    def _add(self, value: float, owners: frozenset[str]) -> None:
        self.all.add_value(value)
        keys = {self._key(o) for o in owners}
        for key in keys or {MARKET_SCOPE}:
            self.scopes[key].add_value(value)
        if INDEX_SCOPE in keys:
            self.scopes[MARKET_SCOPE].add_value(value)

    def add_text(self, text: str, owners: frozenset[str] = frozenset()) -> None:
        for n, scope in numbers_in_context(text, self.symbols, owners, blank_resets=True):
            for v in {n["value"] * n["scale"], n["value"]}:  # the bare mantissa too
                self._add(v, scope)

    def _owners(self, node: dict[str, Any]) -> frozenset[str]:
        found = {str(node.get(k) or "").upper() for k in _OWNER_KEYS}
        for v in node.values():  # a record names its symbol in a direct string ("dip: HUM 7%")
            for s in v if isinstance(v, list) else [v]:
                if isinstance(s, str):
                    found |= tickers_in(s, self.symbols)
        return frozenset(found & self.symbols)

    def add_json(self, node: Any, owners: frozenset[str] = frozenset()) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, (int, float)):
            self._add(abs(float(node)), owners)
        elif isinstance(node, str):
            self.add_text(node, owners)
        elif isinstance(node, dict):
            owners = owners | self._owners(node)
            for k, v in node.items():
                key = str(k).upper()
                self.add_json(v, owners | {key} if key in self.symbols else owners)
        elif isinstance(node, list):
            for v in node:
                self.add_json(v, owners)

    def add_tool_text(self, text: str) -> None:
        """Tool results: JSON documents walked as JSON, everything else read as text."""
        decoder = json.JSONDecoder()
        pos, pending = 0, []
        for m in re.finditer(r"^[{\[]", text or "", re.MULTILINE):
            if m.start() < pos:
                continue
            try:
                obj, end = decoder.raw_decode(text, m.start())
            except ValueError:
                continue
            pending.append(text[pos:m.start()])
            self.add_json(obj)
            pos = end
        pending.append((text or "")[pos:])
        self.add_text("\n".join(pending))

    def add_scope(self, node: Any, symbol: str) -> None:
        """A whole block that belongs to one scope (the index frame, a card)."""
        self.add_json(node, frozenset({symbol}))

    def add_macro(self, node: Any) -> None:
        """The board's macro frame, which a sentence about any name may also cite."""
        macro = Corpus(frozenset())
        macro.add_json(node)
        self.scopes[MACRO_SCOPE] = macro.all

    def freeze(self) -> Corpus:
        self.all.freeze()
        for idx in self.scopes.values():
            idx.freeze()
        return self

    def matches(self, n: dict[str, Any], scope: frozenset[str] = frozenset()) -> bool:
        if not self.symbols:
            return self.all.matches(n)
        if not scope:
            return self.scopes[MARKET_SCOPE].matches(n)
        # A name's sentence may cite the board's macro frame ("XLU fell as the 10y hit 5.18").
        keys = {self._key(s) for s in scope} | {MACRO_SCOPE}
        return any(self.scopes[k].matches(n) for k in keys if k in self.scopes)


def build_index(*, jsons: list[Any] = (), texts: list[str] = (),
                symbols: frozenset[str] | None = None) -> Corpus:
    """The corpus; with `symbols` None every number is checked globally."""
    corpus = Corpus(symbols)
    for j in jsons:
        corpus.add_json(j)
    for t in texts:
        corpus.add_tool_text(t)
    return corpus.freeze()


def unmatched_numbers(text: str, corpus: Corpus) -> list[str]:
    """Printed tokens not found in their scope, first occurrence order, deduped."""
    seen: dict[str, None] = {}
    for n, scope in numbers_in_context(text, corpus.symbols):
        if not corpus.matches(n, scope):
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
        "_Printed above but not found in tonight's board, fact digests or the sessions' "
        "tool results for the name they sit next to. Usually a figure the writer derived "
        "or took from the web; check before relying on it._\n\n"
        + ", ".join(f"`{t}`" for t in unmatched) + "\n"
    )


# Board sections that belong to the index scope rather than to any one name.
_INDEX_SECTIONS = ("index", "macro", "sectors", "events")


def night_corpus(brief_dir: Path, tool_files: list[Path]) -> Corpus:
    """The board (cards per symbol, frames in the index scope), the fact digests
    (keyed by symbol) and the given sessions' tool results."""
    board = _load_json(brief_dir / "board.json") or {}
    cards = ((board.get("candidates") if isinstance(board.get("candidates"), dict) else {})
             .get("cards") or [])
    corpus = Corpus(frozenset(str(c.get("symbol")) for c in cards if c.get("symbol")))
    for key, node in board.items():
        if key in _INDEX_SECTIONS:
            corpus.add_scope(node, INDEX_SCOPE_SYMBOL)
        else:
            corpus.add_json(node)
    corpus.add_macro(board.get("macro"))
    corpus.add_json(_load_json(brief_dir / "facts.json"))
    for path in tool_files:
        corpus.add_tool_text(_read(path))
    return corpus.freeze()


def check_night(brief_dir: Path, production_arm: str) -> dict[str, Any]:
    """Count unmatched numbers per memo (own tool results) and in brief.md (all
    production sessions' tool results + the judge's). Writes numbers.json."""
    out: dict[str, Any] = {"memos": {}, "brief": None}
    for memo in sorted(brief_dir.glob("memo_*.md")):
        key = memo.stem.removeprefix("memo_")
        corpus = night_corpus(brief_dir, [brief_dir / f"tools_{key}.txt"])
        text = _read(memo)
        out["memos"][key] = {"numbers": len(parse_numbers(text)),
                             "unmatched": unmatched_numbers(text, corpus)}
    brief = brief_dir / "brief.md"
    if brief.exists():
        corpus = night_corpus(brief_dir, [*sorted(brief_dir.glob(f"tools_*_{production_arm}.txt")),
                                          brief_dir / "tools_judge.txt"])
        text = _read(brief)
        out["brief"] = {"numbers": len(parse_numbers(text)),
                        "unmatched": unmatched_numbers(text, corpus)}
    (brief_dir / "numbers.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
