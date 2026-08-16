# Handoff — open threads (pruned 2026-07-28)

*Bootstrap for a fresh session: read this, then `CLAUDE.md` (auto-loaded), `AGENTS.md` (module map),
`References/INDEX.md` (which doc is current).*

*Contract (tense rule 2026-07-17; anti-bloat rules 2026-07-28): this file holds ONLY what is **open
right now** — pending verifies, armed experiments, decisions in flight. PAST tense lives in
`BUILD_LOG.md` (grep it) and git; when a thread closes, **delete its entry** — no archive. In-flight
build work lives on `board.json`, not here. Anti-bloat (the growth cycle happens when these are
skipped): (1) every ⏳/▶ item is an ACTION with a date — durable facts/doctrine/warnings go to a
memory, a design doc, or the BUILD_LOG row, never here; (2) any session that touches this file
first DELETES what it can prove closed (BUILD_LOG row or a live check) before adding its own; (3)
a dated verify whose date has passed is either closed out or re-dated — never left; (4) soft
budget ~150 lines: over budget = prune debt, prune before appending.*

---

## ▶ 08-16 main.py strangler rounds 1–3 (#320, #321, #76) — one live verify owed

1. ⏳ **After the next serv deploy/restart:** `journalctl -u hmt-backend` boots clean (the import
   graph changed a lot: `api/hub.py` builds `market_hub` at import; every job now imports from an
   `api/*_jobs.py`), `/health` `jobs` shows ALL heartbeats alive (poll, signal_scan, session_node,
   alert_symbol, trade_sync, proposal_sweeper, idea_tracker, earnings_*, regime_*, reports_scheduler,
   bars_prewarm, equity_tape_flush, rvol_alert, schwab_*, fusion_*, filings_*, book_*), the WS
   quotes stream in the cockpit (the hub bind moved), a chart loads (`/api/bars`) and watchlist rows
   fill (`/api/watchlist/market`). OpenAPI paths+methods were proven identical locally and the
   full suite passed; the live process is the last unchecked surface. `#77` is now only its part
   (b) — quotes/streaming into the provider protocol.

## ▶ 08-15 Per-filing segment ingest (#317) — backfill launched; three verifies owed

1. ⏳ **Backfill completion** (launched 08-15 evening, ~1-2h): check `filing_segment_ingest`
   status counts + errors in `data/research/edgar_facts.db`, then the acceptance question that
   started the round — `query segments GOOGL` must show GoogleCloud Q2 2026 **$24.768B** quarterly.
2. ⏳ **First nightly** 08-16 07:10 UTC: `edgar_refresh.log` should show the new
   `research.edgar_filing_segments --sync` step after the 8-K backfill (non-fatal wiring).
3. ⏳ **When SEC publishes DERA 2026q2** (Sunday ingest will pick it up): run
   `--verify-quarter 2026q2` — the accession-keyed dedupe of the two lanes gets its live test.

Founder queue: **#318** — mobile chat brain runs on DeepSeek; keep/upgrade/hybrid is his call.

Inc 1–5 on main. Detector rides `hmt-equity-collector`; pack line + `query abnormality`.
Draft fire-rule (p99/p95, floors 2.0/3.0, top-N 10) until founder eyeball of replay tables.
- ▶ **Replay eyeball** (Inc 2/3): draft p99/p95 floors 2/3 top-N 10 ran **184 fires/day** on 08-13+08-14. `python tools/abnormality_replay.py`. Lock or keep draft.
- Inc 6 (paired notifications, 1m firing, convergence membership, fusion scoring) stays founder-gated

Idea-ledger revival is its own future thread, not part of #296.

## ▶ 08-11 Fusion rebuild — Section 1 Round A shipped, Round B open

