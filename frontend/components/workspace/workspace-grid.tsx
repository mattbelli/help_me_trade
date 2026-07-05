"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type HTMLAttributes,
  type MouseEvent,
  type PointerEvent,
} from "react";
import ReactGridLayout, { type Layout } from "react-grid-layout/legacy";
import { motion } from "motion/react";
import { Check, ChevronDown, GripVertical, Menu as MenuIcon, SquarePen, X } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-time";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { useSymbolQuote } from "@/lib/use-symbol-quote";
import { cn } from "@/lib/utils";
import {
  colsForLayout,
  GRID_COL_TARGET_PX,
  GRID_MARGIN_PX,
  gridPixelWidth,
} from "@/lib/workspace/grid-metrics";
import type {
  WidgetInstanceConfig,
  WorkspaceLayoutItem,
} from "@/lib/workspace/layout-persistence";
import { flushWorkspaceLayoutSave, useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { useMotionSafe } from "@/lib/workspace/motion";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import { OSCILLATOR_STUDIES, OVERLAY_STUDIES } from "@/lib/chart-studies";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

import { ChannelSelector } from "./channel-selector";
import { SymbolSearchInput } from "./symbol-search-input";
import { WatchlistListSelector } from "./widgets/watchlist-list-selector";
import { WorkspaceWidgetPicker } from "./workspace-widget-picker";

const ROW_HEIGHT_PX = 48;

/** Stable ancestor whose width is unaffected by docked-copilot paddingRight. */
const GRID_HOST_ID = "workspace-grid-host";
const GRID_SCROLL_ID = "workspace-grid-scroll";

function isEditablePanTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return true;
  return target.isContentEditable;
}

function gridMeasureContainer(): HTMLElement | null {
  return (
    document.getElementById(GRID_HOST_ID) ??
    document.getElementById(GRID_SCROLL_ID)
  );
}

const CHART_STYLES = [
  { label: "Candles", value: "candle" as const },
  { label: "Line", value: "line" as const },
];

function resolveChartTimeframe(value?: string): ChartTimeframe {
  return (
    CHART_TIMEFRAMES.find((tf) => tf.value === value) ?? CHART_TIMEFRAMES[5]
  );
}

const dragSafeControlProps = {
  onClick: (e: MouseEvent) => e.stopPropagation(),
  onPointerDown: (e: PointerEvent) => e.stopPropagation(),
};

