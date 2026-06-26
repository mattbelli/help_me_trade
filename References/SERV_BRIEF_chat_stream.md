# Serv brief — build `POST /api/chat/stream` (mobile copilot endpoint)

*Mac→serv handoff. The RN chat copilot UI is **built, verified on-device, and merged on
`feat/mobile-app`** — it streams against the contract in `References/MOBILE_CHAT_BRIEF.md`. Right now it
runs on a local stub (`mobile/src/config.ts` → `CHAT_STUB`) because this endpoint is **404 on serv**. This
brief is the serv side: add the route so mobile can flip `CHAT_STUB=false` and go live. Backend is yours;
mobile needs ZERO further changes once this lands.*

## What to build
A new FastAPI route **`POST /api/chat/stream`** in `api/ai_routes.py` (sibling of the existing
`@router.post("/chat")`), under the same `X-API-Token` auth as every other mobile route, returning
`StreamingResponse(media_type="text/event-stream")`.

It must emit the **simple typed event contract below — NOT the AI-SDK v1 protocol** that the existing
`/api/chat` emits. RN cannot consume the AI-SDK UI-message-stream; that's the whole reason for a separate
endpoint. The mobile parser (`mobile/src/api/chat-stream.ts`) reads **one JSON object per SSE `data:` line**.

### Request body (JSON)
```jsonc
{ "messages": [{ "role": "user"|"assistant", "content": "..." }],
  "sessionId": "optional-string", "mode": "saved"|"temporary", "model": "optional-string" }
```
- **mode mapping:** mobile sends only `saved` | `temporary`. Map to your `CHAT_MODES`:
  `saved → "saved"` (persist; requires `sessionId` like `/api/chat` does), `temporary → "quick"` (no
  persistence, no `sessionId` required). Confirm the exact internal names against `normalize_chat_mode`.

### Response — SSE, one JSON event per `data:` line
```jsonc
{ "type": "start" }
{ "type": "text-delta", "delta": "partial assistant text…" }   // mobile concatenates these
{ "type": "tool-call",   "toolCallId": "t1", "name": "get_context", "args": { "symbol": "AAPL" } }
{ "type": "tool-result", "toolCallId": "t1", "name": "get_context", "ok": true }
{ "type": "proposal", "proposalId": "p_123", "summary": "BUY 10 AAPL @ mkt",
  "symbol": "AAPL", "side": "buy", "qty": 10 }
{ "type": "error", "message": "human-readable" }
{ "type": "done", "sessionId": "s_…", "messageId": "m_…" }
```
- `tool-call`/`tool-result` are optional (drive a "thinking…" chip); fine to omit in v1.
- Terminate the turn with a `done` event. A trailing `[DONE]` line is tolerated but not required.

## Reuse what already exists
- The brain is built: `run_chat_stream(...)` / `finalize_chat_turn(...)` (`ai/chat/agent.py`), chat-session
  CRUD (`db/chat_sessions`), tools (`ai/chat/tools.py`). `run_chat_stream` currently yields **AI-SDK-format**
  chunks — so either (a) add a thin re-encoder that maps the loop's text/tool/proposal events onto the simple
  contract above, or (b) add a parallel generator that yields the simple events directly. Don't make mobile
  parse AI-SDK.
- A `UIMessageStream`-style encoder for the simple format would mirror `ai/chat/stream.py`.

## ⚠️ The safety-critical piece — proposals must be PENDING, never executed here
This is the heart of the mission (`CLAUDE.md` §Safety) and the one place this endpoint must differ from a
naive reuse:

- The mobile flow is **agent proposes → human approves with Face ID → chokepoint executes**. When the copilot
  decides to trade, this endpoint must **create a PENDING phone proposal and emit a `proposal` event with its
  id — and STOP. It must NOT call `ExecutionService.submit()`.** Execution happens only later, when the phone
  hits `POST /api/proposals/{id}/approve` (already built + verified end-to-end).
- ⚠️ The current `propose_order` tool (`ai/chat/tools.py:~675`) **records the proposal AND immediately calls
  `ExecutionService.submit(intent)`**. For this endpoint that submit must not run — the proposal has to land
  as pending and wait for the biometric approve. Pick the cleanest path (a propose-only tool variant, a
  pending-only flag, or gating submit off for this surface) and **adversarially review it (Sonnet) before
  committing** — this is a `trading/`-adjacent change.
- The pending proposal must show up in `GET /api/proposals/pending` (the existing `record_paper_order_proposal`
  path already does this) so it appears in the phone's Approvals screen — that's where the `proposal` event's
  deep-link sends the user.
- Keep the existing guards: the chat agent only proposes (never gets a direct execute toolset); paper accounts
  only; sells reduce a held long; Decimal seam at the propose boundary.

## Verify (curl from serv, then mobile)
```bash
TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2-)
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "X-API-Token: $TOKEN" -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"messages":[{"role":"user","content":"what are my positions and unrealized pnl?"}],"mode":"temporary"}'
# expect: start → text-delta… → done   (real portfolio data)

# proposal path — must create a PENDING proposal and NOT execute:
curl -N -X POST http://127.0.0.1:8000/api/chat/stream -H "X-API-Token: $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: text/event-stream' \
  -d '{"messages":[{"role":"user","content":"buy 1 share of AAPL in my alpaca paper account"}],"mode":"temporary"}'
# expect: …text… → proposal{proposalId,...} → done; the id appears in GET /api/proposals/pending;
#         NO execution recorded in db/order_executions until the phone approves it.
```

## Go-live (mobile side — already done, for reference)
Once this is deployed and the curls pass, mobile goes live with the one-line flip: set
`EXPO_PUBLIC_CHAT_STUB=0` (or change `CHAT_STUB`'s default in `mobile/src/config.ts`) and reload Metro — no
rebuild (pure JS; `expo/fetch` is already bundled). The client already handles every event type above,
including the proposal hand-off to the Face-ID Approvals flow.
