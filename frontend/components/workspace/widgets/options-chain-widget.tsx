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
  type OptionsChainSide,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const POLL_MS = 30_000;

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

function dteFromExpiry(expiry: string | null): number | null {
  if (!expiry) return null;
  try {
    const exp = new Date(`${expiry}T12:00:00`);
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    return Math.round((exp.getTime() - today.getTime()) / 86_400_000);
  } catch {
    return null;
  }
}

function nearestStrike(
  rows: OptionsChainRow[],
  price: number | null,
): number | null {
  if (price == null || rows.length === 0) return null;
  let best = rows[0]!.strike;
  let bestDist = Math.abs(best - price);
  for (const row of rows) {
    const dist = Math.abs(row.strike - price);
    if (dist < bestDist) {
      best = row.strike;
      bestDist = dist;
    }
  }
  return best;
}

function itmClass(
  side: "call" | "put",
  strike: number,
  underlying: number | null,
): string {
  if (underlying == null) return "text-[var(--fg-dim)]";
  const itm =
    side === "call" ? strike < underlying : strike > underlying;
  return itm ? "text-[var(--fg)]" : "text-[var(--fg-dim)]";
}

function ChainCell({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "tabular-nums text-[length:var(--fs-micro)]",
        className,
      )}
    >
      {children}
    </span>
  );
}

function SideCells({
  side,
  data,
  strike,
  underlying,
}: {
  side: "call" | "put";
  data: OptionsChainSide;
  strike: number;
  underlying: number | null;
}) {
  const fg = itmClass(side, strike, underlying);
  const narrowCells = [formatPrice(data.bid), formatPrice(data.ask), formatIv(data.iv)];
  const wideCells =
    side === "call"
      ? [
          formatCompact(data.volume),
          formatCompact(data.oi),
          formatIv(data.iv),
          formatGreek(data.delta),
          formatPrice(data.bid),
          formatPrice(data.ask),
        ]
      : [
          formatPrice(data.bid),
          formatPrice(data.ask),
          formatGreek(data.delta),
          formatIv(data.iv),
          formatCompact(data.oi),
          formatCompact(data.volume),
        ];

  const align =
    side === "call" ? "justify-end text-right" : "justify-start text-left";

  return (
    <div className="min-w-0">
      <div className={cn("flex min-w-0 items-center gap-1 @[32rem]:hidden", align)}>
        {narrowCells.map((text, idx) => (
          <ChainCell key={idx} className={fg}>
            {text}
          </ChainCell>
        ))}
      </div>
      <div
        className={cn(
          "hidden min-w-0 items-center gap-1 @[32rem]:flex",
          align,
        )}
      >
        {wideCells.map((text, idx) => (
          <ChainCell key={idx} className={fg}>
            {text}
          </ChainCell>
        ))}
      </div>
    </div>
  );
}

export function OptionsChainWidget({ config, onConfigChange }: WidgetProps) {
  const [data, setData] = useState<OptionsChainResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedExpiry, setSelectedExpiry] = useState<string | undefined>();
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

  const { publish, linked } = usePublishSymbol(channel);

  const effectiveExpiry = selectedExpiry ?? data?.expiry ?? undefined;

  useEffect(() => {
    setSelectedExpiry(undefined);
  }, [symbol]);

  const load = useCallback(async () => {
    if (!symbol) return;
    try {
      const result = await fetchOptionsChain(symbol, effectiveExpiry);
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (cancelledRef.current) return;
      setError(
        err instanceof Error ? err.message : "Failed to load options chain",
      );
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, [symbol, effectiveExpiry]);

  useEffect(() => {
    cancelledRef.current = false;
    setLoading(true);
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(timer);
    };
  }, [load]);

  const atmStrike = useMemo(
    () => nearestStrike(data?.rows ?? [], data?.underlying_price ?? null),
    [data?.rows, data?.underlying_price],
  );

  const dte = dteFromExpiry(data?.expiry ?? null);

  if (!symbol) {
    return <WidgetEmpty message="Enter a symbol" />;
  }
  if (loading && !data) {
    return <WidgetLoading cols={[2, 1, 2]} />;
  }
  if (error && !data) {
    return <WidgetError message={error} onRetry={load} />;
  }

  const rows = data?.rows ?? [];
  const expirations = data?.expirations ?? [];

  return (
    <div className="@container flex h-full flex-col overflow-hidden font-mono">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)] px-2 py-1 text-[length:var(--fs-micro)] text-[var(--fg-dim)]">
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
        {data?.underlying_price != null ? (
          <>
            <span className="text-[var(--fg-faint)]">·</span>
            <span className="tabular-nums text-[var(--fg)]">
              ${data.underlying_price.toFixed(2)}
            </span>
          </>
        ) : null}
        <span className="text-[var(--fg-faint)]">·</span>
        <select
          {...dragSafeControlProps}
          value={data?.expiry ?? ""}
          onChange={(e) => setSelectedExpiry(e.target.value)}
          className="workspace-no-drag max-w-[9rem] truncate rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1 py-0.5 text-[length:var(--fs-micro)] text-[var(--fg)] outline-none focus:border-[var(--c-accent)]"
          aria-label="Expiration"
        >
          {expirations.length === 0 ? (
            <option value="">no expirations</option>
          ) : (
            expirations.map((exp) => (
              <option key={exp} value={exp}>
                {exp}
              </option>
            ))
          )}
        </select>
        {dte != null ? (
          <>
            <span className="text-[var(--fg-faint)]">·</span>
            <span className="tabular-nums text-[var(--fg-faint)]">{dte}d</span>
          </>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <WidgetEmpty message="No option chain data for this symbol." />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto scroll-shadow-y">
          <div className="sticky top-0 z-10 grid grid-cols-[1fr_auto_1fr] border-b border-[var(--border)] bg-[var(--bg-panel)] px-2 py-0.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            <span className="text-right @[32rem]:hidden">call bid · ask · iv</span>
            <span className="hidden text-right @[32rem]:inline">
              call vol · oi · iv · δ · bid · ask
            </span>
            <span className="text-center">strike</span>
            <span className="@[32rem]:hidden">bid · ask · iv · put</span>
            <span className="hidden @[32rem]:inline">
              put bid · ask · δ · iv · oi · vol
            </span>
          </div>

          {rows.map((row) => {
            const isAtm = atmStrike != null && row.strike === atmStrike;
            return (
              <button
                key={row.strike}
                type="button"
                onClick={() => publish(symbol)}
                className={cn(
                  "grid w-full grid-cols-[1fr_auto_1fr] items-center gap-1 border-b border-[var(--border)] px-2 py-0.5 text-left transition-colors hover:bg-[var(--bg-hover)]",
                  isAtm && "bg-[var(--bg-hover)]",
                  linked ? "cursor-pointer" : "cursor-default",
                )}
              >
                <SideCells
                  side="call"
                  data={row.call}
                  strike={row.strike}
                  underlying={data?.underlying_price ?? null}
                />
                <span className="px-2 text-center text-[length:var(--fs-table)] font-medium tabular-nums text-[var(--fg)]">
                  {row.strike === Math.trunc(row.strike)
                    ? row.strike.toFixed(0)
                    : row.strike}
                </span>
                <SideCells
                  side="put"
                  data={row.put}
                  strike={row.strike}
                  underlying={data?.underlying_price ?? null}
                />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
