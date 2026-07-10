"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
} from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { SymbolSearchInput } from "@/components/workspace/symbol-search-input";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchOptionsChain,
  type OptionsChainResult,
  type OptionsChainRow,
  type OptionsChainStrikes,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ChannelId } from "@/lib/workspace/channels";
import type { WidgetInstanceConfig } from "@/lib/workspace/layout-persistence";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const POLL_MS = 30_000;
const STALE_MS = 20 * 60 * 1000;

const STRIKES_OPTIONS: OptionsChainStrikes[] = [6, 10, 20, 40, "all"];

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

type OptionsChainWidgetConfig = WidgetInstanceConfig & {
  strikes?: OptionsChainStrikes;
};

const dragSafeControlProps = {
  onClick: (e: MouseEvent) => e.stopPropagation(),
  onPointerDown: (e: PointerEvent) => e.stopPropagation(),
};

function formatIv(iv: number | null): string {
  if (iv == null) return "—";
  const pct = iv <= 2 ? iv * 100 : iv;
  return `${pct.toFixed(1)}%`;
}

function formatGreek(val: number | null, decimals = 2): string {
  if (val == null) return "—";
  return val.toFixed(decimals);
}

function formatCompact(val: number | null): string {
  if (val == null) return "—";
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}k`;
  return String(val);
}

function formatPrice(val: number | null): string {
  if (val == null) return "—";
  return val.toFixed(2);
}

function todayEtIso(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

function dteFromExpiryEt(expiry: string): number {
  const exp = new Date(`${expiry.slice(0, 10)}T12:00:00`);
  const today = new Date(`${todayEtIso()}T12:00:00`);
  return Math.round((exp.getTime() - today.getTime()) / 86_400_000);
}

function isMonthlyExpiry(expiry: string): boolean {
  const d = new Date(`${expiry.slice(0, 10)}T12:00:00`);
  if (d.getDay() !== 5) return false;
  const day = d.getDate();
  return day >= 15 && day <= 21;
}

function formatExpiryLabel(expiry: string): string {
  const month = Number.parseInt(expiry.slice(5, 7), 10);
  const day = Number.parseInt(expiry.slice(8, 10), 10);
  const mon = month >= 1 && month <= 12 ? MONTHS[month - 1] : "???";
  const weekly = !isMonthlyExpiry(expiry) ? " (W)" : "";
  const dte = dteFromExpiryEt(expiry);
  return `${mon} ${day}${weekly} · ${dte}d`;
}

function formatAsOfEt(asOf: string): string {
  return new Date(asOf)
    .toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    })
    .toLowerCase();
}

function isStale(asOf: string | null | undefined): boolean {
  if (!asOf) return false;
  const t = new Date(asOf).getTime();
  return Number.isFinite(t) && Date.now() - t > STALE_MS;
}

function spotDividerAfterIndex(
  rows: OptionsChainRow[],
  price: number | null,
): number | null {
  if (price == null || rows.length < 2) return null;
  for (let i = 0; i < rows.length - 1; i++) {
    if (rows[i]!.strike < price && rows[i + 1]!.strike > price) {
      return i;
    }
  }
  return null;
}

function itmTint(
  side: "call" | "put",
  strike: number,
  underlying: number | null,
): string {
  if (underlying == null) return "";
  const itm =
    side === "call" ? strike < underlying : strike > underlying;
  return itm ? "bg-[color-mix(in_srgb,var(--bg-hover)_55%,transparent)]" : "";
}

function NullCell({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[var(--fg-faint)] tabular-nums">{children}</span>
  );
}

function ValueCell({
  children,
  className,
  muted,
}: {
  children: React.ReactNode;
  className?: string;
  muted?: boolean;
}) {
  return (
    <span
      className={cn(
        "tabular-nums text-[length:var(--fs-table)]",
        muted ? "text-[var(--fg-dim)]" : "text-[var(--fg)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

function renderPrice(val: number | null, tone: "bid" | "ask" | "last") {
  const text = formatPrice(val);
  if (text === "—") return <NullCell>{text}</NullCell>;
  const color =
    tone === "bid"
      ? "text-[var(--c-green)]"
      : tone === "ask"
        ? "text-[var(--c-red)]"
        : "text-[var(--fg)]";
  return <ValueCell className={color}>{text}</ValueCell>;
}

function renderMetric(
  val: number | null,
  formatter: (v: number | null) => string,
  muted = false,
) {
  const text = formatter(val);
  if (text === "—") return <NullCell>{text}</NullCell>;
  return <ValueCell muted={muted}>{text}</ValueCell>;
}

function ChainHeader({ wide }: { wide: boolean }) {
  const labelClass =
    "border-r border-[var(--border)] px-1.5 text-right text-[length:var(--fs-micro)] lowercase text-[var(--fg-faint)]";
  const strikeClass =
    "border-r border-[var(--border)] px-2 text-center text-[length:var(--fs-micro)] lowercase text-[var(--fg-faint)]";

  if (wide) {
    return (
      <div className="sticky top-0 z-10 grid grid-cols-[repeat(7,minmax(0,1fr))_5rem_repeat(7,minmax(0,1fr))] border-b border-[var(--border)] bg-[var(--bg-panel)] px-2 py-0.5">
        {(["oi", "vol", "iv", "δ", "last", "ask", "bid"] as const).map(
          (label) => (
            <span key={`c-${label}`} className={labelClass}>
              {label}
            </span>
          ),
        )}
        <span className={strikeClass}>strike</span>
        {(["bid", "ask", "last", "δ", "iv", "vol", "oi"] as const).map(
          (label) => (
            <span key={`p-${label}`} className={labelClass}>
              {label}
            </span>
          ),
        )}
      </div>
    );
  }

  return (
    <div className="sticky top-0 z-10 grid grid-cols-[repeat(3,minmax(0,1fr))_5rem_repeat(3,minmax(0,1fr))] border-b border-[var(--border)] bg-[var(--bg-panel)] px-2 py-0.5">
      {(["last", "ask", "bid"] as const).map((label) => (
        <span key={`c-${label}`} className={labelClass}>
          {label}
        </span>
      ))}
      <span className={strikeClass}>strike</span>
      {(["bid", "ask", "last"] as const).map((label) => (
        <span key={`p-${label}`} className={labelClass}>
          {label}
        </span>
      ))}
    </div>
  );
}

function ChainRow({
  row,
  underlying,
  wide,
  onStrikeClick,
  linked,
}: {
  row: OptionsChainRow;
  underlying: number | null;
  wide: boolean;
  onStrikeClick: () => void;
  linked: boolean;
}) {
  const call = row.call;
  const put = row.put;
  const callTint = itmTint("call", row.strike, underlying);
  const putTint = itmTint("put", row.strike, underlying);
  const strikeLabel =
    row.strike === Math.trunc(row.strike)
      ? row.strike.toFixed(0)
      : String(row.strike);

  const callWide = [
    renderMetric(call.oi, formatCompact, true),
    renderMetric(call.volume, formatCompact, true),
    renderMetric(call.iv, formatIv),
    renderMetric(call.delta, (v) => formatGreek(v)),
    renderPrice(call.last, "last"),
    renderPrice(call.ask, "ask"),
    renderPrice(call.bid, "bid"),
  ];
  const putWide = [
    renderPrice(put.bid, "bid"),
    renderPrice(put.ask, "ask"),
    renderPrice(put.last, "last"),
    renderMetric(put.delta, (v) => formatGreek(v)),
    renderMetric(put.iv, formatIv),
    renderMetric(put.volume, formatCompact, true),
    renderMetric(put.oi, formatCompact, true),
  ];
  const callNarrow = [
    renderPrice(call.last, "last"),
    renderPrice(call.ask, "ask"),
    renderPrice(call.bid, "bid"),
  ];
  const putNarrow = [
    renderPrice(put.bid, "bid"),
    renderPrice(put.ask, "ask"),
    renderPrice(put.last, "last"),
  ];

  const gridClass = wide
    ? "grid-cols-[repeat(7,minmax(0,1fr))_5rem_repeat(7,minmax(0,1fr))]"
    : "grid-cols-[repeat(3,minmax(0,1fr))_5rem_repeat(3,minmax(0,1fr))]";

  return (
    <button
      type="button"
      onClick={onStrikeClick}
      className={cn(
        "grid w-full border-b border-[var(--border)] px-2 text-left transition-colors hover:bg-[var(--bg-hover)]",
        gridClass,
        linked ? "cursor-pointer" : "cursor-default",
      )}
    >
      {(wide ? callWide : callNarrow).map((cell, idx) => (
        <span
          key={`c-${idx}`}
          className={cn("flex items-center justify-end border-r border-[var(--border)] px-1.5 py-0.5", callTint)}
        >
          {cell}
        </span>
      ))}
      <span className="flex items-center justify-center border-r border-[var(--border)] px-2 py-0.5 text-[length:var(--fs-table)] font-medium tabular-nums text-foreground">
        {strikeLabel}
      </span>
      {(wide ? putWide : putNarrow).map((cell, idx) => (
        <span
          key={`p-${idx}`}
          className={cn("flex items-center justify-end border-r border-[var(--border)] px-1.5 py-0.5", putTint)}
        >
          {cell}
        </span>
      ))}
    </button>
  );
}

function SpotDividerRow({
  symbol,
  price,
  innerRef,
}: {
  symbol: string;
  price: number;
  innerRef?: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={innerRef}
      className="border-y border-[var(--border)] px-2 py-0.5 text-center text-[length:var(--fs-table)] text-[var(--c-accent)] tabular-nums"
    >
      {symbol} · {price.toFixed(2)}
    </div>
  );
}

function ChainTable({
  data,
  symbol,
  linked,
  onPublish,
}: {
  data: OptionsChainResult;
  symbol: string;
  linked: boolean;
  onPublish: () => void;
}) {
  const spotRef = useRef<HTMLDivElement | null>(null);
  const rows = data.rows;
  const underlying = data.underlying_price;
  const dividerAfter = spotDividerAfterIndex(rows, underlying);

  useEffect(() => {
    if (dividerAfter == null || !spotRef.current) return;
    spotRef.current.scrollIntoView({ block: "center" });
  }, [data.expiry, dividerAfter, rows.length, underlying]);

  if (rows.length === 0) {
    return (
      <WidgetEmpty message="No strikes in this window for this expiration." />
    );
  }

  return (
    <div className="@container min-h-0 overflow-auto scroll-shadow-y">
      <div className="@[44rem]:hidden">
        <ChainHeader wide={false} />
        {rows.map((row, idx) => (
          <div key={row.strike}>
            <ChainRow
              row={row}
              underlying={underlying}
              wide={false}
              linked={linked}
              onStrikeClick={onPublish}
            />
            {dividerAfter === idx ? (
              <SpotDividerRow
                symbol={symbol}
                price={underlying!}
                innerRef={spotRef}
              />
            ) : null}
          </div>
        ))}
      </div>
      <div className="hidden @[44rem]:block">
        <ChainHeader wide />
        {rows.map((row, idx) => (
          <div key={row.strike}>
            <ChainRow
              row={row}
              underlying={underlying}
              wide
              linked={linked}
              onStrikeClick={onPublish}
            />
            {dividerAfter === idx ? (
              <SpotDividerRow
                symbol={symbol}
                price={underlying!}
                innerRef={spotRef}
              />
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

export function OptionsChainWidget({ config, onConfigChange }: WidgetProps) {
  const chainConfig = config as OptionsChainWidgetConfig;
  const strikes: OptionsChainStrikes = chainConfig.strikes ?? 10;

  const [expirations, setExpirations] = useState<string[]>([]);
  const [chainCache, setChainCache] = useState<Map<string, OptionsChainResult>>(
    () => new Map(),
  );
  const [expandedExpiry, setExpandedExpiry] = useState<string | null>(null);
  const [loadingExpiry, setLoadingExpiry] = useState<string | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const manualMode = channel === "none";
  const committedSymbol = config.symbol ?? "SPY";
  const symbol =
    channel !== "none"
      ? (channelSymbol ?? committedSymbol)
      : committedSymbol;

  const [draftSymbol, setDraftSymbol] = useState(committedSymbol);
  useEffect(() => {
    setDraftSymbol(committedSymbol);
  }, [committedSymbol]);

  const commitSymbol = (override?: string) => {
    const next = (override ?? draftSymbol).toUpperCase().trim();
    if (next && next !== committedSymbol) {
      onConfigChange({ symbol: next });
    } else {
      setDraftSymbol(committedSymbol);
    }
  };

  const setStrikes = (next: OptionsChainStrikes) => {
    onConfigChange({ strikes: next });
  };

  const { publish, linked } = usePublishSymbol(channel);

  const loadExpiry = useCallback(
    async (expiry: string, opts?: { silent?: boolean }) => {
      if (!symbol) return;
      if (!opts?.silent) setLoadingExpiry(expiry);
      try {
        const result = await fetchOptionsChain(symbol, expiry, strikes);
        if (cancelledRef.current) return;
        setChainCache((prev) => new Map(prev).set(expiry, result));
        setAsOf(result.as_of);
        setError(null);
        return result;
      } catch (err) {
        if (cancelledRef.current) return;
        if (!opts?.silent) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load options chain",
          );
        }
        return null;
      } finally {
        if (!cancelledRef.current && !opts?.silent) {
          setLoadingExpiry((cur) => (cur === expiry ? null : cur));
        }
      }
    },
    [symbol, strikes],
  );

  const loadInitial = useCallback(async () => {
    if (!symbol) return;
    setInitialLoading(true);
    setError(null);
    setChainCache(new Map());
    setExpandedExpiry(null);
    try {
      const result = await fetchOptionsChain(symbol, undefined, strikes);
      if (cancelledRef.current) return;
      setExpirations(result.expirations);
      setAsOf(result.as_of);
      const expiry = result.expiry;
      if (expiry) {
        setChainCache(new Map([[expiry, result]]));
        setExpandedExpiry(expiry);
      }
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(
        err instanceof Error ? err.message : "Failed to load options chain",
      );
    } finally {
      if (!cancelledRef.current) setInitialLoading(false);
    }
  }, [symbol, strikes]);

  useEffect(() => {
    cancelledRef.current = false;
    void loadInitial();
    return () => {
      cancelledRef.current = true;
    };
  }, [loadInitial]);

  useEffect(() => {
    if (!symbol || !expandedExpiry) return;
    const timer = setInterval(() => {
      void loadExpiry(expandedExpiry, { silent: true });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [symbol, expandedExpiry, loadExpiry]);

  const handleToggleExpiry = (expiry: string) => {
    if (expandedExpiry === expiry) {
      // Clicking the open expiry collapses it (founder: "can't close a tab").
      setExpandedExpiry(null);
      return;
    }
    setExpandedExpiry(expiry);
    if (!chainCache.has(expiry)) {
      void loadExpiry(expiry);
    } else {
      const cached = chainCache.get(expiry);
      if (cached) {
        setAsOf(cached.as_of);
      }
    }
  };

  const expandedData = expandedExpiry
    ? (chainCache.get(expandedExpiry) ?? null)
    : null;

  const staleNote = useMemo(() => {
    if (!isStale(asOf)) return null;
    return `as of ${formatAsOfEt(asOf!)} ET`;
  }, [asOf]);

  if (!symbol) {
    return <WidgetEmpty message="Enter a symbol" />;
  }
  if (initialLoading) {
    return <WidgetLoading cols={[2, 1, 2]} />;
  }
  if (error && expirations.length === 0) {
    return <WidgetError message={error} onRetry={loadInitial} />;
  }

  return (
    <div className="@container flex h-full flex-col overflow-hidden font-mono">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-2 py-1 text-[length:var(--fs-micro)] text-[var(--fg-dim)]">
        {manualMode ? (
          <SymbolSearchInput
            value={draftSymbol}
            onChange={setDraftSymbol}
            onCommit={commitSymbol}
            onEscape={() => setDraftSymbol(committedSymbol)}
            dragSafeControlProps={dragSafeControlProps}
            inputClassName="workspace-no-drag w-16 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1.5 py-0.5 font-mono text-[length:var(--fs-table)] uppercase text-[var(--c-accent)] outline-none focus:border-[var(--c-accent)]"
          />
        ) : (
          <span className="font-medium text-[var(--c-accent)]">{symbol}</span>
        )}
        <span className="text-[var(--fg-faint)]">·</span>
        <select
          {...dragSafeControlProps}
          value={String(strikes)}
          onChange={(e) => {
            const raw = e.target.value;
            const next =
              raw === "all"
                ? "all"
                : (Number.parseInt(raw, 10) as OptionsChainStrikes);
            setStrikes(next);
          }}
          className="workspace-no-drag rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1 py-0.5 text-[length:var(--fs-micro)] text-[var(--fg)] outline-none focus:border-[var(--c-accent)]"
          aria-label="Strikes window"
        >
          {STRIKES_OPTIONS.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              strikes: {opt}
            </option>
          ))}
        </select>
        {staleNote ? (
          <span className="ml-auto lowercase text-[var(--fg-faint)]">
            {staleNote}
          </span>
        ) : (
          <span className="ml-auto" />
        )}
      </div>

      {expirations.length === 0 ? (
        <WidgetEmpty message="No option chain data for this symbol." />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
          {expirations.map((expiry) => {
            const expanded = expandedExpiry === expiry;
            const cached = chainCache.get(expiry);
            const loading = loadingExpiry === expiry;

            return (
              <div key={expiry} className="border-b border-[var(--border)]">
                <button
                  type="button"
                  {...dragSafeControlProps}
                  onClick={() => handleToggleExpiry(expiry)}
                  className="flex w-full items-center gap-1 px-2 py-1 text-left text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)]"
                >
                  {expanded ? (
                    <ChevronDown className="size-3 shrink-0" />
                  ) : (
                    <ChevronRight className="size-3 shrink-0" />
                  )}
                  <span>{formatExpiryLabel(expiry)}</span>
                </button>

                {expanded ? (
                  loading && !cached ? (
                    <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                      loading…
                    </div>
                  ) : cached ? (
                    <ChainTable
                      data={cached}
                      symbol={symbol}
                      linked={linked}
                      onPublish={() => publish(symbol)}
                    />
                  ) : error ? (
                    <div className="px-2 py-2 text-[length:var(--fs-micro)] text-[var(--c-red)]">
                      {error}
                    </div>
                  ) : null
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
