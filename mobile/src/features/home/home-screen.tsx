/**
 * Home — the center, default surface. The "how's my agent doing / am I winning?"
 * glance: portfolio value, today's P&L, a 1D spark, quick tiles, and a pending
 * nudge. Calm, glanceable, premium. Honest numbers only — the alpha-vs-SPY
 * readout is an explicit placeholder until the server exposes that series.
 */
import { useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { usePendingProposals, usePortfolioHistory, useSnapshot } from '@/api/queries';
import { Card, Delta, Money, Pct, Row, Screen, Skeleton, Sparkline, Stack, Text } from '@/components/ui';
import { colors, pnlColor, spacing } from '@/theme';

export function HomeScreen() {
  const snapshot = useSnapshot();
  const history = usePortfolioHistory('1D');
  const pending = usePendingProposals();

  const summary = snapshot.data?.all_accounts_summary;
  const account = snapshot.data?.account;
  const portfolioValue = summary?.portfolio_value ?? account?.portfolio_value ?? 0;

  const points = history.data?.points ?? [];
  const last = points.length ? points[points.length - 1] : undefined;
  const todayPl = last?.profit_loss ?? 0;
  const todayPct = last?.profit_loss_pct ?? 0;
  const spark = points.map((p) => p.equity);

  const pendingCount = pending.data?.proposals.length ?? 0;
  const loading = snapshot.isLoading;
  const [chartWidth, setChartWidth] = useState(0);

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{ padding: spacing.xl, paddingTop: spacing['4xl'], gap: spacing['2xl'] }}
        refreshControl={
          <RefreshControl
            refreshing={snapshot.isRefetching}
            onRefresh={() => {
              void snapshot.refetch();
              void history.refetch();
            }}
            tintColor={colors.accent}
          />
        }
      >
        {/* Hero value */}
        <Animated.View entering={FadeInDown.duration(400)}>
          <Stack gap={spacing.xs}>
            <Text variant="label" upper color="textFaint">
              portfolio value
            </Text>
            {loading ? (
              <Skeleton height={48} width="70%" />
            ) : (
              <Text variant="display">{moneyString(portfolioValue)}</Text>
            )}
            {loading ? (
              <Skeleton height={18} width="40%" style={{ marginTop: spacing.xs }} />
            ) : (
              <Row gap={spacing.sm}>
                <Delta value={todayPl} size="md" />
                <Pct value={todayPct} size="md" signed pnl />
                <Text variant="callout" color="textFaint">
                  today
                </Text>
              </Row>
            )}
          </Stack>
        </Animated.View>

        {/* 1D spark — measures its full-bleed width */}
        {spark.length > 1 ? (
          <View onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}>
            {chartWidth > 0 ? (
              <Sparkline data={spark} width={chartWidth} height={64} color={pnlColor(todayPl)} fill strokeWidth={2} />
            ) : (
              <View style={{ height: 64 }} />
            )}
          </View>
        ) : null}

        {/* Quick tiles */}
        <Row gap={spacing.md}>
          <Tile label="Buying power" value={summary?.buying_power ?? account?.buying_power} loading={loading} />
          <Tile label="Cash" value={summary?.cash ?? account?.cash} loading={loading} />
        </Row>

        {/* Pending nudge */}
        {pendingCount > 0 ? (
          <Animated.View entering={FadeInDown.delay(80)}>
            <Card style={{ borderColor: colors.accent, backgroundColor: colors.accentWash }}>
              <Row justify="space-between">
                <Stack gap={2}>
                  <Text variant="bodyStrong" color="accent">
                    {pendingCount} proposal{pendingCount > 1 ? 's' : ''} waiting
                  </Text>
                  <Text variant="caption" color="textMuted">
                    Swipe right to review and approve.
                  </Text>
                </Stack>
                <Text variant="title" color="accent">
                  →
                </Text>
              </Row>
            </Card>
          </Animated.View>
        ) : null}

        {/* Alpha vs SPY — honest placeholder */}
        <Card>
          <Stack gap={spacing.xs}>
            <Text variant="label" upper color="textFaint">
              alpha vs S&P 500
            </Text>
            <Text variant="callout" color="textMuted">
              Per-strategy alpha readout is coming once the server exposes the series — no estimates shown here.
            </Text>
          </Stack>
        </Card>
      </ScrollView>
    </Screen>
  );
}

function moneyString(v: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);
}

function Tile({ label, value, loading }: { label: string; value?: number; loading?: boolean }) {
  return (
    <Card style={{ flex: 1 }}>
      <Stack gap={spacing.xs}>
        <Text variant="label" upper color="textFaint">
          {label}
        </Text>
        {loading ? <Skeleton height={20} width="80%" /> : <Money value={value ?? 0} size="md" compact />}
      </Stack>
    </Card>
  );
}
