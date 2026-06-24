"""Prior-session volume-profile node touch detection (PERCEIVE half of propose-only trigger)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from research.intraday_bars import IntradayBar, load_cached_minute_bars
from research.intraday_profile import segment_rth_sessions, session_profile, to_ny


@dataclass(frozen=True)
class ReferenceNodes:
    session_date: date
    poc: float
    vah: float
    val: float
    pdh: float
    pdl: float

    def as_levels(self) -> list[tuple[str, float]]:
        return [
            ("VAH", self.vah),
            ("VAL", self.val),
            ("PDH", self.pdh),
            ("PDL", self.pdl),
        ]


@dataclass(frozen=True)
class NodeTrigger:
    ts: str
    node_name: str
    node_price: float
    direction: str
    target_price: float
    touch_price: float
    context: str


def compute_reference_nodes(
    prior_session_bars: list[IntradayBar],
    *,
    n_bins: int = 100,
    value_area_pct: float = 0.70,
) -> ReferenceNodes | None:
    """Build reference nodes from one prior RTH session's bars."""
    if not prior_session_bars:
        return None

    ordered = sorted(prior_session_bars, key=lambda item: item["t"])
    profile = session_profile(
        ordered, n_bins=n_bins, value_area_pct=value_area_pct
    )
    if profile.get("n_bars", 0) == 0:
        return None

    poc = profile.get("poc_price")
    vah = profile.get("vah")
    val = profile.get("val")
    session_high = profile.get("session_high")
    session_low = profile.get("session_low")
    if poc is None or vah is None or val is None:
        return None
    if session_high is None or session_low is None:
        return None

    session_date = to_ny(ordered[0]["t"]).date()
    return ReferenceNodes(
        session_date=session_date,
        poc=float(poc),
        vah=float(vah),
        val=float(val),
        pdh=float(session_high),
        pdl=float(session_low),
    )


def detect_node_triggers(
    intraday_bars: list[IntradayBar],
    nodes: ReferenceNodes,
    *,
    rearm_points: float = 2.0,
    max_per_node: int = 2,
    max_triggers_per_day: int = 6,
    mode: str = "reversion",
) -> list[NodeTrigger]:
    """Walk current-session bars and emit node-touch triggers with re-arm hysteresis."""
    if mode != "reversion":
        raise ValueError(f"unsupported mode: {mode!r}")

    ordered = sorted(intraday_bars, key=lambda item: item["t"])
    levels = nodes.as_levels()
    armed = {name: True for name, _ in levels}
    max_away: dict[str, float] = {name: 0.0 for name, _ in levels}
    node_trigger_counts: dict[str, int] = {name: 0 for name, _ in levels}
    triggers: list[NodeTrigger] = []

    for bar in ordered:
        bar_low = float(bar["l"])
        bar_high = float(bar["h"])

        for node_name, node_price in levels:
            if not armed[node_name]:
                dist_low = abs(bar_low - node_price)
                dist_high = abs(bar_high - node_price)
                max_away[node_name] = max(
                    max_away[node_name], dist_low, dist_high
                )
                if max_away[node_name] >= rearm_points:
                    armed[node_name] = True
                    max_away[node_name] = 0.0
                continue

            if not (bar_low <= node_price <= bar_high):
                continue

            direction = _reversion_direction(node_price, nodes.poc)
            if direction is None:
                continue

            if node_trigger_counts[node_name] >= max_per_node:
                armed[node_name] = False
                max_away[node_name] = max(
                    abs(bar_low - node_price), abs(bar_high - node_price)
                )
                continue

            if len(triggers) >= max_triggers_per_day:
                armed[node_name] = False
                max_away[node_name] = max(
                    abs(bar_low - node_price), abs(bar_high - node_price)
                )
                continue

            role = "support" if direction == "long" else "resistance"
            triggers.append(
                NodeTrigger(
                    ts=bar["t"],
                    node_name=node_name,
                    node_price=node_price,
                    direction=direction,
                    target_price=nodes.poc,
                    touch_price=node_price,
                    context=(
                        f"tagged prior {node_name} ({role}) -> revert to POC"
                    ),
                )
            )
            node_trigger_counts[node_name] += 1
            armed[node_name] = False
            max_away[node_name] = max(
                abs(bar_low - node_price), abs(bar_high - node_price)
            )

    return triggers


def _reversion_direction(node_price: float, poc: float) -> str | None:
    if node_price < poc:
        return "long"
    if node_price > poc:
        return "short"
    return None


def _parse_cli_date(text: str, *, end_of_day: bool = False) -> datetime:
    day = datetime.strptime(text, "%Y-%m-%d").date()
    if end_of_day:
        return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect prior-session node touches on cached 1-min bars."
    )
    parser.add_argument("--symbol", required=True, help="Symbol (e.g. SPY)")
    parser.add_argument(
        "--date", required=True, help="Current session date YYYY-MM-DD"
    )
    parser.add_argument(
        "--rearm-points",
        type=float,
        default=2.0,
        help="Points away from node before re-arm (default: 2.0)",
    )
    parser.add_argument(
        "--max-per-node",
        type=int,
        default=2,
        help="Max triggers per node per session (default: 2)",
    )
    parser.add_argument(
        "--max-per-day",
        type=int,
        default=6,
        help="Max total triggers per session (default: 6)",
    )
    parser.add_argument("--source", default="schwab", help="Bar source (default: schwab)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    current_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    end = _parse_cli_date(args.date, end_of_day=True)

    # Load a window that includes prior sessions for reference lookup.
    lookback_start = _parse_cli_date(
        (current_date - timedelta(days=14)).isoformat()
    )
    all_bars = load_cached_minute_bars(
        args.symbol, lookback_start, end, source=args.source
    )
    sessions = segment_rth_sessions(all_bars)
    prior_dates = [d for d in sessions if d < current_date]
    if not prior_dates:
        print(f"No prior RTH session before {args.date}", file=sys.stderr)
        return 1

    prior_date = max(prior_dates)
    prior_bars = sessions[prior_date]
    today_bars = sessions.get(current_date, [])

    nodes = compute_reference_nodes(prior_bars)
    if nodes is None:
        print(f"Could not compute reference nodes for {prior_date}", file=sys.stderr)
        return 1

    print(
        f"nodes session={nodes.session_date} "
        f"POC={nodes.poc:.2f} VAH={nodes.vah:.2f} VAL={nodes.val:.2f} "
        f"PDH={nodes.pdh:.2f} PDL={nodes.pdl:.2f}"
    )

    triggers = detect_node_triggers(
        today_bars,
        nodes,
        rearm_points=args.rearm_points,
        max_per_node=args.max_per_node,
        max_triggers_per_day=args.max_per_day,
    )
    if not triggers:
        print("triggers: none")
    for trigger in triggers:
        print(
            f"  {trigger.ts}  {trigger.node_name}  {trigger.direction}  "
            f"touch={trigger.touch_price:.2f}  target={trigger.target_price:.2f}  "
            f"({trigger.context})"
        )
    print(f"total={len(triggers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
