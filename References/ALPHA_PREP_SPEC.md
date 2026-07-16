# ALPHA_PREP_SPEC — from single-founder to alpha-testable (and cloud-ready)

*Planning/design doc. Written 2026-07-06. Grounded in a recon pass over the real tree
(`config/settings.py`, `trading/execution_service.py`, `db/*`, `frontend/…`, deploy tooling).
Companions: `CLAUDE.md` (mission), `AGENTS.md` (map), `References/SERVER_SETUP.md`, board card
`ship-it-multi-tenant`. **This is a spec — no code was changed to produce it.***

---

## 0. Exec summary

The mission is a **trustworthy, always-on agent that pings the phone**. Alpha exists to get that in
front of 1–15 friends and collect decision-quality + trust feedback — with **execution HARD-OFF for
everyone**. Two facts shape the whole plan:

1. **The app is single-tenant to the core.** There is no user/identity concept anywhere (`api/auth.py`
   is one shared bearer token = "the founder"). All state lives in **one `data/trading.db`** +
   **one `data/settings.json`**. Recon found **14 single-tenant subsystems** (§Area 3). True
   multi-tenancy is a real build and is the *launch* work, not the *alpha* work.
2. **Alpha does NOT need multi-tenancy.** The founder's own model is right: **N independent
   single-tenant copies** of the app on serv — each its own port, its own `data/` dir/DB, its own
   `.env`/`settings.json`. That is ~zero new *product* code; it is **provisioning + ops + one ingress
   trick**. Isolation is physical (separate processes/DBs), and per-tester auth is bought for free at
   the edge (Cloudflare Access), so we don't build app auth until the cloud phase.

**Recommended path:**

- **Phase 0 (this week, 1–3 friends)** — pure ops. A provision script stands up an instance with a
  locked-off `settings.json`, Cloudflare Tunnel+Access ingress, tester enters their own Alpaca key in
  the *existing* Settings modal. Kill the leaked `next dev` RAM hog. **No new product code required.**
- **Phase 1 (scale to ~15)** — the two RAM wins (share ONE frontend via runtime backend routing;
  templated systemd units), a real **first-run wizard** (the one genuine product feature), a
  **key-verify endpoint**, README + a lean landing page, and the **license + history-scrub** to
  open-source safely.
- **Phase 2 (cloud/launch)** — the multi-tenant build: identity/auth, per-user data isolation
  (recommend **DB-per-tenant**, which the alpha model already rehearses), per-user secrets, and the
  Alpaca Broker API on-ramp. Execution stays LOCKED; unlocking is the separate Increment-3 track.

**Execution is provably off** with three independent, config-only levers (`execution_mode:"dry_run"`
+ `kill_switch_active:true` + no Alpaca creds); LIVE is already hard-blocked *in code*
(`execution_service.py:131`) and Schwab is structurally read-only (`db/multi_account.py:197`). §Area?

Rough total to a solid 15-person alpha with website + open-source: **~3–4 focused weeks** of build
(Phase 0 ≈ 1–2 days; Phase 1 ≈ 2–3 weeks). The cloud/multi-tenant launch is a **separate multi-week
effort** (Phase 2), scoped here but not costed to the day.

---

## 1. Phased plan (with effort + dependencies)

Effort key: **S** ≤1 day · **M** 1–3 days · **L** 3–10 days · **XL** weeks.

### Phase 0 — This week, 1–3 friends (minimal, ops-only)
| # | Item | Effort | Dep |
|---|------|--------|-----|
| 0.1 | `tools/provision-tester.sh` — clone tree→per-user dir OR shared-tree+per-user `data/`, allocate ports, write locked `settings.json` + `.env`, register systemd units | M | — |
| 0.2 | Locked-off per-instance `settings.json` template (dry_run + kill switch + no creds + strategies disabled) | S | 0.1 |
| 0.3 | Cloudflare Tunnel + Access (Zero-Trust one-time-PIN) → `alice.hmt.<domain>` per tester | M | — |
| 0.4 | Kill the leaked `next dev -p 3005` (~2GB); document "no stray dev servers" | S | — |
| 0.5 | Teardown script + a one-page runbook (`References/TESTER_RUNBOOK.md`) | S | 0.1 |
| 0.6 | Tester enters their own Alpaca key via existing Settings modal; founder walks them through | — | uses today's UI |

