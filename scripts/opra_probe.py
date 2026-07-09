"""Read-only probe of the Alpaca OPRA options feed on our current subscription.

Confirms: (1) OPRA entitlement (Algo Trader Plus) actually returns data, (2) whether it's
real-time vs delayed, (3) whether trade prints carry the fields sweep-detection needs
(exchange 'x', conditions 'c', size 's'). No orders, no writes — pure GET.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE = "https://data.alpaca.markets"
H = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
}
UNDERLYING = "SPY"


def _age(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - t).total_seconds()
        return f"{secs/60:.1f} min ago" if secs < 36000 else f"{secs/3600:.1f} h ago"
    except Exception:
        return ts


def get(path: str, params: dict) -> tuple[int, dict]:
    r = httpx.get(BASE + path, headers=H, params=params, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_raw": r.text[:200]}


def main() -> None:
    print(f"probing OPRA for {UNDERLYING}  (now={datetime.now(timezone.utc).isoformat(timespec='seconds')})\n")

    # 1) chain snapshot via OPRA
    code, data = get(f"/v1beta1/options/snapshots/{UNDERLYING}", {"feed": "opra", "limit": 100})
    print(f"[snapshots feed=opra] HTTP {code}")
    snaps = data.get("snapshots", {}) if isinstance(data, dict) else {}
    if code != 200 or not snaps:
        print("  no OPRA snapshots returned:", str(data)[:300])
    else:
        print(f"  contracts returned: {len(snaps)}")
        # pick the contract whose latest trade is largest size (proxy for 'active')
        def size_of(s):
            return (s.get("latestTrade") or {}).get("s") or 0
        occ, snap = max(snaps.items(), key=lambda kv: size_of(kv[1]))
        lt, lq, gk = snap.get("latestTrade"), snap.get("latestQuote"), snap.get("greeks")
        iv = snap.get("impliedVolatility")
        print(f"  sample contract: {occ}")
        print(f"    latestTrade: {lt}")
        print(f"    latestQuote: {lq}")
        print(f"    greeks: {gk}   IV: {iv}")
        if lt:
            print(f"    -> price={lt.get('p')} size={lt.get('s')} exch={lt.get('x')!r} "
                  f"conds={lt.get('c')!r} age={_age(lt.get('t'))}")

        # 2) individual trade prints for that contract via OPRA
        code2, td = get(f"/v1beta1/options/trades/{occ}", {"feed": "opra", "limit": 10})
        print(f"\n[trades feed=opra] HTTP {code2}")
        trades = td.get("trades", []) if isinstance(td, dict) else []
        if not trades:
            print("  no trades:", str(td)[:200])
        else:
            print(f"  {len(trades)} recent prints (t=time, p=price, s=size, x=exch, c=conditions):")
            for t in trades[-6:]:
                print(f"    {t.get('t','')[:23]}  p={t.get('p')}  s={t.get('s')}  "
                      f"x={t.get('x')!r}  c={t.get('c')!r}")

    # 3) contrast: what does the free indicative feed give?
    code3, data3 = get(f"/v1beta1/options/snapshots/{UNDERLYING}", {"feed": "indicative", "limit": 5})
    isn = data3.get("snapshots", {}) if isinstance(data3, dict) else {}
    print(f"\n[snapshots feed=indicative] HTTP {code3}  contracts={len(isn)}")


if __name__ == "__main__":
    main()
