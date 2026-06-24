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
import { ActivityIndicator, Dimensions, Pressable, RefreshControl, TextInput, View } from 'react-native';
import { FlashList, type FlashListRef } from '@shopify/flash-list';
import Animated, {
  FadeInDown,
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import ReorderableList, {
  type ReorderableListReorderEvent,
  reorderItems,
  useIsActive,
  useReorderableDrag,
} from 'react-native-reorderable-list';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  useAddWatchlistSymbol,
  useRemoveWatchlistSymbol,
  useReorderWatchlist,
  useSymbolSearch,
  useWatchlist,
  useWatchlists,
  useWatchlistMarket,
} from '@/api/queries';
import { useNav } from '@/state/nav';
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
import { colors, fonts, haptics, hairline, pnlColor, spacing } from '@/theme';

import { MiniDayChart } from './mini-day-chart';
import { sessionBadge, sessionView } from './session';

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

// RVOL → display per serv's contract: "2.3×" with color bands; warming/null → "—";
// overnight (yesterday's final) dimmed. Bands: <1.5 dim · 1.5–2 yellow · 2–3 amber · >3 red.
function rvolDisplay(rvol: WatchlistQuote['rvol']): { text: string; color: string; opacity: number } {
  if (!rvol || rvol.value == null || rvol.state === 'warming') {
    return { text: '—', color: colors.textFaint, opacity: 1 };
  }
  const v = rvol.value;
  const color = v >= 3 ? colors.danger : v >= 2 ? colors.chart[4] : v >= 1.5 ? colors.warning : colors.textMuted;
  return { text: `${v.toFixed(1)}×`, color, opacity: rvol.state === 'overnight' ? 0.5 : 1 };
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
  midOpacity = 1,
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
  midOpacity?: number;
  bottom: string;
  bottomColor: string;
}) {
  return (
    <View style={{ width, alignItems: 'flex-end', marginLeft }}>
      <Text style={{ ...mono, fontSize: 11, lineHeight: ROW_TOP, color: top ? topColor : 'transparent' }} numberOfLines={1}>
        {top ?? ' '}
      </Text>
      <Text style={{ ...mono, fontSize: midSize, lineHeight: ROW_MID, color: midColor, opacity: midOpacity }} numberOfLines={1}>{mid}</Text>
      <Text style={{ ...mono, fontSize: 11, lineHeight: ROW_BOT, color: bottomColor }} numberOfLines={1}>{bottom}</Text>
    </View>
  );
}

// The lib fires onDragStart/onIndexChange INSIDE worklets (UI thread), so haptics
// (plain JS) must be hopped back to the JS thread via runOnJS.
const onDragStartHaptic = () => {
  'worklet';
  runOnJS(haptics.impact)();
};
const onIndexChangeHaptic = () => {
  'worklet';
  runOnJS(haptics.selection)();
};

