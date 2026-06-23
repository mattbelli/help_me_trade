import { useEffect, useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { getRecentAlerts } from '@/api/endpoints';
import type { MarketAlert } from '@/api/types';
import {
  Badge,
  EmptyState,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { useAlerts } from '@/state/alerts';
import { colors, spacing } from '@/theme';

export function ActivityScreen() {
  const { alerts, seed } = useAlerts();
  const [seedLoading, setSeedLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const { alerts: recent } = await getRecentAlerts();
        if (!cancelled) {
          seed(recent);
        }
      } finally {
        if (!cancelled) {
          setSeedLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [seed]);

  const refresh = () => {
    setSeedLoading(true);
    void (async () => {
      try {
        const { alerts: recent } = await getRecentAlerts();
        seed(recent);
      } finally {
        setSeedLoading(false);
      }
    })();
  };

  return (
    <Screen>
      <FlashList
        data={alerts}
        keyExtractor={(alert) => alert.id}
        refreshControl={
          <RefreshControl refreshing={seedLoading} onRefresh={refresh} tintColor={colors.accent} />
        }
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: spacing['3xl'] }}
        ListHeaderComponent={
          <View style={{ paddingTop: spacing['4xl'], paddingBottom: spacing.lg }}>
            <Header />
          </View>
        }
        renderItem={({ item }) => <ActivityRow alert={item} />}
        ListEmptyComponent={
          seedLoading ? (
            <Stack gap={spacing.md}>
              <Skeleton height={64} />
              <Skeleton height={64} />
              <Skeleton height={64} />
            </Stack>
          ) : (
            <EmptyState
              title="Quiet for now"
              subtitle="The agent's actions will stream here."
            />
          )
        }
      />
    </Screen>
  );
}

function Header() {
  return (
    <Stack gap={2}>
      <Text variant="label" upper color="accent">
        activity
      </Text>
      <Text variant="title">Recent</Text>
    </Stack>
  );
}

function ActivityRow({ alert }: { alert: MarketAlert }) {
  const source = alert.source ?? 'system';
  const primary =
    alert.action?.summary ??
    alert.action?.message ??
    alert.rule_name ??
    alert.symbol ??
    'Activity';

  return (
    <Stack gap={spacing.sm} style={{ paddingVertical: spacing.md }}>
      <Row justify="space-between" align="center">
        <Text variant="caption" color="textFaint">
          {timeAgo(alert.ts)}
        </Text>
        <Badge label={source} tone={sourceTone(source)} />
      </Row>
      <Text variant="body">{primary}</Text>
    </Stack>
  );
}

function sourceTone(source: string): 'accent' | 'positive' | 'warning' | 'neutral' {
  if (source === 'proposal') return 'accent';
  if (source === 'execution' || source === 'fill') return 'positive';
  if (source === 'exit_engine') return 'warning';
  return 'neutral';
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';

  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}