**Phase 0 total ≈ 1–2 days.** No product-code changes. Gets real friends on real instances fast.

### Phase 1 — Scale alpha to ~15 (the product + polish)
| # | Item | Effort | Dep |
|---|------|--------|-----|
| 1.1 | **Share ONE frontend**: runtime-derive backend base per request (subdomain→backend) in `frontend/lib/api.ts` (client) + `frontend/lib/python-server.ts` (Next server routes) instead of build-baked `NEXT_PUBLIC_API_URL`; a local router (Caddy) maps subdomain→backend port | M | 0.3 |
| 1.2 | **Templated systemd units** `hmt-backend@.service` / `hmt-frontend@.service` (or backend-only if 1.1 lands) replacing the single units | S | — |
| 1.3 | **First-run wizard** (welcome → Alpaca signup+keys → AI provider → default layout preview → ⌘K/tour), gated on an `onboarding_complete` flag | L | 1.5 |
| 1.4 | **`POST /api/broker/verify`** + `POST /api/ai/verify` (ping creds; the single biggest missing capability) | M | — |
| 1.5 | Extract `AlpacaKeysForm` + simplified AI-provider chooser from Settings so wizard reuses them; add `onboarding_complete` to `DEFAULT_SETTINGS` | S | — |
| 1.6 | Per-instance **RAM caps** (systemd `MemoryMax=`), swap headroom check, backup-per-instance cron | S | 1.2 |
| 1.7 | Harden provisioning: idempotent, port registry file, `--dry-run`, collision checks | M | 0.1 |
| 1.8 | **README** (repo) + asset shot-list capture | M | — |
| 1.9 | **Landing site** (lean, reuse Next stack) + waitlist/alpha-signup | M | — |
| 1.10 | **License decision + git-history secret scrub** (gitleaks) → make repo publishable | M | founder decision |

**Phase 1 total ≈ 2–3 weeks** (1.3 is the big one; 1.1/1.4 are the load-bearing infra).

### Phase 2 — Cloud / multi-tenant launch (scoped, not built here)
| # | Item | Effort | Dep |
|---|------|--------|-----|
| 2.1 | Identity/auth layer (users, sessions, login) — replaces the single-token `api/auth.py` | L | founder: auth provider |
| 2.2 | Per-user data isolation — **recommend DB-per-tenant** (extends the alpha model) vs `user_id` seam across 14 subsystems | L–XL | 2.1 |
| 2.3 | Per-user secret storage (broker creds + OAuth tokens out of host `.env`/files into an encrypted per-user vault) | L | 2.1 |
| 2.4 | Per-user settings store (retire the `data/settings.json` process singleton) | L | 2.2 |
| 2.5 | Alpaca **Broker API** on-ramp (embedded per-end-user accounts + data entitlements) | XL | 2.1–2.3 |
| 2.6 | Execution unlock (Increment-3 typed double-gate) — **separate safety track, gated by paper-validation** | XL | all |

**Phase 2 is the real ship-it build; multi-week. Alpha does not block on any of it.**

---

## 2. Area 1 — Alpha hosting on serv (the N-instance provisioning system)

### 2.1 Instance model
Each tester = one **backend** (uvicorn) + (Phase 0) one **frontend** (`next start`), each with:
- **Own `data/` dir + own SQLite DB.** `db/base.py:DEFAULT_DB_PATH` is `data/trading.db` relative to
  repo root, and `config/settings.py:SETTINGS_PATH` is `data/settings.json`. Point each instance at a
  distinct data dir. Two clean ways:
  - **(A) Shared code tree, per-user data dir** (recommended): one checkout at `~/dev/help_me_trade`;
    each instance sets an env var for its data root. *Caveat:* today `DEFAULT_DB_PATH` and
    `SETTINGS_PATH` are hardcoded relative to the repo — supporting a `HMT_DATA_DIR` override is a
    **tiny code change** (S) but it is the ONE product touch Phase 0 benefits from. If we don't want
    even that this week, use (B).
  - **(B) Per-user checkout** (`~/testers/alice/help_me_trade`): zero code change, more disk (~fine,
    647GB free), each has its own `data/`. Simplest for 1–3; wasteful at 15 (git objects × N).
  - **Decision → §Appendix D1.** Recommend adding the `HMT_DATA_DIR` override (S) and using model (A).
