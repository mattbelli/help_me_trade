"""Momentum universe bar refresh and production target-set ranking."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from brokers.errors import BrokerError
from brokers.factory import get_market_data_provider
from config.settings import trading_config
from db.accounts import get_account
from db.base import DEFAULT_DB_PATH, now_iso
from db.managed_positions import close_position, list_open_positions
from db.momentum_bars import load_daily_bars, upsert_daily_bars
from db.momentum_rebalance import complete_run, get_run, start_run
from db.order_executions import recent_open_buy_exists, recent_open_sell_exists
from db.sqlite import get_latest_snapshot
from db.strategies import update_strategy
from research.momentum_reference import compute_target_set, load_sector_map
from signals.notify import notify
from signals.strategy_universe import resolve_universe_spec
from trading.execution_service import ExecutionService
from trading.market_hours import _ET, is_market_open
from trading.models import OrderIntent, RiskRejectedError
from trading.proposal_intake import _AUTO_ENTRY_DEDUP_SECONDS

logger = logging.getLogger(__name__)

# CALENDAR days to fetch. compute_target_set needs MOM_LONG(252)+MOM_SKIP(21)=273 TRADING
# days of history at the rank date; 273 trading days ≈ 396 calendar days, so 480 gives a
# comfortable margin (~331 trading days). (Schwab's `days` param is a calendar lookback.)
DEFAULT_REFRESH_DAYS = 480
_REBALANCE_PACE_SECONDS = 0.3
# A healthy top-quintile is ~95-100 names. If the target collapses to far fewer (stale/partial
# bar data), the diff would spuriously SELL most of the book — abort the rebalance instead.
MINIMUM_MOMENTUM_TARGET = 50
# A rebalance normally completes in well under a minute. If a run stays 'executing' far longer,
# some leg is persistently rejecting (e.g. buying-power exhaustion) and rejected orders bypass
# the 900s dedup — so without a bound it retries that leg every scan forever. Give up after this.
MAX_REBALANCE_AGE_SECONDS = 1800  # 30 min


def _rebalance_run_age(run: dict[str, Any]) -> float:
    created = run.get("created_at")
    if not created:
        return 0.0
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(str(created))).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _bar_date(time_str: str) -> str:
    return str(time_str).strip()[:10]


def _default_as_of(
    bars: dict[str, tuple[list[str], list[float], list[float]]],
) -> str | None:
    if "SPY" in bars and bars["SPY"][0]:
        return bars["SPY"][0][-1]

    latest_dates = [dates[-1] for dates, _closes, _opens in bars.values() if dates]
    if not latest_dates:
        return None
    return Counter(latest_dates).most_common(1)[0][0]


async def refresh_universe_bars(
    *,
    days: int = DEFAULT_REFRESH_DAYS,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Backfill/refresh daily bars for the S&P 500 momentum universe."""
    symbols = resolve_universe_spec("sp500")
    if not symbols:
        return 0

    logger.info(
        "Momentum bars refresh: fetching %d symbols (%d calendar days)",
        len(symbols),
        days,
    )

    try:
        provider = get_market_data_provider()
    except BrokerError as exc:
        logger.warning("Momentum bars refresh skipped — provider unavailable: %s", exc)
        return 0

    try:
        bars_by_symbol = await provider.aget_stock_bars_batch(
            symbols,
            timeframe="1Day",
            days=days,
        )
    except BrokerError:
        logger.exception("Momentum bars batch fetch failed")
        return 0
    except Exception:
        logger.exception("Momentum bars batch fetch failed")
        return 0

    rows: list[tuple[str, str, float, float]] = []
    missing: list[str] = []
    for sym in symbols:
        sym_bars = bars_by_symbol.get(sym)
        if not sym_bars:
            missing.append(sym)
            continue
        for bar in sym_bars:
            rows.append((sym, _bar_date(bar.time), float(bar.open), float(bar.close)))

    if missing:
        logger.warning(
            "Momentum bars refresh: no data for %d/%d symbols (sample: %s)",
            len(missing),
            len(symbols),
            ", ".join(missing[:5]),
        )

    if not rows:
        logger.info("Momentum bars refresh: no rows to upsert")
        return 0

    written = upsert_daily_bars(rows, db_path=db_path)
    logger.info(
        "Momentum bars refresh: upserted %d rows across %d symbols",
        written,
        len(bars_by_symbol),
    )
    return written


