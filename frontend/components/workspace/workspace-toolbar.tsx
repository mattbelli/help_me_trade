"use client";

import { useEffect, useState } from "react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  fetchBroker,
  fetchSettings,
  formatMoney,
  type BrokerInfo,
} from "@/lib/api";
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

function formatCapturedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function brokerAuth(
  info: BrokerInfo | null,
  primary: string,
): { authenticated: boolean; message: string } | null {
  if (!info) return null;
  const entry = info.brokers?.[primary];
  if (entry?.auth) {
    return {
      authenticated: entry.auth.authenticated,
      message: entry.auth.message,
    };
  }
  if (info.auth) {
    return {
      authenticated: info.auth.authenticated,
      message: info.auth.message,
    };
  }
  if (entry) {
    return {
      authenticated: entry.configured,
      message: entry.configured ? "Configured" : "Not configured",
    };
  }
  return null;
}

export function WorkspaceToolbar() {
  const { snapshot, brokerInfo, loading } = useSnapshot();
  const { dayPnl, loading: dayLoading } = useDayPnl();
  const clock = useEtClock();

  const [brokerOpen, setBrokerOpen] = useState(false);
  const [brokerDetails, setBrokerDetails] = useState<BrokerInfo | null>(null);
  const [paperMode, setPaperMode] = useState<boolean | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const broker =
    snapshot?.primary_broker ??
    snapshot?.broker ??
    brokerInfo?.primary_broker ??
    brokerInfo?.broker ??
    "—";

  const isPaper =
    paperMode ??
    (broker === "alpaca" ||
      brokerInfo?.primary_broker === "alpaca" ||
      snapshot?.broker === "alpaca");

  const connected =
    brokerInfo?.brokers?.[broker]?.configured ??
    brokerInfo?.auth?.authenticated ??
    Boolean(snapshot);

  useEffect(() => {
    if (!brokerOpen) return;

    let cancelled = false;

    Promise.all([fetchBroker(), fetchSettings()])
      .then(([info, settings]) => {
        if (cancelled) return;
        setBrokerDetails(info);
        const primary =
          info.primary_broker ?? info.broker ?? settings.primary_broker;
        if (primary === "alpaca") {
          setPaperMode(
            settings.brokers.alpaca.api_base_url.includes("paper"),
          );
        } else {
          setPaperMode(false);
        }
      })
      .catch(() => {
        if (!cancelled) setBrokerDetails(brokerInfo);
      })
      .finally(() => {
        if (!cancelled) setDetailsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [brokerInfo, brokerOpen]);

  const details = brokerDetails ?? brokerInfo;
  const primary =
    details?.primary_broker ?? details?.broker ?? broker;
  const auth = brokerAuth(details, primary);

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

      <DropdownMenu
        open={brokerOpen}
        onOpenChange={(open) => {
          setBrokerOpen(open);
          if (open) {
            setDetailsLoading(true);
            setBrokerDetails(null);
            setPaperMode(null);
          }
        }}
      >
        <DropdownMenuTrigger
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--fg-dim)] capitalize outline-none transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)] focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]"
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              connected
                ? "bg-[var(--c-green)] shadow-[0_0_8px_var(--c-green)]"
                : "bg-[var(--fg-faint)]",
            )}
          />
          {broker}
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="w-64 border-[var(--border)] bg-[var(--panel)] p-3 font-mono text-[11px] text-foreground shadow-[var(--panel-shadow)]"
        >
          {detailsLoading ? (
            <p className="text-[var(--fg-dim)]">Loading…</p>
          ) : (
            <dl className="space-y-2">
              <div>
                <dt className="text-[9.5px] tracking-wide text-[var(--fg-faint)] uppercase">
                  Primary broker
                </dt>
                <dd className="mt-0.5 capitalize text-foreground">{primary}</dd>
              </div>
              <div>
                <dt className="text-[9.5px] tracking-wide text-[var(--fg-faint)] uppercase">
                  Environment
                </dt>
                <dd className="mt-0.5">
                  <span
                    className={cn(
                      "inline-flex rounded-full border px-2 py-px text-[10px]",
                      isPaper
                        ? "border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] text-[var(--c-blue)]"
                        : "border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] text-[var(--c-amber)]",
                    )}
                  >
                    {isPaper ? "paper" : "live"}
                  </span>
                </dd>
              </div>
              {auth ? (
                <div>
                  <dt className="text-[9.5px] tracking-wide text-[var(--fg-faint)] uppercase">
                    Connection
                  </dt>
                  <dd className="mt-0.5 flex items-start gap-1.5">
                    <span
                      className={cn(
                        "mt-1 size-1.5 shrink-0 rounded-full",
                        auth.authenticated
                          ? "bg-[var(--c-green)]"
                          : "bg-[var(--c-red)]",
                      )}
                    />
                    <span className="text-[var(--fg-dim)]">{auth.message}</span>
                  </dd>
                </div>
              ) : null}
              <div>
                <dt className="text-[9.5px] tracking-wide text-[var(--fg-faint)] uppercase">
                  Last update
                </dt>
                <dd className="mt-0.5 text-[var(--fg-dim)]">
                  {snapshot?.captured_at
                    ? formatCapturedAt(snapshot.captured_at)
                    : "No snapshot yet"}
                </dd>
              </div>
            </dl>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

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
