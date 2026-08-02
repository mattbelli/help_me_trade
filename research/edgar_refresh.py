"""Nightly EDGAR companyfacts refresh — a CRC diff of SEC's zip over HTTP Range.

Nothing refreshed the companyfacts corpus on a schedule. Measured 2026-08-02,
AAPL's newest facts were 2026-05-01 — its fiscal-Q3 10-Q was simply absent —
while 10-Q/10-K land 0-3 days after the earnings release. So a filer's newest
fundamentals could exist at SEC and stay invisible to us indefinitely.

**Why a CRC diff and not the obvious alternatives** (both were tried and are
worse). Targeting our watchlists covers 1.5% of the symbols a fusion episode
opened for in the last 7 days. Walking SEC's daily index needs calendar,
weekend, holiday and resume bookkeeping plus a form-type filter to maintain —
and it still misses changes: the index named 197 CIKs filing periodic forms on
2026-07-31 while the corpus itself changed for 457 filers that night.

`companyfacts.zip` is 1.33 GiB and a FULL snapshot, regenerated nightly — but
regenerated *deterministically*, which is the opening. Measured Jul-31 vs Aug-1:
19,687 of 20,144 entries (97.7%) byte-identical, 435 changed, 22 added, 0
removed. The server sends `Accept-Ranges: bytes`, so the zip's central directory
— every entry's name, CRC32 and size — costs 1.77 MB to read. One nightly run is
therefore: read the central directory over Range, diff (CRC, size) against what
we hold, Range-fetch only the entries that moved, parse and upsert only those.
~1/18th the wire cost of a nightly full download, and resumability is inherent
because the diff is against what we actually hold, not against a cursor.

`research.edgar_bulk` stays what it is: the manual full rebuild/repair tool. The
whole download is the right move from scratch and the wrong move every night.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import time
import zipfile
from pathlib import Path

from db.base import connect
from research.edgar_bulk import (
    COMPANYFACTS_ZIP_URL,
    DEFAULT_EDGAR_DIR,
    DEFAULT_FACTS_DB_PATH,
    DEFAULT_USER_AGENT,
    concept_interner,
    ensure_bulk_schema,
    fact_rows,
    record_entry_identity,
    record_ingest_watermark,
    upsert_facts,
)

logger = logging.getLogger(__name__)

# SEC's fair-access limit is 10 req/s across sec.gov. Paced per HTTP request
# rather than per entry, because one entry can span several Range reads.
_REQUEST_INTERVAL_SEC = 0.13

# Read-ahead for the ZipFile handle. Sized off the measured distribution of the
# 457 entries that changed on 2026-08-01: 75.6 MB compressed, median 166 KB,
# p90 303 KB. At 256 KB most entries cost one data request (~1,015 requests,
# ~2 min at the paced rate); 64 KB would halve the bytes but nearly double the
# requests, and requests — not bandwidth — are what SEC rate-limits.
_RANGE_BUFFER_BYTES = 256 * 1024

# A nightly diff finding this many changed entries is not a nightly diff — it is
# a first run, a reseed, or a broken ledger. Doing it silently would turn a cron
# job into an unannounced full corpus rebuild.
_UNEXPECTED_CHANGE_COUNT = 5_000


class HttpRangeFile(io.RawIOBase):
    """Random read access to a remote file over HTTP `Range`, for `zipfile`.

    ⚠️ `readinto`, NOT `read`. `RawIOBase.read()` is implemented in terms of
    `readinto()`, so a subclass that overrides only `read` still raises
    `NotImplementedError` the moment `zipfile` reaches for a buffered read.
    """

    def __init__(self, client, url: str, size: int, *, interval_s: float | None = None):
        self._client = client
        self._url = url
        self._size = size
        self._interval_s = _REQUEST_INTERVAL_SEC if interval_s is None else interval_s
        self._pos = 0
        self.requests = 0
        self.bytes_read = 0
        self.last_modified: str | None = None

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._size + offset
        else:
            raise ValueError(f"unsupported whence {whence}")
        self._pos = max(0, min(target, self._size))
        return self._pos

    def readinto(self, buffer) -> int:
        want = min(len(buffer), self._size - self._pos)
        if want <= 0:
            return 0
        last = self._pos + want - 1
        if self._interval_s:
            time.sleep(self._interval_s)
        response = self._client.get(
            self._url, headers={"Range": f"bytes={self._pos}-{last}"}
        )
        response.raise_for_status()
        chunk = response.content
        buffer[: len(chunk)] = chunk
        self._pos += len(chunk)
        self.requests += 1
        self.bytes_read += len(chunk)
        return len(chunk)


def open_remote_zip(client, url: str = COMPANYFACTS_ZIP_URL) -> tuple[zipfile.ZipFile, HttpRangeFile]:
    """Open the remote zip's central directory without downloading the archive.

    Raises if the server will not serve ranges — silently falling back to a
    1.33 GB download every night is exactly the surprise this job exists to
    avoid.
    """
    head = client.head(url)
    head.raise_for_status()
    if head.headers.get("Accept-Ranges", "").lower() != "bytes":
        raise RuntimeError(f"{url} does not advertise byte ranges — refusing to full-download")
    size = int(head.headers["Content-Length"])
    raw = HttpRangeFile(client, url, size)
    # Carried off the same HEAD: the snapshot's own timestamp is the only way to
    # tell from the logs whether the cron slot lands after SEC's regeneration.
    raw.last_modified = head.headers.get("Last-Modified")
    archive = zipfile.ZipFile(io.BufferedReader(raw, buffer_size=_RANGE_BUFFER_BYTES))
    return archive, raw


def cik_from_entry(name: str) -> int | None:
    """`CIK0000320193.json` -> 320193."""
    stem = Path(name).name
    if not stem.startswith("CIK") or not stem.endswith(".json"):
        return None
    digits = stem[3:-5]
    return int(digits) if digits.isdigit() else None


def held_entry_index(conn) -> dict[int, tuple[int, int]]:
    """cik -> (crc, size) of the zip entry we last ingested for that filer."""
    return {
        int(row[0]): (int(row[1]), int(row[2]))
        for row in conn.execute(
            "SELECT cik, entry_crc, entry_size FROM edgar_ingest_watermark "
            "WHERE entry_crc IS NOT NULL AND entry_size IS NOT NULL"
        )
    }


def diff_entries(infos, held: dict[int, tuple[int, int]]) -> tuple[list, list[int]]:
    """Split the remote entries into (changed infos, unchanged ciks).

    Unchanged still matters — those filers were VERIFIED tonight, and being able
    to say "checked, nothing new" about them is the whole point of the watermark.
    """
    changed = []
    unchanged: list[int] = []
    for info in infos:
        cik = cik_from_entry(info.filename)
        if cik is None:
            continue
        if held.get(cik) == (info.CRC, info.file_size):
            unchanged.append(cik)
        else:
            changed.append(info)
    return changed, unchanged


def seed_entry_index(zip_path: Path, *, db_path: Path = DEFAULT_FACTS_DB_PATH) -> int:
    """Record the entry identities of a zip we already ingested. Run once, by hand.

    Without this the first nightly run sees an empty ledger, calls all 20k
    entries "changed", and turns a cron job into a full 1.33 GB rebuild. Reading
    the local zip's central directory is free and says exactly what we hold.

    It stamps NO `checked_at`: we know what is in the file, we do not know when
    it was last verified against SEC, and those filers keep reading back as
    "never checked" until a real run touches them.

    ⚠️ Seed ONLY from a zip that was actually ingested into THIS corpus. The
    claim being recorded is "the corpus already contains this entry's contents",
    and the next night's diff trusts it — seeding from a newer zip than the one
    that was loaded would skip filers we never ingested.
    """
    conn = connect(db_path)
    try:
        ensure_bulk_schema(conn)
        seeded = 0
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                cik = cik_from_entry(info.filename)
                if cik is None:
                    continue
                record_entry_identity(conn, cik, crc=info.CRC, size=info.file_size)
                seeded += 1
        conn.commit()
    finally:
        conn.close()
    return seeded


def refresh_nightly(
    *,
    db_path: Path = DEFAULT_FACTS_DB_PATH,
    url: str = COMPANYFACTS_ZIP_URL,
    user_agent: str = DEFAULT_USER_AGENT,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Diff the remote zip against what we hold and ingest only what moved.

    The returned `ok` is the honest half of this job. "Checked, nothing new" is
    the common healthy night and stays quiet; "could not check" is an error and
    must be loud. They are told apart by whether anything succeeded — never by
    whether anything was written, because a perfectly healthy night writes no
    facts at all.
    """
    import httpx

    started = time.monotonic()
    stats = {
        "ok": True,
        "entries": 0,
        "changed": 0,
        "unchanged": 0,
        "ingested": 0,
        "facts": 0,
        "failed": {},
        "last_modified": None,
        "requests": 0,
        "megabytes": 0.0,
        "seconds": 0.0,
    }

    conn = connect(db_path)
    try:
        ensure_bulk_schema(conn)
        held = held_entry_index(conn)
        concept_id, flush_concepts = concept_interner(conn)

        with httpx.Client(
            timeout=120.0,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        ) as client:
            archive, raw = open_remote_zip(client, url)
            try:
                infos = [i for i in archive.infolist() if i.filename.endswith(".json")]
                stats["entries"] = len(infos)
                stats["last_modified"] = raw.last_modified

                changed, unchanged = diff_entries(infos, held)
                stats["changed"] = len(changed)
                stats["unchanged"] = len(unchanged)
                logger.warning(
                    "companyfacts snapshot %s: %d entries, %d changed, %d unchanged",
                    stats["last_modified"], len(infos), len(changed), len(unchanged),
                )
                if len(changed) >= _UNEXPECTED_CHANGE_COUNT:
                    logger.warning(
                        "  %d entries changed — that is a first run or a reseed, not a "
                        "nightly diff; this run will do a near-full ingest", len(changed),
                    )
                if limit is not None:
                    changed = changed[:limit]

                if dry_run:
                    stats["requests"] = raw.requests
                    stats["megabytes"] = round(raw.bytes_read / 1e6, 2)
                    stats["seconds"] = round(time.monotonic() - started, 1)
                    return stats

                failed: dict[int, str] = {}
                for info in changed:
                    cik = cik_from_entry(info.filename)
                    try:
                        payload = json.loads(archive.read(info))
                        rows = fact_rows(payload, concept_id)
                        flush_concepts()
                        upsert_facts(conn, rows)
                        if payload.get("cik") is not None:
                            conn.execute(
                                "INSERT OR REPLACE INTO filers (cik, entity_name) VALUES (?, ?)",
                                (int(payload["cik"]), payload.get("entityName")),
                            )
                        record_ingest_watermark(
                            conn,
                            cik,
                            source="nightly",
                            # Index 3 is `filed` — see `fact_rows`.
                            latest_filed=max((row[3] for row in rows), default=None),
                            entry_crc=info.CRC,
                            entry_size=info.file_size,
                        )
                        conn.commit()
                        stats["ingested"] += 1
                        stats["facts"] += len(rows)
                    except Exception as exc:  # noqa: BLE001 - one bad entry must not kill the run
                        conn.rollback()
                        failed[cik] = str(exc)[:200]
                        logger.warning("  CIK %s: entry refresh failed (%s)", cik, str(exc)[:80])
                        # Its CRC is deliberately left stale, so tomorrow's diff
                        # picks it up again. That is the resume mechanism.

                # Only after the changed entries are in: an unchanged filer was
                # verified against tonight's snapshot, so it earns a checked_at
                # while keeping its latest_filed and CRC untouched. This is the
                # "checked, nothing new" case, and on a healthy night it is
                # ~97.7% of the corpus.
                for cik in unchanged:
                    record_ingest_watermark(conn, cik, source="nightly")
                conn.commit()

                stats["failed"] = {str(k): v for k, v in failed.items()}
                stats["requests"] = raw.requests
                stats["megabytes"] = round(raw.bytes_read / 1e6, 2)
                if failed and not stats["ingested"]:
                    stats["ok"] = False  # every entry that moved failed to load
            finally:
                archive.close()
    finally:
        conn.close()

    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly EDGAR companyfacts refresh (CRC diff over HTTP Range)"
    )
    parser.add_argument("--run", action="store_true", help="Diff the remote zip and ingest what moved")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the central directory and report the diff; ingest nothing",
    )
    parser.add_argument(
        "--seed-from",
        type=Path,
        metavar="ZIP",
        nargs="?",
        const=DEFAULT_EDGAR_DIR / "companyfacts.zip",
        help="Record entry CRCs from a zip already ingested (run once; stamps no checked_at)",
    )
    parser.add_argument("--limit", type=int, help="Only ingest the first N changed entries (smoke test)")
    parser.add_argument("--db", type=Path, default=DEFAULT_FACTS_DB_PATH, help="Corpus DB path")
    parser.add_argument("--url", default=COMPANYFACTS_ZIP_URL, help="companyfacts.zip URL")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    # Deny by default: a bare invocation (or an import) must not fire network or
    # DB writes — see the `script-mains-deny-by-default` rule.
    if not (args.run or args.dry_run or args.seed_from):
        _parse_args(["--help"])
        return 0

    if args.seed_from:
        print(f"seeded {seed_entry_index(args.seed_from, db_path=args.db):,} entry identities")
        if not (args.run or args.dry_run):
            return 0

    stats = refresh_nightly(
        db_path=args.db, url=args.url, limit=args.limit, dry_run=args.dry_run
    )
    print(
        f"companyfacts {stats['last_modified']}: {stats['entries']:,} entries, "
        f"{stats['changed']:,} changed / {stats['unchanged']:,} unchanged, "
        f"{stats['ingested']:,} ingested ({stats['facts']:,} facts), "
        f"{stats['requests']} requests / {stats['megabytes']} MB in {stats['seconds']}s"
    )
    if stats["failed"]:
        print(f"FAILED entries: {len(stats['failed'])} — {list(stats['failed'])[:10]}")
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
