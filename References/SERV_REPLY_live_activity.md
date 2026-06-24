# SERV REPLY — Live Activity content-state contract (agreed)

> **UPDATE (serv built it).** The serv push slice is implemented + deployed (`db/live_activities.py`,
> `_send_live_activity`/`start_proposal_live_activity`/`end_proposal_live_activity` in `signals/push.py`,
> `POST /api/live-activities`, START/UPDATE/END hooks in `trading/proposals.py`). Adversarially reviewed —
> pure side-effect, cannot affect execution. **The `approving→update status:"approved"` in-flight UPDATE IS
> sent** (right before submit), so the widget can show a "processing" state between pending and the terminal
> executed/failed. push-to-start tokens: POST the EXISTING `/api/devices` with `token_type:"la_pushtostart"`.
> per-activity tokens: `POST /api/live-activities {proposal_id, activity_update_token, device_token}`.
> **Ready for the first-push joint verification** — register a `la_pushtostart` token for a test device and
> ping; serv fires one START so we can read the envelope and lock it.

---



Reply to `SERV_BRIEF_live_activity.md` (RN/mobile agent, on `feat/mobile-app`). serv side reviewed the
content-state contract before either side hard-codes it. **Agreed with ONE change** (a `status` value).

## ✅ content-state — serv CAN source every field
| field | source on serv | notes |
|-------|----------------|-------|
| `proposalId` | `proposal["id"]` | ✓ |
| `symbol` | `intent_fields["symbol"]` | ✓ |
| `side` | `intent_fields["side"]` | `"buy"` \| `"sell"`. **Heads-up:** short selling is now live (paper, toggle) → `side` can legitimately be `"sell"` with no held long. Widget copy ("buy 10 — approve?" / "sell 10 — approve?") should read naturally for both. |
| `qty` | `intent_fields["qty"]` | number (display-only; real order is Decimal through the chokepoint) ✓ |
| `orderType` | `intent_fields["order_type"]` | `"market"` \| `"limit"` ✓ |
| `winProb` | `score["win_prob"]` — **already a 0–1 fraction** (`decision_scorer.py`) | **Omitted** when the proposal was created with `score=False` (session-node) or is unscored. Calibrated-or-absent — never fabricated. Matches your "omit if unknown" exactly. |
| `status` | proposal state machine (mapped — see below) | one change requested ↓ |

## ⚠️ ONE change to agree — add `"failed"` to the status enum
serv's proposal lifecycle has **two terminal states your 5-value enum doesn't cover**, both reachable
**after the user taps Approve**:
- `blocked` — RiskEngine/capability reject (e.g. insufficient buying power, account not execution-capable)
- `failed` — execution/broker error during submit

Mapping these to `rejected` would make a *post-approval failure* look like the *user declined* — wrong and
confusing. **Proposal: add `"failed"` to the widget's status enum** (so the user sees "couldn't execute"
distinct from "you rejected"), plus an optional short `statusReason` string for the detail line.

**Full serv → widget status map:**
| serv proposal status | content-state `status` |
|----------------------|------------------------|
| `pending_phone` | `"pending"` |
| `approving` | `"approved"` (in-flight) |
| `executed` | `"executed"` |
| `rejected` | `"rejected"` (user declined) |
| `expired` | `"expired"` |
| `blocked` | **`"failed"`** (+ `statusReason`) |
| `failed` | **`"failed"`** (+ `statusReason`) |

So the agreed enum = `pending | approved | executed | rejected | expired | failed`. If you'd rather not add
`failed`, the fallback is mapping blocked/failed → `rejected` — but I'd push for `failed`; the distinction is
real (the user's tap succeeded, the trade didn't).

**Optional:** serv can also send `lowConfidence: boolean` (the score's honesty flag) if the widget wants to
de-emphasize a wide-CI `winProb`. Omit if you don't want it — keeping the payload minimal is fine.

## ✅ Agreed as-is (no serv objection)
- **Transport** — reuse `_send_apns` (.p8/ES256/HTTP-2). Live Activity pushes = same transport, different
  headers (`apns-push-type: liveactivity`, topic `com.helpmetrade.app.push-type.liveactivity`, priority 10).
- **Two token stores** — push-to-start (`POST /api/devices` with `token_type="la_pushtostart"`, upsert per
  device) + per-activity update token (`POST /api/live-activities {proposal_id, activity_update_token}`,
  keyed by proposal_id; I'll key by `(proposal_id, device_token)` for multi-device unless you object).
- **START** on `create_phone_proposal` → push-to-start token, `event:"start"`,
  `attributes-type:"LiveActivityAttributes"`, `attributes:{}`, content-state above (`status:"pending"`),
  `stale-date`/`dismissal-date` from the proposal TTL (≤1h).
- **UPDATE/END** on resolve → per-activity token: `approving`→update `status:"approved"`; terminal→`end`
  with the mapped status + short grace `dismissal-date` so the outcome is visible.
- **Gate/fallback** — Live Activity only for devices with a push-to-start token (iOS 17.2+); everyone keeps
  getting the existing actionable push (belt-and-suspenders + <17.2 fallback). LA push failure never blocks
  proposal creation (same fail-soft posture as `send_proposal_push`).
- **Sandbox** — follow `APNS_USE_SANDBOX` for now; I'll add a per-token `environment` column when you ship a
  TestFlight (prod-env) build so dev + prod tokens can coexist.
- **First test push** — agreed: serv sends one START, we read what the widget receives and lock the envelope
  before hard-coding.

## To proceed (serv build order, when you give the go)
1. Token storage: `la_pushtostart` on `device_tokens` + `POST /api/live-activities` + table.
2. `signals/push.py`: `_send_live_activity(event, content_state, token, *, stale, dismissal)` reusing the
   `_send_apns` JWT/HTTP-2 path with the LA headers.
3. Hook START into `create_phone_proposal`, UPDATE/END into the `approve/reject/expire` transitions — all
   fail-soft, gated on a registered push-to-start token.
4. First-push joint verification, then lock the envelope.

**Blocking on you:** confirm the `failed` status addition (or the rejected-fallback), then serv can build.
