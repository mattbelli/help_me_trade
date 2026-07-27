"""Realtime Alpaca news stream → ``db.news`` (perception substrate, Mode A).

One websocket to ``v1beta2/news`` (its own connection budget — separate from
the single-connection stocks stream), subscribed to ``*``: every Benzinga
headline upserts into ``news_items`` the moment it publishes. A REST backfill
on startup warms the store so the UI is never empty after a restart. Facts
about published headlines — never a signal; the bus catalyst-dot wiring is its
own later round (FUSION_EVIDENCE_BUS_SPEC §2.9).

Liveness: marks the ``news_stream`` heartbeat job on every receive INCLUDING
idle timeouts — proof-of-life is "the socket is connected", not "news arrived"
(overnight gaps are normal; a dead socket is not).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets

from brokers.alpaca import AlpacaAdapter
from brokers.factory import get_broker
from config.settings import alpaca_config, broker_is_configured
from db.news import init_db, normalize_news_item, upsert_news_item

logger = logging.getLogger(__name__)

# v1beta1 is the live path (probed 2026-07-22: v1beta2 and v2 both 404).
NEWS_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
_RECONNECT_BASE_SEC = 2.0
_RECONNECT_MAX_SEC = 60.0


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


async def _backfill_once() -> None:
    """One REST page of the global feed so a fresh store isn't empty."""
    try:
        broker = get_broker("alpaca")
        if not isinstance(broker, AlpacaAdapter):
            return
        items = await asyncio.to_thread(broker.get_news, None, limit=50)
        stored = await asyncio.to_thread(_store_items, items)
        logger.info("news_stream: REST backfill stored %d items", stored)
    except Exception:
        logger.warning("news_stream: REST backfill failed", exc_info=True)


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
    await _backfill_once()

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
                        # on; the REST backfill covers gaps on reconnect.
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
