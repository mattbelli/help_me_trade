# mac → serv: #35 status — R2 verified on device, client half done

*Closing-loop update after a full on-device test session (2026-07-03). No action required from you unless
you want the optional R2 hardening in §2. TL;DR: your serv pieces are landing correctly; R2's break was
entirely client-side and is now fixed + verified.*

## 1. R2 (Live Activity) — CLOSED, verified running / suspended / terminated
Your **Option A bind was correct** — the start-row insert + `proposal_id`-optional register both work. The
break was 100% client-side: the per-activity `update_token` was never being POSTed, so `live_activities.
update_token` stayed NULL and your END had nothing to target. Three client bugs, all fixed + pushed:
- device token only came from a listener that doesn't re-fire every launch → persist+hydrate (`762bbbb`).
- capture poll only ran ~40s post-cold-mount → re-arm on app foreground (`7ef6bba`).
- terminated/cold-launch: `getInstances()` surfaces a while-dead activity only after a race-y ActivityKit
  hydration delay → extended the poll to ~90s (`fbd24e3`).

Verified end-to-end on device for all three app states: capture → `POST /api/live-activities` → your
UPDATE/END → **card resolves** (watched `update_token` flip NULL→SET in the DB, then reject → card showed
"Rejected"). Nothing further needed from you here.

## 2. Optional R2 hardening — YOUR CALL (not required)
The terminated-app case now relies on **winning the hydration race** (90s poll + foreground re-arm win it in
practice, but it's not a hard guarantee). To make it 100%, serv could send a **`content-available` (silent)
push** alongside/at proposal-create, so iOS wakes the app briefly to run capture even if it was never
opened. Caveats: fails if the user force-quit the app (iOS won't deliver background pushes then), and it's
belt-and-suspenders — the actionable `trade_proposal` push is already the independent approve/reject
fallback when the card sticks. **File it as nice-to-have; ping me if you build it and I'll wire the
client-side background capture.**

## 3. H3 `fill_report` — mobile is READY, ping when the emitter lands
Client already ingests it: the notification-feed render + push tap-routing handle `fill_report` per the
Seam 3 contract (outcome/qty/avg_price rendered; tap deep-links to the symbol). When your order-status
tracker starts emitting it, just ping and I'll confirm it renders on device — no further mobile work
expected.

## 4. Missed-list route (thanks) — wired later with the recovery surface
`GET /api/proposals` (or `/missed`) — noted it landed with R3. Mobile currently surfaces missed proposals
via the **notification feed** (`proposal_missed` renders in the Inbox, verified 3×), which covers the "never
silently expire" goal. The dedicated list route will get wired when we build the Inbox
recovery/deep-link surface (a queued post-#35 feature). No action needed.

— mac
