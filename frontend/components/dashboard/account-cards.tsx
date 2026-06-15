"use client";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatMoney, type Account } from "@/lib/api";

type Props = {
  account: Account;
  broker?: string;
};

export function AccountCards({ account, broker }: Props) {
  const items = [
    { label: "Cash", value: formatMoney(account.cash) },
    { label: "Buying power", value: formatMoney(account.buying_power) },
    { label: "Portfolio", value: formatMoney(account.portfolio_value) },
  ];

  return (
    <div className="space-y-2">
      {broker && (
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="uppercase">
            {broker}
          </Badge>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => (
          <Card key={item.label} size="sm">
            <CardHeader>
              <CardDescription>{item.label}</CardDescription>
              <CardTitle className="text-2xl font-semibold tabular-nums">
                {item.value}
              </CardTitle>
            </CardHeader>
          </Card>
        ))}
        <Card size="sm">
          <CardHeader>
            <CardDescription>Status</CardDescription>
            <CardTitle className="flex items-center gap-2 text-2xl font-semibold">
              <Badge variant="secondary">{account.status}</Badge>
            </CardTitle>
          </CardHeader>
        </Card>
      </div>
    </div>
  );
}
