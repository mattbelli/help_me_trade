# Notifications · Artifacts · Scheduled Tasks — the supervision foundation

*The agent now runs many strategies across accounts (combined book + sleeves + dip/earnings/session-node).
"Step away and trust it" requires ONE reliable place to see what it did, on every device. This is that layer —
designed once, for the whole vision, so we never rebuild it. Founder-agreed 2026-06-27.*

## The one sentence that ties it together
**A scheduled task runs → produces an artifact → emits a notification that deep-links to the artifact.**
Three layers, one spine. If the data model anticipates all three from day one, nothing gets built twice.

## Principles (non-negotiable, this is the "no compromise" bar)
1. **The server store is the single source of truth.** Notifications, read-state, artifacts all live server-side.
   Web and mobile are views. Cross-device sync, web push, and mobile parity all *fall out* of this one decision.
2. **Push is transport, not truth.** APNs (mobile) and Web Push (web) are delivery channels that reference a
   stored notification. A missed/cleared push never loses the event — it's still in the inbox.
3. **One emit path: `notify()`.** Every event goes through a single helper that (a) persists the notification,
   (b) broadcasts over WS to the web bell, (c) sends APNs/Web Push to devices — from the same stored row. The
   current scattered `broadcast_alert()` + `send_alert_push()` dual-calls collapse into it. This is what
   guarantees parity and rich, consistent content.
4. **Markdown is the canonical artifact format.** Renders natively on web AND RN (one renderer, no rebuild) and
   is what the LLM authors best. HTML = web-only rich cases; PDF = export-later (no server render pipeline now);
   MDX = skipped (React-in-markdown re-fragments web vs mobile).
5. **Absorb, don't duplicate.** Reuse `db.reports`, `device_tokens`, the bell UI, the push senders, the
   Automations page. Generalize them onto the spine; do not stand up parallel systems.
6. **Design for the whole; build in phases.** The schema below anticipates artifacts + scheduled generative
   tasks even though Phase 1 only ships the inbox.

## Data model
### `notifications` (NEW — the inbox; replaces the ephemeral 50-item deque + localStorage read-state)
```
id           TEXT PK
kind         TEXT   -- proposal | fill | exit | execution | rule_triggered | price_alert |
                    --   report_ready | rvol | earnings | schwab_health | rebalance | system
title        TEXT   -- specific + human ("Price alert: SPY crossed $580 — your prior-session VAL")
body         TEXT   -- detail
severity     TEXT   -- info | success | warning | critical   (drives sound + styling)
ref_type     TEXT   -- what it points to: report | proposal | execution | rule | alert | position
ref_id       TEXT
action_url   TEXT   -- deep link / route the click opens (e.g. /reports/<id>, approvals/<id>)
data_json    TEXT   -- structured payload for rich rendering (symbol, level, price, rule_name, reason…)
created_at   TEXT
read_at      TEXT   -- NULL = unread. SERVER-SIDE → cross-device read sync is automatic.
dismissed_at TEXT
```
*(Single-user app → `read_at` is one column. Multi-user later = a per-user join table; YAGNI now.)*

### `reports` → ARTIFACTS (EXTEND the existing table, don't replace)
Existing `db.reports` (id, kind, symbol, title, body, payload_json, generated_at) already IS an artifact store.
Add one column: `format TEXT DEFAULT 'text'` (`text` | `markdown` | `html`). New reports author Markdown; the
viewer renders it richly. A `report_ready` notification carries `ref_type=report, ref_id=<report id>`.

### `scheduled_tasks` (NEW — Phase 4; for USER/AI-created generative tasks only)
```
id, name, schedule (HH:MM + days / cron), kind (generative_report | …),
spec_json (prompt + allowed tools + output title/kind), enabled, last_run_at, next_run_at
```
**Push-back vs the recon's "replace all loops":** the core system loops (`_poll_loop`, `_signal_scan_loop`,
refresh loops) are infrastructure and STAY as code. `scheduled_tasks` is only for user/AI-authored report/
generative jobs. We migrate the *report-style* loop (`_morning_levels_report_loop`) onto it as the first
example; we do NOT cron-ify polling/scanning.

