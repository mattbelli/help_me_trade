/**
 * Portfolio — the main surface. One scope selector (All · per-account chips)
 * drives EVERYTHING below it: headline value + range delta, the equity curve
 * (serv's snapshot-backed per-account history), the range-scoped P&L pills,
 * and holdings. The range row scopes the delta + pills to the curve's window
 * (1D = since midnight ET, serv-side).
 *
 * No entering animations on this scroll content — they were replaying on the
 * refetch and flicking the scroll. Values use tabular-nums so refreshes don't
 * shift layout. FlashList header holds the glance; positions are the list.
 */
import { useMemo, useState, type ReactNode } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { usePendingProposals, usePortfolioHistory, useSnapshot } from '@/api/queries';
import type { BrokerSnapshot, PortfolioRange, Position, Snapshot } from '@/api/types';
import {
  Card,
  Delta,
  EmptyState,
  ErrorState,
  Eyebrow,
  Money,
  PageHeading,
  Pct,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { EquityScrub } from '@/features/portfolio/equity-scrub';
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
  const { openInbox, activeTab } = useNav();
  const [range, setRange] = useState<PortfolioRange>('1D');
  const [accountFilter, setAccountFilter] = useState<AccountFilter>('all');

  // Pager keeps every surface mounted, so gate the polls to the visible tab
  // (refetch-on-resume covers freshness when swiping back).
  const focused = activeTab === 'home';
  const snapshot = useSnapshot({ poll: focused });

  const snapshots = snapshot.data?.snapshots ?? [];
  // The selected account snapshot; undefined = "All" overview (also the
  // fallback if the filtered account vanishes from the payload).
  const selected =
    accountFilter === 'all' ? undefined : snapshots.find((s) => accountKey(s) === accountFilter);
  const effectiveFilter: AccountFilter = selected ? accountFilter : 'all';
  const scopeId = selected ? accountKey(selected) : 'all_paper';

  // Snapshot-backed scoped history: the equity curve for this scope + window
  // P&L (change / realized / unrealized_change; 1D = since midnight ET).
  const history = usePortfolioHistory(range, scopeId, { poll: focused });
  const pending = usePendingProposals();
  const pendingCount = pending.data?.proposals.length ?? 0;

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([snapshot.refetch(), history.refetch()]),
  );

  const summary = snapshot.data?.all_accounts_summary;
  const account = snapshot.data?.account;
  const value = selected
    ? selected.account.portfolio_value
    : (summary?.portfolio_value ?? account?.portfolio_value ?? 0);
  const buyingPower = selected
    ? selected.account.buying_power
    : (summary?.buying_power ?? account?.buying_power ?? 0);
  const cash = selected ? selected.account.cash : (summary?.cash ?? account?.cash ?? 0);

  const win = history.data;
  const change = win?.change ?? 0;
  const changePct = win?.start_equity ? (change / win.start_equity) * 100 : 0;
  const points = win?.points ?? [];
  const rangeLabel = range === '1D' ? 'today' : range.toLowerCase();

  const positions = useMemo(
    () => (snapshot.data ? collectPositions(snapshot.data, effectiveFilter) : []),
    [snapshot.data, effectiveFilter],
  );

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

            {/* Scope switcher — everything below follows the selected account */}
            {snapshots.length > 1 ? (
              <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
                <Chip label="All" active={effectiveFilter === 'all'} onPress={() => setAccountFilter('all')} />
                {snapshots.map((s) => {
                  const key = accountKey(s);
                  return <Chip key={key} label={s.account_label ?? s.broker} active={effectiveFilter === key} onPress={() => setAccountFilter(key)} />;
                })}
              </Row>
            ) : null}

            {/* Value + range delta */}
            <Stack gap={spacing.xs}>
              <Eyebrow>{selected ? `${selected.account_label ?? selected.broker} · value` : 'portfolio value'}</Eyebrow>
              {snapshot.isLoading ? (
                <Skeleton height={44} width="65%" />
              ) : (
                <Text variant="display">{usd0(value)}</Text>
              )}
              {history.isLoading && !win ? (
                <Skeleton height={18} width={160} />
              ) : (
                <Row gap={spacing.sm} align="baseline">
                  <Delta value={change} size="sm" />
                  <Pct value={changePct} signed pnl size="sm" />
                  <Eyebrow>{rangeLabel}</Eyebrow>
                </Row>
              )}
            </Stack>

            {/* Equity curve (long-press to scrub) + range row */}
            {history.isLoading && !win ? (
              <Skeleton height={144} />
            ) : points.length > 1 ? (
              <EquityScrub points={points} height={144} />
            ) : (
              <View style={{ height: 144, alignItems: 'center', justifyContent: 'center' }}>
                <Text variant="caption" color="textFaint">No history for {range}</Text>
              </View>
            )}
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

            {/* Stat pills — P&L row follows the range window; money row is live */}
            <Stack gap={spacing.sm}>
              <Row gap={spacing.sm}>
                <StatPill label={`realized · ${rangeLabel}`} loading={history.isLoading && !win}>
                  <Delta value={win?.realized ?? 0} size="sm" />
                </StatPill>
                <StatPill label={`unrealized · ${rangeLabel}`} loading={history.isLoading && !win}>
                  <Delta value={win?.unrealized_change ?? 0} size="sm" />
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

            <Eyebrow>{selected ? `holdings · ${selected.account_label ?? selected.broker}` : 'holdings'}</Eyebrow>
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
