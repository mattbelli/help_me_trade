"""Alpaca news → ``db.news`` (perception substrate, Mode A): two lanes, one store.

- WEBSOCKET (``v1beta1/news``, its own connection budget — separate from the
  single-connection stocks stream), subscribed to ``*``.
- REST POLL: a sweep of the recent window every ``_REST_POLL_SEC``. Required, not
  a backup: since 2026-10-21 12:43 UTC the socket carries ONLY fiscal.ai.
  Benzinga still publishes but reaches us by REST alone (measured 11-05: fresh
  socket 0 of 18 Benzinga items, all 87 fiscal.ai). Both lanes upsert by story
  id, so if Benzinga returns to the socket the poll simply finds nothing new.

Facts about published headlines — never a signal.

Liveness: ``news_stream`` is marked on every receive INCLUDING idle timeouts, so
it proves "the socket is connected" and NOTHING about which sources it carries
(it stayed green through the whole Benzinga loss). ``news_poll`` is marked only
when a REST sweep actually succeeded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta

import websockets

from brokers.alpaca import AlpacaAdapter
from brokers.factory import get_broker
from config.settings import alpaca_config, broker_is_configured
from db.news import (
    init_db,
    known_news_ids,
    latest_published_at,
    normalize_news_item,
    upsert_news_item,
)

logger = logging.getLogger(__name__)

# v1beta1 is the live path (probed 2026-07-22: v1beta2 and v2 both 404).
NEWS_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
_RECONNECT_BASE_SEC = 2.0
_RECONNECT_MAX_SEC = 60.0
_REST_POLL_SEC = 15.0
# REST itself lands in batches: Benzinga appears on a 5-minute clock (measured
# 11-05: 16:31:05, 16:36:05, 16:41:05, 16:46:05; max 292s after created_at), so
# every sweep re-reads a window wider than that lag.
_REST_OVERLAP = timedelta(minutes=15)
# How far the first sweep after a start may reach back to fill an outage.
_REST_STARTUP_MAX = timedelta(days=3)


def news_stream_enabled() -> bool:
    return os.environ.get("NEWS_STREAM_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _store_items(raw_items: list[dict]) -> int:
    stored = 0
    for raw in raw_items:
        item = normalize_news_item(raw)
        if item is not None:
            upsert_news_item(item)
            stored += 1
    return stored


def _iso_z(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_sweep_start(now: datetime) -> str:
    """Back to the newest stored row (fills a restart/outage gap), capped."""
    floor = now - _REST_STARTUP_MAX
    latest = latest_published_at()
    if not latest:
        return _iso_z(floor)
    try:
        newest = datetime.fromisoformat(latest)
    except ValueError:
        return _iso_z(floor)
    return _iso_z(max(floor, newest - _REST_OVERLAP))


def _sweep_rest(broker: AlpacaAdapter, start: str) -> int:
    """Store REST items from ``start`` we don't have yet; returns how many."""
    items = broker.get_news_since(start)
    known = known_news_ids([str(i["id"]) for i in items if i.get("id") is not None])
    return _store_items([i for i in items if str(i.get("id")) not in known])


async def news_rest_poll_loop(mark_alive=None) -> None:
    """Long-running REST lane; the only path Benzinga reaches the store by."""
    if not news_stream_enabled():
        return
    await asyncio.to_thread(init_db)
    start = await asyncio.to_thread(_first_sweep_start, datetime.now(UTC))
    while True:
        if broker_is_configured("alpaca"):
            try:
                broker = get_broker("alpaca")
                if not isinstance(broker, AlpacaAdapter):
                    return
                stored = await asyncio.to_thread(_sweep_rest, broker, start)
                if stored:
                    logger.info("news_poll: stored %d new items", stored)
                if mark_alive is not None:
                    mark_alive()
                start = _iso_z(datetime.now(UTC) - _REST_OVERLAP)
            except Exception:
                # Keep ``start``: a failed first sweep must not lose the outage gap.
                logger.warning("news_poll: sweep failed", exc_info=True)
        await asyncio.sleep(_REST_POLL_SEC)


def start_news_lanes(mark_alive) -> list[asyncio.Task]:
    """Both lanes as tasks, each marking its own heartbeat job."""
    return [
        asyncio.create_task(
            news_stream_loop(mark_alive=lambda: mark_alive("news_stream"))
        ),
        asyncio.create_task(
            news_rest_poll_loop(mark_alive=lambda: mark_alive("news_poll"))
        ),
    ]


async def _handshake(ws) -> None:
    """Wait through connected/authenticated, sending auth once if asked."""
    cfg = alpaca_config()
    auth_sent = False
    for _ in range(10):
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        try:
            messages = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(messages, list):
            messages = [messages]
        for item in messages:
            if not isinstance(item, dict):
                continue
            msg_type = item.get("T")
            msg = str(item.get("msg") or "")
            if msg_type == "error":
                # 403 "already authenticated" is success by another name.
                if "authenticated" in msg.lower():
                    return
                raise RuntimeError(f"news stream auth error: {msg}")
            if msg_type == "success" and msg == "authenticated":
                return
            if msg_type == "success" and msg == "connected" and not auth_sent:
                await ws.send(
                    json.dumps(
                        {
                            "action": "auth",
                            "key": cfg.get("api_key_id", "").strip(),
                            "secret": cfg.get("api_secret_key", "").strip(),
                        }
                    )
                )
                auth_sent = True
    raise RuntimeError("news stream handshake did not complete")


async def news_stream_loop(mark_alive=None) -> None:
    """Long-running consumer; reconnects forever with capped backoff."""
    if not news_stream_enabled():
        logger.info("news_stream: disabled (NEWS_STREAM_ENABLED)")
        return
    await asyncio.to_thread(init_db)

    backoff = _RECONNECT_BASE_SEC
    while True:
        if not broker_is_configured("alpaca"):
            await asyncio.sleep(15)
            continue
        cfg = alpaca_config()
        try:
            async with websockets.connect(
                NEWS_WS_URL,
                additional_headers={
                    "APCA-API-KEY-ID": cfg.get("api_key_id", "").strip(),
                    "APCA-API-SECRET-KEY": cfg.get("api_secret_key", "").strip(),
                },
                ping_interval=20,
                ping_timeout=20,
                max_size=2**20,
            ) as ws:
                await _handshake(ws)
                await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                logger.info("news_stream: connected, subscribed to *")
                backoff = _RECONNECT_BASE_SEC
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Idle is normal (overnight); the socket itself is the
                        # proof of life — library pings police the connection.
                        if mark_alive is not None:
                            mark_alive()
                        continue
                    if mark_alive is not None:
                        mark_alive()
                    try:
                        messages = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(messages, list):
                        messages = [messages]
                    news_items = [
                        m for m in messages if isinstance(m, dict) and m.get("T") == "n"
                    ]
                    if news_items:
                        # A store failure (e.g. "database is locked" at the
                        # open) must never escape into the outer except and
                        # tear down a healthy socket — that was 6-25 needless
                        # reconnects/day since 07-23. Drop the batch and move
                        # on; the REST poll re-reads the recent window.
                        try:
                            await asyncio.to_thread(_store_items, news_items)
                        except Exception:
                            logger.warning(
                                "news_stream: store failed for %d items; dropped",
                                len(news_items),
                                exc_info=True,
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "news_stream: error (%s); reconnecting in %.0fs", exc, backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SEC)
