"""The minute poll must stay scoped and expose failed imports to the supervisor."""

from unittest.mock import Mock

import pytest

from api import schwab_jobs


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.setattr(schwab_jobs, "SCHWAB_TRADE_SYNC_ACCOUNT_IDS", frozenset({"selected"}))
    monkeypatch.setattr(
        schwab_jobs,
        "list_accounts",
        Mock(return_value=[
            {"id": "other", "broker_external_id": "other-hash"},
            {"id": "selected", "broker_external_id": "selected-hash"},
        ]),
    )
    sync = Mock(return_value={"accounts": [{"account_id": "selected", "inserted": 0}]})
    monkeypatch.setattr(schwab_jobs, "sync_schwab", sync)
    return sync


@pytest.mark.asyncio
async def test_minute_poll_only_syncs_selected_schwab_account(sync):
    await schwab_jobs._schwab_trade_sync_once()
    schwab_jobs.list_accounts.assert_called_once_with(broker="schwab", enabled_only=True)
    sync.assert_called_once_with(external_ids=["selected-hash"])


def test_empty_selection_never_falls_back_to_all_accounts(monkeypatch, sync):
    monkeypatch.setattr(schwab_jobs, "SCHWAB_TRADE_SYNC_ACCOUNT_IDS", frozenset())
    schwab_jobs._sync_schwab_selected_accounts()
    sync.assert_not_called()
    schwab_jobs.list_accounts.assert_not_called()


def test_unmapped_selection_never_falls_back_to_all_accounts(monkeypatch, sync):
    monkeypatch.setattr(schwab_jobs, "SCHWAB_TRADE_SYNC_ACCOUNT_IDS", frozenset({"unknown"}))
    with pytest.raises(RuntimeError, match="disabled or unmapped"):
        schwab_jobs._sync_schwab_selected_accounts()
    sync.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [{"incomplete": True}, {"error": "account_sync_failed"}])
async def test_failed_import_is_visible_to_job_supervisor(sync, failure):
    sync.return_value = {"accounts": [failure]}
    with pytest.raises(RuntimeError, match="minute sync failed"):
        await schwab_jobs._schwab_trade_sync_once()
