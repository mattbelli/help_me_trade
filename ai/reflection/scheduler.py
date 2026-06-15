"""Background reflection scheduler."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from ai.reflection.service import run_reflection

logger = logging.getLogger(__name__)

REFLECTION_INTERVAL_SECONDS = max(
    300, int(os.environ.get("REFLECTION_INTERVAL_SECONDS", "86400"))
)


async def reflection_loop() -> None:
    """Run reflection on a schedule (default: every 24h)."""
    while True:
        await asyncio.sleep(REFLECTION_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(run_reflection)
            logger.info(
                "Scheduled reflection completed at %s",
                datetime.now(UTC).isoformat(),
            )
        except Exception:
            logger.exception("Scheduled reflection failed")
