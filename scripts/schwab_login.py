"""
One-time (or weekly) Schwab OAuth login.

Opens a browser, saves token to data/schwab_token.json.
Run: python -m scripts.schwab_login
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brokers.errors import BrokerError  # noqa: E402
from brokers.schwab_auth import run_interactive_login  # noqa: E402


def main() -> int:
    try:
        run_interactive_login()
    except BrokerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
