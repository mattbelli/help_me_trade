# Crypto on help_me_trade — data + venue landscape (researched 2026-08-21)

*Initial research, founder-requested. Every "measured" line below was probed LIVE from serv (US residential
IP, MA) on 2026-08-21 — not doc-derived. Three Opus research agents + orchestrator re-verification of the
load-bearing claims (Alpaca REST/WS, Binance/Bybit geo-blocks, Deribit archive, Schwab CME futures).
Context: founder holds crypto on Coinbase + Solflare (Solana), has used Binance. No connection required.*

## TL;DR
- **Yes — the data is free**, and better than expected. Alpaca crypto REST is **unauthenticated** (no key at
  all), 1-min bars to 2021-01-01, L2 orderbook, 200 req/min; the WS needs our key (works, measured 315 book
  updates + 105 quotes in 10 s on BTC at 01:30 UTC). 73 tradable pairs incl. BTC/ETH/SOL/DOGE/XRP/PEPE/BONK/
  WIF/HYPE; paper account `crypto_status: ACTIVE`.
- **Two headline finds:** (1) **`history.deribit.com`** serves the *entire* BTC/ETH **options** tick archive
  free — every print since **2016-11-29** carries IV; 120k expired BTC instruments; DVOL to 2021-03 (the only
  free options-OI/IV corpus in any asset class we have). (2) **Schwab already serves CME crypto futures**
  through our adapter: `/BTC` daily to **2017-12-18** (2,182 bars), `/ETH` to 2021-02, `/MBT` `/MET` 1-min
  (~48-day wall, same as /ES).
- **The US geo-wall is the defining constraint:** `api.binance.com` + `fapi` → **HTTP 451**; Bybit REST →
  **403** (CloudFront). Legal workarounds measured: Binance's own `data-api.binance.vision` mirror (200,
  byte-identical klines) + `data.binance.vision` bulk zips (spot 2017-08, perp funding/OI 2020-01); Bybit
  WS streams fine; Hyperliquid `predictedFundings` returns Binance+Bybit funding in one call.
- **Trading side is the weak half.** Alpaca crypto: **25 bps taker / 15 bps maker** at tier 1, only
  market/limit/stop_limit, GTC/IOC, **no brackets/OCO**, no margin, no shorting, and live eligibility is only
  **28 states** (2025-10-09 list — MA is on it; paper works everywhere). Equities-equivalent friction ×5–10.
- **Three free tiers died in 2026:** CoinCap v2 (host gone), CryptoPanic (Apr 1), CoinDesk Data (May 21).
  Don't build on tutorials that name them.

## Measured matrix (serv, 2026-08-21)

