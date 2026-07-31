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
import xml.etree.ElementTree as ET
from datetime import datetime
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


def _symbol_for_cik(cik: int) -> str | None:
    from research.fundamentals import DEFAULT_FUNDAMENTALS_DB_PATH

    conn = connect(DEFAULT_FUNDAMENTALS_DB_PATH)
    try:
        row = conn.execute(
            "SELECT symbol FROM companies WHERE cik = ? ORDER BY length(symbol) LIMIT 1",
            (str(cik),),
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
) -> dict:
    """Fetch new Item 2.02 releases and store their exhibit text. Idempotent."""
    import httpx

    from db.base import now_iso

    stored = 0
    skipped_known = 0
    skipped_unlisted = 0
    failures = 0

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
                known = conn.execute(
                    "SELECT 1 FROM earnings_releases WHERE accession = ?",
                    (entry["accession"],),
                ).fetchone()
                if known:
                    skipped_known += 1
                    continue
                symbol = _symbol_for_cik(entry["cik"])
                if listed_only and not symbol:
                    # Most Item 2.02 filers are funds, trusts and private debt
                    # issuers with no ticker — fetching their exhibits is pure cost.
                    skipped_unlisted += 1
                    continue
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
                    failures += 1
                    continue

                accepted = entry["accepted_et"]
                if native:
                    # The native header is the filing's own record; the feed
                    # agreed with it on every filing checked, so this is a
                    # cross-check rather than a correction.
                    stamped = datetime.strptime(native, "%Y%m%d%H%M%S").replace(
                        tzinfo=_ET_TZ
                    )
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
                stored += 1
    finally:
        conn.close()

    return {
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
            where = "WHERE symbol = ?"
            params.append(symbol.strip().upper())
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
    parser.add_argument("--latest", metavar="SYMBOL", nargs="?", const="", help="Show stored releases")
    parser.add_argument("--show", metavar="SYMBOL", help="Print the newest release text for a symbol")
    parser.add_argument("--db", type=Path, default=DEFAULT_RELEASES_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if not (args.poll or args.latest is not None or args.show):
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
