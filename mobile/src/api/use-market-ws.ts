import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { wsUrl } from '@/config';

import type { FeedNotification, MarketAlert } from './types';

const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 10_000;
// Ticks are coalesced per symbol and flushed on this cadence. Measured 2026-07-27
// against the real hub: a 40-symbol watchlist arrives at ~132 frames/sec (the server
// throttles to ~10 Hz PER SYMBOL, so the per-client rate scales with list length).
// Delivering those straight through meant ~132 react-query cache writes a second on
// the JS thread, each cloning the whole quote map. Latest-wins per symbol at 4 Hz
// keeps the displayed price identical while cutting cache writes ~30x.
const QUOTE_FLUSH_MS = 250;

type MarketSession = 'pre' | 'regular' | 'post' | 'overnight' | 'closed';

export interface QuoteUpdate {
  symbol: string;
  price: number;
  change_pct: number;
  sparkline?: number[];
  // Extended-hours (serv now folds these into the stream — present pre/post, null in regular):
  ext_price?: number | null;
  ext_change_pct?: number | null;
  market_session?: MarketSession;
}

export interface UseMarketWsOptions {
  token: string | null;
  onAlert?: (alert: MarketAlert) => void;
  onNotification?: (notification: FeedNotification) => void;
  /** Coalesced batch — latest value per symbol, at most one call per QUOTE_FLUSH_MS. */
  onQuotes?: (quotes: QuoteUpdate[]) => void;
  onProposal?: (proposalId: string) => void;
}

interface WsQuoteMessage {
  type: 'quote';
  symbol?: string;
  price?: number;
  change_pct?: number;
  sparkline?: number[];
  ext_price?: number | null;
  ext_change_pct?: number | null;
  market_session?: MarketSession;
}

/** Bulk snapshot. serv sends one the instant a socket is accepted, and again on
 * every `subscribe`. The app used to have no branch for it and dropped it on the
 * floor, so a reconnect showed the pre-drop prices until each symbol happened to
 * trade again — the whole point of the snapshot is to close exactly that gap. */
interface WsQuotesMessage {
  type: 'quotes';
  quotes?: Record<string, Omit<WsQuoteMessage, 'type' | 'symbol'>>;
}

interface WsAlertMessage {
  type: 'alert';
  id?: string;
  ts?: string;
  source?: string;
  rule_id?: string | null;
  rule_name?: string | null;
  action?: MarketAlert['action'];
  symbol?: string | null;
  price?: number | null;
  // Price-alert fields (source === 'price_alert'):
  alert_id?: number | null;
  target_price?: number | null;
  trigger_price?: number | null;
  direction?: string | null;
  note?: string | null;
  // Defensive: older serv builds nested the payload under `alert`. serv now sends
  // it flat, but flatten a nested object if one ever shows up so it can't render blank.
  alert?: Partial<WsAlertMessage>;
}

interface WsNotificationMessage {
  type: 'notification';
  id?: string;
  kind?: string;
  title?: string;
  body?: string;
  severity?: string;
  account_id?: string | null;
  strategy_id?: string | null;
  ref_type?: string | null;
  ref_id?: string | null;
  action_url?: string | null;
  group_key?: string | null;
  data?: Record<string, unknown>;
  created_at?: string;
  read_at?: string | null;
  dismissed_at?: string | null;
}

type WsMessage =
  | WsQuoteMessage
  | WsQuotesMessage
  | WsAlertMessage
  | WsNotificationMessage
  | Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function parseAlert(raw: WsAlertMessage): MarketAlert {
  // Flatten a legacy nested `alert` payload (top-level wins where both exist).
  const msg: WsAlertMessage = raw.alert ? { ...raw.alert, ...raw, alert: undefined } : raw;
  return {
    id: msg.id ?? `alert-${Date.now()}`,
    ts: msg.ts ?? new Date().toISOString(),
    source: msg.source,
    rule_id: msg.rule_id,
    rule_name: msg.rule_name,
    action: msg.action,
    symbol: msg.symbol,
    // Price alerts carry trigger/target, not a single `price`; fall back to trigger.
    price: msg.price ?? msg.trigger_price ?? null,
    alert_id: msg.alert_id,
    target_price: msg.target_price,
    trigger_price: msg.trigger_price,
    direction: msg.direction,
    note: msg.note,
  };
}

