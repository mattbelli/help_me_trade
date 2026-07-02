import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMoney, formatQty, type Position } from "@/lib/api";

type Props = {
  positions: Position[];
  broker?: string;
};

export function PositionsTable({ positions, broker }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Open positions{broker ? ` · ${broker}` : ""}
        </CardTitle>
        <CardDescription>
          Click a symbol to open its chart on the trading page
        </CardDescription>
      </CardHeader>
      <CardContent>
        {positions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open positions</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Price</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead className="text-right">Unrealized P/L</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {positions.map((p) => (
                <TableRow key={p.symbol}>
                  <TableCell className="font-medium">
                    <Link
                      href={`/trading?symbol=${encodeURIComponent(p.symbol)}`}
                      className="text-accent-foreground underline-offset-4 hover:underline"
                    >
                      {p.symbol}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatQty(p.qty)}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{p.side}</Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(p.current_price)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(p.market_value)}
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${p.unrealized_pl >= 0 ? "text-pnl-positive" : "text-pnl-negative"}`}
                  >
                    {formatMoney(p.unrealized_pl)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
