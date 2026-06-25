"""One-shot status probe for the PC (Norgate) decision-engine work.

Run on the Windows PC from the repo root:  python tools/pc_decision_engine_status.py
Then paste the whole output back. Read-only; mutates nothing. Tells us exactly what
already exists (bars pull, labeled dataset, trained artifacts, feature breadth) so we
don't redo any of it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=20
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"(error: {exc})"


def hr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * 4}")


def db_summary(path: Path) -> None:
    if not path.exists():
        print(f"  {path}: MISSING")
        return
    size_mb = path.stat().st_size / 1e6
    print(f"  {path}  ({size_mb:.1f} MB)")
    try:
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        print(f"    tables: {tables}")
        for t in tables:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
                line = f"    {t}: {n} rows"
                if "source" in cols:
                    srcs = [dict(r) for r in con.execute(
                        f"SELECT source, COUNT(*) c FROM {t} GROUP BY source")]
                    line += f" | sources={srcs}"
                if "symbol" in cols:
                    s = con.execute(
                        f"SELECT COUNT(DISTINCT symbol) FROM {t}").fetchone()[0]
                    line += f" | symbols={s}"
                date_col = next(
                    (c for c in ("date", "entry_date", "trade_date") if c in cols), None)
                if date_col:
                    lo, hi = con.execute(
                        f"SELECT MIN({date_col}), MAX({date_col}) FROM {t}").fetchone()
                    line += f" | {date_col} {lo}..{hi}"
                if "outcome" in cols:
                    oc = [dict(r) for r in con.execute(
                        f"SELECT outcome, COUNT(*) c FROM {t} GROUP BY outcome")]
                    line += f" | outcomes={oc}"
                print(line)
            except Exception as exc:  # noqa: BLE001
                print(f"    {t}: (error {exc})")
        con.close()
    except Exception as exc:  # noqa: BLE001
        print(f"    (could not open: {exc})")


def main() -> None:
    hr("GIT STATE")
    print("branch:", _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]))
    dirty = _run(["git", "status", "--porcelain"])
    print("uncommitted changes:", "YES\n" + dirty if dirty else "none")
    print("recent commits:")
    print(_run(["git", "log", "--oneline", "-8"]))

    hr("RAW DATA (gitignored, local-only)")
    db_summary(ROOT / "data" / "research" / "bars.db")
    db_summary(ROOT / "data" / "research" / "labeled.db")
    # Any other *.db the pipeline may have produced
    research_dir = ROOT / "data" / "research"
    if research_dir.exists():
        others = [p.name for p in research_dir.glob("*.db")
                  if p.name not in {"bars.db", "labeled.db"}]
        if others:
            print("  other *.db in data/research:", others)

    hr("TRAINED ARTIFACTS (research/artifacts/)")
    adir = ROOT / "research" / "artifacts"
    for name in ("model.json", "calibrator.json", "ev_table.json", "meta.json"):
        p = adir / name
        print(f"  {name}: {'present' if p.exists() else 'MISSING'}"
              + (f"  (tracked={'yes' if _is_tracked(p) else 'NO — gitignored/untracked'})"
                 if p.exists() else ""))
    meta = adir / "meta.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text())
            for k in ("data_source", "n_samples", "n_features", "survivorship_free",
                      "date_range", "base_rate", "brier", "low_confidence"):
                v = d.get(k, (d.get("honesty") or {}).get(k))
                print(f"    meta.{k}: {v}")
            fn = d.get("feature_names") or []
            print(f"    meta.feature_names ({len(fn)}): {fn}")
        except Exception as exc:  # noqa: BLE001
            print(f"    (could not parse meta.json: {exc})")

    hr("FEATURE BREADTH (research/features.py compute_features)")
    feats = (ROOT / "research" / "features.py")
    if feats.exists():
        txt = feats.read_text(errors="ignore")
        for probe in ("regime", "breadth", "vix", "support", "resistance",
                      "sector", "rel_strength_63", "spy_vs_sma", "dist_to_nearest"):
            print(f"    mentions '{probe}': {'YES' if probe in txt.lower() else 'no'}")
    print("\n(Done. Paste this whole output back.)")


def _is_tracked(p: Path) -> bool:
    rel = p.relative_to(ROOT).as_posix()
    out = _run(["git", "ls-files", "--error-unmatch", rel])
    return rel in out


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
