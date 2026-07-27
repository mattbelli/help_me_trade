"""The slow-write-transaction logger: fires past the threshold, silent under it."""

import logging
import time

import db.base as db_base


def _do_txn(tmp_path, hold_s: float) -> None:
    conn = db_base.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (1)")
        if hold_s:
            time.sleep(hold_s)
        conn.commit()
    finally:
        conn.close()


def test_slow_txn_logged_with_caller_stack(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db_base, "SLOW_TXN_MS", 50)
    with caplog.at_level(logging.WARNING, logger="db.base"):
        _do_txn(tmp_path, hold_s=0.1)
    msgs = [r.message for r in caplog.records if "slow write txn" in r.message]
    assert msgs, "expected a slow-txn warning"
    # The stack summary must name this test module — the txn's real owner.
    assert "test_db_base_slow_txn" in msgs[0]


def test_fast_txn_stays_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db_base, "SLOW_TXN_MS", 500)
    with caplog.at_level(logging.WARNING, logger="db.base"):
        _do_txn(tmp_path, hold_s=0.0)
    assert not [r for r in caplog.records if "slow write txn" in r.message]


def test_rollback_resets_the_stamp(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(db_base, "SLOW_TXN_MS", 50)
    conn = db_base.connect(tmp_path / "t.db")
    try:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.commit()
        conn.execute("INSERT INTO x VALUES (1)")
        time.sleep(0.1)
        conn.rollback()
        with caplog.at_level(logging.WARNING, logger="db.base"):
            conn.execute("INSERT INTO x VALUES (2)")
            conn.commit()
    finally:
        conn.close()
    assert not [r for r in caplog.records if "slow write txn" in r.message]
