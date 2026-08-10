/** Agent-output fixtures: notifications, reports, research, journal, ideas,
 *  fusion, ticker info, news, filings. */
import type {
  FilingEvent,
  FusionDossier,
  FusionEpisode,
  FusionEpisodesResponse,
  FusionRadarResponse,
  IdeaLedgerResponse,
  IdeaOutcomeRow,
  IdeaOutcomesResponse,
  NotificationsResponse,
  ServerNotification,
} from "@/lib/api";
import type {
  CalibrationResult,
  JournalDecisionsResult,
  MyTradesResult,
  MyTradesSummary,
} from "@/lib/journal-api";
import type { ReportRow } from "@/lib/reports-api";
import type { ResearchArtifact, ResearchConfig, ResearchJobSummary } from "@/lib/research";
import type { TickerInfo, WatchlistNewsItem } from "@/lib/ticker-info-api";

import { DAY, NOW_ISO, daysBefore, et, lcg, money, round2 } from "./clock";

// ── Notifications ───────────────────────────────────────────────────────────

type NotifSeed = {
  kind: string;
  title: string;
  body: string;
  severity: ServerNotification["severity"];
  minsAgo: number;
  read: boolean;
};

const NOTIF_SEEDS: NotifSeed[] = [
  { kind: "proposal", title: "AMD — buy 40 @ 189.40", body: "RVOL 2.3× with a hold above the 20-day. Stop 176.00, first target 205.00.", severity: "info", minsAgo: 4, read: false },
  { kind: "risk_block", title: "Order blocked — daily loss cap", body: "TSLA sell 25 rejected: the account is 0.4% from the daily-loss guardrail.", severity: "critical", minsAgo: 19, read: false },
  { kind: "price_alert", title: "BRK.B crossed 520.00", body: "Alert set 21 days ago fired at 10:41 ET. Repeat is on.", severity: "success", minsAgo: 42, read: false },
  { kind: "options_flow_solo", title: "NVDA — 4,200 puts, $1.88M", body: "7 DTE 165P swept across three exchanges. Perception, not a signal.", severity: "info", minsAgo: 58, read: true },
  { kind: "report_ready", title: "Flow decomposition ready", body: "Post-close flow_decomp for 2026-08-07 finished in 41s.", severity: "info", minsAgo: 96, read: true },
  { kind: "fill", title: "Filled — AAPL sell 40 @ 228.71", body: "Paper account paper-main. Net +$9,148.38 after fees.", severity: "success", minsAgo: 134, read: true },
  { kind: "earnings", title: "3 watchlist names report next week", body: "AAPL, MSFT and GOOGL all report inside the next 5 sessions.", severity: "warning", minsAgo: 210, read: true },
  { kind: "idea_resolved", title: "SOFI idea resolved — won", body: "Target 1 hit on day 6. MFE +11.4%, MAE −2.1%.", severity: "success", minsAgo: 305, read: true },
  { kind: "job_watchdog", title: "Overnight collector restarted", body: "hmt-flow-collector exited at 03:12 ET and was restarted by systemd.", severity: "warning", minsAgo: 480, read: true },
  { kind: "scan_ready", title: "RVOL spike scan — 8 matches", body: "Watchlist universe, min RVOL 1.5. Top: SMCI 3.9×.", severity: "info", minsAgo: 620, read: true },
  { kind: "kill_switch", title: "Kill switch armed then cleared", body: "Armed manually at 11:02 ET, cleared at 11:07 ET. No orders were in flight.", severity: "critical", minsAgo: 800, read: true },
  { kind: "morning_brief", title: "Morning brief — 2026-08-07", body: "Term spread flat, VIX 14.2, credit spreads unchanged. Two names in the radar.", severity: "info", minsAgo: 960, read: true },
];

function minutesBefore(mins: number): string {
  const base = new Date(NOW_ISO).getTime();
  return new Date(base - mins * 60_000).toISOString();
}

