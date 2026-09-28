"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Reorder, useDragControls } from "motion/react";
import { Check, GripVertical, Pin } from "lucide-react";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { WatchlistAggregatePanel } from "@/components/workspace/widgets/watchlist-aggregate-panel";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  fetchOvernightTradable,
  fetchSnapshot,
  fetchWatchlists,
  searchSymbols,
  updateWatchlist,
  type Position,
  type WatchlistSettings,
  type WatchlistsState,
} from "@/lib/api";
import { useWatchlistMarket } from "@/lib/use-watchlist-market";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePageActive } from "@/lib/workspace/page-active-context";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import { usePriceFlash } from "@/lib/workspace/use-price-flash";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import {
  DEFAULT_DENSITY,
  DENSITY_OPTIONS,
  DENSITY_ROW_CLASS,
  DENSITY_TEXT_CLASS,
  SYMBOL_MIN_WIDTH,
  daysUntil,
  resolveSymbolWidth,
  resolveWatchlistColumns,
  type ResolvedWatchlistColumn,
  type WatchlistCellContext,
  type WatchlistDensity,
} from "@/lib/workspace/watchlist-columns";
import { useWatchlistLiveSort } from "@/lib/workspace/use-watchlist-live-sort";
import { cn } from "@/lib/utils";

const EMPTY_SYMBOLS: string[] = [];

// Every column — the pinned symbol column included — is a fixed px track, so a
// resize moves ONLY the dragged boundary (Excel-like); the columns right of it
// shift as a block. The LAST column alone is minmax(w, 1fr): when the widget is
// wider than the columns it stretches to the right border (founder: whatever is
// furthest right stays "locked to the end" — no dead zone past the 52W bar).
// Shared by the header + every row (inline style — widths are dynamic per the
// persisted settings blob).
function gridTemplate(symbolWidth: number, widths: number[]): string {
  const tracks = widths.map((w, i) =>
    i === widths.length - 1 ? `minmax(${w}px,1fr)` : `${w}px`,
  );
  return `${symbolWidth}px ${tracks.join(" ")}`;
}

// Vertical boundary line drawn on every non-first cell (header + rows) — makes
// the resizable column edges visible/discoverable.
const COL_LINE_CLASS = "border-l border-[var(--line-divider)]";

function earningsBadgeLabel(days: number): string {
  if (days === 0) return "E·today";
  if (days === 1) return "E·1d";
  return `E·${days}d`;
}

function earningsUrgencyColor(days: number): string {
  if (days <= 1) return "var(--c-red)";
  if (days <= 3) return "var(--c-amber, var(--c-yellow))";
  return "var(--fg-dim)";
}

