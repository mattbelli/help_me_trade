import { useState } from 'react';
import { LayoutAnimation, ScrollView, TextInput, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';

import {
  useBars,
  useCreatePriceAlert,
  useDeletePriceAlert,
  useEarningsHistory,
  usePriceAlerts,
  useWatchlistMarket,
} from '@/api/queries';
import type { EarningsEvent, PriceAlert, WatchlistQuote } from '@/api/types';
import { sessionView } from '@/features/watchlist/session';
import { Icon } from '@/components/ui/icon';
import {
  Button,
  BackRow,
  Card,
  Divider,
  ErrorState,
  Eyebrow,
  Money,
  Pct,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { CandleChart, type ChartMode } from '@/features/chart/candle-chart';
import { ChartPill } from '@/features/chart/chart-pill';
import {
  CHART_RANGES,
  INLINE_RANGE_KEYS,
  RANGE_TO_INTERVAL,
  rangeFor,
  type RangeKey,
} from '@/features/chart/ranges';
import { hairline, radius, spacing, useColors } from '@/theme';

const INLINE_RANGES = CHART_RANGES.filter((r) => INLINE_RANGE_KEYS.includes(r.key));

export default function SymbolDetailScreen() {
  const colors = useColors();
  const router = useRouter();
  const params = useLocalSearchParams<{ symbol: string }>();
  const symbol = typeof params.symbol === 'string' ? params.symbol : (params.symbol?.[0] ?? '');

  const [alertPrice, setAlertPrice] = useState('');
  const [direction, setDirection] = useState<'above' | 'below'>('above');
  const [infoOpen, setInfoOpen] = useState(false);
  const [range, setRange] = useState<RangeKey>('1M');
  const [chartMode, setChartMode] = useState<ChartMode>('candle');

  const activeRange = rangeFor(range);
  const market = useWatchlistMarket(symbol ? [symbol] : undefined);
  const quote = symbol ? market.data?.quotes[symbol] : undefined;
  const bars = useBars(symbol, activeRange.timeframe, activeRange.days);
  const earnings = useEarningsHistory(symbol, 6, infoOpen);
  const alerts = usePriceAlerts(symbol);

  const toggleInfo = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setInfoOpen((v) => !v);
  };
  const createAlert = useCreatePriceAlert();
  const deleteAlert = useDeletePriceAlert();

  const parsedPrice = Number(alertPrice);
  const canAddAlert = Number.isFinite(parsedPrice) && parsedPrice > 0 && !createAlert.isPending;

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
            <PressableScale onPress={toggleInfo} haptic>
              <Row gap={spacing.xs} align="center">
                <Text variant="display" style={{ fontSize: 30, lineHeight: 34 }}>
                  {symbol || '—'}
                </Text>
                <Icon
                  name={infoOpen ? 'chevron-up' : 'chevron-down'}
                  size={22}
                  color={infoOpen ? colors.accent : colors.textMuted}
                />
              </Row>
            </PressableScale>
            {market.isLoading ? (
              <Skeleton height={36} width={160} />
            ) : quote ? (
              (() => {
                const sv = sessionView(quote);
                return (
                  <Row gap={spacing.md} align="baseline">
                    <Money value={sv.last} size="lg" />
                    <Pct value={sv.changePct} signed pnl />
                    {sv.badge ? (
                      <Text variant="caption" style={{ color: colors.warm, letterSpacing: 0.6 }}>
                        {sv.badge}
                      </Text>
                    ) : null}
                  </Row>
                );
              })()
            ) : (
              <Text variant="body" color="textMuted">
                No quote available
              </Text>
            )}
          </Stack>

          {infoOpen ? (
            <CompanyPanel
              quote={quote}
              events={earnings.data?.events ?? []}
              loading={earnings.isLoading}
            />
          ) : null}

          <Stack gap={spacing.md}>
            <Row justify="flex-end">
              <PressableScale
                onPress={() =>
                  router.push(`/chart/${symbol}?interval=${RANGE_TO_INTERVAL[range]}&mode=${chartMode}`)
                }
                haptic
              >
                <Row
                  gap={spacing.xs}
                  align="center"
                  style={{
                    paddingHorizontal: spacing.md,
                    paddingVertical: spacing.xs,
                    borderRadius: radius.pill,
                    borderWidth: hairline,
                    borderColor: colors.border,
                    backgroundColor: colors.surface,
                  }}
                >
                  <Icon name="expand" size={14} color={colors.textMuted} />
                  <Text variant="caption" color="textMuted">
                    Expand
                  </Text>
                </Row>
              </PressableScale>
            </Row>
            <CandleChart
              bars={bars.data?.bars ?? []}
              mode={chartMode}
              livePrice={quote ? sessionView(quote).last : undefined}
              height={240}
              loading={bars.isLoading}
            />
            <Row justify="space-between" align="center">
              <Row gap={spacing.xs}>
                {INLINE_RANGES.map((r) => (
                  <ChartPill
                    key={r.key}
                    label={r.key}
                    active={r.key === range}
                    onPress={() => setRange(r.key)}
                  />
                ))}
              </Row>
              <Row gap={spacing.xs}>
                <ChartPill label="Line" active={chartMode === 'line'} onPress={() => setChartMode('line')} />
                <ChartPill label="Candle" active={chartMode === 'candle'} onPress={() => setChartMode('candle')} />
              </Row>
            </Row>
          </Stack>

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

function CompanyPanel({
  quote,
  events,
  loading,
}: {
  quote?: WatchlistQuote;
  events: EarningsEvent[];
  loading: boolean;
}) {
  const hasNext = Boolean(quote?.next_earnings);
  const earningsLabel = hasNext ? 'Next earnings' : 'Last earnings';
  const earningsValue =
    fmtDate(hasNext ? quote?.next_earnings : quote?.last_earnings) ?? '—';

  return (
    <Card>
      <Stack gap={spacing.lg}>
        <Stack gap={spacing.md}>
          <Eyebrow>company</Eyebrow>
          <Row gap={spacing.md} style={{ flexWrap: 'wrap' }}>
            <StatCell label="Mkt cap" value={fmtMarketCap(quote?.market_cap)} />
            <StatCell label="P/E" value={fmtNum(quote?.pe)} />
            <StatCell label="EPS (ttm)" value={fmtUsd2(quote?.eps)} />
            <StatCell label="Div yield" value={fmtPctStr(quote?.div_yield)} />
            <StatCell label={earningsLabel} value={earningsValue} />
          </Row>
        </Stack>

        <Divider />

        <Stack gap={spacing.md}>
          <Eyebrow>EPS surprise · beat / miss</Eyebrow>
          {loading ? (
            <Stack gap={spacing.sm}>
              <Skeleton height={34} />
              <Skeleton height={34} />
              <Skeleton height={34} />
            </Stack>
          ) : events.length === 0 ? (
            <Text variant="body" color="textMuted">
              No earnings history
            </Text>
          ) : (
            <Stack gap={spacing.md}>
              {events.map((event) => (
                <EpsBeatRow key={event.period} event={event} />
              ))}
            </Stack>
          )}
        </Stack>
      </Stack>
    </Card>
  );
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <Stack gap={spacing.xs} style={{ minWidth: '28%', flexGrow: 1 }}>
      <Eyebrow>{label}</Eyebrow>
      <Text variant="figureSm">{value}</Text>
    </Stack>
  );
}

function EpsBeatRow({ event }: { event: EarningsEvent }) {
  const colors = useColors();
  const surprise = event.surprise_pct;
  const beat = (surprise ?? 0) >= 0;
  // Cap the bar at ±10% surprise so a single blowout doesn't dwarf the rest.
  const frac = surprise == null ? 0 : Math.min(Math.abs(surprise) / 10, 1);

  return (
    <Stack gap={spacing.xs}>
      <Row justify="space-between" align="baseline">
        <Row gap={spacing.sm} align="baseline">
          <Text variant="bodyStrong">{event.label}</Text>
          <Text variant="caption" color="textMuted">
            {fmtUsd2(event.eps_actual)} vs {fmtUsd2(event.eps_estimate)} est
          </Text>
        </Row>
        {surprise == null ? (
          <Text variant="figureSm" color="textFaint">
            —
          </Text>
        ) : (
          <Pct value={surprise} signed pnl size="sm" />
        )}
      </Row>
      <View
        style={{
          height: 4,
          borderRadius: radius.pill,
          backgroundColor: colors.bgInset,
          overflow: 'hidden',
        }}
      >
        <View
          style={{
            width: `${frac * 100}%`,
            height: 4,
            borderRadius: radius.pill,
            backgroundColor: beat ? colors.positive : colors.negative,
          }}
        />
      </View>
    </Stack>
  );
}

function fmtMarketCap(value?: number | null): string {
  // Manual T/B/M — RN's Hermes Intl lacks compact-currency notation.
  if (value == null) return '—';
  const abs = Math.abs(value);
  const tiers: [number, string][] = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
  ];
  for (const [div, suffix] of tiers) {
    if (abs >= div) return `$${(value / div).toFixed(2)}${suffix}`;
  }
  return `$${value.toFixed(0)}`;
}

function fmtNum(value?: number | null): string {
  return value == null ? '—' : value.toFixed(1);
}

function fmtUsd2(value?: number | null): string {
  return value == null ? '—' : `$${value.toFixed(2)}`;
}

function fmtPctStr(value?: number | null): string {
  return value == null ? '—' : `${value.toFixed(2)}%`;
}

const MONTHS_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const;

function fmtDate(iso?: string | null): string | null {
  if (!iso) return null;
  const [year, month, day] = iso.split('-');
  const mi = Number(month) - 1;
  const name = MONTHS_SHORT[mi];
  if (!name || !day) return iso;
  return `${name} ${Number(day)}, ${year}`;
}

function DirectionToggle({
  value,
  onChange,
}: {
  value: 'above' | 'below';
  onChange: (v: 'above' | 'below') => void;
}) {
  const colors = useColors();
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
  const colors = useColors();
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