const NOTIFICATION_ROWS: ServerNotification[] = NOTIF_SEEDS.map((n, i) => ({
  id: `notif_${String(i + 1).padStart(3, "0")}`,
  kind: n.kind,
  title: n.title,
  body: n.body,
  severity: n.severity,
  account_id: i % 3 === 0 ? "acct_paper_main" : null,
  strategy_id: null,
  ref_type: null,
  ref_id: null,
  action_url: null,
  group_key: null,
  data: {},
  created_at: minutesBefore(n.minsAgo),
  read_at: n.read ? minutesBefore(n.minsAgo - 1) : null,
  dismissed_at: null,
  read: n.read,
  dismissed: false,
}));

export const NOTIFICATIONS: NotificationsResponse = {
  notifications: NOTIFICATION_ROWS,
  unread: NOTIFICATION_ROWS.filter((n) => !n.read).length,
};

export const NOTIFICATIONS_EMPTY: NotificationsResponse = {
  notifications: [],
  unread: 0,
};

// ── Reports ─────────────────────────────────────────────────────────────────

const LEVELS_BODY = `## SPY — levels for ${DAY}

| level | price | source |
| --- | --- | --- |
| prior week high | 628.44 | PWH |
| prior day high | 625.88 | PDH |
| point of control | 621.40 | POC |
| prior day low | 617.12 | PDL |
| prior week low | 611.02 | PWL |

**Context.** Yesterday closed inside Wednesday's range, so the balance area is
intact and the POC at 621.40 is the pivot to trade against. A hold above the
prior-day high opens 628–630; failure back under the POC puts 617 in play.

**Honesty.** These are mechanical levels from the session profile. No win rate is
attached because none has been calibrated for level-touch trades.`;

const REPORT_SEEDS = [
  { kind: "levels", symbol: "SPY", title: "SPY levels", hoursAgo: 6 },
  { kind: "morning_brief", symbol: "", title: "Morning brief", hoursAgo: 8 },
  { kind: "flow_decomp", symbol: "", title: "Flow decomposition", hoursAgo: 1 },
  { kind: "evening_brief", symbol: "", title: "Evening brief", hoursAgo: 25 },
  { kind: "levels", symbol: "QQQ", title: "QQQ levels", hoursAgo: 30 },
  { kind: "on_request", symbol: "NVDA", title: "NVDA — why did it break?", hoursAgo: 54 },
];

export const REPORTS: ReportRow[] = REPORT_SEEDS.map((r, i) => ({
  id: `rep_${String(i + 1).padStart(3, "0")}`,
  kind: r.kind,
  symbol: r.symbol,
  title: r.title,
  body: LEVELS_BODY,
  format: "markdown",
  payload: {},
  generated_at: new Date(
    new Date(NOW_ISO).getTime() - r.hoursAgo * 3_600_000,
  ).toISOString(),
}));

export const REPORTS_EMPTY: ReportRow[] = [];

// ── Research ────────────────────────────────────────────────────────────────

export const RESEARCH_CONFIG: ResearchConfig = {
  mode: "searxng",
  searxng_url: "http://searxng:8080",
  warning: null,
  uses_mock_web: false,
  roles: ["planner", "searcher", "writer"],
  effort_presets: ["quick", "standard", "deep", "exhaustive"],
  default_model: "claude-sonnet-5",
  provider: "anthropic",
};