function ChartHeaderControls({
  config,
  channel,
  onConfigChange,
}: {
  config: WidgetInstanceConfig;
  channel: ChannelId;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
}) {
  const channelSymbol = useChannelSymbol(channel);
  const manualMode = channel === "none";
  const symbol =
    channel !== "none"
      ? (channelSymbol ?? config.symbol ?? "SPY")
      : (config.symbol ?? "SPY");
  const { price, live, changePct, extPrice, extChangePct, marketSession } =
    useSymbolQuote({ symbol });
  // Same rule as the watchlist row: off-RTH, show the last extended print so
  // the header, watchlist, and (extended) candles all agree on one number.
  const showExt =
    extPrice != null && marketSession != null && marketSession !== "regular";
  const displayPrice = showExt ? extPrice : price;
  const displayChangePct = showExt ? (extChangePct ?? changePct) : changePct;
  const hasPrice = displayPrice != null;
  const hasChange = displayChangePct != null;
  const isPositive = (displayChangePct ?? 0) >= 0;
  const priceFlash = usePriceFlash(displayPrice);

  // Local draft so the symbol box is freely editable (you can clear it and retype) —
  // we only COMMIT (and refetch the chart) on Enter/blur, not on every keystroke.
  const committedSymbol = config.symbol ?? "SPY";
  const [draftSymbol, setDraftSymbol] = useState(committedSymbol);
  useEffect(() => {
    setDraftSymbol(committedSymbol);
  }, [committedSymbol]);

  const commitSymbol = (override?: string) => {
    const next = (override ?? draftSymbol).toUpperCase().trim();
    if (next && next !== committedSymbol) {
      onConfigChange({ symbol: next });
    } else {
      setDraftSymbol(committedSymbol); // revert empty/unchanged back to current
    }
  };

  return (
    <>
      {manualMode ? (
        <SymbolSearchInput
          value={draftSymbol}
          onChange={setDraftSymbol}
          onCommit={commitSymbol}
          onEscape={() => setDraftSymbol(committedSymbol)}
          dragSafeControlProps={dragSafeControlProps}
          inputClassName="workspace-no-drag w-16 rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1.5 py-0.5 font-mono text-[length:var(--fs-body)] font-semibold text-foreground uppercase outline-none focus:border-[var(--c-accent)]"
        />
      ) : (
        <span className="font-mono text-[length:var(--fs-body)] font-semibold text-[var(--c-accent)] [text-box:trim-both_cap_alphabetic]">
          {symbol}
        </span>
      )}
      <span
        {...dragSafeControlProps}
        className="workspace-no-drag inline-flex items-center gap-1"
      >
        <span
          className={cn(
            "rounded px-0.5 font-mono text-[length:var(--fs-emph)] font-semibold tabular-nums [text-box:trim-both_cap_alphabetic]",
            hasChange
              ? isPositive
                ? "text-[var(--c-green)]"
                : "text-[var(--c-red)]"
              : hasPrice
                ? "text-foreground"
                : "text-[var(--fg-dim)]",
            priceFlash === "up" && "tick-flash-up",
            priceFlash === "down" && "tick-flash-down",
          )}
        >
          {hasPrice ? displayPrice.toFixed(2) : "—"}
        </span>
        {hasChange ? (
          <span
            className={cn(
              "font-mono text-[length:var(--fs-table)] tabular-nums [text-box:trim-both_cap_alphabetic]",
              isPositive ? "text-[var(--c-green)]" : "text-[var(--c-red)]",
            )}
          >
            {`${displayChangePct > 0 ? "+" : ""}${displayChangePct.toFixed(2)}%`}
          </span>
        ) : null}
        <span
          className="flex size-1.5 shrink-0 items-center justify-center"
          title={live ? "Live quote" : "Stale quote"}
          aria-hidden
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              live
                ? "bg-[var(--c-green)]"
                : "border border-[var(--fg-dim)] bg-transparent",
            )}
          />
        </span>
      </span>
    </>
  );
}

/**
 * Right-aligned chart controls: the timeframe dropdown stays one click (heavy
 * use); style + studies live behind a ☰ menu so new chart options land there
 * instead of widening the header toolbar.
 */
