/**
 * Watchlist — Robinhood-style rows.
 *  1-col: SYM · [H / LAST / L] · [CHG% / CHG$] · [VOL / vol] · short day-chart.
 *         Prominent values (LAST · CHG% · VOL) align on the middle line; the small
 *         values (low · CHG$ · vol) on the line below. Each row carries a subtle
 *         magnitude tint (deeper red/green for bigger moves) + a depth shadow.
 *  2-col: dense grid — twice the tickers.
 *
 * Sortable by tapping a column header: tap → desc, tap again → asc, third → user order.
 *
 * Real fields from /api/watchlist/market: price, change_pct, sparkline, intraday,
 * prev_close, volume, day_high, day_low. change$ = price − prev_close.
 * rvol (relative volume) is wired forward-compatibly — shows as "1.8×" once serv sends it.
 */
import { Dimensions, RefreshControl, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useRouter } from 'expo-router';
import { useMemo, useState } from 'react';

import { useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  EmptyState,
  ErrorState,
  Eyebrow,
  Money,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Sparkline,
  Stack,
  Text,
} from '@/components/ui';
import { usePullRefresh } from '@/hooks/use-pull-refresh';
import { colors, fonts, hairline, pnlColor, spacing } from '@/theme';

import { MiniDayChart } from './mini-day-chart';

const SCREEN_W = Dimensions.get('window').width;
const SYM_W = 50; // fits 4–5 char tickers (NVDA, GOOGL)
const PRICE_W = 92; // fits a 4-digit LAST at the prominent "md" size ($1,072.45)
const CHG_W = 66; // fits a double-digit signed percent (−12.55%)
const VOL_W = 46;
const GAP = spacing.sm;
const CHART_W = Math.max(80, SCREEN_W - 2 * spacing.xl - SYM_W - PRICE_W - CHG_W - VOL_W - 3 * GAP);

type SortKey = 'sym' | 'last' | 'chg' | 'vol';
type SortState = { key: SortKey | null; dir: 'asc' | 'desc' };

