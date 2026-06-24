import type { WatchlistQuote } from '@/api/types';

export interface SessionView {
  last: number;
  changePct: number;
  changeDol?: number;
  badge: string | null;
}

// Extended hours: in pre/post/overnight, headline the ext_price with a PRE/AFT/24h
// badge and color by ext_change_pct; otherwise the regular price/change. Shared by
// the watchlist rows and the ticker detail header so both stay session-consistent.
export function sessionView(quote: WatchlistQuote): SessionView {
  const s = quote.market_session;
  if ((s === 'pre' || s === 'post' || s === 'overnight') && quote.ext_price != null) {
    // ext_change_pct is the move vs the regular-session close, so the $ change must
    // be ext_price − price (NOT − prev_close) to keep the sign/value consistent.
    return {
      last: quote.ext_price,
      changePct: quote.ext_change_pct ?? quote.change_pct,
      changeDol: quote.ext_price - quote.price,
      badge: s === 'pre' ? 'PRE' : s === 'post' ? 'AFT' : '24h',
    };
  }
  return {
    last: quote.price,
    changePct: quote.change_pct,
    changeDol: quote.prev_close != null ? quote.price - quote.prev_close : undefined,
    badge: null,
  };
}
