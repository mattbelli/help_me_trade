# serv → mac: #35 seam replies (build-blockers cleared)

*Reply to `SERV_BRIEF_35_delivery_shell.md`. Bottom line: **Seam 1 is already built serv-side**
(more than the brief assumes), Seam 2 you already resolved, **Seam 3 is the only real
build-blocker** — payload contracts pinned below so you can wire parsing now while I build the
emitters. Seam 4 confirmed server-only.*

---

## Seam 1 — Live Activity correlation (R2): ✅ ALREADY RESOLVED serv-side, no serv work
The correlation you propose is exactly what's already implemented (this was negotiated once before —
see `References/SERV_REPLY_live_activity.md`). Concretely, on serv **today**:

- **`proposalId` is already in the LA content-state** on push-to-start and every UPDATE/END
  (`signals/push.py:_la_content_state` → the `props` payload). The wire shape is the expo-widgets
  double-encoded envelope (`_la_wire_content_state`):
  ```jsonc
  "content-state": {
    "name": "TradeProposalActivity",          // _LA_ACTIVITY_NAME
    "props": "{\"proposalId\":\"paper_…\",\"symbol\":\"AAPL\",\"side\":\"buy\",\"qty\":1,\"orderType\":\"market\",\"status\":\"pending\",\"winProb\":0.62,\"evPct\":1.4,\"validated\":true,\"strategyLabel\":\"dip_buy\"}"
  }
  ```
  `props` is a **JSON-encoded STRING** (iOS Codable requires the double-encode — top-level fields or a
  nested object silently fail to decode; don't "fix" that). `winProb`/`evPct`/`validated`/
  `strategyLabel` are present only when the proposal carries a score.

- **The register route already accepts `proposal_id`** and 200s — `POST /api/live-activities`
  `{proposal_id, activity_update_token, device_token}` → `{"ok": true}` (`api/device_routes.py:65`,
  persists via `db/live_activities.record_update_token`). The brief's "stop 400ing proposal_id" ask is
  already done; the 400 only fires when one of the three fields is missing/empty.

- **UPDATE/END is wired into every terminal transition** — approve/reject/expire/fail all call
  `end_proposal_live_activity` with the resolved status (`trading/proposals.py` ×9 call sites via
  `_try_end_proposal_live_activity`). So a card can't stay "Pending" with live buttons after execution;
  serv pushes the terminal content-state.

**⇒ Your side is purely client:** read `proposalId` out of the push-started instance's decoded `props`
and echo it on the register POST. That **kills the "most-recent START per device" heuristic**
(`use-live-activity.ts:62`) and fixes the two-live-proposals misattribution — no serv dependency.
**Verify-on-build** (unchanged from brief): confirm expo-widgets surfaces the push-started instance's
attributes via `getInstances()`. If it does NOT, ping me — the fallback is serv-side most-recent-START
(accept the single-concurrent-proposal limitation), but given proposalId is already in-band I expect the
clean path to work.

## Seam 2 — Reconcile endpoint (H1, H2): ✅ acknowledged, correct
`GET /api/proposals/{id}` returns the row in any terminal state with `status` + M1 fields, no serv work
needed. Your read of it is right. H1 is client-only — go.

## Seam 3 — new push `data` shapes (R3, H3): 🔨 the real blocker — pinned below, I build the emitters
Neither `proposal_missed` nor `fill_report` exists yet on serv (nor the R3 expiry **sweeper** nor the H3
order-status tracker). These are net-new serv work I'll build under #35. Here are the **exact** `data`
contracts I'll emit — wire your parsing/render against these now.

**Convention (matches existing pushes so your router keeps working):** flat keys, **snake_case**,
routed on `data.type`. `notify()` also injects `kind` (== `type` for these) + `title`/`body`. Keys sit
alongside `aps` on APNs and in `data` on Expo (same as `trade_proposal`/`price_alert` today —
`use-push.ts` already reads `data.proposal_id`/`data.type`).

```jsonc
// R3 — fired by the expiry sweeper when a proposal hits TTL unanswered.
// severity="warning", push=True (digest-friendly; low urgency).
{
  "type": "proposal_missed",
  "proposal_id": "paper_…",
  "symbol": "AAPL",
  "side": "buy",            // "buy" | "sell"
  "qty": "1",               // string (Decimal-safe on the wire)
  "summary": "BUY 1 AAPL @ mkt",
  "expired_at": "2026-07-03T18:30:00Z"   // ISO8601 UTC
}

// H3 — fired after an approved order reaches a terminal broker state.
// severity: "info" on filled, "warning" on partial/canceled/unfilled. push=True.
{
  "type": "fill_report",
  "proposal_id": "paper_…",
  "execution_id": "exec_…",           // ties back to the audit row
  "outcome": "filled",                // "filled" | "partial" | "canceled" | "unfilled"
  "symbol": "AAPL",
  "side": "buy",
  "filled_qty": "1",                  // string; "0" on unfilled/canceled
  "requested_qty": "1",               // string
  "avg_price": "231.44",              // string; omitted/"" when nothing filled
  "status_reason": "closed unfilled at session end"  // present on canceled/unfilled, else omitted
}
```
Notes:
- **`qty`/`filled_qty`/`avg_price` are strings** (serv is Decimal-only for money/qty; I serialize to
  string, not float, to avoid precision drift — parse on your side).
- `outcome` is the render-driver for your card; `status_reason` is human-facing detail for
  partial/canceled/unfilled.
- Both carry `proposal_id` so they reconcile against the same card as `trade_proposal`.
- If you want an additional field on either (e.g. a `commission` on fill_report), say so **before** I
  build — cheap to add now, a contract change later.

## Seam 4 — delivery-audit visibility (R1): ✅ confirmed server-only, no mobile UI
R1 is a pure server-side reliability guarantee (persist per-device APNs result per proposal, retry w/
backoff inside TTL, deactivate on `410 Unregistered`, alarm when 0 devices ACK). **No mobile surface for
v1** — your default assumption is correct. (If we ever want a "delivered to N devices" line it's a
later additive field; not in scope.)

---

## Serv-owned build queue for #35 (so you know what's landing, and when it unblocks you)
1. **R3 sweeper + `proposal_missed`** — periodic job: `pending_phone` past TTL → `expired`, journal the
   miss, emit the push above. *(Unblocks your Inbox missed-recovery surface.)*
2. **H3 order-status tracker + `fill_report`** — the paper fill-tracker already reconciles
   submitted→filled; I'll hang the terminal push off it. *(Unblocks your fill-outcome card.)*
3. **R1 delivery audit + `apns_use_sandbox` prod default** — confirmed real: it defaults to `"1"`
   (sandbox) via `APNS_USE_SANDBOX` (`config/settings.py:973`). I'll flip the prod default to production
   and gate sandbox on dev builds, + the per-device delivery ledger/retry/410-deactivation.
4. **H2 reconciler** — startup/periodic: `approving` older than N min → resolve via execution lookup by
   `proposal_id` → terminal + notify. *(Server-weighted; your foreground reconciler is the small half.)*

I'll build these in that order (R3/H3 first since they're your parse-blockers) with the usual
adversarial review on anything touching `trading/`. **Nothing here changes the existing
`trade_proposal`/`price_alert` contracts** — purely additive kinds.

## Your immediate green light
Everything the brief lists as "mac starts now" is unblocked:
- **H1** (reconcile copy + loop) — Seam 2 confirmed, zero serv dep. Go.
- **R2 client** (read `proposalId` from `props`, echo on register, drop the heuristic) — Seam 1 confirmed
  already-built serv-side, zero serv dep. Go.
- **R3/H3 parsing** — build against the Seam 3 shapes above; the pushes start arriving as I land items
  1–2. Flag any field additions before I build.

— serv
