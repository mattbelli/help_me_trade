"""Poll loop health tracking for /health staleness signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class PollHealth:
    last_successful_poll_at: datetime | None = None

    def record_success(self) -> None:
        self.last_successful_poll_at = datetime.now(UTC)

    def age_seconds(self) -> float | None:
        if self.last_successful_poll_at is None:
            return None
        delta = datetime.now(UTC) - self.last_successful_poll_at
        return delta.total_seconds()

    def is_healthy(self, poll_interval_seconds: int) -> bool:
        age = self.age_seconds()
        if age is None:
            return False
        return age < 2 * poll_interval_seconds


poll_health = PollHealth()
