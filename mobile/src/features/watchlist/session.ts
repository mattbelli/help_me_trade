import type { WatchlistQuote } from '@/api/types';

export interface SessionView {
  last: number;
  changePct: number;
  changeDol?: number;
  badge: string | null;
}

export function isExtendedSession(s: WatchlistQuote['market_session']): boolean {
  return s === 'pre' || s === 'post' || s === 'overnight';
}

export function sessionBadge(s: WatchlistQuote['market_session']): string | null {
  return s === 'pre' ? 'PRE' : s === 'post' ? 'AFT' : s === 'overnight' ? '24h' : null;
}

// Extended hours: headline the extended price. serv's `ext_price` is currently
// STALE (it freezes while ext_change_pct keeps moving — serv bug), so DERIVE the
// price from the regular close × the live % move; that keeps it ticking. Fall back
// to ext_price only if the % is missing. Shared by the watchlist rows + the ticker
// detail header so both stay session-consistent.
export function sessionView(quote: WatchlistQuote): SessionView {
  const s = quote.market_session;
  if (isExtendedSession(s)) {
    const pct = quote.ext_change_pct;
    if (pct != null && quote.price != null) {
      const last = quote.price * (1 + pct / 100);
      return { last, changePct: pct, changeDol: last - quote.price, badge: sessionBadge(s) };
    }
    if (quote.ext_price != null) {
      return {
        last: quote.ext_price,
        changePct: quote.ext_change_pct ?? quote.change_pct,
        changeDol: quote.ext_price - quote.price,
        badge: sessionBadge(s),
      };
    }
  }
  return {
    last: quote.price,
    changePct: quote.change_pct,
    changeDol: quote.prev_close != null ? quote.price - quote.prev_close : undefined,
    badge: null,
  };
}
