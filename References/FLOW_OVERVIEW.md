# OPRA options flow — orientation (start here)

*2026-07-28 · The one-stop map for everything options-flow. This is a LIVING doc — update it as
facts change (unlike the registered prereg chain, which is immutable: corrections there are new
dated addenda, never edits). Written the day the Alpaca-quote-cap-era docs were retired; if an
older flow doc contradicts this file, this file wins — check the "dead beliefs" table below first.*

## The two lanes

Flow work runs on two independent lanes. Confusing them is the #1 source of stale-doc damage.

**1. Live lane — Alpaca OPRA websocket** (production, running today)
- Owned by the dedicated `hmt-flow-collector` systemd unit (since 07-15), NOT `hmt-backend`.
  One OPRA websocket per account — that's a vendor limit, not ours.
- Symbol source: the dedicated **"flow" watchlist** (30 slots, pins first, union fallback);
  sub cap 40 is deliberate headroom, not a constraint. Watchlists are runtime filters over
  fixed-cost feeds — never size a design to one.
- Detectors: rolling directional-burst + small-print-cluster (`signals/options_flow/rolling.py`,
  design in `FLOW_INTELLIGENCE_DESIGN_2026-07-11.md` — the detector/schema/display parts of that
  doc are built and current; its capacity/architecture numbers are dead, see below).
- Index-pressure gauge: a **guaranteed gauge lane** (`GAUGE_FETCH_UNDERLYINGS` + reserved budget).
  No production code subscribes OPRA trades to `*` — never has. SPXW rides the SPX subscription on
  the WS lane only; NDX is unavailable (QQQ carries it).
- The **Alpaca condition-code sidecar** (ground truth for multileg labels, Addendum E) rides this
  same entitlement via REST backfill — wildcard historical trades carry the full post-2019 code
  family that Databento's `tcbbo` lacks. Forward capture is automated:
  `tools/nightly_flow_capture.sh` (cron 02:30 UTC Tue–Sat) chains tcbbo+defs download
  (spend-guarded at $0 — the free 12-month window rolls daily) → condition capture → join-ledger
  sweep. The §6b wildcard-WS live daemon is deliberately NOT built (decision 07-28): REST after
  close is complete-by-construction, and no intraday condition-code consumer exists yet.

**2. Research lane — Databento `tcbbo` whole-market corpus** (historical, ~250 sessions)
- Every OPRA trade with the pre-trade BBO attached (⚠️ quote = book BEFORE the trade; ONE snapshot
  per sweep, not per leg). Rolled up by `tools/flow_corpus_rollup.py` into `db/flow_corpus.py`
  (schema v5 frozen; v6 = Addendum E code-labeled multileg, machinery built + armed 07-27).
- Governed by the registered record: `FLOW_BASELINE_PREREG_2026-07-26.md` + Addenda A–E.
  The ordered plan is `FLOW_ROADMAP.md`. Do the phases in order.

## Dead beliefs — measured supersessions

These beliefs shaped the 07-11→07-16 era docs and are all dead by measurement. Any doc asserting
them is historical on that point, whatever else it still governs.

