# Trade Import & History Ledger — Design Spec

**Status:** Approved direction (v1 = analytics + AI diary equally)  
**Related:** `platform-hardening-master-plan.md` (Decimal in Phase 4 — apply here from day one), `ai-feature-deep-dive-and-vision.md` (intelligence layer needs trade history)

---

## 1. Problem & goals

The app today stores **poll snapshots** (account + positions + open orders) but has **no trade ledger**. Users cannot see historical fills, daily P&L, or ask the agent “how did my TSLA trades go this year?”

**v1 goals (both equally):**

| Track | User outcome |
|-------|----------------|
| **Analytics** | Import years of trades; calendar/daily P&L; win rate, totals by symbol; filter by broker/account |
| **AI diary** | Agent can query trade history; optional per-trade or session review with market context |

**Why upload + optional API sync (not either/or):**

| Source | Strength | Limit |
|--------|----------|-------|
| **File upload** | Any brokerage; multi-year Schwab exports; closed accounts | Manual export step |
| **Connected broker pull** | Ongoing sync for recent activity | Schwab API ≈ **60-day** window; Alpaca activities paginate further but schema differs |

---

## 2. Canonical data model

### 2.1 `TradeEvent` (domain)

Immutable row representing one economic event (trade fill, dividend, fee, transfer).

```python
@dataclass(frozen=True)
class TradeEvent:
    id: str                    # UUID
    account_label: str         # e.g. "schwab-main", "alpaca-paper"
    broker: str                # "schwab" | "alpaca" | "fidelity" | "unknown"
    source: str                # "schwab_json_import" | "schwab_api" | "alpaca_api" | ...
    event_type: str            # "trade" | "dividend" | "interest" | "fee" | "transfer" | "other"
    trade_date: date           # settlement/display date from broker
    symbol: str | None         # None for cash-only rows
    side: str | None           # "buy" | "sell" | None
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal              # default 0
    amount: Decimal            # signed cash impact (+ credit, - debit)
    description: str
    external_key: str          # dedup fingerprint (see §4)
    raw_json: str | None       # original row for audit/debug
    imported_at: str           # ISO UTC
```

Use **`Decimal`** and store monetary fields as **TEXT** in SQLite (aligns with hardening plan C3).

### 2.2 SQLite schema

```sql
CREATE TABLE IF NOT EXISTS trade_accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    broker TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_events (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,          -- YYYY-MM-DD
    symbol TEXT,
    side TEXT,
    quantity TEXT,
    price TEXT,
    fees TEXT NOT NULL DEFAULT '0',
    amount TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    external_key TEXT NOT NULL,
    raw_json TEXT,
    imported_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES trade_accounts(id),
    UNIQUE (account_id, external_key)
);

CREATE INDEX IF NOT EXISTS idx_trade_events_date
    ON trade_events(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_events_symbol
    ON trade_events(symbol, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_events_account_date
    ON trade_events(account_id, trade_date DESC);
```

### 2.3 Import batches (audit)

```sql
CREATE TABLE IF NOT EXISTS trade_import_batches (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    filename TEXT,
    from_date TEXT,
    to_date TEXT,
    rows_parsed INTEGER NOT NULL,
    rows_inserted INTEGER NOT NULL,
    rows_skipped INTEGER NOT NULL,
    rows_duplicate INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES trade_accounts(id)
);
```

---

## 3. Schwab JSON parser (your format)

**Input shape:**

```json
{
  "FromDate": "01/01/2026",
  "ToDate": "06/16/2026",
  "TotalTransactionsAmount": "$7,456.53",
  "TotalFeesAndCommAmount": "$68.23",
  "BrokerageTransactions": [
    {
      "Date": "06/15/2026",
      "Action": "Sell",
      "Symbol": "TSLA",
      "Description": "TESLA INC",
      "Quantity": "100",
      "Price": "$410.15",
      "Fees & Comm": "$0.86",
      "Amount": "$41,014.14",
      "AcctgRuleCd": "2"
    }
  ]
}
```

