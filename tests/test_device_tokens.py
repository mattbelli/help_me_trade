"""Device push-token registry."""

from __future__ import annotations

from pathlib import Path

from db.base import connect
from db.device_tokens import (
    deactivate_device_token,
    init_device_token_tables,
    list_active_device_tokens,
    register_device_token,
)


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "devices.db"
    init_device_token_tables(db)
    return db


def test_register_creates_active_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    register_device_token(token="ExponentPushToken[abc]", db_path=db)

    rows = list_active_device_tokens(db_path=db)
    assert len(rows) == 1
    assert rows[0]["token"] == "ExponentPushToken[abc]"
    assert rows[0]["active"] == 1
    assert rows[0]["platform"] == "ios"
    assert rows[0]["token_type"] == "expo"


def test_reregister_upserts_same_token(tmp_path: Path) -> None:
    db = _db(tmp_path)
    register_device_token(
        token="ExponentPushToken[abc]",
        platform="ios",
        app_version="1.0.0",
        db_path=db,
    )
    register_device_token(
        token="ExponentPushToken[abc]",
        platform="android",
        app_version="2.0.0",
        db_path=db,
    )

    with connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) AS cnt FROM device_tokens").fetchone()
    assert int(count["cnt"]) == 1

    rows = list_active_device_tokens(db_path=db)
    assert len(rows) == 1
    assert rows[0]["platform"] == "android"
    assert rows[0]["app_version"] == "2.0.0"
    assert rows[0]["active"] == 1


def test_deactivate_excludes_from_active_list(tmp_path: Path) -> None:
    db = _db(tmp_path)
    register_device_token(token="ExponentPushToken[abc]", db_path=db)
    deactivate_device_token("ExponentPushToken[abc]", db_path=db)

    assert list_active_device_tokens(db_path=db) == []

    with connect(db) as conn:
        row = conn.execute(
            "SELECT active FROM device_tokens WHERE token = ?",
            ("ExponentPushToken[abc]",),
        ).fetchone()
    assert row is not None
    assert row["active"] == 0


def test_list_filters_by_token_type(tmp_path: Path) -> None:
    db = _db(tmp_path)
    register_device_token(
        token="ExponentPushToken[expo1]",
        token_type="expo",
        db_path=db,
    )
    register_device_token(
        token="apns-token-1",
        token_type="apns",
        db_path=db,
    )

    expo_rows = list_active_device_tokens(token_type="expo", db_path=db)
    apns_rows = list_active_device_tokens(token_type="apns", db_path=db)

    assert len(expo_rows) == 1
    assert expo_rows[0]["token"] == "ExponentPushToken[expo1]"
    assert len(apns_rows) == 1
    assert apns_rows[0]["token"] == "apns-token-1"


def test_register_ignores_empty_token(tmp_path: Path) -> None:
    db = _db(tmp_path)
    register_device_token(token="", db_path=db)
    register_device_token(token="   ", db_path=db)

    assert list_active_device_tokens(db_path=db) == []