export const RESEARCH_ARTIFACTS: ResearchArtifact[] = [
  { id: 412, topic: "AMD ahead of the MI400 launch", symbol: "AMD", depth: "deep" },
  { id: 411, topic: "Is the regional-bank credit stress real?", symbol: null, depth: "exhaustive" },
  { id: 410, topic: "NVDA — post-earnings drift setup", symbol: "NVDA", depth: "standard" },
  { id: 409, topic: "Uranium supply squeeze — who benefits", symbol: null, depth: "deep" },
  { id: 408, topic: "BRK.B cash pile vs buyback pace", symbol: "BRK.B", depth: "quick" },
].map((r, i) => ({
  id: r.id,
  topic: r.topic,
  symbol: r.symbol,
  markdown: `# ${r.topic}\n\nThe short version: two of the four angles agree, the other two are silent.\n\n- **Flow.** ${r.symbol ?? "The complex"} saw above-baseline premium on the call side for three sessions.\n- **Fundamentals.** Revenue growth is decelerating, but margins held.\n- **Tape.** Price is above the 200-day and holding the prior-week low.\n`,
  status: "complete",
  depth: r.depth as ResearchArtifact["depth"],
  report: null,
  html: null,
  sources: Array.from({ length: 6 + i }, (_, s) => ({
    url: `https://example.com/source-${s}`,
    title: `Source ${s + 1}`,
  })),
  created_at: new Date(
    new Date(NOW_ISO).getTime() - (i + 1) * 9 * 3_600_000,
  ).toISOString(),
  updated_at: new Date(
    new Date(NOW_ISO).getTime() - (i + 1) * 9 * 3_600_000 + 600_000,
  ).toISOString(),
}));

export const RESEARCH_JOBS: ResearchJobSummary[] = [
  {
    job_id: "job_9a12",
    topic: "AMD ahead of the MI400 launch",
    symbol: "AMD",
    depth: "deep",
    recipe: "single_stock",
    status: "complete",
    created_at: minutesBefore(180),
    started_at: minutesBefore(179),
    finished_at: minutesBefore(146),
    artifact_id: 412,
  },
  {
    job_id: "job_9a11",
    topic: "Is the regional-bank credit stress real?",
    symbol: null,
    depth: "exhaustive",
    recipe: "setup_scan",
    status: "failed",
    created_at: minutesBefore(600),
    started_at: minutesBefore(599),
    finished_at: minutesBefore(560),
    artifact_id: null,
  },
];

// ── Journal ─────────────────────────────────────────────────────────────────

const DECISION_SYMBOLS = ["AAPL", "NVDA", "BRK.B", "ZVZZTW", "AMD", "SPY", "TSLA", "PLTR", "COIN", "SOFI", "MSFT", "GOOGL"];

/** Rough last price per symbol so idea entry/stop/target levels read as real
 *  numbers for that ticker instead of every row landing in the same range. */
const SPOT: Record<string, number> = {
  AAPL: 228.71, NVDA: 171.4, "BRK.B": 517.86, ZVZZTW: 2.87, AMD: 189.77,
  SPY: 624.02, TSLA: 318.05, PLTR: 38.14, COIN: 271.33, SOFI: 15.91,
  MSFT: 489.22, GOOGL: 206.4, SMCI: 51.28, MARA: 16.42,
};

const THESES = [
  "RVOL expansion into a prior-day-high retest with the trend filter on; stop under the session POC.",
  "Gap-and-go that held VWAP all morning — adding on the first higher low.",
  "Failed breakdown at the prior-week low; the sellers never followed through.",
  "Post-earnings drift entry at exactly td=10, sized at 10% of the sleeve.",
  "Call premium ran 2.4× baseline for three sessions while price went nowhere.",
  "Mean reversion off a 2σ stretch from the 20-day, fading into the close.",
];

export const JOURNAL_DECISIONS: JournalDecisionsResult = {
  count: 24,
  decisions: Array.from({ length: 24 }, (_, i) => {
    const rand = lcg(700 + i);
    const won = rand() > 0.44;
    return {
      proposal_id: `prop_${String(i + 1).padStart(4, "0")}`,
      symbol: DECISION_SYMBOLS[i % DECISION_SYMBOLS.length],
      side: i % 4 === 3 ? "sell" : "buy",
      strategy_id: i % 3 === 0 ? "momentum_v3" : "mean_revert_v2",
      source: "decision_engine",
      thesis: THESES[i % THESES.length],
      win_prob: round2(0.44 + rand() * 0.3),
      ev_pct: round2((rand() - 0.4) * 4),
      decision: i % 7 === 6 ? "rejected" : "approved",
      outcome: won ? "win" : "loss",
      realized_pnl: money(round2((won ? 1 : -1) * (180 + rand() * 1600))),
      r_multiple: round2((won ? 1 : -1) * (0.4 + rand() * 2.1)),
      holding_days: 1 + Math.floor(rand() * 14),
      created_at: new Date(
        new Date(NOW_ISO).getTime() - (i + 1) * 19 * 3_600_000,
      ).toISOString(),
    };
  }),
  stats: {
    overall: {
      n: 24,
      wins: 13,
      losses: 11,
      win_rate: 0.542,
      avg_r_multiple: 0.31,
      net_realized_pnl: "4188.22",
      note: "24 resolved calls — below the 50-sample calibration floor",
    },
    by_strategy: [
      { strategy_id: "momentum_v3", n: 9, wins: 6, losses: 3, win_rate: 0.667, net_realized_pnl: "3102.40", avg_r_multiple: 0.71 },
      { strategy_id: "mean_revert_v2", n: 15, wins: 7, losses: 8, win_rate: 0.467, net_realized_pnl: "1085.82", avg_r_multiple: 0.07 },
    ],
  },
};

