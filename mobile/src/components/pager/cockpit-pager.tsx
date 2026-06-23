/**
 * CockpitPager — the heart of the app's navigation. Full-bleed horizontal paging
 * between surfaces with Home centered (brief §5). Holds the app's single market
 * WebSocket connection and fans events out: live alerts → the alerts store,
 * new proposals/executions → query invalidation + haptic, plus a connection dot
 * and a tap-to-jump pending-approvals pill.
 */
import { useCallback, useRef, useState } from 'react';
import { Pressable, View } from 'react-native';
import PagerView from 'react-native-pager-view';
import Animated, { FadeIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQueryClient } from '@tanstack/react-query';

import { usePendingProposals } from '@/api/queries';
import { useMarketWs } from '@/api/use-market-ws';
import { Text } from '@/components/ui';
import { useAuth } from '@/auth/auth-context';
import { useAlerts } from '@/state/alerts';
import { colors, haptics, radius, spacing } from '@/theme';

import { ActivityScreen } from '@/features/activity/activity-screen';
import { ApprovalsScreen } from '@/features/approvals/approvals-screen';
import { HomeScreen } from '@/features/home/home-screen';
import { PortfolioScreen } from '@/features/portfolio/portfolio-screen';
import { SettingsScreen } from '@/features/settings/settings-screen';
import { WatchlistScreen } from '@/features/watchlist/watchlist-screen';

const SURFACES = [
  { key: 'activity', title: 'Activity', render: () => <ActivityScreen /> },
  { key: 'portfolio', title: 'Portfolio', render: () => <PortfolioScreen /> },
  { key: 'home', title: 'Home', render: () => <HomeScreen /> },
  { key: 'approvals', title: 'Approvals', render: () => <ApprovalsScreen /> },
  { key: 'watchlist', title: 'Watchlist', render: () => <WatchlistScreen /> },
  { key: 'settings', title: 'Settings', render: () => <SettingsScreen /> },
] as const;

const HOME_INDEX = 2;
const APPROVALS_INDEX = 3;

export function CockpitPager() {
  const insets = useSafeAreaInsets();
  const pagerRef = useRef<PagerView>(null);
  const [page, setPage] = useState(HOME_INDEX);

  const { token } = useAuth();
  const qc = useQueryClient();
  const { pushAlert } = useAlerts();
  const { data: pending } = usePendingProposals();
  const pendingCount = pending?.proposals.length ?? 0;

  const goTo = useCallback((index: number) => {
    pagerRef.current?.setPage(index);
  }, []);

  const { connected } = useMarketWs({
    token,
    onAlert: pushAlert,
    onProposal: () => {
      haptics.warning();
      void qc.invalidateQueries({ queryKey: ['proposals', 'pending'] });
      void qc.invalidateQueries({ queryKey: ['snapshot'] });
    },
  });

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <PagerView
        ref={pagerRef}
        style={{ flex: 1 }}
        initialPage={HOME_INDEX}
        onPageSelected={(e) => {
          const next = e.nativeEvent.position;
          setPage(next);
          haptics.selection();
        }}
      >
        {SURFACES.map((s) => (
          <View key={s.key} style={{ flex: 1 }} collapsable={false}>
            {s.render()}
          </View>
        ))}
      </PagerView>

      {/* Top locator overlay — page dots + live connection dot. Surfaces render their own titles. */}
      <View
        pointerEvents="box-none"
        style={{ position: 'absolute', top: insets.top + spacing.sm, left: 0, right: 0, alignItems: 'center' }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
          {SURFACES.map((s, i) => (
            <View
              key={s.key}
              style={{
                width: i === page ? 18 : 6,
                height: 6,
                borderRadius: 3,
                backgroundColor: i === page ? colors.accent : colors.borderStrong,
              }}
            />
          ))}
          <View
            style={{
              width: 6,
              height: 6,
              borderRadius: 3,
              marginLeft: spacing.sm,
              backgroundColor: connected ? colors.positive : colors.textFaint,
            }}
          />
        </View>
      </View>

      {/* Pending-approvals pill — only when there's something to act on, and not already there. */}
      {pendingCount > 0 && page !== APPROVALS_INDEX ? (
        <Animated.View
          entering={FadeIn}
          pointerEvents="box-none"
          style={{ position: 'absolute', top: insets.top + spacing['2xl'], left: 0, right: 0, alignItems: 'center' }}
        >
          <Pressable
            onPress={() => goTo(APPROVALS_INDEX)}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: spacing.sm,
              backgroundColor: colors.accent,
              paddingHorizontal: spacing.lg,
              paddingVertical: spacing.sm,
              borderRadius: radius.pill,
            }}
          >
            <Text variant="caption" style={{ color: colors.onAccent }}>
              {pendingCount} pending approval{pendingCount > 1 ? 's' : ''} →
            </Text>
          </Pressable>
        </Animated.View>
      ) : null}
    </View>
  );
}

export { HOME_INDEX, APPROVALS_INDEX };
