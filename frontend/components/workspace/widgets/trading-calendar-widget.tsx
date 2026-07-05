"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { WidgetError } from "@/components/workspace/widget-states";
import {
  dailyRealizedAmount,
  dailyRealizedIsCashFlowFallback,
  fetchPerformanceDashboard,
  type PerformanceDashboard,
} from "@/lib/performance-api";
import { formatTradeMoney } from "@/lib/trades-api";
import { cn } from "@/lib/utils";
import { useDashboardPrefs } from "@/lib/workspace/use-dashboard-prefs";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type DayCell = {
  key: string;
  date: string;
  dayNum: number;
  inMonth: boolean;
  net: number;
  count: number;
  hasData: boolean;
  cashFlowFallback?: boolean;
};

type WeekRow = {
  key: string;
  cells: DayCell[];
  total: number;
};

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

// Local-date "YYYY-MM-DD" — never toISOString(), which shifts to UTC and can
// drift the day-of-month near midnight.
function toDateKey(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

function mondayIndex(d: Date): number {
  // getDay(): 0=Sun..6=Sat -> shift so 0=Mon..6=Sun
  return (d.getDay() + 6) % 7;
}

function formatCompact(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  const abs = Math.abs(value);
  let body: string;
  if (abs >= 1_000_000) body = `$${(abs / 1_000_000).toFixed(1)}M`;
  else if (abs >= 10_000) body = `$${Math.round(abs / 1000)}k`;
  else if (abs >= 1000) body = `$${(abs / 1000).toFixed(1)}k`;
  else body = `$${Math.round(abs)}`;
  return `${sign}${body}`;
}

function pnlClass(value: number): string {
  return value >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]";
}

function buildWeeks(
  year: number,
  month: number,
  dateMap: Map<string, { net: number; count: number; cashFlowFallback?: boolean }>,
): WeekRow[] {
  const firstOfMonth = new Date(year, month, 1);
  const lastOfMonth = new Date(year, month + 1, 0);
  const gridStart = addDays(firstOfMonth, -mondayIndex(firstOfMonth));
  const gridEnd = addDays(lastOfMonth, 6 - mondayIndex(lastOfMonth));

  const weeks: WeekRow[] = [];
  let cursor = gridStart;
  while (cursor <= gridEnd) {
    const cells: DayCell[] = [];
    for (let i = 0; i < 7; i++) {
      const dateKey = toDateKey(cursor);
      const inMonth = cursor.getMonth() === month;
      const stat = dateMap.get(dateKey);
      cells.push({
        key: dateKey,
        date: dateKey,
        dayNum: cursor.getDate(),
        inMonth,
        net: stat?.net ?? 0,
        count: stat?.count ?? 0,
        hasData: Boolean(stat),
        cashFlowFallback: stat?.cashFlowFallback,
      });
      cursor = addDays(cursor, 1);
    }
    const total = cells.reduce((sum, c) => sum + c.net, 0);
    weeks.push({ key: cells[0].key, cells, total });
  }
  return weeks;
}

