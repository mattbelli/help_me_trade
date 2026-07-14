# Data-layer limits audit — 2026-07-14

*Founder ask: "what are our Alpaca rate limits, what are our system limits, why are they in place, and
what's the best way to build this so essentially only the Alpaca API is the bottleneck? we need solid
architecture here. this is the data. without good data this app is nothing."*

**Headline: Alpaca is not the bottleneck anywhere. We use ~30 of 10,000 REST req/min (0.3%) and 2.76% of one
core on the fattest feed they sell. Every constraint in this system is ours — and the worst one is not a cap
at all, it's a missing argument.**

---

## 1. Alpaca's real limits (CONFIRMED — cited)

Tier: **Algo Trader Plus** ($99/mo, individual — *not* Broker API), feed `sip` (equities) + `opra` (options).

| limit | value | note |
|---|---|---|
| REST — market data | **10,000 req/min** | we use ~30/min. Basic tier's 200/min is what most docs show; not us |
| REST — trading | 200 req/min | separate bucket, irrelevant to data |
| Websocket connections | **1 per account PER ENDPOINT** | SIP and OPRA are independent — confirmed by our own probe running both at once. 2nd connection → `406 connection limit exceeded` (we hit this twice on 07-14) |
| Websocket symbols | **"unlimited"** on our tier | the 30-symbol cap is the *free* tier |
| Option **trades** `*` wildcard | **allowed** | docs silent on prohibition; ACKed live on our account; Alpaca staff: wildcard vs explicit list "stream at the same rate" |
| Option **quotes** `*` wildcard | **FORBIDDEN** | exact doc language: *"You cannot subscribe to `*` for option quotes (there are simply too many of them)."* **The only hard vendor wall we have.** |
| Option quote count cap | **none documented, at any tier** | see §4 |
| Snapshots endpoint | `limit` max **1000**/page, `symbols` max 100/req, paginated | |
| Historical bars | `limit` max 10,000, paginated | |
| Failure mode | **`407 slow client`** | server buffers; if we can't keep up it drops the connection. *Reactive*, no published rate number. This — not any cap — is the real ceiling |

Error codes worth knowing: `405 symbol limit exceeded`, `406 connection limit exceeded`,
`407 slow client`, `409 insufficient subscription`.

---

## 2. Our limits — verdicts

| constant | value | verdict | evidence |
|---|---|---|---|
| `MAX_WS_SYMBOLS_ALPACA` | 500 | **JUSTIFIED** | incident-driven 07-13 (`7a8bcac`): 128 desired vs 100 cap silently starved TSLA/XLE/XLF |
| `HOT_QUOTE_MAX` | 60 | **STALE, not wrong** | real live soak 07-09 (`0471d7b`): 150 quote subs ≈ 2,100 msg/s ≈ **52% of a core** on the shared event loop. Honest measurement — but it predates the 07-12 rewrite (`0de5205`) that made the hot path **3.1× faster** and targeted exactly that cost. Never re-soaked. |
| `STREAM_TRADES_MAX` | 1000 | **SAFE, never measured** | `ws_probe_2026-07-09.md`: 1,506 subs ACKed, no error. That's a **floor on vendor tolerance, not a ceiling**. 1,000 is a guess sitting under the one number we ever tried |
| `MAX_WS_SYMBOLS` | 30 | **UNJUSTIFIED — landmine** | day-one commit, no rationale ever written; silently inherited by the Schwab futures child years later. Survives only because we own 3 futures roots. If `MARKET_DATA_PROVIDER` reverts to plain `alpaca`, it re-arms the 07-13 starvation bug |
| `OPTIONS_FLOW_MAX_UNDERLYINGS` | 30 | JUSTIFIED (07-14) | was 25 and alphabetical → SPY/TSLA never watched. Now fits the watchlist; over-cap warns loudly |
| `ACTIVE_SET_PER_UNDERLYING` | 60 | **never bound on merit** | it was picking 60 from a pre-truncated alphabetical 100 — see §3 |
| **chain snapshot fetch** | — | **THE BUG** | see §3 |

---

## 3. The real bottleneck was a default argument

`poll.py` called `adapter.get_option_chain_snapshot(u)` **bare**. That method defaults to
`limit=None, max_pages=1` → Alpaca's own page default: **100 contracts, alphabetical**. An OCC symbol sorts
**ticker → expiry → C before P → strike**, so one bare page = *the nearest expiry, calls only*.

Measured against the live chain, 2026-07-14:

| | real chain | expiries | puts | **we saw** |
|---|---|---|---|---|
| SPY | **13,730** | 35 | 6,865 | **100 — all calls, one expiry (0.7%)** |
| QQQ | 11,574 | 34 | 5,787 | 100 |
| TSLA | 5,710 | 23 | 2,855 | 100 |

