# serv → mac handoff (2026-07-03)

*What changed on serv since your last commit (`750f36c` — "mobile go live on /api/chat/stream +
flag serv proposal-event gap"). Scoped to what affects the RN/mobile client — the API contract,
auth, and push. The other ~120 serv commits are web-cockpit + backend and don't touch your surface;
they're listed at the bottom for context only.*

---

## 1. ✅ Your flagged gap is CLOSED — the `proposal` SSE event now fires
`References/SERV_chat_stream_proposal_event_GAP.md` is resolved. `POST /api/chat/stream` emits the
structured event on trade-proposal turns, exactly the contract you specified
(`ai/chat/mobile_agent.py:80`):
```jsonc
{ "type": "proposal", "proposalId": "paper_…", "summary": "BUY 1 AAPL @ mkt",
  "symbol": "AAPL", "side": "buy", "qty": 1 }
```
Emitted once, right after `create_phone_proposal`, before `done`. If proposal creation fails it
degrades to a `tool-result {ok:false}` and the stream still reaches `done` (never hangs).
**Your in-chat hand-off card should just start rendering — no mobile change needed.**

⚠️ **Repro caveat:** the proposal path is RTH-gated (RiskEngine needs a live quote; off-hours it
returns "no live quote" and no proposal is created → no event). Re-run your original curl **during
market hours** to see the event live. Off-hours failure is expected, not a regression.

---

## 2. ⚠️ AUTH CHANGED (#34) — read this before you next touch a mutation
Shipped `bd3d0fe`. The single-static-token model is gone. New posture:

- **Reads + approvals + non-trading writes:** unchanged — your normal `X-API-Token` (the
  `API_TOKEN` secret) still works for GET, `POST /api/proposals/{id}/approve|reject`, watchlist
  PUTs, chat, etc. **Nothing you do today breaks.** (Verified: mobile has no kill-switch or
  risk-limit UI, so nothing currently hits the new gate.)
- **Mutations are now fail-closed:** if the server ever runs with `API_TOKEN` unset, POST/PUT/PATCH/
  DELETE return 401 (reads still open). Serv has `API_TOKEN` set, so this is a non-issue in prod —
  just don't assume an unauthenticated mutation will ever succeed.
- **NEW second tier — `API_ADMIN_TOKEN`:** risk-control mutations now require a *separate* admin
  token, NOT the normal one. This gates:
  - `PUT /api/settings` containing any `trading.*` key → **403** without admin.
  - **Kill switch** is now its own endpoint: `POST /api/kill-switch` `{"active": bool}`.
    **Asymmetric:** ARM (`true`) works with the **normal** token (anyone can hit the e-stop);
    DISARM (`false`) requires the **admin** token. Setting `kill_switch_active` via `PUT
    /api/settings` is hard-rejected (400 "use /api/kill-switch").
  - Admin ⊃ normal (the admin token also satisfies normal auth).

  **Implication for mobile:** if you ever add a kill-switch toggle or a risk-limits screen, DISARM
  and risk-limit saves must send the admin token (in `X-API-Token` — the backend distinguishes by
  value, not header name). ARM can use the normal token. The founder holds the admin token; it must
  come from the user (biometric-gated secure-store entry), never be hardcoded/bundled. Serv's web
  UI collects it in sessionStorage only — mirror that discipline (SecureStore, never AsyncStorage).

---

## 3. 🔔 NEW critical push kinds — decide how mobile surfaces them
The safety round (#33, `64f5db5`) added `push=True severity="critical"` notifications your handler
doesn't recognize yet. Your `use-push.ts` switches on `data.type` and currently knows
`trade_proposal` + `price_alert`. New ones now fire:

| kind | when | why the user needs it |
|------|------|------------------------|
| `risk_breaker` | daily-loss breaker soft/hard trip | **hard trip auto-arms the kill switch** — trading just halted itself. High-urgency. |
| `risk_block` | paper-mode resolved a live endpoint (config drift), or rebalance run-notional cap hit | a guardrail fired; the user should know an order was refused |
| `research_ready` | a deep-research report finished (`action_url=/research?id=<id>`) | deep-link opportunity (web-only surface today — see §4) |

None are wired on mobile. Minimum: make sure an **unrecognized critical push still shows a system
notification** (don't silently drop). Nice-to-have: a tap route for `risk_breaker` → your Approvals/
kill-switch view once one exists. Confirm the exact `data` payload shape per kind on your side
(`signals/notify.py` is the emitter) before parsing — I did NOT design these around a mobile
contract, so treat the `data` keys as provisional and coordinate if you want specific fields.

**Also:** the kill switch can now change state **without user action** (breaker auto-arm). If mobile
ever displays kill-switch state, don't assume it only flips from a user tap — poll/subscribe.

---

## 4. What's web-only right now (context, likely-future mobile work)
Huge serv build this session, but all of it landed on the **web workspace**, no mobile contract:
- **Research Engine v2** — a real multi-analyst research product (planner→specialists→critic→editor,
  your Schwab/Finnhub data fed in, scannable cited papers). Lives in web workspace widgets +
  `/research`. **No mobile surface exists.** Endpoints if you ever want one: `POST /api/research/run`
  `{topic, symbol, depth}` (depth = quick|standard|deep|exhaustive), `GET /api/research/jobs/{id}`
  (now returns a live `events[]` progress feed + `phase`), `GET /api/research/{artifact_id}` (returns
  `markdown` — render-ready). `research_ready` push already fires on completion (§3).
- Realized-P&L correctness chain, custom-theme fixes, sector-radar/calendar/perf widgets — all web.

---

## 5. 🎯 The queued item that IS yours: #35 — mobile phone-approval delivery shell
The Fable mobile-approval audit (`research/fable/2026-07-02_mobile-approval.md`) findings R1–R3 /
H1–H3 are tracked as the next mobile block and will need coordination:
- **R1** push retry + delivery audit trail (a dropped proposal push currently just vanishes)
- **R2** Live Activity **server half** — the pairing bug you know about; serv needs to START/UPDATE/
  END the activity remotely (see your own `use-live-activity.ts` notes)
- **R3** missed-proposal recovery — an unseen proposal should never silently expire
- **H1–H3** client reconciler + fill-report loop gaps

This is the "step away and trust it" delivery thesis and it's half-serv/half-mac. When you're ready
to pick it up, ping and we'll split the contract (serv: remote Live-Activity control + retry/audit +
missed-proposal endpoint; mac: client reconciler + activity lifecycle).

---

## 6. Current safety posture (so you know what the backend will/won't do)
- Execution is **paper/dry_run only**; LIVE is hard-blocked. Paper now *proves* its endpoint is a
  paper endpoint before placing (rejects + critical-push on drift).
- A **daily-loss breaker** halts new entries (soft) or arms the kill switch (hard) — so mobile may
  see proposals get **blocked** more often; that's the guardrail, surface the rejection reason from
  the approve response, don't treat it as an error.
- Kill-switch ARM blocks ALL new orders including exits (known tradeoff, tracked).
- `API_ADMIN_TOKEN` is set on serv (founder holds the value).

---

## Appendix — serv commit range (context only, no mobile action)
`git log 750f36c..HEAD` = 130 commits. Themes: Fable-audit fix rounds (ops/reliability, data-
correctness, perception, LLM-integration), the realized-P&L correctness chain, a UI-bug batch, the
whole **Research Engine v2** (Phases 1–4 + quality + data + progress feed), and the **safety block**
(#33 execution hardening, #34 auth). Full dated detail: `References/BUILD.md` §11 (top rows dated
2026-07-02/03). Current state + next steps: `References/HANDOFF.md`.
