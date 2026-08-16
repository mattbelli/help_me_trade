"""Regime-local paper sleeves — generals_dip + vol_event (propose-only, Tier-B).

Spec: References/REGIME_SLEEVES_SPEC.md. Evidence standard: these sleeves are
REGIME-LOCAL — qualified on 2021–2026 (mostly last-24m) evidence, NOT tier-1 validated
edges. They ship force-created DISABLED, entry_mode=propose, paper account only, must
never appear in strategy_stats.json, and every proposal carries the committed Tier-B
`regime_stats` block (signals.strategy_stats.REGIME_LOCAL_CONVICTION) with win_prob=None.

Runs on its own POST-CLOSE loop (api.scan_jobs._regime_sleeves_loop): signals are evaluated
on completed daily bars after the 16:00 ET close; proposals target next-open entry
(phone approval overnight → market order fills at the open). Every proposal, suppressed
trigger, and kill-line check is journaled (db.decision_journal). Mechanical kill lines
auto-disable through the EXISTING strategy-state mechanism (db.strategies enabled flag
for the sleeve; params_json trigger flags for vol_event triggers) and notify the user.

Exits:
- generals_dip: handled by the existing exit engine — trading/exit_engine maps the id
  to the dip evaluator, and the seeded None stop/target/trail legs leave a pure
  10-trading-day time cap (the steepyc_dip pattern). exit_mode=propose.
- vol_event: managed HERE (the exit engine skips unknown kinds): drawdown_stab exits
  on SPY close recovering the trailing 20d-high that defined the drawdown (30td cap);
  term_flip is a fixed 21-trading-day hold. Sell-side phone proposals only; the same
  pending_exit marker format as the exit engine so closure reconcile + the decision
  journal resolve exactly as they do for every other strategy. Exit management and
  per-episode kill checks are NEVER gated — they run even while the sleeve is
  disabled or agent proposals are paused (mirrors trading/exit_engine, which
  ignores strategy.enabled); only entries are gated.
"""

from __future__ import annotations

import logging
import uuid
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import floor, isfinite
from pathlib import Path
from typing import Any

from config.settings import is_kill_switch_active, trading_config
from db.accounts import default_execution_account, get_account
from db.base import DEFAULT_DB_PATH, now_iso
from db.decision_journal import by_strategy, record_decision, set_decision
from db.managed_positions import list_open_positions, update_exit_state
from db.order_executions import get_execution
from db.paper_orders import (
    get_proposal,
    list_paper_order_proposals,
    list_pending_proposals,
)
from db.regime import recent_observations
from db.session_node import claim, forget, get_payload, mark
from db.sqlite import get_latest_snapshot
from db.strategies import get_strategy, update_strategy
from research.strategy_features import _trading_days_until
from signals.indicators import pct_change, sma
from signals.notify import notify
from signals.regime import VIX3M_SERIES, vix3m_is_fresh
from signals.strategy_universe import resolve_universe_spec
from signals.trade_context import DatedBar
from trading.market_hours import _ET, is_market_open

logger = logging.getLogger(__name__)

GENERALS_ID = "generals_dip"
VOL_EVENT_ID = "vol_event"
VIX_SPIKE_ID = "vix_spike"  # counted (read-only) toward the vol-event cluster cap
REGIME_SLEEVE_IDS = (GENERALS_ID, VOL_EVENT_ID)
SPY = "SPY"

SLEEVE_TAG = "sleeve=regime_local"

_UNIVERSE_SIZE = 30
_DOLLAR_VOL_WINDOW_TD = 60
_GENERALS_MAX_CONCURRENT = 5
_GENERALS_BAR_DAYS = 120  # calendar days — covers SMA50 (~70 td) with buffer
_UNIVERSE_BAR_DAYS = 150  # 60 td before prior month-end + up to a month since
_SPY_BAR_DAYS = 150  # covers the 30td vol-event hold + 20d-high window + kill checks
_PROPOSAL_TTL_SECONDS = 18 * 3600  # evening proposal must survive to the next open
_BATCH = 20

# Universe ETF denylist — the sp500 source list is single names by construction, but
# the spec says "excluding ETFs (SPY etc.)" so guard belt-and-suspenders.
_ETF_DENYLIST = frozenset(
    {"SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI", "RSP",
     "GLD", "SLV", "TLT", "HYG", "LQD", "EEM", "EFA", "SMH",
     "XLF", "XLK", "XLE", "XLV", "ARKK", "TQQQ", "SQQQ", "SOXL", "UVXY", "VXX"}
)

# vol_event triggers (trigger 1 of the family is the EXISTING vix_spike strategy —
# deliberately NOT rewritten here; it stays as-is and counts toward the cluster cap).
TRIGGER_DRAWDOWN_STAB = "drawdown_stab"
TRIGGER_TERM_FLIP = "term_flip"

# Kill-line parameters (pre-registered in the spec — do not tune casually).
_KILL_EXCESS_WINDOW_DAYS = 183  # trailing ~6 months
_KILL_WIN_RATE_TRADES = 60
_KILL_WIN_RATE_MIN = 0.45
_KILL_DD_MAE = -0.15
_KILL_DD_CONSECUTIVE_LOSSES = 3
_KILL_TF_EPISODES = 10
_KILL_TF_MIN_MEAN_RET = 0.02

# session_node_events KV keys (existing generic claim/mark store — no new state system)
_UNIVERSE_KEY = f"regime:universe:{GENERALS_ID}"
_STATUS_KEY = "regime:status"
_GENERALS_KILL_KEY = f"regime:kill:{GENERALS_ID}"


# --- pure signal logic (tested directly) --------------------------------------------


