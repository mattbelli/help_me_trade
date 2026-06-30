"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FileUp,
  Loader2,
  Sparkles,
  TrendingDown,
  TrendingUp,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
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
import { cn } from "@/lib/utils";
import {
  createTradeAccount,
  fetchDailyTradeStats,
  fetchTradeAccounts,
  fetchTradeEvents,
  fetchTradeSummary,
  formatTradeMoney,
  importTrades,
  parseMoney,
  previewTradeImport,
  requestTradeReview,
  type DailyTradeStat,
  type ImportPreview,
  type TradeAccount,
  type TradeEvent,
  type TradeSummary,
} from "@/lib/trades-api";

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
        {sub && (
          <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
        )}
      </CardContent>
    </Card>
  );
}

function TradeCalendar({
  days,
  selectedDate,
  onSelect,
}: {
  days: DailyTradeStat[];
  selectedDate: string | null;
  onSelect: (date: string | null) => void;
}) {
  const byDate = useMemo(
    () => new Map(days.map((d) => [d.date, d])),
    [days],
  );

  if (days.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Import trades to see daily P&amp;L.
      </p>
    );
  }

  const amounts = days.map((d) => parseMoney(d.net_amount));
  const maxAbs = Math.max(...amounts.map(Math.abs), 1);

  return (
    <div className="flex flex-wrap gap-1.5">
      {days.map((day) => {
        const amount = parseMoney(day.net_amount);
        const intensity = Math.min(Math.abs(amount) / maxAbs, 1);
        const positive = amount >= 0;
        const active = selectedDate === day.date;
        return (
          <button
            key={day.date}
            type="button"
            title={`${day.date}: ${formatTradeMoney(day.net_amount)} (${day.event_count} events)`}
            onClick={() => onSelect(active ? null : day.date)}
            className={cn(
              "h-8 min-w-8 rounded px-1.5 text-[10px] font-medium tabular-nums transition-colors",
              active && "ring-2 ring-accent ring-offset-1 ring-offset-background",
              positive
                ? "bg-pnl-positive/20 text-pnl-positive hover:bg-pnl-positive/30"
                : "bg-pnl-negative/20 text-pnl-negative hover:bg-pnl-negative/30",
            )}
            style={{
              opacity: 0.35 + intensity * 0.65,
            }}
          >
            {day.date.slice(5)}
          </button>
        );
      })}
    </div>
  );
}

