"use client";

import { useState } from "react";

import { AccountCards } from "@/components/dashboard/account-cards";
import { OrdersTable } from "@/components/dashboard/orders-table";
import { PositionsTable } from "@/components/dashboard/positions-table";
import { PageHeader } from "@/components/layout/page-header";
import { PnlChart } from "@/components/overview/pnl-chart";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Skeleton } from "@/components/ui/skeleton";
import type { BrokerSnapshot, PnlRange } from "@/lib/api";

function BrokerSection({ snap }: { snap: BrokerSnapshot }) {
  return (
    <section className="space-y-6">
      <AccountCards account={snap.account} broker={snap.broker} />
      <div className="grid gap-6 xl:grid-cols-2">
        <PositionsTable positions={snap.positions} broker={snap.broker} />
        <OrdersTable orders={snap.open_orders} broker={snap.broker} />
      </div>
    </section>
  );
}

export function OverviewPage() {
  const { snapshot, error, loading } = useSnapshot();
  const [pnlRange, setPnlRange] = useState<PnlRange>("1W");

  const brokerSnapshots =
    snapshot?.snapshots && snapshot.snapshots.length > 0
      ? snapshot.snapshots
      : snapshot
        ? [
            {
              broker: snapshot.broker ?? "broker",
              captured_at: snapshot.captured_at,
              account: snapshot.account,
              position_count: snapshot.position_count,
              positions: snapshot.positions,
              open_order_count: snapshot.open_order_count,
              open_orders: snapshot.open_orders,
            },
          ]
        : [];

  const multiBroker = brokerSnapshots.length > 1;

  if (loading && !snapshot && !error) {
    return (
      <div className="space-y-8">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-72 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Overview"
        subtitle="Account summary, open positions, and working orders from your broker."
      />

      {error && !snapshot && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <p>{error}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            Ensure the API is running:{" "}
            <code className="text-[11px]">
              uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
            </code>
          </p>
        </div>
      )}

      {error && snapshot && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {snapshot && (
        <>
          {multiBroker ? (
            brokerSnapshots.map((snap) => (
              <BrokerSection key={snap.broker} snap={snap} />
            ))
          ) : (
            <AccountCards
              account={snapshot.account}
              broker={snapshot.broker}
            />
          )}

          <PnlChart
            portfolioValue={snapshot.account.portfolio_value}
            range={pnlRange}
            onRangeChange={setPnlRange}
          />

          {!multiBroker && (
            <div className="grid gap-6 xl:grid-cols-2">
              <PositionsTable positions={snapshot.positions} />
              <OrdersTable orders={snapshot.open_orders} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