export const JOURNAL_DECISIONS_EMPTY: JournalDecisionsResult = {
  count: 0,
  decisions: [],
  stats: {
    overall: {
      n: 0,
      wins: 0,
      losses: 0,
      win_rate: null,
      avg_r_multiple: null,
      net_realized_pnl: null,
      note: null,
    },
    by_strategy: [],
  },
};

export const JOURNAL_CALIBRATION: CalibrationResult = {
  n_resolved: 24,
  min_samples: 50,
  by_strategy: [
    { key: "momentum_v3", label: "momentum v3", n: 9, wins: 6, losses: 3, scratches: 0, predicted_win: 0.61, realized_win: 0.667, ci_low: 0.35, ci_high: 0.88, drift: 0.057, avg_r_multiple: 0.71, verdict: "insufficient_data" },
    { key: "mean_revert_v2", label: "mean reversion v2", n: 15, wins: 7, losses: 8, scratches: 0, predicted_win: 0.58, realized_win: 0.467, ci_low: 0.25, ci_high: 0.7, drift: -0.113, avg_r_multiple: 0.07, verdict: "insufficient_data" },
  ],
  by_bucket: [
    { key: "0.5-0.6", label: "50–60%", n: 14, wins: 7, losses: 7, scratches: 0, predicted_win: 0.55, realized_win: 0.5, ci_low: 0.27, ci_high: 0.73, drift: -0.05, avg_r_multiple: 0.11, verdict: "insufficient_data" },
    { key: "0.6-0.7", label: "60–70%", n: 10, wins: 6, losses: 4, scratches: 0, predicted_win: 0.64, realized_win: 0.6, ci_low: 0.31, ci_high: 0.83, drift: -0.04, avg_r_multiple: 0.52, verdict: "insufficient_data" },
  ],
};

export const MY_TRADES: MyTradesResult = {
  count: 18,
  trades: Array.from({ length: 18 }, (_, i) => {
    const rand = lcg(300 + i);
    const pnl = round2((rand() - 0.42) * 2200);
    return {
      symbol: DECISION_SYMBOLS[i % DECISION_SYMBOLS.length],
      asset_class: i % 5 === 0 ? "option" : "equity",
      side: "long",
      quantity: String(10 + Math.floor(rand() * 300)),
      open_date: daysBefore(20 + i),
      close_date: daysBefore(4 + i),
      hold_days: 16,
      pnl: money(pnl),
      outcome: pnl >= 0 ? "win" : "loss",
      pnl_basis: "net_cash",
    };
  }),
};

export const MY_TRADES_EMPTY: MyTradesResult = { count: 0, trades: [] };

export const MY_TRADES_SUMMARY: MyTradesSummary = {
  net_cash_realized: {
    realized_closed: "12844.19",
    closed_win_rate: 0.556,
    reliability: {
      net_cash: "source of truth",
      fifo: "approximate — unreliable on this export",
    },
  },
  behavioral: {
    trade_count: 18,
    date_range: { from: daysBefore(38), to: daysBefore(4) },
    asset_mix: {
      equity_trades: 14,
      option_trades: 4,
      equity_pct: 0.778,
      option_pct: 0.222,
    },
    top_symbols: [
      { symbol: "AAPL", value: "4120.88", share_pct: 0.32 },
      { symbol: "NVDA", value: "-2044.10", share_pct: 0.19 },
      { symbol: "BRK.B", value: "1880.44", share_pct: 0.14 },
    ],
    same_day_flips: 2,
    buy_sell_ratio: 1.12,
  },
  honesty: "Net-cash realized is the source of truth here; FIFO is approximate.",
};