function DayTile({
  cell,
  selected,
  maxAbs,
  onClick,
}: {
  cell: DayCell;
  selected: boolean;
  maxAbs: number;
  onClick: () => void;
}) {
  if (!cell.inMonth) {
    return (
      <div className="flex min-h-[38px] flex-col items-center justify-start rounded-[3px] px-1 py-1">
        <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)] opacity-40">
          {cell.dayNum}
        </span>
      </div>
    );
  }

  const positive = cell.net >= 0;
  const intensity = cell.hasData
    ? Math.min(Math.abs(cell.net) / maxAbs, 1)
    : 0;

  return (
    <button
      type="button"
      onClick={onClick}
      title={
        cell.hasData
          ? `${cell.date}: ${formatTradeMoney(cell.net)}${cell.cashFlowFallback ? " (cash flow)" : ""} (${cell.count} event${cell.count === 1 ? "" : "s"})`
          : cell.date
      }
      className="relative flex min-h-[38px] flex-col items-center justify-start overflow-hidden rounded-[3px] px-1 py-1 text-left transition-colors hover:bg-[var(--bg-hover)]"
      style={
        selected ? { boxShadow: "inset 0 0 0 2px var(--c-accent)" } : undefined
      }
    >
      {cell.hasData ? (
        <span
          aria-hidden
          className="absolute inset-0"
          style={{
            backgroundColor: positive ? "var(--c-green-bg)" : "var(--c-red)",
            opacity: 0.05 + intensity * 0.25,
          }}
        />
      ) : null}
      <span className="relative z-10 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] tabular-nums">
        {cell.dayNum}
      </span>
      <span
        className={cn(
          "relative z-10 mt-0.5 font-mono text-[length:var(--fs-table)] font-semibold tabular-nums",
          cell.hasData ? pnlClass(cell.net) : "text-[var(--fg-faint)]",
        )}
      >
        {cell.hasData ? formatCompact(cell.net) : ""}
      </span>
    </button>
  );
}

function RollupTile({ total, muted }: { total: number; muted?: boolean }) {
  return (
    <div className="flex min-h-[38px] flex-col items-center justify-center rounded-[3px] border-l border-[var(--border)] bg-[var(--bg-inset)] px-1 py-1">
      <span
        className={cn(
          "font-mono text-[length:var(--fs-table)] font-semibold tabular-nums",
          muted && total === 0 ? "text-[var(--fg-faint)]" : pnlClass(total),
        )}
      >
        {muted && total === 0 ? "—" : formatCompact(total)}
      </span>
    </div>
  );
}