- **11 of 30 underlyings never saw a put** — `AMD, CRWD, GLD, GS, KLAC, MU, NBIS, QQQ, SPCX, SPY, TSLA`.
  It hits the *most liquid* names hardest: more strikes ⇒ the page fills with calls before reaching `P`.
- **22 of 30 saw exactly one expiry.**
- `aggregate_directional_pressure` tracks `call_net`/`put_net` — so **`put_net` was structurally zero** for
  our three biggest names. Every directional read on SPY/QQQ/TSLA was bullish by construction. **A signal
  fabricated by omission.**

**We already knew.** BUILD.md 2026-07-09 records the identical diagnosis — *"unfiltered snapshot pages
alphabetically → one expiry, no puts"* — found and fixed **in the chain viewer only**
(`options_chain.py`: `expiration_date=chosen, limit=1000, max_pages=3`, live-verified NVDA 25 expirations ×
86 strikes, both sides). The poll was never carried across, and the method's own docstring enshrined it:
*"Defaults (no filter, one page) keep the original single-page behavior the poll's active-set relies on."*
The UI you look at manually was correct; the engine meant to think for you was not.

**Fix + verification (this round):** page the chain (`limit=1000, max_pages=20`; SPY needs 14 → 1.16s;
whole watchlist ≈ 16s per 300s refresh; ~0.3% of the REST budget). Live result:
`1000 contracts, 395 puts / 605 calls, 30 underlyings, 29 distinct expiries` — **SPY now 20 puts / 16 calls.**

---

## 4. Things we believed that are not true

- ❌ **"Alpaca caps option quotes at 1,000/connection."** `FLOW_CAPACITY_RESEARCH_2026-07-11.md` asserted this.
  **No such limit exists in any Alpaca doc.** It appears to be Polygon's cap (which is real — for Polygon)
  imported by mistake. Our own `ws_probe_2026-07-09.md` already said so: *"treat 1,000 as our own budget, not
  a vendor wall."* This false claim shaped design discussion for days. **Corrected in that file this round.**
- ❌ **"The 30-symbol websocket cap is Alpaca's."** That's the free tier. Ours is unlimited.
- ⚠️ **"`HOT_QUOTE_MAX=60` is defending against nothing."** Wrong — the 52%-of-a-core soak was real. It's
  *stale* (pre-rewrite), not *unjustified*. Staleness and baselessness are different diagnoses.

---

## 5. The rule

> **Every limit must name its owner — a vendor citation, or a measurement taken on current code — log loudly
> when it binds, and decide survival by importance, never by `sorted()`.**

All four defects found on 2026-07-13/14 violate exactly this:

| date | where | dropped | why |
|---|---|---|---|
| 07-13 | equity stream cap | TSLA, XLE, XLF quotes froze | sorted → sliced → silent |
| 07-14 | flow underlyings | SPY, TSLA never watched | sorted → sliced → silent |
| 07-14 | flow contracts | alphabetical tail of the budget | sorted → sliced → silent |
| 07-14 | **chain snapshot** | **every put on SPY/QQQ/TSLA** | vendor's sorted page, silently inherited |

Same shape every time; TSLA every time, because `T` sorts late. **Alphabetical order is uncorrelated with
importance** — a cap over a sorted list is a repeatable way to delete your best names.

---

## 6. Remaining gaps (what we still cannot honestly size)

1. **A live quote soak on post-07-12 code.** The only real quote-CPU number is the 07-09 soak, taken before a
   3.1× rewrite. Every probe since subscribes `trades:['*']` **only** — none has ever touched the quote
   channel. Trades measurements do **not** transfer: quotes run ~20:1 against trades. Prod's "7,400 msg/s at
   13ms" is *circular* for this purpose — it's measured while `HOT_QUOTE_MAX=60` suppresses the very volume
   in question. **No tool exists to run this.** This is the #1 gap: quotes are what direction depends on.
2. **A push past 1,506 trade subs** — before treating `STREAM_TRADES_MAX` 1000→1500 as "free."
3. **Branch W vs C at a real open with a *capped* set** — never done. Prod now streams through the bell, so
   the next open answers it for free.
4. **Standing CPU telemetry on the stream** — today the only way to get a CPU% is to bounce prod and run a
   one-off probe. "Is this cap still justified?" should be answerable from logs.
5. **Whether `MAX_WS_SYMBOLS=30` corresponds to any Schwab limit** — zero repo evidence either way.

## 7. Post-fix consequence, flagged

With the pool corrected, ~1,800 contracts now rank as active against a 1,000 sub budget, so
`STREAM_TRADES_MAX` binds *hard* for the first time and each underlying gets ~33-36. That makes gap #2 the
next real question — the vendor tolerated 1,506 and the client has enormous headroom, so 1000→1500 is
probably free. **Probably is not measured.** A `405 symbol limit exceeded` would now surface via the
error-frame logging added in PR #72.
