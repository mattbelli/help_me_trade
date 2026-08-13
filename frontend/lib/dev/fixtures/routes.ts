/** Endpoint → fixture table for the /dev/gallery harness.
 *
 * Every widget's data reaches it through `apiFetch` → global `fetch`, so ONE
 * table covers every widget. Order matters: the first matching pattern wins, so
 * `/api/research/jobs` must sit above `/api/research`.
 */
import * as accounts from "./accounts";
import * as insights from "./insights";
import * as market from "./market";
import * as performance from "./performance";
import * as trading from "./trading";

export type GalleryState = "default" | "empty" | "loading" | "error";

export const GALLERY_STATES: readonly GalleryState[] = [
  "default",
  "empty",
  "loading",
  "error",
];

export function isGalleryState(value: string | null): value is GalleryState {
  return GALLERY_STATES.includes((value ?? "") as GalleryState);
}

type Handler = (url: URL, state: GalleryState) => unknown;

/** `both(rich, bare)` — the `empty` state serves the bare payload. */
function both(rich: unknown, bare: unknown): Handler {
  return (_url, state) => (state === "empty" ? bare : rich);
}

/** Same payload in every state (config-ish endpoints nobody renders as rows). */
function always(value: unknown): Handler {
  return () => value;
}

const ROUTES: [RegExp, Handler][] = [
  // ── accounts / snapshot ──────────────────────────────────────────────────
  [/^\/api\/snapshot$/, both(accounts.SNAPSHOT, accounts.SNAPSHOT_EMPTY)],
  [/^\/api\/broker$/, always(accounts.BROKER)],
  [
    /^\/api\/schwab\/accounts$/,
    both(accounts.SCHWAB_ACCOUNTS, accounts.SCHWAB_ACCOUNTS_EMPTY),
  ],
  [/^\/api\/schwab\/equity-curve$/, always(accounts.SCHWAB_EQUITY_CURVE)],
  [
    /^\/api\/portfolio\/history$/,
    both(accounts.PORTFOLIO_HISTORY, accounts.PORTFOLIO_HISTORY_EMPTY),
  ],

  // ── trades / orders ──────────────────────────────────────────────────────
  [
    /^\/api\/trades\/accounts$/,
    both({ accounts: accounts.TRADE_ACCOUNTS }, { accounts: [] }),
  ],
  [
    /^\/api\/trades\/events$/,
    both({ events: trading.TRADE_EVENTS }, { events: [] }),
  ],
  [
    /^\/api\/trades\/stats\/summary$/,
    always(performance.PERFORMANCE_DASHBOARD.summary),
  ],
  [
    /^\/api\/trades\/stats\/daily$/,
    both({ days: performance.PERFORMANCE_CALENDAR }, { days: [] }),
  ],
  [
    /^\/api\/trades\/stats\/by-symbol$/,
    both({ symbols: performance.PERFORMANCE_SYMBOLS }, { symbols: [] }),
  ],
  [
    /^\/api\/orders\/executions$/,
    (url, state) => {
      if (state === "empty") return { executions: [], count: 0 };
      const rows =
        url.searchParams.get("status") === "rejected"
          ? trading.ORDER_EXECUTIONS_REJECTED
          : trading.ORDER_EXECUTIONS_REJECTED;
      return { executions: rows, count: rows.length };
    },
  ],

  // ── performance ──────────────────────────────────────────────────────────
  [
    /^\/api\/performance\/dashboard$/,
    (url, state) => {
      if (state === "empty") return performance.PERFORMANCE_DASHBOARD_EMPTY;
      // The trading calendar asks for one month at a time and then sums what it
      // gets as "MONTH P/L". Returning the full 65-day series for every request
      // made that header disagree with the days on screen — so honour the range
      // the way the real endpoint does.
      return performance.dashboardForRange(
        url.searchParams.get("from_date"),
        url.searchParams.get("to_date"),
      );
    },
  ],
  [
    /^\/api\/performance\/day-pnl$/,
    both(accounts.DAY_PNL, accounts.DAY_PNL_EMPTY),
  ],
  [
    /^\/api\/performance\/calendar$/,
    both({ days: performance.PERFORMANCE_CALENDAR }, { days: [] }),
  ],
  [
    /^\/api\/performance\/symbols$/,
    both({ symbols: performance.PERFORMANCE_SYMBOLS }, { symbols: [] }),
  ],
  [
    /^\/api\/performance\/sectors$/,
    both(performance.PERFORMANCE_SECTORS, performance.PERFORMANCE_SECTORS_EMPTY),
  ],

  // ── watchlist / quotes / chart ───────────────────────────────────────────
  [
    /^\/api\/watchlists$/,
    both(market.WATCHLISTS, market.WATCHLISTS_EMPTY),
  ],
  [
    /^\/api\/watchlist\/market$/,
    both(market.WATCHLIST_MARKET, market.WATCHLIST_MARKET_EMPTY),
  ],
  [/^\/api\/watchlist\/aggregate$/, always(market.WATCHLIST_AGGREGATE)],
  [
    /^\/api\/watchlist$/,
    both({ symbols: market.WATCHLIST_SYMBOLS }, { symbols: [] }),
  ],
  [
    /^\/api\/symbols\/overnight$/,
    always({ overnight_tradable: ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"] }),
  ],
  [
    /^\/api\/symbols\/search$/,
    always({ symbols: market.WATCHLIST_SYMBOLS.slice(0, 8) }),
  ],
  [
    /^\/api\/bars$/,
    (url, state) =>
      state === "empty"
        ? market.BARS_EMPTY
        : market.barsFor(url.searchParams.get("symbol") ?? "SPY"),
  ],
  [/^\/api\/session-node\/levels$/, always(market.SESSION_LEVELS)],
  [/^\/api\/drawings$/, always({ drawings: [] })],
  [/^\/api\/chart-layout$/, always({ layout: [] })],

  // ── alerts / proposals / risk ────────────────────────────────────────────
  [
    /^\/api\/price-alerts$/,
    both({ alerts: trading.PRICE_ALERTS }, { alerts: [] }),
  ],
  [
    /^\/api\/proposals\/pending$/,
    both({ proposals: trading.PENDING_PROPOSALS }, { proposals: [] }),
  ],
  [
    /^\/api\/proposals\/score$/,
    always(trading.PENDING_PROPOSALS[0].score ?? null),
  ],
  [/^\/api\/kill-switch$/, always(trading.KILL_SWITCH)],
  [/^\/api\/entries-halt$/, always(trading.ENTRIES_HALT)],

  // ── options flow / index pressure ────────────────────────────────────────
  [
    /^\/api\/options-flow$/,
    both(market.OPTIONS_FLOW, market.OPTIONS_FLOW_EMPTY),
  ],
  [
    /^\/api\/index-pressure\/history$/,
    both(market.INDEX_PRESSURE_TREND, market.INDEX_PRESSURE_TREND_EMPTY),
  ],
  [
    /^\/api\/index-pressure$/,
    both(market.INDEX_PRESSURE, market.INDEX_PRESSURE_EMPTY),
  ],
  [
    /^\/api\/overnight-flow$/,
    both(market.OVERNIGHT_FLOW, market.OVERNIGHT_FLOW_EMPTY),
  ],
  [
    /^\/api\/options-chain$/,
    both(market.OPTIONS_CHAIN, market.OPTIONS_CHAIN_EMPTY),
  ],

  // ── scans ────────────────────────────────────────────────────────────────
  [/^\/api\/scan\/presets$/, always({ presets: market.SCAN_PRESETS })],
  [/^\/api\/scan\/run$/, both(market.SCAN_RUN, market.SCAN_RUN_EMPTY)],
  [
    /^\/api\/scanner\/rvol$/,
    both(market.RVOL_SCANNER, market.RVOL_SCANNER_EMPTY),
  ],

  // ── notifications ────────────────────────────────────────────────────────
  [
    /^\/api\/notifications$/,
    both(insights.NOTIFICATIONS, insights.NOTIFICATIONS_EMPTY),
  ],

  // ── reports / research ───────────────────────────────────────────────────
  [
    /^\/api\/reports$/,
    both({ reports: insights.REPORTS }, { reports: [] }),
  ],
  [/^\/api\/reports\/[^/]+$/, always(insights.REPORTS[0])],
  [/^\/api\/ai\/research-config$/, always(insights.RESEARCH_CONFIG)],
  [/^\/api\/research\/jobs$/, always({ jobs: insights.RESEARCH_JOBS })],
  [/^\/api\/research\/jobs\/[^/]+$/, always(insights.RESEARCH_JOBS[0])],
  [
    /^\/api\/research$/,
    both({ artifacts: insights.RESEARCH_ARTIFACTS }, { artifacts: [] }),
  ],
  [/^\/api\/research\/\d+$/, always(insights.RESEARCH_ARTIFACTS[0])],

  // ── journal ──────────────────────────────────────────────────────────────
  [
    /^\/api\/journal\/decisions$/,
    both(insights.JOURNAL_DECISIONS, insights.JOURNAL_DECISIONS_EMPTY),
  ],
  [/^\/api\/journal\/calibration$/, always(insights.JOURNAL_CALIBRATION)],
  [/^\/api\/journal\/trades\/summary$/, always(insights.MY_TRADES_SUMMARY)],
  [
    /^\/api\/journal\/trades$/,
    both(insights.MY_TRADES, insights.MY_TRADES_EMPTY),
  ],

  // ── ideas / fusion ───────────────────────────────────────────────────────
  [
    /^\/api\/ideas\/outcomes$/,
    both(insights.IDEA_OUTCOMES, insights.IDEA_OUTCOMES_EMPTY),
  ],
  [
    /^\/api\/ideas\/ledger$/,
    both(insights.IDEA_LEDGER, insights.IDEA_LEDGER_EMPTY),
  ],
  [
    /^\/api\/fusion\/radar$/,
    both(insights.FUSION_RADAR, insights.FUSION_RADAR_EMPTY),
  ],
  [
    /^\/api\/fusion\/episodes\/([^/]+)\/dossier$/,
    (url) => ({
      dossier: insights.fusionDossier(url.pathname.split("/")[4] ?? ""),
    }),
  ],
  [
    /^\/api\/fusion\/episodes\/[^/]+$/,
    (url) => insights.fusionEpisode(url.pathname.split("/")[4] ?? ""),
  ],
  [
    /^\/api\/fusion\/episodes$/,
    both(insights.FUSION_EPISODES, insights.FUSION_EPISODES_EMPTY),
  ],

  // ── company info / news / filings ────────────────────────────────────────
  [
    /^\/api\/ticker\/watchlist\/news$/,
    both({ news: insights.WATCHLIST_NEWS }, { news: [] }),
  ],
  [
    /^\/api\/ticker\/[^/]+$/,
    (url) => insights.tickerInfo(decodeURIComponent(url.pathname.split("/")[3] ?? "AAPL")),
  ],
  [
    /^\/api\/filings$/,
    both({ events: insights.FILINGS }, { events: [] }),
  ],
  // ── app shell chrome (mounted around every route, gallery included) ──────
  [/^\/api\/workspace\/custom-themes$/, always({ custom_themes: [] })],
  [/^\/api\/workspace\/(layout|pages|workspaces)$/, always({})],
  [/^\/api\/chat\/sessions$/, always({ sessions: [] })],
  [/^\/api\/version$/, always({ version: "gallery-fixture" })],
  [/^\/health$/, always({ status: "ok", broker: "alpaca" })],
  [
    /^\/api\/ai\/models\/options$/,
    always({
      default_llm: { provider: "anthropic", model: "claude-sonnet-5" },
      options: [
        { provider: "anthropic", model: "claude-sonnet-5", label: "Sonnet 5", provider_label: "Anthropic" },
        { provider: "anthropic", model: "claude-opus-5", label: "Opus 5", provider_label: "Anthropic" },
      ],
    }),
  ],

  [
    /^\/api\/fundamentals\/earnings-history$/,
    (url) => ({
      symbol: url.searchParams.get("symbol") ?? "AAPL",
      events: [
        { period: "2026-05-01", label: "Q2'26", eps_actual: 1.71, eps_estimate: 1.64, surprise_pct: 4.3 },
        { period: "2026-02-01", label: "Q1'26", eps_actual: 2.4, eps_estimate: 2.35, surprise_pct: 2.1 },
        { period: "2025-11-01", label: "Q4'25", eps_actual: 1.44, eps_estimate: 1.52, surprise_pct: -5.3 },
        { period: "2025-08-01", label: "Q3'25", eps_actual: 1.4, eps_estimate: 1.35, surprise_pct: 3.7 },
      ],
    }),
  ],
];

/**
 * Returns the fixture body for a request, or `undefined` when no route matches
 * (the shim serves `{}` and records the miss so a gap is visible, never silent).
 */
export function matchFixture(url: URL, state: GalleryState): unknown {
  for (const [pattern, handler] of ROUTES) {
    if (pattern.test(url.pathname)) return handler(url, state);
  }
  return undefined;
}
