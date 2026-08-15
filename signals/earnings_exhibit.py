"""Earnings releases straight off EDGAR, minutes after they are furnished.

The XBRL corpus (`research.edgar_bulk`) is complete but slow: an earnings 8-K
never lands in companyfacts at all — those facts arrive with the following
10-Q, measured at ~2 days. The release itself is furnished as the EX-99.1
exhibit of an Item 2.02 8-K, and that is public ~5 minutes after the tape moves
(measured on AMZN 2026-07-30: tape 16:01, wire 16:02, exhibit 16:06:45).

So this is the freshness path, and it carries what the corpus structurally
cannot: companyfacts strips XBRL dimensions, so segment revenue is absent by
construction — but the exhibit says "AWS segment sales increased 37% to $33.1
billion" in plain text, along with guidance and the full statements.

⚠️ Nothing here beats the tape. Even Benzinga printed a minute AFTER the 16:01
move. This is the complete document, not a race.
"""

from __future__ import annotations

import argparse
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db.base import connect
from research.bars import RESEARCH_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_RELEASES_DB_PATH = RESEARCH_DATA_DIR / "earnings.db"
DEFAULT_USER_AGENT = "help_me_trade-research matt.belliveau@gmail.com"

# The feed is a rolling window of the latest 100 filings whatever `count` says.
# Measured on a heavy earnings evening: 100 entries spanned 74 minutes (~1.4
# filings/min), so a poll interval well under an hour cannot miss one.
CURRENT_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
    "&company=&dateb=&owner=include&count=100&output=atom"
)
# Per-company 8-K atom — the backfill path. The 5-min poll stays on CURRENT_FEED.
COMPANY_8K_FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
    "&type=8-K&owner=exclude&count=40&output=atom"
)
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

_ATOM = "{http://www.w3.org/2005/Atom}"
_ET_TZ = ZoneInfo("America/New_York")

# Item 2.02 (Results of Operations and Financial Condition) IS the earnings
# release — the atom summary lists a filing's items, so this costs no extra
# request.
EARNINGS_ITEM = "2.02"

_CREATE_RELEASES = """
CREATE TABLE IF NOT EXISTS earnings_releases (
    accession TEXT NOT NULL PRIMARY KEY,
    cik INTEGER NOT NULL,
    symbol TEXT,
    company TEXT,
    filed_date TEXT NOT NULL,
    accepted_et TEXT NOT NULL,
    session TEXT,
    items TEXT,
    exhibit TEXT,
    body TEXT,
    chars INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL
)
"""
_CREATE_RELEASES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_releases_symbol_accepted
    ON earnings_releases (symbol, accepted_et DESC)
