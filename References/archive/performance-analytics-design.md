# Performance Analytics & AI Context — Design

Single reference for the **Performance** page, analytics API, broker sync, and what the AI assistant needs to coach the user effectively.

---

## 1. Goals

| Audience | Need |
|----------|------|
| **Trader (UI)** | See P&L by day/week/month/quarter/year/all/custom; calendar heatmap; per-trade FIFO P&L; win rate; symbol and behavioral patterns |
| **AI assistant** | Compact, structured context so chat/reflection can cite real trades, flag patterns, and avoid hallucinating performance |

Data flows: **Import (full Schwab history)** + **API sync (Alpaca full / Schwab ~1y chunked)** → deduped SQLite ledger → analytics → UI + agent tools.

---

## 2. Broker sync (auto on connect)

**When:** FastAPI lifespan runs `async_sync_all_enabled_brokers()` on startup, then hourly.

| Broker | Source | History |
|--------|--------|---------|
| Alpaca | `GET /v2/account/activities?activity_type=FILL` (paginated) | Full account fill history |
| Schwab | `get_transactions` in 60-day chunks, 365-day lookback | API-limited; **Import remains source of truth** for lifetime Schwab |

Dedup: `UNIQUE (account_id, external_key)` — API rows won’t duplicate import rows when keys differ (`schwab_api|…` vs export hash).

**Manual:** `POST /api/performance/sync` and “Sync now” in Settings → Import and Performance page.

---

## 3. Period model

| Period | `from_date` / `to_date` |
|--------|-------------------------|
| `day` | Today |
| `week` | Monday → today |
| `month` | 1st → today |
| `quarter` | Quarter start → today |
| `year` | Jan 1 → today |
| `all` | No filter |
| `custom` | User-supplied ISO dates |

Resolver: `db/analytics.resolve_period()`.

---

## 4. Metrics (user-facing)

### 4.1 Summary cards (always visible)

- **Net P&L** — sum of all ledger `amount` (trades + dividends + fees; matches brokerage cash impact)
- **Win rate (stocks)** — FIFO closed round-trips on equity symbols only (no option OCC symbols with space)
- **Trade count** — `event_type = trade` fills
- **Total fees** — sum of `fees` column

### 4.2 Calendar heatmap

- Daily `net_amount` + `event_count` from `daily_trade_stats`
- Click day → filter closed-trades table to `close_date`

### 4.3 Cumulative P&L curve

- Running sum of daily net amounts (trade-ledger based, not broker equity curve)
- Complements Overview’s Alpaca **portfolio equity** chart (mark-to-market)

### 4.4 Closed trades (FIFO)

Per symbol, chronological lots:

- Buy → push lot `(qty, cost_per_share, open_date)`
- Sell → match FIFO, emit round-trip `{ symbol, open_date, close_date, qty, pnl, is_option }`

**Why FIFO:** Matches how most retail traders think about “this trade” without requiring lot IDs from every broker.

### 4.5 Charts (v1)

| Chart | Insight |
|-------|---------|
| P&L by symbol (top 10) | Concentration — are gains from one ticker? |
| Stocks vs options mix | Risk profile — options dominate? |
| Day of week | Behavioral — overtrading Fridays? |

### 4.6 Future (backlog)

- Profit factor, avg win / avg loss, expectancy
- Hold time distribution (open_date → close_date)
- Overtrading days (trade_count > N)
- Revenge-trading heuristic (loss day → 2× next-day volume)
- Time-of-day (if timestamps added)
- Options greeks / assignment tracking

---

## 5. AI assistant context (critical)

Agents should **never** infer P&L from portfolio snapshot alone. Use tools:

### 5.1 `get_trade_history`

Raw events + global summary stats. Use for:

- “What TSLA trades did I make in June?”
- “Show my last 20 fills”

### 5.2 `get_performance_insights`

Compact bundle from `db/analytics.ai_performance_context()`:

```json
{
  "period": "year",
  "from_date": "2026-01-01",
  "to_date": "2026-06-14",
  "summary": { "net_amount", "win_rate", "round_trips", ... },
  "asset_classes": { "stock_trades", "option_trades", ... },
  "day_of_week": [...],
  "top_symbols": [...],
  "recent_closed_trades": [...],
  "largest_wins": [...],
  "largest_losses": [...]
}
```

Use for:

- “How am I doing this year?”
- “What’s my win rate?”
- “Which symbols hurt me most?”
- Reflection / journaling prompts with **specific dates and symbols**

### 5.3 System prompt hints (recommended)

- User has imported/synced trade history — prefer `get_performance_insights` over guessing.
- Distinguish **ledger P&L** (realized cash flow) vs **unrealized** (from `get_portfolio_snapshot`).
- Schwab full history may require Import; API sync is incremental/backfill.

### 5.4 Behavioral flags (planned enrichment)

Add to `ai_performance_context` when implemented:

```json
"flags": [
  { "id": "concentration", "severity": "medium", "detail": "62% of YTD P&L from NVDA" },
  { "id": "overtrading", "severity": "low", "detail": "3 days with >8 trades in March" }
]
```

---

## 6. API surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/performance/dashboard?period=&account_id=` | Full dashboard payload |
| GET | `/api/performance/calendar` | Daily stats for heatmap |
| GET | `/api/performance/symbols` | Per-symbol aggregation |
| GET | `/api/performance/ai-context?period=` | Agent bundle |
| POST | `/api/performance/sync` | Manual broker sync |

Existing trade routes (`/api/trades/*`) remain for Import UI and raw event listing.

---

## 7. Frontend

- **Route:** `/performance` (sidebar: Performance)
- **Settings → Import:** file upload + sync button + last_synced_at
- **Period selector:** day | week | month | quarter | year | all | custom
- **Account:** all accounts or single ledger account

---

## 8. Research basis (what helps traders most)

From trading journal / analytics literature and copilot use cases:

1. **Calendar P&L** — fastest emotional calibration (“red March”)
2. **Per-trade P&L** — links decisions to outcomes (journaling)
3. **Win rate + profit factor** — win rate alone misleads; we show win rate + net P&L
4. **Symbol attribution** — stops “I’m good at trading” when one stock carried the year
5. **Activity mix** — options vs stocks changes risk conversation
6. **Day-of-week** — surfaces schedule-driven mistakes
7. **Fees drag** — often ignored, easy win for AI to mention

For the **AI**, pre-aggregated bundles beat dumping 500 raw rows into context: lower tokens, fewer hallucinations, faster coaching.

---

## 9. Files

| Layer | Path |
|-------|------|
| Sync | `trading/sync.py`, `imports/alpaca_activities.py` |
| Analytics | `db/analytics.py`, `db/trades.py` |
| API | `api/performance_routes.py`, `api/main.py` (lifespan sync) |
| Agent | `ai/chat/tools.py` |
| UI | `frontend/app/performance/`, `frontend/components/performance/` |
| Import UI | `frontend/components/settings/settings-trades-panel.tsx` |

---

## 10. Acceptance

- [ ] API restart triggers sync for enabled brokers (check logs)
- [ ] Performance page loads with imported Schwab data
- [ ] Period filters change summary + charts
- [ ] Calendar click filters round-trip table
- [ ] Agent answers “win rate this month” via `get_performance_insights`
- [ ] Alpaca fills appear after sync without duplicate rows
