"""Bulk SEC EDGAR XBRL ingest — the whole corpus from one download.

The per-symbol `data.sec.gov/api/xbrl/companyfacts/CIK*.json` route in
`research.fundamentals` costs one HTTP request per company and only ever covers
symbols we thought to ask for. The same data ships as a single nightly zip
(`companyfacts.zip`, 1.4 GB, 20,122 filers, ~139M facts) — one download replaces
every per-symbol call and picks up filers we never listed.

We keep EVERY concept, not the twelve canonical metrics: an agent asked "what
were the lease obligations" cannot be answered from a curated dozen, and the
whole point of holding the corpus is that no question is out of reach.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
import zipfile
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR

logger = logging.getLogger(__name__)

# The corpus lives in its own file, not in fundamentals.db: it is an ~11 GB
# rebuildable artifact of a public download, and keeping it separate means a
# rebuild never risks the small curated tables (companies/shares) that the live
# backend reads, and never has to be backed up with them.
DEFAULT_FACTS_DB_PATH = RESEARCH_DATA_DIR / "edgar_facts.db"
DEFAULT_FUNDAMENTALS_DB_PATH = RESEARCH_DATA_DIR / "fundamentals.db"
DEFAULT_EDGAR_DIR = RESEARCH_DATA_DIR / "edgar"
DEFAULT_USER_AGENT = "help_me_trade-research matt.belliveau@gmail.com"

COMPANYFACTS_ZIP_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
)
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Rows buffered before an executemany. 200k keeps peak RSS ~200 MB while still
# amortizing the statement overhead across the 139M-row load.
_BATCH_ROWS = 200_000

# A concept is (taxonomy, tag) — us-gaap:Revenues, dei:EntityCommonStockShares…
# Interned to an int because the tag string averages 30 bytes and would
# otherwise be stored 139M times (~4 GB of repeated text).
_CREATE_CONCEPTS = """
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY,
    taxonomy TEXT NOT NULL,
    tag TEXT NOT NULL,
    label TEXT,
    description TEXT,
    UNIQUE (taxonomy, tag)
)
"""

_CREATE_FILERS = """
CREATE TABLE IF NOT EXISTS filers (
    cik INTEGER PRIMARY KEY,
    entity_name TEXT
)
"""

# WITHOUT ROWID: the primary key IS the storage, so there is no second copy of
# a 139M-row index. Column order is the read order — every query is "one filer,
# these concepts, newest period first, as filed by date X".
#
# `accn` is in the key and that is load-bearing: 0.18% of facts share
# (cik, concept, unit, period, form, filed) with a DIFFERENT value — a 10-Q/A
# restating the same day it was filed, or an issuer filing several 424B2s.
# Keyed without the accession they silently overwrite each other.
_CREATE_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
    cik INTEGER NOT NULL,
    concept_id INTEGER NOT NULL,
    period_end TEXT NOT NULL,
    filed TEXT NOT NULL,
    unit TEXT NOT NULL,
    period_start TEXT NOT NULL DEFAULT '',
    accn INTEGER NOT NULL,
    fy INTEGER,
    fp TEXT,
    form TEXT,
    frame TEXT,
    val REAL NOT NULL,
    PRIMARY KEY (cik, concept_id, period_end, filed, unit, period_start, accn)
) WITHOUT ROWID
"""

