"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff, RefreshCw } from "lucide-react";

import { AccountSelector } from "@/components/workspace/account-selector";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { StandInMark } from "@/components/workspace/stand-in-mark";
import {
  EquityFeedPanel,
  FlowFeedPanel,
  useCollectorStats,
} from "@/components/workspace/feed-stats-panel";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  fetchBroker,
  fetchSettings,
  formatMoney,
  type AlpacaDataFeedInfo,
  type BrokerInfo,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useDayPnl, useLedgerDayPnl } from "@/lib/workspace/use-day-pnl";
import {
  isRealScope,
  resolveAccountView,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import { useEtClock } from "@/lib/workspace/use-et-clock";
import { ThemeSwitcher } from "./theme-switcher";
import { NotificationBell } from "./notification-bell";

type MarketSession = "pre" | "regular" | "post" | "overnight" | "closed";

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

/** Mirrors ``brokers/market_clock.py`` for feed-chip session labels. */
function marketSessionNow(now = new Date()): MarketSession {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  }).formatToParts(now);

  const weekday = parts.find((p) => p.type === "weekday")?.value ?? "Mon";
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  const wdMap: Record<string, number> = {
    Mon: 0,
    Tue: 1,
    Wed: 2,
    Thu: 3,
    Fri: 4,
    Sat: 5,
    Sun: 6,
  };
  const wd = wdMap[weekday] ?? 0;
  const minutes = hour * 60 + minute;

  if (wd <= 4) {
    if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return "pre";
    if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return "regular";
    if (minutes >= 16 * 60 && minutes < 20 * 60) return "post";
  }
  if (
    (wd === 4 && minutes >= 20 * 60) ||
    wd === 5 ||
    (wd === 6 && minutes < 20 * 60)
  ) {
    return "closed";
  }
  return "overnight";
}

