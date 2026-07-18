# Backend Composition Audit — the hmt-backend process (2026-07-18)

*Zero-context agent audit of the `api/main.py` process composition (prompt gave only the entry point,
the money stakes, and the one-box/systemd/SQLite constraints — no repo doctrine, no fault-line framing,
no prior audits). Pasted verbatim below; plain-text tables are as received.*

*Orchestrator verification (2026-07-18, before any fix): every load-bearing claim checked against code
and CONFIRMED — sync `run_eod_scan` + `fill_outcomes` on the loop (with `OPTIONS_FLOW_ENABLED=1` live, so
they really run in-backend nightly); fusion ATR sync SQLite on the loop every 300s RTH; exit engine rides
the tail of `_poll_once`; watchdog same-loop + once-per-job-per-day; `social_buzz` mark_alive no-ops
unregistered and `regime_sleeves` has no heartbeat (both invisible to /health + watchdog). Bonus find
while verifying: `test_registry_includes_all_audit_loop_names` was already red on main — the 07-16 fusion
trio was registered without updating the test.*

*Step-1 fixes shipped same day (see BUILD_LOG 2026-07-18): blind jobs registered, EOD scan +
fill_outcomes + fusion ATR moved off-loop via `asyncio.to_thread`. **Line numbers below reference the
pre-fix `api/main.py`.** OPEN (HANDOFF thread): Rank-1 nightly research worker (generalize the collector
seam), Rank-2 exit-engine own cadence (`trading/`-adjacent, needs adversarial review), loop-lag
measurement — note fusion ATR shipped 07-16 and cannot explain the 07-13 stalls, so a suspect is still
unmeasured. Notable disagreement with prior doctrine: the audit argues AGAINST extracting the order path
(approve + ExecutionService + RiskEngine stay one in-process chokepoint; make the loop safe instead) —
weigh this against the rung-3 "extract trading loops" note when that design round opens.*

---

Process composition audit: api.main / hmt-backend
Zero-context read of the code. Runtime rates/stalls/memory are marked HYPOTHESIS with the measurement that would settle them.

1. Inventory
One uvicorn process (deploy/systemd/hmt-backend.service:11), one asyncio loop, lifespan at api/main.py:2117–2224. Background work is two styles: hand-rolled while True loops (heartbeat via mark_alive) and api.jobs supervised jobs (start_all()).

Serving surfaces
Surface    Evidence    Stakes
HTTP JSON API
routers 2228–2241; many @app.* routes from /health onward
Cockpit, kill-switch, settings, data
Phone approve/reject
api/ai_routes.py:262–303 → ExecutionService.submit
Orders
Chat SSE (mobile + web)
api/ai_routes.py:440+, /chat
LLM latency on same loop
Market WebSocket
/ws/market 2276–2280 → market_hub
Live quotes/alerts
/health + job lags
2253–2273, jobs_health()
Ops visibility

