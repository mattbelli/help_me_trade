"""SSE helpers for streaming deep research progress."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from ai.research.graph import run_research


def format_sse_event(phase: str, payload: dict[str, Any]) -> str:
    return f"event: {phase}\ndata: {json.dumps(payload)}\n\n"


def stream_deep_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
) -> Iterator[str]:
    events: list[str] = []

    def on_progress(phase: str, payload: dict[str, Any]) -> None:
        events.append(format_sse_event(phase, payload))

    try:
        result = run_research(
            topic=topic,
            symbol=symbol,
            depth=depth,
            on_progress=on_progress,
        )
        for event in events:
            yield event
        yield format_sse_event(
            "done",
            {
                "artifact_id": result.get("artifact_id"),
                "topic": result.get("topic"),
                "symbol": result.get("symbol"),
                "depth": result.get("depth"),
                "status": result.get("status"),
            },
        )
    except Exception as exc:
        for event in events:
            yield event
        yield format_sse_event("error", {"error": str(exc)})


async def astream_deep_research(
    *,
    topic: str,
    symbol: str | None = None,
    depth: str = "standard",
) -> AsyncIterator[str]:
    import asyncio
    from queue import Queue

    queue: Queue[str | None] = Queue()

    def on_progress(phase: str, payload: dict[str, Any]) -> None:
        queue.put(format_sse_event(phase, payload))

    result_holder: dict[str, Any] = {}
    error_holder: list[Exception] = []

    def worker() -> None:
        try:
            result_holder["result"] = run_research(
                topic=topic,
                symbol=symbol,
                depth=depth,
                on_progress=on_progress,
            )
        except Exception as exc:
            error_holder.append(exc)
        finally:
            queue.put(None)

    import threading

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        while not queue.empty():
            item = queue.get()
            if item is None:
                break
            yield item
            await asyncio.sleep(0)
        if not thread.is_alive() and queue.empty():
            break
        await asyncio.sleep(0.05)

    if error_holder:
        yield format_sse_event("error", {"error": str(error_holder[0])})
        return

    result = result_holder.get("result", {})
    yield format_sse_event(
        "done",
        {
            "artifact_id": result.get("artifact_id"),
            "topic": result.get("topic"),
            "symbol": result.get("symbol"),
            "depth": result.get("depth"),
            "status": result.get("status"),
        },
    )
