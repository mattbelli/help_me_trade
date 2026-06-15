# Help Me Trade — Trading Copilot

Personal trading diary / copilot. Built in small **tracer bullets** so each step is understandable before adding the next layer.

## Bullet 0 — Alpaca paper API setup

**Goal:** Confirm your Alpaca paper API keys work (no app code yet).

### 1. Create a virtual environment

```powershell
cd c:\Users\matth\Documents\Vibing\help_me_trade
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure secrets

```powershell
copy .env.example .env
```

Edit `.env` and set:

| Variable | Value |
|----------|-------|
| `APCA_API_KEY_ID` | Your paper **Key ID** from Alpaca |
| `APCA_API_SECRET_KEY` | Your paper **Secret Key** (shown once when created) |
| `APCA_API_BASE_URL` | `https://paper-api.alpaca.markets` (no `/v2` at the end) |

Get keys: [Alpaca Paper Dashboard](https://app.alpaca.markets/paper/dashboard/overview) → API Keys.

**Edit `.env`, not `.env.example`.** The example file is a template only and may be committed to git.

**Never commit `.env`** — it is in `.gitignore`.

### 3. Verify connectivity

```powershell
python scripts/verify_alpaca.py
```

You should see account status, buying power, and cash printed. If you get `401`, double-check both key ID and secret.

### Paper vs live

- **Paper** (`paper-api.alpaca.markets`) — fake money, safe for learning.
- **Live** (`api.alpaca.markets`) — real money. We stay on paper until you explicitly switch.

## Roadmap

| Bullet | Status | What it does |
|--------|--------|--------------|
| 0 | Done | Alpaca paper API keys + verify script |
| 1 | Done | Fetch data in terminal (separate scripts) |
| 2 | Done | SQLite + FastAPI API |
| 3 UI | Done | Next.js + shadcn dashboard |
| 3 AI | — | First cloud LLM insight |
| 4 | — | Ollama provider |
| 5 | In progress | Schwab adapter |

See `References/` for the full architecture vision.

## Bullet 1 — Fetch data (terminal)

**Goal:** Pull data through the broker adapter — one script per resource.

```powershell
python -m scripts.fetch_account    # cash, buying power, portfolio value
python -m scripts.fetch_positions  # filled holdings only
python -m scripts.fetch_orders     # pending / open orders
```

| Script | Alpaca endpoint | When it's empty |
|--------|-----------------|-----------------|
| `fetch_account` | `/v2/account` | (always has account info) |
| `fetch_positions` | `/v2/positions` | Normal before any fills |
| `fetch_orders` | `/v2/orders?status=open` | Normal when nothing is queued |

**Positions vs orders:** an order waiting for market open shows in `fetch_orders`, not `fetch_positions`, until it fills.

### What each file does

| File | Role |
|------|------|
| `brokers/base.py` | `Position`, `Account`, `Order` types + `BrokerAdapter` interface |
| `brokers/alpaca.py` | Talks to Alpaca API, maps response to our types |
| `brokers/schwab.py` | Stub for Bullet 5 |
| `scripts/fetch_*.py` | One CLI per resource |
| `scripts/_broker_cli.py` | Shared JSON output helper (not run directly) |

## Bullet 2 — SQLite + web dashboard

**Goal:** Poll Alpaca every 45 seconds, save snapshots to SQLite, view in the browser.

### Start the server

```powershell
pip install -r requirements.txt
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

If port 8000 is busy (`WinError 10013`), either stop the other process or pick another port:

```powershell
uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
# then open http://127.0.0.1:8001
```

To see what is using port 8000 on Windows: `netstat -ano | findstr ":8000"`

Open http://127.0.0.1:8000 in your browser.

### API endpoints (JSON)

| Endpoint | Returns |
|----------|---------|
| `GET /api/snapshot` | Latest account + positions + orders |
| `GET /api/positions` | Latest positions only |
| `GET /api/orders` | Latest open orders only |
| `GET /api/account` | Latest account summary |
| `GET /health` | Server status |

Data is stored in `data/trading.db` (gitignored). The server polls Alpaca on startup and every 45s.

### What each file does

| File | Role |
|------|------|
| `db/sqlite.py` | Creates tables, saves/reads snapshots |
| `api/main.py` | FastAPI app, background poller, JSON API |

## Bullet 3 — Next.js + shadcn dashboard

**Goal:** Proper UI that reads from the FastAPI backend (no AI yet).

### Run both servers (two terminals)

**Terminal 1 — API (port 8000):**
```powershell
cd c:\Users\matth\Documents\Vibing\help_me_trade
.\.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — Frontend (port 3001, uses Bun):**
```powershell
cd c:\Users\matth\Documents\Vibing\help_me_trade\frontend
.\dev.ps1
```

Or after restarting the terminal: `bun run dev`

Open **http://localhost:3001**

Use a different port: `bun run dev -- -p 3005` (CORS allows any localhost port).

### Bun setup (one time)

If `bun` is not recognized:

```powershell
powershell -c "irm bun.sh/install.ps1 | iex"
```

Restart the terminal. See [frontend/README.md](frontend/README.md) for all frontend commands.

### Shadcn project setup (already done)

If you need to recreate the frontend:
```powershell
bunx shadcn@latest create -n frontend -t next -d -y
cd frontend
bun install
```
Use `-n frontend` for the name — a positional `frontend` alone will hang on an interactive prompt.

### Frontend layout

| Component | Shows |
|-----------|--------|
| Account cards | Cash, buying power, portfolio, status |
| Positions table | Filled holdings |
| Orders table | Pending orders |
| Insights panel | Placeholder for AI (next bullet) |

Swap in blocks from [shoogle.dev](https://shoogle.dev/search?tab=explore) anytime — drop them in `frontend/components/` and we'll wire them up.

## Schwab broker setup

Schwab uses **OAuth** (not API keys like Alpaca). You need a [Schwab Developer](https://developer.schwab.com) app approved first.

### 1. Add Schwab credentials to `.env`

```env
BROKER=schwab
SCHWAB_API_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
```

The callback URL must **exactly match** what you registered in the Schwab developer portal.

### 2. Log in once (saves token to disk)

```powershell
python -m scripts.schwab_login
```

A browser opens for Schwab login. Token is saved to `data/schwab_token.json` (gitignored).

### 3. Restart the API

```powershell
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

The dashboard shows a **SCHWAB** badge and days until weekly re-auth.

### Switch back to Alpaca

Set `BROKER=alpaca` in `.env` and restart the API.

### Multiple Schwab accounts

If you have more than one linked account, set `SCHWAB_ACCOUNT_HASH` in `.env` (the API will tell you the hash if needed).


