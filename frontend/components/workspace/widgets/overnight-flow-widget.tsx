"use client";

import { useCallback, useState } from "react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import { fetchOvernightFlow, type OvernightFlowResult } from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

// The line is written once per morning (~9:16 ET); a slow poll keeps the
// widget honest across the write without hammering a file-backed endpoint.
const POLL_MS = 300_000;

function fmtMusd(v: number): string {
  return `$${(v / 1_000_000).toFixed(1)}M`;
}

function occRoot(sym: string): string {
  return sym.length > 15 ? sym.slice(0, -15).replace(/\d+$/, "") : sym;
}

/** Trailing-nights sparkline; the newest night is the emphasized end point. */
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * 100;
    const y = 26 - ((v - min) / span) * 24;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const [lastX, lastY] = pts[pts.length - 1].split(",");
  return (
    <svg
      viewBox="0 0 100 28"
      preserveAspectRatio="none"
      className="h-[28px] w-full"
      aria-hidden
    >
      <polyline
        points={pts.join(" ")}
        fill="none"
        stroke="var(--text-muted)"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      <circle
        cx={lastX}
        cy={lastY}
        r="2"
        fill="var(--accent)"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export function OvernightFlowWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish } = usePublishSymbol(channel);
  const [data, setData] = useState<OvernightFlowResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchOvernightFlow());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  usePagePoll(load, POLL_MS);

  if (error) return <WidgetError message={error} onRetry={() => void load()} />;
  if (!data) return <WidgetLoading />;

  const line = data.line;
  const footer = (
    <div className="border-t border-[var(--border-subtle)] px-3 py-[5px] text-[10px] text-[var(--text-muted)]">
      04:00–09:15 ET (Cboe GTH) · alpaca single-source · activity, not direction
    </div>
  );
  if (!line) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1">
          <WidgetEmpty message="no overnight line yet — lands ~9:16 ET each trading morning" />
        </div>
        {footer}
      </div>
    );
  }

  const s = line.slice;
  const z = line.baseline.z;
  // z is context for the open, never a signal — the color grades how unusual
  // the NIGHT was (≥5 matches the phone-alert threshold), not a direction.
  const zClass =
    z === null
      ? "text-[var(--text-muted)]"
      : Math.abs(z) >= 5
        ? "text-[var(--neg)]"
        : Math.abs(z) >= 2
          ? "text-[var(--warn)]"
          : "text-[var(--text-secondary)]";
  const spark = data.history.map((h) => h.notional);

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pt-2">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[20px] font-semibold tabular-nums text-[var(--text-primary)]">
            {fmtMusd(s.notional)}
          </span>
          <span className={cn("font-mono text-[13px] tabular-nums", zClass)}>
            {z === null
              ? (line.baseline.note ?? "no baseline")
              : `z ${z >= 0 ? "+" : ""}${z.toFixed(1)}`}
          </span>
          {z !== null && line.baseline.median !== null ? (
            <span className="text-[10px] text-[var(--text-muted)]">
              vs {fmtMusd(line.baseline.median)} median · {line.baseline.nights}n
            </span>
          ) : null}
          <span className="ml-auto text-[10px] text-[var(--text-muted)]">
            {line.session}
          </span>
        </div>
        <Sparkline values={spark} />
        <div className="pt-1 text-[11px] text-[var(--text-secondary)]">
          puts {Math.round(s.put_share_notional * 100)}% · 0dte{" "}
          {Math.round(s.zero_dte_share_notional * 100)}% · 8:30 window{" "}
          {Math.round(s.macro_window_share * 100)}% ·{" "}
          {s.prints.toLocaleString()} prints
          {s.full_night ? (
            <span className="text-[var(--text-muted)]">
              {" "}
              · full night {fmtMusd(s.full_night.notional)}
            </span>
          ) : null}
        </div>
        <div className="pt-1">
          {(s.top_prints ?? []).slice(0, 3).map((tp) => (
            <div
              key={`${tp.sym}-${tp.et}`}
              className="flex items-baseline gap-2 py-[2px] font-mono text-[11px] tabular-nums"
            >
              <button
                type="button"
                onClick={() => publish(occRoot(tp.sym))}
                className="text-[var(--text-primary)] hover:underline"
              >
                {tp.sym}
              </button>
              <span className="text-[var(--text-secondary)]">
                {tp.size.toLocaleString()}x
              </span>
              <span className="text-[var(--text-secondary)]">
                {fmtMusd(tp.notional)}
              </span>
              <span className="ml-auto text-[var(--text-muted)]">{tp.et} ET</span>
            </div>
          ))}
        </div>
      </div>
      {footer}
    </div>
  );
}
