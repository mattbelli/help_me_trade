"""The confirmatory abstention run — the registered 20-35% band, on the sessions that set it never saw.

Addendum C registered the direction rule after examining one session, and registered
a prediction alongside it: **abstention between 20% and 35% of premium** on the
sessions not yet examined (`FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md:60-62`).
Addendum D left that band untouched and made it binding on the **sum** of the three
abstain states (`..._ADDENDUM_D.md:135-141`). This driver measures it.

**It reports. It never tunes.** Landing outside the band is the finding — Addendum C
forbids absorbing it by adjusting the rule, so nothing here has a knob to adjust:
no threshold beyond the band, no filter beyond the registered exclusions, and the
rule itself is imported, never re-implemented (`signals/options_flow/corpus_direction.py`
is the only implementation of it that exists).

What the band is declared on, exactly:

- **Population** — the corpus-of-record sessions (`schema_version = 7`) MINUS the
  sessions already examined when the band was set. Those two are `EXAMINED_SESSIONS`
  below, with their cites. They are in-sample and cannot confirm the rule they chose.
- **Grain** — a *bucket* is `(date, underlying, right, dte_bucket, mny_bucket)`. The
  rollup's primary key is finer (it also carries `side`, `strict_side`, `multileg`,
  `cond`); those are summed into the bucket, because `net_premium = buy - sell` is a
  quantity ACROSS the `side` key, not within it. Cross-check: this grain gives 45,289
  buckets on 2025-07-28 against the 45,384 Addendum C reports for that session — the
  small gap is the v4+ moneyness re-roll, which split C's single `unknown` arm. Run
  end to end on that same (excluded, in-sample) session this reproduces **25.57%**
  abstention against Addendum D's 24.68%, with `weighting_unstable` at 16.23% against
  D's 15.8% raw-vs-clipped premium share. That is the pipeline check, not a result:
  the session is in-sample by construction and confirms nothing.
- **Weighting** — premium-weighted, never count-weighted (Addendum C:57-58: the
  disagreement is 0.24% of buckets and 15.8% of premium, so counting buckets is wrong
  by two orders of magnitude). The pooled number weights each session by its premium,
  which is identical to running `abstention_share` over every bucket of every
  confirmatory session at once.

**This is the all-prints population, and it is not free of the multi-leg sign
contamination** Addendum D measured (`..._ADDENDUM_D.md:76-98`) — that grain is stored
per bucket and a per-arm reading is a separate measurement with its own Addendum F
gate. Saying so is required: D forbids *describing* a bucket-level result as clean,
not producing the pooled one the band was actually declared on.

    python tools/confirmatory_abstention.py --out data/confirmatory_abstention.json

Read-only against the corpus: it opens `mode=ro`, so it is safe to run against the
live file while anything else is writing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.flow_corpus import DEFAULT_DB_PATH  # noqa: E402
from signals.options_flow.corpus_direction import (  # noqa: E402
    DirectionRead,
    abstention_by_state,
    abstention_share,
    direction_read,
)

#: `FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md:60` — "abstention between 20% and
#: 35% of premium". Addendum D:136 keeps it and binds it to the SUM of the states.
BAND_LOW, BAND_HIGH = 0.20, 0.35

#: "v7 is the corpus of record" — sessions rolled at any other vintage are a
#: different definition and are listed in the artifact rather than pooled in.
CORPUS_OF_RECORD = 7

#: The sessions that had been examined when the band was registered, and are
#: therefore EXCLUDED from the confirmatory set. Addendum C:60 calls the remainder
#: "the ~258 sessions not yet examined" against a then-planned 260-session corpus —
#: i.e. exactly these two. The corpus of record landed at 253 sessions, so the
#: confirmatory set is 251.
EXAMINED_SESSIONS = {
    "2025-07-28": (
        "the audit session the direction rule was chosen after — ADDENDUM_C.md:49 "
        "('This rule was chosen after examining 2025-07-28. That session is "
        "therefore in-sample and cannot confirm it'), ADDENDUM_D.md:140"
    ),
    "2026-07-23": (
        "the validation day the classification rule was set from — ADDENDUM_A.md:10 "
        "(11,152,080 prints characterized), ADDENDUM_B.md:4"
    ),
}

#: `FLOW_BASELINE_PREREG_2026-07-26.md:36` — the registered primary window ends here.
#: Sessions after it are still corpus-of-record and are NOT dropped (that would be a
#: filter nobody registered); they are flagged in the artifact so the reader knows.
PREREG_WINDOW_END = "2026-07-25"

#: Addendum D:66 measured this on the one in-sample session. Reported as context
#: only: D:139-141 explicitly declares no sub-bands and forbids manufacturing
#: predictions out of data already seen.
IN_SAMPLE_REFERENCE = {
    "date": "2025-07-28",
    "abstention_of_premium": 0.2468,
    "note": "in-sample reference only — no sub-bands are declared for the states",
}

_BUCKET_SQL = """
    SELECT SUM(premium_usd),
           SUM(CASE WHEN side = 'buy'  THEN premium_usd ELSE 0 END),
           SUM(CASE WHEN side = 'sell' THEN premium_usd ELSE 0 END),
           SUM(u_premium_clipped),
           SUM(u_premium)
    FROM flow_rollup
    WHERE date = ?
    GROUP BY underlying, right, dte_bucket, mny_bucket
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Read-only. The corpus is 16 GB of measured record and this tool never writes."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def confirmatory_sessions(conn: sqlite3.Connection) -> dict:
    """Split the corpus's sessions into the confirmatory set and what was held out."""
    rows = conn.execute(
        "SELECT date, schema_version FROM corpus_sessions ORDER BY date"
    ).fetchall()
    taken, examined, off_vintage = [], [], []
    for date, version in rows:
        if version != CORPUS_OF_RECORD:
            off_vintage.append({"date": date, "schema_version": version})
        elif date in EXAMINED_SESSIONS:
            examined.append(date)
        else:
            taken.append(date)
    return {
        "sessions": taken,
        "sessions_in_corpus": len(rows),
        "excluded_examined": examined,
        "excluded_off_vintage": off_vintage,
        "after_prereg_window": [d for d in taken if d > PREREG_WINDOW_END],
    }


