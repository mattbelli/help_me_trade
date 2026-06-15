"""Shared helpers for broker fetch CLI scripts."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brokers.errors import BrokerError
from brokers.factory import get_broker


def run_fetch(fetch: Callable[[object], Any]) -> int:
    try:
        broker = get_broker()
        payload = fetch(broker)
    except BrokerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2))
    return 0
