"""/api/collectors/stats — reads the Rust collectors' 1 s snapshots + stamps age."""

from __future__ import annotations

import json
import time
from pathlib import Path

from api.collector_routes import read_snapshot


def test_read_snapshot_stamps_age_and_stale(tmp_path: Path):
    (tmp_path / "equity.json").write_text(json.dumps({"name": "equity", "ts": time.time(), "rate_1s": 5}))
    (tmp_path / "flow.json").write_text(json.dumps({"name": "flow", "ts": time.time() - 60}))
    e = read_snapshot("equity", tmp_path)
    assert e["rate_1s"] == 5 and e["stale"] is False and e["age_s"] < 5
    f = read_snapshot("flow", tmp_path)
    assert f["stale"] is True and f["age_s"] >= 59
    assert read_snapshot("nope", tmp_path) is None
    (tmp_path / "bad.json").write_text("{not json")
    assert read_snapshot("bad", tmp_path) is None
