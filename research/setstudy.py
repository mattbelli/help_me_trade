"""Daily @adaamset level-call review — observational, no look-ahead, offline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR
from research.intraday_bars import (
    DEFAULT_INTRADAY_DB_PATH,
    IntradayBar,
    get_minute_bars,
    load_cached_minute_bars,
)
from research.intraday_profile import segment_rth_sessions, session_profile, to_ny
from research.volume_profile import _near_node

DEFAULT_TRIGGERS_PATH = RESEARCH_DATA_DIR / "adaamset" / "es_triggers_full.json"
DEFAULT_LOG_CSV = RESEARCH_DATA_DIR / "setstudy_log.csv"

_NODE_TOL_PCT = 0.25
_REFRESH_DAYS = 3
_MAX_REFRESH_COST_USD = 0.50

_CSV_FIELDS = [
    "post_id",
    "date",
    "post_ts",
    "direction",
    "level",
    "entry_type",
    "post_price",
    "touched_level",
    "mfe_points",
    "mae_points",
    "direction_worked",
    "near_node",
]


def _parse_ts(ts: str) -> datetime:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _bar_ts(bar: IntradayBar) -> datetime:
    return _parse_ts(bar["t"])


def load_triggers(path: Path = DEFAULT_TRIGGERS_PATH) -> list[dict]:
    """Load trigger JSON array (all rows; direction filter happens in report)."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def latest_trigger_ny_date(triggers: list[dict]) -> date | None:
    """Return the latest NY session date present in ``triggers``."""
    dates: list[date] = []
    for row in triggers:
        post_ts = row.get("post_ts")
        if not post_ts:
            continue
        dates.append(to_ny(str(post_ts)).date())
    return max(dates) if dates else None


def _horizon_bars(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    horizon_hours: float,
) -> list[IntradayBar]:
    deadline = post_ts + timedelta(hours=horizon_hours)
    return [
        bar
        for bar in sorted(bars, key=lambda item: item["t"])
        if _bar_ts(bar) >= post_ts and _bar_ts(bar) <= deadline
    ]


def _post_price(bars: list[IntradayBar], *, post_ts: datetime) -> float | None:
    for bar in sorted(bars, key=lambda item: item["t"]):
        if _bar_ts(bar) > post_ts:
            return float(bar["o"])
    return None


def _touched_level(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    level: float,
    horizon_hours: float,
) -> tuple[bool, str | None]:
    for bar in _horizon_bars(bars, post_ts=post_ts, horizon_hours=horizon_hours):
        if bar["l"] <= level <= bar["h"]:
            return True, bar["t"]
    return False, None


def _mfe_mae(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    post_price: float,
    direction: str,
    horizon_hours: float,
) -> tuple[float, float]:
    mfe = 0.0
    mae = 0.0
    for bar in _horizon_bars(bars, post_ts=post_ts, horizon_hours=horizon_hours):
        if _bar_ts(bar) <= post_ts:
            continue
        if direction == "long":
            mfe = max(mfe, float(bar["h"]) - post_price)
            mae = max(mae, post_price - float(bar["l"]))
        else:
            mfe = max(mfe, post_price - float(bar["l"]))
            mae = max(mae, float(bar["h"]) - post_price)
    return mfe, mae


def _bar_threshold_hit(
    bar: IntradayBar,
    *,
    post_price: float,
    threshold: float,
    direction: str,
) -> bool | None:
    """Return True/False if a threshold is hit in this bar, else None."""
    if direction == "long":
        favorable = post_price + threshold
        adverse = post_price - threshold
        hit_fav = float(bar["h"]) >= favorable
        hit_adv = float(bar["l"]) <= adverse
    else:
        favorable = post_price - threshold
        adverse = post_price + threshold
        hit_fav = float(bar["l"]) <= favorable
        hit_adv = float(bar["h"]) >= adverse

    if not hit_fav and not hit_adv:
        return None
    if hit_fav and not hit_adv:
        return True
    if hit_adv and not hit_fav:
        return False

    open_px = float(bar["o"])
    if direction == "long":
        if open_px >= post_price:
            return False if float(bar["l"]) <= adverse else True
        return True if float(bar["h"]) >= favorable else False

    if open_px <= post_price:
        return False if float(bar["h"]) >= adverse else True
    return True if float(bar["l"]) <= favorable else False


