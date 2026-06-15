"""
Fetch open (unfilled) orders from Alpaca and print as JSON.

Run: python -m scripts.fetch_orders
"""

from __future__ import annotations

import sys
from dataclasses import asdict

from scripts._broker_cli import run_fetch


def main() -> int:
    def fetch(broker):
        orders = broker.get_open_orders()
        return {
            "open_order_count": len(orders),
            "open_orders": [asdict(o) for o in orders],
        }

    return run_fetch(fetch)


if __name__ == "__main__":
    sys.exit(main())
