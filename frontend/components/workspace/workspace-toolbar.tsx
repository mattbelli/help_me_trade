"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchBroker,
  fetchSettings,
  formatMoney,
  type BrokerInfo,
  type BrokerSnapshot,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDayPnl } from "@/lib/workspace/use-day-pnl";
import {
  maskMoney,
  resolveAccountView,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";
import { useEtClock } from "@/lib/workspace/use-et-clock";
import { ThemeSwitcher } from "./theme-switcher";
import { NotificationBell } from "./notification-bell";

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

function accountSelectLabel(entry: BrokerSnapshot): string {
  const base = entry.account_label ?? entry.broker;
  return entry.is_default ? `${base} (default)` : base;
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
  const {
    selectedAccountId,
    privacyMode,
    setSelectedAccountId,
    togglePrivacy,
  } = useDashboardPrefs();

  const view = resolveAccountView(snapshot, selectedAccountId);
  const accountSnapshots = snapshot?.snapshots ?? [];
  const showAccountSelect = accountSnapshots.length > 1;

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
    <header className="relative flex h-11 shrink-0 items-center border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 font-mono text-[11.5px]">
      <div className="flex min-w-0 items-center gap-2 sm:gap-4">
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
          <span className="hidden font-sans text-[13px] font-bold tracking-tight sm:inline">
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
          <span className="hidden items-center rounded-full border border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--c-blue)] sm:inline-flex">
            paper
          </span>
        ) : null}
      </div>

      <div className="absolute left-1/2 hidden -translate-x-1/2 items-center lg:flex">
        <div className="flex items-center gap-2.5 text-[11px] text-[var(--fg-dim)]">
          {loading && !snapshot ? null : snapshot && view ? (
            <>
              {showAccountSelect ? (
                <Select
                  value={selectedAccountId}
                  onValueChange={(v) => setSelectedAccountId(v ?? "all")}
                >
                  <SelectTrigger
                    size="sm"
                    className="h-7 min-w-[7rem] border-[var(--border)] bg-[var(--bg-inset)] px-2 font-mono text-[10.5px] text-foreground"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="font-mono text-[11px]">
                    <SelectItem value="all">All accounts</SelectItem>
                    {accountSnapshots.map((s) =>
                      s.account_id ? (
                        <SelectItem key={s.account_id} value={s.account_id}>
                          {accountSelectLabel(s)}
                        </SelectItem>
                      ) : null,
                    )}
                  </SelectContent>
                </Select>
              ) : null}
              <button
                type="button"
                onClick={togglePrivacy}
                title={privacyMode ? "Show balances" : "Hide balances"}
                className="rounded p-1 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
              >
                {privacyMode ? (
                  <EyeOff className="size-3.5" />
                ) : (
                  <Eye className="size-3.5" />
                )}
              </button>
              <span>
                <span className="text-[var(--fg-faint)]">PORT </span>
                <b className="font-semibold text-foreground">
                  {maskMoney(
                    view.portfolio_value,
                    privacyMode,
                    formatCompactMoney,
                  )}
                </b>
              </span>
              <span>
                <span className="text-[var(--fg-faint)]">BP </span>
                <b className="font-semibold text-foreground">
                  {maskMoney(
                    view.buying_power,
                    privacyMode,
                    formatCompactMoney,
                  )}
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
                      privacyMode
                        ? "text-foreground"
                        : dayPnl >= 0
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]",
                    )}
                  >
                    {maskMoney(dayPnl, privacyMode, formatSignedPnl)}
                  </b>
                ) : (
                  <b className="font-semibold text-foreground">—</b>
                )}
              </span>
              <span>
                <span className="text-[var(--fg-faint)]">UPL </span>
                <b
                  className={cn(
                    "font-semibold",
                    privacyMode
                      ? "text-foreground"
                      : view.unrealized_pl >= 0
                        ? "text-[var(--c-green)]"
                        : "text-[var(--c-red)]",
                  )}
                >
                  {maskMoney(
                    view.unrealized_pl,
                    privacyMode,
                    formatSignedPnl,
                  )}
                </b>
              </span>
            </>
          ) : null}
        </div>
      </div>

      <div className="ml-auto flex items-center gap-1.5 md:gap-5">
        <NotificationBell />
        <SettingsDialog collapsed />
        <ThemeSwitcher />
        <span className="min-w-[4.75rem] text-right text-[var(--fg-dim)]">
          {clock}
        </span>
      </div>
    </header>
  );
}
