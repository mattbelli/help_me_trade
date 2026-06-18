"use client";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMoney } from "@/lib/api";

export function PositionsWidget() {
  const { snapshot, loading } = useSnapshot();
  const positions = snapshot?.positions ?? [];

  if (loading && !snapshot) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (!snapshot) {
    return (
      <p className="p-3 text-sm text-muted-foreground">
        Waiting for positions…
      </p>
    );
  }

  if (positions.length === 0) {
    return (
      <p className="p-3 text-sm text-muted-foreground">No open positions</p>
    );
  }

  return (
    <div className="h-full overflow-auto p-1">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="font-mono text-[9px] tracking-wider uppercase">
              Symbol
            </TableHead>
            <TableHead className="text-right font-mono text-[9px] tracking-wider uppercase">
              Qty
            </TableHead>
            <TableHead className="font-mono text-[9px] tracking-wider uppercase">
              Side
            </TableHead>
            <TableHead className="text-right font-mono text-[9px] tracking-wider uppercase">
              Price
            </TableHead>
            <TableHead className="text-right font-mono text-[9px] tracking-wider uppercase">
              Value
            </TableHead>
            <TableHead className="text-right font-mono text-[9px] tracking-wider uppercase">
              Unrealized
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {positions.map((p) => (
            <TableRow key={p.symbol}>
              <TableCell className="font-mono text-xs font-medium">
                {p.symbol}
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {p.qty}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="font-mono text-[10px]">
                  {p.side}
                </Badge>
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {formatMoney(p.current_price)}
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {formatMoney(p.market_value)}
              </TableCell>
              <TableCell
                className={`text-right font-mono text-xs tabular-nums ${p.unrealized_pl >= 0 ? "text-pnl-positive" : "text-pnl-negative"}`}
              >
                {formatMoney(p.unrealized_pl)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
