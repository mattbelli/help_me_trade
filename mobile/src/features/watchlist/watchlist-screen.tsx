/**
 * Watchlist — Robinhood-style rows.
 *  1-col: taller rows, no column-header labels. Per row:
 *         SYM · [H / LAST / L stacked] · [CHG% / CHG$ stacked] · big intraday day-chart
 *         (time-of-day extent + pulsing live dot).
 *  2-col: dense grid — twice the tickers (symbol + last + chg% + mini spark).
 *
 * Figures are tabular Menlo via Money/Pct/Delta. Real fields from
 * /api/watchlist/market: price, change_pct, sparkline, prev_close, volume,
 * day_high, day_low. change$ = price − prev_close.
 */
import { Dimensions, RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { useState } from 'react';

import { useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  Delta,
  EmptyState,
  ErrorState,
  Eyebrow,
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
import { colors, hairline, pnlColor, spacing } from '@/theme';

import { MiniDayChart } from './mini-day-chart';

/** Prefer the real intraday series; fall back to the daily sparkline when empty. */
function preferIntraday(q: WatchlistQuote): boolean {
  return !!q.intraday && q.intraday.length >= 2;
}
function hasChartData(q: WatchlistQuote): boolean {
  return preferIntraday(q) || q.sparkline.length > 1;
}
function fmtVol(v: number | undefined): string {
  if (v == null) return '';
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return `${v.toFixed(0)}`;
}

const SCREEN_W = Dimensions.get('window').width;
const SYM_W = 50;
const PRICE_W = 96; // fits a 4-digit price at the prominent "md" size ($1,085.33)
const CHG_W = 64;
// extra spacing.sm = breathing gap to the left of the CHG column.
const CHART_W = Math.max(90, SCREEN_W - 2 * spacing.xl - SYM_W - PRICE_W - CHG_W - spacing.md - spacing.sm);

// ─── Header (title + toggle + alerts) ─────────────────────────────────────────
function WatchlistHeader({
  columns,
  onToggleColumns,
  onOpenAlerts,
}: {
  columns: 1 | 2;
  onToggleColumns: () => void;
  onOpenAlerts: () => void;
}) {
  return (
    <Row justify="space-between" align="flex-start" style={{ paddingTop: spacing.sm, paddingBottom: spacing.sm }}>
      <Eyebrow color="accent">watchlist</Eyebrow>
      <Row gap={spacing.xs}>
        <PressableScale onPress={onToggleColumns} haptic>
          <View style={{ padding: spacing.sm }}>
            <Icon name={columns === 1 ? 'columns' : 'list'} size={22} color={colors.textMuted} />
          </View>
        </PressableScale>
        <PressableScale onPress={onOpenAlerts} haptic>
          <View style={{ padding: spacing.sm }}>
            <Icon name="bell" size={22} color={colors.textMuted} />
          </View>
        </PressableScale>
      </Row>
    </Row>
  );
}

// ─── 1-col rich row ───────────────────────────────────────────────────────────
function WatchlistRow({
  symbol,
  quote,
  loading,
  onPress,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
  onPress: () => void;
}) {
  const changeDol = quote?.prev_close != null ? quote.price - quote.prev_close : undefined;
  const high = quote?.day_high;
  const low = quote?.day_low;

  return (
    <PressableScale onPress={onPress}>
      <Row align="center" style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }} gap={0}>
        {/* SYM + volume */}
        <View style={{ width: SYM_W }}>
          <Text variant="bodyStrong" numberOfLines={1}>{symbol}</Text>
          {!loading && quote ? (
            <Text variant="caption" style={{ color: colors.textFaint }} numberOfLines={1}>
              {fmtVol(quote.volume)}
            </Text>
          ) : null}
        </View>

        {/* H / LAST / L */}
        <View style={{ width: PRICE_W, alignItems: 'flex-end' }}>
          {loading ? (
            <Skeleton height={36} width={64} />
          ) : quote ? (
            <>
              <Text variant="caption" style={{ color: colors.textFaint }} numberOfLines={1}>
                {high != null ? high.toFixed(2) : '—'}
              </Text>
              <Money value={quote.price} size="md" numberOfLines={1} />
              <Text variant="caption" style={{ color: colors.textFaint }} numberOfLines={1}>
                {low != null ? low.toFixed(2) : '—'}
              </Text>
            </>
          ) : (
            <Text variant="figureSm" color="textFaint">—</Text>
          )}
        </View>

        {/* CHG% / CHG$  (room for a future "% above expected" column alongside) */}
        <View style={{ width: CHG_W, alignItems: 'flex-end', justifyContent: 'center', marginLeft: spacing.sm }}>
          {loading ? (
            <Skeleton height={32} width={56} />
          ) : quote ? (
            <>
              <Pct value={quote.change_pct} size="sm" signed pnl numberOfLines={1} />
              {changeDol != null ? (
                <Delta value={changeDol} size="sm" compact numberOfLines={1} style={{ marginTop: 4 }} />
              ) : null}
            </>
          ) : (
            <Text variant="figureSm" color="textFaint">—</Text>
          )}
        </View>

        {/* Day chart */}
        <View style={{ width: CHART_W, alignItems: 'flex-end', paddingLeft: spacing.md }}>
          {loading ? (
            <Skeleton height={46} width={CHART_W - spacing.md} />
          ) : quote && hasChartData(quote) ? (
            <MiniDayChart
              data={preferIntraday(quote) ? quote.intraday! : quote.sparkline}
              intraday={preferIntraday(quote)}
              change={quote.change_pct}
              width={CHART_W - spacing.md}
              height={46}
            />
          ) : (
            <View style={{ width: CHART_W - spacing.md, height: 46 }} />
          )}
        </View>
      </Row>
    </PressableScale>
  );
}