def session_reads(conn: sqlite3.Connection, date: str) -> list[tuple[DirectionRead, float]]:
    """One (read, premium) pair per bucket, by the registered rule."""
    return [
        (direction_read(buy - sell, clipped, raw), premium)
        for premium, buy, sell, clipped, raw in conn.execute(_BUCKET_SQL, (date,))
    ]


def session_stats(date: str, reads: list[tuple[DirectionRead, float]]) -> dict:
    """Premium-weighted abstention for one session, split by abstain state."""
    share = abstention_share(reads)
    by_state = abstention_by_state(reads)
    premium = sum(p for _, p in reads)
    return {
        "date": date,
        "abstention": share,
        "by_state": by_state,
        # Addendum D:135-141 binds the band to the SUM of the states, so the two
        # numbers must reconcile; carried per session so drift cannot hide.
        "state_split_gap": sum(by_state.values()) - share,
        "premium_usd": premium,
        "buckets": len(reads),
        "in_band": BAND_LOW <= share <= BAND_HIGH,
    }


def _distribution(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    out = {"n": len(ordered), "min": ordered[0], "max": ordered[-1]}
    if len(ordered) >= 2:
        p25, p50, p75 = quantiles(ordered, n=4, method="inclusive")
        out |= {"p25": p25, "median": p50, "p75": p75}
    return out


def _pooled(sessions: list[dict]) -> dict:
    """Premium-weighted across sessions — the weighting the band was registered on."""
    premium = sum(s["premium_usd"] for s in sessions)
    if premium <= 0:
        return {"abstention": 0.0, "by_state": {}, "premium_usd": premium, "buckets": 0}
    share = sum(s["abstention"] * s["premium_usd"] for s in sessions) / premium
    states = {st for s in sessions for st in s["by_state"]}
    by_state = {
        st: sum(s["by_state"].get(st, 0.0) * s["premium_usd"] for s in sessions) / premium
        for st in sorted(states)
    }
    return {
        "abstention": share,
        "by_state": by_state,
        "premium_usd": premium,
        "buckets": sum(s["buckets"] for s in sessions),
        "in_band": BAND_LOW <= share <= BAND_HIGH,
    }


def run(db_path: Path) -> dict:
    """Measure every confirmatory session and assemble the artifact."""
    with connect(db_path) as conn:
        split = confirmatory_sessions(conn)
        sessions = [
            session_stats(date, session_reads(conn, date)) for date in split["sessions"]
        ]

    shares = [s["abstention"] for s in sessions]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "db": str(db_path),
        "schema_version": CORPUS_OF_RECORD,
        "band": {
            "low": BAND_LOW,
            "high": BAND_HIGH,
            "weighting": "premium-weighted share of bucket premium",
            "binding_on": "the sum of the three abstain states",
            "bucket_grain": "date, underlying, right, dte_bucket, mny_bucket",
            "population": "corpus-of-record sessions minus the examined sessions",
            "registered_in": [
                "References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_C.md:60-62",
                "References/FLOW_BASELINE_PREREG_2026-07-26_ADDENDUM_D.md:135-141",
            ],
        },
        "excluded_examined_sessions": {
            date: EXAMINED_SESSIONS[date] for date in split["excluded_examined"]
        },
        "examined_sessions_not_found_in_corpus": sorted(
            set(EXAMINED_SESSIONS) - set(split["excluded_examined"])
        ),
        "excluded_off_vintage_sessions": split["excluded_off_vintage"],
        "sessions_in_corpus": split["sessions_in_corpus"],
        "sessions_measured": len(sessions),
        "sessions_after_prereg_window": split["after_prereg_window"],
        "multileg_caveat": (
            "All-prints population, as the band was registered. Per ADDENDUM_D.md:76-113 "
            "this reading is NOT free of the multi-leg midpoint-sign contamination; a "
            "per-arm reading is a separate measurement under the Addendum F gate."
        ),
        "in_sample_reference": IN_SAMPLE_REFERENCE,
        "pooled": _pooled(sessions),
        "per_session_distribution": _distribution(shares),
        "sessions_outside_band": {
            "count": sum(1 for s in sessions if not s["in_band"]),
            "below": [s["date"] for s in sessions if s["abstention"] < BAND_LOW],
            "above": [s["date"] for s in sessions if s["abstention"] > BAND_HIGH],
        },
        "state_split_max_gap": max((abs(s["state_split_gap"]) for s in sessions), default=0.0),
        "sessions": sessions,
    }