A. Hand-rolled lifespan tasks (2186–2213)
Name    Cadence    Does    R/W    Die/stall behavior
poll
POLL_INTERVAL_SECONDS default 20s (173)
Broker snapshots; fill reconcile; unmanaged-fill sweep; exit engine (478–529)
Broker APIs; SQLite snapshots/executions/managed positions; may place exits via ExecutionService
Iteration try/except (532–537); retries next cycle. Stall = delayed exits/fills. Watchdog via heartbeat poll.
signal_scan
default 300s
User signal rules + run_strategy_scan; may propose_from_scan_fire
Bars/snapshots; proposals; notify
Logged + retry; heartbeat on success (583–584). Missed proposals if stalled.
session_node
default 60s
SPY session-node propose-only scan
Market data; proposals
Same pattern (592–599).
alert_symbol
30s
Pin WS symbols; off-hours ext-quote alerts
market_hub; quotes
Heartbeat (905–906). Stale alerts if dead.
trade_sync
3600s (+ boot sync)
Ledger sync from brokers
Broker activities → trade_events
Logged; heartbeat. Ledger lag, not immediate order harm.
proposal_sweeper
60s
Expire proposals; retry pushes; orphan reconcile
proposals/push DB
Heartbeat. Missed expiry/push retry if dead.
idea_tracker
30m market / 60m off
Grade open ideas; notify
idea_outcomes; bars via to_thread (859)
Heartbeat. Calibration lag.
filings_initial_fill
once after 15s
Seed EDGAR/congress
filings tables
One-shot; failures logged (1687–1691).
social_buzz
30 min
Reddit/StockTwits → mentions
HTTP + SQLite via to_thread
Loop never dies on error (321–344). mark_alive("social_buzz") but job not registered → watchdog blind (jobs.py:57–60 no-op).
earnings_refresh
24h
Finnhub calendar
earnings DB
Heartbeat.
regime_refresh
24h
Macro regime rows
regime DB
Heartbeat.
regime_sleeves
default 600s
Post-close propose-only sleeves
strategies/proposals
try/except (638–645); no mark_alive, not in job registry → silent to /health watchdog.
momentum_bars_refresh
24h
Daily bars if momentum enabled
bars store
Heartbeat.
volume_profile_refresh
24h
Build profiles for liquid-core ∪ watchlist (709–710); pace 0.6s/symbol (272, 738)
Schwab/Alpaca bars; profile files
Heartbeat only after full pass (751–752). Long cold build. Universe files: ~503+953+101 symbols (overlap).
reports_scheduler
default 900s
Morning levels + configured report jobs
reports DB; notify/LLM
Heartbeat (1646–1647).
bars_prewarm
15s outer; 0.25s between fetches (265, 3813)
Warm _BARS_CACHE for watchlist ≤40 symbols
Market data APIs
Heartbeat. Continuous broker load competitor.
equity_tape_flush
2s
Persist tape events + convergence or probe collector proxy if external (937–1012)
equity_tape DB / TCP :8767
Heartbeat; if external, green only when proxy reachable (972–973).
options_flow stream + sweep
continuous
OPRA WS + sweep consumer
options_flow DB
Only if enabled and OPTIONS_FLOW_EXTERNAL_COLLECTOR=0 (2205–2213). Else external unit.

B. api.jobs supervised jobs (1955–2114, runners in jobs.py:78–106)
Supervisor: sleep → gate → await fn() → mark_alive / bump failures. Backoff on failures (73–75). CancelledError re-raised.

Name    Interval    Role    Notes
Heartbeat shells
various
Track hand-rolled loops
fn=noop, heartbeat_only=True (1966–2003) — supervisor never runs work for these
rvol_alert
300s
Watchlist RVOL notify
Market regular only
options_flow (EOD)
1800s
Nightly UV scan + fill_outcomes
Env-gated; sync on loop (below)
options_flow_intraday
30s
REST poll when not external collector
Uses to_thread for poll (runner.py:323)
setup_scan_nightly
900s
LLM setup scan after 17:30 ET
Heavy; notify inbox
filings_edgar
600s
Form 4 / 13D
to_thread (1664)
filings_congress
6h
Congress trades
to_thread
earnings_alert
6h
t14/t1/t0 pushes
schwab_health
6h
Auth + SPY probe; critical notify
schwab_snapshot
1800s
EOD equity snapshot
db_retention
3600s
Prune/compress 01:00–05:00 ET
to_thread prune
reflection
default 86400s
LLM reflection
to_thread (ai/reflection/scheduler.py:21)
fusion_intake
60s, RTH only
Fusion episode intake
to_thread (1888)
fusion_atr_refresh
300s
ATR cache fill
Sync SQLite + sync bar math on loop (1908–1952)
pipeline_audit
900s
Silence/canary alarms
Async notify path
watchdog
600s
Stale-job + backup-age notify
Same loop as everything else

Out-of-process already (same box)
Unit    Seam    Evidence
hmt-flow-collector
OPTIONS_FLOW_EXTERNAL_COLLECTOR=1 + shared WAL
tools/flow_collector.py:11–14, main.py:222–228, 2206–2210
hmt-equity-collector
EQUITY_TAPE_EXTERNAL_COLLECTOR=1 + local WS proxy
tools/equity_collector.py:10–13, main.py:229–235, 943–974
Deploy kills backend MainPID; collectors optionally restart with backend (tools/serv-deploy.sh:39–42). Comment at serv-deploy.sh:25–26 records a real loop stall where SIGTERM was ignored.

2. Failure-domain analysis
Everything shares: one process, one asyncio loop, one GIL, one systemd restart, one SQLite file (db/base.py:18–25, WAL + busy_timeout=5000).

F1 — Loop stall freezes trading path + watchdog together (highest harm)
Mechanism: Sync or CPU-bound work on the event loop blocks poll → exit engine, fill reconcile, and POST .../approve → ExecutionService.submit, and the watchdog job itself.

Concrete sync-on-loop sites:

