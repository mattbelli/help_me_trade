"""SEC DERA Financial Statement Data Sets — the SEGMENT-level numbers.

`companyfacts` (see `research.edgar_bulk`) strips every XBRL dimension, so the
corpus knows Amazon's total revenue and cannot know AWS's. The SEC's Division of
Economic and Risk Analysis republishes the same filings as quarterly flat files
that KEEP the dimensions, and that is the only free source for
"AWS revenue $128.72B, AWS operating income $45.61B, North America $426.31B".

⚠️ This is a BACKFILL source, not a live one. `2026q2.zip` 404s while Q2
earnings are already public — DERA publishes roughly a quarter in arrears. Fresh
segment numbers come from the 8-K exhibit (`signals.earnings_exhibit`), as prose;
this is what makes them screenable.

Bonus: `sub.txt` carries each filer's SIC, which is how the ~6,500 tickers added
by the bulk ticker sync get a sector without 20k submissions requests.
"""

from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import time
import zipfile
from datetime import date
from pathlib import Path

from research.edgar_bulk import (
    DEFAULT_EDGAR_DIR,
    DEFAULT_FACTS_DB_PATH,
    DEFAULT_FUNDAMENTALS_DB_PATH,
    DEFAULT_USER_AGENT,
    accn_to_int,
    concept_interner,
    ensure_bulk_schema,
)

logger = logging.getLogger(__name__)

DERA_ZIP_URL = (
    "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
)
# 2009q1 exists but is 13 KB — the XBRL mandate had barely started. Kept in the
# range anyway so "everything available" needs no special case.
FIRST_QUARTER = (2009, 1)

_BATCH_ROWS = 200_000

# Dimension strings repeat relentlessly ("BusinessSegments=AmazonWebServicesSegment;
# ConsolidationItems=OperatingSegments" once per company per period per concept),
# so they are interned exactly like concept tags.
_CREATE_SEGMENTS = """
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE
)
"""

# One row per filing. `sic` is the reason this table exists beyond joins.
_CREATE_FILINGS = """
CREATE TABLE IF NOT EXISTS filings (
    accn INTEGER NOT NULL PRIMARY KEY,
    cik INTEGER NOT NULL,
    name TEXT,
    sic INTEGER,
    form TEXT,
    period TEXT,
    filed TEXT,
    fye TEXT
)
"""
_CREATE_FILINGS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_filings_cik ON filings (cik)
"""

# `qtrs` is DERA's duration in quarters and it is a gift: 0 = instant (balance
# sheet), 1 = one quarter, 2 = half, 3 = nine months, 4 = a year. The corpus has
# to infer this from period length; here the filer states it.
#
# `coreg` (co-registrant) is in the key even though a 1.2M-row scan found zero
# collisions without it: it is populated on 2.6% of rows and carries real meaning
# (a subsidiary guarantor filing alongside the parent), so keying without it
# would silently drop those rows the day one does collide.
_CREATE_SEGMENT_FACTS = """
CREATE TABLE IF NOT EXISTS segment_facts (
    cik INTEGER NOT NULL,
    concept_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    qtrs INTEGER NOT NULL,
    segment_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    coreg TEXT NOT NULL DEFAULT '',
    accn INTEGER NOT NULL,
    filed TEXT NOT NULL,
    form TEXT,
    val REAL NOT NULL,
    PRIMARY KEY (cik, concept_id, period_end, qtrs, segment_id, unit, coreg, accn)
) WITHOUT ROWID
"""

_INSERT_SEGMENT_FACT = """
INSERT OR REPLACE INTO segment_facts
    (cik, concept_id, period_end, qtrs, segment_id, unit, coreg, accn, filed, form, val)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def ensure_dera_schema(conn: sqlite3.Connection) -> None:
    ensure_bulk_schema(conn)
    conn.execute(_CREATE_SEGMENTS)
    conn.execute(_CREATE_FILINGS)
    conn.execute(_CREATE_FILINGS_INDEX)
    conn.execute(_CREATE_SEGMENT_FACTS)
    conn.commit()


