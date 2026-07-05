"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getIdeaOutcomes,
  type IdeaOutcomeRow,
  type IdeaOutcomesResponse,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 60_000;

const STATUS_CHIP_ORDER = [
  "pending",
  "triggered",
  "won",
  "lost",
  "expired",
  "invalidated",
  "data_gap",
] as const;

type TabId = "open" | "resolved";

const TABS: { id: TabId; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
];

const TH =
  "border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase";
const TD = "border-b border-[var(--border)] px-2 py-1.5";
const ROW = "transition-colors hover:bg-[var(--bg-hover)]";

function statusClass(status: string): string {
  switch (status) {
    case "won":
      return "text-[var(--c-green)]";
    case "lost":
      return "text-[var(--c-red)]";
    case "triggered":
      return "text-[var(--c-accent)]";
    case "pending":
      return "text-[var(--fg-dim)]";
    case "expired":
    case "invalidated":
    case "data_gap":
      return "text-[var(--fg-faint)]";
    default:
      return "text-[var(--fg-dim)]";
  }
}

function formatDateOnly(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatWinRate(winRate: number | null): string {
  if (winRate == null) return "—";
  return `${(winRate * 100).toFixed(1)}%`;
}

function formatOriginLabel(origin: string): string {
  if (origin === "llm_conviction") return "llm";
  if (origin === "structure") return "struct";
  return origin.slice(0, 6);
}

function formatOriginWinRateSummary(
  byOrigin: NonNullable<IdeaOutcomesResponse["summary"]["by_origin"]>,
): string {
  const entries = Object.entries(byOrigin);
  if (entries.length < 2) return "";
  return entries
    .map(([origin, stats]) => {
      const label = formatOriginLabel(origin);
      const winLoss = (stats.counts.won ?? 0) + (stats.counts.lost ?? 0);
      const total = Object.values(stats.counts).reduce((sum, n) => sum + n, 0);
      const count = winLoss > 0 ? winLoss : total;
      return `${label} ${formatWinRate(stats.win_rate)} (${count})`;
    })
    .join(" · ");
}

function OriginChip({ origin }: { origin: string }) {
  if (origin !== "llm_conviction") return null;
  return (
    <span
      className="ml-1 rounded px-1 py-px font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-violet)] uppercase"
      title="LLM-originated conviction idea (A/B tracked)"
    >
      LLM
    </span>
  );
}

function formatMfeMae(mfe: number | null, mae: number | null): string {
  const parts: string[] = [];
  if (mfe != null) {
    parts.push(`${mfe >= 0 ? "+" : "−"}${Math.abs(mfe).toFixed(1)}%`);
  }
  if (mae != null) {
    parts.push(`${mae >= 0 ? "+" : "−"}${Math.abs(mae).toFixed(1)}%`);
  }
  return parts.length > 0 ? parts.join(" / ") : "—";
}

function formatEntryZone(row: IdeaOutcomeRow): string {
  return `${row.entry_low}–${row.entry_high}`;
}

function StatusCell({ row }: { row: IdeaOutcomeRow }) {
  const label = row.status.replace(/_/g, " ");
  return (
    <td
      className={cn(TD, "text-[length:var(--fs-body)] uppercase", statusClass(row.status))}
      title={row.notes?.trim() ? row.notes : undefined}
    >
      {label}
    </td>
  );
}

function OpenTable({
  rows,
  linked,
  publish,
}: {
  rows: IdeaOutcomeRow[];
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">No open ideas</p>
    );
  }

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-left")}>Entry</th>
            <th className={cn(TH, "text-right")}>Stop</th>
            <th className={cn(TH, "text-right")}>Target</th>
            <th className={cn(TH, "text-right")}>MFE / MAE</th>
            <th className={cn(TH, "text-left")}>As of</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={ROW}>
              <td className={cn(TD, "font-medium text-foreground")}>
                <ClickableSymbol
                  symbol={row.symbol}
                  linked={linked}
                  onActivate={() => publish(row.symbol)}
                />
                <OriginChip origin={row.origin} />
              </td>
              <StatusCell row={row} />
              <td className={cn(TD, "tabular-nums text-[var(--fg-dim)]")}>
                {formatEntryZone(row)}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.stop}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.targets[0] ?? "—"}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {formatMfeMae(row.mfe_pct, row.mae_pct)}
              </td>
              <td className={cn(TD, "text-[var(--fg-faint)]")}>
                {formatDateOnly(row.as_of)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResolvedTable({
  rows,
  linked,
  publish,
}: {
  rows: IdeaOutcomeRow[];
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">
        No resolved ideas
      </p>
    );
  }

  return (
    <div className="px-1.5 py-1">
      <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
        <thead>
          <tr>
            <th className={cn(TH, "text-left")}>Symbol</th>
            <th className={cn(TH, "text-left")}>Status</th>
            <th className={cn(TH, "text-left")}>Resolved</th>
            <th className={cn(TH, "text-right")}>Price</th>
            <th className={cn(TH, "text-right")}>MFE / MAE</th>
            <th className={cn(TH, "text-left")}>As of</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={ROW}>
              <td className={cn(TD, "font-medium text-foreground")}>
                <ClickableSymbol
                  symbol={row.symbol}
                  linked={linked}
                  onActivate={() => publish(row.symbol)}
                />
                <OriginChip origin={row.origin} />
              </td>
              <StatusCell row={row} />
              <td className={cn(TD, "text-[var(--fg-dim)]")}>
                {formatDateOnly(row.resolved_at)}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {row.resolution_price ?? "—"}
              </td>
              <td className={cn(TD, "text-right tabular-nums text-[var(--fg-dim)]")}>
                {formatMfeMae(row.mfe_pct, row.mae_pct)}
              </td>
              <td className={cn(TD, "text-[var(--fg-faint)]")}>
                {formatDateOnly(row.as_of)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IdeaOutcomesWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

  const [tab, setTab] = useState<TabId>("open");
  const [data, setData] = useState<IdeaOutcomesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async (isInitial: boolean) => {
    if (isInitial) setLoading(true);
    try {
      const result = await getIdeaOutcomes();
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to load outcomes");
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load(true);
    const timer = setInterval(() => void load(false), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  const stale = error !== null && data !== null;

  const statusChips = useMemo(() => {
    if (!data) return [];
    const counts = data.summary.counts;
    return STATUS_CHIP_ORDER.filter((status) => (counts[status] ?? 0) > 0).map(
      (status) => `${status} ${counts[status]}`,
    );
  }, [data]);

  if (loading && !data) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (error && !data) {
    return <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--c-red)]">{error}</p>;
  }

  if (
    data &&
    data.open.length === 0 &&
    data.resolved.length === 0 &&
    data.summary.resolved === 0 &&
    Object.keys(data.summary.counts).length === 0
  ) {
    return (
      <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">
        No tracked ideas yet — run a setup scan.
      </p>
    );
  }

  const summary = data!.summary;
  const originSummary = summary.by_origin
    ? formatOriginWinRateSummary(summary.by_origin)
    : "";

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-[var(--border)] px-3 py-1.5">
        <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)]">
          resolved {summary.resolved} · win rate {formatWinRate(summary.win_rate)}
          {originSummary ? ` · ${originSummary}` : ""}
        </span>
        {statusChips.length > 0 ? (
          <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            {statusChips.join(" · ")}
          </span>
        ) : null}
        {stale ? (
          <span
            className="font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--c-amber)] uppercase"
            title={error ?? undefined}
          >
            stale
          </span>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1 border-b border-[var(--border)] px-3 py-1.5">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors",
              tab === id
                ? "bg-[var(--c-accent-soft)] text-[var(--c-accent)]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "open" ? (
          <OpenTable rows={data!.open} linked={linked} publish={publish} />
        ) : (
          <ResolvedTable rows={data!.resolved} linked={linked} publish={publish} />
        )}
      </div>
    </div>
  );
}
