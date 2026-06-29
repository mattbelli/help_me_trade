"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Reorder, useDragControls } from "motion/react";
import { Sparkline } from "@/components/trading/sparkline";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchEarningsHistory,
  fetchOvernightTradable,
  fetchWatchlists,
  searchSymbols,
  updateWatchlist,
  type EarningsEvent,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelStore } from "@/lib/workspace/use-channel-store";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

const ROW_GRID =
  "grid grid-cols-[1.25rem_minmax(0,1fr)_4.5rem_3.25rem_3rem_3.25rem_1rem] items-center gap-x-1";

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

function formatMarketCap(value: number): string {
  if (value >= 1_000_000_000_000) {
    return `$${(value / 1_000_000_000_000).toFixed(2)}T`;
  }
  if (value >= 1_000_000_000) {
    return `$${(value / 1_000_000_000).toFixed(1)}B`;
  }
  if (value >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(0)}M`;
  }
  return `$${value.toLocaleString()}`;
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

function earningsCountdownSuffix(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "1d";
  return `${days}d`;
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
    <div
      ref={wrapRef}
      className="relative flex items-center gap-x-1 border-b border-[var(--border)] bg-[var(--bg-inset)] px-2 py-1.5"
    >
      <span className="w-[1.25rem] shrink-0 text-center font-mono text-[var(--fg-faint)]">
        +
      </span>
      <input
        ref={inputRef}
        type="text"
        value={query}
        disabled={submitting}
        placeholder="Add symbol"
        className="min-w-0 flex-1 bg-transparent font-mono text-[12px] uppercase text-foreground outline-none placeholder:text-[var(--fg-faint)]"
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
        <span className="absolute top-1/2 right-2 -translate-y-1/2 font-mono text-[9px] text-[var(--fg-faint)]">
          …
        </span>
      ) : null}
      {open && suggestions.length > 0 ? (
        <ul className="absolute right-0 left-0 top-full z-10 mt-0.5 max-h-32 overflow-auto rounded border border-[var(--border)] bg-[var(--panel)] py-0.5 shadow-[var(--panel-shadow)]">
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

type Quote = ReturnType<typeof useWatchlistMarket>["quotes"][string];

const EXT_SESSION_BADGE: Record<string, { label: string; title: string }> = {
  pre: { label: "PRE", title: "Pre-market" },
  post: { label: "AFT", title: "After-hours" },
  overnight: { label: "24h", title: "Overnight (24/5)" },
};

const FUNDAMENTALS_GRID =
  "grid grid-cols-3 gap-x-3 gap-y-1.5 px-2 py-2";

function EarningsReactionsRow({ symbol }: { symbol: string }) {
  const [events, setEvents] = useState<EarningsEvent[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEarningsHistory(symbol, 6)
      .then((res) => {
        if (!cancelled) setEvents(res.events);
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div
      style={{
        borderTop: "1px solid var(--border)",
        padding: "4px 8px 5px",
        background: "var(--bg-inset)",
      }}
    >
      <span
        className="text-[9px] font-medium tracking-[0.04em] uppercase"
        style={{ color: "var(--fg-faint)", display: "block", marginBottom: 3 }}
      >
        Earnings (EPS surprise)
      </span>
      {events === null ? (
        <span
          className="text-[10px] tabular-nums"
          style={{ color: "var(--fg-faint)" }}
        >
          …
        </span>
      ) : events.length === 0 ? (
        <span
          className="text-[10px] tabular-nums"
          style={{ color: "var(--fg-faint)" }}
        >
          no data
        </span>
      ) : (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          {events.map((ev) => {
            const surprise = ev.surprise_pct;
            const beat = surprise != null && surprise >= 0;
            const color = surprise == null
              ? "var(--fg-faint)"
              : beat
              ? "var(--c-green)"
              : "var(--c-red)";
            const surpriseStr =
              surprise == null
                ? "—"
                : `${surprise >= 0 ? "+" : ""}${surprise.toFixed(1)}%`;
            const tooltipParts: string[] = [];
            if (ev.eps_actual != null)
              tooltipParts.push(`actual $${ev.eps_actual.toFixed(2)}`);
            if (ev.eps_estimate != null)
              tooltipParts.push(`est $${ev.eps_estimate.toFixed(2)}`);
            const tooltip = tooltipParts.length > 0 ? tooltipParts.join(" vs ") : undefined;
            return (
              <span
                key={ev.period}
                className="text-[10px] tabular-nums"
                style={{ color: "var(--fg-faint)" }}
                title={tooltip}
              >
                {ev.label}{" "}
                <span style={{ color }}>{surpriseStr}</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ExpandableFundamentalsPanel({
  expanded,
  symbol,
  quote,
}: {
  expanded: boolean;
  symbol: string;
  quote: Quote | undefined;
}) {
  const [mounted, setMounted] = useState(false);
  const [openRows, setOpenRows] = useState(false);

  useEffect(() => {
    if (expanded) setMounted(true);
  }, [expanded]);

  useEffect(() => {
    if (mounted && expanded) {
      const id = requestAnimationFrame(() => setOpenRows(true));
      return () => cancelAnimationFrame(id);
    }
    setOpenRows(false);
  }, [mounted, expanded]);

  if (!mounted) return null;

  return (
    <div
      className={cn(
        "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
        openRows && expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
      )}
      onTransitionEnd={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.propertyName === "grid-template-rows" && !expanded) {
          setMounted(false);
        }
      }}
    >
      <div className="min-h-0 overflow-hidden">
        <FundamentalsPanel symbol={symbol} quote={quote} />
      </div>
    </div>
  );
}

// A futures symbol starts with "/", e.g. /ES, /NQ, /CL, /ESU26.
function isFuturesSymbol(symbol: string): boolean {
  return symbol.startsWith("/");
}

function FundamentalsPanel({
  symbol,
  quote,
}: {
  symbol: string;
  quote: Quote | undefined;
}) {
  const dash = "—";
  const futures = isFuturesSymbol(symbol);

  const peStr =
    quote?.pe != null ? quote.pe.toFixed(1) : dash;
  const mktCapStr =
    quote?.market_cap != null ? formatMarketCap(quote.market_cap) : dash;
  const epsStr =
    quote?.eps != null ? `$${quote.eps.toFixed(2)}` : dash;
  const divYieldStr =
    quote?.div_yield != null ? `${quote.div_yield.toFixed(2)}%` : dash;
  const lastEarningsStr = quote?.last_earnings ?? dash;

  const nextEarningsDate = quote?.next_earnings ?? null;
  const nextEarningsDays =
    nextEarningsDate != null ? daysUntil(nextEarningsDate) : null;
  const nextEarningsStr =
    nextEarningsDate == null
      ? dash
      : nextEarningsDays != null &&
          nextEarningsDays >= 0 &&
          nextEarningsDays <= 14
        ? `${nextEarningsDate} · ${earningsCountdownSuffix(nextEarningsDays)}`
        : nextEarningsDate;
  const nextEarningsColor =
    nextEarningsDays != null &&
    nextEarningsDays >= 0 &&
    nextEarningsDays <= 14
      ? earningsUrgencyColor(nextEarningsDays)
      : "var(--fg-dim)";

  const items: { label: string; value: string; color?: string }[] = [
    { label: "P/E", value: peStr },
    { label: "Mkt Cap", value: mktCapStr },
    { label: "EPS", value: epsStr },
    { label: "Div Yield", value: divYieldStr },
    { label: "Last Earns", value: lastEarningsStr },
    { label: "Next Earns", value: nextEarningsStr, color: nextEarningsColor },
  ];

  return (
    <>
      <div
        className={FUNDAMENTALS_GRID}
        style={{
          borderTop: "1px solid var(--border)",
          background: "var(--bg-inset)",
        }}
      >
        {items.map(({ label, value, color }) => (
          <div key={label} className="flex flex-col gap-px">
            <span
              className="text-[9px] font-medium tracking-[0.04em] uppercase"
              style={{ color: "var(--fg-faint)" }}
            >
              {label}
            </span>
            <span
              className="tabular-nums text-[11px]"
              style={{ color: color ?? "var(--fg-dim)" }}
            >
              {value}
            </span>
          </div>
        ))}
      </div>
      {!futures && <EarningsReactionsRow symbol={symbol} />}
    </>
  );
}

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
  const [expanded, setExpanded] = useState(false);

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
          "group px-2 py-1.5 hover:bg-[var(--bg-hover)]",
        )}
      >
        <button
          type="button"
          className="cursor-grab select-none font-mono text-[10px] leading-none text-[var(--fg-faint)] opacity-40 transition-opacity group-hover:opacity-100 active:cursor-grabbing"
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
          <span className="truncate font-medium text-[var(--c-accent)]">
            {symbol}
          </span>
          {showEarningsBadge && nextEarningsDate ? (
            <span
              className="shrink-0 rounded px-0.5 font-mono text-[9px] leading-none"
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
            className="rounded px-0.5 font-mono text-[11px] leading-none text-[var(--fg-faint)] opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--bg-inset)] hover:text-[var(--c-red)] disabled:opacity-40"
            aria-label={`Remove ${symbol}`}
          >
            ×
          </button>
        </span>
        <span
          className="inline-flex items-center justify-end gap-0.5 tabular-nums text-foreground"
          title={extBadge?.title}
        >
          {quote ? formatPrice(displayPrice) : "—"}
          {extBadge ? (
            <span className="rounded px-0.5 font-mono text-[8px] leading-none text-[var(--fg-dim)] ring-1 ring-[var(--border)]">
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
        <button
          type="button"
          aria-label={expanded ? `Collapse ${symbol} details` : `Expand ${symbol} details`}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="flex items-center justify-center rounded text-[var(--fg-faint)] opacity-40 transition-all group-hover:opacity-100 hover:bg-[var(--bg-inset)] hover:text-[var(--fg-dim)]"
          style={{ fontSize: "9px", lineHeight: 1, padding: "1px 2px" }}
        >
          {expanded ? "▲" : "▼"}
        </button>
      </div>
      <ExpandableFundamentalsPanel
        expanded={expanded}
        symbol={symbol}
        quote={quote}
      />
    </Reorder.Item>
  );
}

export function WatchlistWidget({ config }: WidgetProps) {
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
                  "text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
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
                  "text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                  idx === 0 ? "text-left" : "text-right",
                )}
              >
                {h.label}
              </span>
            ),
          )}
          <span />
        </div>

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
              onPublish={() => publishSymbol(symbol)}
              onRemove={() => void removeSymbol(symbol)}
              onDragStart={() => setSortState(null)}
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
