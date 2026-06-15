"""
Fetch account summary from Alpaca and print as JSON.

Run: python -m scripts.fetch_account
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from scripts._broker_cli import run_fetch


def main() -> int:
    return run_fetch(lambda broker: asdict(broker.get_account()))


if __name__ == "__main__":
    sys.exit(main())
