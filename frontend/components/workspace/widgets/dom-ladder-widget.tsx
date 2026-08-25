"use client";

/**
 * dom-ladder-widget — price ladder with click-to-trade.
 *
 * Data reality (Alpaca stocks = top-of-book only): the ladder is a price grid
 * around last, with the NBBO inside market marked with live sizes (shares),
 * session volume-at-price accumulated client-side from the live tape, and our
 * own working orders/position on their rows. There is no L2 depth to draw.
 *
 * Click semantics (ToS/SuperDOM consensus): click the BID column at/below
 * market = buy limit at that price; the ASK column at/above market = sell
 * limit. Wrong-side cells are inert until stop entries ship. Every click is
 * two-step (footer confirm) unless one-click is armed or SHIFT is held.
 * Clicking one of our order markers cancels it; dragging it moves it.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  WidgetEmpty,
  WidgetError,
} from "@/components/workspace/widget-states";
import type { Order } from "@/lib/api";
import {
  cancelManualOrder,
  closeManualPosition,
  fetchManualLimits,
  placeManualOrder,
  replaceManualOrder,
  snapPrice,
  verbText,
  type ManualLimits,
  type ManualVerbResult,
} from "@/lib/manual-trading-api";
import { useNbboQuote } from "@/lib/use-nbbo-quote";
import type { ChannelId } from "@/lib/workspace/channels";
import { useChannelSymbol } from "@/lib/workspace/use-channel-store";
import { useManualQtyStore } from "@/lib/workspace/use-manual-qty-store";
import { usePagePoll } from "@/lib/workspace/use-page-poll";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const ROW_PX = 20;
// buys/sells flex (markers justify toward the center), bid/ask sizes fixed
// next to the price column — kills the dead-space middle at wide widths.
const GRID_COLS = "grid-cols-[1fr_72px_minmax(64px,auto)_72px_1fr]";
const LIMITS_POLL_MS = 30_000;
const RECENTER_DRIFT = 0.25; // recenter when last drifts past this fraction of the window
const DEFAULT_PRESETS = [1, 5, 10, 25, 100];

type PendingAction =
  | { kind: "place"; side: "buy" | "sell"; price: string }
  | { kind: "cancel"; orderId: string; label: string }
  | { kind: "move"; orderId: string; price: string; label: string }
  | { kind: "flatten"; qty: number };

type Feedback = { tone: "ok" | "warn" | "err"; text: string } | null;

function tickFor(price: number): number {
  return price >= 1 ? 0.01 : 0.0001;
}

function levelKey(price: number, tick: number): number {
  return Math.round(price / tick);
}

export function DomLadderWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const configSymbol = config.symbol ?? "SPY";
  const symbol =
    channel !== "none" ? (channelSymbol ?? configSymbol) : configSymbol;

  const quote = useNbboQuote(symbol);
  const { snapshot, refresh, error: snapshotError } = useSnapshot();

  const [qty, setQty] = useState(config.ticketQty ?? "1");
  const [oneClick, setOneClick] = useState(false); // session-only, never persisted
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [busy, setBusy] = useState(false);
  const [limits, setLimits] = useState<ManualLimits | null>(null);

  const presets = config.ticketPresets ?? DEFAULT_PRESETS;

  // Publish the active qty for the chart's context-menu order items.
  const setSharedQty = useManualQtyStore((s) => s.setQty);
  useEffect(() => setSharedQty(qty), [qty, setSharedQty]);

  // ── Ladder geometry ────────────────────────────────────────────────────────
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [rowCount, setRowCount] = useState(30);
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setRowCount(Math.max(10, Math.floor(el.clientHeight / ROW_PX)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const last = quote.price ?? 0;
  const tick = tickFor(last || 1);
  // Center key: follows last until the user scrolls; recenters on drift.
  const [centerKey, setCenterKey] = useState<number | null>(null);
  const pointerInside = useRef(false);
  const lastKey = last > 0 ? levelKey(last, tick) : null;

  useEffect(() => {
    if (lastKey == null) return;
    setCenterKey((prev) => {
      if (prev == null) return lastKey;
      if (
        !pointerInside.current &&
        Math.abs(lastKey - prev) > rowCount * RECENTER_DRIFT
      ) {
        return lastKey;
      }
      return prev;
    });
  }, [lastKey, rowCount]);

  // Reset per symbol.
  const vapRef = useRef<Map<number, number>>(new Map());
  const vapBarRef = useRef<{ time: number; volume: number } | null>(null);
  const [vapVersion, setVapVersion] = useState(0);
  useEffect(() => {
    setCenterKey(null);
    vapRef.current = new Map();
    vapBarRef.current = null;
    setVapVersion((v) => v + 1);
    setPending(null);
    setFeedback(null);
  }, [symbol]);

  // ── Volume-at-price from the live forming bar (approximate: each volume
  //    increment lands on the CURRENT price level; minute-bar granularity) ──
  const forming = quote.formingBar;
  useEffect(() => {
    if (!forming || last <= 0) return;
    const prev = vapBarRef.current;
    let delta = 0;
    if (prev == null || prev.time !== forming.time) {
      delta = forming.volume ?? 0;
    } else {
      delta = Math.max(0, (forming.volume ?? 0) - prev.volume);
    }
    vapBarRef.current = { time: forming.time, volume: forming.volume ?? 0 };
    if (delta > 0) {
      const key = levelKey(last, tick);
      vapRef.current.set(key, (vapRef.current.get(key) ?? 0) + delta);
      setVapVersion((v) => v + 1);
    }
  }, [forming, last, tick]);

  // ── Our working orders / position for this symbol+account ─────────────────
  const accountId = config.ticketAccountId;
  const scoped = useMemo(() => {
    const list = snapshot?.snapshots ?? [];
    return (
      list.find((s) =>
        accountId ? s.account_id === accountId : s.is_default,
      ) ?? list.find((s) => s.money_type === "paper")
    );
  }, [snapshot, accountId]);

  const workingOrders: Order[] = useMemo(
    () =>
      (scoped?.open_orders ?? []).filter((o) => o.symbol === symbol),
    [scoped, symbol],
  );
  const position = useMemo(
    () =>
      (scoped?.positions ?? []).find(
        (p) => p.symbol === symbol && p.side === "long",
      ),
    [scoped, symbol],
  );

  const ordersByKey = useMemo(() => {
    const m = new Map<number, Order[]>();
    for (const o of workingOrders) {
      const price = o.limit_price ?? o.stop_price;
      if (price == null) continue;
      const key = levelKey(price, tick);
      const arr = m.get(key);
      if (arr) arr.push(o);
      else m.set(key, [o]);
    }
    return m;
  }, [workingOrders, tick]);

  const positionKey =
    position && position.avg_entry_price > 0
      ? levelKey(position.avg_entry_price, tick)
      : null;

  // No live quote yet (off-hours, gallery, socket warming): anchor the ladder
  // on the position's mark or a working order's price so it renders something.
  const fallbackAnchor =
    position?.current_price ??
    workingOrders.find((o) => o.limit_price != null)?.limit_price ??
    null;
  useEffect(() => {
    if (lastKey != null || fallbackAnchor == null || fallbackAnchor <= 0) {
      return;
    }
    setCenterKey((prev) =>
      prev == null ? levelKey(fallbackAnchor, tickFor(fallbackAnchor)) : prev,
    );
  }, [lastKey, fallbackAnchor]);

  const loadLimits = useCallback(async () => {
    try {
      setLimits(await fetchManualLimits(accountId));
    } catch {
      setLimits(null);
    }
  }, [accountId]);
  usePagePoll(loadLimits, LIMITS_POLL_MS);

  // ── Actions ───────────────────────────────────────────────────────────────
  const runAction = useCallback(
    async (action: PendingAction) => {
      setBusy(true);
      setFeedback(null);
      let result: ManualVerbResult;
      try {
        if (action.kind === "place") {
          result = await placeManualOrder({
            symbol,
            side: action.side,
            qty,
            order_type: "limit",
            limit_price: action.price,
            account_id: accountId,
          });
        } else if (action.kind === "cancel") {
          result = await cancelManualOrder(action.orderId);
        } else if (action.kind === "move") {
          result = await replaceManualOrder(action.orderId, action.price);
        } else {
          result = await closeManualPosition(symbol, accountId);
        }
      } catch (err) {
        result = { status: "error", reason: String(err) };
      }
      setBusy(false);
      setPending(null);
      const ok =
        result.status === "executed" || result.status === "cancel_sent";
      if (ok) {
        setFeedback({ tone: "ok", text: describe(action, symbol, qty) });
        refresh();
        void loadLimits();
      } else if (result.status === "forbidden") {
        setFeedback({ tone: "warn", text: verbText(result) });
      } else {
        setFeedback({ tone: "err", text: verbText(result) });
      }
    },
    [symbol, qty, accountId, refresh, loadLimits],
  );

  const requestAction = useCallback(
    (action: PendingAction, instant: boolean) => {
      if (busy) return;
      if (instant || oneClick) void runAction(action);
      else setPending(action);
    },
    [busy, oneClick, runAction],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPending(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── Order-marker drag (move an order to a new row) ────────────────────────
  const [drag, setDrag] = useState<{
    orderId: string;
    label: string;
    fromKey: number;
    toKey: number;
  } | null>(null);
  const dragRef = useRef(drag);
  dragRef.current = drag;

  const keyAtClientY = useCallback(
    (clientY: number): number | null => {
      const el = bodyRef.current;
      if (!el || centerKey == null) return null;
      const rect = el.getBoundingClientRect();
      const row = Math.floor((clientY - rect.top) / ROW_PX);
      const topKey = centerKey + Math.floor(rowCount / 2);
      return topKey - row;
    },
    [centerKey, rowCount],
  );

  useEffect(() => {
    if (!drag) return;
    const onMove = (event: PointerEvent) => {
      const key = keyAtClientY(event.clientY);
      if (key != null) {
        setDrag((d) => (d ? { ...d, toKey: key } : d));
      }
    };
    const onUp = () => {
      const d = dragRef.current;
      setDrag(null);
      if (d && d.toKey !== d.fromKey) {
        requestAction(
          {
            kind: "move",
            orderId: d.orderId,
            price: snapPrice(d.toKey * tick),
            label: d.label,
          },
          false,
        );
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [drag, keyAtClientY, requestAction, tick]);

  // ── Rows ──────────────────────────────────────────────────────────────────
  const bidKey = quote.bid ? levelKey(quote.bid, tick) : null;
  const askKey = quote.ask ? levelKey(quote.ask, tick) : null;

  const rows = useMemo(() => {
    if (centerKey == null) return [];
    const half = Math.floor(rowCount / 2);
    const topKey = centerKey + half;
    const out: number[] = [];
    for (let i = 0; i < rowCount; i++) out.push(topKey - i);
    return out;
  }, [centerKey, rowCount]);

  const maxVap = useMemo(() => {
    void vapVersion;
    let max = 0;
    for (const key of rows) {
      max = Math.max(max, vapRef.current.get(key) ?? 0);
    }
    return max;
  }, [rows, vapVersion]);

  const onWheel = useCallback(
    (event: React.WheelEvent) => {
      event.preventDefault();
      const steps = Math.sign(event.deltaY) * Math.max(1, rowCount >> 3);
      setCenterKey((prev) => (prev == null ? prev : prev - steps));
    },
    [rowCount],
  );

  if (!symbol) {
    return <WidgetEmpty message="Enter a symbol" />;
  }
  if (snapshotError && !snapshot) {
    return <WidgetError message="Can't load account snapshot" />;
  }

  const qtyNum = Number(qty);
  const qtyValid = Number.isFinite(qtyNum) && qtyNum > 0;

  return (
    <div className="workspace-no-drag flex h-full min-h-0 flex-col font-mono">
      {/* qty presets + arm toggle */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-[var(--line-divider)] px-2 py-1">
        <input
          className="h-5 w-14 border border-[var(--line-border)] bg-[var(--bg-inset)] px-1 text-right text-[length:var(--fs-body)] tabular-nums text-foreground outline-none focus:border-[var(--c-accent)]"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          onBlur={() => onConfigChange({ ticketQty: qty })}
          aria-label="order quantity"
          inputMode="decimal"
        />
        {presets.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => {
              setQty(String(p));
              onConfigChange({ ticketQty: String(p) });
            }}
            className="border border-[var(--line-border)] px-1 py-px text-[length:var(--fs-micro)] tabular-nums text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]"
          >
            {p}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setOneClick((v) => !v)}
          title="one-click mode: a single click places the order — no confirm"
          className="ml-auto border px-1.5 py-px text-[length:var(--fs-micro)] uppercase tracking-[0.08em]"
          style={{
            borderColor: oneClick ? "var(--c-amber)" : "var(--line-border)",
            color: oneClick ? "var(--c-amber)" : "var(--fg-faint)",
            background: oneClick
              ? "color-mix(in srgb, var(--c-amber) 10%, transparent)"
              : "transparent",
          }}
        >
          {oneClick ? "armed" : "confirm"}
        </button>
        <button
          type="button"
          onClick={() => lastKey != null && setCenterKey(lastKey)}
          title="recenter on last"
          className="border border-[var(--line-border)] px-1.5 py-px text-[length:var(--fs-micro)] uppercase text-[var(--fg-faint)] hover:bg-[var(--bg-hover)]"
        >
          c
        </button>
      </div>

      {/* column headers */}
      <div className={`grid shrink-0 ${GRID_COLS} border-b border-[var(--line-divider)] px-1 text-center text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-table-header)]`}>
        <span>buys</span>
        <span>bid</span>
        <span>price</span>
        <span>ask</span>
        <span>sells</span>
      </div>

      {/* ladder body */}
      <div
        ref={bodyRef}
        className="min-h-0 flex-1 overflow-hidden text-[length:var(--fs-table)] leading-[20px]"
        onWheel={onWheel}
        onPointerEnter={() => {
          pointerInside.current = true;
        }}
        onPointerLeave={() => {
          pointerInside.current = false;
        }}
      >
        {rows.length === 0 ? (
          <WidgetEmpty
            message="Waiting for quotes"
            hint={quote.live ? "no trades yet this session" : "quote stream connecting…"}
          />
        ) : null}
        {rows.map((key) => {
          const price = key * tick;
          const isBid = key === bidKey;
          const isAsk = key === askKey;
          const isLast = key === lastKey;
          const belowOrAtBid = bidKey != null && key <= bidKey;
          const aboveOrAtAsk = askKey != null && key >= askKey;
          const buyClickable = qtyValid && (bidKey == null || key <= bidKey);
          const sellClickable = qtyValid && (askKey == null || key >= askKey);
          const vap = vapRef.current.get(key) ?? 0;
          const orders = ordersByKey.get(key) ?? [];
          const buyOrders = orders.filter((o) => o.side === "buy");
          const sellOrders = orders.filter((o) => o.side === "sell");
          const dragTarget = drag && key === drag.toKey;

          return (
            <div
              key={key}
              className={`grid ${GRID_COLS} items-stretch text-center tabular-nums`}
              style={{
                height: ROW_PX,
                background: dragTarget
                  ? "var(--bg-hover)"
                  : isLast
                    ? "color-mix(in srgb, var(--c-accent) 8%, transparent)"
                    : undefined,
              }}
            >
              {/* my buys */}
              <OrderCell
                orders={buyOrders}
                side="buy"
                onCancel={(o, instant) =>
                  requestAction(
                    {
                      kind: "cancel",
                      orderId: o.id,
                      label: orderLabel(o),
                    },
                    instant,
                  )
                }
                onDragStart={(o) =>
                  setDrag({
                    orderId: o.id,
                    label: orderLabel(o),
                    fromKey: key,
                    toKey: key,
                  })
                }
              />
              {/* bid */}
              <button
                type="button"
                disabled={!buyClickable}
                title={
                  buyClickable
                    ? `buy ${qty} ${symbol} @ ${snapPrice(price)} lmt${oneClick ? "" : " — click, then confirm below (shift-click = instant)"}`
                    : "buy stops above market land later — click at or below the bid"
                }
                onClick={(e) =>
                  buyClickable &&
                  requestAction(
                    { kind: "place", side: "buy", price: snapPrice(price) },
                    e.shiftKey,
                  )
                }
                className="relative disabled:cursor-default"
                style={{
                  color: belowOrAtBid ? "var(--c-green)" : "var(--fg-faint)",
                  background: isBid
                    ? "color-mix(in srgb, var(--c-green) 12%, transparent)"
                    : undefined,
                  cursor: buyClickable ? "pointer" : undefined,
                }}
              >
                {isBid && quote.bidSize ? quote.bidSize.toLocaleString() : ""}
              </button>
              {/* price + VAP */}
              <div
                className="relative border-x border-[var(--line-divider)] px-2"
                style={{
                  color:
                    isBid || isAsk || isLast
                      ? "var(--fg)"
                      : "var(--fg-faint)",
                  fontWeight: isLast ? 600 : undefined,
                }}
              >
                {maxVap > 0 && vap > 0 ? (
                  <div
                    className="absolute inset-y-[3px] left-0"
                    aria-hidden
                    style={{
                      width: `${(vap / maxVap) * 100}%`,
                      background:
                        "color-mix(in srgb, var(--c-blue) 16%, transparent)",
                    }}
                  />
                ) : null}
                <span className="relative">
                  {snapPrice(price)}
                  {positionKey === key ? (
                    <span
                      title={`position ${position?.qty} @ ${snapPrice(position?.avg_entry_price ?? 0)}`}
                      className="ml-1 text-[var(--c-amber)]"
                    >
                      ●
                    </span>
                  ) : null}
                </span>
              </div>
              {/* ask */}
              <button
                type="button"
                disabled={!sellClickable}
                title={
                  sellClickable
                    ? `sell ${qty} ${symbol} @ ${snapPrice(price)} lmt${oneClick ? "" : " — click, then confirm below (shift-click = instant)"}`
                    : "sell stops below market land later — click at or above the ask"
                }
                onClick={(e) =>
                  sellClickable &&
                  requestAction(
                    { kind: "place", side: "sell", price: snapPrice(price) },
                    e.shiftKey,
                  )
                }
                className="relative disabled:cursor-default"
                style={{
                  color: aboveOrAtAsk ? "var(--c-red)" : "var(--fg-faint)",
                  background: isAsk
                    ? "color-mix(in srgb, var(--c-red) 10%, transparent)"
                    : undefined,
                  cursor: sellClickable ? "pointer" : undefined,
                }}
              >
                {isAsk && quote.askSize ? quote.askSize.toLocaleString() : ""}
              </button>
              {/* my sells */}
              <OrderCell
                orders={sellOrders}
                side="sell"
                onCancel={(o, instant) =>
                  requestAction(
                    {
                      kind: "cancel",
                      orderId: o.id,
                      label: orderLabel(o),
                    },
                    instant,
                  )
                }
                onDragStart={(o) =>
                  setDrag({
                    orderId: o.id,
                    label: orderLabel(o),
                    fromKey: key,
                    toKey: key,
                  })
                }
              />
            </div>
          );
        })}
      </div>

      {/* pending confirm */}
      {pending ? (
        <div className="flex shrink-0 items-center gap-2 border-t border-[var(--line-divider)] px-2 py-1 text-[length:var(--fs-body)]">
          <span className="text-[var(--fg-dim)]">
            {describe(pending, symbol, qty)}?
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runAction(pending)}
            className="border border-[var(--c-accent)] px-2 py-px uppercase tracking-[0.08em] text-[var(--c-accent)] hover:bg-[var(--bg-hover)] disabled:opacity-40"
          >
            confirm
          </button>
          <button
            type="button"
            onClick={() => setPending(null)}
            className="border border-[var(--line-border)] px-2 py-px uppercase tracking-[0.08em] text-[var(--fg-faint)] hover:bg-[var(--bg-hover)]"
          >
            esc
          </button>
        </div>
      ) : feedback ? (
        <div
          className="shrink-0 border-t border-[var(--line-divider)] px-2 py-1 text-[length:var(--fs-table)]"
          style={{
            color:
              feedback.tone === "ok"
                ? "var(--c-green)"
                : feedback.tone === "warn"
                  ? "var(--c-amber)"
                  : "var(--c-red)",
          }}
        >
          {feedback.text}
        </div>
      ) : null}

      {/* footer: position + limits */}
      <div className="flex shrink-0 items-center gap-3 border-t border-[var(--line-divider)] px-2 py-1 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
        {position ? (
          <>
            <span className="tabular-nums text-[var(--fg-dim)]">
              {position.qty} @ {snapPrice(position.avg_entry_price)}
            </span>
            <span
              className="tabular-nums"
              style={{
                color:
                  position.unrealized_pl >= 0
                    ? "var(--c-green)"
                    : "var(--c-red)",
              }}
            >
              {position.unrealized_pl >= 0 ? "+" : ""}
              {position.unrealized_pl.toFixed(0)}
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={(e) =>
                requestAction(
                  { kind: "flatten", qty: position.qty },
                  e.shiftKey,
                )
              }
              title="market-close the position and cancel its working orders"
              className="border border-[var(--line-border)] px-1.5 py-px uppercase text-[var(--fg-dim)] hover:border-[var(--c-red)] hover:text-[var(--c-red)] disabled:opacity-40"
            >
              flatten
            </button>
          </>
        ) : (
          <span>flat</span>
        )}
        {!quote.live ? (
          <span
            className="text-[var(--c-amber)]"
            title="live quote stream disconnected — ladder may be stale"
          >
            stream offline
          </span>
        ) : null}
        <span className="ml-auto tabular-nums">
          {limits
            ? `${limits.orders_today}/${limits.max_daily_orders} today${
                limits.kill_switch_active
                  ? " · kill switch"
                  : limits.market_open
                    ? ""
                    : " · closed"
              }${limits.admin_unlocked ? "" : " · locked"}`
            : ""}
        </span>
      </div>
    </div>
  );
}

