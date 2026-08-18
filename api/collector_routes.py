"""Live collector stats for the toolbar feed panels.

The Rust collectors (`collectors/equity_rs`, `collectors/flow_rs`) each write a
JSON snapshot every second to ``data/collector_stats/<name>.json`` (tmp+rename)
— rates, latency percentiles for the last second and the ET day, a per-minute
series, counters. This route just reads both files and stamps how old each is,
so the UI can show "stale" when a collector is down. No DB, no network.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

ROOT = Path(__file__).resolve().parent.parent
STATS_DIR = Path(os.environ.get("COLLECTOR_STATS_DIR", str(ROOT / "data" / "collector_stats")))
NAMES = ("equity", "flow")


def read_snapshot(name: str, stats_dir: Path | None = None) -> dict | None:
    path = (stats_dir or STATS_DIR) / f"{name}.json"
    try:
        raw = path.read_text()
        snap = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snap, dict):
        return None
    ts = snap.get("ts")
    age = time.time() - float(ts) if isinstance(ts, (int, float)) else None
    snap["age_s"] = round(age, 1) if age is not None else None
    # 5 s without a snapshot = the collector process is gone (or wedged).
    snap["stale"] = age is None or age > 5.0
    return snap


@router.get("/api/collectors/stats")
async def collectors_stats() -> dict:
    return {name: read_snapshot(name) for name in NAMES}
