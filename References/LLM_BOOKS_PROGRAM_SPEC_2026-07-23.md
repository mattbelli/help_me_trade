# LLM-Managed Paper Books — Program Build Spec (2026-07-23)

*The build spec for the four-book forward-test program the founder green-lit 07-23. Decisions here
were settled in-session 07-23 (accounts wired + verified paper, panel design agreed, shorts
authorized); this doc turns them into buildable increments. Companion surfaces: the Strategy Lab
book pages (shipped 07-23, `frontend/components/workspace/lab/`) already carry honest-empty seams
waiting on the origins defined in §4.*

> **Amendments (2026-07-23, increment 1 build — these override the body where they conflict):**
> 1. §4's "direct-index map, unknown origin KeyErrors" is wrong: `IDEA_COOLDOWN_MIN` is a
>    defaultdict with a 30-minute fallback — an unlisted origin silently gets 30 min, it never
>    KeyErrors. Entries are still REQUIRED so book lanes get the 1440-min nightly backstop;
>    `tests/test_llm_books.py::test_origin_law_consistency` fails the build if one is missing.
> 2. §4 adds two journal-class lanes: `book:deepseek/considered` + `book:fable/considered` —
>    a solo book's considered-but-not-advanced candidates (calibration-counts-everything; the
>    panel already had seat lanes for this, solo books had nowhere). An advanced panel idea
>    registers in BOTH its seat lane and `book:panel`.
> 3. Lane registry + registration helper + pack builder live in `research/books.py` (the
>    reason-lane precedent); the CLI wrapper is `tools/book_context_pack.py`.
> 4. **ARMING NIGHT (2026-07-23 late, founder: "arm the books… all of these are going live").**
>    The execution round §5.5 pointed at shipped as `entry_style="moo"` through the normal
>    chokepoint: book proposals may be created AFTER HOURS (sized off the last close,
>    whole-share), expire at the NEXT session close (not 30 min), and approval computes TIF
>    at tap time — `opg` before the ~9:28 ET cutoff, market-at-approval after
>    (`trading/market_hours.moo_time_in_force`). NEW same round, closing the founder's
>    "blind between cycles" gap: every advanced pick now ALSO requires a **numeric stop**,
>    and once the entry fills, `trading/stop_guard.py` places that stop as a **resting GTC
>    stop sell** at the broker through the chokepoint (idempotent via durable
>    `stop|<proposal_id>` execution rows; retry w/ backoff; pages the founder if a filled
>    entry can't be protected). RiskEngine grew order-shape gates (tif whitelist; stops =
>    reduce-only whole-share equity sells; opg = whole-share) and a NARROW off-hours
>    reference-close price used ONLY for opg intents and protective stop sells. Panel
>    cadence (§9) resolved by the arming order: **nightly**.
> 5. **ENTRY-TYPE CHOICE (2026-07-24, founder).** Every advanced pick states `entry_type`:
>    `moo` (opg at approval — default) | `limit` (**GTC** limit at the analyst's `limit_price`,
>    RTH) | `ext` (**GTC** limit + `extended_hours` — pre/post + Alpaca 24/5 overnight for
>    eligible names; act near the decision price, stop first actionable next regular open).
>    Order selection is judged like sizing. GTC standing orders are reviewed nightly via the
>    pack's open-orders section; the analyst cancel/replace verb is the NEXT increment (interim:
>    requests in `holds_review`, founder cancels manually). `book_entry` (né `moo_entry`) now
>    gates the off-hours reference pricing + cap exemption for all three styles.
> 6. **KEEPER 2 (2026-08-14).** Closed `mechanism` enum on every candidate (advanced AND
>    considered — that's the n). Stored on `idea_outcomes.mechanism` / `failure_modes_json`.
>    Pack section `Your mechanism ledger` is a **derived read**: n/won/lost/mean α + Wilson,
>    status `insufficient|working|failing|mixed`, honesty bar n≥10. The LLM never writes a
>    0.7. `research/idea_tracker.py` is unchanged (sealed price grader). Failure modes are
>    labels for the pre-mortem / 5b grade, not scored by the sweeper. **Do not fuse** into
>    the Decision Engine (valve §2 + sample size). `expected_direction` is the existing
>    `direction` field — do not add a second one.
> 7. **ORDER-MANAGEMENT VERBS (2026-08-17, founder: "they should be able to prompt me with any
>    changes to orders for their book").** The decision block gains `order_changes`:
>    `move_stop` (+ numeric `stop_price`) · `cancel_entry` · `trim` (+ whole-share `qty`). Each
>    is a normal phone proposal (`trading/book_orders.py`, same row/tap/push/TTL=next close);
>    the tap runs `trading/execution_service.move_protective_stops` / `cancel_book_entries`
>    against what rests at the broker THEN, restricted to the entries/executions the card was
>    minted for (`targets` in the hashed payload — an entry approved this morning is never
>    pulled by last night's cancel). A move = release (write-ahead, broker-confirmed cancel) →
>    re-poll → new stop through `submit()` under the same `stop|<entry>` id, bounded by
>    uncommitted shares, refused when the trigger is at/above the market or the resting stop
>    covers more than is held; new-stop failure restores the old, restore failure marks the
>    row `error` (guard re-arms from the stored price) + pages. An unfilled entry has its
>    stored stop restamped (status `restamped`), same price gate. `trim` = a moo SELL through
>    the ordinary approve path (order_proposal now allows moo sells; GTC styles stay buy-only);
>    the chokepoint's release path takes the stop down and re-stops the remainder. §5.5's
>    "1 trim" push line is now real. The `holds_review` interim ("founder cancels manually")
>    is retired from both prompts (pinned by test). Two Opus adversarial reviewers, split
>    mandates, BLOCK → fixed → re-verified.

## 1. What this is (and is not)

A **forward-test study**: can an LLM given a nightly, deterministic information pack run a paper
book worth following? No backtest exists or is possible — the study IS the evidence. Every book is
paper, every entry is founder-approved (propose mode), every idea is ledger-graded whether taken or
not. This is **not** the app's Decision Engine: the mechanical PERCEIVE→REASON→DECIDE pipeline and
these free-rein books are separate products of the same data, kept apart by the valve rules in §2.
(The panel-on-mechanical-setups design — LLM seats reasoning over pipeline-proposed setups — is its
own workstream: `MULTI_ANGLE_REASONING_AB_2026-07-19.md`, awaiting founder go. Same debate shape,
different substrate; neither gates the other.)

## 2. Non-negotiables (inherited, not new)

- **One account per book** — capital walls; per-book P&L is real account P&L, never attributed math.
- **One-way valve**: mechanical signals (fusion bus, flow, watchlist state) may flow INTO a book's
  context pack; book output NEVER writes back into the fusion bus, mechanical lanes, or any
  perception store. Book proposals exist only as proposals + ledger rows in their own lanes.
- **Provenance everywhere**: every proposal, push, report, and UI row names its book + seat.
- **Chokepoint unchanged**: `trading/execution_service.submit()` + `RiskEngine.validate()`; books
  ship as strategy records **disabled + entry_mode=propose**; founder arms manually.
- **Shorts**: authorized for these books ONLY, via a per-strategy `allow_short` flag — its own
  adversarially-reviewed chokepoint round (§8). Until that round ships, books are long-only too.
- **ToS boundary**: Claude Code sessions are the founder's personal tool driving HIS books —
  proposals he approves. Claude Code is never wired as an app LLM provider; if this ever serves
  other users, their books run on API keys through the app's provider-agnostic seam.
- **Push discipline**: ONE batched nightly push per book cycle (auto-entry-alert-batching);
  `alerts.max_push_per_day` stays founder-controlled.

## 3. The four books

| Book | Account (verified live) | Driver | Mode |
|------|------------------------|--------|------|
| Deep Seek | `acct_alpaca_deepseek` | app LLM seam (DeepSeek key already configured) — nightly job in-process | propose |
| Fable | `acct_alpaca_fable` | scheduled Claude Code session, Fable solo | propose |
| Claude Panel | `acct_alpaca_open` | scheduled Claude Code session: Opus seats + Fable judge | propose |
| Matt + LLM | `acct_fc78907f4bda` | nightly ADVISORY analyst cycle (DeepSeek seam) — report to inbox, **never proposals** | manual |

Panel design (settled): bull + bear **cross-examine the same candidates** (not parallel monologues),
plus a portfolio/risk seat; **Fable judges** against a pre-registered rubric — evidence quality,
falsifiability (every thesis names the level/date that kills it → becomes the stop), R/R, book fit —
and writes a bear pre-mortem for anything it advances. Seats are Opus, judge is Fable
(agent-model-budget-routing). Driver sessions get a narrow prompt and NO sub-agent spawning
(agent-research-fanout-hazard).

## 4. Ledger lanes & origins (the calibration contract)

Seam: `db/idea_outcomes.register_idea(origin=...)`. New origins REQUIRED (each needs an
`IDEA_COOLDOWN_MIN` entry — direct-index map, unknown origin KeyErrors):

- `book:deepseek`, `book:fable`, `book:panel` — the book's ADVANCED ideas (what it actually proposed).
- `book:panel/bull`, `book:panel/bear`, `book:panel/macro`, `book:panel/risk` — EVERY seat's
  candidates, advanced or not (calibration-counts-everything). Seat lanes are **journal-class**
  (`JOURNAL_ORIGINS`) so they're graded per-lane but never pollute headline grading.
- Nothing for Matt + LLM — no machine ideas exist there.

The Lab book actor page's "no idea-ledger origin maps to this book yet" empty state wires to
`book:<x>` the moment these exist. Grading is ordinal (calibration = filter, not goal): the study's
output is "which book/seat lanes deserve to keep running," not a win% headline.

## 5. Nightly pipeline (after close, per book)

1. **Context pack — deterministic script, no LLM** (`tools/book_context_pack.py`): account snapshot
   (positions, cash, day/total P&L from the book's own account), open proposals, its ledger lane
   summary, market frame (indices, breadth, VIX), relevant mechanical perception (fusion attention
   tiers, flow decomp report pointer, earnings calendar) — the valve's INBOUND side. Output: one
   markdown pack per book (markdown, not JSON — deepseek-llm-lessons). Packs are archived to disk
   per date (`data/book_packs/`, gitignored) so every decision is auditable against exactly what the
   model saw.
2. **Session (founder 07-23: agentic, after hours)**: the driver is a **senior analyst at the
   founder's firm** (charter: *create wealth without losing it — swing for home runs when they
   present themselves, never sized to go belly up*). It runs a bounded **read-only tool loop**
   (`research/book_tools.py` allowlist over the chat tool registry: get_context, query,
   list_capabilities, get_earnings_dates, web_search — our data + outside data; mutating tools and
   start_research structurally excluded) and files a markdown report ending in a structured
   decision block: candidates + hold/trim review + a **desk journal** (≤4000 chars, rewritten each
   cycle, fed back verbatim in the next pack — the book's ONLY narrative memory; lanes/reports are
   the mechanical memory; deliberately NOT the app chat-memory store).
3. **Registration & delivery**: every candidate → its lane (§4); advanced proposals → the existing
   proposal intake (`trading/proposal_intake.py`) with book provenance; nightly report stored AND
   delivered to the founder's **app/web inbox** (founder 07-23 — NOT email: a `report_ready`
   notification row via `signals.notify`, push=False, linking `/reports/<id>`).
4. **One batched push** per book: "Fable book: 2 proposals, 1 trim — review by open."
5. **Morning**: founder approves on phone (existing biometric approvals flow) → chokepoint executes.
   **DECIDED (founder 07-23): MOO by default** — approval before the ~9:28 ET auction cutoff submits
   `time_in_force=opg` (fills at the opening auction: matches daily-bar grading by construction,
   removes approval-timing noise from cross-book comparison, deepest liquidity print); **approval
   after the cutoff falls back to market-at-approval** (a queued MOO would execute a stale thesis at
   TOMORROW's open — worse than a slightly-off-auction fill). VWAP/TWAP stay DEAD for paper
   (Elite-gated + not simulated — alpaca-algo-orders-elite-gated).

Matt + LLM variant (upgraded, founder 07-23): the SAME nightly analyst session and inbox report —
"repeat this step with each book, including my co-managed one" — but **advisory-only,
structurally**: no decision block demanded, no proposals, no candidate lists, no lane rows. The
driver enforces this in code, not just prompt.

## 6. Scheduling & ops

- Deep Seek book: in-process scheduled job (existing job loop + `/health` jobs registry — it shows
  up in the Lab "agents ran today" module for free). Fires 17:0x ET after the flow_decomp report.
- Fable + Panel: scheduled Claude Code sessions on serv (crontab, project cd, UTC times — serv
  crons are UTC). Each session's LAST act: write its report + registration payload via one
  authenticated POST; a dead-man check flags a book that produced no report by 20:00 ET.
- All three respect the strategy record: `enabled=false` → the job/session runs a DRY cycle
  (pack + report, no proposals) so the founder can review output quality before arming — the
  proving period is free.

## 7. Build increments (each = own round, verify step named)

1. **Plumbing**: strategy records (3 books, disabled/propose) + origins & cooldowns + lane
   registration helper + `book_context_pack.py`. Verify: pack renders for all 4 accounts against
   live paper state; `register_idea` accepts each new origin; targeted pytest module.
2. **Deep Seek book end-to-end** (cheapest driver proves the whole pipe): nightly job → pack →
   DeepSeek → report + registrations + batched push (push held until founder arms). Verify: dry
   cycle produces a real report from live data; lane rows appear; Lab book page shows them.
3. **Matt + LLM snapshot job.** Verify: nightly render matches account state; zero proposal rows.
4. **Fable + Panel sessions**: session prompts (pre-registered judge rubric text lives in this repo,
   not improvised per-night), cron wiring, dead-man. Verify: one supervised live run each; per-seat
   lanes populated; cross-examination visible in the report.
5. **Shorts unlock round** (§8) — only after 1–4 are proven in long-only propose mode.
6. **Lab UI wiring round**: book pages consume `book:<x>` lanes + nightly report display (the
   panel per-seat grading view the founder called "very strong" in the A2 mock).

## 8. Shorts unlock round (separate, adversarially reviewed)

Per-strategy `allow_short` flag (default false, only settable on book strategies for now);
chokepoint distinguishes **sell-to-close vs sell-to-open** (order_executions already carries
`short_open`); RiskEngine grows short-aware caps (gross exposure = longs + |shorts|, per-name short
cap, no shorting into halts/earnings without explicit flag); Alpaca paper handles borrow natively.
Two parallel Opus reviewers with split mandates before commit (reviews-on-opus). The June
"no shorts ever" record is formally revised by this round's BUILD_LOG row (options-strategies
memory already flagged the revision).

## 9. Founder-lane decisions (open)

- ~~Execution style at approval~~ **DECIDED 07-23: MOO default, market fallback after the auction
  cutoff (§5.5). SHIPPED 07-23 late (amendment 4).**
- ~~Panel cadence~~ **RESOLVED 07-23 by the arming order ("all of these are going live"): nightly.**
- ~~Per-book notional caps at arming time~~ **DECIDED 07-23 (founder): NO imposed per-trade
  sizing — "this is the whole test… to see how they can effectively manage a portfolio." Every
  advanced pick must state its own `notional_usd` (unsized = dropped); the walls are the book's
  cash, the RiskEngine's account-level checks, and founder approval in propose mode. Sizing
  discipline is JUDGED (lanes, risk seat, pre-mortems), never fenced. The strategy-record
  `max_deployed_usd`/`max_concurrent_positions` fields remain available as OPTIONAL founder
  rails, unset by default.**
- When (if ever) a book graduates from propose → bounded-auto: NOT in this spec's scope; would ride
  the Rung-3 design (rung3-bounded-auto-design-intel), never a side door.
