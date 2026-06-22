"""Profile mean-reversion swing backtest on daily bars (Setup A: pivot-acceptance continuation)."""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from research.bars import RESEARCH_DATA_DIR, get_daily_bars
from research.intraday_bars import load_cached_minute_bars
from research.volume_profile import build_volume_profile
from signals.indicators import closes, sma
from signals.trade_context import DatedBar


@dataclass(frozen=True)
class SwingTrade:
    entry_date: date
    entry_px: float
    t1_date: date | None
    t1_px: float | None
    final_date: date
    final_px: float
    exit_reason: str
    anchor_poc: float
    pwh: float
    pwl: float
    points_gross: float
    points_net: float


@dataclass(frozen=True)
class SwingResult:
    trades: list[SwingTrade]
    n_trades: int
    win_rate: float
    avg_points_gross: float
    avg_points_net: float
    total_points_net: float
    avg_R: float
    by_exit_reason: dict[str, int]


@dataclass(frozen=True)
class _WeeklyAnchor:
    anchor_poc: float
    pwh: float
    pwl: float


@dataclass
class _OpenPosition:
    entry_idx: int
    entry_date: date
    entry_px: float
    anchor_poc: float
    pwh: float
    pwl: float
    stop: float
    t1: float
    t2: float
    half_sold: bool = False
    t1_date: date | None = None
    t1_px: float | None = None


def _bar_date(bar: DatedBar) -> date:
    return date.fromisoformat(bar["d"])


def _iso_week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso.year, iso.week)


def _prior_iso_week(key: tuple[int, int]) -> tuple[int, int]:
    year, week = key
    monday = date.fromisocalendar(year, week, 1)
    prior_monday = monday - timedelta(days=7)
    prior_iso = prior_monday.isocalendar()
    return (prior_iso.year, prior_iso.week)


def _weekday_dates(year: int, week: int) -> list[date]:
    return [date.fromisocalendar(year, week, weekday) for weekday in range(1, 6)]


def _group_bars_by_iso_week(bars: list[DatedBar]) -> dict[tuple[int, int], list[DatedBar]]:
    grouped: dict[tuple[int, int], list[DatedBar]] = {}
    for bar in bars:
        key = _iso_week_key(_bar_date(bar))
        grouped.setdefault(key, []).append(bar)
    for week_bars in grouped.values():
        week_bars.sort(key=lambda item: item["d"])
    return grouped


def _is_full_week(week_bars: list[DatedBar], week_key: tuple[int, int]) -> bool:
    if len(week_bars) < 2:
        return False
    expected = {day.isoformat() for day in _weekday_dates(week_key[0], week_key[1])}
    actual = {bar["d"] for bar in week_bars}
    return expected.issubset(actual)


def _weekly_anchor(
    week_bars: list[DatedBar],
    *,
    n_bins: int,
    value_area_pct: float,
) -> _WeeklyAnchor | None:
    if len(week_bars) < 2:
        return None
    profile = build_volume_profile(
        week_bars, n_bins=n_bins, value_area_pct=value_area_pct
    )
    pwh = max(float(bar["h"]) for bar in week_bars)
    pwl = min(float(bar["l"]) for bar in week_bars)
    return _WeeklyAnchor(
        anchor_poc=float(profile["poc_price"]),
        pwh=pwh,
        pwl=pwl,
    )


def _build_weekly_anchors(
    bars: list[DatedBar],
    *,
    n_bins: int,
    value_area_pct: float,
) -> dict[tuple[int, int], _WeeklyAnchor]:
    grouped = _group_bars_by_iso_week(bars)
    anchors: dict[tuple[int, int], _WeeklyAnchor] = {}
    for week_key, week_bars in grouped.items():
        if not _is_full_week(week_bars, week_key):
            continue
        anchor = _weekly_anchor(
            week_bars, n_bins=n_bins, value_area_pct=value_area_pct
        )
        if anchor is not None:
            anchors[week_key] = anchor
    return anchors


def _regime_uptrend(closes_to_date: list[float]) -> bool:
    sma5 = sma(closes_to_date, 5)
    sma10 = sma(closes_to_date, 10)
    if sma5 is None or sma10 is None:
        return False
    return sma5 > sma10