Options EOD scan — run_eod_scan(...) called directly from async job (main.py:1202), which loops underlyings and sync broker HTTP (scan.py:267–275; alpaca.get_option_bars sync _get_data at 1289–1319).
fill_outcomes — sync, up to limit=2000 events, sync get_stock_bars / get_stock_bars_range (main.py:1218; outcomes.py:77–136).
Poll path sync SQLite/CPU — count_pending_paper_executions / count_submitted_executions sync on loop (358–361); evaluate_on_snapshot sync (379, ai/rules/engine.py:219).
_fusion_atr_refresh_once — sync connect() + queries + ATR loops (1908–1952).
Chat finalize — sync finalize_chat_turn in stream finally (ai_routes.py:480–491).
FastAPI def (sync) handlers elsewhere block the loop for their duration.
Contrast (safer): intraday flow poll uses asyncio.to_thread (runner.py:323); idea tracker, filings, social, reflection, fusion intake also offload.

Harm: delayed stops/targets (exit engine only runs after each poll cycle, 514–529); delayed phone approve; delayed fill attribution → unmanaged inventory (sweep exists but also on poll, 505–512).

HYPOTHESIS: EOD options job or fill_outcomes can stall the loop for multi-second to multi-minute stretches under a large watchlist/outcome backlog.
Measure: during after-close with OPTIONS_FLOW_ENABLED=1, sample asyncio lag (e.g. heartbeat task timestamps) and /health jobs[].lag_s for poll while options_flow runs; or wrap run_eod_scan with wall-clock + check whether approve latency spikes.

Supporting incident evidence in-repo (not live): deploy script documents uvicorn surviving SIGTERM during a loop stall (serv-deploy.sh:25–26).

F2 — High-stakes work shares fate with high-churn / heavy work
High-stakes    Coupled with    Coupling
Exit engine + fill reconcile
Poll broker I/O, rules eval, unmanaged sweep
Same function _poll_once (478–529)
Approve HTTP
Chat SSE, bars prewarm, scans, reports, setup_scan
Same process/loop
Proposal expiry / push retry
Everything
Same process; 60s sweeper
Deploy/restart (RestartSec=3, hmt-backend.service:12–13) zeroes all of the above together. Collectors can survive if not restarted — but trading cannot.

HYPOTHESIS: Typical backend restart blackout for exits is on the order of boot + first _poll_once (2184) + up to one poll interval.
Measure: journald timestamps from stop → first "Exit engine" / "Snapshot saved" after deploy.

F3 — Silent or weak failure signals
Watchdog pages at most once per ET day per job (jobs.py:147–159). After the first page, continued staleness is quiet until tomorrow.
Watchdog cannot run if the loop is blocked (F1) — it is just another run_job on the same loop (209, 194–201).
regime_sleeves: no heartbeat registration; failures only in logs (633–645).
social_buzz: calls mark_alive("social_buzz") but name never register_job'd → no /health row, no stale page (social_buzz.py:331–342 vs jobs.py:57–60).
Job iteration failures are logged and retried; user-visible only if something calls notify (watchdog, schwab_health, pipeline_audit, etc.).

F4 — Memory / resource coupling
In-process caches: _BARS_CACHE capped ~256 (3908–3910); _wl_bars_cache; stream buffers if flow in-process.
Volume-profile cold build: large universe, long-lived task (yields via sleep, so not a hard stall, but sustained broker concurrency with prewarm/scans).
Setup scan: up to 4 concurrent candidate assessments (setup_scan.py:909–931) + LLM threads.
HYPOTHESIS: RSS growth correlates with in-process OPRA path and chat concurrency more than with heartbeat loops.
Measure: ps RSS / /health db_size_bytes over a session with collectors external vs in-process; chat load test.

F5 — SQLite writer contention (shared fate across processes)
WAL + 5s busy timeout (db/base.py:15–24). Collectors and API write the same DB. Contention delays writers; under timeout, operations fail (usually logged), not crash the process.

HYPOTHESIS: hot-open flow inserts + poll snapshot writes raise busy / latency.
Measure: SQLite busy_timeout hit logs; write latency histograms around the open.

F6 — Already-mitigated domain (collectors)
Flow/equity sockets were extracted specifically so API loop stalls don't blind streams (flow_collector.py:5–8). Ownership interlock prevents dual sockets (226–228, collector refuse-to-start). That pattern works; remaining harm is what is still inside the API loop.

