#!/usr/bin/env python3
"""IV-rank baseline study — does the variance risk premium exist, and does IV rank predict it?

Implements `References/IV_RANK_BASELINE_PREREG_2026-07-25.md` exactly. That document is
LAW: metrics, thresholds and outcome-consequences were fixed before any VRP was computed,
so this script computes what was pre-registered and nothing else.

    uv run python tools/iv_rank_baseline_study.py            # primary panel
    uv run python tools/iv_rank_baseline_study.py --h4       # + the 21-session horizon arm

Reads the gitignored, externally-licensed corpus (research use only); writes tables to
data/research/iv_rank_baseline/. Nothing here touches trading/ or produces a win%.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import statistics
from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Override so the study can run from a git worktree, where data/ is empty — the corpus
# is gitignored and lives only in the main tree.
DB_PATH = Path(os.environ.get("OPTIONS_IV_DB_PATH") or ROOT / "data" / "options_iv_history.db")
OUT_DIR = ROOT / "data" / "research" / "iv_rank_baseline"

FWD_SESSIONS = 30  # hv_current's own window — see prereg "Instruments"
MIN_TRAILING_OBS = 120  # below this a trailing-year percentile is noise
DECILES = 10
BOOTSTRAP_DRAWS = 2000
COVID_LO, COVID_HI = "2020-02-01", "2020-04-30"
SEED = 20260725  # fixed so the bootstrap is reproducible


@dataclass(slots=True)
class Obs:
    """One symbol-date observation, after all prereg filters."""

    symbol: str
    date: str
    iv: float
    rv_fwd: float
    ivr_range: float | None
    ivr_pct: float | None

    @property
    def vrp(self) -> float:
        return self.iv - self.rv_fwd

    @property
    def month(self) -> str:
        return self.date[:7]


@dataclass
class Dropped:
    """Every observation we refuse, counted — the prereg forbids silent drops."""

    no_forward_row: int = 0
    thin_trailing_window: int = 0
    degenerate_year_range: int = 0
    null_iv_or_hv: int = 0
    kept: int = 0
    detail: dict = field(default_factory=dict)


def _f(v) -> float | None:
    """Corpus columns are TEXT; empty string and NULL both mean absent."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def load_panel(conn: sqlite3.Connection, symbols: list[str] | None = None) -> tuple[list[Obs], Dropped]:
    """Build the observation panel per the prereg definitions."""
    drop = Dropped()
    where, params = "", []
    if symbols:
        where = f" WHERE act_symbol IN ({','.join('?' * len(symbols))})"
        params = symbols
    rows = conn.execute(
        "SELECT act_symbol, date, iv_current, hv_current, iv_year_high, iv_year_low"
        f" FROM volatility_history{where} ORDER BY act_symbol, date",
        params,
    ).fetchall()

    by_symbol: dict[str, list[tuple]] = defaultdict(list)
    for sym, date, iv, hv, hi, lo in rows:
        by_symbol[sym].append((date, iv, hv, hi, lo))

    panel: list[Obs] = []
    for sym, series in by_symbol.items():
        ivs = [_f(r[1]) for r in series]
        hvs = [_f(r[2]) for r in series]
        dates = [r[0] for r in series]
        # Trailing-year IV window, maintained incrementally: a sorted list plus two
        # pointers. Recomputing the window per row is O(n*250) in Python and dominates
        # the whole run; insort/bisect keep it at C speed.
        window: list[float] = []
        left = 0
        for i, (date, _iv, _hv, hi_s, lo_s) in enumerate(series):
            iv = ivs[i]
            if iv is not None:
                insort(window, iv)
            floor = _minus_365(date)
            while left < i and dates[left] <= floor:
                old = ivs[left]
                if old is not None:
                    window.pop(bisect_left(window, old))
                left += 1
            if iv is None:
                drop.null_iv_or_hv += 1
                continue
            # RV_fwd: hv_current exactly FWD_SESSIONS rows ahead. The corpus has gaps,
            # so "rows ahead" is only valid if those rows are actually consecutive
            # sessions; a gap makes the horizon wrong, and the prereg says drop, never
            # interpolate. We approximate consecutiveness by requiring the calendar
            # span to look like ~30 sessions (28-60 days) rather than a hole.
            j = i + FWD_SESSIONS
            if j >= len(series) or hvs[j] is None:
                drop.no_forward_row += 1
                continue
            span = _days_between(date, dates[j])
            if span is None or not (28 <= span <= 60):
                drop.no_forward_row += 1
                continue

            hi, lo = _f(hi_s), _f(lo_s)
            ivr_range = None
            if hi is not None and lo is not None and hi > lo:
                ivr_range = (iv - lo) / (hi - lo)
            elif hi is not None and lo is not None:
                drop.degenerate_year_range += 1

            # IV percentile over the symbol's own trailing 365 days (window is current
            # as of this row, and contains only dates <= date — no lookahead).
            if len(window) >= MIN_TRAILING_OBS:
                ivr_pct = bisect_right(window, iv) / len(window)
            else:
                ivr_pct = None
                drop.thin_trailing_window += 1

            if ivr_range is None and ivr_pct is None:
                continue
            drop.kept += 1
            panel.append(Obs(sym, date, iv, hvs[j], ivr_range, ivr_pct))
    return panel, drop


