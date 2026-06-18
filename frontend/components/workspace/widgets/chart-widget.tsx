"use client";

import { useState } from "react";

import { TvLightChart } from "@/components/trading/tv-light-chart";

export function ChartWidget() {
  const [symbol, setSymbol] = useState("SPY");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-3 py-1.5">
        <label
          htmlFor="workspace-chart-symbol"
          className="font-mono text-[10px] text-[var(--fg-faint)] uppercase"
        >
          Symbol
        </label>
        <input
          id="workspace-chart-symbol"
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase().trim())}
          className="w-20 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-2 py-0.5 font-mono text-[11px] text-foreground uppercase outline-none focus:border-[var(--c-accent)]"
        />
      </div>
      <div className="min-h-0 flex-1">
        {symbol ? (
          <TvLightChart symbol={symbol} compact />
        ) : (
          <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
            Enter a symbol
          </p>
        )}
      </div>
    </div>
  );
}