def _entry_trigger(
    bars: list[DatedBar],
    idx: int,
    *,
    anchor_poc: float,
) -> bool:
    if idx < 1:
        return False
    prior_close = float(bars[idx - 1]["c"])
    low = float(bars[idx]["l"])
    close = float(bars[idx]["c"])
    closes_to_date = closes(bars[:idx])
    if not _regime_uptrend(closes_to_date):
        return False
    if prior_close <= anchor_poc:
        return False
    if low > anchor_poc:
        return False
    return close >= anchor_poc


def _should_enter(
    bars: list[DatedBar],
    idx: int,
    *,
    entry_mode: str,
    anchor_poc: float,
    is_first_bar_of_week: bool,
) -> bool:
    if entry_mode == "profile":
        return _entry_trigger(bars, idx, anchor_poc=anchor_poc)
    if entry_mode == "regime_only":
        if not is_first_bar_of_week:
            return False
        if idx < 1:
            return False
        return _regime_uptrend(closes(bars[:idx]))
    raise ValueError(f"unknown entry_mode: {entry_mode!r}")


def regime_buy_hold_points(bars: list[DatedBar], *, n_bins: int) -> float:
    """Sum close-to-close points on days where prior-close regime is uptrend."""
    _ = n_bins
    total = 0.0
    for i in range(1, len(bars)):
        if _regime_uptrend(closes(bars[:i])):
            total += float(bars[i]["c"]) - float(bars[i - 1]["c"])
    return total


def _trade_points_gross(
    *,
    entry_px: float,
    t1_px: float | None,
    final_px: float,
    half_sold: bool,
) -> float:
    if half_sold and t1_px is not None:
        return 0.5 * (t1_px - entry_px) + 0.5 * (final_px - entry_px)
    return final_px - entry_px


def _intrabar_stop_wins(*, low: float, high: float, stop: float, target: float) -> bool:
    return low <= stop and high >= target


def _simulate_exit(
    bars: list[DatedBar],
    pos: _OpenPosition,
    *,
    max_hold_days: int,
) -> SwingTrade:
    entry_px = pos.entry_px
    t1 = pos.t1
    t2 = pos.t2

    def _finish(
        *,
        bar_date: date,
        final_px: float,
        reason: str,
    ) -> SwingTrade:
        gross = _trade_points_gross(
            entry_px=entry_px,
            t1_px=pos.t1_px,
            final_px=final_px,
            half_sold=pos.half_sold,
        )
        return SwingTrade(
            entry_date=pos.entry_date,
            entry_px=entry_px,
            t1_date=pos.t1_date,
            t1_px=pos.t1_px,
            final_date=bar_date,
            final_px=final_px,
            exit_reason=reason,
            anchor_poc=pos.anchor_poc,
            pwh=pos.pwh,
            pwl=pos.pwl,
            points_gross=gross,
            points_net=gross,
        )

    for j in range(pos.entry_idx + 1, len(bars)):
        bar = bars[j]
        bar_date = _bar_date(bar)
        low = float(bar["l"])
        high = float(bar["h"])
        close = float(bar["c"])
        hold_days = j - pos.entry_idx

        if not pos.half_sold:
            if _intrabar_stop_wins(low=low, high=high, stop=pos.stop, target=t1):
                return _finish(bar_date=bar_date, final_px=pos.stop, reason="stop")
            if close < pos.stop:
                return _finish(bar_date=bar_date, final_px=close, reason="stop")
            if high >= t1:
                pos.half_sold = True
                pos.t1_date = bar_date
                pos.t1_px = t1
                pos.stop = entry_px
                if high >= t2:
                    return _finish(bar_date=bar_date, final_px=t2, reason="t2")
                if close < entry_px:
                    return _finish(
                        bar_date=bar_date, final_px=close, reason="t1_then_stop"
                    )
        else:
            if _intrabar_stop_wins(low=low, high=high, stop=pos.stop, target=t2):
                return _finish(
                    bar_date=bar_date, final_px=pos.stop, reason="t1_then_stop"
                )
            if close < pos.stop:
                return _finish(
                    bar_date=bar_date, final_px=close, reason="t1_then_stop"
                )
            if high >= t2:
                return _finish(bar_date=bar_date, final_px=t2, reason="t2")

        if hold_days >= max_hold_days:
            reason = "t1_then_time" if pos.half_sold else "time"
            return _finish(bar_date=bar_date, final_px=close, reason=reason)

    last = bars[-1]
    reason = "t1_then_time" if pos.half_sold else "time"
    return _finish(
        bar_date=_bar_date(last),
        final_px=float(last["c"]),
        reason=reason,
    )


