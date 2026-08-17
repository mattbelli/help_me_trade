export type ChartTimeframe = {
  label: string;
  value: string;
  intraday: boolean;
};

// No per-timeframe history depth here: the chart loads the newest window and
// backfills older bars as you pan left (card #326), so "how far back" is
// whatever you scroll to.
export const CHART_TIMEFRAMES: ChartTimeframe[] = [
  { label: "1m", value: "1Min", intraday: true },
  { label: "5m", value: "5Min", intraday: true },
  { label: "15m", value: "15Min", intraday: true },
  { label: "1H", value: "1Hour", intraday: true },
  { label: "4H", value: "4Hour", intraday: true },
  { label: "1D", value: "1Day", intraday: false },
  { label: "1W", value: "1Week", intraday: false },
  { label: "1M", value: "1Month", intraday: false },
];

const ET = "America/New_York";

export function isDailyTimeframe(tf: string) {
  return (
    tf === "1Day" ||
    tf === "1D" ||
    tf === "1Week" ||
    tf === "1Month"
  );
}

export function timeframeSeconds(tf: string): number {
  const m = /^(\d+)(Min|Hour|Day|Week|Month)$/.exec(tf);
  if (!m) return 0;
  const n = Number(m[1]);
  if (m[2] === "Min") return n * 60;
  if (m[2] === "Hour") return n * 3600;
  if (m[2] === "Week") return n * 604800;
  if (m[2] === "Month") return n * 2592000;
  return n * 86400; // Day
}

export function toChartTime(iso: string, timeframe: string): number | { year: number; month: number; day: number } {
  if (isDailyTimeframe(timeframe)) {
    const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
    return { year, month, day };
  }
  return Math.floor(new Date(iso).getTime() / 1000);
}

function timeToDate(time: number | { year: number; month: number; day: number }): Date {
  if (typeof time === "number") {
    return new Date(time * 1000);
  }
  return new Date(Date.UTC(time.year, time.month - 1, time.day));
}

export function formatCrosshairTime(
  time: number | { year: number; month: number; day: number },
  intraday: boolean,
): string {
  const date = timeToDate(time);
  if (intraday) {
    return date.toLocaleString("en-US", {
      timeZone: ET,
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  }
  return date.toLocaleDateString("en-US", {
    timeZone: ET,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatVolume(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

export type BarSession = "pre" | "regular" | "post" | "closed";

// Classify an intraday bar by the ET wall-clock of its bucket START (mirrors the
// backend brokers/market_clock.py windows). A bar that straddles a boundary belongs
// to the session its start falls in — so the 09:30 open bar is "regular" and the
// 15:xx→16:00 boundary bar's session is decided by where it opened. Weekend/holiday
// bars don't exist in Schwab's ext data, so "closed" is effectively unreachable here.
// One shared formatter + a per-timestamp memo: the session-bands primitive
// classifies EVERY bar on EVERY chart repaint (each live tick, pan, crosshair
// move). Constructing an Intl.DateTimeFormat per call cost ~0.2ms × thousands
// of bars × tick rate — it pinned the main thread at ~1 FPS across the whole
// app the first time live ticks flowed over ext-hours charts (2026-07-07).
const ET_HM_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: ET,
  hour: "numeric",
  minute: "2-digit",
  hour12: false,
});
const sessionMemo = new Map<number, BarSession>();

export function intradayBarSession(unixSec: number): BarSession {
  const memo = sessionMemo.get(unixSec);
  if (memo !== undefined) return memo;
  const parts = ET_HM_FMT.formatToParts(new Date(unixSec * 1000));
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  const mins = (hour % 24) * 60 + minute;
  let session: BarSession;
  if (mins >= 4 * 60 && mins < 9 * 60 + 30) session = "pre";
  else if (mins >= 9 * 60 + 30 && mins < 16 * 60) session = "regular";
  else if (mins >= 16 * 60 && mins < 20 * 60) session = "post";
  else session = "closed";
  if (sessionMemo.size > 50_000) sessionMemo.clear();
  sessionMemo.set(unixSec, session);
  return session;
}

export function sessionLabel(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ET,
    hour: "numeric",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  }).formatToParts(now);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  const mins = hour * 60 + minute;

  if (mins >= 4 * 60 && mins < 9 * 60 + 30) return "Pre-market";
  if (mins >= 9 * 60 + 30 && mins < 16 * 60) return "Regular";
  if (mins >= 16 * 60 && mins < 20 * 60) return "After-hours";
  return "Closed";
}