def compute_top30_universe(
    bars_by_symbol: dict[str, list[DatedBar]],
    *,
    as_of: str,
    size: int = _UNIVERSE_SIZE,
    window: int = _DOLLAR_VOL_WINDOW_TD,
    exclude: frozenset[str] = _ETF_DENYLIST,
) -> list[str]:
    """Top-`size` symbols by `window`-day average dollar volume, POINT-IN-TIME.

    Only bars dated <= `as_of` (the prior month-end) are used, so a mid-month volume
    explosion can never retroactively promote a name into the current month's list.
    Symbols with fewer than `window` qualifying bars are excluded (fail-closed).
    """
    scored: list[tuple[float, str]] = []
    for sym, bars in bars_by_symbol.items():
        s = str(sym).strip().upper()
        if not s or s in exclude:
            continue
        past = [b for b in bars if b["d"] <= as_of]
        if len(past) < window:
            continue
        recent = past[-window:]
        avg_dollar_vol = sum(float(b["c"]) * float(b["v"]) for b in recent) / window
        scored.append((avg_dollar_vol, s))
    scored.sort(reverse=True)
    return [s for _, s in scored[:size]]


def is_generals_dip_entry(
    close_values: list[float],
    *,
    sma_window: int = 50,
    ret_days: int = 3,
    ret_3d_max: float = -3.5,
) -> tuple[bool, float | None]:
    """close > SMA50 AND 3d return <= -3.5%, on the LAST close. Returns (fired, ret_3d)."""
    ret = pct_change(close_values, ret_days)
    if ret is None:
        return False, None
    sma_val = sma(close_values, sma_window)
    if sma_val is None:
        return False, ret
    return (close_values[-1] > sma_val and ret <= ret_3d_max), ret


def is_drawdown_stab_entry(
    closes: list[float],
    lows: list[float],
    dates: list[str],
    vix_lookup,
    *,
    drawdown_pct: float = 5.0,
    vix_min: float = 25.0,
    high_window: int = 20,
) -> tuple[bool, float | None]:
    """SPY drawdown-stabilization entry at the LAST bar — FIRST qualifying day only.

    Fires iff: close is >= drawdown_pct below its rolling `high_window`-day high close,
    VIX >= vix_min, it's a stabilization day (close > prior close AND low > prior low),
    and NO earlier day of the same uninterrupted drawdown stretch already qualified.
    `vix_lookup(date)` returns the latest VIX close <= date (None ⇒ fail-closed).
    Returns (fired, recovery_level) — the 20d-high close that defined the drawdown.
    """
    n = len(closes)
    if n < high_window + 1 or len(lows) != n or len(dates) != n:
        return False, None

    def hi(i: int) -> float:
        return max(closes[max(0, i - high_window + 1): i + 1])

    def in_drawdown(i: int) -> bool:
        if i < high_window - 1:
            return False
        return closes[i] <= (1.0 - drawdown_pct / 100.0) * hi(i)

    def qualifies(i: int) -> bool:
        if i < 1 or not in_drawdown(i):
            return False
        vix = vix_lookup(dates[i])
        if vix is None or vix < vix_min:
            return False
        return closes[i] > closes[i - 1] and lows[i] > lows[i - 1]

    t = n - 1
    if not qualifies(t):
        return False, None
    j = t - 1
    while j >= 1 and in_drawdown(j):
        if qualifies(j):
            return False, None  # not the first qualifying day of this drawdown
        j -= 1
    return True, hi(t)


def is_term_flip_entry(
    ratios: list[float],
    *,
    flip_level: float = 1.0,
    min_inverted_days: int = 2,
) -> bool:
    """First day back <= 1.0 after >= min_inverted_days consecutive days of
    VIX/VIX3M > 1.0 (close basis). `ratios` is ascending; evaluated at the last value."""
    if len(ratios) < min_inverted_days + 1:
        return False
    if ratios[-1] > flip_level:
        return False
    return all(r > flip_level for r in ratios[-1 - min_inverted_days:-1])


# --- shared plumbing ------------------------------------------------------------------


def _in_post_close_window(t: datetime) -> bool:
    """Mon–Fri, 16:05 ET → midnight — after today's close, before the next session."""
    et = t.astimezone(_ET)
    if et.weekday() >= 5 or is_market_open(et):
        return False
    return (et.hour, et.minute) >= (16, 5)


async def _fetch_daily_bars(
    symbols: list[str], *, days: int
) -> dict[str, list[DatedBar]]:
    """Daily bars via the market-data provider, as date-ascending DatedBars."""
    from brokers.factory import get_market_data_provider
    from signals.strategy_scan import _stock_bars_to_dated

    provider = get_market_data_provider()
    out: dict[str, list[DatedBar]] = {}
    cleaned = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    for start in range(0, len(cleaned), _BATCH):
        chunk = cleaned[start : start + _BATCH]
        try:
            raw = await provider.aget_stock_bars_batch(
                chunk, timeframe="1Day", days=days
            )
        except Exception:
            logger.exception("Regime-sleeve bar fetch failed for %s", chunk)
            continue
        for sym, bars in (raw or {}).items():
            try:
                out[str(sym).upper()] = _stock_bars_to_dated(list(bars or []))
            except Exception:
                logger.exception("Failed to convert bars for %s", sym)
    return out


def _resolve_account(
    strategy: dict[str, Any], *, db_path: Path
) -> dict[str, Any] | None:
    """Bound account, else the default execution account (session-node pattern)."""
    account_id = strategy.get("account_id")
    if account_id:
        account = get_account(str(account_id), db_path=db_path)
    else:
        account = default_execution_account(db_path=db_path)
    if account is None or not account.get("execution_capable"):
        return None
    return account


