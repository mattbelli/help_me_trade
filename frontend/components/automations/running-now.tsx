"use client";

import {
  Activity,
  Bell,
  FileText,
  Scale,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAutomationsOverview,
  type AutomationsOverview,
} from "@/lib/use-automations-overview";

function formatRelativePast(iso: string | null): string {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "—";
  const deltaSec = Math.round((Date.now() - ts) / 1000);
  if (deltaSec < 0) return "just now";
  if (deltaSec < 60) return "just now";
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatRelativeFuture(date: Date | null): string {
  if (!date) return "—";
  const deltaSec = Math.round((date.getTime() - Date.now()) / 1000);
  if (deltaSec <= 0) return "soon";
  if (deltaSec < 3600) return `in ${Math.floor(deltaSec / 60)}m ET`;
  if (deltaSec < 86400) return `in ${Math.floor(deltaSec / 3600)}h ET`;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }) + " ET";
}

function OverviewRow({
  icon: Icon,
  label,
  count,
  countLabel,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  count?: number;
  countLabel?: string;
  detail: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <Icon className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
      <span className="font-medium">{label}</span>
      {countLabel ? (
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px] font-medium">
          {countLabel}
        </Badge>
      ) : count !== undefined ? (
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px] font-medium">
          {count} active
        </Badge>
      ) : null}
      <span className="text-muted-foreground">{detail}</span>
    </div>
  );
}

type Props = {
  overview?: AutomationsOverview;
  loading?: boolean;
};

export function RunningNow(props: Props) {
  const internal = useAutomationsOverview(45_000, props.overview === undefined);
  const overview = props.overview ?? internal.overview;
  const loading = props.loading ?? internal.loading;

  return (
    <Card>
      <CardHeader className="border-b pb-3">
        <CardTitle className="text-base">Running now</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 pt-4">
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-5 w-full max-w-md" />
            ))}
          </div>
        ) : (
          <>
            <OverviewRow
              icon={Activity}
              label="Strategies"
              count={overview.strategies.active}
              detail={`last: ${formatRelativePast(overview.strategies.lastAt)}`}
            />
            <OverviewRow
              icon={Scale}
              label="Scans & Rules"
              count={overview.rules.active}
              detail={`last fire: ${formatRelativePast(overview.rules.lastAt)}`}
            />
            <OverviewRow
              icon={FileText}
              label="Reports"
              countLabel={overview.reports.enabled ? "scheduled" : "off"}
              detail={
                overview.reports.enabled
                  ? `last: ${formatRelativePast(overview.reports.lastAt)} · next: ${formatRelativeFuture(overview.reports.nextAt)}`
                  : `last: ${formatRelativePast(overview.reports.lastAt)}`
              }
            />
            <OverviewRow
              icon={Bell}
              label="Alerts"
              count={overview.alerts.active}
              detail={`last: ${formatRelativePast(overview.alerts.lastAt)}`}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}
