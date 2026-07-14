# Server setup — migrate dev + runtime to the home Linux server (Option B)

> **✅ COMPLETE (2026-06-20).** Migration is done — `serv` runs the always-on stack (uv CPython 3.12 venv,
> systemd `hmt-backend`/`hmt-frontend`, Tailscale, nightly backups, Composer bridge on Node 22). For **running
> the app + viewing it from your devices**, see `STARTUP.md`. This doc is kept as the migration record; a few
> steps differ from how it actually went (Ubuntu 26.04 had no apt `python3.12`, so we used **uv**; repo lives
> at `~/dev/help_me_trade`; tailnet name is `serv.tail78aab8.ts.net`).

*One always-on Linux box runs dev + the agent + the backend; phone/laptop are thin clients over Tailscale.
The Windows-desktop workflow (remote in → run Claude → it stays alive) transfers — you just SSH/remote into
the Linux box instead. Portability audit (2026-06-20): backend code is Linux-clean, no `C:\` paths or
`sys.platform` branches; only the frontend `*.ps1` helper scripts are Windows-only (run npm/bun directly on
Linux). Python works on 3.12 (matches CI) or 3.13.*

## 0. Prereqs on the Linux server
```bash
# Python 3.12+, Node 20+, git, build basics
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip git curl sqlite3
# Node 22+ — the Composer bridge needs >=22.13; Node 20 triggers EBADENGINE. (bun optional for the frontend)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
# Claude Code CLI + Tailscale
#   - install Claude Code per its docs and run `claude` to auth
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
```

## 1. Clone + Python backend
```bash
git clone <your-remote> help_me_trade && cd help_me_trade
python3.12 -m venv .venv && source .venv/bin/activate   # NOTE: Linux is .venv/bin, not Scripts/
pip install -r requirements.txt
```

## 2. Transfer secrets (do NOT git them — copy over Tailscale/scp)
Recreate these files on the server with the SAME values from the Windows desktop:
- **`.env`** (repo root) — keys present today:
  `API_TOKEN, FRONTEND_ORIGIN, BIND_HOST, APCA_API_KEY_ID, APCA_API_SECRET_KEY, APCA_API_BASE_URL, BROKER,
  SCHWAB_API_KEY, SCHWAB_APP_SECRET, SCHWAB_CALLBACK_URL, SEARXNG_URL`
- **`frontend/.env.local`** — `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_API_TOKEN` (update the URL in §6).
- **Composer bridge** — replicate however `CURSOR_API_KEY` is set on the desktop (env var or
  `tools/composer-bridge/.env`); confirm it's present or Composer dispatch won't run.
Copy example: `scp .env frontend/.env.local user@<tailscale-name>:~/help_me_trade/...`

## 3. Transfer the data (the source of truth — gitignored)
The `data/` dir holds `trading.db` (real ledger, executions, memories, chat, snapshots), `settings.json`,
and the Schwab imports. Copy it over **securely**:
```bash
rsync -av ./data/ user@<tailscale-name>:~/help_me_trade/data/
```
After this the server has your real history; the Windows copy becomes the backup until you cut over.

## 4. Verify on Linux before going live
```bash
source .venv/bin/activate
ruff check . && python -m pytest -q          # expect ~312 passing, clean ruff
uvicorn api.main:app --host 127.0.0.1 --port 8000   # then: curl localhost:8000/health
```
Frontend: `cd frontend && npm install && npm run build && npm run start` (or `bun`).

## 5. Run as always-on services (systemd)
`/etc/systemd/system/hmt-backend.service`:
```ini
[Unit]
Description=help_me_trade backend
After=network-online.target
[Service]
WorkingDirectory=/home/<you>/help_me_trade
ExecStart=/home/<you>/help_me_trade/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
User=<you>
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now hmt-backend` (do the same for the frontend, or serve the built Next.js). Now the
agent runs 24/7 and survives reboots.

### hmt-flow-collector — the options-flow pipeline as its own process (2026-07-14)
The intraday options-flow pipeline (30s poll + OPRA websocket + sweep consumer) can run outside the API so a
stalled API event loop can't blind the stream and a hot open can't starve the trading loops. Same code either
way (`signals/options_flow/runner.py`); ownership is flipped by ONE switch in the shared `.env`:
`OPTIONS_FLOW_EXTERNAL_COLLECTOR=1` (API refuses to start it in-process, collector refuses to start without
it — never two OPRA sockets, Alpaca 406s the second).

`/etc/systemd/system/hmt-flow-collector.service`:
```ini
[Unit]
Description=help_me_trade options-flow collector
After=network-online.target
[Service]
WorkingDirectory=/home/<you>/help_me_trade
ExecStart=/home/<you>/help_me_trade/.venv/bin/python tools/flow_collector.py
Restart=always
RestartSec=3
User=<you>
[Install]
WantedBy=multi-user.target
```
Enable (one-time sudo): set `OPTIONS_FLOW_EXTERNAL_COLLECTOR=1` in `.env`, then
`sudo systemctl enable --now hmt-flow-collector && tools/serv-deploy.sh backend` (the backend restart makes
the API drop its in-process pipeline). Roll back by flipping the flag to 0 and reversing the two steps.
Deploys that touch flow code need `systemctl restart hmt-flow-collector` too — `tools/serv-deploy.sh` handles
it when the unit exists.

## 6. Networking — Tailscale + CORS (same class of fix as the LAN-IP issue)
- Bind backend to `0.0.0.0` (service above) so it's reachable on the Tailscale interface.
- Set `frontend/.env.local` → `NEXT_PUBLIC_API_URL=http://<server-tailscale-name>:8000`.
- Add `http://<server-tailscale-name>:3000` (or whatever the frontend serves on) to **`FRONTEND_ORIGIN`** in
  `.env` (CORS) and to **`BIND_HOST`** (allowed-hosts).
- Install Tailscale on phone + laptop; reach the app at the server's Tailscale name from anywhere. **Keep it
  Tailscale/LAN-only — do not port-forward to the public internet** (broker keys + real ledger live here).

## 7. Backups (don't skip — it's your ledger)
```bash
# nightly off-box copy of the SQLite source of truth
0 3 * * *  sqlite3 ~/help_me_trade/data/trading.db ".backup ~/backups/trading-$(date +\%F).db" && \
           rsync ~/backups/ <somewhere-off-box>/
```

## 8. Dev loop on the server (Option B)
- `cd tools/composer-bridge && npm install` (so the Composer bridge runs on Linux).
- Future Claude Code sessions: SSH/remote into the server, run `claude` there — same keep-alive workflow as
  the Windows desktop, now on the always-on Linux box.
- Pre-push habit carries over: `ruff check .` (whole repo) + `pytest` before pushing.

## Notes / decisions deferred
- **Live trading** later wants a thought on home-server reliability (UPS / internet / the off-box backup
  above) vs. a cloud box — Increment-3 conversation, not now (paper).
- Frontend `dev.ps1`/`install.ps1` are Windows-only; on Linux just use `npm`/`bun` directly (or add a
  `dev.sh` later).
