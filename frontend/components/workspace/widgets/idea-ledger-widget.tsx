"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  getIdeaLedger,
  type IdeaLedgerResponse,
  type IdeaLedgerRow,
} from "@/lib/api";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;
const TH =
  "border-b border-[var(--line-divider)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--line-divider)] px-2 py-1.5";
const ROW = "transition-colors hover:bg-[var(--bg-hover)]";

const LANE_COLORS: Record<string, string> = {
  structure: "var(--fg-dim)",
  structure_vetoed: "var(--fg-faint)",
  llm_conviction: "var(--c-violet)",
  fusion_mech: "var(--c-cyan)",
  earnings_drift: "var(--c-amber)",
  convergence: "var(--c-blue)",
};

type LedgerConfig = {
  ledgerOrigin?: string;
  ledgerStatus?: string;
};

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("month")}/${part("day")} ${part("hour")}:${part("minute")}`;
}

function firstTarget(raw: string): string {
  try {
    const targets = JSON.parse(raw) as unknown;
    if (Array.isArray(targets) && targets.length > 0) {
      return String(targets[0]);
    }
  } catch {
    // Malformed historical rows remain visible; only the target cell is blank.
  }
  return "—";
}

function formatResult(value: number | null): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function resultClass(value: number | null): string {
  if (value == null || value === 0) return "text-[var(--fg-faint)]";
  return value > 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]";
}

function LedgerTable({
  rows,
  linked,
  publish,
}: {
  rows: IdeaLedgerRow[];
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  return (
    <div className="h-full overflow-auto scroll-shadow-y px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "whitespace-nowrap text-left")}>Date</th>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left")}>Lane</th>
            <th className={cn(TH, "text-left")}>Dir</th>
            <th className={cn(TH, "text-right")}>Rank</th>
            <th className={cn(TH, "text-right")}>Entry</th>
            <th className={cn(TH, "text-right")}>Stop</th>
            <th className={cn(TH, "text-right")}>Target</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-right")}>Result %</th>
            <th className={cn(TH, "text-left")}>Action</th>
          </tr>
        </thead>
        <tbody className="leading-[20px]">
          {rows.map((row) => (
            <tr key={row.id} className={ROW}>
              <td className={cn(TD, "whitespace-nowrap text-[var(--fg-faint)]")}>
                {formatDate(row.as_of)}
              </td>
              <td className={cn(TD, "font-medium text-foreground")}>
                <ClickableSymbol
                  symbol={row.symbol}
                  linked={linked}
                  onActivate={() => publish(row.symbol)}
                />
              </td>
              <td className={cn(TD, "whitespace-nowrap")}>
                <span
                  style={{ color: LANE_COLORS[row.origin] ?? "var(--fg-dim)" }}
                >
                  {row.origin.toLowerCase()}
                </span>
              </td>
              <td className={cn(TD, "text-[var(--fg-dim)]")}>
                {row.direction.toLowerCase()}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.idea_index}
              </td>
              <td className={cn(TD, "whitespace-nowrap text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.entry_low}–{row.entry_high}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.stop || "—"}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {firstTarget(row.targets_json)}
              </td>
              <td className={cn(TD, "text-[var(--fg-dim)]")}>{row.status}</td>
              <td
                className={cn(
                  TD,
                  "text-right tabular-nums",
                  resultClass(row.ret_pct),
                )}
              >
                {formatResult(row.ret_pct)}
              </td>
              <td className={cn(TD, "text-[var(--fg-dim)]")}>
                {row.user_action || "skipped"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IdeaLedgerWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const ledgerConfig = config as typeof config & LedgerConfig;
  const origin = ledgerConfig.ledgerOrigin;
  const status = ledgerConfig.ledgerStatus;
  const [data, setData] = useState<IdeaLedgerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(
    async (isInitial: boolean) => {
      if (isInitial) setLoading(true);
      try {
        const result = await getIdeaLedger({
          origin: origin && origin !== "all" ? origin : undefined,
          status: status && status !== "all" ? status : undefined,
          limit: 500,
        });
        if (cancelledRef.current) return;
        setData(result);
        setError(null);
      } catch (err) {
        if (cancelledRef.current) return;
        setError(err instanceof Error ? err.message : "Failed to load idea ledger");
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    },
    [origin, status],
  );

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  usePagePoll(load, POLL_MS);

  if (loading && !data) {
    return <WidgetLoading cols={[1, 1, 2, 1, 1]} />;
  }
  if (error && !data) {
    return <WidgetError message={error} onRetry={() => void load(false)} />;
  }
  if (!data || data.rows.length === 0) {
    return <WidgetEmpty message="No ideas match these filters." />;
  }
  return <LedgerTable rows={data.rows} linked={linked} publish={publish} />;
}
