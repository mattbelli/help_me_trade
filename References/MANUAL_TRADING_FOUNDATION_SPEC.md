# Manual Trading Foundation — ticket widget + chart trading + DOM ladder (2026-08-25)

*Founder-directed 2026-08-25: "create the foundation for trading within the app via a widget and via
charts and via dom … ability to set order amts, shift click to auto execute, etc." This overrides the
charter's "deprioritize manual-trader polish" line for this build — the founder asked for it by name.
The safety doctrine is NOT overridden: every order still flows propose→risk→chokepoint, paper-only,
LIVE stays hard-blocked. The existing DOM on `origin/research/decision-engine-data-layer`
(`orderflow-dom-widget.tsx`) is a historical /ES MBO replay viewer whose data engine is PC-only; this
build creates the LIVE equity ladder natively, reusing its rendering conventions.*

## 0. Non-negotiables (inherited, not new)

- **One path to a broker:** `ExecutionService.submit()` behind `RiskEngine.validate()`. No new
  submit surface — manual orders reuse the proposal machinery (`build_order_proposal_from_args` →
  `create_pending_proposal` → `approve_proposal`), compressed into one admin-gated call.
- **Paper only.** Schwab accounts are refused at 4 existing layers; `is_paper_endpoint` gate stands.
- **Admin token authority.** Anything that moves money (submit, cancel, replace) requires the admin
  token (`sessionStorage`, per-tab), same as desk approvals. The shared `API_TOKEN` (compiled into
  the web bundle) never authorizes an order verb.
- **Decimal-only in `trading/`**; the float→Decimal seam stays in `order_proposal.py`.
- **Protective stops are the stop guard's property.** The manual surface never cancels a protective
  stop directly; stop moves route through `move_protective_stops` (existing verb). Cancel of a
  protective stop from the UI is refused server-side (guard churn → daily-cap starvation, measured).
- **Two split-mandate Opus adversarial reviews** on every `trading/` + order-verb diff before commit.

## 1. What exists today (recon summary, verified 2026-08-25)

- `POST /api/proposals` + `POST /api/proposals/{id}/approve` (admin token, payload_hash-bound) work
  end-to-end and have **zero frontend callers**. `OrderIntent` supports market/limit (+ sell-stop via
  guard only); no stop_limit, no bracket at intent level. TIF day/opg/gtc.
- **No cancel/modify HTTP surface exists** for arbitrary working orders. Broker adapter has
  `acancel_order` only (no PATCH replace). `cancel_book_entries` + `move_protective_stops` are the
  only verbs, both book-scoped, both behind `_verb_gate` (paper + kill-switch checks).
- Snapshot (positions + open orders) is ≤ ~40s stale (20s poll + 20s client refresh); backend order
  rows DO carry `stop_price`/`time_in_force` but the frontend `Order` type drops them.
- The Rust equity collector already ingests **all SIP quotes** (`quotes:["*"]`) and discards them:
  proxy `subs_quotes: 0`. The hub never subscribes `quotes` and has no `q` handler. `QuoteState` has
  bid/ask (Schwab path only fills them) and **no sizes**.
- `/ws/market` is the one push channel; per-widget sockets are the established pattern; 100ms
  broadcast throttle (~10 Hz). Trades stream per-symbol end-to-end already (`t` frames drive price).
- Chart engine: `OverlayLine` with `draggable` + `onLineDragEnd` + context-menu hit-testing already
  powers price alerts — order/position lines are the same primitive with an `order:` id prefix.
- Caps that bite a clicker: `max_daily_orders=20` (global+per-account, UTC day), `max_open_orders=10`
  (vs ≤40s-stale snapshot), RTH-only quote gate (off-hours manual orders fail `live_quote` unless
  opg), `max_order_value_usd=50k`, sells reduce-only (short-to-open behind the settings toggle).

## 2. Deliverables

### D1 — Backend: manual order verbs (`api/manual_trading_routes.py` + `trading/manual_orders.py`)

All endpoints: mutation auth = **admin token required** (401/403 otherwise; the ratchet's device
path is for phone approvals and does not apply here). All refuse non-execution-capable accounts.

