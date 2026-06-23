"""Adam @adaamset level-call event study on 1-min /ES bars (fill-after-post, no look-ahead)."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR
from research.intraday_bars import (
    DEFAULT_INTRADAY_DB_PATH,
    IntradayBar,
    load_cached_minute_bars,
)
from research.intraday_profile import is_rth, to_ny

DEFAULT_TRIGGERS_PATH = RESEARCH_DATA_DIR / "adaamset" / "es_triggers_full.json"
DEFAULT_OUTPUT_CSV = RESEARCH_DATA_DIR / "adam_trigger_study.csv"


@dataclass(frozen=True)
class TriggerResult:
    post_id: str
    post_ts: str
    direction: str
    level: float
    filled: bool
    entry_ts: str | None
    exit_ts: str | None
    exit_px: float | None
    exit_reason: str | None
    points_net: float | None
    slippage: float | None
    tranche_a_px: float | None = None
    tranche_b_px: float | None = None
    tranche_b_reason: str | None = None


@dataclass(frozen=True)
class StudyResult:
    results: list[TriggerResult]
    n_triggers: int
    n_filled: int
    fill_rate: float
    hit_rate: float
    avg_points_net: float
    total_points_net: float
    by_direction: dict[str, dict]
    beta_avg_points: float
    avg_slippage_points: float
    median_slippage_points: float


def _parse_ts(ts: str) -> datetime:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _bar_ts(bar: IntradayBar) -> datetime:
    return _parse_ts(bar["t"])


def load_triggers(path: Path = DEFAULT_TRIGGERS_PATH) -> list[dict]:
    """Load trigger JSON and keep long/short rows only."""
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        row
        for row in raw
        if str(row.get("direction", "")).lower() in {"long", "short"}
    ]


def _find_fill(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    level: float,
    fill_window_hours: float,
) -> tuple[bool, str | None]:
    deadline = post_ts + timedelta(hours=fill_window_hours)
    for bar in bars:
        bar_time = _bar_ts(bar)
        if bar_time <= post_ts:
            continue
        if bar_time > deadline:
            break
        if bar["l"] <= level <= bar["h"]:
            return True, bar["t"]
    return False, None


def _find_post_price_fill(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    fill_window_hours: float,
) -> tuple[bool, str | None, float | None]:
    deadline = post_ts + timedelta(hours=fill_window_hours)
    for bar in bars:
        bar_time = _bar_ts(bar)
        if bar_time <= post_ts:
            continue
        if bar_time > deadline:
            break
        return True, bar["t"], float(bar["o"])
    return False, None, None


def _compute_slippage(*, direction: str, entry_px: float, level: float) -> float:
    if direction == "long":
        return entry_px - level
    return level - entry_px


def _exit_long(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    target_points: float,
    stop_points: float,
    max_hold_hours: float,
) -> tuple[str, float, str]:
    target = entry_px + target_points
    stop = entry_px - stop_points
    deadline = fill_ts + timedelta(hours=max_hold_hours)
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        if bar["l"] <= stop:
            return bar["t"], stop, "stop"
        if bar["h"] >= target:
            return bar["t"], target, "target"
        if bar_time >= deadline:
            return bar["t"], bar["c"], "time"
    last = bars[-1]
    return last["t"], last["c"], "time"


def _exit_short(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    target_points: float,
    stop_points: float,
    max_hold_hours: float,
) -> tuple[str, float, str]:
    target = entry_px - target_points
    stop = entry_px + stop_points
    deadline = fill_ts + timedelta(hours=max_hold_hours)
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        if bar["h"] >= stop:
            return bar["t"], stop, "stop"
        if bar["l"] <= target:
            return bar["t"], target, "target"
        if bar_time >= deadline:
            return bar["t"], bar["c"], "time"
    last = bars[-1]
    return last["t"], last["c"], "time"


def _is_after_rth_close(entry_ts: str) -> bool:
    ny = to_ny(entry_ts)
    local = ny.timetz().replace(tzinfo=None)
    return local >= time(16, 0)


def _rth_session_dates(bars: list[IntradayBar], *, fill_ts: datetime) -> list[date]:
    dates: list[date] = []
    seen: set[date] = set()
    for bar in bars:
        if _bar_ts(bar) <= fill_ts:
            continue
        if not is_rth(bar["t"]):
            continue
        session_date = to_ny(bar["t"]).date()
        if session_date not in seen:
            seen.add(session_date)
            dates.append(session_date)
    return sorted(dates)


def _last_rth_bar_on_date(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    session_date: date,
) -> IntradayBar | None:
    last: IntradayBar | None = None
    for bar in bars:
        if _bar_ts(bar) <= fill_ts:
            continue
        if is_rth(bar["t"]) and to_ny(bar["t"]).date() == session_date:
            last = bar
    return last


def _tranche_a_target_date(entry_ts: str, rth_dates: list[date]) -> date | None:
    if not rth_dates:
        return None
    entry_date = to_ny(entry_ts).date()
    if _is_after_rth_close(entry_ts):
        for session_date in rth_dates:
            if session_date > entry_date:
                return session_date
        return None
    for session_date in rth_dates:
        if session_date >= entry_date:
            return session_date
    return rth_dates[0]


def _max_hold_session_date(rth_dates: list[date], *, max_hold_days: int) -> date | None:
    if not rth_dates:
        return None
    index = min(max_hold_days - 1, len(rth_dates) - 1)
    return rth_dates[index]


def _exit_tranche_a_long(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    stop_points: float,
    target_date: date | None,
) -> tuple[str, float, str]:
    initial_stop = entry_px - stop_points
    last_target_bar = (
        _last_rth_bar_on_date(bars, fill_ts=fill_ts, session_date=target_date)
        if target_date is not None
        else None
    )
    last_target_ts = _bar_ts(last_target_bar) if last_target_bar else None
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        if bar["l"] <= initial_stop:
            return bar["t"], initial_stop, "stop"
        if (
            last_target_bar is not None
            and is_rth(bar["t"])
            and to_ny(bar["t"]).date() == target_date
            and bar_time >= last_target_ts
        ):
            return last_target_bar["t"], float(last_target_bar["c"]), "day1_close"
    if last_target_bar is not None:
        return last_target_bar["t"], float(last_target_bar["c"]), "day1_close"
    last = bars[-1]
    return last["t"], float(last["c"]), "day1_close"


def _exit_tranche_a_short(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    stop_points: float,
    target_date: date | None,
) -> tuple[str, float, str]:
    initial_stop = entry_px + stop_points
    last_target_bar = (
        _last_rth_bar_on_date(bars, fill_ts=fill_ts, session_date=target_date)
        if target_date is not None
        else None
    )
    last_target_ts = _bar_ts(last_target_bar) if last_target_bar else None
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        if bar["h"] >= initial_stop:
            return bar["t"], initial_stop, "stop"
        if (
            last_target_bar is not None
            and is_rth(bar["t"])
            and to_ny(bar["t"]).date() == target_date
            and bar_time >= last_target_ts
        ):
            return last_target_bar["t"], float(last_target_bar["c"]), "day1_close"
    if last_target_bar is not None:
        return last_target_bar["t"], float(last_target_bar["c"]), "day1_close"
    last = bars[-1]
    return last["t"], float(last["c"]), "day1_close"


def _exit_tranche_b_long(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    stop_points: float,
    trail_points: float,
    max_hold_date: date | None,
) -> tuple[str, float, str]:
    initial_stop = entry_px - stop_points
    best = entry_px
    last_hold_bar = (
        _last_rth_bar_on_date(bars, fill_ts=fill_ts, session_date=max_hold_date)
        if max_hold_date is not None
        else None
    )
    last_hold_ts = _bar_ts(last_hold_bar) if last_hold_bar else None
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        best = max(best, float(bar["h"]))
        trail_stop = best - trail_points
        effective_stop = max(initial_stop, trail_stop)
        if bar["l"] <= effective_stop:
            reason = "trail" if effective_stop > initial_stop else "stop"
            return bar["t"], effective_stop, reason
        if (
            last_hold_bar is not None
            and is_rth(bar["t"])
            and to_ny(bar["t"]).date() == max_hold_date
            and bar_time >= last_hold_ts
        ):
            return last_hold_bar["t"], float(last_hold_bar["c"]), "max_hold"
    if last_hold_bar is not None:
        return last_hold_bar["t"], float(last_hold_bar["c"]), "max_hold"
    last = bars[-1]
    return last["t"], float(last["c"]), "max_hold"


def _exit_tranche_b_short(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    stop_points: float,
    trail_points: float,
    max_hold_date: date | None,
) -> tuple[str, float, str]:
    initial_stop = entry_px + stop_points
    best = entry_px
    last_hold_bar = (
        _last_rth_bar_on_date(bars, fill_ts=fill_ts, session_date=max_hold_date)
        if max_hold_date is not None
        else None
    )
    last_hold_ts = _bar_ts(last_hold_bar) if last_hold_bar else None
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        best = min(best, float(bar["l"]))
        trail_stop = best + trail_points
        effective_stop = min(initial_stop, trail_stop)
        if bar["h"] >= effective_stop:
            reason = "trail" if effective_stop < initial_stop else "stop"
            return bar["t"], effective_stop, reason
        if (
            last_hold_bar is not None
            and is_rth(bar["t"])
            and to_ny(bar["t"]).date() == max_hold_date
            and bar_time >= last_hold_ts
        ):
            return last_hold_bar["t"], float(last_hold_bar["c"]), "max_hold"
    if last_hold_bar is not None:
        return last_hold_bar["t"], float(last_hold_bar["c"]), "max_hold"
    last = bars[-1]
    return last["t"], float(last["c"]), "max_hold"


def _scaleout_long(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_ts: str,
    entry_px: float,
    stop_points: float,
    trail_points: float,
    max_hold_days: int,
) -> tuple[str, float, float, str]:
    rth_dates = _rth_session_dates(bars, fill_ts=fill_ts)
    target_a_date = _tranche_a_target_date(entry_ts, rth_dates)
    max_hold_date = _max_hold_session_date(rth_dates, max_hold_days=max_hold_days)
    a_ts, a_px, _a_reason = _exit_tranche_a_long(
        bars,
        fill_ts=fill_ts,
        entry_px=entry_px,
        stop_points=stop_points,
        target_date=target_a_date,
    )
    b_ts, b_px, b_reason = _exit_tranche_b_long(
        bars,
        fill_ts=fill_ts,
        entry_px=entry_px,
        stop_points=stop_points,
        trail_points=trail_points,
        max_hold_date=max_hold_date,
    )
    exit_ts = a_ts if _parse_ts(a_ts) >= _parse_ts(b_ts) else b_ts
    return exit_ts, a_px, b_px, b_reason


def _scaleout_short(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_ts: str,
    entry_px: float,
    stop_points: float,
    trail_points: float,
    max_hold_days: int,
) -> tuple[str, float, float, str]:
    rth_dates = _rth_session_dates(bars, fill_ts=fill_ts)
    target_a_date = _tranche_a_target_date(entry_ts, rth_dates)
    max_hold_date = _max_hold_session_date(rth_dates, max_hold_days=max_hold_days)
    a_ts, a_px, _a_reason = _exit_tranche_a_short(
        bars,
        fill_ts=fill_ts,
        entry_px=entry_px,
        stop_points=stop_points,
        target_date=target_a_date,
    )
    b_ts, b_px, b_reason = _exit_tranche_b_short(
        bars,
        fill_ts=fill_ts,
        entry_px=entry_px,
        stop_points=stop_points,
        trail_points=trail_points,
        max_hold_date=max_hold_date,
    )
    exit_ts = a_ts if _parse_ts(a_ts) >= _parse_ts(b_ts) else b_ts
    return exit_ts, a_px, b_px, b_reason


def _beta_points(
    bars: list[IntradayBar],
    *,
    fill_ts: datetime,
    entry_px: float,
    max_hold_hours: float,
) -> float:
    deadline = fill_ts + timedelta(hours=max_hold_hours)
    started = False
    for bar in bars:
        bar_time = _bar_ts(bar)
        if not started:
            if bar_time <= fill_ts:
                continue
            started = True
        if bar_time >= deadline:
            return bar["c"] - entry_px
    if not started:
        return 0.0
    return bars[-1]["c"] - entry_px


def _evaluate_trigger(
    trigger: dict,
    bars: list[IntradayBar],
    *,
    entry_mode: str,
    exit_style: str,
    fill_window_hours: float,
    target_points: float,
    stop_points: float,
    max_hold_hours: float,
    trail_points: float,
    max_hold_days: int,
    cost_points: float,
) -> tuple[TriggerResult, float | None]:
    post_id = str(trigger["post_id"])
    post_ts = str(trigger["post_ts"])
    direction = str(trigger["direction"]).lower()
    level = float(trigger["level"])
    post_dt = _parse_ts(post_ts)

    if entry_mode == "his_level":
        filled, entry_ts = _find_fill(
            bars,
            post_ts=post_dt,
            level=level,
            fill_window_hours=fill_window_hours,
        )
        entry_px = level if filled else None
    else:
        filled, entry_ts, entry_px = _find_post_price_fill(
            bars,
            post_ts=post_dt,
            fill_window_hours=fill_window_hours,
        )

    if not filled or entry_ts is None or entry_px is None:
        return (
            TriggerResult(
                post_id=post_id,
                post_ts=post_ts,
                direction=direction,
                level=level,
                filled=False,
                entry_ts=None,
                exit_ts=None,
                exit_px=None,
                exit_reason=None,
                points_net=None,
                slippage=None,
            ),
            None,
        )

    fill_dt = _parse_ts(entry_ts)
    slippage = _compute_slippage(direction=direction, entry_px=entry_px, level=level)
    tranche_a_px: float | None = None
    tranche_b_px: float | None = None
    tranche_b_reason: str | None = None

    if exit_style == "scaleout":
        if direction == "long":
            exit_ts, tranche_a_px, tranche_b_px, tranche_b_reason = _scaleout_long(
                bars,
                fill_ts=fill_dt,
                entry_ts=entry_ts,
                entry_px=entry_px,
                stop_points=stop_points,
                trail_points=trail_points,
                max_hold_days=max_hold_days,
            )
            gross = 0.5 * (tranche_a_px - entry_px) + 0.5 * (tranche_b_px - entry_px)
        else:
            exit_ts, tranche_a_px, tranche_b_px, tranche_b_reason = _scaleout_short(
                bars,
                fill_ts=fill_dt,
                entry_ts=entry_ts,
                entry_px=entry_px,
                stop_points=stop_points,
                trail_points=trail_points,
                max_hold_days=max_hold_days,
            )
            gross = 0.5 * (entry_px - tranche_a_px) + 0.5 * (entry_px - tranche_b_px)
        exit_px = None
        exit_reason = "scaleout"
    elif direction == "long":
        exit_ts, exit_px, exit_reason = _exit_long(
            bars,
            fill_ts=fill_dt,
            entry_px=entry_px,
            target_points=target_points,
            stop_points=stop_points,
            max_hold_hours=max_hold_hours,
        )
        gross = exit_px - entry_px
    else:
        exit_ts, exit_px, exit_reason = _exit_short(
            bars,
            fill_ts=fill_dt,
            entry_px=entry_px,
            target_points=target_points,
            stop_points=stop_points,
            max_hold_hours=max_hold_hours,
        )
        gross = entry_px - exit_px

    net = gross - cost_points
    beta = _beta_points(
        bars,
        fill_ts=fill_dt,
        entry_px=entry_px,
        max_hold_hours=max_hold_hours,
    )
    return (
        TriggerResult(
            post_id=post_id,
            post_ts=post_ts,
            direction=direction,
            level=level,
            filled=True,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            exit_px=exit_px,
            exit_reason=exit_reason,
            points_net=net,
            slippage=slippage,
            tranche_a_px=tranche_a_px,
            tranche_b_px=tranche_b_px,
            tranche_b_reason=tranche_b_reason,
        ),
        beta,
    )


def run_trigger_study(
    triggers: list[dict],
    bars: list[IntradayBar],
    *,
    entry_mode: str = "post_price",
    exit_style: str = "fixed",
    fill_window_hours: float = 8,
    target_points: float = 20,
    stop_points: float = 10,
    max_hold_hours: float = 48,
    trail_points: float = 30.0,
    max_hold_days: int = 3,
    cost_points: float = 1.0,
) -> StudyResult:
    """Run fill-after-post event study; pivots should already be filtered out."""
    if entry_mode not in {"post_price", "his_level"}:
        raise ValueError(f"entry_mode must be 'post_price' or 'his_level', got {entry_mode!r}")
    if exit_style not in {"fixed", "scaleout"}:
        raise ValueError(f"exit_style must be 'fixed' or 'scaleout', got {exit_style!r}")

    sorted_bars = sorted(bars, key=lambda bar: bar["t"])
    results: list[TriggerResult] = []
    beta_values: list[float] = []
    by_dir: dict[str, list[float]] = {"long": [], "short": []}

    for trigger in triggers:
        direction = str(trigger.get("direction", "")).lower()
        if direction not in {"long", "short"}:
            continue
        result, beta = _evaluate_trigger(
            trigger,
            sorted_bars,
            entry_mode=entry_mode,
            exit_style=exit_style,
            fill_window_hours=fill_window_hours,
            target_points=target_points,
            stop_points=stop_points,
            max_hold_hours=max_hold_hours,
            trail_points=trail_points,
            max_hold_days=max_hold_days,
            cost_points=cost_points,
        )
        results.append(result)
        if result.filled and result.points_net is not None:
            by_dir[direction].append(result.points_net)
            if beta is not None:
                beta_values.append(beta)

    n_triggers = len(results)
    n_filled = sum(1 for result in results if result.filled)
    fill_rate = n_filled / n_triggers if n_triggers else 0.0

    filled_nets = [row.points_net for row in results if row.filled and row.points_net is not None]
    n_wins = sum(1 for net in filled_nets if net > 0)
    hit_rate = n_wins / n_filled if n_filled else 0.0
    total_points_net = sum(filled_nets)
    avg_points_net = total_points_net / n_filled if n_filled else 0.0
    beta_avg_points = sum(beta_values) / len(beta_values) if beta_values else 0.0

    filled_slippages = [
        row.slippage
        for row in results
        if row.filled and row.slippage is not None
    ]
    avg_slippage_points = (
        sum(filled_slippages) / len(filled_slippages) if filled_slippages else 0.0
    )
    median_slippage_points = (
        statistics.median(filled_slippages) if filled_slippages else 0.0
    )

    by_direction: dict[str, dict] = {}
    for direction, nets in by_dir.items():
        count = len(nets)
        wins = sum(1 for net in nets if net > 0)
        total = sum(nets)
        by_direction[direction] = {
            "n_filled": count,
            "hit_rate": wins / count if count else 0.0,
            "avg_points_net": total / count if count else 0.0,
            "total": total,
        }

    return StudyResult(
        results=results,
        n_triggers=n_triggers,
        n_filled=n_filled,
        fill_rate=fill_rate,
        hit_rate=hit_rate,
        avg_points_net=avg_points_net,
        total_points_net=total_points_net,
        by_direction=by_direction,
        beta_avg_points=beta_avg_points,
        avg_slippage_points=avg_slippage_points,
        median_slippage_points=median_slippage_points,
    )


def _write_csv(results: list[TriggerResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "post_id",
                "post_ts",
                "date",
                "direction",
                "level",
                "filled",
                "entry_ts",
                "exit_ts",
                "exit_px",
                "exit_reason",
                "points_net",
                "slippage",
                "tranche_a_px",
                "tranche_b_px",
                "tranche_b_reason",
            ],
        )
        writer.writeheader()
        for row in results:
            writer.writerow(
                {
                    "post_id": row.post_id,
                    "post_ts": row.post_ts,
                    "date": row.post_ts[:10],
                    "direction": row.direction,
                    "level": row.level,
                    "filled": row.filled,
                    "entry_ts": row.entry_ts or "",
                    "exit_ts": row.exit_ts or "",
                    "exit_px": row.exit_px if row.exit_px is not None else "",
                    "exit_reason": row.exit_reason or "",
                    "points_net": row.points_net if row.points_net is not None else "",
                    "slippage": row.slippage if row.slippage is not None else "",
                    "tranche_a_px": row.tranche_a_px if row.tranche_a_px is not None else "",
                    "tranche_b_px": row.tranche_b_px if row.tranche_b_px is not None else "",
                    "tranche_b_reason": row.tranche_b_reason or "",
                }
            )


def _entry_px_from_result(row: TriggerResult) -> float | None:
    if not row.filled or row.slippage is None:
        return None
    if row.direction == "long":
        return row.level + row.slippage
    return row.level - row.slippage


def _print_report(
    study: StudyResult,
    *,
    entry_mode: str = "post_price",
    exit_style: str = "fixed",
) -> None:
    print("DESCRIPTIVE (n≈135), not a significance test.")
    print(f"entry_mode={entry_mode} exit_style={exit_style}")
    print(
        f"n_triggers={study.n_triggers} n_filled={study.n_filled} "
        f"fill_rate={study.fill_rate:.1%}"
    )
    print(
        f"hit_rate={study.hit_rate:.1%} avg_points_net={study.avg_points_net:.2f} "
        f"total_points_net={study.total_points_net:.2f}"
    )
    print(f"beta_avg_points={study.beta_avg_points:.2f}")
    if exit_style == "scaleout":
        a_contribs: list[float] = []
        b_contribs: list[float] = []
        for row in study.results:
            if not row.filled or row.tranche_a_px is None or row.tranche_b_px is None:
                continue
            entry_px = _entry_px_from_result(row)
            if entry_px is None:
                continue
            if row.direction == "long":
                a_contribs.append(0.5 * (row.tranche_a_px - entry_px))
                b_contribs.append(0.5 * (row.tranche_b_px - entry_px))
            else:
                a_contribs.append(0.5 * (entry_px - row.tranche_a_px))
                b_contribs.append(0.5 * (entry_px - row.tranche_b_px))
        if a_contribs:
            avg_a = sum(a_contribs) / len(a_contribs)
            avg_b = sum(b_contribs) / len(b_contribs)
            print(
                f"scaleout_avg_contribution: tranche_a={avg_a:.2f} "
                f"tranche_b={avg_b:.2f}"
            )
    if entry_mode == "post_price":
        print(
            f"avg_slippage_points={study.avg_slippage_points:.2f} "
            f"median_slippage_points={study.median_slippage_points:.2f}"
        )
        filled_slippages = [
            row.slippage
            for row in study.results
            if row.filled and row.slippage is not None
        ]
        if filled_slippages:
            n_worse = sum(1 for s in filled_slippages if s > 0)
            n_better = sum(1 for s in filled_slippages if s < 0)
            n_same = sum(1 for s in filled_slippages if s == 0)
            n = len(filled_slippages)
            print(
                f"slippage_vs_his_level: worse={n_worse / n:.1%} "
                f"better={n_better / n:.1%} same={n_same / n:.1%}"
            )
    print("\nby_direction:")
    for direction in ("long", "short"):
        stats = study.by_direction.get(direction, {})
        print(
            f"  {direction}: n_filled={stats.get('n_filled', 0)} "
            f"hit_rate={stats.get('hit_rate', 0.0):.1%} "
            f"avg_points_net={stats.get('avg_points_net', 0.0):.2f} "
            f"total={stats.get('total', 0.0):.2f}"
        )
    print("\nper_trigger:")
    print(f"{'date':<12} {'dir':<6} {'level':>8} {'filled':<7} {'reason':<8} {'net':>8}")
    for row in study.results:
        reason = row.exit_reason or "-"
        net = f"{row.points_net:.2f}" if row.points_net is not None else "-"
        print(
            f"{row.post_ts[:10]:<12} {row.direction:<6} {row.level:>8.1f} "
            f"{str(row.filled):<7} {reason:<8} {net:>8}"
        )


def _print_both_comparison(study_post: StudyResult, study_his: StudyResult) -> None:
    print("DESCRIPTIVE (n≈135), not a significance test.")
    print("Side-by-side: post_price (follower) vs his_level (idealized limit at named level)\n")

    def _summary(label: str, study: StudyResult) -> None:
        print(f"=== {label} ===")
        print(
            f"n_triggers={study.n_triggers} n_filled={study.n_filled} "
            f"fill_rate={study.fill_rate:.1%}"
        )
        print(
            f"hit_rate={study.hit_rate:.1%} avg_points_net={study.avg_points_net:.2f} "
            f"total_points_net={study.total_points_net:.2f}"
        )
        print(f"beta_avg_points={study.beta_avg_points:.2f}")
        for direction in ("long", "short"):
            stats = study.by_direction.get(direction, {})
            print(
                f"  {direction}: n_filled={stats.get('n_filled', 0)} "
                f"hit_rate={stats.get('hit_rate', 0.0):.1%} "
                f"avg_points_net={stats.get('avg_points_net', 0.0):.2f} "
                f"total={stats.get('total', 0.0):.2f}"
            )
        print()

    _summary("post_price", study_post)
    _summary("his_level", study_his)

    filled_slippages = [
        row.slippage
        for row in study_post.results
        if row.filled and row.slippage is not None
    ]
    print("=== post_price slippage vs his level ===")
    print(
        f"avg_slippage_points={study_post.avg_slippage_points:.2f} "
        f"median_slippage_points={study_post.median_slippage_points:.2f}"
    )
    if filled_slippages:
        n_worse = sum(1 for s in filled_slippages if s > 0)
        n_better = sum(1 for s in filled_slippages if s < 0)
        n_same = sum(1 for s in filled_slippages if s == 0)
        n = len(filled_slippages)
        print(
            f"entered worse than his level: {n_worse / n:.1%} "
            f"better: {n_better / n:.1%} same: {n_same / n:.1%}"
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adam level-call event study on 1-min /ES (fill-after-post)."
    )
    parser.add_argument(
        "--triggers-path",
        type=Path,
        default=DEFAULT_TRIGGERS_PATH,
        help="Path to es_triggers_full.json",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help="CSV output path",
    )
    parser.add_argument("--fill-window-hours", type=float, default=8.0)
    parser.add_argument("--target-points", type=float, default=20.0)
    parser.add_argument("--stop-points", type=float, default=10.0)
    parser.add_argument("--max-hold-hours", type=float, default=48.0)
    parser.add_argument("--trail-points", type=float, default=30.0)
    parser.add_argument("--max-hold-days", type=int, default=3)
    parser.add_argument("--cost-points", type=float, default=1.0)
    parser.add_argument(
        "--exit-style",
        choices=("fixed", "scaleout"),
        default="fixed",
        help="Exit model: fixed (single target/stop) or scaleout (half day-1 + trailed runner)",
    )
    parser.add_argument(
        "--entry-mode",
        choices=("post_price", "his_level"),
        default="post_price",
        help="Entry model: post_price (next bar open after tweet) or his_level (limit at named level)",
    )
    parser.add_argument(
        "--both",
        action="store_true",
        help="Run both entry modes and print side-by-side comparison",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_INTRADAY_DB_PATH,
        help="intraday_bars.db path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    triggers = load_triggers(args.triggers_path)
    if not triggers:
        print("No long/short triggers found.", file=sys.stderr)
        return 1

    post_times = [_parse_ts(row["post_ts"]) for row in triggers]
    start = min(post_times).replace(hour=0, minute=0, second=0, microsecond=0)
    end = max(post_times) + timedelta(hours=args.max_hold_hours)
    bars = load_cached_minute_bars(
        "/ES",
        start,
        end,
        source="databento",
        db_path=args.db_path,
    )
    if not bars:
        print("No cached /ES minute bars (source=databento).", file=sys.stderr)
        return 1

    study_kwargs = {
        "fill_window_hours": args.fill_window_hours,
        "target_points": args.target_points,
        "stop_points": args.stop_points,
        "max_hold_hours": args.max_hold_hours,
        "trail_points": args.trail_points,
        "max_hold_days": args.max_hold_days,
        "cost_points": args.cost_points,
        "exit_style": args.exit_style,
    }

    if args.both:
        study_post = run_trigger_study(triggers, bars, entry_mode="post_price", **study_kwargs)
        study_his = run_trigger_study(triggers, bars, entry_mode="his_level", **study_kwargs)
        _print_both_comparison(study_post, study_his)
        _write_csv(study_post.results, args.output_csv)
        print(f"\nWrote {args.output_csv} (post_price mode)")
        return 0

    study = run_trigger_study(triggers, bars, entry_mode=args.entry_mode, **study_kwargs)
    _print_report(study, entry_mode=args.entry_mode, exit_style=args.exit_style)
    _write_csv(study.results, args.output_csv)
    print(f"\nWrote {args.output_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
