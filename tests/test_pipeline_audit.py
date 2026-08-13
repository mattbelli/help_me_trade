"""Tests for the silence-killer layer (spec §5 / §8 item 4).

Covers: a synthetic parse-fail streak fires exactly one ops_alert (deduped); a
missing canary receipt fires critical; the canary registers into the synthetic
namespace summaries filter; notify(severity="critical") bypasses inbox-only
demotion (asserted against the real notify.py logic); and every rule degrades
gracefully on an empty lane_stats — no alarms, no crashes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
        return {"id": "persisted"}

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

    # Registration runs for REAL against the tmp DB — the verify pass now also
    # probes the ledger leg, and a mocked-away register would (correctly) page.
    notifs: list[dict] = []

    async def fake_notify(**kw):
        notifs.append(kw)
        return {"id": "persisted"}

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
    # Real registration into the tmp DB — the ledger leg must be green too.

    notifs: list[dict] = []

    async def fake_notify(**kw):
        notifs.append(kw)
        return {"id": "persisted"}

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
        return {"id": "persisted"}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(15, 0), db)

    assert len(reg_calls) == 1
    assert reg_calls[0]["origin"] == "canary"
    assert reg_calls[0]["synthetic"] == 1


@pytest.mark.asyncio
async def test_canary_register_retries_through_lock_burst(tmp_path, monkeypatch):
    """2026-07-23 incident: a >5s writer starved the busy timeout and the canary
    row was lost while its push still receipted. The launch now rides out a
    seconds-scale lock burst with bounded retries."""
    import sqlite3

    db = tmp_path / "t.db"
    monkeypatch.setattr(pa, "is_trading_day", lambda d: True)

    async def fake_push(*args, **kwargs):
        return 1

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)

    async def _nosleep(_s):
        return None

    monkeypatch.setattr(pa.asyncio, "sleep", _nosleep)

    attempts: list[int] = []

    def flaky_register(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return 1

    monkeypatch.setattr("db.idea_outcomes.register_idea", flaky_register, raising=False)

    async def fake_notify(**kw):
        return {"id": "persisted"}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(15, 0), db)
    assert len(attempts) == 3  # two locked failures, third succeeds


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
        return {"id": "persisted"}

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


def test_silent_rule_uses_trailing_observed_day_median(tmp_path):
    # The rules judge the last COMPLETED trading day, never today-in-progress,
    # so the collapse is seeded on the judged day and the baseline on the days
    # before it.
    today = date(2026, 7, 17)
    assert pa.is_trading_day(today)
    judged = pa._last_completed_trading_day(today)
    history = pa._trailing_trading_days(judged - timedelta(days=1), 10)

    collapsed_db = tmp_path / "collapsed.db"
    normal_db = tmp_path / "normal.db"
    for d in history:
        for db in (collapsed_db, normal_db):
            _seed(db, "setup_scan", d.isoformat(), "ran", 8)
    _seed(collapsed_db, "setup_scan", judged.isoformat(), "ran", 0)
    _seed(normal_db, "setup_scan", judged.isoformat(), "ran", 8)

    assert (
        pa._trailing_counter_median(
            "setup_scan", "ran", judged, db_path=collapsed_db
        )
        == 8
    )
    collapsed = pa.rule_setup_scan_silent(today, collapsed_db)
    assert collapsed is not None
    assert "trailing median 8" in collapsed
    assert judged.isoformat() in collapsed
    assert pa.rule_setup_scan_silent(today, normal_db) is None


def test_collapse_rules_ignore_today_in_progress(tmp_path):
    """The first audit tick after midnight ET must not page on an empty
    "today" — the 07-21 → 08-11 incident: both silent rules fired at 00:0x ET
    every trading midnight (today's 0-so-far vs a full-day median) and burned
    the once-per-day dedup, so a real same-day death could never page."""
    db = tmp_path / "t.db"
    today = date(2026, 7, 17)  # a trading day with NO rows yet: midnight
    assert pa.is_trading_day(today)
    judged = pa._last_completed_trading_day(today)
    history = [judged] + pa._trailing_trading_days(
        judged - timedelta(days=1), 10
    )
    for d in history:
        _seed(db, "setup_scan", d.isoformat(), "ran", 1)
        _seed(db, "idea_tracker", d.isoformat(), "swept", 32)
    assert pa.rule_setup_scan_silent(today, db) is None
    assert pa.rule_idea_tracker_silent(today, db) is None


@pytest.mark.asyncio
async def test_never_worked_lane_pages_once_daily_then_stops(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    # Grace: a single quiet attempt day must NOT page (real-DB case: the scan's
    # first day had ran=1/output=0 with perfectly healthy wiring).
    _seed(db, "setup_scan", today.isoformat(), "ran", 1)
    _seed(db, "setup_scan", today.isoformat(), "registered", 0)
    assert pa.rule_never_worked(today, db) is None

    # Three distinct attempt days with zero lifetime output → page.
    for d in pa._trailing_trading_days(today, 3)[1:]:
        _seed(db, "setup_scan", d.isoformat(), "ran", 1)

    calls: list[dict] = []

    async def fake_notify(**kw):
        calls.append(kw)
        return {"id": "persisted"}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(10), db)
    await pa.run_audit(_now_at(10), db)

    never_worked = [c for c in calls if "never_worked" in c["title"]]
    assert len(never_worked) == 1
    assert "setup_scan.registered/vetoed" in never_worked[0]["body"]

    # A vetoed shadow registration is output too — quiet ≠ dead.
    _seed(db, "setup_scan", today.isoformat(), "vetoed", 1)
    assert pa.rule_never_worked(today, db) is None


@pytest.mark.asyncio
async def test_failed_notify_does_not_set_alarm_dedup(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    calls = 0

    async def failed_notify(**kw):
        nonlocal calls
        calls += 1
        raise RuntimeError("persist unavailable")

    monkeypatch.setattr(pa, "notify", failed_notify)

    kwargs = {
        "severity": "warning",
        "title": "test",
        "body": "test",
    }
    await pa._fire_alarm("retry_me", today, db, **kwargs)
    await pa._fire_alarm("retry_me", today, db, **kwargs)

    assert calls == 2
    assert (
        lane_stats.day("pipeline_audit", today.isoformat(), db_path=db).get(
            "alarm_fired:retry_me", 0
        )
        == 0
    )


def test_conservation_gap_detects_imbalance(tmp_path):
    """Seeds the EXACT counters production writes (api/main nightly bumps) —
    the candidate-level partition: structure + rejected_by_validation +
    ranker_discarded + no_structure + no_setup == candidates_assessed.
    Conviction/vetoed are deliberately NOT part of the partition."""
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    # 12 assessed: 2 structure + 1 rejected + 1 ranker-discarded + 5 no_structure
    # + 2 no_setup = 11 accounted → 1 fell through an uncounted path.
    _seed(db, "setup_scan", today.isoformat(), "candidates_assessed", 12)
    _seed(db, "setup_scan", today.isoformat(), "structure", 2)
    _seed(db, "setup_scan", today.isoformat(), "rejected_by_validation", 1)
    _seed(db, "setup_scan", today.isoformat(), "ranker_discarded", 1)
    _seed(db, "setup_scan", today.isoformat(), "no_structure", 5)
    _seed(db, "setup_scan", today.isoformat(), "no_setup", 2)
    # Conviction present but must not enter the equation (second lane).
    _seed(db, "setup_scan", today.isoformat(), "conviction_ok", 2)
    msg = pa.conservation_gap(today, db)
    assert msg is not None
    assert "conservation imbalance" in msg

    # Balanced → no alarm.
    _seed(db, "setup_scan", today.isoformat(), "no_setup", 3)
    assert pa.conservation_gap(today, db) is None


def test_tracker_silent_keys_on_swept_heartbeat(tmp_path):
    """A healthy-but-quiet grader (sweeps running, nothing maturing) must NOT
    page; a missing heartbeat with recent life MUST (review HIGH)."""
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    window = pa._trailing_trading_days(today, pa.IDEA_TRACKER_SILENT_DAYS)

    # Quiet but alive: swept every day, zero graded → no alarm.
    for d in window:
        _seed(db, "idea_tracker", d.isoformat(), "swept", 1)
        _seed(db, "idea_tracker", d.isoformat(), "graded", 0)
    assert pa.rule_idea_tracker_silent(today, db) is None

    # Dead: heartbeat absent across the window, but alive before it → alarm.
    db2 = tmp_path / "t2.db"
    older = window[-1] - timedelta(days=10)
    _seed(db2, "idea_tracker", older.isoformat(), "swept", 5)
    msg = pa.rule_idea_tracker_silent(today, db2)
    assert msg is not None
    assert "heartbeat" in msg


def test_scan_silent_keys_on_ran_heartbeat(tmp_path):
    """A scan that runs and qualifies 0 ideas is quiet, not dead — only a
    missing `ran` heartbeat pages (review finding)."""
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    window = pa._trailing_trading_days(today, pa.SETUP_SCAN_SILENT_DAYS)

    # Ran every night, registered nothing → no alarm.
    for d in window:
        _seed(db, "setup_scan", d.isoformat(), "ran", 1)
        _seed(db, "setup_scan", d.isoformat(), "registered", 0)
    assert pa.rule_setup_scan_silent(today, db) is None

    # Did not run across the window, alive before it → alarm.
    db2 = tmp_path / "t2.db"
    older = window[-1] - timedelta(days=10)
    _seed(db2, "setup_scan", older.isoformat(), "ran", 1)
    msg = pa.rule_setup_scan_silent(today, db2)
    assert msg is not None
    assert "did not run" in msg


def test_weekend_tick_judges_fridays_completed_day(tmp_path):
    """A weekend tick judges Friday (the last completed trading day): healthy
    Friday → silent all weekend; dead Friday → pages. The old rule's weekend
    guard suppressed "today" instead — now unnecessary because today-in-progress
    is never judged at all."""
    saturday = date(2026, 7, 18)
    assert not pa.is_trading_day(saturday)
    friday = pa._last_completed_trading_day(saturday)
    history = pa._trailing_trading_days(friday - timedelta(days=1), 10)

    healthy_db = tmp_path / "healthy.db"
    dead_db = tmp_path / "dead.db"
    for d in history:
        for db in (healthy_db, dead_db):
            _seed(db, "setup_scan", d.isoformat(), "ran", 1)
    _seed(healthy_db, "setup_scan", friday.isoformat(), "ran", 1)
    _seed(dead_db, "setup_scan", friday.isoformat(), "ran", 0)

    assert pa.rule_setup_scan_silent(saturday, healthy_db) is None
    msg = pa.rule_setup_scan_silent(saturday, dead_db)
    assert msg is not None
    assert friday.isoformat() in msg


def test_path_a_failures_pure_signal(tmp_path):
    db = tmp_path / "t.db"
    today = datetime.now(_ET).date()
    assert pa.rule_path_a_register_failures(today, db) is None
    _seed(db, "path_a_register", today.isoformat(), "failures", 1)
    assert pa.rule_path_a_register_failures(today, db) is not None


@pytest.mark.asyncio
async def test_dead_ledger_pages_even_with_green_receipt(tmp_path, monkeypatch):
    """A healthy push transport must not hide a dead ledger write path: when
    register_idea silently fails (fail-soft by design), the verify pass pages
    canary_ledger_row_missing (GPT-5.6 audit finding 3)."""
    db = tmp_path / "t.db"
    monkeypatch.setattr(pa, "is_trading_day", lambda d: True)

    async def fake_push(*args, **kwargs):
        return 1  # transport healthy → receipt records

    monkeypatch.setattr("signals.push.send_alert_push", fake_push)
    # Dead ledger: registration returns None (the same shape as a swallowed
    # validation failure or dedup drop).
    monkeypatch.setattr(
        "db.idea_outcomes.register_idea", lambda **kw: None, raising=False
    )

    notifs: list[dict] = []

    async def fake_notify(**kw):
        notifs.append(kw)
        return {"id": "persisted"}

    monkeypatch.setattr(pa, "notify", fake_notify)

    await pa.run_audit(_now_at(15, 0), db)
    await pa.run_audit(_now_at(15, 45), db)

    criticals = [c for c in notifs if c["severity"] == "critical"]
    assert len(criticals) == 1
    assert "ledger row missing" in criticals[0]["title"]
