"""
Fetch open positions from Alpaca and print as JSON.

Positions are filled holdings only — pending orders are not positions.
Run: python -m scripts.fetch_positions
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from scripts._broker_cli import run_fetch


def main() -> int:
    def fetch(broker):
        positions = broker.get_positions()
        return {
            "position_count": len(positions),
            "positions": [asdict(p) for p in positions],
        }

    return run_fetch(fetch)


if __name__ == "__main__":
    sys.exit(main())