// ── Idea outcomes ───────────────────────────────────────────────────────────

const IDEA_SYMBOLS = ["AAPL", "NVDA", "BRK.B", "ZVZZTW", "AMD", "SOFI", "PLTR", "COIN", "SMCI", "MARA", "TSLA", "GOOGL"];

function ideaRow(i: number, status: IdeaOutcomeRow["status"]): IdeaOutcomeRow {
  const rand = lcg(9000 + i);
  const symbol = IDEA_SYMBOLS[i % IDEA_SYMBOLS.length];
  // Entry zone sits just under that symbol's real price level, so a row about
  // ZVZZTW shows $2-ish and one about BRK.B shows $500-ish.
  const entryLow = round2((SPOT[symbol] ?? 100) * (0.96 + rand() * 0.03));
  const resolved = status === "won" || status === "lost" || status === "expired";
  return {
    id: 1000 + i,
    artifact_id: 400 + (i % 12),
    idea_index: i % 4,
    symbol,
    as_of: daysBefore(4 + (i % 30)),
    entry_low: money(entryLow),
    entry_high: money(round2(entryLow * 1.012)),
    stop: money(round2(entryLow * 0.955)),
    targets: [money(round2(entryLow * 1.06)), money(round2(entryLow * 1.11))],
    status,
    origin: i % 3 === 0 ? "research" : "scan",
    triggered_at: status === "pending" ? null : `${daysBefore(3 + (i % 20))}T14:02:00Z`,
    resolved_at: resolved ? `${daysBefore(1 + (i % 10))}T20:00:00Z` : null,
    resolution_price: resolved ? money(round2(entryLow * (status === "won" ? 1.07 : 0.95))) : null,
    first_target_hit: status === "won" ? 1 : null,
    mfe_pct: round2(rand() * 12),
    mae_pct: round2(-rand() * 6),
    founder_verdict: null,
    verdict:
      i % 5 === 0
        ? { take_it: 4, entry_honest: 5, stop_survivable: 4, thesis_quality: 3, note: "entry zone was reachable", graded_at: `${daysBefore(2)}T18:00:00Z` }
        : null,
    notes: null,
    updated_at: NOW_ISO,
    last_price: money(round2(entryLow * 1.02)),
    last_price_at: NOW_ISO,
    expires_on: daysBefore(-(5 + (i % 10))),
    zone_distance_pct: round2((rand() - 0.5) * 6),
  };
}

const OPEN_IDEAS = Array.from({ length: 9 }, (_, i) => ideaRow(i, i % 3 === 0 ? "triggered" : "pending"));
const RESOLVED_IDEAS = Array.from({ length: 16 }, (_, i) =>
  ideaRow(20 + i, i % 3 === 0 ? "lost" : i % 5 === 4 ? "expired" : "won"),
);

export const IDEA_OUTCOMES: IdeaOutcomesResponse = {
  summary: {
    counts: { pending: 6, triggered: 3, won: 9, lost: 5, expired: 2, invalidated: 1, data_gap: 0 },
    resolved: 16,
    win_rate: 0.5625,
    grading: {
      graded: 5,
      means: { take_it: 3.8, entry_honest: 4.2, stop_survivable: 3.6, thesis_quality: 3.4 },
      mean_take_it: 3.8,
      numbers_wrong: 1,
      gate: { min_graded: 20, min_mean_take_it: 3.5, max_numbers_wrong: 2 },
      gate_pass: false,
    },
    by_origin: {
      scan: { counts: { won: 6, lost: 4 }, resolved: 10, win_rate: 0.6 },
      research: { counts: { won: 3, lost: 1, expired: 2 }, resolved: 6, win_rate: 0.5 },
    },
  },
  open: OPEN_IDEAS,
  resolved: RESOLVED_IDEAS,
};