| Dead belief | Lived in | Measured truth |
|---|---|---|
| "Quotes-CPU is the scarce resource — 150 quote subs ≈ 52% of a core; ration quotes by heat" | `FLOW_INTELLIGENCE_DESIGN` §2, `archive/FLOW_CAPACITY_RESEARCH` | Quote cap is **exactly 1,000** subs, and 1,000 subs ≈ **0.09 cores** (~39× cheaper than the old constant). The scarcity the rationing architecture optimized never existed |
| "No documented option-quote count cap" | `archive/DATA_LAYER_LIMITS_AUDIT_2026-07-14` §4 | Cap measured at exactly 1,000 |
| "A wildcard trades lane rotates in production" | pre-#93 gauge design | `COLLECTOR_PRESSURE_AUDIT` F1: no production code ever subscribed `*`; the belief traced to research probes. Gauge coverage = the guaranteed lane |
| "Wildcard was too hot for our client (7/14)" | ops lore | Misdiagnosis — the lag was Alpaca's upstream open-burst queue |
| "OPRA `flags` will mark multileg" | pre-Addendum-D assumption | `flags` is constant 194, marks nothing. True labels = Alpaca condition codes (Addendum E); the ns-collision heuristic is precision 94.7% / recall 50.9% — a floor, not a fence |
| "Multileg ≈ 13% of prints / 33% of premium" | Addendum D (heuristic-measured) | True package premium **58.13%** on 2025-07-28 — the very session the 33% was measured on (52.54% vs heuristic 28.02% on 2026-07-24) |
| "Is the paid paired feed justified?" (the classification lab's central question) | `CLASSIFICATION_LAB_PREREG_2026-07-16` | Answered 07-26 by subscribing to Databento `tcbbo`. The prereg's metric definitions remain LAW for any print-lab work |

## Alpaca limits that still matter (live lane) — carried from the retired 07-14 audit

Tier: Algo Trader Plus, `sip` + `opra`. REST market data 10,000 req/min (we use ~30). One
websocket per endpoint per account (2nd → `406`). Option quotes `*` **forbidden** (the only hard
vendor wall); option trades `*` allowed but unused in production. Snapshots: 100 symbols/req,
1,000/page. The real ceiling is reactive: **`407 slow client`** — the server drops us if we can't
keep up, no published rate number.

## Doc map (flow only)

- **Plan (current):** `FLOW_ROADMAP.md` — ★ the ordered corpus→signal plan. Start there for "what's next."
- **Registered record (immutable — corrections are new addenda):**
  `FLOW_BASELINE_PREREG_2026-07-26.md`, Addenda A (midpoint rule), B (one spot source),
  C (direction rule), D (corrects C; multileg grain), E (code-labeled multileg, v6 migration);
  `CLASSIFICATION_LAB_PREREG_2026-07-16.md`; `OPTION_SPREAD_GATE_PREREG_2026-07-25.md`.
- **Research & proposals:** `FLOW_ALPHA_RESEARCH_2026-07-26.md` (read its verification header
  first); `FLOW_PACKAGE_ORIENTATION_DESIGN_2026-07-27.md` (proposal, probe gated behind
  roadmap 1.9).
- **Live-lane design & audits (partially current):** `FLOW_INTELLIGENCE_DESIGN_2026-07-11.md`
  (detectors/schema/display = built and governing; capacity §2 = dead, see table);
  `COLLECTOR_PRESSURE_AUDIT_2026-07-16.md` (gauge audit; F3 direction-semantics, F4 writer lease,
  F8 trailing-window caveat still open).
- **Archived (record only):** `archive/FLOW_CAPACITY_RESEARCH_2026-07-11.md`,
  `archive/FLOW_DTE_DIVERSITY_SPEC_2026-07-11.md`, `archive/FLOW_VIZ_DESIGN_2026-07-11.md`,
  `archive/DATA_LAYER_LIMITS_AUDIT_2026-07-14.md` (moved 2026-07-28),
  `archive/PC_TO_SERV_2026-07-09_options-flow.md`.

## Where the corpus stands (2026-07-28)

Schema v5 frozen (~250 sessions, ~49M rows). Addendum E v6 machinery built, tested, verified on a
scratch roll (premium drift 0 vs v5; join 98.40%). The migration gate is
`data/alpaca_conditions/join_ledger.json` (`condition_label_join.py --sweep`): every corpus session
must read `floor_pass: true` (≥98% join on contract-resolvable prints) before flipping
`SCHEMA_VERSION` to 6 in `db/flow_corpus.py`. Then: re-roll all sessions **niced, after the
backfill exits, never alongside** (~4.5GB peak per big session on a 15GB box), and re-run roadmap
1.8 on code-labeled arms. Runbook lives in `HANDOFF.md`.