function ChartHeaderMenus({
  config,
  onConfigChange,
}: {
  config: WidgetInstanceConfig;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
}) {
  const timeframe = resolveChartTimeframe(config.timeframe);
  const style = config.style ?? "candle";

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          {...dragSafeControlProps}
          className="workspace-no-drag inline-flex items-center gap-0.5 rounded px-1 py-0.5 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          <span className="[text-box:trim-both_cap_alphabetic]">
            {timeframe.label}
          </span>
          <ChevronDown className="size-2.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="min-w-24 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
        >
          {CHART_TIMEFRAMES.map((tf) => (
            <DropdownMenuItem
              key={tf.value}
              onClick={() => onConfigChange({ timeframe: tf.value })}
              className={cn(
                "px-2 py-1.5 text-[length:var(--fs-body)]",
                timeframe.value === tf.value
                  ? "text-foreground"
                  : "text-[var(--fg-dim)]",
              )}
            >
              {tf.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          {...dragSafeControlProps}
          aria-label="Chart options"
          className="workspace-no-drag inline-flex items-center rounded p-1 text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          <MenuIcon className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="min-w-32 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
        >
          {CHART_STYLES.map((s) => (
            <DropdownMenuItem
              key={s.value}
              onSelect={(e) => e.preventDefault()}
              onClick={() => onConfigChange({ style: s.value })}
              className={cn(
                "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
                style === s.value ? "text-foreground" : "text-[var(--fg-dim)]",
              )}
            >
              {s.label}
              {style === s.value ? <Check className="size-3" /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          {OVERLAY_STUDIES.map((s) => {
            const enabled = (config.studies ?? []).includes(s.id);
            return (
              <DropdownMenuItem
                key={s.id}
                onSelect={(e) => e.preventDefault()}
                onClick={() => {
                  const cur = config.studies ?? [];
                  const next = enabled
                    ? cur.filter((x) => x !== s.id)
                    : [...cur, s.id];
                  onConfigChange({ studies: next });
                }}
                className={cn(
                  "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
                  enabled ? "text-foreground" : "text-[var(--fg-dim)]",
                )}
              >
                {s.label}
                {enabled ? <Check className="size-3" /> : null}
              </DropdownMenuItem>
            );
          })}
          <DropdownMenuSeparator />
          {OSCILLATOR_STUDIES.map((s) => {
            const enabled = (config.studies ?? []).includes(s.id);
            return (
              <DropdownMenuItem
                key={s.id}
                onSelect={(e) => e.preventDefault()}
                onClick={() => {
                  const cur = config.studies ?? [];
                  const next = enabled
                    ? cur.filter((x) => x !== s.id)
                    : [...cur, s.id];
                  onConfigChange({ studies: next });
                }}
                className={cn(
                  "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
                  enabled ? "text-foreground" : "text-[var(--fg-dim)]",
                )}
              >
                {s.label}
                {enabled ? <Check className="size-3" /> : null}
              </DropdownMenuItem>
            );
          })}
          <DropdownMenuItem
            onSelect={(e) => e.preventDefault()}
            onClick={() =>
              onConfigChange({ showSessionNodes: !config.showSessionNodes })
            }
            className={cn(
              "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
              config.showSessionNodes
                ? "text-foreground"
                : "text-[var(--fg-dim)]",
            )}
          >
            Session Nodes
            {config.showSessionNodes ? <Check className="size-3" /> : null}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

function useWidgetHeaderMeta(widgetType: string): string | undefined {
  const { snapshot } = useSnapshot();
  const def = getWidgetDefinition(widgetType);

  if (def?.meta) return def.meta;

  if (widgetType === "open-orders" && snapshot) {
    const count = snapshot.open_order_count;
    return count === 1 ? "1 open" : `${count} open`;
  }

  return undefined;
}

function WidgetRemoveButton({
  title,
  onRemove,
}: {
  title: string;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        type="button"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        className="workspace-no-drag rounded p-0.5 font-mono text-[length:var(--fs-title)] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        aria-label={`Remove ${title}`}
      >
        <X className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-44 border-[var(--border)] bg-[var(--panel)] p-2 font-mono shadow-[var(--panel-shadow)]"
      >
        <p className="px-1 py-1 text-[length:var(--fs-body)] text-[var(--fg-dim)]">
          Remove this widget?
        </p>
        <div className="mt-1.5 flex justify-end gap-1.5">
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded border border-[var(--border)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onRemove();
            }}
            className="rounded border border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--c-red)] transition-colors hover:bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]"
          >
            Remove
          </button>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function WidgetHeader({
  widgetType,
  channelAware,
  channel,
  onChannelChange,
  onRemove,
  watchlistId,
  onWatchlistChange,
  config,
  onConfigChange,
  locked,
  static: isStatic,
}: {
  widgetType: string;
  channelAware?: boolean;
  channel: ChannelId;
  onChannelChange: (channel: ChannelId) => void;
  onRemove: () => void;
  watchlistId?: string;
  onWatchlistChange?: (watchlistId: string) => void;
  config?: WidgetInstanceConfig;
  onConfigChange?: (patch: Partial<WidgetInstanceConfig>) => void;
  locked: boolean;
  static?: boolean;
}) {
  const def = getWidgetDefinition(widgetType);
  const meta = useWidgetHeaderMeta(widgetType);
  const isChart = widgetType === "chart";

  return (
    <div
      className={cn(
        "workspace-drag-handle relative flex h-10 items-center gap-2 border-b border-[var(--border)] px-3",
        !locked && !isStatic && "cursor-grab active:cursor-grabbing",
      )}
    >
      <GripVertical
        className="size-3.5 shrink-0 text-[var(--fg-faint)]"
        aria-hidden
      />
      {isChart && config && onConfigChange ? (
        <ChartHeaderControls
          config={config}
          channel={channel}
          onConfigChange={onConfigChange}
        />
      ) : (
        <span
          className="min-w-0 truncate pb-[0.3em] -mb-[0.3em] text-[length:var(--fs-widget-title)] font-semibold text-foreground [text-box:trim-both_cap_alphabetic]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {def?.title ?? widgetType}
        </span>
      )}
      {meta ? (
        <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)] [text-box:trim-both_cap_alphabetic]">
          {meta}
        </span>
      ) : null}
      <div className="flex-1" />
      {isChart && config && onConfigChange ? (
        <ChartHeaderMenus config={config} onConfigChange={onConfigChange} />
      ) : null}
      {widgetType === "watchlist" && onWatchlistChange ? (
        <>
          {config && onConfigChange ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onConfigChange({ editing: !config.editing, adding: false });
              }}
              onPointerDown={(e) => e.stopPropagation()}
              aria-label={config.editing ? "Done editing" : "Edit list"}
              aria-pressed={config.editing ?? false}
              className={cn(
                "workspace-no-drag inline-flex items-center justify-center rounded p-1 outline-none hover:bg-[var(--bg-hover)]",
                config.editing
                  ? "text-[var(--c-accent)]"
                  : "text-[var(--fg-dim)] hover:text-foreground",
              )}
            >
              <SquarePen className="size-3.5" />
            </button>
          ) : null}
          <WatchlistListSelector
            watchlistId={watchlistId}
            onSelect={onWatchlistChange}
          />
        </>
      ) : null}
      {channelAware ? (
        <ChannelSelector channel={channel} onChange={onChannelChange} />
      ) : null}
      {!locked || isStatic ? (
        <WidgetRemoveButton
          title={def?.title ?? widgetType}
          onRemove={onRemove}
        />
      ) : null}
    </div>
  );
}

// NOTE: react-grid-layout positions each child by cloning it and injecting
// `ref` + `className` (react-grid-item + transforms) + `style`, and the
// Resizable/DraggableCore wrappers append resize-handle `children` + drag
// handlers. As a direct RGL child this component MUST forward all of those to
// its root <div>, or the desktop grid loses positioning/resize/drag. On the
// mobile path it's rendered directly (no injected props), which is fine.
const WidgetCard = forwardRef<
  HTMLDivElement,
  {
    item: WorkspaceLayoutItem;
    locked: boolean;
    static?: boolean;
    flashing?: boolean;
    onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
    onRemove: () => void;
  } & HTMLAttributes<HTMLDivElement>
>(function WidgetCard(
  {
    item,
    locked,
    static: isStatic,
    flashing,
    onConfigChange,
    onRemove,
    className,
    style,
    children,
    ...rest
  },
  ref,
) {
  const def = getWidgetDefinition(item.type);
  const Widget = def?.component;
  const config: WidgetInstanceConfig = item.config ?? {};
  const channel: ChannelId = config.channel ?? "none";

  return (
    <div
      ref={ref}
      id={`widget-${item.i}`}
      className={cn(
        "group grain-surface flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)] hover:shadow-[var(--widget-hover-shadow)]",
        isStatic && "h-full",
        flashing &&
          "ring-2 ring-[var(--c-accent)] ring-offset-1 ring-offset-[var(--panel)]",
        className,
      )}
      style={style}
      {...rest}
    >
      <WidgetHeader
        widgetType={item.type}
        channelAware={def?.channelAware}
        channel={channel}
        onChannelChange={(next) => onConfigChange({ channel: next })}
        onRemove={onRemove}
        watchlistId={config.watchlistId}
        onWatchlistChange={(next) => onConfigChange({ watchlistId: next })}
        config={config}
        onConfigChange={onConfigChange}
        locked={locked}
        static={isStatic}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {Widget ? (
          <Widget
            instanceId={item.i}
            config={config}
            onConfigChange={onConfigChange}
          />
        ) : (
          <p className="p-3 font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)]">
            Unknown widget: {item.type}
          </p>
        )}
      </div>
      {children}
    </div>
  );
});

// Distance (px) over which content fades at a scrollable edge.
const SCROLL_FADE_PX = 40;

type EdgeState = {
  top: boolean;
  bottom: boolean;
  left: boolean;
  right: boolean;
};

export function WorkspaceGrid() {
  const activePageId = useWorkspaceStore((s) => s.activePageId);
  const layout = useWorkspaceStore(
    (s) => s.pages[s.activePageId]?.layout ?? [],
  );
  const focusRequest = useWorkspaceStore((s) => s.focusRequest);
  const {
    hydrated,
    hydrate,
    locked,
    updateLayout,
    updateWidgetConfig,
    removeWidget,
    pickerOpen,
    setPickerOpen,
    setGridCols,
    openPicker,
  } = useWorkspaceStore();

  // Host width (minus scroll padding), null until first measured. cols/width
  // are DERIVED from (layout, hostWidth) synchronously in render — they must
  // never lag the layout in state: RGL remounts on page switch, and mounting
  // with the PREVIOUS page's col count lets its correctBounds clamp wide
  // layouts leftward into overlap (the "charts slide left" bug), a clamp that
  // survives the late cols correction and persists on the next drag-stop.
  const [hostWidth, setHostWidth] = useState<number | null>(null);
  const [flashingId, setFlashingId] = useState<string | null>(null);
  // Fade-in only on page hot-swap (Alt+1-5) — no exit, which would add
  // perceived latency to a keyboard-driven switch. Collapses to instant under
  // reduced motion.
  const pageFade = useMotionSafe({ duration: 0.12 });

  useEffect(() => {
    if (!focusRequest) return;
    const { instanceId } = focusRequest;
    const raf = requestAnimationFrame(() => {
      document
        .getElementById(`widget-${instanceId}`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
      setFlashingId(instanceId);
    });
    const t = setTimeout(() => setFlashingId(null), 1200);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
    };
  }, [focusRequest]);

  // Four-edge scroll indicators: show subtle gradient overlays when there is
  // more content in that direction (right/bottom are the important ones for
  // the growing canvas; top/left show when scrolled away from origin).
  const [edges, setEdges] = useState<EdgeState>({
    top: false,
    bottom: false,
    left: false,
    right: false,
  });
  const scrollRef = useRef<HTMLDivElement>(null);
  const panningRef = useRef(false);
  const panPointerIdRef = useRef<number | null>(null);
  const panStartRef = useRef({ x: 0, y: 0, scrollLeft: 0, scrollTop: 0 });

  const measureEdges = useCallback(() => {
    const el = document.getElementById(GRID_SCROLL_ID);
    if (!el) return;
    const top = el.scrollTop > 2;
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setEdges((prev) => {
      if (
        prev.top === top &&
        prev.bottom === bottom &&
        prev.left === left &&
        prev.right === right
      )
        return prev;
      return { top, bottom, left, right };
    });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const endPan = (pointerId: number) => {
      if (!panningRef.current) return;
      panningRef.current = false;
      panPointerIdRef.current = null;
      el.style.cursor = "";
      try {
        el.releasePointerCapture(pointerId);
      } catch {
        /* not captured */
      }
    };

    const onPointerDown = (e: globalThis.PointerEvent) => {
      if (e.button !== 1) return;
      if (isEditablePanTarget(e.target)) return;
      e.preventDefault();
      panningRef.current = true;
      panPointerIdRef.current = e.pointerId;
      panStartRef.current = {
        x: e.clientX,
        y: e.clientY,
        scrollLeft: el.scrollLeft,
        scrollTop: el.scrollTop,
      };
      el.setPointerCapture(e.pointerId);
      el.style.cursor = "grabbing";
    };

    const onPointerMove = (e: globalThis.PointerEvent) => {
      if (!panningRef.current || e.pointerId !== panPointerIdRef.current) return;
      const start = panStartRef.current;
      el.scrollLeft = start.scrollLeft - (e.clientX - start.x);
      el.scrollTop = start.scrollTop - (e.clientY - start.y);
    };

    const onPointerUp = (e: globalThis.PointerEvent) => {
      if (e.pointerId !== panPointerIdRef.current) return;
      endPan(e.pointerId);
    };

    const onMouseDown = (e: globalThis.MouseEvent) => {
      if (e.button !== 1) return;
      if (isEditablePanTarget(e.target)) return;
      e.preventDefault();
    };

    el.addEventListener("pointerdown", onPointerDown, { capture: true });
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", onPointerUp);
    el.addEventListener("pointercancel", onPointerUp);
    el.addEventListener("mousedown", onMouseDown);

    return () => {
      el.removeEventListener("pointerdown", onPointerDown, { capture: true });
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", onPointerUp);
      el.removeEventListener("pointercancel", onPointerUp);
      el.removeEventListener("mousedown", onMouseDown);
      if (panningRef.current && panPointerIdRef.current != null) {
        endPan(panPointerIdRef.current);
      }
    };
  }, []);

  useEffect(() => {
    void hydrate();
    return () => flushWorkspaceLayoutSave();
  }, [hydrate]);

  useEffect(() => {
    if (!hydrated) return;

    // Measure the host (outside copilot paddingRight) so pin/unpin never
    // shrinks cols and triggers a reflow that could be persisted.
    const el = gridMeasureContainer();
    if (!el) return;

    const measure = () => setHostWidth(el.clientWidth - 32); // 2×16px scroll padding
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [hydrated]);

  // Derived in render (see hostWidth note): the same render that hands RGL a
  // page's layout always hands it that layout's col count.
  const cols = useMemo(
    () => (hostWidth == null ? null : colsForLayout(layout, hostWidth)),
    [layout, hostWidth],
  );
  const width = cols == null ? null : gridPixelWidth(cols);

  useEffect(() => {
    if (cols != null) setGridCols(cols);
  }, [cols, setGridCols]);

  // Re-check the scroll-edge fades whenever canvas geometry changes; rAF so
  // RGL has applied its DOM changes before we read scroll sizes.
  useEffect(() => {
    const id = requestAnimationFrame(measureEdges);
    return () => cancelAnimationFrame(id);
  }, [layout, cols, measureEdges]);

  // Pass each item's real x,y,w,h straight through — NO clamping to window
  // cols. The growing canvas means a widget at x=20,w=8 on a 28-col canvas
  // always sits exactly there, regardless of viewport width. This is what
  // eliminates overlap when the same layout is opened on different screens.
  const gridLayout: Layout = useMemo(
    () =>
      layout.map(({ i, x, y, w, h, minW, minH }) => ({
        i,
        // Defensive: ensure non-negative position and at least 1 wide/tall.
        x: Math.max(0, x),
        y: Math.max(0, y),
        w: Math.max(1, w),
        h: Math.max(1, h),
        minW: minW ?? 4,
        minH: minH ?? 2,
      })),
    [layout],
  );

  const persistLayout = useCallback(
    (nextLayout: Layout) => {
      const merged = layout.map((item) => {
        const cell = nextLayout.find((l) => l.i === item.i);
        if (!cell) return item;
        return { ...item, x: cell.x, y: cell.y, w: cell.w, h: cell.h };
      });
      updateLayout(merged);
    },
    [layout, updateLayout],
  );

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background">
      {pickerOpen ? (
        <WorkspaceWidgetPicker onClose={() => setPickerOpen(false)} />
      ) : null}

      {/* Relative wrapper for the four-edge gradient overlays. Grain lives HERE
          (non-scrolling) so it covers the viewport at every scroll position —
          on the scroller it only spanned the first viewport of content. */}
      <div className="grain-surface relative min-h-0 flex-1 overflow-hidden">
        <div
          ref={scrollRef}
          id={GRID_SCROLL_ID}
          onScroll={measureEdges}
          className="workspace-scroll h-full overflow-auto px-4 pt-4 pb-6"
          style={
            locked
              ? {}
              : {
                  backgroundImage:
                    "radial-gradient(circle, color-mix(in srgb, var(--fg-faint) 30%, transparent) 1px, transparent 1px)",
                  backgroundSize: `${GRID_COL_TARGET_PX + GRID_MARGIN_PX}px ${ROW_HEIGHT_PX + GRID_MARGIN_PX}px`,
                  backgroundPosition: "16px 16px",
                  backgroundAttachment: "local",
                }
          }
        >
          {hydrated && layout.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 font-mono">
              <p className="text-[length:var(--fs-body)] text-[var(--fg-faint)]">
                empty page
              </p>
              <button
                type="button"
                onClick={openPicker}
                className="rounded border border-[var(--border)] px-2.5 py-1 font-mono text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
              >
                add widget
              </button>
              <p className="text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)]">
                or type /add in the console
              </p>
            </div>
          ) : null}
          {hydrated && layout.length > 0 && cols != null && width != null ? (
            <motion.div
              key={activePageId}
              id="workspace-grid-root"
              className="relative"
              style={{ width }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={pageFade}
            >
              <ReactGridLayout
                key={activePageId}
                className="layout"
                layout={gridLayout}
                cols={cols}
                rowHeight={ROW_HEIGHT_PX}
                width={width}
                margin={[GRID_MARGIN_PX, GRID_MARGIN_PX]}
                containerPadding={[0, 0]}
                compactType={null}
                preventCollision
                onDragStop={persistLayout}
                onResizeStop={persistLayout}
                draggableHandle=".workspace-drag-handle"
                draggableCancel=".workspace-no-drag"
                resizeHandles={["se", "sw", "ne", "nw"]}
                isDraggable={!locked}
                isResizable={!locked}
                useCSSTransforms={false}
              >
                {layout.map((item) => (
                  <WidgetCard
                    key={item.i}
                    item={item}
                    locked={locked}
                    flashing={item.i === flashingId}
                    onConfigChange={(patch) =>
                      updateWidgetConfig(item.i, patch)
                    }
                    onRemove={() => removeWidget(item.i)}
                  />
                ))}
              </ReactGridLayout>
            </motion.div>
          ) : null}
        </div>

        {/* Four-edge gradient hints — pointer-events-none so they don't block
            interaction with widgets. Shown only when there is content that way. */}
        {edges.top ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-0 z-10"
            style={{
              height: SCROLL_FADE_PX,
              background: `linear-gradient(to bottom, var(--background), transparent)`,
            }}
          />
        ) : null}
        {edges.bottom ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 z-10"
            style={{
              height: SCROLL_FADE_PX,
              background: `linear-gradient(to top, var(--background), transparent)`,
            }}
          />
        ) : null}
        {edges.left ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 left-0 z-10"
            style={{
              width: SCROLL_FADE_PX,
              background: `linear-gradient(to right, var(--background), transparent)`,
            }}
          />
        ) : null}
        {edges.right ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 z-10"
            style={{
              width: SCROLL_FADE_PX,
              background: `linear-gradient(to left, var(--background), transparent)`,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
