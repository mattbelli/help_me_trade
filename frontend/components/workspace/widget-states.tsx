"use client";

import * as React from "react";

import { TerminalButton } from "@/components/ui/terminal/terminal-button";
import { cn } from "@/lib/utils";

/**
 * Shared widget state kit (UI_AUDIT S2 / UI_POLISH N2). One terminal-styled,
 * dense (p-3, mono) treatment for the three "no rows on screen" states so every
 * widget reads the same. Density is the product: left-aligned, small — never a
 * centered enlarged void.
 */

/** "Genuinely nothing here." One line of copy, optional hint + single action. */
export function WidgetEmpty({
  message,
  hint,
  action,
  className,
}: {
  message: string;
  hint?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}) {
  return (
    <div className={cn("p-3 font-mono", className)}>
      <p className="text-[length:var(--fs-body)] text-[var(--fg-dim)]">
        {message}
      </p>
      {hint ? (
        <p className="mt-0.5 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {hint}
        </p>
      ) : null}
      {action ? (
        <TerminalButton className="mt-2" onClick={action.onClick}>
          {action.label}
        </TerminalButton>
      ) : null}
    </div>
  );
}

/**
 * Terminal skeleton (N2): a mono ROW skeleton mimicking a table, cells as small
 * rounded rects with the tw-shimmer sweep (~1.2s, --bg-hover over --bg-raised).
 * `cols` = relative column widths so callers can match their real layout.
 * Under reduced motion the sweep layer is hidden (globals.css guard) → static
 * cells, no motion.
 */
export function WidgetLoading({
  rows = 4,
  cols = [3, 2, 2],
  className,
}: {
  rows?: number;
  cols?: number[];
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5 p-3", className)} aria-hidden>
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-2">
          {cols.map((w, c) => (
            <div
              key={c}
              className="relative h-3 overflow-hidden rounded-[2px] bg-[var(--bg-raised)]"
              style={{ flexGrow: w, flexBasis: 0 }}
            >
              <span
                className="widget-skeleton-sweep shimmer shimmer-bg absolute inset-0 block"
                style={
                  {
                    "--shimmer-color": "var(--bg-hover)",
                    "--shimmer-duration": 1200,
                  } as React.CSSProperties
                }
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * Inline error row (I3, trust-critical): --c-red accent + message + optional
 * [retry]. "I can't see your positions" must never look like "you have none."
 * Never a giant centered void.
 */
export function WidgetError({
  message = "failed to load",
  onRetry,
  className,
}: {
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 p-3 font-mono text-[length:var(--fs-body)]",
        className,
      )}
    >
      <span className="text-[var(--c-red)]">{message}</span>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="text-[var(--fg-dim)] underline-offset-2 transition-colors hover:text-foreground hover:underline"
        >
          [retry]
        </button>
      ) : null}
    </div>
  );
}
