# The Agent Loop — Architecture + 2026 Tech Research

> The product is an **autonomous trading agent** that runs in the background so the user can work/parent.
> Its engine is one loop: **PERCEIVE → REASON → DECIDE → ACT → DELIVER → LEARN**, run continuously.
> This doc = how each stage works, what we've built, the gaps, and the recommended best-in-class tech (researched mid-2026).
> Specific model names / prices / library versions are research-sourced and churn monthly — verify before committing.

## Scorecard (current build state, from codebase inventory)

| Stage | Built | The critical gap |
|---|---|---|
| **PERCEIVE** | ~85% | fundamentals, news/sentiment, options data |
| **REASON** | ~75% | no agentic multi-step loop, no real RAG/memory retrieval, no guardrails |
| **DECIDE** | ~60% | no position sizing, no stops, no risk model, no backtesting framework |
| **ACT** | ~30% | **RiskEngine + execution not built — this is the bottleneck** |
| **DELIVER** | ~50% | no phone push / mobile (in-app only) |
| **LEARN** | ~70% | no trade journal, outcome→feedback loop not closed, no eval |

## Six load-bearing principles (emerged across every research stream)

1. **The LLM proposes; deterministic code disposes.** The LLM never holds account state and never authorizes a trade. Quant rules generate candidates + stops; the LLM ranks/vetoes within rails and can only make a trade *smaller or absent*, never larger or outside limits.
2. **Function-calling for live numbers; RAG only for unstructured text + memory.** Vector RAG over prices/fundamentals hallucinates plausible-but-wrong figures. Live data = tool calls + SQL.
3. **One fail-closed risk chokepoint.** Exactly one code path can call the broker, inside the risk engine, physically un-bypassable by strategy/LLM. Deny on any uncertainty. (The Knight Capital $460M lesson.)
4. **The user's approve/reject/modify is ground truth for learning — not P&L.** A single trade's P&L is noisy; gate it behind statistical significance (≥30–100 trades) and shrink edges. Clean human signal drives most improvement.
5. **Provider-agnostic, but tiered + swappable.** Default Opus 4.8 (heavy reasoning) + Haiku 4.5 (routine), behind a config swap layer so no vendor lock-in.
6. **Every learned artifact stays a human-readable, user-editable rule/example.** Improvement is inference-time (retrieval + rule promotion), no model training. You can always inspect, correct, veto.

---

## PERCEIVE — sensing the market

**How it works:** ingest market data (real-time + historical), fundamentals, news, calendars → compute indicators → emit a compact "facts" snapshot (latest values + pre-computed state labels like `rsi_state: oversold`) for the agent to reason over. Never dump raw series to the LLM.

**Built:** `brokers/market_stream.py` (shared Alpaca WS, quotes, alert broadcast, ring buffer), `brokers/alpaca.py` (snapshot, batch bars), `_poll_loop` (20s account/positions), `_signal_scan_loop` (300s), `signals/indicators.py` (sma/ema/rsi/atr/crosses), `signals/features.py` (`build_indicator_facts` → flat fact dict).

**Gaps:** fundamentals (P/E, P/S, P/B), news + sentiment, economic/earnings calendars, options data, live incremental indicators.

**Recommended tech:**
- Market data: **stay on Alpaca**; pay **Algo Trader Plus ($99/mo)** when you need real-time SIP/options. Add Polygon ($79) later only for tick backtest history.
- Fundamentals: **SEC EDGAR + Finnhub free** → **FMP Starter ($22/mo)** when XBRL normalization annoys you.
- News: **Alpaca/Benzinga + Finnhub** (free). **Sentiment: run it through your own LLM** (cents/day) not a paid API; FinBERT local fallback.
- Social: **Reddit PRAW** (free). Skip X/StockTwits (closed/expensive).
- Macro + calendars: **FRED** (free) + **Finnhub** (free) — one key covers fundamentals + news + both calendars.
- Indicators: **`pandas-ta-classic`** (batch) + **`talipp`** (live incremental O(1)), TA-Lib 0.6.x optional accelerator. **Do NOT install the original `pandas-ta`** (supply-chain concern).
- Pipeline/storage: asyncio+websockets ingestor (reconnect+backoff+REST gap-fill) · **SQLite(WAL) + Parquet archive + DuckDB analytics** · APScheduler · systemd. No Kafka/Redis/Timescale until multi-box scale. **Day-1 cost ~$0.**

---

## REASON — thinking over the facts

