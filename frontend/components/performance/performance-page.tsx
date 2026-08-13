"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from "recharts";
import { Loader2, RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { RetroReportButton } from "@/components/performance/retro-report-button";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  dailyRealizedAmount,
  fetchPerformanceDashboard,
  syncTrades,
  type PerformanceDashboard,
  type PerformancePeriod,
  type PnlMode,
} from "@/lib/performance-api";
import {
  fetchTradeAccounts,
  formatTradeMoney,
  parseMoney,
  type TradeAccount,
} from "@/lib/trades-api";

type CalendarDay = {
  date: string;
  net_amount: string;
  realized?: string;
  event_count: number;
};

function calendarDayPnl(day: CalendarDay, mode: PnlMode): number {
  if (mode === "realized") {
    return dailyRealizedAmount(day);
  }
  return parseMoney(day.net_amount);
}

function formatCompactMoney(value: string | number): string {
  const n = typeof value === "string" ? parseMoney(value) : value;
  if (n === 0) return "$0";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${sign}$${Math.round(abs / 1000)}k`;
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(1)}k`;
  return `${sign}$${Math.round(abs)}`;
}

const PERIODS: { key: PerformancePeriod; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
  { key: "all", label: "All" },
  { key: "custom", label: "Custom" },
];

const pnlChartConfig = {
  cumulative: { label: "Cumulative P&L", color: "var(--chart-line)" },
} satisfies ChartConfig;

const symbolChartConfig = {
  pnl: { label: "Net P&L", color: "var(--chart-line)" },
} satisfies ChartConfig;

const dowChartConfig = {
  pnl: { label: "Net P&L", color: "var(--chart-line)" },
} satisfies ChartConfig;