def quarters_through(today: date) -> list[str]:
    """Every quarter label from 2009q1 to the one before `today`'s quarter.

    The current quarter is excluded because DERA publishes in arrears — asking
    for it returns a 404 page, not data.
    """
    out: list[str] = []
    year, quarter = FIRST_QUARTER
    last_year, last_quarter = today.year, (today.month - 1) // 3 + 1
    while (year, quarter) < (last_year, last_quarter):
        out.append(f"{year}q{quarter}")
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return out


def _taxonomy_of(version: str) -> str:
    """`us-gaap/2025` -> `us-gaap`; a bare accession means a company extension tag.

    Custom tags are company-specific by definition and every read is CIK-scoped,
    so collapsing them all to `custom` cannot confuse one filer's numbers with
    another's.
    """
    return version.split("/", 1)[0] if "/" in version else "custom"


def _ddate_to_iso(ddate: str) -> str | None:
    """`20251231` -> `2025-12-31`.

    DERA writes bare YYYYMMDD everywhere; the corpus stores ISO. Normalising on
    the way in is not cosmetic — an `as_of='2026-03-01'` filter compared against
    `'20260206'` sorts wrong, so mixed formats would silently corrupt every
    point-in-time read that spans both tables.
    """
    ddate = (ddate or "").strip()
    if len(ddate) != 8 or not ddate.isdigit():
        return None
    return f"{ddate[:4]}-{ddate[4:6]}-{ddate[6:]}"


def ingest_quarter(
    zip_path: Path,
    *,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Load one DERA quarter's segmented rows + filing index. Idempotent."""
    started = time.monotonic()
    owned = conn is None
    if owned:
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-262144")
        ensure_dera_schema(conn)

    concept_id, flush_concepts = concept_interner(conn)
    segment_ids = {
        row[1]: row[0] for row in conn.execute("SELECT id, label FROM segments")
    }
    new_segments: list[tuple] = []

    def segment_id(label: str) -> int:
        sid = segment_ids.get(label)
        if sid is None:
            sid = len(segment_ids) + 1
            segment_ids[label] = sid
            new_segments.append((sid, label))
        return sid

    filings = 0
    written = 0
    skipped = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            # sub.txt first: it maps accession -> cik, which num.txt does not carry.
            cik_of: dict[str, int] = {}
            sub_rows: list[tuple] = []
            with archive.open("sub.txt") as handle:
                text = io.TextIOWrapper(handle, "utf8", errors="replace")
                header = text.readline().rstrip("\n").split("\t")
                idx = {name: i for i, name in enumerate(header)}
                for line in text:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) < len(header):
                        continue
                    adsh = parts[idx["adsh"]]
                    accn = accn_to_int(adsh)
                    if accn is None:
                        continue
                    try:
                        cik = int(parts[idx["cik"]])
                    except (ValueError, KeyError):
                        continue
                    cik_of[adsh] = cik
                    sic_raw = parts[idx["sic"]].strip()
                    sub_rows.append(
                        (
                            accn,
                            cik,
                            parts[idx["name"]],
                            int(sic_raw) if sic_raw.isdigit() else None,
                            parts[idx["form"]],
                            _ddate_to_iso(parts[idx["period"]]),
                            _ddate_to_iso(parts[idx["filed"]]),
                            parts[idx["fye"]],
                        )
                    )
            conn.executemany(
                "INSERT OR REPLACE INTO filings "
                "(accn, cik, name, sic, form, period, filed, fye) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                sub_rows,
            )
            filings = len(sub_rows)
            filed_of = {r[0]: r[6] for r in sub_rows}
            form_of = {r[0]: r[4] for r in sub_rows}

            batch: list[tuple] = []
            with archive.open("num.txt") as handle:
                text = io.TextIOWrapper(handle, "utf8", errors="replace")
                header = text.readline().rstrip("\n").split("\t")
                idx = {name: i for i, name in enumerate(header)}
                # Manual split, not csv.DictReader: this file is 559 MB per
                # quarter and there are 69 of them.
                i_adsh, i_tag, i_ver = idx["adsh"], idx["tag"], idx["version"]
                i_dd, i_q, i_uom = idx["ddate"], idx["qtrs"], idx["uom"]
                i_seg, i_co, i_val = idx["segments"], idx["coreg"], idx["value"]
                for line in text:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) <= i_val:
                        continue
                    label = parts[i_seg].strip()
                    if not label:
                        continue  # undimensioned rows are already in the corpus
                    raw = parts[i_val].strip()
                    if not raw:
                        continue
                    try:
                        value = float(raw)
                    except ValueError:
                        continue
                    period_end = _ddate_to_iso(parts[i_dd])
                    if period_end is None:
                        skipped += 1
                        continue
                    adsh = parts[i_adsh]
                    cik = cik_of.get(adsh)
                    accn = accn_to_int(adsh)
                    if cik is None or accn is None:
                        skipped += 1
                        continue
                    try:
                        qtrs = int(parts[i_q])
                    except ValueError:
                        qtrs = -1
                    batch.append(
                        (
                            cik,
                            concept_id(_taxonomy_of(parts[i_ver]), parts[i_tag]),
                            period_end,
                            qtrs,
                            segment_id(label),
                            parts[i_uom],
                            parts[i_co].strip(),
                            accn,
                            filed_of.get(accn, ""),
                            form_of.get(accn),
                            value,
                        )
                    )
                    written += 1
                    if len(batch) >= _BATCH_ROWS:
                        _flush(conn, new_segments, batch, flush_concepts)
            _flush(conn, new_segments, batch, flush_concepts)
    finally:
        if owned:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
            conn.close()

    return {
        "filings": filings,
        "segment_facts": written,
        "skipped": skipped,
        "seconds": round(time.monotonic() - started, 1),
    }


