"""Raw-frame recorder for the equity wildcard stream — the tape, verbatim.

Alpaca allows ONE stocks socket per account, so a standalone recorder can't sit
beside the collector; instead the collector taps every received frame here.
Format: a zstd stream of msgpack ``[recv_ns, frame_bytes]`` pairs, one file per
ET session date, appended across reconnects (zstd frames concatenate; the
reader decodes across them). ``tools/equity_tape_replay.py`` serves a file back
over the same Alpaca protocol so any collector — Python or Rust — can be run
against a real session, at real pace or as fast as it can decode.

Hot-path cost is one deque append per FRAME (not per message; a frame carries
~10-100 messages). A writer thread drains to disk; if it can't keep up the
newest frames are dropped and counted — the live tape must never wait on disk.
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import msgpack
import zstandard

from trading.market_hours import _ET

logger = logging.getLogger(__name__)

RECORD_QUEUE_MAX = 200_000  # frames; ~a few seconds of the close burst
_WRITER_IDLE_SLEEP = 0.05
_ZSTD_LEVEL = 3


def default_record_dir() -> Path | None:
    raw = os.environ.get("EQUITY_TAPE_RECORD_DIR", "").strip()
    return Path(raw) if raw else None


class RawFrameRecorder:
    """Thread-drained, drop-on-overflow frame sink; ``record()`` is recv-loop safe."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._queue: collections.deque[tuple[int, bytes]] = collections.deque()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="equity-tape-recorder", daemon=True
        )
        self.frames = 0
        self.bytes = 0
        self.drops = 0
        self._path: Path | None = None
        self._fh = None
        self._writer = None
        self._packer = msgpack.Packer(use_bin_type=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)

    @property
    def path(self) -> Path | None:
        return self._path

    # -- hot path -------------------------------------------------------------------

    def record(self, raw: bytes, recv_ns: int) -> None:
        if len(self._queue) >= RECORD_QUEUE_MAX:
            self.drops += 1
            return
        self._queue.append((recv_ns, raw))

    # -- writer thread ----------------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop.is_set() or self._queue:
                if not self._queue:
                    time.sleep(_WRITER_IDLE_SLEEP)
                    continue
                recv_ns, raw = self._queue.popleft()
                self._write(recv_ns, raw)
        except Exception:
            logger.exception("equity tape recorder: writer died — recording stops")
        finally:
            self._close()

    def _write(self, recv_ns: int, raw: bytes) -> None:
        # One file per ET session date, judged on arrival time.
        day = datetime.fromtimestamp(recv_ns / 1e9, _ET).date().isoformat()
        path = self._dir / f"{day}.mpk.zst"
        if path != self._path:
            self._close()
            self._fh = open(path, "ab")
            self._writer = zstandard.ZstdCompressor(level=_ZSTD_LEVEL).stream_writer(
                self._fh
            )
            self._path = path
            logger.info("equity tape recorder: writing %s", path)
        assert self._writer is not None
        self._writer.write(self._packer.pack([recv_ns, raw]))
        self.frames += 1
        self.bytes += len(raw)

    def _close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.flush(zstandard.FLUSH_FRAME)
                self._writer.close()
            except Exception:
                logger.warning("equity tape recorder: close failed", exc_info=True)
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
        self._writer = None
        self._fh = None
        self._path = None


def read_frames(path: Path) -> Iterator[tuple[int, bytes]]:
    """Yield ``(recv_ns, frame_bytes)`` from a recording, across zstd frames.

    Tolerates a file that is still being written (the live collector's current
    session): the trailing partial zstd frame / partial msgpack item is treated
    as end-of-tape, so the morning's first N minutes can be replayed at 9:40."""
    with open(path, "rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh, read_across_frames=True)
        unpacker = msgpack.Unpacker(reader, raw=False, use_list=True)
        try:
            for item in unpacker:
                yield int(item[0]), item[1]
        except (zstandard.ZstdError, msgpack.exceptions.ExtraData, ValueError) as exc:
            logger.info("recording %s: truncated tail (%s) — treating as end of tape", path, exc)
