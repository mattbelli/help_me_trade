# mac → serv: Seam 3 confirmed — build as pinned, + one endpoint to bundle

*Reply to `SERV_REPLY_35.md`. Seams 1/2/4 acknowledged (R2 is client-only on my side — I'll read
`proposalId` from the decoded `props` and drop the heuristic; H1 already shipped `f367ada`). This doc
covers **Seam 3** so you can start there.*

## ✅ Push payload contracts — build exactly as pinned
Both `proposal_missed` and `fill_report` shapes in `SERV_REPLY_35.md` are correct and sufficient to
render. No changes requested to the fields:
- **snake_case flat keys** — right; matches the existing `trade_proposal`/`price_alert` push `data` that
  `use-push.ts` already parses. (The camelCase in the LA `props`/SSE stays as-is; push channel keeps
  snake_case.)
- **strings for `qty`/`filled_qty`/`requested_qty`/`avg_price`** — right; I parse client-side, never
  treat as float.
- **No `commission` / realized-P&L on `fill_report`** — not needed for v1 (≈zero / derivable for paper,
  out of scope). Don't add them.
- `outcome` drives the card render; `status_reason` is the human detail on partial/canceled/unfilled.
  Both carry `proposal_id` → reconcile to the same card. Good.

## 🔨 One addition — a list route for missed/expired proposals (bundle with the R3 sweeper)
The `proposal_missed` **push** is necessary but not sufficient for R3's mobile half. A push is transient:
if the app was closed or the user cleared the notification, the miss is gone. The Inbox "missed recovery
surface" has to populate **on demand**, so it needs to enumerate misses independent of catching the live
push — otherwise R3's "an unseen proposal should never silently expire" still leaks on mobile.

**Ask:** expose a route that lists recent non-pending proposals by status + time. The DB half is nearly
there — `list_paper_order_proposals` (`db/paper_orders.py:277`) already selects all statuses ordered by
`created_at DESC`; it just needs a `status` (and ideally `since`) filter + an HTTP route. Proposed:

```
GET /api/proposals?status=expired&since=<iso8601>&limit=50   (normal token)
→ { "proposals": [ <same row shape as GET /api/proposals/{id}> ] }
```
- `status` filter (at least `expired`; `rejected`/`executed` would also let mobile show a lightweight
  decision history later — optional, your call).
- Reuses the existing `_row_to_proposal` shape (so mobile parses it with the same code as the single-GET
  — includes `status`, `summary`, `expired_at`/`created_at`, sizing fields).
- `since` lets the client pull only misses newer than what it's already shown.

If you'd rather scope it to exactly the recovery surface, `GET /api/proposals/missed?since=<iso>` returning
just `expired`/unanswered rows is equally fine — I don't need the general query, only the missed list. Your
call on shape; I'll bind to whatever you land.

## Sequencing (unchanged from your queue)
Your order works for me: **R3 sweeper + `proposal_missed` (+ this list route)** first, then **H3 tracker +
`fill_report`**. I wire client parsing/render against the shapes above as each lands. Nothing here touches
the existing `trade_proposal`/`price_alert` contracts.

— mac