def _journal_event(
    *,
    strategy_id: str,
    rule_id: str,
    decision: str,
    thesis: str,
    symbol: str = SPY,
    db_path: Path = DEFAULT_DB_PATH,
) -> str | None:
    """Synthetic decision-journal row for a non-proposal event (suppressed trigger /
    kill-line check). qty=0 / order_type='event' — nothing here ever trades.
    Fail-soft: a journal hiccup must never break the scan."""
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    try:
        record_decision(
            proposal_id=event_id,
            symbol=symbol,
            side="none",
            qty="0",
            order_type="event",
            source=f"strategy:{strategy_id}",
            strategy_id=strategy_id,
            rule_id=rule_id,
            thesis=f"{thesis} · {SLEEVE_TAG} trigger_id={rule_id}",
            db_path=db_path,
        )
        set_decision(event_id, decision=decision, decided_by="system", db_path=db_path)
        return event_id
    except Exception:
        logger.exception("Regime-sleeve journal event failed (%s %s)", strategy_id, rule_id)
        return None


async def _propose_entry(
    *,
    symbol: str,
    close: float,
    notional_usd: float,
    account_id: str,
    strategy_id: str,
    rule_id: str,
    thesis: str,
    db_path: Path,
) -> dict[str, Any] | None:
    """Sized next-open BUY phone proposal. Sizing off the signal close (the proposal is
    created after hours — no live quote); RiskEngine still gates at approval time."""
    from trading.proposals import create_phone_proposal

    # isfinite: NaN passes a bare `<= 0` check, then Decimal('nan') makes floor()
    # raise and abort the whole pass — dropping every other valid candidate.
    if not isfinite(close) or close <= 0:
        return None
    qty = int(floor(Decimal(str(notional_usd)) / Decimal(str(close))))
    if qty < 1:
        return None
    row = await create_phone_proposal(
        {
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "order_type": "market",
            "account_id": account_id,
        },
        source=f"strategy:{strategy_id}",
        rule_id=rule_id,
        thesis=thesis,
        entry_ref_price=str(close),
        ttl_seconds=_PROPOSAL_TTL_SECONDS,
        db_path=db_path,
    )
    if not row.get("id") or row.get("error"):
        logger.warning("Regime-sleeve proposal failed for %s: %s", symbol, row)
        return None
    return row


def _close_lookup(bars: list[DatedBar]):
    """date → latest close ON OR BEFORE date (None when before the first bar)."""
    dates = [b["d"] for b in bars]
    closes = [float(b["c"]) for b in bars]

    def lookup(d: str) -> float | None:
        i = bisect_right(dates, d)
        return closes[i - 1] if i > 0 else None

    return lookup


def _obs_lookup(observations: list[tuple[str, float]]):
    """date → latest observation ON OR BEFORE date (FRED publishes with ~1-day lag)."""
    rows = sorted(observations)
    dates = [d for d, _ in rows]
    values = [v for _, v in rows]

    def lookup(d: str) -> float | None:
        i = bisect_right(dates, d)
        return values[i - 1] if i > 0 else None

    return lookup


# --- generals_dip ---------------------------------------------------------------------


def _prior_month_end(eval_date: str) -> str:
    first = date.fromisoformat(eval_date[:10]).replace(day=1)
    return (first - timedelta(days=1)).isoformat()


async def _current_universe(eval_date: str, *, db_path: Path) -> list[str]:
    """This month's point-in-time top-30 (refreshed at the first post-close scan of a
    new month, computed from bars <= the prior month-end)."""
    month = eval_date[:7]
    state = get_payload(_UNIVERSE_KEY, db_path=db_path)
    if state.get("month") == month and state.get("symbols"):
        return [str(s) for s in state["symbols"]]

    as_of = _prior_month_end(eval_date)
    source = resolve_universe_spec("sp500")
    bars = await _fetch_daily_bars(source, days=_UNIVERSE_BAR_DAYS)
    symbols = compute_top30_universe(bars, as_of=as_of)
    if not symbols:
        logger.warning("generals_dip universe refresh produced 0 symbols — keeping prior")
        return [str(s) for s in state.get("symbols") or []]
    mark(
        _UNIVERSE_KEY,
        "universe",
        payload={"month": month, "as_of": as_of, "symbols": symbols},
        db_path=db_path,
    )
    logger.info("generals_dip universe refreshed for %s (as of %s): %s", month, as_of, symbols)
    return symbols


