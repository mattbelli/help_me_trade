#!/usr/bin/env python
"""Standalone options-flow collector process (hmt-flow-collector.service).

Hosts the intraday flow pipeline from signals/options_flow/runner.py — the
30s REST poll, the OPRA websocket, and the sweep consumer — outside the API
process, so a stalled API event loop can't blind the stream (2026-07-13:
20-30s loop stalls killed both streams) and a hot open can't starve the
trading loops. All output still lands in data/trading.db (WAL); the API keeps
serving reads from there untouched.

Interlock: refuses to start unless OPTIONS_FLOW_EXTERNAL_COLLECTOR=1 in the
shared .env — the same flag stops the API from starting the pipeline
in-process. One switch, one OPRA socket, never two (Alpaca 406s the second
connection).

Run: .venv/bin/python tools/flow_collector.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# signals.universe imports config.settings, which loads .env on import —
# everything below sees the same environment the API does.
from signals.options_flow.runner import OptionsFlowRunner  # noqa: E402
from signals.universe import flow_watch_symbols_fresh  # noqa: E402

logger = logging.getLogger("flow_collector")

POLL_SECONDS = max(10, int(os.environ.get("OPTIONS_FLOW_POLL_SECONDS", "30")))


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip() not in ("0", "false", "no")


def watchlist_symbols() -> list[str]:
    # The API owns settings writes; the _fresh variant drops our settings
    # cache each call so flow-list edits made there are picked up without a
    # collector restart. Source = the dedicated "flow" list (pinned-first,
    # slot-capped; union-of-all-lists fallback) — 07-22 game plan.
    base = flow_watch_symbols_fresh()
    # Round 4: hot-lane promotions ride AFTER the watchlist, so the downstream
    # underlying cap clips promotions first and the founder's list is never
    # evicted (FUSION_ATTENTION_TIERS_ROUND4_SPEC). Import inside the guard:
    # this process owns the OPRA socket — even a broken attention module must
    # never cost it the watchlist it already had.
    try:
        from signals.fusion.attention import merge_hot_lane

        return merge_hot_lane(base)
    except Exception:
        logging.getLogger("hmt.flow_collector").warning(
            "hot-lane merge unavailable — serving watchlist only", exc_info=True
        )
        return base


async def _poll_loop(runner: OptionsFlowRunner) -> None:
    while True:
        await runner.intraday_once()
        await asyncio.sleep(POLL_SECONDS)


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not _flag("OPTIONS_FLOW_EXTERNAL_COLLECTOR"):
        logger.error(
            "OPTIONS_FLOW_EXTERNAL_COLLECTOR is not 1 — the API still owns the "
            "pipeline; refusing to start a second OPRA connection"
        )
        return 2
    if not (_flag("OPTIONS_FLOW_ENABLED") and _flag("OPTIONS_FLOW_STREAM_ENABLED")):
        logger.error(
            "OPTIONS_FLOW_ENABLED/OPTIONS_FLOW_STREAM_ENABLED not set — nothing to run"
        )
        return 2

    # Inc 2 pairing shadow (LIVE_FLOW_PAIRING_SPEC_2026-07-28): matcher +
    # Alpaca wildcard codes sidecar, metrics only. Opt-in by flag, and its
    # task is EXCLUDED from the exit wait below — a dead shadow logs and
    # stays dead; it never takes the spine down with it.
    shadow = None
    if _flag("OPTIONS_FLOW_PAIRING_SHADOW"):
        try:
            from config.settings import alpaca_config, load_settings
            from signals.options_flow.shadow_pairing import ShadowPairing

            provider = str(
                (load_settings().get("options_flow") or {}).get("provider")
                or "alpaca"
            ).strip().lower()
            if provider != "databento":
                # The spine itself owns the one Alpaca OPRA socket under
                # provider=alpaca — the shadow's wildcard sub would 406 it
                # into permanent backoff and blind the whole pipeline.
                raise RuntimeError(
                    f"provider={provider!r}: shadow needs the databento spine"
                )
            cfg = alpaca_config()
            ap_key = str(cfg.get("api_key_id") or "").strip()
            ap_secret = str(cfg.get("api_secret_key") or "").strip()
            if not ap_key or not ap_secret:
                raise RuntimeError("Alpaca credentials missing")
            shadow = ShadowPairing(alpaca_key=ap_key, alpaca_secret=ap_secret)
        except Exception:
            logger.exception(
                "pairing shadow requested but failed to init — running without"
            )

    runner = OptionsFlowRunner(
        watchlist_symbols=watchlist_symbols,
        raw_print_tee=shadow.on_dbn if shadow is not None else None,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info(
        "flow collector starting: poll every %ss, stream + sweep consumer",
        POLL_SECONDS,
    )
    tasks = [
        asyncio.create_task(runner.stream_run(), name="flow-stream"),
        asyncio.create_task(runner.sweep_loop(), name="flow-sweeps"),
        asyncio.create_task(_poll_loop(runner), name="flow-poll"),
    ]
    shadow_task = None
    if shadow is not None:
        shadow_task = asyncio.create_task(shadow.run(), name="flow-pairing-shadow")
        shadow_task.add_done_callback(
            lambda t: logger.error(
                "pairing shadow exited (%r) — collector unaffected", t.exception()
            )
            if not t.cancelled() and t.exception() is not None
            else None
        )
    stopper = asyncio.create_task(stop.wait(), name="flow-stop")

    # First exit wins: a stop signal, or any leg dying unexpectedly (systemd
    # Restart=always brings the whole process back — one supervisor, not three).
    done, _ = await asyncio.wait(
        [*tasks, stopper], return_when=asyncio.FIRST_COMPLETED
    )
    if stopper not in done:
        for t in done:
            exc = t.exception()
            logger.error(
                "flow collector: task %s exited%s — shutting down for systemd restart",
                t.get_name(),
                f" ({exc!r})" if exc else "",
            )

    runner.stop()
    stopper.cancel()
    if shadow_task is not None:
        shadow.stop()
        shadow_task.cancel()
        try:
            await shadow_task
        except (asyncio.CancelledError, Exception):
            pass
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    logger.info("flow collector stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
