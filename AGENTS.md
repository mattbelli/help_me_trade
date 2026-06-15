# AGENTS.md

Help Me Trade — a personal trading copilot. Two services:

- **Backend** — FastAPI app (`api/main.py`) that polls a broker (Alpaca / Schwab), stores snapshots in SQLite (`data/trading.db`), and serves a JSON API + a minimal HTML page. Runs on port `8000`.
- **Frontend** — Next.js 16 + shadcn dashboard (`frontend/`), managed with **Bun**. Runs on port `3001` and reads the backend via `NEXT_PUBLIC_API_URL`.

See `README.md` and `frontend/README.md` for the full developer story (the READMEs use PowerShell/Windows examples; the equivalents below are for this Linux VM).

## Cursor Cloud specific instructions

The startup/update script already installs all dependencies (Python venv at `.venv/`, Bun at `~/.bun`, and `frontend/node_modules`). Do NOT re-run dependency installation manually; just start the services.

### Running the services

- **Backend** (from repo root): `.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000`
- **Frontend** (from `frontend/`): `bun run dev` (serves on `http://localhost:3001`). Bun lives at `~/.bun/bin`; if `bun` is not on `PATH` in a fresh shell, use `~/.bun/bin/bun` or `export PATH="$HOME/.bun/bin:$PATH"` (the installer also appends this to `~/.bashrc`).

### Config / env (gotchas)

- The backend reads `.env` at the repo root (loaded via `python-dotenv`). `.env` is gitignored; copy `.env.example` to `.env`. The frontend reads `frontend/.env.local` (set `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`). Both are recreated by hand — they are NOT part of the update script.
- **Broker keys are not included.** Without valid Alpaca paper keys (`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`), the app runs fine but `/api/snapshot` returns `503` ("Waiting for first snapshot…") and account/positions/charts/quotes stay empty. Add real Alpaca paper keys to `.env` to enable live data.
- Leave the Alpaca keys **empty** rather than using the `.env.example` placeholder strings: non-empty placeholders make `broker_is_configured` report `True`, which causes the poller and market-stream WebSocket to spam `401` reconnect errors. Empty keys give a clean "not configured" state (`Skipping alpaca — not configured`).
- `.env` (`load_dotenv`) and `brokers/alpaca.py` import-time `load_dotenv` are read at process start, so **restart uvicorn after editing `.env`**. By contrast, `data/settings.json` (watchlists, settings written via the UI/API) is re-read on every request and overrides `.env`/defaults.

### Persisted state

- Runtime state lives in `data/` (gitignored): `trading.db` (SQLite, created on startup) and `settings.json` (written when settings/watchlists change via the UI/API). Delete `data/settings.json` to reset watchlists/settings back to env + defaults.

### Lint / typecheck / build / test

- Frontend (run from `frontend/`): `bun run lint`, `bun run typecheck`, `bun run build`.
- `bun run lint` currently reports pre-existing `react-hooks/set-state-in-effect` errors in repo source (e.g. `lib/use-watchlist-market.ts`) — these are not from environment setup.
- There is **no automated test suite** (no pytest/jest config). Backend "smoke test": `curl http://127.0.0.1:8000/health`.

### Known pre-existing bug

- The `/trading` and `/charts` pages crash at runtime with `Failed to parse color: lab(...)` from `components/trading/tv-light-chart.tsx` — the `lightweight-charts` library can't parse the Tailwind v4 `lab()`/`oklch()` colors read from computed CSS. The Overview page and watchlist features work fine. This is an app code bug, not an environment issue.
