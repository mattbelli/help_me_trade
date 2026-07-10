"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Chart alert-create popover + right-click menu chrome, shared by TvLightChart
 * and NativeChart (extracted from tv-light-chart so the TV file can die
 * without taking the alert flow with it — CHART_ENGINE.md Phase 2/3).
 */

export const ALERT_DIRECTIONS = ["above", "below", "cross"] as const;
export type AlertDirection = (typeof ALERT_DIRECTIONS)[number];

export function defaultAlertDirection(
  clickedPrice: number,
  currentPrice: number | undefined,
): AlertDirection {
  if (currentPrice == null) return "cross";
  if (clickedPrice > currentPrice) return "above";
  if (clickedPrice < currentPrice) return "below";
  return "cross";
}

/** Keep a chart popup inside its container with an 8px margin. */
function useClampedPosition(
  x: number,
  y: number,
  containerRef: RefObject<HTMLDivElement | null>,
  selfRef: RefObject<HTMLDivElement | null>,
) {
  const [position, setPosition] = useState({ left: x, top: y });
  useEffect(() => {
    const container = containerRef.current;
    const self = selfRef.current;
    if (!container || !self) {
      setPosition({ left: x, top: y });
      return;
    }
    const margin = 8;
    const maxLeft = Math.max(margin, container.clientWidth - self.offsetWidth - margin);
    const maxTop = Math.max(margin, container.clientHeight - self.offsetHeight - margin);
    setPosition({
      left: Math.min(Math.max(margin, x), maxLeft),
      top: Math.min(Math.max(margin, y), maxTop),
    });
  }, [x, y, containerRef, selfRef]);
  return position;
}

export type ChartMenuItem = {
  label: ReactNode;
  onSelect: () => void;
};

type ChartContextMenuProps = {
  menuRef: RefObject<HTMLDivElement | null>;
  x: number;
  y: number;
  containerRef: RefObject<HTMLDivElement | null>;
  items: ChartMenuItem[];
};

/** Minimal right-click menu positioned within the chart container. */
export function ChartContextMenu({
  menuRef,
  x,
  y,
  containerRef,
  items,
}: ChartContextMenuProps) {
  const position = useClampedPosition(x, y, containerRef, menuRef);
  return (
    <div
      ref={menuRef}
      className="workspace-no-drag absolute z-40 min-w-[160px] overflow-hidden rounded-md border border-border/80 bg-background/95 py-0.5 shadow-md backdrop-blur-sm"
      style={{ left: position.left, top: position.top }}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      {items.map((item, i) => (
        <button
          key={i}
          type="button"
          className="w-full px-2.5 py-1.5 text-left text-[11px] text-foreground transition-colors hover:bg-secondary/80"
          onClick={item.onSelect}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

type AlertCreatePopoverProps = {
  x: number;
  y: number;
  containerRef: RefObject<HTMLDivElement | null>;
  price: string;
  direction: AlertDirection;
  repeat: boolean;
  saving: boolean;
  error: string | null;
  onPriceChange: (value: string) => void;
  onDirectionChange: (value: AlertDirection) => void;
  onRepeatChange: (value: boolean) => void;
  onCreate: () => void;
  onCancel: () => void;
};

export function AlertCreatePopover({
  x,
  y,
  containerRef,
  price,
  direction,
  repeat,
  saving,
  error,
  onPriceChange,
  onDirectionChange,
  onRepeatChange,
  onCreate,
  onCancel,
}: AlertCreatePopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const position = useClampedPosition(x, y, containerRef, popoverRef);

  return (
    <div
      ref={popoverRef}
      className="workspace-no-drag absolute z-40 w-[200px] space-y-2 rounded-md border border-border/80 bg-background/95 p-2 shadow-md backdrop-blur-sm"
      style={{ left: position.left, top: position.top }}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <label className="block space-y-0.5">
        <span className="text-[9.5px] tracking-[0.05em] text-muted-foreground uppercase">
          Price
        </span>
        <input
          type="number"
          step="any"
          value={price}
          onChange={(event) => onPriceChange(event.target.value)}
          className="w-full rounded border border-border/80 bg-transparent px-1.5 py-1 text-[11px] tabular-nums text-foreground outline-none focus:border-[var(--c-accent)]"
        />
      </label>

      <div className="space-y-0.5">
        <span className="text-[9.5px] tracking-[0.05em] text-muted-foreground uppercase">
          Direction
        </span>
        <div className="flex gap-1">
          {ALERT_DIRECTIONS.map((dir) => (
            <button
              key={dir}
              type="button"
              onClick={() => onDirectionChange(dir)}
              className={cn(
                "flex-1 rounded border px-1 py-0.5 text-[10px] tracking-wide uppercase transition-colors",
                direction === dir
                  ? "border-[var(--c-accent)] bg-[color-mix(in_srgb,var(--c-accent)_12%,transparent)] text-[var(--c-accent)]"
                  : "border-border/80 text-muted-foreground hover:bg-secondary/80",
              )}
            >
              {dir}
            </button>
          ))}
        </div>
      </div>

      <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <input
          type="checkbox"
          checked={repeat}
          onChange={(event) => onRepeatChange(event.target.checked)}
          className="size-3 rounded border-border/80"
        />
        Persistent
      </label>

      {error ? (
        <p className="text-[10px] text-destructive">{error}</p>
      ) : null}

      <div className="flex gap-1">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="h-7 flex-1 text-[11px]"
          disabled={saving}
          onClick={onCreate}
        >
          Create
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 flex-1 text-[11px]"
          disabled={saving}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