def run_profile_mr_swing(
    bars: list[DatedBar],
    *,
    max_hold_days: int = 10,
    cost_points: float = 1.0,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
    entry_mode: str = "profile",
) -> SwingResult:
    """Long-only pivot-acceptance swing backtest using prior-week profile anchor."""
    if entry_mode not in ("profile", "regime_only"):
        raise ValueError(f"unknown entry_mode: {entry_mode!r}")
    if not bars:
        return SwingResult(
            trades=[],
            n_trades=0,
            win_rate=0.0,
            avg_points_gross=0.0,
            avg_points_net=0.0,
            total_points_net=0.0,
            avg_R=0.0,
            by_exit_reason={},
        )

    anchors = _build_weekly_anchors(
        bars, n_bins=n_bins, value_area_pct=value_area_pct
    )
    trades: list[SwingTrade] = []
    i = 0
    while i < len(bars):
        week_key = _iso_week_key(_bar_date(bars[i]))
        prior_key = _prior_iso_week(week_key)
        anchor = anchors.get(prior_key)
        is_first_bar_of_week = (
            i == 0 or _iso_week_key(_bar_date(bars[i - 1])) != week_key
        )
        if anchor is not None and i >= 10 and _should_enter(
            bars,
            i,
            entry_mode=entry_mode,
            anchor_poc=anchor.anchor_poc,
            is_first_bar_of_week=is_first_bar_of_week,
        ):
            entry_px = float(bars[i]["c"])
            pos = _OpenPosition(
                entry_idx=i,
                entry_date=_bar_date(bars[i]),
                entry_px=entry_px,
                anchor_poc=anchor.anchor_poc,
                pwh=anchor.pwh,
                pwl=anchor.pwl,
                stop=anchor.pwl,
                t1=anchor.pwh,
                t2=anchor.pwh + (anchor.pwh - anchor.pwl),
            )
            raw = _simulate_exit(bars, pos, max_hold_days=max_hold_days)
            trades.append(
                SwingTrade(
                    entry_date=raw.entry_date,
                    entry_px=raw.entry_px,
                    t1_date=raw.t1_date,
                    t1_px=raw.t1_px,
                    final_date=raw.final_date,
                    final_px=raw.final_px,
                    exit_reason=raw.exit_reason,
                    anchor_poc=raw.anchor_poc,
                    pwh=raw.pwh,
                    pwl=raw.pwl,
                    points_gross=raw.points_gross,
                    points_net=raw.points_gross - cost_points,
                )
            )
            while i < len(bars) and _bar_date(bars[i]) <= raw.final_date:
                i += 1
            continue
        i += 1

    n_trades = len(trades)
    net_points = [trade.points_net for trade in trades]
    win_rate = (
        sum(1 for pts in net_points if pts > 0) / n_trades if n_trades else 0.0
    )
    avg_points_gross = (
        sum(trade.points_gross for trade in trades) / n_trades if n_trades else 0.0
    )
    avg_points_net = sum(net_points) / n_trades if n_trades else 0.0
    total_points_net = sum(net_points)
    r_values = [
        trade.points_net / (trade.entry_px - trade.pwl)
        for trade in trades
        if trade.entry_px > trade.pwl
    ]
    avg_R = sum(r_values) / len(r_values) if r_values else 0.0
    by_exit_reason = dict(Counter(trade.exit_reason for trade in trades))

    return SwingResult(
        trades=trades,
        n_trades=n_trades,
        win_rate=win_rate,
        avg_points_gross=avg_points_gross,
        avg_points_net=avg_points_net,
        total_points_net=total_points_net,
        avg_R=avg_R,
        by_exit_reason=by_exit_reason,
    )


def _minute_bar_date(bar) -> date:
    text = bar["t"].strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).date()


