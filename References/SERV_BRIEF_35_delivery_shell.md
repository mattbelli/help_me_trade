# mac → serv: #35 — phone-approval delivery shell (contract split)

*Kicking off the block the SERV_TO_MAC handoff (§5) queued. Source of truth for findings:
`research/fable/2026-07-02_mobile-approval.md` (R1–R3, H1–H3). This brief does one thing: **split the
work across the serv/mac seam and pin the 4 API contracts** so both sides can build in parallel without
guessing. Scope is the PAPER-stage trust shell — S1 (server-verifiable device identity) is live-money-gated
and explicitly OUT of #35.*

## The gap we're closing (audit §4)
Today Matt can step away and *approve*; he cannot step away and *stop checking*. Three guarantees are
missing, and each is half-serv/half-mac:
1. **"If the agent found something, I heard about it"** — broken by R1 (fire-and-forget delivery) + R3
   (missed proposals vanish).
2. **"What I see is current"** — broken by R2 (Live Activity never updates/ends) + M1 (card renders on
   partial data).
3. **"After I approve, I know what happened"** — broken by H3 (no fill report) + H1 (client lies on
   response loss) + H2 (crash orphans `approving`).

## Ownership split

| # | Finding | serv owns | mac owns |
|---|---------|-----------|----------|
| **R1** | Delivery unaudited / at-most-once | persist per-device APNs result per proposal; retry w/ backoff inside TTL; deactivate `410 Unregistered` tokens; alarm/fallback when 0 devices ACK; fix `apns_use_sandbox` prod default | — (server-only; mobile already registers tokens) |
| **R2** | Live Activity server half never built | START/UPDATE/END the activity remotely; **accept `proposal_id` on the token-register route (stop 400ing it)**; include `proposalId` in the LA attributes on push-to-start | read `proposalId` from the activity instance attributes; send it alongside the update token (kills the fragile "most-recent START per device" heuristic in `use-live-activity.ts:62`) |
| **R3** | Expired-unanswered proposals vanish | expiry **sweeper**: transition `pending_phone`→`expired` on TTL even if nobody acts; journal the miss; emit a `proposal_missed` push (digest ok) | handle the `proposal_missed` push kind; a "missed proposals" recovery surface in Inbox |
| **H1** | Client lies on response loss | ensure `GET /api/proposals/{id}` returns terminal `status` + (on `already_decided`) `current` | replace *"Network error — trade not sent"* with *"Result unknown — checking…"* + reconcile via `GET /proposals/{id}`; surface `current` on `already_decided` |
| **H2** | Crash orphans `approving`; no reconciler | startup/periodic reconciler: `approving` older than N min → look up execution by `proposal_id` → resolve + notify | client foreground reconciler: on resume, re-fetch pending + any in-flight proposal's status |
| **H3** | "Executed" = submitted; no fill report | order-status tracking → push `fill_report` (filled / partial / canceled / unfilled-at-close) | handle `fill_report` push kind; render fill outcome on the proposal/inbox card |

## The 4 seams that need serv agreement (build blockers)

**Seam 1 — Live Activity correlation (R2).** Proposed target: serv puts `proposalId` in the LA
content/attributes when it push-to-starts, so the client can echo it back with the per-activity update
token; the register route accepts `{activity_update_token, device_token, proposal_id}` and 200s.
→ *Replaces the current no-`proposal_id` POST that serv 400s and the client's most-recent-START guess
(misattributes when two proposals are live at once).* **Verify on build:** that expo-widgets surfaces the
push-started instance's attributes via `getInstances()`; if not, fallback is serv-side most-recent-START
(accept the concurrency limitation) — flag it and we decide.

**Seam 2 — Reconcile endpoint (H1, H2). ✅ RESOLVED — already exists, no serv work.** `GET
/api/proposals/{proposal_id}` (`ai_routes.py:234` → `db.paper_orders.get_proposal`) is `SELECT *
WHERE id=?` with **no status filter**, so it returns the row in *any* terminal state (executed / expired /
rejected), serialized with `status`, `order_type`, `limit_price`, `created_at`, `summary` (`_row_to_proposal`
`paper_orders.py:77`). Mobile already has the client binding (`endpoints.ts:104`). → The client can turn
"unknown" into truth by GETting the id and reading `status` directly; no `current` field needed. **This
unblocks the H1 mac fix entirely — no serv dependency.** *(Bonus: the single-GET already carries the M1
fields that `list_pending_proposals` omits — so a card that reconciles via this endpoint sees true
order_type/limit_price.)*

**Seam 3 — New push `data` payload shapes (R3, H3 — closes handoff §3).** Mobile currently routes the new
critical kinds on `type` **only**, because serv flagged the `data` sub-fields as provisional. To render
them we need the confirmed per-kind `data` contract:
- `proposal_missed` — `{ type, proposalId, summary, symbol, side, qty, ... }`?
- `fill_report` — `{ type, proposalId, outcome: filled|partial|canceled|unfilled, filledQty?, avgPrice?, ... }`?
Send the exact keys serv will emit (emitter is `signals/notify.py`) and mobile wires parsing + render.

**Seam 4 — Delivery-audit visibility (R1, optional for v1).** Does mobile need to *show* delivery state
(e.g. "delivered to 2 devices"), or is R1 purely a server-side reliability guarantee with no mobile
surface? Default assumption: **server-only, no mobile UI** — confirm.

## Sequencing (paper-stage MVP order)
1. **H1** (reconcile) — highest trust-per-effort; Seam 2 endpoint **confirmed to exist**, so this is
   **client-only, zero serv dependency**. *Mac starts this now.*
2. **R2** (Live Activity lifecycle) — the most trust-corrosive live bug (stale "Pending" cards with live
   buttons post-execution). Needs Seam 1 agreed.
3. **H3 + R3** (fill report + missed recovery) — needs Seam 3 shapes.
4. **R1 + H2** (delivery audit + reconcilers) — server-weighted; mac side is small.

## What mac starts immediately (no serv dependency)
**H1 client copy + reconcile loop** — Seam 2 endpoint confirmed, so this needs nothing from serv. Replace
`proposal-card.tsx:141`'s *"Network error — trade not sent"* with *"Result unknown — checking…"*, then
`getProposal(id)` and render the real `status`. First mac task of #35.

---
*Ping back on the 4 seams (esp. Seam 1's correlation approach and whether Seam 2's endpoint exists) and
we build both halves in parallel. — mac*
