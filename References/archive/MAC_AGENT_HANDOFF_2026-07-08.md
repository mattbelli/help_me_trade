# MAC_AGENT_HANDOFF_2026-07-08 — serv→Mac, post chat-brain-unification + SIP-hybrid session

*From serv (this repo, `/home/mbelli/dev/help_me_trade`) to the Mac/RN agent (owns `mobile/`, different
repo/branch `feat/mobile-app`). I cannot see the RN code — everything below is the serv-side contract +
what changed; you verify/apply on the client. Companions this extends: `RN_AGENT_HANDOFF.md`,
`MOBILE_CHAT_BRIEF.md`, `RN_STREAMING_ALERTS_RELAY.md`, `RN_POSTMARKET_CHARTS_RELAY.md`,
`SERV_TO_MAC_HANDOFF_2026-07-03.md`.*

## HEADLINE ANSWER — is mobile chat on the unified brain?

**No. Mobile chat is stranded on the pre-unification path, unchanged this session.** This is not a
regression (nothing broke) — it's a standing gap the founder was already told about ("Phase 1b
consciously deferred the mobile internals swap"). But concretely, today:

- Web chat (`POST /api/chat`) now runs the unified Python brain: `ai/chat/loop.py::run_chat_loop`, native
  iterative tool-calling (up to 6 rounds, can chain tool calls off results), real token-live streaming, and
  a DB-backed single-consume approval ledger (`db/chat_approvals.py`) gating `propose_order`/`create_rule`.
- Mobile chat (`POST /api/chat/stream`) still runs `ai/chat/mobile_agent.py::run_mobile_chat_stream`, a
  **separate, older, simpler orchestrator**: one upfront JSON "plan" call (`ai/chat/tools.py::plan_tools`,
  not native tool-calling) → run each planned tool once → one final blocking `LLM.complete()` call whose
  text is retroactively chopped into fixed 12-char chunks and replayed as fake `text-delta` events
  (`ai/chat/mobile_agent.py:25-31,150-157`). There is no iteration — the model can't see a tool result and
  decide to call another tool in the same turn, and there is no true token-by-token streaming (it's a
  complete-then-fake-chunk simulation, same as it's always been — not newly degraded).
- **The wire contract is unchanged** — `POST /api/chat/stream`'s SSE event shapes
  (`start`/`text-delta`/`tool-call`/`tool-result`/`proposal`/`error`/`done`) are byte-identical to the
  original `MOBILE_CHAT_BRIEF.md` contract. If your RN client already implements that brief, **it will
  keep working exactly as before** — nothing is broken, nothing requires an urgent client fix.
- What mobile chat **does** inherit for free, because both paths call the same shared functions:
  - `execute_tool()` (`ai/chat/tools.py`) — same tool implementations, so the 07-07 "Schwab accounts visible
    read-only in portfolio snapshot" fix and the "deny-by-default proposal intake" guard apply to mobile
    automatically (verified: `trading/proposals.py::create_phone_proposal` calls the same
    `build_order_proposal_from_args` that gained the `account_is_execution_capable` gate).
  - `_system_prompt()` (`ai/chat/agent.py`) — same system prompt (accounts context, emoji rule, date), so
    prompt parity holds between web and mobile.
  - Memory injection (`turn_memory_context`/`format_memory_context_block`) and the trust-fence provenance
    chips (`founder-authored`/`founder-confirmed`/`agent-inferred (unverified)`) — the 07-08 memory
    trust-fences round explicitly wired these into "BOTH surfaces (web brain + mobile propose-capable
    prompt)". Mobile answers may now reference memories with the same provenance framing as web (text only
    — no RN UI change implied unless you want to render the badge, your call).
  - Memory/journal extraction after the turn (`finalize_chat_turn`) — shared, so mobile turns feed the same
    learning pipeline as web.

### What is genuinely different (beyond streaming quality) — approval handling asymmetry

Mobile's `propose_order` is intercepted **before** `execute_tool` and routed to
`trading.proposals.create_phone_proposal` (a PENDING-only phone proposal for your existing biometric
Approve/Reject flow) — this is intentional, pre-existing, and correct; it is a *different* approval surface
from web's new `chat_approvals` ledger by design (push+biometric vs in-chat card), not a gap.

