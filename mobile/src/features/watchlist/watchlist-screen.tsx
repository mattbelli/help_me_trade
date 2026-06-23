import { useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';

import { useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  Card,
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
import { colors, spacing } from '@/theme';

export function WatchlistScreen() {
  const router = useRouter();
  const [columns, setColumns] = useState<1 | 2>(1);

  const watchlist = useWatchlist();
  const symbols = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  if (watchlist.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
          <Header
            columns={columns}
            onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
            onOpenAlerts={() => router.push('/alerts')}
          />
          <ErrorState onRetry={watchlist.refetch} />
        </View>
      </Screen>
    );
  }

  if (market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
          <Header
            columns={columns}
            onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
            onOpenAlerts={() => router.push('/alerts')}
          />
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
            refreshing={watchlist.isRefetching || market.isRefetching}
            onRefresh={() => {
              void watchlist.refetch();
              void market.refetch();
            }}
            tintColor={colors.accent}
          />
        }
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: spacing['3xl'] }}
        ListHeaderComponent={
          <View style={{ paddingTop: spacing['4xl'], paddingBottom: spacing.lg }}>
            <Header
              columns={columns}
              onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
              onOpenAlerts={() => router.push('/alerts')}
            />
          </View>
        }
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
            <Stack gap={spacing.md}>
              <Skeleton height={56} />
              <Skeleton height={56} />
              <Skeleton height={56} />
            </Stack>
          ) : (
            <EmptyState title="Watchlist empty" subtitle="Add symbols from the web app." />
          )
        }
      />
    </Screen>
  );
}

function Header({
  columns,
  onToggleColumns,
  onOpenAlerts,
}: {
  columns: 1 | 2;
  onToggleColumns: () => void;
  onOpenAlerts: () => void;
}) {
  return (
    <Row justify="space-between" align="flex-start">
      <Stack gap={2}>
        <Text variant="label" upper color="accent">
          watchlist
        </Text>
        <Text variant="title">Symbols</Text>
      </Stack>
      <Row gap={spacing.sm}>
        <PressableScale onPress={onToggleColumns} haptic>
          <View style={{ padding: spacing.sm }}>
            {columns === 1 ? (
              <Icon name="watchlist" size={22} color={colors.textMuted} />
            ) : (
              <ColumnToggleIcon />
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
  );
}

function ColumnToggleIcon() {
  return (
    <Row gap={3}>
      <View style={{ width: 8, height: 14, borderRadius: 2, backgroundColor: colors.textMuted }} />
      <View style={{ width: 8, height: 14, borderRadius: 2, backgroundColor: colors.textMuted }} />
    </Row>
  );
}

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
  const subtitle = quote ? formatQuoteSubtitle(quote) : '';

  return (
    <PressableScale onPress={onPress}>
      <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm }}>
        <Stack gap={2} style={{ flex: 1, minWidth: 72 }}>
          <Text variant="bodyStrong">{symbol}</Text>
          {subtitle ? (
            <Text variant="caption" color="textMuted">
              {subtitle}
            </Text>
          ) : null}
        </Stack>

        {quote && quote.sparkline.length > 1 ? (
          <Sparkline data={quote.sparkline} width={90} height={28} />
        ) : (
          <View style={{ width: 90, height: 28 }} />
        )}

        {loading ? (
          <Skeleton height={36} width={72} />
        ) : quote ? (
          <Stack gap={2} style={{ alignItems: 'flex-end', minWidth: 72 }}>
            <Money value={quote.price} size="sm" />
            <Pct value={quote.change_pct} size="sm" signed pnl />
          </Stack>
        ) : (
          <Skeleton height={36} width={72} />
        )}
      </Row>
    </PressableScale>
  );
}

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
    <PressableScale onPress={onPress} style={{ flex: 1, padding: spacing.xs }}>
      <Card style={{ padding: spacing.md }}>
        <Stack gap={spacing.sm}>
          <Text variant="bodyStrong">{symbol}</Text>
          {loading ? (
            <Skeleton height={32} />
          ) : quote ? (
            <>
              <Money value={quote.price} size="sm" />
              <Pct value={quote.change_pct} size="sm" signed pnl />
              {quote.sparkline.length > 1 ? (
                <Sparkline data={quote.sparkline} width={72} height={20} />
              ) : null}
            </>
          ) : (
            <Skeleton height={32} />
          )}
        </Stack>
      </Card>
    </PressableScale>
  );
}

function formatQuoteSubtitle(quote: WatchlistQuote): string {
  if (quote.day_high != null && quote.day_low != null) {
    return `H ${quote.day_high.toFixed(1)} · L ${quote.day_low.toFixed(1)}`;
  }
  if (quote.prev_close != null) {
    return `Prev ${quote.prev_close.toFixed(2)}`;
  }
  return '';
}
