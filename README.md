# Help Me Trade — Trading Copilot

Personal trading diary / copilot. Built in small **tracer bullets** so each step is understandable before adding the next layer.

## Quick start

### Backend (port 8000)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # add Alpaca paper keys
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend (port 3001)

```bash
cd frontend
bun install
bun run dev
```

Open **http://localhost:3001**. The API defaults to `http://127.0.0.1:8000`.

### Verify AI stack (mock LLM, no API key)

```bash
LLM_PROVIDER=mock python3 scripts/test_ai_stack.py
```

## Roadmap

| Bullet | Status | What it does |
|--------|--------|--------------|
| 0 | Done | Alpaca paper API keys + verify script |
| 1 | Done | Fetch data in terminal (separate scripts) |
| 2 | Done | SQLite + FastAPI API |
| 3 UI | Done | Next.js + shadcn dashboard |
| 3 AI | Done | LLM providers, insights, agent chat, research, memories, rules |
| 4 | Done | Ollama + OpenAI-compatible providers (OpenAI, Anthropic, DeepSeek, OpenRouter) |
| 5 | Partial | Schwab adapter (account/positions/orders; charts still use Alpaca) |

See `References/` for the long-term architecture vision.

## App pages

| Route | Purpose |
|-------|---------|
| `/` | Overview — account, positions, orders, P&L chart |
| `/trading` | Chart, watchlist, price alerts |
| `/charts` | Multi-chart grid |
| `/research` | Deep research runs with HTML reports |
| `/memories` | Browse, search, add, edit, delete agent memories |
| `/agents` | Streaming copilot chat with tools and per-chat model picker |

**Settings** (sidebar gear): broker credentials and AI provider keys/models.

**Right rail**: watchlist, live AI insights (refreshes on each broker poll), RSS placeholder.

## AI setup

1. Open **Settings → AI**
2. Choose provider (OpenAI, Anthropic, DeepSeek, OpenRouter, Ollama, or Mock for offline dev)
3. Enter API key and model (or use defaults)
4. Save — used for insights, research, reflection, and chat

**Per-chat model:** on `/agents`, pick a model in the chat input without changing global settings.

**Deep research web sources:** set `SEARXNG_URL` in `.env` for real web search; otherwise mock snippets are used.

**Memories:** standard chat sessions auto-extract preferences/lessons into Mem0 + SQLite. Manage them on `/memories`.

## Bullet 0 — Alpaca paper API setup

**Goal:** Confirm your Alpaca paper API keys work.

### Configure secrets

Copy `.env.example` to `.env` and set:

| Variable | Value |
|----------|-------|
| `APCA_API_KEY_ID` | Paper **Key ID** from Alpaca |
| `APCA_API_SECRET_KEY` | Paper **Secret Key** |
| `APCA_API_BASE_URL` | `https://paper-api.alpaca.markets` |

Get keys: [Alpaca Paper Dashboard](https://app.alpaca.markets/paper/dashboard/overview) → API Keys.

**Never commit `.env`** — it is in `.gitignore`.

### Verify connectivity

```bash
python scripts/verify_alpaca.py
```

### Paper vs live

- **Paper** (`paper-api.alpaca.markets`) — fake money, safe for learning.
- **Live** (`api.alpaca.markets`) — real money. Stay on paper until you explicitly switch.

## Bullet 1 — Fetch data (terminal)

```bash
python -m scripts.fetch_account
python -m scripts.fetch_positions
python -m scripts.fetch_orders
```

| Script | When it's empty |
|--------|-----------------|
| `fetch_positions` | Normal before any fills |
| `fetch_orders` | Normal when nothing is queued |

## Bullet 2 — SQLite + API

Polls enabled brokers every ~20s, saves snapshots to `data/trading.db`.

| Endpoint | Returns |
|----------|---------|
| `GET /api/snapshot` | Latest account + positions + orders |
| `GET /api/positions` | Positions only |
| `GET /api/orders` | Open orders only |
| `GET /api/account` | Account summary |
| `GET /health` | Server status |

## Bullet 3 — Dashboard + AI

### Key AI endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/insights/stream` | SSE portfolio insight paragraph |
| `POST /api/chat` | Agent chat (SSE) |
| `GET/POST/PATCH/DELETE /api/memories` | Memory CRUD |
| `POST /api/research/run` | Deep research report |
| `GET /api/ai/providers` | Provider catalog for settings UI |

### Project layout

| Area | Path |
|------|------|
| FastAPI app | `api/main.py`, `api/ai_routes.py` |
| Brokers | `brokers/alpaca.py`, `brokers/schwab.py` |
| AI stack | `ai/llm/`, `ai/chat/`, `ai/research/`, `ai/memories/` |
| Frontend | `frontend/` (Next.js 16, Bun) |
| Integration tests | `scripts/test_ai_stack.py` |

## Schwab broker setup

Schwab uses **OAuth**. See [Schwab Developer](https://developer.schwab.com).

```env
SCHWAB_API_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
```

```bash
python -m scripts.schwab_login
```

Token saved to `data/schwab_token.json`. Enable Schwab in Settings alongside Alpaca for a combined overview.

Charts and market data still require Alpaca as primary broker.
