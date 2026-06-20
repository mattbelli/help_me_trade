# Handoff — "you are here" (2026-06-20)

*Bootstrap for a fresh session. Read this + `AGENTS.md` (codebase map) + `BUILD.md` "Realignment" (the R1–R5
plan + dated history). Everything else is in those — this is just current focus, next steps, and gotchas.*
*Other refs: `ARCHITECTURE.md` (guided subsystem tour), `CODE_AUDIT.md` (cleanup backlog; Tier 1 done),
`WATCHLIST_SPEC.md` (next feature), `SERVER_SETUP.md` (Linux migration).*

## ACTIVE (2026-06-20): migrating to the home Linux server (Option B)
Moving dev + runtime from the Windows desktop to an always-on **home Linux server** (`mbelli@192.168.8.50`,
repo rsync'd to `~/dev/help_me_trade`). Follow **`SERVER_SETUP.md`** to finish the Linux-side setup (venv,
deps, `ruff`+`pytest` green on Linux, systemd always-on services, Tailscale, tweak `.env`/`NEXT_PUBLIC_API_URL`
off the Windows `127.0.0.1` values, backups). Server becomes the **sole writer** of `data/trading.db`; Windows
becomes a one-way backup target (never two-way sync the live DB). After migration, the next feature is the
**watchlist v3** (`WATCHLIST_SPEC.md`, Phase A = IEX-proxy RVOL + 52w slider + customization, no new deps).
Note: this repo's auto-memory lives in Claude's *Windows* project dir, so a fresh Linux session won't inherit
it — these docs are the source of truth.

**Deferred decision:** Dockerize (backend + frontend + SearXNG via compose, `data/` as a volume) at the
**live-prep milestone** (Increment 3 era) — 12-factor groundwork (pinned deps, no hardcoded paths, env-based
config) is already done, so it's a small lift, not a refactor. (Node ≥22 required for the Composer bridge.)

## State
Autonomous trading agent; loop = PERCEIVE→REASON→DECIDE→**ACT**→DELIVER→LEARN. **R2 (ACT) just shipped:** a
fail-closed execution **chokepoint** (RiskEngine 10 checks → ExecutionService, the sole broker path) + **real
Alpaca PAPER execution** for **equities and LONG options**, full lifecycle: **buy-to-open AND sell-to-close**
(options limit-only, ×100 multiplier). Sells are guarded so they can ONLY reduce a held long — no shorting.
The agent placed a real paper order end-to-end (TSLA equity queued for Mon open; long-option flow works once
dates are right). ~16 guardrail/grounding bugs caught by adversarial Sonnet review before commit.
`execution_mode` default = `dry_run`; flip to `paper` in Settings → Execution/Risk to place orders.
**LIVE is hard-blocked server-side** (Increment 3). All committed + pushed (`main`); tests **312** green.

## Next options (pick one — my lean: phone delivery, then live double-gate)
- **R3 — phone delivery** (Capacitor + self-hosted APNs, approve/deny/modify from phone). The "be a dad"
  unlock; ties to the deploy model (one server + thin clients). *(sell-to-close: DONE)*
- **R2 Increment 3 — LIVE behind a typed double-gate** (lower caps, market-hours + duplicate checks). Only
  after the founder trusts paper for a while.
- **R2 Increment 4 — bounded auto-execute** (code-defined envelope, no per-order approval inside it).
- **R3 — phone delivery** (Capacitor + self-hosted APNs, approve/deny/modify from phone). The "be a dad"
  unlock; ties to the deploy model (one server + thin clients).
- **R1 backfill** — Schwab Realized-Gain/Loss import (completes P&L on incomplete positions like PLTR) +
  **futures** (only in Schwab/thinkorswim PDFs / 1099-B; a big chunk of real history, not yet ingested).
- **Deploy/access** — Tailscale for private laptop+phone access now; the ship model is one always-on server
  (agent + SQLite source of truth) + thin clients over HTTPS, NOT device sync.

## Open threads / gotchas
- **Parked widget work is uncommitted** (watchlist-v2 [founder DISLIKED it], scans/memory widgets,
  command-console/dock tweaks). Resolve: revert OR commit-the-good-parts (scans/memory were solid).
- Free **IEX feed = regular hours only**; no options market-data subscription confirmed → **options orders
  are limit-only** (premium = `limit_price`). `get_option_quote` returns real bid/ask only if entitled.
- **Backend** runs on the founder's machine at `127.0.0.1:8000`; LAN access needs `0.0.0.0` + CORS origin +
  the frontend `NEXT_PUBLIC_API_URL` change (or Tailscale).
- mem0 OFF; memory store is clean (self-feeding "20% cap" loop fixed with semantic dedup).

## Working agreements (also in auto-loaded memory)
Build via **Composer** (`node tools/composer-bridge/dispatch.mjs --brief <file>`, one run at a time, logs to
`tools/composer-bridge/last-run.log`) — not hand-edits. Recon/review via **Sonnet** subagents.
**Adversarially review safety-critical code (execution/risk) before commit.** Verify the diff + run pytest
yourself; don't trust self-reports. Founder is **cost-conscious about the Opus main loop** — delegate heavy
work. Commit cadence: iterate to "happy" → update BUILD.md → commit + push.
