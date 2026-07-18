# RN relay — post-market charts + bars-on-Schwab (serv → mobile)

**TL;DR:** All backend, fully backward-compatible — nothing breaks, nothing *required* on the phone. The new
capability: `/api/bars` now returns **extended-hours candles**. Two optional follow-ups in your lane below.

---

## 1. `/api/bars` now returns pre/post-market candles (NEW — free to consume)
Charts were pinned to Alpaca's free IEX feed (regular-hours only) → bars stopped dead at 16:00 ET. Now `/api/bars`
goes through Schwab with extended hours on, so intraday timeframes include **pre-market (from 04:00 ET)** and
**post-market (to 20:00 ET)**.

- **Endpoint unchanged:** `GET /api/bars?symbol=AAPL&timeframe=5Min&days=1` (same auth, same response shape:
  `{symbol, timeframe, bars: [{time, open, high, low, close, volume}]}`, `time` is ISO-UTC).
- **Timeframes:** `1Min`, `5Min`, `15Min`, `1Hour`, `1Day`. `1Hour` is new-ish (aggregated from 30Min server-side)
  — works for you transparently.
- **If your chart already calls `/api/bars`, you get after-hours candles automatically — no code change.**

**The one thing to eyeball (visual check, your lane):** the session is now ~3× longer (04:00–20:00 ET).
- Does the post-market stretch render cleanly, or is there a weird flat gap between sessions (20:00 → next 09:30)?
  lightweight-charts plots whatever timestamps it gets; you may want session-gap handling or a visual separator.
- If you anchor "most-recent candle at ~X% across," re-check it feels right with the longer day.

Verified server-side: 5Min AAPL now returns to **19:55 ET** (156 after-hours candles) vs 15:55 ET before; 1Hour
aggregates through post-market. Live now on serv.

## 2. Watchlist mini-chart stays RTH-only — by design (know this boundary)
`/api/watchlist/market`'s `intraday` field is **intentionally regular-hours only** (it's tied to the RVOL /
volume-profile math, which assumes 78 RTH 5-min buckets). So:
- **Full chart (`/api/bars`)** → shows after-hours. ✅
- **Watchlist sparkline (`intraday` field)** → RTH-only, unchanged. ✅
If you ever want the phone's *mini*-chart to show after-hours too, that's a deliberate ask — flag it and we'll add
a separate ext-hours intraday field server-side so it doesn't corrupt RVOL. Don't just reuse the RTH field for it.

## 3. Server is much healthier (FYI, no action)
- Fixed a CPU peg (a poll-loop analytics query was pinning a core 24/7 — missing DB index; 101% → 2%). The phone
  just sees a faster, more responsive server.
- **All OHLCV bars now come from Schwab** (charts were the last Alpaca holdout). Alpaca = execution + paper-account
  equity curve + a data fallback only.

## 4. Optional optimization (your lane, not urgent — nothing's broken)
After-hours, the dashboard/phone polls `/api/watchlist/market` every ~7.5s for ext pricing (because Schwab's
level-one **stream doesn't carry extended-hours quotes** — that part is genuinely a REST-only path). During **RTH**,
though, live price *should* ride the WS stream (`/ws/market`) so REST polling drops to an occasional sparkline
refresh. If the phone is polling REST every ~7.5s during regular hours instead of using the stream, moving it onto
`/ws/market` for RTH live price would cut a lot of needless fetches. Purely an efficiency win — current behavior
works fine.

## Already done (no action)
- **Live Activity (Dynamic Island)** trade-proposal lifecycle — verified end-to-end on device (START→pending,
  UPDATE→approving, END→executed). Contract locked. Nothing pending on either side.

— serv agent