function earningsBadgeStyle(days: number): CSSProperties {
  const color = earningsUrgencyColor(days);
  return {
    color,
    background: `color-mix(in srgb, ${color} 12%, transparent)`,
    boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${color} 35%, var(--border))`,
  };
}

function WatchlistAddRow({
  symbols,
  activeListId,
  densityClass,
  symbolWidth,
  widths,
  onAdded,
  onClose,
  onPublish,
}: {
  symbols: string[];
  activeListId: string;
  densityClass: string;
  symbolWidth: number;
  widths: number[];
  onAdded: (state: WatchlistsState) => void;
  onClose: () => void;
  onPublish: (symbol: string) => void;
}) {
  const [query, setQuery] = useState("");
  // Where the block cursor paints, in characters. Tracked off the input's real
  // selectionStart (not query.length) so ← → / a click move the block with the
  // insertion point instead of parking it at the end.
  const [caretCh, setCaretCh] = useState(0);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  // Server-rejected add (a slot-capped list that's full) — surfaced, not silent.
  const [error, setError] = useState<string | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const onList = useMemo(
    () => new Set(symbols.map((s) => s.toUpperCase())),
    [symbols],
  );

  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) return;

    const id = setTimeout(() => {
      setLoading(true);
      searchSymbols(q)
        .then((results) => {
          setSuggestions(results);
          setOpen(results.length > 0);
          setActiveIndex(-1);
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
          setActiveIndex(-1);
        })
        .finally(() => setLoading(false));
    }, 200);

    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  // Clear the input and keep the row open + focused so the user can add
  // another ticker without reopening. (Escape / × closes the row.)
  const resetForNext = useCallback(() => {
    setQuery("");
    setSuggestions([]);
    setOpen(false);
    setActiveIndex(-1);
    setError(null);
    inputRef.current?.focus();
  }, []);

  const addSymbol = useCallback(
    async (raw: string) => {
      const symbol = raw.trim().toUpperCase();
      if (!symbol) return;

      if (onList.has(symbol)) {
        // Already on the list — focus its chart so the tap isn't a silent no-op.
        onPublish(symbol);
        resetForNext();
        return;
      }

      setSubmitting(true);
      try {
        // Pin the newly added symbol to the TOP of the user's order.
        const next = await updateWatchlist(activeListId, {
          symbols: [symbol, ...symbols],
        });
        onAdded(next);
        resetForNext();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to add symbol");
      } finally {
        setSubmitting(false);
      }
    },
    [activeListId, onAdded, onList, onPublish, resetForNext, symbols],
  );

  return (
    <div
      ref={wrapRef}
      // One more row of the table, not an island floating over it: the same
      // grid tracks as the rows (so the column lines run through it), the rows'
      // divider, the rows' density pitch, the widget's own surface — no inset
      // box, no rounding, no accent glow.
      // z-30 is load-bearing, measured: `position: sticky` ALWAYS opens a
      // stacking context, so the suggestions menu — a child of the sticky Sym
      // cell, which is what pins it on horizontal scroll — is trapped at that
      // cell's level. Without a z here the rows' own sticky cells (z-[1], later
      // in DOM) paint over the menu; document.elementFromPoint inside the open
      // menu returned a ROW's ticker cell. Cost of the 30: while the add row is
      // open, vertical scroll slides its band OVER the sticky column header
      // (z-[2]) instead of under it — pre-existing, see SUMMARY.
      className="relative z-30 grid border-b border-[var(--line-divider)] px-1"
      style={{ gridTemplateColumns: gridTemplate(symbolWidth, widths) }}
    >
      {/* The prompt sits IN the Sym column, where the ticker it is about to add
          will land, and sticky-left like the rows' frozen pane — so at any
          widget width and any horizontal scroll it stays on screen (the add row
          spans the full scrollable column span, so an unpinned input scrolled
          clean out of view). */}
      <div
        className={cn(
          "sticky left-0 z-[1] flex min-w-0 items-center gap-1 border-r border-[var(--line-divider)] bg-[var(--panel)] pr-1",
          densityClass,
        )}
      >
        {/* Terminal prompt, not a form field: the native caret is hidden and a
            blinking block paints at the insertion point — with the mono face
            every glyph is exactly 1ch wide, so the caret index IS its x. That
            block is the whole "type here" affordance (no placeholder, no `+`).
            research-cursor-pulse + steps(1,end) = a hard on/off blink, not a
            fade. */}
        <div className="relative flex min-w-0 flex-1 items-center">
          <input
            ref={inputRef}
            type="text"
            value={query}
            autoFocus
            disabled={submitting}
            aria-label="Add symbol"
            // No size class: `font: inherit` (preflight) hands the input the
            // widget's density font + line-height, so it matches the rows at
            // any density. p-0 kills the UA input padding that would shift the
            // text off the caret's 1ch grid.
            className="w-full min-w-0 bg-transparent p-0 font-mono uppercase text-foreground caret-transparent outline-none"
            onSelect={(e) => setCaretCh(e.currentTarget.selectionStart ?? 0)}
            onChange={(e) => {
              const next = e.target.value.toUpperCase();
              setQuery(next);
              setCaretCh(e.target.selectionStart ?? next.length);
              setActiveIndex(-1);
              setError(null);
              if (!next.trim()) {
                setSuggestions([]);
                setOpen(false);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                if (suggestions.length === 0) return;
                setOpen(true);
                setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setActiveIndex((i) => Math.max(i - 1, -1));
                return;
              }
              if (e.key === "Enter") {
                e.preventDefault();
                const picked =
                  activeIndex >= 0
                    ? suggestions[activeIndex]
                    : query || suggestions[0] || "";
                void addSymbol(picked);
              }
              if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
            onFocus={() => {
              if (suggestions.length > 0) setOpen(true);
            }}
          />
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 w-[1ch] animate-[research-cursor-pulse_1.06s_steps(1,end)_infinite] bg-[var(--c-accent)] motion-reduce:animate-none"
            style={{ left: `${caretCh}ch` }}
          />
        </div>
        {loading ? (
          <span className="shrink-0 font-mono text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            …
          </span>
        ) : null}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close add symbol"
          className="shrink-0 rounded px-0.5 font-mono leading-none text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
        >
          ×
        </button>
        {open && suggestions.length > 0 ? (
          <ul
            // Anchored to the sticky prompt cell, so the menu rides the same
            // pin. A recessed theme surface, never a white color-mix (this was
            // the one surface in the app mixing toward #ffffff): --bg-elevated
            // resolves to the widget's OWN fill on every theme, which left the
            // menu edgeless — --bg-inset is a real plane apart on all five.
            // -left-1 / +0.5rem: the cell sits inside the row's px-1 and carries
            // pr-1, so this is what makes the menu span the Sym column exactly,
            // edge to frozen-pane line.
            className="absolute top-full -left-1 z-50 max-h-32 w-max min-w-[calc(100%+0.5rem)] overflow-auto border border-[var(--border-strong)] bg-[var(--bg-inset)] py-0.5 shadow-2xl"
          >
            {suggestions.map((sym, i) => {
              const already = onList.has(sym.toUpperCase());
              return (
                <li key={sym}>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-2 py-1 text-left font-mono text-[length:var(--fs-body)] text-foreground hover:bg-[var(--c-accent-soft)]",
                      i === activeIndex && "bg-[var(--c-accent-soft)]",
                    )}
                    onMouseEnter={() => setActiveIndex(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      void addSymbol(sym);
                    }}
                  >
                    <span>{sym}</span>
                    {already ? (
                      <span className="text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                        on list
                      </span>
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
        {error ? (
          <div className="absolute top-full -left-1 z-50 w-max min-w-full border border-[var(--c-red)] bg-[var(--bg-inset)] px-2 py-1 font-mono text-[length:var(--fs-table)] text-[var(--c-red)]">
            {error}
          </div>
        ) : null}
      </div>
      {/* Empty cells so the column boundary lines run through the add row the
          same way they run through every data row. */}
      {widths.map((_, i) => (
        <div key={i} className={cn(densityClass, i > 0 && COL_LINE_CLASS)} />
      ))}
    </div>
  );
}

type Quote = ReturnType<typeof useWatchlistMarket>["quotes"][string];

// Sessions whose display price is the extended-hours print. The per-row
// session badges that used to ride along (PRE/AFT/24h on EVERY row) were
// dropped 2026-07-13 — the session is global context, so decorating every row
// said nothing (TradingView rule: decorate exceptions only). The one
// per-symbol exception still worth a marker is a delayed overnight quote.
const EXT_PRICE_SESSIONS = new Set(["pre", "post", "overnight", "closed"]);

const HOLD_MS = 300;
const MOVE_CANCEL_PX = 6;

function pointInRect(x: number, y: number, rect: DOMRect | null): boolean {
  return (
    rect != null &&
    x >= rect.left &&
    x <= rect.right &&
    y >= rect.top &&
    y <= rect.bottom
  );
}

function WatchlistSymbolRow({
  symbol,
  quote,
  position,
  columns,
  symbolWidth,
  widths,
  contentMinWidth,
  densityClass,
  isOvernight,
  linked,
  selected,
  pinnable,
  pinned,
  onTogglePin,
  onPublish,
  onSelect,
  onContextMenu,
  onDragStart,
  onReorderEnd,
  onDragOutRelease,
  getWidgetRect,
  animateLayout,
  onRowNode,
  vtStyle,
}: {
  symbol: string;
  quote: Quote | undefined;
  position: Position | undefined;
  columns: ResolvedWatchlistColumn[];
  symbolWidth: number;
  widths: number[];
  contentMinWidth: number;
  densityClass: string;
  isOvernight: boolean;
  linked: boolean;
  selected: boolean;
  // Slot-capped lists only (the flow list): pinned symbols ride above the cap.
  pinnable: boolean;
  pinned: boolean;
  onTogglePin: () => void;
  onPublish: () => void;
  onSelect: () => void;
  onContextMenu: (e: ReactMouseEvent) => void;
  onDragStart: () => void;
  onReorderEnd: () => void;
  onDragOutRelease: () => void;
  getWidgetRect: () => DOMRect | null;
  // Motion layout animation is drag-only (spring). Quote-driven reseats
  // zero the layout duration and use scoped view-transitions / WAAPI FLIP
  // so we don't leave will-change:transform on every row (that was eating
  // the 1px hairlines).
  animateLayout: boolean;
  onRowNode: (el: HTMLLIElement | null) => void;
  vtStyle: CSSProperties | undefined;
}) {
  const controls = useDragControls();
  // While a held row is dragged OUTSIDE the widget's bounding box it becomes a
  // "release to delete" gesture; back inside, it's a normal reorder.
  const [willDelete, setWillDelete] = useState(false);
  const itemRef = useRef<HTMLLIElement | null>(null);

  // Keep the keyboard-selected row in view as arrows move the selection.
  useEffect(() => {
    if (selected) itemRef.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  // Hold-anywhere-to-drag: a ~300ms hold on a non-interactive part of the row
  // lifts it into a drag; a quick tap (timer never fires) publishes as usual.
  const holdTimer = useRef<number | null>(null);
  const savedEvent = useRef<ReactPointerEvent | null>(null);
  const startPos = useRef<{ x: number; y: number } | null>(null);
  const didDrag = useRef(false);

  const clearHold = useCallback(() => {
    if (holdTimer.current != null) {
      window.clearTimeout(holdTimer.current);
      holdTimer.current = null;
    }
    savedEvent.current = null;
    startPos.current = null;
  }, []);

  const handlePointerDown = useCallback(
    (e: ReactPointerEvent) => {
      // Left button only. A right-press used to be interrupted by the browser's
      // own context menu; now that we preventDefault ours, an un-guarded
      // right-press held past HOLD_MS would lift the row into a drag under the
      // open menu.
      if (e.button !== 0) return;
      // Never start a drag from a ticker button, ⓘ button, or any link.
      if ((e.target as HTMLElement).closest("button, a")) return;
      didDrag.current = false;
      savedEvent.current = e;
      startPos.current = { x: e.clientX, y: e.clientY };
      holdTimer.current = window.setTimeout(() => {
        holdTimer.current = null;
        const saved = savedEvent.current;
        if (!saved) return;
        didDrag.current = true;
        onDragStart();
        controls.start(saved);
      }, HOLD_MS);
    },
    [controls, onDragStart],
  );

  const handlePointerMove = useCallback((e: ReactPointerEvent) => {
    if (holdTimer.current == null || !startPos.current) return;
    const dx = e.clientX - startPos.current.x;
    const dy = e.clientY - startPos.current.y;
    if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) {
      window.clearTimeout(holdTimer.current);
      holdTimer.current = null;
      savedEvent.current = null;
      startPos.current = null;
    }
  }, []);

  const session = quote?.market_session;
  const useExtPrice =
    session != null &&
    EXT_PRICE_SESSIONS.has(session) &&
    quote?.ext_price != null;
  const extBadge =
    useExtPrice && session === "overnight" && quote?.overnight_delayed
      ? { label: "dly", title: "Overnight (24/5) — delayed ~15 min" }
      : null;
  const displayPrice = useExtPrice ? (quote?.ext_price ?? 0) : (quote?.price ?? 0);
  const displayChangePct = useExtPrice
    ? (quote?.ext_change_pct ?? 0)
    : (quote?.change_pct ?? 0);
  const positive = displayChangePct >= 0;

  // Overnight is the one session where rows genuinely differ: 24/5 names and
  // futures keep trading, the rest are frozen until pre-market (dim the row —
  // the pro "stale quote" convention). Every other session renders rows plain:
  // state, not capability.
  const overnightDead =
    session === "overnight" && !isOvernight && !symbol.startsWith("/");

  const nextEarningsDate = quote?.next_earnings ?? null;
  const earningsDays =
    nextEarningsDate != null ? daysUntil(nextEarningsDate) : null;
  const showEarningsBadge =
    earningsDays != null && earningsDays >= 0 && earningsDays <= 14;

  const priceFlash = usePriceFlash(quote ? displayPrice : null);

  const cellCtx: WatchlistCellContext = {
    quote,
    position,
    displayPrice,
    displayChangePct,
    positive,
    extBadge: extBadge ? { label: extBadge.label, title: extBadge.title } : null,
    priceFlash,
  };

  return (
    <Reorder.Item
      ref={(el: HTMLLIElement | null) => {
        itemRef.current = el;
        onRowNode(el);
      }}
      value={symbol}
      layout="position"
      // Quote-driven reseats must not spring — that was the flip-flop. Drag
      // still uses the default layout spring so siblings make a hole.
      transition={animateLayout ? undefined : { layout: { duration: 0 } }}
      // Unlock BOTH axes so a dragged-out row follows the pointer horizontally.
      // The framer-motion Reorder.Item source spreads a `drag` prop AFTER its
      // internal `drag: axis` default, so this overrides the group's y-lock
      // (the lib documents `<Reorder.Item drag />` for both-axes). onReorder is
      // unaffected: the internal onDrag keys off point[axis]/velocity[axis] (y),
      // and dragSnapToOrigin (forced true by the lib) snaps x back to 0 on drop.
      drag
      dragListener={false}
      dragControls={controls}
      whileDrag={{
        scale: 1.02,
        zIndex: 20,
        boxShadow: "0 6px 20px rgba(0, 0, 0, 0.45)",
      }}
      onDrag={(_, info) => {
        // Pointer outside the widget box => release will delete.
        setWillDelete(
          !pointInRect(info.point.x, info.point.y, getWidgetRect()),
        );
      }}
      onDragEnd={(_, info) => {
        const outside = !pointInRect(
          info.point.x,
          info.point.y,
          getWidgetRect(),
        );
        setWillDelete(false);
        // Release outside deletes, immediately: the red ring + tint the row has
        // been wearing since it crossed the edge IS the "let go and it's gone"
        // warning. Release inside = reorder.
        if (outside) onDragOutRelease();
        else onReorderEnd();
      }}
      // [will-change:auto]!: framer flips will-change to "transform" on the
      // first layout animation and NEVER resets it, so every row stays
      // promoted to its own GPU layer — where this 1px border-b rasterizes
      // away at rest (lines vanish until a hover repaint). The stylesheet
      // !important beats framer's inline style; rows still composite while
      // actually animating.
      className="border-b border-[var(--line-divider)] [will-change:auto]!"
      // Row identity hook for the headless UI verification scripts, which need a
      // stable per-row selector. (The context menu gets its symbol from the
      // handler closure, not from the DOM.)
      data-symbol={symbol}
      onContextMenu={(e) => {
        // Suppress the browser menu and open ours at the pointer. Selecting the
        // row too gives the menu a visible anchor — the tinted row is the one
        // the menu's "delete AAPL" is about.
        e.preventDefault();
        onSelect();
        onContextMenu(e);
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={clearHold}
      onPointerCancel={clearHold}
      onClick={() => {
        // A click always marks the row keyboard-selected (even after a drag).
        onSelect();
        // Suppress the publish for the pointerup that ended a drag.
        if (didDrag.current) {
          didDrag.current = false;
          return;
        }
        onPublish();
      }}
      style={{ cursor: linked ? "pointer" : "default", ...vtStyle }}
    >
      <div
        className={cn(
          // No items-center / row-level py: each cell stretches to the row
          // height and carries the density padding itself, so the column
          // boundary lines run edge-to-edge vertically.
          // px-1 (not 2): the hover/selected fill exposes this padding as
          // colored space left of the tickers — keep it to a 4px breath.
          "group/row group grid px-1",
          // Frozen-for-the-night rows read dim (quote is stale until pre).
          overnightDead && "opacity-60",
          // Selection WINS over hover — hover:bg on a selected row painted the
          // row body hover-gray while the sticky cell stayed selection-teal
          // (the cell's selected branch has no group-hover), leaving a
          // two-tone notch left of the tickers whenever the selected row was
          // hovered. One state, one fill.
          selected
            ? "bg-[var(--surface-selected)]"
            : "hover:bg-[var(--bg-hover)]",
          willDelete &&
            "rounded opacity-50 ring-1 ring-[var(--c-red)] bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]",
        )}
        style={{
          gridTemplateColumns: gridTemplate(symbolWidth, widths),
          minWidth: contentMinWidth,
        }}
      >
        {/* Pinned symbol cell — sticky-left with an opaque background so the
            ticker stays legible while the extra columns scroll under it. At rest
            no Reorder.Item transform is applied, so sticky resolves against the
            list's horizontal scrollport correctly. The right border is the
            frozen-pane edge. (Film grain rides above the whole widget body via
            .grain-above — no per-cell grain patches.) */}
        <div
          className={cn(
            "sticky left-0 z-[1] inline-flex min-w-0 items-center gap-1 border-r border-[var(--line-divider)] pr-1",
            densityClass,
            // --surface-selected is a solid per-theme hex (editor knob), so
            // the sticky cell can repaint it opaquely — scrolled columns
            // can't ghost through and the tint can't stack on the row's own.
            // Same for the drag-out danger tint: the row's version mixes red
            // into transparent, which this opaque cell would paint over (the
            // warning stopped dead at the frozen pane — #282 fixed exactly that
            // for the confirm wash, and the confirm is gone), so mix the same
            // 12% red into --panel here. One state, one fill.
            willDelete
              ? "bg-[color-mix(in_srgb,var(--c-red)_12%,var(--panel))]"
              : selected
                ? "bg-[var(--surface-selected)]"
                : "bg-[var(--panel)] group-hover/row:bg-[var(--bg-hover)]",
          )}
        >
          <ClickableSymbol
            symbol={symbol}
            linked={linked}
            onActivate={onPublish}
            // leading-none collapses the text line-box to its cap-height so the
            // live dot (items-center'd against this cluster) sits optically
            // centered on the ticker at any --fs size, not low toward the baseline.
            className="truncate font-medium leading-none"
          />
          {pinnable ? (
            <button
              type="button"
              aria-label={pinned ? `Unpin ${symbol}` : `Pin ${symbol}`}
              aria-pressed={pinned}
              title={
                pinned
                  ? "Pinned — always watched, doesn't use a slot"
                  : "Pin — always watched, doesn't use a slot"
              }
              onClick={(e) => {
                e.stopPropagation();
                onTogglePin();
              }}
              className={cn(
                "shrink-0 rounded p-0.5 leading-none",
                pinned
                  ? "text-[var(--c-accent)]"
                  : "text-[var(--fg-faint)] opacity-0 transition-opacity hover:text-foreground group-hover/row:opacity-100",
              )}
            >
              <Pin className={cn("size-3", pinned && "fill-current")} />
            </button>
          ) : null}
          {/* ml-auto pins the earnings badge to the cell's right edge so
              badges form a tidy right-aligned column instead of zig-zagging
              after tickers of varying width. */}
          {showEarningsBadge && nextEarningsDate ? (
            <span
              className="ml-auto shrink-0 rounded px-1 py-px font-medium font-mono text-[length:var(--fs-table)] leading-none"
              style={earningsBadgeStyle(earningsDays)}
              title={`Earnings ${nextEarningsDate}`}
            >
              {earningsBadgeLabel(earningsDays)}
            </span>
          ) : null}
        </div>
        {columns.map((col, i) => (
          <div
            key={col.def.key}
            className={cn(
              // flex-col + justify-center vertically centers the value while
              // the cell itself stretches to the full row height, so the
              // boundary line runs unbroken through the density padding.
              "flex min-w-0 flex-col justify-center px-1",
              densityClass,
              // Boundary between the sticky cell and column 0 is the sticky
              // cell's own border-r (it must travel with the frozen pane).
              i > 0 && COL_LINE_CLASS,
              col.def.align === "right" ? "text-right" : "text-left",
            )}
          >
            {col.def.cell(cellCtx)}
          </div>
        ))}
      </div>
    </Reorder.Item>
  );
}

// One row in the settings overlay's column list. Its own useDragControls lets
// the grip be the sole drag handle while a click on the rest toggles visibility.
function ColumnSettingRow({
  col,
  onToggle,
}: {
  col: ResolvedWatchlistColumn;
  onToggle: (key: string) => void;
}) {
  const controls = useDragControls();
  return (
    <Reorder.Item
      value={col.def.key}
      dragListener={false}
      dragControls={controls}
      className={cn(
        "flex items-center gap-2 rounded px-1 py-1 hover:bg-[var(--bg-hover)]",
        !col.visible && "opacity-50",
      )}
    >
      <button
        type="button"
        aria-label={`Reorder ${col.def.title}`}
        onPointerDown={(e) => controls.start(e)}
        className="flex cursor-grab touch-none items-center text-[var(--fg-faint)] active:cursor-grabbing"
      >
        <GripVertical className="size-3.5" />
      </button>
      <button
        type="button"
        onClick={() => onToggle(col.def.key)}
        className="flex flex-1 items-center gap-2 text-left"
      >
        <span className="flex size-3.5 shrink-0 items-center justify-center">
          {col.visible ? (
            <Check className="size-3 text-[var(--text-accent)]" />
          ) : null}
        </span>
        <span className="text-foreground">{col.def.title}</span>
        <span className="lowercase text-[var(--fg-faint)]">
          {col.def.category}
        </span>
      </button>
    </Reorder.Item>
  );
}

function WatchlistSettingsPanel({
  resolved,
  density,
  onReorder,
  onToggle,
  onSetDensity,
  onReset,
  onClose,
}: {
  resolved: ResolvedWatchlistColumn[];
  density: WatchlistDensity;
  onReorder: (keys: string[]) => void;
  onToggle: (key: string) => void;
  onSetDensity: (d: WatchlistDensity) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const orderKeys = useMemo(
    () => resolved.map((c) => c.def.key),
    [resolved],
  );

  return (
    <div
      className="absolute inset-0 z-10 overflow-y-auto p-2 font-mono text-[length:var(--fs-table)] scroll-shadow-y"
      // .scroll-shadow-y sets the `background` shorthand, which wipes any
      // background-color utility — paint the opaque fill inline (same pattern
      // as the add-row suggestions dropdown above).
      style={{ backgroundColor: "var(--panel)" }}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
          density
        </span>
        {DENSITY_OPTIONS.map((o) => (
          <button
            key={o.key}
            type="button"
            onClick={() => onSetDensity(o.key)}
            className={cn(
              "flex items-center gap-0.5",
              density === o.key
                ? "text-[var(--text-accent)]"
                : "text-[var(--fg-dim)] hover:text-foreground",
            )}
          >
            {density === o.key ? <Check className="size-3" /> : null}
            {o.label}
          </button>
        ))}
      </div>

      <Reorder.Group
        axis="y"
        values={orderKeys}
        onReorder={onReorder}
        className="m-0 list-none space-y-0.5 p-0"
      >
        {resolved.map((col) => (
          <ColumnSettingRow
            key={col.def.key}
            col={col}
            onToggle={onToggle}
          />
        ))}
      </Reorder.Group>

      <div className="mt-2 flex items-center justify-between border-t border-[var(--line-divider)] pt-2">
        <button
          type="button"
          onClick={onReset}
          className="text-[var(--fg-dim)] hover:text-foreground"
        >
          reset to defaults
        </button>
        <button
          type="button"
          onClick={onClose}
          className="text-[var(--text-accent)] hover:opacity-80"
        >
          done
        </button>
      </div>
    </div>
  );
}

// Keep the menu this far off every viewport edge when a right-click near a
// border would otherwise push it half off-screen.
const MENU_EDGE_PAD = 4;

// Row right-click menu: delete, and pin/unpin. Portaled to <body> and placed
// `fixed` at the pointer — NOT anchored anywhere in the row.
//
// This is the #282 lesson made structural. A row's symbol cell is
// `position: sticky`, which ALWAYS opens a stacking context, and the whole row
// lives inside a clipping scrollport whose content box is `contentMinWidth`
// wide (much wider than the visible widget) — so anything anchored in there
// either renders under a later sticky cell or lands off the visible right edge.
// At body level, clientX/clientY ARE the coordinates: no ancestor can clip it,
// restack it, or redefine what "fixed" is measured against.
function WatchlistContextMenu({
  symbol,
  x,
  y,
  pinnable,
  pinned,
  listRef,
  onDelete,
  onTogglePin,
  onClose,
}: {
  symbol: string;
  x: number;
  y: number;
  // Same gate as the row's own pin button: pins only mean anything on
  // slot-capped lists — on any other list the PUT succeeds but nothing
  // visible changes, so the item would be a silent mutation.
  pinnable: boolean;
  pinned: boolean;
  listRef: RefObject<HTMLDivElement | null>;
  onDelete: () => void;
  onTogglePin: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x, top: y });

  // Clamp into the viewport once the menu's real size is measurable, and move
  // focus onto the first item. A LAYOUT effect, not a passive one: the
  // corrected position has to be in the first paint or a right-click near the
  // bottom edge visibly flashes hanging off the screen before it snaps back.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    setPos({
      left: Math.max(
        MENU_EDGE_PAD,
        Math.min(x, window.innerWidth - width - MENU_EDGE_PAD),
      ),
      top: Math.max(
        MENU_EDGE_PAD,
        Math.min(y, window.innerHeight - height - MENU_EDGE_PAD),
      ),
    });
    // Focus the MENU, not its first item. The first item is `delete`, and it
    // sits directly under the pointer where a context menu opens — focusing it
    // would leave a no-confirm, no-undo destructive action one Enter away the
    // instant the menu appears. Tab still walks into the items.
    el.focus();
  }, [x, y, symbol]);

  useEffect(() => {
    const list = listRef.current;
    // Capture phase, so a right-click on a DIFFERENT row tears this menu down
    // before that row's contextmenu handler reopens it at the new point. The
    // contains() check is what lets an item's own click land first.
    const onPointerDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    // Scrolling the list slides the row out from under a menu that cannot
    // follow it (it is pinned to the viewport, not the row).
    list?.addEventListener("scroll", onClose);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
      list?.removeEventListener("scroll", onClose);
    };
  }, [listRef, onClose]);

  // Hand focus back to the list on close. Removing the focused item orphans
  // focus on <body>; that check is also how we avoid stealing it when the user
  // closed the menu by clicking some other control, which already has it.
  useEffect(
    () => () => {
      if (document.activeElement === document.body) listRef.current?.focus();
    },
    [listRef],
  );

  // whitespace-nowrap: `left` alone makes the menu shrink-to-fit against the
  // space left of the viewport edge, so near a right/bottom corner the labels
  // wrapped to two lines instead of the whole menu sliding back inside
  // (measured at a 314px viewport). Nowrap keeps the width honest, which is
  // what the clamp above measures.
  const itemClass =
    "block w-full whitespace-nowrap px-2 py-1 text-left text-[length:var(--fs-body)]";

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={`${symbol} actions`}
      tabIndex={-1}
      // Same surface as the add-row suggestions menu: a real recessed plane
      // (--bg-inset is a solid hex on every theme), a strong border, a big
      // shadow. Plain lowercase words, no pills, no icons.
      // font-mono is declared HERE, not inherited: living on <body> puts the
      // menu outside the widget root that carries the widget's mono face, so
      // without this it rendered in the app's Inter (measured).
      // outline-none: this container only HOLDS focus so the menu owns it (see
      // the layout effect) — it is not a stop a keyboard user navigates to, and
      // the UA's default focus ring on it painted a thick white box around the
      // whole menu (measured). Tabbing to the items still rings each item.
      className="fixed z-50 border border-[var(--border-strong)] bg-[var(--bg-inset)] py-0.5 font-mono shadow-2xl outline-none"
      style={{ left: pos.left, top: pos.top }}
      // A right-click ON the menu shouldn't summon the browser's menu over it.
      onContextMenu={(e) => e.preventDefault()}
    >
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onDelete();
          onClose();
        }}
        className={cn(
          itemClass,
          // Red-tinted hover rather than the shared accent wash: this is the
          // one destructive item, and the accent (teal) hover read as "safe"
          // under a red label. Screenshotted both — see SUMMARY.
          "text-[var(--c-red)] hover:bg-[color-mix(in_srgb,var(--c-red)_18%,transparent)]",
        )}
      >
        delete {symbol}
      </button>
      {pinnable ? (
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onTogglePin();
            onClose();
          }}
          className={cn(
            itemClass,
            "text-foreground hover:bg-[var(--c-accent-soft)]",
          )}
        >
          {pinned ? "unpin" : "pin"} {symbol}
        </button>
      ) : null}
    </div>,
    document.body,
  );
}

export function WatchlistWidget({
  instanceId,
  config,
  onConfigChange,
}: WidgetProps) {
  const [state, setState] = useState<WatchlistsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [dragOrder, setDragOrder] = useState<string[] | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  // The one open row context menu, in viewport coordinates. Widget-level (not
  // per-row) so only one can ever exist.
  const [menu, setMenu] = useState<{
    symbol: string;
    x: number;
    y: number;
  } | null>(null);
  const widgetRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const widgetRectRef = useRef<DOMRect | null>(null);
  const [sortState, setSortState] = useState<{
    key: string;
    dir: "asc" | "desc";
  } | null>(null);
  const [overnightSymbols, setOvernightSymbols] = useState<Set<string>>(
    new Set(),
  );
  const dragOrderRef = useRef<string[] | null>(null);
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const pageActive = usePageActive();
  // Read inside the poll callbacks rather than as an effect dep: those effects
  // do their initial load on mount (a hidden page must still hold data so a
  // switch paints instantly), so re-running them on every page change would
  // refetch on hide as well as on show.
  const pageActiveRef = useRef(pageActive);
  pageActiveRef.current = pageActive;

  useEffect(() => {
    dragOrderRef.current = dragOrder;
  }, [dragOrder]);

  // A dragged-out row lives inside this widget's react-grid-item, each of which
  // is a transformed sibling / stacking context — so the row paints BEHIND
  // neighboring grid items (e.g. the chart) unless our own grid item wins the
  // z-order. While a row drag is active, raise the grid-item ancestor's
  // z-index, restoring the prior inline value when the drag ends.
  useEffect(() => {
    if (!isDragging) return;
    const gridItem = widgetRef.current?.closest(
      ".react-grid-item",
    ) as HTMLElement | null;
    if (!gridItem) return;
    const prev = gridItem.style.zIndex;
    gridItem.style.zIndex = "40";
    return () => {
      gridItem.style.zIndex = prev;
    };
  }, [isDragging]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchWatchlists();
        if (cancelled) return;
        // Watchlists are edited server-side by more than this widget (chat
        // tools on web AND mobile, other tabs/devices) — converge on server
        // truth, but only re-render when the payload actually changed.
        setState((prev) =>
          prev && JSON.stringify(prev) === JSON.stringify(data) ? prev : data,
        );
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Failed to load watchlists",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const id = window.setInterval(() => {
      if (!pageActiveRef.current) return;
      void load();
    }, 30_000);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);

    return () => {
      cancelled = true;
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const displayList = useMemo(() => {
    if (!state) return null;
    const listId = config.watchlistId ?? state.active_id;
    return (
      state.lists.find((list) => list.id === listId) ??
      // A persisted per-widget list id can dangle (list deleted elsewhere) —
      // degrade to the active list, then the first list, before giving up.
      state.lists.find((list) => list.id === state.active_id) ??
      state.lists[0] ??
      null
    );
  }, [config.watchlistId, state]);

  const symbols = useMemo(
    () => displayList?.symbols ?? EMPTY_SYMBOLS,
    [displayList?.symbols],
  );

  const symbolsKey = useMemo(() => symbols.join(","), [symbols]);

  // Slot-capped lists (the flow list) get the per-row pin toggle; pinned
  // symbols are always watched and ride above the slot cap.
  const pinnable = Boolean(displayList?.max_slots);
  const pinnedSet = useMemo(
    () => new Set(displayList?.pinned ?? []),
    [displayList?.pinned],
  );

  // ── Column settings (per-watchlist, persisted via the settings blob) ───────
  const [localSettings, setLocalSettings] = useState<
    WatchlistSettings | undefined
  >(undefined);
  // Transient width override for the column currently being resize-dragged;
  // committed to settings on pointerup (avoids a persist per mousemove).
  const [widthOverride, setWidthOverride] = useState<{
    key: string;
    width: number;
  } | null>(null);

  const listId = displayList?.id;
  const displayListRef = useRef(displayList);
  const prevListIdRef = useRef<string | null>(null);
  const onConfigChangeRef = useRef(onConfigChange);
  // Keep latest-value refs in sync (declared BEFORE the switch effect so it runs
  // first on each commit, giving that effect fresh values without re-firing it).
  useEffect(() => {
    displayListRef.current = displayList;
    onConfigChangeRef.current = onConfigChange;
  });

  // On watchlist switch: adopt the newly-active list's saved settings and close
  // the settings panel (only on an actual switch, not first mount).
  useEffect(() => {
    if (!listId) return;
    setLocalSettings(displayListRef.current?.settings);
    setWidthOverride(null);
    if (prevListIdRef.current && prevListIdRef.current !== listId) {
      onConfigChangeRef.current({ settingsOpen: false });
    }
    prevListIdRef.current = listId;
  }, [listId]);

  const resolved = useMemo(
    () => resolveWatchlistColumns(localSettings),
    [localSettings],
  );

  const savedSymbolWidth = useMemo(
    () => resolveSymbolWidth(localSettings),
    [localSettings],
  );
  const symbolWidth =
    widthOverride?.key === "symbol" ? widthOverride.width : savedSymbolWidth;

  // Apply the in-flight resize width to the dragged column only.
  const effectiveColumns = useMemo(
    () =>
      widthOverride
        ? resolved.map((c) =>
            c.def.key === widthOverride.key
              ? { ...c, width: widthOverride.width }
              : c,
          )
        : resolved,
    [resolved, widthOverride],
  );

  const visibleColumns = useMemo(
    () => effectiveColumns.filter((c) => c.visible),
    [effectiveColumns],
  );
  // The LAST column ignores its stored width and uses its registry minWidth:
  // its track is minmax(min, 1fr), so it exactly fills to the widget's right
  // border (never a dead zone, never forced overflow from a stale wide save).
  const widths = useMemo(
    () =>
      visibleColumns.map((c, i) =>
        i === visibleColumns.length - 1 ? c.def.minWidth : c.width,
      ),
    [visibleColumns],
  );
  const contentMinWidth = useMemo(
    () => symbolWidth + widths.reduce((sum, w) => sum + w, 0),
    [symbolWidth, widths],
  );

  const density: WatchlistDensity = localSettings?.density ?? DEFAULT_DENSITY;
  const densityClass = DENSITY_ROW_CLASS[density];

  // Persist a settings blob (optimistic local update + debounced PUT). Passing
  // `{}` resets the list to registry defaults (server drops the blob).
  const persistTimer = useRef<number | null>(null);
  const persistSettings = useCallback(
    (next: WatchlistSettings) => {
      setLocalSettings(next);
      const id = displayListRef.current?.id;
      if (!id) return;
      if (persistTimer.current != null) {
        window.clearTimeout(persistTimer.current);
      }
      persistTimer.current = window.setTimeout(() => {
        void updateWatchlist(id, { settings: next })
          .then(setState)
          .catch(() => {
            /* keep the optimistic local state on failure */
          });
      }, 400);
    },
    [],
  );

  useEffect(
    () => () => {
      if (persistTimer.current != null) {
        window.clearTimeout(persistTimer.current);
      }
    },
    [],
  );

  const buildSettings = useCallback(
    (
      cols: ResolvedWatchlistColumn[],
      d: WatchlistDensity,
      symWidth: number,
    ): WatchlistSettings => ({
      columns: [
        // The pinned symbol column persists alongside the registry columns
        // (non-registry key — resolveWatchlistColumns skips it on read).
        { key: "symbol", visible: true, width: symWidth },
        ...cols.map((c) => ({
          key: c.def.key,
          visible: c.visible,
          width: c.width,
        })),
      ],
      density: d,
    }),
    [],
  );

  const handleColumnReorder = useCallback(
    (keys: string[]) => {
      const byKey = new Map(resolved.map((c) => [c.def.key, c]));
      const next = keys
        .map((k) => byKey.get(k))
        .filter((c): c is ResolvedWatchlistColumn => c != null);
      persistSettings(buildSettings(next, density, savedSymbolWidth));
    },
    [resolved, density, savedSymbolWidth, persistSettings, buildSettings],
  );

  const handleColumnToggle = useCallback(
    (key: string) => {
      const next = resolved.map((c) =>
        c.def.key === key ? { ...c, visible: !c.visible } : c,
      );
      persistSettings(buildSettings(next, density, savedSymbolWidth));
    },
    [resolved, density, savedSymbolWidth, persistSettings, buildSettings],
  );

  const handleSetDensity = useCallback(
    (d: WatchlistDensity) => {
      persistSettings(buildSettings(resolved, d, savedSymbolWidth));
    },
    [resolved, savedSymbolWidth, persistSettings, buildSettings],
  );

  const handleResetColumns = useCallback(() => {
    persistSettings({});
  }, [persistSettings]);

  // ── Column resize (header edge drag) ───────────────────────────────────────
  const resizeRef = useRef<{
    key: string;
    startX: number;
    startWidth: number;
    minWidth: number;
    current: number;
  } | null>(null);

  const onResizePointerDown = useCallback(
    (e: ReactPointerEvent, key: string, width: number, minWidth: number) => {
      e.stopPropagation();
      e.preventDefault();
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      resizeRef.current = {
        key,
        startX: e.clientX,
        startWidth: width,
        minWidth,
        current: width,
      };
    },
    [],
  );

  const onResizePointerMove = useCallback((e: ReactPointerEvent) => {
    const r = resizeRef.current;
    if (!r) return;
    const next = Math.min(
      600,
      Math.max(r.minWidth, r.startWidth + (e.clientX - r.startX)),
    );
    r.current = next;
    setWidthOverride({ key: r.key, width: next });
  }, []);

  const onResizePointerUp = useCallback(
    (e: ReactPointerEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      resizeRef.current = null;
      setWidthOverride(null);
      if (r.key === "symbol") {
        persistSettings(buildSettings(resolved, density, r.current));
        return;
      }
      const next = resolved.map((c) =>
        c.def.key === r.key ? { ...c, width: r.current } : c,
      );
      persistSettings(buildSettings(next, density, savedSymbolWidth));
    },
    [resolved, density, savedSymbolWidth, persistSettings, buildSettings],
  );

  // ── Positions (only fetched when a position column is visible) ─────────────
  const needPositions = useMemo(
    () => visibleColumns.some((c) => c.def.category === "position"),
    [visibleColumns],
  );
  const [positions, setPositions] = useState<Record<string, Position>>({});

  useEffect(() => {
    // No position column shown → don't fetch. Any last-known positions stay in
    // state but are never read (no position cell renders them), so leaving them
    // is harmless and avoids a cascading setState here.
    if (!needPositions) return;
    let cancelled = false;
    const load = () => {
      fetchSnapshot()
        .then((snap) => {
          if (cancelled) return;
          const map: Record<string, Position> = {};
          for (const p of snap.positions) map[p.symbol] = p;
          setPositions(map);
        })
        .catch(() => {
          /* leave the last-known positions in place on a transient failure */
        });
    };
    load();
    const id = window.setInterval(() => {
      if (!pageActiveRef.current) return;
      load();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [needPositions]);

  useEffect(() => {
    if (symbols.length === 0) return;

    let cancelled = false;
    fetchOvernightTradable(symbols)
      .then((overnight) => {
        if (!cancelled) {
          setOvernightSymbols(new Set(overnight.map((s) => s.toUpperCase())));
        }
      })
      .catch(() => {
        if (!cancelled) setOvernightSymbols(new Set());
      });

    return () => {
      cancelled = true;
    };
  }, [symbolsKey, symbols]);

  const { quotes, spyChangePct, loading: marketLoading } = useWatchlistMarket({
    symbols,
    enabled: symbols.length > 0,
  });

  // Enrich each quote with its day-change spread vs SPY once here (single
  // source), so the vs_spy column reads q.vs_spy without per-cell math. When
  // SPY's change is unknown, leave vs_spy unset (the column shows DASH).
  const enrichedQuotes = useMemo(() => {
    if (spyChangePct == null) return quotes;
    const out: Record<string, Quote> = {};
    for (const [sym, q] of Object.entries(quotes)) {
      out[sym] = { ...q, vs_spy: q.change_pct - spyChangePct };
    }
    return out;
  }, [quotes, spyChangePct]);

  const sortValueMap = useMemo(() => {
    const map: Record<
      string,
      (q: Quote | undefined, pos: Position | undefined) => number | string | null
    > = {};
    for (const c of resolved) {
      if (c.def.sortValue) map[c.def.key] = c.def.sortValue;
    }
    return map;
  }, [resolved]);

  const getSortValue = useCallback(
    (sym: string): number | string | null => {
      if (!sortState) return null;
      if (sortState.key === "symbol") return sym;
      const fn = sortValueMap[sortState.key];
      if (!fn) return null;
      return fn(enrichedQuotes[sym], positions[sym]);
    },
    [sortState, sortValueMap, enrichedQuotes, positions],
  );

  const { liveOrder, scopeRef, registerRow, rowVtStyle, animateLayout } =
    useWatchlistLiveSort({
      sortState,
      baseOrder: symbols,
      listKey: listId,
      getValue: getSortValue,
      isDragging,
      pageActive,
      instanceId,
      scrollerRef: listRef,
    });

  // Drag order wins (the lift clears any column sort). Live-sort seats only
  // apply while a header is active — sticky, not tick-by-tick.
  const sortedSymbols = dragOrder ?? liveOrder ?? symbols;

  const handleSortHeaderClick = useCallback((key: string) => {
    setSortState((prev) => {
      if (prev?.key === key) {
        if (prev.dir === "asc") return { key, dir: "desc" };
        return null;
      }
      return { key, dir: "asc" };
    });
  }, []);

  const removeSymbol = useCallback(
    async (symbol: string) => {
      if (!displayList || removing) return;
      setRemoving(symbol);
      try {
        const next = await updateWatchlist(displayList.id, {
          symbols: displayList.symbols.filter((s) => s !== symbol),
        });
        setState(next);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to remove symbol",
        );
      } finally {
        setRemoving(null);
      }
    },
    [displayList, removing],
  );

  // Release outside the widget deletes on the spot — no are-you-sure step
  // (founder, overruling round 2: "I didn't want that are-you-sure feature").
  // The red ring + tint + 50% opacity the row wore the whole time it was
  // outside the box IS the warning, and it is live during the gesture rather
  // than after it.
  //
  // Drop the in-progress drag order first: it still contains this symbol and
  // sortedSymbols falls back to the persisted list, so without this the row
  // snaps back into the list for the length of the PUT and then vanishes.
  const deleteFromDrag = useCallback(
    (symbol: string) => {
      setIsDragging(false);
      setDragOrder(null);
      void removeSymbol(symbol);
    },
    [removeSymbol],
  );

  const closeMenu = useCallback(() => setMenu(null), []);

  const togglePin = useCallback(async (symbol: string) => {
    const list = displayListRef.current;
    if (!list) return;
    const cur = list.pinned ?? [];
    const next = cur.includes(symbol)
      ? cur.filter((s) => s !== symbol)
      : [...cur, symbol];
    try {
      setState(await updateWatchlist(list.id, { pinned: next }));
    } catch {
      // Fail-soft: the server rejects an unpin that would overflow the slots
      // (the symbol needs a free slot to land in); state stays as-is.
    }
  }, []);

  const persistReorder = useCallback(
    async (reordered: string[]) => {
      setSortState(null);
      if (!displayList) return;
      if (
        reordered.length === displayList.symbols.length &&
        reordered.every((sym, idx) => sym === displayList.symbols[idx])
      ) {
        return;
      }

      const prevState = state;
      setState((current) => {
        if (!current) return current;
        return {
          ...current,
          lists: current.lists.map((list) =>
            list.id === displayList.id
              ? { ...list, symbols: reordered }
              : list,
          ),
        };
      });

      try {
        const next = await updateWatchlist(displayList.id, {
          symbols: reordered,
        });
        setState(next);
        setDragOrder(null);
      } catch (err) {
        if (prevState) setState(prevState);
        setDragOrder(null);
        setError(
          err instanceof Error ? err.message : "Failed to reorder symbols",
        );
      }
    },
    [displayList, state],
  );

  const handleReorderEnd = useCallback(() => {
    setIsDragging(false);
    const reordered = dragOrderRef.current;
    setDragOrder(null);
    if (reordered) void persistReorder(reordered);
  }, [persistReorder]);

  // Lift: clear any active sort, snapshot the widget box (for the drag-out-to-
  // delete hit-test), suppress sibling text-selection, and drop any stray
  // selection the hold already started.
  const handleDragStart = useCallback(() => {
    setSortState(null);
    setIsDragging(true);
    widgetRectRef.current =
      widgetRef.current?.getBoundingClientRect() ?? null;
    window.getSelection()?.removeAllRanges();
  }, []);

  const getWidgetRect = useCallback(() => widgetRectRef.current, []);

  const handleListKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      const list = sortedSymbols;
      if (list.length === 0) return;

      const cur = selectedSymbol ? list.indexOf(selectedSymbol) : -1;
      // Arrow moves publish immediately (like a click) — the founder drives
      // the linked chart by holding ↑/↓, no Enter needed.
      const moveTo = (symbol: string) => {
        setSelectedSymbol(symbol);
        publish(symbol);
      };
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          moveTo(list[cur < 0 ? 0 : Math.min(cur + 1, list.length - 1)]);
          break;
        case "ArrowUp":
          e.preventDefault();
          moveTo(list[cur < 0 ? list.length - 1 : Math.max(cur - 1, 0)]);
          break;
        case "Enter":
          if (cur >= 0) {
            e.preventDefault();
            publish(list[cur]);
          }
          break;
        case "Delete":
        case "Backspace":
          // Direct, like the drag-out gesture — this key used to arm the
          // confirm, and the confirm is gone.
          if (cur >= 0) {
            e.preventDefault();
            void removeSymbol(list[cur]);
          }
          break;
        case "Escape":
          e.preventDefault();
          setSelectedSymbol(null);
          break;
      }
    },
    [sortedSymbols, selectedSymbol, publish, removeSymbol],
  );

  // A freshly added symbol is pinned to the top of the persisted order — clear
  // any active column sort / in-progress drag so it's visible at the top.
  const handleSymbolAdded = useCallback((next: WatchlistsState) => {
    setState(next);
    setSortState(null);
    setDragOrder(null);
  }, []);

  if (loading && !state) {
    return <WidgetLoading cols={[2, 1, 1, 1]} />;
  }

  if (error && !displayList) {
    return <WidgetError message={error} />;
  }

  if (!displayList || !state) {
    return <WidgetEmpty message="No active watchlist" />;
  }

  const headerCellClass =
    "text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase";

  return (
    <div
      ref={widgetRef}
      className={cn(
        "relative flex h-full flex-col overflow-hidden font-mono",
        DENSITY_TEXT_CLASS[density],
      )}
    >
      {config.settingsOpen ? (
        <WatchlistSettingsPanel
          resolved={resolved}
          density={density}
          onReorder={handleColumnReorder}
          onToggle={handleColumnToggle}
          onSetDensity={handleSetDensity}
          onReset={handleResetColumns}
          onClose={() => onConfigChange({ settingsOpen: false })}
        />
      ) : null}

      {/* Floating aggregate-return panel — follows the displayed list (a list
          switch refetches rather than closing, unlike the settings panel). */}
      {config.aggregateOpen && displayList ? (
        <WatchlistAggregatePanel
          listId={displayList.id}
          linked={linked}
          onSymbolClick={publish}
          onClose={() => onConfigChange({ aggregateOpen: false })}
        />
      ) : null}

      {marketLoading ? (
        <div className="border-b border-[var(--line-divider)] px-3 py-0.5">
          <span className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">loading…</span>
        </div>
      ) : null}

      <div
        ref={listRef}
        tabIndex={0}
        onKeyDown={handleListKeyDown}
        // No scroller padding: the sticky header and the row border-b lines
        // must span the full widget width — padding left see-through gutters
        // beside the header where scrolled rows ghosted into view.
        className="min-h-0 flex-1 overflow-auto scroll-shadow-y outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-[var(--border-strong)]"
      >
        {/* min-width holds the columns open and drives horizontal scroll; the
            header + rows share it so they scroll together left/right. */}
        <div style={{ minWidth: contentMinWidth }}>
          {/* Header row: sticky under the widget chrome on vertical scroll,
              with an opaque panel bg so the scroll-hint gradient never ghosts
              through the cells (it paints behind this row instead). Film grain
              rides above the whole body via .grain-above. */}
          <div
            className="sticky top-0 z-[2] grid border-b border-[var(--line-divider)] bg-[var(--panel)] px-1"
            style={{ gridTemplateColumns: gridTemplate(symbolWidth, widths) }}
          >
            {/* Sticky symbol header cell — mirrors the row cells' frozen pane
                (opaque bg + border-r), with its own resize handle. */}
            <div
              className={cn(
                "sticky left-0 z-[1] flex items-center border-r border-[var(--line-divider)] bg-[var(--panel)] pr-1",
                densityClass,
              )}
            >
              <button
                type="button"
                onClick={() => handleSortHeaderClick("symbol")}
                className={cn(headerCellClass, "min-w-0 flex-1 text-left")}
              >
                Sym
                {sortState?.key === "symbol" ? (
                  <span className="ml-0.5">
                    {sortState.dir === "asc" ? "▲" : "▼"}
                  </span>
                ) : null}
              </button>
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize symbol column"
                onPointerDown={(e) =>
                  onResizePointerDown(e, "symbol", symbolWidth, SYMBOL_MIN_WIDTH)
                }
                onPointerMove={onResizePointerMove}
                onPointerUp={onResizePointerUp}
                onClick={(e) => e.stopPropagation()}
                className="workspace-no-drag absolute top-0 right-0 z-[1] h-full w-[6px] cursor-col-resize touch-none hover:bg-[var(--border-strong)]"
              />
            </div>
            {visibleColumns.map((col, i) => {
              // The left handle drags this column's LEFT boundary, which — as
              // in a spreadsheet — resizes the column before it.
              const prev = i === 0 ? null : visibleColumns[i - 1];
              return (
                <div
                  key={col.def.key}
                  className={cn(
                    "relative flex min-w-0 flex-col justify-center px-1 select-none",
                    densityClass,
                    i > 0 && COL_LINE_CLASS,
                    col.def.align === "right" ? "text-right" : "text-left",
                  )}
                >
                  <button
                    type="button"
                    onClick={() =>
                      col.def.sortable && handleSortHeaderClick(col.def.key)
                    }
                    className={cn(
                      headerCellClass,
                      "w-full",
                      col.def.align === "right" ? "text-right" : "text-left",
                      col.def.sortable ? "cursor-pointer" : "cursor-default",
                    )}
                  >
                    {col.def.label}
                    {sortState?.key === col.def.key ? (
                      <span className="ml-0.5">
                        {sortState.dir === "asc" ? "▲" : "▼"}
                      </span>
                    ) : null}
                  </button>
                  {/* Boundary resize handles: grab either side of a column
                      line. workspace-no-drag + stopPropagation keep them from
                      sorting or dragging the widget. */}
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={`Resize ${prev ? prev.def.title : "symbol"} column`}
                    onPointerDown={(e) =>
                      prev
                        ? onResizePointerDown(
                            e,
                            prev.def.key,
                            prev.width,
                            prev.def.minWidth,
                          )
                        : onResizePointerDown(
                            e,
                            "symbol",
                            symbolWidth,
                            SYMBOL_MIN_WIDTH,
                          )
                    }
                    onPointerMove={onResizePointerMove}
                    onPointerUp={onResizePointerUp}
                    onClick={(e) => e.stopPropagation()}
                    className="workspace-no-drag absolute top-0 left-0 h-full w-[6px] cursor-col-resize touch-none hover:bg-[var(--border-strong)]"
                  />
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={`Resize ${col.def.title} column`}
                    onPointerDown={(e) =>
                      onResizePointerDown(
                        e,
                        col.def.key,
                        col.width,
                        col.def.minWidth,
                      )
                    }
                    onPointerMove={onResizePointerMove}
                    onPointerUp={onResizePointerUp}
                    onClick={(e) => e.stopPropagation()}
                    className="workspace-no-drag absolute top-0 right-0 h-full w-[6px] cursor-col-resize touch-none hover:bg-[var(--border-strong)]"
                  />
                </div>
              );
            })}
          </div>

          {config.adding ? (
            <WatchlistAddRow
              symbols={symbols}
              activeListId={displayList.id}
              densityClass={densityClass}
              symbolWidth={symbolWidth}
              widths={widths}
              onAdded={handleSymbolAdded}
              onClose={() => onConfigChange({ adding: false })}
              onPublish={publish}
            />
          ) : null}

          <div ref={scopeRef}>
          <Reorder.Group
            // Remount the motion list when this page becomes visible so the rows
            // mount fresh at their final position — otherwise the hidden→visible
            // page-position swap makes layout="position" slide them in from the
            // top-left on every switch.
            key={pageActive ? "shown" : "hidden"}
            axis="y"
            values={sortedSymbols}
            onReorder={setDragOrder}
            // select-none ALWAYS (not only while dragging): a click with a
            // 1px pointer wobble text-selects the row, and the browser paints
            // its native selection wash over the content but NOT the row
            // padding — the founder's recurring "blue row with a dark notch on
            // the left" artifact. Rows are controls (click = publish, drag =
            // reorder); text selection has no job here.
            className="m-0 list-none p-0 select-none"
          >
            {sortedSymbols.map((symbol) => (
              <WatchlistSymbolRow
                key={symbol}
                symbol={symbol}
                quote={enrichedQuotes[symbol]}
                position={positions[symbol]}
                columns={visibleColumns}
                symbolWidth={symbolWidth}
                widths={widths}
                contentMinWidth={contentMinWidth}
                densityClass={densityClass}
                isOvernight={overnightSymbols.has(symbol.toUpperCase())}
                linked={linked}
                selected={selectedSymbol === symbol}
                pinnable={pinnable}
                pinned={pinnedSet.has(symbol)}
                onTogglePin={() => void togglePin(symbol)}
                onPublish={() => publish(symbol)}
                onSelect={() => setSelectedSymbol(symbol)}
                onContextMenu={(e) =>
                  setMenu({ symbol, x: e.clientX, y: e.clientY })
                }
                onDragStart={handleDragStart}
                onReorderEnd={handleReorderEnd}
                onDragOutRelease={() => deleteFromDrag(symbol)}
                getWidgetRect={getWidgetRect}
                animateLayout={animateLayout}
                onRowNode={(el) => registerRow(symbol, el)}
                vtStyle={rowVtStyle(symbol)}
              />
            ))}
          </Reorder.Group>
          </div>
        </div>
      </div>

      {menu ? (
        <WatchlistContextMenu
          symbol={menu.symbol}
          x={menu.x}
          y={menu.y}
          pinnable={pinnable}
          pinned={pinnedSet.has(menu.symbol)}
          listRef={listRef}
          onDelete={() => void removeSymbol(menu.symbol)}
          onTogglePin={() => void togglePin(menu.symbol)}
          onClose={closeMenu}
        />
      ) : null}
    </div>
  );
}
