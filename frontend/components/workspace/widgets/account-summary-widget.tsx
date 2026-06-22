"use client";

import { Eye, EyeOff } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney, type BrokerSnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  maskMoney,
  resolveAccountView,
  useDashboardPrefs,
} from "@/lib/workspace/use-dashboard-prefs";

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

function accountSelectLabel(entry: BrokerSnapshot): string {
  const base = entry.account_label ?? entry.broker;
  return entry.is_default ? `${base} (default)` : base;
}

function formatSignedMoney(value: number): string {
  return `${value >= 0 ? "+" : ""}${formatMoney(value)}`;
}

export function AccountSummaryWidget() {
  const { snapshot, loading } = useSnapshot();
  const {
    selectedAccountId,
    privacyMode,
    setSelectedAccountId,
    togglePrivacy,
  } = useDashboardPrefs();

  if (loading && !snapshot) {
    return (
      <div className="grid h-full grid-cols-2 gap-2.5 p-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-md" />
        ))}
      </div>
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

  const accountSnapshots = snapshot.snapshots ?? [];
  const showAccountSelect = accountSnapshots.length > 1;
  const portfolio = view.portfolio_value;
  const deployed =
    portfolio > 0
      ? Math.max(0, Math.round(((portfolio - view.cash) / portfolio) * 100))
      : null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] px-3 py-2">
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
          className="ml-auto rounded p-1 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          {privacyMode ? (
            <EyeOff className="size-3.5" />
          ) : (
            <Eye className="size-3.5" />
          )}
        </button>
      </div>
      <div className="grid flex-1 grid-cols-2 gap-2.5 p-3 lg:grid-cols-4">
        <Tile
          label="Portfolio"
          value={maskMoney(portfolio, privacyMode, formatMoney)}
        />
        <Tile
          label="Buying power"
          value={maskMoney(view.buying_power, privacyMode, formatMoney)}
          sub={deployed != null ? `${deployed}% deployed` : undefined}
        />
        <Tile
          label="Cash"
          value={maskMoney(view.cash, privacyMode, formatMoney)}
        />
        <Tile
          label="Unrealized P/L"
          value={maskMoney(view.unrealized_pl, privacyMode, formatSignedMoney)}
          valueClassName={
            privacyMode
              ? undefined
              : view.unrealized_pl >= 0
                ? "text-[var(--c-green)]"
                : "text-[var(--c-red)]"
          }
        />
      </div>
    </div>
  );
}
