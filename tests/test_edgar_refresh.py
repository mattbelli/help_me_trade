"""The nightly companyfacts CRC diff and the ingest watermark — network-free.

Every case here guards a distinction the store cannot otherwise make: "we
checked and nothing was new" against "we never looked" against "we could not
look". Collapsing any two of them is what makes a stale corpus present itself as
current, which is the bug this whole job exists to prevent.

The remote zip is served by an in-process fake that speaks `Range`, so the real
`HttpRangeFile` + `zipfile` path is exercised end to end. No test touches SEC.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from research import edgar_refresh
from research.edgar_bulk import (
    edgar_ingest_watermark,
    ingest_companyfacts,
    record_ingest_watermark,
)
from research.edgar_refresh import (
    cik_from_entry,
    diff_entries,
    refresh_nightly,
    seed_entry_index,
)

AAPL = 320193
TSLA = 1318605

LAST_MODIFIED = "Sat, 01 Aug 2026 04:38:46 GMT"


def _companyfacts(cik: int, *, filed: str, val: float = 1.0) -> dict:
    return {
        "cik": cik,
        "entityName": f"Filer {cik}",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-03-31",
                                "val": val,
                                "filed": filed,
                                "form": "10-Q",
                                "fp": "Q1",
                                "fy": 2026,
                                "accn": "0001234567-26-000001",
                            }
                        ]
                    },
                }
            }
        },
    }


def _write_zip(path: Path, payloads: dict[int, dict]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for cik, payload in payloads.items():
            archive.writestr(f"CIK{cik:010d}.json", json.dumps(payload))
    return path


class _FakeResponse:
    def __init__(self, content: bytes = b"", headers: dict | None = None):
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None


class _FakeZipServer:
    """Serves bytes over HEAD + `Range` GET, the way sec.gov does."""

    def __init__(self, data: bytes, *, accept_ranges: str = "bytes"):
        self.data = data
        self.accept_ranges = accept_ranges
        self.requests = 0

    def __enter__(self) -> _FakeZipServer:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def head(self, url: str) -> _FakeResponse:
        return _FakeResponse(
            headers={
                "Accept-Ranges": self.accept_ranges,
                "Content-Length": str(len(self.data)),
                "Last-Modified": LAST_MODIFIED,
            }
        )

    def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
        self.requests += 1
        span = (headers or {})["Range"].split("=", 1)[1]
        start, end = (int(part) for part in span.split("-"))
        return _FakeResponse(self.data[start : end + 1])


@pytest.fixture
def facts_db(tmp_path: Path) -> Path:
    return tmp_path / "edgar_facts.db"


@pytest.fixture
def serve(monkeypatch):
    """Point `refresh_nightly`'s httpx client at a zip on disk, unpaced."""
    monkeypatch.setattr(edgar_refresh, "_REQUEST_INTERVAL_SEC", 0.0)

    def _serve(zip_path: Path) -> _FakeZipServer:
        server = _FakeZipServer(zip_path.read_bytes())
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: server)
        return server

    return _serve


# --------------------------------------------------------------------------
# The watermark contract (§3)
# --------------------------------------------------------------------------


def test_watermark_round_trips_and_is_none_for_an_unseen_cik(facts_db: Path) -> None:
    assert edgar_ingest_watermark(AAPL, db_path=facts_db) is None  # no db at all

    from db.base import connect
    from research.edgar_bulk import ensure_bulk_schema

    conn = connect(facts_db)
    ensure_bulk_schema(conn)
    record_ingest_watermark(conn, AAPL, source="nightly", latest_filed="2026-07-29")
    conn.commit()
    conn.close()

    mark = edgar_ingest_watermark(AAPL, db_path=facts_db)
    assert mark == {
        "checked_at": mark["checked_at"],
        "source": "nightly",
        "latest_filed": "2026-07-29",
    }
    # A filer the refresh has never touched stays honestly unknown.
    assert edgar_ingest_watermark(TSLA, db_path=facts_db) is None


