/**
 * Watchlist screen — data-rich, terminal-style table.
 *
 * Layout:
 *  1-col (rich): hairline-divided rows with a pinned column header.
 *                SYM · LAST · CHG% · CHG$ · VOL · 7D-SPARK
 *  2-col (dense): compact grid — twice the tickers, symbol + last + chg% + mini-spark.
 *
 * All numeric figures use Money / Pct / Delta primitives (tabular Menlo) so digits
 * stay fixed-width. Column widths are fixed; SYM is flex:1 — together they mirror
 * the ColumnHeader labels exactly, so nothing drifts.
 *
 * Data source: /api/watchlist/market — Alpaca 12-day bars.
 * Real fields per symbol: price, change_pct, sparkline[7], prev_close, volume,
 *   day_high, day_low. change$ is derived: price − prev_close.
 */

import { View, RefreshControl } from 'react-native';
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

// ─── Column widths (1-col layout) ─────────────────────────────────────────────
// SYM is flex:1 (grows). All other columns are fixed so header labels and data
// figures sit in precisely the same horizontal position.
// Width rationale at figureSm 13pt Menlo (≈7.8pt/char):
//   "$298.76" = 7 chars ≈ 55pt  → 58pt
//   "+2.96%"  = 6 chars ≈ 47pt  → 50pt
//   "+$1.91"  = 6 chars ≈ 47pt  → 54pt  (signed $, so slightly wider safe margin)
//   "2.5M"    = 4 chars ≈ 31pt  → 36pt
//   sparkline  56pt
// Sized for 4-digit prices ($1,085.12), −10.42%, −$126.72, 832K. All figures are
// single-line (numberOfLines=1) so a big value never wraps — it just fills its cell.
const COL = {
  last: 74,
  chgPct: 58,
  chgDol: 64,
  vol: 42,
  spark: 48,
} as const;

// ─── Volume formatter ─────────────────────────────────────────────────────────
function fmtVol(v: number | undefined): string {
  if (v == null) return '—';
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return v.toFixed(0);
}

// ─── Column label ─────────────────────────────────────────────────────────────
function ColLabel({ children, right }: { children: string; right?: boolean }) {
  return (
    <Text
      variant="label"
      upper
      style={{
        color: colors.textFaint,
        fontSize: 10,
        letterSpacing: 0.5,
        textAlign: right ? 'right' : 'left',
      }}
    >
      {children}
    </Text>
  );
}

// ─── Column header row — mirrors WatchlistRow flex layout exactly ──────────────
function ColumnHeader() {
  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        borderTopWidth: hairline,
        borderBottomWidth: hairline,
        borderColor: colors.border,
        paddingVertical: 6,
        marginTop: spacing.xs,
      }}
    >
      <View style={{ flex: 1 }}>
        <ColLabel>SYM</ColLabel>
      </View>
      <View style={{ width: COL.last }}>
        <ColLabel right>LAST</ColLabel>
      </View>
      <View style={{ width: COL.chgPct }}>
        <ColLabel right>CHG%</ColLabel>
      </View>
      <View style={{ width: COL.chgDol }}>
        <ColLabel right>CHG$</ColLabel>
      </View>
      <View style={{ width: COL.vol }}>
        <ColLabel right>VOL</ColLabel>
      </View>
      <View style={{ width: COL.spark }}>
        <ColLabel right>7D</ColLabel>
      </View>
    </View>
  );
}

