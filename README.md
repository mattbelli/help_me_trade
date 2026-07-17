# Help Me Trade — an autonomous trading agent

An **autonomous trading agent that works in the background**: it perceives market data (quotes, options
flow, filings, earnings), reasons over it with LLMs, proposes sized + planned trades to the owner's phone
for one-tap approval, and executes only through a hard-gated risk chokepoint (paper first; live is
double-gated). The web dashboard is a **cockpit to configure and supervise the agent** — a widget-grid
workspace with charts (own engine), watchlists, options-flow, notifications, and a kanban board — not a
manual trading terminal.

**If you are an AI agent working on this repo, stop here**: read `CLAUDE.md` (working agreements —
auto-loaded), then `AGENTS.md` (codebase map), then `References/HANDOFF.md` (current focus). This README
is the human quick-start only.

## Stack

FastAPI (Python 3.12+, port 8000) + Next.js (port 3001) + SQLite (WAL). Brokers: Alpaca (execution =
paper only; SIP data) + Schwab (read-only data/ledger for real accounts). LLM: provider-agnostic factory
(Anthropic / OpenAI / DeepSeek / OpenRouter / Ollama / mock). Runs on an always-on Linux box via systemd
(`hmt-backend`, `hmt-frontend`, plus market-data collector units) — see `References/SERVER_SETUP.md`.

## Quick start (local dev)

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add broker + LLM keys (never commit .env)
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend && npm install && npm run dev   # port 3001
```

Open **http://localhost:3001**. Works with `LLM_PROVIDER=mock` and no broker keys for UI development.

## Deploy (the server)

```bash
tools/serv-deploy.sh [backend|frontend|both]   # build frontend first for frontend deploys
```

## Checks before pushing

```bash
ruff check .                      # whole repo
python -m pytest -q               # ~2,300 tests; run targeted modules during dev
cd frontend && npm run typecheck
```

## Safety model (the short version)

- `trading/execution_service.submit()` is the **only** path to a broker; `RiskEngine.validate()` gates
  every order; kill-switch-first; deny-by-default; paper before live.
- The chat agent can **propose** orders, never place them. Human approves on phone or web.
- Money is `Decimal` end-to-end in `trading/`; secrets live in gitignored `.env`; `data/` (real ledger +
  financial exports) is gitignored and never committed.

## Where things live

| Area | Path |
|------|------|
| API + background loops | `api/` |
| Execution + risk (sacred) | `trading/` |
| Signals, scans, options flow | `signals/` |
| LLM, chat brain, research | `ai/` |
| Ledger + analytics (SQLite) | `db/` |
| Broker adapters | `brokers/` |
| Web cockpit | `frontend/` |
| Phone surface | `mobile/` |
| Specs, plans, history | `References/` |

Full map with one-liners per module: `AGENTS.md`. Roadmap: `References/BUILD.md`; dated build journal:
`References/BUILD_LOG.md` (grep it).