- **Own `.env` + `data/settings.json`.** Config layering is `DEFAULT ← env ← data/settings.json`
  (`config/settings.py:_build_settings`), and **`settings.json` outranks env** — so the per-instance
  `settings.json` is the authoritative lockdown surface (see Area on execution).
- **Own ports.** Backend `8000+i`, frontend `3001` (Phase 0) or shared (Phase 1). Keep a registry
  file `data/tester-ports.json` written by the provision script. ⚠️ Port 3002 = nhl-gm-sim, dev-smoke
  on 3005+ (per MEMORY) — allocate testers in a distinct band, e.g. backend `8100–8130`, frontend
  `3100–3130`.

### 2.2 Provisioning script + runbook
`tools/provision-tester.sh <name> --alpaca-key ... --alpaca-secret ... --ai-provider deepseek`:
1. Allocate next free ports from the registry.
2. Create the data dir; write `.env` (API_TOKEN generated, BIND_HOST, FRONTEND_ORIGIN for the
   subdomain, no live creds) and a **locked `settings.json`** (template below).
3. `systemctl --user enable --now hmt-backend@<name>` (+ frontend in Phase 0).
4. Add the Cloudflare Tunnel ingress rule for `<name>.hmt.<domain>` → the instance.
5. Print the URL + the Access invite.