def test_checked_at_is_tz_aware_utc(facts_db: Path, tmp_path: Path, serve) -> None:
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    serve(zip_path)
    refresh_nightly(db_path=facts_db)

    stamp = datetime.fromisoformat(edgar_ingest_watermark(AAPL, db_path=facts_db)["checked_at"])
    assert stamp.tzinfo is not None, "naive timestamp — the reader cannot tell UTC from local"
    assert stamp.utcoffset().total_seconds() == 0


def test_seed_records_what_we_hold_without_claiming_a_check(
    facts_db: Path, tmp_path: Path
) -> None:
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    assert seed_entry_index(zip_path, db_path=facts_db) == 1
    # We know the entry we hold; we do not know when it was last verified.
    assert edgar_ingest_watermark(AAPL, db_path=facts_db) is None


# --------------------------------------------------------------------------
# Both cadences write it (§3, §4)
# --------------------------------------------------------------------------


def test_nightly_ingests_changed_entries_and_stamps_them(
    facts_db: Path, tmp_path: Path, serve
) -> None:
    zip_path = _write_zip(
        tmp_path / "cf.zip",
        {AAPL: _companyfacts(AAPL, filed="2026-07-29"), TSLA: _companyfacts(TSLA, filed="2026-07-23")},
    )
    serve(zip_path)
    stats = refresh_nightly(db_path=facts_db)

    assert stats["ok"] and stats["changed"] == 2 and stats["ingested"] == 2
    assert stats["last_modified"] == LAST_MODIFIED
    assert edgar_ingest_watermark(AAPL, db_path=facts_db)["latest_filed"] == "2026-07-29"
    assert edgar_ingest_watermark(TSLA, db_path=facts_db)["source"] == "nightly"


def test_bulk_rebuild_also_stamps_the_watermark(facts_db: Path, tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    ingest_companyfacts(zip_path, db_path=facts_db)

    mark = edgar_ingest_watermark(AAPL, db_path=facts_db)
    assert mark["source"] == "bulk"
    assert mark["latest_filed"] == "2026-07-29"


# --------------------------------------------------------------------------
# ★ "checked, nothing new" vs "could not check" (§2, §4)
# --------------------------------------------------------------------------


def test_unchanged_entry_moves_checked_at_but_not_latest_filed(
    facts_db: Path, tmp_path: Path, serve
) -> None:
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    serve(zip_path)
    refresh_nightly(db_path=facts_db)
    first = edgar_ingest_watermark(AAPL, db_path=facts_db)

    # Same snapshot: SEC has nothing new, which is the healthy common case.
    serve(zip_path)
    stats = refresh_nightly(db_path=facts_db)
    second = edgar_ingest_watermark(AAPL, db_path=facts_db)

    assert stats["ok"] and stats["changed"] == 0 and stats["unchanged"] == 1
    assert second["checked_at"] > first["checked_at"], "a verified filer must re-stamp"
    assert second["latest_filed"] == first["latest_filed"], "nothing new must not move the filing"


def test_changed_entry_advances_latest_filed(facts_db: Path, tmp_path: Path, serve) -> None:
    old = _write_zip(tmp_path / "old.zip", {AAPL: _companyfacts(AAPL, filed="2026-05-01")})
    serve(old)
    refresh_nightly(db_path=facts_db)

    new = _write_zip(tmp_path / "new.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29", val=2.0)})
    serve(new)
    stats = refresh_nightly(db_path=facts_db)

    assert stats["changed"] == 1 and stats["ingested"] == 1
    assert edgar_ingest_watermark(AAPL, db_path=facts_db)["latest_filed"] == "2026-07-29"