1. `POST /api/manual/orders` — compound propose+approve:
   body `{symbol, side, qty, order_type(market|limit), limit_price?, account_id?, time_in_force?(day|gtc),
   extended_hours?}`. Server: `build_order_proposal_from_args` → `create_pending_proposal`
   (source tag `manual`, short TTL) → `approve_proposal` with `decided_by="desk:manual"`.
   Returns `{execution_id, status, proposal_id}` or a structured refusal
   `{error, blocked_reason}` (RiskEngine reason passes through verbatim — the UI shows WHY).
   - GTC allowed for manual limits (order_proposal currently forces TIF at approval: manual path
     passes an explicit TIF through `intent_fields`, validated by risk `order_shape`).
   - `extended_hours` only with limit+day, whole-share (risk gate already enforces).
2. `POST /api/manual/orders/{execution_id}/cancel` — cancel ANY working non-protective order in a
   paper account: new `trading/manual_orders.cancel_working_order` behind `_verb_gate`; looks up the
   execution row (ours) → `acancel_order`; **refuses stop rows whose proposal_id starts `stop|`**
   (protective) with a message pointing at move-stop; journals the action.
3. `POST /api/manual/orders/{execution_id}/replace` — body `{limit_price}` (price move only, qty
   unchanged v1): broker cancel → poll to a CONFIRMED zero-fill terminal (done_for_day is NOT
   terminal — a GTC reworks next session) → terminalize our row → `_refresh_snapshot` with the
   dead broker id dropped → fresh chokepoint submit carrying the ORIGINAL TIF/ext (recovered from
   the minting proposal). Not a broker PATCH — every leg re-validates. Loses queue priority;
   acceptable in paper. `replaced_execution_id` returned (journal link = the two proposal rows).
   ⚠ Each replace burns TWO daily-order slots (cancelled rows still count) — visible in /limits.
4. `POST /api/manual/positions/close` (body `{symbol, account_id?}`) — flatten: cancel OUR
   working non-stop, non-book orders on the symbol (broker-confirmed, snapshot re-polled with the
   dead ids dropped), then market sell the held long. Hand-placed orders, the whole stop/trailing
   family, and book orders are NEVER touched — reported back as `untouched_orders`. Equities
   only v1 (options close from the chain later). Long-only v1 (no buy-to-cover).
5. `GET /api/manual/limits` — the clicker's dashboard: remaining daily orders (global + account),
   open-order slots, kill-switch/entries-halt state, market session. One call, ticket + DOM poll it.

