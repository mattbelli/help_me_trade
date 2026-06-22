"""Intraday profile-extreme mean-reversion backtest on 1-minute bars."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR
from research.intraday_bars import IntradayBar, load_cached_minute_bars
from research.intraday_profile import prior_session_reference, segment_rth_sessions

_SAMPLE_SIZE_CAVEAT = (
    "Sample-size reality: ~26 RTH sessions => at most ~26 trades. "
    "WAY too small for statistical significance. "
    "This run is a pipeline sanity-check + first look, NOT edge validation."
)


@dataclass(frozen=True)
class ReversionTrade:
    date: date
    ref_date: date
    entry_ts: str
    entry_px: float
    exit_ts: str
    exit_px: float
    exit_reason: str
    points: float
    gross_return_pct: float


@dataclass(frozen=True)
class ReversionResult:
    trades: list[ReversionTrade]
    n_trades: int
    win_rate: float
    avg_points_gross: float
    avg_points_net: float
    total_points_net: float
    by_exit_reason: dict[str, int]


def _simulate_session_exit(
    session_bars: list[IntradayBar],
    entry_idx: int,
    *,
    stop: float,
    target: float,
) -> tuple[float, str, str]:
    """Walk bars from entry_idx; return (exit_px, exit_ts, exit_reason)."""
    for bar in session_bars[entry_idx:]:
        low = float(bar["l"])
        high = float(bar["h"])
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            return stop, bar["t"], "stop"
        if hit_stop:
            return stop, bar["t"], "stop"
        if hit_target:
            return target, bar["t"], "target"

    last = session_bars[-1]
    return float(last["c"]), last["t"], "time"


def run_reversion_backtest(
    bars: list[IntradayBar],
    *,
    stop_points: float = 10.0,
    cost_points: float = 1.0,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
) -> ReversionResult:
    """Long-only mean reversion from prior-session VAL to prior-session POC."""
    sessions = segment_rth_sessions(bars)
    trades: list[ReversionTrade] = []

    for session_date in sorted(sessions):
        ref = prior_session_reference(
            sessions,
            session_date,
            n_bins=n_bins,
            value_area_pct=value_area_pct,
        )
        if ref is None:
            continue

        val = ref.get("val")
        poc = ref.get("poc_price")
        if val is None or poc is None:
            continue

        entry_level = float(val)
        target = float(poc)
        if target <= entry_level:
            continue

        day_bars = sessions[session_date]
        entry_idx: int | None = None
        for idx, bar in enumerate(day_bars):
            if float(bar["l"]) <= entry_level:
                entry_idx = idx
                break
        if entry_idx is None:
            continue

        stop = entry_level - stop_points
        entry_px = entry_level
        entry_ts = day_bars[entry_idx]["t"]
        exit_px, exit_ts, exit_reason = _simulate_session_exit(
            day_bars,
            entry_idx,
            stop=stop,
            target=target,
        )
        points = exit_px - entry_px
        gross_return_pct = points / entry_px * 100.0 if entry_px != 0 else 0.0

        trades.append(
            ReversionTrade(
                date=session_date,
                ref_date=ref["ref_date"],
                entry_ts=entry_ts,
                entry_px=entry_px,
                exit_ts=exit_ts,
                exit_px=exit_px,
                exit_reason=exit_reason,
                points=points,
                gross_return_pct=gross_return_pct,
            )
        )

    n_trades = len(trades)
    net_points = [trade.points - cost_points for trade in trades]
    win_rate = (
        sum(1 for pts in net_points if pts > 0) / n_trades if n_trades else 0.0
    )
    avg_points_gross = sum(trade.points for trade in trades) / n_trades if n_trades else 0.0
    avg_points_net = sum(net_points) / n_trades if n_trades else 0.0
    total_points_net = sum(net_points)
    by_exit_reason = dict(Counter(trade.exit_reason for trade in trades))

    return ReversionResult(
        trades=trades,
        n_trades=n_trades,
        win_rate=win_rate,
        avg_points_gross=avg_points_gross,
        avg_points_net=avg_points_net,
        total_points_net=total_points_net,
        by_exit_reason=by_exit_reason,
    )


def _sanitize_symbol_filename(symbol: str) -> str:
    return symbol.strip().lstrip("/")


def _trade_csv_path(symbol: str) -> Path:
    name = _sanitize_symbol_filename(symbol)
    return RESEARCH_DATA_DIR / f"intraday_reversion_{name}.csv"


def _write_trades_csv(
    trades: list[ReversionTrade],
    path: Path,
    *,
    cost_points: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "ref_date",
        "entry_ts",
        "entry_px",
        "exit_ts",
        "exit_px",
        "exit_reason",
        "points",
        "gross_return_pct",
        "net_points",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow(
                {
                    "date": trade.date.isoformat(),
                    "ref_date": trade.ref_date.isoformat(),
                    "entry_ts": trade.entry_ts,
                    "entry_px": trade.entry_px,
                    "exit_ts": trade.exit_ts,
                    "exit_px": trade.exit_px,
                    "exit_reason": trade.exit_reason,
                    "points": trade.points,
                    "gross_return_pct": trade.gross_return_pct,
                    "net_points": trade.points - cost_points,
                }
            )


def _print_report(result: ReversionResult, *, cost_points: float) -> None:
    print(_SAMPLE_SIZE_CAVEAT)
    print(f"n_trades={result.n_trades}")
    print()
    print(f"win_rate (net): {result.win_rate:.1%}")
    print(f"avg_points_gross: {result.avg_points_gross:.2f}")
    print(f"avg_points_net: {result.avg_points_net:.2f}")
    print(f"total_points_net: {result.total_points_net:.2f}")
    print(f"EV/trade (net points): {result.avg_points_net:.2f}")
    print()
    print("exit_reason breakdown:")
    for reason in ("target", "stop", "time"):
        count = result.by_exit_reason.get(reason, 0)
        print(f"  {reason}: {count}")
    print()
    print("trades (date, entry, exit, reason, net pts):")
    for trade in result.trades:
        net = trade.points - cost_points
        print(
            f"  {trade.date}  entry={trade.entry_px:.2f}  exit={trade.exit_px:.2f}  "
            f"{trade.exit_reason}  net={net:.2f}"
        )


def _parse_cli_date(text: str, *, end_of_day: bool = False) -> datetime:
    day = datetime.strptime(text, "%Y-%m-%d").date()
    if end_of_day:
        return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile-extreme mean-reversion backtest on cached 1-min bars."
    )
    parser.add_argument("--symbol", required=True, help="Symbol (e.g. /ES)")
    parser.add_argument(
        "--start", required=True, help="Start date YYYY-MM-DD (00:00 UTC)"
    )
    parser.add_argument(
        "--end", required=True, help="End date YYYY-MM-DD (23:59:59 UTC)"
    )
    parser.add_argument(
        "--stop-points",
        type=float,
        default=10.0,
        help="Stop distance below entry in ES points (default: 10)",
    )
    parser.add_argument(
        "--cost-points",
        type=float,
        default=1.0,
        help="Round-trip cost in ES points (default: 1)",
    )
    parser.add_argument("--source", default="schwab", help="Bar source (default: schwab)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start = _parse_cli_date(args.start)
    end = _parse_cli_date(args.end, end_of_day=True)
    bars = load_cached_minute_bars(args.symbol, start, end, source=args.source)
    result = run_reversion_backtest(
        bars,
        stop_points=args.stop_points,
        cost_points=args.cost_points,
    )
    _print_report(result, cost_points=args.cost_points)
    csv_path = _trade_csv_path(args.symbol)
    _write_trades_csv(result.trades, csv_path, cost_points=args.cost_points)
    print()
    print(f"wrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