async def _generals_kill_check(
    strategy: dict[str, Any], eval_date: str, *, db_path: Path
) -> bool:
    """Monthly mechanical kill line. Returns True if the sleeve was auto-disabled.

    Trips when trailing-6-month mean realized excess vs SPY < 0, OR the trailing-60-
    trade win rate < 45% (win-rate leg only once a full 60-trade window exists).
    The check result is journaled EVERY month, pass or fail.
    """
    month = eval_date[:7]
    state = get_payload(_GENERALS_KILL_KEY, db_path=db_path)
    if state.get("last_checked_month") == month:
        return False

    rows = by_strategy(GENERALS_ID, limit=500, db_path=db_path)  # newest first
    resolved = [
        r
        for r in rows
        if r.get("outcome") in ("win", "loss")
        and r.get("entry_fill_price")
        and r.get("exit_fill_price")
    ]

    cutoff = (date.fromisoformat(eval_date[:10]) - timedelta(days=_KILL_EXCESS_WINDOW_DAYS)).isoformat()
    window_rows = [r for r in resolved if str(r.get("resolved_at") or "")[:10] >= cutoff]

    excess_vals: list[float] = []
    if window_rows:
        spy_bars = (await _fetch_daily_bars([SPY], days=300)).get(SPY) or []
        spy_close = _close_lookup(spy_bars)
        for r in window_rows:
            try:
                entry = float(r["entry_fill_price"])
                exitp = float(r["exit_fill_price"])
                spy_e = spy_close(str(r.get("created_at"))[:10])
                spy_x = spy_close(str(r.get("resolved_at"))[:10])
            except (TypeError, ValueError):
                continue
            if entry <= 0 or spy_e is None or spy_x is None or spy_e <= 0:
                continue
            excess_vals.append((exitp / entry - 1.0) - (spy_x / spy_e - 1.0))

    mean_excess = sum(excess_vals) / len(excess_vals) if excess_vals else None

    last = resolved[:_KILL_WIN_RATE_TRADES]
    wins = sum(1 for r in last if r["outcome"] == "win")
    win_rate = wins / len(last) if last else None

    excess_trip = mean_excess is not None and mean_excess < 0
    wr_trip = len(last) >= _KILL_WIN_RATE_TRADES and win_rate is not None and win_rate < _KILL_WIN_RATE_MIN
    tripped = excess_trip or wr_trip

    thesis = (
        f"kill-line check {month}: trailing-6m mean excess vs SPY = "
        f"{'n/a' if mean_excess is None else f'{mean_excess * 100:+.2f}%'} "
        f"(n={len(excess_vals)}); trailing-{_KILL_WIN_RATE_TRADES} win rate = "
        f"{'n/a' if win_rate is None else f'{win_rate:.0%}'} (n={len(last)}) → "
        f"{'FAIL — sleeve auto-disabled' if tripped else 'PASS'}"
    )
    _journal_event(
        strategy_id=GENERALS_ID,
        rule_id=GENERALS_ID,
        decision="kill_check_fail" if tripped else "kill_check_pass",
        thesis=thesis,
        db_path=db_path,
    )
    if tripped:
        update_strategy(GENERALS_ID, enabled=False, db_path=db_path)
        try:
            await notify(
                kind="strategy",
                title="Kill line tripped: generals_dip disabled",
                body=thesis,
                severity="warning",
                strategy_id=GENERALS_ID,
                db_path=db_path,
            )
        except Exception:
            logger.exception("generals_dip kill-line notify failed")
    mark(
        _GENERALS_KILL_KEY,
        "kill_check",
        payload={
            "last_checked_month": month,
            "tripped": tripped,
            "mean_excess": mean_excess,
            "win_rate": win_rate,
            "n_window": len(excess_vals),
            "checked_at": now_iso(),
        },
        db_path=db_path,
    )
    return tripped


async def _scan_generals(
    strategy: dict[str, Any],
    *,
    eval_date: str,
    status: dict[str, str],
    db_path: Path,
) -> list[dict[str, Any]]:
    acted: list[dict[str, Any]] = []

    if await _generals_kill_check(strategy, eval_date, db_path=db_path):
        status[GENERALS_ID] = "auto-disabled (kill line tripped)"
        return []

    account = _resolve_account(strategy, db_path=db_path)
    if account is None:
        status[GENERALS_ID] = "no execution-capable account"
        return []
    notional = float(strategy.get("notional_usd") or 0)
    if notional <= 0:
        status[GENERALS_ID] = "notional is 0"
        return []

    claim_key = f"regime:scan:{GENERALS_ID}:{eval_date}"
    if not claim(claim_key, "scan", db_path=db_path):
        return []  # already evaluated today

    evaluated = False
    try:
        universe = await _current_universe(eval_date, db_path=db_path)
        if not universe:
            status[GENERALS_ID] = "universe unavailable (bar fetch failed)"
            return []

        bars_by_symbol = await _fetch_daily_bars(universe, days=_GENERALS_BAR_DAYS)
        todays = {
            sym: bars
            for sym, bars in bars_by_symbol.items()
            if bars and bars[-1]["d"] == eval_date
        }
        if not todays:
            status[GENERALS_ID] = f"awaiting today's close bars ({eval_date})"
            return []
        evaluated = True

        candidates: list[tuple[float, str, float]] = []
        for sym in universe:
            bars = todays.get(sym)
            if not bars:
                continue
            close_values = [float(b["c"]) for b in bars]
            fired, ret3 = is_generals_dip_entry(
                close_values,
                sma_window=int(strategy["params"].get("sma_window", 50)),
                ret_3d_max=float(strategy["params"].get("ret_3d_max", -3.5)),
            )
            if fired and ret3 is not None:
                candidates.append((ret3, sym, close_values[-1]))
        candidates.sort()  # most oversold first

        if not candidates:
            status[GENERALS_ID] = f"0 flushes in top-30 today ({eval_date})"
            return []

        open_rows = [
            r for r in list_open_positions(db_path=db_path)
            if r["strategy_id"] == GENERALS_ID
        ]
        # list_paper_order_proposals (NOT list_pending_proposals, whose slim rows
        # drop `source` — this filter could never match, so pending buys neither
        # counted toward max-5 nor marked their symbol busy) + a manual expiry
        # guard for pending_phone; approving rows are mid-execution and never
        # expire away. Mirrors the reviewed _vol_cluster_count fix.
        now = now_iso()
        unresolved = [
            p
            for p in list_paper_order_proposals(
                status="pending_phone", limit=200, db_path=db_path
            )
            if not p.get("expires_at") or str(p["expires_at"]) > now
        ] + list_paper_order_proposals(status="approving", limit=200, db_path=db_path)
        pending = [
            p for p in unresolved
            if (p.get("source") or "") == f"strategy:{GENERALS_ID}"
            and str(p.get("side", "")).lower() == "buy"
        ]
        busy = {str(r["symbol"]).upper() for r in open_rows} | {
            str(p.get("symbol", "")).upper() for p in pending
        }
        committed = len(open_rows) + len(pending)
        max_concurrent = strategy.get("max_concurrent_positions") or _GENERALS_MAX_CONCURRENT
        max_pending = trading_config().get("max_pending_agent_proposals", 3)

        proposed = 0
        skipped = 0
        for ret3, sym, close in candidates:
            if sym in busy:
                skipped += 1
                continue
            if committed >= max_concurrent:
                skipped += 1
                continue
            if len(list_pending_proposals(db_path=db_path)) >= max_pending:
                skipped += 1
                continue
            thesis = (
                f"generals_dip: {sym} {ret3:.1f}%/3d flush in the top-30, close>SMA50 — "
                f"next-open entry, 10td fixed hold · {SLEEVE_TAG} · Tier-B regime-local"
            )
            row = await _propose_entry(
                symbol=sym,
                close=close,
                notional_usd=notional,
                account_id=str(account["id"]),
                strategy_id=GENERALS_ID,
                rule_id=GENERALS_ID,
                thesis=thesis,
                db_path=db_path,
            )
            if row is None:
                skipped += 1
                continue
            committed += 1
            proposed += 1
            acted.append({"kind": "propose", "sleeve": GENERALS_ID, "symbol": sym, "proposal_id": row["id"]})

        msg = f"{len(candidates)} flush(es) in top-30, proposed {proposed}"
        if skipped:
            msg += f", skipped {skipped} (held/capped/max-pending)"
        status[GENERALS_ID] = msg
    finally:
        if not evaluated:
            forget(claim_key, db_path=db_path)  # bars not final yet — retry next cycle
    return acted


