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
import { Bell, BellOff, Check, ChevronDown, Eye, GripVertical, Menu as MenuIcon, Plus, SlidersHorizontal, X } from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import { fetchWatchlists } from "@/lib/api";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
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
  FlowTypeKey,
  WidgetInstanceConfig,
  WorkspaceLayoutItem,
  WorkspacePage,
} from "@/lib/workspace/layout-persistence";
import { DEFAULT_FLOW_TYPES } from "@/lib/workspace/layout-persistence";
import { flushWorkspaceLayoutSave, useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { PageActiveContext } from "@/lib/workspace/page-active-context";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import { OSCILLATOR_STUDIES, OVERLAY_STUDIES } from "@/lib/chart-studies";
import { getWidgetDefinition } from "@/lib/workspace/widget-registry";

import {
  unreadCount,
  useNotificationStore,
} from "@/lib/workspace/use-notification-store";

import { ChannelSelector } from "./channel-selector";
import { SymbolSearchInput } from "./symbol-search-input";
import { WatchlistListSelector } from "./widgets/watchlist-list-selector";
import {
  NOTIF_FILTERS,
  resolveNotifFilter,
} from "./widgets/notifications-widget";
import { WorkspaceWidgetPicker } from "./workspace-widget-picker";

const ROW_HEIGHT_PX = 48;
// Scroll container's top padding (pt-4) — offsets scrollTop → grid-row space.
const GRID_CONTENT_PADDING_TOP_PX = 16;

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
                ? "bg-[var(--c-green-bg)]"
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
          aria-label="Visibility"
          className="workspace-no-drag inline-flex items-center rounded p-1 text-[var(--fg-dim)] outline-none hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          <Eye className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="min-w-32 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
        >
          {(
            [
              { key: "showAlerts" as const, label: "Alerts" },
              { key: "showDrawings" as const, label: "Drawings" },
              // Native engine: hides the tag column AND every overlay line
              // with it — the one-click clean chart.
              { key: "showTagColumn" as const, label: "Tag column" },
            ]
          ).map(({ key, label }) => {
            const shown = config[key] ?? true;
            return (
              <DropdownMenuItem
                key={key}
                onSelect={(e) => e.preventDefault()}
                onClick={() => onConfigChange({ [key]: !shown })}
                className={cn(
                  "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
                  shown ? "text-foreground" : "text-[var(--fg-dim)]",
                )}
              >
                {label}
                {shown ? <Check className="size-3" /> : null}
              </DropdownMenuItem>
            );
          })}
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
          {timeframe.intraday ? (
            <DropdownMenuItem
              onSelect={(e) => e.preventDefault()}
              onClick={() =>
                onConfigChange({
                  extendedHours: !(config.extendedHours ?? true),
                })
              }
              className={cn(
                "flex items-center justify-between gap-3 px-2 py-1.5 text-[length:var(--fs-body)]",
                (config.extendedHours ?? true)
                  ? "text-foreground"
                  : "text-[var(--fg-dim)]",
              )}
            >
              Extended Hours
              {(config.extendedHours ?? true) ? (
                <Check className="size-3" />
              ) : null}
            </DropdownMenuItem>
          ) : null}
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
    if (count === 0) return undefined;
    return count === 1 ? "1 open" : `${count} open`;
  }

  return undefined;
}

// Watchlist widgets title themselves with the LIST's name ("Main"), not the
// generic widget label. Mirrors the list selector's resolution (explicit id,
// else the server-side active list); falls back to the registry title until
// the fetch lands or when the id is unknown.
function useWatchlistTitle(
  widgetType: string,
  watchlistId: string | undefined,
): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (widgetType !== "watchlist") return;
    let cancelled = false;
    fetchWatchlists()
      .then((s) => {
        if (cancelled) return;
        const id = watchlistId ?? s.active_id;
        setName(s.lists.find((l) => l.id === id)?.name ?? null);
      })
      .catch(() => {
        if (!cancelled) setName(null);
      });
    return () => {
      cancelled = true;
    };
  }, [widgetType, watchlistId]);
  return widgetType === "watchlist" ? name : null;
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

// Watchlist header: a "+" toggles the inline add-symbol row. (Removing a symbol
// is now a drag-outside-the-widget gesture, handled inside the widget itself.)
function WatchlistHeaderControls({
  adding,
  onToggleAdding,
  settingsOpen,
  onToggleSettings,
}: {
  adding: boolean;
  onToggleAdding: () => void;
  settingsOpen: boolean;
  onToggleSettings: () => void;
}) {
  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onToggleSettings();
        }}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Columns & display"
        title="Columns & display"
        aria-pressed={settingsOpen}
        className={cn(
          "workspace-no-drag inline-flex items-center justify-center rounded p-1 outline-none hover:bg-[var(--bg-hover)]",
          settingsOpen
            ? "text-[var(--c-accent)]"
            : "text-[var(--fg-dim)] hover:text-foreground",
        )}
      >
        <SlidersHorizontal className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onToggleAdding();
        }}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label={adding ? "Close add symbol form" : "Add symbol"}
        aria-pressed={adding}
        className={cn(
          "workspace-no-drag inline-flex items-center justify-center rounded p-1 outline-none hover:bg-[var(--bg-hover)]",
          adding
            ? "text-[var(--c-accent)]"
            : "text-[var(--fg-dim)] hover:text-foreground",
        )}
      >
        <Plus className="size-3.5" />
      </button>
    </>
  );
}

