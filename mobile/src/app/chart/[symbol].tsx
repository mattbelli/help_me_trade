import { useState } from 'react';
import { View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import { useBars, useWatchlistMarket } from '@/api/queries';
import { CandleChart, type ChartMode } from '@/features/chart/candle-chart';
import { ChartPill } from '@/features/chart/chart-pill';
import { INTERVALS, intervalFor, type IntervalKey } from '@/features/chart/ranges';
import { sessionView } from '@/features/watchlist/session';
import { Icon } from '@/components/ui/icon';
import { Money, Pct, PressableScale, Row, Screen, Stack, Text } from '@/components/ui';
import { colors, spacing } from '@/theme';

export default function FullChartScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ symbol: string; interval?: string; mode?: string }>();
  const symbol = typeof params.symbol === 'string' ? params.symbol : (params.symbol?.[0] ?? '');

  const [interval, setInterval] = useState<IntervalKey>(
    INTERVALS.some((i) => i.key === params.interval) ? (params.interval as IntervalKey) : '1D',
  );
  const [mode, setMode] = useState<ChartMode>(params.mode === 'line' ? 'line' : 'candle');

  const iv = intervalFor(interval);
  const bars = useBars(symbol, iv.timeframe, iv.days);
  const market = useWatchlistMarket(symbol ? [symbol] : undefined);
  const quote = symbol ? market.data?.quotes[symbol] : undefined;
  const sv = quote ? sessionView(quote) : null;

  return (
    <Screen edges={{ top: true, bottom: true }}>
      <View style={{ flex: 1, paddingHorizontal: spacing.lg }}>
        <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm }}>
          <Stack gap={2}>
            <Text variant="title">{symbol || '—'}</Text>
            {sv ? (
              <Row gap={spacing.sm} align="baseline">
                <Money value={sv.last} size="sm" />
                <Pct value={sv.changePct} signed pnl size="sm" />
                {sv.badge ? (
                  <Text variant="caption" style={{ color: colors.warm, letterSpacing: 0.6 }}>
                    {sv.badge}
                  </Text>
                ) : null}
              </Row>
            ) : null}
          </Stack>
          <PressableScale onPress={() => router.back()} haptic>
            <View style={{ padding: spacing.sm }}>
              <Icon name="close" size={22} color={colors.textMuted} />
            </View>
          </PressableScale>
        </Row>

        <View style={{ flex: 1 }}>
          <CandleChart
            bars={bars.data?.bars ?? []}
            mode={mode}
            livePrice={sv?.last}
            fullControl
            showLegend
            loading={bars.isLoading}
          />
        </View>

        <Stack gap={spacing.sm} style={{ paddingVertical: spacing.md }}>
          <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
            {INTERVALS.map((i) => (
              <ChartPill key={i.key} label={i.key} active={i.key === interval} onPress={() => setInterval(i.key)} />
            ))}
          </Row>
          <Row gap={spacing.xs}>
            <ChartPill label="Line" active={mode === 'line'} onPress={() => setMode('line')} />
            <ChartPill label="Candle" active={mode === 'candle'} onPress={() => setMode('candle')} />
          </Row>
        </Stack>
      </View>
    </Screen>
  );
}
