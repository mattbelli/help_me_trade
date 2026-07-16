"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchIndexPressure,
  type IndexPressureResult,
  type IndexPressureRow,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

// The window is fixed (09:30-10:00 ET) and rows are final by 10:05, so a lazy
// poll is plenty; during the window itself the minute rows land every ~60s.
const POLL_MS = 10_000;

function fmtNet(v: number): string {
  const a = Math.abs(v);
  const s =
    a >= 1_000_000 ? `$${(a / 1_000_000).toFixed(1)}M` : `$${(a / 1_000).toFixed(0)}K`;
  return v < 0 ? `-${s}` : `+${s}`;
}

/** Bull/bear proportion bar — unclassified stays visually absent (it is unknown,
 * not neutral), so the bar length also communicates how much was classifiable. */
function SplitBar({ row }: { row: IndexPressureRow }) {
  const gross = row.bull_usd + row.bear_usd + row.unclassified_usd;
  if (gross <= 0) return null;
  const bullPct = (row.bull_usd / gross) * 100;
  const bearPct = (row.bear_usd / gross) * 100;
  return (
    <div className="flex h-[3px] w-full overflow-hidden rounded-full bg-[var(--bg-inset)]">
      <div className="bg-[var(--pos)]" style={{ width: `${bullPct}%` }} />
      <div className="ml-auto bg-[var(--neg)]" style={{ width: `${bearPct}%` }} />
    </div>
  );
}

export function IndexPressureWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const windowKey = config.pressureWindow ?? "open";
  const { publish } = usePublishSymbol(channel);
  const [data, setData] = useState<IndexPressureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchIndexPressure(windowKey));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, [windowKey]);

  useEffect(() => {
    void load();
    timerRef.current = setInterval(() => void load(), POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [load]);

  const windowToggle = (
    <span className="ml-auto flex gap-2">
      {(["open", "close"] as const).map((w) => (
        <button
          key={w}
          type="button"
          onClick={() => onConfigChange({ pressureWindow: w })}
          className={cn(
            "lowercase",
            w === windowKey
              ? "text-[var(--text-primary)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]",
          )}
        >
          {w}
        </button>
      ))}
    </span>
  );

  if (error) return <WidgetError message={error} onRetry={() => void load()} />;
  if (!data) return <WidgetLoading />;
  if (data.rows.length === 0) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1">
          <WidgetEmpty
            message={`no ${windowKey}-window flow yet — gauge fills ${
              windowKey === "open" ? "9:30–10:00" : "15:30–16:00"
            } ET`}
          />
        </div>
        <div className="flex border-t border-[var(--border-subtle)] px-3 py-[5px] text-[10px] text-[var(--text-muted)]">
          <span>net premium (bull − bear)</span>
          {windowToggle}
        </div>
      </div>
    );
  }

  const calibrated = data.baseline_days >= 20;
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {data.rows.map((row) => (
          <div
            key={row.underlying}
            className="grid grid-cols-[64px_84px_1fr_44px] items-center gap-x-3 px-3 py-[7px]"
          >
            <button
              type="button"
              onClick={() => publish(row.underlying)}
              className="text-left font-mono text-[13px] font-semibold text-[var(--text-primary)] hover:underline"
            >
              {row.underlying}
            </button>
            <span
              className={cn(
                "font-mono text-[12px] tabular-nums",
                row.net_usd >= 0 ? "text-[var(--pos)]" : "text-[var(--neg)]",
              )}
            >
              {fmtNet(row.net_usd)}
            </span>
            <SplitBar row={row} />
            <span className="text-right font-mono text-[10px] tabular-nums text-[var(--text-muted)]">
              {Math.round(row.classified_frac * 100)}%
            </span>
          </div>
        ))}
      </div>
      <div className="flex border-t border-[var(--border-subtle)] px-3 py-[5px] text-[10px] text-[var(--text-muted)]">
        <span>
          {data.window_et} ET · net premium (bull − bear) · % = side-classified
          {calibrated ? null : (
            <span> · uncalibrated ({data.baseline_days}d of baseline)</span>
          )}
        </span>
        {windowToggle}
      </div>
    </div>
  );
}
