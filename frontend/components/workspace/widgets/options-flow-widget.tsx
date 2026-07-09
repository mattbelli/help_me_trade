"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import {
  fetchOptionsFlow,
  type OptionsFlowEvent,
  type OptionsFlowResult,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 30_000;
const EOD_TS_SUFFIX = "T21:00:00Z";
const HONESTY_TOOLTIP =
  "Facts about the tape, not a buy/sell signal. No win%.";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

type FlowKind = "uv1" | "uv2" | "block" | "sweep" | "pressure";

function formatPremium(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function formatStrike(strike: number | null): string {
  if (strike == null) return "—";
  if (strike === Math.trunc(strike)) return `$${strike.toFixed(0)}`;
  return `$${strike}`;
}

function formatExpiryMonth(expiry: string | null): string {
  if (!expiry) return "???";
  const month = Number.parseInt(expiry.slice(5, 7), 10);
  if (month >= 1 && month <= 12) return MONTHS[month - 1]!;
  return "???";
}

function formatDte(dte: number | null): string {
  if (dte == null) return "—";
  return `${dte}d`;
}

function formatContracts(contracts: number | null): string {
  if (contracts == null) return "—";
  return `${contracts.toLocaleString()} ctr`;
}

function formatTimeEt(ts: string): string {
  if (ts.endsWith(EOD_TS_SUFFIX)) return "EOD";
  try {
    return new Date(ts).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return "—";
  }
}

function rightLetter(right: string | null): "C" | "P" | null {
  if (!right) return null;
  const r = right.trim().toLowerCase();
  if (r.startsWith("c")) return "C";
  if (r.startsWith("p")) return "P";
  return null;
}

function flowKind(ev: OptionsFlowEvent): FlowKind {
  if (ev.event_type === "block") return "block";
  if (ev.event_type === "sweep") return "sweep";
  if (ev.event_type === "directional_pressure") return "pressure";
  if (ev.event_type === "unusual_volume") {
    return ev.occ_symbol ? "uv1" : "uv2";
  }
  return "uv1";
}

function typeLabel(kind: FlowKind): string {
  switch (kind) {
    case "block":
      return "block";
    case "sweep":
      return "sweep";
    case "uv1":
      return "unusual volume";
    case "uv2":
      return "chain surge";
    case "pressure":
      return "pressure";
  }
}

function typeTextClass(kind: FlowKind, direction: OptionsFlowEvent["direction"]): string {
  switch (kind) {
    case "block":
      return "text-[var(--c-yellow)]";
    case "sweep":
      return "text-[var(--c-violet)]";
    case "uv1":
      return "text-[var(--c-cyan)]";
    case "uv2":
      return "text-[var(--c-accent)]";
    case "pressure":
      if (direction === "buy_side") {
        return "text-[var(--c-green)]";
      }
      if (direction === "sell_side") {
        return "text-[var(--c-red)]";
      }
      return "text-[var(--fg-dim)]";
  }
}

function sideLabel(
  letter: "C" | "P",
  direction: OptionsFlowEvent["direction"],
): string {
  const word = letter === "C" ? "call" : "put";
  if (direction === "buy_side") return `▲ ${word}`;
  if (direction === "sell_side") return `▼ ${word}`;
  return word;
}

function sideTextClass(direction: OptionsFlowEvent["direction"]): string {
  // Color follows the ACTION (aggressor side), not the instrument — the word
  // already says call/put. Green ▲ = ask-side, red ▼ = bid-side; a print with
  // no classified side stays dim (founder: a green ▼ reads as a contradiction).
  if (direction === "buy_side") return "text-[var(--c-green)]";
  if (direction === "sell_side") return "text-[var(--c-red)]";
  return "text-[var(--fg-faint)]";
}

function dteBucketFromId(id: string): string {
  const marker = "_bucket_";
  const idx = id.indexOf(marker);
  if (idx === -1) return "chain";
  const bucket = id.slice(idx + marker.length);
  return bucket || "chain";
}

function contractLabel(ev: OptionsFlowEvent): string {
  const letter = rightLetter(ev.right);
  const month = formatExpiryMonth(ev.expiry);
  const strike = formatStrike(ev.strike);
  return letter ? `${month} ${strike}${letter}` : `${month} ${strike}`;
}

function formatDetailLine(ev: OptionsFlowEvent): string {
  const kind = flowKind(ev);

  if (kind === "uv1") {
    const parts = [
      contractLabel(ev),
      formatDte(ev.dte),
      formatContracts(ev.contracts),
    ];
    if (ev.vol_oi_ratio != null) {
      parts.push(`${ev.vol_oi_ratio.toFixed(1)}× OI`);
    }
    return parts.join(" · ");
  }

  if (kind === "uv2") {
    const parts = [
      dteBucketFromId(ev.id),
      ev.vol_zscore != null ? `z ${ev.vol_zscore}σ` : null,
      formatContracts(ev.contracts),
    ].filter(Boolean);
    return parts.join(" · ");
  }

  if (kind === "block" || kind === "sweep") {
    const parts = [
      contractLabel(ev),
      formatDte(ev.dte),
      formatContracts(ev.contracts),
    ];
    if (ev.n_prints != null) {
      parts.push(kind === "sweep" ? `${ev.n_prints} prints` : `${ev.n_prints} print`);
    }
    if (ev.exchanges?.length) {
      parts.push(ev.exchanges.join("/"));
    }
    return parts.join(" · ");
  }

  const side = ev.direction === "buy_side" ? "ask" : "bid";
  const prints = ev.n_prints ?? 0;
  return `net ${formatPremium(ev.premium_usd)} ${side}-side · ${prints} classified prints`;
}

export function OptionsFlowWidget({ config }: WidgetProps) {
  const [data, setData] = useState<OptionsFlowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterCleared, setFilterCleared] = useState(false);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const { publish, linked } = usePublishSymbol(channel);
  const { openTickerInfo } = useTickerInfo();

  const activeFilter =
    channel !== "none" && channelSymbol && !filterCleared
      ? channelSymbol
      : undefined;

  useEffect(() => {
    setFilterCleared(false);
  }, [channelSymbol]);

  const load = useCallback(async () => {
    try {
      const result = await fetchOptionsFlow(50, activeFilter);
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
  }, [activeFilter]);

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
      {events.length === 0 ? (
        <WidgetEmpty message="No unusual options activity recorded yet. The nightly EOD scan populates this when enabled." />
      ) : (
        <div className="relative min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {activeFilter ? (
            <div className="flex items-center gap-1 border-b border-[var(--border)] px-2 py-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
              <span>filtering {activeFilter}</span>
              <span>·</span>
              <button
                type="button"
                aria-label="Clear symbol filter"
                onClick={() => setFilterCleared(true)}
                className="text-[var(--fg-faint)] transition-colors hover:text-[var(--fg-dim)]"
              >
                ×
              </button>
            </div>
          ) : null}
          {events.map((ev) => {
            const kind = flowKind(ev);
            const letter = rightLetter(ev.right);
            const showSideLabel = letter != null;

            return (
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
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="shrink-0 tabular-nums text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                    {formatTimeEt(ev.ts)}
                  </span>
                  <span className="text-[var(--fg-faint)]">·</span>
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
                  </span>
                  <span
                    className={cn(
                      "shrink-0 text-[length:var(--fs-micro)] lowercase",
                      typeTextClass(kind, ev.direction),
                    )}
                  >
                    {typeLabel(kind)}
                  </span>
                  {showSideLabel ? (
                    <span
                      className={cn(
                        "shrink-0 text-[length:var(--fs-micro)] lowercase tabular-nums",
                        sideTextClass(ev.direction),
                      )}
                    >
                      {sideLabel(letter, ev.direction)}
                    </span>
                  ) : null}
                  <span className="min-w-2 flex-1" />
                  <span className="shrink-0 tabular-nums font-medium text-[var(--fg)]">
                    {formatPremium(ev.premium_usd)}
                  </span>
                </div>
                <div className="truncate text-[length:var(--fs-micro)] text-[var(--fg-dim)]">
                  {formatDetailLine(ev)}
                </div>
              </button>
            );
          })}
          <span
            className="pointer-events-auto absolute right-1 bottom-1 text-[var(--fg-faint)] opacity-40"
            title={HONESTY_TOOLTIP}
          >
            <Info className="size-3" />
          </span>
        </div>
      )}
    </div>
  );
}