def _direction_worked(
    bars: list[IntradayBar],
    *,
    post_ts: datetime,
    post_price: float,
    direction: str,
    horizon_hours: float,
    move_threshold_points: float,
) -> bool | None:
    for bar in _horizon_bars(bars, post_ts=post_ts, horizon_hours=horizon_hours):
        if _bar_ts(bar) <= post_ts:
            continue
        result = _bar_threshold_hit(
            bar,
            post_price=post_price,
            threshold=move_threshold_points,
            direction=direction,
        )
        if result is not None:
            return result
    return None


def analyze_call(
    call: dict,
    bars: list[IntradayBar],
    *,
    horizon_hours: float = 24,
    move_threshold_points: float = 20,
) -> dict:
    """Analyze one level call using only bars at/after ``post_ts`` (no look-ahead)."""
    post_ts = _parse_ts(str(call["post_ts"]))
    direction = str(call.get("direction", "")).lower()
    level = float(call["level"])
    entry_type = call.get("entry_type")

    touched, touch_ts = _touched_level(
        bars, post_ts=post_ts, level=level, horizon_hours=horizon_hours
    )
    post_price = _post_price(bars, post_ts=post_ts)

    mfe_points: float | None = None
    mae_points: float | None = None
    direction_worked: bool | None = None
    if post_price is not None:
        mfe_points, mae_points = _mfe_mae(
            bars,
            post_ts=post_ts,
            post_price=post_price,
            direction=direction,
            horizon_hours=horizon_hours,
        )
        direction_worked = _direction_worked(
            bars,
            post_ts=post_ts,
            post_price=post_price,
            direction=direction,
            horizon_hours=horizon_hours,
            move_threshold_points=move_threshold_points,
        )

    return {
        "post_id": str(call.get("post_id", "")),
        "post_ts": call["post_ts"],
        "level": level,
        "direction": direction,
        "entry_type": entry_type,
        "post_price": post_price,
        "touched_level": touched,
        "touch_ts": touch_ts,
        "mfe_points": mfe_points,
        "mae_points": mae_points,
        "direction_worked": direction_worked,
    }


def _level_near_session_node(level: float, session: dict, *, tol_pct: float) -> bool:
    nodes = [session.get("poc"), session.get("vah"), session.get("val")]
    valid = [float(node) for node in nodes if node is not None]
    return _near_node(level, valid, tol_pct)


def _session_summary(profile: dict) -> dict:
    return {
        "poc": profile.get("poc_price"),
        "vah": profile.get("vah"),
        "val": profile.get("val"),
        "high": profile.get("session_high"),
        "low": profile.get("session_low"),
    }


def _calls_for_ny_date(triggers: list[dict], report_date: date) -> list[dict]:
    rows: list[dict] = []
    for row in triggers:
        direction = str(row.get("direction", "")).lower()
        if direction not in {"long", "short"}:
            continue
        if to_ny(str(row["post_ts"])).date() != report_date:
            continue
        rows.append(row)
    return rows


def build_setstudy_report(
    report_date: date,
    triggers: list[dict],
    bars: list[IntradayBar],
    *,
    horizon_hours: float = 24,
    move_threshold_points: float = 20,
    node_tol_pct: float = _NODE_TOL_PCT,
) -> dict:
    """Build a daily report for directional calls on ``report_date``."""
    day_calls = _calls_for_ny_date(triggers, report_date)
    sessions = segment_rth_sessions(bars)
    profile = session_profile(sessions.get(report_date, []))
    session = _session_summary(profile)

    analyzed: list[dict] = []
    for call in day_calls:
        result = analyze_call(
            call,
            bars,
            horizon_hours=horizon_hours,
            move_threshold_points=move_threshold_points,
        )
        result["near_node"] = _level_near_session_node(
            float(call["level"]), session, tol_pct=node_tol_pct
        )
        analyzed.append(result)

    worked = [row for row in analyzed if row.get("direction_worked") is True]
    mfes = [row["mfe_points"] for row in analyzed if row.get("mfe_points") is not None]
    maes = [row["mae_points"] for row in analyzed if row.get("mae_points") is not None]

    return {
        "date": report_date.isoformat(),
        "n_calls": len(analyzed),
        "session": session,
        "calls": analyzed,
        "summary": {
            "n_direction_worked": len(worked),
            "long_worked": sum(
                1
                for row in analyzed
                if row.get("direction") == "long" and row.get("direction_worked") is True
            ),
            "short_worked": sum(
                1
                for row in analyzed
                if row.get("direction") == "short" and row.get("direction_worked") is True
            ),
            "avg_mfe": sum(mfes) / len(mfes) if mfes else None,
            "avg_mae": sum(maes) / len(maes) if maes else None,
        },
    }


