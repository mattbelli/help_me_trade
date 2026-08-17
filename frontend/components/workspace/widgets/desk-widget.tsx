"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  DataTable,
  type DataTableColumn,
} from "@/components/workspace/primitives/data-table";
import { Tag } from "@/components/workspace/primitives/tag";
import {
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  getFusionRadar,
  type FusionRadarResponse,
  type FusionRadarRow,
} from "@/lib/api";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const POLL_MS = 15_000;
const QUIET = "px-2 py-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)]";

type DeskConfig = {
  deskSessions?: number;
};

function formatAge(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 90) return `${Math.round(secs)}s`;
  const mins = secs / 60;
  if (mins < 90) return `${Math.round(mins)}m`;
  const hours = mins / 60;
  if (hours < 36) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

/** Center-split bull(left/green) vs bear(right/red) bar, shared scale per row:
 * the dominant side fills its half, the other side is proportional to it. */
function MassBar({ bull, bear }: { bull: number; bear: number }) {
  const scale = Math.max(bull, bear, 1e-9);
  const bullPct = (bull / scale) * 100;
  const bearPct = (bear / scale) * 100;
  return (
    <div className="flex h-[6px] w-full items-center">
      <div className="flex h-full flex-1 justify-end">
        <div
          className="h-full rounded-l-[1px]"
          style={{ width: `${bullPct}%`, backgroundColor: "var(--c-green)" }}
        />
      </div>
      <div className="h-full w-px bg-[var(--line-divider)]" />
      <div className="flex h-full flex-1 justify-start">
        <div
          className="h-full rounded-r-[1px]"
          style={{ width: `${bearPct}%`, backgroundColor: "var(--c-red)" }}
        />
      </div>
    </div>
  );
}

// A lightweight text section caption — deliberately NOT a bordered/badged bar
// (the grid WidgetHeader owns all widget chrome); the three zones are content
// structure, separated by spacing and a muted label, not a second header.
function Zone({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-3">
      <div className="px-2 pb-0.5 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
        {title}
      </div>
      {children}
    </section>
  );
}

function RadarTable({
  rows,
  linked,
  publish,
}: {
  rows: FusionRadarRow[];
  linked: boolean;
  publish: (symbol: string) => void;
}) {
  const columns: DataTableColumn<FusionRadarRow>[] = [
    {
      key: "symbol",
      header: "Symbol",
      className: "font-medium text-foreground",
      render: (row) => (
        <ClickableSymbol
          symbol={row.symbol}
          linked={linked}
          onActivate={() => publish(row.symbol)}
        />
      ),
    },
    {
      key: "mass",
      header: "Bull / Bear",
      absorb: true,
      render: (row) => (
        <MassBar bull={row.thesis.bull_mass} bear={row.thesis.bear_mass} />
      ),
    },
    {
      key: "conviction",
      header: "Conv",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (row) => row.thesis.conviction.toFixed(2),
    },
    {
      key: "z",
      header: "Z",
      align: "right",
      className: "tabular-nums font-medium text-foreground",
      render: (row) => row.thesis.z.toFixed(1),
    },
    {
      key: "net",
      header: "Net",
      align: "right",
      className: "tabular-nums text-[var(--fg-dim)]",
      render: (row) => (
        <>
          {row.thesis.net > 0 ? "+" : ""}
          {row.thesis.net.toFixed(1)}
        </>
      ),
    },
    {
      key: "flags",
      className: "whitespace-nowrap",
      render: (row) => (
        <>
          {row.tier === "live" ? (
            <Tag tone="green" className="mr-2">
              hot
            </Tag>
          ) : null}
          {row.thesis.contested ? <Tag tone="amber">contested</Tag> : null}
        </>
      ),
    },
    {
      key: "age",
      header: "Age",
      align: "right",
      className: "whitespace-nowrap tabular-nums text-[var(--fg-faint)]",
      render: (row) => formatAge(row.last_evidence_at),
    },
  ];

  return (
    <DataTable columns={columns} rows={rows} rowKey={(row) => row.symbol} />
  );
}

export function DeskWidget({ config }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const deskConfig = config as typeof config & DeskConfig;
  const sessions = deskConfig.deskSessions ?? 5;
  const [data, setData] = useState<FusionRadarResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(
    async (isInitial: boolean) => {
      if (isInitial) setLoading(true);
      try {
        const result = await getFusionRadar({ sessions });
        if (cancelledRef.current) return;
        setData(result);
        setError(null);
      } catch (err) {
        if (cancelledRef.current) return;
        setError(err instanceof Error ? err.message : "Failed to load radar");
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    },
    [sessions],
  );

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  usePagePoll(load, POLL_MS);

  const rows = data?.rows ?? [];

  return (
    <div className="h-full overflow-auto scroll-shadow-y px-1.5 py-1 font-mono">
      <Zone title="needs you">
        <p className={QUIET}>nothing needs you — triggers light up in a later round</p>
      </Zone>
      <Zone title="armed">
        <p className={QUIET}>nothing armed — engine is dark until Round 5</p>
      </Zone>
      <Zone title="warming">
        {loading && !data ? (
          <WidgetLoading rows={3} cols={[1, 4, 1, 1, 1]} className="p-2" />
        ) : error && !data ? (
          <WidgetError message={error} onRetry={() => void load(false)} />
        ) : data?.disabled ? (
          <p className={QUIET}>fusion layer is off (FUSION_ENABLED=0)</p>
        ) : rows.length === 0 ? (
          <p className={QUIET}>no warming names in the last {sessions} sessions</p>
        ) : (
          <RadarTable rows={rows} linked={linked} publish={publish} />
        )}
      </Zone>
    </div>
  );
}
