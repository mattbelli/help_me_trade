import { wsUrl } from '@/config';
import { useCallback, useEffect, useRef, useState } from 'react';

import type { MarketAlert } from './types';

const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 10_000;

export interface QuoteUpdate {
  symbol: string;
  price: number;
  change_pct: number;
  sparkline?: number[];
}

export interface UseMarketWsOptions {
  token: string | null;
  onAlert?: (alert: MarketAlert) => void;
  onQuote?: (quote: QuoteUpdate) => void;
  onProposal?: (proposalId: string) => void;
}

interface WsQuoteMessage {
  type: 'quote';
  symbol?: string;
  price?: number;
  change_pct?: number;
  sparkline?: number[];
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
}

type WsMessage = WsQuoteMessage | WsAlertMessage | Record<string, unknown>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function parseAlert(msg: WsAlertMessage): MarketAlert {
  return {
    id: msg.id ?? `alert-${Date.now()}`,
    ts: msg.ts ?? new Date().toISOString(),
    source: msg.source,
    rule_id: msg.rule_id,
    rule_name: msg.rule_name,
    action: msg.action,
    symbol: msg.symbol,
    price: msg.price,
  };
}

/**
 * Resilient WebSocket hook for /ws/market.
 * Auto-reconnects with exponential backoff; pauses when token is null.
 */
export function useMarketWs({
  token,
  onAlert,
  onQuote,
  onProposal,
}: UseMarketWsOptions) {
  const [connected, setConnected] = useState(false);

  const onAlertRef = useRef(onAlert);
  const onQuoteRef = useRef(onQuote);
  const onProposalRef = useRef(onProposal);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(INITIAL_BACKOFF_MS);

  useEffect(() => {
    onAlertRef.current = onAlert;
    onQuoteRef.current = onQuote;
    onProposalRef.current = onProposal;
  }, [onAlert, onQuote, onProposal]);

  const subscribe = useCallback((symbols: string[]) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }
    ws.send(JSON.stringify({ action: 'subscribe', symbols }));
  }, []);

  useEffect(() => {
    if (!token) {
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

    function handleMessage(event: MessageEvent<string>) {
      try {
        const msg = JSON.parse(event.data) as WsMessage;

        if (isRecord(msg) && msg.type === 'alert') {
          const alert = parseAlert(msg as unknown as WsAlertMessage);
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

        if (isRecord(msg) && msg.type === 'quote') {
          const quoteMsg = msg as unknown as WsQuoteMessage;
          if (typeof quoteMsg.symbol !== 'string') {
            return;
          }
          onQuoteRef.current?.({
            symbol: quoteMsg.symbol,
            price: quoteMsg.price ?? 0,
            change_pct: quoteMsg.change_pct ?? 0,
            sparkline: quoteMsg.sparkline,
          });
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
      wsRef.current?.close();

      const ws = new WebSocket(wsUrl(authToken));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) {
          ws.close();
          return;
        }
        backoffRef.current = INITIAL_BACKOFF_MS;
        setConnected(true);
      };

      ws.onmessage = handleMessage;

      ws.onerror = () => {
        ws.close();
      };

      ws.onclose = () => {
        if (cancelled) {
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
  }, [token]);

  return { connected, subscribe };
}
