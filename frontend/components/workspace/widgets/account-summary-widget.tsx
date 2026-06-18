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
    <div className="rounded-md border border-border bg-muted/40 p-3">
      <p className="font-mono text-[9px] tracking-widest text-muted-foreground uppercase">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 font-mono text-lg font-semibold tracking-tight tabular-nums",
          valueClassName,
        )}
      >
        {value}
      </p>
      {sub ? (
        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
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
      <p className="p-3 text-sm text-muted-foreground">
        Waiting for account snapshot…
      </p>
    );
  }

  const unrealized = snapshot.positions.reduce(
    (sum, p) => sum + p.unrealized_pl,
    0,
  );

  return (
    <div className="grid h-full grid-cols-2 gap-2.5 p-3 lg:grid-cols-4">
      <Tile
        label="Portfolio"
        value={formatMoney(snapshot.account.portfolio_value)}
      />
      <Tile
        label="Buying power"
        value={formatMoney(snapshot.account.buying_power)}
      />
      <Tile label="Cash" value={formatMoney(snapshot.account.cash)} />
      <Tile
        label="Unrealized P/L"
        value={formatMoney(unrealized)}
        valueClassName={
          unrealized >= 0 ? "text-pnl-positive" : "text-pnl-negative"
        }
      />
    </div>
  );
}
