"""Probe Schwab 1-minute price history (incl. futures-symbol formats).

Run AFTER `python -m scripts.schwab_login`. Confirms the token works and tells
us empirically whether /ES-style futures are pullable through the Schwab API
(equities/options are; futures market-data is the open question).

Run: python -m scripts.schwab_probe_minute
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brokers.errors import BrokerError  # noqa: E402
from brokers.schwab_auth import create_client  # noqa: E402

# A known-good equity first (proves the token), then futures-symbol variants.
SYMBOLS = ["SPY", "/ES", "ES", "$ES", "/ESU25", "/MES"]


def _probe(client, symbol: str, start: datetime, end: datetime) -> str:
    try:
        resp = client.get_price_history_every_minute(
            symbol, start_datetime=start, end_datetime=end
        )
    except Exception as exc:  # noqa: BLE001 - probe: report any failure verbatim
        return f"EXC  {symbol!r}: {type(exc).__name__}: {exc}"

    status = resp.status_code
    if status != 200:
        body = resp.text[:200].replace("\n", " ")
        return f"HTTP {status} {symbol!r}: {body}"

    data = resp.json()
    candles = data.get("candles", [])
    empty = data.get("empty", None)
    if not candles:
        return f"OK-EMPTY {symbol!r}: 0 candles (empty={empty})"
    first = candles[0]
    last = candles[-1]

    def _ts(c: dict) -> str:
        return datetime.fromtimestamp(c["datetime"] / 1000, tz=UTC).isoformat()

    return (
        f"OK   {symbol!r}: {len(candles)} candles, "
        f"{_ts(first)} -> {_ts(last)}, last close={last.get('close')}"
    )


def main() -> int:
    try:
        client = create_client(interactive=False)
    except BrokerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Run `python -m scripts.schwab_login` first.", file=sys.stderr)
        return 1

    end = datetime.now(UTC)
    start = end - timedelta(days=5)
    print(f"Probing 1-min candles {start.date()}..{end.date()} (UTC)\n")
    for sym in SYMBOLS:
        print(_probe(client, sym, start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