**Parsing rules:**

| Field | Rule |
|-------|------|
| `Date` | Parse `MM/DD/YYYY` → `date` |
| `Action` | `Buy`/`Sell` → `event_type=trade`, map side; `Qualified Dividend`, `Credit Interest`, `Journal`, etc. → typed non-trade rows (keep in ledger, exclude from win-rate unless configured) |
| `Symbol` | Uppercase; empty → `symbol=None` |
| `Quantity` | `Decimal`; sells may be positive in export — normalize sign by side |
| `Price`, `Fees & Comm`, `Amount` | Strip `$`, commas → `Decimal` |
| `Amount` | Schwab: sells credit positive, buys debit negative — store as signed `amount` |
| `Description`, `AcctgRuleCd` | Preserve in `description` / `raw_json` |

**`external_key` (dedup):**

```
sha256(account_id | trade_date | action | symbol | quantity | price | amount | fees)
```

Normalized to fixed precision strings before hash so `$410.15` and `410.15` match.

**Re-import behavior:** `INSERT OR IGNORE` on `(account_id, external_key)`; batch report lists duplicates.

---

## 4. API

New router: `api/trade_routes.py` (prefix `/api/trades`).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/accounts` | List trade accounts |
| `POST` | `/accounts` | Create account label `{ label, broker }` |
| `POST` | `/import/preview` | Multipart JSON → parse only, no write; return summary + first 50 rows |
| `POST` | `/import` | Multipart JSON + `account_id` → persist batch |
| `GET` | `/events` | Query: `account_id`, `from`, `to`, `symbol`, `event_type`, `limit`, `offset` |
| `GET` | `/stats/summary` | Aggregates for date range: net P&L, fees, trade count, win/loss count |
| `GET` | `/stats/daily` | Per-day net amount for calendar heatmap |
| `GET` | `/stats/by-symbol` | Grouped P&L / trade count |
| `POST` | `/sync` | *(Phase B)* Pull from connected broker into same ledger |

**Preview response example:**

```json
{
  "batch_id": null,
  "from_date": "2026-01-01",
  "to_date": "2026-06-16",
  "parsed": 142,
  "would_insert": 138,
  "duplicates": 4,
  "skipped": 0,
  "warnings": ["Row 12: unknown Action 'Reinvest Dividend' → event_type=other"],
  "sample": [ /* normalized TradeEvent[] */ ]
}
```

Auth: all routes behind existing API token (Phase 2 hardening).

---

## 5. Frontend (v1)

**New page:** `/trades` (sidebar: “Trades” or under “Data”)

| Section | Content |
|---------|---------|
| **Import** | Drag-drop JSON; account picker; preview table; confirm import |
| **Summary cards** | Net P&L, total fees, # trades, win rate (closed round-trips — see §6) |
| **Calendar** | Month view colored by daily net (green/red); click day → trade list |
| **Trade table** | Sortable/filterable: date, symbol, side, qty, price, amount, fees |
| **Date range** | Presets: YTD, 1M, 3M, 1Y, All |

Reuse existing chart/table patterns from `overview-page.tsx` and `pnl-chart.tsx`.

---

## 6. Analytics logic (v1)

**Daily P&L (calendar):** Sum `amount` for `event_type in (trade, dividend, interest, fee)` per `trade_date`. Simple and matches Schwab export totals.

**Win rate (v1 — FIFO round-trips per symbol):**

- Consider only `event_type=trade` rows with symbol.
- FIFO match buys → sells per symbol; round-trip P&L = sell proceeds − buy cost − allocated fees.
- Win = round-trip P&L > 0.
- Document limitation: partial fills, shorts, and options may need v2 matching rules.

**By-symbol stats:** Sum amounts and trade count grouped by `symbol`.

Defer for v2: Sharpe, max drawdown, time-in-trade, options multi-leg.

---

## 7. AI diary integration (v1)

### 7.1 Agent tool: `get_trade_history`

Add to tool definitions (real tool-calling path from hardening Phase 3):

```json
{
  "name": "get_trade_history",
  "description": "Query imported and synced trade history. Use for trade review, P&L questions, and pattern analysis.",
  "parameters": {
    "symbol": { "type": "string" },
    "from_date": { "type": "string", "format": "date" },
    "to_date": { "type": "string", "format": "date" },
    "limit": { "type": "integer", "default": 50 }
  }
}
```

Returns compact JSON (not full `raw_json`) to stay within token budget.

### 7.2 Trade review prompt (optional endpoint)

`POST /api/trades/review`

- Body: `{ symbol?, from_date?, to_date?, focus? }`
- Server loads matching trades + optional snapshot/bars context
- LLM produces structured review: summary, best/worst trades, discipline notes, patterns
- Low temperature (0.2); store result as `research` memory type (optional)

### 7.3 Reflection hook

Reflection scheduler can include “trades in last 7 days” in context (alongside snapshot), so weekly lessons reference actual behavior not just holdings.

---

## 8. Implementation phases

### Phase A — Ledger + Schwab import (MVP)

**Backend**

- `db/trades.py` — schema, CRUD, queries
- `imports/schwab_transactions.py` — parser + money helpers (`imports/money.py`: `parse_dollar`, `Decimal`)
- `api/trade_routes.py` — preview, import, list, stats

**Frontend**

- `/trades` page with import wizard + table + summary cards

**Tests**

- `tests/fixtures/schwab_transactions_sample.json`
- `tests/test_schwab_import.py` — parse, dedup, Buy/Sell amounts
- `tests/test_trade_stats.py` — daily aggregation, FIFO win rate on canned data

### Phase B — Analytics UI polish

- Calendar heatmap component
- By-symbol breakdown chart
- Export CSV of filtered events

### Phase C — AI wiring

- `get_trade_history` tool
- `/api/trades/review` endpoint
- Chat system hint: “User has imported trade history; prefer get_trade_history over guessing.”

### Phase D — Broker sync (optional)

- Alpaca: `/v2/account/activities` paginated → map to `TradeEvent`
- Schwab: `get_transactions` last 60 days → map; UI banner: “For full Schwab history, upload export”

---

## 9. File layout

```
db/trades.py
imports/
  __init__.py
  money.py
  schwab_transactions.py
  base.py              # ImportResult, ImportParser protocol