function GripDots() {
  return (
    <View style={{ flexDirection: 'row', gap: 2.5 }}>
      {[0, 1].map((c) => (
        <View key={c} style={{ gap: 2.5 }}>
          {[0, 1, 2].map((r) => (
            <View key={r} style={{ width: 2, height: 2, borderRadius: 1, backgroundColor: colors.textFaint }} />
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
  onLongPress,
  grip,
  active,
  editing,
  index,
  onDelete,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
  onPress?: () => void;
  onLongPress?: () => void;
  grip?: boolean;
  active?: boolean;
  editing?: boolean;
  index?: number;
  onDelete?: () => void;
}) {
  const sv = quote ? sessionView(quote) : null;
  const rv = rvolDisplay(quote?.rvol);

  // iOS-home-screen jiggle while editing (paused on the row being dragged).
  const tilt = useSharedValue(0);
  useEffect(() => {
    if (editing && !active) {
      const dir = (index ?? 0) % 2 === 0 ? 1 : -1;
      tilt.value = withRepeat(
        withSequence(withTiming(dir * 0.3, { duration: 190 }), withTiming(-dir * 0.3, { duration: 190 })),
        -1,
        true,
      );
    } else {
      tilt.value = withTiming(0, { duration: 120 });
    }
  }, [editing, active, index, tilt]);
  const jiggle = useAnimatedStyle(() => ({ transform: [{ rotateZ: `${tilt.value}deg` }] }));

  return (
    <Animated.View style={[{ borderBottomWidth: hairline, borderBottomColor: colors.border, boxShadow: ROW_SHADOW, backgroundColor: active ? colors.surface : undefined }, jiggle]}>
      <PressableScale onPress={onPress} onLongPress={onLongPress} delayLongPress={250}>
        <Row align="center" gap={0} style={{ paddingVertical: ROW_PV, paddingLeft: PAD_L, paddingRight: PAD_R }}>
          {editing ? (
            <Pressable onPress={onDelete} hitSlop={12} style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: PAD_L, alignItems: 'center', justifyContent: 'center', zIndex: 4 }}>
              <View style={{ width: 18, height: 18, borderRadius: 9, backgroundColor: colors.danger, alignItems: 'center', justifyContent: 'center' }}>
                <View style={{ width: 8, height: 2, borderRadius: 1, backgroundColor: '#fff' }} />
              </View>
            </Pressable>
          ) : grip ? (
            <View pointerEvents="none" style={{ position: 'absolute', left: 2, top: 0, bottom: 0, width: PAD_L - 2, alignItems: 'center', justifyContent: 'center', zIndex: 1 }}>
              <GripDots />
            </View>
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
              topColor={colors.textFaint}
              mid={usd(sv!.last)}
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
              mid={pctStr(sv!.changePct)}
              midColor={pnlColorScaled(sv!.changePct)}
              bottom={sv!.changeDol != null ? dolStr(sv!.changeDol) : '—'}
              bottomColor={sv!.changeDol != null ? pnlColorScaled(sv!.changePct) : colors.textFaint}
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
              mid={rv.text}
              midColor={rv.color}
              midOpacity={rv.opacity}
              bottom={fmtVol(quote.volume)}
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
    </Animated.View>
  );
}

function ReorderableRow({
  symbol,
  quote,
  loading,
  onPress,
  index,
  editing,
  onEnterEdit,
  onDelete,
}: {
  symbol: string;
  quote?: WatchlistQuote;
  loading?: boolean;
  onPress: () => void;
  index: number;
  editing: boolean;
  onEnterEdit: () => void;
  onDelete: (sym: string) => void;
}) {
  const drag = useReorderableDrag(); // call on long-press to INITIATE the drag
  const active = useIsActive();
  return (
    <RowBody
      symbol={symbol}
      quote={quote}
      loading={loading}
      onPress={editing ? undefined : onPress}
      onLongPress={() => {
        onEnterEdit();
        drag();
      }}
      grip
      active={active}
      editing={editing}
      index={index}
      onDelete={() => onDelete(symbol)}
    />
  );
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
function ColumnHeader({ sort, onSort }: { sort: SortState; onSort: (k: SortKey) => void }) {
  return (
    <View style={{ borderTopWidth: hairline, borderTopColor: colors.border, borderBottomWidth: hairline, borderBottomColor: colors.border }}>
      <Row align="center" gap={0} style={{ paddingVertical: spacing.xs + 1, paddingLeft: PAD_L, paddingRight: PAD_R }}>
        <HeadCell label="sym" width={SYM_W} left sortKey="sym" sort={sort} onSort={onSort} />
        <HeadCell label="last" width={PRICE_W} sortKey="last" sort={sort} onSort={onSort} />
        <HeadCell label="chg" width={CHG_W} marginLeft={GAP} sortKey="chg" sort={sort} onSort={onSort} />
        <HeadCell label="rvol" width={VOL_W} marginLeft={GAP} sortKey="vol" sort={sort} onSort={onSort} />
        <View style={{ width: CHART_W, marginLeft: GAP, alignItems: 'flex-end' }}>
          <Text style={{ ...mono, fontSize: 12, letterSpacing: 0.4, color: colors.textMuted }}>1d</Text>
        </View>
      </Row>
    </View>
  );
}

// ─── Page header ──────────────────────────────────────────────────────────────
function WatchlistHeader({ onAdd, onOpenAlerts, editing, onDone, session }: { onAdd: () => void; onOpenAlerts: () => void; editing: boolean; onDone: () => void; session?: string | null }) {
  return (
    <Row justify="space-between" align="center" style={{ paddingLeft: PAD_L, paddingRight: PAD_R, paddingBottom: spacing.sm }}>
      <Row gap={spacing.sm} align="center">
        <Text style={{ fontFamily: fonts.mono, fontSize: 21, letterSpacing: 0.4, color: colors.accent }}>watchlist</Text>
        {session ? (
          <View style={{ paddingHorizontal: 6, paddingVertical: 2, borderRadius: 5, backgroundColor: colors.warmWash }}>
            <Text style={{ ...mono, fontSize: 10, letterSpacing: 0.6, color: colors.warm }}>{session}</Text>
          </View>
        ) : null}
      </Row>
      {editing ? (
        <PressableScale onPress={onDone} haptic>
          <View style={{ paddingVertical: spacing.xs, paddingHorizontal: spacing.sm }}>
            <Text style={{ color: colors.accent, fontWeight: '700', fontSize: 16 }}>Done</Text>
          </View>
        </PressableScale>
      ) : (
        <Row gap={spacing.xs}>
          <PressableScale onPress={onAdd} haptic>
            <View style={{ padding: spacing.xs }}>
              <Icon name="plus" size={24} color={colors.accent} />
            </View>
          </PressableScale>
          <PressableScale onPress={onOpenAlerts} haptic>
            <View style={{ padding: spacing.xs }}>
              <Icon name="bell" size={22} color={colors.textMuted} />
            </View>
          </PressableScale>
        </Row>
      )}
    </Row>
  );
}

// ─── Inline add-ticker row — slides in at the top; type → autocomplete ─────────
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function AddRow({ onAdded, onClose }: { onAdded: (sym: string) => void; onClose: () => void }) {
  const [text, setText] = useState('');
  const debounced = useDebounced(text, 220);
  const search = useSymbolSearch(debounced);
  const add = useAddWatchlistSymbol();
  const suggestions = (search.data ?? []).slice(0, 6);

  // Tapping a suggestion adds it directly (always a real symbol).
  const doAdd = (sym: string) => {
    const s = sym.trim().toUpperCase();
    if (!s || add.isPending) return;
    add.mutate(s, {
      onSuccess: () => {
        setText('');
        onAdded(s);
        onClose();
      },
    });
  };
  // Enter key: only add a REAL symbol — exact search match, else the top suggestion.
  // Prevents adding a typed string that isn't a tradable ticker.
  const submitTyped = () => {
    const s = text.trim().toUpperCase();
    const list = search.data ?? [];
    const pick = list.find((x) => x.toUpperCase() === s) ?? list[0];
    if (pick) doAdd(pick);
  };
  const noMatch = text.trim().length > 0 && !search.isFetching && (search.data?.length ?? 0) === 0;

  return (
    <Animated.View entering={FadeInDown.duration(160)} style={{ borderBottomWidth: hairline, borderBottomColor: colors.border, backgroundColor: colors.surface }}>
      <Row align="center" gap={spacing.sm} style={{ paddingVertical: spacing.sm, paddingLeft: PAD_L, paddingRight: PAD_R }}>
        <Icon name="plus" size={18} color={colors.accent} />
        <TextInput
          value={text}
          onChangeText={(t) => setText(t.toUpperCase())}
          placeholder="add ticker…"
          placeholderTextColor={colors.textFaint}
          autoCapitalize="characters"
          autoCorrect={false}
          autoFocus
          returnKeyType="done"
          onSubmitEditing={submitTyped}
          style={{ ...mono, flex: 1, fontSize: 17, color: colors.text, paddingVertical: 2 }}
        />
        {add.isPending ? <ActivityIndicator size="small" color={colors.accent} /> : null}
        <PressableScale onPress={onClose} haptic>
          <Icon name="close" size={20} color={colors.textMuted} />
        </PressableScale>
      </Row>
      {suggestions.length > 0 ? (
        <View style={{ paddingBottom: spacing.xs }}>
          {suggestions.map((s) => (
            <PressableScale key={s} onPress={() => doAdd(s)} haptic>
              <Row justify="space-between" align="center" style={{ paddingVertical: spacing.sm, paddingLeft: PAD_L + 26, paddingRight: PAD_R }}>
                <Text variant="bodyStrong">{s}</Text>
                <Icon name="plus" size={15} color={colors.textMuted} />
              </Row>
            </PressableScale>
          ))}
        </View>
      ) : null}
      {add.isError ? (
        <Text variant="caption" style={{ color: colors.danger, paddingLeft: PAD_L, paddingBottom: spacing.sm }}>Couldn't add — check the symbol.</Text>
      ) : noMatch ? (
        <Text variant="caption" style={{ color: colors.textFaint, paddingLeft: PAD_L, paddingBottom: spacing.sm }}>No matching symbol.</Text>
      ) : null}
    </Animated.View>
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
  const [sort, setSort] = useState<SortState>({ key: null, dir: 'desc' });
  const [order, setOrder] = useState<string[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState(false);
  const { setSwipeEnabled } = useNav();

  const watchlist = useWatchlist();
  const symbols: string[] = watchlist.data?.symbols ?? [];
  const market = useWatchlistMarket(symbols);
  const quotes = market.data?.quotes ?? {};
  // One session indicator for the whole list (all tickers share the session) —
  // shown in the header during extended hours instead of a badge on every row.
  const sessionTag = sessionBadge(Object.values(quotes)[0]?.market_session);

  const watchlists = useWatchlists();
  const activeId = watchlists.data?.active_id;
  const reorder = useReorderWatchlist();
  const remove = useRemoveWatchlistSymbol();

  const { refreshing, onRefresh } = usePullRefresh(() => Promise.all([watchlist.refetch(), market.refetch()]));

  const loading = watchlist.isLoading;
  const marketLoading = market.isLoading && symbols.length > 0;

  useEffect(() => {
    listRef.current?.scrollToOffset({ offset: 0, animated: false });
  }, [sort]);

  // Edit mode disables page-swipe so a left-swipe deletes a row instead of paging.
  useEffect(() => {
    setSwipeEnabled(!editing);
    return () => setSwipeEnabled(true);
  }, [editing, setSwipeEnabled]);

  // Sorting isn't reorderable, so leave edit mode if a column sort turns on.
  useEffect(() => {
    if (sort.key) setEditing(false);
  }, [sort.key]);

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
      // Sort by the same session-aware values the rows display, so during
      // extended hours a big aftermarket mover (e.g. an earnings beat) ranks by
      // its shown price/change, not its frozen regular-session figure.
      if (k === 'last') return sessionView(q).last;
      if (k === 'chg') return sessionView(q).changePct;
      const r = q.rvol?.value;
      return r != null ? r : (q.volume ?? 0);
    };
    const arr = [...userOrder].sort((a, b) => (k === 'sym' ? a.localeCompare(b) : num(a) - num(b)));
    return sort.dir === 'desc' ? arr.reverse() : arr;
  }, [userOrder, quotes, sort]);

  // Persist the order to the active list (PUT /api/watchlists/{id}) so it survives
  // restarts; setOrder gives instant local feedback.
  const persistOrder = (next: string[]) => {
    setOrder(next);
    if (activeId) reorder.mutate({ id: activeId, symbols: next });
  };

  const onReorder = ({ from, to }: ReorderableListReorderEvent) => {
    persistOrder(reorderItems(data, from, to));
    haptics.tap();
  };

  // Newly-added ticker jumps to the top of the user's order.
  const onAdded = (sym: string) => {
    const base = order ?? symbols;
    persistOrder([sym, ...base.filter((s) => s !== sym)]);
    haptics.success();
  };

  const onDelete = (sym: string) => {
    setOrder((prev) => (prev ?? symbols).filter((s) => s !== sym));
    remove.mutate(sym);
    haptics.impact();
  };

  const reorderable = sort.key === null;

  const header = (
    <View>
      <WatchlistHeader
        onAdd={() => setAdding((a) => !a)}
        onOpenAlerts={() => router.push('/alerts')}
        editing={editing}
        onDone={() => setEditing(false)}
        session={sessionTag}
      />
      <ColumnHeader sort={sort} onSort={onSort} />
      {adding ? <AddRow onAdded={onAdded} onClose={() => setAdding(false)} /> : null}
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
          panActivateAfterLongPress={280}
          onDragStart={onDragStartHaptic}
          onIndexChange={onIndexChangeHaptic}
          keyExtractor={(symbol) => symbol}
          refreshControl={refreshControl}
          contentContainerStyle={{ paddingBottom: 110, paddingTop: spacing.xs }}
          ListHeaderComponent={header}
          ListEmptyComponent={empty}
          renderItem={({ item, index }) => (
            <ReorderableRow
              symbol={item}
              quote={quotes[item]}
              loading={marketLoading && !quotes[item]}
              onPress={() => openSymbol(item)}
              index={index}
              editing={editing}
              onEnterEdit={() => setEditing(true)}
              onDelete={onDelete}
            />
          )}
        />
      ) : (
        <FlashList
          ref={listRef}
          key="watchlist-sorted"
          data={data}
          keyExtractor={(symbol) => symbol}
          refreshControl={refreshControl}
          contentContainerStyle={{ paddingBottom: 110, paddingTop: spacing.xs }}
          ListHeaderComponent={header}
          renderItem={({ item: symbol }) => (
            <RowBody symbol={symbol} quote={quotes[symbol]} loading={marketLoading && !quotes[symbol]} onPress={() => openSymbol(symbol)} />
          )}
          ListEmptyComponent={empty}
        />
      )}
    </Screen>
  );
}