export function TradesPage() {
  const [accounts, setAccounts] = useState<TradeAccount[]>([]);
  const [accountId, setAccountId] = useState<string>("");
  const [events, setEvents] = useState<TradeEvent[]>([]);
  const [summary, setSummary] = useState<TradeSummary | null>(null);
  const [days, setDays] = useState<DailyTradeStat[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [importing, setImporting] = useState(false);
  const [newAccountLabel, setNewAccountLabel] = useState("Schwab Individual");
  const [review, setReview] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const accts = await fetchTradeAccounts();
      setAccounts(accts);
      const activeId = accountId || accts[0]?.id || "";
      if (!accountId && accts[0]?.id) setAccountId(accts[0].id);

      if (!activeId) {
        setEvents([]);
        setSummary(null);
        setDays([]);
        return;
      }

      const [ev, sum, daily] = await Promise.all([
        fetchTradeEvents({
          account_id: activeId,
          from_date: selectedDate ?? undefined,
          to_date: selectedDate ?? undefined,
          limit: 500,
        }),
        fetchTradeSummary({ account_id: activeId }),
        fetchDailyTradeStats({ account_id: activeId }),
      ]);
      setEvents(ev);
      setSummary(sum);
      setDays(daily);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load trades");
    } finally {
      setLoading(false);
    }
  }, [accountId, selectedDate]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handlePreview(file: File) {
    setImportFile(file);
    setPreview(null);
    try {
      const result = await previewTradeImport(file, accountId || undefined);
      setPreview(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Preview failed");
    }
  }

  async function handleImport() {
    if (!importFile) return;
    setImporting(true);
    setError(null);
    try {
      let activeId = accountId;
      if (!activeId) {
        const acct = await createTradeAccount(newAccountLabel, "schwab");
        activeId = acct.id;
        setAccountId(acct.id);
      }
      const result = await importTrades(importFile, activeId);
      setImportOpen(false);
      setImportFile(null);
      setPreview(null);
      setError(
        result.inserted > 0
          ? null
          : `No new rows inserted (${result.duplicates} duplicates)`,
      );
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setImporting(false);
    }
  }

  async function handleReview() {
    if (!accountId) return;
    setReviewing(true);
    setReview(null);
    try {
      const result = await requestTradeReview({ account_id: accountId });
      setReview(result.review);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Review failed");
    } finally {
      setReviewing(false);
    }
  }

  const net = summary ? parseMoney(summary.net_amount) : 0;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Trades"
        subtitle="Import brokerage history, track daily P&L, and review performance with the copilot."
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setImportOpen(true)}>
          <FileUp className="mr-2 size-4" />
          Import Schwab JSON
        </Button>
        {accountId && (
          <Button
            variant="outline"
            onClick={() => void handleReview()}
            disabled={reviewing || !summary?.trade_count}
          >
            {reviewing ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Sparkles className="mr-2 size-4" />
            )}
            AI trade review
          </Button>
        )}
        {accounts.length > 0 && (
          <Select value={accountId} onValueChange={(v) => setAccountId(v ?? "")}>
            <SelectTrigger className="w-[220px]">
              <SelectValue placeholder="Account" />
            </SelectTrigger>
            <SelectContent>
              {accounts.map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.label} ({a.broker})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {summary && summary.event_count > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Net P&L"
            value={formatTradeMoney(summary.net_amount)}
            positive={net > 0 ? true : net < 0 ? false : null}
          />
          <StatCard
            label="Trades"
            value={String(summary.trade_count)}
            sub={`${summary.event_count} total events`}
          />
          <StatCard
            label="Win rate"
            value={
              summary.win_rate != null
                ? `${(summary.win_rate * 100).toFixed(1)}%`
                : "—"
            }
            sub={`${summary.wins}W / ${summary.losses}L · ${summary.round_trips} round-trips (stocks)`}
          />
          <StatCard
            label="Fees"
            value={formatTradeMoney(summary.total_fees)}
          />
        </div>
      )}

      {review && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">AI review</CardTitle>
          </CardHeader>
          <CardContent className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-sm text-muted-foreground">
            {review}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Daily P&L</CardTitle>
          {selectedDate && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelectedDate(null)}
            >
              Clear day filter
            </Button>
          )}
        </CardHeader>
        <CardContent>
          <TradeCalendar
            days={days}
            selectedDate={selectedDate}
            onSelect={setSelectedDate}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {selectedDate ? `Trades on ${selectedDate}` : "All trades"}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading…
            </div>
          ) : events.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No trades yet. Import a Schwab transaction JSON export to get started.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className="text-right">Qty</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {events.map((e) => {
                    const amt = parseMoney(e.amount);
                    return (
                      <TableRow key={e.id}>
                        <TableCell className="tabular-nums">{e.trade_date}</TableCell>
                        <TableCell>
                          <span className="font-medium">{e.symbol ?? "—"}</span>
                          {e.event_type !== "trade" && (
                            <Badge variant="outline" className="ml-2 text-[10px]">
                              {e.event_type}
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="capitalize">{e.side ?? "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {e.quantity ?? "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {e.price ? formatTradeMoney(e.price) : "—"}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right tabular-nums font-medium",
                            amt >= 0 ? "text-pnl-positive" : "text-pnl-negative",
                          )}
                        >
                          <span className="inline-flex items-center justify-end gap-1">
                            {amt >= 0 ? (
                              <TrendingUp className="size-3.5 opacity-60" />
                            ) : (
                              <TrendingDown className="size-3.5 opacity-60" />
                            )}
                            {formatTradeMoney(e.amount)}
                          </span>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Import Schwab transactions</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {!accountId && (
              <div className="space-y-2">
                <Label htmlFor="account-label">New account label</Label>
                <Input
                  id="account-label"
                  value={newAccountLabel}
                  onChange={(e) => setNewAccountLabel(e.target.value)}
                />
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handlePreview(file);
              }}
            />
            <Button
              variant="outline"
              className="w-full"
              onClick={() => fileRef.current?.click()}
            >
              {importFile ? importFile.name : "Choose JSON file"}
            </Button>
            {preview && (
              <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
                <p>
                  <strong>{preview.parsed}</strong> rows parsed ·{" "}
                  <strong>{preview.would_insert}</strong> new ·{" "}
                  {preview.duplicates} duplicates
                </p>
                {preview.from_date && preview.to_date && (
                  <p className="mt-1 text-muted-foreground">
                    {preview.from_date} → {preview.to_date}
                  </p>
                )}
                {preview.warnings.length > 0 && (
                  <p className="mt-2 text-xs text-[var(--c-amber)]">
                    {preview.warnings[0]}
                  </p>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setImportOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleImport()}
              disabled={!importFile || importing}
            >
              {importing && <Loader2 className="mr-2 size-4 animate-spin" />}
              Import
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
