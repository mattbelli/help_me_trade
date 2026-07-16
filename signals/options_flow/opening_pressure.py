"""Index flow pressure gauges — net premium imbalance at the session bookends.

Two fixed 30-minute windows: the open (09:30-10:00 ET — the committed hypothesis,
memory: opening-index-flow-pressure) and the close (15:30-16:00 ET — MOC-adjacent
repositioning), each its own calibration series. Net options-flow imbalance on the
index complex + saturated megacaps is a market-posture read the founder wants
glanceable every morning and afternoon. This module MEASURES it — raw
premium sums with an honest unclassified fraction. It fabricates nothing: no score,
no win%, no "abnormal" flag until a per-minute baseline has accrued enough days
(the volume_profile discipline; baselines accrue silently from day one).

Pure module — no I/O, no clocks (rolling.py discipline): callers feed normalized,
DIR-1-classified prints (``intraday.normalize_print`` shape, same as
``RollingFlow.add_print``) and read snapshots. Transport-agnostic, but NOTE: the
capped active-set lane deliberately excludes 0-1 DTE on SPY/QQQ/IWM/DIA
(``poll._skip_index_zero_dte``), which is most of index opening flow — real
coverage comes from the wildcard trades tape, so rows carry the ``coverage`` stamp.

Bull premium = call buys + put sells; bear = put buys + call sells (aggressor side
from DIR-1; ``undetermined`` prints accrue to ``unclassified`` and are surfaced,
never guessed).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from signals.options_flow.intraday import is_hygiene_excluded

_ET = ZoneInfo("America/New_York")

# Window geometry: two fixed 30-minute session bookends, 1-min buckets. The open
# window reads positioning at the day's most information-dense half hour; the close
# window catches MOC-adjacent repositioning into the bell.
WINDOW_MINUTES = 30
WINDOWS: dict[str, int] = {
    "open": 9 * 60 + 30,  # [09:30, 10:00) ET
    "close": 15 * 60 + 30,  # [15:30, 16:00) ET
}

# The fixed gauge universe. SPX + SPXW both listed: SPXW-rooted contracts appear on
# the wildcard tape under either root depending on the series. NDX/NDXP/RUT are not
# on this feed at all (QQQ/IWM carry them); revisit if the vendor adds them.
INDEX_UNDERLYINGS = ("SPY", "QQQ", "IWM", "DIA", "SPX", "SPXW")
MEGACAP_UNDERLYINGS = ("TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL")
PRESSURE_UNDERLYINGS = frozenset(INDEX_UNDERLYINGS + MEGACAP_UNDERLYINGS)

# Mirror rolling.py's ingest dust floor so the two stream consumers agree on what
# counts as flow at all.
_DUST_MIN_USD = 2_000


@dataclass
class _MinuteCell:
    bull_usd: float = 0.0
    bear_usd: float = 0.0
    unclassified_usd: float = 0.0
    n_prints: int = 0


def window_minute(ts_ms: int, window: str = "open") -> int | None:
    """Bucket index 0..29 for a print inside the window, else None."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=_ET)
    offset = dt.hour * 60 + dt.minute - WINDOWS[window]
    if 0 <= offset < WINDOW_MINUTES:
        return offset
    return None


class PressureGauge:
    """One instance per (session day, window); feed prints, read snapshots/rows."""

    def __init__(self, window: str = "open") -> None:
        if window not in WINDOWS:
            raise ValueError(f"unknown pressure window {window!r}")
        self.window = window
        self._cells: dict[str, dict[int, _MinuteCell]] = defaultdict(dict)

    def add_print(self, p: dict) -> None:
        sym = str(p.get("underlying") or "").strip().upper()
        if sym not in PRESSURE_UNDERLYINGS:
            return
        if is_hygiene_excluded(p.get("conditions") or []):
            return
        if p.get("spread_leg") or p.get("likely_spread"):
            return
        try:
            price = float(p["price"])
            size = int(p["size"])
            ts_ms = int(p["ts_ms"])
        except (KeyError, TypeError, ValueError):
            return
        if price <= 0 or size <= 0:
            return
        prem = price * size * 100.0
        if prem < _DUST_MIN_USD:
            return
        minute = window_minute(ts_ms, self.window)
        if minute is None:
            return

        cells = self._cells[sym]
        cell = cells.get(minute)
        if cell is None:
            cell = cells[minute] = _MinuteCell()

        right = str(p.get("right") or "").strip().lower()[:1]
        direction = str(p.get("direction") or "undetermined")
        if direction == "buy_side" and right == "c":
            cell.bull_usd += prem
        elif direction == "sell_side" and right == "p":
            cell.bull_usd += prem
        elif direction == "buy_side" and right == "p":
            cell.bear_usd += prem
        elif direction == "sell_side" and right == "c":
            cell.bear_usd += prem
        else:
            cell.unclassified_usd += prem
        cell.n_prints += 1

    # --- reads ------------------------------------------------------------------

    def minute_rows(self, date_et: str) -> list[tuple[str, str, int, float, float, float, int]]:
        """(underlying, date, minute, bull, bear, unclassified, n_prints) rows."""
        rows = []
        for sym in sorted(self._cells):
            for minute in sorted(self._cells[sym]):
                c = self._cells[sym][minute]
                rows.append(
                    (sym, date_et, minute, c.bull_usd, c.bear_usd, c.unclassified_usd, c.n_prints)
                )
        return rows

    def snapshot(self) -> dict[str, dict]:
        """Per-underlying window totals: net/bull/bear/unclassified/classified_frac."""
        out: dict[str, dict] = {}
        for sym, cells in self._cells.items():
            bull = sum(c.bull_usd for c in cells.values())
            bear = sum(c.bear_usd for c in cells.values())
            uncls = sum(c.unclassified_usd for c in cells.values())
            n = sum(c.n_prints for c in cells.values())
            gross = bull + bear + uncls
            if gross <= 0:
                continue
            out[sym] = {
                "bull_usd": bull,
                "bear_usd": bear,
                "unclassified_usd": uncls,
                "net_usd": bull - bear,
                "classified_frac": (bull + bear) / gross,
                "n_prints": n,
                "minutes_with_data": len(cells),
            }
        return out
