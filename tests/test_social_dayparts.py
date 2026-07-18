from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from db.social_mentions import get_buzz, init_social_mentions_tables, record_mentions
from trading.market_hours import _ET


def test_day_part_pooling_baseline_math(tmp_path: Path) -> None:
    db = tmp_path / "social.db"
    init_social_mentions_tables(db)
    for day in ("2026-07-14", "2026-07-15", "2026-07-16"):
        record_mentions({"TSLA": {"reddit": 10}}, bucket=f"{day}T14", db_path=db)
        record_mentions({"TSLA": {"reddit": 30}}, bucket=f"{day}T15", db_path=db)
    record_mentions(
        {"TSLA": {"reddit": 80}},
        bucket="2026-07-17T14",
        db_path=db,
    )
    buzz = get_buzz(
        "TSLA",
        db_path=db,
        now=datetime(2026, 7, 17, 14, 30, tzinfo=_ET),
    )
    assert buzz["day_part"] == "close"
    assert buzz["baseline_days"] == 3
    assert buzz["baseline_hour"] == pytest.approx(20.0)
    assert buzz["anomaly_ratio"] == pytest.approx(4.0)
