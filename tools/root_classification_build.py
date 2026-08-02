"""Build (and read) the ONE registered root classification — Addendum G obligation 2.

Addendum G §G.8 replaced three incompatible root lists with a single registered
fence, `{adjusted, test, index, etf, equity}` in that precedence, frozen into one
JSON snapshot plus the loader below. Every future tool imports `load()` and asks
`classify()` / `resolve()`; nothing hand-lists roots again.

The build reads the COMMITTED directory snapshot (`signals/options_flow/
nasdaqtraded_20260730.txt` — the registered vintage; the nasdaqtrader.com URL is
provenance only, it serves the current day) and the registered Alpaca
corporate-actions `name_change` feed, and freezes:

  * the ETN list (Security Name regex, case-sensitive, per G.8),
  * the test list (directory `Test Issue = Y` ∪ the frozen OPRA-only roots),
  * the index list (`db.flow_corpus.INDEX_ROOTS` ∪ the reviewed additions),
  * the successor map + reuse-split list (G.2's terminal-value rule),
  * the delisted census with its REVIEWED per-root class assignment,
  * `LEGACY_ETF_SET` (the retired hand-built `ETF_SET`, never deleted),
  * the dotted OCC ↔ directory normalization, both directions.

The orchestrator's reviews (index-addition, test-root, delisted census, split
exemptions) are INPUTS, passed verbatim as JSON objects — this tool never
guesses them.

Five things a reviewer should know about the mechanics, stated rather than
buried:

1. **Only `.` forms ASSIGN; `$` forms are recorded and never mapped.** G.8 says
   "normalize `.`/`$` suffixes in BOTH directions", and both directions ARE on
   the record — but `$` is Nasdaq Trader's PREFERRED-share marker, and a
   preferred carries no listed options, so every `$` entry is false-mapping
   surface with no legitimate hit behind it. Measured on the registered
   snapshot: of 355 usable `$` map entries exactly TWO are live corpus roots
   with no plain directory row, and BOTH are wrong — `GAMB` → `GAM$B` and
   `ATHA` → `ATH$A` would classify a real equity off a preferred row, hand
   `resolve()` a symbol Alpaca cannot serve, and silently remove the root from
   the layer-2 directory-absent census that G.8 exists to compute. The `.` side
   carries the 8 real class-share hits (`BRKB`, `BFB`, `BFA`, `CRDA`, `HEIA`,
   `MOGA`, `PBRA`, `UHALB`). So the lookup map is built from `.` rows ONLY; the
   `$` forms go to `dollar_forms_not_mapped`. A plain directory row still always
   wins, shadowing and ambiguous forms are still dropped and recorded, and the
   entries that actually do assignment work are listed in `dotted_map_active` so
   all eight can be eyeballed.

2. **The per-segment overlap check is a standing GUARD, not an expected
   filter.** G.2 pins the order: build the reuse-split list FIRST, then run the
   overlap test per segment. The predecessor segment is hard-cut at
   `process_date` (G.2's date-scoping), so the only way a pair can still overlap
   is a SUCCESSOR that was already printing before the seam with no registered
   split to explain it — and that is exactly the condition which fires a split.
   The check is therefore structurally empty under the registered order: it is a
   consistency guard on the split builder, not a filter that is expected to
   remove pairs. It runs on every pair regardless, and the pairs whose RAW date
   sets intersect are recorded separately as `overlap_rescued_by_split` so the
   seam audit (G.2 measured SATS/ECHO and PARA/PSKY clean) stays visible.

3. **The premium filter is applied to every hop, as registered.** A rename whose
   old root carries < $250k is dropped even when it continues a surviving
   chain, which can leave a terminal symbol short of its true last hop. That is
   the registered rule (it is what produces the pre-registered count of 106), so
   it is not silently "fixed" — the affected pairs are recorded in
   `chain_continuations_dropped_by_premium_filter` for the orchestrator.

4. **The old-symbol split trigger is STRICTLY after the seam.** G.2's words are
   "whose OLD symbol keeps printing after `process_date`", and the date-scoping
   interval is closed on the successor side, so the seam session itself belongs
   to the successor. A lone old-ticker print ON the seam day is therefore a
   straggler that merges into the successor identity; treating it as continued
   printing would manufacture a split whose earlier identity has no bar series
   of its own. Only a print strictly AFTER the seam splits.

5. **A reuse split carries its own gap evidence, and can be exempted.** "One bar
   series belongs to at most one issuer" is the split's premise, and it is FALSE
   for a dual-listing consolidation: `CUK → CCL` trips the same rule as `ARIS`,
   but CCL's printing is continuous across the seam (a 1-session gap) whereas
   ARIS sat vacant for months. The tool cannot tell those apart from the feed
   alone, so it does not try: every split records `last_before` / `first_at_or_after`
   / `gap_sessions` on the corpus session calendar, and the orchestrator's
   `--exempt-splits` review can withdraw a split by name. An exempted split is
   removed, the pair maps as a plain continuation, and the exemption reason plus
   the gap evidence are stored verbatim.

A topology collision (two old roots landing in the SAME segment of one terminal
symbol, which would duplicate that symbol's post-seam name-days across two
identities) is recorded and the build FAILS: the collisions are written to
`<out>.collisions.json`, `<out>` is left alone, and the exit code is non-zero.

    python tools/root_classification_build.py \
        --out signals/options_flow/root_classification.json \
        --pull-date 2026-08-08 \
        --expect-after-floor 138 --expect-pairs 106 \
        --test-roots reviews/test_roots.json \
        --index-additions reviews/index_additions.json \
        --delisted reviews/delisted.json \
        --merger-continuations reviews/merger_continuations.json \
        --exempt-splits reviews/exempt_splits.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.flow_corpus import DEFAULT_DB_PATH, INDEX_ROOTS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIRECTORY = ROOT / "signals" / "options_flow" / "nasdaqtraded_20260730.txt"

SCHEMA_VERSION = 1
CLASSES = ("adjusted", "test", "index", "etf", "equity")
UNCLASSIFIED = "unclassified"

# G.8: Security Name match, CASE-SENSITIVE. 60 matches on the registered
# snapshot (44 already ETF=Y, 16 flagged ETF=N).
ETN_PATTERN = r"\bETNs?\b|Exchange[- ]Traded Note"
# G.8 "Mapping first": a root with TRAILING DIGITS is `adjusted` — an excluded
# class of its own. Recorded as the RULE, never as an enumeration (0 of the
# 13,074 directory symbols contain a digit, so no legitimate root is lost).
ADJUSTED_PATTERN = r"[0-9]+$"

CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
DIRECTORY_SOURCE_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqtraded.txt"
# G.2: the registered pull span opens the day before the corpus does.
CA_START_DATE = "2025-07-25"
# G.2: "old root carries >= $250k of TOTAL (all-sides) corpus flow premium".
MIN_OLD_SIDE_PREMIUM = 250_000.0

_ETN_RE = re.compile(ETN_PATTERN)
_ADJUSTED_RE = re.compile(ADJUSTED_PATTERN)
_PLAIN_ROOT_RE = re.compile(r"^[A-Z]+$")
_MAX_CHAIN_HOPS = 32
# 671 records at limit=1000 is a single page today; the cap only has to stop a
# server that never stops handing back tokens.
_MAX_PAGES = 100

logger = logging.getLogger("root_classification_build")


class DirectoryRow(NamedTuple):
    symbol: str
    security_name: str
    etf: bool
    test_issue: bool


# --------------------------------------------------------------------------
# directory snapshot
# --------------------------------------------------------------------------


def parse_directory(text: str) -> tuple[list[DirectoryRow], str]:
    """Parse the pipe-delimited Nasdaq Trader file. Returns (rows, vintage).

    `vintage` is the footer's own timestamp (`File Creation Time: 0730202618:02`)
    — the registered snapshot identity, not the file's mtime.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty directory file")
    header = lines[0].split("|")
    try:
        i_sym = header.index("Symbol")
        i_name = header.index("Security Name")
        i_etf = header.index("ETF")
        i_test = header.index("Test Issue")
    except ValueError as exc:  # pragma: no cover - a schema change, not a data case
        raise ValueError(f"unexpected directory header: {header}") from exc

    vintage = ""
    rows: list[DirectoryRow] = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            vintage = line.split("|", 1)[0].split(": ", 1)[1].strip()
            continue
        fields = line.split("|")
        if len(fields) != len(header):
            raise ValueError(f"malformed directory row ({len(fields)} fields): {line!r}")
        rows.append(DirectoryRow(
            symbol=fields[i_sym].strip(),
            security_name=fields[i_name],
            etf=fields[i_etf].strip() == "Y",
            test_issue=fields[i_test].strip() == "Y",
        ))
    if not vintage:
        raise ValueError("directory file has no 'File Creation Time' footer")
    return rows, vintage


