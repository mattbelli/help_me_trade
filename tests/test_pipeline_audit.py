"""Tests for the silence-killer layer (spec §5 / §8 item 4).

Covers: a synthetic parse-fail streak fires exactly one ops_alert (deduped); a
missing canary receipt fires critical; the canary registers into the synthetic
namespace summaries filter; notify(severity="critical") bypasses inbox-only
demotion (asserted against the real notify.py logic); and every rule degrades
gracefully on an empty lane_stats — no alarms, no crashes.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from db import lane_stats
from db.base import connect as _connect
from signals import pipeline_audit as pa

_ET = ZoneInfo("America/New_York")


def _seed(db_path, lane: str, date_str: str, counter: str, value: float) -> None:
    """Write a lane_stats counter for an arbitrary date (bump only writes today)."""
    lane_stats.init_lane_stats_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO lane_stats (lane, date, counter, value, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(lane, date, counter) DO UPDATE SET value = excluded.value",
            (lane, date_str, counter, float(value), "seed"),
        )
        conn.commit()


def _now_at(hour: int, minute: int = 0) -> datetime:
    """Real ET today at a controlled clock time, so bump()'s et_today() matches
    the audit's now_et.date() (production always runs on real now)."""
    return datetime.now(_ET).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


@pytest.mark.asyncio
async def test_parse_fail_streak_fires_one_ops_alert(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    # Two consecutive trailing trading days at a parse-fail rate of 0.75 (> 0.5).
    for d in pa._trailing_trading_days(today, pa.PARSE_FAIL_STREAK_DAYS):
        _seed(db, "conviction_pass", d.isoformat(), "parse_fail", 6)
        _seed(db, "conviction_pass", d.isoformat(), "ok", 2)

    calls: list[dict] = []

    async def fake_notify(**kw):
        calls.append(kw)
        return {}

    monkeypatch.setattr(pa, "notify", fake_notify)

    # 10:00 ET — before the canary window, so only rate rules can fire.
    await pa.run_audit(_now_at(10), db)

    ops = [c for c in calls if c["kind"] == "ops_alert"]
    assert len(ops) == 1
    assert ops[0]["severity"] == "warning"
    assert "conviction_parse_fail" in ops[0]["title"]

    # Deduped: a second pass the same day fires nothing new.
    calls.clear()
    await pa.run_audit(_now_at(10), db)
    assert calls == []


@pytest.mark.asyncio
async def test_missing_canary_receipt_fires_critical(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(pa, "is_trading_day", lambda d: True)

    async def fake_push(*args, **kwargs):
        return 0  # push failed / no device → NO receipt recorded

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    def fake_register(**kwargs):
        return 1

    monkeypatch.setattr("db.idea_outcomes.register_idea", fake_register, raising=False)

    notifs: list[dict] = []

    async def fake_notify(**kw):
        notifs.append(kw)
        return {}

    monkeypatch.setattr(pa, "notify", fake_notify)

    # 15:00 launch (send fails → no receipt), then 15:45 verify → critical page.
    await pa.run_audit(_now_at(15, 0), db)
    await pa.run_audit(_now_at(15, 45), db)

    crit = [c for c in notifs if c["severity"] == "critical"]
    assert len(crit) == 1
    assert crit[0]["kind"] == "ops_canary"

    # Deduped: a later verify pass the same day does not re-page.
    notifs.clear()
    await pa.run_audit(_now_at(15, 50), db)
    assert [c for c in notifs if c["severity"] == "critical"] == []


@pytest.mark.asyncio
async def test_present_canary_receipt_no_critical(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(pa, "is_trading_day", lambda d: True)

    async def fake_push(*args, **kwargs):
        return 1  # push succeeded → the launch records a receipt

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)
    monkeypatch.setattr(
        "db.idea_outcomes.register_idea", lambda **kw: 1, raising=False
    )

    notifs: list[dict] = []

    async def fake_notify(**kw):
        notifs.append(kw)
        return {}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(15, 0), db)
    await pa.run_audit(_now_at(15, 45), db)

    assert [c for c in notifs if c["severity"] == "critical"] == []


@pytest.mark.asyncio
async def test_canary_registers_synthetic_namespace(tmp_path, monkeypatch):
    """Canary rows are invisible to summaries because they register with
    synthetic=1 (the namespace every summary read path filters out)."""
    db = tmp_path / "t.db"
    monkeypatch.setattr(pa, "is_trading_day", lambda d: True)

    async def fake_push(*args, **kwargs):
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    reg_calls: list[dict] = []

    def fake_register(**kwargs):
        reg_calls.append(kwargs)
        return 1

    monkeypatch.setattr("db.idea_outcomes.register_idea", fake_register, raising=False)

    async def fake_notify(**kw):
        return {}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(15, 0), db)

    assert len(reg_calls) == 1
    assert reg_calls[0]["origin"] == "canary"
    assert reg_calls[0]["synthetic"] == 1