# --- vol_event ------------------------------------------------------------------------


def _trigger_enabled(strategy: dict[str, Any], trigger_id: str) -> bool:
    triggers = strategy.get("params", {}).get("triggers") or {}
    trig = triggers.get(trigger_id) or {}
    return bool(trig.get("enabled", True))


def _disable_trigger(trigger_id: str, reason: str, *, db_path: Path) -> None:
    """Flip the trigger's enabled flag in vol_event params (the existing mechanism)."""
    strategy = get_strategy(VOL_EVENT_ID, db_path=db_path)
    if strategy is None:
        return
    params = dict(strategy.get("params") or {})
    triggers = dict(params.get("triggers") or {})
    trig = dict(triggers.get(trigger_id) or {})
    trig["enabled"] = False
    trig["disabled_reason"] = reason
    trig["disabled_at"] = now_iso()
    triggers[trigger_id] = trig
    params["triggers"] = triggers
    update_strategy(VOL_EVENT_ID, params=params, db_path=db_path)


def _vol_cluster_count(*, db_path: Path) -> int:
    """Active vol-event positions BOOK-WIDE: open vol_event OR vix_spike managed
    positions (vix_spike queried read-only) + unresolved (pending_phone OR approving)
    vol_event AND vix_spike entry (buy) proposals — vix_spike scans intraday, so an
    unapproved morning card must occupy the slot at the evening scan too."""
    open_rows = [
        r for r in list_open_positions(db_path=db_path)
        if r["strategy_id"] in (VOL_EVENT_ID, VIX_SPIKE_ID)
    ]
    # list_paper_order_proposals (NOT list_pending_proposals, whose slim rows drop
    # `source` — the old filter could never match) + a manual expiry guard for the
    # pending_phone rows; approving rows are mid-execution and never expire away.
    now = now_iso()
    entry_sources = (f"strategy:{VOL_EVENT_ID}", f"strategy:{VIX_SPIKE_ID}")
    proposals = [
        p
        for p in list_paper_order_proposals(
            status="pending_phone", limit=200, db_path=db_path
        )
        if not p.get("expires_at") or str(p["expires_at"]) > now
    ] + list_paper_order_proposals(status="approving", limit=200, db_path=db_path)
    pending = [
        p for p in proposals
        if (p.get("source") or "") in entry_sources
        and str(p.get("side", "")).lower() == "buy"
    ]
    return len(open_rows) + len(pending)


def _position_ctx(row: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    """Entry context (trigger_id, recovery_level) stored at propose time, recovered via
    entry execution → proposal id → KV. {} when unrecoverable."""
    try:
        entry_exec_id = row.get("entry_execution_id")
        if not entry_exec_id:
            return {}
        entry_exec = get_execution(str(entry_exec_id), db_path=db_path)
        proposal_id = (entry_exec or {}).get("proposal_id")
        if not proposal_id:
            return {}
        return get_payload(f"regime:ctx:{proposal_id}", db_path=db_path)
    except Exception:
        logger.exception("vol_event ctx lookup failed for position %s", row.get("id"))
        return {}


def _et_date(iso_ts: str) -> str:
    """ET calendar date of an ISO timestamp ('' if unparseable). The post-close window
    is next-day UTC, so a naive [:10] compare would mis-date evening markers."""
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_ET).date().isoformat()


def _exit_blocked(row: dict[str, Any], eval_date: str, *, db_path: Path) -> bool:
    """True while a prior exit proposal is pending or was already attempted today."""
    marker = (row.get("exit_context") or {}).get("pending_exit")
    if not marker:
        return False
    ref = marker.get("ref")
    if ref:
        pr = get_proposal(str(ref), db_path=db_path)
        if pr and pr.get("status") in {"pending_phone", "approving"}:
            return True
    return _et_date(marker.get("at") or "") == eval_date[:10]


