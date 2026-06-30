"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileUp, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  TerminalButton,
  TerminalField,
  TerminalInput,
} from "@/components/ui/terminal";
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
import { syncTrades } from "@/lib/performance-api";

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
    <Card className="border-border/60 bg-card/40">
      <CardHeader className="pb-1 pt-3">
        <CardTitle className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="pb-3">
        <p
          className={cn(
            "text-lg font-medium tabular-nums",
            positive === true && "text-pnl-positive",
            positive === false && "text-pnl-negative",
          )}
        >
          {value}
        </p>
        {sub && <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p>}
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
  if (days.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Import a Schwab JSON export to see daily P&amp;L.
      </p>
    );
  }

  const amounts = days.map((d) => parseMoney(d.net_amount));
  const maxAbs = Math.max(...amounts.map(Math.abs), 1);

  return (
    <div className="flex flex-wrap gap-1">
      {days.map((day) => {
        const amount = parseMoney(day.net_amount);
        const intensity = Math.min(Math.abs(amount) / maxAbs, 1);
        const positive = amount >= 0;
        const active = selectedDate === day.date;
        return (
          <button
            key={day.date}
            type="button"
            title={`${day.date}: ${formatTradeMoney(day.net_amount)}`}
            onClick={() => onSelect(active ? null : day.date)}
            className={cn(
              "h-7 min-w-7 rounded px-1 text-[9px] font-medium tabular-nums",
              active && "ring-2 ring-accent",
              positive
                ? "bg-pnl-positive/20 text-pnl-positive"
                : "bg-pnl-negative/20 text-pnl-negative",
            )}
            style={{ opacity: 0.35 + intensity * 0.65 }}
          >
            {day.date.slice(5)}
          </button>
        );
      })}
    </div>
  );
}

export function SettingsTradesPanel() {
  const [accounts, setAccounts] = useState<TradeAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [events, setEvents] = useState<TradeEvent[]>([]);
  const [summary, setSummary] = useState<TradeSummary | null>(null);
  const [days, setDays] = useState<DailyTradeStat[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [importing, setImporting] = useState(false);
  const [newAccountLabel, setNewAccountLabel] = useState("Schwab Individual");
  const [review, setReview] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [syncing, setSyncing] = useState(false);
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
          limit: 200,
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
      await importTrades(importFile, activeId);
      setImportFile(null);
      setPreview(null);
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

  const net = summary ? parseMoney(summary.net_amount) : 0;
  const activeAccount = accounts.find((a) => a.id === accountId);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">Import trade history</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Import Schwab JSON exports or sync from connected brokers (auto on API
            start). Data stays local in SQLite.
          </p>
          {activeAccount?.last_synced_at && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Last synced:{" "}
              {new Date(activeAccount.last_synced_at).toLocaleString()}
            </p>
          )}
        </div>
        <TerminalButton
          type="button"
          disabled={syncing}
          onClick={() => void handleSync()}
        >
          {syncing ? (
            <Loader2 className="mr-2 size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 size-3.5" />
          )}
          Sync now
        </TerminalButton>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        {accounts.length > 0 && (
          <TerminalField label="Account">
            <Select value={accountId} onValueChange={(v) => setAccountId(v ?? "")}>
              <SelectTrigger className="h-8 w-[200px] bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]">
                <SelectValue placeholder="Account" />
              </SelectTrigger>
              <SelectContent>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </TerminalField>
        )}
        {!accountId && (
          <TerminalField label="New account label">
            <TerminalInput
              className="w-[200px]"
              value={newAccountLabel}
              onChange={(e) => setNewAccountLabel(e.target.value)}
            />
          </TerminalField>
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
        <TerminalButton type="button" onClick={() => fileRef.current?.click()}>
          <FileUp className="mr-2 size-3.5" />
          {importFile ? importFile.name : "Choose JSON"}
        </TerminalButton>
        <TerminalButton
          type="button"
          variant="primary"
          onClick={() => void handleImport()}
          disabled={!importFile || importing}
        >
          {importing && <Loader2 className="mr-2 size-3.5 animate-spin" />}
          Import
        </TerminalButton>
        {accountId && summary && summary.trade_count > 0 && (
          <TerminalButton
            type="button"
            onClick={() => void handleReview()}
            disabled={reviewing}
          >
            {reviewing ? (
              <Loader2 className="mr-2 size-3.5 animate-spin" />
            ) : (
              <Sparkles className="mr-2 size-3.5" />
            )}
            AI review
          </TerminalButton>
        )}
      </div>

      {preview && (
        <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
          Preview: {preview.parsed} rows · {preview.would_insert} new ·{" "}
          {preview.duplicates} duplicates
        </p>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      {summary && summary.event_count > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Net P&L"
            value={formatTradeMoney(summary.net_amount)}
            positive={net > 0 ? true : net < 0 ? false : null}
          />
          <StatCard
            label="Trades"
            value={String(summary.trade_count)}
            sub={`${summary.event_count} events`}
          />
          <StatCard
            label="Win rate"
            value={
              summary.win_rate != null
                ? `${(summary.win_rate * 100).toFixed(1)}%`
                : "—"
            }
            sub={`${summary.round_trips} stock round-trips`}
          />
          <StatCard label="Fees" value={formatTradeMoney(summary.total_fees)} />
        </div>
      )}

      {review && (
        <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground whitespace-pre-wrap">
          {review}
        </div>
      )}

      {days.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-muted-foreground">Daily P&L</p>
            {selectedDate && (
              <TerminalButton
                type="button"
                className="h-7 text-xs"
                onClick={() => setSelectedDate(null)}
              >
                Clear filter
              </TerminalButton>
            )}
          </div>
          <TradeCalendar
            days={days}
            selectedDate={selectedDate}
            onSelect={setSelectedDate}
          />
        </div>
      )}

      <div className="max-h-[280px] overflow-auto rounded-lg border border-border">
        {loading ? (
          <p className="p-4 text-sm text-muted-foreground">Loading…</p>
        ) : events.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No trades imported yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e) => {
                const amt = parseMoney(e.amount);
                return (
                  <TableRow key={e.id}>
                    <TableCell className="text-xs tabular-nums">{e.trade_date}</TableCell>
                    <TableCell className="text-xs">
                      {e.symbol ?? "—"}
                      {e.event_type !== "trade" && (
                        <Badge variant="outline" className="ml-1 text-[9px]">
                          {e.event_type}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-xs capitalize">{e.side ?? "—"}</TableCell>
                    <TableCell
                      className={cn(
                        "text-right text-xs tabular-nums",
                        amt >= 0 ? "text-pnl-positive" : "text-pnl-negative",
                      )}
                    >
                      {formatTradeMoney(e.amount)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
