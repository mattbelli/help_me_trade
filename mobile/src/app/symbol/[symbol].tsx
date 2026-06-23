import { useState } from 'react';
import { ScrollView, TextInput, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import {
  useBars,
  useCreatePriceAlert,
  useDeletePriceAlert,
  usePriceAlerts,
  useWatchlistMarket,
} from '@/api/queries';
import type { PriceAlert, WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  Button,
  Card,
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
import { colors, hairline, radius, spacing } from '@/theme';

export default function SymbolDetailScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ symbol: string }>();
  const symbol = typeof params.symbol === 'string' ? params.symbol : (params.symbol?.[0] ?? '');

  const [chartWidth, setChartWidth] = useState(0);
  const [alertPrice, setAlertPrice] = useState('');
  const [direction, setDirection] = useState<'above' | 'below'>('above');

  const market = useWatchlistMarket(symbol ? [symbol] : undefined);
  const quote = symbol ? market.data?.quotes[symbol] : undefined;
  const bars = useBars(symbol, '1Day', 120);
  const alerts = usePriceAlerts(symbol);
  const createAlert = useCreatePriceAlert();
  const deleteAlert = useDeletePriceAlert();

  const parsedPrice = Number(alertPrice);
  const canAddAlert = Number.isFinite(parsedPrice) && parsedPrice > 0 && !createAlert.isPending;

  const barCloses = bars.data?.bars.map((b) => b.close) ?? [];

  if (market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={market.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{
          paddingTop: spacing.sm,
          paddingHorizontal: spacing.xl,
          paddingBottom: 110,
        }}
      >
        <Stack gap={spacing.xl}>
          <BackRow onBack={() => router.back()} />

          <Stack gap={spacing.sm}>
            <Text variant="display">{symbol || '—'}</Text>
            {market.isLoading ? (
              <Skeleton height={36} width="50%" />
            ) : quote ? (
              <Row gap={spacing.md} align="baseline">
                <Money value={quote.price} size="lg" />
                <Pct value={quote.change_pct} signed pnl />
              </Row>
            ) : (
              <Text variant="body" color="textMuted">
                No quote available
              </Text>
            )}
          </Stack>

          <View onLayout={(e) => setChartWidth(e.nativeEvent.layout.width)}>
            {bars.isLoading || chartWidth === 0 ? (
              <Skeleton height={160} />
            ) : barCloses.length > 1 ? (
              <Sparkline
                data={barCloses}
                width={chartWidth}
                height={160}
                fill
                strokeWidth={2}
              />
            ) : (
              <View style={{ height: 160, alignItems: 'center', justifyContent: 'center' }}>
                <Text variant="caption" color="textFaint">
                  No chart data
                </Text>
              </View>
            )}
          </View>

          {quote ? <KeyInfoGrid quote={quote} /> : null}

          <Stack gap={spacing.md}>
            <Eyebrow>price alerts</Eyebrow>

            <Card>
              <Stack gap={spacing.md}>
                <Row gap={spacing.sm}>
                  <TextInput
                    value={alertPrice}
                    onChangeText={setAlertPrice}
                    placeholder="Price"
                    placeholderTextColor={colors.textFaint}
                    keyboardType="decimal-pad"
                    selectionColor={colors.accent}
                    style={{
                      flex: 1,
                      color: colors.text,
                      fontSize: 16,
                      paddingHorizontal: spacing.lg,
                      paddingVertical: spacing.md,
                      backgroundColor: colors.bgInset,
                      borderRadius: radius.lg,
                      borderWidth: hairline,
                      borderColor: colors.border,
                    }}
                  />
                  <DirectionToggle value={direction} onChange={setDirection} />
                </Row>
                <Button
                  label="Add alert"
                  onPress={() => {
                    if (!canAddAlert || !symbol) return;
                    createAlert.mutate({ symbol, price: parsedPrice, direction });
                    setAlertPrice('');
                  }}
                  disabled={!canAddAlert}
                />
              </Stack>
            </Card>

            {alerts.isLoading ? (
              <Stack gap={spacing.sm}>
                <Skeleton height={44} />
                <Skeleton height={44} />
              </Stack>
            ) : alerts.isError ? (
              <ErrorState onRetry={alerts.refetch} />
            ) : (alerts.data?.alerts.length ?? 0) === 0 ? (
              <Text variant="body" color="textMuted">
                No alerts set for {symbol}
              </Text>
            ) : (
              <Stack gap={spacing.sm}>
                {alerts.data?.alerts.map((alert) => (
                  <AlertRow
                    key={alert.id}
                    alert={alert}
                    onDelete={() => deleteAlert.mutate(alert.id)}
                    deleting={deleteAlert.isPending}
                  />
                ))}
              </Stack>
            )}
          </Stack>
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function BackRow({ onBack }: { onBack: () => void }) {
  return (
    <PressableScale onPress={onBack} haptic>
      <Row gap={spacing.xs} align="center">
        <Icon name="chevron-left" size={20} color={colors.textMuted} />
        <Text variant="body" color="textMuted">
          Back
        </Text>
      </Row>
    </PressableScale>
  );
}

function KeyInfoGrid({ quote }: { quote: WatchlistQuote }) {
  return (
    <Row gap={spacing.md} style={{ flexWrap: 'wrap' }}>
      <InfoCell label="Prev close" value={quote.prev_close} money />
      <InfoCell label="Day high" value={quote.day_high} money />
      <InfoCell label="Day low" value={quote.day_low} money />
      <InfoCell label="Volume" value={quote.volume} volume />
    </Row>
  );
}

function InfoCell({
  label,
  value,
  money,
  volume,
}: {
  label: string;
  value?: number;
  money?: boolean;
  volume?: boolean;
}) {
  return (
    <Card style={{ flex: 1, minWidth: '45%', padding: spacing.md }}>
      <Stack gap={spacing.xs}>
        <Eyebrow>{label}</Eyebrow>
        {value == null ? (
          <Text variant="bodyStrong">—</Text>
        ) : money ? (
          <Money value={value} size="sm" />
        ) : volume ? (
          <Text variant="figureSm">{formatVolume(value)}</Text>
        ) : (
          <Text variant="bodyStrong">{value}</Text>
        )}
      </Stack>
    </Card>
  );
}

function DirectionToggle({
  value,
  onChange,
}: {
  value: 'above' | 'below';
  onChange: (v: 'above' | 'below') => void;
}) {
  return (
    <Row gap={spacing.xs}>
      {(['above', 'below'] as const).map((opt) => {
        const active = value === opt;
        return (
          <PressableScale key={opt} onPress={() => onChange(opt)} haptic>
            <View
              style={{
                paddingHorizontal: spacing.md,
                paddingVertical: spacing.sm,
                borderRadius: radius.pill,
                backgroundColor: active ? colors.accentWash : colors.surface,
                borderWidth: 1,
                borderColor: active ? colors.accent : colors.border,
              }}
            >
              <Text variant="callout" style={{ color: active ? colors.accent : colors.textMuted }}>
                {opt === 'above' ? 'Above' : 'Below'}
              </Text>
            </View>
          </PressableScale>
        );
      })}
    </Row>
  );
}

function AlertRow({
  alert,
  onDelete,
  deleting,
}: {
  alert: PriceAlert;
  onDelete: () => void;
  deleting?: boolean;
}) {
  return (
    <Row justify="space-between" align="center">
      <Row gap={spacing.xs} align="center">
        <Text variant="body">{alert.direction}</Text>
        <Money value={alert.price} size="sm" />
      </Row>
      <PressableScale onPress={onDelete} disabled={deleting} haptic>
        <View style={{ padding: spacing.sm }}>
          <Icon name="close" size={18} color={colors.textMuted} />
        </View>
      </PressableScale>
    </Row>
  );
}

function formatVolume(value: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(
    value,
  );
}
