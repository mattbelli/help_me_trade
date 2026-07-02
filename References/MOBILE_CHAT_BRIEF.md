# Brief for the Mac agent — mobile chat (copilot) UI

*serv→Mac handoff. Implement the AI chat/copilot UI in the React Native app (`mobile/`, branch
`feat/mobile-app`). The chat screen is currently a placeholder (`mobile/src/features/chat/chat-screen.tsx`,
"coming soon"). Build the real UI + a streaming client against the SSE contract defined below. **serv owns the
backend** — a new Python streaming chat endpoint (`POST /api/chat/stream`) — which is being built in parallel;
this brief is the agreed contract so you can build the UI now against a stub and wire to the live endpoint when
it ships. Conventions: `mobile/CLAUDE.md` + `mobile/AGENTS.md`. Reuse `mobile/src/api/*`.*

## Why this isn't "just frontend" (the context that matters)
The web copilot's whole agentic loop — the LLM call, the multi-step tool loop, streaming — lives in the **web's
TypeScript Next.js route** (`frontend/app/api/chat/route.ts`, AI SDK `streamText`). **You cannot reuse that from
RN** (no RN adapter for the AI-SDK stream protocol; that route has no token auth; it'd couple mobile to the Next
server). So serv is moving the loop into a **Python streaming endpoint** that emits a simple typed SSE the phone
can consume. Your job is the RN UI + the streaming client + the proposal hand-off — NOT the agentic loop, the
LLM call, or tool logic (all server-side).

## The contract — `POST /api/chat/stream` (serv builds this)
- **Auth:** `X-API-Token` header (same as every other mobile call — `mobile/src/api/client.ts` already does it).
- **Request body (JSON):**
  ```jsonc
  { "messages": [{ "role": "user"|"assistant", "content": "..." }],
    "sessionId": "optional-string", "mode": "saved"|"temporary", "model": "optional-string" }
  ```
- **Response:** `text/event-stream`. Each SSE `data:` line is one JSON event. Event types:
  ```jsonc
  { "type": "start" }                                              // turn began
  { "type": "text-delta", "delta": "partial assistant text…" }    // append to the assistant bubble
  { "type": "tool-call",   "toolCallId": "t1", "name": "get_context", "args": { "symbol": "AAPL" } }
  { "type": "tool-result", "toolCallId": "t1", "name": "get_context", "ok": true }   // result body omitted/small
  { "type": "proposal", "proposalId": "p_123", "summary": "BUY 10 AAPL @ mkt",
    "symbol": "AAPL", "side": "buy", "qty": 10 }                  // copilot created a PENDING proposal
  { "type": "error", "message": "human-readable" }
  { "type": "done", "sessionId": "s_…", "messageId": "m_…" }      // turn complete + persisted
  ```
  `tool-call`/`tool-result` are for an optional inline "thinking…" chip — not required for v1. The assistant text
  is the concatenation of all `text-delta`s.

## The ONE mission-critical wiring rule — proposals
When the copilot wants to place a trade, the server creates a **pending phone proposal** and emits a `proposal`
event. **Do NOT build an approve/reject UI inside chat.** Instead show a compact affordance in the message
stream — e.g. a card "📋 Proposal ready: BUY 10 AAPL — Review in Approvals" — that deep-links to the **existing**
approvals screen (`mobile/src/features/approvals/proposal-card.tsx`), where the user approves with **biometrics**
(`POST /api/proposals/{id}/approve`, payload_hash, Face ID). That approval flow is already built and IS the
phone-approval mission — chat just hands off to it. (The pending proposal will also already be in
`GET /api/proposals/pending`, so even without the deep-link it surfaces in Approvals.)

## RN streaming — the real technical risk (evaluate first)
RN's `fetch` does not expose `response.body` as a `ReadableStream` in a stock setup, so plain SSE parsing fails.
Pick ONE and confirm it works on device before building the UI on top:
- **`expo/fetch`** (Expo SDK 52+) — supports streaming responses; preferred since this is an Expo app.
- **`react-native-sse`** (EventSource polyfill) — works, but POST + custom headers support varies by version.
- XHR chunk-reading fallback.
Add a small `streamChat()` helper next to `mobile/src/api/client.ts` that opens the POST, reads the SSE lines,
JSON-parses each `data:` line, and yields typed events to the screen. Keep `X-API-Token` from secure-store.

## UI scope (this is the part you own freely)
- Replace the placeholder `chat-screen.tsx`: message list (user/assistant bubbles), an **assistant bubble that
  grows as `text-delta`s arrive**, input bar with send, keyboard-avoiding behavior, auto-scroll, error toast.
- The proposal hand-off card (above).
- Optional v1+: tool "thinking" chips; thread history via `GET /api/chat/sessions` + `POST /api/chat/sessions`
  (session persistence — `sessionId` round-trips through the stream `done` event).
- Match `mobile/CLAUDE.md` design system (theme, components in `mobile/src/components/ui`, `Screen`/`Stack`/`Text`).

## Out of scope (serv owns)
- `POST /api/chat/stream` itself, the LLM call, the tool loop, tool logic, proposal creation/execution.
- FYI only (don't touch): tools are defined in Python `ai/chat/tools.py` and run server-side; there's a separate
  web tool surface (`frontend/lib/chat-tools.ts`) — mobile uses neither directly, it just consumes the stream.

## Build against a stub until the endpoint ships
Until serv lands `/api/chat/stream`, point `streamChat()` at a local stub that emits a canned sequence
(`start` → a few `text-delta`s → optional `proposal` → `done`) so you can build + verify the whole UI + parser
without the backend. Swapping the base URL in is then a one-line change.

## Verify
- `cd mobile && npm run typecheck` (+ the app's lint), and confirm streaming works **on device/simulator**
  (the RN-streaming risk above is the thing to de-risk first).
