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

## UPDATE 3 — FULL LIFECYCLE VERIFIED END-TO-END ✅ (device joint test, 2026-06-24)
START → UPDATE → END all verified on the physical device. The card renders on Lock Screen + Dynamic Island,
flips `pending`(Reject/Approve) → `approved`("Approving…") → `executed`("Executed") and auto-dismisses;
serv's per-activity token auto-cleared on the terminal END. **The serv↔mobile contract is locked.**

### The wire envelope (CONFIRMED from expo-widgets source — `LiveActivityAttributes`)
- `attributes-type`: **`"LiveActivityAttributes"`** (expo's generic type), `attributes`: **`{}`**.
- `content-state`: a **wrapper** — `{ "name": "TradeProposalActivity", "props": "<json-string>" }` where:
  - `name` routes to the registered layout (must equal the `createLiveActivity` name).
  - **`props` is a JSON-encoded STRING** (double-encoded — the trade state stringified), NOT a nested object.
    iOS `Codable` decode fails silently (→ no card) if `props` is sent as an object or the fields are inlined.
- Topic `com.helpmetrade.app.push-type.liveactivity`, `apns-push-type: liveactivity`. START → `la_pushtostart`
  token; UPDATE/END → the per-activity update token. Same `{name, props-string}` body, `event: start|update|end`.

### ⚑ ONE PRODUCTION ASK FOR SERV — correlate the per-activity token by device, not proposalId
expo-widgets' instance API **cannot surface the proposalId** of a push-STARTED activity (no state accessor;
we patched the lib so the per-activity update token even emits — `patches/expo-widgets+56.0.19.patch`). So the
app now auto-POSTs **`{ activity_update_token, device_token }` with NO `proposal_id`** to `POST /api/live-activities`
when it captures the token. **Please map it to the proposal by the most-recent START for that `device_token`**
(one pending proposal per device at a time fits the app model). The `proposal_id` field stays optional/accepted
if present. Until serv correlates, real proposals will START+render but won't UPDATE/END (token unmapped).
- Also note: capture requires the app to run briefly after the START (it polls `getInstances()` on launch).
  A proposal fired while the app is fully killed renders the card but defers token capture until next launch —
  acceptable for now; flag if you want a tighter guarantee.

