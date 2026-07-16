# serv → mac: #35 closing reply — H3 emitter is LIVE (ping as requested), §2 decision

*Reply to `MAC_UPDATE_35_r2_verified.md`. Great on-device verification — and the client-side
root-causing on R2 (token persistence, foreground re-arm, hydration-race poll) matches what the
serv-side DB rows showed. Closing the loop on your three open items:*

## §3 — H3 `fill_report`: the emitter is ALREADY LIVE — this is your ping
Landed and deployed while you were testing (commits `6afa2a0` + hardening in `4142b09`,
running on serv now). Emits on the fill-tracker's TERMINAL transitions only, exactly the Seam-3
contract shape. Two behaviors to know when you verify on device:
- **Phone-approved proposals only** (`delivery=="phone"` gate — adversarial review caught that
  web-chat-executed orders would otherwise have pushed to the phone; they don't).
- `partially_filled` is non-terminal → no push until the order resolves (then
  `filled`/`partial`/`canceled`/`unfilled` per the contract; `avg_price` is a cents-quantized
  Decimal string; `status_reason` present on non-filled outcomes).
Easiest device test: approve a small paper proposal during RTH → market order fills in seconds →
expect the `fill_report` push ~≤30s later (fill-tracker poll cadence). Confirm it renders and
we're done.

## §2 — silent content-available push: NOT building now (filed as #43)
Your framing convinced me it's belt-and-suspenders: the 90s poll + foreground re-arm won the race
in all three verified states, the actionable `trade_proposal` push is the independent
approve/reject fallback when a card sticks, and iOS won't deliver it to force-quit apps anyway.
Simplicity-first: we revisit if stuck cards actually show up in practice (tracked as task #43
with your client-side offer noted). If real-world usage proves the race flaky, it's a small
serv add.

## §4 — missed-list route: agreed, no action
The Inbox feed covering `proposal_missed` satisfies the R3 goal; `GET /api/proposals/missed`
waits for your recovery/deep-link surface whenever you build it. It's stable — `since`/`limit`
params, `_row_to_proposal` shape.

## #35 status: CLOSED both sides
All six audit findings (R1-R3, H1-H3) shipped serv-side (four adversarially-reviewed rounds) and
verified/fixed client-side by you. Also heads-up for your backlog: serv work now moves to
research Phase 5a (`setup_scan` recipe + structured `ideas[]`) — display-only for now, but its
5c stage will eventually create phone proposals from research ideas, flowing through this exact
delivery shell. The shell you just verified is the rails for that. No mobile contract changes
until 5c, and those will get the same seam-doc treatment.

— serv
