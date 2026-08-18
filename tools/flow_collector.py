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

Rust spine (stage 3, `References/FLOW_COLLECTOR_RS_SPEC.md`): with
OPTIONS_FLOW_RUST_SPINE=1 this process instead SUPERVISES `collectors/flow_rs`
(`flow_rs live` owns the Databento socket + detectors + writes) and hosts the
Python back half beside it — the REST poll lane and the `backhalf` sidecar
(enrich → rarity → convergence off the rows). Same unit, same interlock, one
process tree: if either half dies the whole thing exits and systemd restarts
both. Pairing + the GTH tape are OFF in this mode until stage 2 ports them.

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
FLOW_RS_BIN = Path(
    os.environ.get("FLOW_RS_BIN")
    or Path(__file__).resolve().parent.parent / "collectors" / "flow_rs" / "target" / "release" / "flow_rs"
)


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

    if _flag("OPTIONS_FLOW_RUST_SPINE"):
        return await _main_rust_spine()

    # Pairing (LIVE_FLOW_PAIRING_SPEC_2026-07-28): matcher + Alpaca wildcard
    # codes sidecar. Since 2026-08-03 (Inc 3, first step) it ENRICHES — matched
    # condition codes are stamped onto the spine's prints in place, so the
    # F-2 spread/hygiene/ISO gates in the detectors finally see codes on the
    # databento lane. OPTIONS_FLOW_PAIRING_METRICS_ONLY=1 reverts to the pure
    # Inc-2 shadow (measure, never touch). Opt-in by flag, and its task is
    # EXCLUDED from the exit wait below — a dead pairing logs and stays dead;
    # it never takes the spine down with it.
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
            # GTH tape (Phase B, 2026-08-09): the sidecar is the only lane that
            # sees the overnight index session — persist it for the morning
            # line. Guarded separately: a tape init failure (e.g. disk) must
            # cost the persist, never the pairing.
            gth_tape = None
            try:
                from signals.options_flow.gth_tape import GthTape

                gth_tape = GthTape()
            except Exception:
                logger.exception(
                    "GTH tape init failed — sidecar runs without overnight persist"
                )
            shadow = ShadowPairing(
                alpaca_key=ap_key,
                alpaca_secret=ap_secret,
                metrics_only=_flag("OPTIONS_FLOW_PAIRING_METRICS_ONLY"),
                gth_tape=gth_tape,
            )
        except Exception:
            logger.exception(
                "pairing shadow requested but failed to init — running without"
            )

    runner = OptionsFlowRunner(
        watchlist_symbols=watchlist_symbols,
        raw_print_tee=shadow.on_dbn if shadow is not None else None,
        print_attach_tee=(
            shadow.attach_print
            if shadow is not None and not shadow.metrics_only
            else None
        ),
        pairing_repairs=(
            shadow.drain_repairs
            if shadow is not None and not shadow.metrics_only
            else None
        ),
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


async def _main_rust_spine() -> int:
    """Supervise `flow_rs live` + host the poll lane and the back-half sidecar."""
    from db.base import DEFAULT_DB_PATH
    from signals.options_flow.backhalf import BackHalf

    if not FLOW_RS_BIN.is_file() or not os.access(FLOW_RS_BIN, os.X_OK):
        logger.error(
            "OPTIONS_FLOW_RUST_SPINE=1 but %s is missing — build it "
            "(cd collectors/flow_rs && cargo build --release) or set FLOW_RS_BIN",
            FLOW_RS_BIN,
        )
        return 2
    if not str(os.environ.get("DATABENTO_API_KEY") or "").strip():
        logger.error("DATABENTO_API_KEY unset — the Rust spine cannot start")
        return 2
    # Pairing (stage 2, 2026-08-18): the binary runs the Alpaca wildcard-trades
    # sidecar itself (matcher + in-place codes + late repairs + GTH tape) —
    # `--pairing`, creds via env, never argv. Same flag as the Python spine.
    argv = [str(FLOW_RS_BIN), "live", "--db", str(DEFAULT_DB_PATH)]
    env = dict(os.environ)
    pairing = False
    if _flag("OPTIONS_FLOW_PAIRING_SHADOW"):
        from config.settings import alpaca_config

        cfg = alpaca_config()
        ap_key = str(cfg.get("api_key_id") or "").strip()
        ap_secret = str(cfg.get("api_secret_key") or "").strip()
        if ap_key and ap_secret:
            argv.append("--pairing")
            env["APCA_API_KEY_ID"] = ap_key
            env["APCA_API_SECRET_KEY"] = ap_secret
            pairing = True
        else:
            logger.warning("OPTIONS_FLOW_PAIRING_SHADOW set but Alpaca credentials missing — codes + GTH tape OFF")

    proc = await asyncio.create_subprocess_exec(*argv, env=env)
    logger.info("flow collector (rust spine): %s live pid=%s db=%s pairing=%s; poll every %ss",
                FLOW_RS_BIN.name, proc.pid, DEFAULT_DB_PATH, pairing, POLL_SECONDS)

    runner = OptionsFlowRunner(watchlist_symbols=watchlist_symbols)
    backhalf = BackHalf()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(proc.wait(), name="flow-rs-spine"),
        asyncio.create_task(backhalf.run(), name="flow-backhalf"),
        asyncio.create_task(_poll_loop(runner), name="flow-poll"),
    ]
    stopper = asyncio.create_task(stop.wait(), name="flow-stop")
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    if stopper not in done:
        for t in done:
            exc = t.exception() if not t.cancelled() else None
            logger.error(
                "flow collector (rust spine): %s exited%s — shutting down for systemd restart",
                t.get_name(),
                f" (rc={proc.returncode})" if t.get_name() == "flow-rs-spine" else (f" ({exc!r})" if exc else ""),
            )

    backhalf.stop()
    stopper.cancel()
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    logger.info("flow collector (rust spine) stopped")
    return 0


if __name__ == "__main__":
    # Not asyncio.run(): its teardown waits on the default executor, and a
    # `to_thread` REST poll mid-flight (Alpaca timeouts run to 30s) held that
    # wait past the deploy script's 6s TERM grace — every restart was a SIGKILL
    # (measured 08-18), which also threw away the in-memory sweep/gauge state
    # a clean stop would have flushed. Once main() has returned, every leg is
    # stopped and the DB handles are closed; the poll thread has nothing left
    # to write, so exit without waiting for it.
    _loop = asyncio.new_event_loop()
    try:
        _rc = _loop.run_until_complete(main())
    except BaseException:  # noqa: BLE001 — last-chance log before a hard exit
        logger.exception("flow collector: main() raised")
        _rc = 1
    logging.shutdown()
    os._exit(_rc)
