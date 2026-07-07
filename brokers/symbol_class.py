"""Single source of truth for per-asset-class symbol routing.

Both the REST hybrid provider (`brokers.hybrid_market_data`) and the live-stream
hybrid hub (`brokers.hybrid_market_stream`) classify every symbol through the SAME
`classify_symbol` so routing can never diverge between the two seams.

Routing target today (Alpaca Algo Trader Plus SIP + Schwab futures):
  future  -> Schwab   (Alpaca has no futures)
  crypto  -> Alpaca   (SIP crypto; stream is an extension point — see hybrid_market_stream)
  option  -> Alpaca   (OPRA; extension point — not wired this round)
  equity  -> Alpaca   (SIP consolidated tape; ETFs are indistinguishable, same feed)
"""

from __future__ import annotations

import re

SymbolClass = str  # "future" | "crypto" | "option" | "equity"

# OSI/OCC option contract: 1-6 char root, 6-digit YYMMDD, C/P, 8-digit strike
# (strike * 1000, zero-padded). e.g. AAPL240119C00190000, SPY251219P00450000.
_OPTION_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


def classify_symbol(sym: str) -> SymbolClass:
    """Classify *sym* into one of future / crypto / option / equity.

    Order matters (first match wins):
      1. future — leading slash (/ESU25, /CL). Checked FIRST so a slash-prefixed
         future never falls into the crypto "contains a slash" rule.
      2. crypto — an internal slash (BTC/USD, ETH/USD) but no leading slash.
      3. option — a full OSI/OCC contract symbol.
      4. equity — everything else (incl. ETFs; Alpaca doesn't distinguish).
    """
    s = str(sym).strip().upper()
    if s.startswith("/"):
        return "future"
    if "/" in s:
        return "crypto"
    if _OPTION_RE.match(s):
        return "option"
    return "equity"


def rest_provider_for(sym: str) -> str:
    """Return the REST market-data provider name ('schwab' | 'alpaca') for *sym*.

    Futures are the only Schwab-routed class; every other class (equity, crypto,
    option) sources REST bars/quotes from Alpaca SIP.
    """
    return "schwab" if classify_symbol(sym) == "future" else "alpaca"
