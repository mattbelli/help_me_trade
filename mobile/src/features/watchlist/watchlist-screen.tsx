import { useState } from 'react';
import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';

import { useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
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
import { colors, hairline, spacing } from '@/theme';

export function WatchlistScreen() {
  const router = useRouter();
  const [columns, setColumns] = useState<1 | 2>(1);

  const watchlist = useWatchlist();
  const symbols = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};

  const { refreshing, onRefresh } = usePullRefresh(() =>
    Promise.all([watchlist.refetch(), market.refetch()]),
  );

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  if (watchlist.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
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
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
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
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
        }
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110 }}
        ListHeaderComponent={
          <View style={{ paddingTop: spacing.sm, paddingBottom: spacing.lg }}>
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
        <Eyebrow color="accent">watchlist</Eyebrow>
        <Text variant="title">Symbols</Text>
      </Stack>
      <Row gap={spacing.sm}>
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
    <PressableScale onPress={onPress} style={{ flex: 1 }}>
      <Stack
        gap={2}
        style={{
          paddingVertical: spacing.sm,
          borderBottomWidth: hairline,
          borderBottomColor: colors.border,
        }}
      >
        <Text variant="bodyStrong">{symbol}</Text>
        {loading ? (
          <Skeleton height={18} />
        ) : quote ? (
          <Row gap={spacing.xs} align="center">
            <Money value={quote.price} size="sm" />
            <Pct value={quote.change_pct} size="sm" signed pnl />
          </Row>
        ) : (
          <Skeleton height={18} />
        )}
      </Stack>
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