def _existing_post_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["post_id"] for row in reader if row.get("post_id")}


def append_log_rows(report: dict, path: Path = DEFAULT_LOG_CSV) -> int:
    """Append per-call rows to the corpus CSV, deduping by ``post_id``."""
    existing = _existing_post_ids(path)
    rows_to_write: list[dict] = []
    report_date = report["date"]
    for call in report["calls"]:
        post_id = str(call.get("post_id", ""))
        if not post_id or post_id in existing:
            continue
        rows_to_write.append(
            {
                "post_id": post_id,
                "date": report_date,
                "post_ts": call["post_ts"],
                "direction": call["direction"],
                "level": call["level"],
                "entry_type": call.get("entry_type") or "",
                "post_price": call.get("post_price"),
                "touched_level": call.get("touched_level"),
                "mfe_points": call.get("mfe_points"),
                "mae_points": call.get("mae_points"),
                "direction_worked": call.get("direction_worked"),
                "near_node": call.get("near_node"),
            }
        )

    if not rows_to_write:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows_to_write)
    return len(rows_to_write)


def _format_direction_worked(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "—"


def print_report(report: dict) -> None:
    """Print a clean daily setstudy report to stdout."""
    session = report["session"]
    print(f"=== setstudy {report['date']} ===")
    print(
        "Session: "
        f"POC={session.get('poc')} VAH={session.get('vah')} VAL={session.get('val')} "
        f"H={session.get('high')} L={session.get('low')}"
    )
    print(f"Calls: {report['n_calls']}\n")

    for call in report["calls"]:
        ny_time = to_ny(call["post_ts"]).strftime("%H:%M")
        touched = "yes" if call.get("touched_level") else "no"
        mfe = call.get("mfe_points")
        mae = call.get("mae_points")
        mfe_text = f"{mfe:.1f}" if mfe is not None else "—"
        mae_text = f"{mae:.1f}" if mae is not None else "—"
        near = "yes" if call.get("near_node") else "no"
        entry_type = call.get("entry_type") or "—"
        print(
            f"  {ny_time} {call['direction']:5s} @ {call['level']:.1f} "
            f"({entry_type}) touched={touched} MFE={mfe_text} MAE={mae_text} "
            f"worked={_format_direction_worked(call.get('direction_worked'))} "
            f"near_node={near}"
        )

    summary = report["summary"]
    avg_mfe = summary.get("avg_mfe")
    avg_mae = summary.get("avg_mae")
    avg_mfe_text = f"{avg_mfe:.1f}" if avg_mfe is not None else "—"
    avg_mae_text = f"{avg_mae:.1f}" if avg_mae is not None else "—"
    print(
        f"\nSummary: worked={summary['n_direction_worked']}/{report['n_calls']} "
        f"(long={summary['long_worked']} short={summary['short_worked']}) "
        f"avg_MFE={avg_mfe_text} avg_MAE={avg_mae_text}"
    )


def _bar_window_for_date(report_date: date) -> tuple[datetime, datetime]:
    start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=UTC)
    end_day = report_date + timedelta(days=2)
    end = datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59, tzinfo=UTC)
    return start, end