export function TradingCalendarWidget() {
  const now = new Date();
  const [cursor, setCursor] = useState({
    year: now.getFullYear(),
    month: now.getMonth(),
  });
  const [dashboard, setDashboard] = useState<PerformanceDashboard | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const { selectedAccountId } = useDashboardPrefs();

  const isCurrentMonth =
    cursor.year === now.getFullYear() && cursor.month === now.getMonth();

  useEffect(() => {
    setSelectedDate(null);
  }, [cursor.year, cursor.month, selectedAccountId]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const monthStart = toDateKey(new Date(cursor.year, cursor.month, 1));
        const monthEnd = toDateKey(
          new Date(cursor.year, cursor.month + 1, 0),
        );
        const data = await fetchPerformanceDashboard({
          account_id: selectedAccountId,
          period: "custom",
          from_date: monthStart,
          to_date: monthEnd,
        });
        if (cancelled) return;
        setDashboard(data);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load calendar",
        );
        setDashboard(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [cursor.year, cursor.month, selectedAccountId]);

  const dateMap = useMemo(() => {
    const map = new Map<
      string,
      { net: number; count: number; cashFlowFallback?: boolean }
    >();
    if (!dashboard) return map;
    for (const day of dashboard.daily_realized) {
      map.set(day.date, {
        net: dailyRealizedAmount(day),
        count: day.event_count,
        cashFlowFallback: dailyRealizedIsCashFlowFallback(day),
      });
    }
    return map;
  }, [dashboard]);

  const weeks = useMemo(
    () => buildWeeks(cursor.year, cursor.month, dateMap),
    [cursor.year, cursor.month, dateMap],
  );

  const monthTotal = useMemo(
    () => Array.from(dateMap.values()).reduce((sum, d) => sum + d.net, 0),
    [dateMap],
  );

  const maxAbs = useMemo(() => {
    const values = Array.from(dateMap.values()).map((d) => Math.abs(d.net));
    return Math.max(...values, 1);
  }, [dateMap]);

  const dowTotals = useMemo(() => {
    const totals = new Array(7).fill(0) as number[];
    for (const week of weeks) {
      week.cells.forEach((cell, i) => {
        if (cell.inMonth) totals[i] += cell.net;
      });
    }
    return totals;
  }, [weeks]);

  const monthLabel = new Date(cursor.year, cursor.month, 1).toLocaleDateString(
    undefined,
    { month: "long", year: "numeric" },
  );

  const selectedDetail = useMemo(() => {
    if (!selectedDate || !dashboard) return null;
    const stat = dateMap.get(selectedDate);
    const symbols = Array.from(
      new Set(
        dashboard.round_trips
          .filter((rt) => rt.close_date === selectedDate)
          .map((rt) => rt.symbol),
      ),
    );
    return {
      date: selectedDate,
      net: stat?.net ?? 0,
      count: stat?.count ?? 0,
      symbols,
    };
  }, [selectedDate, dashboard, dateMap]);

  const shiftMonth = (delta: number) => {
    setCursor((prev) => {
      const d = new Date(prev.year, prev.month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });
  };

  const resetToday = () => {
    setCursor({ year: now.getFullYear(), month: now.getMonth() });
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-3 py-2">
        <button
          type="button"
          onClick={() => shiftMonth(-1)}
          className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          title="Previous month"
        >
          <ChevronLeft className="size-3.5" />
        </button>
        <button
          type="button"
          onClick={() => shiftMonth(1)}
          className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          title="Next month"
        >
          <ChevronRight className="size-3.5" />
        </button>
        <span className="font-mono text-[length:var(--fs-title)] font-semibold text-foreground">
          {monthLabel}
        </span>
        {!isCurrentMonth ? (
          <button
            type="button"
            onClick={resetToday}
            className="rounded px-1.5 py-0.5 font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          >
            Today
          </button>
        ) : null}
        <span className="ml-auto font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
          Month P/L
        </span>
        <span
          className={cn(
            "font-mono text-[length:var(--fs-title)] font-semibold tabular-nums",
            pnlClass(monthTotal),
          )}
        >
          {formatTradeMoney(monthTotal)}
        </span>
      </div>

      {selectedDetail ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--bg-inset)] px-3 py-1.5">
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            {selectedDetail.date}
          </span>
          <span
            className={cn(
              "font-mono text-[length:var(--fs-body)] font-semibold tabular-nums",
              pnlClass(selectedDetail.net),
            )}
          >
            {formatTradeMoney(selectedDetail.net)}
          </span>
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {selectedDetail.count} close{selectedDetail.count === 1 ? "" : "s"}
          </span>
          {selectedDetail.symbols.length > 0 ? (
            <span className="truncate font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
              {selectedDetail.symbols.join(", ")}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto p-2 scroll-shadow-y">
        {error ? (
          <WidgetError message={error} className="p-2" />
        ) : null}
        {loading && !dashboard ? (
          <div className="grid grid-cols-4 gap-1.5">
            {Array.from({ length: 24 }).map((_, i) => (
              <Skeleton key={i} className="h-9 rounded-[3px]" />
            ))}
          </div>
        ) : (
          <div
            className="grid gap-[3px]"
            style={{
              gridTemplateColumns: "repeat(7, minmax(0, 1fr)) minmax(40px, 0.85fr)",
            }}
          >
            {WEEKDAY_LABELS.map((label) => (
              <div
                key={`hdr-${label}`}
                className="px-1 py-0.5 text-center font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase"
              >
                {label}
              </div>
            ))}
            <div className="px-1 py-0.5 text-center font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
              Week
            </div>

            {weeks.flatMap((week) => [
              ...week.cells.map((cell) => (
                <DayTile
                  key={cell.key}
                  cell={cell}
                  selected={cell.inMonth && cell.date === selectedDate}
                  maxAbs={maxAbs}
                  onClick={() =>
                    setSelectedDate((prev) =>
                      prev === cell.date ? null : cell.date,
                    )
                  }
                />
              )),
              <RollupTile key={`${week.key}-total`} total={week.total} />,
            ])}

            {dowTotals.map((total, i) => (
              <RollupTile key={`dow-${i}`} total={total} muted />
            ))}
            <div className="min-h-[38px] rounded-[3px] border-l border-[var(--border)] bg-[var(--bg-inset)]" />
          </div>
        )}
      </div>
    </div>
  );
}
