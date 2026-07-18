# Schwab Market-Data Integration — Scoping Report

*Saved 2026-06-23. Source: Sonnet recon agent (code read + web), salvaged from the deep-research
run. Motivation: Alpaca's free IEX feed carries only ~2.4% of real volume — useless for the
relative-volume feature. Schwab (funded, READ-ONLY) has real consolidated volume. Companion:
`RVOL_VOLUME_PROFILE_RESEARCH.md`.*

> **SAFETY:** Schwab = real FUNDED accounts. DATA + LEDGER sync ONLY — NEVER place orders against
> it. The data provider class below must simply not implement order methods (the OAuth scope is
> full-access `api`, so read-only is enforced in *our* code, not by Schwab).

## Part A — Current state (code, file:line)
- **Schwab adapter (`brokers/schwab.py`):** account-only. Wraps `schwab-py` sync `Client`;
  `fetch_snapshot()` (150), `get_account`/`get_positions`/`get_open_orders` (+ async). All
  execution stubbed — `aplace_order`/`acancel_order`/`aget_order` raise `NotImplementedError`
  (182–188). **No market-data/quote/price-history/bar capability today.**
- **Auth (`brokers/schwab_auth.py`):** OAuth via `schwab-py` `easy_client`/`client_from_token_file`
  (115, 144). Creds `SCHWAB_API_KEY`/`SCHWAB_APP_SECRET`/`SCHWAB_CALLBACK_URL` from `.env`; token
  JSON at `data/schwab_token.json` (33). **7-day refresh-token expiry**, weekly re-auth via
  `python -m scripts.schwab_login` (91). No scopes in code — governed by the dev app's products.
- **Alpaca bar contract (what Schwab must match):** `StockBar` (alpaca.py:41–48) frozen dataclass
  `{time: str ISO-8601, open, high, low, close, volume: float}`. Methods:
  `aget_stock_bars(symbol, *, timeframe="1Day", days=180)` (578),
  `aget_stock_bars_range(symbol, *, timeframe, start, end)` (615, paginates `_MAX_BAR_PAGES=50`),
  `aget_stock_bars_batch(symbols, *, timeframe="1Day", days=12)` (811) → `dict[str,list[StockBar]]`.
- **Base interface (`brokers/base.py`):** `BrokerAdapter` Protocol (71–94) has **no bar methods** —
  bars are Alpaca-only extras.
- **Consumers:** `GET /api/bars` (main.py:916, `_require_alpaca()` 869); `GET /api/watchlist/market`
  (679, two `aget_stock_bars_batch` calls: daily 12d + 5Min 2d, `_require_alpaca()`-guarded);
  `signals/scan.py:67` & `signals/trade_context.py:356` duck-type via `getattr(broker,
  "aget_stock_bars_batch", None)`; `brokers/market_stream.py:204–206` hard `isinstance(...
  AlpacaAdapter)` guard.

## Part B — Schwab market-data facts (cited)
*(developer.schwab.com spec pages are login-gated → facts cross-corroborated across schwab-py,
Schwabdev, schwab-client-js, R packages, archived official PDF.)*

1. **Price History** — `GET https://api.schwabapi.com/marketdata/v1/pricehistory`. Params:
   `symbol`, `periodType` (day/month/year/ytd), `period`, `frequencyType`
   (minute/daily/weekly/monthly), `frequency` (minute: 1,5,10,15,30), `startDate`/`endDate`
   (**epoch ms**), `needExtendedHoursData`. Returns `CandleList`
   `{candles:[{open,high,low,close,volume(long),datetime(epoch ms)}], symbol, empty,
   previousClose?}`. **1/5/15-min supported** (minute requires `periodType=day`). Depth: ~48 days
   of 1-min, ~9 months for 5–30min, daily back to 1985. **No pagination, one symbol per call**
   (`period` caps at 10d → use `startDate`/`endDate` for full depth).
   (schwab-py docs; kcjmowright PriceHistoryApi)
2. **Quotes** — `GET /marketdata/v1/quotes?symbols=A,B,C` (batch) or `/{symbol}/quotes`.
   `fields=quote` for light polling. Equity full-day volume = `totalVolume`, nested
   `resp[sym]["quote"]["totalVolume"]`. **~100 symbols/call confirmed, no documented REST cap**
   ("500" is the WebSocket-stream limit). One call = one rate unit regardless of symbol count.
   (Schwabdev/DeepWiki; docs.rs QuoteEquity)
