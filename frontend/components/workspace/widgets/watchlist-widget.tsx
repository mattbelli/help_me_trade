"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Moon } from "lucide-react";

import { Sparkline } from "@/components/trading/sparkline";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchOvernightTradable,
  fetchWatchlists,
  searchSymbols,
  updateWatchlist,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelStore } from "@/lib/workspace/use-channel-store";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function WatchlistAddRow({
  symbols,
  activeListId,
  onUpdated,
  colSpan,
}: {
  symbols: string[];
  activeListId: string;
  onUpdated: (state: WatchlistsState) => void;
  colSpan: number;
}) {
  const [editing, setEditing] = useState(false);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const wrapRef = useRef<HTMLTableCellElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
    }
  }, [editing]);

  useEffect(() => {
    const q = query.trim();
    if (!editing || q.length < 1) return;

    const id = setTimeout(() => {
      setLoading(true);
      searchSymbols(q)
        .then((results) => {
          setSuggestions(results);
          setOpen(results.length > 0);
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
        })
        .finally(() => setLoading(false));
    }, 200);

    return () => clearTimeout(id);
  }, [query, editing]);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  const cancel = useCallback(() => {
    setEditing(false);
    setQuery("");
    setSuggestions([]);
    setOpen(false);
  }, []);

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      const current = symbols.map((s) => s.toUpperCase());
      if (current.includes(symbol)) {
        cancel();
        return;
      }

      setSubmitting(true);
      try {
        const next = await updateWatchlist(activeListId, {
          symbols: [...symbols, symbol],
        });
        onUpdated(next);
        cancel();
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, cancel, onUpdated, symbols],
  );

  if (!editing) {
    return (
      <tr>
        <td
          colSpan={colSpan}
          className="border-b border-[var(--border)] px-2 py-1"
        >
          <button
            type="button"
            className="w-full rounded px-1 py-0.5 text-left font-mono text-[11px] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)] hover:text-foreground"
            onClick={() => setEditing(true)}
          >
            + add symbol
          </button>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td
        ref={wrapRef}
        colSpan={colSpan}
        className="relative border-b border-[var(--border)] px-2 py-1"
      >
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 font-mono text-[10px] text-[var(--fg-faint)]">
            +
          </span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            disabled={submitting}
            placeholder="Symbol…"
            className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-0.5 font-mono text-[11px] text-foreground uppercase outline-none placeholder:normal-case placeholder:text-[var(--fg-faint)] focus:border-[var(--border-strong)]"
            onChange={(e) => {
              const next = e.target.value.toUpperCase();
              setQuery(next);
              if (!next.trim()) {
                setSuggestions([]);
                setOpen(false);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void addSymbol(query || suggestions[0] || "");
              }
              if (e.key === "Escape") {
                e.preventDefault();
                cancel();
              }
            }}
            onBlur={() => {
              window.setTimeout(() => {
                if (!wrapRef.current?.contains(document.activeElement)) {
                  cancel();
                }
              }, 120);
            }}
            onFocus={() => {
              if (suggestions.length > 0) setOpen(true);
            }}
          />
          {loading ? (
            <span className="shrink-0 font-mono text-[9px] text-[var(--fg-faint)]">
              …
            </span>
          ) : null}
        </div>
        {open && suggestions.length > 0 ? (
          <ul className="absolute right-2 left-2 z-10 mt-0.5 max-h-32 overflow-auto rounded border border-[var(--border)] bg-[var(--panel)] py-0.5 shadow-[var(--panel-shadow)]">
            {suggestions.map((sym) => (
              <li key={sym}>
                <button
                  type="button"
                  className="w-full px-2 py-1 text-left font-mono text-[11px] text-foreground hover:bg-[var(--bg-hover)]"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    void addSymbol(sym);
                  }}
                >
                  {sym}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </td>
    </tr>
  );
}

export function WatchlistWidget({ config, onConfigChange }: WidgetProps) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overnightSymbols, setOvernightSymbols] = useState<Set<string>>(
    new Set(),
  );
  const setChannelSymbol = useChannelStore((s) => s.setChannelSymbol);
  const channel: ChannelId = config.channel ?? "none";
  const linked = channel !== "none";

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchWatchlists();
        if (cancelled) return;
        setState(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load watchlists",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  const displayList = useMemo(() => {
    if (!state) return null;
    const listId = config.watchlistId ?? state.active_id;
    return state.lists.find((list) => list.id === listId) ?? null;
  }, [config.watchlistId, state]);

  const symbols = useMemo(
    () => displayList?.symbols ?? EMPTY_SYMBOLS,
    [displayList?.symbols],
  );

  const symbolsKey = useMemo(() => symbols.join(","), [symbols]);

  useEffect(() => {
    if (symbols.length === 0) return;

    let cancelled = false;
    fetchOvernightTradable(symbols)
      .then((overnight) => {
        if (!cancelled) {
          setOvernightSymbols(new Set(overnight.map((s) => s.toUpperCase())));
        }
      })
      .catch(() => {
        if (!cancelled) setOvernightSymbols(new Set());
      });

    return () => {
      cancelled = true;
    };
  }, [symbolsKey, symbols]);

  const { quotes, loading: marketLoading } = useWatchlistMarket({
    symbols,
    enabled: symbols.length > 0,
  });

  const removeSymbol = useCallback(
    async (symbol: string) => {
      if (!displayList) return;
      setRemoving(symbol);
      try {
        const next = await updateWatchlist(displayList.id, {
          symbols: displayList.symbols.filter((s) => s !== symbol),
        });
        setState(next);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to remove symbol",
        );
      } finally {
        setRemoving(null);
      }
    },
    [displayList],
  );

  const reorderSymbols = useCallback(
    async (fromIndex: number, toIndex: number) => {
      if (!displayList || fromIndex === toIndex) return;

      const reordered = [...displayList.symbols];
      const [moved] = reordered.splice(fromIndex, 1);
      reordered.splice(toIndex, 0, moved);

      const prevState = state;
      setState((current) => {
        if (!current) return current;
        return {
          ...current,
          lists: current.lists.map((list) =>
            list.id === displayList.id
              ? { ...list, symbols: reordered }
              : list,
          ),
        };
      });

      try {
        const next = await updateWatchlist(displayList.id, {
          symbols: reordered,
        });
        setState(next);
      } catch (err) {
        if (prevState) setState(prevState);
        setError(
          err instanceof Error ? err.message : "Failed to reorder symbols",
        );
      }
    },
    [displayList, state],
  );

  const publishSymbol = useCallback(
    (symbol: string) => {
      if (!linked) return;
      setChannelSymbol(channel, symbol);
    },
    [channel, linked, setChannelSymbol],
  );

  if (loading && !state) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (error && !displayList) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--c-red)]">{error}</p>
    );
  }

  if (!displayList || !state) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No active watchlist
      </p>
    );
  }

  const colSpan = 4;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border)] px-3 py-1.5">
        {state.lists.length > 1 ? (
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex items-center gap-1 font-mono text-[10px] text-[var(--fg-faint)] outline-none hover:text-foreground">
              {displayList.name}
              <ChevronDown className="size-3" />
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              className="min-w-32 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[11px] shadow-[var(--panel-shadow)]"
            >
              {state.lists.map((list) => (
                <DropdownMenuItem
                  key={list.id}
                  onSelect={() => onConfigChange({ watchlistId: list.id })}
                  className="px-2 py-1.5"
                >
                  {list.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <p className="font-mono text-[10px] text-[var(--fg-faint)]">
            {displayList.name}
          </p>
        )}
        {marketLoading ? (
          <span className="font-mono text-[10px] text-[var(--fg-faint)]">
            {" "}
            · loading…
          </span>
        ) : null}
        {linked ? (
          <p className="mt-0.5 font-mono text-[9px] text-[var(--fg-faint)]">
            Click a row to link symbol to channel
          </p>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-1">
        <table className="w-full border-collapse font-mono text-[12px]">
          <thead>
            <tr>
              {(["Sym", "Last", "Δ%", "7d"] as const).map((h, idx) => (
                <th
                  key={h}
                  className={cn(
                    "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                    idx === 0 ? "text-left" : "text-right",
                    h === "7d" && "text-right",
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {symbols.map((symbol, index) => {
              const quote = quotes[symbol];
              const changePct = quote?.change_pct ?? 0;
              const positive = changePct >= 0;
              const isOvernight = overnightSymbols.has(symbol.toUpperCase());

              return (
                <tr
                  key={symbol}
                  onClick={() => publishSymbol(symbol)}
                  onDragOver={(e) => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "move";
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragIndex === null || dragIndex === index) return;
                    void reorderSymbols(dragIndex, index);
                    setDragIndex(null);
                  }}
                  className={cn(
                    "group transition-colors",
                    linked
                      ? "cursor-pointer hover:bg-[var(--bg-hover)]"
                      : "hover:bg-[var(--bg-hover)]",
                    dragIndex === index && "opacity-50",
                  )}
                >
                  <td className="border-b border-[var(--border)] px-2 py-1.5">
                    <span className="inline-flex items-center gap-1">
                      <span
                        draggable
                        onDragStart={(e) => {
                          e.stopPropagation();
                          setDragIndex(index);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                        onDragEnd={() => setDragIndex(null)}
                        onClick={(e) => e.stopPropagation()}
                        className="cursor-grab select-none font-mono text-[10px] leading-none text-[var(--fg-faint)] opacity-0 transition-opacity group-hover:opacity-100 active:cursor-grabbing"
                        aria-label={`Reorder ${symbol}`}
                      >
                        ⠿
                      </span>
                      <span className="font-medium text-[var(--c-accent)]">
                        {symbol}
                      </span>
                      {isOvernight ? (
                        <span
                          title="Tradable 24/5 overnight"
                          aria-label="Tradable 24/5 overnight"
                        >
                          <Moon className="size-3 shrink-0 text-[var(--c-blue)]" />
                        </span>
                      ) : null}
                      <button
                        type="button"
                        disabled={removing === symbol}
                        onClick={(e) => {
                          e.stopPropagation();
                          void removeSymbol(symbol);
                        }}
                        className="rounded px-0.5 font-mono text-[11px] leading-none text-[var(--fg-faint)] opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--bg-inset)] hover:text-[var(--c-red)] disabled:opacity-40"
                        aria-label={`Remove ${symbol}`}
                      >
                        ×
                      </button>
                    </span>
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
                    {quote ? formatPrice(quote.price) : "—"}
                  </td>
                  <td
                    className={cn(
                      "border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums",
                      quote
                        ? positive
                          ? "text-[var(--c-green)]"
                          : "text-[var(--c-red)]"
                        : "text-[var(--fg-dim)]",
                    )}
                  >
                    {quote ? formatPct(changePct) : "—"}
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-right">
                    <div className="flex justify-end">
                      <Sparkline
                        values={quote?.sparkline ?? []}
                        width={48}
                        height={18}
                        className={
                          quote
                            ? positive
                              ? "text-[var(--c-green)]"
                              : "text-[var(--c-red)]"
                            : "text-[var(--fg-faint)]"
                        }
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
            <WatchlistAddRow
              symbols={symbols}
              activeListId={displayList.id}
              onUpdated={setState}
              colSpan={colSpan}
            />
          </tbody>
        </table>
      </div>
    </div>
  );
}
