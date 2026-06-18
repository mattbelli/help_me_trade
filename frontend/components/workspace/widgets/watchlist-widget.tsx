"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Reorder, useDragControls } from "motion/react";
import { Sparkline } from "@/components/trading/sparkline";
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

const ROW_GRID =
  "grid grid-cols-[1.25rem_minmax(0,1fr)_4.5rem_3.25rem_3.25rem] items-center gap-x-1";

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
  const inputRef = useRef<HTMLInputElement>(null);

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

  const reset = useCallback(() => {
    setQuery("");
    setSuggestions([]);
    setOpen(false);
    inputRef.current?.focus();
  }, []);

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      const current = symbols.map((s) => s.toUpperCase());
      if (current.includes(symbol)) {
        reset();
        return;
      }

      setSubmitting(true);
      try {
        const next = await updateWatchlist(activeListId, {
          symbols: [...symbols, symbol],
        });
        onUpdated(next);
        reset();
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, onUpdated, reset, symbols],
  );

  return (
    <div className={cn(ROW_GRID, "border-b border-[var(--border)] px-2 py-1")}>
      <span />
      <div
        ref={wrapRef}
        className="relative flex min-w-0 items-center gap-1.5 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1"
      >
        <span className="shrink-0 font-mono text-[11px] text-[var(--fg-faint)]">
          +
        </span>
        <input
          ref={inputRef}
          type="text"
          value={query}
          disabled={submitting}
          placeholder="Add symbol"
          className="min-w-0 flex-1 bg-transparent py-0.5 font-mono text-[12px] text-foreground uppercase outline-none placeholder:text-[var(--fg-faint)]"
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
              reset();
            }
          }}
          onFocus={() => {
            if (suggestions.length > 0) setOpen(true);
          }}
        />
        {loading ? (
          <span className="absolute top-1/2 right-0 -translate-y-1/2 font-mono text-[9px] text-[var(--fg-faint)]">
            …
          </span>
        ) : null}
        {open && suggestions.length > 0 ? (
          <ul className="absolute right-0 left-0 z-10 mt-0.5 max-h-32 overflow-auto rounded border border-[var(--border)] bg-[var(--panel)] py-0.5 shadow-[var(--panel-shadow)]">
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
      <span />
      <span />
      <span />
    </div>
  );
}

type Quote = ReturnType<typeof useWatchlistMarket>["quotes"][string];

function WatchlistSymbolRow({
  symbol,
  quote,
  isOvernight,
  linked,
  removing,
  onPublish,
  onRemove,
  onDragEnd,
}: {
  symbol: string;
  quote: Quote | undefined;
  isOvernight: boolean;
  linked: boolean;
  removing: boolean;
  onPublish: () => void;
  onRemove: () => void;
  onDragEnd: () => void;
}) {
  const controls = useDragControls();
  const changePct = quote?.change_pct ?? 0;
  const positive = changePct >= 0;

  return (
    <Reorder.Item
      value={symbol}
      dragListener={false}
      dragControls={controls}
      onDragEnd={onDragEnd}
      className={cn(
        ROW_GRID,
        "group border-b border-[var(--border)] px-2 py-1.5",
        linked
          ? "cursor-pointer hover:bg-[var(--bg-hover)]"
          : "hover:bg-[var(--bg-hover)]",
      )}
      onClick={onPublish}
    >
      <button
        type="button"
        className="cursor-grab select-none font-mono text-[10px] leading-none text-[var(--fg-faint)] opacity-40 transition-opacity group-hover:opacity-100 active:cursor-grabbing"
        aria-label={`Reorder ${symbol}`}
        onPointerDown={(e) => {
          e.stopPropagation();
          controls.start(e);
        }}
        onClick={(e) => e.stopPropagation()}
      >
        ⋮⋮
      </button>
      <span className="inline-flex min-w-0 items-center gap-1">
        <span
          className="flex size-1.5 shrink-0 items-center justify-center"
          title={isOvernight ? "Tradable 24/5 overnight" : undefined}
          aria-label={isOvernight ? "Tradable 24/5 overnight" : undefined}
        >
          {isOvernight ? (
            <span className="size-1.5 rounded-full bg-[var(--c-green)]" />
          ) : null}
        </span>
        <span className="truncate font-medium text-[var(--c-accent)]">
          {symbol}
        </span>
        <button
          type="button"
          disabled={removing}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          className="rounded px-0.5 font-mono text-[11px] leading-none text-[var(--fg-faint)] opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--bg-inset)] hover:text-[var(--c-red)] disabled:opacity-40"
          aria-label={`Remove ${symbol}`}
        >
          ×
        </button>
      </span>
      <span className="text-right tabular-nums text-foreground">
        {quote ? formatPrice(quote.price) : "—"}
      </span>
      <span
        className={cn(
          "text-right tabular-nums",
          quote
            ? positive
              ? "text-[var(--c-green)]"
              : "text-[var(--c-red)]"
            : "text-[var(--fg-dim)]",
        )}
      >
        {quote ? formatPct(changePct) : "—"}
      </span>
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
    </Reorder.Item>
  );
}

