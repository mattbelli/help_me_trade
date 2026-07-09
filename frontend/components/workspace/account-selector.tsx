"use client";

import type { ReactNode } from "react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type BrokerSnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";

function accountSelectLabel(entry: BrokerSnapshot): string {
  const base = entry.account_label ?? entry.broker;
  return entry.is_default ? `${base} (default)` : base;
}

function AccountRow({
  dotClassName,
  children,
}: {
  dotClassName?: string;
  children: ReactNode;
}) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      {dotClassName ? (
        <span
          className={cn("size-1.5 shrink-0 rounded-full", dotClassName)}
          aria-hidden
        />
      ) : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

type AccountSelectorProps = {
  value: string;
  onChange: (v: string) => void;
};

export function AccountSelector({ value, onChange }: AccountSelectorProps) {
  const { snapshot } = useSnapshot();
  const { realAccounts } = useRealAccounts();

  const paperSnapshots = (snapshot?.snapshots ?? []).filter(
    (s) => s.money_type !== "real",
  );

  return (
    <Select value={value} onValueChange={(v) => onChange(v ?? "overview")}>
      <SelectTrigger
        size="sm"
        className="h-7 max-w-[13rem] gap-1 border-0 bg-transparent px-0 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] shadow-none transition-colors hover:bg-transparent hover:text-foreground focus-visible:ring-0 [&_svg]:size-3 [&_svg]:text-[var(--fg-dim)]"
      >
        <SelectValue className="truncate" />
      </SelectTrigger>
      <SelectContent className="min-w-[15rem] font-mono text-[length:var(--fs-body)]">
        <SelectItem value="overview" className="px-2.5 py-2">
          Overview — Real + Paper
        </SelectItem>

        {realAccounts.length > 0 ? (
          <SelectGroup className="p-0">
            <SelectLabel className="px-2.5 pt-2 pb-1 font-mono text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--c-green)] uppercase">
              Real · Schwab (read-only)
            </SelectLabel>
            <SelectItem value="all_real" className="px-2.5 py-2">
              <AccountRow dotClassName="bg-[var(--c-green)]">All Schwab</AccountRow>
            </SelectItem>
            {realAccounts.map((acct) => (
              <SelectItem key={acct.id} value={acct.id} className="px-2.5 py-2">
                <AccountRow dotClassName="bg-[var(--c-green)]">
                  {acct.label}
                </AccountRow>
              </SelectItem>
            ))}
          </SelectGroup>
        ) : null}

        {paperSnapshots.length > 0 ? (
          <SelectGroup className="p-0">
            <SelectLabel className="px-2.5 pt-2 pb-1 font-mono text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--c-amber)] uppercase">
              Paper · Alpaca
            </SelectLabel>
            <SelectItem value="all_paper" className="px-2.5 py-2">
              <AccountRow dotClassName="bg-[var(--c-amber)]">
                All Alpaca
              </AccountRow>
            </SelectItem>
            {paperSnapshots.map((s) =>
              s.account_id ? (
                <SelectItem
                  key={s.account_id}
                  value={s.account_id}
                  className="px-2.5 py-2"
                >
                  <AccountRow dotClassName="bg-[var(--c-amber)]">
                    {accountSelectLabel(s)}
                  </AccountRow>
                </SelectItem>
              ) : null,
            )}
          </SelectGroup>
        ) : null}
      </SelectContent>
    </Select>
  );
}