**How it works:** the agentic LLM layer takes facts + memory + signals and reasons toward a decision, using tools for live data and structured output for actions.

**Built:** `ai/llm/factory.py` (provider-agnostic: anthropic/openai/deepseek/openrouter/ollama/mock), `ai/llm/structured.py`, `ai/chat/agent.py` + `brain.py` (stream, tool planner, context), `ai/chat/tools.py` (TOOL_DEFINITIONS + dispatch: portfolio, memories, research, web_search, propose_order, create_rule), `ai/memories/retrieval.py` (token-overlap), `ai/research/` (LangGraph-ish deep research jobs), `ai/insights.py`.

**Gaps:** no durable multi-step agentic loop (single tool call/turn, manual chaining), no real vector RAG (token-overlap only), no prompt caching, no in-code guardrails on tool outputs.

**Recommended tech:**
- Orchestration: **Pydantic AI + DBOS** — provider-agnostic, lowest LOC for solo dev, **true auto-resuming durable execution + background queue backed by your existing SQLite** (no Redis/Celery). LangGraph + AsyncSqliteSaver is the alternative if human-in-the-loop pause/resume is central (its `interrupt()` is more proven).
- Models (tiered, swappable): **HEAVY → Claude Opus 4.8** (GPT-5.5 challenger); **ROUTINE → Claude Haiku 4.5** (Gemini Flash / DeepSeek for volume). No single "best trading model" — frameworks win benchmarks, not models.
- Tool use: free-text reasoning → **forced single tool + strict schema** for orders; `parallel_tool_calls=false` on irreversible actions; risk limits in deterministic code, never prompts; idempotency keys; bounded retry + hard step cap.
- Grounding: **function-calls/SQL for live numbers; RAG only for unstructured corpus + memory.** Retrieval = **sqlite-vec + FTS5/BM25 + RRF + a rerank API** (rerank is the single most impactful component). Embeddings: Voyage/Cohere behind an interface. Migration hatch → LanceDB if you outgrow brute-force KNN.
- Patterns: **two-speed** — fast route+parallel-fetch loop (intraday), slow multi-agent bull/bear debate + reflection (background); reflection only against *verifiable* checks (backtest/risk pass), never "was this a good trade?"
- Cost: prompt caching (volatile data AFTER the cache breakpoint), model routing, batch API (50% off) for overnight work, reasoning-effort tiering.

---

## DECIDE — turning reasoning into a sized, risk-aware order

**How it works:** quant rules produce a candidate + ATR stop → LLM judges/ranks within rails → size it → confidence gate → hard risk gate.

**Built:** `signals/rule_schema.py` (`RuleSpec` → condition dict, ~40-metric `METRIC_REGISTRY`, transition triggers + cooldown), `ai/rules/engine.py` (`evaluate_rules`, snapshot/event facts), `portfolio/analytics.py` (exposure/concentration/drift flags), rule CRUD + events in `db/ai_tables.py`.

**Gaps:** **no position sizing, no stop logic, no risk model (VaR/heat/correlation), no integrated backtesting framework.**

