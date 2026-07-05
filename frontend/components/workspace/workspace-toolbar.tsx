"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff, MessageSquareIcon } from "lucide-react";

import { PagesMenu } from "@/components/layout/pages-menu";
import { AccountSelector } from "@/components/workspace/account-selector";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { SettingsDialog } from "@/components/settings/settings-dialog";
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
import { useCopilotStore } from "@/lib/workspace/use-copilot-store";
import { useDayPnl } from "@/lib/workspace/use-day-pnl";
import {
  isRealScope,
  maskMoney,
  resolveAccountView,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
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

function moneyTypeBadgeKind(
  selectedAccountId: string,
): "real" | "paper" | "both" {
  if (selectedAccountId === "overview") return "both";
  if (isRealScope(selectedAccountId)) return "real";
  return "paper";
}

function MoneyTypeBadge({
  kind,
  className,
}: {
  kind: "real" | "paper" | "both";
  className?: string;
}) {
  if (kind === "both") {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-[3px] font-mono text-[9px] tracking-wide text-[var(--fg-dim)] uppercase",
          className,
        )}
      >
        Real + Paper
      </span>
    );
  }
  if (kind === "real") {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-full border border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] bg-[var(--bg-inset)] px-2.5 py-[3px] font-mono text-[9px] tracking-wide text-[var(--c-amber)] uppercase",
          className,
        )}
      >
        Real
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[var(--bg-inset)] px-2.5 py-[3px] font-mono text-[9px] tracking-wide text-[var(--c-blue)] uppercase",
        className,
      )}
    >
      Paper
    </span>
  );
}