FWD_CALENDAR_DAYS = 44  # 30 sessions * 365/252 — Addendum A1
FWD_TOLERANCE_DAYS = 7


def load_panel_dated(conn: sqlite3.Connection, symbols: list[str] | None = None) -> tuple[list[Obs], Dropped]:
    """Addendum A1 — forward RV anchored on the CALENDAR, not on a row offset.

    The primary indexes RV_fwd 30 rows ahead, which silently becomes a ~70-day horizon
    wherever the corpus sampled sparsely, so the whole pre-2024 era was refused. Because
    hv_current is a 30-session trailing window, the row nearest t+44 days carries a
    window covering roughly (t, t+30 sessions] no matter how dense the sampling was.
    """
    drop = Dropped()
    where, params = "", []
    if symbols:
        where = f" WHERE act_symbol IN ({','.join('?' * len(symbols))})"
        params = symbols
    rows = conn.execute(
        "SELECT act_symbol, date, iv_current, hv_current, iv_year_high, iv_year_low"
        f" FROM volatility_history{where} ORDER BY act_symbol, date",
        params,
    ).fetchall()
    by_symbol: dict[str, list[tuple]] = defaultdict(list)
    for sym, date, iv, hv, hi, lo in rows:
        by_symbol[sym].append((date, iv, hv, hi, lo))

    panel: list[Obs] = []
    for sym, series in by_symbol.items():
        ivs = [_f(r[1]) for r in series]
        hvs = [_f(r[2]) for r in series]
        dates = [r[0] for r in series]
        window: list[float] = []
        left = 0
        for i, (date, _iv, _hv, hi_s, lo_s) in enumerate(series):
            iv = ivs[i]
            if iv is not None:
                insort(window, iv)
            floor = _minus_365(date)
            while left < i and dates[left] <= floor:
                old = ivs[left]
                if old is not None:
                    window.pop(bisect_left(window, old))
                left += 1
            if iv is None:
                drop.null_iv_or_hv += 1
                continue

            target = _plus_days(date, FWD_CALENDAR_DAYS)
            j = bisect_left(dates, target, i + 1)
            best, best_gap = None, None
            for cand in (j - 1, j, j + 1):
                if cand <= i or cand >= len(series) or hvs[cand] is None:
                    continue
                gap = abs(_days_between(target, dates[cand]) or 999)
                if best_gap is None or gap < best_gap:
                    best, best_gap = cand, gap
            if best is None or best_gap > FWD_TOLERANCE_DAYS:
                drop.no_forward_row += 1
                continue

            hi, lo = _f(hi_s), _f(lo_s)
            ivr_range = (iv - lo) / (hi - lo) if hi is not None and lo is not None and hi > lo else None
            if len(window) >= MIN_TRAILING_OBS:
                ivr_pct = bisect_right(window, iv) / len(window)
            else:
                ivr_pct = None
                drop.thin_trailing_window += 1
            if ivr_range is None and ivr_pct is None:
                continue
            drop.kept += 1
            panel.append(Obs(sym, date, iv, hvs[best], ivr_range, ivr_pct))
    return panel, drop


def _plus_days(d: str, n: int) -> str:
    from datetime import date as _d
    from datetime import timedelta

    return (_d.fromisoformat(d) + timedelta(days=n)).isoformat()


