"use client";

import { Info } from "lucide-react";

import { useTickerInfo } from "@/components/workspace/ticker-info-provider";
import { cn } from "@/lib/utils";

export function ClickableSymbol({
  symbol,
  linked,
  onActivate,
  className,
}: {
  symbol: string;
  linked: boolean;
  onActivate: () => void;
  className?: string;
}) {
  const { openTickerInfo } = useTickerInfo();

  return (
    // leading-none: without it this wrapper's line box is ~1.5x the font size,
    // so siblings aligned against it (the watchlist live dot) center on the
    // inflated invisible box instead of the visible glyphs.
    <span className="group/sym inline-flex items-center gap-0.5 leading-none">
      {linked ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onActivate();
          }}
          className={cn(
            "cursor-pointer text-[var(--c-accent)] underline-offset-2 transition-colors hover:underline",
            className,
          )}
        >
          {symbol}
        </button>
      ) : (
        <span className={cn("text-[var(--c-accent)]", className)}>{symbol}</span>
      )}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          openTickerInfo(symbol);
        }}
        aria-label={`${symbol} info`}
        className="text-[var(--fg)] opacity-0 transition-opacity group-hover/sym:opacity-100 group-hover/row:opacity-100 focus-visible:opacity-100"
      >
        <Info className="size-3.5" />
      </button>
    </span>
  );
}
