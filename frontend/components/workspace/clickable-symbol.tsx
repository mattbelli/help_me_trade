"use client";

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
  if (!linked) {
    return (
      <span className={cn("text-[var(--c-accent)]", className)}>{symbol}</span>
    );
  }
  return (
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
  );
}
