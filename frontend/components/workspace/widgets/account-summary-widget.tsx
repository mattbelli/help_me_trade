"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/api";
import { cn } from "@/lib/utils";

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

export function AccountSummaryWidget() {
  const { snapshot, loading } = useSnapshot();

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

  const unrealized = snapshot.positions.reduce(
    (sum, p) => sum + p.unrealized_pl,
    0,
  );
  const portfolio = snapshot.account.portfolio_value;
  const deployed =
    portfolio > 0
      ? Math.max(
          0,
          Math.round(
            ((portfolio - snapshot.account.cash) / portfolio) * 100,
          ),
        )
      : 0;

  return (
    <div className="grid h-full grid-cols-2 gap-2.5 p-3 lg:grid-cols-4">
      <Tile label="Portfolio" value={formatMoney(portfolio)} />
      <Tile
        label="Buying power"
        value={formatMoney(snapshot.account.buying_power)}
        sub={`${deployed}% deployed`}
      />
      <Tile label="Cash" value={formatMoney(snapshot.account.cash)} />
      <Tile
        label="Unrealized P/L"
        value={`${unrealized >= 0 ? "+" : ""}${formatMoney(unrealized)}`}
        valueClassName={
          unrealized >= 0 ? "text-[var(--c-green)]" : "text-[var(--c-red)]"
        }
      />
    </div>
  );
}
