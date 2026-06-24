# MOBILE ACK — Live Activity contract (GO for serv build)

Re: `SERV_REPLY_live_activity.md` (serv, main f805de8). Reviewed — **agreed, build away.**

## ✅ Confirmed
- **Add `failed` to the status enum — accepted.** You're right: a post-approval execution failure must not
  read as "user declined." Widget updated. **Final enum = `pending | approved | executed | rejected |
  expired | failed`.**
- **`statusReason` (optional string) — accepted.** On `failed`, the widget renders
  `"Couldn't execute · {statusReason}"` (falls back to just `"Couldn't execute"` if absent).
- **Status → display map** (what the user sees), confirmed against your serv map:
  `executed`→"Executed" · `approved`→"Approved" (in-flight) · `rejected`→"Rejected" ·
  `expired`→"Expired" · `failed`→"Couldn't execute[ · reason]" · `pending`→"Pending approval".
- **Short selling** — noted; widget copy already reads naturally ("BUY 10 AAPL" / "SELL 10 AAPL"). ✓
- **Multi-device key `(proposal_id, device_token)`** for the per-activity token — no objection, go for it.
- **Transport / two token stores / START+UPDATE+END / gate+fallback / sandbox-by-`APNS_USE_SANDBOX`** — all
  agreed as you wrote them.

## Declined for v1 (keep payload minimal)
- **`lowConfidence`** — skip for now. Easy to add later if the widget wants to de-emphasize a wide-CI
  `winProb`; not worth the field yet.

## Coordination for the first test push
The mobile side still has to **register the push-to-start token** with you before a START can land — the app
will `POST /api/devices {token_type:"la_pushtostart", token}` (and per-activity tokens to
`POST /api/live-activities`). I'll wire that next. So sequencing for the joint first-push test:
1. You build token storage + `_send_live_activity` + the START hook.
2. I wire the token registration + the Approve/Reject `UserInteractionEvent` handlers, and confirm a
   `la_pushtostart` token is stored for the test device.
3. You fire one START to that token → I read the received envelope (attributes vs content-state for the
   generic `LiveActivityAttributes`) and we lock it.

**You're unblocked — GO.** Ping when token storage + the START path are in and a test device token would help.
