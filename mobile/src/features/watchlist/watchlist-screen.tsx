import { RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';

import { useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  Money,
  Pct,
  Row,
  Screen,
  Skeleton,
  Sparkline,
  Stack,
  Text,
} from '@/components/ui';
import { colors, spacing } from '@/theme';

export function WatchlistScreen() {
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
          <Header />
          <ErrorState onRetry={watchlist.refetch} />
        </View>
      </Screen>
    );
  }

  if (market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing['4xl'], paddingHorizontal: spacing.xl }}>
          <Header />
          <ErrorState onRetry={market.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <FlashList
        data={symbols}
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
            <Header />
          </View>
        }
        renderItem={({ item: symbol }) => (
          <WatchlistRow
            symbol={symbol}
            quote={quotes[symbol]}
            loading={marketLoading && !quotes[symbol]}
          />
        )}
        ListEmptyComponent={
          loading ? (
            <Stack gap={spacing.md}>
              <Skeleton height={56} />
              <Skeleton height={56} />
              <Skeleton height={56} />
            </Stack>
          ) : (
            <EmptyState
              title="Watchlist empty"
              subtitle="Add symbols from the web app to see them here."
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
        watchlist
      </Text>
      <Text variant="title">Symbols</Text>
    </Stack>
  );
}

function WatchlistRow({
  symbol,
  quote,
  loading,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
}) {
  return (
    <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm }}>
      <Text variant="bodyStrong" style={{ width: 72 }}>
        {symbol}
      </Text>

      {quote && quote.sparkline.length > 1 ? (
        <Sparkline data={quote.sparkline} width={56} height={22} />
      ) : (
        <View style={{ width: 56, height: 22 }} />
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
  );
}