// ─── Header (title + icons + optional column header) ─────────────────────────
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
    <Stack gap={spacing.sm} style={{ paddingTop: spacing.sm }}>
      <Row justify="space-between" align="flex-start">
        <Stack gap={2}>
          <Eyebrow color="accent">watchlist</Eyebrow>
          <Text variant="title">Symbols</Text>
        </Stack>
        <Row gap={spacing.xs}>
          <PressableScale onPress={onToggleColumns} haptic>
            <View style={{ padding: spacing.sm }}>
              {columns === 1 ? (
                <Icon name="columns" size={22} color={colors.textMuted} />
              ) : (
                <Icon name="list" size={22} color={colors.textMuted} />
              )}
            </View>
          </PressableScale>
          <PressableScale onPress={onOpenAlerts} haptic>
            <View style={{ padding: spacing.sm }}>
              <Icon name="bell" size={22} color={colors.textMuted} />
            </View>
          </PressableScale>
        </Row>
      </Row>

      {columns === 1 && <ColumnHeader />}
    </Stack>
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
  const changeDol =
    quote != null && quote.prev_close != null
      ? quote.price - quote.prev_close
      : undefined;

  return (
    <PressableScale onPress={onPress}>
      <View
        style={{
          paddingVertical: spacing.sm + 2,
          borderBottomWidth: hairline,
          borderBottomColor: colors.border,
        }}
      >
        {/* Data row — flex layout mirrors ColumnHeader exactly */}
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          {/* SYM — grows to fill remaining space */}
          <View style={{ flex: 1 }}>
            <Text variant="bodyStrong" numberOfLines={1}>
              {symbol}
            </Text>
          </View>

          {/* LAST */}
          <View style={{ width: COL.last, alignItems: 'flex-end' }}>
            {loading ? (
              <Skeleton height={13} width={48} />
            ) : quote ? (
              <Money value={quote.price} size="sm" numberOfLines={1} />
            ) : (
              <Text variant="figureSm" color="textFaint">—</Text>
            )}
          </View>

          {/* CHG% */}
          <View style={{ width: COL.chgPct, alignItems: 'flex-end' }}>
            {loading ? (
              <Skeleton height={13} width={40} />
            ) : quote ? (
              <Pct value={quote.change_pct} size="sm" signed pnl numberOfLines={1} />
            ) : (
              <Text variant="figureSm" color="textFaint">—</Text>
            )}
          </View>

          {/* CHG$ */}
          <View style={{ width: COL.chgDol, alignItems: 'flex-end' }}>
            {loading ? (
              <Skeleton height={13} width={44} />
            ) : changeDol != null ? (
              <Delta value={changeDol} size="sm" compact numberOfLines={1} />
            ) : (
              <Text variant="figureSm" color="textFaint">—</Text>
            )}
          </View>

          {/* VOL */}
          <View style={{ width: COL.vol, alignItems: 'flex-end' }}>
            {loading ? (
              <Skeleton height={13} width={28} />
            ) : (
              <Text variant="figureSm" style={{ color: colors.textFaint }}>
                {quote ? fmtVol(quote.volume) : '—'}
              </Text>
            )}
          </View>

          {/* SPARK */}
          <View style={{ width: COL.spark, alignItems: 'flex-end' }}>
            {loading ? (
              <Skeleton height={26} width={COL.spark - 4} />
            ) : quote && quote.sparkline.length > 1 ? (
              <Sparkline
                data={quote.sparkline}
                width={COL.spark}
                height={26}
                color={pnlColor(quote.change_pct)}
                strokeWidth={1.5}
              />
            ) : (
              <View style={{ width: COL.spark, height: 26 }} />
            )}
          </View>
        </View>

        {/* Day range sub-line — only rendered when both fields are present */}
        {!loading && quote?.day_high != null && quote?.day_low != null && (
          <View style={{ marginTop: 2 }}>
            <Text
              variant="caption"
              style={{ color: colors.textFaint, fontSize: 11 }}
            >
              {`H ${quote.day_high.toFixed(2)}  ·  L ${quote.day_low.toFixed(2)}`}
            </Text>
          </View>
        )}
      </View>
    </PressableScale>
  );
}

