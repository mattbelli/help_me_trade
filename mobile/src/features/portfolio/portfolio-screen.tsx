/**
 * Portfolio — the main surface. Portfolio value + today's P&L + equity curve +
 * buying power/cash + holdings, all on one page (merged Home + Portfolio per
 * user feedback). A pending-approval card surfaces here and deep-links to Inbox.
 *
 * No entering animations on this scroll content — they were replaying on the 15s
 * refetch and flicking the scroll. Values use tabular-nums so refreshes don't
 * shift layout. FlashList header holds the glance; positions are the list.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { useDayPnl, usePendingProposals, usePortfolioHistory, useSnapshot } from '@/api/queries';
import type { BrokerSnapshot, PortfolioRange, Position, Snapshot } from '@/api/types';
import {
  Card,
  Delta,
  EmptyState,
  ErrorState,
  Eyebrow,
  Money,
  PageHeading,
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
import { hairline, radius, spacing, useColors } from '@/theme';

const RANGES: PortfolioRange[] = ['1D', '1W', '1M', '1Y', 'ALL'];
type AccountFilter = 'all' | string;
interface PositionRow {
  key: string;
  position: Position;
}

export function PortfolioScreen() {
  const colors = useColors();
  const { openInbox } = useNav();
  const [range, setRange] = useState<PortfolioRange>('1W');
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('all');
  const [chartWidth, setChartWidth] = useState(0);

  const snapshot = useSnapshot();
  // Realized (net-cash, ET-day) day P&L — matches the web DAY tile and fixes the
  // after-hours leak the equity-curve MTM carried. `all_paper` = this screen's scope.
  const dayPnlQ = useDayPnl('all_paper');
  const curve = usePortfolioHistory(range);
  const pending = usePendingProposals();
  const pendingCount = pending.data?.proposals.length ?? 0;

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([snapshot.refetch(), dayPnlQ.refetch(), curve.refetch()]),
  );

  const summary = snapshot.data?.all_accounts_summary;
  const account = snapshot.data?.account;
  const value = summary?.portfolio_value ?? account?.portfolio_value ?? 0;

  const dayPnl = dayPnlQ.data?.day_pnl ?? 0;
  const unrealizedPl = summary?.unrealized_pl ?? 0;
  const buyingPower = summary?.buying_power ?? account?.buying_power ?? 0;
  const cash = summary?.cash ?? account?.cash ?? 0;

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
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        ListHeaderComponent={
          <Stack gap={spacing.xl} style={{ paddingTop: spacing.sm, paddingBottom: spacing.lg }}>
            {/* Page header — logo + title, broker status on the right */}
            <Row justify="space-between" align="center">
              <PageHeading title="portfolio" />
              <Row gap={spacing.xs} align="center">
                <Text variant="caption" style={{ color: colors.positive }}>
                  ●
                </Text>
                <Eyebrow>alpaca · paper</Eyebrow>
              </Row>
            </Row>

            {/* Value */}
            <Stack gap={spacing.xs}>
              <Eyebrow>portfolio value</Eyebrow>
              {snapshot.isLoading ? (
                <Skeleton height={44} width="65%" />
              ) : (
                <Text variant="display">{usd0(value)}</Text>
              )}
            </Stack>

            {/* Stat pills — 2×2 grid above the chart */}
            <Stack gap={spacing.sm}>
              <Row gap={spacing.sm}>
                <StatPill label="realized" loading={dayPnlQ.isLoading}>
                  <Delta value={dayPnl} size="sm" />
                </StatPill>
                <StatPill label="unrealized" loading={snapshot.isLoading}>
                  <Delta value={unrealizedPl} size="sm" />
                </StatPill>
              </Row>
              <Row gap={spacing.sm}>
                <StatPill label="buying power" loading={snapshot.isLoading}>
                  <Money value={buyingPower} size="sm" />
                </StatPill>
                <StatPill label="cash" loading={snapshot.isLoading}>
                  <Money value={cash} size="sm" />
                </StatPill>
              </Row>
            </Stack>

            {/* Equity curve + range */}
            <View onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}>
              {curve.isLoading || chartWidth === 0 ? (
                <Skeleton height={110} />
              ) : curvePts.length > 1 ? (
                <Sparkline data={curvePts} width={chartWidth} height={110} color={colors.accent} fill strokeWidth={2} />
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

            <Eyebrow>holdings</Eyebrow>
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

/** Compact stat pill — a quarter-width surface chip: tiny label over a small figure. */
function StatPill({
  label,
  loading,
  children,
}: {
  label: string;
  loading?: boolean;
  children: ReactNode;
}) {
  const colors = useColors();
  return (
    <View
      style={{
        flex: 1,
        gap: 3,
        paddingHorizontal: spacing.sm,
        paddingVertical: spacing.sm,
        borderRadius: radius.lg,
        backgroundColor: colors.surface,
        borderWidth: hairline,
        borderColor: colors.border,
      }}
    >
      <Eyebrow>{label}</Eyebrow>
      {loading ? <Skeleton height={16} width="80%" /> : children}
    </View>
  );
}

function Chip({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  const colors = useColors();
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