def occ_form(symbol: str) -> str:
    """Directory form -> OCC form: `BRK.B` -> `BRKB`, `ABR$D` -> `ABRD`."""
    return symbol.replace(".", "").replace("$", "")


def _candidate_forms(
    rows: Iterable[DirectoryRow], mark: str, plain: set[str]
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """OCC form -> directory symbol for the rows carrying `mark`, plus the drops."""
    candidates: dict[str, list[str]] = {}
    for r in rows:
        if mark in r.symbol:
            candidates.setdefault(occ_form(r.symbol), []).append(r.symbol)

    mapping: dict[str, str] = {}
    dropped: list[dict[str, object]] = []
    for form, syms in candidates.items():
        syms = sorted(syms)
        if form in plain:
            dropped.append({"occ_form": form, "directory_symbols": syms,
                            "reason": "shadows_plain_directory_row"})
        elif len(syms) > 1:
            dropped.append({"occ_form": form, "directory_symbols": syms,
                            "reason": "ambiguous_multiple_directory_rows"})
        else:
            mapping[form] = syms[0]
    return mapping, sorted(dropped, key=lambda d: d["occ_form"])


def _dotted_map(
    rows: Iterable[DirectoryRow], corpus_roots: Iterable[str] = ()
) -> tuple[dict[str, str], list[dict[str, object]], dict[str, str], list[dict[str, str]]]:
    """The normalization record: what MAPS, what is only recorded, what is live.

    Returns (mapping, dropped, dollar_forms_not_mapped, active).

    Only `.` rows may assign a class or a bars symbol — see the module docstring:
    the `$` marker is preferred shares, which carry no listed options, and its
    only two live corpus hits (`GAMB`, `ATHA`) are both false. The `$` forms are
    still computed and returned so "normalized in both directions" stays on the
    record; they are never consulted by `classify()` or `resolve()`.

    `active` is the subset that actually does work — an OCC form that is a live
    corpus root with no plain directory row of its own.
    """
    rows = list(rows)
    plain = {r.symbol for r in rows if "." not in r.symbol and "$" not in r.symbol}
    mapping, dropped = _candidate_forms(rows, ".", plain)
    dollar, dollar_dropped = _candidate_forms(rows, "$", plain)

    roots = set(corpus_roots)
    active = sorted(
        ({"occ_form": form, "directory_symbol": sym}
         for form, sym in mapping.items() if form in roots),
        key=lambda d: d["occ_form"],
    )
    for entry in dollar_dropped:
        entry["mark"] = "$"
    for entry in dropped:
        entry["mark"] = "."
    return (mapping, sorted(dropped + dollar_dropped, key=lambda d: (d["occ_form"], d["mark"])),
            dict(sorted(dollar.items())), active)


# --------------------------------------------------------------------------
# corporate actions
# --------------------------------------------------------------------------


def fetch_name_changes(
    start: str,
    end: str,
    *,
    api_key: str,
    api_secret: str,
    url: str = CORPORATE_ACTIONS_URL,
    limit: int = 1000,
    transport: httpx.BaseTransport | None = None,
) -> list[dict]:
    """Every `name_change` record in [start, end], following the page token.

    The pager is bounded twice: a repeated token aborts (a server that hands back
    its own cursor would otherwise spin forever, silently duplicating records),
    and so does a hard page cap.
    """
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    out: list[dict] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    with httpx.Client(timeout=90.0, transport=transport) as client:
        for page in range(1, _MAX_PAGES + 1):
            params: dict[str, object] = {
                "types": "name_change", "start": start, "end": end, "limit": limit,
            }
            if token:
                params["page_token"] = token
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()
            if "corporate_actions" not in payload:
                raise ValueError(
                    f"corporate-actions payload has no `corporate_actions` key "
                    f"(page {page}, keys {sorted(payload)})")
            out.extend((payload.get("corporate_actions") or {}).get("name_changes") or [])
            token = payload.get("next_page_token")
            if not token:
                logger.info("name_change records pulled: %d (%d page(s))", len(out), page)
                return out
            if token in seen_tokens:
                raise ValueError(f"corporate-actions pager repeated its token at page {page}")
            seen_tokens.add(token)
    raise ValueError(f"corporate-actions pager exceeded {_MAX_PAGES} pages")


class _Pair(NamedTuple):
    old: str
    new: str
    process_date: str
    source: str


def _filter_pairs(
    name_changes: Iterable[Mapping[str, object]],
    premium: Mapping[str, float],
    min_premium: float,
) -> tuple[list[_Pair], list[_Pair], dict[str, int]]:
    """G.2's registered map filter, in its registered order.

    plain-alphabetic old/new -> old-side premium >= the floor -> `old != new`.
    The last step is what removes the CUSIP-only records (an unchanged ticker
    with a new CUSIP); the prereg's counts are 138 before it and 106 after.

    Also returns the renames dropped by the PREMIUM floor alone, so the build can
    record any that would have continued a surviving chain.
    """
    raw = list(name_changes)
    alpha: list[_Pair] = []
    for rec in raw:
        old = str(rec.get("old_symbol") or "").strip().upper()
        new = str(rec.get("new_symbol") or "").strip().upper()
        if _PLAIN_ROOT_RE.match(old) and _PLAIN_ROOT_RE.match(new):
            alpha.append(_Pair(old, new, str(rec.get("process_date") or ""), "name_change"))

    funded = [p for p in alpha if premium.get(p.old, 0.0) >= min_premium]
    unfunded = [p for p in alpha if premium.get(p.old, 0.0) < min_premium and p.old != p.new]
    pairs = [p for p in funded if p.old != p.new]
    counts = {
        "name_change_records_raw": len(raw),
        "after_plain_alphabetic": len(alpha),
        "after_old_side_premium_floor": len(funded),
        "after_old_ne_new": len(pairs),
    }
    return pairs, unfunded, counts


# --------------------------------------------------------------------------
# splits, chains, checks
# --------------------------------------------------------------------------


def _gap_evidence(
    symbol: str, seam: str, corpus_dates: Mapping[str, set[str]], sessions: Sequence[str]
) -> dict[str, object]:
    """How long this ticker actually went quiet around the seam.

    The split's premise is that one bar series belongs to at most one issuer. A
    months-long vacancy supports it (ARIS); a 1-session gap says the ticker never
    stopped trading and the "reuse" is a dual listing folding in (CUK -> CCL).
    `gap_sessions` is the positional distance on the corpus session calendar, so
    adjacent sessions read 1.
    """
    dates = corpus_dates.get(symbol, ())
    before = max((d for d in dates if d < seam), default=None)
    after = min((d for d in dates if d >= seam), default=None)
    gap: int | None = None
    if before is not None and after is not None:
        index = {d: i for i, d in enumerate(sessions)}
        if before in index and after in index:
            gap = index[after] - index[before]
    return {"last_before": before, "first_at_or_after": after, "gap_sessions": gap}


def _build_splits(
    pairs: Iterable[_Pair],
    corpus_dates: Mapping[str, set[str]],
    sessions: Sequence[str] = (),
    exempt: Mapping[str, object] | None = None,
) -> tuple[dict[str, list[str]], list[dict[str, object]], list[dict[str, object]]]:
    """G.2's reuse-split list, built FIRST (the order is pinned).

    A `name_change` whose NEW symbol carries corpus rows earlier than
    `process_date`, or whose OLD symbol keeps printing STRICTLY AFTER it, splits
    that symbol's identity at that date. The strictness is G.2's own word
    ("after") and follows from the interval being closed on the successor side:
    the seam session belongs to the successor, so an old-ticker print ON the seam
    day is a straggler inside the successor identity, not evidence of a second
    tenant. Splitting on it would manufacture an earlier identity with no bar
    series of its own.

    A symbol named in `exempt` (the orchestrator's review) keeps NO split — the
    pair maps as a plain continuation — and the withdrawal is returned with its
    gap evidence.
    """
    exempt = exempt or {}
    splits: dict[str, set[str]] = {}
    reasons: list[dict[str, object]] = []
    exempted: list[dict[str, object]] = []

    def _record(symbol: str, seam: str, reason: str, pair: _Pair) -> None:
        entry: dict[str, object] = {
            "symbol": symbol, "date": seam, "reason": reason,
            "pair": f"{pair.old}->{pair.new}",
            **_gap_evidence(symbol, seam, corpus_dates, sessions),
        }
        if symbol in exempt:
            entry["exemption"] = exempt[symbol]
            exempted.append(entry)
            return
        splits.setdefault(symbol, set()).add(seam)
        reasons.append(entry)

    for p in pairs:
        if any(d < p.process_date for d in corpus_dates.get(p.new, ())):
            _record(p.new, p.process_date, "new_symbol_has_earlier_corpus_rows", p)
        if any(d > p.process_date for d in corpus_dates.get(p.old, ())):
            _record(p.old, p.process_date, "old_symbol_keeps_printing", p)

    def _key(r: Mapping[str, object]) -> tuple:
        return (r["symbol"], r["date"], r["reason"])

    return ({s: sorted(v) for s, v in sorted(splits.items())},
            sorted(reasons, key=_key), sorted(exempted, key=_key))


def _segment(boundaries: list[str], when: str | None) -> tuple[str | None, str | None]:
    """The [start, end) segment of one symbol containing `when`.

    `when=None` means the pull date, i.e. the last segment. The interval is
    closed on the successor side (G.2), which is what makes a rename at exactly
    `start` belong to the PREVIOUS segment.
    """
    if when is None:
        return (boundaries[-1] if boundaries else None), None
    start = max((b for b in boundaries if b <= when), default=None)
    end = min((b for b in boundaries if b > when), default=None)
    return start, end


def _next_hop(
    symbol: str,
    when: str | None,
    renames: Mapping[str, list[tuple[str, str]]],
    splits: Mapping[str, list[str]],
) -> tuple[str, str] | None:
    """The rename that carries this symbol's segment forward, if any."""
    start, end = _segment(list(splits.get(symbol, ())), when)
    for process_date, new in renames.get(symbol, ()):
        if (start is None or process_date > start) and (end is None or process_date <= end):
            return process_date, new
    return None


def _terminal(
    symbol: str,
    when: str | None,
    renames: Mapping[str, list[tuple[str, str]]],
    splits: Mapping[str, list[str]],
) -> str:
    """Follow the rename chain to its last symbol. Multi-hop; cycle-guarded."""
    seen: set[tuple[str, str | None]] = set()
    for _ in range(_MAX_CHAIN_HOPS):
        if (symbol, when) in seen:
            return symbol
        seen.add((symbol, when))
        hop = _next_hop(symbol, when, renames, splits)
        if hop is None:
            return symbol
        when, symbol = hop
    return symbol


def _renames_index(pairs: Iterable[_Pair]) -> dict[str, list[tuple[str, str]]]:
    idx: dict[str, list[tuple[str, str]]] = {}
    for p in pairs:
        idx.setdefault(p.old, []).append((p.process_date, p.new))
    return {k: sorted(v) for k, v in idx.items()}


def _segment_dates(
    symbol: str, start: str | None, end: str | None, corpus_dates: Mapping[str, set[str]]
) -> set[str]:
    return {d for d in corpus_dates.get(symbol, ())
            if (start is None or d >= start) and (end is None or d < end)}


def _overlap_check(
    pairs: Iterable[_Pair],
    splits: Mapping[str, list[str]],
    corpus_dates: Mapping[str, set[str]],
    exempt: Mapping[str, object] | None = None,
) -> tuple[list[_Pair], list[dict[str, object]], list[dict[str, object]]]:
    """Per-segment overlap, run on EVERY pair. See docstring note 2.

    Returns (surviving pairs, excluded+recorded, raw-overlap-rescued-by-split).

    A pair whose successor is EXEMPTED from splitting is exempted here too, and
    necessarily so: the exemption's whole content is "this apparent reuse is a
    continuous identity", and the guard's one trigger is a successor printing
    before the seam. Without the waiver the exemption would delete the pair from
    the map rather than merge it — the opposite of what was reviewed.
    """
    exempt = exempt or {}
    kept: list[_Pair] = []
    excluded: list[dict[str, object]] = []
    rescued: list[dict[str, object]] = []
    for p in pairs:
        old_bounds = list(splits.get(p.old, ()))
        # the predecessor segment is the one ENDING at process_date
        old_start = max((b for b in old_bounds if b < p.process_date), default=None)
        pre = _segment_dates(p.old, old_start, p.process_date, corpus_dates)
        new_start, new_end = _segment(list(splits.get(p.new, ())), p.process_date)
        post = _segment_dates(p.new, new_start, new_end, corpus_dates)

        clash = sorted(pre & post)
        raw_clash = sorted(corpus_dates.get(p.old, set()) & corpus_dates.get(p.new, set()))
        if clash and p.new not in exempt:
            excluded.append({"old": p.old, "new": p.new, "process_date": p.process_date,
                             "overlapping_dates": clash})
            continue
        if raw_clash:
            rescued.append({"old": p.old, "new": p.new, "process_date": p.process_date,
                            "waived_by_exemption": p.new in exempt,
                            "raw_overlapping_dates": raw_clash})
        kept.append(p)
    def _key(e: Mapping[str, object]) -> tuple:
        return (e["old"], e["new"], e["process_date"])

    return kept, sorted(excluded, key=_key), sorted(rescued, key=_key)


def _topology_check(
    pairs: Iterable[_Pair], splits: Mapping[str, list[str]]
) -> list[dict[str, object]]:
    """Two old roots landing in ONE segment of one terminal symbol (G.2).

    Segment-aware on purpose: a sequential REUSE (`A -> S` then later `B -> S`
    with a registered split between them) lands in two DIFFERENT segments and is
    not a collision; a genuine merge (`A -> S` and `B -> S`, same segment) is.
    """
    landings: dict[tuple[str, str | None], set[str]] = {}
    for p in pairs:
        start, _ = _segment(list(splits.get(p.new, ())), p.process_date)
        landings.setdefault((p.new, start), set()).add(p.old)
    return sorted(
        ({"terminal_symbol": sym, "segment_start": start, "old_roots": sorted(olds)}
         for (sym, start), olds in landings.items() if len(olds) > 1),
        key=lambda c: (c["terminal_symbol"], c["segment_start"] or ""),
    )


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------


def _normalize_review(review: Mapping[str, object] | None) -> dict[str, object]:
    """A review object, keys normalized to OCC form, values kept VERBATIM.

    Keys are normalized because the reviews are written by hand from census
    output that may quote either ticker form — `BRK.B` and `BRKB` must not become
    two different entries — while the values (class, reason, evidence) are the
    orchestrator's words and are stored untouched.
    """
    if review is None:
        return {}
    if not isinstance(review, Mapping):
        raise TypeError(
            f"review inputs must be JSON OBJECTS keyed by root, got {type(review).__name__}; "
            "a bare list loses the reviewer's class/reason and is not accepted")
    return {occ_form(str(k).strip().upper()): v for k, v in sorted(review.items())}


def build_snapshot(
    *,
    directory_text: str,
    directory_path: str,
    directory_sha256: str,
    name_changes: Iterable[Mapping[str, object]],
    premium: Mapping[str, float],
    dates_for: Callable[[Iterable[str]], dict[str, set[str]]],
    legacy_etf_set: Iterable[str],
    pull_date: str,
    generated_at: str,
    start_date: str = CA_START_DATE,
    sessions: Sequence[str] = (),
    test_roots: Mapping[str, object] | None = None,
    index_additions: Mapping[str, object] | None = None,
    delisted: Mapping[str, Mapping[str, object]] | None = None,
    merger_continuations: Iterable[Mapping[str, str]] = (),
    exempt_splits: Mapping[str, object] | None = None,
    min_premium: float = MIN_OLD_SIDE_PREMIUM,
) -> dict:
    """The registered snapshot, as a plain dict. Deterministic: everything sorted.

    The four review inputs are OBJECTS keyed by root (`{"ZTST": {"reason": ...}}`)
    and are stored VERBATIM — the orchestrator's naming reviews are committed as
    written, never flattened to bare lists that lose the reasoning.
    """
    rows, vintage = parse_directory(directory_text)
    dotted, dotted_dropped, dollar_forms, dotted_active = _dotted_map(rows, premium)
    delisted = _normalize_review(delisted)
    test_roots = _normalize_review(test_roots)
    index_additions = _normalize_review(index_additions)
    exempt_splits = _normalize_review(exempt_splits)

    etn = sorted(r.symbol for r in rows if _ETN_RE.search(r.security_name))
    etn_set = set(etn)
    directory_test = sorted(r.symbol for r in rows if r.test_issue)
    directory_class = {
        r.symbol: ("etf" if (r.etf or r.symbol in etn_set) else "equity")
        for r in rows if not r.test_issue
    }

    pairs, unfunded, counts = _filter_pairs(name_changes, premium, min_premium)
    for entry in merger_continuations:
        pairs.append(_Pair(
            str(entry["old"]).strip().upper(), str(entry["new"]).strip().upper(),
            str(entry["process_date"]), str(entry.get("source") or "merger_continuation"),
        ))
    counts["after_merger_continuations"] = len(pairs)

    involved = {s for p in pairs for s in (p.old, p.new)}
    corpus_dates = dates_for(sorted(involved))

    # ORDER IS PINNED (G.2): splits first, then the overlap test PER SEGMENT.
    # Looped to a fixed point because excluding a pair withdraws the splits it
    # registered, which could in principle expose an overlap in another pair.
    excluded: list[dict[str, object]] = []
    rescued: list[dict[str, object]] = []
    for _ in range(len(pairs) + 1):
        splits, split_reasons, exempted = _build_splits(
            pairs, corpus_dates, sessions, exempt_splits)
        pairs, newly_excluded, rescued = _overlap_check(
            pairs, splits, corpus_dates, exempt_splits)
        if not newly_excluded:
            break
        excluded.extend(newly_excluded)
    collisions = _topology_check(pairs, splits)

    surviving_new = {p.new for p in pairs}
    dropped_continuations = sorted(
        ({"old": p.old, "new": p.new, "process_date": p.process_date,
          "old_side_premium_usd": premium.get(p.old, 0.0)}
         for p in unfunded if p.old in surviving_new),
        key=lambda d: (d["old"], d["process_date"]),
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "pull_date": pull_date,
        "precedence": list(CLASSES),
        "directory_snapshot": {
            "path": directory_path,
            "vintage": vintage,
            "sha256": directory_sha256,
            "symbols": len(rows),
            "etf_y": sum(1 for r in rows if r.etf),
            "test_issue_y": len(directory_test),
        },
        "source_urls": {
            "directory": DIRECTORY_SOURCE_URL,
            "corporate_actions": CORPORATE_ACTIONS_URL,
        },
        "corporate_actions_span": {"start": start_date, "end": pull_date,
                                   "types": "name_change"},
        "adjusted_rule": {
            "regex": ADJUSTED_PATTERN,
            "description": "a root with trailing digits is `adjusted` (rule, never an "
                           "enumeration); EXCLUDED from every arm, counted with premium",
        },
        "etn_rule": {"regex": ETN_PATTERN, "field": "Security Name",
                     "case_sensitive": True},
        "etn_list": etn,
        "test_list": {"directory": directory_test, "opra_only": test_roots},
        "index_list": {"index_roots": sorted(INDEX_ROOTS), "additions": index_additions},
        "directory_class": dict(sorted(directory_class.items())),
        "delisted": delisted,
        "dotted_map": dict(sorted(dotted.items())),
        "dotted_map_active": dotted_active,
        "dotted_dropped": dotted_dropped,
        # recorded so "normalized in BOTH directions" stays on the record; never
        # consulted by classify()/resolve(). See module docstring note 1.
        "dollar_forms_not_mapped": dollar_forms,
        "successor_map": sorted(
            ({"old": p.old, "new": p.new, "process_date": p.process_date, "source": p.source}
             for p in pairs),
            key=lambda e: (e["old"], e["process_date"]),
        ),
        "reuse_splits": splits,
        "reuse_split_reasons": split_reasons,
        "exempted_splits": exempted,
        "exempt_splits_review": exempt_splits,
        "excluded_overlaps": excluded,
        "overlap_rescued_by_split": rescued,
        "topology_collisions": collisions,
        "chain_continuations_dropped_by_premium_filter": dropped_continuations,
        "min_old_side_premium_usd": min_premium,
        "legacy_etf_set": sorted(legacy_etf_set),
        "counts": counts,
    }


# --------------------------------------------------------------------------
# the loader — the ONE fence every future tool imports
# --------------------------------------------------------------------------


class RootClassification:
    """Read side of the registered snapshot: `classify()` and `resolve()`."""

    def __init__(self, doc: Mapping[str, object]) -> None:
        version = doc.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"root_classification schema_version {version!r} != {SCHEMA_VERSION} — "
                "this loader cannot read that snapshot; regeneration is a new addendum")
        self.doc = doc
        self._test = set(doc["test_list"]["directory"]) | set(doc["test_list"]["opra_only"])
        self._index = set(doc["index_list"]["index_roots"]) | set(doc["index_list"]["additions"])
        self._directory_class: dict[str, str] = dict(doc["directory_class"])
        # the review assigns a class per root; the rest of the entry is its notes
        self._delisted: dict[str, str] = {
            k: str(v["class"]) for k, v in doc["delisted"].items()}
        self._dotted: dict[str, str] = dict(doc["dotted_map"])
        self._adjusted = re.compile(str(doc["adjusted_rule"]["regex"]))
        self._renames: dict[str, list[tuple[str, str]]] = {}
        for entry in doc["successor_map"]:
            self._renames.setdefault(entry["old"], []).append(
                (entry["process_date"], entry["new"]))
        self._renames = {k: sorted(v) for k, v in self._renames.items()}
        self._splits: dict[str, list[str]] = {
            k: sorted(v) for k, v in doc["reuse_splits"].items()}

    # -- normalization ----------------------------------------------------
    def occ_form(self, symbol: str) -> str:
        """Directory form -> OCC form (`BRK.B` -> `BRKB`)."""
        return occ_form(symbol)

    def directory_form(self, root: str) -> str | None:
        """OCC form -> directory form (`BRKB` -> `BRK.B`); None when there is none.

        `.` rows only — a `$` (preferred) row can never be the answer, so `GAMB`
        stays `GAMB` and reaches the layer-2 census. A plain directory row still
        wins outright.
        """
        if root in self._directory_class or root in self._test:
            return root
        return self._dotted.get(root)

    def split_boundaries(self, root: str) -> list[str]:
        """The registered reuse-split dates for `root`, earliest first.

        Read-only accessor over the frozen snapshot, added so consumers ask the
        LOADER rather than reaching into the raw JSON. Two things need it:
        `resolve()` alone cannot say whether a segment is the last one, and a
        root with NO boundaries is provably date-invariant under `resolve()`
        (`_segment` returns (None, None), so every rename hop applies at any
        date) — which is what makes caching a resolve by root alone safe.
        """
        return list(self._splits.get(occ_form(root.strip().upper()), ()))

    # -- the fence --------------------------------------------------------
    def resolve(self, root: str, date: str | None = None) -> str | None:
        """Terminal symbol for bars/price/classification reads.

        Follows the rename chain (multi-hop) through the segment `date` falls
        in, then hands back the DIRECTORY form so bars stored under `BRK.B` are
        reachable. `date=None` means the pull date.

        Returns None when the identity is UNKNOWABLE: `date` falls in a
        non-last reuse segment of a root the chain never left — the era's
        tenant departed without a rename (delisted/acquired), so its history
        lives under NO current symbol (Alpaca keys bars by the CURRENT asset;
        the reused ticker serves the successor's history). Ex: Barnes-era `B`
        (pre-2025-05-09), pre-reuse `ARIS`. Consumers see a bars miss ->
        drop_no_bars / `unclassified`. Delta-review-A MAJOR, 2026-08-02.
        """
        plain = occ_form(root.strip().upper())
        terminal = _terminal(plain, date, self._renames, self._splits)
        if date is not None and terminal == plain:
            bounds = self._splits.get(plain)
            if bounds and date < bounds[-1]:
                return None
        return self.directory_form(terminal) or terminal

    def classify(self, root: str, date: str | None = None) -> str:
        """`adjusted | test | index | etf | equity | unclassified`, in precedence."""
        raw = root.strip().upper()
        # G.8 "Mapping first": trailing digits decide before anything is mapped.
        if self._adjusted.search(raw):
            return "adjusted"
        terminal = self.resolve(raw, date)
        if terminal is None:
            return "unclassified"
        plain = occ_form(terminal)
        if terminal in self._test or plain in self._test:
            return "test"
        if terminal in self._index or plain in self._index:
            return "index"
        cls = self._directory_class.get(terminal) or self._directory_class.get(
            self._dotted.get(plain, ""))
        if cls:
            return cls
        return self._delisted.get(plain, UNCLASSIFIED)


