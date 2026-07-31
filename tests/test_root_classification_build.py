"""The registered root classification (Addendum G obligation 2).

Hermetic: the corporate-actions feed is a fixture, the corpus is two in-memory
dicts. The DIRECTORY half runs against the real committed snapshot — the counts
below (13,074 / 5,563 / 33 / 60) are registered quantities in G.8, so a parser
that drifts off them must fail here rather than in the one-shot run.

The one non-hermetic test is the integration guard at the bottom: it reproduces
G.2's pre-registered rename counts against the live feed + the real corpus, and
is skipped unless explicitly opted into.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from db.flow_corpus import DEFAULT_DB_PATH, INDEX_ROOTS
from tools.root_classification_build import (
    RootClassification,
    _overlap_check,
    _Pair,
    build_snapshot,
    load,
    occ_form,
    parse_directory,
)

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "signals" / "options_flow" / "nasdaqtraded_20260730.txt"

# --------------------------------------------------------------------------
# the synthetic corporate-actions feed
# --------------------------------------------------------------------------

NAME_CHANGES = [
    # a plain rename
    {"old_symbol": "OLDA", "new_symbol": "NEWA", "process_date": "2026-01-15"},
    # a multi-hop chain: HOPA -> HOPB -> HOPC
    {"old_symbol": "HOPA", "new_symbol": "HOPB", "process_date": "2025-09-01"},
    {"old_symbol": "HOPB", "new_symbol": "HOPC", "process_date": "2026-03-01"},
    # CUSIP-only: the ticker is unchanged, only the CUSIP moved
    {"old_symbol": "SAMEX", "new_symbol": "SAMEX", "process_date": "2025-11-01",
     "old_cusip": "111111101", "new_cusip": "111111200"},
    # CUSIP-SHAPED symbols: not plain alphabetic, dropped before the floor
    {"old_symbol": "185CNT011", "new_symbol": "185CNT011", "process_date": "2026-02-05"},
    {"old_symbol": "812CNT012", "new_symbol": "SEAV", "process_date": "2026-02-09"},
    # reuse, the ARIS shape: the NEW symbol already has earlier corpus rows
    {"old_symbol": "REUSA", "new_symbol": "SHARED", "process_date": "2026-02-19"},
    # reuse, the SPCX shape: the OLD symbol keeps printing after process_date
    {"old_symbol": "KEEPO", "new_symbol": "KEEPN", "process_date": "2026-04-07"},
    # below the $250k old-side floor
    {"old_symbol": "TINYO", "new_symbol": "TINYN", "process_date": "2026-05-01"},
]

MERGER_CONTINUATIONS = [
    {"old": "PARA", "new": "PSKY", "process_date": "2025-08-07",
     "source": "merger_continuation"},
]


def _span(first: str, last: str) -> set[str]:
    """Every 1st-of-month between two YYYY-MM dates — dates only need ordering."""
    y0, m0 = (int(x) for x in first.split("-"))
    y1, m1 = (int(x) for x in last.split("-"))
    out, y, m = set(), y0, m0
    while (y, m) <= (y1, m1):
        out.add(f"{y:04d}-{m:02d}-01")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


CORPUS_DATES = {
    "OLDA": _span("2025-08", "2026-01") - {"2026-01-01"} | {"2026-01-02"},
    "NEWA": _span("2026-02", "2026-07"),
    "HOPA": _span("2025-08", "2025-08"),
    "HOPB": _span("2025-10", "2026-02"),
    "HOPC": _span("2026-04", "2026-07"),
    "SAMEX": _span("2025-08", "2026-07"),
    # SHARED is occupied twice: an early tenant, then REUSA from 2026-02-19
    "REUSA": _span("2025-09", "2026-02"),
    "SHARED": _span("2025-08", "2025-10") | _span("2026-03", "2026-07"),
    # KEEPO goes on printing after it is renamed away
    "KEEPO": _span("2025-08", "2026-07"),
    "KEEPN": _span("2026-05", "2026-07"),
    "TINYO": _span("2025-08", "2026-04"),
    "TINYN": _span("2026-06", "2026-07"),
    "PARA": {"2025-08-06"},
    "PSKY": _span("2025-09", "2026-07"),
}

PREMIUM = {s: 5_000_000.0 for s in CORPUS_DATES}
PREMIUM["TINYO"] = 1_000.0        # under the floor
PREMIUM["SAMEX"] = 9_000_000.0    # funded, but old == new


def _dates_for(symbols):
    return {s: set(CORPUS_DATES.get(s, ())) for s in symbols}


def _build(**over):
    text = DIRECTORY.read_text(encoding="utf-8")
    kwargs = dict(
        directory_text=text,
        directory_path="signals/options_flow/nasdaqtraded_20260730.txt",
        directory_sha256="0" * 64,
        name_changes=NAME_CHANGES,
        premium=PREMIUM,
        dates_for=_dates_for,
        legacy_etf_set={"SPY", "QQQ", "GLD"},
        pull_date="2026-07-31",
        generated_at="2026-07-31T00:00:00+00:00",
        test_roots=["ZTST", "PTEST"],
        index_additions=["XAU", "NANOS"],
        delisted={"WBA": "equity", "ALTS": "etf"},
        merger_continuations=MERGER_CONTINUATIONS,
    )
    kwargs.update(over)
    return build_snapshot(**kwargs)


@pytest.fixture(scope="module")
def doc():
    return _build()


@pytest.fixture(scope="module")
def fence(doc):
    return RootClassification(doc)


# --------------------------------------------------------------------------
# the directory snapshot — registered counts
# --------------------------------------------------------------------------


def test_directory_parses_to_the_registered_counts():
    rows, vintage = parse_directory(DIRECTORY.read_text(encoding="utf-8"))
    assert len(rows) == 13_074
    assert sum(1 for r in rows if r.etf) == 5_563
    assert vintage == "0730202618:02"


def test_test_issue_rows_are_33_and_all_etf_n():
    rows, _ = parse_directory(DIRECTORY.read_text(encoding="utf-8"))
    tests = [r for r in rows if r.test_issue]
    assert len(tests) == 33
    assert all(not r.etf for r in tests)
    assert {"CBO", "CBX", "CTEST", "NTEST", "ZAZZT", "ZJZZT"} <= {r.symbol for r in tests}


def test_etn_regex_matches_60_split_44_44_16(doc):
    rows, _ = parse_directory(DIRECTORY.read_text(encoding="utf-8"))
    by_symbol = {r.symbol: r for r in rows}
    etn = doc["etn_list"]
    assert len(etn) == 60
    assert sum(1 for s in etn if by_symbol[s].etf) == 44
    n_flagged = sorted(s for s in etn if not by_symbol[s].etf)
    assert n_flagged == ["AMUB", "ATMP", "BDCZ", "DGP", "DGZ", "DJP", "DZZ", "FNGD",
                         "FNGO", "GRN", "MLPB", "PFFL", "SMHB", "UCIB", "VXX", "VXZ"]


def test_etn_regex_is_case_sensitive_and_word_bounded():
    from tools.root_classification_build import _ETN_RE
    assert _ETN_RE.search("iPath Series B S&P 500 VIX Short-Term Futures ETN")
    assert _ETN_RE.search("Some Exchange-Traded Note due 2030")
    assert _ETN_RE.search("Some Exchange Traded Note due 2030")
    assert _ETN_RE.search("Two ETNs linked to something")
    assert not _ETN_RE.search("Etn Capital Corporation")     # case-sensitive
    assert not _ETN_RE.search("BIOTECH Corp")                # word-bounded


def test_no_directory_symbol_contains_a_digit():
    """G.8 leans on this: the `adjusted` rule cannot swallow a real root."""
    rows, _ = parse_directory(DIRECTORY.read_text(encoding="utf-8"))
    assert not [r.symbol for r in rows if any(c.isdigit() for c in r.symbol)]


def test_malformed_row_raises_rather_than_being_skipped():
    text = "Nasdaq Traded|Symbol|Security Name|ETF|Test Issue\nY|A|Agilent|N|N\nY|B|Oops\n"
    with pytest.raises(ValueError, match="malformed"):
        parse_directory(text)


def test_missing_footer_raises():
    text = "Nasdaq Traded|Symbol|Security Name|ETF|Test Issue\nY|A|Agilent|N|N\n"
    with pytest.raises(ValueError, match="File Creation Time"):
        parse_directory(text)


# --------------------------------------------------------------------------
# dotted normalization, both directions
# --------------------------------------------------------------------------


def test_occ_form_strips_dot_and_dollar():
    assert occ_form("BRK.B") == "BRKB"
    assert occ_form("ABR$D") == "ABRD"
    assert occ_form("AAPL") == "AAPL"


@pytest.mark.parametrize("occ,directory", [
    ("BRKB", "BRK.B"), ("BFB", "BF.B"), ("HEIA", "HEI.A"), ("PBRA", "PBR.A"),
])
def test_the_four_class_share_forms_map_both_ways(fence, occ, directory):
    assert fence.doc["dotted_map"][occ] == directory
    assert fence.occ_form(directory) == occ
    assert fence.directory_form(occ) == directory
    assert fence.resolve(occ) == directory
    assert fence.resolve(directory) == directory
    assert fence.classify(occ) == "equity"
    assert fence.classify(directory) == "equity"


def test_a_stripped_form_never_shadows_a_real_listed_symbol(fence, doc):
    """`GL$D` strips to `GLD`. The gold ETF must win, and the shadow be recorded."""
    assert "GLD" not in doc["dotted_map"]
    assert fence.classify("GLD") == "etf"
    assert fence.classify("META") == "equity"
    dropped = {d["occ_form"]: d for d in doc["dotted_dropped"]}
    assert dropped["GLD"]["directory_symbols"] == ["GL$D"]
    assert dropped["GLD"]["reason"] == "shadows_plain_directory_row"
    assert dropped["META"]["directory_symbols"] == ["MET$A"]


def test_an_ambiguous_stripped_form_is_dropped_and_recorded(doc):
    """`ET$I` and `ETI$` both strip to `ETI` — unresolvable, so neither is used."""
    dropped = {d["occ_form"]: d for d in doc["dotted_dropped"]}
    assert dropped["ETI"]["reason"] == "ambiguous_multiple_directory_rows"
    assert dropped["ETI"]["directory_symbols"] == ["ET$I", "ETI$"]
    assert "ETI" not in doc["dotted_map"]


# --------------------------------------------------------------------------
# the map filter
# --------------------------------------------------------------------------


def test_cusip_only_and_cusip_shaped_records_are_dropped(doc):
    pairs = {(e["old"], e["new"]) for e in doc["successor_map"]}
    assert ("SAMEX", "SAMEX") not in pairs
    assert not [e for e in doc["successor_map"] if not e["old"].isalpha()]
    assert doc["counts"]["name_change_records_raw"] == len(NAME_CHANGES)
    assert doc["counts"]["after_plain_alphabetic"] == 7      # the two CUSIP-shaped go
    assert doc["counts"]["after_old_side_premium_floor"] == 6   # TINYO under the floor
    assert doc["counts"]["after_old_ne_new"] == 5              # SAMEX (old == new) goes


def test_the_merger_continuation_parameter_lands_in_the_map(doc):
    entry = next(e for e in doc["successor_map"] if e["old"] == "PARA")
    assert entry == {"old": "PARA", "new": "PSKY", "process_date": "2025-08-07",
                     "source": "merger_continuation"}
    assert doc["counts"]["after_merger_continuations"] == 6


def test_a_below_floor_rename_never_enters_the_map(doc):
    assert not [e for e in doc["successor_map"] if e["old"] == "TINYO"]


def test_a_dropped_chain_continuation_is_recorded(doc):
    """TINYO is nobody's successor here, so the record is empty — but the field
    exists, because the registered floor applies per hop and can truncate one."""
    assert doc["chain_continuations_dropped_by_premium_filter"] == []


def test_a_below_floor_rename_that_continues_a_chain_is_recorded():
    doc = _build(
        name_changes=NAME_CHANGES + [
            {"old_symbol": "NEWA", "new_symbol": "LASTA", "process_date": "2026-06-01"}],
        premium=PREMIUM | {"NEWA": 1_000.0},
    )
    assert doc["chain_continuations_dropped_by_premium_filter"] == [
        {"old": "NEWA", "new": "LASTA", "process_date": "2026-06-01",
         "old_side_premium_usd": 1_000.0}]
    assert RootClassification(doc).resolve("OLDA") == "NEWA"   # truncated, as registered


# --------------------------------------------------------------------------
# terminal-symbol resolution
# --------------------------------------------------------------------------


def test_a_plain_rename_resolves_to_the_successor(fence):
    assert fence.resolve("OLDA") == "NEWA"
    assert fence.resolve("OLDA", "2025-09-01") == "NEWA"   # reads are NOT date-scoped
    assert fence.resolve("NEWA") == "NEWA"


def test_a_multi_hop_chain_resolves_to_its_last_symbol(fence):
    assert fence.resolve("HOPA") == "HOPC"
    assert fence.resolve("HOPB") == "HOPC"
    assert fence.resolve("HOPC") == "HOPC"


def test_the_merger_continuation_resolves(fence):
    assert fence.resolve("PARA") == "PSKY"


def test_an_unknown_root_resolves_to_itself(fence):
    assert fence.resolve("ZZQQ") == "ZZQQ"


def test_resolution_is_case_and_whitespace_insensitive(fence):
    assert fence.resolve(" hopa ") == "HOPC"


def test_a_rename_cycle_terminates(fence):
    doc = _build(name_changes=[
        {"old_symbol": "LOOPA", "new_symbol": "LOOPB", "process_date": "2025-09-01"},
        {"old_symbol": "LOOPB", "new_symbol": "LOOPA", "process_date": "2026-03-01"},
    ], premium={"LOOPA": 5e6, "LOOPB": 5e6})
    assert RootClassification(doc).resolve("LOOPA") in {"LOOPA", "LOOPB"}


# --------------------------------------------------------------------------
# reuse splits
# --------------------------------------------------------------------------


def test_a_new_symbol_with_earlier_corpus_rows_splits(doc):
    """The ARIS shape: SHARED was occupied before REUSA renamed into it."""
    assert doc["reuse_splits"]["SHARED"] == ["2026-02-19"]
    reason = next(r for r in doc["reuse_split_reasons"] if r["symbol"] == "SHARED")
    assert reason["reason"] == "new_symbol_has_earlier_corpus_rows"
    assert reason["pair"] == "REUSA->SHARED"


def test_an_old_symbol_that_keeps_printing_splits(doc):
    """The SPCX shape: KEEPO is renamed away and the ticker keeps printing."""
    assert doc["reuse_splits"]["KEEPO"] == ["2026-04-07"]
    reason = next(r for r in doc["reuse_split_reasons"] if r["symbol"] == "KEEPO")
    assert reason["reason"] == "old_symbol_keeps_printing"


def test_the_split_makes_resolution_date_scoped(fence):
    """Before the seam KEEPO is the predecessor; at and after it, the new tenant."""
    assert fence.resolve("KEEPO", "2026-01-01") == "KEEPN"
    assert fence.resolve("KEEPO", "2026-04-07") == "KEEPO"
    assert fence.resolve("KEEPO", "2026-06-01") == "KEEPO"
    assert fence.resolve("KEEPO") == "KEEPO"          # None = the pull date
    assert fence.resolve("KEEPN") == "KEEPN"


def test_a_clean_rename_needs_no_split(doc):
    assert "OLDA" not in doc["reuse_splits"]
    assert "NEWA" not in doc["reuse_splits"]
    assert "PARA" not in doc["reuse_splits"]
    assert "PSKY" not in doc["reuse_splits"]


# --------------------------------------------------------------------------
# the overlap guard
# --------------------------------------------------------------------------


def test_an_overlapping_pair_is_excluded_and_recorded():
    """The guard itself, exercised with the split withheld.

    It watches one direction: the SUCCESSOR was already printing before the seam
    with no registered split to explain it, so the pair is not a clean identity
    continuation. Through the registered pipeline the branch is unreachable —
    that condition is exactly what fires a split — which is why it is a standing
    consistency guard on the split builder rather than an expected filter.
    """
    pair = _Pair("AAA", "BBB", "2026-03-01", "name_change")
    dates = {"AAA": {"2026-02-01", "2026-04-01"}, "BBB": {"2026-02-01", "2026-05-01"}}
    kept, excluded, _ = _overlap_check([pair], {}, dates)
    assert kept == []
    assert excluded == [{"old": "AAA", "new": "BBB", "process_date": "2026-03-01",
                         "overlapping_dates": ["2026-02-01"]}]


def test_the_split_rescues_the_same_pair_and_the_rescue_is_recorded():
    pair = _Pair("AAA", "BBB", "2026-03-01", "name_change")
    dates = {"AAA": {"2026-02-01", "2026-04-01"}, "BBB": {"2026-02-01", "2026-05-01"}}
    kept, excluded, rescued = _overlap_check([pair], {"BBB": ["2026-03-01"]}, dates)
    assert kept == [pair]
    assert excluded == []
    assert rescued == [{"old": "AAA", "new": "BBB", "process_date": "2026-03-01",
                        "raw_overlapping_dates": ["2026-02-01"]}]


def test_the_predecessor_segment_is_hard_cut_at_process_date():
    """G.2's date-scoping: `date < process_date` is the predecessor, always — so
    an old symbol that keeps printing can never drag post-seam rows into the
    merged identity, split or no split."""
    pair = _Pair("AAA", "BBB", "2026-03-01", "name_change")
    dates = {"AAA": {"2026-02-01", "2026-04-01"}, "BBB": {"2026-04-01"}}
    kept, excluded, _ = _overlap_check([pair], {}, dates)
    assert kept == [pair] and excluded == []


def test_the_registered_pipeline_excludes_nothing_here(doc):
    assert doc["excluded_overlaps"] == []
    assert {r["old"] for r in doc["overlap_rescued_by_split"]} == {"REUSA", "KEEPO"}


# --------------------------------------------------------------------------
# topology
# --------------------------------------------------------------------------


def test_two_old_roots_on_one_terminal_segment_fail_the_build():
    doc = _build(
        name_changes=[
            {"old_symbol": "COLA", "new_symbol": "TERM", "process_date": "2026-01-01"},
            {"old_symbol": "COLB", "new_symbol": "TERM", "process_date": "2026-01-01"},
        ],
        premium={"COLA": 5e6, "COLB": 5e6, "TERM": 5e6},
        merger_continuations=[],
    )
    assert doc["topology_collisions"] == [
        {"terminal_symbol": "TERM", "segment_start": None, "old_roots": ["COLA", "COLB"]}]


def test_the_cli_refuses_to_freeze_a_colliding_map(tmp_path, monkeypatch, capsys):
    """Recorded AND loud: the map is written to a `.collisions.json` sidecar and
    the registered path is left untouched, with a non-zero exit code."""
    from tools import root_classification_build as rcb

    colliding = _build(
        name_changes=[
            {"old_symbol": "COLA", "new_symbol": "TERM", "process_date": "2026-01-01"},
            {"old_symbol": "COLB", "new_symbol": "TERM", "process_date": "2026-01-01"},
        ],
        premium={"COLA": 5e6, "COLB": 5e6, "TERM": 5e6},
        merger_continuations=[],
    )
    monkeypatch.setattr(rcb, "fetch_name_changes", lambda *a, **k: [])
    monkeypatch.setattr(rcb, "corpus_premium", lambda p: {})
    monkeypatch.setattr(rcb, "corpus_dates_for", lambda p: _dates_for)
    monkeypatch.setattr(rcb, "build_snapshot", lambda **k: colliding)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")

    out = tmp_path / "root_classification.json"
    rc = rcb.main(["--out", str(out), "--corpus-db", str(tmp_path / "nope.db")])
    assert rc == 1
    assert not out.exists()
    sidecar = tmp_path / "root_classification.json.collisions.json"
    assert json.loads(sidecar.read_text())["topology_collisions"]


def test_a_sequential_reuse_of_one_ticker_is_not_a_collision():
    """Two tenants of TERM, separated by a registered split, are two identities."""
    doc = _build(
        name_changes=[
            {"old_symbol": "COLA", "new_symbol": "TERM", "process_date": "2026-01-01"},
            {"old_symbol": "COLB", "new_symbol": "TERM", "process_date": "2026-06-01"},
        ],
        premium={"COLA": 5e6, "COLB": 5e6, "TERM": 5e6},
        dates_for=lambda syms: {s: set(d) for s, d in {
            "COLA": _span("2025-08", "2025-12"),
            "COLB": _span("2025-08", "2026-05"),
            "TERM": _span("2026-01", "2026-07"),
        }.items() if s in set(syms)},
        merger_continuations=[],
    )
    assert doc["topology_collisions"] == []
    assert doc["reuse_splits"]["TERM"] == ["2026-06-01"]
    fence = RootClassification(doc)
    assert fence.resolve("COLA") == "TERM"
    assert fence.resolve("COLB") == "TERM"
    assert fence.resolve("TERM", "2026-03-01") == "TERM"


def test_a_straight_chain_is_not_a_collision(doc):
    assert doc["topology_collisions"] == []


# --------------------------------------------------------------------------
# the fence: precedence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("root,expected", [
    # adjusted wins over everything, on the RAW root, before any mapping
    ("BABA2", "adjusted"), ("FDX1", "adjusted"), ("SOXS2", "adjusted"),
    ("WOLF1", "adjusted"), ("CMCS1", "adjusted"), ("SPX1", "adjusted"),
    # test beats index/etf/equity
    ("ZVZZT", "test"), ("CTEST", "test"), ("CBX", "test"), ("CBO", "test"),
    ("ZTST", "test"), ("PTEST", "test"),          # the OPRA-only parameter
    # index
    ("SPX", "index"), ("SPXW", "index"), ("VIX", "index"), ("RUT", "index"),
    ("XAU", "index"), ("NANOS", "index"),         # the index-addition parameter
    # etf
    ("SPY", "etf"), ("QQQ", "etf"), ("IWM", "etf"), ("GLD", "etf"),
    ("VXX", "etf"), ("FNGD", "etf"), ("DJP", "etf"),   # ETN-sourced, ETF=N rows
    # equity
    ("AAPL", "equity"), ("NVDA", "equity"), ("META", "equity"), ("BRKB", "equity"),
    # the delisted-census parameter, with its REVIEWED class
    ("WBA", "equity"), ("ALTS", "etf"),
    # nothing matched
    ("ZZQQ", "unclassified"), ("QQZZX", "unclassified"),
])
def test_classification_precedence(fence, root, expected):
    assert fence.classify(root) == expected


def test_adjusted_precedes_the_directory_lookup(fence):
    """SOXS is a real ETF row; SOXS2 must not inherit it."""
    assert fence.classify("SOXS") == "etf"
    assert fence.classify("SOXS2") == "adjusted"


def test_a_near_miss_root_is_never_guessed_toward_a_directory_row(fence):
    """G.8: a hypothetical bare CMCS stays unclassified, never becomes CMCSA."""
    assert fence.classify("CMCSA") == "equity"
    assert fence.classify("CMCS") == "unclassified"


def test_the_identity_classifies_once_under_the_terminal_symbol(fence):
    """PARA is directory-absent; PSKY is the row that classifies it."""
    assert fence.classify("PARA") == "equity"
    assert fence.resolve("PARA") == "PSKY"


def test_index_roots_come_from_flow_corpus_not_a_copy(doc):
    assert set(doc["index_list"]["index_roots"]) == set(INDEX_ROOTS)
    assert len(doc["index_list"]["index_roots"]) == 18


def test_legacy_etf_set_is_frozen_in_the_snapshot(doc):
    assert doc["legacy_etf_set"] == ["GLD", "QQQ", "SPY"]


def test_the_real_legacy_etf_set_is_importable():
    """The build imports it rather than copying it — the seven consumers migrate."""
    from tools.multileg_arm_probe import ETF_SET
    assert len(ETF_SET) == 50 and "SPY" in ETF_SET


def test_precedence_order_is_recorded(doc):
    assert doc["precedence"] == ["adjusted", "test", "index", "etf", "equity"]


def test_the_adjusted_class_is_a_rule_not_an_enumeration(doc):
    assert doc["adjusted_rule"]["regex"] == "[0-9]+$"
    assert "adjusted_list" not in doc


# --------------------------------------------------------------------------
# snapshot shape
# --------------------------------------------------------------------------


def test_the_snapshot_carries_its_vintage_and_provenance(doc):
    snap = doc["directory_snapshot"]
    assert snap["vintage"] == "0730202618:02"
    assert snap["symbols"] == 13_074 and snap["etf_y"] == 5_563
    assert snap["test_issue_y"] == 33
    assert doc["source_urls"]["directory"].endswith("nasdaqtraded.txt")
    assert doc["source_urls"]["corporate_actions"].endswith("/v1/corporate-actions")
    assert doc["corporate_actions_span"] == {
        "start": "2025-07-25", "end": "2026-07-31", "types": "name_change"}
    assert doc["schema_version"] == 1
    assert doc["min_old_side_premium_usd"] == 250_000.0


def test_the_snapshot_is_deterministic_and_sorts_cleanly(tmp_path):
    a = json.dumps(_build(), indent=2, sort_keys=True)
    b = json.dumps(_build(), indent=2, sort_keys=True)
    assert a == b
    for key in ("etn_list", "legacy_etf_set"):
        assert _build()[key] == sorted(_build()[key])
    assert [e["old"] for e in _build()["successor_map"]] == sorted(
        e["old"] for e in _build()["successor_map"])


def test_round_trips_through_the_loader(tmp_path, doc):
    path = tmp_path / "root_classification.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    fence = load(path)
    assert fence.classify("SPY") == "etf"
    assert fence.classify("BABA2") == "adjusted"
    assert fence.resolve("HOPA") == "HOPC"
    assert fence.resolve("BRKB") == "BRK.B"


# --------------------------------------------------------------------------
# the bars-tool extension
# --------------------------------------------------------------------------


def test_the_bars_universe_filter_keeps_dotted_class_share_forms():
    from tools.flow_corpus_bars import _ticker_ok
    assert _ticker_ok("BRK.B") and _ticker_ok("BF.B")
    assert _ticker_ok("HEI.A") and _ticker_ok("PBR.A")
    assert _ticker_ok("AAPL") and _ticker_ok("BABA2")
    assert not _ticker_ok("") and not _ticker_ok("/CL") and not _ticker_ok("ABR$D")


# --------------------------------------------------------------------------
# integration: G.2's pre-registered counts, against the live feed + real corpus
# --------------------------------------------------------------------------

_DB = Path(os.environ.get("HMT_FLOW_CORPUS_DB", str(DEFAULT_DB_PATH)))


@pytest.mark.skipif(
    os.environ.get("HMT_ROOT_CLASSIFICATION_INTEGRATION") != "1" or not _DB.exists(),
    reason="needs HMT_ROOT_CLASSIFICATION_INTEGRATION=1, the real corpus DB and network",
)
def test_the_registered_rename_counts_reproduce():
    """G.2: 138 plain-alphabetic funded records, 106 after `old != new`."""
    from tools.root_classification_build import (
        CA_START_DATE,
        corpus_dates_for,
        corpus_premium,
        fetch_name_changes,
    )

    key = os.environ.get("ALPACA_API_KEY") or os.environ["APCA_API_KEY_ID"]
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ["APCA_API_SECRET_KEY"]
    doc = build_snapshot(
        directory_text=DIRECTORY.read_text(encoding="utf-8"),
        directory_path=str(DIRECTORY),
        directory_sha256="0" * 64,
        name_changes=fetch_name_changes(
            CA_START_DATE, "2026-07-30", api_key=key, api_secret=secret),
        premium=corpus_premium(_DB),
        dates_for=corpus_dates_for(_DB),
        legacy_etf_set=(),
        pull_date="2026-07-30",
        generated_at="2026-07-30T00:00:00+00:00",
        merger_continuations=MERGER_CONTINUATIONS,
    )
    assert doc["counts"]["after_old_side_premium_floor"] == 138
    assert doc["counts"]["after_old_ne_new"] == 106
    assert doc["topology_collisions"] == []
    assert doc["excluded_overlaps"] == []
