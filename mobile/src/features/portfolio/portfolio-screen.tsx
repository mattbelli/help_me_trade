/**
 * Portfolio — the main surface. Portfolio value + today's P&L + equity curve +
 * buying power/cash + holdings, all on one page (merged Home + Portfolio per
 * user feedback). A pending-approval card surfaces here and deep-links to Inbox.
 *
 * No entering animations on this scroll content — they were replaying on the 15s
 * refetch and flicking the scroll. Values use tabular-nums so refreshes don't
 * shift layout. FlashList header holds the glance; positions are the list.
 */
import { useMemo, useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { usePendingProposals, usePortfolioHistory, useSnapshot } from '@/api/queries';
import type { BrokerSnapshot, PortfolioRange, Position, Snapshot } from '@/api/types';
import {
  Card,
  Delta,
  EmptyState,
  ErrorState,
  Money,
  Pct,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Sparkline,
  Stack,
  Text,
} from '@/components/ui';
import { usePullRefresh } from '@/hooks/use-pull-refresh';
import { useNav } from '@/state/nav';
import { colors, pnlColor, radius, spacing } from '@/theme';

const RANGES: PortfolioRange[] = ['1D', '1W', '1M', '1Y', 'ALL'];
type AccountFilter = 'all' | string;
interface PositionRow {
  key: string;
  position: Position;
}

export function PortfolioScreen() {
  const { openInbox } = useNav();
  const [range, setRange] = useState<PortfolioRange>('1W');
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('all');
  const [chartWidth, setChartWidth] = useState(0);

  const snapshot = useSnapshot();
  const today = usePortfolioHistory('1D');
  const curve = usePortfolioHistory(range);
  const pending = usePendingProposals();
  const pendingCount = pending.data?.proposals.length ?? 0;

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([snapshot.refetch(), today.refetch(), curve.refetch()]),
  );

  const summary = snapshot.data?.all_accounts_summary;
  const account = snapshot.data?.account;
  const value = summary?.portfolio_value ?? account?.portfolio_value ?? 0;

  const todayPts = today.data?.points ?? [];
  const todayLast = todayPts.length ? todayPts[todayPts.length - 1] : undefined;
  const todayPl = todayLast?.profit_loss ?? 0;
  const todayPct = todayLast?.profit_loss_pct ?? 0;

  const curvePts = (curve.data?.points ?? []).map((p) => p.equity);
  const positions = useMemo(
    () => (snapshot.data ? collectPositions(snapshot.data, accountFilter) : []),
    [snapshot.data, accountFilter],
  );
  const snapshots = snapshot.data?.snapshots ?? [];

  if (snapshot.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
          <ErrorState onRetry={snapshot.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <FlashList
        data={positions}
        keyExtractor={(item) => item.key}
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: spacing['3xl'] }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        ListHeaderComponent={
          <Stack gap={spacing.xl} style={{ paddingTop: spacing['3xl'], paddingBottom: spacing.lg }}>
            {/* Value + today */}
            <Stack gap={spacing.xs}>
              <Text variant="label" upper color="textFaint">
                portfolio value
              </Text>
              {snapshot.isLoading ? (
                <Skeleton height={44} width="65%" />
              ) : (
                <>
                  <Text variant="display">{usd0(value)}</Text>
                  <Row gap={spacing.sm}>
                    <Delta value={todayPl} size="md" />
                    <Pct value={todayPct} size="md" signed pnl />
                    <Text variant="callout" color="textFaint">
                      today
                    </Text>
                  </Row>
                </>
              )}
            </Stack>

            {/* Equity curve + range */}
            <View onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}>
              {curve.isLoading || chartWidth === 0 ? (
                <Skeleton height={110} />
              ) : curvePts.length > 1 ? (
                <Sparkline data={curvePts} width={chartWidth} height={110} color={pnlColor(curvePts[curvePts.length - 1] - curvePts[0])} fill strokeWidth={2} />
              ) : (
                <View style={{ height: 110, alignItems: 'center', justifyContent: 'center' }}>
                  <Text variant="caption" color="textFaint">No history for {range}</Text>
                </View>
              )}
            </View>
            <Row gap={spacing.xs}>
              {RANGES.map((r) => {
                const active = r === range;
                return (
                  <PressableScale key={r} onPress={() => setRange(r)} haptic>
                    <View style={{ paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.pill, backgroundColor: active ? colors.accentWash : 'transparent' }}>
                      <Text variant="label" upper style={{ color: active ? colors.accent : colors.textMuted }}>{r}</Text>
                    </View>
                  </PressableScale>
                );
              })}
            </Row>

            {/* Tiles */}
            <Row gap={spacing.md}>
              <Tile label="Buying power" value={summary?.buying_power ?? account?.buying_power} loading={snapshot.isLoading} />
              <Tile label="Cash" value={summary?.cash ?? account?.cash} loading={snapshot.isLoading} />
            </Row>

            {/* Pending approvals → Inbox */}
            {pendingCount > 0 ? (
              <PressableScale onPress={openInbox}>
                <Card style={{ borderColor: colors.accent, backgroundColor: colors.accentWash }}>
                  <Row justify="space-between">
                    <Stack gap={2}>
                      <Text variant="bodyStrong" color="accent">
                        {pendingCount} approval{pendingCount > 1 ? 's' : ''} waiting
                      </Text>
                      <Text variant="caption" color="textMuted">Tap to review in your Inbox.</Text>
                    </Stack>
                    <Text variant="title" color="accent">→</Text>
                  </Row>
                </Card>
              </PressableScale>
            ) : null}

            {/* Account switcher */}
            {snapshots.length > 1 ? (
              <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
                <Chip label="All" active={accountFilter === 'all'} onPress={() => setAccountFilter('all')} />
                {snapshots.map((s) => {
                  const key = accountKey(s);
                  return <Chip key={key} label={s.account_label ?? s.broker} active={accountFilter === key} onPress={() => setAccountFilter(key)} />;
                })}
              </Row>
            ) : null}

            <Text variant="label" upper color="textFaint">holdings</Text>
          </Stack>
        }
        renderItem={({ item }) => <PositionRowItem position={item.position} />}
        ListEmptyComponent={
          snapshot.isLoading ? null : <EmptyState title="No positions" subtitle="The agent hasn't opened anything yet." />
        }
      />
    </Screen>
  );
}