def load(path: str | Path) -> RootClassification:
    """Load the registered snapshot. This is the import every future tool makes."""
    return RootClassification(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# corpus reads (read-only, always)
# --------------------------------------------------------------------------


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def corpus_premium(db_path: Path) -> dict[str, float]:
    """TOTAL (all-sides) premium per underlying — G.2's registered filter base."""
    with _connect_ro(db_path) as conn:
        return {r["underlying"]: float(r["p"]) for r in conn.execute(
            "SELECT underlying, SUM(premium_usd) AS p FROM flow_rollup GROUP BY underlying")}


def corpus_sessions(db_path: Path) -> list[str]:
    """The corpus session calendar — what `gap_sessions` is measured on."""
    with _connect_ro(db_path) as conn:
        return sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM corpus_sessions"))


def corpus_dates_for(db_path: Path) -> Callable[[Iterable[str]], dict[str, set[str]]]:
    def _dates(symbols: Iterable[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        with _connect_ro(db_path) as conn:
            for sym in symbols:
                out[sym] = {r[0] for r in conn.execute(
                    "SELECT DISTINCT date FROM flow_rollup WHERE underlying = ?", (sym,))}
        return out
    return _dates


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def _load_json(path: str | None, default):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else default


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", required=True, help="output JSON path (never defaulted)")
    p.add_argument("--directory", default=str(DEFAULT_DIRECTORY),
                   help="the COMMITTED directory snapshot")
    p.add_argument("--corpus-db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--backfill-db", default=None,
                   help="crawl DB (backfill.db) for extended-span old-side evidence")
    p.add_argument("--pull-date", required=True,
                   help="the obligation-2 pull date, YYYY-MM-DD (never today() — the "
                        "snapshot's identity must not depend on when it was run)")
    p.add_argument("--start-date", default=CA_START_DATE)
    p.add_argument("--min-premium", type=float, default=MIN_OLD_SIDE_PREMIUM)
    p.add_argument("--expect-after-floor", type=int,
                   help="registered count of plain-alphabetic funded records (138); "
                        "REQUIRED for the registered run, warn-only without it")
    p.add_argument("--expect-pairs", type=int,
                   help="registered count of mapped pairs after `old != new` (106)")
    p.add_argument("--force-new-addendum", action="store_true",
                   help="overwrite an existing --out; the snapshot is FROZEN once "
                        "generated, so regeneration is a new dated addendum")
    p.add_argument("--test-roots", help="JSON object {root: {...}} — the test-root review")
    p.add_argument("--index-additions", help="JSON object {root: {...}} — the index review")
    p.add_argument("--delisted",
                   help='JSON object {root: {"class": ..., ...}} — the census review')
    p.add_argument("--merger-continuations",
                   help='JSON list [{"old","new","process_date","source"}]')
    p.add_argument("--exempt-splits",
                   help='JSON object {symbol: {"reason": ...}} — splits the review '
                        "withdraws (a dual-listing consolidation is not a ticker reuse)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    load_dotenv(ROOT / ".env")

    out = Path(args.out)
    if out.exists() and not args.force_new_addendum:
        logger.error("%s already exists — the snapshot is frozen once generated. "
                     "Regeneration is a new dated addendum; pass --force-new-addendum "
                     "only once that addendum is written.", out)
        return 1
    for flag, value in (("--expect-after-floor", args.expect_after_floor),
                        ("--expect-pairs", args.expect_pairs)):
        if value is None:
            logger.warning("%s not given — the registered run must pass it", flag)

    key = os.environ.get("ALPACA_API_KEY") or os.environ["APCA_API_KEY_ID"]
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ["APCA_API_SECRET_KEY"]

    directory_path = Path(args.directory)
    directory_text = directory_path.read_text(encoding="utf-8")
    sha = hashlib.sha256(directory_text.encode("utf-8")).hexdigest()

    db_path = Path(args.corpus_db)
    logger.info("summing corpus premium (all sides) from %s", db_path)
    premium = corpus_premium(db_path)
    logger.info("corpus underlyings: %d", len(premium))

    dates_fn = corpus_dates_for(db_path)
    if args.backfill_db:
        # Extended-span evidence (backfill addendum): the corpus DB starts
        # 2025-07-25, so renames processed before it have NO old-side premium or
        # trading dates there — the crawl DB (per-root ≥$25k groups over
        # 2024-02-15..2025-07-25) supplies both. Roots ARE the contemporaneous
        # symbols on that side of a rename, which is exactly the old-side
        # evidence the premium floor and the gap/overlap tests need.
        bf_path = Path(args.backfill_db)
        logger.info("merging backfill evidence from %s", bf_path)
        with _connect_ro(bf_path) as conn:
            for r in conn.execute(
                    "SELECT root, SUM(premium) AS p FROM prints GROUP BY root"):
                premium[r["root"]] = premium.get(r["root"], 0.0) + float(r["p"])

        corpus_dates = dates_fn

        def _merged_dates(symbols):
            out = corpus_dates(symbols)
            with _connect_ro(bf_path) as conn:
                for sym in symbols:
                    out.setdefault(sym, set()).update(
                        row[0] for row in conn.execute(
                            "SELECT DISTINCT session FROM prints WHERE root = ?",
                            (sym,)))
            return out
        dates_fn = _merged_dates

    name_changes = fetch_name_changes(
        args.start_date, args.pull_date, api_key=key, api_secret=secret)
    if not name_changes:
        logger.error("the corporate-actions pull returned ZERO name_change records for "
                     "%s..%s — an empty successor map would silently unmap every rename. "
                     "Nothing written.", args.start_date, args.pull_date)
        return 1

    from tools.multileg_arm_probe import ETF_SET  # noqa: PLC0415 — heavy, build-only

    doc = build_snapshot(
        directory_text=directory_text,
        directory_path=str(directory_path.relative_to(ROOT)) if directory_path.is_relative_to(ROOT)
        else str(directory_path),
        directory_sha256=sha,
        name_changes=name_changes,
        premium=premium,
        dates_for=dates_fn,
        sessions=corpus_sessions(db_path),
        legacy_etf_set=ETF_SET,
        pull_date=args.pull_date,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        start_date=args.start_date,
        test_roots=_load_json(args.test_roots, {}),
        index_additions=_load_json(args.index_additions, {}),
        delisted=_load_json(args.delisted, {}),
        merger_continuations=_load_json(args.merger_continuations, []),
        exempt_splits=_load_json(args.exempt_splits, {}),
        min_premium=args.min_premium,
    )

    sidecar = Path(f"{args.out}.collisions.json")
    if doc["topology_collisions"]:
        sidecar.write_text(json.dumps(
            {"topology_collisions": doc["topology_collisions"], "counts": doc["counts"],
             "generated_at": doc["generated_at"], "pull_date": doc["pull_date"]},
            indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for c in doc["topology_collisions"]:
            logger.error("TOPOLOGY COLLISION: %s -> %s (segment from %s)",
                         c["old_roots"], c["terminal_symbol"], c["segment_start"])
        logger.error("map NOT frozen; resolve the collisions and re-run. wrote %s", sidecar)
        return 1

    for flag, expected, actual in (
        ("--expect-after-floor", args.expect_after_floor,
         doc["counts"]["after_old_side_premium_floor"]),
        ("--expect-pairs", args.expect_pairs, len(doc["successor_map"])),
    ):
        if expected is not None and expected != actual:
            logger.error("%s = %d but the build computed %d — the registered quantity "
                         "does not reproduce. Nothing written.", flag, expected, actual)
            return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar.unlink(missing_ok=True)   # a stale one would outlive the failure it recorded
    logger.info("wrote %s | successor pairs %d | splits %d | exempted %d | excluded %d "
                "| ETN %d", out, len(doc["successor_map"]), len(doc["reuse_splits"]),
                len(doc["exempted_splits"]), len(doc["excluded_overlaps"]),
                len(doc["etn_list"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
