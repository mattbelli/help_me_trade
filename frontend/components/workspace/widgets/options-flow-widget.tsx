"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import { fetchOptionsFlow, type OptionsFlowResult } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

function formatPremium(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function formatDay(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "—";
  }
}

export function OptionsFlowWidget({ config }: WidgetProps) {
  const [data, setData] = useState<OptionsFlowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const { openTickerInfo } = useTickerInfo();

  const load = useCallback(async () => {
    try {
      const result = await fetchOptionsFlow(50);
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(
        err instanceof Error ? err.message : "Failed to load options flow",
      );
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

  if (loading && !data) {
    return <WidgetLoading cols={[1, 2, 1]} />;
  }
  if (error && !data) {
    return <WidgetError message={error} onRetry={load} />;
  }

  const events = data?.events ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {/* Caveat banner — perception, NOT a signal (design §3/§6) */}
      <div className="flex items-start gap-1.5 border-b border-[var(--border)] px-2 py-1">
        <Info className="mt-0.5 size-3 shrink-0 text-[var(--fg-faint)]" />
        <span className="text-[length:var(--fs-micro)] leading-tight text-[var(--fg-faint)]">
          Unusual options activity — facts about the tape, not a buy/sell
          signal. No win%.
        </span>
      </div>

      {events.length === 0 ? (
        <WidgetEmpty message="No unusual options activity recorded yet. The nightly EOD scan populates this when enabled." />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {events.map((ev) => (
            <button
              key={ev.id}
              type="button"
              onClick={() => publish(ev.underlying)}
              title={ev.caveat}
              className={cn(
                "group/row flex w-full flex-col gap-0.5 border-b border-[var(--border)] px-2 py-1.5 text-left transition-colors hover:bg-[var(--bg-hover)]",
                linked ? "cursor-pointer" : "cursor-default",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="inline-flex min-w-0 items-center gap-1">
                  <span className="font-medium text-[var(--c-accent)]">
                    {ev.underlying}
                  </span>
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`${ev.underlying} info`}
                    onClick={(e) => {
                      e.stopPropagation();
                      openTickerInfo(ev.underlying);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        openTickerInfo(ev.underlying);
                      }
                    }}
                    className="shrink-0 cursor-pointer text-[var(--fg)] opacity-0 transition-opacity group-hover/row:opacity-100 focus-visible:opacity-100"
                  >
                    <Info className="size-3" />
                  </span>
                  <span className="shrink-0 rounded-sm border border-[var(--border)] px-1 text-[length:var(--fs-micro)] uppercase tracking-wide text-[var(--fg-faint)]">
                    {ev.event_type.replace(/_/g, " ")}
                  </span>
                </span>
                <span className="shrink-0 tabular-nums text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                  {formatPremium(ev.premium_usd)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[length:var(--fs-table)] text-[var(--fg-dim)]">
                  {ev.reason}
                </span>
                <span className="shrink-0 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                  {formatDay(ev.ts)}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