## Current-state fragmentation → the fix
| Event | Today: Web bell (WS) | Today: Mobile (APNs) | After `notify()` |
|---|---|---|---|
| Price alert | ✓ | ✓ | ✓ both, persisted |
| Proposal | ✓ | ✓ | ✓ both, persisted |
| Exit (sell) | ✓ | ✓ | ✓ both |
| Rule triggered | ✓ (vague) | ✗ | ✓ both + SPECIFIC content |
| Fill / execution | ✓ | ✗ | ✓ both |
| RVOL / earnings / Schwab health | ✗ | ✓ | ✓ both |
| Auto-entry fill / rebalance summary | ✗ | ✓ | ✓ both |
| Report ready | ✗ | ✓ (push only) | ✓ both + opens the report |
Read-state today = browser `localStorage` (never syncs). After: server `read_at` (syncs everywhere).

## Absorb map
- `broadcast_alert()` WS fan-out — **WRAP** (keep WS as the web transport; `notify()` calls it after persisting).
- `market_hub.recent_alerts()` deque + `/api/alerts/recent` — **REPLACE** with the `notifications` table + API.
- Notification bell (Zustand + localStorage) — **WRAP**: keep the UI; source from `/api/notifications` + a WS
  `notification` event; mark-read hits the server (drop localStorage as the read-state source of truth).
- `send_alert_push` / `send_proposal_push` / LiveActivity — **KEEP** as transports, called by `notify()`.
- `device_tokens` — **KEEP** (already multi-token-type; Web Push subscriptions register here in Phase 2).
- `db.reports` + reports widget — **EXTEND** (add `format`; widget gains a Markdown renderer).
- `_morning_levels_report_loop` — **MIGRATE** into `scheduled_tasks` in Phase 4 (others stay).
- Automations page — **WRAP**: gains a "Scheduled Tasks" section; existing sections unchanged.

## Phased build (each phase usable; schema designed for the whole now)
**Phase 1 — Inbox core (the foundation).**
- `db/notifications.py` (table + CRUD: create / list / mark_read / mark_all_read / unread_count / dismiss).
- `notify()` unified emit helper (persist → WS broadcast `notification` event → APNs). Re-route ALL ~12 emit
  sites through it with specific title/body/`data`. Read-state changes also broadcast (live multi-device sync).
- Endpoints: `GET /api/notifications`, `POST /api/notifications/{id}/read`, `/read-all`, `/{id}/dismiss`,
  `GET /api/notifications/unread-count`.
- Web bell reworked to consume the API + WS, server-backed read-state.
- (Mobile inbox screen = RN agent lane; backend parity lands immediately because `notify()` always pushes.)

**Phase 2 — Delivery parity + sound.** Web Push (service worker + VAPID, subscriptions in `device_tokens`);
sound option (client setting keyed on `severity`); APNs unchanged.

**Phase 3 — Artifacts + Markdown viewer.** `format` column; in-app Markdown viewer (web + RN); migrate the
session-node/morning report to Markdown; `report_ready` notifications deep-link to it.

**Phase 4 — AI scheduled generative tasks.** `scheduled_tasks` + a generic runner; a curated AI write-tool
(`schedule_report(prompt, schedule, tools)`); the "8am macro report with upcoming earnings/events" proof —
runs → Markdown artifact → `report_ready` notification.

## Open questions / deferred
- PDF export (from Markdown) — later, if ever needed.
- Notification retention/archival policy (cap + age-out) — decide during Phase 1.
- Per-notification "actioned" vs "read" (e.g. a proposal approved) — model now (`dismissed_at` + ref status) or
  Phase 2.