def test_a_failed_entry_writes_no_watermark_and_stays_pending(
    facts_db: Path, tmp_path: Path, serve, monkeypatch
) -> None:
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    serve(zip_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(edgar_refresh, "fact_rows", _boom)
    stats = refresh_nightly(db_path=facts_db)

    assert stats["ok"] is False, "a run that could check nobody must not report success"
    assert stats["ingested"] == 0 and len(stats["failed"]) == 1
    # Never claims a check it did not make...
    assert edgar_ingest_watermark(AAPL, db_path=facts_db) is None
    # ...and leaves the CRC stale, so the next night retries it. That is the
    # whole resume mechanism: state lives in what we hold, not in a cursor.
    monkeypatch.undo()
    serve(zip_path)
    assert refresh_nightly(db_path=facts_db)["changed"] == 1


def test_quiet_night_is_not_an_error(facts_db: Path, tmp_path: Path, serve) -> None:
    """Zero facts written is the healthy case and must never look like failure."""
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    serve(zip_path)
    refresh_nightly(db_path=facts_db)
    serve(zip_path)
    stats = refresh_nightly(db_path=facts_db)

    assert stats["ok"] is True and stats["facts"] == 0 and stats["unchanged"] == 1


def test_a_server_without_range_support_is_refused(facts_db: Path, tmp_path: Path, monkeypatch) -> None:
    """Falling back to a 1.33 GB nightly download must never happen silently."""
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    server = _FakeZipServer(zip_path.read_bytes(), accept_ranges="none")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: server)

    with pytest.raises(RuntimeError, match="byte ranges"):
        refresh_nightly(db_path=facts_db)


# --------------------------------------------------------------------------
# The plumbing traps
# --------------------------------------------------------------------------


def test_range_reader_serves_zipfile_through_readinto(tmp_path: Path, serve) -> None:
    """`RawIOBase.read` delegates to `readinto` — overriding only `read` raises."""
    zip_path = _write_zip(tmp_path / "cf.zip", {AAPL: _companyfacts(AAPL, filed="2026-07-29")})
    server = serve(zip_path)

    archive, raw = edgar_refresh.open_remote_zip(server, "https://example.invalid/cf.zip")
    try:
        assert [i.filename for i in archive.infolist()] == [f"CIK{AAPL:010d}.json"]
        assert json.loads(archive.read(archive.infolist()[0]))["cik"] == AAPL
    finally:
        archive.close()
    assert raw.requests > 0 and raw.bytes_read < len(zip_path.read_bytes()) * 4


def test_cik_from_entry() -> None:
    assert cik_from_entry("CIK0000320193.json") == 320193
    assert cik_from_entry("README.txt") is None
    assert cik_from_entry("CIKnope.json") is None


def test_diff_splits_changed_from_unchanged(tmp_path: Path) -> None:
    zip_path = _write_zip(
        tmp_path / "cf.zip",
        {AAPL: _companyfacts(AAPL, filed="2026-07-29"), TSLA: _companyfacts(TSLA, filed="2026-07-23")},
    )
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        held = {AAPL: (infos[0].CRC, infos[0].file_size)}
        changed, unchanged = diff_entries(infos, held)

    assert unchanged == [AAPL]
    assert [cik_from_entry(i.filename) for i in changed] == [TSLA]


# --------------------------------------------------------------------------
# Deny by default (§1)
# --------------------------------------------------------------------------


def test_no_flag_invocation_does_nothing(facts_db: Path, monkeypatch, capsys) -> None:
    called: list[str] = []
    monkeypatch.setattr(edgar_refresh, "refresh_nightly", lambda **kw: called.append("run"))
    monkeypatch.setattr(edgar_refresh, "seed_entry_index", lambda *a, **kw: called.append("seed"))

    # Prints help and exits 0, the shape `edgar_bulk.main` already uses.
    with pytest.raises(SystemExit) as exit_info:
        edgar_refresh.main([])
    assert exit_info.value.code == 0
    assert called == [], "a bare invocation must fire no network and no DB write"
    assert not facts_db.exists()
    assert "usage:" in capsys.readouterr().out


def test_main_exit_code_reports_a_failed_run(monkeypatch) -> None:
    """The wrapper's dead-man escalates on rc != 0 — a broken run must return it."""
    stats = {
        "ok": False, "entries": 1, "changed": 1, "unchanged": 0, "ingested": 0,
        "facts": 0, "failed": {"320193": "boom"}, "last_modified": None,
        "requests": 1, "megabytes": 0.0, "seconds": 0.1,
    }
    monkeypatch.setattr(edgar_refresh, "refresh_nightly", lambda **kw: stats)
    assert edgar_refresh.main(["--run"]) == 1

    stats["ok"] = True
    assert edgar_refresh.main(["--run"]) == 0
