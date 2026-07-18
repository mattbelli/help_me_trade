"""Read-only Strategy Lab artifact routes and cohort capability stubs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/strategy-lab", tags=["strategy-lab"])

STRATEGY_LAB_ARTIFACTS_DIR = (
    Path(__file__).resolve().parents[1] / "research" / "artifacts" / "strategy_lab"
)

_JSON_CACHE: dict[Path, tuple[int, int, Any]] = {}


def _read_json(path: Path) -> Any:
    """Read JSON while invalidating cached values when mtime or size changes."""
    stat = path.stat()
    cached = _JSON_CACHE.get(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    if cached is not None and cached[:2] == signature:
        return cached[2]
    value = json.loads(path.read_text(encoding="utf-8"))
    _JSON_CACHE[path] = (*signature, value)
    return value


def _artifacts_absent_catalog() -> dict[str, Any]:
    return {
        "sleeves": [],
        "benchmarks": [],
        "capability": "unavailable",
        "error": "artifact_directory_absent",
    }


@router.get("")
def strategy_lab_catalog() -> dict[str, Any]:
    if not STRATEGY_LAB_ARTIFACTS_DIR.is_dir():
        return _artifacts_absent_catalog()

    sleeves: list[dict[str, Any]] = []
    for path in sorted(STRATEGY_LAB_ARTIFACTS_DIR.glob("*.json")):
        if path.name == "benchmarks.json":
            continue
        try:
            artifact = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(artifact, dict):
            continue
        sleeves.append({key: value for key, value in artifact.items() if key != "equity"})

    benchmark_ids: list[str] = []
    benchmark_path = STRATEGY_LAB_ARTIFACTS_DIR / "benchmarks.json"
    try:
        benchmarks = _read_json(benchmark_path)
        series = benchmarks.get("series", {}) if isinstance(benchmarks, dict) else {}
        if isinstance(series, dict):
            benchmark_ids = list(series)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    return {"sleeves": sleeves, "benchmarks": benchmark_ids}


@router.get("/benchmarks")
def strategy_lab_benchmarks() -> Any:
    path = STRATEGY_LAB_ARTIFACTS_DIR / "benchmarks.json"
    try:
        return _read_json(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Strategy Lab benchmarks not found") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Strategy Lab benchmarks unreadable") from exc


@router.get("/cohorts")
def strategy_lab_cohorts() -> dict[str, Any]:
    return {"generated_at": None, "capability": "not_built", "lanes": []}


@router.get("/cohorts/{lane_id}")
def strategy_lab_cohort_lane(lane_id: str) -> None:
    raise HTTPException(status_code=404, detail=f"Strategy Lab cohort lane {lane_id!r} not found")


@router.get("/sleeves/{sleeve_id}")
def strategy_lab_sleeve(sleeve_id: str) -> Any:
    if not sleeve_id or Path(sleeve_id).name != sleeve_id:
        raise HTTPException(status_code=404, detail="Strategy Lab sleeve not found")
    path = STRATEGY_LAB_ARTIFACTS_DIR / f"{sleeve_id}.json"
    try:
        artifact = _read_json(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Strategy Lab sleeve not found") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail="Strategy Lab sleeve unreadable") from exc
    if path.name == "benchmarks.json" or not isinstance(artifact, dict):
        raise HTTPException(status_code=404, detail="Strategy Lab sleeve not found")
    return artifact