async def _vol_event_exits(
    strategy: dict[str, Any],
    spy_bars: list[DatedBar],
    eval_date: str,
    parts: list[str],
    *,
    db_path: Path,
) -> list[dict[str, Any]]:
    """Propose-only mechanical exits for open vol_event positions (the exit engine
    skips this kind). Same pending_exit marker format as trading/exit_engine so
    closure reconcile + journal resolution work unchanged."""
    from trading.proposals import create_phone_proposal

    acted: list[dict[str, Any]] = []
    rows = [r for r in list_open_positions(db_path=db_path) if r["strategy_id"] == VOL_EVENT_ID]
    if not rows:
        return []
    if not spy_bars:
        parts.append("exits: no SPY bars")
        return []
    last_close = float(spy_bars[-1]["c"])
    params = strategy.get("params") or {}
    dd_cap = int(params.get("dd_max_holding_days", 30))
    tf_cap = int(params.get("tf_max_holding_days", 21))

    for row in rows:
        if _exit_blocked(row, eval_date, db_path=db_path):
            continue
        ctx = _position_ctx(row, db_path=db_path)
        trigger_id = ctx.get("trigger_id")
        holding = max(0, _trading_days_until(str(row["entry_date"]), now_iso()))

        reason = None
        if trigger_id == TRIGGER_TERM_FLIP:
            if holding >= tf_cap:
                reason = f"time_cap_{tf_cap}td"
        elif trigger_id == TRIGGER_DRAWDOWN_STAB:
            level = ctx.get("recovery_level")
            if level is not None and last_close >= float(level):
                reason = "recovered_20d_high"
            elif holding >= dd_cap:
                reason = f"time_cap_{dd_cap}td"
        else:
            # Unrecoverable ctx — never strand a position: longest-cap backstop.
            logger.warning("vol_event position %s missing trigger ctx", row["id"])
            if holding >= dd_cap:
                reason = "time_cap_missing_ctx"
        if reason is None:
            continue

        account = get_account(str(row["account_id"]), db_path=db_path)
        broker_name = str((account or {}).get("broker") or "").lower()
        snapshot = (
            get_latest_snapshot(broker_name, account_id=str(row["account_id"]), db_path=db_path)
            if broker_name
            else None
        )
        pos = next(
            (
                p
                for p in (snapshot or {}).get("positions", [])
                if str(p.get("symbol", "")).upper() == SPY
            ),
            None,
        )
        if pos is None:
            continue  # snapshot lag / closed externally — reconcile handles it
        qty = int(floor(float(pos.get("qty") or 0)))
        if qty < 1:
            continue

        res = await create_phone_proposal(
            {
                "symbol": SPY,
                "side": "sell",
                "qty": qty,
                "order_type": "market",
                "account_id": str(row["account_id"]),
            },
            source=f"exit:{VOL_EVENT_ID}",
            rule_id=str(trigger_id) if trigger_id else None,
            score=False,
            thesis=f"vol_event exit ({reason}) · {SLEEVE_TAG} trigger_id={trigger_id}",
            db_path=db_path,
        )
        marker = {
            "kind": "propose",
            "ref": str(res["id"]) if res.get("id") and not res.get("error") else "",
            "reason": reason if res.get("id") and not res.get("error") else f"failed:{reason}",
            "action": "exit",
            "qty": qty,
            "at": now_iso(),
        }
        update_exit_state(
            row["id"],
            exit_context={**(row.get("exit_context") or {}), "pending_exit": marker},
            db_path=db_path,
        )
        if not marker["ref"]:
            logger.warning("vol_event exit proposal failed for %s: %s", row["id"], res)
            continue
        try:
            await notify(
                kind="exit",
                title=f"Exit: {SPY} — vol_event [{reason}]",
                body=f"SELL {qty} {SPY} — {reason}",
                severity="info",
                strategy_id=VOL_EVENT_ID,
                ref_type="position",
                ref_id=str(row["id"]),
                action_url=f"/positions/{row['id']}",
                data={"symbol": SPY, "reason": reason, "qty": qty, "trigger_id": trigger_id},
                push=False,
                db_path=db_path,
            )
        except Exception:
            logger.exception("vol_event exit notify failed")
        parts.append(f"exit proposed ({trigger_id}: {reason})")
        acted.append({"kind": "exit_propose", "sleeve": VOL_EVENT_ID, "position_id": row["id"], "reason": reason})
    return acted


async def _vol_event_kill_checks(
    spy_bars: list[DatedBar], *, db_path: Path
) -> None:
    """Per-episode mechanical kill lines, run once per newly-resolved episode:
    - drawdown_stab: episode MAE <= -15% OR 3 consecutive losing episodes → disable.
    - term_flip: mean 21d return < +2.0% over the latest 10 episodes → suspend.
    Every check is journaled, pass or fail."""
    rows = by_strategy(VOL_EVENT_ID, limit=500, db_path=db_path)  # newest first
    resolved = [
        r
        for r in reversed(rows)  # chronological
        if r.get("outcome") in ("win", "loss", "scratch")
        and r.get("rule_id") in (TRIGGER_DRAWDOWN_STAB, TRIGGER_TERM_FLIP)
        and r.get("entry_fill_price")
    ]
    if not resolved:
        return
    spy_close = _close_lookup(spy_bars) if spy_bars else None

    for idx, r in enumerate(resolved):
        key = f"regime:killck:{r['proposal_id']}"
        if not claim(key, "kill_check", db_path=db_path):
            continue
        trigger_id = str(r["rule_id"])
        history = [x for x in resolved[: idx + 1] if x.get("rule_id") == trigger_id]

        if trigger_id == TRIGGER_DRAWDOWN_STAB:
            mae = None
            try:
                entry = float(r["entry_fill_price"])
                start = str(r.get("created_at"))[:10]
                end = str(r.get("resolved_at") or now_iso())[:10]
                if spy_close is not None and entry > 0:
                    window = [
                        float(b["c"]) for b in spy_bars if start < b["d"] <= end
                    ]
                    if window:
                        mae = min(window) / entry - 1.0
            except (TypeError, ValueError):
                mae = None
            consec_losses = 0
            for x in reversed(history):
                if x["outcome"] == "loss":
                    consec_losses += 1
                else:
                    break
            mae_trip = mae is not None and mae <= _KILL_DD_MAE
            tripped = mae_trip or consec_losses >= _KILL_DD_CONSECUTIVE_LOSSES
            thesis = (
                f"kill-line check (episode {r['proposal_id']}): MAE="
                f"{'n/a' if mae is None else f'{mae * 100:+.1f}%'}, "
                f"consecutive losses={consec_losses}"
                f" → {'FAIL — drawdown_stab disabled' if tripped else 'PASS'}"
            )
        else:  # term_flip
            rets: list[float] = []
            for x in history[-_KILL_TF_EPISODES:]:
                try:
                    e = float(x["entry_fill_price"])
                    xp = float(x["exit_fill_price"])
                    if e > 0:
                        rets.append(xp / e - 1.0)
                except (TypeError, ValueError):
                    continue
            mean_ret = sum(rets) / len(rets) if rets else None
            tripped = (
                len(rets) >= _KILL_TF_EPISODES
                and mean_ret is not None
                and mean_ret < _KILL_TF_MIN_MEAN_RET
            )
            thesis = (
                f"kill-line check (episode {r['proposal_id']}): mean 21d return over "
                f"last {len(rets)} episodes = "
                f"{'n/a' if mean_ret is None else f'{mean_ret * 100:+.2f}%'}"
                f" (needs {_KILL_TF_EPISODES} episodes to trip) → "
                f"{'FAIL — term_flip suspended' if tripped else 'PASS'}"
            )

        _journal_event(
            strategy_id=VOL_EVENT_ID,
            rule_id=trigger_id,
            decision="kill_check_fail" if tripped else "kill_check_pass",
            thesis=thesis,
            db_path=db_path,
        )
        if tripped and _trigger_enabled(
            get_strategy(VOL_EVENT_ID, db_path=db_path) or {}, trigger_id
        ):
            _disable_trigger(trigger_id, thesis, db_path=db_path)
            try:
                await notify(
                    kind="strategy",
                    title=f"Kill line tripped: vol_event/{trigger_id} disabled",
                    body=thesis,
                    severity="warning",
                    strategy_id=VOL_EVENT_ID,
                    db_path=db_path,
                )
            except Exception:
                logger.exception("vol_event kill-line notify failed")