api/trade_routes.py
ai/chat/tools.py       # get_trade_history
frontend/
  app/trades/page.tsx
  components/trades/
    trade-import-dialog.tsx
    trade-table.tsx
    trade-summary-cards.tsx
    trade-calendar.tsx
  lib/trades-api.ts
tests/
  fixtures/schwab_transactions_sample.json
  test_schwab_import.py
  test_trade_stats.py
```

---

## 10. Success criteria

- [ ] User uploads Schwab JSON; preview shows correct row count and sample
- [ ] Re-upload same file reports duplicates, inserts zero new rows
- [ ] `/trades` shows calendar + table for imported date range
- [ ] Summary stats match Schwab export totals within rounding (±$0.01)
- [ ] Agent answers “What TSLA trades did I make in 2026?” using `get_trade_history`
- [ ] Trade review endpoint produces grounded summary citing actual trade rows
- [ ] All money fields use `Decimal` / TEXT — no float in trade ledger

---

## 11. Open decisions (defaults chosen)

| Question | v1 default |
|----------|------------|
| Multiple Schwab accounts? | One `trade_account` per import; user picks/creates label |
| Non-trade rows in win rate? | Excluded; shown in ledger and daily P&L |
| Options / multi-leg | Store as-is; FIFO may be wrong — document in UI |
| Merge with live snapshots? | Separate views; “Holdings” = snapshot, “Trades” = ledger |

---

*Next step: implementation plan (Phase A tasks) or start Phase A directly after Phase 0 hardening.*
