/**
 * Watchlist — full-bleed Robinhood-style rows.
 *  1-col (user order): grip · SYM · [H/LAST/L] · [CHG%/CHG$] · [VOL/vol] · day-chart.
 *    Rows + dividers span edge-to-edge; the % FONT color scales with magnitude.
 *    Long-press / grab the grip dots to drag-reorder (user order only).
 *  1-col (column sort): same rows, no grips; tap a header to sort (desc→asc→user).
 *  2-col: dense grid — twice the tickers.
 *  "+" (left of the column headers) adds a ticker.
 *
 * Real fields from /api/watchlist/market: price, change_pct, sparkline, intraday,
 * prev_close, volume, day_high, day_low. change$ = price − prev_close.
 * rvol shows "1.8×" once serv sends it. Reorder is local (no serv persist endpoint yet).
 */
import { ActivityIndicator, Dimensions, Modal, Pressable, RefreshControl, TextInput, View } from 'react-native';
import { FlashList, type FlashListRef } from '@shopify/flash-list';
import ReorderableList, {
  type ReorderableListReorderEvent,
  reorderItems,
  useIsActive,
  useReorderableDrag,
} from 'react-native-reorderable-list';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { useAddWatchlistSymbol, useWatchlist, useWatchlistMarket } from '@/api/queries';
import type { WatchlistQuote } from '@/api/types';
import { Icon } from '@/components/ui/icon';
import {
  EmptyState,
  ErrorState,
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
const PAD_L = 28; // left gutter holds the grip / + in line with the content
const PAD_R = spacing.xl;
const SYM_W = 50;
const PRICE_W = 90;
const CHG_W = 64;
const VOL_W = 50; // fits "53.4M"
const GAP = spacing.sm;
const CHART_W = Math.max(64, SCREEN_W - PAD_L - PAD_R - SYM_W - PRICE_W - CHG_W - VOL_W - 3 * GAP);

const ROW_PV = spacing.sm; // tighter rows
const ROW_TOP = 14;
const ROW_MID = 20;
const ROW_BOT = 14;
// "Opposite" of the old black drop-shadow: a faint light edge for depth.
const ROW_SHADOW = '0px 1px 1.5px rgba(220,228,240,0.05)';

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
  if (v >= 100_000_000) return `${(v / 1_000_000).toFixed(0)}M`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
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
// Magnitude → font color: small moves muted, big moves vivid. ±5% ≈ full intensity.
function pnlColorScaled(pct: number): string {
  const t = Math.min(Math.abs(pct) / 5, 1);
  const dim = pct >= 0 ? [74, 138, 103] : [158, 90, 88];
  const vivid = pct >= 0 ? [104, 232, 154] : [255, 120, 116];
  const c = dim.map((d, i) => Math.round(d + (vivid[i] - d) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

const mono = { fontFamily: fonts.mono, fontVariant: ['tabular-nums' as const] };

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

function GripDots() {
  return (
    <View style={{ flexDirection: 'row', gap: 3 }}>
      {[0, 1].map((c) => (
        <View key={c} style={{ gap: 3 }}>
          {[0, 1, 2].map((r) => (
            <View key={r} style={{ width: 3, height: 3, borderRadius: 1.5, backgroundColor: colors.textMuted }} />
          ))}
        </View>
      ))}
    </View>
  );
}

// ─── 1-col row body (shared by reorderable + sorted views) ────────────────────
function RowBody({
  symbol,
  quote,
  loading,
  onPress,
  onDrag,
  active,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
  onPress: () => void;
  onDrag?: () => void;
  active?: boolean;
}) {
  const changeDol = quote?.prev_close != null ? quote.price - quote.prev_close : undefined;
  const rvolRaw = quote?.rvol as unknown;
  const rvolNum = typeof rvolRaw === 'number' ? rvolRaw : typeof rvolRaw === 'string' ? Number(rvolRaw) : NaN;
  const hasRvol = Number.isFinite(rvolNum);

  return (
    <View style={{ borderBottomWidth: hairline, borderBottomColor: colors.border, boxShadow: ROW_SHADOW, backgroundColor: active ? colors.surface : undefined }}>
      <PressableScale onPress={onPress}>
        <Row align="center" gap={0} style={{ paddingVertical: ROW_PV, paddingLeft: PAD_L, paddingRight: PAD_R }}>
          {onDrag ? (
            <Pressable onPressIn={onDrag} hitSlop={12} style={{ position: 'absolute', left: 2, top: 0, bottom: 0, width: PAD_L - 2, alignItems: 'center', justifyContent: 'center', zIndex: 3 }}>
              <GripDots />
            </Pressable>
          ) : null}

          <View style={{ width: SYM_W }}>
            <Text style={{ fontSize: 17, fontWeight: '700', letterSpacing: 0.2, color: colors.text }} numberOfLines={1}>{symbol}</Text>
          </View>

          {loading ? (
            <View style={{ width: PRICE_W, alignItems: 'flex-end' }}><Skeleton height={46} width={70} /></View>
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

          {loading ? (
            <View style={{ width: CHG_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={46} width={50} /></View>
          ) : quote ? (
            <TriColumn
              width={CHG_W}
              marginLeft={GAP}
              mid={pctStr(quote.change_pct)}
              midColor={pnlColorScaled(quote.change_pct)}
              bottom={changeDol != null ? dolStr(changeDol) : '—'}
              bottomColor={changeDol != null ? pnlColorScaled(quote.change_pct) : colors.textFaint}
            />
          ) : (
            <View style={{ width: CHG_W, marginLeft: GAP }} />
          )}

          {loading ? (
            <View style={{ width: VOL_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={46} width={40} /></View>
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

          <View style={{ width: CHART_W, alignItems: 'flex-end', marginLeft: GAP }}>
            {loading ? (
              <Skeleton height={40} width={CHART_W} />
            ) : quote && hasChartData(quote) ? (
              <MiniDayChart
                data={preferIntraday(quote) ? quote.intraday! : quote.sparkline}
                intraday={preferIntraday(quote)}
                change={quote.change_pct}
                width={CHART_W}
                height={40}
              />
            ) : (
              <View style={{ width: CHART_W, height: 40 }} />
            )}
          </View>
        </Row>
      </PressableScale>
    </View>
  );
}

// Reorderable wrapper — must call the drag hooks inside ReorderableList's renderItem.
function ReorderableRow({ symbol, quote, loading, onPress }: { symbol: string; quote?: WatchlistQuote; loading?: boolean; onPress: () => void }) {
  const drag = useReorderableDrag();
  const active = useIsActive();
  return <RowBody symbol={symbol} quote={quote} loading={loading} onPress={onPress} onDrag={drag} active={active} />;
}

// ─── 2-col dense cell ─────────────────────────────────────────────────────────
function WatchlistCard({ symbol, quote, loading, onPress }: { symbol: string; quote?: WatchlistQuote; loading?: boolean; onPress: () => void }) {
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
            <Text style={{ ...mono, fontSize: 13, color: pnlColorScaled(quote.change_pct) }} numberOfLines={1}>{pctStr(quote.change_pct)}</Text>
          </Row>
        ) : (
          <Text variant="figureSm" color="textFaint">—</Text>
        )}
      </Stack>
    </PressableScale>
  );
}

// ─── Sortable column header (full-bleed rules; + lives in the left gutter) ─────
function HeadCell({ label, width, marginLeft = 0, left = false, sortKey, sort, onSort }: { label: string; width: number; marginLeft?: number; left?: boolean; sortKey: SortKey; sort: SortState; onSort: (k: SortKey) => void }) {
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
function ColumnHeader({ sort, onSort, onAdd }: { sort: SortState; onSort: (k: SortKey) => void; onAdd: () => void }) {
  return (
    <View style={{ borderTopWidth: hairline, borderTopColor: colors.border, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
      <Row align="center" gap={0} style={{ paddingVertical: spacing.xs + 1, paddingLeft: PAD_L, paddingRight: PAD_R }}>
        <Pressable onPress={onAdd} hitSlop={12} style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: PAD_L, alignItems: 'center', justifyContent: 'center', zIndex: 3 }}>
          <Icon name="plus" size={16} color={colors.accent} />
        </Pressable>
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

// ─── Page header ──────────────────────────────────────────────────────────────
function WatchlistHeader({ columns, onToggleColumns, onOpenAlerts }: { columns: 1 | 2; onToggleColumns: () => void; onOpenAlerts: () => void }) {
  return (
    <Row justify="space-between" align="center" style={{ paddingLeft: PAD_L, paddingRight: PAD_R, paddingBottom: spacing.sm }}>
      <Text style={{ fontFamily: fonts.mono, fontSize: 21, letterSpacing: 0.4, color: colors.accent }}>watchlist</Text>
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

// ─── Add-ticker modal ─────────────────────────────────────────────────────────
function AddTickerModal({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const [text, setText] = useState('');
  const add = useAddWatchlistSymbol();

  const submit = () => {
    const sym = text.trim().toUpperCase();
    if (!sym) return;
    add.mutate(sym, {
      onSuccess: () => {
        setText('');
        onClose();
      },
    });
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', paddingHorizontal: spacing.xl }}>
        <Pressable onPress={(e) => e.stopPropagation()} style={{ backgroundColor: colors.surface, borderRadius: 16, padding: spacing.xl, borderWidth: hairline, borderColor: colors.border }}>
          <Text variant="bodyStrong" style={{ marginBottom: spacing.md }}>Add ticker</Text>
          <TextInput
            value={text}
            onChangeText={(t) => setText(t.toUpperCase())}
            placeholder="e.g. NVDA"
            placeholderTextColor={colors.textFaint}
            autoCapitalize="characters"
            autoCorrect={false}
            autoFocus
            returnKeyType="done"
            onSubmitEditing={submit}
            style={{ ...mono, fontSize: 18, color: colors.text, borderWidth: hairline, borderColor: colors.border, borderRadius: 10, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: colors.bg }}
          />
          {add.isError ? <Text variant="caption" style={{ color: colors.danger, marginTop: spacing.sm }}>Couldn't add — check the symbol.</Text> : null}
          <Row gap={spacing.sm} justify="flex-end" style={{ marginTop: spacing.lg }}>
            <PressableScale onPress={onClose} haptic style={{ paddingVertical: spacing.sm, paddingHorizontal: spacing.lg }}>
              <Text style={{ color: colors.textMuted }}>Cancel</Text>
            </PressableScale>
            <PressableScale onPress={submit} haptic style={{ paddingVertical: spacing.sm, paddingHorizontal: spacing.lg, borderRadius: 10, backgroundColor: colors.accent }}>
              {add.isPending ? <ActivityIndicator size="small" color={colors.bg} /> : <Text style={{ color: colors.bg, fontWeight: '700' }}>Add</Text>}
            </PressableScale>
          </Row>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function SkeletonRows() {
  return (
    <Stack gap={0}>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <View key={i} style={{ borderBottomWidth: hairline, borderBottomColor: colors.border }}>
          <Row align="center" gap={0} style={{ paddingVertical: ROW_PV, paddingLeft: PAD_L, paddingRight: PAD_R }}>
            <View style={{ width: SYM_W }}><Skeleton height={16} width={36} /></View>
            <View style={{ width: PRICE_W, alignItems: 'flex-end' }}><Skeleton height={46} width={70} /></View>
            <View style={{ width: CHG_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={46} width={50} /></View>
            <View style={{ width: VOL_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={46} width={40} /></View>
            <View style={{ width: CHART_W, alignItems: 'flex-end', marginLeft: GAP }}><Skeleton height={40} width={CHART_W} /></View>
          </Row>
        </View>
      ))}
    </Stack>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export function WatchlistScreen() {
  const router = useRouter();
  const listRef = useRef<FlashListRef<string>>(null);
  const [columns, setColumns] = useState<1 | 2>(1);
  const [sort, setSort] = useState<SortState>({ key: null, dir: 'desc' });
  const [order, setOrder] = useState<string[] | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const watchlist = useWatchlist();
  const symbols: string[] = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};

  const { refreshing, onRefresh } = usePullRefresh(() => Promise.all([watchlist.refetch(), market.refetch()]));

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  useEffect(() => {
    listRef.current?.scrollToOffset({ offset: 0, animated: false });
  }, [sort]);

  const onSort = (key: SortKey) =>
    setSort((s) => (s.key !== key ? { key, dir: 'desc' } : s.dir === 'desc' ? { key, dir: 'asc' } : { key: null, dir: 'desc' }));

  // User order: keep the user's local order, reconciled with the server's set.
  const userOrder = useMemo(() => {
    if (!order) return symbols;
    const known = order.filter((s) => symbols.includes(s));
    const added = symbols.filter((s) => !order.includes(s));
    return [...known, ...added];
  }, [order, symbols]);

  const data = useMemo(() => {
    if (!sort.key) return userOrder;
    const k = sort.key;
    const num = (sym: string) => {
      const q = quotes[sym];
      if (!q) return Number.NEGATIVE_INFINITY;
      if (k === 'last') return q.price;
      if (k === 'chg') return q.change_pct;
      const r = Number(q.rvol);
      return Number.isFinite(r) ? r : (q.volume ?? 0);
    };
    const arr = [...userOrder].sort((a, b) => (k === 'sym' ? a.localeCompare(b) : num(a) - num(b)));
    return sort.dir === 'desc' ? arr.reverse() : arr;
  }, [userOrder, quotes, sort]);

  const onReorder = ({ from, to }: ReorderableListReorderEvent) => {
    setOrder(reorderItems(data, from, to));
  };

  const reorderable = columns === 1 && sort.key === null;

  const header = (
    <View>
      <WatchlistHeader
        columns={columns}
        onToggleColumns={() => setColumns((c) => (c === 1 ? 2 : 1))}
        onOpenAlerts={() => router.push('/alerts')}
      />
      {columns === 1 ? <ColumnHeader sort={sort} onSort={onSort} onAdd={() => setAddOpen(true)} /> : null}
    </View>
  );

  const empty = loading ? <SkeletonRows /> : <EmptyState title="Watchlist empty" subtitle="Tap + to add a symbol." />;
  const refreshControl = <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />;
  const openSymbol = (symbol: string) => router.push(`/symbol/${symbol}`);

  if (watchlist.isError || market.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.xs }}>
          {header}
          <View style={{ paddingHorizontal: spacing.xl }}>
            <ErrorState onRetry={() => { void watchlist.refetch(); void market.refetch(); }} />
          </View>
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      {reorderable ? (
        <ReorderableList
          data={data}
          onReorder={onReorder}
          keyExtractor={(symbol) => symbol}
          refreshControl={refreshControl}
          contentContainerStyle={{ paddingBottom: 110, paddingTop: spacing.xs }}
          ListHeaderComponent={header}
          ListEmptyComponent={empty}
          renderItem={({ item }) => (
            <ReorderableRow symbol={item} quote={quotes[item]} loading={marketLoading && !quotes[item]} onPress={() => openSymbol(item)} />
          )}
        />
      ) : (
        <FlashList
          ref={listRef}
          key={`watchlist-${columns}`}
          data={data}
          numColumns={columns}
          keyExtractor={(symbol) => symbol}
          refreshControl={refreshControl}
          contentContainerStyle={{ paddingBottom: 110, paddingTop: spacing.xs, paddingHorizontal: columns === 2 ? spacing.lg : 0 }}
          ListHeaderComponent={header}
          renderItem={({ item: symbol }) =>
            columns === 1 ? (
              <RowBody symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => openSymbol(symbol)} />
            ) : (
              <WatchlistCard symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => openSymbol(symbol)} />
            )
          }
          ListEmptyComponent={empty}
        />
      )}

      <AddTickerModal visible={addOpen} onClose={() => setAddOpen(false)} />
    </Screen>
  );
}
