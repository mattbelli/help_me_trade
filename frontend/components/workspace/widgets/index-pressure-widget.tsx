"use client";

import { useCallback, useState } from "react";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchIndexPressure,
  fetchOvernightFlow,
  type IndexPressureResult,
  type IndexPressureRow,
  type OvernightFlowResult,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

// Windows are fixed session bookends; rows are final ~5 min after each closes.
// The collector flushes minute rows every ~10s in-window, so a 10s poll tracks
// the tape essentially live during the half hours the gauge exists for. The
// overnight view is a once-a-morning file (~9:16 ET) — a slow poll is honest.
const POLL_MS = 10_000;
const OVERNIGHT_POLL_MS = 300_000;

const WINDOWS = ["open", "close", "overnight"] as const;
type WindowKey = (typeof WINDOWS)[number];

function fmtNet(v: number): string {
  const a = Math.abs(v);
  const s =
    a >= 1_000_000 ? `$${(a / 1_000_000).toFixed(1)}M` : `$${(a / 1_000).toFixed(0)}K`;
  return v < 0 ? `-${s}` : `+${s}`;
}

function fmtMusd(v: number): string {
  return `$${(v / 1_000_000).toFixed(1)}M`;
}

function occRoot(sym: string): string {
  return sym.length > 15 ? sym.slice(0, -15).replace(/\d+$/, "") : sym;
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

function OvernightBody({
  data,
  publish,
}: {
  data: OvernightFlowResult;
  publish: (symbol: string) => void;
}) {
  const line = data.line;
  if (!line) {
    return (
      <WidgetEmpty message="no overnight line yet — lands ~9:16 ET each trading morning" />
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
  return (
    <div className="px-3 pt-2">
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
      <Sparkline values={data.history.map((h) => h.notional)} />
      <div className="pt-1 text-[11px] text-[var(--text-secondary)]">
        puts {Math.round(s.put_share_notional * 100)}% · 0dte{" "}
        {Math.round(s.zero_dte_share_notional * 100)}% · 8:30 window{" "}
        {Math.round(s.macro_window_share * 100)}% · {s.prints.toLocaleString()}{" "}
        prints
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
  );
}

export function IndexPressureWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const windowKey: WindowKey = config.pressureWindow ?? "open";
  const { publish } = usePublishSymbol(channel);
  const [data, setData] = useState<IndexPressureResult | null>(null);
  const [overnight, setOvernight] = useState<OvernightFlowResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      if (windowKey === "overnight") {
        setOvernight(await fetchOvernightFlow());
      } else {
        setData(await fetchIndexPressure(windowKey));
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, [windowKey]);

  usePagePoll(load, windowKey === "overnight" ? OVERNIGHT_POLL_MS : POLL_MS);

  const windowToggle = (
    <span className="ml-auto flex gap-2">
      {WINDOWS.map((w) => (
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

  if (windowKey === "overnight") {
    if (!overnight) return <WidgetLoading />;
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <OvernightBody data={overnight} publish={publish} />
        </div>
        <div className="flex border-t border-[var(--border-subtle)] px-3 py-[5px] text-[10px] text-[var(--text-muted)]">
          <span>04:00–09:15 ET GTH · alpaca single-source · activity, not direction</span>
          {windowToggle}
        </div>
      </div>
    );
  }

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
  // Surface a lane anomaly (mixed/unexpected coverage) — silence means the normal
  // guaranteed gauge lane; anything else must be visible, not smoothed over.
  const lanes = new Set(
    data.rows.map((r) => r.coverage).filter((c): c is string => Boolean(c)),
  );
  lanes.delete("gauge_lane");
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
          {lanes.size > 0 ? (
            <span className="text-[var(--warn)]"> · lane: {[...lanes].join(",")}</span>
          ) : null}
        </span>
        {windowToggle}
      </div>
    </div>
  );
}