Teardown `tools/deprovision-tester.sh <name>`: stop+disable units, remove ingress rule, **archive**
(don't delete) the data dir off-box, free the ports. A one-page `References/TESTER_RUNBOOK.md`
captures the human steps (Access invite email, "here's your link", data retention).

### 2.3 systemd — templated units
Replace the single `deploy/systemd/hmt-backend.service` / `hmt-frontend.service` with **template
units**:
```ini
# hmt-backend@.service
[Service]
User=mbelli
WorkingDirectory=/home/mbelli/dev/help_me_trade
Environment=HMT_DATA_DIR=/home/mbelli/testers/%i/data
Environment=HMT_PORT=%i-derived        # from an EnvironmentFile the script writes
EnvironmentFile=/home/mbelli/testers/%i/instance.env
ExecStart=/home/mbelli/dev/help_me_trade/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port ${HMT_PORT}
MemoryMax=900M
Restart=always
RestartSec=3
```
`serv-deploy.sh` (kills MainPID; systemd Restart=always relaunches) generalizes to
`systemctl restart hmt-backend@<name>` — no sudo, same discipline (per the serv-deploy MEMORY note).
The founder's own instance stays on the current non-templated units, or becomes `@founder`.

### 2.4 The two RAM wins
serv is tight (15GB, swap ~3.7/4GB). Per instance ≈ 670MB backend + 250MB frontend.

1. **Kill the leaked `next dev -p 3005` (~2GB).** One-time reclaim; add a "no stray `next dev`; use
   `next start` for served instances" note to `STARTUP.md`. Immediate, free (**Phase 0, S**).
2. **Share ONE frontend across all instances (Phase 1, saves ~250MB × (N-1)).** The frontend is
   identical code; only the backend differs. **Blocker (real code change, not ops):**
   `frontend/lib/api.ts:6` and `frontend/lib/python-server.ts:4` read the backend URL + token from
   **build-baked `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_API_TOKEN`** (`process.env` at build time). A
   single shared frontend build has ONE baked URL+token, so it can't talk to N backends as-is. Fix:
   - **Client** (`api.ts`): derive `API_BASE` at runtime from `window.location` (subdomain), or call
     same-origin `/backend/*` and let the edge/Caddy route by subdomain.
   - **Next server routes** (`python-server.ts`, the chat-brain proxy): resolve the target backend
     **per request** from the incoming `Host` header, not at module load.
   - **Token:** stop baking a per-user token. Put all instances behind **Cloudflare Access** (edge
     identity) and use a **single shared internal `API_TOKEN`** on the private tunnel/localhost —
     Access is the real per-user gate; the internal token just fences off non-tunnel traffic. This is
     why Access (§2.5) also unlocks share-one-frontend.
   Effort **M**. Worth it at ~15 (saves ~3.5GB); skip for 1–3.

### 2.5 Ingress — Cloudflare Tunnel + Access
- **Tunnel** (`cloudflared`): free HTTPS `*.hmt.<domain>` → localhost backends/frontend; **hides the
  home IP, offloads static assets, sidesteps ISP no-server ToS + port-forwarding**. Bandwidth is a
  non-issue (~100kbps/user). Replaces the "Tailscale-only, never port-forward" posture from
  `SERVER_SETUP.md §6` for *testers* (the founder can keep Tailscale for himself).
- **Access (Cloudflare Zero Trust, free ≤50 users)**: gate each subdomain to the tester's email
  (one-time-PIN). **This buys per-tester authentication for free — so we do NOT build app-level auth
  for alpha.** Isolation between testers stays physical (separate DB/port); Access just ensures only
  Alice reaches Alice's instance.
- Ingress rule per tester written by the provision script; `Host` header → correct local port
  (also feeds the Phase-1 share-one-frontend routing).

### 2.6 Guaranteeing execution-off + data isolation per instance
- **Data isolation:** distinct `data/` dir per instance (separate SQLite files) = physical isolation.
  No shared writes; the WAL DB is per-instance. Backups are per-instance (extend the
  `SERVER_SETUP.md §7` `.backup` cron to loop over `~/testers/*/data`).
- **Execution-off:** the locked `settings.json` template (see the lockdown checklist in the Execution
  area) — `execution_mode:"dry_run"`, `kill_switch_active:true`, empty Alpaca creds +
  `enabled:false`, `agent_proposals_enabled:false`, `allow_short_selling:false`. Because
  `settings.json` outranks env, this cannot be overridden by a stray env var. LIVE is hard-blocked in
  code regardless. **Any one of {dry_run, no-creds} already makes a broker order impossible; all three
  = three independent locks.**

### 2.7 Product-code changes this area needs
- **Phase 0:** ideally the `HMT_DATA_DIR` override (S) — otherwise zero.
- **Phase 1:** share-one-frontend runtime routing (M). Everything else is ops/scripts/units.

---

## 3. Area 2 — Onboarding / first-run UX

### 3.1 What EXISTS (reuse — don't rebuild)
- **Settings modal** `frontend/components/settings/settings-dialog.tsx` — Alpaca key entry lives in
  its `brokers` section (`api_key_id`/`api_secret_key`/`api_base_url`, masked-secret handling). Saves
  via `saveSettings()` → `PUT /api/settings` (`api/main.py:1727` → `apply_settings_patch`).
- **AI-provider UI** `frontend/components/settings/ai-provider-settings.tsx` +
  `frontend/lib/ai-settings.ts` — providers openai/anthropic/deepseek/openrouter/ollama(+mock);
  `PROVIDER_FIELDS` + `requiresKey`; default = **deepseek**; Ollama = the "local" option.
- **Default layout seeding** — already present, client-side:
  `frontend/lib/workspace/layout-persistence.ts:DEFAULT_WORKSPACE_LAYOUT` (account-summary, orders,
  positions, watchlist, chart, equity-curve, notifications) + `use-workspace-store.ts` re-seeds on
  empty localStorage. Default watchlist comes from backend `config/settings.py:70`
  (`SPY,QQQ,AAPL,NVDA,MU,AMD`). **There is no blank empty-state** — a new browser already gets a
  populated cockpit. The wizard *confirms* the layout, it doesn't fill a void.
- **⌘K terminal** — `frontend/components/workspace/terminal/terminal.tsx:506` (meta/ctrl+K toggles;
  `/` opens command mode). Mounted in `AppShell`.
- **Chat** — Terminal input → assistant-ui runtime; `frontend/app/api/chat/route.ts` proxies to the
  **Python brain** (`CHAT_BRAIN="python"` default; the AGENTS.md "TS host" note is stale).

### 3.2 What's MISSING (the wizard as a real feature)
1. **First-run gate/detector** — nothing records "setup done." Add `onboarding_complete: false` to
   `DEFAULT_SETTINGS` (`config/settings.py`) + `apply_settings_patch`; gate the wizard on
   `!onboarding_complete` (or on `GET /api/broker` `alpaca.configured===false`).
2. **Wizard shell** — mount a full-screen `<OnboardingWizard/>` in
   `frontend/components/layout/app-shell.tsx` (wraps every route, already branches on `fullBleed`),
   rendered *before* `WorkspacePage`. Steps + states:
   - **Welcome** (what HMT is, the mission, "execution is OFF in alpha — this is data+AI+UI").
   - **Alpaca** — "sign up (link: alpaca.markets) → paste key/secret" → **Verify** button.
   - **AI provider** — simplified single-pick card (OpenAI / Anthropic / DeepSeek / Ollama-local) →
     key (or Ollama base URL) → **Verify**.
   - **Layout preview** — show the seeded default; "looks good / reset to default."
   - **⌘K + tour** — coach-mark: "⌘K opens your terminal; ask it anything." Set
     `onboarding_complete=true`.
   Each step: `idle → validating → ok/error`. Dismissible/skippable (writes the flag).
3. **Key validation (biggest gap)** — `PUT /api/settings` only stores; `GET /api/broker`'s
   `configured` just means "non-empty," never "valid." Add **`POST /api/broker/verify`** (lightweight
   authed Alpaca call; there's a CLI `scripts/verify_alpaca.py` to mirror) and **`POST /api/ai/verify`**
   (reuse the model-listing plumbing behind `chat-model-picker-settings.tsx`). Friendly errors:
   bad-key vs network vs wrong-endpoint.
4. **Reusable sub-forms** — extract an `AlpacaKeysForm` and a `SimpleAiProviderPicker` from the
   Settings modal so wizard + Settings share one implementation (avoid drift).
5. **Copy/links assets** — Alpaca signup URL, per-provider help text, tour strings. All net-new.

### 3.3 Phasing
- **Phase 0:** skip the wizard — the founder provisions each instance and the tester enters their key
  in the existing Settings modal. Ship **`POST /api/broker/verify`** early if cheap (nice friendly
  win). 
- **Phase 1:** build the wizard (1.3, **L**) + verify endpoints (1.4, **M**) + extractions (1.5, S).
  This is the one genuine product feature in the alpha prep.

---

## 4. Area 3 — Cloud / multi-tenant prep (the launch build, scoped)

### 4.1 Single-tenant subsystem inventory (14) — this list IS the multi-tenant work
Everything rides on **one `data/trading.db`** (`db/base.py:DEFAULT_DB_PATH`, WAL) + **one
`data/settings.json`** singleton. No table has a `user_id`; `api/auth.py` is one shared token.

| # | Subsystem | Where | Single-tenancy mechanism | MT difficulty |
|---|-----------|-------|--------------------------|---------------|
| 1 | Watchlist | `config/settings.py:463–685` → `settings.json` | nested key in the settings singleton, context-free accessors | **Hard** (rides the file singleton) |
| 2 | Workspace layout | `db/workspace.py` → `workspace_state(key PK,value,updated_at)` | one global row per key (`layout`/`pages`/`themes`), last-write-wins | Medium (PK→`(user_id,key)`) |
| 3 | Settings | `config/settings.py` → `settings.json` | process-cached filesystem singleton (`load_settings`) read everywhere | **Hard** |
| 4 | Memory | `db/ai_tables.py` `memories` (+FTS) | no user_id; global pool; FTS+embedding span all rows | Medium (col + FTS rebuild) |
| 5 | Chat history | `db/chat_sessions.py` `chat_sessions`/`chat_messages` | no user_id; one global inbox | Easy (col on sessions) |
| 6 | Accounts / broker conns | `db/accounts.py`, `db/multi_account.py`, `brokers/factory.py` | multi-*account* but single-*owner*; **creds resolve from host env/`.env`**, Schwab OAuth = single `data/schwab_token.json` | **Hard** (secrets not in DB) |
| 7 | Idea/calibration ledger | `db/idea_outcomes.py` `idea_outcomes` | no user_id; `founder_verdict` bakes in the founder | Easy (col) |
| 8 | Journal | `db/decision_journal.py` | no user_id; `decided_by` is free text | Easy (col) |
| 9 | Positions/executions | `db/managed_positions.py`, `db/order_executions.py`, `db/trades.py` | scoped by `account_id`/`strategy_id`, no user_id; **money/Decimal + chokepoint tables** | Medium (needs adversarial review) |
| 10 | Auth / user model | `api/auth.py` | **none exists** — one shared `API_TOKEN`/`ADMIN_TOKEN` = the founder | **Hard/foundational** |
| 11 | Alerts + rules | `price_alerts`, `rules`, `rule_events` | global | Easy |
| 12 | Notifications + push tokens | `db/notifications.py`, `db/device_tokens.py`, `db/push_deliveries.py` | account_id/strategy_id, **no user_id** — the phone-approval delivery path | Easy–Medium |
| 13 | Proposals/approvals | `db/paper_orders.py`, `db/chat_approvals.py` | global, session/account only | Easy |
| 14 | Research/reports/charts | `db/reports.py`, `research_artifacts`, `chart_layouts`/`chart_drawings` | global (charts keyed by symbol only) | Easy |

Legitimately-global market data (`momentum_daily_bars`, `regime_macro`, `earnings_calendar`,
`social_mentions`, snapshots) can stay shared — not tenant state.

**The 4 genuinely HARD ones:** (10) auth (must be built from zero), (3)+(1) the settings/watchlist
filesystem singleton, (6) broker creds/OAuth living in host env + a single token file, and (9) the
money/execution tables under the Decimal + adversarial-review safety regime.

### 4.2 Isolation strategy — recommend **DB-per-tenant** for the near term
Two options:
- **`user_id` seam across ~14 subsystems** — voluminous (column + index + query filter on ~10 easy
  tables; plus the FTS rebuild, the settings-singleton teardown, and the money-table review). High
  surface area, every query a place to leak another tenant's data. Right *eventual* answer for a large
  SaaS.
- **DB-per-tenant (one SQLite file per user)** — **recommended to start.** It is exactly what the
  alpha N-instance model already does, so Phase 2 *continues* the alpha architecture instead of
  replacing it. Isolation is physical (a query cannot cross tenants). The only real code change is a
  **request→tenant→DB-path resolver** (from the authenticated identity) threaded where `db_path` is
  already a parameter — and it already is (`db/*` accessors default `db_path=DEFAULT_DB_PATH`). The
  settings singleton (§3/§1 in the table) is the piece that resists this and must move to per-tenant.
  Scales fine to hundreds of users on SQLite; revisit Postgres + `user_id` only at real scale.
- **Decision → §Appendix D2.** Recommend DB-per-tenant now; keep the `user_id`-in-Postgres migration
  as the far-future scale path.

### 4.3 Cheap seams to add NOW (avoid hardening single-tenant further)
- **`HMT_DATA_DIR` override** (§Area 1) — makes "which DB/settings for this instance" a runtime
  choice; the exact seam DB-per-tenant needs later. Do it in Phase 0/1.
- **Keep `db_path` a parameter everywhere** (it already is) — do not add modules that reach for
  `DEFAULT_DB_PATH` implicitly. Enforce in review of new `db/*` code.
- **Don't add more state to `data/settings.json`.** New per-user state should go to a table (with a
  `user_id`-ready shape) rather than the settings singleton, which is the hardest thing to
  multi-tenant. (Ironically `onboarding_complete` is small enough to live in settings for alpha; note
  it as tech-debt for the settings-teardown.)
- **Execution stays LOCKED through Phase 2.** Multi-tenancy does not imply unlocking execution; the
  Increment-3 double-gate is a separate, paper-validation-gated track and must not be conflated with
  the auth work.

### 4.4 Auth (Phase 2)
Replace the single-token `api/auth.py` with real identity. For a solo founder, **do not hand-roll
auth for a money-touching app** — use a managed provider (Clerk / Auth0 / Supabase Auth) or, if
staying edge-centric, **Cloudflare Access + a thin `users` table** keyed by the Access-verified email
(continuity with alpha). Sessions map to a tenant → DB path (§4.2). Route guards move from
"token present" to "identity → authorized for this tenant's data." **Decision → §Appendix D3.**

---

## 5. Area 4 — Repo README (in-depth)

No `README` or `LICENSE` exists at root today. Proposed `README.md` outline:

1. **What it is + the mission** — autonomous trading agent that works in the background so a busy
   parent can step away; a cockpit to *supervise*, not a click-trading terminal. The autonomy ladder
   (alert → propose-to-phone → bounded auto-execute).
2. **Screenshots/GIF hero** — the cockpit + a proposal→phone-approval GIF (see shot-list).
3. **Quickstart / self-host** — clone, `.venv` + `pip install`, `.env` from `.env.example`,
   `npm install`+build, run backend+frontend, open localhost. Note the **always-on caveat** (the
   mission needs 24/7 reachability — self-host is the power-user escape hatch, hosted is the main
   path).
4. **Hosted option** — "don't want to run a server? join the alpha / waitlist" → landing page.
5. **Architecture tour** — link `AGENTS.md`; the PERCEIVE→REASON→DECIDE loop; FastAPI + Next + SQLite;
   provider-agnostic LLM.
6. **★ The SAFETY model** (the trust centerpiece, and the reason to be open-source) — the single
   execution chokepoint (`trading/execution_service.submit`), `RiskEngine.validate` deny-by-default,
   kill-switch-first, paper-before-live, LIVE hard-blocked pending a typed double-gate, Schwab
   read-only. "Audit it yourself" — link the files.
7. **Configuration** — the `DEFAULT ← env ← settings.json` layering; execution_mode; the lockdown
   levers.
8. **Contributing** — Composer/agent cadence is internal; for external contributors: ruff + pytest +
   `npm run typecheck` pre-push; adversarial review required on `trading/`.
9. **License** — see Area 6.

**Where assets go:** `docs/assets/` (images/GIFs/MP4). **Shot-list to capture:**
- ⌘K terminal opening + a chat question answered (GIF).
- **Proposal → phone Approve/Reject** (the headline unlock) — screen-record the push + tap (MP4).
- The cockpit workspace grid (still).
- RVOL/watchlist column in action (still or short GIF).
- A deep-research report rendering (still).
- The kill switch / execution-off state (still — reinforces the safety story).

---

## 6. Area 5 — Website (lean marketing + docs)

**Scope (don't over-build):** one landing page + a docs home.
- **Landing sections:** hero (what/why + the mission one-liner) · the autonomy ladder ·
  screenshots/the phone-approval GIF · "self-host vs hosted" · **alpha-signup / waitlist** (email
  capture) · pricing-later placeholder · a prominent "open-source & auditable — see the chokepoint"
  trust link.
- **Stack — reuse the existing Next.js app** as a separate static route/site rather than a new
  framework: add `frontend/app/(marketing)/` routes or a tiny separate `site/` Next export
  (`output: 'export'`) served as static via the same Cloudflare Tunnel. Lightest path, no new
  toolchain, matches the team's skills. Avoid a second framework (Astro/Hugo) unless the founder wants
  full separation.
- **Docs** live in-repo as Markdown (the README + `References/` selectively surfaced); render later
  with a minimal MD viewer if needed. Don't build a docs portal for alpha.
- **Waitlist:** a form → a simple table or a hosted form (Tally/Formspree) to avoid backend work.
- **Effort ≈ M** (Phase 1, item 1.9).

---

## 7. Area 6 — Licensing + business model

**Business model:** open-source core + **charge-monthly-to-host** (the always-on hosted instance is
the product; self-host is the escape hatch). Open source doubles as a **trust feature** for a
money-touching app (auditable chokepoint) — an explicit asset the founder wants.

**License recommendation: AGPLv3 now; keep BSL in the back pocket.**
- **AGPLv3** is genuine OSI open-source (maximizes the trust/auditability narrative), and its
  *network* copyleft is a real deterrent: anyone hosting a modified version must publish their entire
  modified stack. Simple, well-understood, sole-author-friendly.
- **BSL / Elastic-style source-available** is the tool literally designed to *stop a cloud giant
  reselling your hosting* (time-limited commercial-use restriction), but it is **not** open-source, so
  it undercuts the trust story, and a cloud-giant reseller is **not a near-term alpha threat**.
- Because the founder is the **sole copyright holder**, you can relicense *future* versions to BSL if a
  real competitor emerges. Starting AGPL → later BSL is easy; starting BSL and claiming "open-source
  trust" is contradictory. **→ Ship AGPLv3, revisit at commercial launch. §Appendix D4.**

**Repo-hygiene checklist to open-source safely** (verified items marked ✓):
- ✓ `.env`, `.env.local`, `.env.*.local`, `data/`, `*.db`, `secrets/` are **gitignored**
  (`.gitignore`).
- ✓ **Nothing sensitive is tracked** — `git ls-files` shows only `.env.example` (placeholders) and a
  `mobile/.claude/settings.json`; no `data/`, no real `.env`, no `*.db`, no `settings.json`.
- ✗ **Scan git history before publishing** — 713 commits; even though `data/`/`.env` were never
  tracked, run **`gitleaks detect`** (or trufflehog) over full history and eyeball any hit. If a
  secret is ever found, rotate it and rewrite history (git-filter-repo) before going public.
- ✗ **The founder's real financial data in `data/` must NEVER be published** — it is gitignored; also
  confirm no ledger/export snippets were pasted into tracked `References/*.md` or test fixtures (grep
  before publish).
- **Add `LICENSE` (AGPLv3) + a `README`** (Area 5) at root — neither exists today.
- Review `References/` docs for anything private (account numbers, real balances, personal notes)
  before the repo is public; several reference docs are candid internal notes.
- Confirm `.env.example` has only placeholders (✓ it does).

---

## 8. Appendix — open founder decisions (with recommendations)

- **D1 — Instance layout: shared code tree + `HMT_DATA_DIR` vs per-user checkout.**
  **Recommend:** add the tiny `HMT_DATA_DIR` override (S) and use one shared checkout with per-user
  data dirs — cleaner at 15, and it's the exact seam DB-per-tenant needs in Phase 2. (Per-user
  checkouts are fine for the very first 1–2 if you want literally zero code change this week.)

- **D2 — Multi-tenant isolation: DB-per-tenant vs `user_id` seam.**
  **Recommend:** DB-per-tenant (one SQLite per user). It continues the alpha architecture, gives
  physical isolation (no cross-tenant query leaks), and `db_path` is already a parameter everywhere.
  Defer the `user_id`-in-Postgres seam to real scale.

- **D3 — Auth for the cloud phase.**
  **Recommend:** don't hand-roll. Use **Cloudflare Access + a thin `users` table** (continuity with
  the alpha edge-auth) for launch, or a managed provider (Clerk/Supabase) if you want in-app login UX.
  Never build password/session crypto yourself for a money app.