function feedChipDisplay(
  dataFeed: AlpacaDataFeedInfo,
  session: MarketSession,
): { label: string; paid: boolean; dimmed: boolean } {
  if (dataFeed.tier === "paid") {
    return {
      label: session === "overnight" ? "BOATS" : "SIP",
      paid: true,
      dimmed: false,
    };
  }
  return {
    label: "IEX",
    paid: false,
    dimmed: session === "overnight",
  };
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
  flashValue?: number | null;
}) {
  const flash = usePriceFlash(flashValue);
  return (
    <div className="rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-0.5 text-center leading-tight">
      <div className="font-mono text-[length:var(--fs-micro)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
        {label}
      </div>
      <div
        className={cn(
          "rounded font-mono text-[length:var(--fs-emph)] font-semibold tabular-nums text-foreground",
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
  const {
    selectedAccountId,
    privacyMode,
    setSelectedAccountId,
    togglePrivacy,
  } = useDashboardPrefs();

  const { realAccounts, realSummary } = useRealAccounts();

  const { dayPnl: realDayPnl, loading: realDayLoading } = useLedgerDayPnl(
    isRealScope(selectedAccountId) ? selectedAccountId : "",
  );

  const view = resolveAccountView(snapshot, selectedAccountId);
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

  const [brokerOpen, setBrokerOpen] = useState(false);
  const [brokerDetails, setBrokerDetails] = useState<BrokerInfo | null>(null);
  const [paperMode, setPaperMode] = useState<boolean | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [feedRechecking, setFeedRechecking] = useState(false);
  const [opraOpen, setOpraOpen] = useState(false);
  // Live collector stats: polled once a second, only while a feed dropdown is open.
  const { stats: collectorStats } = useCollectorStats(brokerOpen || opraOpen);
  const flowSnap = collectorStats?.flow ?? null;
  const flowLive = !!flowSnap && !flowSnap.stale && flowSnap.connected;

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
  const alpacaDataFeed = details?.brokers?.alpaca?.data_feed;
  const marketSession = marketSessionNow();
  const feedChip = alpacaDataFeed
    ? feedChipDisplay(alpacaDataFeed, marketSession)
    : null;

  const recheckAlpacaFeed = () => {
    setFeedRechecking(true);
    fetchBroker({ recheckFeed: true })
      .then((info) => {
        setBrokerDetails(info);
      })
      .catch(() => {
        /* keep prior details on failure */
      })
      .finally(() => {
        setFeedRechecking(false);
      });
  };

  const metricsTiles =
    loading && !snapshot ? null : selectedAccountId === "overview" ? (
      <>
        <ToolbarTile
          label="REAL"
          value={formatCompactMoney(realSummary.liquidation_value ?? 0)}
          flashValue={realSummary.liquidation_value ?? 0}
        />
        <ToolbarTile
          label="PAPER"
          value={formatCompactMoney(
            snapshot?.all_accounts_summary?.portfolio_value ?? 0,
          )}
          flashValue={snapshot?.all_accounts_summary?.portfolio_value ?? 0}
        />
      </>
    ) : realView ? (
      <>
        <ToolbarTile
          label="PORT"
          value={formatCompactMoney(realView.portfolio_value)}
          flashValue={realView.portfolio_value}
        />
        <ToolbarTile
          label="CASH"
          value={formatCompactMoney(realView.cash)}
          flashValue={realView.cash}
        />
        <ToolbarTile
          label="DAY"
          value={
            realDayLoading && realDayPnl == null
              ? "—"
              : realDayPnl != null
                ? formatSignedPnl(realDayPnl)
                : "—"
          }
          flashValue={realDayPnl}
          valueClassName={
            realDayPnl != null && realDayPnl !== 0
              ? realDayPnl >= 0
                ? "text-[var(--c-green)]"
                : "text-[var(--c-red)]"
              : undefined
          }
        />
        <ToolbarTile
          label="UPL"
          value={formatSignedPnl(realView.unrealized_pl)}
          flashValue={realView.unrealized_pl}
          valueClassName={
            realView.unrealized_pl >= 0
              ? "text-[var(--c-green)]"
              : "text-[var(--c-red)]"
          }
        />
      </>
    ) : view ? (
      <>
        <ToolbarTile
          label="PORT"
          value={formatCompactMoney(view.portfolio_value)}
          flashValue={view.portfolio_value}
        />
        <ToolbarTile
          label="BP"
          value={formatCompactMoney(view.buying_power)}
          flashValue={view.buying_power}
        />
        <ToolbarTile
          label="DAY"
          value={
            dayLoading && dayPnl == null
              ? "—"
              : dayPnl != null
                ? formatSignedPnl(dayPnl)
                : "—"
          }
          flashValue={dayPnl}
          valueClassName={
            dayPnl != null
              ? dayPnl >= 0
                ? "text-[var(--c-green)]"
                : "text-[var(--c-red)]"
              : undefined
          }
        />
        <ToolbarTile
          label="UPL"
          value={formatSignedPnl(view.unrealized_pl)}
          flashValue={view.unrealized_pl}
          valueClassName={
            view.unrealized_pl >= 0
              ? "text-[var(--c-green)]"
              : "text-[var(--c-red)]"
          }
        />
      </>
    ) : null;

  return (
    <header className="flex h-[52px] shrink-0 items-center border-b border-[var(--border)] bg-[var(--bg-raised)] px-3.5 font-mono text-[length:var(--fs-title)]">
      <div className="flex min-w-0 items-center gap-2 sm:gap-4">
        <div className="flex items-center gap-2">
          <StandInMark duo className="size-6" />
          <span className="hidden font-mono text-[length:var(--fs-emph)] font-bold tracking-[0.08em] sm:inline">
            HMT
          </span>
        </div>

        <span
          className="hidden h-4 w-px shrink-0 bg-[var(--border)] sm:block"
          aria-hidden
        />

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
            className={cn(
              "inline-flex cursor-pointer outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]",
              feedChip
                ? "items-center gap-2 rounded px-2.5 py-[5px] hover:bg-[var(--bg-hover)]"
                : "items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-[3px] text-[var(--fg-dim)] capitalize hover:border-[var(--border-strong)] hover:bg-[var(--bg-hover)]",
            )}
            title={
              feedChip
                ? `Alpaca · ${feedChip.label} feed · ${isPaper ? "paper" : "live"} account`
                : undefined
            }
          >
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                connected
                  ? "bg-[var(--c-green-bg)] shadow-[0_0_8px_var(--c-green-bg)]"
                  : "bg-[var(--fg-faint)]",
              )}
            />
            {feedChip ? (
              <span className="inline-flex items-baseline gap-1.5">
                <span
                  className={cn(
                    "font-mono text-[length:var(--fs-body)] font-bold tracking-[0.08em] uppercase",
                    feedChip.paid
                      ? "text-[var(--c-accent)]"
                      : feedChip.dimmed
                        ? "text-[var(--fg-faint)]"
                        : "text-[var(--fg-dim)]",
                  )}
                >
                  {feedChip.label}
                </span>
                <span
                  className={cn(
                    "font-mono text-[length:var(--fs-micro)] tracking-[0.14em] uppercase",
                    isPaper ? "text-[var(--c-amber)]" : "text-[var(--c-green)]",
                  )}
                >
                  {isPaper ? "PAPER" : "REAL"}
                </span>
              </span>
            ) : (
              <span className="capitalize">{broker}</span>
            )}
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="w-72 border-[var(--border)] bg-[var(--panel)] p-3 font-mono text-[length:var(--fs-body)] text-foreground shadow-[var(--panel-shadow)]"
          >
            {detailsLoading ? (
              <p className="text-[var(--fg-dim)]">Loading…</p>
            ) : (
              <dl className="space-y-2">
                <div>
                  <dt className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                    Primary broker
                  </dt>
                  <dd className="mt-0.5 capitalize text-foreground">{primary}</dd>
                </div>
                <div>
                  <dt className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                    Environment
                  </dt>
                  <dd className="mt-0.5">
                    <span
                      className={cn(
                        "inline-flex rounded-full border px-2 py-px text-[length:var(--fs-table)]",
                        isPaper
                          ? "border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] text-[var(--c-amber)]"
                          : "border-[color-mix(in_srgb,var(--c-green)_35%,transparent)] text-[var(--c-green)]",
                      )}
                    >
                      {isPaper ? "paper" : "live"}
                    </span>
                  </dd>
                </div>
                {auth ? (
                  <div>
                    <dt className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                      Connection
                    </dt>
                    <dd className="mt-0.5 flex items-start gap-1.5">
                      <span
                        className={cn(
                          "mt-1 size-1.5 shrink-0 rounded-full",
                          auth.authenticated
                            ? "bg-[var(--c-green-bg)]"
                            : "bg-[var(--c-red)]",
                        )}
                      />
                      <span className="text-[var(--fg-dim)]">{auth.message}</span>
                    </dd>
                  </div>
                ) : null}
                {details?.brokers?.alpaca?.configured && alpacaDataFeed ? (
                  <div>
                    <dt className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                      Market data
                    </dt>
                    <dd className="mt-0.5">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p
                            className={cn(
                              alpacaDataFeed.feed === "sip"
                                ? "text-[var(--c-green)]"
                                : "text-[var(--fg-dim)]",
                            )}
                          >
                            {alpacaDataFeed.feed === "sip"
                              ? "SIP · real-time"
                              : "IEX · free"}
                            {alpacaDataFeed.configured === "auto" ? (
                              <span className="ml-1.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                                auto-detected
                              </span>
                            ) : null}
                          </p>
                          {alpacaDataFeed.feed === "iex" ? (
                            <p className="mt-0.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                              overnight delayed ~15m
                            </p>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          title="Recheck data feed tier"
                          disabled={feedRechecking}
                          onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            recheckAlpacaFeed();
                          }}
                          className="inline-flex shrink-0 items-center gap-0.5 rounded px-1 py-0.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--fg-dim)] disabled:opacity-50"
                        >
                          <RefreshCw
                            className={cn(
                              "size-3",
                              feedRechecking && "animate-spin",
                            )}
                          />
                          recheck
                        </button>
                      </div>
                    </dd>
                  </div>
                ) : null}
                <div>
                  <dt className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                    Last update
                  </dt>
                  <dd className="mt-0.5 text-[var(--fg-dim)]">
                    {snapshot?.captured_at
                      ? formatCapturedAt(snapshot.captured_at)
                      : "No snapshot yet"}
                  </dd>
                </div>
                <div className="border-t border-[var(--border)] pt-2">
                  <EquityFeedPanel snap={collectorStats?.equity ?? null} />
                </div>
              </dl>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu open={opraOpen} onOpenChange={setOpraOpen}>
          <DropdownMenuTrigger
            className="inline-flex cursor-pointer items-center gap-2 rounded px-2.5 py-[5px] outline-none transition-colors hover:bg-[var(--bg-hover)] focus-visible:ring-2 focus-visible:ring-[var(--c-accent)]"
            title="Databento OPRA · options flow collector (Rust)"
          >
            <span
              className={cn(
                "size-1.5 shrink-0 rounded-full",
                flowLive
                  ? "bg-[var(--c-green-bg)] shadow-[0_0_8px_var(--c-green-bg)]"
                  : collectorStats
                    ? "bg-[var(--c-red)]"
                    : "bg-[var(--fg-faint)]",
              )}
            />
            <span className="font-mono text-[length:var(--fs-body)] font-bold tracking-[0.08em] text-[var(--c-accent)] uppercase">
              OPRA
            </span>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="w-72 border-[var(--border)] bg-[var(--panel)] p-3 font-mono text-[length:var(--fs-body)] text-foreground shadow-[var(--panel-shadow)]"
          >
            <FlowFeedPanel snap={flowSnap} />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="min-w-6 flex-1" aria-hidden />

      <div className="ml-auto flex items-center gap-1.5 md:gap-3">
        <div className="hidden items-center gap-1.5 lg:flex">
          {showAccountSelect ? (
            <AccountSelector
              value={selectedAccountId}
              onChange={setSelectedAccountId}
            />
          ) : null}
          <div
            className={cn(
              "flex items-center gap-1.5 overflow-hidden transition-[max-width,opacity,transform] duration-[240ms] ease-out",
              privacyMode
                ? "pointer-events-none max-w-0 translate-x-2 opacity-0"
                : "max-w-[640px] translate-x-0 opacity-100",
            )}
          >
            {metricsTiles}
          </div>
          <button
            type="button"
            onClick={togglePrivacy}
            title={privacyMode ? "Show balances" : "Hide balances"}
            className="shrink-0 rounded p-1 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          >
            {privacyMode ? (
              <EyeOff className="size-3.5" />
            ) : (
              <Eye className="size-3.5" />
            )}
          </button>
        </div>

        <div
          className="hidden h-[18px] w-px shrink-0 bg-[var(--border)] lg:block"
          aria-hidden
        />

        <NotificationBell />
        <SettingsDialog collapsed />
        <ThemeSwitcher />
        <span
          className="h-4 w-px shrink-0 bg-[var(--border)]"
          aria-hidden
        />
        <span className="min-w-[4.75rem] text-right text-[var(--fg-dim)]">
          {clock}
        </span>
      </div>
    </header>
  );
}