def _daily_bars_date_bounds(symbol: str) -> tuple[date | None, date | None]:
    sym = symbol.strip().upper()
    cached = get_daily_bars(sym, date(1900, 1, 1), date(2100, 1, 1), source="schwab")
    if not cached:
        return None, None
    return date.fromisoformat(cached[0]["d"]), date.fromisoformat(cached[-1]["d"])


def validate_weekly_anchor(symbol: str = "/ES") -> dict:
    """Compare daily-approx weekly POC to true 1-min weekly POC for overlapping cache weeks."""
    sym = symbol.strip()
    daily_start, daily_end = _daily_bars_date_bounds(sym)
    if daily_start is None or daily_end is None:
        return {"weeks": [], "mean_abs_diff_pts": 0.0, "median_abs_diff_pts": 0.0}

    daily_bars = get_daily_bars(sym, daily_start, daily_end, source="schwab")
    daily_by_week = _group_bars_by_iso_week(daily_bars)

    minute_start = datetime(
        daily_start.year, daily_start.month, daily_start.day, tzinfo=UTC
    )
    minute_end = datetime(
        daily_end.year, daily_end.month, daily_end.day, 23, 59, 59, tzinfo=UTC
    )
    minute_bars = load_cached_minute_bars(sym, minute_start, minute_end, source="schwab")
    minute_by_week: dict[tuple[int, int], list] = {}
    for bar in minute_bars:
        key = _iso_week_key(_minute_bar_date(bar))
        minute_by_week.setdefault(key, []).append(bar)

    rows: list[dict] = []
    diffs: list[float] = []
    for week_key in sorted(set(daily_by_week) & set(minute_by_week)):
        daily_week = daily_by_week[week_key]
        minute_week = minute_by_week[week_key]
        if len(daily_week) < 2 or len(minute_week) < 2:
            continue
        daily_profile = build_volume_profile(daily_week)
        true_profile = build_volume_profile(minute_week)
        daily_poc = float(daily_profile["poc_price"])
        true_poc = float(true_profile["poc_price"])
        abs_diff = abs(daily_poc - true_poc)
        diffs.append(abs_diff)
        year, week = week_key
        rows.append(
            {
                "week": f"{year}-W{week:02d}",
                "daily_poc": daily_poc,
                "true_poc": true_poc,
                "abs_diff_pts": abs_diff,
            }
        )

    mean_diff = statistics.mean(diffs) if diffs else 0.0
    median_diff = statistics.median(diffs) if diffs else 0.0
    return {
        "weeks": rows,
        "mean_abs_diff_pts": mean_diff,
        "median_abs_diff_pts": median_diff,
    }


def _sanitize_symbol_filename(symbol: str) -> str:
    return symbol.strip().lstrip("/")


def _trade_csv_path(symbol: str) -> Path:
    name = _sanitize_symbol_filename(symbol)
    return RESEARCH_DATA_DIR / f"profile_mr_swing_{name}.csv"


