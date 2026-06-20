# Startup & access

The app runs 24/7 on the home server **`serv`** (`serv.tail78aab8.ts.net` / `100.94.77.104`, user `mbelli`,
repo `~/dev/help_me_trade`). Both processes are **systemd services that survive reboots** — you normally don't
start anything by hand.

## Viewing the app from PC / laptop / phone
Any device on your tailnet (Tailscale installed + signed in as `matt.belliveau@`):

- **App (frontend):** http://serv.tail78aab8.ts.net:3001
- **API health:** http://serv.tail78aab8.ts.net:8000/health

Steps per device:
- **PC / laptop:** already tailnet nodes — just open the URL.
- **iPhone:** install the **Tailscale** app → sign in → open the URL in Safari. Optional: Share → **Add to
  Home Screen** for a full-screen, app-like icon (PWA glance).
- No in-app login: the API token is baked into the build, and the backend allows the `serv` host + CORS origin.
- Plain **HTTP is fine** — Tailscale (WireGuard) encrypts the link. **Keep it tailnet/LAN-only; never
  port-forward** (real ledger + broker keys live here).

## Services (systemd) — run automatically
| Service | Process | Port |
|---|---|---|
| `hmt-backend` | `uvicorn api.main:app` (`0.0.0.0`) | 8000 |
| `hmt-frontend` | `npm run start` (Next.js prod) | 3001 |

Manage (sudo prompts for a password on this box):
- Status: `systemctl status hmt-backend hmt-frontend`
- After deploying code: `sudo systemctl restart hmt-backend hmt-frontend`
  (frontend changes also need a rebuild first: `cd frontend && npm run build`)
- Logs: `journalctl -u hmt-backend -f`

Unit files + the nightly backup script live in tracked `deploy/`.

## Local development (hot reload)
Use the helper — starts backend (reload) + frontend (dev) together and tears both down on Ctrl-C:
```bash
./dev.sh
```
Or manually:
```bash
source .venv/bin/activate
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000   # :8000
cd frontend && npm run dev                                    # :3001 → http://localhost:3001
```

⚠️ **Port clash:** the systemd services already hold `:8000` and `:3001`. Before `./dev.sh`, stop them:
```bash
sudo systemctl stop hmt-backend hmt-frontend     # ... develop ...
sudo systemctl start hmt-backend hmt-frontend    # restore the always-on stack when done
```

Environment notes:
- venv is **uv-managed CPython 3.12** (`.venv/bin/...`). Rebuild:
  `uv venv --seed --python 3.12 .venv && uv pip install -r requirements.txt ruff`.
- **Node 22** via nvm in interactive shells (the Composer bridge needs ≥22.13).
- Backend reads `.env` at repo root (Alpaca keys, `CURSOR_API_KEY`, etc.); `--reload` hot-restarts on `.py`
  edits, but **brand-new modules** may need a full `Ctrl-C` + rerun.
- **Pre-push:** `ruff check .` + `python -m pytest -q` + `cd frontend && npm run typecheck` (CI runs all three).

## Useful .env knobs (optional)
| Var | Default | Effect |
|-----|---------|--------|
| `SIGNAL_SCAN_INTERVAL_SECONDS` | `300` | How often the signal engine scans watched symbols (lower = faster alerts while testing; min 30). |
| `POLL_INTERVAL_SECONDS` | `20` | Portfolio snapshot poll cadence (min 5). |
| `APCA_DATA_FEED` | `iex` | Market-data feed. `iex` = free, regular hours only. `sip` = paid (after-hours/deeper) — needs an Alpaca data subscription. |

## Watch the signal engine
The backend logs `Signal scan triggered: [...]` whenever a rule fires (`journalctl -u hmt-backend -f`). The
scan runs once on startup, then every `SIGNAL_SCAN_INTERVAL_SECONDS`.