@pytest.mark.asyncio
async def test_critical_notify_bypasses_inbox_only(tmp_path, monkeypatch):
    """Asserts the real notify.py logic: an inbox-only kind at severity=critical
    still pushes; the same kind at severity=info is demoted to inbox."""
    from signals import notify as notify_mod

    db = tmp_path / "t.db"

    sent: list = []

    async def fake_send(title, body, *, data=None, db_path=None):
        sent.append(data)
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_send)

    async def fake_ws(payload):
        return None

    monkeypatch.setattr(notify_mod, "_broadcast_ws", fake_ws)

    # "idea_resolved" is an INBOX_ONLY_KINDS member.
    await notify_mod.notify(
        kind="idea_resolved", title="t", severity="critical", push=True, db_path=db
    )
    assert len(sent) == 1

    sent.clear()
    await notify_mod.notify(
        kind="idea_resolved", title="t", severity="info", push=True, db_path=db
    )
    assert sent == []


@pytest.mark.asyncio
async def test_zero_history_no_alarms(tmp_path, monkeypatch):
    db = tmp_path / "t.db"

    calls: list[dict] = []

    async def fake_notify(**kw):
        calls.append(kw)
        return {}

    monkeypatch.setattr(pa, "notify", fake_notify)

    # Empty lane_stats, before the canary window → no rule may fire, no crash.
    await pa.run_audit(_now_at(10), db)
    assert calls == []


def test_rules_return_none_on_empty(tmp_path):
    """The pure rule functions never raise and return None on an empty DB."""
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    assert pa.rule_conviction_parse_fail(today, db) is None
    assert pa.rule_setup_scan_silent(today, db) is None
    assert pa.rule_idea_tracker_silent(today, db) is None
    assert pa.rule_path_a_register_failures(today, db) is None
    assert pa.rule_max_open_age(today, db) is None
    assert pa.conservation_gap(today, db) is None


def test_conservation_gap_detects_imbalance(tmp_path):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    # 10 assessed, only 8 accounted for → 2 fell through an uncounted path.
    _seed(db, "setup_scan", today.isoformat(), "candidates_assessed", 10)
    _seed(db, "setup_scan", today.isoformat(), "structure", 3)
    _seed(db, "setup_scan", today.isoformat(), "conviction", 2)
    _seed(db, "setup_scan", today.isoformat(), "vetoed", 2)
    _seed(db, "setup_scan", today.isoformat(), "rejected_by_validation", 1)
    msg = pa.conservation_gap(today, db)
    assert msg is not None
    assert "conservation imbalance" in msg

    # Balanced → no alarm.
    _seed(db, "setup_scan", today.isoformat(), "rejected_by_validation", 3)
    assert pa.conservation_gap(today, db) is None


def test_path_a_failures_pure_signal(tmp_path):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    assert pa.rule_path_a_register_failures(today, db) is None
    _seed(db, "path_a_register", today.isoformat(), "failures", 1)
    assert pa.rule_path_a_register_failures(today, db) is not None