def _write_trades_csv(trades: list[SwingTrade], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "entry_date",
        "entry_px",
        "t1_date",
        "t1_px",
        "final_date",
        "final_px",
        "exit_reason",
        "anchor_poc",
        "pwh",
        "pwl",
        "points_gross",
        "points_net",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow(
                {
                    "entry_date": trade.entry_date.isoformat(),
                    "entry_px": trade.entry_px,
                    "t1_date": trade.t1_date.isoformat() if trade.t1_date else "",
                    "t1_px": trade.t1_px if trade.t1_px is not None else "",
                    "final_date": trade.final_date.isoformat(),
                    "final_px": trade.final_px,
                    "exit_reason": trade.exit_reason,
                    "anchor_poc": trade.anchor_poc,
                    "pwh": trade.pwh,
                    "pwl": trade.pwl,
                    "points_gross": trade.points_gross,
                    "points_net": trade.points_net,
                }
            )


def _print_control_report(
    profile: SwingResult,
    regime_only: SwingResult,
    regime_bh_points: float,
) -> None:
    def _mode_line(label: str, result: SwingResult) -> None:
        print(
            f"{label}: n_trades={result.n_trades}  win_rate={result.win_rate:.1%}  "
            f"avg_points_net={result.avg_points_net:.2f}  "
            f"total_points_net={result.total_points_net:.2f}  avg_R={result.avg_R:.2f}"
        )

    print("alpha-vs-beta control (profile vs regime-only ablation):")
    _mode_line("profile", profile)
    _mode_line("regime_only", regime_only)
    print(f"regime_buy_hold_points: {regime_bh_points:.2f}")
    if profile.total_points_net <= regime_only.total_points_net:
        print("ANCHOR ADDS NO ALPHA vs regime-only ablation")
    else:
        edge = profile.total_points_net - regime_only.total_points_net
        print(f"profile edge vs regime_only: {edge:.2f} points")


def _print_report(result: SwingResult) -> None:
    print(f"n_trades={result.n_trades}")
    print(f"win_rate: {result.win_rate:.1%}")
    print(f"avg_points_gross: {result.avg_points_gross:.2f}")
    print(f"avg_points_net: {result.avg_points_net:.2f}")
    print(f"total_points_net: {result.total_points_net:.2f}")
    print(f"avg_R: {result.avg_R:.2f}")
    print()
    print("exit_reason breakdown:")
    for reason in sorted(result.by_exit_reason):
        print(f"  {reason}: {result.by_exit_reason[reason]}")
    print()
    print("trades (entry, t1, final, reason, net pts):")
    for trade in result.trades:
        t1_part = (
            f"t1={trade.t1_px:.2f}@{trade.t1_date}"
            if trade.t1_px is not None and trade.t1_date is not None
            else "t1=-"
        )
        print(
            f"  {trade.entry_date} entry={trade.entry_px:.2f}  {t1_part}  "
            f"final={trade.final_px:.2f}@{trade.final_date}  {trade.exit_reason}  "
            f"net={trade.points_net:.2f}"
        )


def _print_anchor_validation(summary: dict) -> None:
    print()
    print("weekly anchor validation (daily-approx vs 1-min true POC):")
    print(f"mean_abs_diff_pts: {summary['mean_abs_diff_pts']:.2f}")
    print(f"median_abs_diff_pts: {summary['median_abs_diff_pts']:.2f}")
    print("week  daily_poc  true_poc  abs_diff_pts")
    for row in summary["weeks"]:
        print(
            f"  {row['week']}  {row['daily_poc']:.2f}  {row['true_poc']:.2f}  "
            f"{row['abs_diff_pts']:.2f}"
        )


def _parse_cli_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile MR swing backtest on daily bars (pivot-acceptance continuation)."
    )
    parser.add_argument("--symbol", default="/ES", help="Symbol (default: /ES)")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--max-hold-days",
        type=int,
        default=10,
        help="Max sessions to hold remainder (default: 10)",
    )
    parser.add_argument(
        "--cost-points",
        type=float,
        default=1.0,
        help="Round-trip cost in ES points (default: 1)",
    )
    parser.add_argument(
        "--validate-anchor",
        action="store_true",
        help="Print daily vs 1-min weekly POC comparison for cached weeks",
    )
    parser.add_argument(
        "--source", default="schwab", help="Daily bar source (default: schwab)"
    )
    parser.add_argument(
        "--control",
        action="store_true",
        help="Run profile vs regime-only ablation and regime buy-and-hold baseline",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start = _parse_cli_date(args.start)
    end = _parse_cli_date(args.end)
    bars = get_daily_bars(args.symbol, start, end, source=args.source)
    common = {
        "max_hold_days": args.max_hold_days,
        "cost_points": args.cost_points,
    }
    if args.control:
        profile = run_profile_mr_swing(bars, entry_mode="profile", **common)
        regime_only = run_profile_mr_swing(bars, entry_mode="regime_only", **common)
        regime_bh = regime_buy_hold_points(bars, n_bins=100)
        _print_control_report(profile, regime_only, regime_bh)
        return 0

    result = run_profile_mr_swing(bars, entry_mode="profile", **common)
    _print_report(result)
    csv_path = _trade_csv_path(args.symbol)
    _write_trades_csv(result.trades, csv_path)
    print()
    print(f"wrote {csv_path}")
    if args.validate_anchor:
        summary = validate_weekly_anchor(args.symbol)
        _print_anchor_validation(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
