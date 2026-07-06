"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Reorder, useDragControls } from "motion/react";
import { Sparkline } from "@/components/trading/sparkline";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchOvernightTradable,
  fetchWatchlists,
  searchSymbols,
  updateWatchlist,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistDragStore } from "@/lib/use-watchlist-drag-store";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePageActive } from "@/lib/workspace/page-active-context";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

function rowGrid(): string {
  return "grid grid-cols-[minmax(0,1fr)_4.5rem_3.25rem_3rem_3.25rem] items-center gap-x-1";
}

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
  onPublish,
}: {
  symbols: string[];
  activeListId: string;
  onAdded: (state: WatchlistsState) => void;
  onClose: () => void;
  onPublish: (symbol: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const onList = useMemo(
    () => new Set(symbols.map((s) => s.toUpperCase())),
    [symbols],
  );

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) return;

    const id = setTimeout(() => {
      setLoading(true);
      searchSymbols(q)
        .then((results) => {
          setSuggestions(results);
          setOpen(results.length > 0);
          setActiveIndex(-1);
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
          setActiveIndex(-1);
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

  // Clear the input and keep the row open + focused so the user can add
  // another ticker without reopening. (Escape / × closes the row.)
  const resetForNext = useCallback(() => {
    setQuery("");
    setSuggestions([]);
    setOpen(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }, []);

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      if (onList.has(symbol)) {
        // Already on the list — focus its chart so the tap isn't a silent no-op.
        onPublish(symbol);
        resetForNext();
        return;
      }

      setSubmitting(true);
      try {
        // Pin the newly added symbol to the TOP of the user's order.
        const next = await updateWatchlist(activeListId, {
          symbols: [symbol, ...symbols],
        });
        onAdded(next);
        resetForNext();
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, onAdded, onList, onPublish, resetForNext, symbols],
  );

  return (
    <div
      ref={wrapRef}
      className="relative z-30 m-1.5 flex items-center gap-x-1 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5 transition-colors focus-within:border-[var(--c-accent)]"
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
          setActiveIndex(-1);
          if (!next.trim()) {
            setSuggestions([]);
            setOpen(false);
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            if (suggestions.length === 0) return;
            setOpen(true);
            setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, -1));
            return;
          }
          if (e.key === "Enter") {
            e.preventDefault();
            const picked =
              activeIndex >= 0 ? suggestions[activeIndex] : query || suggestions[0] || "";
            void addSymbol(picked);
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
        <ul
          className="absolute right-0 left-0 top-full z-50 mt-0.5 max-h-32 overflow-auto rounded border border-[var(--border-strong)] py-0.5 shadow-2xl ring-1 ring-black/50 scroll-shadow-y"
          style={{
            backgroundColor: "color-mix(in srgb, var(--panel) 86%, #ffffff)",
          }}
        >
          {suggestions.map((sym, i) => {
            const already = onList.has(sym.toUpperCase());
            return (
              <li key={sym}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-center justify-between gap-2 px-2 py-1 text-left font-mono text-[length:var(--fs-body)] text-foreground hover:bg-[var(--c-accent-soft)]",
                    i === activeIndex && "bg-[var(--c-accent-soft)]",
                  )}
                  onMouseEnter={() => setActiveIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    void addSymbol(sym);
                  }}
                >
                  <span>{sym}</span>
                  {already ? (
                    <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      on list
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
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

const HOLD_MS = 300;
const MOVE_CANCEL_PX = 6;

function pointInRect(x: number, y: number, rect: DOMRect | null): boolean {
  return (
    rect != null &&
    x >= rect.left &&
    x <= rect.right &&
    y >= rect.top &&
    y <= rect.bottom
  );
}

function WatchlistSymbolRow({
  symbol,
  quote,
  isOvernight,
  linked,
  instanceId,
  onPublish,
  onDragStart,
  onReorderEnd,
  onDelete,
}: {
  symbol: string;
  quote: Quote | undefined;
  isOvernight: boolean;
  linked: boolean;
  instanceId: string;
  onPublish: () => void;
  onDragStart: () => void;
  onReorderEnd: () => void;
  onDelete: () => void;
}) {
  const controls = useDragControls();
  // Hold-anywhere-to-drag: a ~300ms hold on a non-interactive part of the row
  // lifts it into a drag; a quick tap (timer never fires) publishes as usual.
  const holdTimer = useRef<number | null>(null);
  const savedEvent = useRef<ReactPointerEvent | null>(null);
  const startPos = useRef<{ x: number; y: number } | null>(null);
  const didDrag = useRef(false);

  const clearHold = useCallback(() => {
    if (holdTimer.current != null) {
      window.clearTimeout(holdTimer.current);
      holdTimer.current = null;
    }
    savedEvent.current = null;
    startPos.current = null;
  }, []);

  const handlePointerDown = useCallback(
    (e: ReactPointerEvent) => {
      // Never start a drag from a ticker button, ⓘ button, or any link.
      if ((e.target as HTMLElement).closest("button, a")) return;
      didDrag.current = false;
      savedEvent.current = e;
      startPos.current = { x: e.clientX, y: e.clientY };
      holdTimer.current = window.setTimeout(() => {
        holdTimer.current = null;
        const saved = savedEvent.current;
        if (!saved) return;
        didDrag.current = true;
        onDragStart();
        useWatchlistDragStore.getState().setDragging(instanceId);
        controls.start(saved);
      }, HOLD_MS);
    },
    [controls, instanceId, onDragStart],
  );

  const handlePointerMove = useCallback((e: ReactPointerEvent) => {
    if (holdTimer.current == null || !startPos.current) return;
    const dx = e.clientX - startPos.current.x;
    const dy = e.clientY - startPos.current.y;
    if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) {
      window.clearTimeout(holdTimer.current);
      holdTimer.current = null;
      savedEvent.current = null;
      startPos.current = null;
    }
  }, []);

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
      whileDrag={{
        scale: 1.02,
        zIndex: 20,
        boxShadow: "0 6px 20px rgba(0, 0, 0, 0.45)",
      }}
      onDrag={(_, info) => {
        const { trashRect, setOverTrash } = useWatchlistDragStore.getState();
        setOverTrash(pointInRect(info.point.x, info.point.y, trashRect));
      }}
      onDragEnd={(_, info) => {
        const { trashRect, setDragging } = useWatchlistDragStore.getState();
        const onTrash = pointInRect(info.point.x, info.point.y, trashRect);
        setDragging(null);
        if (onTrash) onDelete();
        else onReorderEnd();
      }}
      className="border-b border-[var(--border)]"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={clearHold}
      onPointerCancel={clearHold}
      onClick={() => {
        // Suppress the publish for the pointerup that ended a drag.
        if (didDrag.current) {
          didDrag.current = false;
          return;
        }
        onPublish();
      }}
      style={{ cursor: linked ? "pointer" : "default" }}
    >
      <div
        className={cn(
          rowGrid(),
          "group/row group px-2 py-1.5 hover:bg-[var(--bg-hover)]",
        )}
      >
        <span className="inline-flex min-w-0 items-center gap-1">
          <span
            className="flex size-1.5 shrink-0 items-center justify-center"
            title={isOvernight ? "Tradable 24/5 overnight" : undefined}
            aria-label={isOvernight ? "Tradable 24/5 overnight" : undefined}
          >
            {isOvernight ? (
              <span className="size-1.5 rounded-full bg-[var(--c-green-bg)]" />
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
              className="shrink-0 rounded px-1 py-px font-medium font-mono text-[length:var(--fs-table)] leading-none"
              style={earningsBadgeStyle(earningsDays)}
              title={`Earnings ${nextEarningsDate}`}
            >
              {earningsBadgeLabel(earningsDays)}
            </span>
          ) : null}

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
              (quote?.intraday?.length ? quote.intraday : quote?.sparkline) ??
              []
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

export function WatchlistWidget({
  config,
  onConfigChange,
  instanceId,
}: WidgetProps) {
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
  const pageActive = usePageActive();

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
      if (!displayList || removing) return;
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
    [displayList, removing],
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
    return <WidgetLoading cols={[2, 1, 1, 1]} />;
  }

  if (error && !displayList) {
    return <WidgetError message={error} />;
  }

  if (!displayList || !state) {
    return <WidgetEmpty message="No active watchlist" />;
  }

  return (
    <div className="flex h-full flex-col overflow-hidden font-mono text-[length:var(--fs-title)]">
      {marketLoading ? (
        <div className="border-b border-[var(--border)] px-3 py-0.5">
          <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">loading…</span>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-1 scroll-shadow-y">
        <div
          className={cn(
            rowGrid(),
            "gap-x-1 border-b border-[var(--border)] px-2 py-1.5",
          )}
        >
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
                  "text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase",
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
                  "text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase",
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
            onPublish={publish}
          />
        ) : null}

        <Reorder.Group
          // Remount the motion list when this page becomes visible so the rows
          // mount fresh at their final position — otherwise the hidden→visible
          // page-position swap makes layout="position" slide them in from the
          // top-left on every switch.
          key={pageActive ? "shown" : "hidden"}
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
              instanceId={instanceId}
              onPublish={() => publish(symbol)}
              onDragStart={() => setSortState(null)}
              onReorderEnd={handleReorderEnd}
              onDelete={() => void removeSymbol(symbol)}
            />
          ))}
        </Reorder.Group>
      </div>
    </div>
  );
}