function Tile({ label, value, loading }: { label: string; value?: number; loading?: boolean }) {
  return (
    <Card style={{ flex: 1 }}>
      <Stack gap={spacing.xs}>
        <Text variant="label" upper color="textFaint">{label}</Text>
        {loading ? <Skeleton height={20} width="80%" /> : <Money value={value ?? 0} size="md" compact />}
      </Stack>
    </Card>
  );
}

function Chip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <PressableScale onPress={onPress}>
      <View style={{ paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.pill, backgroundColor: active ? colors.accentWash : colors.surface, borderWidth: 1, borderColor: active ? colors.accent : colors.border }}>
        <Text variant="callout" style={{ color: active ? colors.accent : colors.textMuted }}>{label}</Text>
      </View>
    </PressableScale>
  );
}

function PositionRowItem({ position }: { position: Position }) {
  return (
    <Row justify="space-between" align="center" style={{ paddingVertical: spacing.md }}>
      <Stack gap={2} style={{ flex: 1 }}>
        <Text variant="bodyStrong">{position.symbol}</Text>
        <Text variant="caption" color="textFaint">
          {position.qty} @ {position.avg_entry_price.toFixed(2)}
        </Text>
      </Stack>
      <Stack gap={2} style={{ alignItems: 'flex-end' }}>
        <Money value={position.market_value} size="sm" />
        <Delta value={position.unrealized_pl} size="sm" />
      </Stack>
    </Row>
  );
}

function usd0(v: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(v);
}

function accountKey(s: BrokerSnapshot): string {
  return s.account_id ?? s.broker;
}

function collectPositions(data: Snapshot, filter: AccountFilter): PositionRow[] {
  const bs = data.snapshots;
  if (!bs || bs.length <= 1) {
    return (data.positions ?? []).map((p) => ({ key: `${p.symbol}-${data.broker ?? 'default'}`, position: p }));
  }
  if (filter === 'all') {
    return bs.flatMap((s) => s.positions.map((p) => ({ key: `${p.symbol}-${accountKey(s)}`, position: p })));
  }
  const sel = bs.find((s) => accountKey(s) === filter);
  return (sel?.positions ?? []).map((p) => ({ key: `${p.symbol}-${filter}`, position: p }));
}
