"""News REST lane: Benzinga reaches the store by REST alone since 2026-10-21."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import signals.news_stream as ns
from brokers.alpaca import AlpacaAdapter
from db.news import init_db, known_news_ids, recent_news, upsert_news_item


def _raw(item_id: int, headline: str = "h", symbol: str = "NSSYM") -> dict:
    return {
        "id": item_id,
        "headline": headline,
        "symbols": [symbol],
        "source": "benzinga",
        "created_at": "2026-11-05T16:00:00Z",
    }


class _FakeBroker(AlpacaAdapter):
    def __init__(self, items=(), error: Exception | None = None):
        self.items = list(items)
        self.error = error
        self.starts: list[str] = []

    def get_news_since(self, start, *, max_pages=200):
        self.starts.append(start)
        if self.error:
            raise self.error
        return self.items


def test_get_news_since_walks_every_page(monkeypatch):
    pages = [
        {"news": [{"id": 1}, {"id": 2}], "next_page_token": "t1"},
        {"news": [{"id": 3}], "next_page_token": None},
    ]
    seen: list[dict] = []
    broker = AlpacaAdapter.__new__(AlpacaAdapter)

    def fake_get(path, *, params=None):
        seen.append(params)
        return pages[len(seen) - 1]

    monkeypatch.setattr(broker, "_get_data", fake_get)
    items = broker.get_news_since("2026-11-05T00:00:00Z")
    assert [i["id"] for i in items] == [1, 2, 3]
    assert seen[0]["start"] == "2026-11-05T00:00:00Z"
    assert "page_token" not in seen[0]
    assert seen[1]["page_token"] == "t1"


def test_get_news_since_raises_on_vendor_failure(monkeypatch):
    broker = AlpacaAdapter.__new__(AlpacaAdapter)

    def boom(path, *, params=None):
        raise RuntimeError("503")

    monkeypatch.setattr(broker, "_get_data", boom)
    with pytest.raises(RuntimeError):
        broker.get_news_since("2026-11-05T00:00:00Z")


def test_sweep_writes_only_unseen_rows(monkeypatch):
    init_db()
    upsert_news_item(
        {
            "id": "9100001",
            "headline": "already here",
            "summary": "",
            "url": "",
            "source": "benzinga",
            "author": "",
            "symbols": ["NSKEEP"],
            "image_url": None,
            "published_at": "2026-11-05T16:00:00Z",
            "updated_at": None,
        }
    )
    written: list[dict] = []
    real = ns.upsert_news_item
    monkeypatch.setattr(
        ns, "upsert_news_item", lambda item: (written.append(item), real(item))
    )
    broker = _FakeBroker([_raw(9100001, "stale copy"), _raw(9100002, "new one", "NSNEW")])

    assert ns._sweep_rest(broker, "2026-11-05T15:00:00Z") == 1
    assert [w["id"] for w in written] == ["9100002"]
    assert known_news_ids(["9100001", "9100002", "nope"]) == {"9100001", "9100002"}
    assert recent_news(symbol="NSNEW")[0]["headline"] == "new one"


def _run_one_pass(monkeypatch, broker) -> list[str]:
    """One loop iteration; the sleep raises so the infinite loop ends."""

    class _Stop(Exception):
        pass

    async def stop(_seconds):
        raise _Stop

    alive: list[str] = []
    monkeypatch.setattr(ns.asyncio, "sleep", stop)
    monkeypatch.setattr(ns, "get_broker", lambda _name: broker)
    monkeypatch.setattr(ns, "broker_is_configured", lambda _name: True)
    with pytest.raises(_Stop):
        asyncio.run(ns.news_rest_poll_loop(mark_alive=lambda: alive.append("x")))
    return alive


def test_poll_marks_alive_on_success(monkeypatch):
    assert _run_one_pass(monkeypatch, _FakeBroker([])) == ["x"]


def test_poll_failure_does_not_mark_alive(monkeypatch):
    broker = _FakeBroker(error=RuntimeError("503"))
    assert _run_one_pass(monkeypatch, broker) == []
    assert broker.starts  # it did try


def test_first_sweep_reaches_back_to_newest_row_with_overlap(monkeypatch):
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(ns, "latest_published_at", lambda: "2026-11-05T15:00:00Z")
    assert ns._first_sweep_start(now) == "2026-11-05T14:45:00Z"


@pytest.mark.parametrize("latest", [None, "not-a-date", "2026-01-01T00:00:00Z"])
def test_first_sweep_is_capped(monkeypatch, latest):
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(ns, "latest_published_at", lambda: latest)
    assert ns._first_sweep_start(now) == ns._iso_z(now - timedelta(days=3))


def test_start_news_lanes_marks_each_lanes_own_heartbeat(monkeypatch):
    async def fake_loop(mark_alive=None):
        mark_alive()

    monkeypatch.setattr(ns, "news_stream_loop", fake_loop)
    monkeypatch.setattr(ns, "news_rest_poll_loop", fake_loop)
    marked: list[str] = []

    async def run():
        await asyncio.gather(*ns.start_news_lanes(marked.append))

    asyncio.run(run())
    assert marked == ["news_stream", "news_poll"]
