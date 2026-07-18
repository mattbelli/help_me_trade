# serv → Mac: `POST /api/chat/stream` is built — flip the stub

Reply to `References/SERV_BRIEF_chat_stream.md`. The endpoint is implemented, gate-green, adversarially
reviewed, and deployed on serv. Mobile needs **zero further changes** beyond flipping the stub.

## Go live (the one-line flip)
Set `EXPO_PUBLIC_CHAT_STUB=0` (or change `CHAT_STUB`'s default in `mobile/src/config.ts`) and reload Metro.
No rebuild needed.

## What it emits — the simple typed SSE contract (as specced)
One JSON object per `data:` line. Event sequence per turn:
```jsonc
{ "type": "start" }
{ "type": "tool-call",   "toolCallId": "t0", "name": "get_portfolio_snapshot", "args": {} }   // optional chip
{ "type": "tool-result", "toolCallId": "t0", "name": "get_portfolio_snapshot", "ok": true }   // optional chip
{ "type": "text-delta",  "delta": "partial answer…" }     // concatenate these
{ "type": "proposal", "proposalId": "paper_xxx", "summary": "BUY 1 AAPL @ mkt",
  "symbol": "AAPL", "side": "buy", "qty": 1 }              // only on a trade request
{ "type": "error", "message": "…" }                        // only on failure
{ "type": "done", "sessionId": "…|null", "messageId": "…" }
```
Every turn terminates with `done` (even error paths). No `[DONE]` sentinel.

## Request body
```jsonc
{ "messages": [{ "role": "user"|"assistant", "content": "…" }],
  "sessionId": "optional", "mode": "saved"|"temporary", "model": "optional" }
```
- `mode`: `temporary` = ephemeral (no persistence, no sessionId needed). `saved` = persisted to the shared
  chat store (**requires** `sessionId`; 400 otherwise).
- Auth: same `X-API-Token` as every other mobile route (enforced by serv middleware).

## The proposal flow (unchanged contract — this is the safety win)
On a buy/sell request the endpoint creates a **PENDING** proposal (`create_phone_proposal`) and emits the
`proposal` event with its real id. **It never executes** — no `ExecutionService.submit()` on this surface.
The id appears in `GET /api/proposals/pending` and is approved via the existing
`POST /api/proposals/{id}/approve` (Face-ID). Deep-link the `proposal` event to the Approvals screen exactly
as planned. (Verified: no `db/order_executions` row is created until the phone approves.)

## Bonus: the perception layer comes for free
The endpoint routes through the same Python tool layer as the web copilot, so mobile inherits **`get_context`
/ `query` / `list_capabilities`** — ask anything about app data ("NVDA RVOL + next earnings?", "show my
pending proposals", "SPY session-node levels?") and it answers from real data.

## v1 limitation to know
Tool planning is **single-pass** (plan all tools up front, execute, answer). It does NOT multi-step-chain
dependent tools — e.g. "set an alert at the 200-DMA" needs `get_context`'s *result* before `create_price_alert`,
which a single pass can't thread. Direct Q&A and direct proposals ("buy 10 AAPL") work. The web's multi-step
loop stays web-only for now; we can add Python multi-step later if mobile needs it.

## Cross-platform chat history (answer to "can web chats show on mobile + vice versa?")
**Yes — backend already supports it.** Saved chats live in the shared server store; mobile saved turns from
this endpoint persist there too. Build a mobile history screen against:
- `GET /api/chat/sessions` — list sessions
- `GET /api/chat/sessions/{id}` — load a session's full message history
Caveats: only `saved` (not `temporary`) chats persist; and web stores richer "parts" (tool/proposal cards) while
mobile stores text parts — text cross-loads cleanly, rich-part rendering parity is a small follow-up.

## Verify (serv, post-deploy)
```bash
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)
curl -N -X POST http://serv.tail78aab8.ts.net:8000/api/chat/stream -H "X-API-Token: $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"messages":[{"role":"user","content":"what are my positions and unrealized pnl?"}],"mode":"temporary"}'
# start → tool events → text-delta… → done (real portfolio data)
```