export const IDEA_OUTCOMES_EMPTY: IdeaOutcomesResponse = {
  summary: { counts: {}, resolved: 0, win_rate: null },
  open: [],
  resolved: [],
};

export const IDEA_LEDGER: IdeaLedgerResponse = {
  total: 42,
  rows: Array.from({ length: 24 }, (_, i) => {
    const rand = lcg(6100 + i);
    const ret = round2((rand() - 0.45) * 14);
    const spy = round2((rand() - 0.5) * 3);
    return {
      id: 2000 + i,
      as_of: daysBefore(2 + i),
      updated_at: NOW_ISO,
      symbol: IDEA_SYMBOLS[i % IDEA_SYMBOLS.length],
      direction: i % 6 === 5 ? "short" : "long",
      origin: i % 3 === 0 ? "research" : "scan",
      idea_index: i % 4,
      entry_low: money(round2((SPOT[IDEA_SYMBOLS[i % IDEA_SYMBOLS.length]] ?? 100) * 0.97)),
      entry_high: money(round2((SPOT[IDEA_SYMBOLS[i % IDEA_SYMBOLS.length]] ?? 100) * 0.99)),
      stop: money(round2((SPOT[IDEA_SYMBOLS[i % IDEA_SYMBOLS.length]] ?? 100) * 0.93)),
      targets_json: "[]",
      status: i % 4 === 0 ? "lost" : i % 7 === 6 ? "pending" : "won",
      user_action: i % 5 === 0 ? "taken" : "skipped",
      ret_pct: ret,
      spy_ret_pct: spy,
      alpha_pct: round2(ret - spy),
      source_ref: `artifact:${400 + (i % 12)}`,
    };
  }),
};

export const IDEA_LEDGER_EMPTY: IdeaLedgerResponse = { rows: [], total: 0 };

// ── Fusion (desk + evidence stacks) ─────────────────────────────────────────

export const FUSION_RADAR: FusionRadarResponse = {
  now: NOW_ISO,
  sessions: 3,
  rows: IDEA_SYMBOLS.slice(0, 10).map((symbol, i) => {
    const rand = lcg(770 + i);
    const bull = round2(rand() * 9);
    const bear = round2(rand() * 9);
    return {
      symbol,
      tier: i < 3 ? "live" : i < 7 ? "poll" : "scan",
      maturity: round2(rand()),
      thesis: {
        bull_mass: bull,
        bear_mass: bear,
        net: round2(bull - bear),
        conviction: round2(rand()),
        z: round2((rand() - 0.5) * 4),
        contested: Math.abs(bull - bear) < 1.2,
      },
      trigger: { bull_mass: round2(rand() * 4), bear_mass: round2(rand() * 4) },
      last_evidence_at: minutesBefore(3 + i * 17),
      dot_count: 2 + ((i * 3) % 11),
    };
  }),
};

export const FUSION_RADAR_EMPTY: FusionRadarResponse = {
  rows: [],
  now: NOW_ISO,
  sessions: 3,
};

const EPISODES: FusionEpisode[] = IDEA_SYMBOLS.slice(0, 8).map((symbol, i) => {
  const rand = lcg(880 + i);
  return {
    id: `ep_${String(i + 1).padStart(3, "0")}`,
    symbol,
    market_day: DAY,
    opened_at: minutesBefore(300 - i * 25),
    updated_at: minutesBefore(9 + i * 4),
    anchor_dot_id: `dot_${i}_0`,
    anchor_price: money(round2(40 + rand() * 400)),
    anchor_atr14: money(round2(1 + rand() * 8)),
    anchor_direction: i % 3 === 2 ? "bear" : "bull",
    registration_called: i % 4 === 0 ? 1 : 0,
    score: round2(rand() * 7),
    direction_state: i % 3 === 2 ? "bear" : i % 5 === 4 ? "contested" : "bull",
    contested: i % 5 === 4 ? 1 : 0,
    signature: "flow+tape",
    market_shock: 0,
    dot_count: 2 + ((i * 3) % 9),
  };
});

