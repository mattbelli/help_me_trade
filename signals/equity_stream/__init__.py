"""Market-wide equity tape collector (hmt-equity-collector).

Owns the Alpaca stocks v2 websocket (1 connection/account, measured 2026-07-15:
a second connection 406s) and drinks the wildcard tape — every US equity print
and NBBO update. Detects tape facts (blocks/dark pool via signals.equity_tape,
ISO sweeps, print-velocity spikes) market-wide instead of only the ~30 hub
symbols, and serves the API's market hub its watchlist slice back through a
local proxy speaking the Alpaca JSON protocol (signals.equity_stream.proxy).

Measured 2026-07-15 midday: trades["*"] 3.8k msgs/s + quotes["*"] 13.5k msgs/s
= ~9% of one core with full Python decode, lag p50 14ms. Facts about the tape,
never buy/sell signals — direction is labeled with its basis and caveats travel
on every event.
"""
