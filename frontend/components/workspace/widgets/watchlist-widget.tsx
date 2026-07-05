"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Reorder, useDragControls } from "motion/react";
import { Sparkline } from "@/components/trading/sparkline";
import { Skeleton } from "@/components/ui/skeleton";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  fetchOvernightTradable,
  fetchWatchlists,
  searchSymbols,
  updateWatchlist,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

const ROW_GRID =
  "grid grid-cols-[1.25rem_minmax(0,1fr)_4.5rem_3.25rem_3rem_3.25rem] items-center gap-x-1";

function rvolColor(value: number): string {
  if (value >= 3) return "var(--c-red)";
  if (value >= 2) return "var(--c-amber)";
  if (value >= 1.5) return "var(--c-yellow)";
  return "var(--fg-dim)";
}

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

function daysUntil(dateStr: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!match) return null;

  const [, year, month, day] = match;
  const target = new Date(Number(year), Number(month) - 1, Number(day));
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const diffMs = target.getTime() - today.getTime();
  return Math.round(diffMs / (24 * 60 * 60 * 1000));
}

function earningsBadgeLabel(days: number): string {
  if (days === 0) return "E·today";
  if (days === 1) return "E·1d";
  return `E·${days}d`;
}

function earningsUrgencyColor(days: number): string {
  if (days <= 1) return "var(--c-red)";
  if (days <= 3) return "var(--c-amber, var(--c-yellow))";
  return "var(--fg-dim)";
}