// ─── 2-col dense cell ─────────────────────────────────────────────────────────
// Each cell gets equal horizontal padding so adjacent cells share a natural gutter.
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
      <Stack
        gap={4}
        style={{
          flex: 1,
          paddingVertical: spacing.sm + 1,
          paddingHorizontal: spacing.xs,
          borderBottomWidth: hairline,
          borderBottomColor: colors.border,
        }}
      >
        {/* Symbol row with mini sparkline */}
        <Row justify="space-between" align="center" gap={spacing.xs}>
          <Text variant="bodyStrong" numberOfLines={1} style={{ flex: 1 }}>
            {symbol}
          </Text>
          {!loading && quote && quote.sparkline.length > 1 ? (
            <Sparkline
              data={quote.sparkline}
              width={40}
              height={18}
              color={pnlColor(quote.change_pct)}
              strokeWidth={1.3}
            />
          ) : null}
        </Row>

        {/* Price + change% */}
        {loading ? (
          <Skeleton height={13} />
        ) : quote ? (
          <Row gap={spacing.xs} align="center">
            <Money value={quote.price} size="sm" />
            <Pct value={quote.change_pct} size="sm" signed pnl />
          </Row>
        ) : (
          <Text variant="figureSm" color="textFaint">—</Text>
        )}
      </Stack>
    </PressableScale>
  );
}

// ─── Loading skeleton rows (1-col) ────────────────────────────────────────────
function SkeletonRows() {
  return (
    <Stack gap={0}>
      {[54, 54, 54, 54, 54].map((h, i) => (
        <View
          key={i}
          style={{
            paddingVertical: spacing.sm + 2,
            borderBottomWidth: hairline,
            borderBottomColor: colors.border,
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <View style={{ flex: 1 }}>
              <Skeleton height={16} width={48} />
            </View>
            <View style={{ width: COL.last, alignItems: 'flex-end' }}>
              <Skeleton height={13} width={48} />
            </View>
            <View style={{ width: COL.chgPct, alignItems: 'flex-end' }}>
              <Skeleton height={13} width={40} />
            </View>
            <View style={{ width: COL.chgDol, alignItems: 'flex-end' }}>
              <Skeleton height={13} width={44} />
            </View>
            <View style={{ width: COL.vol, alignItems: 'flex-end' }}>
              <Skeleton height={13} width={28} />
            </View>
            <View style={{ width: COL.spark, alignItems: 'flex-end' }}>
              <Skeleton height={26} width={COL.spark - 4} />
            </View>
          </View>
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

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([watchlist.refetch(), market.refetch()]),
  );

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  function header(cols: 1 | 2) {
    return (
      <View style={{ paddingBottom: spacing.sm }}>
        <WatchlistHeader
          columns={cols}
          onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
          onOpenAlerts={() => router.push('/alerts')}
        />
      </View>
    );
  }

  if (watchlist.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          {header(columns)}
          <ErrorState onRetry={watchlist.refetch} />
        </View>
      </Screen>
    );
  }

  if (market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          {header(columns)}
          <ErrorState onRetry={market.refetch} />
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
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.accent}
          />
        }
        contentContainerStyle={{
          paddingHorizontal: spacing.xl,
          paddingBottom: 110,
          paddingTop: spacing.sm,
        }}
        ListHeaderComponent={header(columns)}
        renderItem={({ item: symbol }) =>
          columns === 1 ? (
            <WatchlistRow
              symbol={symbol}
              quote={quotes[symbol]}
              loading={marketLoading && !quotes[symbol]}
              onPress={() => router.push(`/symbol/${symbol}`)}
            />
          ) : (
            <WatchlistCard
              symbol={symbol}
              quote={quotes[symbol]}
              loading={marketLoading && !quotes[symbol]}
              onPress={() => router.push(`/symbol/${symbol}`)}
            />
          )
        }
        ListEmptyComponent={
          loading ? (
            columns === 1 ? (
              <SkeletonRows />
            ) : (
              <Stack gap={spacing.md} style={{ paddingTop: spacing.xs }}>
                <Skeleton height={54} />
                <Skeleton height={54} />
                <Skeleton height={54} />
                <Skeleton height={54} />
              </Stack>
            )
          ) : (
            <EmptyState
              title="Watchlist empty"
              subtitle="Add symbols from the web app."
            />
          )
        }
      />
    </Screen>
  );
}
