"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { Sparkline } from "@/components/trading/sparkline";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
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

function WatchlistAddSymbol({
  symbols,
  activeListId,
  onUpdated,
}: {
  symbols: string[];
  activeListId: string;
  onUpdated: (state: WatchlistsState) => void;
}) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) return;

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
  }, [query]);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      const current = symbols.map((s) => s.toUpperCase());
      if (current.includes(symbol)) {
        setQuery("");
        setOpen(false);
        return;
      }

      setSubmitting(true);
      try {
        const next = await updateWatchlist(activeListId, {
          symbols: [...symbols, symbol],
        });
        onUpdated(next);
        setQuery("");
        setSuggestions([]);
        setOpen(false);
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, onUpdated, symbols],
  );

  return (
    <div ref={wrapRef} className="relative px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <span className="shrink-0 font-mono text-[10px] text-[var(--fg-faint)]">
          +
        </span>
        <input
          type="text"
          value={query}
          disabled={submitting}
          placeholder="Add symbol…"
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
            if (e.key === "Escape") setOpen(false);
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
    </div>
  );
}

export function WatchlistWidget({ config, onConfigChange }: WidgetProps) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
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

      <WatchlistAddSymbol
        symbols={symbols}
        activeListId={displayList.id}
        onUpdated={setState}
      />

      {symbols.length === 0 ? (
        <p className="px-3 py-2 font-mono text-[11px] text-[var(--fg-dim)]">
          No symbols yet — add one above
        </p>
      ) : (
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
              {symbols.map((symbol) => {
                const quote = quotes[symbol];
                const changePct = quote?.change_pct ?? 0;
                const positive = changePct >= 0;

                return (
                  <tr
                    key={symbol}
                    onClick={() => publishSymbol(symbol)}
                    className={cn(
                      "group transition-colors",
                      linked
                        ? "cursor-pointer hover:bg-[var(--bg-hover)]"
                        : "hover:bg-[var(--bg-hover)]",
                    )}
                  >
                    <td className="border-b border-[var(--border)] px-2 py-1.5">
                      <span className="inline-flex items-center gap-1">
                        <span className="font-medium text-[var(--c-accent)]">
                          {symbol}
                        </span>
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
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