function orderLabel(o: Order): string {
  const price = o.limit_price ?? o.stop_price;
  return `${o.side} ${o.qty - (o.filled_qty || 0)} @ ${
    price != null ? snapPrice(price) : "mkt"
  }`;
}

function describe(action: PendingAction, symbol: string, qty: string): string {
  if (action.kind === "place") {
    return `${action.side} ${qty} ${symbol} @ ${action.price} lmt`;
  }
  if (action.kind === "cancel") return `cancel ${action.label}`;
  if (action.kind === "move") {
    return `move ${action.label} → ${action.price}`;
  }
  return `flatten ${symbol} (${action.qty})`;
}

function OrderCell({
  orders,
  side,
  onCancel,
  onDragStart,
}: {
  orders: Order[];
  side: "buy" | "sell";
  onCancel: (order: Order, instant: boolean) => void;
  onDragStart: (order: Order) => void;
}) {
  if (orders.length === 0) return <span aria-hidden />;
  const color = side === "buy" ? "var(--c-green)" : "var(--c-red)";
  const total = orders.reduce(
    (sum, o) => sum + (o.qty - (o.filled_qty || 0)),
    0,
  );
  const isStop = orders.every((o) => o.order_type === "stop");
  if (isStop) {
    // Not a control — the guard owns it. Plain colored word, no box.
    return (
      <span
        title="protective stop — managed by the stop guard"
        className={`my-[3px] text-[length:var(--fs-micro)] tabular-nums leading-[12px] text-[var(--c-amber)] ${
          side === "buy" ? "mr-1 justify-self-end" : "ml-1 justify-self-start"
        }`}
      >
        stop {total}
      </span>
    );
  }
  return (
    <button
      type="button"
      title={`${orders.map(orderLabel).join(", ")} — click to cancel, drag to move`}
      onClick={(e) => onCancel(orders[0], e.shiftKey)}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        // A quick click cancels; a hold-and-move drags. Arm the drag on a
        // small delay so click stays the primary gesture.
        const order = orders[0];
        const timer = setTimeout(() => onDragStart(order), 180);
        const clear = () => clearTimeout(timer);
        e.currentTarget.addEventListener("pointerup", clear, { once: true });
        e.currentTarget.addEventListener("pointerleave", clear, {
          once: true,
        });
      }}
      className={`my-[3px] min-w-10 border px-1.5 text-[length:var(--fs-micro)] tabular-nums leading-[12px] disabled:opacity-70 ${
        side === "buy" ? "mr-1 justify-self-end" : "ml-1 justify-self-start"
      }`}
      style={{
        borderColor: color,
        color,
        background: `color-mix(in srgb, ${color} 10%, transparent)`,
      }}
    >
      {total}
    </button>
  );
}