async def _scan_vol_event(
    strategy: dict[str, Any],
    *,
    eval_date: str,
    status: dict[str, str],
    db_path: Path,
    spy_bars: list[DatedBar],
) -> list[dict[str, Any]]:
    """ENTRY side only. Exits + per-episode kill checks are hoisted into
    run_regime_sleeves_scan so they run even while this scan is skipped
    (sleeve disabled / proposals paused / kill switch)."""
    acted: list[dict[str, Any]] = []
    parts: list[str] = []

    account = _resolve_account(strategy, db_path=db_path)
    if account is None:
        status[VOL_EVENT_ID] = "no execution-capable account"
        return []
    notional = float(strategy.get("notional_usd") or 0)

    claim_key = f"regime:scan:{VOL_EVENT_ID}:{eval_date}"
    if claim(claim_key, "scan", db_path=db_path):
        evaluated = False
        try:
            if not spy_bars or spy_bars[-1]["d"] != eval_date:
                parts.append(f"awaiting today's SPY close bar ({eval_date})")
                return acted
            evaluated = True

            params = strategy.get("params") or {}
            vix_obs = recent_observations("VIXCLS", limit=60, db_path=db_path)
            vix_lookup = _obs_lookup(vix_obs)
            closes = [float(b["c"]) for b in spy_bars]
            lows = [float(b["l"]) for b in spy_bars]
            dates = [str(b["d"]) for b in spy_bars]

            fired: list[tuple[str, dict[str, Any], str]] = []  # (trigger, ctx, why)

            if not _trigger_enabled(strategy, TRIGGER_DRAWDOWN_STAB):
                parts.append("drawdown_stab disabled (kill line)")
            elif not vix_obs:
                parts.append("drawdown_stab dormant (no VIX data)")
            else:
                dd_fired, level = is_drawdown_stab_entry(
                    closes,
                    lows,
                    dates,
                    vix_lookup,
                    drawdown_pct=float(params.get("drawdown_pct", 5.0)),
                    vix_min=float(params.get("vix_min", 25.0)),
                )
                if dd_fired and claim(
                    f"regime:fire:{TRIGGER_DRAWDOWN_STAB}:{eval_date}", "fire", db_path=db_path
                ):
                    fired.append(
                        (
                            TRIGGER_DRAWDOWN_STAB,
                            {"recovery_level": level, "signal_date": eval_date},
                            f"first stabilization day in ≥5% SPY drawdown (VIX≥25); "
                            f"exit on recovery to {level:.2f} or {params.get('dd_max_holding_days', 30)}td cap",
                        )
                    )

            if not _trigger_enabled(strategy, TRIGGER_TERM_FLIP):
                parts.append("term_flip disabled (kill line)")
            elif not vix3m_is_fresh(
                db_path=db_path, now=date.fromisoformat(eval_date)
            ):
                parts.append("term_flip dormant (vix3m stale)")
            else:
                v3m_obs = recent_observations(VIX3M_SERIES, limit=60, db_path=db_path)
                vix_map = dict(vix_obs)
                v3m_map = dict(v3m_obs)
                joint = sorted(set(vix_map) & set(v3m_map))
                ratios = [vix_map[d] / v3m_map[d] for d in joint if v3m_map[d] > 0]
                if is_term_flip_entry(ratios) and claim(
                    f"regime:fire:{TRIGGER_TERM_FLIP}:{joint[-1]}", "fire", db_path=db_path
                ):
                    fired.append(
                        (
                            TRIGGER_TERM_FLIP,
                            {"signal_date": joint[-1]},
                            "VIX/VIX3M back ≤ 1.0 after ≥2 inverted days; "
                            f"fixed {params.get('tf_max_holding_days', 21)}td hold",
                        )
                    )

            for trigger_id, ctx, why in fired:
                if _vol_cluster_count(db_path=db_path) >= 1:
                    _journal_event(
                        strategy_id=VOL_EVENT_ID,
                        rule_id=trigger_id,
                        decision="suppressed",
                        thesis=(
                            f"suppressed (cluster cap): {trigger_id} fired {eval_date} "
                            f"but a vol-event/vix_spike position is already active"
                        ),
                        db_path=db_path,
                    )
                    parts.append(f"{trigger_id} fired — suppressed (cluster cap)")
                    acted.append({"kind": "suppressed", "sleeve": VOL_EVENT_ID, "trigger_id": trigger_id})
                    continue
                if notional <= 0:
                    parts.append(f"{trigger_id} fired — notional is 0")
                    continue
                thesis = (
                    f"vol_event/{trigger_id}: {why} · next-open entry · "
                    f"{SLEEVE_TAG} trigger_id={trigger_id} · Tier-B regime-local"
                )
                row = await _propose_entry(
                    symbol=SPY,
                    close=closes[-1],
                    notional_usd=notional,
                    account_id=str(account["id"]),
                    strategy_id=VOL_EVENT_ID,
                    rule_id=trigger_id,
                    thesis=thesis,
                    db_path=db_path,
                )
                if row is None:
                    parts.append(f"{trigger_id} fired — proposal failed")
                    continue
                mark(
                    f"regime:ctx:{row['id']}",
                    "entry_context",
                    payload={"trigger_id": trigger_id, **ctx},
                    db_path=db_path,
                )
                parts.append(f"{trigger_id} fired — proposed")
                acted.append(
                    {"kind": "propose", "sleeve": VOL_EVENT_ID, "trigger_id": trigger_id, "proposal_id": row["id"]}
                )

            if not fired:
                parts.insert(0, "no active vol episode")
        finally:
            if not evaluated:
                forget(claim_key, db_path=db_path)

    if parts:
        status[VOL_EVENT_ID] = "; ".join(parts)
    return acted