Not in v1 (documented deferrals): stop/stop_limit ENTRY orders (risk `order_shape` widening — own
reviewed round), brackets/OCO (#2), qty replace, options ticket (chain widget integration later).

### D2 — Backend: NBBO quotes to the frontend (`brokers/market_stream.py`, ~40 lines)

- `QuoteState` += `bid_size`, `ask_size`, `quote_ts`; `_quote_payload` emits them.
- Hub subscribes `quotes` for a **separate pinned-quotes set** (the DOM's focused symbol only —
  never the whole trade set; SPY quotes alone exceed 1k/s and the proxy client queue caps at 2000).
  New client action `{"action":"quotes","symbols":[...]}` (replace-set, ≤3 symbols/socket).
- `_handle_alpaca_message` += `q` branch → `_on_quote` → throttled broadcast (existing 100ms).
- Rust collector unchanged (publishes `CH_QUOTES` the moment `wants()` flips). Verify `bs/as` units
  (round lots vs shares) against a live frame before rendering sizes ×100.

### D3 — Trade ticket widget (`trade-ticket` registry entry, channel-aware)

Compact single-panel ticket (registry widget; the founder asked for "via a widget" explicitly —
that is the merge-proposal decision for the picker rule):
- Symbol (channel-following, editable), account selector (`GET /api/strategies/accounts`), side
  toggle, qty with **preset buttons** (user-editable presets persisted in widget config), order type
  market/limit, limit price (± tick steppers, prefilled from NBBO), TIF day/gtc, est. notional +
  buying power + position line, `manual/limits` strip (orders left today, open slots, session).
- Submit = two-step armed button (house pattern from `pending-proposal-card`) — click BUY → armed
  "confirm BUY 100 TSLA @ 412.50" → click again commits. **Shift-click = one-step instant submit**
  (the auto-execute gesture, D6). Rejection renders the RiskEngine `blocked_reason` verbatim inline.
- No admin token in sessionStorage → the form renders but submit shows "unlock in Settings →
  Execution / Risk" (same 403 copy as the desk).

### D4 — Chart trading (extend `native-chart.tsx` + engine, alert-line pattern)

- **Working-order lines** (draggable, `order:` prefix, 0.55-alpha green/red per side, label
  `BUY 100 @ 412.50 · lmt`) + **position line** (avg price, non-draggable, dim; label shows qty +
  uPnL) + **protective-stop lines** (draggable via move_protective_stops, amber).
- Drag an order line → `replace` endpoint; drag stop → move stop; failure snaps back (alert nonce
  pattern). Poll orders on the chart at 5s while any working order exists for the symbol, else 20s.
- Right-click on pane → context menu gains "buy 100 @ 412.25 lmt" / "sell 100 @ 412.25 lmt"
  (qty = active ticket/DOM preset) + "cancel order" / "close position" on line hits. Menu submit
  obeys the same armed/instant rules (D6). Chart-trading arm toggle lives in the chart header menu
  (no floating buttons on canvas — house rule).

### D5 — DOM ladder widget (`dom` registry entry, channel-aware)

Fresh build on main; rendering conventions from the research-branch widget (grid rows
bid | price | ask, size bars in row background, best-bid/ask emphasis, tokens only):
- **Price ladder** centered on last, tick = $0.01 (respect sub-$1 4-decimal pricing), rows fill the
  widget height; auto-recenter when last drifts >25% from center unless pointer is inside the
  ladder (platform convention); manual recenter button in footer.
- Columns: `[my buys] [bid size] [price] [ask size] [my sells]` + session **volume-by-price**
  accumulated client-side from trade frames (thin histogram behind price column). NBBO sizes from
  D2; last-trade row flashes on prints.
- **Click semantics (per platform consensus — final wording pending the conventions report):**
  click buy column below market → buy limit at price; ask/sell column above market → sell limit;
  clicks on the wrong side of market are refused v1 (no stop entries yet, cells render disabled).
  Click own working order marker → cancel. Drag own marker to a new row → replace.
- Header: qty presets (shared config with ticket), account, armed-mode toggle. Footer: position
  qty/avg/uPnL + flatten button + `manual/limits` strip.
- Data: own `/ws/market` socket, `subscribe` for trades + `quotes` action for the focused symbol.

### D6 — The arming model (safety UX, shared by all three surfaces)

- Default: every click/drag order action is **two-step** (armed confirm).
- **Shift-click = instant** for that single action (founder-requested gesture).
- Widget-level **one-click mode** toggle (per platform convention, e.g. SuperDOM "one-click"):
  armed until page reload, shows a persistent visual state (accent border on the widget frame),
  never persisted across sessions.
- Esc cancels an armed state. All refusals show the server's reason verbatim — never a silent fail.

## 3. Build order + verification

1. D2 quotes plumbing (+ pytest on hub `q` handling) — enables everything visual.
2. D1 verbs (+ pytest: auth, protective-stop refusal, replace revalidation, Schwab refusal,
   caps passthrough) → **two-Opus split-mandate adversarial review before anything consumes it**.
3. D3 ticket → gallery fixtures + ui-shot 4 states + ui-review agent.
4. D4 chart lines/menu (+ engine vitest for new geometry if any) → screenshots.
5. D5 DOM (+ fixtures/screenshots) → ui-review.
6. Full: `ruff check .`, targeted pytest modules, `npm run typecheck && npm run check:tokens`,
   `npm run test:engine`. Board card + BUILD_LOG row + HANDOFF in the landing commit.

## 4. Open founder decisions (defaults chosen, flag on review)

- `max_daily_orders=20` will starve active DOM use — left as-is (visible in the limits strip);
  bump is a settings change he can make himself.
- Manual orders target the default account ("Matt + LLM") unless picked; fills in sub-accounts
  won't reach performance surfaces until #249 lands (known, noted in UI as nothing new).
- Off-hours: manual orders fail the RTH `live_quote` gate (by design). The limits strip shows
  `closed`; the refusal reason renders verbatim.
- GTC + extended_hours manual limits are standing overnight orders with NO stop guard (unlike
  book_ext, which arms one). Manual = the founder's own hand; accepted, named here on purpose.
- Alpaca quote sizes are SHARES (round lots ended 2025-11-03) — never ×100.
- Idempotency: every money verb takes `client_request_id`; a retry replays the first outcome
  (60s in-process window — single-writer app).
