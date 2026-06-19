# Startup

Two processes — run each in its own terminal from the repo root.

## Backend — FastAPI (port 8000)
```powershell
.venv\Scripts\Activate.ps1
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```
- `--reload` hot-restarts on `.py` edits. **New modules** (e.g. a brand-new file) sometimes
  need a full restart — `Ctrl-C`, then rerun.
- Reads `.env` in the repo root (Alpaca keys, `CURSOR_API_KEY`, etc.).
- First-time setup: `python -m venv .venv` → activate → `pip install -r requirements.txt`.

## Frontend — Next.js (port 3001)
```powershell
cd frontend
npm run dev
```
- Open **http://localhost:3001** — it calls the API at `http://127.0.0.1:8000`.

## Useful .env knobs (optional)
| Var | Default | Effect |
|-----|---------|--------|
| `SIGNAL_SCAN_INTERVAL_SECONDS` | `300` | How often the signal engine scans watched symbols (lower = faster alerts while testing; min 30). |
| `POLL_INTERVAL_SECONDS` | `20` | Portfolio snapshot poll cadence (min 5). |
| `APCA_DATA_FEED` | `iex` | Market-data feed. `iex` = free, regular hours only. `sip` = paid (after-hours/deeper) — needs an Alpaca data subscription. |

## Watch the signal engine
The backend console prints `Signal scan triggered: [...]` whenever a rule fires. The scan runs
once on startup, then every `SIGNAL_SCAN_INTERVAL_SECONDS`.