function parseNotification(raw: WsNotificationMessage): FeedNotification | null {
  if (typeof raw.id !== 'string' || typeof raw.kind !== 'string') {
    return null;
  }
  return {
    id: raw.id,
    kind: raw.kind,
    title: raw.title ?? '',
    body: raw.body ?? '',
    severity: raw.severity ?? 'info',
    account_id: raw.account_id ?? null,
    strategy_id: raw.strategy_id ?? null,
    ref_type: raw.ref_type ?? null,
    ref_id: raw.ref_id ?? null,
    action_url: raw.action_url ?? null,
    group_key: raw.group_key ?? null,
    data: raw.data ?? {},
    created_at: raw.created_at ?? new Date().toISOString(),
    read_at: raw.read_at ?? null,
    dismissed_at: raw.dismissed_at ?? null,
  };
}

/**
 * Resilient WebSocket hook for /ws/market.
 * Auto-reconnects with exponential backoff; pauses when token is null OR the app
 * is backgrounded. Holding the quote stream open behind the lock screen keeps the
 * JS thread + radio awake on every tick — a real battery/heat drain during market
 * hours — so we close on background and reconnect fresh on foreground.
 */
export function useMarketWs({
  token,
  onAlert,
  onNotification,
  onQuotes,
  onProposal,
}: UseMarketWsOptions) {
  const [connected, setConnected] = useState(false);
  const [appActive, setAppActive] = useState(() => AppState.currentState !== 'background');

  useEffect(() => {
    const sub = AppState.addEventListener('change', (state) => {
      setAppActive(state !== 'background');
    });
    return () => sub.remove();
  }, []);

  const onAlertRef = useRef(onAlert);
  const onNotificationRef = useRef(onNotification);
  const onQuotesRef = useRef(onQuotes);
  const onProposalRef = useRef(onProposal);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(INITIAL_BACKOFF_MS);
  // symbol -> latest tick seen since the last flush.
  const pendingQuotesRef = useRef(new Map<string, QuoteUpdate>());
  // Last requested subscription, replayed on every open so a reconnect can never
  // leave us connected-but-silent.
  const lastSymbolsRef = useRef<string[]>([]);

  useEffect(() => {
    onAlertRef.current = onAlert;
    onNotificationRef.current = onNotification;
    onQuotesRef.current = onQuotes;
    onProposalRef.current = onProposal;
  }, [onAlert, onNotification, onQuotes, onProposal]);

  // Flush loop lives outside the connection effect on purpose: it must survive
  // reconnects, and a socket that drops mid-window still gets its last ticks out.
  useEffect(() => {
    const timer = setInterval(() => {
      const pending = pendingQuotesRef.current;
      if (pending.size === 0) {
        return;
      }
      const batch = Array.from(pending.values());
      pending.clear();
      onQuotesRef.current?.(batch);
    }, QUOTE_FLUSH_MS);
    return () => clearInterval(timer);
  }, []);

  const subscribe = useCallback((symbols: string[]) => {
    // Remember it either way. serv sends a client NOTHING until it subscribes —
    // an unsubscribed socket gets one snapshot and then silence forever, with no
    // error and no disconnect — so a request that arrives while the socket is
    // still CONNECTING must not be dropped on the floor. `onopen` replays this.
    lastSymbolsRef.current = symbols;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.send(JSON.stringify({ action: 'subscribe', symbols }));
  }, []);

  useEffect(() => {
    if (!token || !appActive) {
      setConnected(false);
      return;
    }

    const authToken = token;

    // Fresh token (e.g. re-auth) starts a fresh backoff, not whatever the
    // previous connection's retry loop left behind.
    backoffRef.current = INITIAL_BACKOFF_MS;

    let cancelled = false;

    function clearReconnectTimer() {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    }

    function scheduleReconnect() {
      if (cancelled) {
        return;
      }
      clearReconnectTimer();
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
      reconnectTimerRef.current = setTimeout(connect, delay);
    }

    function bufferQuote(symbol: string, payload: Omit<WsQuoteMessage, 'type' | 'symbol'>) {
      // Latest wins: a symbol ticking 10x inside one flush window costs one entry,
      // not ten renders. Same field mapping the single-quote path always used.
      pendingQuotesRef.current.set(symbol, {
        symbol,
        price: payload.price ?? 0,
        change_pct: payload.change_pct ?? 0,
        sparkline: payload.sparkline,
        ext_price: payload.ext_price,
        ext_change_pct: payload.ext_change_pct,
        market_session: payload.market_session,
      });
    }

    function handleMessage(event: MessageEvent<string>) {
      try {
        const msg = JSON.parse(event.data) as WsMessage;

        if (isRecord(msg) && msg.type === 'alert') {
          const raw = msg as unknown as WsAlertMessage & { kind?: unknown; type?: unknown };
          // Serv mirrors notifications into the alert buffer; the notification stream renders them.
          if (
            typeof raw.kind === 'string'
            || (raw.alert && typeof raw.alert === 'object' && (raw.alert as { type?: unknown }).type === 'notification')
          ) {
            return;
          }
          const alert = parseAlert(raw);
          onAlertRef.current?.(alert);

          const action = alert.action;
          if (
            action?.type === 'order_proposal' &&
            typeof action.proposal_id === 'string' &&
            action.proposal_id
          ) {
            onProposalRef.current?.(action.proposal_id);
          }
          return;
        }

        if (isRecord(msg) && msg.type === 'notification') {
          const notification = parseNotification(msg as unknown as WsNotificationMessage);
          if (notification) {
            onNotificationRef.current?.(notification);
          }
          return;
        }

        if (isRecord(msg) && msg.type === 'quote') {
          const quoteMsg = msg as unknown as WsQuoteMessage;
          if (typeof quoteMsg.symbol !== 'string') {
            return;
          }
          bufferQuote(quoteMsg.symbol, quoteMsg);
          return;
        }

        if (isRecord(msg) && msg.type === 'quotes') {
          const bulk = (msg as unknown as WsQuotesMessage).quotes;
          if (!isRecord(bulk)) {
            return;
          }
          for (const [symbol, payload] of Object.entries(bulk)) {
            if (isRecord(payload)) {
              bufferQuote(symbol, payload);
            }
          }
        }
      } catch {
        // Ignore malformed frames.
      }
    }

    function connect() {
      if (cancelled) {
        return;
      }

      clearReconnectTimer();
      // Detach the outgoing socket's handlers BEFORE closing it. Closing an open
      // socket fires its `onclose` asynchronously — often after the replacement is
      // already open — and that handler both flips `connected` back to false and
      // schedules ANOTHER reconnect, which then closes the healthy socket a second
      // later. That is a self-sustaining loop: it leaves `connected` false most of
      // the time, so the re-subscribe effect never fires, so the server has us down
      // as a client with an empty symbol set and sends nothing. Silent frozen prices.
      const previous = wsRef.current;
      if (previous) {
        previous.onopen = null;
        previous.onmessage = null;
        previous.onerror = null;
        previous.onclose = null;
        previous.close();
      }

      const ws = new WebSocket(wsUrl(authToken));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled || wsRef.current !== ws) {
          ws.close();
          return;
        }
        backoffRef.current = INITIAL_BACKOFF_MS;
        // Re-arm the subscription here rather than waiting for the consumer's
        // effect to notice `connected` flipped: React can batch a close and the
        // following open into one render, in which case the boolean never
        // visibly changes and the effect never runs.
        if (lastSymbolsRef.current.length > 0) {
          ws.send(JSON.stringify({ action: 'subscribe', symbols: lastSymbolsRef.current }));
        }
        setConnected(true);
      };

      ws.onmessage = handleMessage;

      ws.onerror = () => {
        ws.close();
      };

      ws.onclose = () => {
        // Belt-and-braces with the detach above: only the CURRENT socket may
        // report the connection down or ask for a retry. A superseded one
        // speaks for a connection nobody is using any more.
        if (cancelled || wsRef.current !== ws) {
          return;
        }
        setConnected(false);
        scheduleReconnect();
      };
    }

    connect();

    return () => {
      cancelled = true;
      clearReconnectTimer();
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [token, appActive]);

  return { connected, subscribe };
}