function StatCard({
  label,
  value,
  sub,
  positive,
}: {
  label: string;
  value: string;
  sub?: string;
  positive?: boolean | null;
}) {
  return (
    <Card className="border-border/60 bg-card/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p
          className={cn(
            "text-2xl font-medium tabular-nums tracking-tight",
            positive === true && "text-pnl-positive",
            positive === false && "text-pnl-negative",
          )}
        >
          {value}
        </p>
        {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
      </CardContent>
    </Card>
  );
}

function CalendarHeatmap({
  days,
  selectedDate,
  onSelect,
  mode,
}: {
  days: CalendarDay[];
  selectedDate: string | null;
  onSelect: (date: string | null) => void;
  mode: PnlMode;
}) {
  if (days.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {mode === "realized"
          ? "No closed trades in this period."
          : "Import or sync trades to see daily activity."}
      </p>
    );
  }

  const amounts = days.map((d) => calendarDayPnl(d, mode));
  const maxAbs = Math.max(...amounts.map(Math.abs), 1);

  return (
    <div className="flex flex-wrap gap-2">
      {days.map((day) => {
        const amount = calendarDayPnl(day, mode);
        const intensity = Math.min(Math.abs(amount) / maxAbs, 1);
        const positive = amount >= 0;
        const active = selectedDate === day.date;
        const countLabel =
          mode === "realized"
            ? `${day.event_count} event${day.event_count === 1 ? "" : "s"}`
            : `${day.event_count} event${day.event_count === 1 ? "" : "s"}`;
        const displayAmount =
          mode === "realized" && day.realized != null
            ? day.realized
            : day.net_amount;
        return (
          <button
            key={day.date}
            type="button"
            title={`${day.date}: ${formatTradeMoney(displayAmount)}${mode === "realized" && day.realized == null ? " (cash flow)" : ""} (${countLabel})`}
            onClick={() => onSelect(active ? null : day.date)}
            className={cn(
              "flex min-w-[4.75rem] flex-col items-center justify-center rounded-md px-2 py-2 text-center transition-colors",
              "min-h-[3.5rem]",
              active && "ring-2 ring-accent ring-offset-1 ring-offset-background",
              positive
                ? "bg-pnl-positive/20 text-pnl-positive hover:bg-pnl-positive/30"
                : "bg-pnl-negative/20 text-pnl-negative hover:bg-pnl-negative/30",
            )}
            style={{ opacity: 0.4 + intensity * 0.6 }}
          >
            <span className="text-[10px] font-medium leading-none text-muted-foreground/90">
              {day.date.slice(5)}
            </span>
            <span className="mt-1 text-[11px] font-semibold tabular-nums leading-none">
              {formatCompactMoney(amount)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function PerformancePage() {
  const [accounts, setAccounts] = useState<TradeAccount[]>([]);
  const [accountId, setAccountId] = useState<string>("all");
  const [period, setPeriod] = useState<PerformancePeriod>("month");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [dashboard, setDashboard] = useState<PerformanceDashboard | null>(null);
  const [pnlMode, setPnlMode] = useState<PnlMode>("realized");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeAccountId = accountId === "all" ? undefined : accountId;

  const realAccounts = useMemo(
    () => accounts.filter((a) => a.broker !== "alpaca"),
    [accounts],
  );
  const paperAccounts = useMemo(
    () => accounts.filter((a) => a.broker === "alpaca"),
    [accounts],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const accts = await fetchTradeAccounts();
      setAccounts(accts);

      const dash = await fetchPerformanceDashboard({
        account_id: activeAccountId,
        period,
        from_date: period === "custom" ? customFrom || undefined : undefined,
        to_date: period === "custom" ? customTo || undefined : undefined,
      });
      setDashboard(dash);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load performance");
      setDashboard(null);
    } finally {
      setLoading(false);
    }
  }, [activeAccountId, period, customFrom, customTo]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      await syncTrades();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  useEffect(() => {
    setSelectedDate(null);
  }, [pnlMode, period, accountId, customFrom, customTo]);

  const calendarDays = useMemo((): CalendarDay[] => {
    if (!dashboard) return [];
    return pnlMode === "realized"
      ? dashboard.daily_realized
      : dashboard.daily_cash_flow;
  }, [dashboard, pnlMode]);

  const cumulativeSeries = useMemo(() => {
    let running = 0;
    return calendarDays.map((d) => {
      const daily = calendarDayPnl(d, pnlMode);
      running += daily;
      return {
        date: d.date.slice(5),
        fullDate: d.date,
        cumulative: running,
        daily,
      };
    });
  }, [calendarDays, pnlMode]);

  const symbolBars = useMemo(() => {
    if (!dashboard) return [];
    const bySymbol = new Map<string, number>();
    for (const rt of dashboard.round_trips) {
      if (rt.is_option) continue;
      const sym = rt.symbol;
      bySymbol.set(sym, (bySymbol.get(sym) ?? 0) + parseMoney(rt.pnl));
    }
    return [...bySymbol.entries()]
      .map(([symbol, pnl]) => ({ symbol, pnl }))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
      .slice(0, 10);
  }, [dashboard]);

  const assetPie = useMemo(() => {
    if (!dashboard) return [];
    const ac = dashboard.asset_classes;
    return [
      { name: "Stocks", value: ac.stock_trades, fill: "var(--chart-line)" },
      {
        name: "Options",
        value: ac.option_trades,
        fill: "var(--fg-dim)",
      },
    ].filter((x) => x.value > 0);
  }, [dashboard]);

  const dowBars = useMemo(() => {
    if (!dashboard) return [];
    const source =
      pnlMode === "realized"
        ? dashboard.day_of_week_realized
        : dashboard.day_of_week;
    return source.map((d) => ({
      day: d.day,
      pnl: parseMoney(d.net_amount),
      trades: d.trade_count,
    }));
  }, [dashboard, pnlMode]);

  const summary = dashboard?.summary;
  const headlinePnl = dashboard
    ? parseMoney(
        pnlMode === "realized"
          ? dashboard.realized_pnl
          : dashboard.cash_flow_pnl,
      )
    : 0;

  const filteredRoundTrips = useMemo(() => {
    if (!dashboard) return [];
    const trips = dashboard.round_trips;
    if (!selectedDate) return trips;
    if (pnlMode === "realized") {
      return trips.filter((r) => r.close_date === selectedDate);
    }
    return trips.filter(
      (r) => r.open_date === selectedDate || r.close_date === selectedDate,
    );
  }, [dashboard, selectedDate, pnlMode]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <PageHeader
          title="Performance"
          subtitle="Trade ledger analytics — import history + auto-sync from connected brokers."
        />
        <div className="flex flex-wrap items-start gap-2">
          <RetroReportButton period={period} accountId={accountId} />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={syncing}
            onClick={() => void handleSync()}
            className="shrink-0"
          >
            {syncing ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 size-4" />
            )}
            Sync brokers
          </Button>
        </div>
      </div>

      {error && (
        <p className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {!loading && dashboard?.scope_fallback && accountId !== "all" && (
        <p
          role="alert"
          className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
        >
          Couldn&apos;t resolve this account — showing ALL real accounts instead.
        </p>
      )}

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[160px]">
          <Label className="text-xs text-muted-foreground">Account</Label>
          <Select value={accountId} onValueChange={(v) => setAccountId(v ?? "all")}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All accounts (real)</SelectItem>
              {realAccounts.length > 0 ? (
                <SelectGroup>
                  <SelectLabel>Real · Schwab</SelectLabel>
                  {realAccounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ) : null}
              {paperAccounts.length > 0 ? (
                <SelectGroup>
                  <SelectLabel>Paper · Alpaca</SelectLabel>
                  {paperAccounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              ) : null}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-wrap gap-1 rounded-lg border border-border p-1">
          {PERIODS.map(({ key, label }) => (
            <Button
              key={key}
              type="button"
              size="sm"
              variant={period === key ? "default" : "ghost"}
              className={cn(
                "h-7 px-2.5 text-xs",
                period !== key && "text-muted-foreground",
              )}
              onClick={() => setPeriod(key)}
            >
              {label}
            </Button>
          ))}
        </div>

        {period === "custom" && (
          <>
            <div>
              <Label className="text-xs text-muted-foreground">From</Label>
              <Input
                type="date"
                className="mt-1 w-36"
                value={customFrom}
                onChange={(e) => setCustomFrom(e.target.value)}
              />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">To</Label>
              <Input
                type="date"
                className="mt-1 w-36"
                value={customTo}
                onChange={(e) => setCustomTo(e.target.value)}
              />
            </div>
          </>
        )}
      </div>

      {!loading && dashboard && (
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <Label className="text-xs text-muted-foreground">P&amp;L view</Label>
          <div className="flex gap-1 rounded-lg border border-border p-1">
            <Button
              type="button"
              size="sm"
              variant={pnlMode === "realized" ? "default" : "ghost"}
              className={cn(
                "h-7 px-3 text-xs",
                pnlMode !== "realized" && "text-muted-foreground",
              )}
              onClick={() => setPnlMode("realized")}
            >
              Realized
            </Button>
            <Button
              type="button"
              size="sm"
              variant={pnlMode === "cashflow" ? "default" : "ghost"}
              className={cn(
                "h-7 px-3 text-xs",
                pnlMode !== "cashflow" && "text-muted-foreground",
              )}
              onClick={() => setPnlMode("cashflow")}
            >
              Cash flow
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {pnlMode === "realized"
              ? "Closed trades only — profit/loss when positions are sold."
              : "All buys and sells — includes money deployed in open positions."}
          </p>
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-lg" />
            ))}
          </div>
          <Skeleton className="h-64 rounded-lg" />
        </div>
      ) : !dashboard ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No trade data yet. Import a Schwab export in Settings → Import, or
            connect Alpaca and sync.
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label={pnlMode === "realized" ? "Realized P&L" : "Net cash flow"}
              value={formatTradeMoney(headlinePnl)}
              positive={headlinePnl > 0 ? true : headlinePnl < 0 ? false : null}
              sub={
                pnlMode === "realized"
                  ? "FIFO closed trades in period"
                  : "Buys + sells in period"
              }
            />
            <StatCard
              label="Win rate (stocks)"
              value={
                dashboard.stock_win_rate != null
                  ? `${(dashboard.stock_win_rate * 100).toFixed(1)}%`
                  : "—"
              }
              sub={`${summary?.wins ?? 0}W / ${summary?.losses ?? 0}L · ${summary?.round_trips ?? 0} round-trips`}
            />
            <StatCard
              label="Trades"
              value={String(summary?.trade_count ?? 0)}
              sub={`${summary?.event_count ?? 0} total events`}
            />
            <StatCard
              label="Fees"
              value={formatTradeMoney(summary?.total_fees ?? "0")}
              positive={false}
            />
          </div>

          <div className="mb-6 grid gap-4 lg:grid-cols-2">
            <Card className="border-border/80">
              <CardHeader>
                <CardTitle className="text-base font-medium">
                  {pnlMode === "realized"
                    ? "Cumulative realized P&L"
                    : "Cumulative cash flow"}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {cumulativeSeries.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No daily data</p>
                ) : (
                  <ChartContainer config={pnlChartConfig} className="h-[220px] w-full">
                    <LineChart data={cumulativeSeries}>
                      <CartesianGrid vertical={false} strokeDasharray="3 3" />
                      <XAxis dataKey="date" tickLine={false} axisLine={false} />
                      <YAxis
                        tickLine={false}
                        axisLine={false}
                        width={64}
                        tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`}
                      />
                      <ChartTooltip
                        content={
                          <ChartTooltipContent
                            formatter={(value) => formatTradeMoney(Number(value))}
                          />
                        }
                      />
                      <Line
                        type="monotone"
                        dataKey="cumulative"
                        stroke="var(--chart-line)"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ChartContainer>
                )}
              </CardContent>
            </Card>

            <Card className="border-border/80">
              <CardHeader>
                <CardTitle className="text-base font-medium">
                  Calendar
                </CardTitle>
              </CardHeader>
              <CardContent>
                <CalendarHeatmap
                  days={calendarDays}
                  selectedDate={selectedDate}
                  onSelect={setSelectedDate}
                  mode={pnlMode}
                />
                {selectedDate && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {pnlMode === "realized"
                      ? `Showing closed trades on ${selectedDate}.`
                      : `Highlighting ${selectedDate} activity.`}{" "}
                    Click again to clear.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="mb-6 grid gap-4 lg:grid-cols-3">
            <Card className="border-border/80 lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-base font-medium">
                  P&L by symbol (closed stock trades)
                </CardTitle>
              </CardHeader>
              <CardContent>
                {symbolBars.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No closed trades</p>
                ) : (
                  <ChartContainer config={symbolChartConfig} className="h-[220px] w-full">
                    <BarChart data={symbolBars} layout="vertical" margin={{ left: 8 }}>
                      <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                      <XAxis type="number" tickLine={false} axisLine={false} />
                      <YAxis
                        type="category"
                        dataKey="symbol"
                        tickLine={false}
                        axisLine={false}
                        width={48}
                      />
                      <ChartTooltip
                        content={
                          <ChartTooltipContent
                            formatter={(value) => formatTradeMoney(Number(value))}
                          />
                        }
                      />
                      <Bar dataKey="pnl" radius={4}>
                        {symbolBars.map((entry) => (
                          <Cell
                            key={entry.symbol}
                            fill={
                              entry.pnl >= 0
                                ? "var(--pnl-positive)"
                                : "var(--pnl-negative)"
                            }
                          />
                        ))}
                      </Bar>
                    </BarChart>
                  </ChartContainer>
                )}
              </CardContent>
            </Card>

            <Card className="border-border/80">
              <CardHeader>
                <CardTitle className="text-base font-medium">
                  Trade mix
                </CardTitle>
              </CardHeader>
              <CardContent>
                {assetPie.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No trades</p>
                ) : (
                  <ChartContainer
                    config={{
                      stocks: { label: "Stocks", color: "var(--chart-line)" },
                      options: { label: "Options", color: "var(--fg-dim)" },
                    }}
                    className="h-[220px] w-full"
                  >
                    <PieChart>
                      <Pie
                        data={assetPie}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={48}
                        outerRadius={72}
                        paddingAngle={2}
                      />
                      <ChartTooltip content={<ChartTooltipContent />} />
                    </PieChart>
                  </ChartContainer>
                )}
                <p className="mt-2 text-center text-xs text-muted-foreground">
                  {dashboard.asset_classes.stock_trades} stock ·{" "}
                  {dashboard.asset_classes.option_trades} option fills
                </p>
              </CardContent>
            </Card>
          </div>

          <Card className="mb-6 border-border/80">
            <CardHeader>
              <CardTitle className="text-base font-medium">
                Day of week
              </CardTitle>
            </CardHeader>
            <CardContent>
              {dowBars.length === 0 ? (
                <p className="text-sm text-muted-foreground">No data</p>
              ) : (
                <ChartContainer config={dowChartConfig} className="h-[180px] w-full">
                  <BarChart data={dowBars}>
                    <CartesianGrid vertical={false} strokeDasharray="3 3" />
                    <XAxis dataKey="day" tickLine={false} axisLine={false} />
                    <YAxis tickLine={false} axisLine={false} width={56} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar dataKey="pnl" fill="var(--chart-line)" radius={4} />
                  </BarChart>
                </ChartContainer>
              )}
            </CardContent>
          </Card>

          <Card className="border-border/80">
            <CardHeader>
              <CardTitle className="text-base font-medium">
                Closed trades (FIFO P&L)
              </CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {filteredRoundTrips.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No closed round-trips in this period.
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Open</TableHead>
                      <TableHead>Close</TableHead>
                      <TableHead className="text-right">Qty</TableHead>
                      <TableHead className="text-right">P&L</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredRoundTrips.map((rt, i) => {
                      const pnl = parseMoney(rt.pnl);
                      return (
                        <TableRow key={`${rt.symbol}-${rt.close_date}-${i}`}>
                          <TableCell className="font-medium">
                            {rt.symbol}
                            {rt.is_option && (
                              <span className="ml-1 text-[10px] text-muted-foreground">
                                OPT
                              </span>
                            )}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {rt.open_date}
                          </TableCell>
                          <TableCell>{rt.close_date}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rt.quantity}
                          </TableCell>
                          <TableCell
                            className={cn(
                              "text-right tabular-nums font-medium",
                              pnl >= 0
                                ? "text-pnl-positive"
                                : "text-pnl-negative",
                            )}
                          >
                            {formatTradeMoney(rt.pnl)}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
