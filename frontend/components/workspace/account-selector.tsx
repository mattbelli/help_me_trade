"use client";

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
import { useRealAccounts } from "@/lib/workspace/use-real-accounts";

function accountSelectLabel(entry: BrokerSnapshot): string {
  const base = entry.account_label ?? entry.broker;
  return entry.is_default ? `${base} (default)` : base;
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
        className="h-7 min-w-[7rem] border-[var(--border)] bg-[var(--bg-inset)] px-2 font-mono text-[10.5px] text-foreground"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="font-mono text-[11px]">
        <SelectItem value="overview">Overview — Real + Paper</SelectItem>

        {realAccounts.length > 0 ? (
          <SelectGroup>
            <SelectLabel>Real · Schwab (read-only)</SelectLabel>
            <SelectItem value="all_real">All Schwab</SelectItem>
            {realAccounts.map((acct) => (
              <SelectItem key={acct.id} value={acct.id}>
                {acct.label}
              </SelectItem>
            ))}
          </SelectGroup>
        ) : null}

        {paperSnapshots.length > 0 ? (
          <SelectGroup>
            <SelectLabel>Paper · Alpaca</SelectLabel>
            <SelectItem value="all_paper">All Alpaca</SelectItem>
            {paperSnapshots.map((s) =>
              s.account_id ? (
                <SelectItem key={s.account_id} value={s.account_id}>
                  {accountSelectLabel(s)}
                </SelectItem>
              ) : null,
            )}
          </SelectGroup>
        ) : null}
      </SelectContent>
    </Select>
  );
}