3. **Auth/scope** — **One app, one token covers data + trading.** "Market Data Production" and
   "Accounts and Trading Production" are *product subscriptions* attached at registration, not
   OAuth scopes. OAuth scope is the literal string `api` — **no read-only scope exists**;
   read-only enforced in code. **No separate market-data fee for retail** — included with a funded
   brokerage account; real-time vs delayed inherited from the account. (schwab-py getting-started;
   Schwabdev tokens.py)
4. **Rate limits** — **~120 req/min** (community consensus). Throttle = HTTP 429; use fixed 60s +
   exponential backoff, don't parse sub-codes. Access token 30 min (auto-refreshed); **refresh
   token 7 days, fixed from issuance, non-rolling, hard server-enforced** → silent auth death is
   the key risk. (schwab-py auth)
5. **Volume quality** — Schwab dramatically better. Historical `pricehistory` = complete/reliable
   (full server-recorded volume). Quotes carry `quoteType`: `NBBO` (full SIP) vs `NFL` (real-time
   from a subset) — still far broader than Alpaca. Alpaca free = IEX-only ~2.4%; full SIP on
   Alpaca = $99/mo. Schwab full historical volume = free. Caveat: real-time *streamed* candle
   volume is snapshot-sampled (undercounts intrabar) → reload from history.

## Part C — Recommended architecture
**A separate `MarketDataProvider` abstraction, NOT bolted onto `BrokerAdapter`.** Because:
1. **Safety/separation matches the mission** — execution stays Alpaca paper; account+ledger+data on
   Schwab read-only. Bars already aren't in `BrokerAdapter`. A data provider has no order methods →
   zero surface for Schwab execution to creep in.
2. **Decouples "where I trade" from "where I get data"** — orthogonal axes.
3. **Minimal churn** — define `MarketDataProvider` with the 3 signatures already in use
   (`aget_stock_bars`, `_range`, `_batch`) returning `StockBar`. `AlpacaAdapter` already satisfies
   it; add `SchwabMarketData` mapping `pricehistory`/`quotes` → `StockBar` (epoch-ms→ISO,
   volume→float). Consumers depend on the provider, not the broker.

- **Source selection:** config flag `MARKET_DATA_PROVIDER=schwab|alpaca` (default schwab once
  validated), resolved in `brokers/factory.py` independently of the execution broker.
- **Caching / rate limits (~100 symbols):** intraday = **one batched `/quotes?symbols=...`** per
  cycle (one rate unit, well under 120/min). Historical = `pricehistory` one-symbol-per-call
  (~100 calls) → small semaphore (5–10 concurrent) + 60s backoff on 429; cache daily candles
  aggressively. For RVOL = **nightly historical intraday pull** (20–30 days of 5-min bars/symbol →
  volume-by-time-of-day profile, persisted), then intraday `totalVolume` vs profile.
- **Refresh-token safety (dad-with-a-job risk):** automate weekly headless re-auth AND wire the
  existing APNs push to alert when token age crosses ~6 days, so a failed re-auth pings the phone
  instead of blinding the agent silently.

## Risks / open questions / verify before building
- **Confirm the dev app has "Market Data Production" attached** and "Ready For Use" (not
  "Approved-Pending"). May need re-registration if only the trading product is attached. *(User
  action — gates the whole feature.)*
- **Verify SIP-completeness empirically:** diff a daily AAPL `pricehistory` volume vs the official
  consolidated close (label inferred, not doc-stated).
- **1-min depth (~30–48d) is shallow** for long volume-profile baselines → use **5-min** for
  multi-month history.
- **120/min limit & 429 sub-codes are community-sourced**, not verbatim Schwab → conservative
  backoff, don't branch on sub-codes.
- **`schwab-py easy_client` is sync** → wrap the provider's calls in `asyncio.to_thread` (as the
  existing adapter does) to keep the async contract.
- **Read-only is on us** (scope `api` = full) → the data provider must not implement order methods.

## Suggested build order
1. `SchwabMarketData` provider + `MarketDataProvider` abstraction + `MARKET_DATA_PROVIDER` flag.
2. Nightly volume-profile precompute job + storage.
3. RVOL compute + watchlist column (see `RVOL_VOLUME_PROFILE_RESEARCH.md`).