export const FUSION_EPISODES: FusionEpisodesResponse = {
  episodes: EPISODES,
  day: DAY,
};

export const FUSION_EPISODES_EMPTY: FusionEpisodesResponse = {
  episodes: [],
  day: DAY,
};

export function fusionEpisode(id: string) {
  const episode = EPISODES.find((e) => e.id === id) ?? EPISODES[0];
  return {
    episode,
    dots: Array.from({ length: episode.dot_count ?? 3 }, (_, i) => ({
      id: `dot_${episode.id}_${i}`,
      episode_id: episode.id,
      source_store: i % 2 === 0 ? "options_flow" : "equity_tape",
      source_event_id: `src_${i}`,
      source_ingest_seq: i,
      symbol: episode.symbol,
      ts: minutesBefore(120 - i * 11),
      component: i % 2 === 0 ? "flow" : "tape",
      actor_group: i % 3 === 0 ? "institutional" : "unknown",
      dot_kind: i % 2 === 0 ? "sweep" : "range_break",
      direction: i % 4 === 3 ? "bear" : "bull",
      raw_value: money(round2(i * 1.7 + 2)),
      points: 1 + (i % 3),
      detail: { note: "fixture dot" },
      trigger_price: episode.anchor_price,
      prior_close_atr14: episode.anchor_atr14,
      stamped: 1,
      created_at: minutesBefore(120 - i * 11),
    })),
  };
}

export function fusionDossier(id: string): FusionDossier {
  const episode = EPISODES.find((e) => e.id === id) ?? EPISODES[0];
  return {
    episode_id: episode.id,
    symbol: episode.symbol,
    market_day: DAY,
    summary:
      "Two angles agree (flow + tape), one is silent (fundamentals), none contradict.",
    claims: [
      { claim_id: "c1", angle: "flow", statement: "Call premium ran 2.4× the 20-day baseline", value: 2.4, ts: minutesBefore(85), direction: "bull", fresh: true, contradicts: [] },
      { claim_id: "c2", angle: "tape", statement: "Held the prior-day high on the retest", value: null, ts: minutesBefore(52), direction: "bull", fresh: true, contradicts: [] },
      { claim_id: "c3", angle: "fundamentals", statement: "No filing inside the window", value: null, ts: minutesBefore(600), direction: "neutral", fresh: false, contradicts: [] },
    ],
  };
}

// ── Ticker info / news / filings ────────────────────────────────────────────

export function tickerInfo(symbol: string): TickerInfo {
  return {
    symbol,
    profile: {
      name: symbol === "SPY" ? "SPDR S&P 500 ETF Trust" : `${symbol} Holdings Inc.`,
      ticker: symbol,
      exchange: "NASDAQ NMS - GLOBAL MARKET",
      finnhubIndustry: "Technology",
      marketCapitalization: 3_412_000,
      logo: "",
      weburl: "https://example.com",
      ipo: "1998-11-19",
      shareOutstanding: 14_920,
      country: "US",
      currency: "USD",
    },
    metrics: {
      pe_ttm: 34.1,
      eps_ttm: 6.71,
      week_52_high: 268.4,
      week_52_low: 164.2,
      beta: 1.18,
      div_yield: 0.44,
      gross_margin: 46.2,
      net_margin: 25.8,
      market_cap: 3_412_000,
      ps_ttm: 8.4,
      roe_ttm: 147.2,
      revenue_growth_yoy: 8.1,
    },
    news: NEWS_ITEMS.slice(0, 6).map(({ symbol: _s, ...rest }) => rest),
    facts: {
      symbol,
      quote: { price: 228.71, change_pct: 1.22, market_session: "regular" },
      rvol: { value: 1.42, state: "live" },
      session_nodes: { poc: 226.4, pdh: 229.88, pdl: 224.12, "52wh": 268.4, "52wl": 164.2, session_date: daysBefore(1) },
      technicals: { sma50: 221.8, sma200: 201.4, vs_sma200_pct: 13.6, rsi14: 61.2, atr14: 4.8, dist_from_20d_high_pct: -1.4, trend: "up" },
      fundamentals: { pe: 34.1, eps: 6.71, div_yield: 0.44, market_cap: 3_412_000 },
      earnings: { last: daysBefore(31), next: daysBefore(-58) },
      decision_score: { calibration: "unproven", n_samples: 22 },
      active_alerts: [],
    },
    short_interest: {
      short_shares: 118_400_000,
      days_to_cover: 1.4,
      change_pct: -3.2,
      avg_daily_volume: 84_200_000,
      settlement_date: daysBefore(12),
    },
  };
}

