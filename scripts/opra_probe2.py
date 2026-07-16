"""Read-only probes P-2 and P-4 from the options-flow design doc (§8). All pure GETs.

P-2: does the trading-API contracts endpoint return open_interest? (needed for UV-1 vol/OI)
P-4: does a chain snapshot carry daily volume? (would simplify Phase-1 active-set ranking)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

H = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
}
DATA = os.environ.get("APCA_DATA_BASE_URL", "https://data.alpaca.markets")
TRADING = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")


def get(base, path, params=None):
    r = httpx.get(base + path, headers=H, params=params or {}, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_raw": r.text[:200]}


def main():
    print(f"TRADING base={TRADING}\nDATA base={DATA}\n")

    # P-2: contracts + open_interest
    code, data = get(TRADING, "/v2/options/contracts",
                     {"underlying_symbols": "SPY", "limit": 3})
    print(f"[P-2 /v2/options/contracts] HTTP {code}")
    contracts = (data or {}).get("option_contracts") or (data or {}).get("contracts") or []
    if contracts:
        c = contracts[0]
        print(f"  keys: {sorted(c.keys())}")
        print(f"  open_interest={c.get('open_interest')!r}  oi_date={c.get('open_interest_date')!r}  "
              f"symbol={c.get('symbol')!r}  strike={c.get('strike_price')!r}  exp={c.get('expiration_date')!r}")
    else:
        print("  no contracts:", str(data)[:300])

    # P-4: snapshot full key dump for one liquid contract
    code2, snap = get(DATA, "/v1beta1/options/snapshots/SPY", {"feed": "opra", "limit": 40})
    print(f"\n[P-4 snapshots/SPY] HTTP {code2}")
    snaps = (snap or {}).get("snapshots", {})
    if snaps:
        occ, s = next(iter(snaps.items()))
        print(f"  sample contract: {occ}")
        print(f"  top-level keys: {sorted(s.keys())}")
        for k in ("dailyBar", "minuteBar", "prevDailyBar"):
            if k in s:
                print(f"  {k}: {json.dumps(s[k])[:160]}")
        if "dailyBar" not in s and "minuteBar" not in s:
            print("  -> no bar in snapshot; active-set ranking needs a separate bars/trades call")
    else:
        print("  no snapshots:", str(snap)[:200])


if __name__ == "__main__":
    main()