_INSERT_FACT = """
INSERT OR REPLACE INTO facts
    (cik, concept_id, period_end, filed, unit, period_start, accn,
     fy, fp, form, frame, val)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def upsert_facts(conn, rows: list[tuple]) -> None:
    """Write fact rows. Shared with the per-symbol top-up path so both writers
    use identical key semantics — including `accn`, without which same-day
    amendments overwrite each other."""
    if rows:
        conn.executemany(_INSERT_FACT, rows)


def concept_interner(conn):
    """Return (fn, flush) that maps (taxonomy, tag) -> a stable concept id."""
    ids = _load_concept_ids(conn)
    pending: list[tuple] = []

    def concept_id(taxonomy: str, tag: str, label=None, description=None) -> int:
        key = (taxonomy, tag)
        cid = ids.get(key)
        if cid is None:
            cid = len(ids) + 1
            ids[key] = cid
            pending.append((cid, taxonomy, tag, label, description))
        return cid

    def flush() -> None:
        if pending:
            conn.executemany(
                "INSERT OR IGNORE INTO concepts (id, taxonomy, tag, label, description) "
                "VALUES (?, ?, ?, ?, ?)",
                list(pending),
            )
            pending.clear()

    return concept_id, flush


def ensure_bulk_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_CONCEPTS)
    conn.execute(_CREATE_FILERS)
    conn.execute(_CREATE_FACTS)
    conn.commit()


def accn_to_int(accn: str) -> int | None:
    """`0001193125-10-016098` -> 119312510016098.

    An accession is 10+2+6 digits, which fits an int64 with room to spare, so
    it costs 8 bytes instead of 21 and stays losslessly reversible.
    """
    digits = str(accn).replace("-", "")
    if not digits.isdigit():
        return None
    return int(digits)


def accn_to_str(value: int) -> str:
    digits = f"{int(value):018d}"
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def _load_concept_ids(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    return {
        (row[0], row[1]): row[2]
        for row in conn.execute("SELECT taxonomy, tag, id FROM concepts")
    }


def fact_rows(payload: dict, concept_id) -> list[tuple]:
    """One companyfacts document -> insertable fact rows (every taxonomy)."""
    cik = payload.get("cik")
    if cik is None:
        return []
    cik = int(cik)
    rows: list[tuple] = []
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        if not isinstance(concepts, dict):
            continue
        for tag, concept in concepts.items():
            cid = concept_id(taxonomy, tag, concept.get("label"), concept.get("description"))
            for unit, entries in (concept.get("units") or {}).items():
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    end, val, filed = item.get("end"), item.get("val"), item.get("filed")
                    accn = item.get("accn")
                    if end is None or val is None or filed is None or accn is None:
                        continue
                    accn_int = accn_to_int(accn)
                    if accn_int is None:
                        continue
                    try:
                        value = float(val)
                    except (TypeError, ValueError):
                        # Non-numeric facts exist (text blocks); they are not
                        # what this corpus is for.
                        continue
                    rows.append(
                        (
                            cik,
                            cid,
                            str(end),
                            str(filed),
                            str(unit),
                            str(item.get("start") or ""),
                            accn_int,
                            item.get("fy"),
                            item.get("fp"),
                            item.get("form"),
                            item.get("frame"),
                            value,
                        )
                    )
    return rows


def ingest_companyfacts(
    zip_path: Path,
    *,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    limit: int | None = None,
    progress_every: int = 2000,
) -> dict:
    """Load every fact in `companyfacts.zip` into `db_path`. Returns a summary.

    Deliberately not `db.base.connect`: a 139M-row load through WAL grows a
    multi-GB -wal file before the first checkpoint. This is a rebuildable cache
    of a public download, so the build trades durability for speed and the file
    is left in WAL mode for readers afterwards.
    """
    started = time.monotonic()
    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-262144")  # 256 MB page cache
        ensure_bulk_schema(conn)

        concept_id, flush_concepts = concept_interner(conn)

        batch: list[tuple] = []
        filers: list[tuple] = []
        facts_written = 0
        filers_seen = 0
        skipped = 0

        def flush() -> None:
            nonlocal batch, filers
            flush_concepts()
            if filers:
                conn.executemany(
                    "INSERT OR REPLACE INTO filers (cik, entity_name) VALUES (?, ?)",
                    filers,
                )
                filers = []
            upsert_facts(conn, batch)
            batch = []
            conn.commit()

        with zipfile.ZipFile(zip_path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".json")]
            if limit is not None:
                names = names[:limit]
            total = len(names)
            for index, name in enumerate(names, 1):
                try:
                    payload = json.loads(archive.read(name))
                except Exception:  # noqa: BLE001 - one bad file must not kill a 20k-file load
                    logger.warning("unparseable companyfacts entry %s — skipping", name)
                    skipped += 1
                    continue
                rows = fact_rows(payload, concept_id)
                if payload.get("cik") is not None:
                    filers.append((int(payload["cik"]), payload.get("entityName")))
                    filers_seen += 1
                batch.extend(rows)
                facts_written += len(rows)
                if len(batch) >= _BATCH_ROWS:
                    flush()
                if progress_every and index % progress_every == 0:
                    elapsed = time.monotonic() - started
                    logger.warning(
                        "  %d/%d filers  %s facts  %.0fs  (%.0f filers/s)",
                        index, total, f"{facts_written:,}", elapsed, index / max(elapsed, 1e-9),
                    )
            flush()

        concepts_total = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()

    elapsed = time.monotonic() - started
    return {
        "filers": filers_seen,
        "facts": facts_written,
        "concepts": concepts_total,
        "skipped": skipped,
        "seconds": round(elapsed, 1),
        "db_mb": round(db_path.stat().st_size / 1e6, 1),
    }


def sync_ticker_map(
    *,
    db_path: Path = DEFAULT_FUNDAMENTALS_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
) -> int:
    """Refresh `companies` from EDGAR's ticker map so every listed name resolves.

    Only symbol->CIK is touched. SIC/sector stay whatever a previous
    `research.fundamentals` run cached — the bulk zip carries no SIC, and
    re-fetching 20k submissions documents to get it is a separate job.
    """
    import httpx

    from research.fundamentals import _ensure_schema

    with httpx.Client(timeout=60.0, headers={"User-Agent": user_agent}) as client:
        response = client.get(COMPANY_TICKERS_URL)
        response.raise_for_status()
        payload = response.json()

    conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        _ensure_schema(conn)
        rows = [
            (str(entry["ticker"]).strip().upper(), str(entry["cik_str"]).strip())
            for entry in payload.values()
        ]
        conn.executemany(
            """
            INSERT INTO companies (symbol, cik) VALUES (?, ?)
            ON CONFLICT(symbol) DO UPDATE SET cik = excluded.cik
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def download_companyfacts(
    *,
    dest_dir: Path = DEFAULT_EDGAR_DIR,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Path:
    """Fetch the nightly companyfacts zip (~1.4 GB) to `dest_dir`."""
    import httpx

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "companyfacts.zip"
    tmp = dest.with_suffix(".zip.part")
    with httpx.stream(
        "GET",
        COMPANYFACTS_ZIP_URL,
        headers={"User-Agent": user_agent},
        timeout=120.0,
        follow_redirects=True,
    ) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    tmp.replace(dest)
    return dest


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk SEC EDGAR XBRL corpus ingest")
    parser.add_argument("--download", action="store_true", help="Fetch companyfacts.zip first")
    parser.add_argument("--ingest", action="store_true", help="Load the zip into the fundamentals DB")
    parser.add_argument("--sync-tickers", action="store_true", help="Refresh symbol->CIK from EDGAR")
    parser.add_argument("--limit", type=int, help="Only ingest the first N filers (smoke test)")
    parser.add_argument("--zip", type=Path, default=DEFAULT_EDGAR_DIR / "companyfacts.zip")
    parser.add_argument("--db", type=Path, default=DEFAULT_FACTS_DB_PATH,
                        help="Bulk corpus DB (facts/concepts/filers)")
    parser.add_argument("--companies-db", type=Path, default=DEFAULT_FUNDAMENTALS_DB_PATH,
                        help="Curated DB holding the symbol->CIK companies table")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if not (args.download or args.ingest or args.sync_tickers):
        _parse_args(["--help"])
        return

    if args.download:
        path = download_companyfacts(dest_dir=args.zip.parent)
        print(f"downloaded {path} ({path.stat().st_size / 1e9:.2f} GB)")

    if args.sync_tickers:
        print(f"ticker map: {sync_ticker_map(db_path=args.companies_db):,} symbols")

    if args.ingest:
        summary = ingest_companyfacts(args.zip, db_path=args.db, limit=args.limit)
        print(
            f"ingested {summary['facts']:,} facts / {summary['concepts']:,} concepts "
            f"from {summary['filers']:,} filers in {summary['seconds']}s "
            f"({summary['db_mb']:,} MB, {summary['skipped']} skipped)"
        )


if __name__ == "__main__":
    main()