Section-by-section rebuild (founder's method) per `References/fusion_layer_audit/README.md`; card #302.
Round A widened the macro substrate and wired it to both agent surfaces (BUILD_LOG 08-11 row).
- ✅ **Cboe close cron — CHECKED 08-12, passed.** Both firings (22:00 + 23:00 UTC) wrote 08-11 rows for
  all 7 series, and the values are the settle: `last_trade_time` is 16:15:01 ET, the index settlement
  print. ⚠️ Two gotchas for whoever reads these next: post-settle Cboe rolls `prev_day_close` forward
  so it EQUALS today's close on every series — the VXTLT/VXEEM rejection test was never really about
  that field, it was about `last_trade_time` (16:02 + a different tz = not a settle); and VIX1D and
  VIX9D both printed exactly 12.52, which is coincidence, not a duplicated feed — their open/high/low
  differ (7.52/12.63/7.07 vs 12.95/13.10/12.32), checked against the vendor directly.
- ▶ **Round B** (not started): consolidate the fusion regime dot onto `macro.db` (it still reads the
  thin `regime_macro`), delete `market_shock`'s dead VIX arm + the never-resetting `MAX()` latch.
- ▶ **founder call**: no UI surface renders the regime axes (now 8 + index gamma). **No new widget**
  (standing rule) — so this needs a merge target on an existing widget, or it stays agent-only.
- ✅ **CPI check — ANSWERED EARLY 08-12, and it found the real gap.** The books were not blind (fable's
  08-11 session does discuss CPI) but it had gone to the OPEN WEB for it: the calendar shipped onto the
  `market` perception resource and **not onto the nightly book pack**, which is what a book reads
  first — the 08-11 packs carry no CPI line at all. Fixed at the same edit site as the gamma line
  (`research/books._market_frame_section`); the pack now renders `CPI in 1d (2026-08-12)` plus the
  `not_covered` caveat, pinned by two tests. Same class as `research-engine-bypassed-perception`: a
  source is not wired until EVERY consumer surface has it, and the pack was the one skipped.
- ▶ **Founder's 1-by-1 queue**: ~~1 apply reviews~~ ✅ · ~~2 macro event calendar~~ ✅ ·
  ~~3 correlation + breadth~~ ✅ · ~~4 deeper daily-bar backfill~~ ✅ (found + fixed a silent
  batch-pagination truncation; a second review then found 6 MAJORs in that fix — all closed) ·
  ~~5 SKEW in, VXTLT/VXEEM rejected~~ ✅ · ~~6 dealer positioning~~ ✅ (gamma 08-12, vanna + charm
  08-12; #303 + two BUILD_LOG rows) · **7 Round B cleanup — the only one left.**
- ▶ **Three carried-forward notes from the dealer-exposure rounds** (notes, not cards): `oi_walls`
  still cannot filter by strike or read a past date despite 27.6M rows of per-strike history — the
  gamma work needed the whole latest board and got `oi_board()`, but a strike/date drill-down is
  still missing; `list_capabilities()` is now **~22.7k chars against a 20,000 tool-result cap** (was
  20,364 before `gex`, 22,168 after it, and the vanna/charm round gave most of its own increase back
  by compressing the `gex` description) — it has no rows to drop so `fit_to_cap` passes it through
  whole rather than corrupting it, but every manifest read spends ~5.5k tokens, and the fix is a
  two-tier manifest (names + one-liners, full descriptions on request), not a shorter description;
  and a **VIX-flat historical backfill of the FLIP level only** is defensible research — the flip is
  far less vol-sensitive than the magnitude — which would give percentiles years earlier than
  waiting 60 sessions. Unrun.
- ▶ **No UI surface renders any of the regime axes** (8 macro + gamma/vanna/charm). Founder's call,
  and it stays agent-only until he makes it: standing instruction is **no new widget**, so this needs
  a merge target on an existing one (`widget-consolidation-direction`).
- ▶ **Design still standing**: the SME collar is a known POSITION so it belongs with reference levels,
  not as a statistic; single-name GEX stays in Section 3.
- ▶ Sections 2–6 unstarted; Section 2 (equity price+volume) owns cronning `equity_bars_5min`
  (frozen 07-27, NOT decaying — Alpaca serves it retroactively, verified 08-11).

## ▶ 08-13 #308 chart-hang round — follow-ups (fix shipped, see BUILD_LOG 08-13)

The Alpaca OPRA sidecar degradation (08-07→08-12, tracked here since 08-11) RECOVERED 08-13:
pairing matched 99.1%, wire lag a-lane p50 11-15ms all session — entry closed per its own
checkpoint. The #308 chart-hang verdict landed on OUR side (prewarm self-saturation since
PR #102); serve-stale + hot-spec scope cut shipped + deployed 08-13 ~19:24 UTC.
- ⏳ **founder in-browser feel-check**: timeframe switching should paint instantly (stale ok)
  and never sit 10-40s; first view of a never-cached combo still cold-loads once.
- ⏳ **WS keepalive blame probe**: the chronic stocks-stream ping timeouts (61-124/weekday
  since ≥07-28, market-hours clustered) are ambiguous — our loop starvation vs Alpaca edge.
  With prewarm calmed, watch the daily count; if it collapses, it was us (case closed, no
  probe needed). If not, run a standalone minimal WS client beside the backend for a day
  BEFORE any Alpaca ticket.
- ⏳ **reboot-persistent bars cache** (uncarded): the 08-13 power-off cold-started every
  cache into a vendor slow patch — worst-case UX. A periodic disk snapshot of _BARS_CACHE
  would make reboots paint instantly. Small, wants its own round.
- ⏳ unexplained (carried): flow-collector restarted ~12× in 3 min Sun 08-09 10:47Z then
  stabilized — if that wasn't founder/deploy activity, worth one journal look.

## ▶ 08-10 board restore — 1 revived thread still needs action, 2 founder calls back in queue

Merge `520e944c` (08-01) silently dropped 16 board cards; all restored 08-10 as #284–#299
(BUILD_LOG row has the forensics). #299 (Addendum-G scoring) and #298 (quiet-tape prereg) closed
08-11 — both registered runs completed, both verdicts DEAD (see the BUILD_LOG row + the cards).
Remaining:

- ⏳ **#297 Schwab re-auth Akamai** — "VERDICT 2026-08-06" passed unobserved; check the manual
  bridge + daily cron actually held (last successful re-auth timestamp) before closing.
- **Founder lane refilled:** #294 (multi-angle reasoning A/B greenlight, waiting since 07-19) and
  #295 (ext-entry sizing) are back in the queue at `/board`.
- **Guard follow-up (unbuilt):** #276 fixed the reformat CAUSE, but no guard stops a merge
  resolution replacing `board.json` wholesale — a card-count-drop check in the commit hook would
  have caught this on 08-01.

## ▶ 08-09 board-format + flow-provenance round — Monday verifies owed

Both shipped + deployed (BUILD_LOG rows; cards #276 and #246). `feat/wire-data-everywhere` is
closed — its useful half was re-applied to main and the branch + worktree deleted, so the board
merge-hazard noted here earlier is gone. Open:

- **Mon 08-10, first session since two same-day changes — check together.** (a) `dbn_nbbo` flow
  events must now show `confirmed aggressor` in the score breakdown and read *"NBBO on the print"* /
  *"side confirmed on the quote at print time"* — never *"side off a polled quote"*. (b) Main's
  backend basis repair (`6681005e`) has never run in a live session: confirm sided events stop
  writing `direction_basis='none'` (was ~8.7–11.2k/day). One query covers both:
  `select substr(ts,1,10), direction_basis, count(*) from options_flow_events where
  direction!='undetermined' and ts>='2026-08-10' group by 1,2`.
- **Founder in-browser check owed:** board cards select/copy with the mouse, show `#num`, still
  toggle on click and Enter/Space; and a flow event's *side basis* cell + why-line read correctly.
  **Extended 08-10 (card #277):** the flow widget's side column now speaks words — buy/sell
  saturated, mid buy/mid sell muted, new dim `right` column, flag column gone; check narrow-width
  layout too (column widths were rebalanced, min-width 632→608).
  **Extended 08-10 pm (cards #278-280, agent-UI prep round — see BUILD_LOG):** 7 table widgets
  migrated to the new `DataTable` primitive (deployed). Deltas to eyeball: tables should look
  byte-identical except the ⓘ ticker button now reveals on whole-row hover in filings/desk/
  evidence-stack/idea-outcomes (was ticker-only), and orders' Side dead-space now truncates
  instead of forcing width. Quick tour: `/dev/gallery` renders every widget on fake data;
  `node tools/ui-shot.mjs <widget-id>` screenshots one (dev server on 3055 first).
  **Extended 08-10 eve (card #281, deployed):** orders/positions grew a real Account column
  (plain dim word, chip gone, rows single-line again), order status reads lowercase words
  (`working`/`partial fill` — partial fill now amber, was wrongly green), ALL sortable table
  headers app-wide went back to UPPERCASE (positions/desk/etc. were silently Title-case), and a
  failed orders snapshot now shows an error + retry instead of "Waiting for orders…".
  **Extended 08-10 late (cards #282/#283 + picker fix, deployed):** watchlist — drag a row out
  and release: it's DELETED, immediately, no confirm (founder overruled #282's pill the same
  evening; red ring while dragging is the only warning, and there is no undo — his explicit
  call). Right-click a row → context menu: `delete SYM`, plus `pin/unpin` on slot-capped lists
  only. Delete/Backspace on the keyboard-selected row also deletes. Comfortable rows are 29px
  (was 33); the add row is a real table row with a blinking block cursor (founder steered it
  live in the coder's pane). Widget picker: Space→type→arrows→Enter launches a widget.

## ▶ Agent-UI prep follow-ups (from the 08-10 round's day-one catches — not yet carded)
- ~~Validation case~~ **SHIPPED 08-10 (card #281)** — the process proof worked: coder followed the
  rule card, screenshot loop caught a wrap regression before review, ui-review found a real
  pre-existing Blocker. New follow-ups it surfaced: (a) orders `Type`/`Mode` columns still render
  raw enums (`labels.ts` `order_type` kind sits ready); (b) **product call:** in single-account
  scopes `accountLabel` is empty so the Account column is all `—` — hide it there, or keep the
  stable layout? (c) reports-widget wraps its `label()` in a bordered uppercase box — the banned
  pill again, candidate next fix; (d) Filled/Rejected order tabs can't carry Account until the
  backend adds an account field to `TradeEvent`/`OrderExecution`.
- **Invisible loading skeleton (REAL bug, every theme):** skeleton cell bg == panel hex
  (`--bg-raised`→`--surface-toolbar` == `--surface-widget`); only the reduced-motion-hidden
  shimmer ever made it visible. One-token fix; founder pick of shade.
- Gallery-caught layout defects awaiting their turn: idea-outcomes rows wrap/double at default
  width; options-chain sticky header scrolls away on ATM auto-scroll; index-pressure footer
  collides with its tabs at default width; performance-stats period selector dead space.
- **80 pre-existing `react-hooks/*` eslint errors** block eslint ever gating; tailwind
  `no-unknown-classes` at warn needs an allowlist of our ~155 hand-written classes before it
  could go to error. index-pressure still carries 16 off-ladder px sizes (2 below the 10px
  floor) + knob-family token dialect — sweep candidate.
- `check:tokens` is green but inert until wired into pre-push/CI.
- **Watchlist #283 leftovers:** a FAILED delete is completely silent — `removeSymbol` sets
  `error` but it only renders when `!displayList`, i.e. never in practice (pre-existing; now
  more exposed with three delete paths and no confirm). Two right-click menu skins exist:
  `page-switcher.tsx` (`--panel`/rounded/Title Case) vs the watchlist's
  (`--bg-inset`/square/lowercase) — one should win codebase-wide.
- **Watchlist round leftovers (08-10, card #282):** gallery has NO fixture for
  `PUT /api/watchlists/{id}` — unmatched writes answer `{}` and kill the widget, so no gallery/
  ui-shot check can exercise ANY watchlist write (add/remove/reorder/density); worth a fixture.
  Add-row-over-header z-order (z-30 vs header z-[2]) — pre-existing, needs a widget z-order
  re-plan, founder saw + accepted current state. Framer `whileDrag` scale (1.02) never reverts
  after a drag-out release — armed row sits ~2% wide/10px displaced until next re-render,
  cosmetic. `lib/api.ts` `searchSymbols` uses banned `cache:"no-store"`. Watchlist
  `WidgetLoading cols={[2,1,1,1]}` vs 6 real columns; earnings badge is still a boxed chip.
- **Before merging any worktree branch**, run `git merge-tree --write-tree main <branch>` first —
  main's board was renumbered and split on 08-09, so a branch carrying its own `board.json` edits
  should take main's board wholesale and re-apply its card edits with `tools/board.py note|move`.

## ▶ GTH overnight flow (Phase B) — shipped 08-09; first-night verifies owed

Everything deployed (BUILD_LOG row + card `gth-overnight-phase-b`); baseline is pre-filled from the
260-night census so z is live from night one. Two dated checks close this entry:

- **⏳ Sun 2026-08-10 ~20:30 ET — first tape fill.** `data/gth_tape/2026-08-11.jsonl` growing +
  `gth_w=` advancing on the pairing stats line (`journalctl -u hmt-flow-collector`, UTC). Zero rows
  by 22:00 ET = the tee is broken; fix before the morning line has nothing to read.
- **⏳ Mon 2026-08-11 ~9:20 ET — first morning line.** `overnight_flow` report in the inbox,
  `data/research/gth_census/morning_line.json` written, and the **index-pressure widget's
  `overnight` footer view** renders it (the standalone widget was consolidated away same day —
  card `widget-consolidation-2026-08`). Phone must stay quiet unless z≥5.
- **⏳ Founder in-browser pass on the consolidation round** (typecheck+build only so far):
  positions summary strip, scans → RVOL preset, news → filings scope, idea-outcomes → Ledger tab,
  mobile portfolio tab (now positions + orders). Same pass: the index-pressure widget's new
  `trend` view (daily open/close tilt vs SPY day %) and the weekend carry-last-session fallbacks
  (card `index-pressure-trend-view`) — all four views should show data RIGHT NOW, on a weekend.

---

## ▶ EDGAR nightly refresh — LIVE since 08-02; one tuning item open

Deployed, seeded and cron-installed 08-02 (see the BUILD_LOG row + card
`edgar-companyfacts-nightly-refresh`). Only this remains:

- **⏳ After ~3 runs — retune the slot from data, not from my one sample.** 07:10 UTC was set off a
  single `Last-Modified` reading (04:38:46 UTC on 08-02). Every run logs the snapshot's own
  `Last-Modified`; if it consistently lands later than ~05:30 UTC, move the slot later. Being early
  is self-healing (the diff finds nothing, the next run catches up), so this is tuning, not a bug.

---

## ▶ 2026-08-06 Schwab re-auth — the verdict on the Akamai hardening

Token is healthy (minted 08-01 13:28Z, cliff **2026-08-08**). serv's IP is still edge-blocked from
`sws-gateway.schwab.com`; six anti-detection fixes shipped 08-01 but **none tested against Schwab**.
Diagnosis + the full fix list live on card `schwab-reauth-akamai-block` and the 08-01 BUILD_LOG row.

- **⏳ 2026-08-06 — cron's first real attempt** (it skips while the token is under 5 days). Check
  `data/reauth.log`. Success = the hardening cleared the block. Failure now reports **"Akamai
  edge-blocked this IP"** with a `Reference #` instead of accusing the password — if you see a
  credential error instead, the guard itself is wrong.
- **⏳ If it fails:** `python -m tools.schwab_manual_exchange --start`, open the URL on an unblocked
  machine, paste the `127.0.0.1:8182?code=...` back **fast** (Schwab codes expire in minutes), then
  `--finish '<url>' --restart-backend`. Must land before the **08-08** cliff.
- ⚠️ **`data/schwab_reauth_profile` is load-bearing** — deleting it makes the next run look like a
  brand-new device, which is the signature we just spent the round removing.

---

## ▶ Book position protection — stops place AND release; deploy is the open action

Both prior alarms here are closed and their entries deleted per the tense rule: `stop-guard-never-fires`
shipped and the guard fired live 07-30, and `resting-stop-locks-the-position` shipped 08-01. Verified
live 08-01: AMZN 50 @ `acct_alpaca_fable` and GOOGL 61 @ `acct_alpaca_deepseek` each carry their GTC
stop (233.00 / 314.90), and both were locked at `available` 0.

- **⏳ Stop TRIGGER prices reach the book pack — needs a deploy + one poll to become visible, and two
  surfaces are still blind.** Landed 08-07 (see the log row): the level was missing from the
  `order_snapshots` table itself, so this is a schema migration — it applies on the next `init_db`,
  meaning the **backend must restart** and one poll must write a fresh snapshot before tonight's pack
  shows anything. Historic rows stay null by design. **Deployed + verified live 08-07**: all four book
  stops now carry their trigger (AMZN 233.00 / GOOGL 341.90 / MU 787.90 / PAYC 207.40), and AMZN +
  GOOGL match the levels recorded here on 08-01 from a different process. Schwab's two parsers have
  the same defect and are now their own card, `schwab-orders-blind-stops` — **not** blocked as first
  written: the field names (`stopPrice`, `duration`) are confirmed in the vendored schwab-py.
  Also open: perception exposes Schwab's accounts (`schwab_accounts` resource) but has no ALPACA
  open-orders resource, so outside the nightly pack the books cannot query their own resting risk.
- **⏳ First real exit is the verify.** Deployed 08-01 16:16 UTC (backend PID 2093943, frontend
  2094116, both collectors); the release path has never run against a live broker. Watch the first
  book exit on AMZN/GOOGL: expect the stop cancelled, the market exit filled, and either a residual
  stop at the same trigger or an inbox note saying nothing was left to stop. Any CRITICAL
  "UNPROTECTED position" push means a failure path fired — read it, it names which.
- **⏳ The non-atomic snapshot needs BOTH halves moved together — a naive reorder is worse.** Attempted
  and REVERTED 08-02 (see the log row); read the reasons before re-attempting. Reading positions after
  the orders read does **not** buy an ordering invariant: measured, it shifts a **~21 ms** probability
  window, and Alpaca's own fill→position lag is unmeasured and may exceed it. Worse,
  `_uncommitted_shares` carries a compensation (`held -= exit_qty` when the exit is absent from
  open_orders) that assumes positions may be the **older** read — make positions the fresher one and
  it double-counts, zeroing the re-stop budget, and `_restop_residual` skips on `allot <= 0` with **no
  page**: a bare position plus an inbox note saying nothing was left uncommitted. A real fix changes
  the read ordering **and** that compensation in one reviewed change, and needs Alpaca's actual
  propagation lag measured first. A test now pins the concurrent read so the halves cannot drift apart.
- **⏳ `'rejected'` is the wrong terminal for an orphan, and it is what the reconciler writes.** The
  terminaliser shipped 08-02 with corroborated absence (two independent routes must agree), so a wrong
  terminalisation is now unlikely — but the *consequence* is still sharp, because `'rejected'` means
  "never reached the broker" to four subsystems at once: `list_open_protective_stops` drops the row (a
  live stop can no longer be RELEASED → position un-exitable while the stop guard pages it bare),
  `count_executions_today` / `sum_today_notional` / `sum_inflight_notional` all drop it (freeing risk
  budget a live order still uses), the auto-entry dedup re-opens on a symbol that already has a live
  order, and `trading/proposals` marks the proposal failed. A distinct `orphaned` terminal that still
  counts toward the caps would be the honest fix; it touches every status consumer, so it is its own
  round.
- **⏳ `get_broker_for_account` silently falls back to DEFAULT credentials** when an account has no
  connection row, so `_end_orphan_row`'s account-scope guard proves the *call* was scoped, not that the
  adapter is that account's. Latent today (all 10 `trade_accounts` rows carry `connection_id` and
  `broker_external_id`, and `_poll_account` returns early on an external-id mismatch); reachable via a
  newly-added account with a NULL `broker_external_id`. Assert adapter identity rather than trusting
  the caller three frames up.
- **⏳ `unprotected-position-monitor`** (card, `next`, high) — now also owns the specific hole that a
  dead stop row removes an entry from the guard's work list for good. ⚠️ The obvious fix was built,
  reviewed and REVERTED 08-01 for three measured reasons (dry_run re-submits forever; a hand cancel
  ping-pongs uncapped and can exhaust `max_daily_orders`; re-protection is sized from the entry, not
  the position) — read the card before re-attempting. The chokepoint pages critically instead.
- **⚠️ Timing is load-bearing, not cosmetic:** Alpaca 422s a stop whose trigger is already breached
  (*"stop price must be less than current price"*), so the guard must place on fill while price is
  still above the stop. After a gap-down it **cannot** place one at all — "re-place it each morning"
  is not a workaround. The release path's residual re-stop carries the same gate.
- **Settled 07-31:** `ext` sizing stays at PARITY (founder cleared the decision without a change) —
  the overnight window is an accepted cost, not an open question. Sizing is no longer a lever here.
- Then `book-accounts-invisible-in-cockpit`, then `ext-hours-soft-stop` (data gate already passes —
  the 24/7 poll already carries a live overnight mark).

---

## ▶ Overdue dated verifies (close out or re-date — oldest first)

- ⏳ (was Mon 07-27) flow-EOD: an `options_flow_daily` row landed for 07-27 at 16:xx ET, no 00:1x
  ET run; backfilled rows carry `close_oi IS NULL`.
- ⏳ (was Mon 07-27) IV corpus first live cron run: `iv_daily` row per universe symbol + sane
  `bars_current`; then 07-28: OI backfill lands on 07-27's rows (OI publishes a session late).
- ⏳ (was 07-28 open) books MOO probe `exec_e22a1bd20a39` (F, 1sh, market DAY, resting since
  Friday): filled at Monday's open ⇒ paper-MOO queued-DAY fix holds; expired ⇒ fix wrong, needs
  submit-at-the-open. The stop_guard half is no longer blocked — the guard ships and has fired.
- ⏳ chart prewarm: `/health` bars_prewarm `lag_s` stays well under the shortest TTL across a few
  sessions (`chart-bars-prewarm-storms`).

## ▶ 2026-07-27 Postgres migration — design BANKED, execution PAUSED on trigger

Trigger (instrumented): db_error-attributed dropped sweeps, `database is locked`, or sustained
multi-second slow-txn queuing → execute `References/POSTGRES_MIGRATION_2026-07-27.md` as written
(§12 Round 0 first), no re-litigating. Card `postgres-migration-decision` (backlog). Open:
- Pull forward regardless: backup watchdog fails OPEN on unreadable backup age
  (`api/jobs.py:189-190` — must fail closed) + `.env` chmod 600.
- When triggered: open the `daily_bars` schema-leak and `rule_events` FK-orphan pre-cutover cards.

## ▶ Whole-market flow corpus — v6 landed; discovery + live builds open

Corpus state: 251/251 sessions at v6 (`multileg_source=codes`), doctrine recorded in ROADMAP 1.8,
Addendum D–F, and memories (multileg fence reaches single names; orders-not-prints for N; one BBO
snapshot per sweep). Open threads only:
- **⏳ Live package-awareness build** (board notes 28s/28u) — priors now measured at v6;
  1.7/1.10 and the 1.11+ discovery program are unblocked on trustworthy labels.
- **⏳ Phase 2 OI backfill running** (launched 07-28, `opra-oi-backfill.service`, ~9.5 h): check
  07-29 that `data/databento/opra_oi/` holds ~251 npz files and the log shows no cost-gate abort;
  then build 2.3 (reconcile prints vs OI deltas — aggregate open-vs-close, never per-print) and
  2.4 (re-grade the Phase 1 signal on the split). 2.1 facts recorded in ROADMAP §2.1. Also 07-29:
  cross-check `2026-07-17.npz` vs `options_flow_daily.close_oi` date 07-17 (both = 07-16 close
  positions; broker chain pipe vs statistics pipe — same OCC number, validates both decodes).
  NOT runnable today: no concurrent ALL_SYMBOLS pulls while the backfill grinds.
- **⏳ 2.3 running across the corpus** (`oi-reconcile.service`, log `data/oi_reconcile.log`, ~8s
  per session): confirm 07-29 it covered every consecutive OI pair and the flag rates stayed near
  the smoke (clean ~98.6%). Numbers + the three bugs are recorded in ROADMAP §2.3 and the
  BUILD_LOG row. THEN 2.4 — which needs its OWN pre-registered pass/fail before it runs, and may
  use `f_open` **cross-sectionally only** (its >0.5 level is an artifact of OI decaying via
  exercise/assignment/expiry, not a finding).
- **⏳ o band → §4 classifier sequencing** (§3c PASSED out-of-sample 3.33×, 07-28): o is a
  feature awaiting siblings (Phase 2 OI deltas, Phase 3 hedge prints); no standalone surface, no
  grading without prereg; later generalization = 3+leg clusters (~37–43% of package premium).
- **⏳ Flow perception surface — SCHEDULED (cron 04:30 UTC Tue–Sat), earnings coverage SHIPPED,
  alerts unarmed, no UI widget** (BUILD_LOG 07-29/07-30 rows; card `flow-perception-ship`).
  Nightly wrapper = corpus roll → earnings coverage refresh (non-fatal) → scan. Earnings estimates
  land as `estimated=1`/`edgar_estimate` — money-path readers (`next_earnings_date`) stay
  confirmed-only BY DESIGN; `earnings_phrase()` renders the estimate caveat on every surface.
  ⏳ Open: **first full ~3.9k-name coverage sweep** on serv (wrapper does it incrementally at
  04:30, or run `tools/earnings_coverage_refresh.py` manually — ~9 min); founder arms
  `alerts.flow_perception_alerts_enabled` (recommend ≥3 components or top-N — ≥2 yields ~451
  candidates/session); UI widget; watchlist fundamentals panel still shows confirmed dates only
  (surfacing estimates there needs a UI label first).
- **⏳ 1.14g verdict LANDED 07-30 (BUILD_LOG row) — follow-ups unblocked:** PRODUCT = NOT
  DEMOTED (all three components clear their negligible bands after RV-stratification); SCIENCE =
  no survival authority (top-RV-quintile balance failed; benchmark (d) leans "mostly RV in
  disguise"). Open: (a) the letter "F" was consumed 07-28 — the judging-rules amendment is now
  **Addendum G IS LAW (v12, 2026-07-31 — BUILD_LOG row has the gauntlet record**; card
  `addendum-g-judging-rules`). **Pre-run obligations 1+2+3/3b ALL DISCHARGED 07-31** (BUILD_LOG
  rows: `FLOW_1_14F_FINDINGS_REGISTRATION.md` IS LAW v5, 17-blocker gauntlet; G.8 root-
  classification snapshot FROZEN + bar backfill; obligation-3 registered run COMPLETE — Q5 null
  USABLE, f −0.87pt BELOW f₀, **DEAD (dilution) is the near-certain scoring-run verdict**).
  Remaining obligations 5→6 are **calendar-blocked by G.10 itself** (they need bars through
  d+10, so they inherit the ≥ close-of-2026-08-07 bound) — nothing on this path is runnable
  before then; (b) RESOLVED 07-30 — founder PARKED the
  flow perception surface (card `flow-perception-ship`, backlog); a vol/volume abnormality
  scanner is carded instead (`vol-abnormality-scanner`, next); (c) `short_dte_share` grain
  contradiction stays parked.
- **⏳ H1 quiet-tape — implementation BUILT + FROZEN 07-31 (H1.6 #2 discharged both halves;
  BUILD_LOG row has the full review record; FREEZE HASH `a2cf65f` — the registered
  implementation; any later change to `tools/h1_quiet_tape.py` = dated addendum).**
  `tools/h1_quiet_tape.py` + 40 tests; oi_reconcile extended through 07-29 (07-24 coverage
  81.4% — judgment window keeps 37 dates). Open, in order: ⏳ ≥ 2026-08-08: `--stage record`
  (#3) then `--stage calibrate` (#4) on the frozen code — BOTH refuse earlier in code; SPY +
  equity bars must be topped up through d+10 first (pure acquisition); ⏳ `--stage score`
  (#5) STRICTLY AFTER the Addendum G verdict is recorded (pass the verdict artifact via
  `--g-verdict`); any code change before #5 = dated addendum + re-freeze.
- ✅ Confirmatory abstention run COMPLETE 07-30 (BUILD_LOG row): 19.97% pooled — 0.03pt below
  the band, recorded as the registered finding. Thread closed.
- **⏳ Phase 3 (dealer hedge footprint): 3.1 + 3.2 DONE (3.2 verdict FOOTPRINT-AMBIGUOUS,
  direction-without-size — BUILD_LOG rows + ROADMAP result blocks are the record). 3.3
  REGISTERED 2026-08-01, founder-blessed "Ok lets run it", labels FROZEN** (SHA
  `104c184e…a089e57`, HIGH 25 / NULL 84 / ANTI 8, `tools/hedge_footprint_freeze.py`).
  Measurement BUILT (`tools/hedge_label_drift.py` + 177 tests, seal locks in code).
  **Gate 1 FAILED on both populations — the registered ladder is EXHAUSTED: NO-RUN,
  forward returns NEVER unsealed. 3.3 is DORMANT-ACCRUING** (ROADMAP Amendments 1–3:
  narrow 0.18 → wide 0.29 power at the 1.5% floor; MDE 3.0%; binding leg =
  m-residualized; HIGH accrues ≈3–4/month → meaningful re-gate ~a year out; any earlier
  re-test = NEW registration, disclosed weaker). Wide artifacts:
  `hedge_footprint_wide/equity/` (250 events frozen, SHA `c87592df…97264f`).
  ✅ CLOSED 2026-08-03: 3.3 UNSEALED AND ANSWERED — verdict **NO-VERDICT-UNDERPOWERED,
  point estimate ~zero** (Δ̂ +0.05% [−0.90..+1.13], p 0.60; realized MDE 3.0% vs the
  1.5% floor — realized event-day noise = ~2× the controls-only estimate). Study
  DORMANT; no tranche-2. The full record = BUILD_LOG 2026-08-03 unseal row + ROADMAP
  3.3 Addenda 5/6 + `results_combined_3p3.json`. ★ Doctrine for every future gate:
  controls-only power estimates are ~2× optimistic — carry a realized-vol stress.
  ⚠️ Fence note stands: `resolve()` returns None for non-last-reuse-segment dates;
  any consumer added later must handle None.
  ⏳ **3.4 spread orientation: prereg round 1 ran 2026-08-03 and closed BLOCKED — see
  ROADMAP 3.4 round-1 block + BUILD_LOG row.** Two-Opus split-mandate review killed the
  o-band-agreement design at today's corpus size (validation cell ~30–65 events after
  honest filters vs ~300–500 the power gate demands, ~10× short) AND on validity (the
  o sign convention itself was never validated — §3's own required sanity check never
  ran — and σ_o mechanically couples to the underlying's concurrent move). 3.4 is
  PARKED-ACCRUAL. **The o sign validation RAN 2026-08-04 (BUILD_LOG row + ROADMAP 3.4
  consequence (ii)): Step-1 verdict INSTRUMENT-DEAD at the registered strict/±100ms arm
  (a_cal 0.545, CI ∋ 0.5; T+60s placebo c over bound); Step 2 never ran, nothing
  unsealed. The registered diagnostic INVERTED the a_p≈1 assumption — midpoint-signed
  tied prints at |Δt| ≤ 2ms read a_cal 0.858 (n=2,695): the cross-tape instrument is
  strongly LIVE under the corrected arm the registration didn't bet on.** Founder GO
  2026-08-05; **Amendment 1 REGISTERED + APPROVED same day**
  (`O_SIGN_VALIDATION_AMENDMENT1_2026-08-05.md`, 4 rounds, v4 APPROVE×2 — BUILD_LOG
  row). **All 15 Build-obligation deltas BUILT 2026-08-05** (BUILD_LOG row: +20 pinned
  tests, module 34/34 green, full suite 3,980 green). ✅ CLOSED 2026-08-05: **Step 1′
  RAN — final verdict INSTRUMENT-WEAK′; the o-sign family is DEAD (arm 2 of 2 per the
  family ledger — no further arm without a NEW prereg); forward returns NEVER unsealed**
  (BUILD_LOG row). The instrument REPLICATED out-of-sample (mixture a_cal 0.845
  bootstrap-t [0.780, 0.878], perm p 2×10⁻⁵, κ 0.69 on 17 fresh disjoint `pool[3::6]`
  sessions, 5,100 events 0 fetch faults; placebo law + positive control passed; regime
  post-Feb STRONGER 0.910 vs 0.821) — but the composition-reweighted LB of record
  0.769 < θ* 0.90 (pilot-refined; keep-rate 0.29), and the conjunction gate failed
  independently: power 0.505 (0.145 at the registered 2× stress), premium-range
  containment FAILED. Step 2: ZERO fetches. Artifacts `data/research/o_sign/`
  (step1p.json, s2_leg_meta.json, gate output). Any 3.4 revival must
  start from the round-1 fix-list in the ROADMAP block (residualization on (m, m²),
  two-sided verdict incl. INVERTED, in-band-only edges, label-accuracy sweep in the
  power gate, isolation on |H| not premium, any-size isolation base).
- **⏳ Deferred until a consumer exists:** wire `by_measure` calls into the idea ledger so grading
  can rank the three weightings ordinally.
- **⏳ Two known guard holes** (deliberate, close later): 16:00–16:15 ET index-settlement window
  unmonitored (equity `is_market_open` flips False at 16:00; ~31% of premium settles there);
  truncation detection is size-only with a hard ceiling — durable fix = assert last record ~16:15 ET.
- **⏳ Equity-bar coverage gaps** feed ROADMAP 1.4/1.5: live names (CMA/PSTG/MMC/CTRA/CFLT/MPW/AZN)
  have no bars → no moneyness; adjusted contracts (WOLF1 etc.) inflate `no_spot_$` via the
  hardcoded 100 multiplier. 1.4 must read the per-root breakdown, not the bucket.

## ▶ 2026-07-26 audit round — remaining open cards

Findings live on cards; six of nine closed. Still open: `chat-dead-ts-surface` (dead TS brain incl.
an unguarded `propose_order` endpoint) · `copilot-memory-store-empty` (0 active memories, embeddings
backfill never ran) · `fusion-unminted-dots` (4 of 4 BUILT 07-28; `rvol_ex_prints` awaits its deploy —
see fusion section).
REASON/DECIDE wall-down was proven live Monday (TSLA/XLE admissions); the `REASON_EMIT_ENABLED`
flip is founder-gated on the z-vs-outcome audit (~08-05, see fusion section).

## ▶ Fusion — Horizon Redesign Round 5 + measurement follow-ups

**Cohort boundaries — both deploys are now placed, on separate dates** (news moves catalyst reach UP,
the attention frozen-peak fix moves legacy scores DOWN — together the score step is uninterpretable;
rationale in the two 07-28 BUILD_LOG rows):
- ✅ **NEWS COHORT BOUNDARY = 2026-07-30, and it is a CLEAN full session, not a partial one.** Settled
  by measurement 08-01, not by the restart timestamp: the post-reboot backend start was 07-30
  03:40:47 UTC, and the first news dot was written 07-30 13:32:09 UTC — 09:32 ET, two minutes after
  the bell. The 8,550-dot burst in that hour is the backfill (dot `ts` reaches back to 07-22); live
  minting ran 07-30 and 07-31 (10,958 dots total). Nothing landed mid-session, so no day needs a
  PARTIAL label.
- ▶ **RVOL COHORT BOUNDARY = the first session after the #135 deploy (08-03 open).** Deployed
  08-01 with the market closed, which is a pre-open deploy with no session in between — never
  mid-session, and a different cohort date from news.
- ⏳ 08-03+: `rvol_dots` / `rvol_skipped_*` counters, and the per-pass write-cost benchmark the review
  asked for (still never done). Attention scores stepping DOWN = **the correction, not signal**.
  ⚠️ **The old "the migration full-scans ~591k `evidence_dots`, keep it off the lock-starve path"
  warning was wrong and is retired** — measured 08-01 on a copy of the live DB: the table is 158,506
  rows, the migration UPDATEs **262** of them across 75 episodes (39 still open), deletes nothing,
  touches no other component, and runs in **37 ms**, idempotent. It is not a lock hazard.
- ⏳ 08-03+: watch `volume_profile_refresh`. `profile_universe()` now unions open-episode symbols, which
  took it 1,008 → 1,876 (+868 measured 08-01), so the nightly build is roughly twice as long at its
  existing 0.6 s spacing. Equities route to **Alpaca** under hybrid (`_child_for` sends only futures to
  Schwab), so there is no WAF exposure — but that job's `# safely under Schwab's ~120/min` comment is
  now a stale relic and the pacing is untuned for the provider it actually hits.
- ⏳ next in this lane: filings → fusion dots — needs `ingest_seq` on `filings_events` plus a founder call
  on insider-buy direction (`none` like news, or a deliberate exception). Congress rows stale since 07-02.

Lane DARK (`REASON_EMIT_ENABLED=False`) until Round 5. Open:
- ⏳ ≈2026-08-05: T=3.5 revisit (recount T tables on print-price-era + flow-list-era dots) AND the
  z-vs-outcome audit from the 07-28 integrity round — together they feed the REASON_EMIT decision.
- founder dials open: thesis/trigger half-lives, conviction bar, trigger TTLs — changes land as
  founder-visible diffs, never silent.
- NEXT round: position-watch (entry-snapshot divergence alerts, notify-never-act) → Round 5 ARM.
- ✅ **card `dossier-repair` LANDED** (closed 08-08 — D1–D5 verified on main, 50/50 dossier tests
  green; see the BUILD_LOG rows). Arming still waits on the remaining Evidence Bus §2.9
  components (one per round: oi_walls, options_chain/IV, DERA segments, filings_events) + the
  contested-wall gate. ✅ The EXPOSING work SHIPPED 08-08 and card `dossier-wire` CLOSED 08-09
  (see the BUILD_LOG row): `dossier` perception resource (all three agent surfaces via the
  registry), on-demand `GET /api/fusion/episodes/{id}/dossier`, evidence-stack `dots · dossier`
  view — all ephemeral, `put_dossier` stays REASON-lane-only; `episodes` resource closed the
  discovery gap same-day. Founder's in-browser look at the widget view was flagged at close;
  visual nits, if any, become a UI-polish follow-up.
  ✅ **D5's watermark dependency is CLOSED** — `research.edgar_bulk.edgar_ingest_watermark` shipped
  the same day and the import in `signals/dossier._edgar_ingest_watermark` resolves; contract
  round-trip verified (changed filer → `checked_at` + `latest_filed`; unchanged → `checked_at` only;
  unseen → `None`).
  ✅ **The nightly job is ACTIVE** (deployed + seeded + cron installed 08-02), so `fundamentals`
  claims now render `[SEC checked <day>]` against a same-day corpus instead of
  `[SEC recency unknown — corpus never checked]`.
  Doc lineage settled 08-02 so it isn't re-hunted: only THREE of the ~15 fusion docs govern the
  dossier — Unified Design 07-16 (component 3 = its entire spec), Evidence Bus 07-17 §1b (hands it
  BACK to Unified; adds §3 Phase C one-archetype sequencing + §2.9 news-dots-first), Round 3 07-22
  (the episode model D1 conformed to). D1 restored conformance — Round 3 already stamped
  `market_day … now = OPENING session only, not a key`.
- ⚠ full pytest suite live-fires outbound HTTPS (suite-hang + WAF risk) — needs a network-marking
  round.
- wider-stop sibling lane + index-underlying spot (SPX/NDX/RUT/VIX unstampable) deferred on the
  `fusion-mech-measurement-integrity` card.

## ▶ Chart load perf — founder eyeballs open

PR #102 deployed + machine-verified 07-23. Open: founder verifies in-browser (warm paints on
timeframe switches); ETH-off charts still paint pre/post — make `ext=false` truly RTH-only in
`_build_bars_response` (mind half-days), next UI round.

## ▶ Per-symbol vol model — shipped disabled; ARMING is the open thread

Double-gated off (`volatility_enabled` + per-strategy `vol_target`). Open:
- ⚠ two split-mandate Opus reviewers owed BEFORE arming (not before commit) — point them at the
  clamp/fail-soft paths and halt guard, not estimator math.
- rollout order fixed: shadow → paper opt-in on ONE strategy → perception dot (own card).
- shadow output graded against outcomes before arming anything (`calibration-counts-everything`).

## ▶ LLM paper books — ARMED + LIVE; increments open

- ✅ **2026-08-14 Keeper 2 shipped** — closed `mechanism` enum on every book candidate
  (advanced + considered). `idea_tracker` unchanged (sealed price grader). Pack section
  `Your mechanism ledger` is a derived n-gated Wilson scoreboard (`MIN_SAMPLES=10`, never a
  0.7). Failure modes stored, not scored. **Do not fuse** into the Decision Engine (valve + n).
- ⏸ **2026-07-30 Matt + LLM PAUSED** — account verified empty (0 positions). Founder wants this as a
  CHAT interaction, not a nightly pass; deliverable is on card `matt-llm-belongs-in-chat` (pull
  `build_book_pack` into a chat turn, then decide whether the book/journal/report-kind retire).
- ⏸ **2026-07-30 Claude Panel PAUSED** (cron line commented + `BOOKS['panel'].paused=True`). Solo books still run.
  - ⚠ `acct_alpaca_open` positions now get NO nightly hold/trim review. Resting GTC stops still cap
    downside; nothing trims winners or reacts to a broken thesis. Decide: review manually, let the exit
    engine own it, or resume the panel.

Spec `LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md` (header amendments override body). Open:
- ⚠ **2026-08-07 prompt amendment is live and UNVERIFIED against a real cycle.** Both prompts grew a
  `## Report format` section (see the log row); it is pinned identical across `fable.md` and
  `_CHARTER` by test, because the two books are compared and a format drifting onto one is a
  confound — keep them in step. The block asserts client behaviour (TL;DR → standfirst, tables
  stacking below 560px, signed figures auto-coloured) taken from
  `mobile/src/features/reports/report-document.ts`, which is **not on `origin/feat/mobile-app`** —
  that viewer is unpushed and the claims could not be checked from here. Watch the first Fable and
  Deep Seek cycles after deploy: expect real `##` sections in both and no json in the prose.
- ⚠ **The mobile viewer restyles our report HTML BY CLASS NAME** — `wrap · hero · badge · meta ·
  summary · metrics · metric-card · metric-label · metric-value · report-section · panel · dual ·
  citations` in `render_report_html` (`ai/research/report.py`). Structural HTML changes are free;
  the class names are now a cross-client contract, and a new class renders unstyled on the phone.
  Flag additions rather than discovering them on a phone.
- ⏳ first fully-armed nightly cycles end-to-end at 17:05/17:20/17:40 ET (proposals straight from
  the cycle, no replay).
- MOO probe outcome — see overdue list.
- increment 5: shorts unlock (own adversarially-reviewed round; `_propose_advanced` hard-blocks
  shorts until it ships).
- ⚠ NEXT increment before analysts lean on GTC styles: order-management verbs (cancel/replace for
  standing GTC entries + resting stops); until then analysts request cancels in `holds_review`,
  founder acts manually — the nightly pack's open-orders section is the review surface.
- Lab book positions section honest-empty (per-account positions endpoint); session-book research
  depth v2.
- ⏳ **2026-07-30 tonight: confirm the seats actually USE the perception CLI.** The books were
  blind (no perception access at all) until `tools/perception_query.py` was wired into
  `book_session.sh --allowedTools` + both prompts. This assumes Agent-spawned seats inherit the
  session allowlist — likely (the 07-29 scrapers used session-level WebFetch/WebSearch) but
  UNVERIFIED. Check tonight's transcripts for `perception_query.py` calls and for a drop in
  web-scrape sub-agents; if seats can't run it, the pack must carry the data instead.
- ⏳ **2026-07-30 forced tool-choice was NOT ported and is now gone from the code.** The retired TS
  route forced `toolChoice: propose_order` on an order turn; `ai/chat/loop.py` is `tool_choice="auto"`
  throughout. The intent prompt block says the same thing in prose, so this is belt-and-braces — but if
  "[Calling propose_order...]" narration reappears in chat, this is the first suspect. Porting it needs
  per-provider handling (DeepSeek thinking models 400 on `tool_choice`).

## ▶ Waiting on founder

- ⚠ global `allow_short_selling=true` in prod settings — broader than the per-strategy book
  authorization (any approved sell with no held long can short-to-open on paper; books + stops
  excluded, chat/manual not). Confirm or narrow when increment 5 builds the per-strategy flag.
- ⚠ subscribe the phone to the ntfy dead-man topic (topic string in crontab) — pages go nowhere
  until then.
- Overnight 07-18 harvest + draft PR #100 — judge together.
- Classification-lab addenda sign-off (DTE-cut addendum + F3 direction-semantics stamp).
- `braveapi` engine signup for SearXNG (likely unnecessary at 30 results without it).

## ▶ Options IV corpus — verifies + the two premium-selling gates

Settled science lives in the three preregs + memories (rank gate unsupported; level real but absent
on our 37 names). Open:
- cron + OI verifies — see overdue list.
- spread gate: intraday re-run — see overdue list.
- the capped-payoff gate is UNTOUCHED by all measurement so far (seller keeps ≤ premium, left tail
  uncapped; C4 band 29.7% negative, p05 −0.3105) — nothing may open a selling lane past it.

## ▶ Options flow classification scorer — ✅ REGISTERED RUN COMPLETE 07-30, thread closed

Zero viable cells across all 10 method×variants; per the prereg's conclusion clause the paid
paired feed is justified — the 07-26 Databento tcbbo subscription stands validated by
pre-registered measurement (BUILD_LOG row; card `classification-lab-scorer`, done). Deferred
audit findings F3/F4/F8 still ride `index-0dte-concentration-study` / gauge cards.

## ▶ Backend composition — Rank-2 open

Rank-2 = exit-engine own cadence (in-process, off the `_poll_once` tail); `trading/`-adjacent →
max depth + two-reviewer adversarial review. NOTE: the audit argues AGAINST extracting the order
path out-of-process — resolve against the old rung-3 note at design time.

## ▶ Exit engine — first real proof 2026-07-29

7 earnings_drift positions must auto-sell 2026-07-29. Verify throttle marks (`exit_engine.py:390`)
don't delay the open fill; then fix the amc `td==1` docstring (order fills via next-cycle retries
after the open, not after-close queueing — RiskEngine's no-live-quote gate rejects after-close).
Sibling unguarded fallbacks tracked on `data-correctness-m-tail`.
