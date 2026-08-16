"""The account poll: every POLL_INTERVAL_SECONDS fetch each enabled account's
snapshot, persist it, run rules/exit-engine/stop-guard hooks, and keep
``api.poll_health`` honest. ``_poll_once`` also runs inline at startup.

Extracted verbatim from ``api/main`` (strangler round 3). ``api/main``'s
lifespan starts ``_poll_loop`` and awaits ``_poll_once`` first;
``api/trade_jobs`` reuses ``_poll_ext_quotes_for_alerts``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from ai.rules.engine import evaluate_on_snapshot
from api.hub import market_hub
from api.jobs import mark_alive
from api.poll_health import poll_health
from brokers.errors import BrokerError
from brokers.factory import (
    connection_is_configured,
    get_broker,
    get_broker_for_account,
    get_enabled_broker_names,
    get_market_data_provider,
)
from brokers.market_clock import market_session as _market_session
from config.settings import broker_is_configured
from db.accounts import (
    get_connection,
    get_default_account,
    list_accounts,
    reconcile_account_external_id,
)
from db.order_executions import count_pending_executions, count_submitted_executions
from db.sqlite import compute_and_save_snapshot_analytics, get_latest_snapshot, save_snapshot
from trading.exit_engine import run_exit_engine
from trading.fill_tracker import reconcile_open_executions, reconcile_pending_executions
from trading.stop_guard import place_protective_stops

logger = logging.getLogger(__name__)


POLL_INTERVAL_SECONDS = max(5, int(os.environ.get("POLL_INTERVAL_SECONDS", "20")))


async def _poll_account(account: dict) -> bool:
    account_id = str(account["id"])
    broker_name = str(account["broker"]).strip().lower()
    connection_id = account.get("connection_id")
    if connection_id:
        connection = await asyncio.to_thread(get_connection, str(connection_id))
        if connection is None or not connection_is_configured(connection):
            logger.warning(
                "Skipping account %s — connection not configured",
                account_id,
            )
            return False
    elif not broker_is_configured(broker_name):
        logger.warning("Skipping account %s — broker not configured", account_id)
        return False

    try:
        broker = get_broker_for_account(account_id)
        # Stamped BEFORE the fetch: see save_snapshot's captured_at note.
        asked_at = datetime.now(timezone.utc).isoformat()
        fetched_account, positions, orders = await broker.afetch_snapshot()
        registered_external_id = account.get("broker_external_id")
        fetched_external_id = str(fetched_account.id)
        if registered_external_id:
            if str(registered_external_id) != fetched_external_id:
                logger.error(
                    "Snapshot account id mismatch for %s: registered=%s fetched=%s — skipping save",
                    account_id,
                    registered_external_id,
                    fetched_external_id,
                )
                return False
        else:
            await asyncio.to_thread(
                reconcile_account_external_id,
                account_id,
                fetched_external_id,
            )

        captured_at = await asyncio.to_thread(
            save_snapshot,
            fetched_account,
            positions,
            orders,
            broker=broker_name,
            account_id=account_id,
            captured_at=asked_at,
        )
        logger.info(
            "Snapshot saved at %s (%s account=%s)",
            captured_at,
            broker_name,
            account_id,
        )
        # Per-account fill reconcile (each account via its own adapter).
        if count_pending_executions(broker_name, account_id=account_id) > 0:
            await reconcile_pending_executions(broker_name, account_id=account_id)
        if count_submitted_executions(broker_name, account_id=account_id) > 0:
            await reconcile_open_executions(broker_name, account_id=account_id)
        # Book protective stops ride the same pass: the snapshot saved above
        # already shows a just-filled entry, so the stop validates first try.
        if broker_name == "alpaca":
            await place_protective_stops(
                broker_name, account_id=account_id, positions=positions
            )
        # Analytics + rules-eval stay default-account-only (broker-keyed display +
        # watchlist-global rules) — unchanged.
        if not account.get("is_default"):
            return True
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        snapshot = await asyncio.to_thread(
            get_latest_snapshot,
            broker_name,
            account_id=account_id,
        )
        if snapshot:
            symbols = {
                str(p.get("symbol", "")).upper()
                for p in snapshot.get("positions") or []
                if p.get("symbol")
            }
            quotes = market_hub.quote_snapshot(symbols)
            triggered = evaluate_on_snapshot(snapshot, quotes=quotes)
            if triggered:
                logger.info(
                    "Rules triggered on poll (%s account=%s): %s",
                    broker_name,
                    account_id,
                    [t["rule_id"] for t in triggered],
                )
                from signals.notify import notify

                for item in triggered:
                    await notify(
                        kind="rule_triggered",
                        title=f"{item['rule_name']} triggered — {item['action'].get('type', '?')}",
                        body=str(item["action"]),
                        severity="info",
                        ref_type="rule",
                        ref_id=item["rule_id"],
                        action_url="/alerts",
                        data={
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        },
                        push=False,
                    )
        return True
    except Exception:
        logger.exception("Poll failed for account %s (%s)", account_id, broker_name)
        return False


async def _poll_broker(broker_name: str) -> bool:
    if not broker_is_configured(broker_name):
        logger.warning("Skipping %s — not configured", broker_name)
        return False
    try:
        broker = get_broker(broker_name)
        # Stamped BEFORE the fetch: see save_snapshot's captured_at note.
        asked_at = datetime.now(timezone.utc).isoformat()
        account, positions, orders = await broker.afetch_snapshot()
        trade_account = get_default_account(broker_name)
        captured_at = await asyncio.to_thread(
            save_snapshot,
            account,
            positions,
            orders,
            broker=broker_name,
            account_id=trade_account["id"] if trade_account else None,
            captured_at=asked_at,
        )
        logger.info("Snapshot saved at %s (%s)", captured_at, broker_name)
        await asyncio.to_thread(compute_and_save_snapshot_analytics, broker_name)
        if count_pending_executions(broker_name) > 0:
            await reconcile_pending_executions(broker_name)
        if count_submitted_executions(broker_name) > 0:
            await reconcile_open_executions(broker_name)
        snapshot = await asyncio.to_thread(
            get_latest_snapshot,
            broker_name,
            account_id=trade_account["id"] if trade_account else None,
        )
        if snapshot:
            symbols = {
                str(p.get("symbol", "")).upper()
                for p in snapshot.get("positions") or []
                if p.get("symbol")
            }
            quotes = market_hub.quote_snapshot(symbols)
            triggered = evaluate_on_snapshot(snapshot, quotes=quotes)
            if triggered:
                logger.info(
                    "Rules triggered on poll (%s): %s",
                    broker_name,
                    [t["rule_id"] for t in triggered],
                )
                from signals.notify import notify

                for item in triggered:
                    await notify(
                        kind="rule_triggered",
                        title=f"{item['rule_name']} triggered — {item['action'].get('type', '?')}",
                        body=str(item["action"]),
                        severity="info",
                        ref_type="rule",
                        ref_id=item["rule_id"],
                        action_url="/alerts",
                        data={
                            "rule_id": item["rule_id"],
                            "rule_name": item["rule_name"],
                            "action": item["action"],
                            "broker": broker_name,
                        },
                        push=False,
                    )
        return True
    except Exception:
        logger.exception("Poll failed for %s", broker_name)
        return False


async def _poll_once() -> None:
    tasks: list[asyncio.Task[bool]] = []
    alpaca_accounts = [
        account
        for account in list_accounts(enabled_only=True)
        if str(account["broker"]).strip().lower() == "alpaca"
    ]
    for account in alpaca_accounts:
        tasks.append(asyncio.create_task(_poll_account(account)))

    for name in get_enabled_broker_names():
        if name == "schwab":
            tasks.append(asyncio.create_task(_poll_broker(name)))

    if not tasks:
        return
    results = await asyncio.gather(*tasks, return_exceptions=False)
    if any(results):
        poll_health.record_success()
        mark_alive("poll")

    # Unmanaged-fill sweep (audit H1/M1) — ONCE per cycle, cross-account, AFTER the
    # per-account reconciles above (so any fill attributed this cycle already has its
    # managed/orphan row). A partial fill that jumped terminal across a poll gap, or a
    # fill whose attribution was lost to a restart, holds real shares with NO exit
    # management + NO page; this quarantines + pages them. Additive to the reconcile
    # loop, not a new bg loop; fail-isolated so it can never break the poll.
    try:
        from trading.position_ledger import sweep_unmanaged_terminal_fills

        swept = await sweep_unmanaged_terminal_fills()
        if swept:
            logger.warning("Unmanaged-fill sweep quarantined %d new orphan fill(s)", swept)
    except Exception:
        logger.exception("Unmanaged-fill sweep pass failed")

    # Exit engine — ONE pass after all snapshots are saved + fills reconciled this
    # cycle, so it reads fresh per-account snapshots and a current managed ledger. It
    # walks open managed positions and dispatches sells per each strategy's exit_mode
    # (auto -> ExecutionService chokepoint; propose -> phone proposal). Fail-closed
    # inside; isolated here so an exit hiccup can never break the poll loop.
    try:
        outcomes = await run_exit_engine()
        acted = [o for o in outcomes if o.get("action") in {"exit", "scale", "closed"}]
        if acted:
            logger.info(
                "Exit engine acted on %d position(s): %s",
                len(acted),
                [(o.get("symbol"), o.get("action"), o.get("reason")) for o in acted],
            )
    except Exception:
        logger.exception("Exit engine pass failed")


async def _poll_loop() -> None:
    while True:
        try:
            await _poll_once()
        except Exception:
            logger.exception("Poll loop iteration failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _poll_ext_quotes_for_alerts(symbols: set[str]) -> None:
    """Fetch extended-hours quotes for alert symbols and feed apply_extended.

    Server-side so off-hours price alerts fire even with no browser/phone polling
    the watchlist (the only other apply_extended caller). Schwab-only (ext quotes);
    fail-soft — a quote miss just skips the symbol this cycle.
    """
    try:
        provider = get_market_data_provider()
    except BrokerError:
        return
    from brokers.hybrid_market_data import HybridMarketData
    from brokers.schwab_market_data import SchwabMarketData

    if not isinstance(provider, (SchwabMarketData, HybridMarketData)):
        return
    session = _market_session()
    try:
        ext_quotes = await provider.aget_quotes(sorted(symbols))
    except Exception:
        logger.debug("Alert ext-quote poll failed", exc_info=True)
        return
    for sym in symbols:
        eq = ext_quotes.get(sym, {})
        ext_price = eq.get("ext_last")
        if ext_price is None:
            continue
        try:
            await market_hub.apply_extended(
                sym,
                ext_price=ext_price,
                ext_change_pct=eq.get("ext_change_pct"),
                market_session=session,
                broadcast=True,
            )
        except Exception:
            logger.debug("apply_extended (alert poll) failed for %s", sym, exc_info=True)
