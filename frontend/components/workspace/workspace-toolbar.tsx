"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDayPnl } from "@/lib/workspace/use-day-pnl";
import { useEtClock } from "@/lib/workspace/use-et-clock";

import { ThemeSwitcher } from "./theme-switcher";

function formatCompactMoney(value: number): string {
  if (Math.abs(value) >= 1000) {
    return `$${(value / 1000).toFixed(1)}k`;
  }
  return formatMoney(value);
}

function formatSignedPnl(value: number): string {
  const prefix = value >= 0 ? "+" : "";
  return `${prefix}${formatMoney(value)}`;
}

export function WorkspaceToolbar() {
  const { snapshot, brokerInfo, loading } = useSnapshot();
  const { dayPnl, loading: dayLoading } = useDayPnl();
  const clock = useEtClock();

  const broker =
    snapshot?.primary_broker ??
    snapshot?.broker ??
    brokerInfo?.primary_broker ??
    brokerInfo?.broker ??
    "—";

  const isPaper =
    broker === "alpaca" ||
    brokerInfo?.primary_broker === "alpaca" ||
    snapshot?.broker === "alpaca";

  const connected =
    brokerInfo?.brokers?.[broker]?.configured ??
    brokerInfo?.auth?.authenticated ??
    Boolean(snapshot);

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-card px-3 font-mono text-[11px]">
      <div className="flex items-center gap-2">
        <span className="grid size-5 place-items-center rounded-md bg-accent text-[10px] font-bold text-accent-foreground">
          ▰
        </span>
        <span className="font-sans text-sm font-semibold tracking-tight">
          help_me_trade
        </span>
      </div>

      <Badge
        variant="outline"
        className="gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[10px] font-normal uppercase"
      >
        <span
          className={cn(
            "size-1.5 rounded-full",
            connected ? "bg-pnl-positive" : "bg-muted-foreground",
          )}
        />
        {broker}
      </Badge>

      {isPaper ? (
        <Badge
          variant="outline"
          className="rounded-full border-accent/35 px-2.5 py-0.5 font-mono text-[10px] font-normal text-accent"
        >
          paper
        </Badge>
      ) : null}

      <div className="ml-auto flex items-center gap-5 text-muted-foreground">
        {loading && !snapshot ? (
          <Skeleton className="h-4 w-48" />
        ) : snapshot ? (
          <>
            <span>
              <span className="text-muted-foreground/70">PORT </span>
              <b className="font-semibold text-foreground">
                {formatMoney(snapshot.account.portfolio_value)}
              </b>
            </span>
            <span>
              <span className="text-muted-foreground/70">BP </span>
              <b className="font-semibold text-foreground">
                {formatCompactMoney(snapshot.account.buying_power)}
              </b>
            </span>
            <span>
              <span className="text-muted-foreground/70">DAY </span>
              {dayLoading && dayPnl == null ? (
                <b className="font-semibold text-foreground">—</b>
              ) : dayPnl != null ? (
                <b
                  className={cn(
                    "font-semibold",
                    dayPnl >= 0 ? "text-pnl-positive" : "text-pnl-negative",
                  )}
                >
                  {formatSignedPnl(dayPnl)}
                </b>
              ) : (
                <b className="font-semibold text-foreground">—</b>
              )}
            </span>
          </>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        <ThemeSwitcher />
        <span className="min-w-[4.75rem] text-right text-muted-foreground">
          {clock}
        </span>
      </div>
    </header>
  );
}