def _days_between(a: str, b: str) -> int | None:
    from datetime import date as _d

    try:
        return (_d.fromisoformat(b) - _d.fromisoformat(a)).days
    except ValueError:
        return None


def _minus_365(d: str) -> str:
    from datetime import date as _d
    from datetime import timedelta

    return (_d.fromisoformat(d) - timedelta(days=365)).isoformat()


def month_block_bootstrap(
    terms: dict[str, tuple[float, ...]], combine, draws: int = BOOTSTRAP_DRAWS
) -> tuple[float, float]:
    """95% CI by resampling whole calendar MONTHS.

    Months are the block because they absorb BOTH threats at once: the ~30x overlap
    inside a symbol's own forward windows, and the cross-sectional dependence from every
    symbol selling off together. Resampling rows would manufacture significance.

    ``terms`` maps month -> the additive components of the statistic (sums and counts),
    and ``combine`` turns their totals into the statistic. Keeping the statistic additive
    is what makes 2,000 draws over a 1.8M-row panel cheap: a draw sums a few hundred
    tuples instead of rebuilding the sample.
    """
    months = list(terms)
    if len(months) < 3:
        return (float("nan"), float("nan"))
    width = len(next(iter(terms.values())))
    rng = random.Random(SEED)
    vals = []
    for _ in range(draws):
        totals = [0.0] * width
        for _ in months:
            t = terms[rng.choice(months)]
            for i in range(width):
                totals[i] += t[i]
        v = combine(totals)
        if v is not None and not math.isnan(v):
            vals.append(v)
    if len(vals) < draws // 10:
        return (float("nan"), float("nan"))
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))])


