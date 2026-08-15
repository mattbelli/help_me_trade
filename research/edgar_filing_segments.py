"""Per-filing XBRL segment ingest — the FRESH lane into `segment_facts`.

`research.edgar_dera` fills segment-level numbers from SEC DERA's quarterly flat
files, which publish a quarter-plus in arrears (2026q2.zip still 404s in
mid-August while Alphabet's Q2 10-Q has been on EDGAR since 2026-07-23). The
dimensions it recovers are sitting in every filing's own XBRL the day it lands,
so this module reads them straight from the source: for each new 10-Q/10-K by a
listed filer, fetch the SEC-extracted instance (`*_htm.xml`), keep every
numeric fact whose context carries an explicit dimension, and write it into the
SAME `segment_facts` table.

Two lanes, one table, dedupe by construction: rows are primary-keyed through
the accession number, and labels/units/taxonomies are serialized to DERA's own
conventions (`BusinessSegments=GoogleCloud;ConsolidationItems=OperatingSegments;`
— pairs sorted, trailing `;`; `Statement`/`Axis`/`Member` affixes stripped), so
when DERA finally republishes the same filing its rows land on ours as
no-op replaces. DERA stays the deep-history authority and repairs anything this
lane skips (typed dimensions, co-registrant facts, unlisted filers).

`--verify-quarter` re-parses filings DERA already covers and diffs the rows —
the parser is measured against ground truth, never assumed.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from research.edgar_bulk import (
    DEFAULT_FACTS_DB_PATH,
    DEFAULT_FUNDAMENTALS_DB_PATH,
    DEFAULT_USER_AGENT,
    accn_to_int,
    accn_to_str,
    concept_interner,
)
from research.edgar_dera import ensure_dera_schema

logger = logging.getLogger(__name__)

FORM_IDX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx"
FILING_DIR_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A"})

_XBRLI = "{http://www.xbrl.org/2003/instance}"
_XBRLDI = "{http://xbrl.org/2006/xbrldi}"

# ~8 req/s against SEC's stated 10/s ceiling; the whole nightly delta is a few
# hundred filings so politeness costs a minute, not an hour.
_SLEEP_S = 0.12
_MAX_ATTEMPTS = 3
_CHECKPOINT_EVERY = 200

# One row per filing this lane has already looked at, so the nightly sync only
# fetches the delta. `no_instance` is terminal (pre-inline-XBRL paper filings —
# DERA covers them later); `error` retries up to _MAX_ATTEMPTS.
_CREATE_INGEST = """
CREATE TABLE IF NOT EXISTS filing_segment_ingest (
    accn INTEGER NOT NULL PRIMARY KEY,
    cik INTEGER NOT NULL,
    form TEXT NOT NULL,
    filed TEXT NOT NULL,
    status TEXT NOT NULL,
    facts INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 1,
    checked_at TEXT NOT NULL
)
"""

_INSERT_FACT = """
INSERT OR REPLACE INTO segment_facts
    (cik, concept_id, period_end, qtrs, segment_id, unit, coreg, accn, filed, form, val)
VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?)
"""

# Hosts whose namespaces are shared taxonomies; anything else is a company
# extension and collapses to `custom`, matching research.edgar_dera._taxonomy_of.
_TAXONOMY_HOSTS = ("fasb.org", "xbrl.sec.gov", "xbrl.ifrs.org", "xbrl.org")


def _taxonomy_for_uri(uri: str) -> str:
    host = re.sub(r"^https?://(www\.)?", "", uri).split("/", 1)[0]
    if host not in _TAXONOMY_HOSTS:
        return "custom"
    segments = [s for s in uri.split("/")[3:] if s]
    named = [s for s in segments if not re.fullmatch(r"[\d-]+", s)]
    return named[-1] if named else "custom"


def _dera_axis(qname: str) -> str:
    name = qname.split(":", 1)[-1]
    name = re.sub(r"^Statement", "", name)
    return re.sub(r"Axis$", "", name)


def _dera_member(qname: str) -> str:
    return re.sub(r"Member$", "", qname.split(":", 1)[-1])


def _qtrs(start: str | None, end: str) -> int:
    """DERA's duration-in-quarters: 0 = instant, 1 = quarter, 4 = year.

    No floor on short durations: DERA itself rounds a days-long period (a SPAC's
    two-week share issuance) to 0 — verified against the 2026q1 sweep, where a
    max(1, ...) clamp cost every such row.
    """
    if not start:
        return 0
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return round(days / 91.3125)


def _month_end(iso: str) -> str:
    """Round to the nearest month end, as DERA's `ddate` does.

    52/53-week filers end periods mid-week (AMD's FY2025 ends 2025-12-27, which
    DERA records as 2025-12-31). Storing the literal filing date would file the
    same fact under two period_ends the day DERA republishes it — measured as
    three ~5%-match filings in the first 25-filing verify sweep, all of them
    fiscal-week filers.
    """
    day = date.fromisoformat(iso)
    first = day.replace(day=1)
    prev_end = first - timedelta(days=1)
    next_end = (first + timedelta(days=45)).replace(day=1) - timedelta(days=1)
    return (
        prev_end if (day - prev_end).days < (next_end - day).days else next_end
    ).isoformat()


def parse_instance(xml_bytes: bytes, *, filer_cik: int) -> tuple[list[tuple], dict]:
    """Extract dimensioned numeric facts from an extracted-XBRL instance.

    Returns (rows, skips) where each row is
    (taxonomy, tag, period_end, qtrs, segment_label, unit, val) and skips counts
    what was deliberately left for the DERA lane (typed dimensions, contexts
    whose entity is a co-registrant rather than the filer).
    """
    root = ET.fromstring(xml_bytes)
    skips = {"typed": 0, "coreg": 0}

    units: dict[str, str] = {}
    for unit in root.iter(f"{_XBRLI}unit"):
        measures = [m.text.split(":", 1)[-1] for m in unit.iter(f"{_XBRLI}measure") if m.text]
        divide = unit.find(f"{_XBRLI}divide")
        if divide is not None and len(measures) >= 2:
            # DERA collapses currency-per-share to the bare currency (EPS rows
            # read `USD`); every other ratio keeps the slash (`USD/Unit`).
            units[unit.get("id")] = (
                measures[0] if measures[1] == "shares" else f"{measures[0]}/{measures[1]}"
            )
        elif measures:
            units[unit.get("id")] = measures[0]

    contexts: dict[str, tuple[str | None, str, str]] = {}
    for ctx in root.iter(f"{_XBRLI}context"):
        entity = ctx.find(f"{_XBRLI}entity")
        identifier = entity.findtext(f"{_XBRLI}identifier") if entity is not None else None
        try:
            entity_cik = int((identifier or "").strip())
        except ValueError:
            entity_cik = None
        pairs = []
        typed = False
        for member in ctx.iter(f"{_XBRLDI}explicitMember"):
            pairs.append(f"{_dera_axis(member.get('dimension'))}={_dera_member(member.text.strip())};")
        for _ in ctx.iter(f"{_XBRLDI}typedMember"):
            typed = True
        if typed:
            skips["typed"] += 1
            continue
        if not pairs:
            continue
        if entity_cik != filer_cik:
            skips["coreg"] += 1
            continue
        period = ctx.find(f"{_XBRLI}period")
        end = period.findtext(f"{_XBRLI}endDate") or period.findtext(f"{_XBRLI}instant")
        if not end:
            continue
        start = period.findtext(f"{_XBRLI}startDate")
        contexts[ctx.get("id")] = (start, end, "".join(sorted(pairs)))

    rows: list[tuple] = []
    for el in root.iter():
        ctx = contexts.get(el.get("contextRef") or "")
        if ctx is None:
            continue
        unit = units.get(el.get("unitRef") or "")
        if unit is None:
            continue  # text blocks and non-numeric facts carry no unit
        text = (el.text or "").strip()
        if not text:
            continue
        try:
            val = float(text)
        except ValueError:
            continue
        start, end, label = ctx
        _, _, tag = el.tag.rpartition("}")
        taxonomy = _taxonomy_for_uri(el.tag[1:].rpartition("}")[0])
        rows.append(
            (taxonomy, tag, _month_end(end), _qtrs(start, end), label, unit, val)
        )
    return rows, skips


def _pick_instance(names: list[str]) -> str | None:
    """The SEC-extracted instance for inline filings, the native one for old."""
    for name in names:
        if name.endswith("_htm.xml"):
            return name
    for name in names:
        if re.search(r"-\d{8}\.xml$", name):
            return name
    return None


def _parse_form_idx(text: str) -> list[tuple[str, int, str, str]]:
    """form.idx lines -> (form, cik, filed, accession). Header/rule lines drop out."""
    out = []
    for line in text.splitlines():
        parts = line.rsplit(None, 3)
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        form = parts[0].split(None, 1)[0]
        if form not in FORMS or not parts[3].endswith(".txt"):
            continue
        out.append((form, int(parts[1]), parts[2], Path(parts[3]).stem))
    return out


def _listed_ciks(companies_db: Path) -> set[int]:
    conn = sqlite3.connect(f"file:{companies_db}?mode=ro", uri=True)
    try:
        return {
            int(row[0])
            for row in conn.execute("SELECT DISTINCT CAST(cik AS INTEGER) FROM companies")
        }
    finally:
        conn.close()


def _quarter_labels(db_conn: sqlite3.Connection, today: date) -> list[str]:
    """Quarters after DERA's coverage, through today's — the gap this lane owns."""
    row = db_conn.execute("SELECT MAX(quarter) FROM dera_quarters").fetchone()
    if row and row[0]:
        year, q = int(row[0][:4]), int(row[0][-1])
    else:  # no DERA at all: cover this quarter and the previous one
        year, q = today.year, (today.month - 1) // 3 + 1
        year, q = (year - 1, 4) if q == 1 else (year, q - 1)
    out = []
    last = (today.year, (today.month - 1) // 3 + 1)
    while (year, q) < last:
        q += 1
        if q > 4:
            year, q = year + 1, 1
        out.append(f"{year}q{q}")
    return out


def _fetch(client, url: str):
    for attempt in (1, 2):
        response = client.get(url)
        if response.status_code in (429, 503) and attempt == 1:
            time.sleep(2.0)
            continue
        return response
    return response


def sync(
    *,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    companies_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
    limit: int | None = None,
    today: date | None = None,
) -> dict:
    """Parse every listed 10-Q/10-K in the post-DERA window not yet ingested."""
    import httpx

    started = time.monotonic()
    conn = sqlite3.connect(db_path, timeout=60.0)
    # WAL, same reasoning as edgar_dera.backfill: this file is shared with the
    # live backend, so the ingest must never lock readers out.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    ensure_dera_schema(conn)
    conn.execute(_CREATE_INGEST)
    conn.commit()

    concept_id, flush_concepts = concept_interner(conn)
    listed = _listed_ciks(companies_db)
    totals = {"filings": 0, "facts": 0, "no_instance": 0, "errors": 0, "skipped_done": 0}
    quarters = _quarter_labels(conn, today or date.today())

    seen = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT accn, status, attempts FROM filing_segment_ingest")
    }

    def segment_id(label: str) -> int:
        row = conn.execute("SELECT id FROM segments WHERE label = ?", (label,)).fetchone()
        if row:
            return row[0]
        return conn.execute("INSERT INTO segments (label) VALUES (?)", (label,)).lastrowid

    try:
        with httpx.Client(
            timeout=60.0, headers={"User-Agent": user_agent}, follow_redirects=True
        ) as client:
            todo: list[tuple[str, int, str, str]] = []
            for quarter in quarters:
                year, q = int(quarter[:4]), int(quarter[-1])
                response = _fetch(client, FORM_IDX_URL.format(year=year, q=q))
                if response.status_code == 404:
                    continue  # the quarter's index appears with its first filing
                response.raise_for_status()
                for form, cik, filed, accession in _parse_form_idx(response.text):
                    accn = accn_to_int(accession)
                    if cik not in listed or accn is None:
                        continue
                    status, attempts = seen.get(accn, (None, 0))
                    if status == "ok" or status == "no_instance" or (
                        status == "error" and attempts >= _MAX_ATTEMPTS
                    ):
                        totals["skipped_done"] += 1
                        continue
                    todo.append((form, cik, filed, accession))
                time.sleep(_SLEEP_S)

            if limit:
                todo = todo[:limit]
            logger.info("filing-segment sync: %d filings to parse", len(todo))

            for i, (form, cik, filed, accession) in enumerate(todo):
                accn = accn_to_int(accession)
                folder = accession.replace("-", "")
                status, facts = "error", 0
                try:
                    time.sleep(_SLEEP_S)
                    listing = _fetch(
                        client,
                        FILING_DIR_URL.format(cik=cik, folder=folder, name="index.json"),
                    )
                    listing.raise_for_status()
                    names = [item["name"] for item in listing.json()["directory"]["item"]]
                    instance = _pick_instance(names)
                    if instance is None:
                        status = "no_instance"
                    else:
                        time.sleep(_SLEEP_S)
                        doc = _fetch(
                            client,
                            FILING_DIR_URL.format(cik=cik, folder=folder, name=instance),
                        )
                        doc.raise_for_status()
                        rows, _ = parse_instance(doc.content, filer_cik=cik)
                        for taxonomy, tag, end, qtrs, label, unit, val in rows:
                            conn.execute(
                                _INSERT_FACT,
                                (
                                    cik,
                                    concept_id(taxonomy, tag),
                                    end,
                                    qtrs,
                                    segment_id(label),
                                    unit,
                                    accn,
                                    filed,
                                    form,
                                    val,
                                ),
                            )
                        status, facts = "ok", len(rows)
                except ET.ParseError:
                    status = "no_instance"  # a served error page, not XBRL
                except Exception as exc:  # noqa: BLE001 - one filing must not stop the sweep
                    logger.warning("filing segments %s failed (%s)", accession, str(exc)[:80])

                flush_concepts()
                prior_attempts = seen.get(accn, (None, 0))[1]
                conn.execute(
                    "INSERT OR REPLACE INTO filing_segment_ingest "
                    "(accn, cik, form, filed, status, facts, attempts, checked_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        accn, cik, form, filed, status, facts,
                        prior_attempts + 1,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
                conn.commit()
                totals["filings"] += 1
                totals["facts"] += facts
                if status == "no_instance":
                    totals["no_instance"] += 1
                elif status == "error":
                    totals["errors"] += 1
                if (i + 1) % _CHECKPOINT_EVERY == 0:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    logger.info(
                        "  %d/%d filings, %d facts", i + 1, len(todo), totals["facts"]
                    )
    finally:
        conn.commit()
        conn.close()

    totals["seconds"] = round(time.monotonic() - started, 1)
    return totals


def verify_quarter(
    quarter: str,
    *,
    sample: int = 25,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    companies_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict:
    """Measure the parser against DERA ground truth. Writes nothing.

    Re-parses `sample` filings that DERA already ingested and diffs the rows on
    the full natural key. `matched` counts DERA rows this parser reproduced;
    `missing` is what it failed to recover (the number that matters);
    `extra` is what it found that DERA dropped (harmless, but reported).
    """
    import httpx

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    listed = _listed_ciks(companies_db)
    year, q = int(quarter[:4]), int(quarter[-1])
    span = (f"{year}-{3 * q - 2:02d}-01", f"{year}-{3 * q:02d}-31")
    candidates = [
        (row[0], row[1])
        for row in conn.execute(
            """
            SELECT DISTINCT f.accn, f.cik FROM filings f
            WHERE f.form IN ('10-Q', '10-K') AND f.filed BETWEEN ? AND ?
              AND EXISTS (SELECT 1 FROM segment_facts s WHERE s.accn = f.accn)
            ORDER BY f.accn
            """,
            span,
        )
        if row[1] in listed
    ]
    # Deterministic spread over the quarter rather than the first N filers.
    step = max(1, len(candidates) // sample)
    picked = candidates[::step][:sample]

    report = {"filings": 0, "dera_rows": 0, "matched": 0, "missing": 0, "extra": 0}
    worst: list[tuple[float, str]] = []
    with httpx.Client(
        timeout=60.0, headers={"User-Agent": user_agent}, follow_redirects=True
    ) as client:
        for accn, cik in picked:
            dera = {
                (row[0], row[1], row[2], row[3], row[4], row[5], round(row[6], 2))
                for row in conn.execute(
                    """
                    SELECT k.taxonomy, k.tag, s.period_end, s.qtrs, g.label, s.unit, s.val
                    FROM segment_facts s
                    JOIN concepts k ON k.id = s.concept_id
                    JOIN segments g ON g.id = s.segment_id
                    WHERE s.accn = ? AND s.coreg = ''
                    """,
                    (accn,),
                )
            }
            accession = accn_to_str(accn)
            folder = accession.replace("-", "")
            time.sleep(_SLEEP_S)
            listing = _fetch(
                client, FILING_DIR_URL.format(cik=cik, folder=folder, name="index.json")
            )
            if listing.status_code != 200:
                continue
            instance = _pick_instance(
                [item["name"] for item in listing.json()["directory"]["item"]]
            )
            if instance is None:
                continue
            time.sleep(_SLEEP_S)
            doc = _fetch(
                client, FILING_DIR_URL.format(cik=cik, folder=folder, name=instance)
            )
            rows, _ = parse_instance(doc.content, filer_cik=cik)
            mine = {(t, g, e, qt, la, u, round(v, 2)) for t, g, e, qt, la, u, v in rows}
            matched = len(dera & mine)
            report["filings"] += 1
            report["dera_rows"] += len(dera)
            report["matched"] += matched
            report["missing"] += len(dera - mine)
            report["extra"] += len(mine - dera)
            if dera:
                worst.append((matched / len(dera), accession))
    conn.close()
    report["match_rate"] = (
        round(report["matched"] / report["dera_rows"], 4) if report["dera_rows"] else None
    )
    report["worst"] = sorted(worst)[:5]
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Per-filing XBRL segment ingest")
    parser.add_argument("--sync", action="store_true", help="Ingest the post-DERA window")
    parser.add_argument("--limit", type=int, help="Cap filings this run (testing)")
    parser.add_argument("--verify-quarter", help="Diff parser vs DERA for e.g. 2026q1")
    parser.add_argument("--sample", type=int, default=25, help="Filings per verify run")
    parser.add_argument("--db", type=Path, default=DEFAULT_FACTS_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if args.verify_quarter:
        report = verify_quarter(args.verify_quarter, sample=args.sample, db_path=args.db)
        print(json.dumps(report, indent=2))
        return
    if not args.sync:
        _parse_args(["--help"])
        return
    totals = sync(db_path=args.db, limit=args.limit)
    print(
        f"{totals['facts']:,} segment facts from {totals['filings']:,} filings "
        f"({totals['no_instance']} without XBRL, {totals['errors']} errors, "
        f"{totals['skipped_done']:,} already done) in {totals['seconds']}s"
    )


if __name__ == "__main__":
    main()