const FLOW_TYPE_OPTIONS: { key: FlowTypeKey; label: string }[] = [
  { key: "sweep", label: "sweeps" },
  { key: "block", label: "blocks" },
  { key: "flow_burst", label: "bursts (30m net)" },
  { key: "flow_cluster", label: "clusters (small prints)" },
  { key: "directional_pressure", label: "pressure (day net)" },
  { key: "unusual_volume", label: "unusual volume (EOD)" },
];

/** ⚙ event-type filter — sits to the left of the channel dot. Drives the fetch
    so unchecking a kind removes it from the tape (not just hidden client-side). */
function OptionsFlowHeaderControls({
  config,
  onConfigChange,
}: {
  config: WidgetInstanceConfig;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
}) {
  const active = config.flowTypes ?? DEFAULT_FLOW_TYPES;
  const isDefault = config.flowTypes === undefined;

  const toggle = (key: FlowTypeKey) => {
    const has = active.includes(key);
    const next = has ? active.filter((k) => k !== key) : [...active, key];
    // preserve the canonical order regardless of click order
    onConfigChange({
      flowTypes: FLOW_TYPE_OPTIONS.map((o) => o.key).filter((k) =>
        next.includes(k),
      ),
    });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        className={cn(
          "workspace-no-drag inline-flex shrink-0 items-center justify-center rounded p-1 outline-none transition-colors hover:bg-[var(--bg-hover)]",
          isDefault
            ? "text-[var(--fg-dim)] hover:text-foreground"
            : "text-[var(--c-accent)]",
        )}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        aria-label="Filter flow types"
        title="Filter flow types"
      >
        <SlidersHorizontal className="size-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="min-w-44 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
      >
        <div className="px-2 py-1 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
          flow types
        </div>
        {FLOW_TYPE_OPTIONS.map((o) => (
          <DropdownMenuCheckboxItem
            key={o.key}
            checked={active.includes(o.key)}
            onCheckedChange={() => toggle(o.key)}
            closeOnClick={false}
            className="px-2 py-1.5 text-[var(--fg-dim)]"
          >
            {o.label}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Notifications header: unread count + one ⚙ dropdown holding the category
    filter, the new-card sound toggle, and mark-all-read — replacing the old
    in-widget filter/button row (widgets don't get a second header bar). */
function NotificationsHeaderControls({
  config,
  onConfigChange,
}: {
  config: WidgetInstanceConfig;
  onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
}) {
  const notifications = useNotificationStore((s) => s.notifications);
  const markAllRead = useNotificationStore((s) => s.markAllRead);
  const unread = unreadCount(notifications);
  const filter = resolveNotifFilter(config.notifFilter);
  const dingOn = config.ding !== false;

  return (
    <>
      {unread > 0 ? (
        <span className="font-mono text-[length:var(--fs-table)] tabular-nums text-[var(--fg-faint)]">
          {unread} unread
        </span>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          className={cn(
            "workspace-no-drag inline-flex shrink-0 items-center justify-center rounded p-1 outline-none transition-colors hover:bg-[var(--bg-hover)]",
            filter === "all"
              ? "text-[var(--fg-dim)] hover:text-foreground"
              : "text-[var(--c-accent)]",
          )}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label="Notification filter & options"
          title="Filter & options"
        >
          <SlidersHorizontal className="size-3.5" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="min-w-40 border-[var(--border)] bg-[var(--panel)] p-1 font-mono text-[length:var(--fs-body)] shadow-[var(--panel-shadow)]"
        >
          <div className="px-2 py-1 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
            show
          </div>
          {NOTIF_FILTERS.map((mode) => (
            <DropdownMenuItem
              key={mode}
              onClick={() => onConfigChange({ notifFilter: mode })}
              className={cn(
                "flex items-center justify-between gap-3 px-2 py-1 lowercase",
                filter === mode ? "text-foreground" : "text-[var(--fg-dim)]",
              )}
            >
              {mode}
              {filter === mode ? <Check className="size-3" /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={(e) => e.preventDefault()}
            onClick={() => onConfigChange({ ding: !dingOn })}
            className="flex items-center justify-between gap-3 px-2 py-1 text-[var(--fg-dim)]"
          >
            sound on new
            {dingOn ? (
              <Bell className="size-3" />
            ) : (
              <BellOff className="size-3 text-[var(--fg-faint)]" />
            )}
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={unread === 0}
            onClick={() => markAllRead()}
            className={cn(
              "px-2 py-1",
              unread === 0 ? "text-[var(--fg-faint)]" : "text-[var(--c-accent)]",
            )}
          >
            mark all read{unread > 0 ? ` (${unread})` : ""}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
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
  const watchlistTitle = useWatchlistTitle(widgetType, watchlistId);
  const isChart = widgetType === "chart";

  return (
    <div
      className={cn(
        "workspace-drag-handle relative flex h-10 items-center gap-2 border-b border-[var(--line-divider)] bg-[var(--surface-header)] px-3",
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
          className="min-w-0 truncate pb-[0.3em] -mb-[0.3em] text-[length:var(--fs-widget-title)] font-semibold text-[var(--fg-widget-title)] [text-box:trim-both_cap_alphabetic]"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {watchlistTitle ?? def?.title ?? widgetType}
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
            <WatchlistHeaderControls
              adding={config.adding ?? false}
              onToggleAdding={() =>
                onConfigChange({ adding: !config.adding })
              }
              settingsOpen={config.settingsOpen ?? false}
              onToggleSettings={() =>
                onConfigChange({ settingsOpen: !config.settingsOpen })
              }
            />
          ) : null}
          <WatchlistListSelector
            watchlistId={watchlistId}
            onSelect={onWatchlistChange}
          />
        </>
      ) : null}
      {widgetType === "price-alerts" && config && onConfigChange ? (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onConfigChange({ adding: !config.adding });
          }}
          onPointerDown={(e) => e.stopPropagation()}
          aria-label={config.adding ? "Close new alert form" : "Add alert"}
          aria-pressed={config.adding ?? false}
          className={cn(
            "workspace-no-drag inline-flex items-center justify-center rounded p-1 outline-none hover:bg-[var(--bg-hover)]",
            config.adding
              ? "text-[var(--c-accent)]"
              : "text-[var(--fg-dim)] hover:text-foreground",
          )}
        >
          <Plus className="size-3.5" />
        </button>
      ) : null}
      {widgetType === "options-flow" && config && onConfigChange ? (
        <OptionsFlowHeaderControls
          config={config}
          onConfigChange={onConfigChange}
        />
      ) : null}
      {widgetType === "notifications" && config && onConfigChange ? (
        <NotificationsHeaderControls
          config={config}
          onConfigChange={onConfigChange}
        />
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
    domId?: string;
    onConfigChange: (patch: Partial<WidgetInstanceConfig>) => void;
    onRemove: () => void;
  } & HTMLAttributes<HTMLDivElement>
>(function WidgetCard(
  {
    item,
    locked,
    static: isStatic,
    flashing,
    domId,
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
      id={domId ?? `widget-${item.i}`}
      className={cn(
        "group grain-surface grain-above flex flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--panel)] shadow-[var(--panel-shadow)] transition-[border-color,box-shadow] hover:border-[var(--border-strong)] hover:shadow-[var(--widget-hover-shadow)]",
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

type WorkspacePageGridProps = {
  page: WorkspacePage;
  hostWidth: number;
  active: boolean;
  locked: boolean;
  flashingId: string | null;
};

// One persistent RGL grid per page. EVERY page stays mounted; inactive ones are
// hidden with visibility+absolute — NOT display:none, which would report 0×0 to
// each chart's ResizeObserver and force a full teardown/rebuild. Because the
// grids never unmount, a page switch does ZERO widget reconstruction: charts and
// watchlists are already built, laid out, and live. Each page derives cols from
// ITS OWN layout, so a grid is never mounted with a foreign page's col count
// (the old "charts slide left / overlap" bug that key={activePageId} guarded).
function WorkspacePageGrid({
  page,
  hostWidth,
  active,
  locked,
  flashingId,
}: WorkspacePageGridProps) {
  const updateLayout = useWorkspaceStore((s) => s.updateLayout);
  const updateWidgetConfig = useWorkspaceStore((s) => s.updateWidgetConfig);
  const removeWidget = useWorkspaceStore((s) => s.removeWidget);
  const setGridCols = useWorkspaceStore((s) => s.setGridCols);

  const layout = page.layout;
  const cols = useMemo(
    () => colsForLayout(layout, hostWidth),
    [layout, hostWidth],
  );
  const width = gridPixelWidth(cols);

  // Only the active page feeds gridCols (drives new-widget placement).
  useEffect(() => {
    if (active) setGridCols(cols);
  }, [active, cols, setGridCols]);

  // Pass each item's real x,y,w,h straight through — NO clamping to window cols;
  // the growing canvas means a widget always sits exactly where it was placed.
  const gridLayout: Layout = useMemo(
    () =>
      layout.map(({ i, x, y, w, h, minW, minH }) => ({
        i,
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
      updateLayout(merged, page.id);
    },
    [layout, updateLayout, page.id],
  );

  if (layout.length === 0) return null;

  return (
    <PageActiveContext.Provider value={active}>
    <div
      id={`workspace-grid-root-${page.id}`}
      className="relative"
      style={
        active
          ? { width, position: "relative" }
          : {
              width,
              position: "absolute",
              top: 0,
              left: 0,
              visibility: "hidden",
              pointerEvents: "none",
            }
      }
    >
      <ReactGridLayout
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
            domId={`widget-${page.id}-${item.i}`}
            locked={locked}
            flashing={active && item.i === flashingId}
            onConfigChange={(patch) => updateWidgetConfig(item.i, patch, page.id)}
            onRemove={() => removeWidget(item.i, page.id)}
          />
        ))}
      </ReactGridLayout>
    </div>
    </PageActiveContext.Provider>
  );
}

export function WorkspaceGrid() {
  const pages = useWorkspaceStore((s) => s.pages);
  const activePageId = useWorkspaceStore((s) => s.activePageId);
  const focusRequest = useWorkspaceStore((s) => s.focusRequest);
  const clearFocusRequest = useWorkspaceStore((s) => s.clearFocusRequest);
  const {
    hydrated,
    hydrate,
    locked,
    pickerOpen,
    setPickerOpen,
    openPicker,
  } = useWorkspaceStore();

  const activeLayout = pages[activePageId]?.layout ?? [];

  // Host width (minus scroll padding), null until first measured.
  const [hostWidth, setHostWidth] = useState<number | null>(null);
  const [flashingId, setFlashingId] = useState<string | null>(null);

  useEffect(() => {
    if (!focusRequest) return;
    const { instanceId } = focusRequest;
    const raf = requestAnimationFrame(() => {
      document
        .getElementById(`widget-${activePageId}-${instanceId}`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
      setFlashingId(instanceId);
    });
    // Consume the request once the flash completes so a later page-switch back
    // to this page doesn't re-run this effect against the stale request.
    const t = setTimeout(() => {
      setFlashingId(null);
      clearFocusRequest();
    }, 1200);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
    };
  }, [focusRequest, activePageId, clearFocusRequest]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const panningRef = useRef(false);
  const panPointerIdRef = useRef<number | null>(null);
  const panStartRef = useRef({ x: 0, y: 0, scrollLeft: 0, scrollTop: 0 });

  // (The four-edge gradient "scroll hints" that used to be measured here were
  // removed 2026-07-13: their canvas-colored gradients were invisible over the
  // canvas itself, so their only visible effect was darkening the edges of
  // widgets pinned at the viewport boundary — the founder read it as an
  // artifact, on both the watchlist's 52W column and its ticker column.)
  const measureEdges = useCallback(() => {
    const el = document.getElementById(GRID_SCROLL_ID);
    if (!el) return;
    // Register the visible grid-row band so addWidget can place a fresh widget
    // in view. Grid rows are ROW_HEIGHT + margin tall; scrollTop is offset by
    // the scroll container's top padding.
    const rowPx = ROW_HEIGHT_PX + GRID_MARGIN_PX;
    const topPx = el.scrollTop - GRID_CONTENT_PADDING_TOP_PX;
    const rowTop = Math.max(0, Math.floor(topPx / rowPx));
    const rowBottom = Math.max(
      rowTop,
      Math.floor((topPx + el.clientHeight - 1) / rowPx),
    );
    useWorkspaceStore.getState().setVisibleRows({ top: rowTop, bottom: rowBottom });
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

  // Re-check the scroll-edge fades on page switch or any layout change; rAF so
  // RGL has applied its DOM changes before we read scroll sizes.
  useEffect(() => {
    const id = requestAnimationFrame(measureEdges);
    return () => cancelAnimationFrame(id);
  }, [activePageId, pages, measureEdges]);

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
                    "radial-gradient(circle, var(--surface-grid-dot) 1px, transparent 1px)",
                  backgroundSize: `${GRID_COL_TARGET_PX + GRID_MARGIN_PX}px ${ROW_HEIGHT_PX + GRID_MARGIN_PX}px`,
                  backgroundPosition: "16px 16px",
                  backgroundAttachment: "local",
                }
          }
        >
          {hydrated && activeLayout.length === 0 ? (
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
          {hydrated && hostWidth != null
            ? Object.values(pages).map((page) => (
                <WorkspacePageGrid
                  key={page.id}
                  page={page}
                  hostWidth={hostWidth}
                  active={page.id === activePageId}
                  locked={locked}
                  flashingId={flashingId}
                />
              ))
            : null}
        </div>
      </div>
    </div>
  );
}