function ToolbarTile({
  label,
  value,
  valueClassName,
  flashValue,
}: {
  label: string;
  value: string;
  valueClassName?: string;
  // Raw number the tile shows, for the tick-flash wash. Pass null (e.g. under
  // privacy mode) to suppress the flash.
  flashValue?: number | null;
}) {
  const flash = usePriceFlash(flashValue);
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-0.5 text-center leading-tight">
      <div className="font-mono text-[8px] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
        {label}
      </div>
      <div
        className={cn(
          "rounded font-mono text-[14px] font-semibold tabular-nums text-foreground",
          flash === "up" && "tick-flash-up",
          flash === "down" && "tick-flash-down",
          valueClassName,
        )}
      >
        {value}
      </div>
    </div>
  );
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
  const copilotOpen = useCopilotStore((s) => s.open);
  const toggleCopilot = useCopilotStore((s) => s.toggle);
  const {
    selectedAccountId,
    privacyMode,
    setSelectedAccountId,
    togglePrivacy,
  } = useDashboardPrefs();

  const { realAccounts, realSummary } = useRealAccounts();

  const view = resolveAccountView(snapshot, selectedAccountId);
  // Real (Schwab) balances for the toolbar tiles when a real scope is selected —
  // resolveAccountView returns null for real scopes (the paper/real firewall), so
  // the tiles must read from the Schwab source here instead.
  const realView = isRealScope(selectedAccountId)
    ? selectedAccountId === "all_real"
      ? {
          portfolio_value: realSummary.liquidation_value ?? 0,
          cash: realSummary.cash ?? 0,
          unrealized_pl: realSummary.unrealized_pl ?? 0,
          day_pl: realSummary.day_pl ?? 0,
        }
      : (() => {
          const acct = realAccounts.find((a) => a.id === selectedAccountId);
          return acct
            ? {
                portfolio_value: acct.liquidation_value,
                cash: acct.cash,
                unrealized_pl: acct.unrealized_pl,
                day_pl: acct.day_pl,
              }
            : null;
        })()
    : null;
  const paperSnapshots = (snapshot?.snapshots ?? []).filter(
    (s) => s.money_type !== "real",
  );
  const showAccountSelect =
    paperSnapshots.length > 1 || realAccounts.length > 0;
  const badgeKind = moneyTypeBadgeKind(selectedAccountId);

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
    <header className="relative flex h-[52px] shrink-0 items-center border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 font-mono text-[11.5px]">
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

        <PagesMenu />

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

        <MoneyTypeBadge kind={badgeKind} className="hidden sm:inline-flex" />
      </div>

      <div className="absolute left-1/2 hidden -translate-x-1/2 items-center lg:flex">
        <div className="flex items-center gap-1.5 text-[var(--fg-dim)]">
          {loading && !snapshot ? null : (
            <>
              {showAccountSelect ? (
                <AccountSelector
                  value={selectedAccountId}
                  onChange={setSelectedAccountId}
                />
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
              {selectedAccountId === "overview" ? (
                // Firewall: two separate portfolio totals, never one blended figure.
                <>
                  <ToolbarTile
                    label="REAL"
                    value={maskMoney(
                      realSummary.liquidation_value ?? 0,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={
                      privacyMode ? null : (realSummary.liquidation_value ?? 0)
                    }
                  />
                  <ToolbarTile
                    label="PAPER"
                    value={maskMoney(
                      snapshot?.all_accounts_summary?.portfolio_value ?? 0,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={
                      privacyMode
                        ? null
                        : (snapshot?.all_accounts_summary?.portfolio_value ?? 0)
                    }
                  />
                </>
              ) : realView ? (
                <>
                  <ToolbarTile
                    label="PORT"
                    value={maskMoney(
                      realView.portfolio_value,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={privacyMode ? null : realView.portfolio_value}
                  />
                  <ToolbarTile
                    label="CASH"
                    value={maskMoney(
                      realView.cash,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={privacyMode ? null : realView.cash}
                  />
                  <ToolbarTile
                    label="DAY"
                    value={maskMoney(
                      realView.day_pl,
                      privacyMode,
                      formatSignedPnl,
                    )}
                    flashValue={privacyMode ? null : realView.day_pl}
                    valueClassName={
                      !privacyMode && realView.day_pl !== 0
                        ? realView.day_pl >= 0
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : undefined
                    }
                  />
                  <ToolbarTile
                    label="UPL"
                    value={maskMoney(
                      realView.unrealized_pl,
                      privacyMode,
                      formatSignedPnl,
                    )}
                    flashValue={privacyMode ? null : realView.unrealized_pl}
                    valueClassName={
                      !privacyMode
                        ? realView.unrealized_pl >= 0
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : undefined
                    }
                  />
                </>
              ) : view ? (
                <>
                  <ToolbarTile
                    label="PORT"
                    value={maskMoney(
                      view.portfolio_value,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={privacyMode ? null : view.portfolio_value}
                  />
                  <ToolbarTile
                    label="BP"
                    value={maskMoney(
                      view.buying_power,
                      privacyMode,
                      formatCompactMoney,
                    )}
                    flashValue={privacyMode ? null : view.buying_power}
                  />
                  <ToolbarTile
                    label="DAY"
                    value={
                      dayLoading && dayPnl == null
                        ? "—"
                        : dayPnl != null
                          ? maskMoney(dayPnl, privacyMode, formatSignedPnl)
                          : "—"
                    }
                    flashValue={privacyMode ? null : dayPnl}
                    valueClassName={
                      !privacyMode && dayPnl != null
                        ? dayPnl >= 0
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : undefined
                    }
                  />
                  <ToolbarTile
                    label="UPL"
                    value={maskMoney(view.unrealized_pl, privacyMode, formatSignedPnl)}
                    flashValue={privacyMode ? null : view.unrealized_pl}
                    valueClassName={
                      !privacyMode
                        ? view.unrealized_pl >= 0
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : undefined
                    }
                  />
                </>
              ) : null}
            </>
          )}
        </div>
      </div>

      <div className="ml-auto flex items-center gap-1.5 md:gap-5">
        <button
          type="button"
          data-copilot-toggle
          onClick={toggleCopilot}
          title={`${copilotOpen ? "Close" : "Open"} copilot (Ctrl/Cmd+J)`}
          aria-label="Toggle copilot"
          aria-pressed={copilotOpen}
          className={cn(
            "inline-flex cursor-pointer items-center rounded p-1 outline-none transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]",
            copilotOpen
              ? "text-[var(--c-accent)]"
              : "text-[var(--fg-faint)]",
          )}
        >
          <MessageSquareIcon className="size-3.5" />
        </button>
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
