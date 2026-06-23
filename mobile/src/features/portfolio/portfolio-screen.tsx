import { useMemo, useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { usePortfolioHistory, useSnapshot } from '@/api/queries';
import type { BrokerSnapshot, PortfolioRange, Position, Snapshot } from '@/api/types';
import {
  Delta,
  EmptyState,
  ErrorState,
  Money,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Sparkline,
  Stack,
  Text,
} from '@/components/ui';
import { colors, pnlColor, spacing } from '@/theme';

const RANGES: PortfolioRange[] = ['1D', '3D', '1W', '1M', '1Y', 'ALL'];

type AccountFilter = 'all' | string;

interface PositionRow {
  key: string;
  position: Position;
}

export function PortfolioScreen() {
  const [range, setRange] = useState<PortfolioRange>('1W');
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('all');
  const [chartWidth, setChartWidth] = useState(0);

  const snapshot = useSnapshot();
  const history = usePortfolioHistory(range);

  const summary = snapshot.data?.all_accounts_summary;
  const account = snapshot.data?.account;
  const portfolioValue = summary?.portfolio_value ?? account?.portfolio_value ?? 0;
  const unrealizedPl = summary?.unrealized_pl ?? 0;

  const snapshots = snapshot.data?.snapshots ?? [];
  const showAccountSwitcher = snapshots.length > 1;

  const positions = useMemo(
    () => (snapshot.data ? collectPositions(snapshot.data, accountFilter) : []),
    [snapshot.data, accountFilter],
  );

  const equityPoints = history.data?.points.map((p) => p.equity) ?? [];
  const sparkColor =
    equityPoints.length > 1
      ? pnlColor(equityPoints[equityPoints.length - 1] - equityPoints[0])
      : pnlColor(unrealizedPl);

  const loading = snapshot.isLoading;
  const historyLoading = history.isLoading;

  if (snapshot.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
          <Header />
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
        refreshControl={
          <RefreshControl
            refreshing={snapshot.isRefetching || history.isRefetching}
            onRefresh={() => {
              void snapshot.refetch();
              void history.refetch();
            }}
            tintColor={colors.accent}
          />
        }
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: spacing['3xl'] }}
        ListHeaderComponent={
          <Stack gap={spacing.lg} style={{ paddingTop: spacing['4xl'], paddingBottom: spacing.lg }}>
            <Header />

            {loading ? (
              <Skeleton height={48} width="70%" />
            ) : (
              <Stack gap={spacing.xs}>
                <Money value={portfolioValue} size="lg" compact />
                <Row gap={spacing.sm} align="center">
                  <Delta value={unrealizedPl} size="md" />
                  <Text variant="callout" color="textFaint">
                    unrealized
                  </Text>
                </Row>
              </Stack>
            )}

            {historyLoading ? (
              <Skeleton height={120} />
            ) : equityPoints.length > 1 ? (
              <View onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}>
                {chartWidth > 0 ? (
                  <Sparkline
                    data={equityPoints}
                    width={chartWidth}
                    height={120}
                    color={sparkColor}
                    fill
                    strokeWidth={2}
                  />
                ) : (
                  <View style={{ height: 120 }} />
                )}
              </View>
            ) : null}

            <RangeSelector range={range} onSelect={setRange} />

            {showAccountSwitcher ? (
              <AccountSwitcher
                snapshots={snapshots}
                selected={accountFilter}
                onSelect={setAccountFilter}
              />
            ) : null}
          </Stack>
        }
        renderItem={({ item }) => <PositionRowItem position={item.position} />}
        ListEmptyComponent={
          loading ? null : (
            <EmptyState
              title="No positions"
              subtitle="The agent hasn't opened anything yet."
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
        portfolio
      </Text>
      <Text variant="title">Holdings</Text>
    </Stack>
  );
}

function RangeSelector({
  range,
  onSelect,
}: {
  range: PortfolioRange;
  onSelect: (r: PortfolioRange) => void;
}) {
  return (
    <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
      {RANGES.map((r) => {
        const active = r === range;
        return (
          <PressableScale key={r} onPress={() => onSelect(r)}>
            <View
              style={{
                paddingHorizontal: spacing.md,
                paddingVertical: spacing.sm,
                borderRadius: 999,
                backgroundColor: active ? colors.accentWash : 'transparent',
              }}
            >
              <Text variant="label" upper style={{ color: active ? colors.accent : colors.textMuted }}>
                {r}
              </Text>
            </View>
          </PressableScale>
        );
      })}
    </Row>
  );
}

function AccountSwitcher({
  snapshots,
  selected,
  onSelect,
}: {
  snapshots: BrokerSnapshot[];
  selected: AccountFilter;
  onSelect: (key: AccountFilter) => void;
}) {
  return (
    <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
      <AccountChip label="All" active={selected === 'all'} onPress={() => onSelect('all')} />
      {snapshots.map((s) => {
        const key = accountKey(s);
        const label = s.account_label ?? s.broker;
        return (
          <AccountChip
            key={key}
            label={label}
            active={selected === key}
            onPress={() => onSelect(key)}
          />
        );
      })}
    </Row>
  );
}

function AccountChip({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <PressableScale onPress={onPress}>
      <View
        style={{
          paddingHorizontal: spacing.md,
          paddingVertical: spacing.sm,
          borderRadius: 999,
          backgroundColor: active ? colors.accentWash : colors.surface,
          borderWidth: 1,
          borderColor: active ? colors.accent : colors.border,
        }}
      >
        <Text variant="callout" style={{ color: active ? colors.accent : colors.textMuted }}>
          {label}
        </Text>
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

function accountKey(snapshot: BrokerSnapshot): string {
  return snapshot.account_id ?? snapshot.broker;
}

function collectPositions(data: Snapshot, filter: AccountFilter): PositionRow[] {
  const brokerSnapshots = data.snapshots;

  if (!brokerSnapshots || brokerSnapshots.length <= 1) {
    return (data.positions ?? []).map((position) => ({
      key: `${position.symbol}-${data.broker ?? 'default'}`,
      position,
    }));
  }

  if (filter === 'all') {
    return brokerSnapshots.flatMap((snapshot) =>
      snapshot.positions.map((position) => ({
        key: `${position.symbol}-${accountKey(snapshot)}`,
        position,
      })),
    );
  }

  const selected = brokerSnapshots.find((snapshot) => accountKey(snapshot) === filter);
  return (selected?.positions ?? []).map((position) => ({
    key: `${position.symbol}-${filter}`,
    position,
  }));
}
