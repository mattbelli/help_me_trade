/**
 * CockpitPager — app shell. Bottom tab bar + horizontal swipe over four
 * surfaces (Portfolio · Watchlist · Inbox · Settings). Holds the app's single
 * market WebSocket and APNs wiring.
 *
 * BUG FIX: the page elements are built ONCE (useMemo []), so cockpit re-renders
 * (tab highlight, badges, WS events every ~10-15s) never remount the surfaces —
 * that was causing the "reload on swipe" + "scroll flicks down/up every 15s".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import PagerView from 'react-native-pager-view';
import { useQueryClient } from '@tanstack/react-query';

import { usePendingProposals, useWatchlist } from '@/api/queries';
import { useMarketWs, type QuoteUpdate } from '@/api/use-market-ws';
import type { WatchlistMarket } from '@/api/types';
import { TabBar, type TabDef } from '@/components/nav/tab-bar';
import { useAuth } from '@/auth/auth-context';
import { usePush } from '@/push/use-push';
import { useLiveActivity } from '@/push/use-live-activity';
import { useAlerts } from '@/state/alerts';
import { NavProvider } from '@/state/nav';
import { haptics, useColors } from '@/theme';

import { ChatScreen } from '@/features/chat/chat-screen';
import { InboxScreen } from '@/features/inbox/inbox-screen';
import { PortfolioScreen } from '@/features/portfolio/portfolio-screen';
import { isExtendedSession } from '@/features/watchlist/session';
import { SettingsScreen } from '@/features/settings/settings-screen';
import { WatchlistScreen } from '@/features/watchlist/watchlist-screen';

const TABS: TabDef[] = [
  { key: 'inbox', label: 'Inbox', icon: 'inbox' },
  { key: 'watchlist', label: 'Watchlist', icon: 'watchlist' },
  { key: 'home', label: 'Home', icon: 'portfolio' },
  { key: 'chat', label: 'Chat', icon: 'chat' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
];
const INBOX_INDEX = 0;
const HOME_INDEX = 2;

export function CockpitPager() {
  const colors = useColors();
  const pagerRef = useRef<PagerView>(null);
  const [page, setPage] = useState(HOME_INDEX);
  const [swipeEnabled, setSwipeEnabled] = useState(true);
  const lastPage = useRef(HOME_INDEX);

  const { token } = useAuth();
  const qc = useQueryClient();
  const { pushAlert, pushNotification } = useAlerts();
  const { data: pending } = usePendingProposals();
  const pendingCount = pending?.proposals.length ?? 0;

  const goTo = useCallback((index: number) => pagerRef.current?.setPage(index), []);

  usePush({ onOpenApprovals: () => goTo(INBOX_INDEX) });
  useLiveActivity({ onOpenApprovals: () => goTo(INBOX_INDEX) });

  // Live prices: subscribe the active watchlist's symbols and merge each tick
  // into every watchlist-market cache entry, so rows update in real time
  // instead of waiting on the 6s REST poll. Spread the prior quote and touch
  // ONLY the price-bearing fields — never clobber rvol or the company-info
  // fields the poll fills in. ext_* / market_session let pre/post-market rows
  // headline the live extended-hours price straight off the stream.
  // Subscribe the SAME query the watchlist screen renders (['watchlist']), not
  // active_id out of ['watchlists'] — two caches with different staleness can
  // diverge after a list switch on another device, leaving the screen showing
  // list A while the socket streams list B (frozen rows).
  const { data: watchlist } = useWatchlist();
  const activeSymbols = useMemo(() => watchlist?.symbols ?? [], [watchlist]);

  const onQuotes = useCallback(
    (batch: QuoteUpdate[]) => {
      // ONE cache write per flush, not one per tick: the whole batch folds into a
      // single quotes object so a 40-symbol list costs one render, not forty.
      qc.setQueriesData<WatchlistMarket>({ queryKey: ['watchlist-market'] }, (old) => {
        if (!old) return old;
        let quotes = old.quotes;
        let touched = false;
        for (const q of batch) {
          const prev = quotes[q.symbol];
          if (!prev) continue;
          const session = q.market_session ?? prev.market_session;
          const extended = isExtendedSession(session);
          const next = { ...prev };
          // In extended hours, keep the REST regular-session close as the price
          // baseline (sessionView derives the headline from it + the live ext %).
          // Only let the WS overwrite price/change in the regular session.
          if (!extended) {
            next.price = q.price;
            next.change_pct = q.change_pct;
          }
          if (q.sparkline) next.sparkline = q.sparkline;
          if (q.market_session !== undefined) next.market_session = q.market_session;
          if (q.ext_price != null) next.ext_price = q.ext_price;
          if (q.ext_change_pct != null) next.ext_change_pct = q.ext_change_pct;
          if (!touched) {
            quotes = { ...quotes };
            touched = true;
          }
          quotes[q.symbol] = next;
        }
        // Returning `old` unchanged when nothing matched keeps react-query from
        // notifying subscribers for a batch of symbols this cache doesn't hold.
        return touched ? { ...old, quotes } : old;
      });
    },
    [qc],
  );

  const { connected, subscribe } = useMarketWs({
    token,
    onAlert: pushAlert,
    onNotification: pushNotification,
    onQuotes,
    onProposal: () => {
      haptics.warning();
      void qc.invalidateQueries({ queryKey: ['proposals', 'pending'] });
      void qc.invalidateQueries({ queryKey: ['snapshot'] });
      void qc.invalidateQueries({ queryKey: ['alerts', 'recent'] });
    },
  });

  // (Re)subscribe whenever the socket reconnects or the active list changes.
  useEffect(() => {
    if (connected && activeSymbols.length > 0) {
      subscribe(activeSymbols);
    }
  }, [connected, activeSymbols, subscribe]);

  // Build pages ONCE so cockpit re-renders never remount/refetch a surface.
  const pages = useMemo(
    () => [
      <View key="inbox" style={{ flex: 1 }} collapsable={false}><InboxScreen /></View>,
      <View key="watchlist" style={{ flex: 1 }} collapsable={false}><WatchlistScreen /></View>,
      <View key="home" style={{ flex: 1 }} collapsable={false}><PortfolioScreen /></View>,
      <View key="chat" style={{ flex: 1 }} collapsable={false}><ChatScreen /></View>,
      <View key="settings" style={{ flex: 1 }} collapsable={false}><SettingsScreen /></View>,
    ],
    [],
  );

  return (
    <NavProvider value={{ openInbox: () => goTo(INBOX_INDEX), setSwipeEnabled }}>
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <PagerView
          ref={pagerRef}
          style={{ flex: 1 }}
          initialPage={HOME_INDEX}
          scrollEnabled={swipeEnabled}
          offscreenPageLimit={4}
          onPageSelected={(e) => {
            const next = e.nativeEvent.position;
            setPage(next);
            // Dedupe: the pager can emit onPageSelected more than once per settle;
            // only buzz on an actual page change.
            if (next !== lastPage.current) {
              lastPage.current = next;
              haptics.selection();
            }
          }}
        >
          {pages}
        </PagerView>

        <TabBar tabs={TABS} index={page} onSelect={goTo} badges={{ inbox: pendingCount > 0 }} />
      </View>
    </NavProvider>
  );
}