def production_target_set(
    *,
    as_of: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict]:
    """Compute the live residual-momentum top-quintile target set."""
    sector_map = load_sector_map()
    universe = resolve_universe_spec("sp500")
    bars = load_daily_bars(universe, db_path=db_path)

    rank_date = as_of
    if not rank_date:
        rank_date = _default_as_of(bars)
    if not rank_date:
        return []

    # Min-real-history is enforced upstream: compute_target_set requires a 252-bar lookback
    # index, so any symbol with <252 real bars at rank_date is skipped (no padding in prod
    # bars). The remaining safety screen is the extreme-residual guard below.
    target = compute_target_set(bars, sector_map, rank_date)
    return _apply_extreme_residual_gate(target)


# Calibrated on 15yr Norgate (180 month-ends, 16,190 selections): legit residuals top out at
# +10.83 (CVNA's real 25x recovery), p99.9 = +5.0. Hard-drop > +12 passes every legit selection
# ever observed and catches data artifacts (e.g. SNDK's +32 spin-off glitch); soft-flag > +8.
RESIDUAL_HARD_DROP = 12.0
RESIDUAL_SOFT_FLAG = 8.0


def _apply_extreme_residual_gate(target: list[dict]) -> list[dict]:
    """Drop implausible-residual names (data artifacts) before they can be bought.

    Momentum auto-BUYS the top quintile, and "huge 12-month return" is exactly what a
    price-history glitch (spin-off / split / incomplete listing) produces — so a bad bar
    doesn't just add noise, it gets selected. Hard-drop > +12; soft-flag (keep + log) > +8.
    """
    screened: list[dict] = []
    for row in target:
        r = float(row.get("residual_momentum", 0.0))
        if r > RESIDUAL_HARD_DROP:
            logger.warning(
                "Momentum sanity gate: HARD-DROP %s — residual %.2f > %.1f (data artifact?)",
                row.get("symbol"),
                r,
                RESIDUAL_HARD_DROP,
            )
            continue
        if r > RESIDUAL_SOFT_FLAG:
            logger.warning(
                "Momentum sanity gate: SOFT-FLAG %s — residual %.2f > %.1f (review)",
                row.get("symbol"),
                r,
                RESIDUAL_SOFT_FLAG,
            )
        screened.append(row)
    return screened


def _current_rebalance_month() -> str:
    return datetime.now(_ET).strftime("%Y-%m")


def _latest_close(symbol: str, *, db_path: Path) -> Decimal | None:
    bars = load_daily_bars([symbol], db_path=db_path)
    sym = symbol.strip().upper()
    if sym not in bars:
        return None
    _dates, closes, _opens = bars[sym]
    if not closes:
        return None
    return Decimal(str(closes[-1]))


def _held_qty_map(
    strategy_id: str,
    account_id: str | None,
    *,
    db_path: Path,
) -> dict[str, Decimal]:
    snapshot_qty: dict[str, Decimal] = {}
    if account_id:
        account = get_account(str(account_id), db_path=db_path)
        if account is not None:
            broker = str(account["broker"]).strip().lower()
            snapshot = get_latest_snapshot(
                broker, account_id=str(account_id), db_path=db_path
            )
            if snapshot:
                for pos in snapshot.get("positions") or []:
                    if str(pos.get("side", "")).lower() != "long":
                        continue
                    sym = str(pos.get("symbol", "")).strip().upper()
                    if not sym:
                        continue
                    snapshot_qty[sym] = Decimal(str(pos.get("qty", 0)))

    held: dict[str, Decimal] = {}
    for row in list_open_positions(db_path=db_path):
        if row["strategy_id"] != strategy_id:
            continue
        sym = str(row["symbol"]).strip().upper()
        if sym in snapshot_qty and snapshot_qty[sym] > 0:
            qty = snapshot_qty[sym]
        elif row.get("qty"):
            qty = Decimal(str(row["qty"]))
        else:
            continue
        if qty > 0:
            held[sym] = qty
    return held


