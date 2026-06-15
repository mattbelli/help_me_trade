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
import { type Order } from "@/lib/api";

type Props = {
  orders: Order[];
  broker?: string;
};

export function OrdersTable({ orders, broker }: Props) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Open orders{broker ? ` · ${broker}` : ""}</CardTitle>
        <CardDescription>
          Pending orders — not positions until filled
        </CardDescription>
      </CardHeader>
      <CardContent>
        {orders.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open orders</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Filled</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orders.map((o) => (
                <TableRow key={o.id}>
                  <TableCell className="font-medium">{o.symbol}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {o.qty}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{o.side}</Badge>
                  </TableCell>
                  <TableCell>{o.order_type}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{o.status}</Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {o.filled_qty}
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
