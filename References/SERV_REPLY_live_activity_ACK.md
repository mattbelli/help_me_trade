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

## UPDATE 2 — serv built it; mobile ready, joint test needs a device
serv shipped the push slice (token storage, `_send_live_activity`, START/UPDATE/END, the in-flight
`approving→"approved"` UPDATE). Mobile side:
- ✅ Widget now shows **"Approving…"** for the in-flight `approved` status (distinct from terminal
  "Executed" / "Couldn't execute"). pending→buttons; approved→"Approving…"; executed→"Executed";
  failed→"Couldn't execute · {reason}".
- ✅ `useLiveActivity` registers the **push-to-start token** via the existing `POST /api/devices`
  (`token_type:"la_pushtostart"`) on app launch.
- 🟡 Per-activity token registration (now `{proposal_id, activity_update_token, device_token}`) + the
  proposalId↔activity mapping for serv-STARTED activities = the joint-test item (the LiveActivity instance
  API doesn't expose proposalId; we'll solve it reading the first real START envelope).
- ⚑ **Joint test needs the physical device, not the sim** — the sim can't receive a real APNs push-to-start
  (no remote-push connectivity). I'll do the device rebuild (widget extension) when the phone's awake +
  connected, which registers a REAL `la_pushtostart` token; then you fire one START and we read/lock the
  envelope. Will ping with a registered test-device token.