def _flush(conn, new_segments: list[tuple], batch: list[tuple], flush_concepts) -> None:
    flush_concepts()
    if new_segments:
        conn.executemany(
            "INSERT OR IGNORE INTO segments (id, label) VALUES (?, ?)", new_segments
        )
        new_segments.clear()
    if batch:
        conn.executemany(_INSERT_SEGMENT_FACT, batch)
        batch.clear()
    conn.commit()


def download_quarter(
    quarter: str,
    *,
    dest_dir: Path = DEFAULT_EDGAR_DIR,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Path | None:
    """Fetch one quarter's zip. Returns None when SEC has not published it yet."""
    import httpx

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{quarter}.zip"
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    response = httpx.get(
        DERA_ZIP_URL.format(quarter=quarter),
        headers={"User-Agent": user_agent},
        timeout=180.0,
        follow_redirects=True,
    )
    if response.status_code == 404:
        logger.warning("DERA %s not published yet", quarter)
        return None
    response.raise_for_status()
    tmp = dest.with_suffix(".zip.part")
    tmp.write_bytes(response.content)
    if not zipfile.is_zipfile(tmp):
        # A 200 that is really the "not found" HTML page.
        tmp.unlink(missing_ok=True)
        logger.warning("DERA %s served a non-zip payload", quarter)
        return None
    tmp.replace(dest)
    return dest


def backfill(
    quarters: list[str] | None = None,
    *,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    dest_dir: Path = DEFAULT_EDGAR_DIR,
    keep_zips: bool = True,
    today: date | None = None,
) -> dict:
    """Download + ingest every DERA quarter, oldest first."""
    quarters = quarters or quarters_through(today or date.today())
    started = time.monotonic()
    conn = sqlite3.connect(db_path, timeout=60.0)
    # WAL, deliberately — NOT the journal_mode=MEMORY the one-shot corpus build
    # uses. Segment facts share a file with the 124M-fact corpus that the live
    # backend reads, and MEMORY journalling locks every reader out for the whole
    # run ("database is locked"). This is a recurring quarterly job, so it must
    # stay concurrent; the cost is hidden anyway because the run is
    # download-bound (~38s/quarter fetching vs ~10s ingesting).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-262144")
    ensure_dera_schema(conn)

    totals = {"quarters": 0, "filings": 0, "segment_facts": 0, "missing": 0}
    try:
        for quarter in quarters:
            path = download_quarter(quarter, dest_dir=dest_dir)
            if path is None:
                totals["missing"] += 1
                continue
            summary = ingest_quarter(path, conn=conn)
            totals["quarters"] += 1
            totals["filings"] += summary["filings"]
            totals["segment_facts"] += summary["segment_facts"]
            logger.warning(
                "  %s: %s segment facts, %s filings, %.0fs (running total %s)",
                quarter,
                f"{summary['segment_facts']:,}",
                f"{summary['filings']:,}",
                summary["seconds"],
                f"{totals['segment_facts']:,}",
            )
            # Fold the WAL back each quarter so it cannot grow to tens of GB
            # across a 70-quarter run.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if not keep_zips:
                path.unlink(missing_ok=True)
    finally:
        conn.commit()
        conn.close()

    totals["seconds"] = round(time.monotonic() - started, 1)
    totals["db_gb"] = round(db_path.stat().st_size / 1e9, 2)
    return totals


def sync_sic_from_filings(
    *,
    facts_db: Path = DEFAULT_FACTS_DB_PATH,
    companies_db: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
) -> int:
    """Backfill `companies.sic`/`sector` from DERA's filing index.

    The bulk ticker sync writes symbol->CIK only, leaving thousands of tickers
    with no sector. `sub.txt` carries the SIC on every filing, so the newest
    filing per CIK answers it for free — no 20k submissions requests.
    """
    from research.fundamentals import _ensure_schema, sic_to_sector

    conn = sqlite3.connect(facts_db, timeout=60.0)
    try:
        latest = {
            row[0]: row[1]
            for row in conn.execute(
                """
                SELECT cik, sic FROM filings f
                WHERE sic IS NOT NULL
                  AND filed = (SELECT MAX(filed) FROM filings x
                               WHERE x.cik = f.cik AND x.sic IS NOT NULL)
                """
            )
        }
    finally:
        conn.close()
    if not latest:
        return 0

    conn = sqlite3.connect(companies_db, timeout=60.0)
    try:
        _ensure_schema(conn)
        rows = [
            (sic, sic_to_sector(sic), str(cik))
            for cik, sic in latest.items()
        ]
        cur = conn.executemany(
            "UPDATE companies SET sic = ?, sector = ? WHERE cik = ? AND sic IS NULL",
            rows,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SEC DERA segment-level ingest")
    parser.add_argument("--backfill", action="store_true", help="Download + ingest every quarter")
    parser.add_argument("--quarter", action="append", help="Ingest specific quarter(s), e.g. 2026q1")
    parser.add_argument("--sync-sic", action="store_true", help="Backfill companies.sic from filings")
    parser.add_argument("--drop-zips", action="store_true", help="Delete each zip after ingest")
    parser.add_argument("--db", type=Path, default=DEFAULT_FACTS_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if not (args.backfill or args.quarter or args.sync_sic):
        _parse_args(["--help"])
        return

    if args.backfill or args.quarter:
        totals = backfill(
            args.quarter, db_path=args.db, keep_zips=not args.drop_zips
        )
        print(
            f"{totals['segment_facts']:,} segment facts from {totals['quarters']} quarters "
            f"({totals['filings']:,} filings, {totals['missing']} not published) "
            f"in {totals['seconds']}s — DB now {totals['db_gb']} GB"
        )

    if args.sync_sic:
        print(f"sector backfilled for {sync_sic_from_filings(facts_db=args.db):,} tickers")


if __name__ == "__main__":
    main()