function earningsBadgeStyle(days: number): CSSProperties {
  const color = earningsUrgencyColor(days);
  return {
    color,
    background: `color-mix(in srgb, ${color} 12%, transparent)`,
    boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${color} 35%, var(--border))`,
  };
}

function WatchlistAddRow({
  symbols,
  activeListId,
  onAdded,
  onClose,
}: {
  symbols: string[];
  activeListId: string;
  onAdded: (state: WatchlistsState) => void;
  onClose: () => void;
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

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      const current = symbols.map((s) => s.toUpperCase());
      if (current.includes(symbol)) {
        onClose();
        return;
      }

      setSubmitting(true);
      try {
        // Pin the newly added symbol to the TOP of the user's order.
        const next = await updateWatchlist(activeListId, {
          symbols: [symbol, ...symbols],
        });
        onAdded(next);
        onClose();
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, onAdded, onClose, symbols],
  );

  return (
    <div
      ref={wrapRef}
      className="relative m-1.5 flex items-center gap-x-1 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 transition-colors focus-within:border-[var(--c-accent)]"
    >
      <span className="w-[1.25rem] shrink-0 text-center font-mono text-[var(--c-accent)]">
        +
      </span>
      <input
        ref={inputRef}
        type="text"
        value={query}
        autoFocus
        disabled={submitting}
        placeholder="Add symbol"
        className="min-w-0 flex-1 bg-transparent font-mono text-[length:var(--fs-title)] uppercase text-foreground outline-none placeholder:text-[var(--fg-faint)]"
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
            onClose();
          }
        }}
        onFocus={() => {
          if (suggestions.length > 0) setOpen(true);
        }}
      />
      {loading ? (
        <span className="shrink-0 font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
          …
        </span>
      ) : null}
      <button
        type="button"
        onClick={onClose}
        aria-label="Close add symbol"
        className="shrink-0 rounded px-0.5 font-mono text-[length:var(--fs-title)] leading-none text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
      >
        ×
      </button>
      {open && suggestions.length > 0 ? (
        <ul className="absolute right-0 left-0 top-full z-10 mt-0.5 max-h-32 overflow-auto rounded border border-[var(--border)] bg-[var(--panel)] py-0.5 shadow-[var(--panel-shadow)]">
          {suggestions.map((sym) => (
            <li key={sym}>
              <button
                type="button"
                className="w-full px-2 py-1 text-left font-mono text-[length:var(--fs-body)] text-foreground hover:bg-[var(--bg-hover)]"
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

type Quote = ReturnType<typeof useWatchlistMarket>["quotes"][string];

const EXT_SESSION_BADGE: Record<string, { label: string; title: string }> = {
  pre: { label: "PRE", title: "Pre-market" },
  post: { label: "AFT", title: "After-hours" },
  overnight: { label: "24h", title: "Overnight (24/5)" },
  closed: { label: "EXT", title: "Last extended-hours trade (market closed)" },
};

function WatchlistSymbolRow({
  symbol,
  quote,
  isOvernight,
  linked,
  removing,
  onPublish,
  onRemove,
  onDragStart,
  onDragEnd,
}: {
  symbol: string;
  quote: Quote | undefined;
  isOvernight: boolean;
  linked: boolean;
  removing: boolean;
  onPublish: () => void;
  onRemove: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const controls = useDragControls();

  const session = quote?.market_session;
  const extBadge =
    session && session in EXT_SESSION_BADGE && quote?.ext_price != null
      ? EXT_SESSION_BADGE[session]
      : null;
  const displayPrice = extBadge ? (quote?.ext_price ?? 0) : (quote?.price ?? 0);
  const displayChangePct = extBadge
    ? (quote?.ext_change_pct ?? 0)
    : (quote?.change_pct ?? 0);
  const positive = displayChangePct >= 0;

  const nextEarningsDate = quote?.next_earnings ?? null;
  const earningsDays =
    nextEarningsDate != null ? daysUntil(nextEarningsDate) : null;
  const showEarningsBadge =
    earningsDays != null && earningsDays >= 0 && earningsDays <= 14;

  const priceFlash = usePriceFlash(quote ? displayPrice : null);

  return (
    <Reorder.Item
      value={symbol}
      layout="position"
      dragListener={false}
      dragControls={controls}
      onDragEnd={onDragEnd}
      className="border-b border-[var(--border)]"
      onClick={onPublish}
      style={{ cursor: linked ? "pointer" : "default" }}
    >
      <div
        className={cn(
          ROW_GRID,
          "group/row group px-2 py-1.5 hover:bg-[var(--bg-hover)]",
        )}
      >
        <button
          type="button"
          className="cursor-grab select-none font-mono text-[length:var(--fs-table)] leading-none text-[var(--fg-faint)] opacity-40 transition-opacity group-hover:opacity-100 active:cursor-grabbing"
          aria-label={`Reorder ${symbol}`}
          onPointerDown={(e) => {
            e.stopPropagation();
            onDragStart();
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
          <ClickableSymbol
            symbol={symbol}
            linked={linked}
            onActivate={onPublish}
            className="truncate font-medium"
          />
          {showEarningsBadge && nextEarningsDate ? (
            <span
              className="shrink-0 rounded px-0.5 font-mono text-[length:var(--fs-micro)] leading-none"
              style={earningsBadgeStyle(earningsDays)}
              title={`Earnings ${nextEarningsDate}`}
            >
              {earningsBadgeLabel(earningsDays)}
            </span>
          ) : null}
          <button
            type="button"
            disabled={removing}
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            className="rounded px-0.5 font-mono text-[length:var(--fs-body)] leading-none text-[var(--fg-faint)] opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--bg-inset)] hover:text-[var(--c-red)] disabled:opacity-40"
            aria-label={`Remove ${symbol}`}
          >
            ×
          </button>
        </span>
        <span
          className={cn(
            "inline-flex items-center justify-end gap-0.5 rounded tabular-nums text-foreground",
            priceFlash === "up" && "tick-flash-up",
            priceFlash === "down" && "tick-flash-down",
          )}
          title={extBadge?.title}
        >
          {quote ? formatPrice(displayPrice) : "—"}
          {extBadge ? (
            <span className="rounded px-0.5 font-mono text-[length:var(--fs-micro)] leading-none text-[var(--fg-dim)] ring-1 ring-[var(--border)]">
              {extBadge.label}
            </span>
          ) : null}
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
          {quote ? formatPct(displayChangePct) : "—"}
        </span>
        {quote?.rvol?.value != null ? (
          <span
            className={cn(
              "text-right tabular-nums",
              quote.rvol.state === "overnight" && "opacity-50",
            )}
            style={{ color: rvolColor(quote.rvol.value) }}
            title={
              quote.rvol.state === "overnight"
                ? "Yesterday's volume (pre-market)"
                : `${quote.rvol.value.toFixed(2)}× normal volume for this time of day`
            }
          >
            {quote.rvol.value.toFixed(1)}×
          </span>
        ) : (
          <span className="text-right tabular-nums text-[var(--fg-faint)]">—</span>
        )}
        <div className="flex justify-end">
          <Sparkline
            values={
              (quote?.intraday?.length ? quote.intraday : quote?.sparkline) ?? []
            }
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
      </div>
    </Reorder.Item>
  );
}

export function WatchlistWidget({ config, onConfigChange }: WidgetProps) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragOrder, setDragOrder] = useState<string[] | null>(null);
  const [sortState, setSortState] = useState<{
    key: "symbol" | "price" | "change_pct" | "rvol";
    dir: "asc" | "desc";
  } | null>(null);
  const [overnightSymbols, setOvernightSymbols] = useState<Set<string>>(
    new Set(),
  );
  const dragOrderRef = useRef<string[] | null>(null);
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);

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

  const baseOrder = dragOrder ?? symbols;

  const sortedSymbols = useMemo(() => {
    if (!sortState) return baseOrder;

    const { key, dir } = sortState;
    const mul = dir === "asc" ? 1 : -1;

    return [...baseOrder].sort((a, b) => {
      const getValue = (sym: string): number | string | null => {
        const q = quotes[sym];
        switch (key) {
          case "symbol":
            return sym;
          case "price":
            return q?.price ?? null;
          case "change_pct":
            return q?.change_pct ?? null;
          case "rvol":
            return q?.rvol?.value ?? null;
        }
      };

      const va = getValue(a);
      const vb = getValue(b);
      const aMissing = va == null;
      const bMissing = vb == null;
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;

      if (key === "symbol") {
        return mul * (va as string).localeCompare(vb as string);
      }
      return mul * ((va as number) - (vb as number));
    });
  }, [baseOrder, sortState, quotes]);

  const handleSortHeaderClick = useCallback(
    (key: "symbol" | "price" | "change_pct" | "rvol") => {
      setSortState((prev) => {
        if (prev?.key === key) {
          if (prev.dir === "asc") return { key, dir: "desc" };
          return null;
        }
        return { key, dir: "asc" };
      });
    },
    [],
  );

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
      setSortState(null);
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

  // A freshly added symbol is pinned to the top of the persisted order — clear
  // any active column sort / in-progress drag so it's visible at the top.
  const handleSymbolAdded = useCallback((next: WatchlistsState) => {
    setState(next);
    setSortState(null);
    setDragOrder(null);
  }, []);

  if (loading && !state) {
    return <Skeleton className="m-3 h-full min-h-32 rounded-md" />;
  }

  if (error && !displayList) {
    return (
      <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--c-red)]">{error}</p>
    );
  }

  if (!displayList || !state) {
    return (
      <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">
        No active watchlist
      </p>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {marketLoading ? (
        <div className="border-b border-[var(--border)] px-3 py-0.5">
          <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">loading…</span>
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
          {(
            [
              { label: "Sym", key: "symbol" as const },
              { label: "Last", key: "price" as const },
              { label: "Δ%", key: "change_pct" as const },
              { label: "RVOL", key: "rvol" as const },
              { label: "1d" },
            ] as const
          ).map((h, idx) =>
            "key" in h ? (
              <button
                key={h.label}
                type="button"
                onClick={() => handleSortHeaderClick(h.key)}
                className={cn(
                  "text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase",
                  idx === 0 ? "text-left" : "text-right",
                )}
              >
                {h.label}
                {sortState?.key === h.key ? (
                  <span className="ml-0.5">{sortState.dir === "asc" ? "▲" : "▼"}</span>
                ) : null}
              </button>
            ) : (
              <span
                key={h.label}
                className={cn(
                  "text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-faint)] uppercase",
                  idx === 0 ? "text-left" : "text-right",
                )}
              >
                {h.label}
              </span>
            ),
          )}
        </div>

        {config.adding ? (
          <WatchlistAddRow
            symbols={symbols}
            activeListId={displayList.id}
            onAdded={handleSymbolAdded}
            onClose={() => onConfigChange({ adding: false })}
          />
        ) : null}

        <Reorder.Group
          axis="y"
          values={sortedSymbols}
          onReorder={setDragOrder}
          className="m-0 list-none p-0"
        >
          {sortedSymbols.map((symbol) => (
            <WatchlistSymbolRow
              key={symbol}
              symbol={symbol}
              quote={quotes[symbol]}
              isOvernight={overnightSymbols.has(symbol.toUpperCase())}
              linked={linked}
              removing={removing === symbol}
              onPublish={() => publish(symbol)}
              onRemove={() => void removeSymbol(symbol)}
              onDragStart={() => setSortState(null)}
              onDragEnd={handleReorderEnd}
            />
          ))}
        </Reorder.Group>
      </div>
    </div>
  );
}
