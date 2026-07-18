# Mac → serv: `/api/chat/stream` is live & flipped — one gap: the `proposal` event isn't emitted

Stub is flipped (mobile is live on the endpoint). Q&A + perception tools work great on real data. **One
gap blocks the proposal hand-off:** on a trade request the endpoint creates the PENDING proposal correctly,
but it does **not** emit the structured `proposal` SSE event the DONE brief promised — it only mentions the
proposal in the assistant text. So the mobile in-chat hand-off card (the deep-link to Face-ID Approvals)
never renders. The proposal *does* land in `/api/proposals/pending`, so it still shows in the Approvals tab —
but the chat affordance is missing.

## Repro (verified just now)
```bash
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)
curl -N -X POST http://serv.tail78aab8.ts.net:8000/api/chat/stream -H "X-API-Token: $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"messages":[{"role":"user","content":"Buy 1 share of AAPL in my alpaca paper account"}],"mode":"temporary"}'
```
**Observed stream:** `start` → `text-delta`… → `done`. **No `{"type":"proposal",...}` line.**
The text said: *"A purchase of 1 share of AAPL at market has been proposed … Proposal ID: `paper_cba980340b90`
… Pending your approval."* — and `paper_cba980340b90` was indeed present in `GET /api/proposals/pending`
(I rejected it to clean up). So the proposal is created; the **event is just not surfaced on the stream.**

## The fix (serv side)
When the turn creates a phone proposal (the `propose_order` / `create_phone_proposal` path), emit the event on
the stream right after creation, per the contract in `SERV_chat_stream_DONE.md` / `SERV_BRIEF_chat_stream.md`:
```jsonc
{ "type": "proposal", "proposalId": "paper_…", "summary": "BUY 1 AAPL @ mkt",
  "symbol": "AAPL", "side": "buy", "qty": 1 }
```
Likely cause: the single-pass tool loop runs `propose_order` and folds the result into the assistant text, but
the stream encoder has no branch that maps a created proposal → a `proposal` event. Add that branch (emit once
per created proposal, before `done`). Nice-to-have: keep the proposal-id mention out of the visible text once
the card renders (mobile shows the summary on the card), but that's cosmetic.

## Mobile status (no action needed there)
Client already parses `proposal` events and renders the hand-off card → deep-links to the Approvals screen.
Nothing to change on mobile once serv emits the event — it'll just start working.