| Source | Key? | US OK | WS | History | What it uniquely gives |
|---|---|---|---|---|---|
| **Alpaca crypto** (`/v1beta3/crypto/us`) | REST no / WS yes | ✅ | ✅ | 1-min → 2021-01 | bars+trades+quotes+L2 book; same broker as our chokepoint; `us-1` = Kraken US feed, `eu-1` = Kraken EU; `global` retired |
| **Coinbase** Adv Trade + Exchange | no | ✅ | ✅ | daily → 2016-01 (350/req cap) | L2 book; **view-only key reads holdings** (`can_view` w/o `can_trade`) |
| Binance.com | — | ⛔ 451 | ⛔ | — | blocked outright |
| ↳ `data-api.binance.vision` | no | ✅ 200 | ✅ | same as global | Binance's own public mirror |
| ↳ `data.binance.vision` bulk | no | ✅ | — | spot 2017-08; UM perps + funding 2020-01; OI metrics 2020-09 | zip dumps; ⚠️ bookTicker stops 2024-04 |
| Binance.US | no | ✅ | ✅ | shallow | thin |
| **Kraken** spot | no | ✅ | ✅ v2 | OHLC **721-bar cap**; `Trades since=0` → 2013-10; free bulk OHLCVT CSV | deepest free spot tape |
| **Kraken Futures** | no | ✅ | ✅ | 2022-03 | **16 free analytics series**: CVD, liquidations, L/S, top-trader, basis; zero rate-limit cost |
| Bybit REST | — | ⛔ 403 | ✅ **WS works** | `public.bybit.com` tick tape 2020-03 | fragile — same CDN could extend the block |
| **OKX** | no | ✅ | ✅ | spot 1D → 2018-01 (pages back) | funding, OI, L/S, taker vol, **liquidation orders**, option greeks; ⚠️ use `1Dutc` bars |
| **Deribit** `www` + **`history`** | no | ✅ | ✅ | **options ticks → 2016-11 w/ IV**; DVOL → 2021-03 | ★ the free options corpus; `markprice.options` WS streams whole-chain mark IV; ⚠️ instruments call = 75 MB unpaginated; no published rate limit |
| **Hyperliquid** | no | ✅ | ✅ | **~5,000-bar rolling** only | 232 perps OI/funding; `predictedFundings` = Binance/Bybit/HL side by side; 1200 wt/min |
| dYdX v4 | no | ✅ | ✅ | 2023-11 | ~1% of HL volume — perception only |
| **Schwab CME** `/BTC /MBT /ETH /MET` | our acct | ✅ | ✅ | daily 2017-12; 1-min ~48 d | ★ free regulated-venue futures via existing adapter |
| **CoinGecko** Demo key | free key | ✅ | — | 365 d cap | universe + mcap + **categories**; keyless pool measured **3–6 calls/min** (shared IP) — get the key; non-commercial license |
| CoinMarketCap | free key | ✅ | — | 1 mo intraday / 1 yr daily | 15k credits/mo; ToS personal-vs-commercial conflict |
| CoinPaprika | no | ✅ | — | 1 yr daily | 20k/mo keyless failover |
| **DeFiLlama** | no | ✅ | — | stablecoins → 2017 | TVL, stablecoin supply, DEX vol, fees/revenue, yields; ⚠️ bridges now 402 Pro-only |
| **alternative.me F&G** | no | ✅ | — | daily → 2018-02 | sentiment regime |
| **CFTC COT** (Socrata) | no | ✅ | — | weekly, years | CME BTC/ETH positioning by trader class (TFF `gpe5-46if`; codes 133741/133742/146021/146022). 08-11: leveraged money 4,997 long / **12,049 short** — the basis trade, visible free |
| Farside Investors | no (browser UA) | ✅ | — | 2024-01-11 | per-issuer daily BTC+ETH spot-ETF flows; scrape |
| mempool.space / blockchain.info | no | ✅ | — | — | BTC fee regime, hashrate, difficulty |
| Dune | free key | ✅ | — | — | 2,500 credits/mo — weekly batch SQL, not a poller |
| **Solana public RPC** | no | ✅ | — | — | `getTokenAccountsByOwner` → full holdings from a public address (⚠️ **Token-2022 needs a 2nd call**, programId `TokenzQd…`; measured +428 accounts missed otherwise) |
| **Jupiter** `lite-api` | no | ✅ | — | — | Price v3 (v2 is 404), token lists, `ultra/v1/holdings`; 0.5 rps |
| Helius | free key | ✅ | webhooks | — | 1M credits/mo, DAS holdings **+ USD** in one call, 2 rps DAS |
| DexScreener | no | ✅ | — | — | pairs/liquidity/trending; budget 60 rpm |
| Birdeye / Solscan | free-ish / key | ✅ | — | — | 30k CU/mo burns in days; Solscan no free tier |
| **Alpaca news** (Benzinga) | have | ✅ | ✅ | — | already wired; crypto tagged BTCUSD/ETHUSD/SOLUSD/XRPUSD/DOGEUSD + cross-tagged COIN/MSTR/MARA |
| RSS | no | ✅ | — | — | CoinDesk `/arc/outboundfeeds/rss` (the `/feed` path 429s), TheBlock, Cointelegraph, Decrypt |
| ⛔ Coinglass / Glassnode / LunarCrush / CryptoQuant / Laevitas / Velo / Amberdata / Messari / CoinDesk Data / CryptoPanic / CoinCap | — | — | — | — | no usable free API in 2026 |

## Alpaca trading facts (verified 2026-08-21)
- Fees by 30-day volume: T1 $0–100K **0.15 % maker / 0.25 % taker** → T8 $100M+ 0.00/0.10. Charged on the
  asset received, posted end-of-day.
- Order types **market, limit, stop_limit**; TIF **gtc, ioc**. No bracket/OCO documented → stops would be
  fully client-side (our `exit_engine` already does this for options; crypto is 24/7 so it must never sleep).