"""


def ensure_release_schema(conn) -> None:
    conn.execute(_CREATE_RELEASES)
    conn.execute(_CREATE_RELEASES_INDEX)
    conn.commit()


def session_of(accepted_et: str) -> str | None:
    """`bmo` / `amc` / `dmh` from an offset-bearing Eastern timestamp.

    Takes an ISO string that already carries its UTC offset (what the atom feed
    and the native ACCEPTANCE-DATETIME header give). It deliberately will NOT
    accept a naive stamp: the submissions JSON's `acceptanceDateTime` wears a
    `Z` that is only sometimes real, so any hour derived from it is a coin flip.
    """
    try:
        moment = datetime.fromisoformat(accepted_et)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    clock = moment.astimezone(_ET_TZ).time()
    if clock < datetime.strptime("09:30", "%H:%M").time():
        return "bmo"
    if clock >= datetime.strptime("16:00", "%H:%M").time():
        return "amc"
    return "dmh"


def parse_current_feed(xml_text: str) -> list[dict]:
    """Atom feed -> Item 2.02 filings only, with a trustworthy Eastern timestamp.

    `<updated>` carries an explicit offset (`-04:00`/`-05:00`, and it tracks DST)
    and was verified against EDGAR's own ACCEPTANCE-DATETIME header on filers of
    both kinds — including NVDA, whose submissions JSON reports the same instant
    as `20:21Z`. The feed is right where that JSON field is not.
    """
    root = ET.fromstring(xml_text.encode("utf-8", "replace"))
    out: list[dict] = []
    for entry in root.findall(f"{_ATOM}entry"):
        summary = (entry.findtext(f"{_ATOM}summary") or "")
        items = re.findall(r"Item (\d+\.\d+):", summary)
        if EARNINGS_ITEM not in items:
            continue
        title = entry.findtext(f"{_ATOM}title") or ""
        link = entry.find(f"{_ATOM}link")
        href = link.get("href") if link is not None else ""
        match = re.search(r"/data/(\d+)/(\d+)/([\d-]+)-index", href or "")
        if not match:
            continue
        cik, folder, accession = match.groups()
        company = re.sub(r"^8-K\s*-\s*", "", title)
        company = re.sub(r"\s*\(\d{10}\)\s*\(Filer\)\s*$", "", company).strip()
        out.append(
            {
                "cik": int(cik),
                "folder": folder,
                "accession": accession,
                "company": company,
                "accepted_et": entry.findtext(f"{_ATOM}updated") or "",
                "items": ",".join(items),
            }
        )
    return out


def exhibit_text(html: str) -> str:
    """EX-99.1 HTML -> readable text, tables kept row-wise.

    A regex tag-stripper turns an earnings release into a column of orphaned
    numbers — the labels live in sibling cells. Rendering each `<tr>` as
    pipe-joined cells keeps "Net sales | 200,576 | 167,702" legible to a model.
    """
    import lxml.html

    doc = lxml.html.fromstring(html)
    for node in doc.xpath("//script|//style"):
        node.getparent().remove(node)
    lines: list[str] = []
    for element in doc.iter():
        if element.tag == "tr":
            cells = [
                " ".join(cell.text_content().split())
                for cell in element.xpath("./td|./th")
            ]
            cells = [c for c in cells if c]
            if cells:
                lines.append(" | ".join(cells))
        elif element.tag in ("p", "div", "h1", "h2", "h3", "li") and not element.xpath(
            ".//table"
        ):
            text = " ".join(element.text_content().split())
            if text and (not lines or lines[-1] != text):
                lines.append(text)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _headers_url(cik: int, folder: str, accession: str) -> str:
    return f"{ARCHIVE_BASE}/{cik}/{folder}/{accession}-index-headers.html"


def parse_filing_header(html: str) -> tuple[str | None, str | None]:
    """(native ACCEPTANCE-DATETIME as YYYYMMDDHHMMSS in ET, EX-99.1 filename).

    One request answers both questions, which is why this is the fetch used —
    the header block lists every document with its TYPE.
    """
    # The header block appears both raw and HTML-escaped on the same page, so
    # match either delimiter rather than relying on which copy comes first.
    accepted = re.search(r"ACCEPTANCE-DATETIME(?:&gt;|[>:])\s*(\d{14})", html)
    exhibit = None
    for match in re.finditer(
        r"(?:&lt;|<)TYPE(?:&gt;|>)([^\s<&]+).*?(?:&lt;|<)FILENAME(?:&gt;|>)([^\s<&]+)",
        html,
        re.S,
    ):
        kind, name = match.group(1), match.group(2)
        if kind.upper().startswith("EX-99") and name.lower().endswith((".htm", ".html")):
            exhibit = name
            break
    return (accepted.group(1) if accepted else None), exhibit


def _symbol_for_cik(cik: int, *, companies_db: Path | None = None) -> str | None:
    """Map a CIK to a listed ticker.

    One CIK often has many share classes (BAC + 16 preferreds). Prefer the
    common share: no hyphen, then shortest symbol. Length-only used to pick
    BML-PG over BAC.
    """
    from research.fundamentals import DEFAULT_FUNDAMENTALS_DB_PATH

    conn = connect(companies_db or DEFAULT_FUNDAMENTALS_DB_PATH)
    try:
        row = conn.execute(
            """
            SELECT symbol FROM companies WHERE CAST(cik AS INTEGER) = ?
            ORDER BY (symbol LIKE '%-%') ASC, length(symbol) ASC
            LIMIT 1
            """,
            (int(cik),),
        ).fetchone()
    except Exception:  # noqa: BLE001 - a missing companies table is not fatal here
        return None
    finally:
        conn.close()
    return row["symbol"] if row else None


def poll_once(
    *,
    db_path: Path = DEFAULT_RELEASES_DB_PATH,
    user_agent: str = DEFAULT_USER_AGENT,
    limit: int | None = None,
    listed_only: bool = True,
    companies_db: Path | None = None,
) -> dict:
    """Fetch new Item 2.02 releases and store their exhibit text. Idempotent."""
    import httpx

    stored = skipped_known = skipped_unlisted = failures = 0
    conn = connect(db_path)
    try:
        ensure_release_schema(conn)
        with httpx.Client(
            timeout=60.0, headers={"User-Agent": user_agent}, follow_redirects=True
        ) as client:
            feed = client.get(CURRENT_FEED_URL)
            feed.raise_for_status()
            entries = parse_current_feed(feed.text)
            if limit:
                entries = entries[:limit]
            for entry in entries:
                outcome = _ingest_entry(
                    conn, client, entry, listed_only=listed_only, companies_db=companies_db
                )
                if outcome == "stored":
                    stored += 1
                elif outcome == "known":
                    skipped_known += 1
                elif outcome == "unlisted":
                    skipped_unlisted += 1
                else:
                    failures += 1
    finally:
        conn.close()

    return {
        "stored": stored,
        "already_had": skipped_known,
        "unlisted": skipped_unlisted,
        "failed": failures,
    }


def _ingest_entry(
    conn,
    client,
    entry: dict,
    *,
    listed_only: bool,
    companies_db: Path | None = None,
) -> str:
    """Store one Item 2.02 filing. Returns stored|known|unlisted|failed."""
    from db.base import now_iso

    known = conn.execute(
        "SELECT 1 FROM earnings_releases WHERE accession = ?",
        (entry["accession"],),
    ).fetchone()
    if known:
        return "known"
    symbol = _symbol_for_cik(entry["cik"], companies_db=companies_db)
    if listed_only and not symbol:
        # Most Item 2.02 filers are funds, trusts and private debt
        # issuers with no ticker — fetching their exhibits is pure cost.
        return "unlisted"
    try:
        header = client.get(
            _headers_url(entry["cik"], entry["folder"], entry["accession"])
        )
        header.raise_for_status()
        native, exhibit = parse_filing_header(header.text)
        body = ""
        if exhibit:
            doc = client.get(
                f"{ARCHIVE_BASE}/{entry['cik']}/{entry['folder']}/{exhibit}"
            )
            doc.raise_for_status()
            body = exhibit_text(doc.text)
    except Exception as exc:  # noqa: BLE001 - one bad filing must not stop the poll
        logger.warning(
            "earnings exhibit %s failed (%s)", entry["accession"], str(exc)[:80]
        )
        return "failed"

    accepted = entry["accepted_et"]
    if native:
        # The native header is the filing's own record; the feed
        # agreed with it on every filing checked, so this is a
        # cross-check rather than a correction.
        stamped = datetime.strptime(native, "%Y%m%d%H%M%S").replace(tzinfo=_ET_TZ)
        accepted = stamped.isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO earnings_releases
            (accession, cik, symbol, company, filed_date, accepted_et,
             session, items, exhibit, body, chars, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry["accession"],
            entry["cik"],
            symbol,
            entry["company"],
            accepted[:10],
            accepted,
            session_of(accepted),
            entry["items"],
            exhibit,
            body,
            len(body),
            now_iso(),
        ),
    )
    conn.commit()
    return "stored"


def _cik_for_symbol(symbol: str, *, companies_db: Path | None = None) -> int | None:
    from research.fundamentals import DEFAULT_FUNDAMENTALS_DB_PATH

    conn = connect(companies_db or DEFAULT_FUNDAMENTALS_DB_PATH)
    try:
        row = conn.execute(
            "SELECT cik FROM companies WHERE symbol = ?",
            (symbol.strip().upper(),),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
    if not row or row["cik"] is None:
        return None
    try:
        return int(str(row["cik"]).lstrip("0") or "0")
    except (TypeError, ValueError):
        return None


def _ciks_for_symbols(
    symbols: set[str], *, companies_db: Path | None = None
) -> dict[str, int]:
    """Batch symbol -> CIK, tolerant of a missing companies table."""
    from research.fundamentals import DEFAULT_FUNDAMENTALS_DB_PATH

    if not symbols:
        return {}
    conn = connect(companies_db or DEFAULT_FUNDAMENTALS_DB_PATH)
    try:
        placeholders = ",".join("?" * len(symbols))
        return {
            row["symbol"]: int(row["cik"])
            for row in conn.execute(
                f"SELECT symbol, CAST(cik AS INTEGER) AS cik FROM companies "
                f"WHERE symbol IN ({placeholders})",
                sorted(symbols),
            )
            if row["cik"]
        }
    except Exception:  # noqa: BLE001 - degrade to symbol-string matching
        return {}
    finally:
        conn.close()


def _symbols_missing_releases(
    *,
    days: int,
    calendar_db: Path,
    releases_db: Path,
    companies_db: Path | None = None,
    as_of: _date | None = None,
) -> list[str]:
    """Calendar names in the last `days` whose print is not in earnings_releases.

    Calendar lives in the app DB (`earnings_calendar`); exhibits live in
    `data/research/earnings.db`. Confirmed dates only — estimates are not a
    reason to hit EDGAR. A name is a candidate when ANY of its recent confirmed
    dates has no stored release within ±2 days.
    """
    as_of = as_of or datetime.now(_ET_TZ).date()
    since = (as_of - timedelta(days=int(days))).isoformat()
    until = as_of.isoformat()

    cal = connect(calendar_db)
    try:
        if not cal.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='earnings_calendar'"
        ).fetchone():
            return []
        cal_rows = cal.execute(
            """
            SELECT symbol, date FROM earnings_calendar
            WHERE date >= ? AND date <= ?
              AND COALESCE(estimated, 0) = 0
            """,
            (since, until),
        ).fetchall()
    finally:
        cal.close()

    rel = connect(releases_db)
    try:
        ensure_release_schema(rel)
        by_sym: dict[str, set[str]] = {}
        by_cik: dict[int, set[str]] = {}
        for row in rel.execute(
            """
            SELECT cik, symbol, accepted_et FROM earnings_releases
            WHERE accepted_et >= ?
            """,
            (since,),
        ):
            day = (row["accepted_et"] or "")[:10]
            if row["symbol"]:
                by_sym.setdefault(row["symbol"], set()).add(day)
            if row["cik"] is not None:
                by_cik.setdefault(int(row["cik"]), set()).add(day)
    finally:
        rel.close()

    # Coverage is per COMPANY, not per symbol string: the store labels a filer
    # with its shortest share class (Alphabet = GOOG), while the calendar lists
    # every class (GOOG and GOOGL). Matching on symbol alone made such names
    # eternal candidates — refetched nightly, never satisfied.
    cik_of = _ciks_for_symbols(
        {row["symbol"] for row in cal_rows}, companies_db=companies_db
    )
    missing: set[str] = set()
    for row in cal_rows:
        symbol = row["symbol"]
        try:
            day = _date.fromisoformat(row["date"])
        except (TypeError, ValueError):
            continue
        window = {(day + timedelta(days=off)).isoformat() for off in range(-2, 3)}
        covered = set(by_sym.get(symbol, set()))
        cik = cik_of.get(symbol)
        if cik is not None:
            covered |= by_cik.get(cik, set())
        if not (covered & window):
            missing.add(symbol)

    # One candidate per company — share classes resolve to the same 8-K feed.
    out, seen_ciks = [], set()
    for symbol in sorted(missing):
        cik = cik_of.get(symbol)
        if cik is not None:
            if cik in seen_ciks:
                continue
            seen_ciks.add(cik)
        out.append(symbol)
    return out


def backfill_missing(
    *,
    days: int = 21,
    db_path: Path = DEFAULT_RELEASES_DB_PATH,
    calendar_db: Path | None = None,
    companies_db: Path | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    sleep_s: float = 0.13,
    as_of: _date | None = None,
) -> dict:
    """Fetch Item 2.02 8-Ks for calendar names in the last `days` with no stored release.

    Uses the per-company 8-K atom (not the live current-events feed). The 5-min
    poll stays on the current feed; this is the nightly hole-fill for names
    whose print landed before the archive existed (first fetch 2026-07-31).
    """
    import httpx

    from db.base import DEFAULT_DB_PATH

    names = _symbols_missing_releases(
        days=days,
        calendar_db=calendar_db or DEFAULT_DB_PATH,
        releases_db=db_path,
        companies_db=companies_db,
        as_of=as_of,
    )
    stored = skipped_known = skipped_unlisted = failures = 0
    companies = 0
    conn = connect(db_path)
    try:
        ensure_release_schema(conn)
        with httpx.Client(
            timeout=60.0, headers={"User-Agent": user_agent}, follow_redirects=True
        ) as client:
            for i, symbol in enumerate(names):
                cik = _cik_for_symbol(symbol, companies_db=companies_db)
                if not cik:
                    skipped_unlisted += 1
                    continue
                companies += 1
                if i:
                    time.sleep(sleep_s)
                try:
                    feed = client.get(COMPANY_8K_FEED_URL.format(cik=f"{cik:010d}"))
                    feed.raise_for_status()
                    entries = [
                        e for e in parse_current_feed(feed.text) if "2.02" in e["items"]
                    ]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("8-K backfill %s failed (%s)", symbol, str(exc)[:80])
                    failures += 1
                    continue
                for entry in entries:
                    outcome = _ingest_entry(
                        conn,
                        client,
                        entry,
                        listed_only=True,
                        companies_db=companies_db,
                    )
                    if outcome == "stored":
                        stored += 1
                    elif outcome == "known":
                        skipped_known += 1
                    elif outcome == "unlisted":
                        skipped_unlisted += 1
                    else:
                        failures += 1
                    time.sleep(sleep_s)
    finally:
        conn.close()

    return {
        "candidates": len(names),
        "companies": companies,
        "stored": stored,
        "already_had": skipped_known,
        "unlisted": skipped_unlisted,
        "failed": failures,
    }


def latest_releases(
    symbol: str | None = None,
    *,
    limit: int = 10,
    with_body: bool = False,
    db_path: Path = DEFAULT_RELEASES_DB_PATH,
    companies_db: Path | None = None,
) -> list[dict]:
    """Most recent stored releases, newest first."""
    columns = (
        "accession, cik, symbol, company, filed_date, accepted_et, session, exhibit, chars"
    )
    if with_body:
        columns += ", body"
    conn = connect(db_path)
    try:
        ensure_release_schema(conn)
        where, params = "", []
        if symbol:
            # Match by CIK too: the store labels a filer with its shortest share
            # class (Alphabet stores as GOOG), so a GOOGL lookup by symbol string
            # alone reads back empty for a company we hold.
            sym = symbol.strip().upper()
            cik = _cik_for_symbol(sym, companies_db=companies_db)
            if cik is not None:
                where = "WHERE (symbol = ? OR cik = ?)"
                params += [sym, cik]
            else:
                where = "WHERE symbol = ?"
                params.append(sym)
        rows = conn.execute(
            f"SELECT {columns} FROM earnings_releases {where} "
            "ORDER BY accepted_et DESC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EDGAR Item 2.02 earnings-release reader")
    parser.add_argument("--poll", action="store_true", help="Fetch new releases now")
    parser.add_argument("--limit", type=int, help="Cap filings processed this poll")
    parser.add_argument("--all-filers", action="store_true", help="Include filers with no ticker")
    parser.add_argument(
        "--backfill-days",
        type=int,
        metavar="N",
        help="Per-company 8-K atom for calendar names in the last N days with no stored release",
    )
    parser.add_argument("--latest", metavar="SYMBOL", nargs="?", const="", help="Show stored releases")
    parser.add_argument("--show", metavar="SYMBOL", help="Print the newest release text for a symbol")
    parser.add_argument("--db", type=Path, default=DEFAULT_RELEASES_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if not (
        args.poll or args.backfill_days is not None or args.latest is not None or args.show
    ):
        _parse_args(["--help"])
        return

    if args.poll:
        summary = poll_once(
            db_path=args.db, limit=args.limit, listed_only=not args.all_filers
        )
        print(
            f"stored {summary['stored']}  already_had {summary['already_had']}  "
            f"unlisted {summary['unlisted']}  failed {summary['failed']}"
        )

    if args.backfill_days is not None:
        summary = backfill_missing(days=args.backfill_days, db_path=args.db)
        print(
            f"backfill candidates {summary['candidates']}  companies {summary['companies']}  "
            f"stored {summary['stored']}  already_had {summary['already_had']}  "
            f"unlisted {summary['unlisted']}  failed {summary['failed']}"
        )

    if args.latest is not None:
        for row in latest_releases(args.latest or None, db_path=args.db):
            print(
                f"{row['accepted_et']}  {(row['symbol'] or '-'):6s} "
                f"{(row['session'] or '-'):4s} {row['chars']:>7,} chars  {row['company'][:44]}"
            )

    if args.show:
        rows = latest_releases(args.show, limit=1, with_body=True, db_path=args.db)
        if not rows:
            print(f"no stored release for {args.show}")
            return
        print(f"=== {rows[0]['company']} — accepted {rows[0]['accepted_et']} ===\n")
        print(rows[0]["body"])


if __name__ == "__main__":
    main()
