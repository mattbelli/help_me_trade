"use client";

import { Eye, EyeOff } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney, type SchwabAccount } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  isPaperAggregateScope,
  maskMoney,
  resolveAccountView,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";

type TileProps = {
  label: string;
  value: string;
  sub?: string;
  valueClassName?: string;
};

function Tile({ label, value, sub, valueClassName }: TileProps) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--bg-inset)] px-3 py-2.5">
      <p className="font-mono text-[9px] tracking-[0.1em] text-[var(--fg-faint)] uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-1.5 font-mono text-[19px] font-semibold tracking-tight tabular-nums",
          valueClassName,
        )}
      >
        {value}
      </p>
      {sub ? (
        <p className="mt-0.5 font-mono text-[10.5px] text-[var(--fg-dim)]">
          {sub}
        </p>
      ) : null}
    </div>
  );
}

function MoneyTypeBadge({ kind }: { kind: "real" | "paper" | "both" }) {
  if (kind === "both") {
    return (
      <span className="inline-flex rounded-full border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-px font-mono text-[9px] tracking-wide text-[var(--fg-dim)] uppercase">
        Real + Paper
      </span>
    );
  }
  if (kind === "real") {
    return (
      <span className="inline-flex rounded-full border border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] bg-[var(--bg-inset)] px-2 py-px font-mono text-[9px] tracking-wide text-[var(--c-amber)] uppercase">
        Real
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full border border-[color-mix(in_srgb,var(--c-blue)_35%,transparent)] bg-[var(--bg-inset)] px-2 py-px font-mono text-[9px] tracking-wide text-[var(--c-blue)] uppercase">
      Paper
    </span>
  );
}

function formatSignedMoney(value: number): string {
  return `${value >= 0 ? "+" : ""}${formatMoney(value)}`;
}

function RealSummaryCard({
  title,
  portfolio,
  cash,
  longMarketValue,
  unrealizedPl,
  accountCount,
  privacyMode,
  muted,
}: {
  title: string;
  portfolio: number;
  cash: number;
  longMarketValue?: number;
  unrealizedPl?: number;
  accountCount?: number;
  privacyMode: boolean;
  muted?: string;
}) {
  if (muted) {
    return (
      <div className="flex min-w-0 flex-1 flex-col rounded-md border border-[var(--border)] bg-[var(--bg-inset)] p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="font-mono text-[10px] font-medium tracking-wide text-foreground uppercase">
            {title}
          </span>
          <MoneyTypeBadge kind="real" />
        </div>
        <p className="font-mono text-xs text-[var(--fg-dim)]">{muted}</p>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="mb-2 flex items-center gap-2 px-1">
        <span className="font-mono text-[10px] font-medium tracking-wide text-foreground uppercase">
          {title}
        </span>
        <MoneyTypeBadge kind="real" />
        {accountCount != null && accountCount > 1 ? (
          <span className="font-mono text-[9px] text-[var(--fg-faint)]">
            {accountCount} accounts
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <Tile
          label="Portfolio"
          value={maskMoney(portfolio, privacyMode, formatMoney)}
        />
        <Tile
          label="Cash"
          value={maskMoney(cash, privacyMode, formatMoney)}
        />
        {longMarketValue != null ? (
          <Tile
            label="Long market value"
            value={maskMoney(longMarketValue, privacyMode, formatMoney)}
          />
        ) : null}
        {unrealizedPl != null ? (
          <Tile
            label="Unrealized P/L"
            value={maskMoney(unrealizedPl, privacyMode, formatSignedMoney)}
            valueClassName={
              privacyMode
                ? undefined
                : unrealizedPl >= 0
                  ? "text-[var(--c-green)]"
                  : "text-[var(--c-red)]"
            }
          />
        ) : null}
      </div>
    </div>
  );
}

function PaperSummaryCard({
  title,
  portfolio,
  cash,
  buyingPower,
  unrealizedPl,
  accountCount,
  privacyMode,
}: {
  title: string;
  portfolio: number;
  cash: number;
  buyingPower: number;
  unrealizedPl: number;
  accountCount?: number;
  privacyMode: boolean;
}) {
  const deployed =
    portfolio > 0
      ? Math.max(0, Math.round(((portfolio - cash) / portfolio) * 100))
      : null;

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="mb-2 flex items-center gap-2 px-1">
        <span className="font-mono text-[10px] font-medium tracking-wide text-foreground uppercase">
          {title}
        </span>
        <MoneyTypeBadge kind="paper" />
        {accountCount != null && accountCount > 1 ? (
          <span className="font-mono text-[9px] text-[var(--fg-faint)]">
            {accountCount} accounts
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <Tile
          label="Portfolio"
          value={maskMoney(portfolio, privacyMode, formatMoney)}
        />
        <Tile
          label="Buying power"
          value={maskMoney(buyingPower, privacyMode, formatMoney)}
          sub={deployed != null ? `${deployed}% deployed` : undefined}
        />
        <Tile
          label="Cash"
          value={maskMoney(cash, privacyMode, formatMoney)}
        />
        <Tile
          label="Unrealized P/L"
          value={maskMoney(unrealizedPl, privacyMode, formatSignedMoney)}
          valueClassName={
            privacyMode
              ? undefined
              : unrealizedPl >= 0
                ? "text-[var(--c-green)]"
                : "text-[var(--c-red)]"
          }
        />
      </div>
    </div>
  );
}

function findRealAccount(
  accounts: SchwabAccount[],
  id: string,
): SchwabAccount | undefined {
  return accounts.find((a) => a.id === id);
}

export function AccountSummaryWidget() {
  const { snapshot, loading } = useSnapshot();
  const { selectedAccountId, privacyMode, togglePrivacy } = useDashboardPrefs();
  const {
    realAccounts,
    realSummary,
    realAuthenticated,
    realLoading,
  } = useRealAccounts();

  if ((loading && !snapshot) || (realLoading && selectedAccountId === "overview")) {
    return (
      <div className="grid h-full grid-cols-2 gap-2.5 p-3">
        {Array.from({ length: 2 }).map((_, i) => (
          <Skeleton key={i} className="h-32 rounded-md" />
        ))}
      </div>
    );
  }

  if (!snapshot && !selectedAccountId.startsWith("schwab_") && selectedAccountId !== "all_real") {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        Waiting for account snapshot…
      </p>
    );
  }

  const renderBody = () => {
    if (selectedAccountId === "overview") {
      const paper = snapshot?.all_accounts_summary;
      return (
        <div className="flex flex-col gap-4 lg:flex-row">
          <RealSummaryCard
            title="Real · Schwab"
            portfolio={realSummary.liquidation_value ?? 0}
            cash={realSummary.cash ?? 0}
            accountCount={realSummary.account_count}
            privacyMode={privacyMode}
            muted={
              !realAuthenticated ? "Schwab not connected" : undefined
            }
          />
          {paper ? (
            <PaperSummaryCard
              title="Paper · Alpaca"
              portfolio={paper.portfolio_value}
              cash={paper.cash}
              buyingPower={paper.buying_power}
              unrealizedPl={paper.unrealized_pl}
              accountCount={paper.account_count}
              privacyMode={privacyMode}
            />
          ) : (
            <p className="flex min-w-0 flex-1 items-center p-3 font-mono text-xs text-[var(--fg-dim)]">
              Waiting for paper snapshot…
            </p>
          )}
        </div>
      );
    }

    if (selectedAccountId === "all_real") {
      return (
        <RealSummaryCard
          title="All Schwab"
          portfolio={realSummary.liquidation_value ?? 0}
          cash={realSummary.cash ?? 0}
          longMarketValue={realSummary.long_market_value}
          unrealizedPl={realSummary.unrealized_pl}
          accountCount={realSummary.account_count}
          privacyMode={privacyMode}
          muted={!realAuthenticated ? "Schwab not connected" : undefined}
        />
      );
    }

    if (selectedAccountId.startsWith("schwab_")) {
      const acct = findRealAccount(realAccounts, selectedAccountId);
      if (!acct) {
        return (
          <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
            {realAuthenticated
              ? "Schwab account not found…"
              : "Schwab not connected"}
          </p>
        );
      }
      return (
        <RealSummaryCard
          title={acct.label}
          portfolio={acct.liquidation_value}
          cash={acct.cash}
          longMarketValue={acct.long_market_value}
          unrealizedPl={acct.unrealized_pl}
          privacyMode={privacyMode}
        />
      );
    }

    if (!snapshot) {
      return (
        <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
          Waiting for account snapshot…
        </p>
      );
    }

    const view = resolveAccountView(snapshot, selectedAccountId);
    if (!view) {
      return (
        <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
          Waiting for account snapshot…
        </p>
      );
    }

    const title = isPaperAggregateScope(selectedAccountId)
      ? "All Alpaca"
      : view.label;

    return (
      <PaperSummaryCard
        title={title}
        portfolio={view.portfolio_value}
        cash={view.cash}
        buyingPower={view.buying_power}
        unrealizedPl={view.unrealized_pl}
        accountCount={
          isPaperAggregateScope(selectedAccountId)
            ? snapshot.all_accounts_summary?.account_count
            : undefined
        }
        privacyMode={privacyMode}
      />
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center border-b border-[var(--border)] px-3 py-2">
        <button
          type="button"
          onClick={togglePrivacy}
          title={privacyMode ? "Show balances" : "Hide balances"}
          className="ml-auto rounded p-1 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          {privacyMode ? (
            <EyeOff className="size-3.5" />
          ) : (
            <Eye className="size-3.5" />
          )}
        </button>
      </div>
      <div className="flex-1 p-3">{renderBody()}</div>
    </div>
  );
}
