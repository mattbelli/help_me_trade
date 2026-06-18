"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
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
    <header className="flex h-11 shrink-0 items-center gap-4 border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 font-mono text-[11.5px]">
      <div className="flex items-center gap-2">
        <span
          className="grid size-5 place-items-center rounded-md text-[11px] font-extrabold text-[var(--c-on-accent)]"
          style={{
            backgroundImage:
              "linear-gradient(135deg, var(--c-accent), var(--c-violet))",
          }}
        >
          ▰
        </span>
        <span className="font-sans text-[13px] font-bold tracking-tight">
          help_me_trade
        </span>
      </div>

      <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--fg-dim)] capitalize">
        <span
          className={cn(
            "size-1.5 rounded-full",
            connected
              ? "bg-[var(--c-green)] shadow-[0_0_8px_var(--c-green)]"
              : "bg-[var(--fg-faint)]",
          )}
        />
        {broker}
      </span>

      {isPaper ? (
        <span className="inline-flex items-center rounded-full border border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--c-blue)]">
          paper
        </span>
      ) : null}

      <div className="ml-auto hidden items-center gap-5 text-[var(--fg-dim)] md:flex">
        {loading && !snapshot ? null : snapshot ? (
          <>
            <span>
              <span className="text-[var(--fg-faint)]">PORT </span>
              <b className="font-semibold text-foreground">
                {formatMoney(snapshot.account.portfolio_value)}
              </b>
            </span>
            <span>
              <span className="text-[var(--fg-faint)]">BP </span>
              <b className="font-semibold text-foreground">
                {formatCompactMoney(snapshot.account.buying_power)}
              </b>
            </span>
            <span>
              <span className="text-[var(--fg-faint)]">DAY </span>
              {dayLoading && dayPnl == null ? (
                <b className="font-semibold text-foreground">—</b>
              ) : dayPnl != null ? (
                <b
                  className={cn(
                    "font-semibold",
                    dayPnl >= 0
                      ? "text-[var(--c-green)]"
                      : "text-[var(--c-red)]",
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

      <div className="ml-auto flex items-center gap-1.5 md:ml-0">
        <ThemeSwitcher />
        <span className="min-w-[4.75rem] text-right text-[var(--fg-dim)]">
          {clock}
        </span>
      </div>
    </header>
  );
}
