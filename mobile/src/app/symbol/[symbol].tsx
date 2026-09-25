import { useCallback, useMemo, useState } from 'react';
import { ScrollView, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';

import {
  useBars,
  useCreatePriceAlert,
  useDeletePriceAlert,
  useEarningsHistory,
  usePriceAlerts,
  useTicker,
  useWatchlistMarket,
} from '@/api/queries';
import type { PriceAlert, TickerNewsItem, TickerProfile, WatchlistQuote } from '@/api/types';
import { sessionView } from '@/features/watchlist/session';
import { Icon } from '@/components/ui/icon';
import {
  BackRow,
  Divider,
  ErrorState,
  Eyebrow,
  Money,
  Pct,
  PressableScale,
  Row,
  Screen,
  Segmented,
  Skeleton,
  Stack,
  Text,
  formatUsd,
} from '@/components/ui';
import { SkiaChart, type ChartAlertLevel, type ChartMode } from '@/features/chart/skia-chart';
import { ChartPill } from '@/features/chart/chart-pill';
import { EpsChart } from '@/features/chart/eps-chart';
import { useLiveBars } from '@/features/chart/live-bars';
import { INTERVALS, intervalFor, type IntervalKey } from '@/features/chart/ranges';
import { decodeSymbolParam } from '@/lib/symbol-route';
import { useFocusSymbol } from '@/state/focused-symbols';
import { duration, hairline, haptics, radius, spacing, useColors, type Colors } from '@/theme';

const TABS = [
  { key: 'chart', label: 'chart' },
  { key: 'fundamentals', label: 'fundamentals' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

export default function SymbolDetailScreen() {
  const colors = useColors();
  const router = useRouter();
  const params = useLocalSearchParams<{ symbol: string }>();
  const rawSymbol = typeof params.symbol === 'string' ? params.symbol : (params.symbol?.[0] ?? '');
  const symbol = decodeSymbolParam(rawSymbol);

  const [tab, setTab] = useState<TabKey>('chart');
  const [interval, setInterval] = useState<IntervalKey>('1D');
  const [chartMode, setChartMode] = useState<ChartMode>('candle');

  const iv = intervalFor(interval);
  const market = useWatchlistMarket(symbol ? [symbol] : undefined);
  const quote = symbol ? market.data?.quotes[symbol] : undefined;
  const bars = useBars(symbol, iv.timeframe, iv.days);
  useFocusSymbol(symbol);
  const chartBars = useLiveBars(symbol, bars.data?.bars, iv.timeframe);
  // Today's daily bar for "Open" — tiny fetch, cached separately from the chart.
  const dailyBars = useBars(symbol, '1Day', 7);
  const ticker = useTicker(symbol);
  const earnings = useEarningsHistory(symbol, 6, tab === 'fundamentals');
  const alerts = usePriceAlerts(symbol);
  const createAlert = useCreatePriceAlert();
  const deleteAlert = useDeletePriceAlert();

  const sv = quote ? sessionView(quote) : null;
  const last = sv?.last;

  // Only trust the latest daily bar's open when it IS today's bar (pre-market it
  // is yesterday's — showing that as "Open" would lie).
  const dayOpen = useMemo(() => {
    const list = dailyBars.data?.bars;
    if (!list?.length) return null;
    const barDate = new Date(list[list.length - 1].time);
    const now = new Date();
    const sameDay =
      barDate.getFullYear() === now.getFullYear() &&
      barDate.getMonth() === now.getMonth() &&
      barDate.getDate() === now.getDate();
    return sameDay ? list[list.length - 1].open : null;
  }, [dailyBars.data]);

  const chartAlerts = useMemo<ChartAlertLevel[]>(
    () =>
      (alerts.data?.alerts ?? []).map((a) => ({
        id: a.id,
        price: a.price,
        direction: a.direction,
      })),
    [alerts.data],
  );

  // Create-from-chart (crosshair release pill). Smart default direction mirrors
  // the web chart: above if the picked price is over the last, below if under.
  const onCreateAlertAtPrice = useCallback(
    (price: number) => {
      if (!symbol) return;
      const rounded = Math.round(price * 100) / 100;
      const direction =
        last == null || rounded === last ? 'cross' : rounded > last ? 'above' : 'below';
      createAlert.mutate(
        { symbol, price: rounded, direction, repeat: false },
        { onSuccess: () => haptics.success() },
      );
    },
    [symbol, last, createAlert],
  );

  const metrics = ticker.data?.metrics;

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

  const rv = rvolView(quote?.rvol, colors);
  const marketItems: StatItem[] = [
    { label: 'Open', value: fmtMoney(dayOpen) },
    { label: 'Prev close', value: fmtMoney(quote?.prev_close) },
    { label: 'Day high', value: fmtMoney(quote?.day_high) },
    { label: 'Day low', value: fmtMoney(quote?.day_low) },
    { label: '52W high', value: fmtMoney(metrics?.week_52_high) },
    { label: '52W low', value: fmtMoney(metrics?.week_52_low) },
    { label: 'Volume', value: quote?.volume != null ? formatVolume(quote.volume) : '—' },
    { label: 'RVOL', value: rv.text, color: rv.color },
  ];

  const hasNextEarnings = Boolean(quote?.next_earnings);
  const revGrowth = metrics?.revenue_growth_yoy;
  const fundamentalItems: StatItem[] = [
    {
      label: 'Mkt cap',
      value: fmtMarketCap(
        quote?.market_cap ?? (metrics?.market_cap != null ? metrics.market_cap * 1e6 : null),
      ),
    },
    { label: 'P/E', value: fmtNum(quote?.pe ?? metrics?.pe_ttm) },
    { label: 'EPS (ttm)', value: fmtUsd2(quote?.eps ?? metrics?.eps_ttm) },
    { label: 'Div yield', value: fmtPctStr(quote?.div_yield ?? metrics?.div_yield) },
    { label: 'Beta', value: metrics?.beta != null ? metrics.beta.toFixed(2) : '—' },
    { label: 'P/S', value: fmtNum(metrics?.ps_ttm) },
    { label: 'ROE', value: fmtPctStr(metrics?.roe_ttm) },
    { label: 'Gross margin', value: fmtPctStr(metrics?.gross_margin) },
    { label: 'Net margin', value: fmtPctStr(metrics?.net_margin) },
    {
      label: 'Rev growth',
      value: revGrowth == null ? '—' : `${revGrowth >= 0 ? '+' : ''}${revGrowth.toFixed(1)}%`,
      color: revGrowth == null ? undefined : revGrowth >= 0 ? colors.positive : colors.negative,
    },
    {
      label: hasNextEarnings ? 'Next earnings' : 'Last earnings',
      value: fmtDate(hasNextEarnings ? quote?.next_earnings : quote?.last_earnings) ?? '—',
    },
  ];

  return (
    <Screen>
      <View style={{ flex: 1, paddingTop: spacing.sm }}>
        {/* Persistent header: ticker + live price + tabs */}
        <Stack gap={spacing.lg} style={{ paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <Stack gap={spacing.sm}>
            <Text variant="display" style={{ fontSize: 30, lineHeight: 34 }}>
              {symbol || '—'}
            </Text>
            {market.isLoading ? (
              <Skeleton height={36} width={160} />
            ) : sv ? (
              <Row gap={spacing.md} align="baseline">
                <Money value={sv.last} size="lg" />
                <Pct value={sv.changePct} signed pnl />
                {sv.badge ? (
                  <Text variant="caption" style={{ color: colors.warm, letterSpacing: 0.6 }}>
                    {sv.badge}
                  </Text>
                ) : null}
              </Row>
            ) : (
              <Text variant="body" color="textMuted">
                No quote available
              </Text>
            )}
          </Stack>
          <Segmented options={TABS} value={tab} onChange={setTab} />
        </Stack>

        <ScrollView contentContainerStyle={{ paddingTop: spacing.xl, paddingBottom: 110 }}>
          {tab === 'chart' ? (
            <Animated.View entering={FadeIn.duration(duration.fast)}>
              <Stack gap={spacing.xl}>
                <Stack gap={spacing.md}>
                  {/* Full-bleed hero chart */}
                  <SkiaChart
                    bars={chartBars}
                    mode={chartMode}
                    livePrice={chartBars.at(-1)?.close}
                    height={340}
                    loading={bars.isLoading}
                    resetKey={`${symbol}:${interval}`}
                    alerts={chartAlerts}
                    onCreateAlertAtPrice={onCreateAlertAtPrice}
                  />
                  <Stack gap={spacing.sm} style={{ paddingHorizontal: spacing.xl }}>
                    <Row gap={spacing.xs} style={{ flexWrap: 'wrap' }}>
                      {INTERVALS.map((i) => (
                        <ChartPill
                          key={i.key}
                          label={i.key}
                          active={i.key === interval}
                          onPress={() => setInterval(i.key)}
                        />
                      ))}
                    </Row>
                    <Row justify="flex-end" gap={spacing.xs}>
                      <ChartPill label="Line" active={chartMode === 'line'} onPress={() => setChartMode('line')} />
                      <ChartPill label="Candle" active={chartMode === 'candle'} onPress={() => setChartMode('candle')} />
                    </Row>
                  </Stack>
                </Stack>

                <Stack gap={spacing.xl} style={{ paddingHorizontal: spacing.xl }}>
                  <Stack gap={spacing.md}>
                    <Eyebrow>market data</Eyebrow>
                    <StatsGrid items={marketItems} />
                    {/* Top-of-book only (no depth feed) — an honest bid|spread|ask
                        strip, not a ladder. Alpaca-only symbols stream 0s → hidden. */}
                    {quote?.bid != null && quote?.ask != null && quote.bid > 0 && quote.ask > 0 ? (
                      <BidAskStrip bid={quote.bid} ask={quote.ask} />
                    ) : null}
                  </Stack>

                  <Stack gap={spacing.md}>
                    <Eyebrow>price alerts</Eyebrow>
                    {alerts.isLoading ? (
                      <Stack gap={spacing.sm}>
                        <Skeleton height={40} />
                        <Skeleton height={40} />
                      </Stack>
                    ) : alerts.isError ? (
                      <ErrorState onRetry={alerts.refetch} />
                    ) : (alerts.data?.alerts.length ?? 0) === 0 ? (
                      <Text variant="callout" color="textMuted">
                        No alerts for {symbol}. Long-press the chart, release, then tap the
                        “＋ alert” pill to set one.
                      </Text>
                    ) : (
                      <Stack gap={0}>
                        {alerts.data?.alerts.map((alert, i) => (
                          <View key={alert.id}>
                            {i > 0 ? <Divider /> : null}
                            <AlertRow
                              alert={alert}
                              onDelete={() => deleteAlert.mutate(alert.id)}
                              deleting={deleteAlert.isPending}
                            />
                          </View>
                        ))}
                      </Stack>
                    )}
                  </Stack>
                </Stack>
              </Stack>
            </Animated.View>
          ) : (
            <Animated.View
              entering={FadeIn.duration(duration.fast)}
              style={{ paddingHorizontal: spacing.xl }}
            >
              <Stack gap={spacing['2xl']}>
                <CompanyHeader
                  symbol={symbol}
                  profile={ticker.data?.profile}
                  loading={ticker.isLoading}
                />

                <Stack gap={spacing.md}>
                  <Eyebrow>valuation · quality</Eyebrow>
                  {ticker.isLoading && !ticker.data ? (
                    <Stack gap={spacing.sm}>
                      <Skeleton height={34} />
                      <Skeleton height={34} />
                      <Skeleton height={34} />
                    </Stack>
                  ) : (
                    <StatsGrid items={fundamentalItems} />
                  )}
                </Stack>

                <Stack gap={spacing.md}>
                  <Eyebrow>eps · estimate → actual</Eyebrow>
                  {earnings.isLoading ? (
                    <Skeleton height={170} />
                  ) : (earnings.data?.events.length ?? 0) === 0 ? (
                    <Text variant="body" color="textMuted">
                      No earnings history
                    </Text>
                  ) : (
                    <EpsChart events={earnings.data?.events ?? []} />
                  )}
                </Stack>

                <NewsSection news={ticker.data?.news ?? []} loading={ticker.isLoading} />
              </Stack>
            </Animated.View>
          )}
        </ScrollView>
      </View>
    </Screen>
  );
}

// ── stats grid ──

type StatItem = { label: string; value: string; color?: string };

/** Dense two-column labeled stats (iOS-Stocks style): muted label left, mono
 *  tabular value right, hairline dividers, column-major fill. */
function StatsGrid({ items }: { items: StatItem[] }) {
  const mid = Math.ceil(items.length / 2);
  const cols = [items.slice(0, mid), items.slice(mid)];
  return (
    <Row gap={spacing['2xl']} align="flex-start">
      {cols.map((col, ci) => (
        <Stack key={ci} gap={0} style={{ flex: 1 }}>
          {col.map((item, i) => (
            <View key={item.label}>
              {i > 0 ? <Divider /> : null}
              <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm + 1 }}>
                <Text variant="caption" color="textMuted">
                  {item.label}
                </Text>
                <Text variant="figureSm" style={item.color ? { color: item.color } : undefined}>
                  {item.value}
                </Text>
              </Row>
            </View>
          ))}
        </Stack>
      ))}
    </Row>
  );
}

/** Top-of-book strip — bid | spread | ask. Deliberately NOT a depth ladder:
 *  the stream only carries NBBO, so this presents exactly that. */
function BidAskStrip({ bid, ask }: { bid: number; ask: number }) {
  const colors = useColors();
  const spread = Math.max(ask - bid, 0);
  const mid = (bid + ask) / 2;
  const spreadPct = mid > 0 ? (spread / mid) * 100 : 0;
  return (
    <Row
      gap={0}
      style={{
        backgroundColor: colors.bgInset,
        borderRadius: radius.lg,
        borderWidth: hairline,
        borderColor: colors.border,
        paddingVertical: spacing.md,
      }}
    >
      <Stack gap={spacing.xs} style={{ flex: 1, alignItems: 'center' }}>
        <Eyebrow>bid</Eyebrow>
        <Text variant="figure" style={{ color: colors.positive }}>
          {bid.toFixed(2)}
        </Text>
      </Stack>
      <Stack
        gap={spacing.xs}
        style={{
          flex: 1,
          alignItems: 'center',
          borderLeftWidth: hairline,
          borderRightWidth: hairline,
          borderColor: colors.border,
        }}
      >
        <Eyebrow>spread</Eyebrow>
        <Row gap={spacing.xs} align="baseline">
          <Text variant="figure">{spread.toFixed(2)}</Text>
          <Text variant="caption" color="textFaint">
            {spreadPct.toFixed(2)}%
          </Text>
        </Row>
      </Stack>
      <Stack gap={spacing.xs} style={{ flex: 1, alignItems: 'center' }}>
        <Eyebrow>ask</Eyebrow>
        <Text variant="figure" style={{ color: colors.negative }}>
          {ask.toFixed(2)}
        </Text>
      </Stack>
    </Row>
  );
}

// ── fundamentals ──

function CompanyHeader({
  symbol,
  profile,
  loading,
}: {
  symbol: string;
  profile?: TickerProfile;
  loading: boolean;
}) {
  const colors = useColors();
  if (loading && !profile) {
    return (
      <Row gap={spacing.md} align="center">
        <Skeleton height={40} width={40} />
        <Stack gap={spacing.xs} style={{ flex: 1 }}>
          <Skeleton height={18} width={180} />
          <Skeleton height={12} width={120} />
        </Stack>
      </Row>
    );
  }
  const sub = [profile?.exchange, profile?.finnhubIndustry].filter(Boolean).join(' · ');
  return (
    <Row gap={spacing.md} align="center">
      {profile?.logo ? (
        <Image
          source={{ uri: profile.logo }}
          style={{
            width: 40,
            height: 40,
            borderRadius: radius.md,
            backgroundColor: colors.surfaceRaised,
          }}
          contentFit="contain"
        />
      ) : null}
      <Stack gap={2} style={{ flex: 1 }}>
        <Text variant="heading" numberOfLines={1}>
          {profile?.name ?? symbol}
        </Text>
        {sub ? (
          <Text variant="caption" color="textMuted" numberOfLines={1}>
            {sub}
          </Text>
        ) : null}
      </Stack>
    </Row>
  );
}

function NewsSection({ news, loading }: { news: TickerNewsItem[]; loading: boolean }) {
  const items = news.slice(0, 4);
  if (!loading && items.length === 0) return null;
  return (
    <Stack gap={spacing.md}>
      <Eyebrow>news</Eyebrow>
      {loading && items.length === 0 ? (
        <Stack gap={spacing.sm}>
          <Skeleton height={40} />
          <Skeleton height={40} />
        </Stack>
      ) : (
        <Stack gap={0}>
          {items.map((item, i) => (
            <View key={item.url}>
              {i > 0 ? <Divider /> : null}
              <NewsRow item={item} />
            </View>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

function NewsRow({ item }: { item: TickerNewsItem }) {
  return (
    <PressableScale
      onPress={() => void WebBrowser.openBrowserAsync(item.url)}
      scaleTo={0.985}
      haptic
    >
      <Stack gap={spacing.xs} style={{ paddingVertical: spacing.md }}>
        <Text variant="callout" numberOfLines={2}>
          {item.headline}
        </Text>
        <Text variant="caption" color="textFaint">
          {[item.source, relTime(item.datetime)].filter(Boolean).join(' · ')}
        </Text>
      </Stack>
    </PressableScale>
  );
}

// ── price alerts ──

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
  const dir = alert.direction;
  const dirColor =
    dir === 'above' ? colors.positive : dir === 'below' ? colors.negative : colors.textMuted;
  const arrow = dir === 'above' ? '↑' : dir === 'below' ? '↓' : '↕';
  return (
    <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm }}>
      <Row gap={spacing.md} align="center" style={{ flex: 1, marginRight: spacing.md }}>
        <Text variant="figure" style={{ color: dirColor }}>
          {arrow}
        </Text>
        <Money value={alert.price} size="sm" />
        <Text variant="caption" color="textMuted">
          {dir}
          {alert.repeat ? ' · repeats' : ''}
        </Text>
        {alert.note ? (
          <Text variant="caption" color="textFaint" numberOfLines={1} style={{ flexShrink: 1 }}>
            {alert.note}
          </Text>
        ) : null}
      </Row>
      <PressableScale onPress={onDelete} disabled={deleting} haptic>
        <View style={{ padding: spacing.sm }}>
          <Icon name="close" size={16} color={colors.textMuted} />
        </View>
      </PressableScale>
    </Row>
  );
}

// ── formatters ──

/** RVOL display, mirroring the watchlist bands: <1.5 dim · 1.5–2 yellow ·
 *  2–3 amber · >3 red; warming/null → "—". */
function rvolView(rvol: WatchlistQuote['rvol'], colors: Colors): { text: string; color?: string } {
  if (!rvol || rvol.value == null || rvol.state === 'warming') {
    return { text: '—' };
  }
  const v = rvol.value;
  const color =
    v >= 3 ? colors.danger : v >= 2 ? colors.chart[4] : v >= 1.5 ? colors.warning : colors.textMuted;
  return { text: `${v.toFixed(1)}×`, color };
}

function fmtMoney(value?: number | null): string {
  return value == null ? '—' : formatUsd(value);
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

function relTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  const mins = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function formatVolume(value: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(
    value,
  );
}