// ─── 2-col dense cell ─────────────────────────────────────────────────────────
function WatchlistCard({
  symbol,
  quote,
  loading,
  onPress,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
  onPress: () => void;
}) {
  return (
    <PressableScale onPress={onPress} style={{ flex: 1 }}>
      <Stack gap={4} style={{ flex: 1, paddingVertical: spacing.sm + 1, paddingHorizontal: spacing.xs, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
        <Row justify="space-between" align="center" gap={spacing.xs}>
          <Text variant="bodyStrong" numberOfLines={1} style={{ flex: 1 }}>{symbol}</Text>
          {!loading && quote && quote.sparkline.length > 1 ? (
            <Sparkline data={quote.sparkline} width={40} height={18} color={pnlColor(quote.change_pct)} strokeWidth={1.3} />
          ) : null}
        </Row>
        {loading ? (
          <Skeleton height={13} />
        ) : quote ? (
          <Row gap={spacing.xs} align="center">
            <Money value={quote.price} size="sm" numberOfLines={1} />
            <Pct value={quote.change_pct} size="sm" signed pnl numberOfLines={1} />
          </Row>
        ) : (
          <Text variant="figureSm" color="textFaint">—</Text>
        )}
      </Stack>
    </PressableScale>
  );
}

function SkeletonRows() {
  return (
    <Stack gap={0}>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <View key={i} style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
          <Row align="center" gap={0}>
            <View style={{ width: SYM_W }}><Skeleton height={16} width={40} /></View>
            <View style={{ width: PRICE_W, alignItems: 'flex-end' }}><Skeleton height={36} width={64} /></View>
            <View style={{ width: CHG_W, alignItems: 'flex-end' }}><Skeleton height={32} width={56} /></View>
            <View style={{ width: CHART_W, alignItems: 'flex-end', paddingLeft: spacing.md }}><Skeleton height={46} width={CHART_W - spacing.md} /></View>
          </Row>
        </View>
      ))}
    </Stack>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export function WatchlistScreen() {
  const router = useRouter();
  const [columns, setColumns] = useState<1 | 2>(1);

  const watchlist = useWatchlist();
  const symbols: string[] = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};

  const { refreshing, onRefresh } = usePullRefresh(() => Promise.all([watchlist.refetch(), market.refetch()]));

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  const header = (
    <WatchlistHeader
      columns={columns}
      onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
      onOpenAlerts={() => router.push('/alerts')}
    />
  );

  if (watchlist.isError || market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          {header}
          <ErrorState onRetry={() => { void watchlist.refetch(); void market.refetch(); }} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <FlashList
        key={`watchlist-${columns}`}
        data={symbols}
        numColumns={columns}
        keyExtractor={(symbol) => symbol}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110, paddingTop: spacing.sm }}
        ListHeaderComponent={header}
        renderItem={({ item: symbol }) =>
          columns === 1 ? (
            <WatchlistRow symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => router.push(`/symbol/${symbol}`)} />
          ) : (
            <WatchlistCard symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => router.push(`/symbol/${symbol}`)} />
          )
        }
        ListEmptyComponent={
          loading ? (
            <SkeletonRows />
          ) : (
            <EmptyState title="Watchlist empty" subtitle="Add symbols from the web app." />
          )
        }
      />
    </Screen>
  );
}