def refresh_recent_bars(
    *,
    source: str = "databento",
    days: int = _REFRESH_DAYS,
    db_path: Path = DEFAULT_INTRADAY_DB_PATH,
    max_cost_usd: float = _MAX_REFRESH_COST_USD,
) -> None:
    """Top up recent /ES minute bars. Requires explicit opt-in; prints cost first."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    start_s = start.date().isoformat()
    end_s = end.date().isoformat()

    if source == "databento":
        from research.databento_loader import estimate_cost, pull_ohlcv_1m

        cost, size = estimate_cost(start=start_s, end=end_s)
        print(f"refresh estimate: {start_s}..{end_s} cost=${cost:.4f} ({size / 1e6:.2f} MB)")
        if cost > max_cost_usd:
            raise RuntimeError(
                f"Refresh cost ${cost:.4f} exceeds limit ${max_cost_usd:.2f}; aborting."
            )
        n = pull_ohlcv_1m(start=start_s, end=end_s, db_path=db_path)
        print(f"refresh cached {n} databento bars")
        return

    bars = get_minute_bars("/ES", start, end, source=source, db_path=db_path)
    print(f"refresh cached {len(bars)} {source} bars")


def refresh_tweets_and_extract(
    *,
    username: str = "Adaamset",
    max_tweets: int = 200,
    data_dir: Path | None = None,
    triggers_path: Path = DEFAULT_TRIGGERS_PATH,
) -> list[dict]:
    """Pull latest tweets (merge), extract triggers, write ``triggers_path``."""
    from research.es_trigger_extract import extract_es_triggers, write_triggers
    from research.x_pull import fetch_user_tweets, save_tweets

    base = data_dir or RESEARCH_DATA_DIR
    user_dir = base / username.lower()
    fetched, media_map = fetch_user_tweets(username, max_tweets)
    merged = save_tweets(user_dir, fetched, media_map)
    triggers = extract_es_triggers(merged)
    write_triggers(triggers, triggers_path)
    return triggers


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daily @adaamset level-call review vs /ES price action."
    )
    parser.add_argument(
        "--date",
        default=None,
        help="NY session date YYYY-MM-DD (default: latest in triggers file)",
    )
    parser.add_argument(
        "--refresh-tweets",
        action="store_true",
        help="Pull latest tweets (merge), re-extract triggers, then run report",
    )
    parser.add_argument(
        "--max-tweets",
        type=int,
        default=200,
        help="Max tweets to fetch when --refresh-tweets (default: 200)",
    )
    parser.add_argument(
        "--username",
        default="Adaamset",
        help="X username for --refresh-tweets (default: Adaamset)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Top up recent minute bars first (optional; may spend API credits)",
    )
    parser.add_argument(
        "--source",
        default="databento",
        help="Minute bar source in cache (default: databento)",
    )
    parser.add_argument(
        "--triggers-path",
        type=Path,
        default=DEFAULT_TRIGGERS_PATH,
        help="Path to es_triggers_full.json",
    )
    parser.add_argument(
        "--log-csv",
        type=Path,
        default=DEFAULT_LOG_CSV,
        help="Growing corpus CSV path",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_INTRADAY_DB_PATH,
        help="intraday_bars.db path",
    )
    parser.add_argument("--horizon-hours", type=float, default=24.0)
    parser.add_argument("--move-threshold-points", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.refresh_tweets:
        triggers = refresh_tweets_and_extract(
            username=args.username,
            max_tweets=args.max_tweets,
            triggers_path=args.triggers_path,
        )
    else:
        triggers = load_triggers(args.triggers_path)
    if not triggers:
        print("No triggers found.", file=sys.stderr)
        return 1

    if args.date:
        report_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        report_date = latest_trigger_ny_date(triggers)
        if report_date is None:
            print("Could not infer latest NY date from triggers.", file=sys.stderr)
            return 1

    if args.refresh:
        try:
            refresh_recent_bars(
                source=args.source,
                db_path=args.db_path,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    start, end = _bar_window_for_date(report_date)
    bars = load_cached_minute_bars(
        "/ES",
        start,
        end,
        source=args.source,
        db_path=args.db_path,
    )
    if not bars:
        print(
            f"No cached /ES minute bars for {report_date} (source={args.source}).",
            file=sys.stderr,
        )
        return 1

    report = build_setstudy_report(
        report_date,
        triggers,
        bars,
        horizon_hours=args.horizon_hours,
        move_threshold_points=args.move_threshold_points,
    )
    print_report(report)
    appended = append_log_rows(report, args.log_csv)
    if appended:
        print(f"\nAppended {appended} row(s) to {args.log_csv}")
    else:
        print(f"\nNo new rows for {args.log_csv} (already logged or no calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