def _compute_rebalance_plan(
    strategy: dict[str, Any],
    *,
    db_path: Path,
) -> dict[str, list[dict[str, str]]] | None:
    sid = strategy["id"]
    target_rows = production_target_set(db_path=db_path)
    if not target_rows or len(target_rows) < MINIMUM_MOMENTUM_TARGET:
        return None

    residual = {
        str(d["symbol"]).strip().upper(): float(d.get("residual_momentum", 0.0))
        for d in target_rows
    }
    target_syms = set(residual)
    held = _held_qty_map(sid, strategy.get("account_id"), db_path=db_path)

    sell_syms = sorted(s for s in held if s not in target_syms)
    # Highest-conviction first, so a tight max_deployed_usd cap drops the LOWEST-residual
    # names — not late-alphabet ones (which a plain sorted() would).
    buy_syms = sorted(
        (s for s in target_syms if s not in held),
        key=lambda s: residual.get(s, 0.0),
        reverse=True,
    )

    notional = Decimal(str(strategy.get("notional_usd") or 0))
    if notional <= 0 or not target_syms:
        return {"buys": [], "sells": []}

    per_name = notional / Decimal(len(target_syms))
    max_deployed_raw = strategy.get("max_deployed_usd")
    max_deployed = (
        Decimal(str(max_deployed_raw))
        if max_deployed_raw is not None
        else None
    )

    sells: list[dict[str, str]] = []
    for sym in sell_syms:
        # Sell the ENTIRE held position (fractional included) to fully exit a dropout —
        # flooring to whole shares would strand the fractional remainder forever.
        qty = held[sym]
        if qty > 0:
            sells.append({"symbol": sym, "qty": str(qty)})

    buys: list[dict[str, str]] = []
    deployed = Decimal(0)
    for i, sym in enumerate(buy_syms):
        price = _latest_close(sym, db_path=db_path)
        if price is None or price <= 0:
            logger.warning(
                "Momentum rebalance: skip buy %s — no bar price", sym
            )
            continue
        # Dollar-weighted FRACTIONAL shares (matches the dollar-weighted backtest):
        # deploy the full per-name budget instead of flooring to whole shares, which
        # left ~24% in cash and dropped every name priced above per_name (NVDA/META/…).
        # ROUND_FLOOR keeps us at or under budget (never over-deploy / over-spend BP).
        qty = (per_name / price).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)
        if qty <= 0:
            continue
        cost = qty * price
        if max_deployed is not None and deployed + cost > max_deployed:
            logger.warning(
                "Momentum rebalance: max_deployed_usd cap reached (~$%s) — dropping %d "
                "lower-conviction names: %s",
                deployed,
                len(buy_syms) - i,
                buy_syms[i:][:10],
            )
            break
        buys.append({"symbol": sym, "qty": str(qty)})
        deployed += cost

    return {"buys": buys, "sells": sells}


def _open_position_id(
    *,
    account_id: str,
    symbol: str,
    strategy_id: str,
    db_path: Path,
) -> str | None:
    sym = symbol.strip().upper()
    for row in list_open_positions(account_id=account_id, db_path=db_path):
        if row["strategy_id"] == strategy_id and str(row["symbol"]).upper() == sym:
            return str(row["id"])
    return None


