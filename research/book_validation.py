"""Combined-book validation pipeline — NAV, CAPM alpha, PBO/DSR gates.

Promoted from gitignored scratch (data/research/_edgehunt/combined_book_nav.py,
capm_alpha.py, hardening_checks23_overfit_regime.py) so the armed book's headline
numbers (Sharpe ~1.42, Jensen alpha ~+6.7%/yr, PBO=0.000) are reproducible and
CI-guarded instead of living only on one PC. Logic is a verbatim transplant —
any intentional change to a sleeve rule must update the golden fixture and the
EDGE_HUNT_FINDINGS entry together.

The armed book (validated 2026-06-26, EDGE_HUNT_FINDINGS "Tier-1 hardening"):
  70% residual momentum (sector-idiosyncratic 12-1, top-quintile EW, monthly)
  20% steep-YC dip (c>SMA200, 3d<-5%, SPY 3d<-1.5%, Y10-Y2>0.5; 10d hold)
  10% VIX-spike reversion (VIX>28 from calm; exit VIX<20 or 60d)
Idle tactical capital earns 0% (conservative). Monthly NAV 2010-01..present.

Three consumers:
  * `python -m research.book_validation` — full run against the local research
    DBs; prints the report; `--write-fixture` refreshes the golden CSV.
  * tests/test_book_validation.py — CI-guarded math tests (synthetic + the
    committed fixture; no DBs needed) + a local integration test (skipped
    where bars.db is absent).
  * downstream research — import compute_stats / capm_regression / gates.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.bars import DEFAULT_BARS_DB_PATH, RESEARCH_DATA_DIR
from research.overfit import deflated_sharpe_ratio, pbo_cscv

DEFAULT_REGIME_DB_PATH = RESEARCH_DATA_DIR / "regime.db"
DEFAULT_FUND_DB_PATH = RESEARCH_DATA_DIR / "fundamentals.db"
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "combined_book_monthly_nav.csv"

# ── Allocation (the armed book) ───────────────────────────────────────────────
W_MOM = 0.70
W_DIP = 0.20
W_VIX = 0.10

# ── Sleeve parameters (identical to the confirmed-edge backtests) ─────────────
MOM_LONG = 252
MOM_SKIP = 21
QUINTILE_FRAC = 0.20
MIN_SECTOR_STOCKS = 3
YC_THRESHOLD = 0.5
VIX_SPIKE_THRESH = 28.0
VIX_CALM_THRESH = 22.0
CALM_MIN_DAYS = 8
CALM_WINDOW = 12
VIX_EXIT_THRESH = 20.0
MAX_HOLD_VIX = 60
COST_BPS = 10  # round-trip bps
START_DATE = "2010-01-01"

# Weight-perturbation configs for the PBO/CSCV gate (pre-specified, baseline first).
PBO_CONFIGS = [
    (0.70, 0.20, 0.10),  # BASELINE (the book)
    (0.60, 0.30, 0.10),
    (0.60, 0.20, 0.20),
    (0.50, 0.40, 0.10),
    (0.50, 0.30, 0.20),
    (0.80, 0.10, 0.10),
    (0.70, 0.30, 0.00),
    (0.70, 0.00, 0.30),
    (1.00, 0.00, 0.00),
    (0.50, 0.50, 0.00),
    (0.80, 0.20, 0.00),
    (0.60, 0.40, 0.00),
]

_delist_pat = re.compile(r"^(.+)-(\d{6})$")


def _delist_month(sym: str) -> str | None:
    m = _delist_pat.match(sym)
    return f"{m.group(2)[:4]}-{m.group(2)[4:6]}" if m else None


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADERS
# ═══════════════════════════════════════════════════════════════════════════════


def load_spy_bars(bars_db: Path = DEFAULT_BARS_DB_PATH) -> tuple[list, list, list]:
    conn = sqlite3.connect(str(bars_db))
    rows = conn.execute(
        "SELECT date, o, c FROM daily_bars WHERE symbol='SPY' ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows], [float(r[1]) for r in rows], [float(r[2]) for r in rows]


def load_vix(regime_db: Path = DEFAULT_REGIME_DB_PATH) -> tuple[list, list]:
    conn = sqlite3.connect(str(regime_db))
    rows = conn.execute(
        "SELECT date, value FROM macro WHERE series='vix' ORDER BY date"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def load_regime(regime_db: Path = DEFAULT_REGIME_DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(str(regime_db))
    y10 = pd.read_sql_query(
        "SELECT date, value AS y10 FROM macro WHERE series='y10'", conn
    ).set_index("date")
    y2 = pd.read_sql_query(
        "SELECT date, value AS y2 FROM macro WHERE series='y2'", conn
    ).set_index("date")
    conn.close()
    reg = y10.join(y2, how="outer").sort_index()
    reg["yc_spread"] = reg["y10"] - reg["y2"]
    return reg.ffill()


def load_rf_monthly(regime_db: Path = DEFAULT_REGIME_DB_PATH) -> pd.Series:
    """Y2 yield (%/yr) -> monthly rf = y2 / 12 / 100, indexed by 'YYYY-MM'."""
    conn = sqlite3.connect(str(regime_db))
    df = pd.read_sql_query(
        "SELECT date, value FROM macro WHERE series='y2' ORDER BY date", conn
    )
    conn.close()
    df["month"] = df["date"].str[:7]
    df = df.sort_values("date").groupby("month", as_index=False).last()
    df["rf"] = df["value"].astype(float) / 12.0 / 100.0
    return df.set_index("month")["rf"]


def load_sector_membership(fund_db: Path = DEFAULT_FUND_DB_PATH) -> dict:
    conn = sqlite3.connect(str(fund_db))
    rows = conn.execute(
        "SELECT symbol, sector FROM companies "
        "WHERE sector IS NOT NULL AND sector != '' AND sector != 'Unknown'"
    ).fetchall()
    conn.close()
    excl = {"Agriculture"}
    return {r[0]: r[1] for r in rows if r[1] not in excl}


def load_stock_bars(syms: list, bars_db: Path = DEFAULT_BARS_DB_PATH) -> list:
    placeholders = ",".join("?" * len(syms))
    conn = sqlite3.connect(str(bars_db))
    rows = conn.execute(
        f"SELECT symbol, date, o, c FROM daily_bars "
        f"WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
        list(syms),
    ).fetchall()
    conn.close()
    return rows


def get_month_ends(spy_dates: list) -> list:
    me: dict = {}
    for d in spy_dates:
        me[d[:7]] = d
    return sorted(me.values())[:-1]


# ═══════════════════════════════════════════════════════════════════════════════
# SLEEVE A — RESIDUAL MOMENTUM (always-on core, 70%)
# ═══════════════════════════════════════════════════════════════════════════════


def run_mom_sleeve(
    symbol_sector, sym_dates, sym_opens, sym_closes, sym_dmap, spy_dates, spy_opens_arr, month_ends
):
    """Returns (period_dates, port_rets_net, spy_rets) lists — one row per month."""
    all_dates = sorted({d for sym in sym_dates for d in sym_dates[sym]})
    global_dmap = {d: i for i, d in enumerate(all_dates)}

    period_dates, period_rets, spy_rets_out = [], [], []
    min_hist = MOM_LONG + MOM_SKIP + 10

    for mi in range(len(month_ends) - 1):
        rank_date = month_ends[mi]
        exit_me = month_ends[mi + 1]

        spy_ri = sym_dmap.get("SPY", {}).get(rank_date)
        if spy_ri is None or spy_ri < min_hist:
            continue

        gidx = global_dmap.get(rank_date)
        if gidx is None or gidx + 1 >= len(all_dates):
            continue
        entry_date = all_dates[gidx + 1]

        gidx_x = global_dmap.get(exit_me)
        if gidx_x is None or gidx_x + 1 >= len(all_dates):
            continue
        exit_date = all_dates[gidx_x + 1]

        stock_mom: dict = {}
        for sym in symbol_sector:
            dm = _delist_month(sym)
            if dm is not None and rank_date[:7] >= dm:
                continue
            dmap = sym_dmap.get(sym)
            if dmap is None:
                continue
            ri = dmap.get(rank_date)
            if ri is None:
                continue
            idx_long = ri - MOM_LONG
            idx_skip = ri - MOM_SKIP
            if idx_long < 0 or idx_skip < 0:
                continue
            c_l = sym_closes[sym][idx_long]
            c_s = sym_closes[sym][idx_skip]
            if c_l <= 0 or c_s <= 0:
                continue
            stock_mom[sym] = (c_s / c_l) - 1.0

        if len(stock_mom) < 10:
            continue

        sec_rets: dict = defaultdict(list)
        for sym, mom in stock_mom.items():
            sec_rets[symbol_sector[sym]].append(mom)
        sec_avg = {s: float(np.mean(v)) for s, v in sec_rets.items() if len(v) >= MIN_SECTOR_STOCKS}

        idio = [
            (mom - sec_avg[symbol_sector[sym]], sym)
            for sym, mom in stock_mom.items()
            if symbol_sector[sym] in sec_avg
        ]
        if len(idio) < 10:
            continue

        idio.sort(reverse=True)
        n_top = max(1, int(len(idio) * QUINTILE_FRAC))
        top_syms = [s for _, s in idio[:n_top]]

        pos_rets = []
        for sym in top_syms:
            dmap = sym_dmap.get(sym)
            if dmap is None:
                continue
            ei = dmap.get(entry_date)
            xi = dmap.get(exit_date)
            if ei is None or xi is None:
                continue
            oe = sym_opens[sym][ei]
            ox = sym_opens[sym][xi]
            if oe <= 0:
                continue
            pos_rets.append((ox / oe) - 1.0)

        if not pos_rets:
            continue

        port_ret_net = float(np.mean(pos_rets)) - COST_BPS / 10_000.0

        spy_ei = sym_dmap.get("SPY", {}).get(entry_date)
        spy_xi = sym_dmap.get("SPY", {}).get(exit_date)
        if spy_ei is not None and spy_xi is not None:
            so_e = sym_opens["SPY"][spy_ei]
            so_x = sym_opens["SPY"][spy_xi]
            spy_ret = (so_x / so_e) - 1.0 if so_e > 0 else float("nan")
        else:
            spy_ret = float("nan")

        period_dates.append(rank_date)
        period_rets.append(port_ret_net)
        spy_rets_out.append(spy_ret)

    return period_dates, period_rets, spy_rets_out


# ═══════════════════════════════════════════════════════════════════════════════
# SLEEVE B — STEEP-YC DIP TACTICAL (20%)
# ═══════════════════════════════════════════════════════════════════════════════


def run_dip_sleeve(
    sym_dates, sym_opens, sym_closes, spy_dates, spy_opens_arr, spy_closes_arr, regime_df
) -> pd.DataFrame:
    """Returns DataFrame: symbol, signal_date, return_pct (net, %), spy_ret (%)."""
    spy_idx_map = {d: i for i, d in enumerate(spy_dates)}
    spy_c = np.array(spy_closes_arr)
    spy_3d = np.full(len(spy_dates), np.nan)
    spy_3d[3:] = (spy_c[3:] - spy_c[:-3]) / spy_c[:-3] * 100

    records = []
    for sym in sym_dates:
        if sym in ("SPY",) or sym.startswith("/"):
            continue
        dates = sym_dates[sym]
        closes = sym_closes[sym]
        opens = sym_opens[sym]
        n = len(dates)
        if n < 215:
            continue

        sma200 = pd.Series(closes).rolling(200, min_periods=200).mean().values
        ret3 = np.full(n, np.nan)
        ret3[3:] = (closes[3:] - closes[:-3]) / closes[:-3] * 100

        for i in range(202, n - 11):
            if np.isnan(sma200[i]) or np.isnan(ret3[i]):
                continue
            if closes[i] <= sma200[i]:
                continue
            if ret3[i] > -5.0:
                continue
            date_t = dates[i]
            if date_t not in spy_idx_map:
                continue
            si = spy_idx_map[date_t]
            if np.isnan(spy_3d[si]) or spy_3d[si] > -1.5:
                continue

            oe = opens[i + 1]
            ox = opens[i + 11]
            if oe <= 0 or ox <= 0:
                continue
            ret10 = (ox / oe - 1.0) * 100

            entry_date = dates[i + 1]
            if entry_date not in spy_idx_map:
                continue
            ei = spy_idx_map[entry_date]
            if ei + 10 >= len(spy_opens_arr):
                continue
            spy_e = spy_opens_arr[ei]
            spy_x = spy_opens_arr[ei + 10]
            if spy_e <= 0:
                continue
            spy_ret10 = (spy_x / spy_e - 1.0) * 100

            records.append(
                {
                    "symbol": sym,
                    "signal_date": date_t,
                    "return_pct": ret10 - COST_BPS / 100.0,
                    "spy_ret": spy_ret10,
                }
            )

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df2 = df.rename(columns={"signal_date": "date"}).copy()
    df2["date"] = pd.to_datetime(df2["date"])
    df2 = df2.sort_values("date").reset_index(drop=True)

    reg_r = regime_df.reset_index().rename(columns={"index": "date"})
    reg_r["date"] = pd.to_datetime(reg_r["date"])

    merged = pd.merge_asof(df2, reg_r[["date", "yc_spread"]], on="date", direction="backward")
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    merged = merged.rename(columns={"date": "signal_date"})

    return merged[merged["yc_spread"] > YC_THRESHOLD].copy().reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SLEEVE C — VIX SPIKE REVERSION (10%)
# ═══════════════════════════════════════════════════════════════════════════════


def run_vix_sleeve(vix_dates, vix_vals, spy_dates, spy_opens_arr):
    """Returns (episodes list, unconditional SPY mean return by hold length)."""
    spy_open_by_date = {spy_dates[i]: spy_opens_arr[i] for i in range(len(spy_dates))}
    n = len(vix_dates)

    episodes: list = []
    in_position = False
    position_exit_idx = -1

    i = CALM_WINDOW
    while i < n:
        if in_position and i <= position_exit_idx:
            i += 1
            continue
        in_position = False

        date_t = vix_dates[i]
        if date_t < START_DATE:
            i += 1
            continue
        if vix_vals[i] <= VIX_SPIKE_THRESH:
            i += 1
            continue

        prior = vix_vals[i - CALM_WINDOW : i]
        if sum(1 for v in prior if v < VIX_CALM_THRESH) < CALM_MIN_DAYS:
            i += 1
            continue

        if i + 1 >= n:
            break
        entry_date = vix_dates[i + 1]
        entry_open = spy_open_by_date.get(entry_date)
        if not entry_open or entry_open <= 0:
            i += 1
            continue

        exit_vix_idx = None
        for k in range(1, MAX_HOLD_VIX + 1):
            idx_k = i + k
            if idx_k >= n:
                exit_vix_idx = n - 1
                break
            if vix_vals[idx_k] < VIX_EXIT_THRESH:
                exit_vix_idx = idx_k
                break
        if exit_vix_idx is None:
            exit_vix_idx = min(i + MAX_HOLD_VIX, n - 1)

        exit_fill_idx = min(exit_vix_idx + 1, n - 1)
        exit_fill_date = vix_dates[exit_fill_idx]
        exit_open = spy_open_by_date.get(exit_fill_date)
        if not exit_open or exit_open <= 0:
            i += 1
            continue

        strat_ret = (exit_open - entry_open) / entry_open
        hold_days = max(exit_fill_idx - (i + 1), 1)

        episodes.append(
            {
                "signal_date": date_t,
                "entry_date": entry_date,
                "hold_days": hold_days,
                "strategy_ret": strat_ret,
                "entry_month": entry_date[:7],
            }
        )

        in_position = True
        position_exit_idx = exit_fill_idx
        i = exit_fill_idx + 1

    rets_by_len: dict = defaultdict(list)
    for k in range(len(spy_dates)):
        if spy_dates[k] < START_DATE:
            continue
        for length in range(1, MAX_HOLD_VIX + 6):
            j = k + length
            if j >= len(spy_opens_arr) or spy_opens_arr[k] <= 0:
                break
            rets_by_len[length].append((spy_opens_arr[j] - spy_opens_arr[k]) / spy_opens_arr[k])
    uncond_spy = {length: float(np.mean(v)) for length, v in rets_by_len.items() if v}

    return episodes, uncond_spy


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINE INTO MONTHLY NAV
# ═══════════════════════════════════════════════════════════════════════════════


def build_monthly_nav(
    mom_dates,
    mom_rets,
    spy_monthly_rets,
    dip_df,
    vix_episodes,
    month_ends,
    spy_dates,
    spy_opens_arr,
) -> pd.DataFrame:
    """book_ret_M = W_MOM*mom + W_DIP*dip + W_VIX*vix; idle tactical sleeves earn 0."""
    mom_by_month = {d[:7]: (r, s) for d, r, s in zip(mom_dates, mom_rets, spy_monthly_rets)}

    dip_by_month: dict = defaultdict(list)
    if not dip_df.empty:
        for _, row in dip_df.iterrows():
            dip_by_month[str(row["signal_date"])[:7]].append(float(row["return_pct"]))

    vix_by_month: dict = defaultdict(list)
    for ep in vix_episodes:
        vix_by_month[ep["entry_month"]].append(ep["strategy_ret"])

    spy_open_by_date = {spy_dates[k]: spy_opens_arr[k] for k in range(len(spy_dates))}

    rows = []
    for mi in range(len(month_ends) - 1):
        me_this = month_ends[mi]
        me_next = month_ends[mi + 1]
        if me_this < START_DATE:
            continue

        month_key = me_this[:7]

        mom_r_spy = mom_by_month.get(month_key)
        if mom_r_spy is None:
            continue
        mom_r, spy_r = mom_r_spy

        dip_trades = dip_by_month.get(month_key, [])
        dip_r = float(np.mean(dip_trades)) / 100.0 if dip_trades else 0.0

        vix_eps = vix_by_month.get(month_key, [])
        vix_r = float(np.mean(vix_eps)) - COST_BPS / 10_000.0 if vix_eps else 0.0

        book_r = W_MOM * mom_r + W_DIP * dip_r + W_VIX * vix_r

        if spy_r is None or np.isnan(spy_r):
            try:
                idx_this = spy_dates.index(me_this)
                idx_next = spy_dates.index(me_next)
                e_date = spy_dates[idx_this + 1] if idx_this + 1 < len(spy_dates) else None
                x_date = spy_dates[idx_next + 1] if idx_next + 1 < len(spy_dates) else None
                if e_date and x_date:
                    so_e = spy_open_by_date.get(e_date, 0)
                    so_x = spy_open_by_date.get(x_date, 0)
                    spy_r = (so_x / so_e) - 1.0 if so_e > 0 else float("nan")
                else:
                    spy_r = float("nan")
            except (ValueError, IndexError):
                spy_r = float("nan")

        rows.append(
            {
                "month": month_key,
                "book_ret": book_r,
                "spy_ret": spy_r,
                "mom_ret": mom_r,
                "dip_ret": dip_r,
                "vix_ret": vix_r,
                "n_dip": len(dip_trades),
                "n_vix": len(vix_eps),
            }
        )

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# STATS / CAPM / GATES (the math CI actually guards)
# ═══════════════════════════════════════════════════════════════════════════════


def compute_stats(monthly_df: pd.DataFrame, label: str = "FULL") -> dict | None:
    """CAGR / Sharpe / Sortino / maxDD / corr / rolling-12m beat-rate from monthly rows."""
    df = monthly_df.dropna(subset=["book_ret", "spy_ret"]).copy()
    n = len(df)
    if n < 12:
        return None

    book_arr = df["book_ret"].values
    spy_arr = df["spy_ret"].values

    book_nav = np.cumprod(1 + book_arr)
    spy_nav = np.cumprod(1 + spy_arr)

    n_years = n / 12.0
    book_cagr = book_nav[-1] ** (1 / n_years) - 1
    spy_cagr = spy_nav[-1] ** (1 / n_years) - 1

    book_vol = float(np.std(book_arr, ddof=1)) * np.sqrt(12)
    spy_vol = float(np.std(spy_arr, ddof=1)) * np.sqrt(12)

    book_sharpe = (float(np.mean(book_arr)) * 12) / book_vol if book_vol > 0 else float("nan")
    spy_sharpe = (float(np.mean(spy_arr)) * 12) / spy_vol if spy_vol > 0 else float("nan")

    neg = book_arr[book_arr < 0]
    down_vol = float(np.std(neg, ddof=1)) * np.sqrt(12) if len(neg) > 1 else float("nan")
    book_sortino = (float(np.mean(book_arr)) * 12) / down_vol if down_vol and down_vol > 0 else float("nan")

    roll_max_b = np.maximum.accumulate(book_nav)
    book_maxdd = float(np.min((book_nav - roll_max_b) / roll_max_b))
    roll_max_s = np.maximum.accumulate(spy_nav)
    spy_maxdd = float(np.min((spy_nav - roll_max_s) / roll_max_s))

    corr = float(np.corrcoef(book_arr, spy_arr)[0, 1])

    roll_wins = roll_total = 0
    for start in range(n - 11):
        book_12 = float(np.prod(1 + book_arr[start : start + 12]))
        spy_12 = float(np.prod(1 + spy_arr[start : start + 12]))
        roll_total += 1
        if book_12 > spy_12:
            roll_wins += 1
    pct_beat = roll_wins / roll_total if roll_total > 0 else float("nan")

    return {
        "label": label,
        "n_months": n,
        "book_cagr": book_cagr,
        "spy_cagr": spy_cagr,
        "book_vol": book_vol,
        "spy_vol": spy_vol,
        "book_sharpe": book_sharpe,
        "spy_sharpe": spy_sharpe,
        "book_sortino": book_sortino,
        "book_maxdd": book_maxdd,
        "spy_maxdd": spy_maxdd,
        "corr": corr,
        "pct_rolling12m_beat_spy": pct_beat,
        "roll_wins": roll_wins,
        "roll_total": roll_total,
    }


def capm_regression(book_ret: np.ndarray, spy_ret: np.ndarray, rf: np.ndarray, label: str = "") -> dict:
    """OLS excess_book ~ alpha + beta*excess_spy; Newey-West SEs when statsmodels exists.

    Returns monthly + geometric-annualised Jensen alpha, t-stat, p-value, beta, R².
    """
    y = np.asarray(book_ret, dtype=float) - np.asarray(rf, dtype=float)
    x = np.asarray(spy_ret, dtype=float) - np.asarray(rf, dtype=float)
    n = len(y)

    x_c = np.column_stack([np.ones(n), x])
    coeffs, _, _, _ = np.linalg.lstsq(x_c, y, rcond=None)
    alpha_m = float(coeffs[0])
    beta_coef = float(coeffs[1])

    y_hat = x_c @ coeffs
    resid = y - y_hat
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    alpha_se = beta_se = t_stat = p_val = None
    method = "OLS"
    try:
        import scipy.stats
        import statsmodels.api as sm
        import statsmodels.stats.sandwich_covariance as sw

        res_sm = sm.OLS(y, x_c).fit()
        n_lags = max(1, int(np.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))
        cov_nw = sw.cov_hac(res_sm, nlags=n_lags)
        se_nw = np.sqrt(np.diag(cov_nw))
        alpha_se = float(se_nw[0])
        beta_se = float(se_nw[1])
        t_stat = alpha_m / alpha_se
        p_val = 2.0 * (1.0 - scipy.stats.t.cdf(abs(t_stat), df=n - 2))
        method = f"Newey-West (HAC, lags={n_lags})"
    except Exception:
        try:
            import scipy.stats

            s2 = ss_res / max(n - 2, 1)
            xtxi = np.linalg.inv(x_c.T @ x_c)
            se_v = np.sqrt(np.diag(s2 * xtxi))
            alpha_se = float(se_v[0])
            beta_se = float(se_v[1])
            t_stat = alpha_m / alpha_se
            p_val = 2.0 * (1.0 - scipy.stats.t.cdf(abs(t_stat), df=n - 2))
            method = "OLS (plain SEs — statsmodels unavailable)"
        except Exception:
            method = "OLS (no scipy/statsmodels)"

    alpha_annual = (1.0 + alpha_m) ** 12 - 1.0
    alpha_annual_se = (12.0 * (1.0 + alpha_m) ** 11.0 * alpha_se) if alpha_se is not None else None

    return {
        "label": label,
        "n": n,
        "method": method,
        "alpha_monthly": alpha_m,
        "alpha_annual": alpha_annual,
        "alpha_se": alpha_se,
        "alpha_annual_se": alpha_annual_se,
        "t_stat": t_stat,
        "p_val": p_val,
        "beta": beta_coef,
        "beta_se": beta_se,
        "r2": r2,
    }


def bootstrap_sharpe_ci(
    monthly_rets: np.ndarray,
    *,
    spy_sharpe_benchmark: float,
    block_size: int = 3,
    n_boot: int = 5000,
    seed: int = 42,
) -> dict:
    """Circular moving-block bootstrap of the annualised Sharpe (block ~1 quarter)."""
    arr = np.asarray(monthly_rets, dtype=float)
    n = len(arr)
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block_size))

    boot_sharpes = np.empty(n_boot)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        indices = np.concatenate([(start + np.arange(block_size)) % n for start in starts])[:n]
        sample = arr[indices]
        mu = float(np.mean(sample))
        sig = float(np.std(sample, ddof=1))
        boot_sharpes[b] = (mu * 12) / (sig * np.sqrt(12)) if sig > 0 else 0.0

    return {
        "observed_sharpe": float(np.mean(arr) * 12 / (np.std(arr, ddof=1) * np.sqrt(12))),
        "ci_lo": float(np.percentile(boot_sharpes, 2.5)),
        "ci_hi": float(np.percentile(boot_sharpes, 97.5)),
        "p_le_spy": float(np.mean(boot_sharpes <= spy_sharpe_benchmark)),
        "n_boot": n_boot,
        "block_size": block_size,
    }


def overfit_gates(monthly_df: pd.DataFrame, *, n_trials_grid: tuple = (10, 40, 76, 100)) -> dict:
    """PBO/CSCV over the pre-specified weight configs + DSR on the baseline book."""
    per_sleeve = monthly_df[["mom_ret", "dip_ret", "vix_ret"]].values
    n_months = len(monthly_df)
    config_matrix = np.zeros((n_months, len(PBO_CONFIGS)))
    for j, (wm, wd, wv) in enumerate(PBO_CONFIGS):
        config_matrix[:, j] = wm * per_sleeve[:, 0] + wd * per_sleeve[:, 1] + wv * per_sleeve[:, 2]

    pbo_result = pbo_cscv(config_matrix, n_splits=16)

    book_arr = monthly_df["book_ret"].values
    per_obs_sharpes = [
        float(np.mean(config_matrix[:, j])) / float(np.std(config_matrix[:, j], ddof=1))
        for j in range(len(PBO_CONFIGS))
    ]
    sr_var = float(np.var(per_obs_sharpes, ddof=1))
    dsr_by_trials = {
        n_trials: deflated_sharpe_ratio(book_arr, n_trials=n_trials, sr_variance=sr_var)
        for n_trials in n_trials_grid
    }

    return {
        "pbo": pbo_result["pbo"],
        "median_logit": pbo_result["median_logit"],
        "n_combinations": pbo_result["n_combinations"],
        "sr_variance": sr_var,
        "dsr_by_trials": dsr_by_trials,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE (requires the local research DBs)
# ═══════════════════════════════════════════════════════════════════════════════


def run_pipeline(
    *,
    bars_db: Path = DEFAULT_BARS_DB_PATH,
    regime_db: Path = DEFAULT_REGIME_DB_PATH,
    fund_db: Path = DEFAULT_FUND_DB_PATH,
) -> pd.DataFrame:
    """Data -> sleeves -> monthly NAV DataFrame (with rf column for CAPM)."""
    spy_dates, spy_opens_arr, spy_closes_arr = load_spy_bars(bars_db)
    vix_dates, vix_vals = load_vix(regime_db)
    regime_df = load_regime(regime_db)
    symbol_sector = load_sector_membership(fund_db)

    all_syms = list(symbol_sector.keys()) + ["SPY"]
    bar_rows = load_stock_bars(all_syms, bars_db)

    sym_bars: dict = defaultdict(list)
    for r in bar_rows:
        sym_bars[r[0]].append((r[1], float(r[2]), float(r[3])))

    sym_dates_d: dict = {}
    sym_opens_d: dict = {}
    sym_closes_d: dict = {}
    sym_dmap: dict = {}
    for sym, bars in sym_bars.items():
        bars.sort()
        sym_dates_d[sym] = [b[0] for b in bars]
        sym_opens_d[sym] = np.array([b[1] for b in bars])
        sym_closes_d[sym] = np.array([b[2] for b in bars])
        sym_dmap[sym] = {d: i for i, d in enumerate(sym_dates_d[sym])}

    if "SPY" not in sym_dates_d:
        sym_dates_d["SPY"] = spy_dates
        sym_opens_d["SPY"] = np.array(spy_opens_arr)
        sym_closes_d["SPY"] = np.array(spy_closes_arr)
        sym_dmap["SPY"] = {d: i for i, d in enumerate(spy_dates)}

    month_ends = get_month_ends(spy_dates)

    mom_dates, mom_rets, spy_monthly_rets = run_mom_sleeve(
        symbol_sector, sym_dates_d, sym_opens_d, sym_closes_d, sym_dmap,
        spy_dates, spy_opens_arr, month_ends,
    )
    dip_df = run_dip_sleeve(
        sym_dates_d, sym_opens_d, sym_closes_d, spy_dates, spy_opens_arr, spy_closes_arr, regime_df
    )
    vix_episodes, _uncond = run_vix_sleeve(vix_dates, vix_vals, spy_dates, spy_opens_arr)

    monthly = build_monthly_nav(
        mom_dates, mom_rets, spy_monthly_rets, dip_df, vix_episodes,
        month_ends, spy_dates, spy_opens_arr,
    )

    rf = load_rf_monthly(regime_db)
    monthly["rf"] = monthly["month"].map(rf).ffill().fillna(0.0)
    return monthly


def print_report(monthly: pd.DataFrame) -> None:
    full = compute_stats(monthly, "FULL SAMPLE")
    robust = compute_stats(
        monthly[~monthly["month"].str[:4].isin({"2020", "2026"})].copy(), "Ex-2020+2026"
    )
    for s in (full, robust):
        if s is None:
            continue
        print(f"\n[{s['label']}] n={s['n_months']} months")
        print(f"  Book CAGR {s['book_cagr']*100:+.2f}%  SPY {s['spy_cagr']*100:+.2f}%")
        print(f"  Book Sharpe {s['book_sharpe']:.3f}  SPY {s['spy_sharpe']:.3f}")
        print(f"  Book maxDD {s['book_maxdd']*100:.2f}%  SPY {s['spy_maxdd']*100:.2f}%")
        print(f"  Roll-12m beat SPY {s['pct_rolling12m_beat_spy']*100:.1f}%")

    clean = monthly.dropna(subset=["book_ret", "spy_ret"])
    capm = capm_regression(
        clean["book_ret"].values, clean["spy_ret"].values, clean["rf"].values, "book vs SPY"
    )
    print(f"\n[CAPM] {capm['method']}")
    print(f"  Jensen alpha {capm['alpha_annual']*100:+.2f}%/yr  beta {capm['beta']:.3f}  "
          f"t {capm['t_stat']:.2f}  p {capm['p_val']:.4f}" if capm["t_stat"] is not None else
          f"  Jensen alpha {capm['alpha_annual']*100:+.2f}%/yr  beta {capm['beta']:.3f}")

    gates = overfit_gates(clean)
    print(f"\n[Overfit gates] PBO {gates['pbo']:.4f} ({gates['n_combinations']} CSCV combos)")
    for n_trials, res in gates["dsr_by_trials"].items():
        print(f"  DSR @ n_trials={n_trials}: {res['dsr']:.4f}")

    if full:
        bs = bootstrap_sharpe_ci(
            clean["book_ret"].values, spy_sharpe_benchmark=full["spy_sharpe"]
        )
        print(f"\n[Bootstrap Sharpe] observed {bs['observed_sharpe']:.3f}  "
              f"95% CI [{bs['ci_lo']:.3f}, {bs['ci_hi']:.3f}]  "
              f"P(<=SPY {full['spy_sharpe']:.2f}) {bs['p_le_spy']*100:.1f}%")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Combined-book validation pipeline")
    parser.add_argument("--write-fixture", action="store_true",
                        help=f"Refresh the golden fixture at {FIXTURE_PATH}")
    args = parser.parse_args(argv)

    monthly = run_pipeline()
    print(f"Monthly rows: {len(monthly)} ({monthly['month'].iloc[0]}..{monthly['month'].iloc[-1]})")
    print_report(monthly)

    if args.write_fixture:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        monthly.to_csv(FIXTURE_PATH, index=False, float_format="%.10g")
        print(f"\nGolden fixture written: {FIXTURE_PATH} ({len(monthly)} rows)")


if __name__ == "__main__":
    main()