- **D4 — License.**
  **Recommend:** **AGPLv3 now** (trust + copyleft deterrent), relicense future versions to **BSL** only
  if a cloud-giant reseller actually materializes. You're the sole author, so this option stays open.

- **D5 — Onboarding wizard timing.**
  **Recommend:** Phase 1, not this week. For 1–3 friends, pre-provision + the existing Settings modal
  is enough; ship `POST /api/broker/verify` early as the one friendly quick win.

- **D6 — Ingress: Cloudflare Tunnel+Access vs stay Tailscale-only.**
  **Recommend:** Cloudflare Tunnel + Access for *testers* (free HTTPS, hides home IP, free per-user
  auth, sidesteps ISP/port-forward). Keep Tailscale for the founder's own access. This also unlocks
  the share-one-frontend RAM win.

- **D7 — Alpaca SIP / data.** (Already founder-owned, noted for completeness.) Each tester brings
  their **own free Alpaca account** (free IEX real-time; SIP optional if *they* pay). The app already
  supports per-connection Alpaca creds (`alpaca_config_for_suffix`, `db/accounts.py`). Do **not** pipe
  the founder's single-subscriber SIP feed to testers (violates the data agreement).

---

## 9. Effort roll-up
- **Phase 0 (1–3 friends, this week):** ~**1–2 days**, ops-only (+ optional `HMT_DATA_DIR`, S).
- **Phase 1 (scale to ~15 + open-source + site):** ~**2–3 weeks** (wizard is the big rock; runtime
  backend routing + verify endpoints are the load-bearing infra).
- **Phase 2 (cloud/multi-tenant launch):** **separate multi-week build** — auth + DB-per-tenant
  resolver + per-user secrets + settings-singleton teardown + Alpaca Broker API. Execution stays
  LOCKED; unlocking is the independent Increment-3 track.

Every step above is judged against the mission: it either gets real testers onto a **trustworthy,
always-on, phone-pinging** agent (provisioning, ingress, onboarding, safety-lockdown) or clears the
path to charge-to-host at launch (multi-tenant seams, license, site). Manual-trader polish and
un-guarded execution are deliberately out of scope.