async def execute_rebalance(
    strategy: dict[str, Any],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Execute a monthly momentum portfolio rebalance (paper-fenced upstream).

    ★ PRECONDITION — bind this strategy to a DEDICATED account. "Held" for buy-suppression is
    sourced from this strategy's managed_positions, so a broker long that exists WITHOUT a
    managed row (shared account with another strategy, or a fill whose ledger row failed to
    create) would be re-bought. Acceptable on a dedicated paper account; a LIVE unlock must add
    an explicit account-dedicated guard first.
    """
    sid = strategy["id"]
    month = _current_rebalance_month()
    account_id = strategy.get("account_id")
    if not account_id:
        logger.warning("Momentum rebalance skipped for %s — no bound account", sid)
        return {"status": "no_account", "month": month}

    account = get_account(str(account_id), db_path=db_path)
    if account is None:
        logger.warning("Momentum rebalance skipped for %s — account missing", sid)
        return {"status": "no_account", "month": month}

    broker_name = str(account["broker"]).strip().lower()

    existing_run = get_run(sid, month, db_path=db_path)
    if existing_run is not None and existing_run.get("status") == "complete":
        # The run table is authoritative: a 'complete' run means this month is done even if
        # last_rebalanced_month never stamped (crash between complete_run and update_strategy).
        return {"status": "already_rebalanced", "month": month}
    last_month = strategy.get("last_rebalanced_month")
    if last_month == month and (
        existing_run is None or existing_run.get("status") != "executing"
    ):
        return {"status": "already_rebalanced", "month": month}

    # Only rebalance during the regular session. The month ticks at MIDNIGHT ET, so the first
    # scan of a new month is after-hours — firing then means no live quotes (RiskEngine rejects
    # every order, the run gets stuck) and partial fills based on whichever names happen to be
    # cached in the hub. Defer to the first regular-session scan, when the whole universe prices.
    if not is_market_open():
        return {"status": "market_closed", "month": month}

    if existing_run is not None and existing_run.get("status") == "executing":
        plan = existing_run.get("plan") or {}
        # Bound the retry of a stuck run: persistent rejects keep it 'executing', and rejected
        # orders bypass the 900s dedup — so without this it retries the stuck leg every scan
        # forever. Give up after MAX_REBALANCE_AGE: stamp the month, alert, move on.
        if _rebalance_run_age(existing_run) > MAX_REBALANCE_AGE_SECONDS:
            complete_run(sid, month, db_path=db_path)
            update_strategy(sid, last_rebalanced_month=month, db_path=db_path)
            logger.error(
                "Momentum rebalance %s month=%s ABANDONED after >%ds stuck — unfinished plan "
                "(%d sells, %d buys); persistent rejects? Next month recomputes fresh.",
                sid,
                month,
                MAX_REBALANCE_AGE_SECONDS,
                len(plan.get("sells") or []),
                len(plan.get("buys") or []),
            )
            await notify(
                kind="rebalance",
                title=f"Momentum rebalance abandoned: {sid} {month}",
                body=(
                    f"{sid} {month}: orders kept failing — rebalance abandoned, "
                    "retries next month."
                ),
                severity="warning",
                strategy_id=sid,
                group_key=f"rebalance:{sid}:{month}",
                data={"strategy_id": sid, "month": month, "status": "abandoned"},
                db_path=db_path,
            )
            return {"status": "abandoned", "month": month}
        logger.info(
            "Momentum rebalance RESUME for %s month=%s — %d sells, %d buys",
            sid,
            month,
            len(plan.get("sells") or []),
            len(plan.get("buys") or []),
        )
    else:
        plan = _compute_rebalance_plan(strategy, db_path=db_path)
        if plan is None:
            logger.warning(
                "Momentum rebalance skipped for %s — empty target set (no bars yet?)",
                sid,
            )
            return {"status": "no_target", "month": month}
        start_run(sid, month, plan, db_path=db_path)

    target_rows = production_target_set(db_path=db_path)
    target_syms = {
        str(d["symbol"]).strip().upper() for d in target_rows
    } if target_rows else set()

    # Idempotent-by-STATE: drop legs already reflected in current holdings so a partial run
    # that resumes after the 900s dedup window can't re-fire completed orders (sells of names
    # no longer held; buys of names already held). The recent_open_* guards below still cover
    # the brief submit→fill-reconcile gap within a single run.
    current_held = _held_qty_map(sid, account_id, db_path=db_path)
    sells = [
        s for s in (plan.get("sells") or [])
        if str(s["symbol"]).strip().upper() in current_held
    ]
    buys = [
        b for b in (plan.get("buys") or [])
        if str(b["symbol"]).strip().upper() not in current_held
    ]
    nb = 0
    ns = 0
    failed = 0
    deployed = Decimal(0)

    for leg in sells:
        symbol = str(leg["symbol"]).strip().upper()
        qty = Decimal(str(leg["qty"]))
        if recent_open_sell_exists(
            symbol,
            account_id=str(account_id),
            within_seconds=_AUTO_ENTRY_DEDUP_SECONDS,
            db_path=db_path,
        ):
            continue
        intent = OrderIntent(
            proposal_id=f"rebalance_sell|{sid}|{now_iso()}",
            session_id=None,
            symbol=symbol,
            side="sell",
            qty=qty,
            order_type="market",
            limit_price=None,
            broker=broker_name,
            asset_class="us_equity",
            account_id=str(account_id),
            rebalance=True,
        )
        try:
            await ExecutionService.submit(intent, db_path=db_path)
            # Paper simplification: close the managed row on submit (paper market orders fill
            # instantly + the exit engine skips portfolio rows, so the rebalance is the sole
            # closer). For live / non-trivial order types this should move to fill-confirmation
            # so an unfilled sell can't orphan a still-held position. (Feature is paper-fenced.)
            position_id = _open_position_id(
                account_id=str(account_id),
                symbol=symbol,
                strategy_id=sid,
                db_path=db_path,
            )
            if position_id is not None:
                close_position(
                    position_id,
                    reason="rebalance_dropout",
                    db_path=db_path,
                )
            ns += 1
        except RiskRejectedError as exc:
            logger.warning(
                "Momentum rebalance sell rejected for %s: %s", symbol, exc
            )
            failed += 1
        except Exception:
            logger.exception(
                "Momentum rebalance sell failed for %s", symbol
            )
            failed += 1
        await asyncio.sleep(_REBALANCE_PACE_SECONDS)

    for leg in buys:
        symbol = str(leg["symbol"]).strip().upper()
        qty = Decimal(str(leg["qty"]))
        if recent_open_buy_exists(
            symbol,
            account_id=str(account_id),
            within_seconds=_AUTO_ENTRY_DEDUP_SECONDS,
            db_path=db_path,
        ):
            continue
        intent = OrderIntent(
            proposal_id=f"auto_entry|{sid}|{now_iso()}",
            session_id=None,
            symbol=symbol,
            side="buy",
            qty=qty,
            order_type="market",
            limit_price=None,
            broker=broker_name,
            asset_class="us_equity",
            account_id=str(account_id),
            rebalance=True,
        )
        try:
            await ExecutionService.submit(intent, db_path=db_path)
            price = _latest_close(symbol, db_path=db_path)
            if price is not None:
                deployed += qty * price
            nb += 1
        except RiskRejectedError as exc:
            logger.warning(
                "Momentum rebalance buy rejected for %s: %s", symbol, exc
            )
            failed += 1
        except Exception:
            logger.exception(
                "Momentum rebalance buy failed for %s", symbol
            )
            failed += 1
        await asyncio.sleep(_REBALANCE_PACE_SECONDS)

    if failed:
        return {
            "status": "partial",
            "month": month,
            "bought": nb,
            "sold": ns,
            "failed": failed,
        }

    complete_run(sid, month, db_path=db_path)
    update_strategy(
        sid,
        last_rebalanced_month=month,
        db_path=db_path,
    )

    nh = len(target_syms)
    mode = trading_config()["execution_mode"]
    await notify(
        kind="rebalance",
        title=f"Momentum rebalance complete: {sid} {month}",
        body=(
            f"Bought {nb}, sold {ns}, holding {nh} · "
            f"~${float(deployed):,.0f} [{mode}]"
        ),
        severity="success",
        strategy_id=sid,
        group_key=f"rebalance:{sid}:{month}",
        data={
            "strategy_id": sid,
            "month": month,
            "bought": nb,
            "sold": ns,
            "held": nh,
            "deployed_usd": float(deployed),
            "execution_mode": mode,
        },
        db_path=db_path,
    )
    return {
        "status": "rebalanced",
        "month": month,
        "bought": nb,
        "sold": ns,
        "held": nh,
    }
