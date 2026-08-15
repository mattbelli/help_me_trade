"""Read-only Strategy Lab artifact routes and cohort capability stubs."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from db.base import DEFAULT_DB_PATH
from db.managed_positions import list_open_positions
from db.order_executions import get_execution
from db.sqlite import get_all_latest_snapshots
from db.strategies import get_strategy

router = APIRouter(prefix="/api/strategy-lab", tags=["strategy-lab"])

STRATEGY_LAB_ARTIFACTS_DIR = (
    Path(__file__).resolve().parents[1] / "research" / "artifacts" / "strategy_lab"
)
STRATEGY_LAB_DB_PATH = DEFAULT_DB_PATH

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


def _entry_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _unrealized_pct(entry_price: Any, mark: Any) -> float | None:
    if entry_price is None or mark is None:
        return None
    try:
        entry = Decimal(str(entry_price))
        current = Decimal(str(mark))
        if entry == 0:
            return None
        return float((current / entry - Decimal(1)) * Decimal(100))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _planned_exit_note(strategy: dict[str, Any] | None) -> str | None:
    if not strategy:
        return None
    params = strategy.get("params")
    if not isinstance(params, dict):
        return None
    lead = params.get("exit_lead_td")
    cap = params.get("max_holding_days")
    parts: list[str] = []
    if isinstance(lead, (int, float)) and not isinstance(lead, bool):
        unit = "day" if lead == 1 else "days"
        parts.append(f"exit {lead:g} trading {unit} before next earnings")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        parts.append(f"{cap:g}d safety cap")
    return " · ".join(parts) or None


@router.get("/paper")
def strategy_lab_paper() -> dict[str, Any]:
    open_positions = list_open_positions(db_path=STRATEGY_LAB_DB_PATH)
    if not open_positions:
        return {"books": []}

    snapshots = get_all_latest_snapshots(db_path=STRATEGY_LAB_DB_PATH)
    account_marks: dict[tuple[str, str], dict[str, Any]] = {}
    legacy_marks: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        account_id = snapshot.get("account_id")
        for position in snapshot.get("positions") or []:
            symbol = str(position.get("symbol") or "").upper()
            if not symbol:
                continue
            snap = {
                "mark": position.get("current_price"),
                "avg_entry": position.get("avg_entry_price"),
            }
            if account_id:
                account_marks[(str(account_id), symbol)] = snap
            else:
                legacy_marks[symbol] = snap

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in open_positions:
        grouped.setdefault(str(row["strategy_id"]), []).append(row)

    today = datetime.now(UTC).date()
    books: list[dict[str, Any]] = []
    for strategy_id, rows in grouped.items():
        strategy = get_strategy(strategy_id, db_path=STRATEGY_LAB_DB_PATH)
        positions: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            execution_id = row.get("entry_execution_id")
            execution = (
                get_execution(str(execution_id), db_path=STRATEGY_LAB_DB_PATH)
                if execution_id
                else None
            )
            snap = account_marks.get((str(row.get("account_id") or ""), symbol))
            if snap is None:
                snap = legacy_marks.get(symbol, {})
            mark = snap.get("mark")
            entry_price = (execution or {}).get("avg_fill_price")
            entry_source = "fill" if entry_price is not None else None
            if entry_price is None and snap.get("avg_entry") is not None:
                # No linked fill (bulk-rebalance rows): fall back to the broker's
                # account-level average cost — blended if several strategies hold
                # the same symbol in this account.
                entry_price = snap.get("avg_entry")
                entry_source = "broker_avg"
            entry_day = _entry_day(row.get("entry_date"))
            positions.append(
                {
                    "symbol": symbol,
                    "qty": row.get("qty"),
                    "entry_date": row.get("entry_date"),
                    "entry_price": entry_price,
                    "entry_source": entry_source,
                    "mark": mark,
                    "unrealized_pct": _unrealized_pct(entry_price, mark),
                    "days_held": max((today - entry_day).days, 0) if entry_day else None,
                }
            )

        non_null_returns = [
            position["unrealized_pct"]
            for position in positions
            if position["unrealized_pct"] is not None
        ]
        entry_dates = [str(row["entry_date"]) for row in rows if row.get("entry_date")]
        books.append(
            {
                "strategy_id": strategy_id,
                "label": (strategy or {}).get("name") or strategy_id,
                "positions": positions,
                "n_positions": len(positions),
                "mean_unrealized_pct": (
                    sum(non_null_returns) / len(non_null_returns)
                    if non_null_returns
                    else None
                ),
                "oldest_entry": min(entry_dates) if entry_dates else None,
                "planned_exit_note": _planned_exit_note(strategy),
            }
        )

    return {"books": books}


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
