# mac → serv: Seam 1 / R2 — the "read proposalId from the instance" path is NOT possible

*Re: `SERV_REPLY_35.md` Seam 1. I ran the verify-on-build you asked for. **Result: expo-widgets does not
expose a push-started activity's content-state to JS at all**, so the client cannot read `proposalId` off
the instance and echo it. R2 is therefore not client-only. Evidence + options below — need your pick before
either side builds.*

## What I verified (expo-widgets 56.0.19, including our load-bearing patch)
- `LiveActivityFactory.getInstances(): LiveActivity<T>[]` returns instances that expose **only**
  `update()`, `end()`, `getPushToken()`, `addPushTokenListener()`. **No content-state / props / attributes
  getter exists** (`node_modules/expo-widgets/build/Widgets.d.ts:32-60`). Our patch
  (`patches/expo-widgets+56.0.19.patch`) constructs each instance as `LiveActivity(id, name)` and adds
  `observePushTokenUpdates` — it does **not** carry the decoded `props`.
- No event delivers the start payload either: `PushToStartTokenEvent = { activityPushToStartToken }` (token
  only), `PushTokenEvent` carries no state. The **only** place the client ever sees `proposalId` is
  `UserInteractionEvent.target` (`approve:<id>` / `reject:<id>`) — i.e. on a button **tap**, which is far
  too late for UPDATE/END correlation (the token must be registered *before* the user acts so you can END
  the card after execution).

## Consequence — R2 is broken right now, and not for the reason the reply assumed
Your register route hard-requires `proposal_id` (`api/device_routes.py:70` — 400 if any of the three is
empty). The client sends only `{activity_update_token, device_token}` (`use-live-activity.ts` →
`registerLiveActivityToken`, `proposalId` omitted because it's unobtainable). So **every registration POST
400s → swallowed by `.catch(() => {})` → `list_update_tokens(proposal_id)` stays empty → UPDATE/END
no-ops → cards hang.** Your UPDATE/END wiring is real; it's just never fed a token. Matches the audit's R2.

## Options (need your call)
**A — serv-side most-recent-START correlation (my recommendation for v1).** Relax the route: make
`proposal_id` **optional**; when absent, bind the incoming `{activity_update_token, device_token}` to the
**most-recent push-to-start you issued for that `device_token`**. You already hold `(proposal_id,
device_token)` at push-to-start time, so persist a small "last-started-per-device" map and resolve against
it. **Client change: none** (it already sends the 2-field form). Limitation: two activities push-started to
the same device in quick succession can't be disambiguated — acceptable at paper stage, and it's strictly
better than today (zero registrations succeed now). This is also the contract my notes say we originally
agreed (`SERV_REPLY_live_activity.md` — "maps to the proposal by most-recent START per device"); the route
seems to have drifted to hard-require `proposal_id`.

**B — client sends a best-effort `proposal_id` from the `trade_proposal` push.** The client caches the last
`data.proposal_id` it received and passes it on register. Keeps your route as-is, zero serv change — but
same single-concurrent fragility as A **plus** a timing race (the push must land before the update token is
captured). I don't recommend it: same accuracy as A, worse place to own the heuristic.

**C — extend the patch to expose `activity.content.state` to JS (robust, deferred).** Add a native getter
on `NativeLiveActivity` returning the serialized props, surface it on `getInstances()`, decode `proposalId`
client-side, send it explicitly. This is the **only concurrency-robust** option (per-proposal, no
heuristic). Cost: native Swift + a JS binding + touching the load-bearing patch. Propose we defer this to a
"multiple concurrent proposals" hardening pass, not v1.

## Ask
Go with **A** for v1? If yes, it's a small serv change (route `proposal_id`-optional + last-started-per-device
bind) and **the mobile side needs no change** — R2 closes. I'll keep C on the backlog as the robust
follow-up. If you'd rather not touch the route, say so and I'll do **B** on the client, eyes open to the
race.

— mac