const HEADLINES = [
  ["NVDA", "Nvidia slips as a hyperscaler trims its 2027 order book", "Reuters"],
  ["AAPL", "Apple's services line grows 14% — the quarter's real story", "Bloomberg"],
  ["AMD", "AMD lands a second sovereign-cloud win in Europe", "CNBC"],
  ["BRK.B", "Berkshire's cash pile hits another record as buybacks pause", "WSJ"],
  ["TSLA", "Tesla cuts European lease pricing for the third time this year", "Reuters"],
  ["SPY", "Stocks close mixed as the term spread flattens again", "AP"],
  ["ZVZZTW", "A warrant nobody trades just printed 900k shares", "Test Feed"],
  ["COIN", "Coinbase institutional volumes fall for a second month", "The Block"],
  ["SOFI", "SoFi's deposit growth beats — but the loan book is the question", "Barron's"],
  ["MSFT", "Microsoft reorganizes its AI infrastructure group, again", "The Information"],
  ["PLTR", "Palantir's government backlog is growing faster than revenue", "Bloomberg"],
  ["GOOGL", "Alphabet's cloud margin story survives another quarter", "CNBC"],
];

const NEWS_ITEMS: WatchlistNewsItem[] = HEADLINES.map(([symbol, headline, source], i) => ({
  symbol,
  headline,
  source,
  url: `https://example.com/news/${i}`,
  datetime: minutesBefore(12 + i * 47),
  summary:
    "One-paragraph summary that is deliberately long enough to wrap onto a second line inside a narrow widget frame, because that is exactly where card layouts break.",
  image: "",
}));

export const WATCHLIST_NEWS = NEWS_ITEMS;
export const WATCHLIST_NEWS_EMPTY: WatchlistNewsItem[] = [];

export const FILINGS: FilingEvent[] = [
  { kind: "insider_buy", symbol: "SOFI", person: "Anthony Noto", role: "CEO", side: "buy", value: 1_204_000 },
  { kind: "stake_13d", symbol: "SMCI", person: "Elliott Management", role: "13D filer", side: "buy", value: 84_200_000 },
  { kind: "congress_trade", symbol: "NVDA", person: "Rep. J. Doe", role: "House", side: "sell", value: 250_000 },
  { kind: "insider_buy", symbol: "BRK.B", person: "Gregory Abel", role: "Vice Chairman", side: "buy", value: 2_880_000 },
  { kind: "congress_trade", symbol: "AMD", person: "Sen. A. Roe", role: "Senate", side: "buy", value: 100_000 },
  { kind: "insider_buy", symbol: "ZVZZTW", person: "Test Officer", role: "CFO", side: "buy", value: 41_000 },
].map((f, i) => ({
  id: 500 + i,
  uid: `filing_${i}`,
  kind: f.kind as FilingEvent["kind"],
  symbol: f.symbol,
  person: f.person,
  role: f.role,
  side: f.side as FilingEvent["side"],
  value_usd: f.value,
  value_label: `$${(f.value / 1_000_000).toFixed(2)}M`,
  filed_at: minutesBefore(90 + i * 220),
  traded_at: daysBefore(2 + i),
  url: `https://www.sec.gov/example/${i}`,
  detail: {},
}));

export const FILINGS_EMPTY: FilingEvent[] = [];

export { et, NOW_ISO };