**However**, mobile's loop only special-cases `propose_order` — `create_rule` is NOT intercepted
(`ai/chat/mobile_agent.py:67-129`, the `if name == "propose_order"` branch is the only carve-out). It falls
through to `execute_tool("create_rule", …)`, which (`ai/chat/tools.py:1087-1116`) **creates and enables the
rule immediately**, no approval step at all. On web, `create_rule` is one of the two `APPROVAL_TOOLS` and
always pauses for an Approve/Reject card first. So today: asking the mobile copilot to "alert me when X
crosses Y" arms a live rule with zero confirmation, while the identical request through web chat requires an
explicit tap. **This is a serv-side code issue (`ai/chat/mobile_agent.py`), not something you can or should
fix in the RN client** — flagging it to the orchestrator to fix serv-side. Nothing for you to do here except
know it exists if the founder asks "why did an alert fire that I never approved on my phone."

## Ranked task list for the Mac agent

### 1. Chat — verify only, likely no code change needed (RISK: low now that contract is confirmed stable)
- **Verify** your `streamChat()` parser still matches the six event types in `MOBILE_CHAT_BRIEF.md` — they
  are unchanged. Confirm on a live call against `POST /api/chat/stream`.
- **Verify** you are NOT calling the legacy no-tools web route — there never was a mobile-facing one to
  begin with (mobile always had its own `/api/chat/stream`), but double-check no stray code points at
  `/api/chat` (that route now emits AI-SDK `UIMessageStream` chunks, an entirely different wire format your
  parser would choke on — it's the web-only proxy target).
- **No change required** for the approval hand-off — keep deep-linking `proposal` events to the existing
  Approvals screen / biometric flow exactly as `MOBILE_CHAT_BRIEF.md` describes. That flow is untouched and
  is the correct, safer mechanism (mobile's own ledger-equivalent is `create_phone_proposal`'s row + your
  existing `POST /api/proposals/{id}/approve` payload_hash check — you do NOT need `chat_approvals`
  `approval_id`/hash plumbing, that's web-card-only).
- **Optional, not urgent:** if you want mobile chat to feel as responsive as web's new token-live stream,
  that requires a serv-side rewrite (swap mobile onto `ai/chat/loop.py`) — out of your scope, flag back to
  serv as a product ask if the founder wants it. Today's 12-char-chunk simulation is unchanged and still
  functions.
- **Mac agent to confirm (can't see from serv):** does your current RN client already correctly consume all
  six event types, or is anything still relying on assumptions from before the brief was finalized?

### 2. SIP hybrid quotes — verify checklist, contract unchanged (RISK: low)
Serv now routes equities/ETFs through Alpaca SIP (regular price, bars, streaming) and keeps Schwab as the
**only** source for extended-hours fields (`ext_price`/`ext_change_pct`) and fundamentals. Futures stay on
Schwab entirely. This is all behind `MARKET_DATA_PROVIDER=hybrid` and is a data-path-only change — the
`/ws/market` and `/api/watchlist/market` **response shapes are byte-identical** to what `RN_STREAMING_ALERTS_RELAY.md`
already documents:
```jsonc
// WS quote frame (api/main.py ~line 2078-2114, unchanged shape)
{
  "type": "quote", "symbol": "TSLA", "live": true,
  "price": 382.50, "change_pct": -0.12, "sparkline": [...],
  "bid": 382.45, "ask": 382.55, "volume": 4321000,
  "ext_price": 382.74, "ext_change_pct": 0.22, "market_session": "overnight"
}
```
```jsonc
// GET /api/watchlist/market (api/main.py ~line 2081-2102, unchanged shape)
{ "quotes": { "TSLA": {
    "price": ..., "change_pct": ..., "sparkline": [...], "prev_close": ...,
    "volume": ..., "day_high": ..., "day_low": ..., "intraday": [...],
    "market_session": "pre"|"regular"|"post"|"overnight"|"closed",
    "ext_price": ..., "ext_change_pct": ..., "pe": ..., "eps": ..., "div_yield": ...,
    "market_cap": ..., "last_earnings": ..., "next_earnings": ..., "rvol": {...}
}}}
```
- **Verify — ext/overnight behavior just observed live (2026-07-06 fix, `brokers/market_clock.py` +
  `brokers/schwab_market_stream.py::_on_equity`):** outside `regular` session, `price`/`change_pct`/
  `sparkline` FREEZE at the 4pm regular close; `ext_price`/`ext_change_pct`/`market_session` are what keep
  moving (sourced from the Schwab level-one stream and Schwab REST quotes, batched into the WS via
  `apply_extended()`). This matches what `RN_AGENT_HANDOFF.md` already says mobile does — confirm your
  client still headlines `ext_price` (not `price`) whenever `market_session != "regular"`, and shows the
  PRE/AFT/24h(overnight) badge from `market_session`, exactly as before. `market_session` values are now
  formally 5-valued: `pre|regular|post|overnight|closed` — `closed` (weekend) also carries the last extended
  print, not null, so don't treat `closed` as "no data."
  - **Known prior workaround, re-verify it's still needed:** `RN_AGENT_HANDOFF.md` notes mobile *derives*
    `ext_price = price × (1 + ext_change_pct/100)` because serv's `ext_price` used to go stale in pre/post.
    That specific staleness bug predates this session's fixes and was never confirmed re-tested live per
    `RN_POSTMARKET_CHARTS_RELAY.md`'s "optional" section. **Mac agent to confirm**: with the fresh SIP-hybrid
    + freeze fix, does serv's raw `ext_price` now stay fresh enough that the client-side derivation can be
    retired, or is it still needed as a safety net? (Serv can't verify this without your client-side eyes on
    a live overnight session.)
- Charts (`/api/bars`) have **no overnight candles** — they still stop/gap at session boundaries per
  `RN_POSTMARKET_CHARTS_RELAY.md` (pre/post now included, but true 20:00→04:00 overnight is not, since Schwab
  extended-hours bars don't cover the globex-style overnight block for equities the way `ext_price` ticks
  do). Quotes update overnight; candles don't. Confirm your chart doesn't render a misleading flat/extending
  line implying candle data exists overnight when it's really just the last close repeated.
- **Symbol cap raised 30→100** on the equity WS stream (Alpaca SIP has no such limit; was previously capped
  to protect the old Schwab-only stream). No client action — just know a larger watchlist won't get silently
  truncated server-side anymore.
- Crypto/option symbol classification exists (`brokers/symbol_class.py::classify_symbol`) but their live
  streams are **deferred** — REST-routed only, no action needed on your side, just don't expect live crypto
  ticks over `/ws/market` yet.
- **Net:** this is a verify-only pass. Nothing indicates the mobile quote/chart contract broke. If the
  founder reports mobile quotes look wrong, it is very likely the client-side derivation workaround above
  now double-correcting a value serv already fixed — check that first.

### 3. Other serv deltas this session — quick scan, mostly no client action

| Item | Changed | Mobile impact |
|---|---|---|
| **Day P&L endpoint** | `GET /api/performance/day-pnl?account_id=<workspace-selector-id>` is a **new** endpoint (07-07, `api/performance_routes.py:40-62`) returning `{"day_pnl": float, "has_activity": bool, "scope_fallback": bool}` — realized, net-cash, ET-day, sourced from the same `daily_realized` the trading calendar uses. It replaces reading Schwab's raw intraday `day_pl` snapshot field, which had a known bug: an after-hours-closed trade leaks into the next day's figure. | **Action for you:** if the mobile Home screen shows a "Day P&L" figure sourced from a Schwab snapshot field or an older path, switch it to this endpoint for correctness — same account-id semantics as every other performance widget (`account_scope.py` accepts `schwab_<last4>`, `acct_*`, `all_real`, `all_paper`, `overview`, fail-soft). If mobile already calls this endpoint, no action — just confirm the account_id you pass resolves the way you expect (fail-soft to aggregate-real on an unrecognized id, `scope_fallback:true` flags that). |
| **Schwab accounts visible in chat brain** | `get_portfolio_snapshot` now folds in real (read-only) Schwab accounts that previously fell through the cracks (poll_runs-only bug) — the brain used to wrongly tell the founder he was "all in cash." | **No mobile action** — shared `execute_tool`, mobile chat already benefits automatically (see Headline section). Nothing to change. |
| **Deny-by-default proposal intake** | `build_order_proposal_from_args` now rejects a Schwab/read-only account_id *before* a proposal row is created (previously it could be created and only failed at approve-time). | **No mobile action** — `create_phone_proposal` uses the same shared builder, so this hardening applies to phone proposals for free. Worth knowing: if a user tries "sell my Schwab AAPL" via mobile chat, they should now get an immediate in-chat rejection message instead of a proposal that silently fails on approve. |
| **Memory Phase 1 (judge, provenance, trust fences)** | Judge-mediated ADD/UPDATE/SUPERSEDE, provenance badges, per-session quote-save cap (5), confirmed/pinned rows untouchable. | **No mobile action** — wired into "both surfaces" per the 07-08 commit; mobile inherits the safety properties (can't be tricked into overwriting a founder-confirmed memory) automatically since it's the same `save_memory`/`forget_memory` tool implementations. If you ever want to *render* the provenance chip in the RN chat UI, that's a nice-to-have, not a fix. |
| **Approval-tool asymmetry (`create_rule` unguarded on mobile)** | See Headline section — mobile's `create_rule` bypasses any approval step. | **Not your fix** (serv-side `ai/chat/mobile_agent.py` bug) — flagged to serv/orchestrator below. No RN client change possible or needed; the gap is entirely in what serv executes before your client ever sees a response. |

## Files/endpoints referenced (serv-side, for your own verification if you have API access)
- `api/ai_routes.py:440-501` — `POST /api/chat/stream` (mobile route, unchanged contract).
- `api/ai_routes.py:504-599+` — `POST /api/chat` (web-only unified-brain route, different wire format — not
  for mobile).
- `ai/chat/mobile_agent.py`, `ai/chat/mobile_stream.py` — mobile's orchestrator + SSE encoder.
- `ai/chat/loop.py`, `ai/chat/web_agent.py`, `ai/chat/tool_specs.py`, `db/chat_approvals.py` — the unified web
  brain (not on mobile's path).
- `ai/chat/tools.py` — shared tool implementations (`execute_tool`, `plan_tools`) both surfaces call.
- `brokers/hybrid_market_data.py`, `brokers/hybrid_market_stream.py`, `brokers/symbol_class.py`,
  `brokers/market_clock.py` — SIP-hybrid data/stream routing + session clock.
- `api/main.py` — `/ws/market` quote broadcast (~2078-2114) and `GET /api/watchlist/market` (~1990-2120).
- `api/performance_routes.py:40-62` — new `GET /api/performance/day-pnl`.
- `db/account_scope.py` — workspace-selector → ledger-account resolver used by performance endpoints.

## Serv-side gaps found this recon (not yours to fix — flagging for the orchestrator)
1. **`create_rule` executes immediately on mobile chat with no approval step**, while web requires an
   Approve/Reject card for the same tool. `ai/chat/mobile_agent.py`'s per-tool loop only special-cases
   `propose_order`; it should either intercept `create_rule` the same way (route to a pending/confirm state)
   or the mobile planner prompt should stop offering `create_rule` until an equivalent mobile-side gate
   exists. Recommend a serv-side fix before this surprises the founder with an unapproved live alert.
2. **Mobile's "final answer" streaming is simulated, not real** (blocking `complete()` + fixed-size chunking)
   — this was true before this session too, just newly contrasted by web's real token-live streaming. Not
   urgent, but worth a founder-visibility note since the UX gap between web and mobile chat responsiveness
   will now be more noticeable.
3. **Unresolved from a prior session, still open:** whether serv's raw `ext_price` staleness bug (that
   caused mobile to build its own client-side derivation workaround) is actually fixed by this session's SIP
   hybrid + stream-freeze work, or whether the workaround is still load-bearing. Nobody has re-tested this
   specific combination live. Recommend a joint serv+mobile live check next pre-market/post-market session.
