/**
 * CockpitPager — app shell. Bottom tab bar + horizontal swipe over four
 * surfaces (Portfolio · Watchlist · Inbox · Settings). Holds the app's single
 * market WebSocket and APNs wiring.
 *
 * BUG FIX: the page elements are built ONCE (useMemo []), so cockpit re-renders
 * (tab highlight, badges, WS events every ~10-15s) never remount the surfaces —
 * that was causing the "reload on swipe" + "scroll flicks down/up every 15s".
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import PagerView from 'react-native-pager-view';
import { useQueryClient } from '@tanstack/react-query';

import { usePendingProposals } from '@/api/queries';
import { useMarketWs } from '@/api/use-market-ws';
import { TabBar, type TabDef } from '@/components/nav/tab-bar';
import { useAuth } from '@/auth/auth-context';
import { usePush } from '@/push/use-push';
import { useAlerts } from '@/state/alerts';
import { NavProvider } from '@/state/nav';
import { colors, haptics } from '@/theme';

import { ChatScreen } from '@/features/chat/chat-screen';
import { InboxScreen } from '@/features/inbox/inbox-screen';
import { PortfolioScreen } from '@/features/portfolio/portfolio-screen';
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
  const pagerRef = useRef<PagerView>(null);
  const [page, setPage] = useState(HOME_INDEX);
  const lastPage = useRef(HOME_INDEX);

  const { token } = useAuth();
  const qc = useQueryClient();
  const { pushAlert } = useAlerts();
  const { data: pending } = usePendingProposals();
  const pendingCount = pending?.proposals.length ?? 0;

  const goTo = useCallback((index: number) => pagerRef.current?.setPage(index), []);

  usePush({ onOpenApprovals: () => goTo(INBOX_INDEX) });

  useMarketWs({
    token,
    onAlert: pushAlert,
    onProposal: () => {
      haptics.warning();
      void qc.invalidateQueries({ queryKey: ['proposals', 'pending'] });
      void qc.invalidateQueries({ queryKey: ['snapshot'] });
      void qc.invalidateQueries({ queryKey: ['alerts', 'recent'] });
    },
  });

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
    <NavProvider value={{ openInbox: () => goTo(INBOX_INDEX) }}>
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <PagerView
          ref={pagerRef}
          style={{ flex: 1 }}
          initialPage={HOME_INDEX}
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
