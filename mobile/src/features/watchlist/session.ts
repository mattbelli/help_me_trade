import type { WatchlistQuote } from '@/api/types';

export interface SessionView {
  last: number;
  changePct: number;
  changeDol?: number;
  badge: string | null;
}

export function isExtendedSession(s: WatchlistQuote['market_session']): boolean {
  return s === 'pre' || s === 'post' || s === 'overnight' || s === 'closed';
}

export function sessionBadge(s: WatchlistQuote['market_session']): string | null {
  return s === 'pre' ? 'PRE' : s === 'post' ? 'AFT' : s === 'overnight' ? '24h' : s === 'closed' ? 'CLOSED' : null;
}

// Extended hours (incl. weekend `closed`): headline the extended price. serv's
// `ext_price` is now authoritative (SIP-hybrid + stream-freeze fix; founder-verified
// synced to desktop 2026-07-07), so PREFER it directly. The old regular-close × live-%
// derivation is kept only as a fallback for when `ext_price` is absent but a % move is
// present. `closed` carries the last extended print (not null), so it uses this path
// too. Shared by the watchlist rows + the ticker detail header so both stay consistent.
export function sessionView(quote: WatchlistQuote): SessionView {
  const s = quote.market_session;
  if (isExtendedSession(s)) {
    if (quote.ext_price != null) {
      return {
        last: quote.ext_price,
        changePct: quote.ext_change_pct ?? quote.change_pct,
        changeDol: quote.price != null ? quote.ext_price - quote.price : undefined,
        badge: sessionBadge(s),
      };
    }
    const pct = quote.ext_change_pct;
    if (pct != null && quote.price != null) {
      const last = quote.price * (1 + pct / 100);
      return { last, changePct: pct, changeDol: last - quote.price, badge: sessionBadge(s) };
    }
  }
  return {
    last: quote.price,
    changePct: quote.change_pct,
    changeDol: quote.prev_close != null ? quote.price - quote.prev_close : undefined,
    badge: null,
  };
}
