"""How much does the number check catch? Fake every printed number and count survivors.

Every quantity in brief.md and the production memos is multiplied by 1.03 / 1.07 /
1.13 (rounded to its printed precision) and checked in its own sentence's scope; a fake that still "matches" our data is a
number the check could not have caught. Also reports the real unmatched count on the
untouched text, so a tighter check that starts flagging true numbers shows up.

Usage:
    python -m research.brief.number_check_eval data/briefs/2026-09-25 [--global]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from research.brief import verify

FACTORS = (1.03, 1.07, 1.13)
_PRECISION = {0: "integer", 1: "1-decimal", 2: "2-decimal"}


def evaluate(text: str, corpus: verify.Corpus) -> dict[str, Any]:
    fakes: dict[float, dict[str, Any]] = {}
    rows = verify.numbers_in_context(text, corpus.symbols)
    for factor in FACTORS:
        by: dict[str, list[int]] = {}
        for n, scope in rows:
            value = round(n["value"] * factor, n["decimals"])
            if value == n["value"]:
                continue  # prints the same as the real number (0.6% x1.03): no fake
            cell = by.setdefault(_PRECISION.get(n["decimals"], "3+ decimals"), [0, 0])
            cell[0] += corpus.matches({**n, "value": value}, scope)
            cell[1] += 1
        fakes[factor] = {"matched": sum(c[0] for c in by.values()),
                         "total": sum(c[1] for c in by.values()), "by": by}
    return {"numbers": len(rows), "real_unmatched": verify.unmatched_numbers(text, corpus),
            "fakes": fakes}


def night_files(brief_dir: Path, arm: str = "opus") -> dict[str, list[Path]]:
    """{text file: the tool results its corpus includes} — the pairs check_night uses."""
    files = {brief_dir / "brief.md": [*sorted(brief_dir.glob(f"tools_*_{arm}.txt")),
                                      brief_dir / "tools_judge.txt"]}
    for memo in sorted(brief_dir.glob(f"memo_*_{arm}.md")):
        files[memo] = [brief_dir / f"tools_{memo.stem.removeprefix('memo_')}.txt"]
    return files


def run(brief_dir: Path, *, scoped: bool = True) -> dict[str, dict[str, Any]]:
    out = {}
    for path, tools in night_files(brief_dir).items():
        if not path.exists():
            continue
        corpus = verify.night_corpus(brief_dir, tools)
        if not scoped:
            corpus.symbols = frozenset()  # every number checked against the whole corpus
        out[path.name] = evaluate(path.read_text(encoding="utf-8"), corpus)
    return out


def render(results: dict[str, dict[str, Any]]) -> str:
    lines = [f"{'file':<22} {'numbers':>7} {'real unmatched':>14} "
             + " ".join(f"{'fake x' + str(f):>11}" for f in FACTORS)]
    tot: dict[float, list[int]] = {f: [0, 0] for f in FACTORS}
    for name, r in results.items():
        cells = []
        for f in FACTORS:
            m, t = r["fakes"][f]["matched"], r["fakes"][f]["total"]
            tot[f][0] += m
            tot[f][1] += t
            cells.append(f"{m:>4}/{t:<3} {100 * m / t if t else 0:>2.0f}%")
        lines.append(f"{name:<22} {r['numbers']:>7} {len(r['real_unmatched']):>14} " + " ".join(cells))
    lines.append(f"{'overall':<22} {'':>7} {'':>14} " + " ".join(
        f"{m:>4}/{t:<3} {100 * m / t if t else 0:>2.0f}%" for m, t in tot.values()))
    lines.append("")
    lines.append("x1.07 fakes matched by precision (all files):")
    by: dict[str, list[int]] = {}
    for r in results.values():
        for k, (m, t) in r["fakes"][1.07]["by"].items():
            by.setdefault(k, [0, 0])
            by[k][0] += m
            by[k][1] += t
    lines += [f"  {k:<12} {m}/{t}" for k, (m, t) in sorted(by.items())]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("brief_dir", type=Path)
    p.add_argument("--global", dest="global_", action="store_true",
                   help="no scoping: every number against the whole corpus")
    p.add_argument("--show", action="store_true", help="list the real unmatched tokens")
    a = p.parse_args()
    results = run(a.brief_dir, scoped=not a.global_)
    print(render(results))
    if a.show:
        for name, r in results.items():
            print(f"\n{name}: {', '.join(r['real_unmatched']) or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