**Recommended tech:**
- Hybrid: **LLM proposes, deterministic engine disposes** (principle #1). Account state read-only via tools; LLM constrained to a typed action space; can only shrink/skip.
- Confidence: **don't trust verbalized confidence** — use **multi-provider/multi-sample agreement** as the score (fits provider-agnostic), abstain/shrink below threshold; map confidence → size multiplier, not go/no-go. Calibrate (temperature scaling → histogram binning), track ECE/Brier.
- Sizing: **take the minimum** of fixed-fractional (1–2% risk cap), **quarter-to-half Kelly** (never full — overbetting is asymmetrically fatal and backtested edges are inflated), and vol-target — then × confidence multiplier.
- Risk: ATR(14)×2 stops, R-multiples + rolling expectancy, **portfolio heat ≤6%**, correlation-cluster caps (corr>0.7 → bucket), single ≤10–20% / sector ≤30%, circuit breakers. All config, not constants.
- Strategy representation: keep your rule schema for *signals*; compile conditions with **`simpleeval`** (not `eval`); parity via an executor that abstracts data/broker + injects a clock (never `datetime.now()`).
- Backtesting: **lumibot** (same code backtest→paper→live on Alpaca) as primary; **open-source vectorbt** for research sweeps. Avoid backtrader/blankly (dead); nautilus has no Alpaca adapter.
- Promotion gate (never go live on one number): Backtest (DSR>0.95, ≥200 trades) → **CPCV/walk-forward (PBO<0.5, WFE>50%)** → **60–90d paper within 30% of backtest** → small live ramp.

---

## ACT — execution, gated by approval, behind guardrails  ⚠️ THE BOTTLENECK

**How it works:** agent proposes an order → human approves/denies/modifies (from phone) → a fail-closed risk engine validates → broker executes. Later: bounded auto-execution inside a code-enforced envelope.

**Built:** `db/paper_orders.py` (`record_paper_order_proposal`, status `recorded`), `propose_order`/`create_rule` tools, rule approval endpoints. **Proposals only — nothing executes.**

**Gaps (biggest in the whole system):** **no risk engine, no broker execution path, no kill switch, no order state machine, no reconciliation, no approval-execution binding.**

**Recommended tech:**
- Broker: **Alpaca** behind a thin `BrokerAdapter` (IBKR via `ib_async` deferred; Tradier/tastytrade only if options become core). SDK: **`alpaca-py`** (never `alpaca-trade-api`). Fills via `trade_updates` WS.
- Idempotency: deterministic **`client_order_id` = your `proposal_id`**, persisted before first submit, reused on retry (Alpaca 422s on conflict → look up, don't re-POST).
- Order model: state machine over Alpaca's 16 statuses; dedup fills on `execution_id`; trust `order.filled_qty`. Retry only `{429,500,503,504}` w/ jittered backoff; never blind-retry POSTs.
- Reconciliation: **broker = source of truth.** Backfill `GET /v2/orders?status=all` + positions + account on startup/reconnect/daily. Dedicated subaccount for the agent.
- Approval gate: **immutable proposal** with TTL + idempotency_key + `policy_version`. Hash the approved payload; snapshot a price "witness". **Re-validate at execution time, not just approval (TOCTOU)** — never fire a stale price. Modify = new proposal.
- **Risk engine = single fail-closed chokepoint** (principle #3): per-trade risk, position/order-size, price collar, daily-loss halt, drawdown floor, max positions, concentration, real-time buying power, dup. Strategies may tighten, never loosen.
- **Kill switch** separate from strategy fate (manual from phone + auto circuit breakers): cancel-all + block-new, idempotent, requires explicit human reset.
- Money: **`Decimal` everywhere**; SQLite `INTEGER`/`TEXT` not `REAL`; quantities as TEXT(Decimal); round at boundaries (ROUND_DOWN qty to 1e-9).
- Staged rollout: **DRY_RUN(paper) → propose-only(paper) → propose-only(live, 25→50→100% ramp) → bounded auto-exec inside a code envelope** (whitelist + daily notional/loss caps + hours), HITL outside it.

---

## DELIVER — reaching the user's phone, interactively

**How it works:** agent event → push to phone with interactive Approve/Deny/Modify → user responds; dashboard streams live state.

**Built:** `broadcast_alert` + `/api/alerts/recent` (ring buffer), in-app notification store + bell + banner (just shipped), price-alert monitor, `_alert_symbol_loop`. **In-app only — no phone.**

**Gaps:** native push, interactive notification actions, the approve/deny/modify mobile flow.

**Recommended tech:**
- **Capacitor + native APNs confirmed.** iOS Web Push **cannot render action buttons** (disqualified for lock-screen approve/deny). One Next.js codebase → Capacitor shell.
- Push backend: **self-hosted APNs direct from FastAPI via `aioapns`** (.p8 token). No OneSignal/FCM/Expo needed (buttons live in the client category, not the push service).
- Buttons: register `UNNotificationCategory` via **`@capacitor/local-notifications` `registerActionTypes()`** at startup (the push plugin lacks it; categories are global), send matching `aps.category`. **All actions `foreground:true`** (background actions don't fire JS reliably) — for a trade you want the app to open anyway. Modify → `router.push()` deep-link into the order form. Test on a physical device.
- Realtime: **SSE** (`sse-starlette`) multiplexed on one HTTP/2 endpoint for the dashboard; **REST POST** for actions with **client-generated idempotency keys**. SSE foreground only; visible APNs push for background events + REST reconcile on resume (silent push = best-effort only).
- Confirmation UX: risk-tiered — one-tap+undo (small) → `authenticationRequired`/Face ID (medium) → open-app + biometric transaction-signing bound to the proposal (large). Dual TTL (price validity vs decision window); revalidate price at approval; auto-cancel on expiry. Append-only hash-chained audit log.
- **Live Activities / Dynamic Island: defer** — real but ~90% native Swift, 1–2 weeks cold. Add only if the timed-approval card becomes a signature feature.

---

## LEARN — getting more helpful over time

**How it works:** approve/reject/modify + trade outcomes → updated preferences, promoted/demoted rules, dynamic few-shot bank, recalibrated confidence — all inference-time, no training.

**Built:** memory store (`db/ai_tables.py`: preference/lesson/research + FTS), `ai/chat/memory_extract.py` (LLM extraction + learned chips), context injection, reflection loop (`ai/reflection/`, 24h), trade ledger + FIFO P&L (`db/trades.py`), `_trade_sync_loop`.

**Gaps:** trade/decision journal, outcome→behavior loop not closed, no eval/observability, memory retrieval is token-overlap (no vectors).

**Recommended tech:**
- Store: **SQLite + `sqlite-vec`**, ~6 tables (preferences, rules, decisions, outcomes, market_snapshots, episodes). Keep mem0 only if already useful; **don't add a memory SaaS.** Cognee (local-first graph) only if multi-hop temporal queries become real. Memory leaderboards are vendor noise — choose on architecture.
- Model: CoALA taxonomy — preferences (always in context), rules (procedural, in system prompt), decisions+outcomes+episodes (episodic, retrieved). Per-source decay (news fast, filings slow, lessons slowest). Vectorize only free text; SQL for numbers.
- Retrieval: `recall_memory` tool — SQL pre-filter → sqlite-vec KNN → recency+importance+relevance re-score → top 5–8 compact cards (memory-as-tool beats giant context blobs).
- Feedback loop: **approve/reject/modify = primary clean signal** → infer a user-editable rule (CIPHER), promote on repeated approvals / demote on rejections (Bugbot pattern), ExpeL importance-count GC. **P&L = gated** (≥30–100 trades, James-Stein shrinkage, score the *decision* not the outcome). Confidence: verbalized → temperature scaling → histogram binning vs realized hit-rate.
- Eval/observability: **Langfuse self-hosted** (only $0 full-parity, OTel-native for FastAPI) + DeepEval (hard-gate metrics) + a 30–50-case golden suite in CI. Track Sortino/Sharpe + max drawdown **net of costs**, keyed to prompt+model version. Validate any LLM-judge against your own labels first.
- Journal: single **append-only, hash-chained** table; **record reasoning BEFORE the outcome** (honest training data, separates decision quality from luck); dual human-readable + structured format → one record serves as audit log, few-shot example, and eval datapoint.

---

## Consolidated recommended stack

```
PERCEIVE  Alpaca($99 when needed) + SEC/Finnhub/FRED(free) + own-LLM sentiment +
          pandas-ta-classic/talipp · SQLite+Parquet+DuckDB · asyncio ingestor
REASON    Pydantic AI + DBOS (durable, SQLite-backed) · Opus 4.8 / Haiku 4.5 (swappable) ·
          function-calls for live data, sqlite-vec+BM25+RRF+rerank for memory/corpus
DECIDE    rules→candidate+stop · LLM ranks within rails · agreement-confidence · min(fixed,¼-½Kelly,vol) ·
          lumibot (backtest=paper=live) + vectorbt research · DSR/PBO/paper promotion gate
ACT       Alpaca via BrokerAdapter · proposal(TTL+hash+idempotency, revalidate@exec) ·
          ONE fail-closed risk chokepoint + kill switch · Decimal · DRY_RUN→paper→live ramp→bounded auto
DELIVER   Next.js + Capacitor · self-hosted APNs (aioapns) · local-notifications categories (foreground) ·
          SSE dashboard + REST actions(idempotent) · biometric tiered confirm · defer Live Activities
LEARN     SQLite+sqlite-vec (6 tables, CoALA) · recall_memory tool · approve/reject/modify primary signal,
          P&L gated · Langfuse self-host + DeepEval · append-only pre-outcome decision journal
```

## What this points to (sequencing)

ACT is the bottleneck (30%) **and** the most safety-critical — and it's exactly what the **position-risk-guardian first job** + **autonomy ladder** + **phone approval** all converge on. The natural build order:
1. **Risk engine + execution path** (the fail-closed chokepoint, paper/DRY_RUN first) — unblocks everything.
2. **Phone delivery** (Capacitor + APNs + interactive approve/deny/modify) — the "be a dad" unlock.
3. **Decision journal + close the learn loop** (approve/reject/modify → rules) — so it gets better.
4. Deepen PERCEIVE (fundamentals/news) and REASON (durable agent loop + real RAG) as the agent's job demands.