export function WatchlistWidget({ config }: WidgetProps) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragOrder, setDragOrder] = useState<string[] | null>(null);
  const [overnightSymbols, setOvernightSymbols] = useState<Set<string>>(
    new Set(),
  );
  const dragOrderRef = useRef<string[] | null>(null);
  const setChannelSymbol = useChannelStore((s) => s.setChannelSymbol);
  const channel: ChannelId = config.channel ?? "none";
  const linked = channel !== "none";

  useEffect(() => {
    dragOrderRef.current = dragOrder;
  }, [dragOrder]);

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

  const orderedSymbols = dragOrder ?? symbols;

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

  const persistReorder = useCallback(
    async (reordered: string[]) => {
      if (!displayList) return;
      if (
        reordered.length === displayList.symbols.length &&
        reordered.every((sym, idx) => sym === displayList.symbols[idx])
      ) {
        return;
      }

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
        setDragOrder(null);
      } catch (err) {
        if (prevState) setState(prevState);
        setDragOrder(null);
        setError(
          err instanceof Error ? err.message : "Failed to reorder symbols",
        );
      }
    },
    [displayList, state],
  );

  const handleReorderEnd = useCallback(() => {
    const reordered = dragOrderRef.current;
    setDragOrder(null);
    if (reordered) void persistReorder(reordered);
  }, [persistReorder]);

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
    <div className="flex h-full flex-col overflow-hidden font-mono text-[12px]">
      {marketLoading ? (
        <div className="border-b border-[var(--border)] px-3 py-0.5">
          <span className="text-[10px] text-[var(--fg-faint)]">loading…</span>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-1">
        <div
          className={cn(
            ROW_GRID,
            "gap-x-1 border-b border-[var(--border)] px-2 py-1.5",
          )}
        >
          <span />
          {(["Sym", "Last", "Δ%", "7d"] as const).map((h, idx) => (
            <span
              key={h}
              className={cn(
                "text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                idx === 0 ? "text-left" : "text-right",
              )}
            >
              {h}
            </span>
          ))}
        </div>

        <Reorder.Group
          axis="y"
          values={orderedSymbols}
          onReorder={setDragOrder}
          className="m-0 list-none p-0"
        >
          {orderedSymbols.map((symbol) => (
            <WatchlistSymbolRow
              key={symbol}
              symbol={symbol}
              quote={quotes[symbol]}
              isOvernight={overnightSymbols.has(symbol.toUpperCase())}
              linked={linked}
              removing={removing === symbol}
              onPublish={() => publishSymbol(symbol)}
              onRemove={() => void removeSymbol(symbol)}
              onDragEnd={handleReorderEnd}
            />
          ))}
        </Reorder.Group>

        <WatchlistAddRow
          symbols={symbols}
          activeListId={displayList.id}
          onUpdated={setState}
        />
      </div>
    </div>
  );
}