def render(result: dict) -> str:
    """The plain-text report. Every number here is also in the JSON artifact."""
    pooled, dist = result["pooled"], result["per_session_distribution"]
    outside = result["sessions_outside_band"]
    lines = [
        "=== confirmatory abstention run — registered band "
        f"{BAND_LOW:.0%}-{BAND_HIGH:.0%} of premium ===",
        f"corpus            {result['db']}  (schema_version {result['schema_version']})",
        f"sessions          {result['sessions_measured']} measured of "
        f"{result['sessions_in_corpus']} in corpus",
        "excluded          " + ", ".join(
            f"{d} (examined)" for d in result["excluded_examined_sessions"]
        ),
    ]
    if result["examined_sessions_not_found_in_corpus"]:
        lines.append(
            "⚠ examined session absent from corpus: "
            + ", ".join(result["examined_sessions_not_found_in_corpus"])
        )
    if result["excluded_off_vintage_sessions"]:
        lines.append(
            f"⚠ off-vintage sessions excluded: {len(result['excluded_off_vintage_sessions'])}"
        )
    if result["sessions_after_prereg_window"]:
        lines.append(
            f"note              {len(result['sessions_after_prereg_window'])} measured session(s) "
            f"fall after the prereg window end {PREREG_WINDOW_END}; kept, not filtered"
        )

    verdict = "INSIDE the band" if pooled["in_band"] else "OUTSIDE the band — a FINDING"
    lines += [
        "",
        "--- pooled (premium-weighted, the registered weighting) ---",
        f"  abstention      {pooled['abstention']:.2%}   {verdict}",
        f"  premium         ${pooled['premium_usd'] / 1e9:,.1f}B over {pooled['buckets']:,} buckets",
        "  by state:",
    ]
    lines += [
        f"    {state:<20} {share:.2%}" for state, share in pooled["by_state"].items()
    ]
    lines += [
        f"    {'sum of states':<20} {sum(pooled['by_state'].values()):.2%} "
        f"(band is binding on this sum)",
        "",
        "--- per-session abstention ---",
        "  " + "".join(f"{k:>10}" for k in ("min", "p25", "median", "p75", "max")),
        "  " + "".join(
            f"{dist.get(k, float('nan')):>9.2%} " for k in ("min", "p25", "median", "p75", "max")
        ),
        "",
        f"--- sessions outside the band: {outside['count']} of "
        f"{result['sessions_measured']} ---",
    ]
    below = set(outside["below"])
    for s in result["sessions"]:
        if s["in_band"]:
            continue
        where = "below" if s["date"] in below else "above"
        top = max(s["by_state"].items(), key=lambda kv: kv[1], default=("-", 0.0))
        lines.append(
            f"  {s['date']}  {s['abstention']:7.2%}  {where:<5}  "
            f"largest state: {top[0]} {top[1]:.2%}"
        )
    if not outside["count"]:
        lines.append("  (none)")

    ref = result["in_sample_reference"]
    lines += [
        "",
        f"in-sample reference {ref['date']} {ref['abstention_of_premium']:.2%} — {ref['note']}",
        f"state split reconciles to within {result['state_split_max_gap']:.2e} per session",
        result["multileg_caveat"],
        "",
        "The band is registered. A result outside it is reported, never absorbed by "
        "adjusting the rule (ADDENDUM_C.md:61-62).",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--out", type=Path, required=True, help="JSON artifact path")
    args = ap.parse_args()

    result = run(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(render(result))
    print(f"\nartifact: {args.out}")


if __name__ == "__main__":
    main()