3. Split recommendations (by expected harm prevented)
Existing seam to generalize (discovered in code, not docs):

Env ownership flag + standalone tools/*.py + systemd Restart=always + SQLite WAL handoff + API stands down.
Optional: local health probe (equity proxy TCP, main.py:913–931).
No broker, no containers. One developer-operable unit.

Rank 1 — Nightly / bulk research–data worker (split)
Move: setup_scan_nightly, options_flow EOD (+ fill_outcomes), volume_profile_refresh, momentum_bars_refresh, reflection, optionally db_retention / fusion_atr_refresh.

Failure modes removed: F1 sync EOD (main.py:1202, outcomes.py:77+); F2 heavy LLM (setup_scan.py:857+) and multi-hour profile builds sharing fate with exits/approve; deploy of "research code" need not kill trading if worker is separate (API deploys still kill trading — unavoidable without further splits).

Seam: HMT_RESEARCH_WORKER=1 (or per-job flags). Worker runs the same job fns via a small tools/research_worker.py cloning jobs.run_job. API registers those jobs disabled when flag set (mirror OPTIONS_FLOW_EXTERNAL_COLLECTOR).

Cost: one unit, shared .env, WAL contention (F5), duplicate settings reload. Medium ops cost; high payoff vs F1.

Rank 2 — Soften poll↔exit coupling (cheap; not a new unit)
Not a process split. Isolate exit engine + unmanaged sweep onto their own short loop (e.g. 5–10s) reading latest snapshots from DB, instead of only at end of _poll_once (514–529).

Failure mode reduced: broker snapshot slowness delaying exits even when loop is otherwise healthy.

Cost: low. Keep ExecutionService in-process. Do not move exit engine to another process yet (order path + risk + broker session complexity for little gain if Rank 1 lands).

Rank 3 — Perception / alert cron worker (optional)
Move: filings_*, social_buzz, earnings_alert, earnings_refresh, regime_refresh, schwab_health, schwab_snapshot, reports_scheduler, rvol_alert, pipeline_audit (notify-only).

Failure modes: F2 churn; quieter /health; reduces deploy blast radius for "feed" code.

Harm prevented: mostly attention/uptime, not dollars, unless schwab_health silence lets data die unnoticed (already soft). Lower priority than Rank 1.

Seam: same worker pattern; read-only broker probes OK in worker.

Rank 4 — Keep flow/equity collectors (already done); do not pull them back
If flags are 0 in some env, in-process stream+sweep return (2205–2213) — that re-creates the original stall domain. Prefer collectors on for production.

Do not split (no failure-mode payoff worth the complexity)
Keep together    Why
HTTP approve/reject + ExecutionService + RiskEngine
Order correctness needs one audited chokepoint (execution_service.py:48+); splitting needs RPC + dual risk surface for one maintainer
Poll snapshots + fill reconcile (same process as submit)
Attribution and pending/open reconcile assume live broker + DB (358–361, fill_tracker)
Proposal sweeper
Must share proposal DB with approve path; light; expiry is money-adjacent
Market hub WS + alert_symbol
Cockpit UX; equity tape already externalizable
Chat brain
Annoying under load but not dollar-path; splitting Next↔Python further adds ops without Rank-1 payoff
Watchdog alone
Useless off-loop unless it becomes a separate unit that only HTTP-probes /health (nice later; not a substitute for Rank 1)
Signal/strategy/session_node scans
Propose-only; moderate weight; need market data already in API; splitting without Rank 1 is premature

Harm-ranked findings (summary)
Shared-loop stall can freeze exits, approves, and the watchdog — evidenced by sync EOD/outcomes/fusion ATR on the loop (main.py:1202, 1218, 1908–1952); deploy note of TERM-resistant stall (serv-deploy.sh:25–26). Dollar harm: late/missed exits and stuck approvals.
Exit engine cadence is hostage to poll duration (514–529). Dollar harm under slow broker snapshots.
Whole trading stack dies on every backend deploy/crash (hmt-backend.service + lifespan). Collectors can live; orders cannot.
Watchdog is once-per-day and same-loop (jobs.py:147–159, 194–201) — weak under prolonged stall.
Blind spots: regime_sleeves, social_buzz not properly job-registered.
Already-correct split: flow/equity collectors via env+WAL — generalize that to a research/bulk worker, don't invent new infrastructure.
I did not observe a live system; every timing/memory claim above is HYPOTHESIS pending the named measurements.
