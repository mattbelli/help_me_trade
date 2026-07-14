"use client";

import { useState, type ReactElement } from "react";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import { fetchTickerInfo, type TickerInfo } from "@/lib/ticker-info-api";

// Casually sweeping the pointer down a table shouldn't re-hit the ticker
// endpoint per row — cache previews for a few minutes, module-wide.
const cache = new Map<string, { at: number; data: TickerInfo }>();
const TTL_MS = 5 * 60_000;

function formatCap(millions?: number): string | null {
  if (!millions || millions <= 0) return null;
  if (millions >= 1_000_000) return `$${(millions / 1_000_000).toFixed(2)}T`;
  if (millions >= 1_000) return `$${(millions / 1_000).toFixed(1)}B`;
  return `$${millions.toFixed(0)}M`;
}

/**
 * Hover a ticker → small preview card (logo, name, price, market cap) with a
 * click-through to the full company-info panel. Wraps the ticker element as
 * the hover trigger; touch devices never open it (the ⓘ button stays the
 * touch path).
 */
export function SymbolHoverCard({
  symbol,
  children,
}: {
  symbol: string;
  children: ReactElement;
}) {
  const { openTickerInfo } = useTickerInfo();
  const [info, setInfo] = useState<TickerInfo | null>(null);
  const [failed, setFailed] = useState(false);

  function handleOpenChange(open: boolean) {
    if (!open) return;
    const hit = cache.get(symbol);
    if (hit && Date.now() - hit.at < TTL_MS) {
      setInfo(hit.data);
      return;
    }
    setInfo(null);
    setFailed(false);
    fetchTickerInfo(symbol)
      .then((data) => {
        cache.set(symbol, { at: Date.now(), data });
        setInfo(data);
      })
      .catch(() => setFailed(true));
  }

  const quote = info?.facts.quote;
  const changePct = quote?.change_pct;
  const cap = formatCap(
    info?.profile.marketCapitalization ?? info?.metrics.market_cap,
  );
  const pe = info?.metrics.pe_ttm;

  return (
    <HoverCard onOpenChange={handleOpenChange}>
      <HoverCardTrigger delay={200} closeDelay={120} render={children} />
      {/* side=right keeps the card OFF the ticker column — opened below, it
          covers the next rows and catches the pointer as the user moves down
          the list (the card-stays-open-on-hover behavior then shows the wrong
          symbol). Flips left automatically at the viewport edge. */}
      <HoverCardContent
        side="right"
        align="start"
        sideOffset={10}
        className="w-72 border border-[var(--border)] bg-[var(--panel)] p-3 font-mono text-[length:var(--fs-body)] text-foreground shadow-[var(--panel-shadow)]"
      >
        {failed ? (
          <span className="text-[var(--fg-faint)]">no data for {symbol}</span>
        ) : !info ? (
          <span className="text-[var(--fg-faint)]">loading…</span>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2.5">
              {info.profile.logo ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={info.profile.logo}
                  alt=""
                  className="size-8 shrink-0 rounded bg-white/90 object-contain"
                />
              ) : null}
              <div className="min-w-0">
                <div className="truncate font-medium">
                  {info.profile.name ?? symbol}
                </div>
                <div className="truncate text-[length:var(--fs-table)] text-[var(--fg-faint)] lowercase">
                  {[info.profile.finnhubIndustry, info.profile.exchange]
                    .filter(Boolean)
                    .join(" · ") || symbol}
                </div>
              </div>
            </div>

            {quote?.price != null ? (
              <div className="flex items-baseline gap-2 tabular-nums">
                <span className="text-[length:var(--fs-title)] font-medium">
                  {quote.price.toFixed(2)}
                </span>
                {changePct != null ? (
                  <span
                    className={
                      changePct >= 0
                        ? "text-[var(--c-green)]"
                        : "text-[var(--c-red)]"
                    }
                  >
                    {changePct >= 0 ? "+" : ""}
                    {changePct.toFixed(2)}%
                  </span>
                ) : null}
              </div>
            ) : null}

            {cap || pe != null ? (
              <div className="flex gap-3 text-[length:var(--fs-table)] text-[var(--fg-dim)] tabular-nums">
                {cap ? (
                  <span>
                    <span className="text-[var(--fg-faint)]">mcap </span>
                    {cap}
                  </span>
                ) : null}
                {pe != null ? (
                  <span>
                    <span className="text-[var(--fg-faint)]">p/e </span>
                    {pe.toFixed(1)}
                  </span>
                ) : null}
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => openTickerInfo(symbol)}
              className="self-start text-[length:var(--fs-table)] text-[var(--c-accent)] underline-offset-2 hover:underline"
            >
              full info →
            </button>
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}