# --- status + entry point ---------------------------------------------------------------


def _set_status(status: dict[str, str], *, db_path: Path) -> None:
    payload = get_payload(_STATUS_KEY, db_path=db_path)
    sleeves = dict(payload.get("sleeves") or {})
    for sleeve_id, text in status.items():
        sleeves[sleeve_id] = {"status": text, "at": now_iso()}
    mark(_STATUS_KEY, "status", payload={"sleeves": sleeves}, db_path=db_path)


def sleeves_status(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    """Dormancy-visible status payload for the API: per-sleeve enable state, last scan
    result (WHY nothing fired), universe month, trigger flags, kill-check state."""
    stored = get_payload(_STATUS_KEY, db_path=db_path).get("sleeves") or {}
    out: dict[str, Any] = {"sleeves": {}}

    generals = get_strategy(GENERALS_ID, db_path=db_path)
    universe_state = get_payload(_UNIVERSE_KEY, db_path=db_path)
    kill_state = get_payload(_GENERALS_KILL_KEY, db_path=db_path)
    out["sleeves"][GENERALS_ID] = {
        "enabled": bool(generals and generals.get("enabled")),
        "entry_mode": (generals or {}).get("entry_mode"),
        "last_scan": stored.get(GENERALS_ID),
        "universe_month": universe_state.get("month"),
        "universe": universe_state.get("symbols") or [],
        "kill_check": kill_state or None,
    }

    vol = get_strategy(VOL_EVENT_ID, db_path=db_path)
    triggers = ((vol or {}).get("params") or {}).get("triggers") or {}
    out["sleeves"][VOL_EVENT_ID] = {
        "enabled": bool(vol and vol.get("enabled")),
        "entry_mode": (vol or {}).get("entry_mode"),
        "last_scan": stored.get(VOL_EVENT_ID),
        "triggers": triggers,
        "vix3m_fresh": vix3m_is_fresh(db_path=db_path),
    }
    return out


async def run_regime_sleeves_scan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Post-close scan for both regime-local sleeves. Propose-only — never submits."""
    acted: list[dict[str, Any]] = []
    try:
        t = now if now is not None else datetime.now(_ET)
        if not _in_post_close_window(t):
            return []
        eval_date = t.astimezone(_ET).date().isoformat()
        status: dict[str, str] = {}

        # NEVER-GATED position management (adversarial-review fix, 2026-07-03):
        # vol_event exit proposals + per-episode kill checks run on every pass,
        # independent of the sleeve's enabled flag, agent_proposals_enabled, and
        # the kill switch — mirroring trading/exit_engine, which manages open
        # positions regardless of strategy.enabled. Only ENTRIES stay gated below;
        # otherwise a disabled sleeve strands its open positions past their holding
        # caps (and the orphan pins the cluster cap forever).
        vol_parts: list[str] = []
        spy_bars: list[DatedBar] = []
        try:
            spy_bars = (await _fetch_daily_bars([SPY], days=_SPY_BAR_DAYS)).get(SPY) or []
            vol_strategy = get_strategy(VOL_EVENT_ID, db_path=db_path) or {}
            acted += await _vol_event_exits(
                vol_strategy, spy_bars, eval_date, vol_parts, db_path=db_path
            )
            await _vol_event_kill_checks(spy_bars, db_path=db_path)
        except Exception:
            logger.exception("vol_event exit/kill management failed")
            vol_parts.append("exit/kill management error (see logs)")

        cfg = trading_config()
        gates_ok = bool(cfg.get("agent_proposals_enabled", True)) and not is_kill_switch_active()

        for sleeve_id, scan, extra in (
            (GENERALS_ID, _scan_generals, {}),
            (VOL_EVENT_ID, _scan_vol_event, {"spy_bars": spy_bars}),
        ):
            strategy = get_strategy(sleeve_id, db_path=db_path)
            if strategy is None or not strategy.get("enabled"):
                status[sleeve_id] = "disabled (not armed)"
                continue
            if not gates_ok:
                status[sleeve_id] = "gated (proposals disabled or kill switch)"
                continue
            try:
                acted += await scan(
                    strategy, eval_date=eval_date, status=status, db_path=db_path, **extra
                )
            except Exception:
                logger.exception("Regime-sleeve scan failed for %s", sleeve_id)
                status[sleeve_id] = "scan error (see logs)"
        if vol_parts:
            entry_status = status.get(VOL_EVENT_ID)
            status[VOL_EVENT_ID] = "; ".join(
                vol_parts + ([entry_status] if entry_status else [])
            )
        _set_status(status, db_path=db_path)
    except Exception:
        logger.exception("Regime-sleeves scan failed")
    return acted
