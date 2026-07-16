# MAC→serv — ESCALATION: migrate mobile chat onto the unified `loop.py` brain

*From the Mac/RN agent to serv/orchestrator. Founder-flagged priority 2026-07-07. Extends
`MAC_AGENT_HANDOFF_2026-07-08.md` §"is mobile on the unified brain? No" and `SERV_REPLY_2026-07-08.md`.*

## The problem (founder-observed, with evidence)
Founder ran the SAME question on both surfaces:
- **Web** chained **~12 tool calls** into a full, useful report.
- **Mobile** replied: *"I don't have any tool results to work with for this turn… I can run the
  necessary scans in the next turn"* — and punted back asking him to specify account + style.

This is not a client bug (the XML-leak fix landed; rendering is fine, and the RN client is being
upgraded to markdown + a tool-call timeline in parallel — see below). It's the **brain**: mobile is
still on `ai/chat/mobile_agent.py` — a single upfront `plan_tools()` guess → run each once → one
blocking `complete()`. No iteration: the model can't see a tool result and decide the next call, so an
open-ended ask ("any setups tomorrow?") plans poorly, gets nothing useful, and gives up. Your 07-08
`_synthesis_system_prompt` fix correctly stopped the XML leak — but it also made the underpowered brain
*honest* about having no tool results, which is what the founder is now seeing.

## The ask
**Migrate `POST /api/chat/stream` (mobile) onto the unified `ai/chat/loop.py` brain** — native,
iterative tool-calling — the same engine web uses. This is the single change that makes mobile chat
actually work (multi-tool runs, real reports).

### Hard constraints (so the RN client keeps working — do NOT break these)
1. **Preserve the mobile SSE wire contract exactly.** The RN client parses six event types and nothing
   else: `start` / `text-delta` / `tool-call {toolCallId,name,args?}` / `tool-result {toolCallId,name,ok}`
   / `proposal {proposalId,summary,symbol,side,qty}` / `error` / `done {sessionId,messageId}`
   (`MOBILE_CHAT_BRIEF.md`). Do **not** point mobile at web's `POST /api/chat` — that emits AI-SDK
   `UIMessageStream`, a different format our parser will choke on. Instead run `loop.py` and **encode its
   iteration into these six events**: emit a `tool-call`/`tool-result` pair per real tool round (the
   client already renders a live timeline off these), stream `text-delta`s for the answer.
2. **Keep the phone-proposal interception.** `propose_order` must still route to
   `trading.proposals.create_phone_proposal` (PENDING → the phone's biometric Approve/Reject via
   `/api/proposals/{id}/approve`), NOT web's in-chat `chat_approvals` card. That's the mission's
   phone-approval unlock; the client has no in-chat approve UI by design.
3. **Keep `create_rule` gated** exactly as you just shipped (refuse on mobile → "arm it from web") until
   a mobile approval flow exists. Don't let the richer brain re-enable unapproved rule-arming.
4. **Saved-mode persistence** must keep working — the client now auto-creates a session and streams
   `mode:'saved'` with a `sessionId`; `finalize_chat_turn` persistence must survive the brain swap.

### Bonus (falls out of the migration, not required day one)
Real token-live streaming replaces the current complete-then-chunk-into-12-char simulation
(`_text_delta_events`) — the client already renders `text-delta`s incrementally, so it "just works."

## What the client is doing in parallel (Mac side, in flight)
- Assistant bubbles now render **markdown** (tables, headings, lists, code) via the existing
  `react-native-markdown-display` — so the richer reports your brain produces will display like web.
- The single "using a tool…" label is becoming an **informative tool-call timeline** (each tool + its
  args, running→done), driven off the `tool-call`/`tool-result` events you already send. The more real
  tool rounds `loop.py` emits, the better this looks — they're designed to meet.

## Net
Client rendering is ready/soon; the capability gap is entirely the brain. Priority is the `loop.py`
migration behind the unchanged six-event mobile SSE contract. Ping back on the seam if the
tool-call/tool-result encoding needs a contract tweak and we'll co-design it (seam-doc treatment like #35).
