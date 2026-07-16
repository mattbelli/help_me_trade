"""Recent alert ring buffer, notification inbox, and REST API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import market_hub
from brokers.market_stream import MarketStreamHub
from db.notifications import (
    create_notification,
    dismiss,
    init_notifications_tables,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)


@pytest.fixture
def auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-secret-token"
    monkeypatch.setattr("api.auth.API_TOKEN", token)
    return token


@pytest.fixture
def notifications_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "notifications.db"
    init_notifications_tables(db)
    from db import base as db_base

    monkeypatch.setattr(db_base, "DEFAULT_DB_PATH", db)
    monkeypatch.setenv("TRADING_DB_PATH", str(db))
    return db


@pytest.fixture
def notification_client(
    notifications_db: Path, auth_token: str
) -> TestClient:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers.update({"X-API-Token": auth_token})
        yield client


# ---------------------------------------------------------------------------
# Legacy alert ring buffer (/api/alerts/recent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broadcast_alert_enriches_and_records() -> None:
    hub = MarketStreamHub()
    await hub.broadcast_alert(
        {"source": "rule", "rule_name": "X", "action": {"type": "alert"}}
    )
    alerts = hub.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["rule_name"] == "X"
    assert alerts[0]["id"]
    assert alerts[0]["ts"]


@pytest.mark.asyncio
async def test_recent_alerts_ring_buffer_newest_first() -> None:
    hub = MarketStreamHub()
    for i in range(55):
        await hub.broadcast_alert({"source": "rule", "rule_name": f"r{i}"})
    alerts = hub.recent_alerts()
    assert len(alerts) == 50
    assert alerts[0]["rule_name"] == "r54"
    assert alerts[-1]["rule_name"] == "r5"


def test_api_alerts_recent_returns_buffer() -> None:
    from api.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        market_hub._recent_alerts.clear()
        market_hub._recent_alerts.append(
            {
                "id": "alert-test123",
                "ts": "2026-06-19T12:00:00+00:00",
                "source": "rule",
                "rule_name": "Test Rule",
                "action": {"type": "alert"},
            }
        )
        response = client.get("/api/alerts/recent")

    assert response.status_code == 200
    body = response.json()
    assert "alerts" in body
    ids = {item["id"] for item in body["alerts"]}
    assert "alert-test123" in ids
    match = next(a for a in body["alerts"] if a["id"] == "alert-test123")
    assert match["rule_name"] == "Test Rule"


# ---------------------------------------------------------------------------
# db/notifications CRUD
# ---------------------------------------------------------------------------

def test_create_notification_returns_parsed_data(notifications_db: Path) -> None:
    row = create_notification(
        kind="price_alert",
        title="SPY crossed $580",
        body="Prior-session VAL",
        data={"symbol": "SPY", "level": 580},
        db_path=notifications_db,
    )
    assert row["id"]
    assert len(row["id"]) == 12
    assert row["kind"] == "price_alert"
    assert row["data"] == {"symbol": "SPY", "level": 580}
    assert row["read"] is False
    assert row["read_at"] is None


def test_list_notifications_newest_first_and_filters(notifications_db: Path) -> None:
    create_notification(
        kind="system",
        title="First",
        account_id="acct_a",
        strategy_id="strat_1",
        db_path=notifications_db,
    )
    second = create_notification(
        kind="system",
        title="Second",
        account_id="acct_a",
        strategy_id="strat_2",
        db_path=notifications_db,
    )
    create_notification(
        kind="system",
        title="Third",
        account_id="acct_b",
        db_path=notifications_db,
    )

    all_rows = list_notifications(db_path=notifications_db)
    assert [r["title"] for r in all_rows] == ["Third", "Second", "First"]

    limited = list_notifications(limit=2, db_path=notifications_db)
    assert len(limited) == 2
    assert limited[0]["title"] == "Third"

    by_account = list_notifications(account_id="acct_a", db_path=notifications_db)
    assert {r["title"] for r in by_account} == {"First", "Second"}

    by_strategy = list_notifications(strategy_id="strat_2", db_path=notifications_db)
    assert len(by_strategy) == 1
    assert by_strategy[0]["title"] == "Second"

    before = list_notifications(
        before=second["created_at"],
        db_path=notifications_db,
    )
    assert [r["title"] for r in before] == ["First"]


def test_mark_read_idempotent(notifications_db: Path) -> None:
    row = create_notification(kind="fill", title="Filled", db_path=notifications_db)
    updated = mark_read(row["id"], db_path=notifications_db)
    assert updated is not None
    assert updated["read"] is True
    assert updated["read_at"] is not None

    again = mark_read(row["id"], db_path=notifications_db)
    assert again is not None
    assert again["read_at"] == updated["read_at"]


def test_mark_all_read_returns_count(notifications_db: Path) -> None:
    create_notification(
        kind="system",
        title="A",
        account_id="acct_x",
        db_path=notifications_db,
    )
    create_notification(
        kind="system",
        title="B",
        account_id="acct_x",
        db_path=notifications_db,
    )
    create_notification(
        kind="system",
        title="C",
        account_id="acct_y",
        db_path=notifications_db,
    )

    count = mark_all_read(account_id="acct_x", db_path=notifications_db)
    assert count == 2
    assert unread_count(account_id="acct_x", db_path=notifications_db) == 0
    assert unread_count(account_id="acct_y", db_path=notifications_db) == 1


def test_unread_count_excludes_read_and_dismissed(notifications_db: Path) -> None:
    unread = create_notification(kind="system", title="Unread", db_path=notifications_db)
    read_row = create_notification(kind="system", title="Read", db_path=notifications_db)
    dismissed_row = create_notification(
        kind="system", title="Dismissed", db_path=notifications_db
    )

    mark_read(read_row["id"], db_path=notifications_db)
    dismiss(dismissed_row["id"], db_path=notifications_db)

    assert unread_count(db_path=notifications_db) == 1
    assert unread["id"] in {r["id"] for r in list_notifications(db_path=notifications_db)}


def test_dismiss_sets_dismissed_at(notifications_db: Path) -> None:
    row = create_notification(kind="system", title="Dismiss me", db_path=notifications_db)
    updated = dismiss(row["id"], db_path=notifications_db)
    assert updated is not None
    assert updated["dismissed"] is True
    assert updated["dismissed_at"] is not None

    hidden = list_notifications(db_path=notifications_db)
    assert hidden == []

    visible = list_notifications(include_dismissed=True, db_path=notifications_db)
    assert len(visible) == 1


# ---------------------------------------------------------------------------
# signals/notify()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_persists_broadcasts_and_pushes(
    notifications_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broadcasts: list[dict] = []
    push_calls: list[dict] = []

    async def _broadcast(payload: dict) -> None:
        broadcasts.append(payload)

    async def _push(title: str, body: str, *, data=None, db_path=None) -> int:
        push_calls.append(
            {"title": title, "body": body, "data": data, "db_path": db_path}
        )
        return 1

    monkeypatch.setattr("brokers.market_stream.market_hub.broadcast_alert", _broadcast)
    monkeypatch.setattr("signals.push.send_alert_push", _push)

    from signals.notify import notify

    row = await notify(
        kind="proposal",
        title="Trade proposal",
        body="Buy 10 SPY",
        data={"symbol": "SPY"},
        db_path=notifications_db,
    )

    assert row["kind"] == "proposal"
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "notification"
    assert broadcasts[0]["id"] == row["id"]
    assert len(push_calls) == 1
    assert push_calls[0]["title"] == "Trade proposal"
    assert push_calls[0]["data"]["type"] == "notification"
    assert push_calls[0]["data"]["notification_id"] == row["id"]
    assert push_calls[0]["data"]["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_notify_survives_broadcast_failure(
    notifications_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(_payload: dict) -> None:
        raise RuntimeError("ws down")

    monkeypatch.setattr("brokers.market_stream.market_hub.broadcast_alert", _boom)
    monkeypatch.setattr("signals.push.send_alert_push", lambda *a, **k: 0)

    from signals.notify import notify

    row = await notify(kind="system", title="Still saved", db_path=notifications_db)
    assert row["title"] == "Still saved"
    stored = list_notifications(db_path=notifications_db)
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_notify_survives_push_failure(
    notifications_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _noop(_payload: dict) -> None:
        return None

    monkeypatch.setattr("brokers.market_stream.market_hub.broadcast_alert", _noop)

    async def _boom(*_a, **_k) -> int:
        raise RuntimeError("push down")

    monkeypatch.setattr("signals.push.send_alert_push", _boom)

    from signals.notify import notify

    row = await notify(kind="system", title="Still saved", db_path=notifications_db)
    assert row["title"] == "Still saved"
    assert len(list_notifications(db_path=notifications_db)) == 1


@pytest.mark.asyncio
async def test_notify_soon_schedules_task(
    notifications_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = asyncio.Event()

    async def _notify(**kwargs):
        done.set()
        return {"id": "scheduled"}

    monkeypatch.setattr("signals.notify.notify", _notify)

    from signals.notify import notify_soon

    notify_soon(kind="system", title="Async", db_path=notifications_db)
    await asyncio.wait_for(done.wait(), timeout=1.0)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

def test_api_list_notifications(notification_client: TestClient, notifications_db: Path) -> None:
    create_notification(kind="system", title="Hello", db_path=notifications_db)

    response = notification_client.get("/api/notifications")
    assert response.status_code == 200
    body = response.json()
    assert body["unread"] == 1
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["title"] == "Hello"


def test_api_mark_read(notification_client: TestClient, notifications_db: Path) -> None:
    row = create_notification(kind="system", title="Read me", db_path=notifications_db)

    response = notification_client.post(f"/api/notifications/{row['id']}/read")
    assert response.status_code == 200
    updated = response.json()["notification"]
    assert updated["read"] is True

    missing = notification_client.post("/api/notifications/missing-id/read")
    assert missing.status_code == 404


def test_api_mark_all_read(notification_client: TestClient, notifications_db: Path) -> None:
    create_notification(
        kind="system",
        title="A",
        account_id="acct_1",
        db_path=notifications_db,
    )
    create_notification(
        kind="system",
        title="B",
        account_id="acct_1",
        db_path=notifications_db,
    )

    response = notification_client.post(
        "/api/notifications/read-all",
        json={"account_id": "acct_1"},
    )
    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert notification_client.get("/api/notifications/unread-count").json()["unread"] == 0


def test_api_dismiss(notification_client: TestClient, notifications_db: Path) -> None:
    row = create_notification(kind="system", title="Dismiss", db_path=notifications_db)

    response = notification_client.post(f"/api/notifications/{row['id']}/dismiss")
    assert response.status_code == 200
    assert response.json()["notification"]["dismissed"] is True

    missing = notification_client.post("/api/notifications/missing-id/dismiss")
    assert missing.status_code == 404


def test_api_unread_count(notification_client: TestClient, notifications_db: Path) -> None:
    create_notification(kind="system", title="One", db_path=notifications_db)

    response = notification_client.get("/api/notifications/unread-count")
    assert response.status_code == 200
    assert response.json()["unread"] == 1


@pytest.mark.asyncio
async def test_notify_inbox_only_kinds_never_push(
    notifications_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambient kinds are demoted to inbox-only at the chokepoint even when the
    caller asks to push; warning/critical severity escapes the demotion."""
    push_calls: list[str] = []

    async def _broadcast(payload: dict) -> None:
        pass

    async def _push(title: str, body: str, *, data=None, db_path=None) -> int:
        push_calls.append(title)
        return 1

    monkeypatch.setattr("brokers.market_stream.market_hub.broadcast_alert", _broadcast)
    monkeypatch.setattr("signals.push.send_alert_push", _push)

    from signals.notify import notify

    row = await notify(
        kind="rvol", title="RVOL spike", push=True, db_path=notifications_db
    )
    assert row["kind"] == "rvol"
    assert push_calls == []  # demoted: stored + broadcast, no phone buzz

    await notify(
        kind="rvol",
        title="RVOL source down",
        severity="warning",
        push=True,
        db_path=notifications_db,
    )
    assert push_calls == ["RVOL source down"]  # a real problem still pushes

    await notify(
        kind="price_alert", title="SPY crossed 740", db_path=notifications_db
    )
    assert push_calls[-1] == "SPY crossed 740"  # phone tier untouched
