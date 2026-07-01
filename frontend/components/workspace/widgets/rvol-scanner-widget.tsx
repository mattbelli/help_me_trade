"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import { fetchRvolScanner, type RvolScannerResult } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 25_000;

const SESSION_LABELS: Record<string, string> = {
  pre: "pre-market",
  regular: "regular",
  post: "after-hours",
  overnight: "overnight",
  closed: "closed",
};

function rvolColor(value: number): string {
  if (value >= 3) return "var(--c-red)";
  if (value >= 2) return "var(--c-amber)";
  if (value >= 1.5) return "var(--c-yellow)";
  return "var(--fg-dim)";
}

function formatVolume(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(0)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

function formatPrice(v: number): string {
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatAsOf(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return "—";
  }
}

export function RvolScannerWidget({ config }: WidgetProps) {
  const [data, setData] = useState<RvolScannerResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { openTickerInfo } = useTickerInfo();

  const load = useCallback(async () => {
    try {
      const result = await fetchRvolScanner(1.5, 50);
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load scanner");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  // ── Loading state ──────────────────────────────────────────────────────────
  if (loading && !data) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  if (error && !data) {
    return <p className="p-3 font-mono text-xs text-[var(--c-red)]">{error}</p>;
  }

  // ── Off-hours / empty state ────────────────────────────────────────────────
  const offHours = !data || data.session !== "regular" || data.count === 0;

  const sessionLabel = data
    ? (SESSION_LABELS[data.session] ?? data.session)
    : "—";

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[12px]">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-2 py-1.5">
        <span className="text-[9.5px] font-medium uppercase tracking-[0.05em] text-[var(--fg-faint)]">
          {sessionLabel}
        </span>
        {data ? (
          <span className="text-[9.5px] tabular-nums text-[var(--fg-faint)]">
            {formatAsOf(data.as_of)}
          </span>
        ) : null}
      </div>

      {/* Off-hours / empty notice */}
      {offHours ? (
        <p className="p-3 text-[11px] text-[var(--fg-dim)]">
          {data?.note ?? "Scanner runs during market hours (9:30–16:00 ET)."}
        </p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          {/* Column headers */}
          <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_3.75rem_3.25rem_3.5rem] items-center gap-x-1 border-b border-[var(--border)] px-2 py-1.5">
            <span className="text-[9.5px] font-medium uppercase tracking-[0.05em] text-[var(--fg-faint)]">
              #
            </span>
            {(["Sym", "Last", "RVOL", "Vol"] as const).map((h, idx) => (
              <span
                key={h}
                className={cn(
                  "text-[9.5px] font-medium uppercase tracking-[0.05em] text-[var(--fg-faint)]",
                  idx === 0 ? "text-left" : "text-right",
                )}
              >
                {h}
              </span>
            ))}
          </div>

          {/* Rows */}
          {data!.results.map((row, idx) => (
            <button
              key={row.symbol}
              type="button"
              onClick={() => publish(row.symbol)}
              className={cn(
                "grid w-full grid-cols-[1.25rem_minmax(0,1fr)_3.75rem_3.25rem_3.5rem] items-center gap-x-1 border-b border-[var(--border)] px-2 py-1.5 text-left transition-colors",
                linked
                  ? "cursor-pointer hover:bg-[var(--bg-hover)]"
                  : "cursor-default hover:bg-[var(--bg-hover)]",
              )}
            >
              {/* Rank */}
              <span className="text-[10px] tabular-nums text-[var(--fg-faint)]">
                {idx + 1}
              </span>
              {/* Symbol */}
              <span className="group/sym inline-flex min-w-0 items-center gap-0.5">
                <span className="truncate font-medium text-[var(--c-accent)]">
                  {row.symbol}
                </span>
                <span
                  role="button"
                  tabIndex={0}
                  aria-label={`${row.symbol} info`}
                  onClick={(e) => {
                    e.stopPropagation();
                    openTickerInfo(row.symbol);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      openTickerInfo(row.symbol);
                    }
                  }}
                  className="shrink-0 cursor-pointer text-[var(--fg-faint)] opacity-0 transition-opacity hover:text-[var(--c-accent)] group-hover/sym:opacity-100 focus-visible:opacity-100"
                >
                  <Info className="size-3" />
                </span>
              </span>
              {/* Price */}
              <span className="text-right tabular-nums text-foreground">
                {formatPrice(row.price)}
              </span>
              {/* RVOL */}
              <span
                className="text-right tabular-nums"
                style={{ color: rvolColor(row.rvol) }}
                title={`${row.rvol.toFixed(2)}× normal volume for this time of day`}
              >
                {row.rvol.toFixed(1)}×
              </span>
              {/* Volume */}
              <span className="text-right tabular-nums text-[var(--fg-dim)]">
                {formatVolume(row.total_volume)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
