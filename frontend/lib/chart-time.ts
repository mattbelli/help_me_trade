import { TickMarkType } from "lightweight-charts";

export type ChartTimeframe = {
  label: string;
  value: string;
  days: number;
  intraday: boolean;
};

export const CHART_TIMEFRAMES: ChartTimeframe[] = [
  { label: "1m", value: "1Min", days: 10, intraday: true },
  { label: "5m", value: "5Min", days: 30, intraday: true },
  { label: "15m", value: "15Min", days: 60, intraday: true },
  { label: "1H", value: "1Hour", days: 180, intraday: true },
  { label: "1D", value: "1Day", days: 365, intraday: false },
];

const ET = "America/New_York";

export function isDailyTimeframe(tf: string) {
  return tf === "1Day" || tf === "1D";
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

// lightweight-charts decides the tick DENSITY from the zoom level and tells us
// the level of each tick via `tickMarkType` — so the axis naturally fades from
// times → days → months → years as you zoom out. We just format each level.
// (For intraday data, day-boundary ticks fall on the first bar of the session,
// i.e. ≈market open, so the date appears there.)
export function formatTickMark(
  time: number | { year: number; month: number; day: number },
  tickMarkType: TickMarkType,
): string {
  const date = timeToDate(time);
  switch (tickMarkType) {
    case TickMarkType.Year:
      return date.toLocaleDateString("en-US", { timeZone: ET, year: "numeric" });
    case TickMarkType.Month:
      return date.toLocaleDateString("en-US", { timeZone: ET, month: "short" });
    case TickMarkType.DayOfMonth:
      return date.toLocaleDateString("en-US", {
        timeZone: ET,
        month: "short",
        day: "numeric",
      });
    case TickMarkType.Time:
      return date.toLocaleTimeString("en-US", {
        timeZone: ET,
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      });
    case TickMarkType.TimeWithSeconds:
      return date.toLocaleTimeString("en-US", {
        timeZone: ET,
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      });
    default:
      return "";
  }
}

export function formatVolume(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
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
