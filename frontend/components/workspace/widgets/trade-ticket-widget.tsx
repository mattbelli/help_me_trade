"use client";

/**
 * trade-ticket-widget — the manual order surface.
 *
 * Every submit rides POST /api/manual/orders: proposal → RiskEngine → the one
 * chokepoint, paper-only, admin-token-gated. The button is two-step armed by
 * default; SHIFT-click sends in one step. A refusal renders the server's
 * reason verbatim — the RiskEngine's words, never rewritten.
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
  WidgetError,
} from "@/components/workspace/widget-states";
import {
  fetchLabAccounts,
  type LabAccount,
  type Position,
} from "@/lib/api";
import { label } from "@/lib/labels";
import {
  fetchManualLimits,
  placeManualOrder,
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

const DEFAULT_PRESETS = [1, 5, 10, 25, 100];
const ARM_TIMEOUT_MS = 5_000;
const LIMITS_POLL_MS = 30_000;

const inputCls =
  "h-6 border border-[var(--line-border)] bg-[var(--bg-inset)] px-1.5 " +
  "font-mono text-[length:var(--fs-body)] text-foreground outline-none " +
  "focus:border-[var(--c-accent)]";

const selectCls =
  "h-6 border border-[var(--line-border)] bg-[var(--bg-inset)] px-1 " +
  "font-mono text-[length:var(--fs-body)] text-[var(--fg-dim)] outline-none " +
  "focus:border-[var(--c-accent)]";

type Feedback =
  | { tone: "ok" | "warn" | "err"; text: string }
  | null;

function fmtUsd(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

export function TradeTicketWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const channelSymbol = useChannelSymbol(channel);
  const configSymbol = config.symbol ?? "SPY";
  const symbol =
    channel !== "none" ? (channelSymbol ?? configSymbol) : configSymbol;

  const [symbolDraft, setSymbolDraft] = useState(symbol);
  useEffect(() => setSymbolDraft(symbol), [symbol]);

  const quote = useNbboQuote(symbol);
  const { snapshot, refresh } = useSnapshot();

  const [accounts, setAccounts] = useState<LabAccount[]>([]);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const accountId =
    config.ticketAccountId ??
    accounts.find((a) => a.is_default)?.id ??
    accounts[0]?.id;

  const [limits, setLimits] = useState<ManualLimits | null>(null);

  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState(config.ticketQty ?? "1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState<"day" | "gtc">("day");
  const [extHours, setExtHours] = useState(false);

  const [armed, setArmed] = useState(false);
  const [sending, setSending] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const presets = config.ticketPresets ?? DEFAULT_PRESETS;

  // Publish the active qty for the chart's context-menu order items.
  const setSharedQty = useManualQtyStore((s) => s.setQty);
  useEffect(() => setSharedQty(qty), [qty, setSharedQty]);

  useEffect(() => {
    let cancelled = false;
    fetchLabAccounts()
      .then((rows) => {
        if (!cancelled) {
          setAccounts(rows);
          setAccountsError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setAccountsError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadLimits = useCallback(async () => {
    try {
      setLimits(await fetchManualLimits(accountId));
    } catch {
      // The strip goes quiet rather than blocking the ticket.
      setLimits(null);
    }
  }, [accountId]);
  usePagePoll(loadLimits, LIMITS_POLL_MS);

  // Disarm on symbol / side / qty / price changes — the armed label may no
  // longer describe the order that would send.
  useEffect(() => {
    setArmed(false);
  }, [symbol, side, qty, orderType, limitPrice, tif, extHours, accountId]);

  useEffect(() => {
    if (!armed) return;
    armTimer.current = setTimeout(() => setArmed(false), ARM_TIMEOUT_MS);
    return () => {
      if (armTimer.current) clearTimeout(armTimer.current);
    };
  }, [armed]);

  const position: Position | undefined = useMemo(() => {
    const scoped = snapshot?.snapshots?.find(
      (s) => s.account_id === accountId,
    );
    return (scoped?.positions ?? []).find((p) => p.symbol === symbol);
  }, [snapshot, accountId, symbol]);

  const qtyNum = Number(qty);
  const priceForNotional =
    orderType === "limit" ? Number(limitPrice) : (quote.price ?? 0);
  const estNotional =
    Number.isFinite(qtyNum) && qtyNum > 0 && priceForNotional > 0
      ? qtyNum * priceForNotional
      : null;

  const qtyValid = Number.isFinite(qtyNum) && qtyNum > 0;
  const priceValid =
    orderType === "market" ||
    (Number.isFinite(Number(limitPrice)) && Number(limitPrice) > 0);
  const ready = Boolean(symbol) && qtyValid && priceValid && !sending;

  const send = useCallback(async () => {
    if (!ready) return;
    setSending(true);
    setArmed(false);
    setFeedback(null);
    let result: ManualVerbResult;
    try {
      result = await placeManualOrder({
        symbol,
        side,
        qty,
        order_type: orderType,
        limit_price: orderType === "limit" ? limitPrice : undefined,
        account_id: accountId,
        time_in_force: tif,
        extended_hours: extHours || undefined,
      });
    } catch (err) {
      result = { status: "error", reason: String(err) };
    }
    setSending(false);
    if (result.status === "executed") {
      setFeedback({
        tone: "ok",
        text: `sent — ${side} ${qty} ${symbol}${
          orderType === "limit" ? ` @ ${limitPrice}` : " mkt"
        }`,
      });
      refresh();
      void loadLimits();
    } else if (result.status === "forbidden") {
      setFeedback({ tone: "warn", text: verbText(result) });
    } else {
      setFeedback({ tone: "err", text: verbText(result) });
    }
  }, [
    ready, symbol, side, qty, orderType, limitPrice, accountId, tif,
    extHours, refresh, loadLimits,
  ]);

  const onSubmitClick = useCallback(
    (event: React.MouseEvent) => {
      if (!ready) return;
      if (event.shiftKey || armed) {
        void send();
      } else {
        setArmed(true);
      }
    },
    [ready, armed, send],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setArmed(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const commitSymbol = useCallback(() => {
    const next = symbolDraft.trim().toUpperCase();
    if (!next || next === symbol) {
      setSymbolDraft(symbol);
      return;
    }
    onConfigChange({ symbol: next });
  }, [symbolDraft, symbol, onConfigChange]);

  if (accountsError) {
    return <WidgetError message="Can't load trading accounts" />;
  }

  const sideColor = side === "buy" ? "var(--c-green)" : "var(--c-red)";
  const submitLabel = sending
    ? "sending…"
    : armed
      ? `confirm ${side} ${qty} ${symbol}${
          orderType === "limit" ? ` @ ${limitPrice || "?"}` : ""
        }`
      : `${side} ${symbol}`;

  return (
    <div className="workspace-no-drag flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-2 font-mono">
      {/* symbol + book */}
      <div className="flex items-center gap-2">
        <input
          className={`${inputCls} w-20 uppercase`}
          value={symbolDraft}
          onChange={(e) => setSymbolDraft(e.target.value)}
          onBlur={commitSymbol}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitSymbol();
          }}
          aria-label="symbol"
          spellCheck={false}
        />
        <div className="flex items-baseline gap-2 text-[length:var(--fs-body)] tabular-nums">
          <button
            type="button"
            className="text-[var(--c-green)]"
            title="bid — click to set limit price"
            onClick={() => {
              if (quote.bid) {
                setOrderType("limit");
                setLimitPrice(snapPrice(quote.bid));
              }
            }}
          >
            {quote.bid ? snapPrice(quote.bid) : "—"}
            {quote.bidSize ? (
              <span className="ml-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                ×{quote.bidSize}
              </span>
            ) : null}
          </button>
          <span className="text-[var(--fg-faint)]">/</span>
          <button
            type="button"
            className="text-[var(--c-red)]"
            title="ask — click to set limit price"
            onClick={() => {
              if (quote.ask) {
                setOrderType("limit");
                setLimitPrice(snapPrice(quote.ask));
              }
            }}
          >
            {quote.ask ? snapPrice(quote.ask) : "—"}
            {quote.askSize ? (
              <span className="ml-1 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
                ×{quote.askSize}
              </span>
            ) : null}
          </button>
        </div>
        <div className="ml-auto text-right text-[length:var(--fs-body)] tabular-nums">
          <span className="text-foreground">
            {quote.price ? snapPrice(quote.price) : "—"}
          </span>
          {quote.changePct != null ? (
            <span
              className="ml-1.5 text-[length:var(--fs-table)]"
              style={{
                color:
                  quote.changePct >= 0 ? "var(--c-green)" : "var(--c-red)",
              }}
            >
              {quote.changePct >= 0 ? "+" : ""}
              {quote.changePct.toFixed(2)}%
            </span>
          ) : null}
        </div>
      </div>

      {/* side / qty / presets */}
      <div className="flex items-center gap-2">
        <div className="flex">
          {(["buy", "sell"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSide(s)}
              className="border px-2 py-0.5 text-[length:var(--fs-body)] uppercase tracking-[0.08em] first:border-r-0"
              style={{
                borderColor:
                  side === s
                    ? s === "buy"
                      ? "var(--c-green)"
                      : "var(--c-red)"
                    : "var(--line-border)",
                color:
                  side === s
                    ? s === "buy"
                      ? "var(--c-green)"
                      : "var(--c-red)"
                    : "var(--fg-faint)",
                background: side === s ? "var(--bg-inset)" : "transparent",
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <input
          className={`${inputCls} w-16 text-right tabular-nums`}
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          onBlur={() => onConfigChange({ ticketQty: qty })}
          aria-label="quantity"
          inputMode="decimal"
        />
        <div className="flex gap-1">
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => {
                setQty(String(p));
                onConfigChange({ ticketQty: String(p) });
              }}
              className="border border-[var(--line-border)] px-1.5 py-0.5 text-[length:var(--fs-table)] tabular-nums text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]"
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* type / price / tif */}
      <div className="flex items-center gap-2">
        <select
          className={selectCls}
          value={orderType}
          onChange={(e) =>
            setOrderType(e.target.value as "market" | "limit")
          }
          aria-label="order type"
        >
          <option value="market">market</option>
          <option value="limit">limit</option>
        </select>
        {orderType === "limit" ? (
          <div className="flex items-center gap-1">
            <input
              className={`${inputCls} w-20 text-right tabular-nums`}
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder={quote.price ? snapPrice(quote.price) : ""}
              aria-label="limit price"
              inputMode="decimal"
            />
            <div className="flex flex-col">
              {(["+", "-"] as const).map((dir) => (
                <button
                  key={dir}
                  type="button"
                  className="px-1 text-[length:var(--fs-micro)] leading-[8px] text-[var(--fg-faint)] hover:text-foreground"
                  onClick={() => {
                    const base =
                      Number(limitPrice) || quote.price || 0;
                    if (!base) return;
                    const tick = base >= 1 ? 0.01 : 0.0001;
                    setLimitPrice(
                      snapPrice(base + (dir === "+" ? tick : -tick)),
                    );
                  }}
                >
                  {dir}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <select
          className={selectCls}
          value={tif}
          onChange={(e) => setTif(e.target.value as "day" | "gtc")}
          aria-label="time in force"
          disabled={orderType === "market"}
          title={
            orderType === "market" ? "market orders are day-only" : undefined
          }
        >
          <option value="day">day</option>
          <option value="gtc">gtc</option>
        </select>
        {orderType === "limit" ? (
          <label className="flex items-center gap-1 text-[length:var(--fs-table)] text-[var(--fg-faint)]">
            <input
              type="checkbox"
              checked={extHours}
              onChange={(e) => setExtHours(e.target.checked)}
            />
            ext
          </label>
        ) : null}
      </div>

      {/* account / notional / position */}
      <div className="flex items-center gap-2 text-[length:var(--fs-table)]">
        <select
          className={selectCls}
          value={accountId ?? ""}
          onChange={(e) =>
            onConfigChange({ ticketAccountId: e.target.value })
          }
          aria-label="account"
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
            </option>
          ))}
        </select>
        <span className="text-[var(--fg-faint)]">
          est{" "}
          <span className="tabular-nums text-[var(--fg-dim)]">
            {estNotional != null ? fmtUsd(estNotional) : "—"}
          </span>
        </span>
        <span className="ml-auto text-[var(--fg-faint)]">
          {position
            ? `${position.qty} @ ${snapPrice(position.avg_entry_price)}`
            : "flat"}
        </span>
      </div>

      {/* submit */}
      <button
        type="button"
        disabled={!ready}
        onClick={onSubmitClick}
        title="click twice to send — shift-click sends immediately"
        className="w-full border py-1.5 text-[length:var(--fs-title)] uppercase tracking-[0.1em] transition-colors disabled:opacity-40"
        style={{
          borderColor: sideColor,
          color: armed ? "var(--text-on-accent)" : sideColor,
          background: armed
            ? sideColor
            : "color-mix(in srgb, " + sideColor + " 8%, transparent)",
        }}
      >
        {submitLabel}
      </button>

      {feedback ? (
        <div
          className="text-[length:var(--fs-table)]"
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

      {/* limits strip */}
      <div className="mt-auto flex items-center gap-3 border-t border-[var(--line-divider)] pt-1.5 text-[length:var(--fs-micro)] uppercase tracking-[0.08em] text-[var(--fg-faint)]">
        {limits ? (
          <>
            <span
              title="orders today vs the daily cap"
              className="tabular-nums"
              style={{
                color:
                  limits.orders_today >= limits.max_daily_orders
                    ? "var(--c-red)"
                    : undefined,
              }}
            >
              {limits.orders_today}/{limits.max_daily_orders} today
            </span>
            <span title="open orders vs the cap" className="tabular-nums">
              {limits.open_orders}/{limits.max_open_orders} open
            </span>
            {limits.kill_switch_active ? (
              <span className="text-[var(--c-red)]">kill switch</span>
            ) : limits.entries_halted ? (
              <span className="text-[var(--c-amber)]">entries halted</span>
            ) : null}
            {!quote.live ? (
              <span className="text-[var(--c-amber)]" title="live quote stream disconnected — prices may be stale">
                stream offline
              </span>
            ) : null}
            <span className="ml-auto">
              {limits.market_open ? "rth" : "closed"} ·{" "}
              {label("execution_mode", limits.execution_mode)}
              {!limits.admin_unlocked ? " · locked" : ""}
            </span>
          </>
        ) : (
          <span>limits unavailable</span>
        )}
      </div>
    </div>
  );
}
