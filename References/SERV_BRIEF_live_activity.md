# SERV BRIEF — Live Activity push delivery (trade-proposal approve/reject)

*From the RN/mobile agent, 2026-06-24. The riskiest, must-land-first slice of the Live Activity /
Dynamic Island feature is the **serv-side ActivityKit→APNs push** that starts / updates / ends the
activity. This brief is that slice. Mobile owns the widget UI + AppIntent + token registration.*

## Goal
When serv creates a trade proposal, in addition to the existing actionable push, **start a Live Activity**
on the user's Lock Screen / Dynamic Island showing the proposal with Approve / Reject. User taps Approve →
(mobile) wakes the app → calls the existing `POST /api/proposals/{id}/approve` → chokepoint. serv then
**updates/ends** the activity to show the outcome.

## What serv already has (reuse it)
- `_send_apns` — .p8 / ES256 JWT / HTTP-2 sender (verified, deployed). Live Activity pushes use the SAME
  transport; only the **headers + payload** differ.
- `device_tokens` table + `POST /api/devices` + `send_proposal_push` wired into `create_phone_proposal`.

## 1. New tokens to store (two kinds)
Live Activities need two APNs token types beyond the normal device token:
- **push-to-start token** (one per device, iOS **17.2+**) — lets serv START an activity with no app running.
  Mobile will register it. Suggest: extend `POST /api/devices` with `token_type='la_pushtostart'` (or a
  new column on `device_tokens`). These rotate — upsert/replace on each report.
- **per-activity update token** (one per live activity) — lets serv UPDATE/END that specific activity.
  Mobile reports it when an activity starts. Suggest new endpoint:
  `POST /api/live-activities {proposal_id, activity_update_token}` → store keyed by proposal_id (+ device).
  Also rotates; keep the latest.

## 2. The content-state contract (agree with mobile FIRST — mobile decodes it)
The Live Activity has a fixed `ActivityAttributes` (static) + `ContentState` (dynamic). The JSON serv pushes
under `content-state` must match the widget's decoder field-for-field. Proposed (mobile will confirm exact
names/casing):
```jsonc
// attributes (static, set once at start)
{ "proposalId": "abc123", "symbol": "AAPL", "side": "buy" }
// content-state (dynamic; sent at start + every update)
{ "qty": 10, "orderType": "market",
  "winProb": 0.62, "entry": 201.5, "stop": 198.0,        // optional decision context
  "ttlExpiresAt": "2026-06-24T21:30:00Z",
  "status": "pending" }                                   // pending | approved | rejected | expired | executed
```
Keep it small (ActivityKit payload budget ~4KB). Money as numbers is fine here (display-only; the real
order still goes through the chokepoint with Decimal).

## 3. START push (on proposal create, iOS 17.2+ devices)
Send to the device's **push-to-start token**:
- Headers: `apns-push-type: liveactivity`, `apns-topic: <BUNDLE_ID>.push-type.liveactivity`
  (BUNDLE_ID = `com.helpmetrade.app`), `apns-priority: 10`, token auth (existing .p8).
- Payload `aps`:
  ```jsonc
  { "timestamp": 1750800000,
    "event": "start",
    "attributes-type": "TradeProposalAttributes",   // mobile confirms the Swift struct name
    "attributes": { ...static... },
    "content-state": { ...dynamic..., "status": "pending" },
    "stale-date": <ttl unix>,            // when iOS dims it as stale
    "dismissal-date": <ttl unix>,        // auto-remove at/after TTL
    "alert": { "title": "Trade proposal", "body": "AAPL buy 10 — approve?" } }
  ```
- Keep sending the **existing actionable push too** (belt-and-suspenders + iOS < 17.2 fallback).

## 4. UPDATE / END push (on resolve: approved / rejected / expired)
Send to that activity's **per-activity update token**:
- Same `apns-push-type: liveactivity` / topic / priority.
- Payload `aps`: `event: "update"` (or `"end"`), updated `content-state` (e.g. `status: "executed"`),
  `dismissal-date` (end → set to now or a short grace so the user sees the outcome briefly).
- If no update token yet recorded (race: activity not started or app never reported it), no-op gracefully.

## 5. Lifetime / expiry
Activity budget is ~8h active + ~4h Lock Screen (~12h max). Our proposals carry TTL ≤ 1h so this rarely
bites, but: set `stale-date`/`dismissal-date` from the proposal TTL, and on TTL-expiry send an `end` with
`status: "expired"`. If an activity outlives the budget, serv just stops being able to update it — the
existing push + in-app card remain the source of truth.

## 6. Gate / fallback
- Only attempt the Live Activity push for devices that registered a push-to-start token (iOS 17.2+); others
  get the existing actionable push only.
- Never let a Live Activity push failure block proposal creation — log + continue (same posture as
  `send_proposal_push`).

## Open items to confirm jointly
- Final `content-state` field names + the Swift attributes-type name (mobile owns the decoder).
- Whether to key update tokens by proposal_id alone or (proposal_id, device) for multi-device.
- Apple's exact push-to-start payload envelope (mobile is verifying against current ActivityKit docs during
  the widget build) — align the `event`/`attributes-type` keys before serv hard-codes them.

Sources mobile verified: Apple "Starting and updating Live Activities with ActivityKit push notifications"
+ `pushToStartToken` docs; Expo SDK 56 widgets (stable). iOS gates: 16.1 base / 16.2 update / 17.2
push-to-start.