function preferIntraday(q: WatchlistQuote): boolean {
  return !!q.intraday && q.intraday.length >= 2;
}
function hasChartData(q: WatchlistQuote): boolean {
  return preferIntraday(q) || q.sparkline.length > 1;
}
function fmtVol(v: number | undefined): string {
  if (v == null) return '—';
  if (v >= 100_000_000) return `${(v / 1_000_000).toFixed(0)}M`; // 153M — keep it narrow
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`; // 53.4M
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return `${v.toFixed(0)}`;
}
function pctStr(v: number): string {
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
}
function dolStr(v: number): string {
  const s = v > 0 ? '+' : v < 0 ? '−' : '';
  return `${s}$${Math.abs(v).toFixed(2)}`;
}
function usd(v: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
}
// Subtle heatmap: the bigger the move, the more saturated the row tint. ±4% ≈ full.
function rowTint(pct: number | undefined): string {
  if (pct == null) return 'transparent';
  const a = (Math.min(Math.abs(pct) / 4, 1) * 0.1).toFixed(3);
  return pct >= 0 ? `rgba(91,201,138,${a})` : `rgba(239,111,108,${a})`;
}

const mono = { fontFamily: fonts.mono, fontVariant: ['tabular-nums' as const] };

// Shared 3-row rhythm so every column lines up:
//   top = high (price only) · mid = the PROMINENT value · bottom = the small value
const ROW_TOP = 15;
const ROW_MID = 22;
const ROW_BOT = 15;

function TriColumn({
  width,
  marginLeft = 0,
  top,
  topColor = colors.textFaint,
  mid,
  midColor,
  midSize = 15,
  bottom,
  bottomColor,
}: {
  width: number;
  marginLeft?: number;
  top?: string;
  topColor?: string;
  mid: string;
  midColor: string;
  midSize?: number;
  bottom: string;
  bottomColor: string;
}) {
  return (
    <View style={{ width, alignItems: 'flex-end', marginLeft }}>
      <Text style={{ ...mono, fontSize: 11, lineHeight: ROW_TOP, color: top ? topColor : 'transparent' }} numberOfLines={1}>
        {top ?? ' '}
      </Text>
      <Text style={{ ...mono, fontSize: midSize, lineHeight: ROW_MID, color: midColor }} numberOfLines={1}>{mid}</Text>
      <Text style={{ ...mono, fontSize: 11, lineHeight: ROW_BOT, color: bottomColor }} numberOfLines={1}>{bottom}</Text>
    </View>
  );
}

// ─── Sortable column header — mirrors the row's flex geometry exactly ──────────
function HeadCell({
  label,
  width,
  marginLeft = 0,
  left = false,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  width: number;
  marginLeft?: number;
  left?: boolean;
  sortKey: SortKey;
  sort: SortState;
  onSort: (k: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <PressableScale onPress={() => onSort(sortKey)} haptic style={{ width, marginLeft }}>
      <Row gap={2} align="center" justify={left ? 'flex-start' : 'flex-end'}>
        <Text style={{ ...mono, fontSize: 12, letterSpacing: 0.4, color: active ? colors.accent : colors.textMuted }}>{label}</Text>
        <Text style={{ ...mono, fontSize: 9, color: active ? colors.accent : 'transparent' }}>{sort.dir === 'desc' ? '▼' : '▲'}</Text>
      </Row>
    </PressableScale>
  );
}
function ColumnHeader({ sort, onSort }: { sort: SortState; onSort: (k: SortKey) => void }) {
  return (
    <View
      style={{
        borderTopWidth: hairline,
        borderTopColor: colors.border,
        borderBottomWidth: hairline,
        borderBottomColor: colors.border,
        paddingVertical: spacing.xs + 1,
        marginBottom: spacing.xs,
      }}
    >
      <Row align="center" gap={0}>
        <HeadCell label="sym" width={SYM_W} left sortKey="sym" sort={sort} onSort={onSort} />
        <HeadCell label="last" width={PRICE_W} sortKey="last" sort={sort} onSort={onSort} />
        <HeadCell label="chg" width={CHG_W} marginLeft={GAP} sortKey="chg" sort={sort} onSort={onSort} />
        <HeadCell label="vol" width={VOL_W} marginLeft={GAP} sortKey="vol" sort={sort} onSort={onSort} />
        <View style={{ width: CHART_W, marginLeft: GAP, alignItems: 'flex-end' }}>
          <Text style={{ ...mono, fontSize: 12, letterSpacing: 0.4, color: colors.textMuted }}>1d</Text>
        </View>
      </Row>
    </View>
  );
}

// ─── Page header: eyebrow + title + icons ─────────────────────────────────────
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
    <Row justify="space-between" align="flex-end" style={{ paddingBottom: spacing.sm }}>
      <Stack gap={1}>
        <Eyebrow color="accent">watchlist</Eyebrow>
        <Text variant="title">Symbols</Text>
      </Stack>
      <Row gap={spacing.sm}>
        <PressableScale onPress={onToggleColumns} haptic>
          <View style={{ padding: spacing.xs }}>
            <Icon name={columns === 1 ? 'columns' : 'list'} size={22} color={colors.textMuted} />
          </View>
        </PressableScale>
        <PressableScale onPress={onOpenAlerts} haptic>
          <View style={{ padding: spacing.xs }}>
            <Icon name="bell" size={22} color={colors.textMuted} />
          </View>
        </PressableScale>
      </Row>
    </Row>
  );
}

// ─── 1-col row ────────────────────────────────────────────────────────────────
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
  // Forward-compatible: accept rvol as number OR numeric string; fall back to volume.
  const rvolRaw = quote?.rvol as unknown;
  const rvolNum = typeof rvolRaw === 'number' ? rvolRaw : typeof rvolRaw === 'string' ? Number(rvolRaw) : NaN;
  const hasRvol = Number.isFinite(rvolNum);

  return (
    <PressableScale onPress={onPress}>
      <View style={{ backgroundColor: rowTint(quote?.change_pct), boxShadow: '0px 2px 5px rgba(0,0,0,0.22)' }}>
        <Row align="center" gap={0} style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
          {/* SYM */}
          <View style={{ width: SYM_W }}>
            <Text style={{ fontSize: 17, fontWeight: '700', letterSpacing: 0.2, color: colors.text }} numberOfLines={1}>{symbol}</Text>
          </View>

          {/* H / LAST / L */}
          {loading ? (
            <View style={{ width: PRICE_W, alignItems: 'flex-end' }}><Skeleton height={50} width={72} /></View>
          ) : quote ? (
            <TriColumn
              width={PRICE_W}
              top={quote.day_high != null ? quote.day_high.toFixed(2) : undefined}
              mid={usd(quote.price)}
              midColor={colors.text}
              midSize={16}
              bottom={quote.day_low != null ? quote.day_low.toFixed(2) : '—'}
              bottomColor={colors.textFaint}
            />
          ) : (
            <View style={{ width: PRICE_W }} />
          )}

          {/* CHG% (mid) / CHG$ (bottom) */}
          {loading ? (
            <View style={{ width: CHG_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={50} width={50} /></View>
          ) : quote ? (
            <TriColumn
              width={CHG_W}
              marginLeft={GAP}
              mid={pctStr(quote.change_pct)}
              midColor={pnlColor(quote.change_pct)}
              bottom={changeDol != null ? dolStr(changeDol) : '—'}
              bottomColor={changeDol != null ? pnlColor(changeDol) : colors.textFaint}
            />
          ) : (
            <View style={{ width: CHG_W, marginLeft: GAP }} />
          )}

          {/* VOL (mid) / vol (bottom) — shows rvol "1.8×" once serv provides it */}
          {loading ? (
            <View style={{ width: VOL_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={50} width={44} /></View>
          ) : quote ? (
            <TriColumn
              width={VOL_W}
              marginLeft={GAP}
              mid={hasRvol ? `${rvolNum.toFixed(1)}×` : fmtVol(quote.volume)}
              midColor={hasRvol && rvolNum >= 1.5 ? colors.accent : colors.text}
              bottom={hasRvol ? fmtVol(quote.volume) : 'vol'}
              bottomColor={colors.textFaint}
            />
          ) : (
            <View style={{ width: VOL_W, marginLeft: GAP }} />
          )}

          {/* Short day chart */}
          <View style={{ width: CHART_W, alignItems: 'flex-end', marginLeft: GAP }}>
            {loading ? (
              <Skeleton height={42} width={CHART_W} />
            ) : quote && hasChartData(quote) ? (
              <MiniDayChart
                data={preferIntraday(quote) ? quote.intraday! : quote.sparkline}
                intraday={preferIntraday(quote)}
                change={quote.change_pct}
                width={CHART_W}
                height={42}
              />
            ) : (
              <View style={{ width: CHART_W, height: 42 }} />
            )}
          </View>
        </Row>
      </View>
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
      <View style={{ flex: 1, backgroundColor: rowTint(quote?.change_pct) }}>
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
              <Text style={{ ...mono, fontSize: 13, color: pnlColor(quote.change_pct) }} numberOfLines={1}>{pctStr(quote.change_pct)}</Text>
            </Row>
          ) : (
            <Text variant="figureSm" color="textFaint">—</Text>
          )}
        </Stack>
      </View>
    </PressableScale>
  );
}

function SkeletonRows() {
  return (
    <Stack gap={0}>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <View key={i} style={{ paddingVertical: spacing.md, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
          <Row align="center" gap={0}>
            <View style={{ width: SYM_W }}><Skeleton height={16} width={36} /></View>
            <View style={{ width: PRICE_W, alignItems: 'flex-end' }}><Skeleton height={50} width={72} /></View>
            <View style={{ width: CHG_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={50} width={50} /></View>
            <View style={{ width: VOL_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={50} width={44} /></View>
            <View style={{ width: CHART_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={42} width={CHART_W} /></View>
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
  const [sort, setSort] = useState<SortState>({ key: null, dir: 'desc' });

  const watchlist = useWatchlist();
  const symbols: string[] = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};

  const { refreshing, onRefresh } = usePullRefresh(() => Promise.all([watchlist.refetch(), market.refetch()]));

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  // tap a header: desc → asc → off (back to user order)
  const onSort = (key: SortKey) =>
    setSort((s) => (s.key !== key ? { key, dir: 'desc' } : s.dir === 'desc' ? { key, dir: 'asc' } : { key: null, dir: 'desc' }));

  const data = useMemo(() => {
    if (!sort.key) return symbols;
    const k = sort.key;
    const num = (sym: string) => {
      const q = quotes[sym];
      if (!q) return Number.NEGATIVE_INFINITY;
      if (k === 'last') return q.price;
      if (k === 'chg') return q.change_pct;
      const r = Number(q.rvol);
      return Number.isFinite(r) ? r : (q.volume ?? 0);
    };
    const arr = [...symbols].sort((a, b) => (k === 'sym' ? a.localeCompare(b) : num(a) - num(b)));
    return sort.dir === 'desc' ? arr.reverse() : arr;
  }, [symbols, quotes, sort]);

  const header = (
    <View>
      <WatchlistHeader
        columns={columns}
        onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
        onOpenAlerts={() => router.push('/alerts')}
      />
      {columns === 1 ? <ColumnHeader sort={sort} onSort={onSort} /> : null}
    </View>
  );

  if (watchlist.isError || market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.xs, paddingHorizontal: spacing.xl }}>
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
        data={data}
        numColumns={columns}
        keyExtractor={(symbol) => symbol}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />}
        contentContainerStyle={{ paddingHorizontal: spacing.xl, paddingBottom: 110, paddingTop: spacing.xs }}
        ListHeaderComponent={header}
        renderItem={({ item: symbol }) =>
          columns === 1 ? (
            <WatchlistRow symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => router.push(`/symbol/${symbol}`)} />
          ) : (
            <WatchlistCard symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => router.push(`/symbol/${symbol}`)} />
          )
        }
        ListEmptyComponent={
          loading ? <SkeletonRows /> : <EmptyState title="Watchlist empty" subtitle="Add symbols from the web app." />
        }
      />
    </Screen>
  );
}
