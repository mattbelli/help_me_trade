"""Schwab ops jobs: auth/health, daily snapshots, and scoped minute trade sync.
``api/background_jobs`` registers these jobs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime

from brokers.factory import get_market_data_provider
from config.settings import broker_is_configured
from db.accounts import list_accounts
from trading.market_hours import _ET
from trading.sync import sync_schwab

logger = logging.getLogger(__name__)


SCHWAB_HEALTH_INTERVAL_SECONDS = max(
    300, int(os.environ.get("SCHWAB_HEALTH_INTERVAL_SECONDS", str(6 * 3600)))
)
_SCHWAB_HEALTH_ALERTS_ENABLED = os.environ.get("SCHWAB_HEALTH_ALERTS_ENABLED", "1").strip() not in (
    "0",
    "false",
    "no",
)
SCHWAB_SNAPSHOT_INTERVAL_SECONDS = max(
    300, int(os.environ.get("SCHWAB_SNAPSHOT_INTERVAL_SECONDS", str(1800)))
)
SCHWAB_TRADE_SYNC_INTERVAL_SECONDS = 60.0
# Explicit ledger IDs, not labels or last-four selectors. Empty disables the
# minute job; the existing hourly all-account reconciliation still runs.
SCHWAB_TRADE_SYNC_ACCOUNT_IDS = frozenset(
    value.strip()
    for value in os.environ.get("SCHWAB_TRADE_SYNC_ACCOUNT_IDS", "").split(",")
    if value.strip()
)


def _sync_schwab_selected_accounts() -> dict:
    if not SCHWAB_TRADE_SYNC_ACCOUNT_IDS:
        return {"skipped": "no selected accounts"}
    accounts = {
        account["id"]: account["broker_external_id"]
        for account in list_accounts(broker="schwab", enabled_only=True)
        if account.get("broker_external_id")
    }
    if not SCHWAB_TRADE_SYNC_ACCOUNT_IDS <= accounts.keys():
        raise RuntimeError("Schwab minute sync: selected account is disabled or unmapped")
    # A nonempty, resolved scope can never fall back to an all-account sweep.
    result = sync_schwab(
        external_ids=[accounts[key] for key in sorted(SCHWAB_TRADE_SYNC_ACCOUNT_IDS)]
    )
    if result.get("error") or any(
        account.get("error") or account.get("incomplete")
        for account in result.get("accounts", [])
    ):
        raise RuntimeError("Schwab minute sync failed; see transaction sync logs")
    return result


async def _schwab_trade_sync_once() -> None:
    """Discover fills even when position snapshots and activity events miss them."""
    result = await asyncio.to_thread(_sync_schwab_selected_accounts)
    logger.info("Schwab minute trade sync: %s", result)


# Schwab health alert state: last-alert date (resets each ET day)
_schwab_health_last_date: dict[
    str, date
] = {}  # key: alert type ("expiring"/"offline"/"probe_failed")
_schwab_probe_failures = 0
_SCHWAB_PROBE_FAILURE_THRESHOLD = 3


async def _schwab_health_once() -> None:
    """Single pass: auth bookkeeping checks + one SPY quote probe."""
    global _schwab_probe_failures

    from brokers.schwab_auth import auth_status
    from signals.notify import notify

    status = auth_status()
    today_et = datetime.now(_ET).date()

    if not status.get("authenticated"):
        if _schwab_health_last_date.get("offline") != today_et:
            await notify(
                kind="schwab_health",
                title="Schwab data offline",
                body="Token invalid; re-auth needed. Run scripts.schwab_reauth or re-login.",
                severity="critical",
                group_key="schwab_health_offline",
                data={"auth_status": "offline"},
            )
            _schwab_health_last_date["offline"] = today_et
            logger.warning("Schwab health alert: token offline")
    else:
        days_left = status.get("estimated_days_until_reauth")
        if days_left is not None and days_left <= 1.0:
            if _schwab_health_last_date.get("expiring") != today_et:
                await notify(
                    kind="schwab_health",
                    title="Schwab re-auth needed",
                    body=(
                        f"Token expires within ~{days_left:.1f} day(s). "
                        "Run scripts.schwab_reauth or re-login."
                    ),
                    severity="warning",
                    group_key="schwab_health_expiring",
                    data={
                        "auth_status": "expiring",
                        "days_left": days_left,
                    },
                )
                _schwab_health_last_date["expiring"] = today_et
                logger.warning("Schwab health alert: token expiring in %.2f days", days_left)

    # Probe only when the token *claims* to be fine — an offline token already
    # paged above; probing it too would double-alert (alert fatigue).
    if status.get("authenticated") and broker_is_configured("schwab"):
        try:
            provider = get_market_data_provider("schwab")
            await provider.aget_quotes(["SPY"])
            _schwab_probe_failures = 0
        except Exception:
            _schwab_probe_failures += 1
            logger.exception(
                "Schwab data probe failed (%d consecutive)",
                _schwab_probe_failures,
            )
            if _schwab_probe_failures >= _SCHWAB_PROBE_FAILURE_THRESHOLD:
                if _schwab_health_last_date.get("probe_failed") != today_et:
                    await notify(
                        kind="schwab_health",
                        title="Schwab data probe failing",
                        body=(
                            f"Schwab data probe failing ({_SCHWAB_PROBE_FAILURE_THRESHOLD}x) — "
                            "token or WAF problem; market data may be dead."
                        ),
                        severity="critical",
                        group_key="schwab_health_probe_failed",
                        data={
                            "auth_status": "probe_failed",
                            "consecutive_failures": _schwab_probe_failures,
                        },
                    )
                    _schwab_health_last_date["probe_failed"] = today_et
                    logger.warning(
                        "Schwab health alert: data probe failed %d times",
                        _schwab_probe_failures,
                    )


_schwab_snapshot_last_date: dict = {}


async def _schwab_snapshot_once() -> None:
    now_et = datetime.now(_ET)
    today = now_et.date()
    if now_et.weekday() >= 5:  # weekend — no trading day
        return
    after_close = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 5)
    if not after_close:
        return
    if _schwab_snapshot_last_date.get("date") == today:
        return
    from brokers.schwab_accounts import get_schwab_accounts
    from brokers.schwab_auth import token_exists

    if not token_exists():
        return
    data = await get_schwab_accounts(force=True)
    if data.get("authenticated"):
        _schwab_snapshot_last_date["date"] = today
        logger.info("Schwab EOD equity snapshot captured for %s", today)