- Orders evaluate against `non_marginable_buying_power`; no margin, no short. Max $200k notional/order;
  per-asset `min_order_size` (BTC 0.000014). Price-band rejection vs external reference.
- Custody Alpaca Crypto LLC (FinCEN MSB, Fireblocks MPC) — not SIPC. Wallet transfers: **BTC + ETH/ERC-20
  only** (no Solana), withdrawal 50 bps + gas.
- No perps/futures (C# SDK says plainly "does not currently offer"); Alpaca Derivatives LLC registered but
  dormant. Kraken Embed (2025-06) is Broker-API B2B, not our retail surface.
- Official MCP server has a read-only `crypto-data` toolset (8 tools); crypto *orders* sit in `trading` —
  our "never expose `trading` to the chat agent" rule carries over unchanged.

## What it would touch in our code (sized 2026-08-21)
- `brokers/symbol_class.py` already classifies `BTC/USD`-style symbols as `crypto`; `hybrid_market_data.py`
  **defers** them (no REST bars) and `hybrid_market_stream.py` **drops** them (`TODO(crypto)`). Wiring
  Alpaca's crypto REST + WS child is the smallest first step — bars/charts/watchlists light up.
- `trading/`: `asset_class` is hard-coded `us_equity | us_option` (`models.py`, `order_proposal.py`,
  `exit_engine.py`); fractional quantum is already 1e-9 (matches Alpaca crypto); **`fill_tracker.py`
  quantizes prices to cents** — PEPE/BONK/SHIB at $0.00001 would print as $0.00.
- ~20 files carry market-hours gates (`trading/market_hours.py`, `risk_engine.py`, `execution_service.py`,
  `signals/fusion/*`, `exit_evaluators.py`…). 24/7 breaks "daily loss resets at close", "no orders after
  hours", `closes[-1]` = today mid-session (daily-bar-baseline trap, worse on a market that never closes).
- Ledger/P&L: fee-in-kind (sell ETH → fee in BTC) doesn't fit a USD-fee ledger row; needs a conversion at
  fill time.
- Perception (WIRE IT EVERYWHERE): `ai/perception/resources.py` entry + reader; `research/book_tools.py`;
  `tools/book_session.sh --allowedTools`.

## Recommended shape (not a decision — founder call, board founder lane)
1. **Rung 0 — crypto as a regime angle for what we already trade** (BUILD.md §2 option (a), cheap): BTC/ETH
   spot + DVOL + funding (OKX/Hyperliquid) + F&G + stablecoin supply + COT as `query("market")` fields.
   Zero trading-side change. Fusion gets "risk-on/off" from a 24/7 market that leads equities at the open.
2. **Rung 1 — carry crypto as a perception + paper universe**: wire Alpaca crypto REST/WS through the hybrid
   adapters; charts + watchlists + agents see BTC/ETH/SOL; paper book(s) may trade it. Requires the `trading/`
   asset-class + 24/7 + sub-penny + fee-in-kind work above, gated behind paper.
3. **Rung 2 — crypto options/futures research corpus**: pull `history.deribit.com` (BTC+ETH options ticks w/
   IV since 2016) + Binance bulk perps + Schwab CME into `data/` as a research corpus — the same
   pre-registered-study machinery as the flow corpus, on a venue with a complete free history.
4. **Live execution**: only after paper forward-test, and only if the 25-bps friction survives the measured
   edge. Coinbase/Solflare holdings can be read read-only (Coinbase view-only key; Solana public RPC) for
   the portfolio picture without ever touching execution.

## Time-sensitive
- Schwab's 1-min futures wall is ~48 days; `tools/futures_bars_archive.py` archives only `/ES /NQ`. Adding
  `/MBT /MET` (or `/BTC /ETH`) is a one-line `SYMBOLS` change — every un-archived day is gone for good.

## Open before any commercial/live use
- CoinGecko Demo = non-commercial; CMC ToS contradicts itself; DexScreener §1 redistribution. Human ToS read.
- Alpaca crypto execution quality vs Coinbase/Kraken: **UNVERIFIED** — measure spreads off the free L2 book
  before trusting the `us` venue.
- Deribit public rate limits: undocumented — throttle conservatively.
- Cloud caveat: Bybit & co. also block datacenter ranges; every "US OK" above holds for serv (residential),
  not necessarily for a cloud host.