def _mean_terms(obs: list[Obs]) -> dict[str, tuple[float, ...]]:
    """Per-month (sum of VRP, count) — the additive form of a mean."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for o in obs:
        a = acc[o.month]
        a[0] += o.vrp
        a[1] += 1
    return {m: tuple(v) for m, v in acc.items()}


def _mean_combine(t: list[float]) -> float | None:
    return t[0] / t[1] if t[1] else None


def _diff_terms(obs: list[Obs], key: str) -> dict[str, tuple[float, ...]]:
    """Per-month (top sum, top n, bottom sum, bottom n) for the H2 decile difference."""
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for o in obs:
        r = getattr(o, key)
        if r is None:
            continue
        a = acc[o.month]
        if r >= 0.9:
            a[0] += o.vrp
            a[1] += 1
        elif r < 0.1:
            a[2] += o.vrp
            a[3] += 1
    return {m: tuple(v) for m, v in acc.items()}


def _diff_combine(t: list[float]) -> float | None:
    if not t[1] or not t[3]:
        return None
    return t[0] / t[1] - t[2] / t[3]


def mean_vrp(obs: list[Obs]) -> float | None:
    return statistics.fmean(o.vrp for o in obs) if obs else None


def effective_n(obs: list[Obs]) -> int:
    """Independent symbol-months — the only honest n for an overlapping panel."""
    return len({(o.symbol, o.month) for o in obs})


def decile_table(obs: list[Obs], key: str) -> list[dict]:
    """Per-rank-decile means WITH the mandatory tail columns."""
    scored = [o for o in obs if getattr(o, key) is not None]
    if not scored:
        return []
    out = []
    for d in range(DECILES):
        lo, hi = d / DECILES, (d + 1) / DECILES
        bucket = [
            o for o in scored
            if lo <= getattr(o, key) < hi or (d == DECILES - 1 and getattr(o, key) == 1.0)
        ]
        if not bucket:
            continue
        vrps = sorted(o.vrp for o in bucket)
        by_sm: dict[tuple, list[float]] = defaultdict(list)
        for o in bucket:
            by_sm[(o.symbol, o.month)].append(o.vrp)
        worst_sm = min(statistics.fmean(v) for v in by_sm.values())
        out.append({
            "decile": d + 1,
            "n_rows": len(bucket),
            "n_symbol_months": effective_n(bucket),
            "mean_vrp": statistics.fmean(vrps),
            "median_vrp": statistics.median(vrps),
            "p05_vrp": vrps[int(0.05 * len(vrps))],
            "worst_symbol_month": worst_sm,
            "share_negative": sum(1 for v in vrps if v < 0) / len(vrps),
            "mean_iv": statistics.fmean(o.iv for o in bucket),
            "mean_rv_fwd": statistics.fmean(o.rv_fwd for o in bucket),
        })
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation — H3's monotonicity test."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def evaluate(obs: list[Obs], label: str) -> dict:
    """H1/H2/H3 against the pre-registered thresholds, plus the mandated tails."""
    res: dict = {"label": label, "n_rows": len(obs), "n_symbol_months": effective_n(obs),
                 "n_symbols": len({o.symbol for o in obs}),
                 "date_range": [min(o.date for o in obs), max(o.date for o in obs)] if obs else None}
    m = mean_vrp(obs)
    lo, hi = month_block_bootstrap(_mean_terms(obs), _mean_combine)
    vrps = sorted(o.vrp for o in obs)
    res["H1"] = {
        "mean_vrp": m, "ci95": [lo, hi],
        "passes": bool(m and m > 0 and lo == lo and lo > 0),
        "median_vrp": statistics.median(vrps) if vrps else None,
        "p05_vrp": vrps[int(0.05 * len(vrps))] if vrps else None,
        "share_negative": (sum(1 for v in vrps if v < 0) / len(vrps)) if vrps else None,
    }
    for key in ("ivr_range", "ivr_pct"):
        table = decile_table(obs, key)
        res[key] = {"deciles": table}
        if len(table) < DECILES:
            res[key]["H2"] = res[key]["H3"] = {"passes": False, "reason": "incomplete deciles"}
            continue
        scored = [o for o in obs if getattr(o, key) is not None]
        top = [o for o in scored if getattr(o, key) >= 0.9]
        bot = [o for o in scored if getattr(o, key) < 0.1]

        d = (mean_vrp(top) - mean_vrp(bot)) if top and bot else None
        dlo, dhi = month_block_bootstrap(_diff_terms(scored, key), _diff_combine)
        res[key]["H2"] = {
            "top_decile_mean": mean_vrp(top), "bottom_decile_mean": mean_vrp(bot),
            "difference": d, "ci95": [dlo, dhi],
            "passes": bool(d and d > 0 and dlo == dlo and dlo > 0),
        }
        rho = spearman([r["decile"] for r in table], [r["mean_vrp"] for r in table])
        res[key]["H3"] = {"spearman": rho, "threshold": 0.6, "passes": bool(rho and rho >= 0.6)}
    return res


H4_SESSIONS = 21  # ~30 CALENDAR days, matching a 30-day implied — see prereg H4
H4_BAR_DAYS = 2500


def load_panel_h4(conn: sqlite3.Connection, symbols: list[str]) -> tuple[list[Obs], dict]:
    """Prereg H4 — forward RV over 21 SESSIONS from our own bars, not the vendor's column.

    hv_current is a 30-SESSION (~42 calendar day) window while iv_current is ~30 CALENDAR
    days. Both are annualized so they compare to first order, but term structure makes
    the mismatch a real second-order bias. This arm removes it by computing forward RV
    over exactly the option's calendar life, from bars we hold.
    """
    from brokers.alpaca import AlpacaAdapter

    adapter = AlpacaAdapter.from_settings()
    bars: dict = {}
    for start in range(0, len(symbols), 25):
        chunk = symbols[start : start + 25]
        try:
            bars.update(adapter.get_stock_bars_batch(chunk, timeframe="1Day", days=H4_BAR_DAYS) or {})
        except Exception:
            for sym in chunk:  # one bad ticker must not empty the batch (see sampler)
                try:
                    bars.update(adapter.get_stock_bars_batch([sym], timeframe="1Day", days=H4_BAR_DAYS) or {})
                except Exception:
                    pass

    panel: list[Obs] = []
    meta = {"symbols_requested": len(symbols), "symbols_with_bars": 0, "no_forward_window": 0}
    for sym in symbols:
        rows = bars.get(sym) or []
        if len(rows) < H4_SESSIONS + 2:
            continue
        meta["symbols_with_bars"] += 1
        bdates = [r.time[:10] for r in rows]
        closes = [r.close for r in rows]
        bidx = {d: i for i, d in enumerate(bdates)}
        rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

        series = conn.execute(
            "SELECT date, iv_current, iv_year_high, iv_year_low FROM volatility_history"
            " WHERE act_symbol=? ORDER BY date", (sym,)).fetchall()
        ivs = [_f(r[1]) for r in series]
        dates = [r[0] for r in series]
        window: list[float] = []
        left = 0
        for i, (date, _iv, hi_s, lo_s) in enumerate(series):
            iv = ivs[i]
            if iv is not None:
                insort(window, iv)
            floor = _minus_365(date)
            while left < i and dates[left] <= floor:
                old = ivs[left]
                if old is not None:
                    window.pop(bisect_left(window, old))
                left += 1
            if iv is None:
                continue
            b = bidx.get(date)
            # rets[k] is the return INTO bar k+1, so the forward window is rets[b : b+21].
            if b is None or b + H4_SESSIONS >= len(rets):
                meta["no_forward_window"] += 1
                continue
            fwd = rets[b : b + H4_SESSIONS]
            rv = statistics.stdev(fwd) * math.sqrt(252)
            if rv <= 0:
                continue
            hi, lo = _f(hi_s), _f(lo_s)
            ivr_range = (iv - lo) / (hi - lo) if hi is not None and lo is not None and hi > lo else None
            ivr_pct = bisect_right(window, iv) / len(window) if len(window) >= MIN_TRAILING_OBS else None
            if ivr_range is None and ivr_pct is None:
                continue
            panel.append(Obs(sym, date, iv, rv, ivr_range, ivr_pct))
    return panel, meta


def quintile(v: float) -> int:
    return min(4, int(v * 5))


def level_control(obs: list[Obs], key: str = "ivr_range") -> dict:
    """Addendum A2 — is the rank effect a LEVEL effect wearing a rank costume?

    A2a sorts deciles by IV level instead of rank; A2b double-sorts into 5x5
    level x rank cells. The gate question is whether rank still orders VRP once level
    is held roughly fixed.
    """
    scored = [o for o in obs if getattr(o, key) is not None]
    if not scored:
        return {}
    # IV level expressed as a cross-sectional percentile, index-aligned with `scored`
    # (Obs is slotted and unhashable, so no dict may be keyed by it).
    lv = sorted(o.iv for o in scored)
    level_pct = [bisect_right(lv, o.iv) / len(lv) for o in scored]
    a2a = []
    for d in range(DECILES):
        lo, hi = d / DECILES, (d + 1) / DECILES
        bucket = [o for o, p in zip(scored, level_pct, strict=True)
                  if lo <= p < hi or (d == DECILES - 1 and p == 1.0)]
        if not bucket:
            continue
        vrps = sorted(o.vrp for o in bucket)
        a2a.append({"decile": d + 1, "n_rows": len(bucket),
                    "mean_vrp": statistics.fmean(vrps), "p05_vrp": vrps[int(0.05 * len(vrps))],
                    "share_negative": sum(1 for v in vrps if v < 0) / len(vrps),
                    "mean_iv": statistics.fmean(o.iv for o in bucket)})
    a2a_rho = spearman([r["decile"] for r in a2a], [r["mean_vrp"] for r in a2a]) if len(a2a) == DECILES else None

    # A2b: 5x5 level x rank cells.
    cells: dict[tuple[int, int], list[Obs]] = defaultdict(list)
    for o, p in zip(scored, level_pct, strict=True):
        cells[(quintile(p), quintile(getattr(o, key)))].append(o)
    grid = []
    survives_everywhere = True
    for lq in range(5):
        row = []
        for rq in range(5):
            b = cells.get((lq, rq), [])
            row.append({"n": len(b), "mean_vrp": mean_vrp(b), "mean_iv": statistics.fmean(o.iv for o in b) if b else None})
        grid.append(row)
        top, bot = row[4]["mean_vrp"], row[0]["mean_vrp"]
        if top is None or bot is None or top <= bot:
            survives_everywhere = False

    # Pooled within-level difference: top-rank-quintile minus bottom, per level quintile,
    # then averaged — with a month-block CI on the same quantity.
    def within_terms() -> dict[str, tuple[float, ...]]:
        acc: dict[str, list[float]] = defaultdict(lambda: [0.0] * 20)
        for o, p in zip(scored, level_pct, strict=True):
            rq = quintile(getattr(o, key))
            if rq not in (0, 4):
                continue
            a = acc[o.month]
            base = quintile(p) * 4 + (0 if rq == 4 else 2)
            a[base] += o.vrp
            a[base + 1] += 1
        return {m: tuple(v) for m, v in acc.items()}

    def within_combine(t: list[float]) -> float | None:
        diffs = []
        for lq in range(5):
            b = lq * 4
            if t[b + 1] and t[b + 3]:
                diffs.append(t[b] / t[b + 1] - t[b + 2] / t[b + 3])
        return statistics.fmean(diffs) if len(diffs) == 5 else None

    terms = within_terms()
    pooled = within_combine([sum(x) for x in zip(*terms.values(), strict=False)])
    plo, phi = month_block_bootstrap(terms, within_combine)
    return {
        "A2a_level_deciles": a2a, "A2a_spearman": a2a_rho,
        "A2b_grid": grid,
        "A2_pooled_within_level_diff": pooled, "A2_ci95": [plo, phi],
        "A2_rank_survives_in_every_level_quintile": survives_everywhere,
        "A2_passes": bool(survives_everywhere and pooled and pooled > 0 and plo == plo and plo > 0),
    }


def conditional_premise(obs: list[Obs], key: str = "ivr_range") -> dict:
    """Addendum A3 — does selling ONLY at high IV rank clear zero, where selling always did not?"""
    top = [o for o in obs if getattr(o, key) is not None and getattr(o, key) >= 0.9]
    if not top:
        return {}
    m = mean_vrp(top)
    lo, hi = month_block_bootstrap(_mean_terms(top), _mean_combine)
    vrps = sorted(o.vrp for o in top)
    return {"n_rows": len(top), "n_symbol_months": effective_n(top), "mean_vrp": m,
            "ci95": [lo, hi], "p05_vrp": vrps[int(0.05 * len(vrps))],
            "share_negative": sum(1 for v in vrps if v < 0) / len(vrps),
            "passes": bool(m and m > 0 and lo == lo and lo > 0)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", help="comma-separated subset (default: whole corpus)")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--addendum", action="store_true",
                    help="also run Addendum A (A1 horizon recovery, A2 level control, A3 conditional)")
    ap.add_argument("--h4", action="store_true",
                    help="run prereg H4: 21-session forward RV from our own bars (needs Alpaca)")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    panel, drop = load_panel(conn, symbols)
    print(f"panel: {len(panel)} observations, {len({o.symbol for o in panel})} symbols, "
          f"{effective_n(panel)} symbol-months")
    print(f"dropped: no_forward_row={drop.no_forward_row} thin_window={drop.thin_trailing_window} "
          f"degenerate_range={drop.degenerate_year_range} null_iv={drop.null_iv_or_hv}")

    # Prereg: every result reported twice, with and without the 2020 vol event.
    ex_covid = [o for o in panel if not (COVID_LO <= o.date <= COVID_HI)]
    results = {
        "prereg": "References/IV_RANK_BASELINE_PREREG_2026-07-25.md",
        "dropped": {k: v for k, v in vars(drop).items() if k != "detail"},
        "full": evaluate(panel, "full sample"),
        "ex_covid_2020": evaluate(ex_covid, "excluding 2020-02-01..2020-04-30"),
    }

    if args.addendum:
        results["addendum"] = _run_addendum(conn, symbols, panel)

    if args.h4:
        # Prereg H4 needs symbols we hold bars for: the chain sampler's own universe,
        # intersected with the corpus (only 37 of 53 are in this curated set).
        from signals.options_chain_snapshot import is_optionable_symbol
        from tools.options_chain_sampler import CORE_ROOTS, all_watchlist_symbols_fresh

        want = sorted({*CORE_ROOTS, *[s for s in all_watchlist_symbols_fresh() if is_optionable_symbol(s)]})
        covered = [s for s in want if conn.execute(
            "SELECT 1 FROM volatility_history WHERE act_symbol=? LIMIT 1", (s,)).fetchone()]
        h4_panel, h4_meta = load_panel_h4(conn, covered)
        h4 = evaluate(h4_panel, "H4: 21-session forward RV from our own bars")
        h4["meta"] = h4_meta
        # Same symbols, vendor 30-session horizon — the like-for-like comparison that
        # isolates the horizon change from the universe change.
        same = [o for o in panel if o.symbol in set(covered)]
        h4["control_same_symbols_vendor_horizon"] = evaluate(same, "same symbols, vendor 30-session horizon") if same else None
        results["H4_horizon_robustness"] = h4

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    _report(results)
    if args.addendum:
        _report_addendum(results["addendum"])
    if args.h4:
        h4 = results["H4_horizon_robustness"]
        print(f"\n\n{'#' * 78}\nH4 HORIZON ROBUSTNESS (pre-registered)\n{'#' * 78}")
        print(f"\n{h4['meta']}")
        for block, name in ((h4, "21-session, our bars"),
                            (h4.get("control_same_symbols_vendor_horizon"), "control: 30-session, vendor")):
            if not block:
                continue
            b1 = block["H1"]
            print(f"\n--- {name}: {block['n_rows']:,} rows, {block['n_symbol_months']:,} symbol-months, "
                  f"{block['n_symbols']} symbols, {block['date_range']}")
            print(f"    H1 {b1['mean_vrp']:+.4f} CI [{b1['ci95'][0]:+.4f}, {b1['ci95'][1]:+.4f}] "
                  f"-> {'PASS' if b1['passes'] else 'FAIL'}")
            for key in ("ivr_range", "ivr_pct"):
                blk = block.get(key, {})
                if "difference" in blk.get("H2", {}):
                    print(f"    {key}: H2 {blk['H2']['difference']:+.4f} "
                          f"CI [{blk['H2']['ci95'][0]:+.4f}, {blk['H2']['ci95'][1]:+.4f}] "
                          f"{'PASS' if blk['H2']['passes'] else 'FAIL'} | H3 rho "
                          f"{blk['H3']['spearman']:+.3f} {'PASS' if blk['H3']['passes'] else 'FAIL'}")
    print(f"\nwrote {out / 'results.json'}")


def _run_addendum(conn: sqlite3.Connection, symbols: list[str] | None, panel: list[Obs]) -> dict:
    """Addendum A — all three arms, each labelled SECONDARY / exploratory."""
    dated, ddrop = load_panel_dated(conn, symbols)
    ex_covid_dated = [o for o in dated if not (COVID_LO <= o.date <= COVID_HI)]
    return {
        "spec": "References/IV_RANK_BASELINE_PREREG_2026-07-25_ADDENDUM_A.md",
        "label": "SECONDARY / exploratory — does not rescore the primary",
        "A1_horizon_recovery": {
            "dropped": {k: v for k, v in vars(ddrop).items() if k != "detail"},
            "full": evaluate(dated, "A1 date-anchored horizon, full sample"),
            "ex_covid_2020": evaluate(ex_covid_dated, "A1 date-anchored, excluding 2020-02..04"),
        },
        "A2_level_control": {
            "primary_panel": level_control(panel),
            "A1_panel": level_control(dated),
        },
        "A3_conditional_premise": {
            "primary_panel": conditional_premise(panel),
            "A1_panel": conditional_premise(dated),
        },
    }


def _report_addendum(a: dict) -> None:
    print(f"\n\n{'#' * 78}\nADDENDUM A — {a['label']}\n{'#' * 78}")
    a1 = a["A1_horizon_recovery"]
    print(f"\nA1 dropped: {a1['dropped']}")
    for arm in ("full", "ex_covid_2020"):
        r = a1[arm]
        h1 = r["H1"]
        print(f"\n--- {r['label']}: {r['n_rows']:,} rows, {r['n_symbol_months']:,} symbol-months, "
              f"{r['n_symbols']} symbols, {r['date_range']}")
        print(f"    H1 mean VRP {h1['mean_vrp']:+.4f} CI [{h1['ci95'][0]:+.4f}, {h1['ci95'][1]:+.4f}] "
              f"-> {'PASS' if h1['passes'] else 'FAIL'}   (p05 {h1['p05_vrp']:+.4f}, "
              f"neg {h1['share_negative']:.1%})")
        for key in ("ivr_range", "ivr_pct"):
            b = r.get(key, {})
            if "difference" in b.get("H2", {}):
                print(f"    {key}: H2 {b['H2']['difference']:+.4f} "
                      f"CI [{b['H2']['ci95'][0]:+.4f}, {b['H2']['ci95'][1]:+.4f}] "
                      f"{'PASS' if b['H2']['passes'] else 'FAIL'} | "
                      f"H3 rho {b['H3']['spearman']:+.3f} {'PASS' if b['H3']['passes'] else 'FAIL'}")

    for panel_name, lc in a["A2_level_control"].items():
        if not lc:
            continue
        print(f"\n--- A2 level control [{panel_name}]")
        print(f"    A2a level-sorted deciles spearman = {lc['A2a_spearman']:+.3f} "
              f"(rank-sorted was the comparison)")
        print("    A2b mean VRP by IV-level quintile (rows) x IV-rank quintile (cols):")
        print(f"        {'':>10}" + "".join(f"{'rank Q' + str(q + 1):>10}" for q in range(5)))
        for lq, row in enumerate(lc["A2b_grid"]):
            cells = "".join(
                f"{c['mean_vrp']:>+10.4f}" if c["mean_vrp"] is not None else f"{'-':>10}"
                for c in row
            )
            print(f"        lvl Q{lq + 1:<5}{cells}")
        print(f"    rank orders VRP within EVERY level quintile: "
              f"{lc['A2_rank_survives_in_every_level_quintile']}")
        pooled = lc["A2_pooled_within_level_diff"]
        if pooled is None:
            # Some level quintile had no populated top- or bottom-rank cell; the
            # pooled difference is undefined rather than zero, and is reported as such.
            print("    pooled within-level (topQ - botQ) = undefined (empty cells) -> A2 FAIL")
        else:
            print(f"    pooled within-level (topQ - botQ) = {pooled:+.4f} "
                  f"CI [{lc['A2_ci95'][0]:+.4f}, {lc['A2_ci95'][1]:+.4f}] "
                  f"-> A2 {'PASS' if lc['A2_passes'] else 'FAIL'}")

    for panel_name, a3 in a["A3_conditional_premise"].items():
        if not a3:
            continue
        print(f"\n--- A3 conditional premise [{panel_name}]: top IV-rank decile only")
        print(f"    n={a3['n_rows']:,} ({a3['n_symbol_months']:,} symbol-months)  "
              f"mean VRP {a3['mean_vrp']:+.4f} CI [{a3['ci95'][0]:+.4f}, {a3['ci95'][1]:+.4f}] "
              f"-> {'PASS' if a3['passes'] else 'FAIL'}")
        print(f"    p05 {a3['p05_vrp']:+.4f}   share negative {a3['share_negative']:.1%}")


def _report(res: dict) -> None:
    for arm in ("full", "ex_covid_2020"):
        r = res[arm]
        print(f"\n{'=' * 78}\n{r['label']}  —  {r['n_rows']:,} rows, "
              f"{r['n_symbol_months']:,} symbol-months, {r['n_symbols']} symbols")
        h1 = r["H1"]
        print(f"\nH1 premise: mean VRP = {h1['mean_vrp']:+.4f} vol pts, "
              f"95% CI [{h1['ci95'][0]:+.4f}, {h1['ci95'][1]:+.4f}] "
              f"-> {'PASS' if h1['passes'] else 'FAIL'}")
        print(f"   median {h1['median_vrp']:+.4f}   p05 {h1['p05_vrp']:+.4f}   "
              f"share negative {h1['share_negative']:.1%}")
        for key, name in (("ivr_range", "IV rank (year range)"), ("ivr_pct", "IV percentile (trailing)")):
            block = r.get(key, {})
            if not block.get("deciles"):
                continue
            print(f"\n   {name}")
            print(f"   {'dec':>3} {'n':>9} {'mean VRP':>9} {'p05':>8} {'worst s-m':>10} {'neg%':>6} "
                  f"{'meanIV':>7} {'meanRVf':>8}")
            for d in block["deciles"]:
                print(f"   {d['decile']:>3} {d['n_rows']:>9,} {d['mean_vrp']:>+9.4f} "
                      f"{d['p05_vrp']:>+8.4f} {d['worst_symbol_month']:>+10.4f} "
                      f"{d['share_negative']:>6.1%} {d['mean_iv']:>7.4f} {d['mean_rv_fwd']:>8.4f}")
            h2, h3 = block.get("H2", {}), block.get("H3", {})
            if "difference" in h2:
                print(f"   H2 top-bottom = {h2['difference']:+.4f} "
                      f"CI [{h2['ci95'][0]:+.4f}, {h2['ci95'][1]:+.4f}] "
                      f"-> {'PASS' if h2['passes'] else 'FAIL'}")
            if "spearman" in h3:
                print(f"   H3 spearman   = {h3['spearman']:+.3f} (>= 0.6) "
                      f"-> {'PASS' if h3['passes'] else 'FAIL'}")


if __name__ == "__main__":
    main()
