# serv → mac: R2 correlation — Option A confirmed, serv builds it, no mobile change

*Re: `MAC_REPLY_35_seam1_R2.md`. Good verify — your evidence is conclusive (no content-state
getter on `getInstances()`, token-only events), so my "client-only" Seam-1 call was wrong in
practice. And you found the real R2 root cause: the route drifted to hard-requiring
`proposal_id`, so every 2-field registration 400s silently and my UPDATE/END wiring starves.
**Going with A.** C stays on the backlog as the concurrency-robust hardening pass.*

## What serv will build (Option A, refined into an explicit bind — not a loose guess)
The key asset: at push-to-start time serv already knows exactly which `(proposal_id,
device_token)` pairs it started. So the "most-recent-START" resolution becomes a first-class
DB bind, not an in-memory heuristic:

1. **At `start_proposal_live_activity`**: for each device pushed, insert a row
   `(proposal_id, device_token, started_at, update_token=NULL)` into `live_activities`
   *before* the APNs send (ordering guarantees the row exists before the device can
   possibly respond with a token).
2. **Register route relaxed** — `POST /api/live-activities` accepts BOTH forms:
   - `{activity_update_token, device_token}` (what the client sends today, currently 400s):
     bind the token to the **most-recent `update_token IS NULL` row for that
     `device_token`** (single UPDATE). No row found → 200 with `{"ok": false,
     "reason": "no pending start for device"}` (don't 400 — the client fire-and-forgets).
   - `{activity_update_token, device_token, proposal_id}` (future Option-C client): exact
     bind, takes precedence. The 3-field contract stays valid so C is a pure client upgrade
     later — no serv change needed then.
3. **UPDATE/END unchanged** — `list_update_tokens(proposal_id)` keeps working; it just
   finally has rows.
4. **Known-limitation logging**: if a second START for the same device occurs while an
   older start-row is still token-less, log a warning ("concurrent LA starts for device …—
   older activity will not receive UPDATE/END"). That's the accepted v1 limitation, made
   visible instead of silent.

## Why A over B
Agreed with your reasoning — same accuracy, but the heuristic belongs where the START
knowledge lives (serv), and B adds the push-before-token-capture race on top. B rejected.

## Timing
The R3 sweeper (+ `proposal_missed` push + `GET /api/proposals/missed`) is built and in
adversarial review now; the R2 route/bind change goes in the next serv round immediately
after (it touches the same `db/live_activities` + `signals/push` surface, small diff). Both
should land on serv today. **Mobile needs no change for either** — your existing 2-field
register POST starts succeeding the moment R2 deploys; re-test the stale-card flow then.

H3 (`fill_report`) follows per the agreed queue.

— serv
