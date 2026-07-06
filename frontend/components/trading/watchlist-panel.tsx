"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import { Sparkline } from "@/components/trading/sparkline";
import { TickerSearch } from "@/components/trading/ticker-search";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  addWatchlistSymbol,
  createWatchlist,
  deleteWatchlist,
  fetchWatchlists,
  removeWatchlistSymbol,
  setActiveWatchlist,
  updateWatchlist,
  type Watchlist,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import { cn } from "@/lib/utils";

type Props = {
  activeSymbol?: string;
  onSelect: (symbol: string) => void;
  className?: string;
  variant?: "card" | "rail";
};

function formatPct(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPrice(value: number) {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

const EXT_SESSION_BADGE: Record<string, { label: string; title: string }> = {
  pre: { label: "PRE", title: "Pre-market" },
  post: { label: "AFT", title: "After-hours" },
  overnight: { label: "24h", title: "Overnight (24/5)" },
};

const EMPTY_SYMBOLS: string[] = [];

export function WatchlistPanel({
  activeSymbol,
  onSelect,
  className,
  variant = "card",
}: Props) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [addQuery, setAddQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [draftName, setDraftName] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);

  const activeList: Watchlist | undefined = state?.lists.find(
    (list) => list.id === state.active_id,
  );
  const symbols = useMemo(
    () => activeList?.symbols ?? EMPTY_SYMBOLS,
    [activeList?.symbols],
  );
  const {
    quotes,
    live: marketLive,
    loading: marketLoading,
  } = useWatchlistMarket({ symbols, enabled: symbols.length > 0 });

  const load = useCallback(async () => {
    try {
      const data = await fetchWatchlists();
      setState(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load watchlists");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  async function handleSwitch(id: string) {
    try {
      const next = await setActiveWatchlist(id);
      setState(next);
      setMenuOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to switch watchlist");
    }
  }

  async function handleAdd(symbol: string) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    const alreadyOnList = symbols.includes(sym);
    try {
      setError(null);
      if (!alreadyOnList) await addWatchlistSymbol(sym);
      await load();
      setAddQuery("");
      setNotice(
        alreadyOnList
          ? `${sym} is already in ${activeList?.name ?? "this list"}`
          : `Added ${sym}`,
      );
      onSelect(sym);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add symbol");
    }
  }

  async function handleRemove(symbol: string) {
    try {
      await removeWatchlistSymbol(symbol);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove symbol");
    }
  }

  async function handleCreate() {
    const name = draftName.trim();
    if (!name) return;
    try {
      const next = await createWatchlist(name);
      setState(next);
      setDraftName("");
      setCreateOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create watchlist");
    }
  }

  async function handleRename() {
    if (!activeList) return;
    const name = draftName.trim();
    if (!name) return;
    try {
      const next = await updateWatchlist(activeList.id, { name });
      setState(next);
      setEditOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rename watchlist");
    }
  }

  async function handleDelete() {
    if (!activeList || !state) return;
    if (state.lists.length <= 1) return;
    try {
      const next = await deleteWatchlist(activeList.id);
      setState(next);
      setEditOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete watchlist");
    }
  }

  function openEdit() {
    setDraftName(activeList?.name ?? "");
    setEditOpen(true);
  }

  function openCreate() {
    setDraftName("");
    setCreateOpen(true);
    setMenuOpen(false);
  }

  const headerControls = (
    <div className="flex items-center gap-1">
      <div ref={menuRef} className="relative min-w-0 flex-1">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 rounded-md border border-border/80 bg-muted/20 px-2.5 py-1.5 text-left text-sm font-medium hover:bg-muted/40"
        >
          <span className="truncate">{activeList?.name ?? "Watchlist"}</span>
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
        </button>
        {menuOpen && state && (
          <div className="absolute top-full right-0 left-0 z-50 mt-1 overflow-hidden rounded-md border border-border bg-popover shadow-md">
            <ul className="max-h-48 overflow-auto py-1">
              {state.lists.map((list) => (
                <li key={list.id}>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-muted",
                      list.id === state.active_id && "bg-muted/60 font-medium",
                    )}
                    onClick={() => handleSwitch(list.id)}
                  >
                    <span className="truncate">{list.name}</span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {list.symbols.length}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="border-t border-border p-1">
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
                onClick={openCreate}
              >
                <Plus className="size-3.5" />
                New watchlist
              </button>
            </div>
          </div>
        )}
      </div>
      <Button
        type="button"
        size="icon-sm"
        variant="ghost"
        aria-label="Edit watchlist"
        onClick={openEdit}
      >
        <Pencil className="size-3.5" />
      </Button>
    </div>
  );

  const listBody = (
    <>
      <div className="space-y-2 px-3 pt-3">
        {headerControls}
        <div className="flex gap-2">
          <TickerSearch
            value={addQuery}
            onChange={(v) => {
              setAddQuery(v);
              if (notice) setNotice(null);
            }}
            onSelect={handleAdd}
            existing={symbols}
            className="flex-1"
          />
          <Button
            type="button"
            size="icon-sm"
            variant="secondary"
            aria-label="Add to watchlist"
            onClick={() => handleAdd(addQuery)}
          >
            <Plus className="size-4" />
          </Button>
        </div>
      </div>

      {error ? (
        <p className="px-3 pt-2 text-xs text-destructive">{error}</p>
      ) : notice ? (
        <p className="px-3 pt-2 text-xs text-muted-foreground">{notice}</p>
      ) : null}

      <ScrollArea className="min-h-0 flex-1 px-2 py-2">
        {loading ? (
          <p className="px-1 text-sm text-muted-foreground">Loading…</p>
        ) : symbols.length === 0 ? (
          <p className="px-1 text-sm text-muted-foreground">
            Add tickers above. Click a row to open its chart.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {symbols.map((sym) => {
              const quote = quotes[sym];
              const session = quote?.market_session;
              const extBadge =
                session && session in EXT_SESSION_BADGE && quote?.ext_price != null
                  ? EXT_SESSION_BADGE[session]
                  : null;
              const displayPrice = extBadge
                ? (quote?.ext_price ?? 0)
                : (quote?.price ?? 0);
              const change = extBadge
                ? (quote?.ext_change_pct ?? 0)
                : (quote?.change_pct ?? 0);
              const positive = change >= 0;

              return (
                <li key={sym}>
                  <div
                    className={cn(
                      "group grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md border border-transparent px-2 py-2",
                      activeSymbol === sym
                        ? "border-border bg-muted/50"
                        : "hover:bg-muted/30",
                    )}
                  >
                    <button
                      type="button"
                      className="min-w-0 text-left"
                      onClick={() => onSelect(sym)}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold tracking-tight">
                          {sym}
                        </span>
                        {quote && (
                          <span
                            className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground tabular-nums"
                            title={extBadge?.title}
                          >
                            {formatPrice(displayPrice)}
                            {extBadge ? (
                              <span className="rounded px-0.5 font-mono text-[8px] leading-none text-muted-foreground ring-1 ring-border">
                                {extBadge.label}
                              </span>
                            ) : null}
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <Sparkline values={quote?.sparkline ?? []} />
                        {marketLoading && !quote && (
                          <span className="text-[10px] text-muted-foreground">
                            …
                          </span>
                        )}
                      </div>
                    </button>

                    <span
                      className={cn(
                        "text-xs font-medium tabular-nums",
                        quote
                          ? positive
                            ? "text-pnl-positive"
                            : "text-pnl-negative"
                          : "text-muted-foreground",
                      )}
                    >
                      {quote ? formatPct(change) : "—"}
                    </span>

                    <button
                      type="button"
                      className="rounded p-1 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted hover:text-foreground"
                      aria-label={`Remove ${sym}`}
                      onClick={() => handleRemove(sym)}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </ScrollArea>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New watchlist</DialogTitle>
            <DialogDescription>
              Create a named list you can switch between from the dropdown.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder="e.g. Tech, Dividends"
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={handleCreate}>
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit watchlist</DialogTitle>
            <DialogDescription>
              Rename this list or delete it if you have more than one.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            placeholder="Watchlist name"
            onKeyDown={(e) => e.key === "Enter" && handleRename()}
          />
          <DialogFooter className="sm:justify-between">
            {state && state.lists.length > 1 ? (
              <Button
                type="button"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                onClick={handleDelete}
              >
                <Trash2 className="size-3.5" />
                Delete
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => setEditOpen(false)}>
                Cancel
              </Button>
              <Button type="button" onClick={handleRename}>
                Save
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );

  if (variant === "rail") {
    return (
      <div className={cn("flex h-full min-h-0 flex-col", className)}>
        <div className="border-b border-border px-3 py-2">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">Watchlist</p>
            {marketLive ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-pnl-positive/10 px-1.5 py-0.5 text-[10px] font-medium text-pnl-positive">
                <span className="size-1.5 rounded-full bg-pnl-positive animate-pulse" />
                Live
              </span>
            ) : (
              <span className="text-[10px] text-muted-foreground">REST</span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">
            7-day trend · daily change
          </p>
        </div>
        {listBody}
      </div>
    );
  }

  return (
    <Card className={cn("border-border/80", className)}>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Watchlist</CardTitle>
        <CardDescription>Click a symbol to load its chart</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 p-0">{listBody}</CardContent>
    </Card>
  );
}
