#!/usr/bin/env python
"""Standalone equity-tape collector process (hmt-equity-collector.service).

Owns the Alpaca stocks v2 websocket (1 connection/account) and runs the
market-wide tape pipeline from signals/equity_stream/runner.py — wildcard
trades+quotes, block/dark-pool/sweep/velocity detection, and the local proxy
that serves the API's market hub its watchlist slice. All output lands in
data/trading.db (WAL); the API keeps serving reads untouched.

Interlock: refuses to start unless EQUITY_TAPE_EXTERNAL_COLLECTOR=1 in the
shared .env — the same flag points the API's hub at the local proxy instead of
upstream and stands down its in-process tape flush. One switch, one stocks
socket, never two (Alpaca 406s the second connection).

EQUITY_RUST_COLLECTOR=1 → this process SUPERVISES `collectors/equity_rs` (the
Rust port, IDENTICAL on the recorded-tape A/B) as a child: same unit, same
interlock, same DB + proxy port, creds via env. The two Python-only legs of the
old runner ride beside it in `signals/equity_stream/sidecar.py` — convergence
off the `equity_tape_events` rows, abnormality off the 5-min bars the binary
writes to data/equity_bars_5min.db. Either side dying exits the process
(systemd restarts both). The raw-frame tee is the binary's own
(EQUITY_TAPE_RECORD_DIR, same file format). Rollback = drop the flag + restart.

Run: .venv/bin/python tools/equity_collector.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config.settings loads .env on import — everything below sees the same
# environment the API does.
from config.settings import alpaca_config  # noqa: E402
from signals.equity_stream.recorder import (  # noqa: E402
    RawFrameRecorder,
    default_record_dir,
)
from signals.equity_stream.runner import EquityTapeRunner  # noqa: E402

logger = logging.getLogger("equity_collector")


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip() not in ("0", "false", "no")


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # The API's 15s health probe is a bare TCP connect; websockets.server logs
    # each one as a full InvalidMessage traceback. Loopback-only listener —
    # silence the handshake noise.
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

    if not _flag("EQUITY_TAPE_EXTERNAL_COLLECTOR"):
        logger.error(
            "EQUITY_TAPE_EXTERNAL_COLLECTOR is not 1 — the API hub still owns "
            "the stocks socket; refusing to start a second connection"
        )
        return 2
    cfg = alpaca_config()
    key = str(cfg.get("api_key_id") or "").strip()
    secret = str(cfg.get("api_secret_key") or "").strip()
    if not key or not secret:
        logger.error("Alpaca credentials missing — nothing to stream")
        return 2

    if _flag("EQUITY_RUST_COLLECTOR"):
        return await _main_rust(key, secret)

    # EQUITY_TAPE_RECORD_DIR=<dir> taps every raw frame to <dir>/<ET date>.mpk.zst
    # for tools/equity_tape_replay.py (the Python↔Rust collector A/B harness).
    recorder: RawFrameRecorder | None = None
    rec_dir = default_record_dir()
    if rec_dir is not None:
        recorder = RawFrameRecorder(rec_dir)
        recorder.start()
        logger.info("equity tape recorder ON → %s", rec_dir)
    runner = EquityTapeRunner(key=key, secret=secret, recorder=recorder)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("equity collector starting: wildcard tape + local proxy")
    tasks = [
        asyncio.create_task(runner.stream.run(), name="equity-stream"),
        asyncio.create_task(runner.proxy.serve(), name="equity-proxy"),
        asyncio.create_task(runner.flush_loop(), name="equity-flush"),
        asyncio.create_task(runner.spot_flush_loop(), name="equity-spots"),
        asyncio.create_task(runner.stats_loop(), name="equity-stats"),
        asyncio.create_task(runner.abnormality_loop(), name="equity-abnormality"),
    ]
    stopper = asyncio.create_task(stop.wait(), name="equity-stop")

    # First exit wins: a stop signal, or any leg dying unexpectedly (systemd
    # Restart=always brings the whole process back — one supervisor, not four).
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    if stopper not in done:
        for t in done:
            exc = t.exception()
            logger.error(
                "equity collector: task %s exited%s — shutting down for systemd restart",
                t.get_name(),
                f" ({exc!r})" if exc else "",
            )

    runner.stop()
    stopper.cancel()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    if recorder is not None:
        recorder.stop()
        logger.info(
            "equity tape recorder: frames=%d drops=%d", recorder.frames, recorder.drops
        )
    logger.info("equity collector stopped")
    return 0


async def _main_rust(key: str, secret: str) -> int:
    """Supervise `equity_rs` + host the convergence/abnormality sidecar."""
    from db.base import DEFAULT_DB_PATH
    from db.equity_bars_5min import DEFAULT_PATH as BARS_DB_PATH
    from signals.equity_stream.sidecar import EquitySidecar

    rs_bin = Path(
        os.environ.get("EQUITY_RS_BIN")
        or Path(__file__).resolve().parent.parent / "collectors/equity_rs/target/release/equity_rs"
    )
    if not rs_bin.is_file() or not os.access(rs_bin, os.X_OK):
        logger.error(
            "EQUITY_RUST_COLLECTOR=1 but %s is missing — build it "
            "(cd collectors/equity_rs && cargo build --release) or set EQUITY_RS_BIN",
            rs_bin,
        )
        return 2
    # creds via env (the binary reads APCA_API_KEY_ID/_SECRET_KEY) — never argv
    env = {**os.environ, "APCA_API_KEY_ID": key, "APCA_API_SECRET_KEY": secret}
    proc = await asyncio.create_subprocess_exec(
        str(rs_bin), "--db", str(DEFAULT_DB_PATH), "--bars-db", str(BARS_DB_PATH), env=env,
    )
    logger.info("equity collector (rust): %s pid=%s db=%s + Python sidecar", rs_bin.name, proc.pid, DEFAULT_DB_PATH)

    sidecar = EquitySidecar(alpaca_creds=(key, secret))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(proc.wait(), name="equity-rs"),
        asyncio.create_task(sidecar.convergence_loop(), name="equity-convergence"),
        asyncio.create_task(sidecar.abnormality_loop(), name="equity-abnormality"),
    ]
    stopper = asyncio.create_task(stop.wait(), name="equity-stop")
    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    if stopper not in done:
        for t in done:
            exc = t.exception() if not t.cancelled() else None
            logger.error(
                "equity collector (rust): %s exited%s — shutting down for systemd restart",
                t.get_name(),
                f" (rc={proc.returncode})" if t.get_name() == "equity-rs" else (f" ({exc!r})" if exc else ""),
            )

    sidecar.stop()
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
    logger.info("equity collector (rust) stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
